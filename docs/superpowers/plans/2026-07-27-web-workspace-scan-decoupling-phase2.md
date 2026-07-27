# Web 工作区与扫描任务解耦 - Phase 2（前端）实现计划

- **日期**：2026-07-27
- **分支**：`feat/fork-py`
- **状态**：outline，二期实施（依赖 Phase 1 后端）
- **关联**：设计见 `specs/2026-07-27-web-workspace-scan-decoupling-design.md`（下称「spec」）；后端见 `plans/2026-07-27-web-workspace-scan-decoupling-phase1.md`
- **范围**：前端从单 scan 视图（ws=scan）切到「ws 容器 + 多 scan 任务」视图。

## 前置与依赖

- **依赖 Phase 1 的 scan-scoped API**（spec §5.1）。Phase 1 完成（或至少 T4 scan-scoped API + shim 上线）前，端到端联调不可行。
- **并行策略**：若与 Phase 1 同终端并行，先基于本 plan + spec §5 API 契约做前端组件 + vitest mock（MSW 或手写 mock），不依赖后端真存在；端到端联调等 Phase 1 完成。
- **schema 钉死**：`workspace.json`（spec §4.1）、`ScanSummary`（spec §4.2）、API 路径/响应（spec §5）以 spec 为准；本 plan 不改定义。两终端别在 plan 里各自改 schema。

## 任务（outline）

### F1. 类型 + API client
- `api/types.ts`：增 `ScanSummary`（spec §4.2）；`Workspace` 增 `scan_count`/`latest_status`/`latest_created_at`（spec §5.3）；`ScanResponse` 增 `scan_id`。
- `api/client.ts`：增 scan-scoped helper -- `listScans(ws)`、`getScan(ws, scanId)`、`scanReportUrl/ws/scanId`、`scanDeliverablesUrl`、`cancelScan(ws, scanId)`、`resumeScan(ws, scanId)`、`scanEventsUrl`（SSE）。保留旧 ws-scoped helper（过渡期，切完后删）。

### F2. WorkspaceDetail 改造为 ws 概览 + 扫描列表
- `routes/WorkspaceDetail/index.tsx`：`/p/:workspace` = ws 概览（ws 元数据 + **扫描任务列表** + 成员/仓库/settings 入口）。
- 扫描任务卡片：scan_id、status（StatusBadge）、scan_type、created_at、cost、vuln_count；操作「查看 / 恢复（未完成）/ 重跑 / 取消（running）/ 删除」。
- 「新建扫描」按钮 -> `/scan/new?workspace=<ws>`（预填 ws）。
- 空态：ws 无 scan -> 「尚无扫描任务，新建扫描」。

### F3. per-scan 路由
- 新路由 `/p/:workspace/scans/:scanId/{overview,report,deliverables,logs,live}`，复用现有 Tab 组件（OverviewTab/ReportTab/DeliverablesTab/LogsTab/LiveTab），数据源切到 scan-scoped 新 API（`getScan`/`scanReportUrl`/...）。
- `router.tsx`：加 `scans/:scanId` 子路由；旧 `/p/:workspace/{overview,report,...}`（ws-scoped）过渡期保留指向 latest scan（shim），切完后移除。
- LiveTab：SSE 切 `scanEventsUrl(ws, scanId)`。

### F4. ScanNewPage
- 提交 `POST /api/scan` 拿 `scan_id` -> `navigate(/p/:ws/scans/:scanId/live)`。
- ws 选择不变（P1/P2 已有）；repo source 从选定 ws 的 repo 列表取（P2 不变）。

### F5. scan 操作
- **重跑**：scan 卡片「重跑」-> `POST /api/scan`（同 ws，预填 source）-> 新 scan_id -> 跳新 live。
- **恢复**：「恢复」按钮（仅未完成 scan 显示）-> `resumeScan(ws, scanId)` -> 跳该 scan live。
- **取消**：「取消」（仅 running）-> `cancelScan(ws, scanId)` -> 刷新列表。
- **删除**：「删除」-> 确认 -> `DELETE /api/workspaces/{ws}/scans/{scan_id}` -> 刷新列表（删 scan 不删 ws）。
- **workspace 无「再次扫描」入口**（spec §12.7）-- 只有「新建扫描」+ 扫描列表。

### F6. 移除 shim（前端切完后）
- 前端全面切 scan-scoped 新 API 后，删除：
  - `api/workspaces.py` 中 `/{ws}/report|deliverables|logs|events` shim。
  - `api/scan.py` 的 `DELETE /{ws}` shim + `GET /{ws}` 的 scans[] 兼容字段（若前端不再用）。
- 保留 scan-scoped 端点。此任务可与 Phase 1 协调（Phase 1 终态验收后执行）。

### F7. i18n
- `locales/{zh,en}.json` 增 `workspace.scans.*`：list title / empty / newScan / view / resume / rerun / cancel / delete / deleteConfirm。

### F8. 测试（vitest）
- WorkspaceDetail：扫描列表渲染、空态、scan 卡片操作按钮按 status 显隐。
- per-scan 路由：数据从 scan-scoped API 加载。
- ScanNewPage：提交后跳 `scans/:scanId/live`。
- scan 操作：重跑/恢复/取消/删除调对应 API + 列表刷新。
- mock 策略：MSW 或 `vi.mock` 基于 spec §5 契约拦截请求，不依赖后端。

## 风险

- **依赖 Phase 1**：端到端联调需 Phase 1 完成。并行时前端用 mock，联调阶段若 Phase 1 API 契约微调，前端需对齐（故 schema 钉死在 spec）。
- **路由兼容**：旧 `/p/:workspace/{overview,...}` 链接（书签/外部）过渡期指向 latest scan，切完后 301 到 latest scan 路由或保留 shim。
- **feat/fork-py 在途工作**：前端改动集中在 `frontend/src/routes/WorkspaceDetail` + `pages/ScanNewPage` + `api/`，与后端物理分离冲突小。

## 不在范围（spec §2）

- CLI/worker.py 同步走 `scans/` 子目录。
- 跨 ws scan 共享/对比、per-ws scan 并发上限、scan 配置快照。
- 后端 shim 移除的最终协调（F6，与 Phase 1 联合验收）。
