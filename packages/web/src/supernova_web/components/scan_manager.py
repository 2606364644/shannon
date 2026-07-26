from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles

from temporalio.client import Client

from supernova_core.services.temporal_infra import WEB_TASK_QUEUE_WHITEBOX
from supernova_core.session import SessionManager
from supernova_whitebox.pipeline.workflows import WhiteboxScanWorkflow
from supernova_whitebox.pipeline.shared import PipelineInput
from supernova_web.models import ScanRequest
from .repo_manager import _resolve_repo_dir, _validate_ws_segment
from .scan_liveness import is_scan_recently_active


class TemporalUnavailable(Exception):
    pass


class TooManyScans(Exception):
    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"已有扫描在跑（并发上限 {limit}）")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScanManager:
    """C1 Phase B: web 提交端改为 temporal workflow 提交者(不再 fork CLI 子进程).

    start → Client.connect + start_workflow(提交到固定 queue supernova-wb-web, worker
    容器消费); _watch → tail events.ndjson 直到 scan_end; cancel → handle.cancel(temporal
    原生). active_pids 返空(无本机 pid, 判活靠 heartbeat mtime). 黑盒/correlation C1 化留 Phase C.
    """

    def __init__(self, workspaces_dir: Path, repos_dir: Path, config_store: Any,
                 max_concurrent: int = 1, scan_timeout: float = 0.0) -> None:
        self._workspaces_dir = Path(workspaces_dir)
        self._repos_dir = Path(repos_dir)
        self._config_store = config_store
        self._max_concurrent = max(1, max_concurrent)
        self._scan_timeout = scan_timeout
        # C1: _procs(子进程) → _handles(temporal WorkflowHandle). cancel 用 handle.cancel().
        self._handles: dict[str, Any] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        # 进行中的 scan 请求快照（ws -> ScanRequest），供 active_repo_sources() 判引用
        self._active_reqs: dict[str, ScanRequest] = {}

    # ---- 公共 API ----
    def active_pids(self) -> dict[str, int]:
        # C1: web 无本机 pid(扫描跑在 worker 容器). 判活完全靠 scan_liveness.is_scan_recently_active
        # (heartbeat mtime). orphan_reconciler 的 is_scan_recently_active 门兜底(不误杀活 workflow).
        return {}

    def active_repo_sources(self) -> set[tuple[str, str]]:
        """当前正在跑的 scan 引用的 (ws, repo 名) 集合（DELETE /repos 判引用用）。

        T3: 返回值改为 (ws, name) 元组集合——P2 隔离后 ws 维度必须参与引用判定,
        否则 ws-A 的 scan 引用 repo-X 时 ws-B 仍能删 ws-B/repo-X(虽然 RepoManager
        已 per-ws 隔离不会真删错, 但 409 引用门本应感知 ws)。
        """
        out: set[tuple[str, str]] = set()
        for ws, req in self._active_reqs.items():
            if req.source is not None and req.source.kind == "repo":
                out.add((ws, req.source.value))
        return out

    async def reap_zombies(self) -> None:
        """lifespan 启动时扫无主子进程。C1 后 web 无子进程 → no-op."""
        return None

    async def start(self, req: ScanRequest) -> str:
        await self._check_temporal()
        if len(self._handles) >= self._max_concurrent:
            raise TooManyScans(self._max_concurrent)

        if req.type == "correlation":
            # correlation 解析仍需 yaml(out_workspace), 但 C1 提交留 Phase C.
            target, yaml_path = await self._resolve_inputs(req)
            ws = self._resolve_out_workspace(yaml_path)
        else:
            target, yaml_path = await self._resolve_inputs(req)
            # P1: ws 必须由 admin 预建 (create_scan 已校验存在 + 成员); 不再自动生成。
            ws = req.workspace

        ws_dir = self._workspaces_dir / ws
        ws_dir.mkdir(parents=True, exist_ok=True)
        # C1: web 端做 session 创建 + owner(原 run_scan:121-135 迁来; CLI 那套不再走).
        SessionManager(self._workspaces_dir).create_workspace(
            web_url=req.url or "", repo_path=target or "", name=ws)
        self._mark_owner(ws_dir, "web")
        event_file = ws_dir / "events.ndjson"
        self._active_reqs[ws] = req

        try:
            if req.type == "whitebox":
                handle = await self._submit_whitebox(target, ws, event_file, req)
            elif req.type == "blackbox":
                raise NotImplementedError("blackbox C1 化留 Phase C(本 plan 白盒聚焦)")
            else:
                raise ValueError(f"correlation 暂未 C1 化: {req.type}")
        except BaseException:
            # 提交失败: _watch 不会被调度, 必须在此清理 _active_reqs, 否则
            # active_repo_sources() 持续误报引用 → DELETE /repos 误判 409.
            self._active_reqs.pop(ws, None)
            raise
        self._handles[ws] = handle
        self._tasks[ws] = asyncio.create_task(self._watch(ws, event_file))
        return ws

    async def _submit_whitebox(self, target: str | None, ws: str,
                               event_file: Path, req: ScanRequest) -> Any:
        """算 workflow_id(读 resumeAttempts) + Client.connect + start_workflow 到固定 queue.

        event_file 塞进 PipelineInput(worker 容器 setup_display 据此挂 StructuredEventRenderer).
        workflow_id 在提交时定(activity 不能改自己的 workflow_id).
        """
        client = await Client.connect(self._temporal_address())
        workflow_id = self._resolve_workflow_id(ws)
        inp = PipelineInput(
            repo_path=target or "",
            web_url=req.url or "",
            workspace_name=ws,
            event_file=str(event_file),
        )
        handle = await client.start_workflow(
            WhiteboxScanWorkflow.run, inp, id=workflow_id,
            task_queue=WEB_TASK_QUEUE_WHITEBOX,
        )
        # 提交成功后锚定 submitted_at(scan_liveness 提交宽限门据此判冷启动窗口, 防误杀).
        # 失败分支(start_workflow 抛)不会到达此处 → 提交失败不写 submitted_at.
        self._mark_submitted_at(self._workspaces_dir / ws)
        return handle

    def _resolve_workflow_id(self, ws: str) -> str:
        """读 session.json top-level resumeAttempts, 算 -resume-N 后缀(workflow_id 提交时定).

        复用 worker.py resolve_workflow_id 语义(resume_attempt>0 加 -resume-N); web 端
        读 session.json 算 n(等价 run_scan:209-224 的读法).
        """
        session_file = self._workspaces_dir / ws / "session.json"
        n = 0
        if session_file.exists():
            try:
                data = json.loads(session_file.read_text("utf-8"))
                attempts = data.get("resumeAttempts") or []
                if isinstance(attempts, list):
                    n = len(attempts)
            except (json.JSONDecodeError, OSError):
                n = 0
        return f"{ws}-resume-{n}" if n > 0 else ws

    async def cancel(self, ws: str) -> dict | None:
        """取消 scan 三轨(C1 后):
        ① _handles 有(web 自起) → handle.cancel()(temporal 原生, 传播到 workflow + heartbeat activity).
        ② heartbeat fresh(owner=host 在跑) → 写 cancel.requested(协作式兜底, 兼容 host CLI).
        ③ heartbeat stale(已死) → 标 cancelled + was_dead.
        web 侧立即返回(不等 worker 真退)→ 状态立即翻转 → Delete 立即可用.
        """
        ws_dir = self._workspaces_dir / ws
        if not ws_dir.exists():
            return None  # 唯一 404 情况
        # ① web 自起: handle.cancel(temporal 原生) + _mark_cancelled 兜底标记.
        # 必须兜底: worker 的 workflow except CancelledError 分支不写 scan_end / 不更新
        # session(仅 try 正常完成分支调 finalize_summary)。web 不标 → session 卡 running
        # (heartbeat stale 后误显 interrupted, 非 cancelled) + _watch 等不到 scan_end 永不
        # 退出 → _handles[ws] 占死 → max_concurrent 槽位泄漏、新扫描起不来。标终态则
        # _status_of 终态优先显 cancelled + _watch 见 scan_end 退出释放槽位(对齐 ②/③ 轨).
        handle = self._handles.get(ws)
        if handle is not None:
            try:
                await handle.cancel()
            except Exception:
                pass  # best-effort; temporal 侧 workflow cancel
            await self._mark_cancelled(ws_dir)
            return {"cancelled": ws}
        # ②/③ owner=host 或已死:标 cancelled(③ 不写协作式信号)
        if is_scan_recently_active(ws_dir):
            (ws_dir / "cancel.requested").write_text("", encoding="utf-8")
            await self._mark_cancelled(ws_dir)
            return {"cancelled": ws, "via": "signal"}
        await self._mark_cancelled(ws_dir)
        return {"cancelled": ws, "was_dead": True}

    def _mark_owner(self, ws_dir: Path, owner: str) -> None:
        """标 session.json owner(web/host)。best-effort:子进程 MetricsTracker 用
        dict(existing) 浅拷贝保留 top-level key。owner 只服务 cancel 分轨诊断。"""
        session_file = ws_dir / "session.json"
        try:
            data = json.loads(session_file.read_text("utf-8")) if session_file.exists() else {}
            if not isinstance(data, dict):
                data = {}
            data["owner"] = owner
            session_file.write_text(json.dumps(data), encoding="utf-8")
        except (OSError, ValueError):
            pass

    def _mark_submitted_at(self, ws_dir: Path) -> None:
        """提交成功后写 session.json submitted_at(scan_liveness 提交宽限门锚点, 防冷启动误杀).

        best-effort read-modify-write(与 _mark_owner 同模式); 仅 _submit_whitebox 提交成功后调.
        每次 start_workflow 提交刷新 → resume 场景也准确(resume 时 created_at 是原始建仓时间,
        不能用作「最近提交」锚点).
        """
        session_file = ws_dir / "session.json"
        try:
            data = json.loads(session_file.read_text("utf-8")) if session_file.exists() else {}
            if not isinstance(data, dict):
                data = {}
            data["submitted_at"] = time.time()
            session_file.write_text(json.dumps(data), encoding="utf-8")
        except (OSError, ValueError):
            pass

    async def _mark_cancelled(self, ws_dir: Path) -> None:
        """标 session.status=cancelled + completed_at + 写 scan_end(若无)。让 _status_of
        因终态优先立即返 cancelled(列表/详情翻转,Delete 立即可用)。"""
        event_file = ws_dir / "events.ndjson"
        if not self._has_scan_end(event_file):
            await self._write_scan_end(event_file, "cancelled", -1, "用户取消(web Cancel)")
        try:
            SessionManager(self._workspaces_dir).update_session(
                ws_dir, {"status": "cancelled", "completed_at": time.time()})
        except Exception:  # noqa: BLE001 - 标记是 best-effort,不阻塞 cancel
            pass

    # ---- 钩子（可 monkeypatch）----
    def _temporal_address(self) -> str:
        """SUPERNOVA_TEMPORAL_HOST:PORT（默认 localhost:7233）。web 容器内 temporal 在
        compose 服务名 `temporal` 上(非 localhost), Client.connect 必须用它."""
        host = os.environ.get("SUPERNOVA_TEMPORAL_HOST", "localhost")
        port = int(os.environ.get("SUPERNOVA_TEMPORAL_PORT", "7233"))
        return f"{host}:{port}"

    async def _check_temporal(self) -> None:
        import socket

        host = os.environ.get("SUPERNOVA_TEMPORAL_HOST", "localhost")
        port = int(os.environ.get("SUPERNOVA_TEMPORAL_PORT", "7233"))

        def _probe() -> bool:
            try:
                with socket.create_connection((host, port), timeout=1.0):
                    return True
            except OSError:
                return False

        loop = asyncio.get_running_loop()
        if not await loop.run_in_executor(None, _probe):
            raise TemporalUnavailable()

    # ---- 内部 ----
    async def _resolve_inputs(self, req: ScanRequest) -> tuple[str | None, Path | None]:
        target: str | None = None
        yaml_path: Path | None = None
        if req.source is not None:
            if req.source.kind == "repo":
                target = self._resolve_repo_path(req.workspace, req.source.value)
            else:  # path
                target = req.source.value
        if req.type == "correlation":
            yaml_path = await self._resolve_correlation_yaml(req)
        return target, yaml_path

    def _resolve_repo_path(self, ws: str, name: str) -> str:
        """将 repo 名（可为 group/repo）解析为 workspaces/<ws>/repos 内绝对路径，
        并校验 state==ready。

        T3: P2 隔离后 repo 路径从全局 repos/ 改为 workspaces/<ws>/repos（RepoManager
        per-ws 落地）；ws 来自 P1 的 req.workspace（scan 路由已校验 ws 存在 + 用户为
        成员），这里再过一道 _validate_ws_segment 是 defense-in-depth（与 RepoManager
        ._repos_root 同档约束）。
        """
        _validate_ws_segment(ws)
        repo_dir = _resolve_repo_dir(self._workspaces_dir / ws / "repos", name)
        if not repo_dir.is_dir():
            raise ValueError(f"仓库不存在：{name}")
        meta_file = repo_dir / ".supernova-repo.json"
        state = "ready"
        if meta_file.exists():
            try:
                state = json.loads(meta_file.read_text("utf-8", errors="replace")).get("state", "ready")
            except json.JSONDecodeError:
                state = "ready"  # 元数据损坏不阻塞扫描
        if state != "ready":
            raise ValueError(f"仓库未就绪（state={state}），请先在 ws 内完成 clone")
        return str(repo_dir)

    def _resolve_out_workspace(self, yaml_path: Path | None) -> str:
        """从 correlation yaml 解析 out_workspace，作为 event_file 所在 ws。"""
        from supernova_core.config.parser import parse_multi_repo_config
        if yaml_path is None or not Path(yaml_path).exists():
            raise ValueError("correlation 扫描需可解析的 yaml 以取 out_workspace")
        cfg = parse_multi_repo_config(yaml_path)
        return cfg.correlation.out_workspace

    async def _resolve_correlation_yaml(self, req: ScanRequest) -> Path:
        assert self._config_store is not None, "correlation 需 config_store"
        if req.config_name:
            # 走 store 的校验路径(禁止 "/" / ".." / 空), 不能直接拼 web-multi-{name}.yaml
            # 否则 config_name="../evil" 路径遍历绕过 store 校验.
            return self._config_store.path_for(req.config_name)
        if req.config_content:
            if req.save_as:
                return self._config_store.write(req.save_as, req.config_content)
            return self._config_store.write_temp(req.config_content)
        raise ValueError("correlation 扫描需 config_name 或 config_content")

    async def _watch(self, ws: str, event_file: Path) -> None:
        """C1: tail events.ndjson 直到 scan_end(worker finalize_summary 写)或超时.

        session-status 同步:周期 handle.describe() 轮询 workflow 状态,发现 FAILED /
        TIMED_OUT / TERMINATED 时(worker 进程崩溃/容器死/被 terminate,workflow except
        跑不到)自行写 scan_end + session.status=failed.
        """
        from temporalio.client import WorkflowExecutionStatus
        # describe() 见到的终态集合 → 标 failed
        _FAILED_STATES = {
            WorkflowExecutionStatus.FAILED,
            WorkflowExecutionStatus.TIMED_OUT,
            WorkflowExecutionStatus.TERMINATED,
        }
        try:
            deadline = (time.monotonic() + self._scan_timeout) if self._scan_timeout > 0 else None
            describe_tick = 0
            while not self._has_scan_end(event_file):
                if deadline is not None and time.monotonic() > deadline:
                    if not self._has_scan_end(event_file):
                        await self._write_scan_end(event_file, "timeout", -1, "web 超时收尾")
                    break
                # 每 ~15s describe 一次(0.5s sleep × 30 = 15s)
                describe_tick += 1
                if describe_tick >= 30:
                    describe_tick = 0
                    handle = self._handles.get(ws)
                    if handle is not None:
                        try:
                            desc = await handle.describe()
                            if desc.status in _FAILED_STATES:
                                await self._write_scan_end(
                                    event_file, "failed", -1,
                                    f"workflow {desc.status.name}",
                                    session_status="failed", workspace_name=ws,
                                    workspaces_dir=self._workspaces_dir,
                                )
                                break
                        except Exception:
                            pass  # temporal 断连等:忽略,下个 tick 重试
                await asyncio.sleep(0.5)
        finally:
            # 兜底: 若 worker 未写 scan_end(异常/crash), 补一条
            if not self._has_scan_end(event_file):
                await self._write_scan_end(event_file, "crashed", -1, "worker 未写 scan_end")
            # 清理登记必须兜底: 即便 _write_scan_end 抛出, _handles/_tasks/_active_reqs 也得释放,
            # 否则 active_repo_sources() 误报引用.
            self._handles.pop(ws, None)
            self._tasks.pop(ws, None)
            self._active_reqs.pop(ws, None)

    @staticmethod
    def _has_scan_end(event_file: Path) -> bool:
        if not event_file.exists():
            return False
        for line in event_file.read_text("utf-8", errors="replace").splitlines()[-5:]:
            try:
                if json.loads(line).get("type") == "scan_end":
                    return True
            except json.JSONDecodeError:
                continue
        return False

    async def _write_scan_end(self, event_file: Path, status: str,
                              returncode: int, stderr_tail: str,
                              session_status: str | None = None,
                              workspace_name: str | None = None,
                              workspaces_dir: Path | None = None) -> None:
        payload = {
            "ts": _now_iso(), "category": "CONTROL", "type": "scan_end",
            "status": status, "returncode": returncode, "stderr_tail": stderr_tail,
        }
        # session-status 同步:先写 session.json 再写 events.ndjson —— 若 session 写失败,
        # events 不写(_watch 下个 describe tick 重试);若 session 成功而 events 失败,
        # session 已是终态(failed),finally 的 crashed 兜底补 events(scan_end)。
        # (顺序反过来会让 events 有 scan_end 而 session 永远 running = 重现 ghost-scan)
        if session_status and workspace_name and workspaces_dir is not None:
            SessionManager(workspaces_dir).update_session(
                workspaces_dir / workspace_name,
                {"status": session_status, "completed_at": time.time()},
            )
        async with aiofiles.open(event_file, "a") as fh:
            await fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
