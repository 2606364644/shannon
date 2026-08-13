from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles

_log = logging.getLogger(__name__)

from temporalio.client import Client

from supernova_core.services.temporal_infra import WEB_TASK_QUEUE_WHITEBOX
from supernova_core.runtime.workflow_timeout import workflow_run_timeout
from supernova_core.session import SessionManager
from supernova_core.utils.paths import (
    blackbox_dir, blackbox_run_dir, combined_run_dir, whitebox_dir)
from supernova_whitebox.pipeline.workflows import WhiteboxScanWorkflow
from supernova_whitebox.pipeline.shared import PipelineInput
from supernova_web.models import ScanRequest
from .host_profile_store import (
    HostMapping,
    HostProfileRefreshEmpty,
    fetch_and_parse_hosts,
)
from .repo_manager import _resolve_repo_dir, _validate_ws_segment, resolve_linked_repo_path
from .scan_liveness import is_scan_recently_active
from .scan_store import ScanStore
from .workspaces_indexer import _compute_status


class TemporalUnavailable(Exception):
    pass


class TooManyScans(Exception):
    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"已有扫描在跑（并发上限 {limit}）")


class ScanRunning(Exception):
    """删除 running scan 被拒（应先 cancel 再删，对齐 delete_workspace 删 ws 前要求无 running scan）。

    删在跑 workflow 的目录会致 _watch describe 不存在的 workflow / max_concurrent 槽位泄漏。
    """
    def __init__(self, scan_id: str) -> None:
        self.scan_id = scan_id
        super().__init__(f"扫描正在运行，请先取消再删除：{scan_id}")


class AuthValidationPending(Exception):
    """认证验证 workflow 仍在运行，结果未就绪（块2：get_result 非阻塞查询）。

    verify-status 端点 catch 后转 503，前端继续轮询——修轮询超时误判：workflow 实测跑 88–153s
    而前端轮询上限 120s，阻塞 result() 致 HTTP 挂死 → 成功被显示成失败。改 describe() 非阻塞：
    RUNNING → 抛本异常（秒级返回）；终态 → result()（已就绪不阻塞）。对前端 "running" 与
    "Temporal down" 都是 "继续轮询"，故端点统一 503 不细分。
    """


# resume 放行的状态：已停未完成（worker 中途停止、有部分进度可续）。
# completed/failed（已结束）/ cancelled（用户主动停）/ running（在跑，resume 会重复提交）
# -> 不可 resume，用重扫（POST /api/scan 起新 scan_id，旧记录保留）。
_RESUMABLE_STATUSES = frozenset({"interrupted", "crashed"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScanManager:
    """C1 Phase B + T3: web 提交端改为 temporal workflow 提交者(不再 fork CLI 子进程).

    T3: 1 ws : N scans -- _handles/_tasks/_active_reqs key 由 ws 改 (ws, scan_id)；
    ScanStore.create_scan 建 scans/<scan_id>/session.json（ws 根不再写 session.json）；
    event_file=scan_dir/events.ndjson（worker 据其 parent 推导产物目录，零改动）。
    同 ws 多 scan 不互斥（key 不同），全局 max_concurrent 保留为全局上限。
    start -> Client.connect + start_workflow; _watch -> tail events.ndjson 直到 scan_end;
    cancel(ws, scan_id) 精确取消（scan_id 强制；旧 cancel(ws) shim + DELETE /api/scan/{ws} 已于 e1406473 移除）;
    resume(ws, scan_id) 续跑 interrupted/crashed scan（completed/failed/cancelled/running 拒）。active_pids 返空(判活靠 heartbeat mtime)。
    """

    def __init__(self, workspaces_dir: Path, repos_dir: Path, config_store: Any,
                 max_concurrent: int = 1, scan_timeout: float = 0.0,
                 ws_config_store: Any = None,
                 auth_profile_store: Any = None,
                 host_profile_store: Any = None) -> None:
        self._workspaces_dir = Path(workspaces_dir)
        self._repos_dir = Path(repos_dir)
        self._config_store = config_store
        self._max_concurrent = max(1, max_concurrent)
        self._scan_timeout = scan_timeout
        # T3: _handles/_tasks/_active_reqs key = (ws, scan_id)（同 ws 多 scan 不互斥）。
        self._handles: dict[tuple[str, str], Any] = {}
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        # 进行中的 scan 请求快照（(ws, scan_id) -> ScanRequest），供 active_repo_sources() 判引用
        self._active_reqs: dict[tuple[str, str], ScanRequest] = {}
        # P3c 阶段 2：per-ws 配置解析（None=CLI/旧测试兜底，走全局 env）
        self._ws_config_store = ws_config_store
        # 认证档案库（Task 4）：认证管理页"测试登录"探针读写 verify_status 的 store;
        # None=旧测试/CLI 兜底（不调 start_auth_validation 即不影响既有流程）。
        self._auth_profile_store = auth_profile_store
        # HOST 档案库（Task 11 会用）：domain→IP 映射注入扫描前 resolve_host。
        # None=旧测试/CLI 兜底（不影响既有流程）；T10 仅声明参数让 app 能 boot。
        self.host_profile_store = host_profile_store
        # T3: ScanStore 复用 core SessionManager 做 scan 读写（core 零改动）。
        self._store = ScanStore(self._workspaces_dir)
        # 串行化黑盒 create_scan，保证 <wb>~<N> 序号分配原子（防并发同白盒争同序号）。
        self._create_scan_lock = asyncio.Lock()
        # 组合扫描接力编排 task（spec §7.2）：key=(ws, scan_id) → asyncio.Task。
        # start 组合分支 asyncio.create_task(_combined_orchestrator) 登记；orchestrator
        # finally 自 pop。用于 cancel/delete 时取消接力 + 诊断（fire-and-forget，不阻塞 start）。
        self._orchestrator_tasks: dict[tuple[str, str], asyncio.Task] = {}
        # 组合扫描崩溃恢复 task（spec §7.5，Task 5）：key=(ws, scan_id) → asyncio.Task。
        # orphan_reconciler 经 _kick_combined_reconcile fire-and-forget 登记；恢复完成
        # （或异常）后 finally 自 pop。防止 re-entry 重复 fire（幂等）+ 诊断。
        self._reconcile_tasks: dict[tuple[str, str], asyncio.Task] = {}
    def active_pids(self) -> dict[str, int]:
        # C1: web 无本机 pid(扫描跑在 worker 容器). 判活完全靠 scan_liveness.is_scan_recently_active
        # (heartbeat mtime). orphan_reconciler 的 is_scan_recently_active 门兜底(不误杀活 workflow).
        return {}

    def active_repo_sources(self) -> set[tuple[str, str]]:
        """当前正在跑的 scan 引用的 (ws, repo 名) 集合（DELETE /repos 判引用用）。

        T3: _active_reqs key 改 (ws, scan_id) 元组，值仍含 req.workspace -- 派生 (ws, name)
        不受 key 变化影响（ws 维度仍参与引用判定，防 ws-A 的 scan 误锁 ws-B 的同名 repo）。
        """
        out: set[tuple[str, str]] = set()
        for (ws, _scan_id), req in self._active_reqs.items():
            if req.source is not None and req.source.kind == "repo":
                out.add((ws, req.source.value))
        return out

    async def reap_zombies(self) -> None:
        """lifespan 启动时扫无主子进程。C1 后 web 无子进程 -> no-op."""
        return None

    def reap_stale_probes(self) -> int:
        """启动期清所有 ws 的 auth-probes/*/ 残留(worker 异常残留的明文 probe 目录)。

        验证是即时操作(test → 取 result → 删),无长期运行态;启动时残留 = 上次 worker
        崩溃。整目录删(含明文 scan-config.yaml)。返清理数量。
        """
        import shutil
        n = 0
        if not self._workspaces_dir.is_dir():
            return 0
        for ws_dir in self._workspaces_dir.iterdir():
            probes = ws_dir / "auth-probes"
            if probes.is_dir():
                for probe in probes.iterdir():
                    if probe.is_dir():
                        shutil.rmtree(probe, ignore_errors=True)
                        n += 1
                try:
                    probes.rmdir()  # 空了删父目录
                except OSError:
                    pass
        return n

    async def start(self, req: ScanRequest) -> tuple[str, str]:
        """T3: 提交新 scan -> (ws, scan_id)。

        HOST source is resolved before ``ScanStore.create_scan`` so invalid or empty
        mappings cannot leave a ghost scan.  Once the directory exists, the immutable
        ``host_config`` snapshot is written before auth config/workflow submission.
        """
        await self._check_temporal()
        if len(self._handles) >= self._max_concurrent:
            raise TooManyScans(self._max_concurrent)

        if req.type == "correlation":
            target, yaml_path = await self._resolve_inputs(req)
            ws = self._resolve_out_workspace(yaml_path)
        else:
            target, yaml_path = await self._resolve_inputs(req)
            ws = req.workspace

        # HOST only belongs to a blackbox stage.  Resolve it before creating a scan
        # directory; pure whitebox/correlation intentionally ignore legacy HOST fields.
        host_config = None
        if req.type == "blackbox" or (req.type == "whitebox" and req.url):
            host_config = await self._resolve_host_config(req, ws)

        ws_dir = self._workspaces_dir / ws
        ws_dir.mkdir(parents=True, exist_ok=True)

        lineage: str | None = None
        if req.type == "blackbox":
            if not req.reuse_whitebox_scan_id:
                raise ValueError("blackbox 扫描必须复用白盒结果（reuse_whitebox_scan_id）")
            if self._store.get_scan_dir(ws, req.reuse_whitebox_scan_id) is None:
                raise ValueError(f"要复用的白盒扫描不存在: {req.reuse_whitebox_scan_id}")
            lineage = req.reuse_whitebox_scan_id

        if req.type == "blackbox":
            async with self._create_scan_lock:
                scan_id, scan_dir = self._store.create_scan(
                    ws, req.url or "", target or "", req.type, lineage=lineage)
        else:
            scan_id, scan_dir = self._store.create_scan(
                ws, req.url or "", target or "", req.type)
        self._mark_owner(scan_dir, "web")
        event_file = scan_dir / "events.ndjson"
        scan_key = (ws, scan_id)
        self._active_reqs[scan_key] = req

        try:
            # Snapshot before auth config and before Temporal submission.
            if host_config is not None:
                SessionManager(scan_dir.parent).update_session(
                    scan_dir, {"host_config": host_config})

            if req.type == "whitebox":
                if req.url:
                    config_path = await self._dump_auth_config(req, ws, scan_dir)
                    host_mappings = self._host_config_mappings(host_config)
                    SessionManager(scan_dir.parent).update_session(scan_dir, {
                        "combined": True,
                        "bb_url": req.url,
                        # Keep the legacy field for old readers/reconcile paths.
                        "bb_host_mappings": host_mappings,
                        "bb_auth_ref": self._snapshot_auth_ref(req),
                        "bb_phase": "precheck",
                    })
                    ok = await self._run_precheck(
                        scan_dir, ws, scan_id, req.url, config_path,
                        host_mappings=host_mappings)
                    if not ok:
                        await self._mark_bb(scan_dir, "failed", "auth_failed")
                        await self._ensure_scan_end(scan_dir)
                        self._active_reqs.pop(scan_key, None)
                        return ws, scan_id
                    await self._mark_bb(scan_dir, "pending")
                handle = await self._submit_whitebox(
                    target, ws, scan_id, scan_dir, event_file, req.url or "")
                SessionManager(scan_dir.parent).update_session(
                    scan_dir, {"source_repo": req.source.value if req.source else None})
                if req.url:
                    orch = asyncio.create_task(
                        self._combined_orchestrator(scan_key, handle, scan_dir, req))
                    self._orchestrator_tasks[scan_key] = orch
            elif req.type == "blackbox":
                config_path, repo_path = await self._resolve_blackbox_inputs(
                    req, ws, scan_dir, target)
                host_mappings = self._host_config_mappings(host_config)
                handle = await self._submit_blackbox(
                    repo_path, ws, scan_id, scan_dir, event_file, req.url or "",
                    config_path, host_mappings=host_mappings)
                SessionManager(scan_dir.parent).update_session(
                    scan_dir, {"reuse_whitebox_scan_id": req.reuse_whitebox_scan_id})
            else:
                raise ValueError(f"correlation 暂未 C1 化: {req.type}")
        except BaseException as exc:
            self._active_reqs.pop(scan_key, None)
            self._handles.pop(scan_key, None)
            self._tasks.pop(scan_key, None)
            self._orchestrator_tasks.pop(scan_key, None)
            # A directory was already created, so never leave it in running state.
            await self._mark_submission_failed(scan_dir, event_file, exc)
            raise
        self._handles[scan_key] = handle
        self._tasks[scan_key] = asyncio.create_task(self._watch(scan_key, event_file, scan_dir))
        return ws, scan_id

    async def resume(self, ws: str, scan_id: str) -> tuple[str, str]:
        """T3: resume 已停未完成的 scan（interrupted/crashed）-> 续跑。

        递增 scan_dir/session.json 的 resumeAttempts + 提交 resume workflow（workflow_id =
        {ws}-{scan_id}-resume-N，由 _resolve_workflow_id 读递增后的 resumeAttempts 算）。
        completed/failed/cancelled/running -> ValueError（用重扫 POST /api/scan 起新 scan）。
        """
        scan_dir = self._store.get_scan_dir(ws, scan_id)
        if scan_dir is None:
            raise ValueError("scan 不存在")
        mgr = SessionManager(scan_dir.parent)
        status = _compute_status(scan_dir, mgr.get_status(scan_dir))
        if status not in _RESUMABLE_STATUSES:
            raise ValueError(f"该扫描状态为 {status}，不可恢复，请重新扫描")
        await self._check_temporal()
        if len(self._handles) >= self._max_concurrent:
            raise TooManyScans(self._max_concurrent)

        data = mgr.get_session_data(scan_dir)
        event_file = scan_dir / "events.ndjson"
        scan_key = (ws, scan_id)

        # ── 组合扫描 resume（spec §11.4）──────────────────────────────────────
        # 读 session combined/bb_phase/bb_rerun_attempts 分阶段：
        # - pending/precheck → 重交白盒（-resume-N）+ 重启完整 _combined_orchestrator（接力续跑）。
        # - running → 黑盒 workflow 仍在 Temporal 跑 → re-attach handle + 仅做报告的编排 task。
        # 零回归：仅 combined 真时触发；非组合走下方既有路径不变。
        if data.get("combined"):
            # 续跑前剥掉旧 scan_end（所有分支共享；否则 _watch 见旧 scan_end 立即退出）。
            self._strip_trailing_scan_end(event_file)
            bb_phase = data.get("bb_phase")
            bb_rerun = int(data.get("bb_rerun_attempts") or 0)
            mgr.update_session(scan_dir, {"status": "running", "completed_at": None})
            # 重建 ScanRequest 供 _active_reqs（active_repo_sources 引用锁）+ 编排 task。
            # auth 字段从 bb_auth_ref 回填（_snapshot_auth_ref 对称重建，供 _run_blackbox_phase）。
            req = self._build_combined_resume_req(data, ws)
            self._active_reqs[scan_key] = req

            if bb_phase == "running":
                # 黑盒 workflow 仍在 Temporal 跑（scan_manager 进程死了，Temporal 留活）→
                # re-attach handle（get_workflow_handle，不重 submit——workflow_id 已固定）。
                # workflow_id = _resolve_workflow_id + suffix（与 _reconcile_combined_scan 同口径；
                # running 分支不递增 resumeAttempts，故 _resolve_workflow_id 算出与原提交一致的 id）。
                suffix = f"-bb-rerun-{bb_rerun}" if bb_rerun > 0 else "-bb"
                bb_wf_id = self._resolve_workflow_id(ws, scan_id) + suffix
                try:
                    client = await Client.connect(self._temporal_address())
                    handle = client.get_workflow_handle(bb_wf_id)
                except BaseException as exc:
                    self._active_reqs.pop(scan_key, None)
                    await self._mark_submission_failed(scan_dir, event_file, exc)
                    raise
                # 附仅做报告的编排 task（接力已发生、黑盒已 submit；只 await 完成 → 融合报告）。
                orch = asyncio.create_task(
                    self._combined_report_orchestrator(scan_key, handle, scan_dir))
                self._orchestrator_tasks[scan_key] = orch
            else:
                # pending/precheck（白盒阶段中断）→ 重交白盒 workflow（-resume-N，复用
                # _submit_whitebox）+ 重启完整 _combined_orchestrator（白盒完成后接力黑盒 + 报告）。
                attempts = data.get("resumeAttempts") or []
                if not isinstance(attempts, list):
                    attempts = []
                n = len(attempts) + 1
                attempts = list(attempts) + [
                    {"workflowId": f"{ws}-{scan_id}-resume-{n}", "ts": time.time()}]
                mgr.update_session(scan_dir, {"resumeAttempts": attempts})
                repo_path = data.get("repo_path") or ""
                web_url = data.get("web_url") or ""
                try:
                    handle = await self._submit_whitebox(
                        repo_path, ws, scan_id, scan_dir, event_file, web_url)
                except BaseException as exc:
                    self._active_reqs.pop(scan_key, None)
                    await self._mark_submission_failed(scan_dir, event_file, exc)
                    raise
                orch = asyncio.create_task(
                    self._combined_orchestrator(scan_key, handle, scan_dir, req))
                self._orchestrator_tasks[scan_key] = orch
            self._handles[scan_key] = handle
            self._tasks[scan_key] = asyncio.create_task(
                self._watch(scan_key, event_file, scan_dir))
            return ws, scan_id

        # ── 非组合：既有 resume 路径（零回归）──────────────────────────────────
        repo_path = data.get("repo_path") or ""
        web_url = data.get("web_url") or ""
        scan_type = mgr.get_scan_type(scan_dir) or "whitebox"
        # blackbox reuse：凭 session.reuse_whitebox_scan_id 重解析 wb_scan_dir（修 reuse resume
        # fail-fast：create_scan target="" → repo_path 读空 → workflow detect_whitebox_results=False）。
        reuse_whitebox_scan_id = data.get("reuse_whitebox_scan_id")
        # 递增 resumeAttempts -> _resolve_workflow_id 算 -resume-N 后缀
        attempts = data.get("resumeAttempts") or []
        if not isinstance(attempts, list):
            attempts = []
        n = len(attempts) + 1
        attempts = list(attempts) + [
            {"workflowId": f"{ws}-{scan_id}-resume-{n}", "ts": time.time()}]
        mgr.update_session(scan_dir, {
            "resumeAttempts": attempts, "status": "running", "completed_at": None})

        # 续跑前剥掉旧 scan_end（中断时 orphan_reconciler/_watch 写的），否则 _watch 见旧
        # scan_end 立即退出，无法 tail 新 workflow。旧中断事件从 ndjson 末尾移除（session
        # 的 resumeAttempts 已记 resume 次数，历史可追溯）。
        self._strip_trailing_scan_end(event_file)

        # 重构 ScanRequest 供 _active_reqs（active_repo_sources 判引用用）。blackbox 必须带
        # reuse_whitebox_scan_id 过 model_validator（_blackbox_requires_reuse），否则 ValidationError。
        req_kwargs: dict = dict(type=scan_type, url=web_url or None, workspace=ws)
        if scan_type == "blackbox" and reuse_whitebox_scan_id:
            req_kwargs["reuse_whitebox_scan_id"] = reuse_whitebox_scan_id
        req = ScanRequest(**req_kwargs)
        self._active_reqs[scan_key] = req
        try:
            if scan_type == "blackbox":
                # resume 黑盒：config_path 从 scan_dir/scan-config.yaml 回填（原 auth 配置）。
                cfg = scan_dir / "scan-config.yaml"
                config_path = str(cfg) if cfg.exists() else None
                # reuse 黑盒：凭 reuse_whitebox_scan_id 重解析 wb_scan_dir 作 repo_path（修 fail-fast：
                # start 已落 reuse_id；此处对齐 _resolve_blackbox_inputs 的 get_scan_dir 口径，
                # 随 workspaces_dir 配置走，不存易失绝对路径）。无 reuse_id（legacy）回落 repo_path。
                if reuse_whitebox_scan_id:
                    wb_scan_dir = self._store.get_scan_dir(ws, reuse_whitebox_scan_id)
                    if wb_scan_dir is None:
                        raise ValueError(f"要复用的白盒扫描已不存在: {reuse_whitebox_scan_id}")
                    repo_path = str(wb_scan_dir)
                handle = await self._submit_blackbox(
                    repo_path or None, ws, scan_id, scan_dir, event_file, web_url, config_path,
                    host_mappings=self._session_host_mappings(data))
            else:
                handle = await self._submit_whitebox(
                    repo_path, ws, scan_id, scan_dir, event_file, web_url)
        except BaseException as exc:
            self._active_reqs.pop(scan_key, None)
            await self._mark_submission_failed(scan_dir, event_file, exc)
            raise
        self._handles[scan_key] = handle
        self._tasks[scan_key] = asyncio.create_task(self._watch(scan_key, event_file, scan_dir))
        return ws, scan_id

    async def _submit_whitebox(self, target: str | None, ws: str, scan_id: str,
                               scan_dir: Path, event_file: Path, web_url: str) -> Any:
        """算 workflow_id(读 resumeAttempts) + Client.connect + start_workflow 到固定 queue.

        T3: workspace_name=scan_id（worker 据此 + event_file.parent 推导 scan_dir 产物目录，
        workspace_name 对 web 路径仅作展示）。event_file 塞进 PipelineInput(worker 容器
        setup_display 据此挂 StructuredEventRenderer)。workflow_id 在提交时定(activity 不能改).
        """
        # 先解析并校验 workspace-owned Provider 配置，缺配置时不要连接或提交 Temporal。
        provider_config = self._resolve_provider_config(ws)
        client = await Client.connect(self._temporal_address())
        workflow_id = self._resolve_workflow_id(ws, scan_id)
        inp = PipelineInput(
            repo_path=target or "",
            web_url=web_url or "",
            workspace_name=scan_id,
            event_file=str(event_file),
            provider_config=provider_config,
        )
        handle = await client.start_workflow(
            WhiteboxScanWorkflow.run, inp, id=workflow_id,
            task_queue=WEB_TASK_QUEUE_WHITEBOX,
            run_timeout=workflow_run_timeout(),
        )
        # 提交成功后锚定 submitted_at(scan_liveness 提交宽限门据此判冷启动窗口, 防误杀).
        # 失败分支(start_workflow 抛)不会到达此处 -> 提交失败不写 submitted_at.
        self._mark_submitted_at(scan_dir)
        return handle

    async def _dump_auth_config(
        self, req: ScanRequest, ws: str, scan_dir: Path,
    ) -> str | None:
        """展开认证配置 → 写 scan_dir/scan-config.yaml，返 config_path（无认证返 None）。

        原 _resolve_blackbox_inputs 的 config_path 段抽出，供组合分支 t0 预验证复用（组合模式
        无 reuse_whitebox_scan_id，不走 _resolve_blackbox_inputs 的 repo_path/reuse 段）。auth→YAML
        展开逻辑（profile 三子模式 + inline 多角色）零改动——core 合流点 parse_config 直读明文 YAML。

        三互斥子模式（model_validator _auth_profile_xor_inline 已保证互斥）：
          - profile_id + cred_ids[] = 子集：展开选中 credentials → accounts[]；
          - profile_id + cred_id     = 单角色（旧契约）：展开该 credential → 单 authentication；
          - profile_id 单独          = 全角色：展开所有 credentials → accounts[]。
          - authentication(inline)   = Authentication.model_validate → dump（+ auth_accounts 多角色）。
        无认证字段 → None（公开目标不登录，预验证/黑盒均跳过 auth 段）。
        """
        import yaml
        from supernova_core.models.config import Authentication
        from .auth_profile_store import credential_to_authentication

        def _dump_auth_payload(payload: dict) -> str:
            cfg_file = scan_dir / "scan-config.yaml"
            cfg_file.write_text(
                yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                encoding="utf-8")
            return str(cfg_file)

        def _expand_multi_identity(profile, creds: list) -> str:
            """多身份展开：creds 列表 → authentication(primary=首个low) + accounts[](其余)。

            子集模式与全角色模式共用：调用方负责筛 creds 子集（子集）或传全量（全角色）。
            primary = 首个 low（无 low 回落首个，兜底防全 high 时无 primary）。
            """
            from supernova_core.utils.authz_identity import derive_privilege_tier
            # high_priv_names 硬编码（plan 作者认可简化；env 化推迟）。
            high_priv_names = ["admin"]

            def _tier_of(c):
                return derive_privilege_tier(c.role, high_priv_names)

            lows = [c for c in creds if _tier_of(c) == "low"]
            primary = lows[0] if lows else (creds[0] if creds else None)
            if primary is None:
                raise ValueError(f"认证档案无凭据: {req.auth_profile_id}")
            primary_auth = credential_to_authentication(profile, primary)
            accounts = []
            for c in creds:
                if c.id == primary.id:
                    continue
                creds_d = {"username": c.username, "password": c.password}
                if getattr(c, "totp_secret", None):
                    creds_d["totp_secret"] = c.totp_secret
                accounts.append({
                    "id": c.id,
                    "role": c.role,
                    "tier": _tier_of(c),
                    "credentials": creds_d,
                })
            payload = {
                "authentication": primary_auth.model_dump(exclude_none=True, mode="json"),
                "accounts": accounts,
            }
            return _dump_auth_payload(payload)

        if req.auth_profile_id and req.auth_credential_ids:
            # 子集模式（2026-08-06）：展开选中的 credentials → accounts[]（默认前端全选）。
            if self._auth_profile_store is None:
                raise RuntimeError("auth_profile_store 未注入，无法展开认证档案")
            profile = self._auth_profile_store.get(ws, req.auth_profile_id)
            if profile is None:
                raise ValueError(f"认证档案不存在: {req.auth_profile_id}")
            selected_ids = set(req.auth_credential_ids)
            selected = [c for c in profile.credentials if c.id in selected_ids]
            if not selected:
                raise ValueError(f"选中的角色凭据不存在: {req.auth_credential_ids}")
            return _expand_multi_identity(profile, selected)
        if req.auth_profile_id and req.auth_credential_id:
            # 单角色模式（旧契约，向后兼容）：展开该 credential → 单 authentication。
            if self._auth_profile_store is None:
                raise RuntimeError("auth_profile_store 未注入，无法展开认证档案")
            profile = self._auth_profile_store.get(ws, req.auth_profile_id)
            if profile is None:
                raise ValueError(f"认证档案不存在: {req.auth_profile_id}")
            cred = next((c for c in profile.credentials if c.id == req.auth_credential_id), None)
            if cred is None:
                raise ValueError(f"角色凭据不存在: {req.auth_credential_id}")
            auth = credential_to_authentication(profile, cred)
            return _dump_auth_payload(
                {"authentication": auth.model_dump(exclude_none=True, mode="json")})
        if req.auth_profile_id:
            # 全角色模式（子项目2 T10）：展开所有 credentials → accounts[]。
            if self._auth_profile_store is None:
                raise RuntimeError("auth_profile_store 未注入，无法展开认证档案")
            profile = self._auth_profile_store.get(ws, req.auth_profile_id)
            if profile is None:
                raise ValueError(f"认证档案不存在: {req.auth_profile_id}")
            return _expand_multi_identity(profile, profile.credentials)
        if req.authentication:
            try:
                auth = Authentication.model_validate(req.authentication)
            except Exception as exc:
                raise ValueError(f"登录配置无效: {exc}") from exc
            payload = {"authentication": auth.model_dump(exclude_none=True, mode="json")}
            # inline 多角色（2026-08-07）：auth_accounts 非空 → 展开 accounts[]（id=角色 slug 去重、
            # tier=derive_privilege_tier、totp_secret 透传）。形状对齐 profile 模式 _expand_multi_identity。
            if req.auth_accounts:
                from supernova_core.utils.authz_identity import derive_privilege_tier
                used_ids: set[str] = set()
                accounts = []
                for acc in req.auth_accounts:
                    role = (acc.get("role") or "").strip() or "role"
                    base = "".join(ch if ch.isalnum() else "-" for ch in role.lower())
                    while "--" in base:
                        base = base.replace("--", "-")
                    base = base.strip("-") or "role"
                    slug = base
                    n = 2
                    while slug in used_ids:
                        slug = f"{base}-{n}"
                        n += 1
                    used_ids.add(slug)
                    creds = {"username": acc.get("username", ""), "password": acc.get("password", "")}
                    if acc.get("totp_secret"):
                        creds["totp_secret"] = acc["totp_secret"]
                    accounts.append({
                        "id": slug,
                        "role": role,
                        "tier": derive_privilege_tier(role, ["admin"]),
                        "credentials": creds,
                    })
                payload["accounts"] = accounts
            return _dump_auth_payload(payload)
        return None  # 无认证字段（公开目标）

    async def _resolve_blackbox_inputs(
        self, req: ScanRequest, ws: str, scan_dir: Path, target: str | None,
    ) -> tuple[str | None, str | None]:
        """解析黑盒提交的 config_path(登录 YAML) + repo_path(复用白盒 / 指定仓库 / standalone)。

        config_path 委托 _dump_auth_config（auth→YAML 展开，profile 三子模式 + inline 多角色）；
        blackbox workflow `if input.config_path:` 据此跑 run_blackbox_auth_validation。校验失败 raise ValueError。
        repo_path: req.reuse_whitebox_scan_id → 该白盒 scan_dir 作 repo_path
          （detect_whitebox_results 在 Path(repo_path)/deliverables 找白盒 queue；wb scan_dir/
          deliverables 即白盒产物落点，blackbox 自身产物靠 workspace_path 另落 bb scan_dir）；
          否则 target（req.source.repo 经 _resolve_inputs 解析）；standalone（无 reuse 无 source）→ None。
        """
        config_path = await self._dump_auth_config(req, ws, scan_dir)
        # 黑盒 = 白盒下游 exploitation-only（阶段 2）：恒复用白盒结果，无 standalone/repo 兜底。
        # model_validator 已在请求层拦 reuse 缺失；此处工作层独立兜底（不依赖 pydantic，防 model 层被误改）。
        if not req.reuse_whitebox_scan_id:
            raise ValueError("blackbox 扫描必须复用白盒结果（reuse_whitebox_scan_id）")
        wb_scan_dir = self._store.get_scan_dir(ws, req.reuse_whitebox_scan_id)
        if wb_scan_dir is None:
            raise ValueError(f"要复用的白盒扫描不存在: {req.reuse_whitebox_scan_id}")
        repo_path: str | None = str(wb_scan_dir)
        return config_path, repo_path

    @staticmethod
    def _normalize_host_mapping_dict(mappings: Any, *, allow_empty: bool = False) -> dict[str, str]:
        """Validate and normalize either HostMapping objects, lists, or snapshot dicts."""
        if isinstance(mappings, dict):
            items = mappings.items()
        elif mappings is None:
            items = []
        else:
            items = (
                (m.get("host"), m.get("ip")) if isinstance(m, dict)
                else (getattr(m, "host", None), getattr(m, "ip", None))
                for m in mappings
            )
        normalized: dict[str, str] = {}
        for host, ip in items:
            mapping = HostMapping(ip=str(ip), host=str(host))
            previous = normalized.get(mapping.host)
            if previous is not None and previous != mapping.ip:
                raise ValueError(f"HOST host {mapping.host!r} maps to multiple IPs")
            normalized[mapping.host] = mapping.ip
        if not normalized and not allow_empty:
            raise ValueError("HOST 配置没有有效 mapping，拒绝回退到默认 DNS")
        return normalized

    @staticmethod
    def _host_config_mappings(host_config: dict | None) -> dict[str, str]:
        if not host_config:
            return {}
        if not host_config.get("enabled", True):
            return {}
        return ScanManager._normalize_host_mapping_dict(host_config.get("mappings"))

    def _session_host_mappings(self, data: dict) -> dict[str, str]:
        """Read the immutable snapshot; only legacy combined fields are fallback."""
        if "host_config" in data and data.get("host_config") is not None:
            cfg = data.get("host_config")
            if not isinstance(cfg, dict):
                raise ValueError("HOST snapshot 损坏，无法安全恢复")
            if not cfg.get("enabled", True):
                return {}
            return self._normalize_host_mapping_dict(cfg.get("mappings"))
        # Pre-snapshot combined scans stored this field; empty means legacy no-HOST.
        if "bb_host_mappings" in data:
            return self._normalize_host_mapping_dict(
                data.get("bb_host_mappings") or {}, allow_empty=True)
        return {}

    async def _resolve_host_config(
        self, req: ScanRequest, ws: str,
    ) -> dict | None:
        """Resolve one HOST source into an immutable per-scan snapshot."""
        if req.host_profile_id is None and req.host_url is None:
            return None

        warnings: list[str] = []
        if req.host_profile_id is not None:
            if self.host_profile_store is None:
                raise RuntimeError("host_profile_store 未注入，无法解析 HOST 档案")
            profile = self.host_profile_store.get(ws, req.host_profile_id)
            if profile is None:
                raise ValueError(f"HOST 档案不存在: {req.host_profile_id}")
            if profile.source_url:
                try:
                    refreshed = await self.host_profile_store.refresh(ws, req.host_profile_id)
                    if refreshed is not None:
                        profile = refreshed
                    get_warnings = getattr(self.host_profile_store, "refresh_warnings", None)
                    if get_warnings is not None:
                        warnings.extend(get_warnings(ws, req.host_profile_id))
                except HostProfileRefreshEmpty as exc:
                    raise ValueError(str(exc)) from exc
                except Exception as exc:
                    if profile.mappings:
                        warnings.append(f"HOST profile refresh failed: {exc}")
                    else:
                        raise ValueError(f"HOST profile refresh failed: {exc}") from exc
            mappings = self._normalize_host_mapping_dict(profile.mappings)
            return {
                "enabled": True,
                "source": "profile",
                "profile_id": req.host_profile_id,
                "source_url": profile.source_url,
                "mappings": mappings,
                "warnings": warnings,
                "resolved_at": time.time(),
            }

        mappings, fetch_warnings = await fetch_and_parse_hosts(req.host_url)
        normalized = self._normalize_host_mapping_dict(mappings)
        warnings.extend(fetch_warnings)
        return {
            "enabled": True,
            "source": "url",
            "profile_id": None,
            "source_url": req.host_url,
            "mappings": normalized,
            "warnings": warnings,
            "resolved_at": time.time(),
        }

    async def _resolve_host_mappings(
        self, req: ScanRequest, ws: str,
    ) -> dict[str, str]:
        """Backward-compatible wrapper returning only the resolved mapping dict."""
        config = await self._resolve_host_config(req, ws)
        return self._host_config_mappings(config)

    async def _submit_blackbox(
        self, repo_path: str | None, ws: str, scan_id: str, scan_dir: Path,
        event_file: Path, web_url: str, config_path: str | None,
        host_mappings: dict[str, str] | None = None,
        workflow_id_suffix: str = "",
    ) -> Any:
        """提交黑盒 scan 到 supernova-bb-web queue。参照 _submit_whitebox。

        BlackboxPipelineInput.event_file 非 None → workflow 走 worker 路径（setup_display 注入
        StructuredEventRenderer 写 events.ndjson，web live 页可见）。workspaces_root 用 web 已知
        的 self._workspaces_dir（worker 容器共享同 volume，路径一致）。

        workflow_id_suffix（spec §7.6，组合接力复用本方法的关键）：默认 ""（零回归，既有调用
        workflow_id 不变）；组合接力首跑传 "-bb"、续跑传 "-bb-rerun-N"。不手写 chained 版
        （原草案手写版漏传 host_mappings 等字段，重复造轮子——见 spec §5）。
        """
        from supernova_blackbox.pipeline.shared import BlackboxPipelineInput
        from supernova_blackbox.pipeline.workflows import BlackboxScanWorkflow
        from supernova_core.services.temporal_infra import WEB_TASK_QUEUE_BLACKBOX

        # 与白盒路径一致：配置不完整时在连接 Temporal 前失败。
        provider_config = self._resolve_provider_config(ws)
        client = await Client.connect(self._temporal_address())
        workflow_id = self._resolve_workflow_id(ws, scan_id) + workflow_id_suffix
        inp = BlackboxPipelineInput(
            web_url=web_url,
            repo_path=repo_path,
            workspace_name=scan_id,
            config_path=config_path,
            event_file=str(event_file),
            provider_config=provider_config,
            workspaces_root=str(self._workspaces_dir),
            exploit=True,
            host_mappings=host_mappings or {},
        )
        handle = await client.start_workflow(
            BlackboxScanWorkflow.run, inp, id=workflow_id,
            task_queue=WEB_TASK_QUEUE_BLACKBOX,
            run_timeout=workflow_run_timeout(),
        )
        self._mark_submitted_at(scan_dir)
        return handle

    async def start_auth_validation(self, ws: str, profile_id: str, cred_id: str) -> dict:
        """认证管理页"测试登录":写 probe scan-config.yaml + 起 AuthValidationWorkflow。

        probe 目录 = workspaces/<ws>/auth-probes/probe-<uuid8>，内含明文 scan-config.yaml
        （core 合流点 parse_config 直读 YAML，无对象通道——明文债收窄到此目录，get_result 后删）。
        workflow_id = authval-<ws>-probe-<uuid8>（与扫描 workflow namespace 隔离）。
        返 {workflow_id, probe_dir} dict（前端轮询 verify-status 端点需两者）。
        """
        import yaml
        from uuid import uuid4
        from supernova_blackbox.pipeline.shared import BlackboxAuthValidationInput
        from supernova_blackbox.pipeline.workflows import AuthValidationWorkflow
        from supernova_core.services.temporal_infra import WEB_TASK_QUEUE_BLACKBOX
        from .auth_profile_store import credential_to_authentication

        if self._auth_profile_store is None:
            raise RuntimeError("auth_profile_store 未注入，无法启动认证验证探针")
        profile = self._auth_profile_store.get(ws, profile_id)
        if profile is None:
            raise ValueError(f"认证档案不存在: {profile_id}")
        cred = next((c for c in profile.credentials if c.id == cred_id), None)
        if cred is None:
            raise ValueError(f"角色凭据不存在: {cred_id}")
        # 块3c：覆盖清理——同 (profile,cred) 上次验证留的旧 probe（VerifyStatus.probe_dir）删掉，
        # 防 auth-probes/ 无限堆积（每次验证一个 probe-<uuid8>）。越界守护：只删 auth-probes 下的
        # （VerifyStatus 若被污染指向任意路径，不删——容器以 root 跑，防任意路径删除）。
        if cred.verify_status.probe_dir:
            import shutil
            old_probe = Path(cred.verify_status.probe_dir).resolve()
            allowed = (self._workspaces_dir / ws / "auth-probes").resolve()
            if old_probe.is_relative_to(allowed):
                shutil.rmtree(old_probe, ignore_errors=True)
        probe_id = f"probe-{uuid4().hex[:8]}"
        probe_dir = self._workspaces_dir / ws / "auth-probes" / probe_id
        probe_dir.mkdir(parents=True, exist_ok=True)
        auth = credential_to_authentication(profile, cred)
        cfg_file = probe_dir / "scan-config.yaml"
        cfg_file.write_text(
            yaml.safe_dump({"authentication": auth.model_dump(exclude_none=True, mode="json")},
                           allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        client = await Client.connect(self._temporal_address())
        # _resolve_provider_config 在 ws_config_store 非None 时会 validate_ws_config；
        # 探针不应因 per-ws provider 配置异常而阻塞（api_key 是可选的，活动层有 env 兜底）。
        try:
            api_key = self._resolve_provider_config(ws).get("api_key")
        except Exception:
            api_key = None
        inp = BlackboxAuthValidationInput(
            web_url=profile.login_url,
            config_path=str(cfg_file),
            workspace_path=str(probe_dir),
            api_key=api_key,
            # 块1c：event_file 落点 = probe_dir/events.ndjson。workflow 经 setup_display 把
            # agent 登录每步写此文件（验证过程可见），verify-log 端点读它回看/实时观看。
            event_file=str(probe_dir / "events.ndjson"),
        )
        handle = await client.start_workflow(
            AuthValidationWorkflow.run, inp,
            id=f"authval-{ws}-{probe_id}", task_queue=WEB_TASK_QUEUE_BLACKBOX,
        )
        # 写 running 中间态——前端重载过程页时识别 state=running → 重挂 VerifyLivePanel 重连 SSE
        # 恢复实时观测（EventTailer 从头重放 events.ndjson 追上现实）。必须在 start_workflow 成功
        # 之后写，避免 connect/start 抛错致 running 滞留；终态由 get_auth_validation_result 覆盖回填。
        # workflow_id/probe_dir 与下方 return 同源，天然满足 get_auth_validation_result 的越界守护。
        from .auth_profile_store import VerifyStatus
        self._auth_profile_store.set_verify_status(
            ws, profile_id, cred_id,
            VerifyStatus(state="running", probe_dir=str(probe_dir), workflow_id=handle.id),
        )
        return {"workflow_id": handle.id, "probe_dir": str(probe_dir)}

    async def start_batch_auth_validation(self, ws: str, profile_id: str,
                                          cred_ids: list[str] | None) -> dict:
        """档案级批量认证验证(认证管理页"测试登录"多选角色):逐个独立验证每个选中角色能否登录。

        语义(对齐 spec §2):串行 N 次 Branch A 单次登录(非越权对比)。为每个选中 cred 建独立
        probe_dir + scan-config.yaml(role 不入 YAML)→ 起 BatchAuthValidationWorkflow(串行)→
        写首 cred running verify_status(前端轮询 profile 定位 running 订阅其 verify-events)→
        起 watcher 周期回填各 cred 终态。返回 {workflow_id}(batch workflow id)。

        cred_ids None/空 = 全选;cred_ids 子集 = 仅选中。cred_id 越界守护:必须属该 pid profile
        (防注入任意 id 越界)。各 cred 覆盖清理旧 probe(复用单 cred 覆盖逻辑,防 auth-probes/ 堆积)。
        """
        import shutil
        import yaml
        from uuid import uuid4
        from supernova_blackbox.pipeline.shared import (
            BlackboxAuthValidationBatchInput, BlackboxAuthValidationBatchItem)
        from supernova_blackbox.pipeline.workflows import BatchAuthValidationWorkflow
        from supernova_core.services.temporal_infra import WEB_TASK_QUEUE_BLACKBOX
        from .auth_profile_store import credential_to_authentication, VerifyStatus

        if self._auth_profile_store is None:
            raise RuntimeError("auth_profile_store 未注入，无法启动批量认证验证")
        profile = self._auth_profile_store.get(ws, profile_id)
        if profile is None:
            raise ValueError(f"认证档案不存在: {profile_id}")
        valid_ids = {c.id for c in profile.credentials}
        if cred_ids:
            bad = [i for i in cred_ids if i not in valid_ids]
            if bad:
                raise ValueError(f"角色凭据不属于该档案: {bad}")
            selected = [c for c in profile.credentials if c.id in set(cred_ids)]
        else:
            selected = list(profile.credentials)
        if not selected:
            raise ValueError("未选择任何角色凭据")
        # 各 cred 覆盖清旧 probe + 建 probe_dir + 写 scan-config.yaml(role 不入 YAML)
        allowed_parent = (self._workspaces_dir / ws / "auth-probes").resolve()
        items: list = []
        cred_probe_map: dict[str, dict] = {}
        for cred in selected:
            if cred.verify_status.probe_dir:
                old_probe = Path(cred.verify_status.probe_dir).resolve()
                if old_probe.is_relative_to(allowed_parent):
                    shutil.rmtree(old_probe, ignore_errors=True)
            probe_id = f"probe-{uuid4().hex[:8]}"
            probe_dir = self._workspaces_dir / ws / "auth-probes" / probe_id
            probe_dir.mkdir(parents=True, exist_ok=True)
            auth = credential_to_authentication(profile, cred)
            cfg_file = probe_dir / "scan-config.yaml"
            cfg_file.write_text(
                yaml.safe_dump({"authentication": auth.model_dump(exclude_none=True, mode="json")},
                               allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            items.append(BlackboxAuthValidationBatchItem(
                cred_id=cred.id,
                web_url=profile.login_url,
                config_path=str(cfg_file),
                workspace_path=str(probe_dir),
                event_file=str(probe_dir / "events.ndjson"),
            ))
            cred_probe_map[cred.id] = {"probe_dir": str(probe_dir)}
        try:
            api_key = self._resolve_provider_config(ws).get("api_key")
        except Exception:
            api_key = None
        inp = BlackboxAuthValidationBatchInput(items=items, api_key=api_key)
        client = await Client.connect(self._temporal_address())
        batch_wf_id = f"authval-batch-{ws}-{uuid4().hex[:8]}"
        handle = await client.start_workflow(
            BatchAuthValidationWorkflow.run, inp,
            id=batch_wf_id, task_queue=WEB_TASK_QUEUE_BLACKBOX,
        )
        # 写首 cred running —— 前端轮询 profile 定位 running → 订阅其 verify-events 实时观测。
        # 其余 cred 保持 unverified,watcher 在各 cred 终态时回填(避免一次写全 running 致前端误判并发)。
        first = selected[0]
        self._auth_profile_store.set_verify_status(
            ws, profile_id, first.id,
            VerifyStatus(state="running", probe_dir=cred_probe_map[first.id]["probe_dir"],
                         workflow_id=handle.id),
        )
        # 起 watcher(Slice 3):周期 query batch_progress → 回填各 cred 终态 verify_status + 删其
        # scan-config(保留 events.ndjson)。fire-and-forget;watcher 全终态后自退。
        asyncio.create_task(self._watch_batch_progress(
            ws, profile_id, handle.id, cred_probe_map))
        return {"workflow_id": handle.id}

    async def _watch_batch_progress(self, ws: str, profile_id: str, workflow_id: str,
                                    cred_probe_map: dict[str, dict]) -> None:
        """批量认证验证 watcher:周期(~2s)query batch_progress → 回填各 cred verify_status。

        分层(spec §4.4):blackbox activity 不调 web store;web 层本 watcher 周期 query temporal
        workflow 的 batch_progress handler,发现某 cred 刚终态 → set_verify_status + 删该 cred
        scan-config.yaml(密码卫生,保留 events.ndjson 供回看)+ probe_dir 越界守护。running 的 cred
        写 running verify_status(前端轮询 profile 定位 running 订阅其 verify-events)。全部终态
        (all_done)→ 退出。fire-and-forget(start_batch 起 asyncio.create_task)。
        """
        from supernova_blackbox.pipeline.workflows import BatchAuthValidationWorkflow

        allowed_parent = (self._workspaces_dir / ws / "auth-probes").resolve()
        backfilled: set[str] = set()
        try:
            client = await Client.connect(self._temporal_address())
            handle = client.get_workflow_handle(workflow_id)
            while True:
                progress = await handle.query(BatchAuthValidationWorkflow.batch_progress)
                for item in progress.get("items", []):
                    cred_id = item.get("cred_id")
                    if not cred_id or cred_id not in cred_probe_map:
                        continue  # 未追踪的 cred(防回填意外实体)
                    state = item.get("state")
                    probe_dir = cred_probe_map[cred_id]["probe_dir"]
                    if state in ("success", "failed"):
                        if cred_id in backfilled:
                            continue
                        self._apply_batch_cred_terminal(
                            ws, profile_id, cred_id, item, probe_dir, workflow_id, allowed_parent)
                        backfilled.add(cred_id)
                    elif state == "running":
                        # 首 cred 已在 start 写 running;后续 cred 在前一个终态后变 running → 补写
                        self._ensure_batch_cred_running(
                            ws, profile_id, cred_id, probe_dir, workflow_id)
                if progress.get("all_done"):
                    break
                await asyncio.sleep(2)
        except Exception:
            pass  # best-effort:Temporal 不可用/query 抛错不致 web 崩(前端轮询 profile 兜底)

    def _apply_batch_cred_terminal(self, ws: str, profile_id: str, cred_id: str, item: dict,
                                   probe_dir: str, workflow_id: str, allowed_parent: Path) -> None:
        """回填某 cred 终态 verify_status + 删其 scan-config(密码卫生)。越界 probe_dir 不动。"""
        from .auth_profile_store import VerifyStatus
        resolved = Path(probe_dir).resolve()
        if not resolved.is_relative_to(allowed_parent):
            return  # 越界守护:不回填不删(防 map 污染致任意路径删除)
        status = VerifyStatus(
            state="success" if item.get("state") == "success" else "failed",
            failure_point=item.get("failure_point"),
            failure_detail=item.get("failure_detail"),
            last_verified_at=datetime.now(timezone.utc).isoformat(),
            probe_dir=probe_dir,
            workflow_id=workflow_id,
        )
        self._auth_profile_store.set_verify_status(ws, profile_id, cred_id, status)
        cfg = resolved / "scan-config.yaml"
        if cfg.exists():
            try:
                cfg.unlink()
            except Exception:
                pass

    def _ensure_batch_cred_running(self, ws: str, profile_id: str, cred_id: str,
                                   probe_dir: str, workflow_id: str) -> None:
        """running 的 cred 若仍 unverified → 写 running(前端定位 running 订阅其 events)。
        幂等:已 running/终态不覆盖(首 cred start 已写;终态 watcher 已回填)。"""
        from .auth_profile_store import VerifyStatus
        profile = self._auth_profile_store.get(ws, profile_id)
        if profile is None:
            return
        cred = next((c for c in profile.credentials if c.id == cred_id), None)
        if cred is None or cred.verify_status.state != "unverified":
            return  # 已 running/终态 → 不覆盖
        self._auth_profile_store.set_verify_status(
            ws, profile_id, cred_id,
            VerifyStatus(state="running", probe_dir=probe_dir, workflow_id=workflow_id))

    async def get_auth_validation_result(
        self, ws: str, workflow_id: str, probe_dir: str,
        profile_id: str, cred_id: str,
    ) -> "VerifyStatus":
        """取 workflow result → 回填 verify_status → 删 probe 目录(含明文 YAML)。

        Temporal SDK 对 dataclass result 反序列化为实例（Task 2 实测），但保持双模解析
        （dict 兜底）以防御 SDK 版本差异。失败时 failure_point 缺失回落 out_of_band
        （对齐 validate_authentication.AUTH_VALIDATION_SCHEMA 的 enum）。

        try/finally 包裹:即使 Temporal fetch / set_verify_status 抛错,明文 probe 目录也必清
        （否则 scan-config.yaml 含明文密码会滞留磁盘至下次 worker 重启）。

        守护(2026-08-05 fix-wave):probe_dir 必须 resolve 到 workspaces/<ws>/auth-probes/ 下,
        workflow_id 必须以 authval-<ws>- 开头。二者均在校验失败时 ValueError(不 rmtree),
        防最低权限 workspace_member 经 verify-status 端点任意删路径 / 跨 ws 读结果。
        """
        from pathlib import Path
        from datetime import datetime, timezone
        from .auth_profile_store import VerifyStatus

        if self._auth_profile_store is None:
            raise RuntimeError("auth_profile_store 未注入，无法回填认证验证结果")
        # 守护①:probe_dir 必须在 workspaces/<ws>/auth-probes/ 下(防任意路径删除)。
        # get_workflow_handle(bogus_id).result() 对不存在 workflow 抛错 → finally 必跑,
        # 故纯 client-side 校验是唯一防线(不允许靠 Temporal 抛错触发 rmtree)。
        allowed_parent = (self._workspaces_dir / ws / "auth-probes").resolve()
        resolved_probe = Path(probe_dir).resolve()
        if not resolved_probe.is_relative_to(allowed_parent):
            raise ValueError(f"probe_dir 越界(必须在 {allowed_parent} 下): {probe_dir}")
        # 守护②:workflow_id 必须绑本 ws(start_auth_validation 产 authval-<ws>-probe-<uuid8>),
        # 防 ws-A 成员读 ws-B 的 auth 验证 workflow 结果(跨 ws 信息泄露)。
        if not workflow_id.startswith((f"authval-{ws}-", f"authval-batch-{ws}-")):
            raise ValueError(
                f"workflow_id 越界(必须以 authval-{ws}- 或 authval-batch-{ws}- 开头): {workflow_id}")
        try:
            client = await Client.connect(self._temporal_address())
            handle = client.get_workflow_handle(workflow_id)
            # 块2：describe() 非阻塞查状态；RUNNING → 抛 AuthValidationPending（端点转 503，
            # 前端继续轮询），绝不阻塞 result()——修轮询超时误判（workflow 跑 88–153s > 前端 120s
            # 上限，阻塞 result() 致 HTTP 挂死 → COMPLETED+success 被 UI 误判 failed）。终态才 result()。
            from temporalio.client import WorkflowExecutionStatus
            desc = await handle.describe()
            if desc.status == WorkflowExecutionStatus.RUNNING:
                raise AuthValidationPending(f"验证 workflow 仍在运行: {workflow_id}")
            raw = await handle.result()
            # AuthValidationResult 经 Temporal 序列化:本 SDK 下为实例,dict 模式防御 SDK 差异。
            success = raw.get("success") if isinstance(raw, dict) else getattr(raw, "success", False)
            now = datetime.now(timezone.utc).isoformat()
            if success:
                status = VerifyStatus(state="success", last_verified_at=now,
                                      probe_dir=probe_dir, workflow_id=workflow_id)
            else:
                fp = (raw.get("failure_point") if isinstance(raw, dict)
                      else getattr(raw, "failure_point", None)) or "out_of_band"
                fd = (raw.get("failure_detail") if isinstance(raw, dict)
                      else getattr(raw, "failure_detail", None))
                status = VerifyStatus(
                    state="failed", failure_point=fp, failure_detail=fd, last_verified_at=now,
                    probe_dir=probe_dir, workflow_id=workflow_id,
                )
            self._auth_profile_store.set_verify_status(ws, profile_id, cred_id, status)
            return status
        finally:
            # 块3a：收窄清理——只删明文 scan-config.yaml（密码卫生），保留 events.ndjson +
            # auth-state.json 供 verify-log 回看/诊断（spec 块3）。整目录 rmtree 会清掉刚落的
            # 过程记录。用 resolved_probe（已校验在 allowed_parent 下）防 TOCTOU 改原始 probe_dir 串。
            cfg = resolved_probe / "scan-config.yaml"
            if cfg.exists():
                try:
                    cfg.unlink()
                except OSError:
                    pass

    async def get_auth_validation_log(
        self, ws: str, workflow_id: str, probe_dir: str, tail: int | None = None,
    ) -> list[dict]:
        """读 probe_dir/events.ndjson 验证过程记录（块3b）。越界守护同 get_result（probe_dir
        须在 workspaces/<ws>/auth-probes/ 下、workflow_id 须 authval-<ws>- 开头，防任意路径读 /
        跨 ws 读 events）。tail=N 取末 N 条（实时观看减传输）;默认全量（事后回看）。文件不存在 →
        []（workflow 未跑/未落盘，前端显示暂无记录）。非法 JSON 行容错跳过。
        """
        import json
        allowed_parent = (self._workspaces_dir / ws / "auth-probes").resolve()
        resolved_probe = Path(probe_dir).resolve()
        if not resolved_probe.is_relative_to(allowed_parent):
            raise ValueError(f"probe_dir 越界(必须在 {allowed_parent} 下): {probe_dir}")
        if not workflow_id.startswith((f"authval-{ws}-", f"authval-batch-{ws}-")):
            raise ValueError(
                f"workflow_id 越界(必须以 authval-{ws}- 或 authval-batch-{ws}- 开头): {workflow_id}")
        events_file = resolved_probe / "events.ndjson"
        if not events_file.exists():
            return []
        lines = [ln for ln in events_file.read_text("utf-8").splitlines() if ln.strip()]
        if tail is not None and tail > 0:
            lines = lines[-tail:]
        out: list[dict] = []
        for ln in lines:
            try:
                parsed = json.loads(ln)
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, dict):
                out.append(parsed)
        return out

    async def auth_validation_events_path(
        self, ws: str, workflow_id: str, probe_dir: str,
    ) -> Path:
        """verify-events SSE 的落点解析 + 越界守护（块4）。

        复用 get_auth_validation_log 的两道守护（probe_dir 须在 workspaces/<ws>/auth-probes/
        下、workflow_id 须 authval-<ws>- 开头，防任意路径读 / 跨 ws 读 events），返回 resolved
        ``events.ndjson`` Path（文件可能尚未落盘——EventTailer 会等它出现）。守护失败 ValueError
        （端点转 403）。
        """
        allowed_parent = (self._workspaces_dir / ws / "auth-probes").resolve()
        resolved_probe = Path(probe_dir).resolve()
        if not resolved_probe.is_relative_to(allowed_parent):
            raise ValueError(f"probe_dir 越界(必须在 {allowed_parent} 下): {probe_dir}")
        if not workflow_id.startswith((f"authval-{ws}-", f"authval-batch-{ws}-")):
            raise ValueError(
                f"workflow_id 越界(必须以 authval-{ws}- 或 authval-batch-{ws}- 开头): {workflow_id}")
        return resolved_probe / "events.ndjson"


    def _resolve_provider_config(self, ws: str) -> dict:
        """P3c 阶段 2：per-ws 解析（ws_config_store）；None -> 全局 env 兜底（阶段1/CLI）。

        ws_config_store 非None 时严格使用 workspace-owned 字段并校验完整性，不从全局配置
        补字段。None（CLI/旧测试）走全局 env。
        """
        if self._ws_config_store is not None:
            from .ws_config_store import validate_ws_config
            validate_ws_config(self._ws_config_store.read(ws))
            return self._ws_config_store.resolve_provider_config(ws)
        from dataclasses import asdict
        from supernova_core.agents.providers import build_provider_config
        return asdict(build_provider_config())

    def _snapshot_auth_ref(self, req: ScanRequest) -> dict:
        """认证明文不进 session.json（D2）。只存 profile_id（非敏感引用）；
        inline 模式存 None——认证明文已在 scan-config.yaml（t0 dump），不重复存。"""
        if req.auth_profile_id:
            return {"profile_id": req.auth_profile_id,
                    "cred_id": req.auth_credential_id,
                    "cred_ids": req.auth_credential_ids}
        return {"profile_id": None}  # inline 认证在 scan-config.yaml

    def _compute_expected_agents(self, req: ScanRequest) -> dict:
        """进度分母：预期白盒 agent 数（spec §9.5）。

        返回 {"whitebox": N}（blackbox 部分在黑盒 submit 时补，见 Task 4——黑盒只 exploit
        白盒发现的类，expected.blackbox 须等白盒 queue 已知才能算）。

        口径（够准即可，收起态精度门槛低，spec §9.2/§9.5）：
        - 白盒默认跑全部 5 vuln 类（inj/xss/ssrf/auth/authz）；
        - SUPERNOVA_LLM_TRACK_ENABLED=0 时，taint 类（inj/xss/ssrf，DEGRADABLE_VULN_CLASSES）
          的 vuln agent 关闭（GitNexus chain_verdict 主干兜底，非 vuln agent，不计入），
          authz/auth 的 LLM 全保留；故关轨时 vuln agent 数减少。
        - 固定骨架 agent：pre-recon + recon + attack-chain + report（与 vuln agent 无关，恒计）。

        N = 4（骨架）+ vuln_agent_count（开轨 5 / 关轨 2）。
        """
        from supernova_core.config.concurrency import is_llm_track_enabled
        from supernova_core.models.agents import ALL_VULN_CLASSES, DEGRADABLE_VULN_CLASSES

        # 白盒默认全跑（web ScanRequest 无 vuln_classes 选择入口）。
        selected = list(ALL_VULN_CLASSES)
        if is_llm_track_enabled():
            vuln_agent_count = len(selected)
        else:
            # 关轨：taint 类 vuln agent 关（GitNexus 兜底，非 vuln agent），仅 authz/auth 跑。
            vuln_agent_count = sum(1 for vc in selected
                                   if vc not in DEGRADABLE_VULN_CLASSES)
        # 骨架 agent（恒跑，与 vuln 类选择 / LLM 轨无关）：pre-recon / recon / attack-chain / report。
        skeleton = 4
        return {"whitebox": skeleton + vuln_agent_count}

    def _resolve_workflow_id(self, ws: str, scan_id: str) -> str:
        """T3: 读 scan_dir/session.json top-level resumeAttempts, 算 -resume-N 后缀。

        workflow_id = {ws}-{scan_id}[-resume-N]（新 scheme）。复用 worker.py
        resolve_workflow_id 语义(resume_attempt>0 加 -resume-N); web 端读 scan_dir
        session.json 算 n(等价 run_scan:209-224 的读法)。
        """
        scan_dir = self._workspaces_dir / ws / "scans" / scan_id
        session_file = scan_dir / "session.json"
        n = 0
        if session_file.exists():
            try:
                data = json.loads(session_file.read_text("utf-8"))
                attempts = data.get("resumeAttempts") or []
                if isinstance(attempts, list):
                    n = len(attempts)
            except (json.JSONDecodeError, OSError):
                n = 0
        base = f"{ws}-{scan_id}"
        return f"{base}-resume-{n}" if n > 0 else base

    async def cancel(self, ws: str, scan_id: str) -> dict | None:
        """取消 scan 三轨(C1 后):
        ① _handles 有(web 自起) -> handle.cancel()(temporal 原生, 传播到 workflow + heartbeat activity).
        ② heartbeat fresh(owner=host 在跑) -> 写 cancel.requested(协作式兜底, 兼容 host CLI).
        ③ heartbeat stale(已死) -> 标 cancelled + was_dead.

        scan_id 必填（scan-scoped DELETE /api/workspaces/{ws}/scans/{scan_id}）。ws 列表行无
        scan_id 的取消由前端 cancelActiveScan 先 listScans 解析出在跑 scan 再调本方法。
        web 侧立即返回(不等 worker 真退)-> 状态立即翻转 -> Delete 立即可用.
        """
        scan_dir = self._store.get_scan_dir(ws, scan_id)
        if scan_dir is None:
            return None  # scan 不存在 -> 404

        scan_key = (ws, scan_id)

        # ── 组合扫描 cancel（spec §11.5）──────────────────────────────────────
        # 按 bb_phase/bb_rerun_attempts 算 workflow_id → re-attach + handle.cancel()；
        # orchestrator task 一并取消（防后台接力继续 submit 黑盒/写 scan_end 与终态竞争）。
        # 零回归：仅 combined 真时触发；非组合走下方既有 ①②③ 轨不变。
        try:
            _combined = SessionManager(scan_dir.parent).get_session_data(scan_dir).get("combined")
        except Exception:  # noqa: BLE001 - session 读失败 → 当非组合处理（零回归）
            _combined = None
        if _combined:
            return await self._cancel_combined(ws, scan_id, scan_dir, scan_key)

        # ① web 自起: handle.cancel(temporal 原生) + _mark_cancelled 兜底标记.
        # 必须兜底: worker 的 workflow except CancelledError 分支不写 scan_end / 不更新
        # session(仅 try 正常完成分支调 finalize_summary)。web 不标 -> session 卡 running
        # (heartbeat stale 后误显 interrupted, 非 cancelled) + _watch 等不到 scan_end 永不
        # 退出 -> _handles 占死 -> max_concurrent 槽位泄漏、新扫描起不来。标终态则
        # _status_of 终态优先显 cancelled + _watch 见 scan_end 退出释放槽位(对齐 ②/③ 轨).
        handle = self._handles.get(scan_key)
        if handle is not None:
            try:
                await handle.cancel()
            except Exception:
                pass  # best-effort; temporal 侧 workflow cancel
            await self._mark_cancelled(scan_dir)
            return {"cancelled": scan_id}
        # ②/③ owner=host 或已死:标 cancelled(③ 不写协作式信号)
        if is_scan_recently_active(scan_dir):
            (scan_dir / "cancel.requested").write_text("", encoding="utf-8")
            await self._mark_cancelled(scan_dir)
            return {"cancelled": scan_id, "via": "signal"}
        await self._mark_cancelled(scan_dir)
        return {"cancelled": scan_id, "was_dead": True}

    async def delete(self, ws: str, scan_id: str) -> dict | None:
        """删除单个 scan（真删目录，spec §5.1 DELETE）。

        running scan -> ScanRunning（端点转 409，先 cancel 再删，避免删在跑 workflow 的目录致
        _watch describe 不存在的 workflow / max_concurrent 槽位泄漏）；不存在 -> None（端点 404）。
        删除范围由 ScanStore.delete_scan 决定（源①整删 scans/<id>/，源② legacy 根仅删产物保 ws 壳）。
        """
        scan_dir = self._store.get_scan_dir(ws, scan_id)
        if scan_dir is None:
            return None
        mgr = SessionManager(scan_dir.parent)
        if _compute_status(scan_dir, mgr.get_status(scan_dir)) == "running":
            raise ScanRunning(scan_id)
        # 防御：清残留登记（非 running 时 _watch finally 通常已清，此处兜底防异常路径泄漏）。
        scan_key = (ws, scan_id)
        self._handles.pop(scan_key, None)
        self._tasks.pop(scan_key, None)
        self._active_reqs.pop(scan_key, None)
        self._orchestrator_tasks.pop(scan_key, None)  # 组合接力 task（已 self-pop，兜底）
        self._store.delete_scan(ws, scan_id)
        return {"deleted": scan_id}

    def _mark_owner(self, scan_dir: Path, owner: str) -> None:
        """标 scan session.json owner(web/host)。best-effort:子进程 MetricsTracker 用
        dict(existing) 浅拷贝保留 top-level key。owner 只服务 cancel 分轨诊断。"""
        session_file = scan_dir / "session.json"
        try:
            data = json.loads(session_file.read_text("utf-8")) if session_file.exists() else {}
            if not isinstance(data, dict):
                data = {}
            data["owner"] = owner
            session_file.write_text(json.dumps(data), encoding="utf-8")
        except (OSError, ValueError):
            pass

    def _mark_submitted_at(self, scan_dir: Path) -> None:
        """提交成功后写 scan session.json submitted_at(scan_liveness 提交宽限门锚点, 防冷启动误杀).

        best-effort read-modify-write(与 _mark_owner 同模式); 仅 _submit_whitebox 提交成功后调.
        每次 start_workflow 提交刷新 -> resume 场景也准确(resume 时 created_at 是原始建 scan 时间,
        不能用作「最近提交」锚点).
        """
        session_file = scan_dir / "session.json"
        try:
            data = json.loads(session_file.read_text("utf-8")) if session_file.exists() else {}
            if not isinstance(data, dict):
                data = {}
            data["submitted_at"] = time.time()
            session_file.write_text(json.dumps(data), encoding="utf-8")
        except (OSError, ValueError):
            pass

    async def _mark_cancelled(self, scan_dir: Path) -> None:
        """标 session.status=cancelled + completed_at + 写 scan_end(若无)。让 _status_of
        因终态优先立即返 cancelled(列表/详情翻转,Delete 立即可用)。"""
        event_file = scan_dir / "events.ndjson"
        if not self._has_scan_end(event_file):
            await self._write_scan_end(event_file, "cancelled", -1, "用户取消(web Cancel)")
        try:
            SessionManager(scan_dir.parent).update_session(
                scan_dir, {"status": "cancelled", "completed_at": time.time()})
        except Exception:  # noqa: BLE001 - 标记是 best-effort,不阻塞 cancel
            pass

    async def _mark_submission_failed(
        self, scan_dir: Path, event_file: Path, error: BaseException
    ) -> None:
        """Finalize a scan whose directory exists but whose workflow was not submitted."""
        try:
            if self._has_scan_end(event_file):
                SessionManager(scan_dir.parent).update_session(
                    scan_dir, {"status": "failed", "completed_at": time.time()})
            else:
                await self._write_scan_end(
                    event_file, "failed", -1, str(error),
                    session_status="failed", scan_dir=scan_dir)
        except Exception:  # noqa: BLE001 - cleanup must not hide the original submit error
            _log.exception("failed to finalize scan submission failure: %s", scan_dir)

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
        # 关联仓库优先：命中 linked_repos.json → 返回其存储路径（无 state 校验，关联仓库
        # 无 clone 状态；多 ws 可共享同一路径）。name 与私有克隆不重名（link 时禁碰撞）。
        linked = resolve_linked_repo_path(self._workspaces_dir, ws, name)
        if linked is not None:
            return linked
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

    async def _watch(self, scan_key: tuple[str, str], event_file: Path,
                     scan_dir: Path) -> None:
        """C1: tail events.ndjson 直到 scan_end(worker finalize_summary 写)或超时.

        T3: scan_key=(ws, scan_id) 用于 _handles 查询；scan_dir 用于 session-status 同步。
        session-status 同步:周期 handle.describe() 轮询 workflow 状态,发现 FAILED /
        TIMED_OUT / TERMINATED 时(worker 进程崩溃/容器死/被 terminate,workflow except
        跑不到)自行写 scan_end + session.status=failed.
        """
        from temporalio.client import WorkflowExecutionStatus
        # describe() 见到的终态集合 -> 标 failed
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
                    handle = self._handles.get(scan_key)
                    if handle is not None:
                        try:
                            desc = await handle.describe()
                            if desc.status in _FAILED_STATES:
                                await self._write_scan_end(
                                    event_file, "failed", -1,
                                    f"workflow {desc.status.name}",
                                    session_status="failed", scan_dir=scan_dir,
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
            self._handles.pop(scan_key, None)
            self._tasks.pop(scan_key, None)
            self._active_reqs.pop(scan_key, None)

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
                              scan_dir: Path | None = None) -> None:
        payload = {
            "ts": _now_iso(), "category": "CONTROL", "type": "scan_end",
            "status": status, "returncode": returncode, "stderr_tail": stderr_tail,
        }
        # session-status 同步:先写 session.json 再写 events.ndjson -- 若 session 写失败,
        # events 不写(_watch 下个 describe tick 重试);若 session 成功而 events 失败,
        # session 已是终态(failed),finally 的 crashed 兜底补 events(scan_end)。
        # (顺序反过来会让 events 有 scan_end 而 session 永远 running = 重现 ghost-scan)
        if session_status and scan_dir is not None:
            SessionManager(scan_dir.parent).update_session(
                scan_dir, {"status": session_status, "completed_at": time.time()})
        async with aiofiles.open(event_file, "a") as fh:
            await fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _strip_trailing_scan_end(event_file: Path) -> None:
        """剥掉 events.ndjson 末尾的 scan_end 行（resume 续跑前清障，让 _watch 能 tail 新 workflow）。

        中断/崩溃时 orphan_reconciler/_watch 在末尾写了 scan_end；resume 续跑后 _watch 的
        _has_scan_end 会因旧 scan_end 立即返 True 而退出，无法跟踪新 workflow。故续跑前移除
        末尾连续的 scan_end 行（保留之前的事件历史）。文件不存在则 no-op。
        """
        if not event_file.exists():
            return
        try:
            lines = event_file.read_text("utf-8", errors="replace").splitlines()
        except OSError:
            return
        while lines:
            try:
                if json.loads(lines[-1]).get("type") == "scan_end":
                    lines.pop()
                    continue
            except (json.JSONDecodeError, ValueError):
                pass
            break
        event_file.write_text(
            ("\n".join(lines) + "\n") if lines else "", encoding="utf-8")

    # ── 组合扫描接力编排（spec §7，Task 4）───────────────────────────────────

    async def _cancel_combined(self, ws: str, scan_id: str, scan_dir: Path,
                               scan_key: tuple[str, str]) -> dict:
        """组合扫描 cancel（spec §11.5）：按 bb_phase/bb_rerun_attempts 算 workflow_id →
        re-attach + handle.cancel()；orchestrator task 一并取消 + 标终态 cancelled。

        workflow_id 算法（与 resume / _reconcile_combined_scan 同口径）：
        - running + rerun=0 → {ws}-{scan_id}-bb；
        - running + rerun=N>0 → {ws}-{scan_id}-bb-rerun-N；
        - 其他（pending/precheck）→ {ws}-{scan_id}（白盒）。

        orchestrator task（_orchestrator_tasks 登记）取消：防后台接力继续 submit 黑盒 / 写
        scan_end 与 _mark_cancelled 终态竞争。_handles 同步清（白盒 handle 残留 running 阶段）。
        Temporal 连接/cancel best-effort（不可达仍标 cancelled——对齐既有 cancel ③ 轨语义）。
        """
        data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
        bb_phase = data.get("bb_phase")
        bb_rerun = int(data.get("bb_rerun_attempts") or 0)
        # 算 workflow_id（与 resume running 分支同算法）
        if bb_phase == "running":
            suffix = f"-bb-rerun-{bb_rerun}" if bb_rerun > 0 else "-bb"
        else:
            suffix = ""
        wf_id = self._resolve_workflow_id(ws, scan_id) + suffix
        # 取消编排 task（fire-and-forget，不再 submit 黑盒 / 不与终态竞争）
        orch = self._orchestrator_tasks.pop(scan_key, None)
        if orch is not None and not orch.done():
            orch.cancel()
        # re-attach + handle.cancel()（best-effort；Temporal 不可达不阻断标终态）
        try:
            client = await Client.connect(self._temporal_address())
            handle = client.get_workflow_handle(wf_id)
            await handle.cancel()
        except Exception:  # noqa: BLE001 - best-effort; temporal 不可达仍标 cancelled
            pass
        # 清残留 handle 登记（白盒 handle 在 running 阶段仍挂 _handles；对齐 delete 清理）
        self._handles.pop(scan_key, None)
        await self._mark_cancelled(scan_dir)
        return {"cancelled": scan_id}

    async def _combined_orchestrator(self, scan_key: tuple[str, str], wb_handle: Any,
                                     scan_dir: Path, req: ScanRequest) -> None:
        """组合接力编排（spec §7.2）：await 白盒完成 → _run_blackbox_phase；try/except/finally
        统一经 _ensure_scan_end（幂等）收尾。

        ws 取自 scan_key[0]（修原 bug #4：曾误用 scan_dir.parent.name 致跨 ws 错配；scan_dir
        在 scans/<id>/ 下，parent.name 是 scan_id 非 ws）。fire-and-forget（start 起
        asyncio.create_task）；finally 自 pop _orchestrator_tasks。

        scan_end 不变量（spec §7.4）：成功路径黑盒 finalize 已写 scan_end → _ensure_scan_end
        no-op；异常/跳过/提交失败 → _ensure_scan_end 补写防 _watch 永久 tail。绝不在此裸调
        _write_scan_end（原草案 bug：成功路径写了第二条 scan_end）。
        """
        ws, scan_id = scan_key
        final_status = "completed"
        try:
            await wb_handle.result()
            await self._run_blackbox_phase(
                scan_dir, ws, scan_id, self._snapshot_auth_ref(req))
        except Exception as exc:
            final_status = "failed"
            # 接力任意阶段失败（白盒 result 抛 / 预检后黑盒提交抛 / 黑盒 result 抛 / 报告生成抛）
            # → 标 bb_phase=failed + 原因；白盒报告（已落盘）保留。
            await self._mark_bb(scan_dir, "failed", str(exc))
        finally:
            # 幂等收尾：成功路径黑盒已写 scan_end → no-op；异常/跳过 → 补写。
            await self._ensure_scan_end(scan_dir, status=final_status)
            self._orchestrator_tasks.pop(scan_key, None)

    async def _combined_report_orchestrator(self, scan_key: tuple[str, str],
                                            bb_handle: Any, scan_dir: Path) -> None:
        """仅做报告的组合编排（spec §11.4，resume bb_phase=running 分支）。

        黑盒 workflow 已在跑（resume 前 submit 过，接力已发生），scan_manager 进程崩溃后
        resume re-attach 了黑盒 handle。本 task 镜像 _combined_orchestrator 的「await handle →
        生成报告」尾段，但不重 submit 黑盒（接力已完成 submit 阶段）：

        await bb_handle.result() → _generate_combined_report → _mark_bb(completed)；
        try/except/finally 经 _ensure_scan_end（幂等）收尾（与 _combined_orchestrator 同构）。
        """
        final_status = "completed"
        try:
            await bb_handle.result()
            await self._generate_combined_report(scan_dir)
            await self._mark_bb(scan_dir, "completed")
        except Exception as exc:
            final_status = "failed"
            await self._mark_bb(scan_dir, "failed", str(exc))
        finally:
            await self._ensure_scan_end(scan_dir, status=final_status)
            self._orchestrator_tasks.pop(scan_key, None)

    def _build_combined_resume_req(self, data: dict, ws: str) -> ScanRequest:
        """从 session data 重建组合扫描 ScanRequest（resume 编排 task 用）。

        编排 task 读 req 的 url + _snapshot_auth_ref（auth 引用）。url 优先 bb_url（组合目标），
        回落 web_url。auth 字段从 bb_auth_ref 对称重建（_snapshot_auth_ref 的逆），保证
        _run_blackbox_phase 拿到与 start 时一致的 auth_ref dict。

        type=whitebox + url → _whitebox_combined_optional 校验组合模式认证字段互斥（与 start 同）。
        """
        url = data.get("bb_url") or data.get("web_url")
        req_kwargs: dict = dict(type="whitebox", url=url, workspace=ws)
        auth_ref = data.get("bb_auth_ref") or {}
        pid = auth_ref.get("profile_id")
        if pid:
            req_kwargs["auth_profile_id"] = pid
            if auth_ref.get("cred_id"):
                req_kwargs["auth_credential_id"] = auth_ref["cred_id"]
            if auth_ref.get("cred_ids"):
                req_kwargs["auth_credential_ids"] = auth_ref["cred_ids"]
        return ScanRequest(**req_kwargs)

    async def _run_blackbox_phase(self, scan_dir: Path, ws: str, scan_id: str,
                                  auth_ref: dict, run_id: str,
                                  workflow_id_suffix: str = "-bb-1") -> None:
        """组合接力公共段（spec §7.3）：预检白盒产物 → 复用 _submit_blackbox（suffix 后缀）→
        等黑盒 → 融合报告。

        单目录接力的核心：repo_path/event_file/config_path 全指回白盒 scan_dir，黑盒产物落
        scan_dir/deliverables/blackbox/（黑盒 workflow 零代码改动，spec §5）。本方法**不直接
        写 scan_end**——收尾交 _combined_orchestrator / _rerun_blackbox_orchestrator 的
        _ensure_scan_end（幂等）。

        workflow_id_suffix（Task 7 扩展）：默认 "-bb"（Task 4 首跑，零回归）；rerun_blackbox
        传 "-bb-rerun-N"（D5 续跑，每次起新黑盒 workflow id）。透传给 _submit_blackbox。

        _generate_combined_report 由 Task 8 实现；本任务仅保留调用点（单测 mock 之）。
        """
        # 预检白盒产物（recon_deliverable.md + 至少一个非空 queue）。不全 → 跳过黑盒。
        if not self._whitebox_deliverables_ready(scan_dir):
            await self._mark_run(scan_dir, run_id, "skipped",
                                 reason="白盒无可利用产物", status="skipped")
            return
        # 读 session 取黑盒目标 URL + HOST 映射（start 组合分支 dump scan-config.yaml
        # + 写 bb_url/bb_host_mappings 到 session）。bb_url 缺失回落 web_url。
        session = SessionManager(scan_dir.parent).get_session_data(scan_dir)
        bb_url = session.get("bb_url") or session.get("web_url") or ""
        host_mappings = self._session_host_mappings(session)
        # 按白盒发现的非空 queue vuln 类补 expected_agents.blackbox（黑盒只 exploit 白盒发现的类，
        # spec §9.5）：进度分母动态化，收起态百分比更准。
        bb_expected = self._count_nonempty_queues(scan_dir)
        if bb_expected > 0:
            expected = dict(session.get("expected_agents") or {})
            expected["blackbox"] = bb_expected
            try:
                SessionManager(scan_dir.parent).update_session(
                    scan_dir, {"expected_agents": expected})
            except Exception:  # noqa: BLE001 - best-effort 进度分母，不阻塞接力
                pass
        # event_file 指 run 子目录（黑盒从 event_file.parent 推 workspace_path → 产物落
        # run-K/deliverables/blackbox/，spec §4）；repo_path/config_path 仍指白盒任务根
        # （黑盒读 deliverables/whitebox/ queue）。
        run_dir = blackbox_run_dir(scan_dir, run_id)
        bb_handle = await self._submit_blackbox(
            repo_path=str(scan_dir), ws=ws, scan_id=scan_id, scan_dir=scan_dir,
            event_file=run_dir / "events.ndjson", web_url=bb_url,
            config_path=str(scan_dir / "scan-config.yaml"),
            host_mappings=host_mappings, workflow_id_suffix=workflow_id_suffix)
        await self._mark_run(scan_dir, run_id, "running", status="running")
        await bb_handle.result()
        await self._generate_combined_report(scan_dir, run_id)  # → combined/run-K/
        await self._mark_run(scan_dir, run_id, "completed", status="completed")

    async def rerun_blackbox(self, ws: str, scan_id: str,
                             new_auth: ScanRequest | None = None) -> None:
        """黑盒 failed 后换认证续跑（spec §11.3 / D5）：复用白盒产物，起新黑盒 workflow
        ``{ws}-{scan_id}-bb-rerun-{N}``（N 每次 +1）。

        前置守卫（零回归）：
        - scan 存在；session.combined 真且 bb_phase=="failed"——仅失败的组合黑盒可续跑。
        - 白盒产物完好（_whitebox_deliverables_ready）。

        流程：
        1. new_auth 非空 → 重 dump scan-config.yaml（_dump_auth_config）+ 更新 session.bb_auth_ref
           （D2：只存 profile_id 引用）。new_auth 为空则沿用原 scan-config.yaml。
        2. _run_precheck 预验证（新）认证——fail → _mark_bb(failed, auth_failed) + return。
        3. bb_rerun_attempts +=1 → 持久化 + bb_phase=running。
        4. suffix="-bb-rerun-{N}" → fire-and-forget _rerun_blackbox_orchestrator（登记
           _orchestrator_tasks，与 _combined_orchestrator 同构：try/except/finally + 幂等
           _ensure_scan_end）。

        scan_end 不变量：_rerun_blackbox_orchestrator 的 _ensure_scan_end 幂等（成功路径黑盒
        finalize 已写 → no-op；异常 → 补写），不重复写。
        """
        scan_dir = self._store.get_scan_dir(ws, scan_id)
        if scan_dir is None:
            raise ValueError("scan 不存在")
        mgr = SessionManager(scan_dir.parent)
        data = mgr.get_session_data(scan_dir)
        if not data.get("combined") or data.get("bb_phase") != "failed":
            raise ValueError("仅黑盒 failed 的组合扫描可续跑")
        if not self._whitebox_deliverables_ready(scan_dir):
            raise ValueError("白盒产物需完好才能续跑黑盒")
        # 换认证：重 dump scan-config.yaml + 更新 bb_auth_ref（D2：只存引用）。
        if new_auth:
            await self._dump_auth_config(new_auth, ws, scan_dir)
            mgr.update_session(scan_dir, {
                "bb_auth_ref": self._snapshot_auth_ref(new_auth)})
        # 预验证（新）认证——fail → 标 auth_failed + return（不起黑盒）。
        cfg = scan_dir / "scan-config.yaml"
        config_path = str(cfg) if cfg.exists() else None
        bb_url = data.get("bb_url") or data.get("web_url") or ""
        if not await self._run_precheck(
            scan_dir, ws, scan_id, bb_url, config_path,
            host_mappings=self._session_host_mappings(data)):
            await self._mark_bb(scan_dir, "failed", "auth_failed")
            return
        # bb_rerun_attempts 递增 + bb_phase=running。
        bb_rer_attempts = int(data.get("bb_rerun_attempts") or 0) + 1
        mgr.update_session(scan_dir, {
            "bb_rerun_attempts": bb_rer_attempts, "bb_phase": "running"})
        suffix = f"-bb-rerun-{bb_rer_attempts}"
        # auth_ref 从 session 读（new_auth 时上面已更新；否则原值）。
        auth_ref = mgr.get_session_data(scan_dir).get("bb_auth_ref") or {"profile_id": None}
        scan_key = (ws, scan_id)
        self._orchestrator_tasks[scan_key] = asyncio.create_task(
            self._rerun_blackbox_orchestrator(
                scan_key, scan_dir, ws, scan_id, auth_ref, suffix))

    async def _rerun_blackbox_orchestrator(self, scan_key: tuple[str, str],
                                           scan_dir: Path, ws: str, scan_id: str,
                                           auth_ref: dict, suffix: str) -> None:
        """黑盒续跑编排（spec §11.3，与 _combined_orchestrator 同构）：调 _run_blackbox_phase
        （传 rerun suffix）→ try/except/finally 经 _ensure_scan_end（幂等）收尾。

        成功路径黑盒 finalize 已写 scan_end → _ensure_scan_end no-op（不写第二条，核心不变量）；
        异常 / 提交失败 → 补写 scan_end 防 _watch 永久 tail。finally 自 pop _orchestrator_tasks。
        """
        final_status = "completed"
        try:
            await self._run_blackbox_phase(
                scan_dir, ws, scan_id, auth_ref, workflow_id_suffix=suffix)
        except Exception as exc:
            final_status = "failed"
            await self._mark_bb(scan_dir, "failed", str(exc))
        finally:
            await self._ensure_scan_end(scan_dir, status=final_status)
            self._orchestrator_tasks.pop(scan_key, None)

    async def _rerun_orchestrator(self, scan_key: tuple[str, str], scan_dir: Path,
                                  ws: str, scan_id: str, auth_ref: dict,
                                  run_id: str, suffix: str) -> None:
        """run 版编排（spec §7.3，与 _rerun_blackbox_orchestrator 同构）：调
        ``_run_blackbox_phase(run_id, suffix)`` → try/except/finally 经幂等
        ``_ensure_scan_end`` 收尾。finally 自 pop ``_orchestrator_tasks``。

        成功路径黑盒 finalize 已写 scan_end → ``_ensure_scan_end`` no-op；异常/提交失败
        → 补写 scan_end 防 _watch 永久 tail。
        """
        final_status = "completed"
        try:
            await self._run_blackbox_phase(
                scan_dir, ws, scan_id, auth_ref, run_id, workflow_id_suffix=suffix)
        except Exception as exc:
            final_status = "failed"
            await self._mark_run(scan_dir, run_id, "failed",
                                 reason=str(exc), status="failed")
        finally:
            await self._ensure_scan_end(scan_dir, status=final_status)
            self._orchestrator_tasks.pop(scan_key, None)

    async def _add_blackbox_run(self, ws: str, wb_scan_id: str,
                                req: ScanRequest | None = None) -> str:
        """给已有白盒任务加一个黑盒 run（spec §6/§7.1 #8 手动入口）。

        流程：白盒产物就绪检查 → （req 给了认证则重 dump scan-config.yaml + 更新 bb_url/
        bb_auth_ref）→ 串行分配 run-K（_create_scan_lock）→ 有认证则 _run_precheck（fail →
        run 标 failed + 收尾，不起黑盒）→ fire-and-forget ``_rerun_orchestrator(run_id,
        -bb-{K})``。返回 run_id。

        req=None（沿用现盘 scan-config.yaml）：无 scan-config.yaml → 公开目标，跳过预验证。
        """
        scan_dir = self._store.get_scan_dir(ws, wb_scan_id)
        if scan_dir is None:
            raise ValueError("白盒任务不存在")
        if not self._whitebox_deliverables_ready(scan_dir):
            raise ValueError("白盒产物未就绪，不能加黑盒")
        mgr = SessionManager(scan_dir.parent)
        data = mgr.get_session_data(scan_dir)
        cfg = scan_dir / "scan-config.yaml"
        if req is not None:
            await self._dump_auth_config(req, ws, scan_dir)
            bb_url = req.url or data.get("bb_url") or data.get("web_url") or ""
            mgr.update_session(scan_dir, {
                "bb_url": bb_url,
                "bb_auth_ref": self._snapshot_auth_ref(req),
            })
            data = mgr.get_session_data(scan_dir)  # 刷新（bb_url/bb_auth_ref 已写）
        config_path = str(cfg) if cfg.exists() else None
        bb_url = data.get("bb_url") or data.get("web_url") or ""
        auth_ref = data.get("bb_auth_ref") or {"profile_id": None}
        # 序号分配须串行（与 create_scan 同 lock 口径，防并发同序号）。
        async with self._create_scan_lock:
            run_id, _run_dir = self._store.create_blackbox_run(
                ws, wb_scan_id, auth_ref=auth_ref)
        k = int(run_id.split("-")[1])
        if config_path and not await self._run_precheck(
                scan_dir, ws, wb_scan_id, bb_url, config_path,
                host_mappings=self._session_host_mappings(data)):
            await self._mark_run(scan_dir, run_id, "failed",
                                 reason="auth_failed", status="failed")
            await self._ensure_scan_end(scan_dir, status="failed")
            return run_id
        scan_key = (ws, wb_scan_id)
        self._orchestrator_tasks[scan_key] = asyncio.create_task(
            self._rerun_orchestrator(
                scan_key, scan_dir, ws, wb_scan_id, auth_ref, run_id, f"-bb-{k}"))
        return run_id

    async def _run_precheck(self, scan_dir: Path, ws: str, scan_id: str,
                            web_url: str, config_path: str | None,
                            host_mappings: dict[str, str] | None = None) -> bool:
        """D4 t0 预验证：复用 AuthValidationWorkflow 登一次目标站，pass 返 True。

        event_file 用独立文件 authcheck-events.ndjson（不写主 events——预验证 workflow finalize
        可能写 scan_end，混入主 events 流会提前触发 _watch 退出）。黑盒 workflow 零代码改动，
        起一个独立的 AuthValidationWorkflow（与认证管理页「测试登录」探针同源）。

        config_path=None（公开目标无认证）→ 跳过预验证直接 pass（无登录可验）。
        """
        if not config_path:
            return True  # 公开目标无认证 → 无需预验证
        from supernova_blackbox.pipeline.workflows import AuthValidationWorkflow
        from supernova_blackbox.pipeline.shared import BlackboxAuthValidationInput
        from supernova_core.services.temporal_infra import WEB_TASK_QUEUE_BLACKBOX
        client = await Client.connect(self._temporal_address())
        inp = BlackboxAuthValidationInput(
            web_url=web_url, config_path=config_path,
            workspace_path=str(scan_dir),
            event_file=str(scan_dir / "authcheck-events.ndjson"),  # 独立 events
            api_key=self._resolve_provider_config(ws).get("api_key"),
            host_mappings=host_mappings or {})
        handle = await client.start_workflow(
            AuthValidationWorkflow.run, inp,
            id=f"{ws}-{scan_id}-authcheck", task_queue=WEB_TASK_QUEUE_BLACKBOX)
        result = await handle.result()  # AuthValidationResult
        return bool(result and getattr(result, "success", False))

    async def _generate_combined_report(self, scan_dir: Path, run_id: str) -> None:
        """生成 per-run 融合报告（spec §9/§10.2）→ combined/run-K/combined_report.md。

        按 vuln_class（injection/xss/ssrf/authz）交叉白盒 queue + 黑盒 verdicts。
        白盒根 = scan_dir/deliverables/whitebox/，黑盒根 = run 子目录 deliverables/blackbox/，
        输出根 = scan_dir/combined/{run_id}/。实现在 ``combined_report_renderer``；本 async
        入口仅包装（文件读 inline，无须 offload），供 ``_run_blackbox_phase`` / reconcile
        在黑盒完成后 await。
        """
        from supernova_web.components.combined_report_renderer import (
            render_combined_report)
        run_dir = blackbox_run_dir(scan_dir, run_id)
        render_combined_report(
            whitebox_root=whitebox_dir(scan_dir / "deliverables"),
            blackbox_root=blackbox_dir(run_dir / "deliverables"),
            out_dir=combined_run_dir(scan_dir, run_id))

    async def _ensure_scan_end(self, scan_dir: Path, status: str = "completed") -> None:
        """幂等收尾（spec §7.4，修原 bug）：events 无 scan_end 才补写。

        成功路径黑盒 finalize 已写 scan_end → no-op（**不写第二条**，核心不变量）；
        异常/跳过/提交失败 → 补写 scan_end 防 _watch 永久 tail（_watch 纯 tail 认 scan_end 退出）。
        _watch 的 finally 兜底（"worker 未写 scan_end" 补写）仍作最后一道防线。
        """
        if self._has_scan_end(scan_dir / "events.ndjson"):
            return
        session_status = status if status in {"completed", "failed", "cancelled", "crashed", "timeout"} else None
        await self._write_scan_end(
            scan_dir / "events.ndjson", status, 0, f"combined {status}",
            session_status=session_status, scan_dir=scan_dir)

    def _whitebox_deliverables_ready(self, scan_dir: Path) -> bool:
        """预检白盒产物可被黑盒利用（spec §7.3）：

        True iff deliverables/whitebox/recon_deliverable.md 存在 AND 至少一个非空
        {vt}_exploitation_queue.json（vulnerabilities 列表非空）。黑盒只 exploit 白盒发现的
        类，缺其一则跳过黑盒（bb_phase=skipped）。
        """
        wb_dir = scan_dir / "deliverables" / "whitebox"
        if not (wb_dir / "recon_deliverable.md").is_file():
            return False
        return self._count_nonempty_queues(scan_dir) > 0

    def _count_nonempty_queues(self, scan_dir: Path) -> int:
        """数 deliverables/whitebox/ 下非空的 {vt}_exploitation_queue.json 数（vulnerabilities
        列表非空）。用于预检 + expected_agents.blackbox 分母（spec §9.5）。"""
        wb_dir = scan_dir / "deliverables" / "whitebox"
        if not wb_dir.is_dir():
            return 0
        n = 0
        for qf in wb_dir.glob("*_exploitation_queue.json"):
            try:
                data = json.loads(qf.read_text("utf-8", errors="replace"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(data, dict) and isinstance(data.get("vulnerabilities"), list) \
                    and len(data["vulnerabilities"]) > 0:
                n += 1
        return n

    async def _mark_bb(self, scan_dir: Path, phase: str,
                       reason: str | None = None) -> None:
        """写 session.json 的 bb_phase（+ bb_reason）。phase 取自
        {precheck, pending, running, completed, failed, skipped}（spec §9.2 分段）。

        best-effort（对齐 _mark_owner / _mark_submitted_at 的 read-modify-write 模式，
        经 SessionManager.update_session 合并，不覆盖其他 top-level key）。标记失败不阻塞接力。
        """
        data: dict = {"bb_phase": phase}
        if reason is not None:
            data["bb_reason"] = reason
        try:
            SessionManager(scan_dir.parent).update_session(scan_dir, data)
        except Exception:  # noqa: BLE001 - 标记 best-effort，不阻塞接力
            pass

    async def _mark_run(self, scan_dir: Path, run_id: str, phase: str,
                        reason: str | None = None,
                        status: str | None = None) -> None:
        """run 级 phase 写入（spec §5.2/§5.3，取代 _mark_bb 对 run 的调用）：
        写 run 级 session（bb_phase/bb_reason/status）+ 任务 bb_runs[] 条目 + latest_bb_run。

        best-effort（对齐 _mark_bb）：经 ``_store.update_blackbox_run`` 合并，标记失败不阻塞
        接力。终态 status（completed/failed/skipped）顺带写 completed_at 时间戳。
        """
        try:
            self._store.update_blackbox_run(
                self._ws_of(scan_dir), self._scan_id_of(scan_dir), run_id,
                status=status, phase=phase, reason=reason,
                completed_at=_now_iso() if status in ("completed", "failed", "skipped",
                                                      "cancelled") else None)
        except Exception:  # noqa: BLE001 - best-effort，不阻塞接力
            pass

    @staticmethod
    def _ws_of(scan_dir: Path) -> str:
        """从 scan_dir 派生 ws：<workspaces>/<ws>/scans/<scan_id> → <ws>。"""
        return scan_dir.parent.parent.name

    @staticmethod
    def _scan_id_of(scan_dir: Path) -> str:
        """从 scan_dir 派生 scan_id：scans/<scan_id> → <scan_id>。"""
        return scan_dir.name

    # ── 组合扫描崩溃恢复（spec §7.5，Task 5）────────────────────────────────

    async def _query_workflow_status(self, workflow_id: str) -> str | None:
        """查 temporal workflow 执行状态，返状态名小写（'running'/'completed'/…）或 None。

        None = 查询超时 / workflow 不存在 / temporal 不可达（best-effort，异常绝不阻塞
        reconcile）。对齐 orphan_reconciler._workflow_still_running 的 describe() 查询模式，
        但返完整状态名供 _reconcile_combined_scan 分支决策（completed vs running vs not-found）。
        """
        timeout = float(
            os.environ.get("SUPERNOVA_RECONCILE_TEMPORAL_TIMEOUT_SECONDS", "5"))

        async def _probe() -> str | None:
            client = await Client.connect(self._temporal_address())
            desc = await client.get_workflow_handle(workflow_id).describe()
            return desc.status.name.lower()  # RUNNING→"running", COMPLETED→"completed"

        try:
            return await asyncio.wait_for(_probe(), timeout=timeout)
        except Exception:  # noqa: BLE001 - 超时/断连/workflow 不存在 → None
            return None

    async def _reconcile_combined_scan(self, scan_dir: Path) -> None:
        """进程重启后对组合扫描（combined=true）按 bb_phase 补接力/补报告/补 scan_end
        （spec §7.5 崩溃恢复）。

        组合接力是 scan_manager 进程内 asyncio task（非 Temporal durable），容器重启后
        _combined_orchestrator 协程丢失 → session 卡 running 且 events 无 scan_end。本方法
        在 orphan_reconciler 的 per-scan 恢复后被调用，按 bb_phase 分支恢复：

        - precheck + authcheck workflow COMPLETED + 白盒 COMPLETED → 补 _run_blackbox_phase。
        - pending + 白盒 workflow COMPLETED → 补 _run_blackbox_phase（接力）。
        - running + 黑盒 workflow COMPLETED → 补 _generate_combined_report + _mark_bb(completed)。
        - 任意 bb_phase + workflow 仍 RUNNING → 不干预（让 temporal 自然完成）。
        - 任意 bb_phase + workflow 不活跃 + events 无 scan_end → _ensure_scan_end 补写。

        **非组合扫描立即返回（零回归）**——纯白盒/纯黑盒恢复路径不受影响。

        ws/scan_id 从 scan_dir 路径派生（<workspaces>/<ws>/scans/<scan_id>），与
        orphan_reconciler._workflow_id_from_scan_dir 同口径（不用 scan_dir.parent.name——
        那是 "scans" 非 ws，是原 bug #4 的根因）。
        """
        # 派生 ws/scan_id（与 _workflow_id_from_scan_dir 同口径，守 bug #4）
        if scan_dir.parent.name != "scans":
            return  # legacy 根 scan / 辅助目录 → 无可靠 ws/scan_id
        ws = scan_dir.parent.parent.name
        scan_id = scan_dir.name

        session_file = scan_dir / "session.json"
        if not session_file.exists():
            return
        try:
            data = json.loads(session_file.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not data.get("combined"):
            return  # 非组合 → 零回归（纯白盒/纯黑盒恢复路径不受影响）

        bb_phase = data.get("bb_phase")
        auth_ref = data.get("bb_auth_ref") or {}
        bb_rerun = int(data.get("bb_rerun_attempts") or 0)
        wf_active = False  # 某分支发现 workflow 仍 RUNNING → True（跳过 scan_end 补写）

        try:
            if bb_phase in ("precheck", "pending"):
                # precheck: 先确认 authcheck 已完成（仍跑 = 预验证进行中 → 不干预）。
                if bb_phase == "precheck":
                    auth_status = await self._query_workflow_status(
                        f"{ws}-{scan_id}-authcheck")
                    if auth_status == "running":
                        wf_active = True
                # 白盒 workflow 状态：completed → 补黑盒接力；running → 不干预。
                if not wf_active:
                    wb_status = await self._query_workflow_status(
                        self._resolve_workflow_id(ws, scan_id))
                    if wb_status == "running":
                        wf_active = True
                    elif wb_status == "completed":
                        # 白盒完成 → 补黑盒接力（_run_blackbox_phase 内部预检产物 +
                        # submit -bb workflow + 等结果 + 生成报告）。
                        await self._run_blackbox_phase(
                            scan_dir, ws, scan_id, auth_ref)

            elif bb_phase == "running":
                # 黑盒 workflow：首跑 -bb / 续跑 -bb-rerun-N（spec §7.6 + §11）。
                suffix = f"-bb-rerun-{bb_rerun}" if bb_rerun > 0 else "-bb"
                bb_status = await self._query_workflow_status(
                    self._resolve_workflow_id(ws, scan_id) + suffix)
                if bb_status == "running":
                    wf_active = True
                elif bb_status == "completed":
                    # 黑盒完成 → 补融合报告 + 标 completed（接力最后一步崩溃补全）。
                    await self._generate_combined_report(scan_dir)
                    await self._mark_bb(scan_dir, "completed")

            # failed/skipped/completed/None → 无需接力，只补 scan_end（fall-through）
        except Exception:  # noqa: BLE001 - reconcile 是兜底增强，绝不因单 scan 异常拖垮
            _log.exception("_reconcile_combined_scan failed for %s", scan_dir)
        finally:
            # scan_end 不变量（review fix #1）：必须在 finally——即使 reconcile 内部
            # raise（如 Task-8 _generate_combined_report NotImplementedError stub、或
            # _run_blackbox_phase 提交后抛），也要确保 events 有 scan_end，否则 scan
            # 永久卡 running（orphan_reconciler 已委托本方法、未写 interrupted 兜底）。
            # workflow 仍活跃（wf_active=True）→ 跳过（让 temporal 自然完成写 scan_end）。
            if not wf_active:
                try:
                    await self._ensure_scan_end(scan_dir)
                except Exception:  # noqa: BLE001 - finally 内 best-effort
                    _log.exception(
                        "_ensure_scan_end failed in reconcile finally for %s", scan_dir)

    def _kick_combined_reconcile(self, scan_dir: Path) -> None:
        """Fire-and-forget 组合扫描恢复（review fix #2：非阻塞，防 startup 阻塞）。

        _reconcile_combined_scan 可能调 _run_blackbox_phase → await bb_handle.result()
        阻塞整个黑盒运行（分钟~小时级）。若在 orphan_reconciler.reconcile_orphaned 里
        inline await（startup 遍历 / events 触发），会阻塞 app 启动完成 + 串行阻塞其他
        孤儿 scan 恢复。故 reconcile_orphaned 调本方法（sync、立即返回），本方法内部
        asyncio.create_task 在后台跑 _reconcile_combined_scan（对齐 start() fire
        _combined_orchestrator 的 create_task 模式）。

        幂等：同一 (ws, scan_id) 已有在途恢复 task → 不重复 fire（防 re-entry 多重
        黑盒提交）。task 完成/异常后 finally 自 pop _reconcile_tasks。
        """
        if scan_dir.parent.name != "scans":
            return
        ws = scan_dir.parent.parent.name
        scan_id = scan_dir.name
        scan_key = (ws, scan_id)
        if scan_key in self._reconcile_tasks:
            return  # 已在恢复中（幂等）

        async def _run() -> None:
            try:
                await self._reconcile_combined_scan(scan_dir)
            except Exception:  # noqa: BLE001 - background task 必须自兜底（不崩 event loop）
                _log.exception(
                    "_reconcile_combined_scan background task failed for %s", scan_dir)
            finally:
                self._reconcile_tasks.pop(scan_key, None)

        self._reconcile_tasks[scan_key] = asyncio.create_task(_run())

