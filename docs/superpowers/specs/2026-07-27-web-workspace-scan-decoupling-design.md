# Web 工作区与扫描任务解耦 - 设计文档

- **日期**：2026-07-27
- **分支**：`feat/fork-py`
- **状态**：已确认方向，分两 plan 实现（Phase 1 后端 / Phase 2 前端）
- **依赖**：P0（认证）+ P1（workspace 成员制）+ P2（repos 隔离）+ P3c（per-ws 配置隔离）已落地
- **关联 plan**：`plans/2026-07-27-web-workspace-scan-decoupling-phase1.md`（后端）、`...-phase2.md`（前端）

## 1. 背景

P0-P3c 引入了 workspace 成员制 / repos 隔离 / per-ws 配置隔离，但 **workspace 与 scan 在存储层 1:1 混在一起**：

- `workspaces/<ws>/session.json` 既是 workspace 元数据载体，也是**单个 scan** 的状态机（status/metrics/resumeAttempts/...）。
- `SessionManager.list_workspaces()`（`packages/core/src/supernova_core/session.py:63`）扫 `workspaces/*/session.json` —— **列 workspace 就是列 scan**。
- `scan_manager.start()`（`packages/web/src/supernova_web/components/scan_manager.py:85`）：`SessionManager.create_workspace(name=ws)` 在 ws 根写 session.json；`_handles[ws]` 单槽（`scan_manager.py:120`）；重扫靠 `resumeAttempts` 复位**同一** workspace（`_resolve_workflow_id` `scan_manager.py:165`）。
- `GET /api/workspaces/{ws}`、`/{ws}/report|deliverables|logs|events` 全部直读 ws 根 = 单 scan 视图。
- worker 侧 `WhiteboxScanWorkflow` 用 `workspace_path = Path(input.event_file).parent` 推导产物目录（`packages/whitebox/src/supernova_whitebox/pipeline/workflows.py:123`）—— 产物落在 event_file 所在目录。

用户诉求（5 条）：① ws 可有多个扫描任务（**未满足，核心 gap**）；② ws 可有多个仓库（P2 已满足）；③ ws 可分配用户（P1 已满足）；④ ws 可隔离扫描任务（随 ① 满足）；⑤ ws 可隔离仓库和产物（P1/P2 已满足）。本设计聚焦 ①④。

**用户确认的心智模型**：「扫描任务才有再次扫描，workspace 没有再次扫描选项」。即 **scan 是被重跑/恢复的实体，workspace 是容器**。workspace 上有「新建扫描」「扫描列表」；scan 上有「查看/恢复/重跑/取消/删除」。

## 2. 目标 / 非目标

### 目标
- 1 workspace : N scans；每个 scan 独立目录、独立状态机、独立产物。
- 同一 ws 内多 scan 可并发（不互斥）。
- scan 可独立查看/恢复/重跑/取消/删除；workspace 只作容器 + 成员/仓库/配置归属。
- 旧数据（ws 根单 session.json）幂等迁移到 `scans/<scan_id>/`。
- core `SessionManager` 源码零改动、worker 零改动（复用 + 路径推导）。

### 非目标
- CLI / `worker.py` / `cli/main.py` 同步走 `scans/` 子目录 —— 留 legacy 双源兼容（spec §7），二期统一。
- 跨 ws scan 共享/对比。
- per-ws scan 并发上限（全局 `max_concurrent` 保留）。
- scan 配置版本化（每次 scan 存当时 ws_config 快照）—— 二期可加 `scans/<id>/config.snapshot.yaml`。
- 旧 workflow（`{ws}[-resume-N]` scheme）的 resume —— legacy scan 视为只读归档。

## 3. 存储布局（target）

```
workspaces/<ws>/
  workspace.json          # NEW: ws 元数据 {name, created_at, owner, description?}
  config.yaml             # P3c per-ws 配置（不动）
  repos/<name>/           # P2 per-ws 仓库（不动）
  scans/
    <scan_id>/            # NEW: 每个扫描任务独立目录
      session.json        # ← 从 ws 根迁来；scan 状态机（schema 不变）
      events.ndjson
      workflow.log, agents/, prompts/
      deliverables/whitebox/...
```

- **scan_id**：ws 内唯一，格式 `YYYYMMDD-HHMMSS`（本地时区紧凑秒级，无冒号，对齐 `SessionManager._default_workspace_name`）；同秒碰撞追加 `-2`/`-3`。
- **全局 scan 标识**：复合键 `(ws, scan_id)`。
- **scan session.json schema 不变**：沿用 `SessionManager.create_workspace` 写的字段（web_url/repo_path/created_at/scan_type/status/completed_at/links/deliverables_summary/completed_agents/metrics）+ 现有扩展（owner/submitted_at/resumeAttempts）。scan_id 即该 session 所在目录名。

## 4. 数据模型

### 4.1 workspace.json（NEW）
```json
{
  "name": "<ws_name>",
  "created_at": "<ISO8601 或 unix>",
  "owner": "web" | "host" | "<username>" | "legacy",
  "description": null
}
```
- `POST /api/workspaces` 写此文件（替代现 minimal session.json，`api/workspaces.py:47`）。
- legacy 迁移时为无 workspace.json 的 ws 补写（owner 取原 session.json owner 或 "legacy"）。

### 4.2 ScanSummary（list/detail API 返回）
```
ScanSummary {
  scan_id: str
  scan_type: "whitebox" | "blackbox" | "correlation"
  status: str          # 归一后（终态优先 + heartbeat，复用 _status_of）
  created_at: number   # unix
  completed_at: number | null
  vuln_count: number
  total_cost_usd: number | null
  cost_currency: str | null
  is_running: bool
}
```

### 4.3 scan session.json
不变（沿用 `SessionManager` 现有 schema）。scan_id = 所在目录名。

## 5. API 契约（Phase 1 实现 / Phase 2 消费）

### 5.1 新 scan-scoped 端点（Phase 1 T4，`api/scans.py` 新文件，挂 `/api/workspaces`）
| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| GET | `/api/workspaces/{ws}/scans` | 列该 ws 的 scans（`ScanSummary[]`，按 created_at 倒序） | `workspace_member` |
| GET | `/api/workspaces/{ws}/scans/{scan_id}` | scan 详情（同旧 `GET /{ws}` payload shape，读 scan_dir） | `workspace_member` |
| GET | `/api/workspaces/{ws}/scans/{scan_id}/deliverables?path=` | 产物摘要或文件内容 | `workspace_member` |
| GET | `/api/workspaces/{ws}/scans/{scan_id}/deliverables/{filename}?track=` | 单产物文件 | `workspace_member` |
| GET | `/api/workspaces/{ws}/scans/{scan_id}/report` | 综合报告 + PoC（text/plain） | `workspace_member` |
| GET | `/api/workspaces/{ws}/scans/{scan_id}/logs?file=` | 日志文件列表或内容 | `workspace_member` |
| GET | `/api/workspaces/{ws}/scans/{scan_id}/events` | SSE，tail scan_dir/events.ndjson | `workspace_member` |
| DELETE | `/api/workspaces/{ws}/scans/{scan_id}` | cancel 该 scan | `workspace_member` |
| POST | `/api/workspaces/{ws}/scans/{scan_id}/resume` | 恢复未完成 scan（increment resumeAttempts，提交 resume workflow） | `workspace_member` |

- 路径校验：scan_id 拒 `..`/`/`/空（复用 `_validate_ws_segment` 档约束 + `resolve().is_relative_to(scans_dir)`）。
- `resume` 仅对非终态 scan 放行（终态 422）。

### 5.2 兼容 shim（Phase 1 保留，Phase 2 切完后移除）
旧端点改操作 ws 内「最新 scan」（`ScanStore.latest_scan(ws)`：active 优先，否则 max created_at）：
- `GET /api/workspaces/{ws}` —— 返回旧 payload shape + **增 `scans: ScanSummary[]`**。
- `GET /api/workspaces/{ws}/report|deliverables|logs|events` —— 转发到 latest scan。
- `DELETE /api/scan/{ws}` —— cancel latest/active scan。

`POST /api/scan` 的 `ScanAccepted` 增字段：
```
ScanAccepted { workspace: str, scan_id: str }   # scan_id 新增；旧前端忽略仍可用
```

### 5.3 workspace 列表（Phase 1 T2）
`GET /api/workspaces` 每 ws 增聚合字段：
```
Workspace {
  name, scan_type, status, created_at, completed_at,    # 旧字段（status/created_at 取 latest scan）
  vuln_count, total_cost_usd, cost_currency, total_duration_ms,
  links, is_correlation,
  scan_count: number,            # NEW
  latest_status: str,            # NEW（= status，显式）
  latest_created_at: number      # NEW（= created_at，显式）
}
```

## 6. scan 生命周期与并发

- **新建扫描**：`POST /api/scan`（ws=req.workspace 校验不变）-> `ScanStore.create_scan(ws, ...)` 建 scan_id -> 提交 temporal workflow -> 返回 `(ws, scan_id)`。不复位 resume。
- **重跑**：scan 卡片「重跑」-> `POST /api/scan`（同 ws，预填 source）-> 新 scan_id -> 跳新 live。**workspace 无「再次扫描」入口**。
- **恢复**：`POST .../scans/{scan_id}/resume`（仅未完成）-> 读该 scan 的 `resumeAttempts` 算 workflow_id（`{ws}-{scan_id}[-resume-N]`）-> 提交 resume workflow。
- **取消**：`DELETE .../scans/{scan_id}` -> `handle.cancel()` + `_mark_cancelled(scan_dir)`。
- **并发**：`scan_manager._handles`/`_tasks`/`_active_reqs` 由 key=`ws` 改 key=`(ws, scan_id)`；同 ws 多 scan 不互斥。全局 `max_concurrent`（P3c 阶段3 已放宽到 N）保留为全局上限。

## 7. legacy 迁移与双源兼容

### 7.1 启动迁移（幂等，`app.py` lifespan）
扫 `workspaces/*/session.json`（ws 根），best-effort `shutil.move` 入 `workspaces/<ws>/scans/<legacy_id>/`：
- legacy_id 从原 session.json `created_at` 派生 `YYYYMMDD-HHMMSS`；碰撞 `-2`/`-3`。
- 补 `workspace.json`（owner 取原 owner 字段或 "legacy"）。
- 已迁（ws 根无 session.json）跳过；损坏 session.json 记 warning 跳过，不阻断启动。

### 7.2 双源兼容（indexer）
`WorkspacesIndexer` 列 scan 时同时识别两来源：
- 新：`workspaces/<ws>/scans/*/session.json`
- legacy：`workspaces/<ws>/session.json`（CLI/`worker.py`/`cli/main.py` 仍产此路径）

统一列为 ScanSummary。**不强迫 CLI 同步改**（二期统一）。

## 8. 关键降风险点（core / worker 零改动）

- **core 零改动**：`SessionManager` 的 `get_session_data`/`update_session`/`get_status`/`get_created_at`/`mark_agent_completed`/... 全部只收 `workspace_path`、不依赖 `workspaces_dir`。web 层用 `SessionManager(ws_dir / "scans")` 把 scans 目录当 workspaces 根复用全部 scan 读写：`create_workspace(name=scan_id, ...)` 即在 `scans/<scan_id>/session.json` 建 scan；`list_workspaces()` 即列该 ws 的 scans。`packages/core`、CLI、`worker.py`、`workspace_discovery` 一行不改。
- **worker 零改动**：web 把 `event_file = scan_dir / "events.ndjson"` 塞进 `PipelineInput`，worker 据其 parent 推导 `workspace_path = scan_dir`（`workflows.py:123` 分支），产物自然落 scan 子目录。`workspace_name` 字段对 web 路径仅作展示，置为 `scan_id`。

## 9. 角色与权限（复用 P1，不变）

- `workspace_member`：读 scan 产物、cancel scan、resume scan。
- `workspace_manager`：管理 ws 成员、删 ws、改 ws 配置。
- admin：全局兜底，见所有 ws。
- scan 不引入独立 ACL —— 能访问 ws 就能访问该 ws 所有 scan（与 P2 repo 同模型）。

## 10. 不变量 / 铁律边界

1. **core `SessionManager` 源码零改动**（Phase 1 T6 加 grep 断言关键签名未变）。
2. **worker 零改动**（`PipelineInput` 不增字段；`workspace_name=scan_id` 复用）。
3. **不碰双轨 / 确定性层 / LLM 轨 prompt / 合并器**（CLAUDE.md §1）—— 本改动全在 web session/存储层 + core 复用。
4. **任意时刻一个 scan_id 仅对应一个 session.json**（无 ws 根泄漏）。
5. **scan_id 路径校验**：拒 `..`/`/`/空（防路径遍历，复用 `repo_manager._validate_ws_segment` 档约束）。
6. **legacy 迁移幂等 + best-effort + 不阻断启动**。

## 11. 依赖关系（Phase 1 ↔ Phase 2）

- **Phase 2 前端依赖 Phase 1 的 scan-scoped API**（§5.1）。
- 两终端若并行：Phase 2 终端先基于本 spec §5 API 契约做前端组件 + vitest mock（不依赖后端真存在）；端到端联调等 Phase 1 完成。
- 两终端同在 `feat/fork-py` 分支：后端（`packages/web/src`）/前端（`packages/web/frontend`）物理分离冲突小；`workspace.json` schema（§4.1）放本 spec 钉死，两边别在 plan 里改定义。

## 12. 决策记录

1. **scan 落 `scans/<scan_id>/` 子目录**（非 ws 根）—— 解耦 1:1 的核心。
2. **core/worker 零改动**（web 复用 `SessionManager` + worker 路径推导）—— 降 feat/fork-py 在途工作回归风险。
3. **scan_id = 时间戳**（非 uuid）—— 可读、可排序，对齐现有 ws 命名风格。
4. **重扫 = 新 scan 任务**（旧 scan 保留）；**恢复 = 续跑未完成 scan**—— 贴合用户心智模型「scan 才有再次扫描」。
5. **legacy 双源兼容**（不强迫 CLI 同步改）—— 降低波及面；二期统一。
6. **shim 保留**（Phase 1 旧前端不破，Phase 2 切完移除）—— 增量、可回滚。
7. **workspace 无「再次扫描」入口**（只有「新建扫描」+「扫描列表」）—— 用户明确确认。

---

**下一步**：Phase 1 plan（后端 TDD）、Phase 2 plan（前端，二期）各自实现。
