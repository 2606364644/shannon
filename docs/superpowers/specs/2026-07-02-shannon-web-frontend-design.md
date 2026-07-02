# Shannon Web Platform — 子项目 2：前端 SPA

> 上位设计：`docs/superpowers/specs/2026-07-02-shannon-web-platform-design.md`（总体架构、ndjson 事件 schema 三方硬契约、信息架构 3 层、实时 dashboard 复刻保真度约定、错误处理均在那里定稿，本子 spec 不重复，只聚焦前端实现细节）。
> 后端契约来源：`docs/superpowers/specs/2026-07-02-shannon-web-backend-design.md`（API、SSE、ndjson schema）。

## 范围

子项目 2 交付**前端 SPA**，依赖子项目 1 已定稿的 ndjson 契约 + API：

1. React + Vite + TS 工程脚手架
2. 2 主页面（开启扫描页 / 项目列表页）+ 详情子页 5 tab
3. 6 组件 + SSE hook + `dashboardReducer`
4. 前端单测（vitest + testing-library，含 reducer 与 core 对齐测试）
5. Vite proxy 接子项目 1 后端

**不含**：后端、core 改动、部署 wiring（子项目 1）。

---

## 1. 工程脚手架

```
packages/web/frontend/
├── package.json               react/react-router/react-markdown/rehype-*/@monaco-editor/react/vitest
├── vite.config.ts             Vite proxy /api → localhost:7878
├── tsconfig.json
├── index.html
└── src/
    ├── main.tsx               入口
    ├── App.tsx                react-router 路由
    ├── router.tsx             路由定义
    ├── pages/
    │   ├── ScanNewPage.tsx
    │   └── WorkspaceListPage.tsx
    ├── routes/WorkspaceDetail/
    │   ├── index.tsx          路由壳 + tab 导航(Outlet)
    │   ├── OverviewTab.tsx
    │   ├── ReportTab.tsx
    │   ├── DeliverablesTab.tsx
    │   ├── LogsTab.tsx
    │   └── LiveTab.tsx
    ├── components/
    │   ├── DashboardPanel.tsx     ★ rich 框复刻
    │   ├── LogStream.tsx          SSE 滚动日志
    │   ├── MarkdownView.tsx       react-markdown + rehype
    │   ├── YamlEditor.tsx         @monaco-editor/react
    │   ├── FileTree.tsx           deliverables 产物树
    │   └── StatusBadge.tsx        状态徽章(●✓✗⚠ + 🔗联动)
    ├── api/
    │   ├── client.ts              fetch 封装
    │   ├── useEventSource.ts      SSE hook
    │   └── types.ts               TS 类型(对齐 ndjson schema + API 响应)
    ├── state/
    │   └── dashboardReducer.ts    ★ 复刻 DashboardState.apply
    └── styles/
        └── events.css             事件颜色(对齐 STYLE_MAP)
```

**Vite proxy**：`/api` → `http://localhost:7878`（dev 模式；生产由后端静态托管或 nginx）。

---

## 2. 路由与页面

### 2.1 路由

```
/                        → WorkspaceListPage
/scan/new                → ScanNewPage
/p/:workspace            → WorkspaceDetail (默认 tab: 进行中→live, 完成→report)
/p/:workspace/overview   → OverviewTab
/p/:workspace/report     → ReportTab
/p/:workspace/deliverables → DeliverablesTab
/p/:workspace/logs       → LogsTab
/p/:workspace/live       → LiveTab
```

### 2.2 项目列表页（`WorkspaceListPage.tsx`）

- `GET /api/workspaces` → 表格：项目名 · StatusBadge · 漏洞数 · 成本 · 时间 · scan_type。
- 自动刷新（轮询 5s）或手动刷新按钮。
- 联动 workspace（scan_type=correlation）显 🔗 图标，点击进详情页"下钻"区。
- 「新建扫描」按钮 → `/scan/new`。

### 2.3 开启扫描页（`ScanNewPage.tsx`）

扫描类型 3 选 1（白盒/黑盒/联动）→ 动态表单（总体 spec 表单示意图）：

- **白盒/黑盒区**：代码来源（本地路径 / git URL）；git URL 模式显示分支/commit 可选 + 强制重新 clone；目标 URL；workspace 名（可空自动生成）；仅黑盒显示「复用最新白盒结果」复选框。
- **联动区**：multi-repo.yaml 上传 / 从已有选（`GET /api/multi-configs`）/ 手写编辑器（YamlEditor），「保存为配置」或「直接运行」。
- **workspace 名冲突校验**：填写时 `GET /api/workspaces` 查重，同名弹确认"将断点续扫"。
- 提交 `POST /api/scan` → 202 → 跳 `/p/{ws}/live`。

---

## 3. 详情子页 5 tab

### 3.1 概览 tab（`OverviewTab.tsx`）

`GET /api/workspaces/{ws}` → 渲染 `session.json` 指标。**兼容新旧两格式**（扁平 / 嵌套 `session` 子对象——后端已用 SessionManager 归一，前端只消费归一后的 API 响应，但 TS 类型要含两格式字段的可选性）：

- 顶部：StatusBadge + scan_type + web_url + repo_path + 时间区间。
- 成本/耗时大数字：`metrics.total_cost_usd` / `metrics.total_duration_ms`。
- 各阶段 breakdown：`metrics.phases`（pre-recon/recon/vulnerability/exploitation/reporting 的 duration_ms/duration_percentage/cost_usd/agent_count）—— 表格或进度条。
- 各 agent 明细：`metrics.agents`（name → duration/cost/success/attempt/model）—— 表格，失败 agent 标红。
- 联动 workspace：额外显示"下钻"区，列出子白盒 ws（`links.child_workspaces` 或 correlation 专属字段），点击跳子 ws 详情。

### 3.2 报告 tab（`ReportTab.tsx`）

`GET /api/workspaces/{ws}/report` → md 原文 → `MarkdownView` 渲染。

**报告有两种结构**（探索发现），MarkdownView 都要支持：
- 仅静态分析：`# {Class} Findings` → `## Identified Vulnerabilities` → `### {ID}: {Type}` + 加粗键值字段。
- 含利用证据：顶部 `## Exploited` → `### {ID}` + Severity/Impact/Exploitation Steps/Proof of Impact（含 JSON 代码块）+ `## Other Verdicts`。

MarkdownView 用 `react-markdown` + `rehype-highlight`（代码块语法高亮，含 JSON）+ `rehype-slug` + `rehype-autolink-headings`（目录锚点）。左 TOC（从 H1/H2/H3 生成）右正文，长报告可折叠。**无表格**（探索确认报告无 markdown 表格），但保留 GFM 表格能力以防未来。

### 3.3 产物 tab（`DeliverablesTab.tsx`）

`GET /api/workspaces/{ws}/deliverables` → 产物清单 `{vuln_queues, reports}` + 文件树。

- `FileTree` 组件渲染 deliverables 目录树（含 `whitebox/`/`blackbox/` 子目录，新旧布局兼容——后端已归一）。
- 点击文件：
  - `.md` → MarkdownView 预览。
  - `.json`（`*_exploitation_queue.json` / `attack_chains.json` / `code_index.json` 等）→ JSON 树查看器（折叠/展开），空 `[]`（如 attack_chains 常态）优雅显示"无数据"。
  - 其他 → 下载。
- 漏洞队列 JSON 重点呈现：`vulnerabilities[]` 渲染成卡片（ID / vulnerability_type / externally_exploitable 徽章 / confidence / source_endpoint），点击展开详情。

### 3.4 日志 tab（`LogsTab.tsx`）

- 左侧文件列表：`agents/*.log`（JSONL）+ `workflow.log`（纯文本）+ `activity_failures.log`。
- 右侧内容：
  - `agents/*.log`：JSONL 逐事件渲染（agent_start/llm_response/tool_start/tool_end），tool 的 `parameters`/`result` 可折叠；大文件（探索发现 pre-recon 115KB）虚拟滚动或分页。
  - `workflow.log`：纯文本 tail，带语法着色（PHASE/STEP/AGENT 标签）。
- `GET /api/workspaces/{ws}/logs` 返回文件列表 + 单文件内容。

### 3.5 实时 tab（`LiveTab.tsx`）★

扫描进行中默认 tab。`GET /api/workspaces/{ws}/events`（SSE）→ 事件流。

**布局**（总体 spec 已定，复刻终端两层）：

```
┌─ DashboardPanel（固定顶部，状态条 + 运行中 agent）─┐
│  pre-recon · step 3/7 · 02:14 · $0.0234           │
│  ⠼ sink-discovery t2  grep *.py for sql sinks     │
├──────────────────────────────────────────────────┤
│  LogStream（滚动日志区，逐事件带颜色）              │
│  [09:44:01] PHASE  Starting pre-recon             │
│  [09:44:02] STEP   ○ code-index                   │
│  [09:44:05] AGENT  ▶ [Injection] started          │
│  ...                                               │
└──────────────────────────────────────────────────┘
```

- `DashboardPanel`：从 `dashboardReducer` 累积的快照渲染状态条（current_phase/step N/M/elapsed/cost）+ 运行中 agent 行（spinner + turn + last_action）。elapsed 前端 `setInterval(1s)` 本地自增（零后端 tick）。
- `LogStream`：逐事件渲染带颜色日志行（按 category 上色，对齐 `RichConsoleRenderer.STYLE_MAP`）。
- `scan_end` 事件 → 关闭 SSE 流 + 状态条定格 + 提示"扫描完成/失败"，可切到报告 tab。
- 联动 workspace 的实时 tab：额外渲染 correlation_progress 事件（repo 进度网格 + edge 状态列表），子 ws 下钻链接。

---

## 4. `dashboardReducer`（★ 核心，复刻 DashboardState.apply）

`src/state/dashboardReducer.ts`：1:1 复刻 `packages/core/src/shannon_core/display/dashboard_state.py:70-132` 的 `DashboardState.apply(event)` 逻辑。

```ts
interface AgentRow { name; status: "running"|"done"|"failed"; attempt; turn;
  last_action?; last_action_detail?; last_turn_text?; duration_ms?; cost_usd?; error?; }
interface DashboardState { current_phase?; agents: Record<string,AgentRow>;
  phase_units: string[]; unit_status: Record<string,string>; unit_intent: Record<string,string>; }
// 派生: completed_count / total_cost / total_units / completed_units / running_units

function dashboardReducer(state: DashboardState, event: NdjsonEvent): DashboardState
// 按 event.type 分支,逻辑 1:1 对齐 DashboardState.apply:
//   PhaseEvent → current_phase + phase_units 重置
//   StepEvent → unit_status 更新
//   AgentEvent → agents[name] 状态 + _set_unit
//   ToolCallEvent → agents[name].last_action_detail (humanize_tool_call)
//   LlmTurnEvent → agents[name].turn + last_turn_text (first_nonempty_line)
//   ResumeEvent → completed_agents 标 done
//   ErrorEvent/SummaryEvent/WorkflowHeader → 无状态变化
```

**`humanize_tool_call` / `first_nonempty_line` 也要 TS 复刻**（`formatters.py`）——前端渲染 agent 行的 action 文本需要它们。这两个是纯函数，直接移植。

### 4.1 reducer 对齐测试（关键铁律）

`src/state/dashboardReducer.test.ts`：给定事件序列，断言快照字段，**与 core 的 `DashboardState` 单测对齐**（同输入同输出）。

- 从 `packages/core/tests/display/test_dashboard_state.py` 复用其事件序列 fixture，断言 TS reducer 产出等价快照（已确认该测试存在）。
- `humanize_tool_call` / `first_nonempty_line` 的 TS 复刻对齐 `packages/core/tests/display/test_formatters.py` 的用例（已确认存在）。
- 覆盖：phase 切换重置 units / agent start-end 状态流转 / tool call 更新 last_action / llm turn 更新 turn+text / resume 标 done。
- **此测试是前端独立可信的基石**——reducer 与 core 行为一致，dashboard 渲染才有依据。

---

## 5. SSE hook（`useEventSource.ts`）

```ts
function useEventSource<T>(url: string): { events: T[]; status: "open"|"closed"|"error"; lastEventId?: string }
```

- 封装 `EventSource`，`onmessage` → 解析 ndjson JSON → 累积到 state。
- 自动重连：`onerror` 关闭后 `setTimeout` 重连，重连带 `Last-Event-ID`（byte offset，后端 EventTailer 支持）。
- `scan_end` 事件 → 标记完成 + 关闭流。
- 组件 unmount → close。

---

## 6. 事件颜色（`events.css`，对齐 STYLE_MAP）

按总体 spec「实时 dashboard 复刻保真度约定」节：

```css
.ev-phase   { color: #22d3ee; font-weight: bold; }   /* bold cyan */
.ev-agent   { color: #58a6ff; }                       /* blue (start) */
.ev-agent-ok   { color: #3fb950; }                    /* green (success) */
.ev-agent-fail { color: #f85149; }                    /* red (fail) */
.ev-tool    { color: #d29922; }                        /* yellow 🔧 */
.ev-llm     { color: #bc8cff; }                        /* magenta 💭 */
.ev-error   { color: #f85149; font-weight: bold; }    /* bold red */
.ev-info    { color: #22d3ee; }                        /* cyan */
.ev-warn    { color: #d29922; }                        /* yellow */
```

深色主题配色（终端色板近似）。spinner 用 CSS 动画复刻 braille 帧序列 `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`。

---

## 7. 测试

| 文件 | 范围 |
|---|---|
| `dashboardReducer.test.ts` | ★ 与 core DashboardState 对齐(同输入同输出) |
| `useEventSource.test.ts` | 事件累积 / scan_end 关闭 / 重连带 Last-Event-ID |
| `DashboardPanel.test.tsx` | 状态条字段渲染 / 运行中 agent 行 / elapsed 自增 |
| `LogStream.test.tsx` | 按 category 上色 / 事件渲染 |
| `MarkdownView.test.tsx` | 标题/代码块/TOC 锚点 / 两种报告结构 |
| `YamlEditor.test.tsx` | yaml parse 校验 / 保存/直接运行 |
| `ScanNewPage.test.tsx` | 类型切换动态表单 / workspace 名冲突弹确认 |
| `WorkspaceListPage.test.tsx` | 列表渲染 / 状态徽章 / 联动 🔗 |

vitest + @testing-library/react。**不 mock 后端真实响应**——用 MSW（Mock Service Worker）模拟 API + SSE，对齐子项目 1 的 ndjson schema。

---

## 8. 与子项目 1 的契约对齐

- **ndjson schema**：`src/api/types.ts` 的 `NdjsonEvent` 类型严格对齐总体 spec「ndjson 事件 schema」节（通用字段 + 各 type 附加字段）。若后端实际产出与 schema 偏差，回总体 spec 改并同步两边。
- **API 响应**：`src/api/types.ts` 的 Workspace/Scan/Deliverables 类型对齐子项目 1 的 API 响应模型。
- **SSE**：`useEventSource` 消费的 `data: {json}` 格式对齐子项目 1 EventTailer 的 SSE 编码。
- **冒烟依赖**：前端冒烟需子项目 1 后端跑起来（Vite proxy），或用 MSW 离线测。

---

## 9. 风险

1. **reducer 与 core 漂移**：core 的 `DashboardState.apply` 若将来改动，前端 reducer 不会自动同步。缓解：reducer 对齐测试 + 总体 spec 契约稳定性条款（改 core apply 要同步前端）。
2. **大日志文件渲染**：`agents/*.log` 可达 100KB+，全量渲染卡顿。缓解：虚拟滚动（react-window）或分页 tail。
3. **报告结构差异**：两种报告结构（静态分析 vs 利用证据）TOC 生成要兼容。缓解：MarkdownView 从实际标题动态生成 TOC，不写死结构。
4. **SSE 长连接稳定性**：扫描可能跑几十分钟，SSE 连接稳定性 + 重连续传是体验关键。缓解：useEventSource 重连 + Last-Event-ID 续传 + 状态条显示连接状态。
