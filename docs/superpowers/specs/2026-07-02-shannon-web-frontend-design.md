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

扫描类型 3 选 1（白盒/黑盒/联动）→ 动态表单（总体 spec 表单示意图），**统一 segmented 入口、按类型动态显隐字段**：

- **白盒/黑盒区**：代码来源（本地路径 / git URL）；git URL 模式显示分支/commit 可选 + 强制重新 clone；目标 URL；workspace 名（可空自动生成）；仅黑盒显示「复用最新白盒结果」复选框。
  - **黑盒 `--latest` 软默认陷阱**（主 spec §扫描类型）：CLI 有软默认 `--latest`（无 flag 也尝试复用按 url 匹配的最近白盒）。前端复选框：勾选 → 传 `--latest`；**不勾选 → 后端传 `--repo` 显式 standalone 规避软默认复用**。复选框旁 ⓘ 标注此语义，避免用户困惑。
- **联动区**：multi-repo.yaml 上传 / 从已有选（`GET /api/multi-configs`）/ 手写编辑器（YamlEditor），「保存为配置」或「直接运行」。
- **workspace 名冲突校验**：填写时 `GET /api/workspaces` 查重，同名弹确认「将断点续扫」（CLI `-w` 语义=存在则恢复，诚实呈现「恢复已有进度」，非 generic name-exists 错误）。
- **提交 `POST /api/scan`**：成功 → 202 → 跳 `/p/{ws}/live`。**失败错误码**：400（Temporal `localhost:7233` 未就绪）/ 409（并发超限）/ 422（联动 yaml 校验失败，附行号）—— 前端按码显示可操作提示（如 400 提示「先启动 Temporal」）。

---

## 3. 详情子页 5 tab

### 3.1 概览 tab（`OverviewTab.tsx`）

`GET /api/workspaces/{ws}` → 渲染 `session.json` 指标。**兼容新旧两格式**（扁平 / 嵌套 `session` 子对象；后端 SessionManager 归一，前端消费归一后响应，TS 类型含两格式可选）。

真实字段（以 NodeGoat `session.json` 为准）：顶层 `web_url`/`repo_path`/`created_at`(unix)/`scan_type`/`status`/`completed_at`/`links`{parent_workspace,child_workspaces}/`deliverables_summary`/`completed_agents`/`metrics`/`session`(嵌套旧格式)；`metrics` = `total_duration_ms`/`total_cost_usd`/`phases`/`agents`。

**⚠ status 矛盾兜底 + flag 后端**：真实存在顶层 `status:"running"` 与嵌套 `session.status:"completed"` 不一致（顶层未随 session 结束回写，疑似后端归一 bug）。前端：优先消费归一后 status；两源矛盾时状态条显示矛盾标注、**不假装单一**；此为**子项目 1 后端归一待修问题，需 flag 给后端**。

呈现（signature = 阶段瀑布）：
- **状态条**：StatusBadge + `scan_type` + `web_url` + `repo_path` + 时间区间（`created_at`→`completed_at`）。
- **大数字**（Plex Mono）：`total_cost_usd` / `total_duration_ms`(→分秒) / agent 数。
- **阶段瀑布（signature）**：`metrics.phases` **按实际 key 动态渲染**（阶段集不写死 —— NodeGoat 为 `pre-recon`/`recon`/`vulnerability-analysis`/`reporting` 4 阶段、**无 exploitation**、名带 `-analysis`；黑盒/联动阶段集不同）。横向条宽度 = `duration_percentage`，标注 `duration_ms`(→分秒) / `cost_usd` / `agent_count`，一眼看出主战场阶段。
- **agent 明细**（等宽台账）：`metrics.agents`（key=agent 名 → `duration_ms`/`cost_usd`/`success`/`attempt_number`/`model`/可选 `error`）。**分级标色**：`success:false`→红；`attempt_number>1` 或 `error` 存在→黄（重试/警告，如 injection-vuln 撞 429 重试）；正常→默认。不把重试误判为失败。
- **联动 workspace**：`links.child_workspaces` 非空时显「下钻」区，列子白盒 ws，点击跳子 ws 详情。

跨 tab 统一：等宽台账风格（WorkspaceListPage）、Plex Mono 数据、语义色。

### 3.2 报告 tab（`ReportTab.tsx`）

`GET /api/workspaces/{ws}/report` → md 原文 → `MarkdownView` 渲染。

**报告结构**（以真实 `comprehensive_security_assessment_report.md` 为准 —— 单结构、按漏洞类型组织；**非早期探索概括的「静态/利用证据两种结构」，该概括已废弃**）：

1. `## 执行摘要` —— 总体结论 + **「最高风险发现」编号列表**（按业务影响排序，每条带 vuln ID + 类型 + 可达性 + 一句话）+ 修复优先级建议。→ ReportTab 的 **hero**：置顶展开、可折叠，每条锚链到正文对应漏洞。
2. `## 按漏洞类型汇总` —— 每类型一个 `### {Type}` 块（`Count` / `Severity range` / `Key findings`）→ TOC 类型行 + 类型概览的数据源。
3. 类型详情 —— `## {Type}`（Injection / Cross-Site Scripting (XSS) / Authentication / Authorization (AuthZ) / Server-Side Request Forgery (SSRF)）→ 每漏洞一个 `### {ID}: {title}`，正文为加粗键值字段。

**每漏洞字段两种变体**，MarkdownView 都渲染成对齐键值行（非普通列表）：Injection 类用 `- **key:** value` 列表（`vulnerability_type` / `externally_exploitable` / `source` / `sink_call` / `verdict` / `witness_payload` / `confidence` / `notes` 等）；XSS/Auth/Authz/SSRF 类用 `**Summary:**` 下 `- **key:** value` + 收尾 `**Notes:**`。

**呈现决策（signature）**：
- **可达性索引，不做 severity 色点** —— severity 在真实报告稀疏不统一（仅类型汇总节有 range、个别 `Verdict（High）` 带过），撑不起逐条色点；改用 `externally_exploitable: true`（几乎每条都有、语义关键 = 公网可达优先修）做 TOC 漏洞项标记（●可达 / ○内部），类型行带 `Count + Severity range`。
- **witness_payload = PoC 代码块** —— 真实利用证据是单字符串 `witness_payload`（非虚构的 Steps + Proof JSON），渲染成等宽代码块 + 一键复制。
- 布局：执行摘要 hero 置顶 + 左 TOC（按类型分组 + 可达性 ●）右正文（键值对齐 + witness PoC）。

MarkdownView 用 `react-markdown` + `rehype-highlight` + `rehype-slug` + `rehype-autolink-headings`（目录锚点）。长报告可折叠。**无表格**（真实报告无 markdown 表格），保留 GFM 表格能力以防未来。

### 3.3 产物 tab（`DeliverablesTab.tsx`）

`GET /api/workspaces/{ws}/deliverables` → 产物清单 + 文件树。两区域：

**① 漏洞聚合网格（signature，置顶）** —— 跨所有 `*_exploitation_queue.json` 聚合 `vulnerabilities[]`，一屏看全（终端要 cat 多个 queue 自己拼，这是 web 增量价值）。每张卡片：
- 标题行：`ID` + `vulnerability_type` + 徽章组
- 徽章：`externally_exploitable` ●可达（跨 ReportTab 统一）+ **`merge_source` 双轨徽章**（`llm-only`→[💭LLM轨] / `gitnexus-only`→[🔍GN轨] / `both`→[✓双轨确认]，色板对齐 LiveTab：LLM magenta / GitNexus cyan）+ `confidence`
- 摘要：`source_endpoint` · `vulnerable_code_location`
- 展开详情：`missing_defense` / `exploitation_hypothesis` / `suggested_exploit_technique` / `notes`（大段，Witness payloads 嵌文本内）

> 真实字段说明：exploitation_queue 的 `evidence_chain`/`source_track`/`witness_payload`/`path`/`verdict` 在 NodeGoat **全为 null**，不作为卡片字段；真实可用结构化字段为 `missing_defense`/`exploitation_hypothesis`/`suggested_exploit_technique`。
> **injection 类无独立 queue**（真实数据缺口，NodeGoat 仅 `injection_analysis_deliverable.md` + 报告 md）：聚合网格标注「injection 漏洞见报告 / analysis_deliverable」，不假装聚合全。

**② 文件树**（`FileTree` 渲染 deliverables 目录，含 `whitebox/`/`blackbox/`，新旧布局后端已归一），点击按类型分流：
- `*_exploitation_queue.json` → 复用①的漏洞卡片（不另起 JSON 树）
- `*_findings.md` → MarkdownView，**标注「≈ 对应 `*_llm_queue.json`」**（两者逐字同内容，不重复呈现）
- 其他 `.md`（`comprehensive_report` / `*_analysis_deliverable.md` / `recon` / `pre_recon` / `code_index_summary`）→ MarkdownView；`comprehensive_report.md` 标「⤴ 跳 ReportTab」
- 空数组 JSON（`attack_chains.json` / `route_chains.json` / `*_gitnexus_queue.json` 常态 `[]`）→ 「无数据（常态空）」优雅空态
- 大 JSON（`code_index.json` 可达 100KB+）→ JSON 树查看器 + **虚拟滚动**（react-window）
- 其他 `.json`（`audit_plan` / `entry_points` / `parameter_graph` / `framework_analysis` / `frontend_mapping` / `auth_config_scan`）→ JSON 树查看器（折叠/展开）
- 其他类型 → 下载

**跨 tab 统一**：可达性 ●、双轨语义色（LLM 💭 magenta / GitNexus 🔍 cyan）与 LiveTab 一致。

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
| `MarkdownView.test.tsx` | 执行摘要 hero/折叠 + TOC 按类型+可达性 ● + 键值对齐 + witness PoC + 两种字段变体 |
| `DeliverablesTab.test.tsx` | 漏洞聚合网格 + `merge_source` 双轨徽章 + 可达性 ● + injection 无 queue 标注 + findings≈llm_queue 等价 + 空态 / 大 JSON 虚拟滚动 |
| `OverviewTab.test.tsx` | 阶段瀑布(动态阶段集) + 大数字 + agent 明细 `attempt_number`/`error` 重试分级标色 + status 矛盾兜底 + 联动 child_workspaces |
| `YamlEditor.test.tsx` | yaml parse 校验 / 保存/直接运行 |
| `ScanNewPage.test.tsx` | 3 类 segmented 切换动态字段 + 黑盒 `--latest` 勾选/不勾选传参 + workspace 名冲突断点续扫确认 + 提交错误码(400/409/422)处理 |
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
3. **报告字段两变体**：Injection 类 `- **key:** value` 与 XSS/Auth/Authz/SSRF 类 `**Summary:**`+`**Notes:**` 结构不同，键值渲染要兼容。缓解：MarkdownView 检测 `### {ID}` 下两种加粗键值包裹、统一渲染成对齐键值行；TOC 从真实 H2/H3 动态生成，不写死结构。
4. **SSE 长连接稳定性**：扫描可能跑几十分钟，SSE 连接稳定性 + 重连续传是体验关键。缓解：useEventSource 重连 + Last-Event-ID 续传 + 状态条显示连接状态。
