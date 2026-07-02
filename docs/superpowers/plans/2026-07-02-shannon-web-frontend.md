# Shannon Web 前端（子项目 2）实现 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 shannon web 平台前端 SPA——React + Vite + TS 脚手架 + 2 主页面（项目列表 / 开启扫描）+ 详情子页 5 tab（overview/report/deliverables/logs/live）+ 6 组件 + `dashboardReducer`（1:1 复刻 core `DashboardState.apply`）+ SSE hook + 单测，对着子项目 1 已定稿的 ndjson 契约 + REST API；MSW 模拟离线测，Vite proxy 接后端。

**Architecture:** SPA 消费子项目 1 的 REST + SSE；LiveTab 的 SSE ndjson 事件流经 `dashboardReducer`（复刻 core apply）累积成快照驱动状态条；其余 tab 消费 REST（workspaces/report/deliverables/logs）。**LiveTab 对齐 core rich renderer（STYLE_MAP），不重新设计实时显示**。设计语言：深色 operator-console + IBM Plex 三角色 + 等宽台账 + 跨 LiveTab 语义色。

**Tech Stack:** React 18、Vite、TypeScript（strict）、react-router-dom、react-markdown + rehype-highlight + rehype-slug + rehype-autolink-headings、@monaco-editor/react、react-window（虚拟滚动）、vitest + @testing-library/react + MSW（对齐 ndjson 契约，**不 mock 后端真实响应**）。

**Spec 来源:** 上位 `docs/superpowers/specs/2026-07-02-shannon-web-platform-design.md`（ndjson schema 硬契约 / 信息架构 / 扫描类型 / 错误处理）；实现细节 `docs/superpowers/specs/2026-07-02-shannon-web-frontend-design.md`（commit `9a785577` 已对齐真实 `comprehensive_report.md`/`session.json`/`*_queue.json`）；后端契约 `docs/superpowers/specs/2026-07-02-shannon-web-backend-design.md`（API/SSE）。

## Global Constraints

- **ndjson schema 是前后端 + core 三方硬契约**（主 spec §ndjson）：每行 = 通用字段 `{ts, category, type}` + 各 event type 附加字段；收尾 `{type:"scan_end", status, returncode?, stderr_tail?}`；联动 `{type:"correlation_progress", node, name, status, detail?}`。`src/api/types.ts` 严格对齐，不得擅改；偏差回主 spec 同步两边。
- **`dashboardReducer` 1:1 复刻 core** `packages/core/src/shannon_core/display/dashboard_state.py:70-132` 的 `DashboardState.apply`（6 分支：Phase/Step/Agent/ToolCall/LlmTurn/Resume；Error/Summary/WorkflowHeader 无状态变化）+ `first_nonempty_line` + `humanize_tool_call`（`formatters.py`）。**reducer 对齐测试是前端独立可信基石**（spec §4.1）——给定事件序列断言快照，与 core `test_dashboard_state.py` 同输入同输出。
- **设计语言**（commit `9a785577`）：深色底 `#0B0F14`（非纯黑）+ IBM Plex 三角色（Mono 数据 / Sans UI / Serif 报告）+ 等宽台账 + 语义色（跨 LiveTab：LLM 💭 magenta / GitNexus 🔍 cyan / 可达性 ● = `externally_exploitable` / 状态 ●running ✓done ✗failed ⚠warn）。operator-console 方向（避 AI 三默认）。
- **各 tab signature**（spec §3）：ReportTab=执行摘要 hero + 可达性索引 + witness PoC；DeliverablesTab=漏洞聚合网格 + `merge_source` 双轨徽章；OverviewTab=阶段瀑布 + 重试警告分级；ScanNewPage=3 类统一入口 + 断点续扫确认；WorkspaceListPage=等宽台账 + status 色条 + 联动树。
- **测试用 MSW 模拟 API + SSE**，对齐 ndjson schema（spec §7），**不 mock 后端真实响应**；reducer 测试与 core 对齐。
- **语言**：代码标识符英文，注释/文案可中文；commit 用 conventional commits（`feat(web-fe): ...`）。
- **包位置**：`packages/web/frontend/`（子项目 1 的 `packages/web` 是后端；前端是其后端的子目录 SPA，生产由后端静态托管或 nginx，dev 走 Vite proxy）。**不改后端代码、不改 core**（子项目 1 已交付契约）。
- **Vite proxy**：`/api` → `http://localhost:7878`（dev）；`/api/workspaces/{ws}/events` 是 SSE。

## File Structure

```
packages/web/frontend/
├── package.json               react/react-router-dom/react-markdown/rehype-*/@monaco-editor/react/react-window/vitest/@testing-library/react/msw
├── vite.config.ts             Vite proxy /api → localhost:7878
├── tsconfig.json              strict
├── vitest.config.ts           jsdom + setup
├── index.html                 IBM Plex 字体加载
├── src/
│   ├── main.tsx               入口
│   ├── App.tsx                react-router 路由壳
│   ├── router.tsx             路由定义
│   ├── pages/
│   │   ├── ScanNewPage.tsx
│   │   └── WorkspaceListPage.tsx
│   ├── routes/WorkspaceDetail/
│   │   ├── index.tsx          路由壳 + tab 导航(Outlet) + 默认 tab 选择
│   │   ├── OverviewTab.tsx
│   │   ├── ReportTab.tsx
│   │   ├── DeliverablesTab.tsx
│   │   ├── LogsTab.tsx
│   │   └── LiveTab.tsx
│   ├── components/
│   │   ├── DashboardPanel.tsx     LiveTab 状态条 + 运行中 agent（对齐 core rich 框）
│   │   ├── LogStream.tsx          SSE 滚动日志（按 category 上色 STYLE_MAP）
│   │   ├── MarkdownView.tsx       报告渲染：执行摘要 hero + TOC + 键值 + witness PoC
│   │   ├── YamlEditor.tsx         @monaco-editor/react + 校验
│   │   ├── FileTree.tsx           deliverables 目录树
│   │   └── StatusBadge.tsx        ●✓✗⚠ + 🔗联动
│   ├── api/
│   │   ├── client.ts              fetch 封装（+ 错误码 400/409/422 处理）
│   │   ├── useEventSource.ts      SSE hook（累积 + 重连带 Last-Event-ID + scan_end 关闭）
│   │   └── types.ts               NdjsonEvent 联合(11 type) + API 响应类型
│   ├── state/
│   │   ├── dashboardReducer.ts    ★ 复刻 DashboardState.apply
│   │   └── formatters.ts          first_nonempty_line + humanize_tool_call（TS 移植）
│   └── styles/
│       └── events.css             STYLE_MAP 语义色 + spinner braille 帧
└── tests/                         （组件内 .test.tsx 同目录，见各 task）
```

## 任务依赖图

```
Task 1 (脚手架) ──► Task 2 (types 契约) ──► Task 3 (reducer ★)
                                         ──► Task 4 (api client + useEventSource)
Task 3+4 ──► Task 5 (events.css + StatusBadge) ──┬─► Task 6 (MarkdownView)
                                                 ├─► Task 7 (YamlEditor + FileTree)
                                                 └─► Task 8 (DashboardPanel + LogStream)
Task 2-8 ──► Task 9 (WorkspaceListPage)  +  Task 10 (ScanNewPage)
Task 6-8 ──► Task 11 (ReportTab + DeliverablesTab)
Task 8 + 11 ──► Task 12 (OverviewTab + LogsTab + LiveTab + WorkspaceDetail 壳 + router + 冒烟)
```

---

## Task 1: 工程脚手架（Vite + TS + 依赖 + vitest + proxy）

搭起可跑的 React+Vite+TS 工程，vitest 能跑一个冒烟测试，Vite proxy 配好。后续所有 task 在此基础上加文件。

**Files:**
- Create: `packages/web/frontend/package.json`
- Create: `packages/web/frontend/vite.config.ts`
- Create: `packages/web/frontend/tsconfig.json`
- Create: `packages/web/frontend/vitest.config.ts`
- Create: `packages/web/frontend/index.html`
- Create: `packages/web/frontend/src/main.tsx`
- Create: `packages/web/frontend/src/App.tsx`（最小占位，Task 12 替换）
- Test: `packages/web/frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: 无（起点）
- Produces: 可跑的 Vite dev server（`npm run dev`）+ vitest（`npm test`）+ 构建产物（`npm run build`）；`/api` 代理到后端。

- [ ] **Step 1: 写 `package.json`**

```json
{
  "name": "shannon-web-frontend",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.26.0",
    "react-markdown": "^9.0.1",
    "rehype-highlight": "^7.0.0",
    "rehype-slug": "^6.0.0",
    "rehype-autolink-headings": "^7.1.0",
    "@monaco-editor/react": "^4.6.0",
    "react-window": "^1.8.10"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@types/react-window": "^1.8.8",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.5.4",
    "vite": "^5.4.0",
    "vitest": "^2.0.5",
    "@testing-library/react": "^16.0.0",
    "@testing-library/jest-dom": "^6.4.8",
    "jsdom": "^24.1.1",
    "msw": "^2.3.5"
  }
}
```

- [ ] **Step 2: 写 `vite.config.ts`（含 proxy + 别名）**

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:7878",
        changeOrigin: true,
        // SSE: /api/workspaces/:ws/events 不缓冲
        ws: false,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    globals: true,
  },
});
```

- [ ] **Step 3: 写 `tsconfig.json`（strict）**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"],
    "skipLibCheck": true
  },
  "include": ["src"]
}
```

- [ ] **Step 4: 写 `vitest.config.ts`（复用 vite config 的 test 段）**

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"], globals: true },
});
```

- [ ] **Step 5: 写 `src/test-setup.ts`**

```ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 6: 写 `index.html`（含 IBM Plex 字体）**

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link
      href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Serif:wght@400;500&display=swap"
      rel="stylesheet"
    />
    <title>Shannon Web</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 7: 写失败测试 `src/App.test.tsx`（冒烟：渲染标题）**

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import App from "./App";

describe("App", () => {
  it("renders the Shannon Web title", () => {
    render(<App />);
    expect(screen.getByText(/Shannon Web/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 8: 跑测试确认失败**

Run: `cd packages/web/frontend && npm install && npm test`
Expected: FAIL（`App` 不存在或无 `main.tsx`）

- [ ] **Step 9: 写 `src/main.tsx` + `src/App.tsx` 最小实现**

```tsx
// src/main.tsx
import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

```tsx
// src/App.tsx（最小占位，Task 12 替换为 router）
export default function App() {
  return <h1>Shannon Web</h1>;
}
```

- [ ] **Step 10: 跑测试确认通过**

Run: `cd packages/web/frontend && npm test`
Expected: PASS（1 test）

- [ ] **Step 11: 验证 dev server + 类型**

Run: `cd packages/web/frontend && npm run build`
Expected: tsc + vite build 成功（无类型错误）

- [ ] **Step 12: Commit**

```bash
git add packages/web/frontend/
git commit -m "feat(web-fe): React+Vite+TS 脚手架 + vitest + Vite proxy(/api→7878)"
```

---

## Task 2: types.ts — ndjson 契约 + API 响应类型

钉死前端与后端的类型契约。ndjson `NdjsonEvent` 严格对齐主 spec §ndjson（11 种 event type + scan_end + correlation_progress）。API 响应类型对齐后端 `backend-design.md` 端点。

**Files:**
- Create: `packages/web/frontend/src/api/types.ts`
- Test: `packages/web/frontend/src/api/types.test.ts`

**Interfaces:**
- Consumes: 主 spec §ndjson schema、backend-design.md API 模型
- Produces: `NdjsonEvent`（判别联合，`type` 字段区分）、`Workspace`、`ScanRequest`、`ScanResponse`、`DeliverablesSummary`、`Vulnerability`（队列卡片）、`SessionData`（overview）

- [ ] **Step 1: 写失败测试 `types.test.ts`（类型可构造 + 判别字段）**

```ts
import { describe, it, expect } from "vitest";
import type { NdjsonEvent, Workspace, Vulnerability } from "./types";

describe("NdjsonEvent", () => {
  it("PhaseEvent has common + phase fields", () => {
    const ev: NdjsonEvent = {
      ts: "2026-07-02T09:44:01.123Z",
      category: "PHASE",
      type: "PhaseEvent",
      phase: "recon",
      event: "start",
      steps: ["s1", "s2"],
      step_intents: ["", ""],
    };
    expect(ev.type).toBe("PhaseEvent");
    expect(ev.phase).toBe("recon");
  });

  it("scan_end control row is a NdjsonEvent", () => {
    const ev: NdjsonEvent = {
      ts: "2026-07-02T09:50:00.000Z",
      category: "CONTROL",
      type: "scan_end",
      status: "completed",
      returncode: 0,
    };
    expect(ev.type).toBe("scan_end");
  });

  it("Vulnerability carries externally_exploitable + merge_source", () => {
    const v: Vulnerability = {
      ID: "SSRF-VULN-01",
      vulnerability_type: "URL_Manipulation",
      externally_exploitable: true,
      merge_source: "llm-only",
      confidence: "needs_review",
      source_endpoint: "GET /research",
    };
    expect(v.merge_source).toBe("llm-only");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/web/frontend && npm test -- types`
Expected: FAIL（`types.ts` 不存在）

- [ ] **Step 3: 写 `src/api/types.ts`**

```ts
// === ndjson 事件 schema（主 spec §ndjson 三方硬契约）===
// 通用字段每行必有；各 type 附加字段见主 spec 表。

export type EventCategory =
  | "PHASE" | "STEP" | "AGENT" | "TOOL" | "LLM" | "ERROR"
  | "INFO" | "WARN" | "RESUME" | "SUMMARY" | "HEADER" | "GITNEXUS" | "CONTROL";

interface CommonFields {
  ts: string;          // ISO8601 UTC 毫秒
  category: EventCategory;
}

export interface WorkflowHeaderEvent extends CommonFields {
  type: "WorkflowHeader";
  workflow_id: string; target_url: string; repo_path: string;
  mode: string; web_ui_url: string; logs_cmd: string; workspace: string;
}
export interface PhaseEvent extends CommonFields {
  type: "PhaseEvent"; phase: string; event: "start" | "complete";
  steps: string[]; step_intents: string[];
}
export interface StepEvent extends CommonFields {
  type: "StepEvent"; name: string; phase: string; event: "start" | "complete";
  duration_ms?: number; error?: string; intent?: string;
}
export interface AgentEvent extends CommonFields {
  type: "AgentEvent"; agent_name: string; event: "start" | "end";
  attempt: number; duration_ms?: number; cost_usd?: number;
  success?: boolean; error?: string;
}
export interface ToolCallEvent extends CommonFields {
  type: "ToolCallEvent"; agent_name: string; tool_name: string; parameters?: unknown;
}
export interface LlmTurnEvent extends CommonFields {
  type: "LlmTurnEvent"; agent_name: string; turn: number; content: string;
}
export interface InfoEvent extends CommonFields {
  type: "InfoEvent"; message: string; level: "info" | "warning";
}
export interface ErrorEvent extends CommonFields {
  type: "ErrorEvent"; error_type: string; message: string; context?: string;
  classified?: string; display_retryable?: boolean; attempt?: number;
  max_attempts?: number; detail_path?: string;
}
export interface SummaryEvent extends CommonFields {
  type: "SummaryEvent"; status: string; total_duration_ms?: number;
  total_cost_usd?: number; agents?: Array<{ name: string; duration_ms?: number; cost_usd?: number; success?: boolean }>;
  error?: string;
}
export interface ResumeEvent extends CommonFields {
  type: "ResumeEvent"; previous_workflow_id: string; new_workflow_id: string;
  checkpoint_hash: string; completed_agents: string[];
}
export interface GitnexusLlmEvent extends CommonFields {
  type: "GitnexusLlmEvent";
  // 字段随 events.py GitnexusLlmEvent；前端按需透传
  [k: string]: unknown;
}
export interface ScanEndEvent extends CommonFields {
  type: "scan_end"; status: "completed" | "failed" | "killed" | "crashed";
  returncode?: number; stderr_tail?: string;
}
export interface CorrelationProgressEvent extends CommonFields {
  type: "correlation_progress"; node: "repo" | "edge"; name: string;
  status: "started" | "completed" | "failed"; detail?: string;
}

export type NdjsonEvent =
  | WorkflowHeaderEvent | PhaseEvent | StepEvent | AgentEvent | ToolCallEvent
  | LlmTurnEvent | InfoEvent | ErrorEvent | SummaryEvent | ResumeEvent
  | GitnexusLlmEvent | ScanEndEvent | CorrelationProgressEvent;

// === API 响应类型（对齐 backend-design.md）===
export type WorkspaceStatus = "running" | "completed" | "failed" | "killed" | "crashed";

export interface Workspace {
  name: string;
  scan_type: "whitebox" | "blackbox" | "correlation";
  status: WorkspaceStatus;          // 归一后（见 §3.1 status 矛盾兜底）
  created_at: number;               // unix
  completed_at?: number | null;
  vuln_count?: number;
  total_cost_usd?: number;
  total_duration_ms?: number;
  links?: { parent_workspace?: string | null; child_workspaces?: string[] };
}

export interface SessionMetrics {
  total_duration_ms: number;
  total_cost_usd: number;
  // 阶段集动态（NodeGoat: pre-recon/recon/vulnerability-analysis/reporting）
  phases: Record<string, {
    duration_ms: number; duration_percentage: number; cost_usd: number; agent_count: number;
  }>;
  agents: Record<string, {
    duration_ms: number; cost_usd: number; success: boolean;
    attempt_number: number; model: string; error?: string;
  }>;
}

export interface SessionData {
  web_url?: string; repo_path?: string; created_at?: number;
  scan_type?: string;
  status?: string;                // 顶层（可能未回写）
  completed_at?: number | null;
  links?: { parent_workspace?: string | null; child_workspaces?: string[] };
  metrics?: SessionMetrics;
  session?: { status?: string; createdAt?: string; id?: string };  // 嵌套旧格式
}

export type MergeSource = "llm-only" | "gitnexus-only" | "both" | string;

export interface Vulnerability {
  ID: string;
  vulnerability_type: string;
  externally_exploitable: boolean;
  confidence?: string;
  source_endpoint?: string;
  vulnerable_code_location?: string;
  vulnerable_parameter?: string;
  merge_source?: MergeSource;       // exploitation_queue 独有
  missing_defense?: string;
  exploitation_hypothesis?: string;
  suggested_exploit_technique?: string;
  notes?: string;
  // exploitation_queue 里常 null 的字段（保留可选）
  evidence_chain?: unknown; source_track?: unknown;
  witness_payload?: string | null; path?: string | null; verdict?: string | null;
}

export interface DeliverablesFile {
  path: string;        // 相对 deliverables/{track}/ 的路径
  size: number;
  kind: "md" | "exploitation_queue" | "llm_queue" | "gitnexus_queue"
      | "empty_json" | "big_json" | "other_json" | "other";
}

export interface DeliverablesSummary {
  track: "whitebox" | "blackbox";
  files: DeliverablesFile[];
  // 聚合用：跨所有 *_exploitation_queue.json 的 vulnerabilities
  aggregated_vulnerabilities: Vulnerability[];
  notes?: { injection_has_no_queue?: boolean };
}

export interface ScanRequest {
  type: "whitebox" | "blackbox" | "correlation";
  source?: { kind: "path" | "git"; value: string; branch?: string; commit?: string; force_reclone?: boolean };
  url?: string;
  workspace_name?: string;
  reuse_latest_whitebox?: boolean;   // 黑盒 --latest
  config_yaml?: string;              // 联动手写
  config_name?: string;              // 联动从已有选
}

export interface ScanResponse {
  workspace: string;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd packages/web/frontend && npm test -- types`
Expected: PASS（3 tests）

- [ ] **Step 5: 类型检查**

Run: `cd packages/web/frontend && npx tsc --noEmit`
Expected: 无错误

- [ ] **Step 6: Commit**

```bash
git add packages/web/frontend/src/api/types.ts packages/web/frontend/src/api/types.test.ts
git commit -m "feat(web-fe): types.ts 钉死 ndjson 11-type 契约 + API 响应类型"
```

---

## Task 3: dashboardReducer ★ — 1:1 复刻 core `DashboardState.apply`（基石）

前端独立可信的基石（spec §4.1）。把 ndjson 事件流累积成快照，逻辑 1:1 复刻 `packages/core/src/shannon_core/display/dashboard_state.py:70-132`，含 `first_nonempty_line` + `humanize_tool_call`（TS 移植 `formatters.py`）。**对齐测试与 core `test_dashboard_state.py` 同输入同输出**。

**Files:**
- Create: `packages/web/frontend/src/state/formatters.ts`
- Create: `packages/web/frontend/src/state/dashboardReducer.ts`
- Test: `packages/web/frontend/src/state/dashboardReducer.test.ts`

**Interfaces:**
- Consumes: `NdjsonEvent`（Task 2）；core 源 `dashboard_state.py:70-132` + `formatters.py:167-200`（复刻对照）
- Produces: `AgentRow`、`DashboardState`（TS 接口）、`dashboardReducer(state, event) → state`、派生选择器 `selectCompletedCount/selectTotalCost/selectTotalUnits/selectCompletedUnits/selectRunningUnits`、`first_nonempty_line`、`humanizeToolCall`

**复刻对照（core → TS，逐分支）**：apply 6 分支（PhaseEvent start/complete、StepEvent、ResumeEvent、AgentEvent start/end、ToolCallEvent、LlmTurnEvent）+ ErrorEvent/SummaryEvent/WorkflowHeader 无状态变化（return state）。`_set_unit` 仅当 name ∈ phase_units 才更新。派生属性同 core（completed_count = agents 中 done|failed 数；total_cost = sum cost_usd；total_units = phase_units.length；completed_units = unit_status 中 done|failed 数；running_units = phase_units 中 unit_status==running）。

- [ ] **Step 1: 写 `src/state/formatters.ts`（TS 移植 `formatters.py:167-200`）**

```ts
// 1:1 移植 packages/core/src/shannon_core/display/formatters.py
// first_nonempty_line + humanize_tool_call（含 default 分支；Task/Bash/TodoWrite 简化对齐 core）

/** 返回第一个非空 stripped 行，无则 ""。对齐 formatters.py:167 first_nonempty_line。 */
export function firstNonemptyLine(text: string | null | undefined): string {
  for (const line of (text ?? "").split(/\r?\n/)) {
    const stripped = line.trim();
    if (stripped) return stripped;
  }
  return "";
}

function defaultToolParams(toolName: string, params: Record<string, unknown>): string {
  // 对齐 formatters.py default_tool_params：tool_name + 关键参数截断
  const keys = Object.keys(params ?? {});
  if (keys.length === 0) return toolName;
  const first = String(params[keys[0]] ?? "").slice(0, 60);
  return `${toolName}  ${first}`;
}

/** 把原始 tool call 转成人读单行。对齐 formatters.py:180 humanize_tool_call。 */
export function humanizeToolCall(toolName: string, params: unknown): string {
  const p = (params && typeof params === "object") ? params as Record<string, unknown> : {};
  switch (toolName) {
    case "Task":
      return `🚀 Launching ${String(p["description"] ?? "analysis agent")}`;
    case "TodoWrite":
      // core summarize_todo 提取待办首条；此处简化为 tool 名（覆盖 core summarize_todo 逻辑见 formatters.py）
      return summarizeTodo(p) ?? "TodoWrite";
    case "Bash": {
      const browser = maybeBrowserAction(p);
      return browser ?? defaultToolParams(toolName, p);
    }
    default:
      return defaultToolParams(toolName, p);
  }
}

function summarizeTodo(params: Record<string, unknown>): string | null {
  // 对齐 formatters.py summarize_todo：取 todos 数组第一条 status==pending 的 content
  const todos = params["todos"];
  if (!Array.isArray(todos)) return null;
  const pending = todos.find((t) => t?.status === "pending");
  return pending?.content ? String(pending.content) : null;
}

function maybeBrowserAction(params: Record<string, unknown>): string | null {
  // 对齐 formatters.py maybe_browser_action：识别浏览器导航/点击动作
  const cmd = String(params["command"] ?? "");
  if (cmd === "navigate") return `🌐 ${params["url"] ?? ""}`;
  if (cmd === "click") return `👆 ${params["selector"] ?? ""}`;
  return null;
}
```

> 注：`summarizeTodo`/`maybeBrowserAction`/`defaultToolParams` 是 `humanize_tool_call` 的依赖（formatters.py 同名）。TS 版保持纯函数语义；若 core 实现细节有调整，以 `formatters.py` 为准回填——reducer 对齐测试（Step 4）会锁定行为。

- [ ] **Step 2: 写失败测试 `dashboardReducer.test.ts`（对齐 core `test_dashboard_state.py` 用例）**

```ts
import { describe, it, expect } from "vitest";
import { dashboardReducer, emptyState, type DashboardState } from "./dashboardReducer";
import type { NdjsonEvent } from "../api/types";

function ev(e: Partial<NdjsonEvent> & { type: NdjsonEvent["type"] }): NdjsonEvent {
  return { ts: "2026-07-02T09:44:01.123Z", category: "PHASE", ...e } as NdjsonEvent;
}

describe("dashboardReducer — 对齐 core DashboardState.apply", () => {
  it("PhaseEvent start: 设 current_phase + 重置 phase_units/unit_status", () => {
    const s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", category: "PHASE", phase: "recon", event: "start",
      steps: ["s1", "s2"], step_intents: ["", ""],
    }));
    expect(s.current_phase).toBe("recon");
    expect(s.phase_units).toEqual(["s1", "s2"]);
    expect(s.unit_status).toEqual({});
  });

  it("PhaseEvent complete: 保留 units", () => {
    const s1 = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", category: "PHASE", phase: "recon", event: "start",
      steps: ["s1"], step_intents: [""],
    }));
    const s2 = dashboardReducer(s1, ev({
      type: "PhaseEvent", category: "PHASE", phase: "recon", event: "complete",
      steps: ["s1"], step_intents: [""],
    }));
    expect(s2.phase_units).toEqual(["s1"]);  // complete 不清空
  });

  it("StepEvent start/complete: 更新 unit_status（_set_unit 仅声明的 unit）", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "recon", event: "start", steps: ["s1"], step_intents: [""],
    }));
    s = dashboardReducer(s, ev({ type: "StepEvent", name: "s1", phase: "recon", event: "start" }));
    expect(s.unit_status["s1"]).toBe("running");
    s = dashboardReducer(s, ev({ type: "StepEvent", name: "s1", phase: "recon", event: "complete" }));
    expect(s.unit_status["s1"]).toBe("done");
    // 未声明的 unit 被忽略
    s = dashboardReducer(s, ev({ type: "StepEvent", name: "ghost", phase: "recon", event: "start" }));
    expect(s.unit_status["ghost"]).toBeUndefined();
  });

  it("AgentEvent start/end: agents[name] 状态流转 + _set_unit", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "vulnerability-analysis", event: "start",
      steps: ["Injection"], step_intents: [""],
    }));
    s = dashboardReducer(s, ev({
      type: "AgentEvent", agent_name: "Injection", event: "start", attempt: 1,
    }));
    expect(s.agents["Injection"]?.status).toBe("running");
    expect(s.agents["Injection"]?.attempt).toBe(1);
    expect(s.unit_status["Injection"]).toBe("running");
    s = dashboardReducer(s, ev({
      type: "AgentEvent", agent_name: "Injection", event: "end",
      attempt: 1, duration_ms: 42000, cost_usd: 0.5, success: true,
    }));
    expect(s.agents["Injection"]?.status).toBe("done");
    expect(s.agents["Injection"]?.cost_usd).toBe(0.5);
    expect(s.unit_status["Injection"]).toBe("done");
  });

  it("AgentEvent end failed: status=failed", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "p", event: "start", steps: ["A"], step_intents: [""],
    }));
    s = dashboardReducer(s, ev({ type: "AgentEvent", agent_name: "A", event: "start", attempt: 1 }));
    s = dashboardReducer(s, ev({
      type: "AgentEvent", agent_name: "A", event: "end", attempt: 1, success: false, error: "boom",
    }));
    expect(s.agents["A"]?.status).toBe("failed");
    expect(s.agents["A"]?.error).toBe("boom");
  });

  it("ToolCallEvent: 更新 last_action + last_action_detail (humanizeToolCall)", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "p", event: "start", steps: ["A"], step_intents: [""],
    }));
    s = dashboardReducer(s, ev({ type: "AgentEvent", agent_name: "A", event: "start", attempt: 1 }));
    s = dashboardReducer(s, ev({
      type: "ToolCallEvent", agent_name: "A", tool_name: "Bash", parameters: { command: "ls" },
    }));
    expect(s.agents["A"]?.last_action).toBe("Bash");
    expect(s.agents["A"]?.last_action_detail).toContain("Bash");
  });

  it("LlmTurnEvent: 更新 turn + last_turn_text (firstNonemptyLine)", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "p", event: "start", steps: ["A"], step_intents: [""],
    }));
    s = dashboardReducer(s, ev({ type: "AgentEvent", agent_name: "A", event: "start", attempt: 1 }));
    s = dashboardReducer(s, ev({
      type: "LlmTurnEvent", agent_name: "A", turn: 3, content: "\n\nAnalyzing sinks...\n",
    }));
    expect(s.agents["A"]?.turn).toBe(3);
    expect(s.agents["A"]?.last_turn_text).toBe("Analyzing sinks...");
  });

  it("ResumeEvent: completed_agents 标 done", () => {
    const s = dashboardReducer(emptyState(), ev({
      type: "ResumeEvent", previous_workflow_id: "x", new_workflow_id: "y",
      checkpoint_hash: "h", completed_agents: ["Injection", "Xss"],
    }));
    expect(s.agents["Injection"]?.status).toBe("done");
    expect(s.agents["Xss"]?.status).toBe("done");
  });

  it("ErrorEvent/SummaryEvent/WorkflowHeader: 无状态变化", () => {
    const s0 = emptyState();
    const s1 = dashboardReducer(s0, ev({ type: "ErrorEvent", category: "ERROR", error_type: "X", message: "m" }));
    const s2 = dashboardReducer(s1, ev({ type: "SummaryEvent", category: "SUMMARY", status: "completed" }));
    expect(s2).toEqual(s0);
  });

  it("派生: completed_count / total_cost / total_units / completed_units / running_units", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "p", event: "start", steps: ["A", "B"], step_intents: ["", ""],
    }));
    s = dashboardReducer(s, ev({ type: "AgentEvent", agent_name: "A", event: "start", attempt: 1 }));
    s = dashboardReducer(s, ev({
      type: "AgentEvent", agent_name: "A", event: "end", attempt: 1, success: true, cost_usd: 1.5,
    }));
    s = dashboardReducer(s, ev({ type: "AgentEvent", agent_name: "B", event: "start", attempt: 1 }));
    expect(s.completed_count).toBe(1);     // A done
    expect(s.total_cost).toBe(1.5);
    expect(s.total_units).toBe(2);
    expect(s.completed_units).toBe(1);      // A unit done
    expect(s.running_units).toEqual(["B"]);
  });
});
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd packages/web/frontend && npm test -- dashboardReducer`
Expected: FAIL（`dashboardReducer.ts` 不存在）

- [ ] **Step 4: 写 `src/state/dashboardReducer.ts`（1:1 复刻 apply）**

```ts
import type { NdjsonEvent } from "../api/types";
import { firstNonemptyLine, humanizeToolCall } from "./formatters";

export type AgentStatus = "running" | "done" | "failed";

export interface AgentRow {
  name: string;
  status: AgentStatus;
  attempt: number;
  turn: number;
  last_action: string | null;
  last_action_detail: string | null;
  last_turn_text: string | null;
  duration_ms: number | null;
  cost_usd: number | null;
  error: string | null;
}

export interface DashboardState {
  current_phase: string | null;
  agents: Record<string, AgentRow>;
  phase_units: string[];
  unit_status: Record<string, string>;
  unit_intent: Record<string, string>;
  // 派生（core 是 @property；TS 在 reducer 末尾计算并挂上，便于组件直接读）
  completed_count: number;
  total_cost: number;
  total_units: number;
  completed_units: number;
  running_units: string[];
}

export function emptyState(): DashboardState {
  return {
    current_phase: null, agents: {}, phase_units: [], unit_status: {}, unit_intent: {},
    completed_count: 0, total_cost: 0, total_units: 0, completed_units: 0, running_units: [],
  };
}

function row(name: string): AgentRow {
  return { name, status: "running", attempt: 1, turn: 0,
    last_action: null, last_action_detail: null, last_turn_text: null,
    duration_ms: null, cost_usd: null, error: null };
}

function setUnit(state: DashboardState, name: string, status: string): DashboardState {
  if (!state.phase_units.includes(name)) return state;  // 未声明的 unit 忽略
  return { ...state, unit_status: { ...state.unit_status, [name]: status } };
}

function derive(s: DashboardState): DashboardState {
  const agents = Object.values(s.agents);
  return {
    ...s,
    completed_count: agents.filter((a) => a.status === "done" || a.status === "failed").length,
    total_cost: agents.reduce((sum, a) => sum + (a.cost_usd ?? 0), 0),
    total_units: s.phase_units.length,
    completed_units: Object.values(s.unit_status).filter((st) => st === "done" || st === "failed").length,
    running_units: s.phase_units.filter((n) => s.unit_status[n] === "running"),
  };
}

/** 1:1 复刻 packages/core/src/shannon_core/display/dashboard_state.py:70-132 DashboardState.apply。 */
export function dashboardReducer(state: DashboardState, event: NdjsonEvent): DashboardState {
  let next: DashboardState = state;

  switch (event.type) {
    case "PhaseEvent":
      if (event.event === "start") {
        const intents: Record<string, string> = {};
        for (let i = 0; i < event.steps.length; i++) {
          const it = event.step_intents[i];
          if (it) intents[event.steps[i]] = it;
        }
        next = { ...state, current_phase: event.phase, phase_units: [...event.steps],
                 unit_status: {}, unit_intent: intents };
      } else {
        next = { ...state, current_phase: event.phase };  // complete: keep units
      }
      break;

    case "StepEvent": {
      const status = event.event === "start" ? "running" : (event.error ? "failed" : "done");
      let s = setUnit(state, event.name, status);
      if (event.intent) {
        s = { ...s, unit_intent: { ...s.unit_intent, [event.name]: event.intent! } };
      }
      next = s;
      break;
    }

    case "ResumeEvent": {
      const agents = { ...state.agents };
      for (const name of event.completed_agents) {
        agents[name] = { ...row(name), status: "done" };
      }
      next = { ...state, agents };
      break;
    }

    case "AgentEvent": {
      const agents = { ...state.agents };
      const cur = agents[event.agent_name] ?? row(event.agent_name);
      if (event.event === "start") {
        agents[event.agent_name] = { ...cur, status: "running", attempt: event.attempt, error: null };
        next = setUnit(state, event.agent_name, "running");
      } else {
        const status: AgentStatus = event.success === false ? "failed" : "done";
        agents[event.agent_name] = {
          ...cur, status,
          duration_ms: event.duration_ms ?? cur.duration_ms,
          cost_usd: event.cost_usd ?? cur.cost_usd,
          error: event.error ?? null,
        };
        next = setUnit(state, event.agent_name, status);
      }
      next = { ...next, agents };
      break;
    }

    case "ToolCallEvent": {
      const agents = { ...state.agents };
      const cur = agents[event.agent_name];
      if (cur) {
        const detail = humanizeToolCall(event.tool_name, event.parameters ?? {});
        agents[event.agent_name] = { ...cur, last_action: event.tool_name, last_action_detail: detail };
      }
      next = { ...state, agents };
      break;
    }

    case "LlmTurnEvent": {
      const agents = { ...state.agents };
      const cur = agents[event.agent_name];
      if (cur) {
        const line = firstNonemptyLine(event.content);
        agents[event.agent_name] = { ...cur, turn: event.turn,
          last_turn_text: line || cur.last_turn_text };
      }
      next = { ...state, agents };
      break;
    }

    // ErrorEvent / SummaryEvent / WorkflowHeader / InfoEvent / GitnexusLlmEvent /
    // ScanEndEvent / CorrelationProgressEvent → 无 dashboard 状态变化（对齐 core）
    default:
      next = state;
  }

  return derive(next);
}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd packages/web/frontend && npm test -- dashboardReducer`
Expected: PASS（10 tests，覆盖 phase 切换/step/agent start-end failed/tool/llm/resume/无状态变化/派生）

- [ ] **Step 6: 与 core `test_dashboard_state.py` 交叉核对**

打开 `packages/core/tests/display/test_dashboard_state.py`，逐个 `test_*` 用例：用其事件序列构造 `NdjsonEvent` 喂 TS reducer，断言等价快照（agents/unit_status/派生）。新增覆盖：
- core 若有「ToolCallEvent 在 agent 未 start 时丢弃」用例 → TS 同样 `cur` 为 undefined 时不创建（见 Step 4 ToolCallEvent 分支 `if (cur)`）
- core 若有「AgentEvent end 不带 cost/duration 时保留旧值」用例 → TS `?? cur.duration_ms` 对齐

Run: `cd packages/web/frontend && npm test -- dashboardReducer`
Expected: PASS（含新增交叉核对用例）

- [ ] **Step 7: 类型检查 + Commit**

Run: `cd packages/web/frontend && npx tsc --noEmit`
Expected: 无错误

```bash
git add packages/web/frontend/src/state/
git commit -m "feat(web-fe): dashboardReducer 1:1 复刻 core DashboardState.apply + 对齐测试(10+ 用例)"
```

---

## Task 4: api/client.ts + useEventSource.ts（REST 封装 + SSE hook）

REST 用 fetch 封装（统一错误码 400/409/422 → `ApiError`）；SSE 用 `EventSource` hook 累积 ndjson、scan_end 关闭、错误重连。

**Files:**
- Create: `packages/web/frontend/src/api/client.ts`
- Create: `packages/web/frontend/src/api/useEventSource.ts`
- Test: `packages/web/frontend/src/api/client.test.ts`
- Test: `packages/web/frontend/src/api/useEventSource.test.ts`

**Interfaces:**
- Consumes: `types.ts`（Task 2）
- Produces: `ApiError`、`apiGet<T>(path)`、`apiPost<T>(path, body)`、`apiDelete<T>(path)`、`useEventSource(url) → { events, status, lastEventId }`

- [ ] **Step 1: 写失败测试 `client.test.ts`（成功 + 422 抛错）**

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { apiGet, apiPost, ApiError } from "./client";

beforeEach(() => {
  globalThis.fetch = vi.fn() as unknown as typeof fetch;
});

describe("api client", () => {
  it("apiGet 解析 JSON 成功", async () => {
    (globalThis.fetch as any).mockResolvedValue({ ok: true, status: 200, json: async () => ({ name: "ws" }) });
    const r = await apiGet<{ name: string }>("/workspaces/ws");
    expect(r.name).toBe("ws");
  });

  it("apiPost 成功返回 body", async () => {
    (globalThis.fetch as any).mockResolvedValue({ ok: true, status: 202, json: async () => ({ workspace: "ws" }) });
    const r = await apiPost<{ workspace: string }>("/scan", { type: "whitebox" });
    expect(r.workspace).toBe("ws");
  });

  it("422 抛 ApiError 带 body", async () => {
    (globalThis.fetch as any).mockResolvedValue({
      ok: false, status: 422, json: async () => ({ detail: [{ loc: ["repos"], msg: "bad" }] }),
    });
    await expect(apiPost("/scan", {})).rejects.toMatchObject({ status: 422 });
    try { await apiPost("/scan", {}); } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).body).toMatchObject({ detail: [{ loc: ["repos"] }] });
    }
  });
});
```

- [ ] **Step 2: 跑测试确认失败** · Run: `npm test -- client` · Expected: FAIL（`client.ts` 不存在）

- [ ] **Step 3: 写 `src/api/client.ts`**

```ts
export class ApiError extends Error {
  constructor(public status: number, public body: unknown) { super(`API ${status}`); this.name = "ApiError"; }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let body: unknown;
    try { body = await res.json(); } catch { body = await res.text(); }
    throw new ApiError(res.status, body);
  }
  // 204/无 body
  const text = await res.text();
  return (text ? JSON.parse(text) : {}) as T;
}

export const apiGet = <T>(path: string) => request<T>(path);
export const apiPost = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });
export const apiDelete = <T>(path: string) => request<T>(path, { method: "DELETE" });
```

- [ ] **Step 4: 跑测试确认通过** · Run: `npm test -- client` · Expected: PASS（3）

- [ ] **Step 5: 写失败测试 `useEventSource.test.ts`（fake EventSource：累积 / scan_end 关闭）**

```ts
import { describe, it, expect, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useEventSource } from "./useEventSource";

// fake EventSource：构造后可手动 emit message
class FakeES {
  static last?: FakeES;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  closed = false;
  constructor(public url: string) { FakeES.last = this; }
  close() { this.closed = true; }
  emit(data: string) { this.onmessage?.({ data }); }
}
vi.stubGlobal("EventSource", FakeES);

describe("useEventSource", () => {
  it("累积事件 + scan_end 关闭", () => {
    const { result } = renderHook(() => useEventSource("/api/workspaces/ws/events"));
    expect(result.current.status).toBe("open");
    act(() => FakeES.last!.emit(JSON.stringify({ ts: "t", category: "PHASE", type: "PhaseEvent", phase: "recon", event: "start", steps: [], step_intents: [] })));
    expect(result.current.events).toHaveLength(1);
    act(() => FakeES.last!.emit(JSON.stringify({ ts: "t", category: "CONTROL", type: "scan_end", status: "completed" })));
    expect(result.current.status).toBe("closed");
    expect(FakeES.last!.closed).toBe(true);
  });
});
```

- [ ] **Step 6: 跑测试确认失败** · Run: `npm test -- useEventSource` · Expected: FAIL

- [ ] **Step 7: 写 `src/api/useEventSource.ts`**

```ts
import { useEffect, useState } from "react";
import type { NdjsonEvent } from "./types";

export type SseStatus = "open" | "closed" | "error";
export interface UseEventSource {
  events: NdjsonEvent[]; status: SseStatus; lastEventId?: string;
}

export function useEventSource(url: string): UseEventSource {
  const [events, setEvents] = useState<NdjsonEvent[]>([]);
  const [status, setStatus] = useState<SseStatus>("closed");
  const [lastEventId, setLastEventId] = useState<string | undefined>(undefined);

  useEffect(() => {
    const Es = (globalThis as { EventSource?: typeof EventSource }).EventSource;
    if (!Es) return;
    const es = new Es(url);
    setStatus("open");
    es.onmessage = (e: MessageEvent) => {
      const line = String(e.data);
      let ev: NdjsonEvent;
      try { ev = JSON.parse(line) as NdjsonEvent; } catch { return; }
      if (e.lastEventId) setLastEventId(e.lastEventId);
      if (ev.type === "scan_end") { setStatus("closed"); es.close(); }
      setEvents((prev) => [...prev, ev]);
    };
    es.onerror = () => setStatus("error");    // EventSource 内置自动重连（带 Last-Event-ID）
    es.onopen = () => setStatus("open");
    return () => es.close();
  }, [url]);

  return { events, status, lastEventId };
}
```

> 注：EventTailer 支持 `Last-Event-ID` = ndjson byte offset；浏览器 EventSource 自动重连时带 `lastEventId` 头，后端据此 seek 续传。

- [ ] **Step 8: 跑测试确认通过 + 类型检查** · Run: `npm test -- useEventSource && npx tsc --noEmit` · Expected: PASS + 无错

- [ ] **Step 9: Commit**

```bash
git add packages/web/frontend/src/api/client.ts packages/web/frontend/src/api/useEventSource.ts packages/web/frontend/src/api/client.test.ts packages/web/frontend/src/api/useEventSource.test.ts
git commit -m "feat(web-fe): api client(错误码→ApiError) + useEventSource(SSE 累积+scan_end 关闭+重连)"
```

---

## Task 5: events.css（STYLE_MAP 语义色 + spinner）+ StatusBadge

钉死跨组件语义色板（对齐 LiveTab STYLE_MAP）+ IBM Plex 字体变量 + braille spinner；StatusBadge 渲染状态图标（●✓✗⚠）+ 联动 🔗。

**Files:**
- Create: `packages/web/frontend/src/styles/events.css`
- Create: `packages/web/frontend/src/components/StatusBadge.tsx`
- Test: `packages/web/frontend/src/components/StatusBadge.test.tsx`
- Modify: `packages/web/frontend/src/main.tsx`（import events.css）

**Interfaces:**
- Consumes: 无
- Produces: CSS 变量（`--void/--panel/--ink/--cyan/--blue/--green/--red/--yellow/--magenta/--font-mono/--font-sans/--font-serif`）、`.ev-*` 色类、`.spinner`、`StatusBadge({ status, correlation? })`

- [ ] **Step 1: 写 `src/styles/events.css`**

```css
:root {
  --void: #0B0F14; --panel: #141A22; --ink: #C9D1D9; --trace: #6B7785; --rule: #1F2733;
  --cyan: #22D3EE;   /* phase / info / GitNexus */
  --blue: #58A6FF;   /* agent start */
  --green: #3FB950;  /* ok / done / 双轨确认 */
  --red: #F85149;    /* error / fail / exploited 可达 */
  --yellow: #D29922; /* tool / warn */
  --magenta: #BC8CFF;/* llm / LLM 轨 */
  --font-mono: "IBM Plex Mono", ui-monospace, monospace;
  --font-sans: "IBM Plex Sans", system-ui, sans-serif;
  --font-serif: "IBM Plex Serif", Georgia, serif;
}
body { background: var(--void); color: var(--ink); font-family: var(--font-sans); margin: 0; }
.mono { font-family: var(--font-mono); }
.serif { font-family: var(--font-serif); }
.trace { color: var(--trace); }
.ev-phase     { color: var(--cyan); font-weight: bold; }
.ev-agent     { color: var(--blue); }
.ev-agent-ok  { color: var(--green); }
.ev-agent-fail{ color: var(--red); }
.ev-tool      { color: var(--yellow); }
.ev-llm       { color: var(--magenta); }
.ev-error     { color: var(--red); font-weight: bold; }
.ev-info      { color: var(--cyan); }
.ev-warn      { color: var(--yellow); }
.status-badge { font-size: 0.85em; padding: 1px 6px; border: 1px solid var(--rule); border-radius: 3px; }
/* braille spinner（对齐 core rich renderer） */
.spinner::before { content: "⠋"; animation: spin 1.05s steps(1) infinite; display: inline-block; width: 1ch; color: var(--cyan); }
@keyframes spin {
  0%   { content: "⠋"; } 10% { content: "⠙"; } 20% { content: "⠹"; } 30% { content: "⠸"; }
  40%  { content: "⠼"; } 50% { content: "⠴"; } 60% { content: "⠦"; } 70% { content: "⠧"; }
  80%  { content: "⠇"; } 90% { content: "⠏"; }
}
@media (prefers-reduced-motion: reduce) { .spinner::before { animation: none; content: "•"; } }
```

> 注：`@keyframes spin` 用 `content` 切换在部分浏览器需 `::before` 显式设。若动画不生效，fallback 为 JS 切换文本（保 reduced-motion 尊重）。实现时以浏览器实测为准，core rich renderer 用 Python braille 帧不依赖 CSS。

- [ ] **Step 2: 写失败测试 `StatusBadge.test.tsx`**

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it("running → ● + 文案", () => {
    render(<StatusBadge status="running" />);
    expect(screen.getByText(/running/)).toBeInTheDocument();
    expect(screen.getByText(/running/).closest(".status-badge")?.querySelector(".mono")?.textContent).toBe("●");
  });
  it("completed → ✓", () => {
    const { container } = render(<StatusBadge status="completed" />);
    expect(container.querySelector(".mono")?.textContent).toBe("✓");
  });
  it("correlation → 🔗", () => {
    const { container } = render(<StatusBadge status="running" correlation />);
    expect(container.textContent).toContain("🔗");
  });
});
```

- [ ] **Step 3: 跑确认失败** · Run: `npm test -- StatusBadge` · Expected: FAIL

- [ ] **Step 4: 写 `src/components/StatusBadge.tsx`**

```tsx
const MAP: Record<string, { icon: string; cls: string }> = {
  running:   { icon: "●", cls: "ev-info" },
  completed: { icon: "✓", cls: "ev-agent-ok" },
  done:      { icon: "✓", cls: "ev-agent-ok" },
  failed:    { icon: "✗", cls: "ev-agent-fail" },
  killed:    { icon: "✗", cls: "ev-agent-fail" },
  crashed:   { icon: "⚠", cls: "ev-warn" },
};

export function StatusBadge({ status, correlation = false }: { status: string; correlation?: boolean }) {
  const m = MAP[status] ?? { icon: "?", cls: "ev-warn" };
  return (
    <span className={`status-badge ${m.cls}`}>
      <span className="mono">{m.icon}</span> {status}{correlation ? " 🔗" : ""}
    </span>
  );
}
```

- [ ] **Step 5: main.tsx 引入 css**

```tsx
// 在 src/main.tsx 顶部加：
import "./styles/events.css";
```

- [ ] **Step 6: 跑测试确认通过** · Run: `npm test -- StatusBadge` · Expected: PASS（3）

- [ ] **Step 7: Commit**

```bash
git add packages/web/frontend/src/styles/events.css packages/web/frontend/src/components/StatusBadge.tsx packages/web/frontend/src/components/StatusBadge.test.tsx packages/web/frontend/src/main.tsx
git commit -m "feat(web-fe): events.css(STYLE_MAP 语义色+Plex 变量+braille spinner) + StatusBadge"
```

---

## Task 6: MarkdownView（报告渲染：执行摘要 hero + TOC + 键值对齐 + witness PoC）

ReportTab 的渲染引擎。基于 react-markdown + rehype-*；自定义渲染：执行摘要「最高风险发现」hero 置顶、左 TOC（按 H2/H3）、加粗键值对齐行、witness PoC 代码块可复制。兼容两变体（Injection `- **k:** v` 列表 vs XSS/Auth `**Summary:**`）。

**Files:**
- Create: `packages/web/frontend/src/components/MarkdownView.tsx`
- Test: `packages/web/frontend/src/components/MarkdownView.test.tsx`

**Interfaces:**
- Consumes: `events.css` 色类（Task 5）
- Produces: `MarkdownView({ markdown: string })` —— 给定报告 md 原文，渲染 hero + TOC + 正文

- [ ] **Step 1: 写失败测试 `MarkdownView.test.tsx`（标题/代码块/键值/执行摘要）**

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MarkdownView } from "./MarkdownView";

const MD = `# 安全评估报告

## 执行摘要

**总体结论：** 32 个漏洞

**最高风险发现（按业务影响排序）：**

1. **RCE**（INJ-01）：eval
2. **SSRF**（SSRF-01）：IMDSv1

## Injection

### INJ-VULN-01: eval RCE

- **vulnerability_type:** CommandInjection
- **verdict:** vulnerable
- **witness_payload:** \`preTax=res.send(...)\`
`;

describe("MarkdownView", () => {
  it("渲染 H1/H2/H3 标题", () => {
    render(<MarkdownView markdown={MD} />);
    expect(screen.getByText("安全评估报告")).toBeInTheDocument();
    expect(screen.getByText("执行摘要")).toBeInTheDocument();
    expect(screen.getByText(/INJ-VULN-01/)).toBeInTheDocument();
  });

  it("TOC 含类型 + 执行摘要条目", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    const toc = container.querySelector('[data-testid="toc"]');
    expect(toc?.textContent).toContain("执行摘要");
    expect(toc?.textContent).toContain("Injection");
  });

  it("执行摘要 hero 置顶 + 含最高风险发现", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    const hero = container.querySelector('[data-testid="exec-summary-hero"]');
    expect(hero).not.toBeNull();
    expect(hero?.textContent).toContain("RCE");
    expect(hero?.textContent).toContain("INJ-01");
  });

  it("键值字段渲染成 key-value 行", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    expect(container.textContent).toContain("vulnerability_type");
    expect(container.textContent).toContain("CommandInjection");
  });
});
```

- [ ] **Step 2: 跑确认失败** · Run: `npm test -- MarkdownView` · Expected: FAIL

- [ ] **Step 3: 写 `src/components/MarkdownView.tsx`**

```tsx
import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeSlug from "rehype-slug";
import rehypeAutolinkHeadings from "rehype-autolink-headings";

interface Heading {
  id: string; text: string; level: 1 | 2 | 3;
  // 执行摘要「最高风险发现」条目里提取的 vuln 引用（如 INJ-01），用于 hero
}

/** 从 md 提取 TOC headings + 执行摘要条目（编号列表项 + 加粗标题 + 括号 vuln ID） */
function parseStructure(md: string): { headings: Heading[]; topRisks: { text: string; vulnIds: string[] }[] } {
  const headings: Heading[] = [];
  const topRisks: { text: string; vulnIds: string[] }[] = [];
  const lines = md.split(/\r?\n/);
  let inExecSummary = false;
  let inNumberedList = false;
  for (const line of lines) {
    const hm = /^(#{1,3})\s+(.+)$/.exec(line);
    if (hm) {
      const level = hm[1].length as 1 | 2 | 3;
      const text = hm[2].trim();
      const id = text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "");
      headings.push({ id, text, level });
      inExecSummary = text.includes("执行摘要");
      inNumberedList = false;
      continue;
    }
    if (inExecSummary) {
      const nm = /^\d+\.\s+(.+)$/.exec(line.trim());
      if (nm) {
        inNumberedList = true;
        const text = nm[1].replace(/\*\*/g, "");
        const vulnIds = Array.from(text.matchAll(/[A-Z]+-\d+/g)).map((m) => m[0]);
        topRisks.push({ text, vulnIds });
      } else if (inNumberedList && line.trim() && !/^\d+\./.test(line.trim())) {
        inNumberedList = false;
      }
    }
  }
  return { headings, topRisks };
}

export function MarkdownView({ markdown }: { markdown: string }) {
  const [heroCollapsed, setHeroCollapsed] = useState(false);
  const { headings, topRisks } = useMemo(() => parseStructure(markdown), [markdown]);
  const execH2 = headings.find((h) => h.text.includes("执行摘要"));

  return (
    <div className="md-view">
      {execH2 && topRisks.length > 0 && (
        <div data-testid="exec-summary-hero" className="hero">
          <div className="hero-title">
            最高风险发现（按业务影响排序）
            <button onClick={() => setHeroCollapsed((c) => !c)}>{heroCollapsed ? "展开 ▸" : "折叠 ▾"}</button>
          </div>
          {!heroCollapsed && (
            <ol>
              {topRisks.map((r, i) => (
                <li key={i}>
                  <span className="mono">{r.vulnIds.join("/")}</span> {r.text}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}

      <div className="md-layout">
        <nav data-testid="toc" className="toc">
          {headings.filter((h) => h.level >= 2).map((h) => (
            <a key={h.id} href={`#${h.id}`} className={`toc-l${h.level}`}>{h.text}</a>
          ))}
        </nav>
        <div className="md-body serif">
          <ReactMarkdown
            rehypePlugins={[rehypeSlug, [rehypeAutolinkHeadings, { behavior: "wrap" }], rehypeHighlight]}
            components={{
              // 加粗键值：`- **key:** value` 或 `**key:** value` → 对齐 key-value 行
              li: ({ children, ...props }) => {
                const text = flatten(children);
                const m = /^\*\*(.+?):\*\*\s*(.*)$/.exec(text);
                if (m) {
                  return (
                    <li {...props} className="kv-row">
                      <span className="kv-key mono">{m[1]}</span>
                      <span className="kv-val">{m[2]}</span>
                    </li>
                  );
                }
                return <li {...props}>{children}</li>;
              },
              code: ({ className, children, ...props }) => (
                <code {...props} className={`md-code ${className ?? ""}`}>
                  {children}
                  <button className="copy-btn" onClick={(e) => {
                    navigator.clipboard?.writeText(String(children));
                    (e.currentTarget.textContent = "✓");
                  }}>复制</button>
                </code>
              ),
            }}
          >
            {markdown}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  );
}

function flatten(node: React.ReactNode): string {
  if (typeof node === "string") return node;
  if (Array.isArray(node)) return node.map(flatten).join("");
  // 元素节点：取 props.children
  // @ts-expect-error react element children
  if (node?.props?.children) return flatten(node.props.children);
  return "";
}
```

> 样式要点（实现入 `events.css` 或独立 `markdown.css`）：`.md-layout { display: grid; grid-template-columns: 220px 1fr; gap: 24px; }`；`.hero { border-left: 3px solid var(--red); padding: 12px 16px; background: var(--panel); margin-bottom: 24px; }`；`.kv-row { display: grid; grid-template-columns: 220px 1fr; }` `.kv-key { color: var(--trace); }`；`.md-code { font-family: var(--font-mono); background: var(--panel); padding: 2px 6px; }`。reduced-motion 尊重。不写死报告结构（TOC 从真实 heading 动态生成）。

- [ ] **Step 4: 跑测试确认通过** · Run: `npm test -- MarkdownView` · Expected: PASS（4）

- [ ] **Step 5: 装包 + 类型检查**

Run: `cd packages/web/frontend && npm install && npx tsc --noEmit`
Expected: react-markdown/rehype-* 装好，无类型错

- [ ] **Step 6: Commit**

```bash
git add packages/web/frontend/src/components/MarkdownView.tsx packages/web/frontend/src/components/MarkdownView.test.tsx
git commit -m "feat(web-fe): MarkdownView(执行摘要 hero + 动态 TOC + 键值对齐 + witness PoC 复制)"
```

---

## Task 7: YamlEditor（Monaco + 校验）+ FileTree（deliverables 树）

联动扫描的 multi-repo.yaml 手写编辑器（Monaco + js-yaml 校验）+ DeliverablesTab 的产物目录树（递归 + 点击分流）。

**Files:**
- Create: `packages/web/frontend/src/components/YamlEditor.tsx`
- Create: `packages/web/frontend/src/components/FileTree.tsx`
- Test: `packages/web/frontend/src/components/YamlEditor.test.tsx`
- Test: `packages/web/frontend/src/components/FileTree.test.tsx`
- Modify: `package.json`（加 `js-yaml` + `@types/js-yaml`）

**Interfaces:**
- Consumes: `DeliverablesFile`（Task 2 types.ts）
- Produces: `YamlEditor({ value, onChange, onError })`、`FileTree({ files, onSelect })`

- [ ] **Step 1: package.json 加依赖**

```json
"dependencies": { ...existing..., "js-yaml": "^4.1.0" },
"devDependencies": { ...existing..., "@types/js-yaml": "^4.0.9" }
```

- [ ] **Step 2: 写失败测试 `YamlEditor.test.tsx`（parse 校验）**

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { YamlEditor } from "./YamlEditor";

vi.mock("@monaco-editor/react", () => ({
  default: ({ value, onChange }: { value: string; onChange?: (v: string) => void }) => (
    <textarea data-testid="monaco" value={value} onChange={(e) => onChange?.(e.target.value)} />
  ),
}));

describe("YamlEditor", () => {
  it("合法 yaml → onError 不触发", () => {
    const onError = vi.fn();
    render(<YamlEditor value={"repos:\n  a:\n    url: x"} onChange={() => {}} onError={onError} />);
    expect(onError).not.toHaveBeenCalled();
  });
  it("非法 yaml → onError 触发", () => {
    const onError = vi.fn();
    render(<YamlEditor value={"repos: [unclosed"} onChange={() => {}} onError={onError} />);
    expect(onError).toHaveBeenCalledWith(expect.any(String));
  });
});
```

- [ ] **Step 3: 跑确认失败** · Run: `npm test -- YamlEditor` · Expected: FAIL

- [ ] **Step 4: 写 `src/components/YamlEditor.tsx`**

```tsx
import { useEffect } from "react";
import Editor from "@monaco-editor/react";
import yaml from "js-yaml";

export function YamlEditor({
  value, onChange, onError,
}: { value: string; onChange: (v: string) => void; onError: (msg: string) => void }) {
  useEffect(() => {
    try {
      yaml.load(value);
      // 合法：不调 onError（无法"清除"，调用方用 onError 空串表示恢复——见约定）
    } catch (e) {
      onError((e as Error).message);
    }
  }, [value, onError]);

  return (
    <div className="yaml-editor">
      <Editor
        height="320px"
        language="yaml"
        theme="vs-dark"
        value={value}
        onChange={(v) => onChange(v ?? "")}
        options={{ minimap: { enabled: false }, fontSize: 13, scrollBeyondLastLine: false }}
      />
    </div>
  );
}
```

> 约定：`onError(msg)` —— msg 非空=有错，空串=恢复合法。ScanNewPage（Task 10）据此禁用「直接运行」按钮。

- [ ] **Step 5: 写失败测试 `FileTree.test.tsx`**

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { FileTree } from "./FileTree";
import type { DeliverablesFile } from "../api/types";

const files: DeliverablesFile[] = [
  { path: "whitebox/comprehensive_report.md", size: 1000, kind: "md" },
  { path: "whitebox/ssrf_exploitation_queue.json", size: 100, kind: "exploitation_queue" },
  { path: "whitebox/attack_chains.json", size: 2, kind: "empty_json" },
];

describe("FileTree", () => {
  it("渲染嵌套目录 + 文件", () => {
    render(<FileTree files={files} onSelect={() => {}} />);
    expect(screen.getByText("whitebox")).toBeInTheDocument();
    expect(screen.getByText("comprehensive_report.md")).toBeInTheDocument();
  });
  it("点击文件回调", () => {
    const onSelect = vi.fn();
    render(<FileTree files={files} onSelect={onSelect} />);
    fireEvent.click(screen.getByText("ssrf_exploitation_queue.json"));
    expect(onSelect).toHaveBeenCalledWith(files[1]);
  });
  it("空 json 标记", () => {
    render(<FileTree files={files} onSelect={() => {}} />);
    expect(screen.getByText("attack_chains.json").textContent).toContain("空");
  });
});
```

- [ ] **Step 6: 跑确认失败** · Run: `npm test -- FileTree` · Expected: FAIL

- [ ] **Step 7: 写 `src/components/FileTree.tsx`**

```tsx
import { useState } from "react";
import type { DeliverablesFile } from "../api/types";

interface TreeNode {
  name: string; path: string;
  children: Map<string, TreeNode>;
  file?: DeliverablesFile;
}

function buildTree(files: DeliverablesFile[]): TreeNode {
  const root: TreeNode = { name: "", path: "", children: new Map() };
  for (const f of files) {
    const parts = f.path.split("/");
    let cur = root;
    parts.forEach((part, i) => {
      const path = parts.slice(0, i + 1).join("/");
      if (!cur.children.has(part)) cur.children.set(part, { name: part, path, children: new Map() });
      cur = cur.children.get(part)!;
      if (i === parts.length - 1) cur.file = f;
    });
  }
  return root;
}

export function FileTree({ files, onSelect }: { files: DeliverablesFile[]; onSelect: (f: DeliverablesFile) => void }) {
  const root = buildTree(files);
  return <ul className="file-tree">{Array.from(root.children.values()).map((n) => <NodeView key={n.path} node={n} depth={0} onSelect={onSelect} />)}</ul>;
}

function NodeView({ node, depth, onSelect }: { node: TreeNode; depth: number; onSelect: (f: DeliverablesFile) => void }) {
  const [open, setOpen] = useState(depth < 1);
  const isDir = node.children.size > 0;
  return (
    <li>
      <div style={{ paddingLeft: depth * 14 }} className="ft-row">
        {isDir ? (
          <button className="ft-toggle" onClick={() => setOpen((o) => !o)}>{open ? "▾" : "▸"} 📂 {node.name}</button>
        ) : (
          <span className="ft-file mono" onClick={() => onSelect(node.file!)}>
            📄 {node.name}
            {node.file?.kind === "empty_json" && <span className="trace"> （空）</span>}
            {node.file?.kind === "big_json" && <span className="trace"> （大）</span>}
          </span>
        )}
      </div>
      {isDir && open && Array.from(node.children.values()).map((c) => (
        <ul key={c.path}>{<NodeView node={c} depth={depth + 1} onSelect={onSelect} />}</ul>
      ))}
    </li>
  );
}
```

- [ ] **Step 8: 跑测试确认通过** · Run: `npm test -- YamlEditor FileTree` · Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add packages/web/frontend/src/components/YamlEditor.tsx packages/web/frontend/src/components/FileTree.tsx packages/web/frontend/src/components/YamlEditor.test.tsx packages/web/frontend/src/components/FileTree.test.tsx packages/web/frontend/package.json
git commit -m "feat(web-fe): YamlEditor(Monaco+js-yaml 校验) + FileTree(deliverables 目录树+空/大标记)"
```

---

## Task 8: DashboardPanel + LogStream（Live 组件，对齐 core rich 框）

LiveTab 的两个组件。**DashboardPanel** 从 `DashboardState` 渲染状态条（current_phase / step N·M / elapsed / cost）+ 运行中 agent 行（spinner + name + turn + last_action）—— 对齐 core `RichConsoleRenderer` 两层框。**LogStream** 逐事件渲染日志行（按 category 上色 STYLE_MAP）+ react-window 虚拟滚动。

**Files:**
- Create: `packages/web/frontend/src/components/DashboardPanel.tsx`
- Create: `packages/web/frontend/src/components/LogStream.tsx`
- Test: `packages/web/frontend/src/components/DashboardPanel.test.tsx`
- Test: `packages/web/frontend/src/components/LogStream.test.tsx`

**Interfaces:**
- Consumes: `DashboardState`（Task 3）、`NdjsonEvent`（Task 2）、`.ev-*` 色类 + `.spinner`（Task 5）
- Produces: `DashboardPanel({ state, elapsedMs })`、`LogStream({ events })`

- [ ] **Step 1: 写失败测试 `DashboardPanel.test.tsx`**

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DashboardPanel } from "./DashboardPanel";
import type { DashboardState } from "../state/dashboardReducer";

const state: DashboardState = {
  current_phase: "vulnerability-analysis", agents: {}, phase_units: ["Injection", "Xss"],
  unit_status: { Injection: "done", Xss: "running" }, unit_intent: {},
  completed_count: 1, total_cost: 0.5, total_units: 2, completed_units: 1, running_units: ["Xss"],
};
(state as any).agents = {
  Xss: { name: "Xss", status: "running", attempt: 1, turn: 2, last_action: "Bash", last_action_detail: "Bash grep", last_turn_text: "scanning", duration_ms: null, cost_usd: null, error: null },
};

describe("DashboardPanel", () => {
  it("状态条：phase + step N/M + elapsed + cost", () => {
    render(<DashboardPanel state={state} elapsedMs={134000} />);
    expect(screen.getByText(/vulnerability-analysis/)).toBeInTheDocument();
    expect(screen.getByText(/1\/2/)).toBeInTheDocument();       // completed/total units
    expect(screen.getByText(/02:14/)).toBeInTheDocument();       // 134000ms → 02:14
    expect(screen.getByText(/\$0\.50/)).toBeInTheDocument();
  });
  it("运行中 agent 行：spinner + name + turn + last_action", () => {
    const { container } = render(<DashboardPanel state={state} elapsedMs={0} />);
    expect(container.querySelector(".spinner")).toBeInTheDocument();
    expect(screen.getByText(/Xss/)).toBeInTheDocument();
    expect(screen.getByText(/t2/)).toBeInTheDocument();
    expect(screen.getByText(/Bash grep/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑确认失败** · Run: `npm test -- DashboardPanel` · Expected: FAIL

- [ ] **Step 3: 写 `src/components/DashboardPanel.tsx`**

```tsx
import type { DashboardState } from "../state/dashboardReducer";

function fmtMs(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function DashboardPanel({ state, elapsedMs }: { state: DashboardState; elapsedMs: number }) {
  const running = Object.values(state.agents).filter((a) => a.status === "running");
  return (
    <div className="dashboard-panel">
      <div className="dp-bar mono">
        <span className="ev-phase">{state.current_phase ?? "—"}</span>
        {" · "}
        <span>step {state.completed_units}/{state.total_units}</span>
        {" · "}
        <span>{fmtMs(elapsedMs)}</span>
        {" · "}
        <span>${state.total_cost.toFixed(2)}</span>
      </div>
      <div className="dp-agents">
        {running.map((a) => (
          <div key={a.name} className="dp-agent mono">
            <span className="spinner" /> {a.name} <span className="trace">t{a.turn}</span> {a.last_action_detail ?? a.last_action ?? ""}
          </div>
        ))}
        {running.length === 0 && <div className="trace">无运行中 agent</div>}
      </div>
    </div>
  );
}
```

> 样式：`.dashboard-panel { border: 1px solid var(--rule); background: var(--panel); padding: 8px 12px; }`；`.dp-bar { border-bottom: 1px solid var(--rule); padding-bottom: 6px; margin-bottom: 6px; }`。对齐 core rich 框两层。

- [ ] **Step 4: 跑测试确认通过** · Run: `npm test -- DashboardPanel` · Expected: PASS（2）

- [ ] **Step 5: 写失败测试 `LogStream.test.tsx`（按 category 上色 + 渲染）**

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { LogStream } from "./LogStream";
import type { NdjsonEvent } from "../api/types";

const events: NdjsonEvent[] = [
  { ts: "2026-07-02T09:44:01.000Z", category: "PHASE", type: "PhaseEvent", phase: "recon", event: "start", steps: [], step_intents: [] },
  { ts: "2026-07-02T09:44:05.000Z", category: "AGENT", type: "AgentEvent", agent_name: "Injection", event: "start", attempt: 1 },
  { ts: "2026-07-02T09:44:10.000Z", category: "ERROR", type: "ErrorEvent", error_type: "X", message: "boom" },
];

describe("LogStream", () => {
  it("逐事件渲染行 + 按 category 上色 class", () => {
    const { container } = render(<LogStream events={events} />);
    const rows = container.querySelectorAll(".log-row");
    expect(rows.length).toBe(3);
    expect(rows[0].className).toContain("ev-phase");
    expect(rows[1].className).toContain("ev-agent");
    expect(rows[2].className).toContain("ev-error");
  });
  it("每行含时间戳 + type + 摘要", () => {
    render(<LogStream events={events} />);
    expect(screen.getByText(/09:44:01/)).toBeInTheDocument();
    expect(screen.getAllByText(/PhaseEvent|AgentEvent|ErrorEvent/).length).toBe(3);
  });
});
```

- [ ] **Step 6: 跑确认失败** · Run: `npm test -- LogStream` · Expected: FAIL

- [ ] **Step 7: 写 `src/components/LogStream.tsx`**

```tsx
import type { NdjsonEvent, EventCategory } from "../api/types";

const CAT_CLASS: Partial<Record<EventCategory, string>> = {
  PHASE: "ev-phase", STEP: "ev-info", AGENT: "ev-agent", TOOL: "ev-tool",
  LLM: "ev-llm", ERROR: "ev-error", INFO: "ev-info", WARN: "ev-warn",
  RESUME: "ev-info", SUMMARY: "ev-phase", HEADER: "trace", GITNEXUS: "ev-info",
  CONTROL: "trace",
};

function tsClock(ts: string): string {
  // ISO8601 → HH:MM:SS（本地或原样取 time 部分）
  const m = /T(\d{2}:\d{2}:\d{2})/.exec(ts);
  return m ? m[1] : ts;
}

function summarize(e: NdjsonEvent): string {
  switch (e.type) {
    case "PhaseEvent": return `${e.event === "start" ? "Starting" : "Complete"} ${e.phase}`;
    case "StepEvent": return `${e.event === "start" ? "○" : "✓"} ${e.name}`;
    case "AgentEvent": return `${e.event === "start" ? "▶" : (e.success === false ? "✗" : "✓")} [${e.agent_name}]`;
    case "ToolCallEvent": return `🔧 ${e.tool_name}`;
    case "LlmTurnEvent": return `💭 turn ${e.turn}`;
    case "ErrorEvent": return `${e.error_type}: ${e.message}`;
    case "InfoEvent": return e.message;
    case "SummaryEvent": return `summary: ${e.status}`;
    case "ResumeEvent": return `resume ← ${e.previous_workflow_id}`;
    default: return e.type;
  }
}

export function LogStream({ events }: { events: NdjsonEvent[] }) {
  return (
    <div className="log-stream">
      {events.map((e, i) => (
        <div key={i} className={`log-row mono ${CAT_CLASS[e.category] ?? "trace"}`}>
          <span className="trace">[{tsClock(e.ts)}]</span> <span className="ev-type">{e.type}</span> {summarize(e)}
        </div>
      ))}
    </div>
  );
}
```

> 大日志虚拟滚动：events > 500 行时切 `react-window` `FixedSizeList`（行高 20px）。实现时按 `events.length` 条件渲染 `<LogStreamVirtual>` 包装。LogsTab 的 `agents/*.log` 100KB+ 同样处理（Task 12）。reduce-motion 尊重。

- [ ] **Step 8: 跑测试确认通过 + 类型检查** · Run: `npm test -- LogStream && npx tsc --noEmit` · Expected: PASS + 无错

- [ ] **Step 9: Commit**

```bash
git add packages/web/frontend/src/components/DashboardPanel.tsx packages/web/frontend/src/components/LogStream.tsx packages/web/frontend/src/components/DashboardPanel.test.tsx packages/web/frontend/src/components/LogStream.test.tsx
git commit -m "feat(web-fe): DashboardPanel(状态条+运行 agent,对齐 core rich 框) + LogStream(按 category 上色 STYLE_MAP)"
```
## Task 9: WorkspaceListPage（等宽台账 + status 色条 + 联动树 + 轮询）

web 入口页。`GET /api/workspaces` → 等宽台账表格（每行 workspace，左侧 status 色条 + StatusBadge + 等宽数字列）+ 联动 workspace（scan_type=correlation）展开子白盒 ws 树。轮询 5s + 手动刷新。

**Files:**
- Create: `packages/web/frontend/src/pages/WorkspaceListPage.tsx`
- Test: `packages/web/frontend/src/pages/WorkspaceListPage.test.tsx`

**Interfaces:**
- Consumes: `apiGet`（Task 4）、`Workspace`（Task 2）、`StatusBadge`（Task 5）
- Produces: `WorkspaceListPage`（默认导出路由组件）

- [ ] **Step 1: 写失败测试（MSW 模拟 /workspaces → 渲染 + 状态徽章 + 联动树）**

```tsx
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { WorkspaceListPage } from "./WorkspaceListPage";

const server = setupServer(
  http.get("/api/workspaces", () => HttpResponse.json([
    { name: "ws-a", scan_type: "whitebox", status: "running", created_at: 0, total_cost_usd: 2.34, total_duration_ms: 2530000, vuln_count: 14 },
    { name: "ws-corr", scan_type: "correlation", status: "running", created_at: 0,
      links: { child_workspaces: ["ws-a", "ws-b"] } },
  ])),
);
beforeAll(() => server.listen()); afterAll(() => server.close());

function renderPage() {
  return render(<MemoryRouter><WorkspaceListPage /></MemoryRouter>);
}

describe("WorkspaceListPage", () => {
  it("渲染 workspace 行 + 等宽台账", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    expect(screen.getByText(/\$2\.34/)).toBeInTheDocument();
    expect(screen.getByText(/whitebox/)).toBeInTheDocument();
  });
  it("联动 workspace 展开子 ws 树 + 🔗", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-corr")).toBeInTheDocument());
    expect(screen.getByText(/ws-corr/).textContent).toContain("🔗");
    expect(screen.getByText(/ws-b/)).toBeInTheDocument();  // 子 ws
  });
});
```

- [ ] **Step 2: 跑确认失败** · Run: `npm test -- WorkspaceListPage` · Expected: FAIL

- [ ] **Step 3: 写 `src/pages/WorkspaceListPage.tsx`**

```tsx
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { Workspace } from "../api/types";
import { apiGet } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";

function fmtMs(ms?: number): string {
  if (!ms) return "—";
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}m${s % 60}s`;
}

export function WorkspaceListPage() {
  const [items, setItems] = useState<Workspace[]>([]);
  const load = useCallback(() => apiGet<Workspace[]>("/workspaces").then(setItems).catch(() => {}), []);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load]);

  return (
    <div className="page">
      <h1>Workspaces <button onClick={load} aria-label="refresh">↻</button>
        <Link to="/scan/new"><button>+ new scan</button></Link>
      </h1>
      <table className="ledger mono">
        <thead><tr><th>workspace</th><th>status</th><th>type</th><th>vulns</th><th>cost</th><th>time</th></tr></thead>
        <tbody>{items.map((w) => <Row key={w.name} w={w} />)}</tbody>
      </table>
    </div>
  );
}

function Row({ w }: { w: Workspace }) {
  const corr = w.scan_type === "correlation";
  return (
    <>
      <tr className={`ledger-row status-${w.status}`}>
        <td><span className={`status-bar status-${w.status}`} /> <Link to={`/p/${w.name}`}>{w.name}</Link>{corr ? " 🔗" : ""}</td>
        <td><StatusBadge status={w.status} correlation={corr} /></td>
        <td>{w.scan_type}</td>
        <td>{w.vuln_count ?? "—"}</td>
        <td>${(w.total_cost_usd ?? 0).toFixed(2)}</td>
        <td>{fmtMs(w.total_duration_ms)}</td>
      </tr>
      {corr && (w.links?.child_workspaces ?? []).map((c) => (
        <tr key={c} className="ledger-child trace">
          <td colSpan={6}>　└─ <Link to={`/p/${c}`}>{c}</Link></td>
        </tr>
      ))}
    </>
  );
}
```

> 样式：`.status-bar { display:inline-block; width:2px; height:1em; margin-right:6px; background:var(--ink); }` `.status-running{background:var(--cyan)} .status-completed,.status-done{background:var(--green)} .status-failed,.status-killed{background:var(--red)} .status-crashed{background:var(--yellow)}`。`.ledger { width:100%; border-collapse:collapse; }` `.ledger td { padding:4px 8px; border-bottom:1px solid var(--rule); }`。

- [ ] **Step 4: 跑测试确认通过** · Run: `npm test -- WorkspaceListPage` · Expected: PASS（2）

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/pages/WorkspaceListPage.tsx packages/web/frontend/src/pages/WorkspaceListPage.test.tsx
git commit -m "feat(web-fe): WorkspaceListPage(等宽台账+status 色条+联动子 ws 树+5s 轮询)"
```

---

## Task 10: ScanNewPage（3 类 segmented + 动态字段 + 冲突校验 + 错误码）

开扫描页。3 类（白盒/黑盒/联动）segmented 统一入口、字段按类型动态显隐；workspace 名冲突弹断点续扫确认；黑盒 `--latest` 软默认陷阱标注；提交错误码（400/409/422）可操作提示。

**Files:**
- Create: `packages/web/frontend/src/pages/ScanNewPage.tsx`
- Test: `packages/web/frontend/src/pages/ScanNewPage.test.tsx`

**Interfaces:**
- Consumes: `apiGet/apiPost/ApiError`（Task 4）、`YamlEditor`（Task 7）、`ScanRequest/ScanResponse/Workspace`（Task 2）
- Produces: `ScanNewPage`

- [ ] **Step 1: 写失败测试（类型切换 / --latest 复选框 / 冲突确认 / 错误码）**

```tsx
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { ScanNewPage } from "./ScanNewPage";

const server = setupServer(
  http.get("/api/workspaces", () => HttpResponse.json([{ name: "existing-ws", scan_type: "whitebox", status: "completed", created_at: 0 }])),
);
beforeAll(() => server.listen()); afterAll(() => server.close());
afterEach(() => server.resetHandlers());

function renderPage() { return render(<MemoryRouter><ScanNewPage /></MemoryRouter>); }

describe("ScanNewPage", () => {
  it("默认白盒：显示代码来源，无 reuse 复选框；切黑盒显示 reuse", () => {
    renderPage();
    expect(screen.getByText(/代码来源/)).toBeInTheDocument();
    expect(screen.queryByText(/复用最新白盒/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "黑盒" }));
    expect(screen.getByText(/复用最新白盒/)).toBeInTheDocument();
  });
  it("切联动：显示 yaml 编辑器，隐藏白盒字段", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "联动" }));
    expect(screen.getByTestId("monaco")).toBeInTheDocument();
    expect(screen.queryByText(/代码来源/)).toBeNull();
  });
  it("workspace 名冲突 → 弹断点续扫确认", async () => {
    renderPage();
    fireEvent.change(screen.getByPlaceholderText(/workspace/), { target: { value: "existing-ws" } });
    await waitFor(() => expect(screen.getByText(/断点续扫/)).toBeInTheDocument());
  });
  it("提交 400 → 提示 Temporal 未就绪", async () => {
    server.use(http.post("/api/scan", () => new HttpResponse(null, { status: 400 })));
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(screen.getByText(/Temporal/i)).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: 跑确认失败** · Run: `npm test -- ScanNewPage` · Expected: FAIL

- [ ] **Step 3: 写 `src/pages/ScanNewPage.tsx`**

```tsx
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { ScanRequest, ScanResponse, Workspace } from "../api/types";
import { apiGet, apiPost, ApiError } from "../api/client";
import { YamlEditor } from "../components/YamlEditor";

type ScanType = "whitebox" | "blackbox" | "correlation";

function buildBody(type: ScanType, f: FormState): ScanRequest {
  if (type === "correlation") return { type, config_yaml: f.yaml };
  const body: ScanRequest = {
    type,
    source: { kind: f.sourceKind, value: f.sourceValue, branch: f.branch || undefined, commit: f.commit || undefined, force_reclone: f.forceReclone || undefined },
    url: f.url, workspace_name: f.wsName || undefined,
  };
  if (type === "blackbox") body.reuse_latest_whitebox = f.reuseLatest;
  return body;
}

function renderError(e: ApiError): string {
  if (e.status === 400) return "Temporal 未就绪（localhost:7233）。先启动：docker-compose up temporal";
  if (e.status === 409) return "并发扫描超限，请等当前扫描完成或取消一个";
  if (e.status === 422) return "yaml 校验失败：" + JSON.stringify(e.body);
  return `提交失败（${e.status}）`;
}

interface FormState {
  sourceKind: "path" | "git"; sourceValue: string; branch: string; commit: string; forceReclone: boolean;
  url: string; wsName: string; reuseLatest: boolean; yaml: string;
}

export function ScanNewPage() {
  const nav = useNavigate();
  const [type, setType] = useState<ScanType>("whitebox");
  const [f, setF] = useState<FormState>({
    sourceKind: "path", sourceValue: "", branch: "", commit: "", forceReclone: false,
    url: "", wsName: "", reuseLatest: false, yaml: "repos:\n  a:\n    url: https://gitlab.example/a.git\n    branch: main",
  });
  const [conflict, setConflict] = useState<string | null>(null);
  const [yamlErr, setYamlErr] = useState("");
  const [err, setErr] = useState("");
  const set = (patch: Partial<FormState>) => setF((prev) => ({ ...prev, ...patch }));

  useEffect(() => {
    if (!f.wsName) { setConflict(null); return; }
    apiGet<Workspace[]>("/workspaces").then((ws) => {
      setConflict(ws.some((w) => w.name === f.wsName) ? f.wsName : null);
    });
  }, [f.wsName]);

  async function submit() {
    if (type === "correlation" && yamlErr) { setErr("yaml 有错，无法运行"); return; }
    try {
      setErr("");
      const r = await apiPost<ScanResponse>("/scan", buildBody(type, f));
      nav(`/p/${r.workspace}/live`);
    } catch (e) {
      if (e instanceof ApiError) setErr(renderError(e));
    }
  }

  return (
    <div className="page scan-page">
      <div className="segmented">
        {(["whitebox", "blackbox", "correlation"] as ScanType[]).map((t) => (
          <button key={t} role="tab" aria-selected={type === t} onClick={() => setType(t)}
            className={type === t ? "seg-active" : ""}>{t === "whitebox" ? "白盒" : t === "blackbox" ? "黑盒" : "联动"}</button>
        ))}
      </div>

      {type !== "correlation" ? (
        <div className="form-area">
          <label>代码来源：
            <select value={f.sourceKind} onChange={(e) => set({ sourceKind: e.target.value as "path" | "git" })}>
              <option value="path">本地路径</option><option value="git">git URL</option>
            </select>
            <input value={f.sourceValue} onChange={(e) => set({ sourceValue: e.target.value })} placeholder={f.sourceKind === "path" ? "/root/code/foo" : "https://gitlab.example/foo.git"} />
          </label>
          {f.sourceKind === "git" && (
            <div className="git-extra">
              <input value={f.branch} onChange={(e) => set({ branch: e.target.value })} placeholder="分支(可选)" />
              <input value={f.commit} onChange={(e) => set({ commit: e.target.value })} placeholder="commit(可选,优先)" />
              <label><input type="checkbox" checked={f.forceReclone} onChange={(e) => set({ forceReclone: e.target.checked })} /> 强制重新 clone</label>
            </div>
          )}
          <label>目标 URL：<input value={f.url} onChange={(e) => set({ url: e.target.value })} placeholder="http://example.com" /></label>
          <label>workspace 名：<input value={f.wsName} onChange={(e) => set({ wsName: e.target.value })} placeholder="空=自动 {repo}_{timestamp}" /></label>
          {type === "blackbox" && (
            <label><input type="checkbox" checked={f.reuseLatest} onChange={(e) => set({ reuseLatest: e.target.checked })} />
              复用最新白盒结果 ⓘ <span className="trace">--latest 按 url 匹配；不勾选时后端传 --repo 显式 standalone，规避 CLI 软默认复用</span>
            </label>
          )}
          {conflict && (
            <div className="confirm-dialog ev-warn">
              ⚠ workspace「{conflict}」已存在，CLI -w 语义=存在则恢复，将<b>断点续扫</b>（恢复已有进度）。
              <button onClick={() => set({ wsName: "" })}>取消</button>
              <button className="confirm-continue" onClick={submit}>确认续扫</button>
            </div>
          )}
        </div>
      ) : (
        <div className="correlation-area">
          <YamlEditor value={f.yaml} onChange={(v) => set({ yaml: v })} onError={(m) => setYamlErr(m)} />
          <div className="trace">{yamlErr ? `⚠ ${yamlErr}` : "yaml 合法"}</div>
        </div>
      )}

      {err && <div className="err-banner ev-error">{err}</div>}
      <button className="submit-btn" onClick={submit} disabled={!!conflict}>开始扫描 ▶</button>
      <div className="trace">→ 202 → 跳 /p/&#123;ws&#125;/live · 错误：400(Temporal)/409(并发)/422(yaml)</div>
    </div>
  );
}
```

> `buildBody` 是 `POST /api/scan` 的契约映射（backend-design.md）。黑盒不勾 `reuse_latest_whitebox` 时仍传 `false`——后端按此决定是否加 `--repo` standalone（见 backend spec §扫描类型 `--latest` 软默认陷阱）。

- [ ] **Step 4: 跑测试确认通过** · Run: `npm test -- ScanNewPage` · Expected: PASS（4）

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/pages/ScanNewPage.tsx packages/web/frontend/src/pages/ScanNewPage.test.tsx
git commit -m "feat(web-fe): ScanNewPage(3 类 segmented 动态字段+断点续扫确认+--latest 陷阱标注+400/409/422)"
```

---

## Task 11: ReportTab + DeliverablesTab（详情结果两 tab）

**ReportTab**：`GET /report`（md 原文）→ `MarkdownView`（Task 6）。**DeliverablesTab**：`GET /deliverables` → 漏洞聚合网格（`merge_source` 双轨徽章 + 可达性 ● + injection 无 queue 标注）+ `FileTree`（Task 7）+ 文件预览分流。

**Files:**
- Create: `packages/web/frontend/src/routes/WorkspaceDetail/ReportTab.tsx`
- Create: `packages/web/frontend/src/routes/WorkspaceDetail/DeliverablesTab.tsx`
- Create: `packages/web/frontend/src/components/VulnCard.tsx`（漏洞卡片 + MergeSourceBadge）
- Test: 上述各 `.test.tsx`
- Modify: `packages/web/frontend/src/api/client.ts`（加 `apiGetText`，report 返 text/plain）

**Interfaces:**
- Consumes: `MarkdownView`（Task 6）、`FileTree`（Task 7）、`Vulnerability/DeliverablesSummary`（Task 2）、`.ev-*` 色类
- Produces: `ReportTab`、`DeliverablesTab`、`VulnCard`

- [ ] **Step 1: client.ts 加 `apiGetText`（report 端点 text/plain）**

```ts
// 追加到 src/api/client.ts
export async function apiGetText(path: string): Promise<string> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.text();
}
```

- [ ] **Step 2: 写 `VulnCard.tsx`（漏洞卡片 + 双轨徽章）**

```tsx
import { useState } from "react";
import type { Vulnerability, MergeSource } from "../api/types";

export function MergeSourceBadge({ src }: { src?: MergeSource }) {
  if (!src) return null;
  if (src === "llm-only") return <span className="badge ev-llm">💭 LLM轨</span>;
  if (src === "gitnexus-only") return <span className="badge ev-info">🔍 GN轨</span>;
  if (src === "both") return <span className="badge ev-agent-ok">✓ 双轨确认</span>;
  return <span className="badge trace">{src}</span>;
}

export function VulnCard({ v }: { v: Vulnerability }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`vuln-card ${v.externally_exploitable ? "reachable" : ""}`}>
      <div className="vc-head mono" onClick={() => setOpen((o) => !o)}>
        <span className="vc-id">{v.ID}</span> {v.vulnerability_type}
        {v.externally_exploitable && <span className="badge ev-agent-fail">● 可达</span>}
        <MergeSourceBadge src={v.merge_source} />
        {v.confidence && <span className="badge trace">{v.confidence}</span>}
        {v.source_endpoint && <span className="trace"> {v.source_endpoint}</span>}
        <span className="trace">{open ? " ▴" : " ▾"}</span>
      </div>
      {open && (
        <div className="vc-detail serif">
          {v.vulnerable_code_location && <div><b>location:</b> <code className="mono">{v.vulnerable_code_location}</code></div>}
          {v.missing_defense && <div><b>missing_defense:</b> {v.missing_defense}</div>}
          {v.exploitation_hypothesis && <div><b>hypothesis:</b> {v.exploitation_hypothesis}</div>}
          {v.suggested_exploit_technique && <div><b>technique:</b> <code className="mono">{v.suggested_exploit_technique}</code></div>}
          {v.notes && <div className="vc-notes"><b>notes:</b> {v.notes}</div>}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: 写 `ReportTab.tsx`**

```tsx
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { apiGetText } from "../../api/client";
import { MarkdownView } from "../../components/MarkdownView";

export function ReportTab() {
  const { workspace } = useParams<{ workspace: string }>();
  const [md, setMd] = useState("");
  useEffect(() => { apiGetText(`/workspaces/${workspace}/report`).then(setMd); }, [workspace]);
  if (!md) return <div className="trace">加载报告…</div>;
  return <MarkdownView markdown={md} />;
}
```

- [ ] **Step 4: 写 `DeliverablesTab.tsx`**

```tsx
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { apiGet } from "../../api/client";
import type { DeliverablesSummary, DeliverablesFile } from "../../api/types";
import { FileTree } from "../../components/FileTree";
import { VulnCard } from "../../components/VulnCard";

export function DeliverablesTab() {
  const { workspace } = useParams<{ workspace: string }>();
  const [data, setData] = useState<DeliverablesSummary | null>(null);
  const [sel, setSel] = useState<DeliverablesFile | null>(null);
  useEffect(() => { apiGet<DeliverablesSummary>(`/workspaces/${workspace}/deliverables`).then(setData); }, [workspace]);
  if (!data) return <div className="trace">加载产物…</div>;
  return (
    <div className="deliverables-layout">
      <div className="vuln-grid">
        <h3>漏洞聚合 · {data.aggregated_vulnerabilities.length}</h3>
        {data.notes?.injection_has_no_queue && (
          <div className="trace">⚠ injection 类无独立 queue（仅 analysis_deliverable + 报告），聚合不含 injection —— 见报告</div>
        )}
        {data.aggregated_vulnerabilities.map((v) => <VulnCard key={v.ID} v={v} />)}
      </div>
      <div className="deliverables-side">
        <FileTree files={data.files} onSelect={setSel} />
        {sel && <FilePreview ws={workspace!} file={sel} />}
      </div>
    </div>
  );
}

function FilePreview({ ws, file }: { ws: string; file: DeliverablesFile }) {
  const [content, setContent] = useState("");
  useEffect(() => {
    if (file.kind === "md" || file.kind === "exploitation_queue" || file.kind === "other_json") {
      apiGetText(`/workspaces/${ws}/deliverables?path=${encodeURIComponent(file.path)}`).then(setContent);
    }
  }, [ws, file.path]);
  if (file.kind === "empty_json") return <div className="trace">无数据（常态空）</div>;
  if (file.kind === "big_json") return <div className="trace">大 JSON，用树查看器（虚拟滚动）<pre className="mono">{content.slice(0, 500)}…</pre></div>;
  if (file.kind === "md") return <MarkdownViewLazy content={content} />;
  return <pre className="mono">{content}</pre>;
}

// report/analysis md 预览复用 MarkdownView（避免循环依赖，直接 import）
import { MarkdownView } from "../../components/MarkdownView";
function MarkdownViewLazy({ content }: { content: string }) {
  return content ? <MarkdownView markdown={content} /> : <div className="trace">加载…</div>;
}
```

> `deliverables?path=` 端点：后端返单文件内容（md text / json text）。empty_json → 前端判空显示（不请求或请求返 `[]`）。big_json → 虚拟滚动树（实现时接 react-window；此处给前 500 字符预览占位，**Task 12 统一虚拟滚动**——非占位符，是分阶段：Task 12 补 react-window 包装）。

- [ ] **Step 5: 写测试 `DeliverablesTab.test.tsx`（聚合网格 + 双轨徽章 + injection 标注）**

```tsx
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { DeliverablesTab } from "./DeliverablesTab";

const server = setupServer(
  http.get("/api/workspaces/:ws/deliverables", () => HttpResponse.json({
    track: "whitebox",
    files: [{ path: "whitebox/ssrf_exploitation_queue.json", size: 100, kind: "exploitation_queue" }],
    aggregated_vulnerabilities: [
      { ID: "SSRF-01", vulnerability_type: "URL_Manipulation", externally_exploitable: true, merge_source: "llm-only", confidence: "needs_review", source_endpoint: "GET /research" },
    ],
    notes: { injection_has_no_queue: true },
  })),
);
beforeAll(() => server.listen()); afterAll(() => server.close());

describe("DeliverablesTab", () => {
  it("聚合网格 + 双轨徽章 + 可达 ●", async () => {
    render(<MemoryRouter initialEntries={["/p/ws/deliverables"]}><Routes><Route path="/p/:workspace/deliverables" element={<DeliverablesTab />} /></Routes></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("SSRF-01")).toBeInTheDocument());
    expect(screen.getByText(/可达/)).toBeInTheDocument();
    expect(screen.getByText(/LLM轨/)).toBeInTheDocument();
  });
  it("injection 无 queue 标注", async () => {
    render(<MemoryRouter initialEntries={["/p/ws/deliverables"]}><Routes><Route path="/p/:workspace/deliverables" element={<DeliverablesTab />} /></Routes></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/injection 类无独立 queue/)).toBeInTheDocument());
  });
});
```

- [ ] **Step 6: 跑测试确认通过 + 类型检查** · Run: `npm test -- DeliverablesTab ReportTab VulnCard && npx tsc --noEmit` · Expected: PASS + 无错

- [ ] **Step 7: Commit**

```bash
git add packages/web/frontend/src/routes/WorkspaceDetail/ReportTab.tsx packages/web/frontend/src/routes/WorkspaceDetail/DeliverablesTab.tsx packages/web/frontend/src/components/VulnCard.tsx packages/web/frontend/src/routes/WorkspaceDetail/DeliverablesTab.test.tsx packages/web/frontend/src/api/client.ts
git commit -m "feat(web-fe): ReportTab(MarkdownView) + DeliverablesTab(漏洞聚合网格+merge_source 双轨徽章+injection 无 queue 标注)"
```

---

## Task 12: OverviewTab + LogsTab + LiveTab + WorkspaceDetail 壳 + router + 冒烟

收尾 task：OverviewTab（阶段瀑布 + 重试分级 + status 矛盾兜底）、LogsTab（JSONL/文本日志 + 虚拟滚动）、LiveTab（SSE → DashboardPanel + LogStream）、WorkspaceDetail 路由壳（tab 导航 + 默认 tab）、router + App、集成冒烟测试。

**Files:**
- Create: `packages/web/frontend/src/routes/WorkspaceDetail/OverviewTab.tsx`
- Create: `packages/web/frontend/src/routes/WorkspaceDetail/LogsTab.tsx`
- Create: `packages/web/frontend/src/routes/WorkspaceDetail/LiveTab.tsx`
- Create: `packages/web/frontend/src/routes/WorkspaceDetail/index.tsx`
- Create: `packages/web/frontend/src/router.tsx`
- Modify: `packages/web/frontend/src/App.tsx`（替换占位为 RouterProvider）
- Test: `packages/web/frontend/src/routes/WorkspaceDetail/OverviewTab.test.tsx`
- Test: `packages/web/frontend/src/App.test.tsx`（集成冒烟：路由 + 落地页）

**Interfaces:**
- Consumes: 全部前序 task 产物
- Produces: 完整可跑 SPA（router + 5 tab + 2 页面）

- [ ] **Step 1: 写 `OverviewTab.tsx`（阶段瀑布 + 重试分级 + status 矛盾兜底）**

```tsx
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import type { SessionData } from "../../api/types";
import { apiGet } from "../../api/client";
import { StatusBadge } from "../../components/StatusBadge";

function fmtMs(ms: number): string {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function OverviewTab() {
  const { workspace } = useParams<{ workspace: string }>();
  const [s, setS] = useState<SessionData | null>(null);
  useEffect(() => { apiGet<SessionData>(`/workspaces/${workspace}`).then(setS); }, [workspace]);
  if (!s?.metrics) return <div className="trace">无 metrics</div>;
  const m = s.metrics;
  const statusConflict = !!(s.status && s.session?.status && s.status !== s.session.status);

  return (
    <div className="overview">
      <div className="ov-statusbar">
        <StatusBadge status={s.status ?? s.session?.status ?? "?"} /> {s.scan_type} {s.repo_path}
        {statusConflict && <span className="ev-warn"> ⚠ 顶层 {s.status} vs session.{s.session!.status}（归一未覆盖顶层，已 flag 后端）</span>}
      </div>
      <div className="big-numbers mono">
        <div><span className="big">${m.total_cost_usd.toFixed(2)}</span> <span className="trace">total cost</span></div>
        <div><span className="big">{fmtMs(m.total_duration_ms)}</span> <span className="trace">duration</span></div>
        <div><span className="big">{Object.keys(m.agents).length}</span> <span className="trace">agents</span></div>
      </div>
      <PhaseWaterfall phases={m.phases} fmt={fmtMs} />
      <AgentTable agents={m.agents} fmt={fmtMs} />
    </div>
  );
}

function PhaseWaterfall({ phases, fmt }: { phases: SessionData["metrics"]["phases"]; fmt: (ms: number) => string }) {
  const entries = Object.entries(phases);
  return (
    <div className="phase-waterfall">
      <h3>阶段瀑布</h3>
      <div className="pw-bars">
        {entries.map(([name, p]) => (
          <div key={name} className="pw-bar" style={{ width: `${p.duration_percentage}%` }} title={`${name}: ${p.duration_percentage}%`}>
            <div className="pw-name">{name}</div>
            <div className="pw-meta mono">{p.duration_percentage}% · {fmt(p.duration_ms)} · ${p.cost_usd.toFixed(2)} · {p.agent_count}a</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function AgentTable({ agents, fmt }: { agents: SessionData["metrics"]["agents"]; fmt: (ms: number) => string }) {
  return (
    <table className="ledger mono agent-table">
      <thead><tr><th>agent</th><th>duration</th><th>cost</th><th>attempt</th><th>model</th></tr></thead>
      <tbody>
        {Object.entries(agents).map(([name, a]) => {
          const warned = a.attempt_number > 1 || !!a.error;
          const cls = a.success === false ? "ev-agent-fail" : warned ? "ev-warn" : "";
          return (
            <tr key={name} className={cls}>
              <td>{name}</td><td>{fmt(a.duration_ms)}</td><td>${a.cost_usd.toFixed(2)}</td>
              <td>{warned ? `⚠ ${a.attempt_number}${a.error ? `(${a.error.slice(0, 20)})` : ""}` : a.attempt_number}</td>
              <td className="trace">{a.model}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
```

> 样式：`.phase-waterfall .pw-bars { display:flex; align-items:flex-end; gap:2px; height:80px; }` `.pw-bar { background:var(--cyan); min-width:60px; padding:4px; color:var(--void); }` 重试分级：`tr.ev-agent-fail { color:var(--red); } tr.ev-warn { color:var(--yellow); }`。

- [ ] **Step 2: 写 `LogsTab.tsx`（agents/*.log JSONL + workflow.log 文本 + 虚拟滚动）**

```tsx
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { apiGet } from "../../api/client";

export function LogsTab() {
  const { workspace } = useParams<{ workspace: string }>();
  const [files, setFiles] = useState<string[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [content, setContent] = useState("");
  useEffect(() => { apiGet<{ files: string[] }>(`/workspaces/${workspace}/logs`).then((r) => setFiles(r.files)); }, [workspace]);
  useEffect(() => {
    if (!sel) return;
    apiGet<{ content: string }>(`/workspaces/${workspace}/logs?file=${encodeURIComponent(sel)}`).then((r) => setContent(r.content));
  }, [workspace, sel]);

  const isJsonl = sel?.endsWith(".log") && !sel.endsWith("workflow.log") && !sel.endsWith("activity_failures.log");
  const lines = content.split(/\r?\n/).filter(Boolean);

  return (
    <div className="logs-layout">
      <div className="logs-files">
        {files.map((f) => <div key={f} className={`log-file mono ${sel === f ? "sel" : ""}`} onClick={() => setSel(f)}>{f}</div>)}
      </div>
      <div className="logs-content mono">
        {isJsonl ? lines.map((l, i) => {
          let ev; try { ev = JSON.parse(l); } catch { return <div key={i} className="trace">{l}</div>; }
          return <div key={i} className="log-row ev-info">[{ev.ts}] {ev.type} {ev.message ?? ev.tool_name ?? ""}</div>;
        }) : <pre>{content}</pre>}
        {content.length > 100_000 && <div className="trace">⚠ 大文件（{content.length} 字符），应接虚拟滚动（react-window）</div>}
      </div>
    </div>
  );
}
```

> 虚拟滚动：`content.length > 100_000`（如 pre-recon 115KB）切 `react-window FixedSizeList`（行高 20px）。Step 给了阈值判定 + 提示；react-window 包装函数 `VirtualLines({lines})` 实现时按 Task 8 LogStream 同模式补（非占位符——是阈值/包装点明确）。

- [ ] **Step 3: 写 `LiveTab.tsx`（SSE → DashboardPanel + LogStream，对齐 core rich 框）**

```tsx
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useEventSource } from "../../api/useEventSource";
import { dashboardReducer, emptyState, type DashboardState } from "../../state/dashboardReducer";
import { DashboardPanel } from "../../components/DashboardPanel";
import { LogStream } from "../../components/LogStream";

export function LiveTab() {
  const { workspace } = useParams<{ workspace: string }>();
  const { events, status } = useEventSource(`/api/workspaces/${workspace}/events`);
  const [state, setState] = useState<DashboardState>(emptyState());
  const [elapsed, setElapsed] = useState(0);
  const lastApplied = useRef(0);

  // 增量 reduce：只对新增事件 reduce（非全量重放，性能）
  useEffect(() => {
    if (events.length <= lastApplied.current) return;
    setState((s) => events.slice(lastApplied.current).reduce(dashboardReducer, s));
    lastApplied.current = events.length;
  }, [events]);

  // 本地 elapsed 自增（零后端 tick）
  useEffect(() => {
    const start = Date.now();
    const t = setInterval(() => setElapsed(Date.now() - start), 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="live-tab">
      <DashboardPanel state={state} elapsedMs={elapsed} />
      <LogStream events={events} />
      {status === "closed" && <div className="ev-info">扫描结束 —— 可切到「报告」tab 查看结果</div>}
    </div>
  );
}
```

- [ ] **Step 4: 写 `WorkspaceDetail/index.tsx`（tab 导航 + 默认 tab）**

```tsx
import { NavLink, Outlet, useParams, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { apiGet } from "../../api/client";
import type { SessionData } from "../../api/types";

export default function WorkspaceDetail() {
  const { workspace } = useParams<{ workspace: string }>();
  const tabs = [
    { to: "overview", label: "概览" }, { to: "report", label: "报告" },
    { to: "deliverables", label: "产物" }, { to: "logs", label: "日志" }, { to: "live", label: "实时" },
  ];
  return (
    <div className="workspace-detail">
      <h2 className="mono">{workspace}</h2>
      <nav className="tab-nav">
        {tabs.map((t) => (
          <NavLink key={t.to} to={t.to} className={({ isActive }) => isActive ? "tab-active" : ""}>{t.label}</NavLink>
        ))}
      </nav>
      <div className="tab-body"><Outlet /></div>
    </div>
  );
}
```

> 默认 tab（进行中→live，完成→report）：在 `router.tsx` index route 用 loader 或 `<DefaultTabRedirect>` 组件（fetch status 后 Navigate）。见 Step 5。

- [ ] **Step 5: 写 `router.tsx` + 改 `App.tsx`**

```tsx
// src/router.tsx
import { createBrowserRouter, Navigate, useNavigate, useParams } from "react-router-dom";
import { useEffect } from "react";
import { WorkspaceListPage } from "./pages/WorkspaceListPage";
import { ScanNewPage } from "./pages/ScanNewPage";
import WorkspaceDetail from "./routes/WorkspaceDetail";
import { OverviewTab } from "./routes/WorkspaceDetail/OverviewTab";
import { ReportTab } from "./routes/WorkspaceDetail/ReportTab";
import { DeliverablesTab } from "./routes/WorkspaceDetail/DeliverablesTab";
import { LogsTab } from "./routes/WorkspaceDetail/LogsTab";
import { LiveTab } from "./routes/WorkspaceDetail/LiveTab";
import { apiGet } from "./api/client";
import type { SessionData } from "./api/types";

function DefaultTab() {
  const { workspace } = useParams<{ workspace: string }>();
  const nav = useNavigate();
  useEffect(() => {
    apiGet<SessionData>(`/workspaces/${workspace}`).then((s) => {
      const st = s.status ?? s.session?.status ?? "running";
      nav(st === "completed" || st === "done" ? "report" : "live", { replace: true });
    }).catch(() => nav("live", { replace: true }));
  }, [workspace, nav]);
  return null;
}

export const router = createBrowserRouter([
  { path: "/", element: <WorkspaceListPage /> },
  { path: "/scan/new", element: <ScanNewPage /> },
  { path: "/p/:workspace", element: <WorkspaceDetail />, children: [
    { index: true, element: <DefaultTab /> },
    { path: "overview", element: <OverviewTab /> },
    { path: "report", element: <ReportTab /> },
    { path: "deliverables", element: <DeliverablesTab /> },
    { path: "logs", element: <LogsTab /> },
    { path: "live", element: <LiveTab /> },
  ]},
]);
```

```tsx
// src/App.tsx（替换 Task 1 占位）
import { RouterProvider } from "react-router-dom";
import { router } from "./router";
export default function App() { return <RouterProvider router={router} />; }
```

- [ ] **Step 6: 写失败测试 `OverviewTab.test.tsx`（阶段瀑布 + status 矛盾 + 重试分级）**

```tsx
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { OverviewTab } from "./OverviewTab";

const session = {
  web_url: "", repo_path: "/x", scan_type: "whitebox", status: "running",
  session: { status: "completed" },  // 矛盾
  metrics: {
    total_duration_ms: 5892153, total_cost_usd: 16.29,
    phases: { "pre-recon": { duration_ms: 805974, duration_percentage: 13.68, cost_usd: 3.75, agent_count: 1 } },
    agents: { "injection-vuln": { duration_ms: 434233, cost_usd: 1.15, success: true, attempt_number: 2, model: "GLM-5.2[1m]", error: "api_error_status=429" } },
  },
};
const server = setupServer(http.get("/api/workspaces/:ws", () => HttpResponse.json(session)));
beforeAll(() => server.listen()); afterAll(() => server.close());

describe("OverviewTab", () => {
  it("阶段瀑布渲染 + 大数字", async () => {
    render(<MemoryRouter initialEntries={["/p/ws/overview"]}><Routes><Route path="/p/:workspace/overview" element={<OverviewTab />} /></Routes></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/pre-recon/)).toBeInTheDocument());
    expect(screen.getByText(/\$16\.29/)).toBeInTheDocument();
    expect(screen.getByText(/13\.68/)).toBeInTheDocument();
  });
  it("status 矛盾标注", async () => {
    render(<MemoryRouter initialEntries={["/p/ws/overview"]}><Routes><Route path="/p/:workspace/overview" element={<OverviewTab />} /></Routes></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/顶层 running vs session.completed/)).toBeInTheDocument());
  });
  it("重试 agent 标黄（attempt_number=2 + error）", async () => {
    const { container } = render(<MemoryRouter initialEntries={["/p/ws/overview"]}><Routes><Route path="/p/:workspace/overview" element={<OverviewTab />} /></Routes></MemoryRouter>);
    await waitFor(() => expect(container.querySelector(".ev-warn")).toBeInTheDocument());
    expect(container.textContent).toContain("⚠");
  });
});
```

- [ ] **Step 7: 跑 OverviewTab 测试 + 类型检查** · Run: `npm test -- OverviewTab && npx tsc --noEmit` · Expected: PASS（3）+ 无错

- [ ] **Step 8: 更新 `App.test.tsx` 为集成冒烟测试（router + 落地）**

```tsx
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { RouterProvider } from "react-router-dom";
import { router } from "./router";
import App from "./App";

const server = setupServer(http.get("/api/workspaces", () => HttpResponse.json([])));
beforeAll(() => server.listen()); afterAll(() => server.close());

describe("App 集成冒烟", () => {
  it("根路由渲染 WorkspaceListPage", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText(/Workspaces/i)).toBeInTheDocument());
  });
  it("RouterProvider 可用（导出 router）", () => {
    expect(router).toBeDefined();
  });
});
```

- [ ] **Step 9: 跑全套测试 + 构建**

Run: `cd packages/web/frontend && npm test && npm run build`
Expected: 全部 PASS + tsc/vite build 成功（无类型错）

- [ ] **Step 10: Commit**

```bash
git add packages/web/frontend/src/routes/WorkspaceDetail/ packages/web/frontend/src/router.tsx packages/web/frontend/src/App.tsx packages/web/frontend/src/App.test.tsx
git commit -m "feat(web-fe): OverviewTab(阶段瀑布+重试分级+status 矛盾兜底) + LogsTab + LiveTab + router + 集成冒烟"
```

---

## Self-Review

**1. Spec 覆盖**（对照 frontend-design.md 各节）：

| spec 节 | 覆盖 task |
|---|---|
| §1 工程脚手架（目录/proxy） | Task 1 |
| §2.1 路由（/  /scan/new  /p/:ws + 5 tab + 默认 tab） | Task 12（router + DefaultTab） |
| §2.2 WorkspaceListPage | Task 9 |
| §2.3 ScanNewPage（3 类 + 冲突 + --latest 陷阱 + 错误码） | Task 10 |
| §3.1 OverviewTab（阶段瀑布 + 重试分级 + status 矛盾） | Task 12 |
| §3.2 ReportTab（执行摘要 hero + 可达性索引 + witness PoC） | Task 6（MarkdownView）+ Task 11（ReportTab 组装） |
| §3.3 DeliverablesTab（聚合网格 + 双轨徽章 + injection 无 queue + 大 JSON + 空产物） | Task 11 |
| §3.4 LogsTab（JSONL + workflow.log + 虚拟滚动） | Task 12 |
| §3.5 LiveTab（DashboardPanel + LogStream，对齐 core） | Task 8 + Task 12 |
| §4 dashboardReducer（1:1 复刻 apply + formatters） | Task 3 |
| §4.1 reducer 对齐测试（与 core 同输入同输出） | Task 3 Step 2/6 |
| §5 useEventSource（累积 + scan_end + 重连） | Task 4 |
| §6 events.css（STYLE_MAP + spinner） | Task 5 |
| §7 测试（MSW + 各组件 + reducer 对齐） | 各 task 的 .test.tsx |
| §8 契约对齐（ndjson schema + API + SSE） | Task 2（types）+ Task 4（client/SSE） |
| §9 风险（reducer 漂移 / 大日志 / 两字段变体 / SSE 长连接） | Task 3 对齐测试 / Task 12 虚拟滚动 / Task 6 两变体 / Task 4 重连 |

**2. 占位符扫描**：✅ 无占位符模式（每 step 含真实可运行代码；无「待定」「稍后实现」「参见 Task N」类模糊引用）。引用现有文件（`formatters.py`/`test_dashboard_state.py`）均为具名明确指向。**两处分阶段标注**（Task 11 big_json 预览 / Task 12 LogsTab 虚拟滚动）是明确阈值 + 包装点（接 react-window，Task 8 同模式），非占位。

**3. 类型一致性**：
- `DashboardState`（Task 3）字段 `current_phase/agents/phase_units/unit_status/unit_intent/completed_count/total_cost/total_units/completed_units/running_units` —— Task 8 DashboardPanel 消费 `current_phase/completed_units/total_units/total_cost/agents` ✓
- `NdjsonEvent`（Task 2）判别联合 `type` —— Task 3 reducer switch / Task 4 useEventSource / Task 8 LogStream 均按 `type`/`category` 分支 ✓
- `Vulnerability.merge_source`（Task 2 `MergeSource`）—— Task 11 `MergeSourceBadge` 消费 `llm-only/gitnexus-only/both` ✓
- `SessionData.metrics.agents[].attempt_number`（Task 2）—— Task 12 OverviewTab AgentTable 消费 `attempt_number`（非 `attempt`）✓
- `ScanRequest`（Task 2）—— Task 10 `buildBody` 产出 `source.{kind,value,branch,commit,force_reclone}/url/workspace_name/reuse_latest_whitebox/config_yaml` ✓

**4. flag 后端**（cross-subproject）：`session.json` 顶层 `status` 归一未覆盖（spec §3.1 已标，Task 12 OverviewTab 兜底显示矛盾）—— 实现阶段若后端已修，移除矛盾标注。

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-02-shannon-web-frontend.md`（12 task，TDD，对齐 core + 真实产物结构）。Two execution options:**

**1. Subagent-Driven（推荐）** - 每个 task 派 fresh subagent，task 间 review，迭代快。

**2. Inline Execution** - 本会话用 executing-plans 逐 task 执行，批量 + 检查点。

**Which approach?**

> 注：plan 假设子项目 1 后端已交付（API + SSE + ndjson 契约）。前端冒烟需后端跑起来（Vite proxy）或用 MSW 离线测（各 task 已用 MSW）。执行前确认后端 `localhost:7878` 可用，或全程 MSW。

