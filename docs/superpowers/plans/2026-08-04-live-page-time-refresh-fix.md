# Live 实时页时间/刷新修复 Plan

> 日期 2026-08-04 · 分支 feat/fork-py · 现场扫描 delivery-20260804-024910

## 根因摘要

### 问题 1：刚开始扫就显示 8h+（时区 bug）✅ 铁证
- worker 容器时区 = UTC（compose 未设 TZ，容器默认 UTC；宿主机是 CST +08:00）。
- `format_log_time()`（`packages/core/.../formatters.py:42-44`）用 `datetime.now()` 写 events.ndjson 的 `ts` = worker 容器 UTC 墙钟，格式 `"2026-08-04 02:49:13"`，**无时区后缀、空格分隔**。`workflow_logger.py` 12 个 `log_*` 全用它。
- 前端 `LiveTab.tsx:50` `Date.parse("2026-08-04 02:49:13")`：无时区非标准串，JS 按浏览器本地时区（UTC+8）解释 → epoch 比真实 UTC **早 8h** → `elapsed = Date.now() - phaseStartMs` 多算 8h → 开扫即显 8h+。
- 附带：`scan_end` 的 ts 格式不统一（worker 正常完成用 `format_log_time()` 无时区；web 回退用 `_now_iso()` UTC ISO `+00:00`），同文件混用。
- `types.ts:9` 注释 `ISO8601 UTC 毫秒` 与实际不符。

### 问题 2：开始时间/耗时/花费未有效展示
- live 页**不显示开始时间**。
- 耗时是「当前 phase 耗时」（每进新 phase 归零），非「总扫描耗时」；且中 8h bug。
- cost 来自 SSE `AgentEvent` end 累积，运行中只有**已结束** agent 有 cost，无后端兜底。
- 后端 `getScan` 返回 `created_at`/`completed_at`（Unix 时间戳，`time.time()`，**无时区问题**）+ `metrics.total_cost_usd`，但 `ScanDetail.tsx` fetch 的 `meta` 没下传给 `LiveTab`（Outlet 隔断）。

### 问题 3：step/agents 不刷新、显示 0/0 无运行中 agent
- 代码层无 bug（事件在写、worker/web 共享文件、recon 现场在跑）。
- 用户补充现场：**「一直 step 0/0 agents 0/0 8h 06m 47s $0.00 没更新」**。`0/0 0/0 $0.00` = `emptyState`（SSE 事件未进 state），但 `8h+` = phaseStartMs 非 null（events 有 PhaseEvent start）——在当前源码下矛盾（events 空则 elapsed=0）。**强烈指向线上 web 镜像 build 旧**（跑着与当前源码不同版本的 LiveTab），与 memory 反复提到的「需 rebuild web 镜像」吻合。

---

## 修复方案

### P1 前端（rebuild web 镜像）—— 解决问题 1/2/3 主体

#### 1.1 修 8h 时差（ts 解析归一化）
- `LiveTab.tsx`：新增 `parseEventTs(ts: string): number` helper，`Date.parse` 前归一化：
  - 无时区串（`"2026-08-04 02:49:13"` 空格分隔）→ 当 UTC：`Date.parse(ts.replace(" ", "T") + "Z")`。
  - 带时区串（`...Z` / `...+00:00`，scan_end web 回退）→ 原样 `Date.parse`。
  - 兼容历史 ndjson（无时区）+ 新扫描（带 Z）+ 混合 scan_end。
- `phaseStartMs` 用 `parseEventTs(e.ts)` 替代 `Date.parse(e.ts)`（LiveTab.tsx:50）。
- `types.ts:9` 注释更正为实际语义。

#### 1.2 接通 getScan（开始时间 + 总耗时 + cost 兜底）
- `LiveTab.tsx`：仿 `OverviewTab.tsx` 模式，调 `getScan(workspace, scanId)` 一次性 fetch `meta`（SessionData）；扫描完成（scanEnd 出现）时 refetch 取最终 `completed_at` + `metrics.total_cost_usd`。
- 数据下传 `DashboardPanel`：`created_at`（Unix）、`completed_at`（Unix|null）、`finalCost`（完成时）。

#### 1.3 DashboardPanel 扩展展示
- 顶栏新增（现有 `phase / step N/M / agents N/M / 阶段耗时 / cost` 基础上）：
  - **开始时间**：`new Date(created_at*1000).toLocaleString()`（Unix 无时区问题）。
  - **总耗时**：运行中 `Date.now() - created_at*1000`（每秒 tick）；完成 `(completed_at - created_at)*1000`。
  - **阶段耗时**：保留现有 `elapsedMs`（修 8h 后正确），语义标注「阶段」。
  - **cost**：运行中取 SSE 累积 `state.total_cost`；完成取 `max(state.total_cost, finalCost)` 对齐后端。
- LiveTab 用 `created_at` 派生总耗时的 tick（复用现有 setInterval 模式）。

#### 1.4 SSE 实时性指示（诊断问题 3）
- `LiveTab.tsx`：从 events 派生 `lastEventMs`（最后一条事件 ts 经 parseEventTs）+ `eventsCount`。
- `DashboardPanel`：渲染「连接状态 Badge（已有 open/error/closed）」+「最后事件 X 秒前」+「events N 条」。让「没更新」可见可诊断（区分「SSE 断连」vs「扫描暂无新事件」vs「build 旧」）。

#### 1.5 i18n + 测试
- `locales/zh.json` / `en.json` 加 key：`startedAt`（开始时间）、`totalDuration`（总耗时）、`phaseDuration`（阶段耗时）、`lastEvent`（最后事件）、`eventsCount`（事件数）等。
- 更新 `DashboardPanel.test.tsx`：新字段断言（开始时间/总耗时/cost 兜底）；保留现有 step/agents/elapsed/cost 契约。
- 更新 `LiveTab.test.tsx`：mock `getScan` + ts 归一化（无时区串 + 带 Z 串均正确解析）。

### P2 后端（rebuild worker 镜像）—— 问题 1 治本（可选但推荐）

- `structured_event_renderer.py`：`_serialize()` 的 `ts` 从 `event.timestamp` 改为 `format_timestamp()`（UTC ISO 带 Z，毫秒）；`render()` 里 worker scan_end 那行的 `ts` 同改 `format_timestamp()`。
  - 与 `event.timestamp`（= `format_log_time()`，CLI rich_renderer 仍用，本地可读）**解耦**：CLI 显示不变，ndjson ts 自描述时区（Z），不依赖容器时区。
- （可选）`scan_manager.py:_now_iso()` 统一产 `format_timestamp()` 风格（带 Z），消除 scan_end 混合格式。
- P2 让新扫描的 ndjson ts 带 Z，P1 前端归一化兼容历史 + 兜底，双保险。

---

## 改动文件清单

**前端（rebuild web）：**
- `packages/web/frontend/src/routes/WorkspaceDetail/LiveTab.tsx`（getScan + parseEventTs + 总耗时 + 完成refetch + 实时性数据）
- `packages/web/frontend/src/components/DashboardPanel.tsx`（开始时间/总耗时/阶段耗时/cost兜底/实时性指示）
- `packages/web/frontend/src/api/types.ts`（ts 注释更正）
- `packages/web/frontend/src/locales/zh.json` + `en.json`（新 key）
- `packages/web/frontend/src/components/DashboardPanel.test.tsx` + `routes/WorkspaceDetail/LiveTab.test.tsx`（更新）

**后端（rebuild worker，P2 可选）：**
- `packages/core/src/supernova_core/display/structured_event_renderer.py`（ts 用 format_timestamp）
- `packages/web/src/supernova_web/components/scan_manager.py`（_now_iso 统一，可选）

## Rebuild 步骤（修复生效 + 解决问题 3 主因 build 旧）
1. `uv sync --all-packages`（如有依赖变更）
2. 前端测试：`./node_modules/.bin/vitest run packages/web/frontend/src/components/DashboardPanel.test.tsx packages/web/frontend/src/routes/WorkspaceDetail/LiveTab.test.tsx`（pnpm 陷阱见 memory，用 .bin 直跑）
3. `docker compose build web worker`（P2 做则含 worker；不做则仅 web）
4. `docker compose up -d web worker`（force-recreate）
5. 真机验证：新建扫描开 live 页，确认开始时间/总耗时/阶段耗时/cost 正确、8h 消失、SSE 实时性指示更新。

## 风险
- P2 改 `StructuredEventRenderer` 需确认无其他消费者依赖 `ts=event.timestamp`（已确认：rich_renderer 用 event.timestamp 独立、agent_logger/log_stream 用 format_timestamp 独立，不读 ndjson ts）。
- 前端归一化假设「无时区串 = UTC」（worker 容器历来 UTC）；P2 后新扫描带 Z 不再依赖此假设。
- 现有扫描的历史 ndjson 仍是旧格式，前端归一化兜底（当 UTC）——正确，因 worker 历来 UTC。
- rebuild 是问题 3 生效前提：若不 rebuild web，线上仍跑旧 build，代码修复不生效。
