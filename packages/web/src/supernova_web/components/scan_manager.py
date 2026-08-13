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
from supernova_core.runtime.workflow_timeout import workflow_run_timeout
from supernova_core.session import SessionManager
from supernova_whitebox.pipeline.workflows import WhiteboxScanWorkflow
from supernova_whitebox.pipeline.shared import PipelineInput
from supernova_web.models import ScanRequest
from .host_profile_store import fetch_and_parse_hosts
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

    # ---- 公共 API ----
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

        ScanStore.create_scan 建 scans/<scan_id>/session.json（不复位 resume；重扫=新 scan）。
        event_file=scan_dir/events.ndjson 塞进 PipelineInput（worker 据其 parent 推导 scan_dir）。
        """
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

        # 黑盒:提前解析白盒血缘作 scan_id 前缀（<wb_scan_id>~<N>）。_resolve_blackbox_inputs
        # 仍负责 config_path + repo_path（需 scan_dir，在 create_scan 后），此处只提前拿 lineage
        # 喂 _gen_scan_id；reuse 校验与 _resolve_blackbox_inputs 幂等重复，可接受。
        lineage: str | None = None
        if req.type == "blackbox":
            if not req.reuse_whitebox_scan_id:
                raise ValueError("blackbox 扫描必须复用白盒结果（reuse_whitebox_scan_id）")
            if self._store.get_scan_dir(ws, req.reuse_whitebox_scan_id) is None:
                raise ValueError(f"要复用的白盒扫描不存在: {req.reuse_whitebox_scan_id}")
            lineage = req.reuse_whitebox_scan_id

        # 黑盒 create_scan 在锁内，保证 ~<N> 序号分配原子（防并发同白盒争同序号）；
        # 白盒无序号竞态，不加锁。
        if req.type == "blackbox":
            async with self._create_scan_lock:
                scan_id, scan_dir = self._store.create_scan(
                    ws, req.url or "", target or "", req.type, lineage=lineage)
        else:
            # T3: ScanStore 建 scan_id 目录 + session.json（ws 根不再写 session.json）。
            scan_id, scan_dir = self._store.create_scan(
                ws, req.url or "", target or "", req.type)
        self._mark_owner(scan_dir, "web")
        event_file = scan_dir / "events.ndjson"
        scan_key = (ws, scan_id)
        self._active_reqs[scan_key] = req

        try:
            if req.type == "whitebox":
                handle = await self._submit_whitebox(
                    target, ws, scan_id, scan_dir, event_file, req.url or "")
                # 持久化 repo 名（source.value，可为 group/repo）供重跑预填白盒仓库--
                # repo_path 是绝对路径，前端 listRepos 返回的 Repo 无 path 字段无法反查 name。
                SessionManager(scan_dir.parent).update_session(
                    scan_dir, {"source_repo": req.source.value if req.source else None})
            elif req.type == "blackbox":
                config_path, repo_path = await self._resolve_blackbox_inputs(
                    req, ws, scan_dir, target)
                # Phase 2 HOST 档案：解析 host→IP 映射（选档案 / 填 GET 链接 / 都不填），
                # 灌入 BlackboxPipelineInput 供下游 per-scan 代理 + sandbox /etc/hosts 注入。
                host_mappings = await self._resolve_host_mappings(req, ws)
                handle = await self._submit_blackbox(
                    repo_path, ws, scan_id, scan_dir, event_file, req.url or "",
                    config_path, host_mappings=host_mappings)
                # 持久化 reuse_whitebox_scan_id 供 resume 重解析 wb_scan_dir（修 reuse resume
                # fail-fast：create_scan 第三参 target="" → session.repo_path 读空，resume 需凭
                # reuse_id 重定位白盒产物目录，不存易失的绝对路径）。
                SessionManager(scan_dir.parent).update_session(
                    scan_dir, {"reuse_whitebox_scan_id": req.reuse_whitebox_scan_id})
            else:
                raise ValueError(f"correlation 暂未 C1 化: {req.type}")
        except BaseException:
            # 提交失败: _watch 不会被调度, 必须在此清理 _active_reqs, 否则
            # active_repo_sources() 持续误报引用 -> DELETE /repos 误判 409.
            self._active_reqs.pop(scan_key, None)
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
        event_file = scan_dir / "events.ndjson"
        self._strip_trailing_scan_end(event_file)

        # 重构 ScanRequest 供 _active_reqs（active_repo_sources 判引用用）。blackbox 必须带
        # reuse_whitebox_scan_id 过 model_validator（_blackbox_requires_reuse），否则 ValidationError。
        req_kwargs: dict = dict(type=scan_type, url=web_url or None, workspace=ws)
        if scan_type == "blackbox" and reuse_whitebox_scan_id:
            req_kwargs["reuse_whitebox_scan_id"] = reuse_whitebox_scan_id
        req = ScanRequest(**req_kwargs)
        scan_key = (ws, scan_id)
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
                    repo_path or None, ws, scan_id, scan_dir, event_file, web_url, config_path)
            else:
                handle = await self._submit_whitebox(
                    repo_path, ws, scan_id, scan_dir, event_file, web_url)
        except BaseException:
            self._active_reqs.pop(scan_key, None)
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
        client = await Client.connect(self._temporal_address())
        workflow_id = self._resolve_workflow_id(ws, scan_id)
        # P3c 阶段 2：按 ws 解析 provider_config（ws_config_store 非None）或全局 env 兜底。
        provider_config = self._resolve_provider_config(ws)
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

    async def _resolve_blackbox_inputs(
        self, req: ScanRequest, ws: str, scan_dir: Path, target: str | None,
    ) -> tuple[str | None, str | None]:
        """解析黑盒提交的 config_path(登录 YAML) + repo_path(复用白盒 / 指定仓库 / standalone)。

        config_path(两互斥分支,model_validator _auth_profile_xor_inline 保证二选一):
          - req.auth_profile_id+auth_credential_id → AuthProfileStore.get 展开该角色 →
            credential_to_authentication → dump 成 {authentication: {...}} YAML 写 scan_dir/
            scan-config.yaml(明文,core 合流点 parse_config 直读 YAML 的约束)。档案/角色缺失 raise ValueError。
          - req.authentication(inline dict) → Authentication.model_validate 校验 → 同样 dump 写 YAML。
          blackbox workflow `if input.config_path:` 据此跑 run_blackbox_auth_validation。校验失败 raise ValueError。
        repo_path: req.reuse_whitebox_scan_id → 该白盒 scan_dir 作 repo_path
          （detect_whitebox_results 在 Path(repo_path)/deliverables 找白盒 queue；wb scan_dir/
          deliverables 即白盒产物落点，blackbox 自身产物靠 workspace_path 另落 bb scan_dir）；
          否则 target（req.source.repo 经 _resolve_inputs 解析）；standalone（无 reuse 无 source）→ None。
        """
        import yaml
        from supernova_core.models.config import Authentication

        config_path: str | None = None
        # 选已保存档案：三互斥子模式（model_validator _auth_profile_xor_inline 已保证互斥）：
        #   - profile_id + cred_ids[] = 子集（2026-08-06）：展开选中的 credentials → accounts[]；
        #   - profile_id + cred_id     = 单角色（旧契约，向后兼容）：展开该 credential → 单 authentication；
        #   - profile_id 单独          = 全角色（子项目2 T10）：展开所有 credentials → accounts[]。
        # 子集与全角色共用「credentials 列表 → primary + accounts[] payload」逻辑（_expand_multi_identity）。
        # 与下方 inline authentication 互斥。
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
            config_path = _expand_multi_identity(profile, selected)
        elif req.auth_profile_id and req.auth_credential_id:
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
            config_path = _dump_auth_payload(
                {"authentication": auth.model_dump(exclude_none=True, mode="json")})
        elif req.auth_profile_id:
            # 全角色模式（子项目2 T10）：展开所有 credentials → accounts[]。
            if self._auth_profile_store is None:
                raise RuntimeError("auth_profile_store 未注入，无法展开认证档案")
            profile = self._auth_profile_store.get(ws, req.auth_profile_id)
            if profile is None:
                raise ValueError(f"认证档案不存在: {req.auth_profile_id}")
            config_path = _expand_multi_identity(profile, profile.credentials)
        elif req.authentication:
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
            config_path = _dump_auth_payload(payload)

        # 黑盒 = 白盒下游 exploitation-only（阶段 2）：恒复用白盒结果，无 standalone/repo 兜底。
        # model_validator 已在请求层拦 reuse 缺失；此处工作层独立兜底（不依赖 pydantic，防 model 层被误改）。
        if not req.reuse_whitebox_scan_id:
            raise ValueError("blackbox 扫描必须复用白盒结果（reuse_whitebox_scan_id）")
        wb_scan_dir = self._store.get_scan_dir(ws, req.reuse_whitebox_scan_id)
        if wb_scan_dir is None:
            raise ValueError(f"要复用的白盒扫描不存在: {req.reuse_whitebox_scan_id}")
        repo_path: str | None = str(wb_scan_dir)
        return config_path, repo_path

    async def _resolve_host_mappings(
        self, req: ScanRequest, ws: str,
    ) -> dict[str, str]:
        """解析 HOST 档案 → {host: ip} dict（Phase 2，2026-08-12）。

        两种互斥来源（ScanRequest._host_profile_xor_url model_validator 已保证互斥）：
          - req.host_profile_id: store.get 读档;若 profile.source_url 则 best-effort refresh
            （try/except 包：refresh 内部仅吞 fetch 异常,write 失败需本层兜底,回落快照 mappings）。
            store 未注入 → RuntimeError（对齐 auth_profile_store guards）。
          - req.host_url: fetch_and_parse_hosts（扫描启动时拉取;fetch 错误自然冒泡为 scan-start 失败,
            用户给的 URL 不可达属合理 fail-fast）。
          - 都不填 → {} （不启用 HOST 代理,向后兼容,既有扫描字节不变）。

        host keys 已被 HostMapping.host field_validator 强制 strip+lowercase,与下游
        urlparse(url).hostname（小写）一致,无大小写 MISS 风险。
        """
        if req.host_profile_id:
            if self.host_profile_store is None:
                raise RuntimeError("host_profile_store 未注入，无法解析 HOST 档案")
            profile = self.host_profile_store.get(ws, req.host_profile_id)
            if profile is None:
                raise ValueError(f"HOST 档案不存在: {req.host_profile_id}")
            # source_url 档案 best-effort refresh（拉最新 /etc/hosts）;refresh 内部吞 fetch
            # 异常但未吞 write 异常,故本层再包一层 try/except,任何失败都回落快照。
            if profile.source_url:
                try:
                    refreshed = await self.host_profile_store.refresh(
                        ws, req.host_profile_id)
                    if refreshed is not None:
                        profile = refreshed
                except Exception:
                    pass  # best-effort:回落存储快照,不阻断扫描
            return {m.host: m.ip for m in profile.mappings}
        if req.host_url:
            mappings, _warnings = await fetch_and_parse_hosts(req.host_url)
            return {m.host: m.ip for m in mappings}
        return {}

    async def _submit_blackbox(
        self, repo_path: str | None, ws: str, scan_id: str, scan_dir: Path,
        event_file: Path, web_url: str, config_path: str | None,
        host_mappings: dict[str, str] | None = None,
    ) -> Any:
        """提交黑盒 scan 到 supernova-bb-web queue。参照 _submit_whitebox。

        BlackboxPipelineInput.event_file 非 None → workflow 走 worker 路径（setup_display 注入
        StructuredEventRenderer 写 events.ndjson，web live 页可见）。workspaces_root 用 web 已知
        的 self._workspaces_dir（worker 容器共享同 volume，路径一致）。
        """
        from supernova_blackbox.pipeline.shared import BlackboxPipelineInput
        from supernova_blackbox.pipeline.workflows import BlackboxScanWorkflow
        from supernova_core.services.temporal_infra import WEB_TASK_QUEUE_BLACKBOX

        client = await Client.connect(self._temporal_address())
        workflow_id = self._resolve_workflow_id(ws, scan_id)
        provider_config = self._resolve_provider_config(ws)
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

        ws_config_store 非None 时先 validate_ws_config fail-fast（非法 ai_provider 不提交），
        再 resolve_provider_config 拼「全局默认 + ws 覆盖」。None（CLI/旧测试）走全局 env。
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
