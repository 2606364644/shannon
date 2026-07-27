# Web 工作区与扫描任务解耦 - Phase 1（后端）实现计划

- **日期**：2026-07-27
- **分支**：`feat/fork-py`
- **状态**：待用户审批
- **关联**：设计见 `specs/2026-07-27-web-workspace-scan-decoupling-design.md`（下称「spec」）；前端见 `plans/2026-07-27-web-workspace-scan-decoupling-phase2.md`
- **范围**：core 零改动 + web 多 scan 数据模型/API + legacy 迁移 + shim 兼容旧前端 + 后端 TDD。旧前端靠 shim 不破。

## 前置

- 读 spec §3（存储布局）/ §4（数据模型）/ §5（API 契约）/ §8（core/worker 零改动）/ §10（不变量）。
- **core `SessionManager` 源码不改**（spec §8.1/§10.1）。web 层用 `SessionManager(ws_dir / "scans")` 复用全部 scan 读写。
- **worker 零改动**（spec §8.2）。web 把 `event_file = scan_dir / "events.ndjson"` 塞进 `PipelineInput`。
- 不碰双轨/确定性/LLM 轨（CLAUDE.md §1）。

## 任务（TDD）

### T1. scan 存储读写（`components/scan_store.py` 新文件）
- `ScanStore(workspaces_dir)`：
  - `create_scan(ws, web_url, repo_path, scan_type) -> tuple[str, Path]`：gen scan_id（`YYYYMMDD-HHMMSS`，同秒 `-2`/`-3`）；`SessionManager(ws_dir / "scans").create_workspace(name=scan_id, web_url, repo_path, scan_type=scan_type)`；返回 `(scan_id, scan_dir)`。
  - `list_scans(ws) -> list[ScanSummary]`：经 `SessionManager(ws_dir/"scans").list_workspaces()`（新源）+ 兼容 ws 根 `session.json`（legacy 源，spec §7.2 双源）合并，按 created_at 倒序；每项经 `workspaces_indexer._status_of` 归一 status。
  - `get_scan_dir(ws, scan_id) -> Path`：含路径校验（拒 `..`/`/`/空，复用 `repo_manager._validate_ws_segment` + `resolve().is_relative_to(scans_dir)`）。
  - `latest_scan(ws) -> Path | None`：active 优先，否则 max created_at。
- `ScanSummary` dataclass/pydantic（spec §4.2）。
- **测试 `tests/test_scan_store.py`**：建 scan 落 `scans/<id>/session.json`；list_scans 双源（新 scan + legacy 根 scan）合并排序；同秒碰撞 `-2`；路径校验拒越界；latest_scan 取最新/active 优先。

### T2. workspace.json 元数据 + indexer 改造
- `WorkspaceMeta` 读写：`workspaces/<ws>/workspace.json`（spec §4.1）。
- `POST /api/workspaces`（`api/workspaces.py:28`）：改写 workspace.json（替代现 minimal session.json，`api/workspaces.py:47`）；admin 自动 manager 不变。
- `WorkspacesIndexer.list_workspaces()`（`workspaces_indexer.py:86`）：改列 `workspaces/*/workspace.json`（新）+ 兼容 `workspaces/*/session.json`（legacy 无 workspace.json 的 ws）；每 ws 经 `ScanStore.list_scans` 聚合 `scan_count`/`latest_status`/`latest_created_at`（spec §5.3）。
- `GET /api/workspaces` 返回增 `scan_count`/`latest_status`/`latest_created_at`。
- **测试**：建 ws 写 workspace.json；list 含 legacy ws；scan_count/latest_status 聚合正确；空 ws（无 scan）scan_count=0 不崩。

### T3. scan_manager 改多 scan
- `scan_manager.start(req)`（`scan_manager.py:85`）：ws=req.workspace（校验不变）；`scan_id, scan_dir = ScanStore.create_scan(ws, req.url or "", target or "", req.type)`；`event_file = scan_dir / "events.ndjson"`；`_handles[(ws, scan_id)]`/`_active_reqs[(ws, scan_id)]`/`_tasks[(ws, scan_id)]`；`workflow_id = _resolve_workflow_id(ws, scan_id)`（读 `scan_dir/session.json` resumeAttempts -> `{ws}-{scan_id}` 或 `-resume-N`）；`PipelineInput(repo_path=target, web_url=req.url, workspace_name=scan_id, event_file=str(event_file), provider_config)`；返回 `(ws, scan_id)`。
- `_resolve_workflow_id(ws, scan_id)`：从 ws 根读改读 `scan_dir/session.json`。
- `cancel(ws, scan_id)`：按 `(ws, scan_id)` 取 handle.cancel + `_mark_cancelled(scan_dir)`。保留 `cancel(ws)` shim -> 对 latest/active scan（供旧 `DELETE /api/scan/{ws}`）。
- `active_repo_sources()`（`scan_manager.py:68`）：从 `_active_reqs`（值含 `req.workspace`）派生 (ws, repo) 集 -- key 变元组不影响。
- `max_concurrent` 检查：`len(self._handles) >= limit`（key 变元组不改变计数语义）。
- `_watch`、`_write_scan_end`、`_mark_owner`、`_mark_submitted_at`：`ws` 参数改 `scan_dir` 或 `(ws, scan_id)`，写 scan_dir/session.json + scan_dir/events.ndjson。
- **测试 `tests/test_scan_manager_multi.py`**：同 ws 起两个 scan 不互斥（`_handles` 两键，不触发 TooManyScans）；cancel 按 scan_id 精确；提交失败清理 `_active_reqs`；active_repo_sources 多 ws 多 scan；_watch 各 scan 独立 tail 各自 events.ndjson。

### T4. scan-scoped API 路由 + shim 改造
- **新 `api/scans.py`**（挂 `/api/workspaces`，spec §5.1 全部端点）：list/detail/deliverables/report/logs/events(SSE)/cancel/resume。所有路由 `Depends(workspace_member)`；resume 仅 interrupted/crashed（completed/failed/cancelled/running 均 422，spec §5.1）。
- **shim 改造（注：shim 后续已全部移除，见末行）**：
  - ~~`api/workspaces.py`：`GET /{ws}`（`workspaces.py:69`）改读 `ScanStore.latest_scan(ws)` 返旧 payload + `scans: ScanSummary[]`；`/{ws}/report|deliverables|logs`（`workspaces.py:111+`）转发 latest scan~~
  - ~~`api/events.py`：`GET /{ws}/events` 转发 latest scan events.ndjson~~
  - `api/scan.py`：`POST /api/scan`（`scan.py:14`）返回 `ScanAccepted(workspace, scan_id)`（保留，真端点）；~~`DELETE /api/scan/{ws}`（`scan.py:43`）cancel latest/active~~
  - **后续（commit `e1406473`）**：上述 ws-scoped GET shim + `DELETE /api/scan/{ws}` 已全部移除（Phase 2 前端全切 scan-scoped，零调用）；前端 WorkspaceListPage 改 `cancelActiveScan(ws)` 走 scan-scoped `cancelScan`。spec §5.2 已同步。
- `models.py`：`ScanAccepted` 加 `scan_id: str`。
- **测试 `tests/test_scans_api.py`**：list/detail/report/deliverables/logs 按 scan_id；跨 ws 403；resume interrupted/crashed 202、completed/failed/cancelled/running 422；`POST /api/scan` 返 scan_id；resume 提交后 resumeAttempts+1。（shim 相关测试随 `e1406473` 删；新增 `test_legacy_ws_scoped_delete_now_404` 锁定 `DELETE /api/scan/{ws}` 已 404。）

### T5. legacy 迁移（启动幂等）
- `app.py` lifespan（与现有 legacy ws->admin 迁移同阶段，`app.py:100` 附近）：扫 `workspaces/*/session.json`（ws 根），best-effort `shutil.move` 入 `workspaces/<ws>/scans/<legacy_id>/`（legacy_id 从 created_at 派生，碰撞 `-2`/`-3`），补 `workspace.json`（owner 取原 owner 或 "legacy"）。已迁跳过；异常记 warning 不阻断启动。
- **测试 `tests/test_legacy_scan_migration.py`**：ws 根 session.json -> `scans/<id>/`；幂等（再跑不重复）；损坏 session.json 跳过不崩；多 ws 并存迁移。

### T6. 回归 + 不变量断言
- 跑改动相关测试文件（勿广跑全套，CLAUDE.md §3 预存挂起/失败）：`test_scan_store.py`、`test_scan_manager_multi.py`、`test_scans_api.py`、`test_legacy_scan_migration.py` + 现有 `test_workspace_members_store.py`/`test_workspace_filter.py`/`test_repos_ws_isolation.py`/`test_scan_*`/`test_workspace_*` 回归。
- **不变量断言**（新测试）：
  1. 任意时刻一个 scan_id 仅对应一个 session.json（无 ws 根泄漏）。
  2. `GET /api/workspaces` 的 ws status = latest scan 聚合（不混入 scan-only 字段作 ws 状态）。
  3. core `SessionManager` 源码零改动：grep 断言 `packages/core/src/supernova_core/session.py` 关键签名（`def create_workspace`、`def list_workspaces`、`def get_session_data`）未变（或 import 行为未变）。
- **真机冒烟（人工）**：admin 建 ws -> clone repo -> 起 scan A -> 不 cancel 起 scan B -> 两 scan 各自 live 流不串台 -> A 完成后 resume B -> 各自 report 独立 -> delete scan A 不影响 B。

## 风险

- **legacy 双源兼容**：indexer 同时认 `scans/*/session.json` + ws 根 `session.json`（CLI/worker.py 仍产后者）。Phase 2 不动 CLI；后续统一另立小 task。
- **workflow_id scheme 变更**：新 scan 用 `{ws}-{scan_id}[-resume-N]`，与旧 `{ws}[-resume-N]` 不同。仅影响新 scan；旧 workflow resume 不支持（legacy scan 只读归档，spec §2 非目标）。
- **迁移 move 风险**：`shutil.move` 失败回滚难；best-effort + 幂等 + 不阻断启动；迁移前建议备份 `workspaces/`。
- **feat/fork-py 在途工作**：core 零改动 + web 新增为主（少改旧文件），降低与 ~40 项未 push 工作冲突。T3/T4 改 scan_manager/api 属必要改动，单独 commit 便于审查。

## 不在范围（spec §2）

- CLI/worker.py 同步走 `scans/` 子目录（legacy 双源兼容，二期统一）。
- 前端 UI（Phase 2）。
- 跨 ws scan 共享/对比、per-ws scan 并发上限、scan 配置快照。
