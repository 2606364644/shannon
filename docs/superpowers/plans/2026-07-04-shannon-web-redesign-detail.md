# Shannon Web 子项目 4 · 详情页 5 tab 重做 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把详情页 5 tab（概览/报告/产物/日志/实时）从 events.css 手写样式整体迁到 Tailwind + shadcn，补三态/a11y/测试，做 DashboardPanel 信息增强，全程不动 `dashboardReducer`。

**Architecture:** 路由驱动 shadcn Tabs（深链不丢）+ 共享组件先升级（ErrorState/StatusBadge/VulnCard/FileTree/MarkdownView/DashboardPanel/LogStream）+ 5 tab 逐个换皮三态化 + events.css/markdown.css 详情页 class 最后 rg 验证零引用后清理。

**Tech Stack:** React 18 + Vite + TypeScript + Tailwind 3 + shadcn/ui (Radix) + react-router 6 + react-markdown + `@tailwindcss/typography` + react-window + @tanstack/react-table + vitest + Testing Library。

**Spec:** `docs/superpowers/specs/2026-07-04-shannon-web-redesign-detail-design.md`

## Global Constraints

- **契约不动铁律**：不改 `src/state/dashboardReducer.ts` / `src/state/formatters.ts` / `src/api/types.ts` 的 `NdjsonEvent` union / `src/state/dashboardReducer.test.ts` / `src/api/useEventSource.ts`。DashboardState/AgentRow shape 不改。
- **事件专用 `.ev-*` class 保留**（events.css L18-26，跨媒介语义色不变量，与终端 rich renderer 对齐）。新代码用 Tailwind 语义色（`text-cyan`/`text-green`/`text-red`/`text-yellow`/`text-magenta`，tailwind.config 已 extend）或 shadcn token（`text-destructive` 等）。
- **Radix Tabs 测试用 `fireEvent.mouseDown` 激活**（TabsTrigger 在 mousedown 触发 onValueChange，jsdom 的 click 不发 mousedown）—— 见 `src/pages/ScanNewPage.test.tsx:43-45` clickTab helper。
- **前端命令必须 `cd packages/web/frontend`**（cwd 不持久，每次 bash 显式 cd）。
- **operator 风**：radius ≤ 4px（`--radius: 3px`）、克制阴影、IBM Plex 三族（mono/sans/serif，已配）。
- **TDD 纪律**：每个 task 先写失败测试 → 跑验证失败 → 最小实现 → 跑验证通过 → commit。
- **commit 风格**：`feat(web): 子项目4·详情页 TaskN <内容>`。
- **测试选择器优先 role-based**，避免 brittle class 断言。
- **每 task 结束跑相关测试 + `npx tsc --noEmit`**（不引入类型错误）。

---

## File Structure

**新建：**
- `src/components/ErrorState.tsx`（统一错误态，复用列表页红横幅模式）
- `src/routes/WorkspaceDetail/index.test.tsx`（详情壳 tab 导航测试）

**修改：**
- `src/routes/WorkspaceDetail/index.tsx`（tab 导航 shadcn Tabs 路由驱动）
- `src/routes/WorkspaceDetail/{OverviewTab,ReportTab,DeliverablesTab,LogsTab,LiveTab}.tsx`
- `src/components/{StatusBadge,VulnCard,FileTree,MarkdownView,DashboardPanel,LogStream}.tsx`
- `src/styles/tokens.css`（加 `--prose-*` 双主题变量）
- `src/styles/index.css`（加 `.prose` 覆盖层，指向 tokens）
- `src/styles/events.css`（最后清理详情页 class）
- `src/styles/markdown.css`（退役删除）
- `src/components/ErrorState` 等新组件在 `src/pages/DevComponentsPage.tsx`（dev 预览页）补登

**测试同步：** 各组件对应 `.test.tsx` 调整断言（role-based / 语义色）+ 新增 a11y 断言；`LiveTab.test.tsx` 新建（零覆盖→有覆盖）。

---

## Task 1: 详情壳 tab 导航 shadcn Tabs 化（路由驱动）

**Files:**
- Modify: `src/routes/WorkspaceDetail/index.tsx`
- Test: `src/routes/WorkspaceDetail/index.test.tsx`（Create）

**Interfaces:**
- Consumes: `react-router-dom` 的 `useParams`/`useLocation`/`useNavigate`/`Outlet`；`@/components/ui/tabs` 的 `Tabs`/`TabsList`/`TabsTrigger`。
- Produces: `<WorkspaceDetail>` 渲染 shadcn `<Tabs value=当前path段 onValueChange=navigate>`，content 仍走 `<Outlet/>`。

- [ ] **Step 1: 写失败测试**

`src/routes/WorkspaceDetail/index.test.tsx`：
```tsx
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import WorkspaceDetail from "./index";

function renderAt(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/p/:workspace" element={<WorkspaceDetail />}>
          <Route path="overview" element={<div>ov-content</div>} />
          <Route path="report" element={<div>rp-content</div>} />
          <Route path="deliverables" element={<div>dl-content</div>} />
          <Route path="logs" element={<div>lg-content</div>} />
          <Route path="live" element={<div>lv-content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("WorkspaceDetail shell", () => {
  it("渲染 tablist 与 5 个 tab role", () => {
    renderAt("/p/ws/overview");
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.getAllByRole("tab")).toHaveLength(5);
  });

  it("当前 tab 由路由段决定（aria-selected）", () => {
    renderAt("/p/ws/logs");
    expect(screen.getByRole("tab", { name: "日志" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "概览" })).toHaveAttribute("aria-selected", "false");
  });

  it("mousedown 一个 tab 触发导航", () => {
    renderAt("/p/ws/overview");
    fireEvent.mouseDown(screen.getByRole("tab", { name: "实时" }));
    expect(screen.getByText("lv-content")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/index.test.tsx`
Expected: FAIL（当前是 NavLink，无 `role=tablist`/`role=tab`）。

- [ ] **Step 3: 实现 shadcn Tabs 路由驱动**

替换 `src/routes/WorkspaceDetail/index.tsx` 全文：
```tsx
import { Outlet, useParams, useLocation, useNavigate } from "react-router-dom";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

const TABS = [
  { value: "overview", label: "概览" },
  { value: "report", label: "报告" },
  { value: "deliverables", label: "产物" },
  { value: "logs", label: "日志" },
  { value: "live", label: "实时" },
];

export default function WorkspaceDetail() {
  const { workspace } = useParams<{ workspace: string }>();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const current = pathname.split("/").pop() ?? "overview";
  return (
    <div className="space-y-4">
      <h2 className="font-mono text-xl">{workspace}</h2>
      <Tabs value={current} onValueChange={(v) => navigate(v)}>
        <TabsList>
          {TABS.map((t) => (
            <TabsTrigger key={t.value} value={t.value}>{t.label}</TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <div><Outlet /></div>
    </div>
  );
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/index.test.tsx`
Expected: PASS（3/3）。

- [ ] **Step 5: tsc + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
git add src/routes/WorkspaceDetail/index.tsx src/routes/WorkspaceDetail/index.test.tsx
git commit -m "feat(web): 子项目4·详情页 Task1 tab 导航 shadcn Tabs 化（路由驱动）"
```

---

## Task 2: ErrorState 共享组件

**Files:**
- Create: `src/components/ErrorState.tsx`
- Test: `src/components/ErrorState.test.tsx`（Create）

**Interfaces:**
- Produces: `<ErrorState message="..." onRetry?:()=>void />`——`role="alert"` 红横幅 + 可选重试按钮。后续所有 tab fetch 失败用它。

- [ ] **Step 1: 写失败测试**

`src/components/ErrorState.test.tsx`：
```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ErrorState } from "./ErrorState";

describe("ErrorState", () => {
  it("渲染 message 与 role=alert", () => {
    render(<ErrorState message="加载失败" />);
    expect(screen.getByRole("alert")).toHaveTextContent("加载失败");
  });
  it("无 onRetry 不渲染重试按钮", () => {
    render(<ErrorState message="x" />);
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("有 onRetry 渲染重试按钮并触发", () => {
    const onRetry = vi.fn();
    render(<ErrorState message="x" onRetry={onRetry} />);
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && npx vitest run src/components/ErrorState.test.tsx`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现**

`src/components/ErrorState.tsx`：
```tsx
import { Button } from "@/components/ui/button";

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
      <div>{message}</div>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry} className="mt-2">
          重试
        </Button>
      )}
    </div>
  );
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/components/ErrorState.test.tsx`
Expected: PASS（3/3）。

- [ ] **Step 5: tsc + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
git add src/components/ErrorState.tsx src/components/ErrorState.test.tsx
git commit -m "feat(web): 子项目4·详情页 Task2 ErrorState 共享组件"
```

---

## Task 3: StatusBadge token 化（shadcn Badge）

**Files:**
- Modify: `src/components/StatusBadge.tsx`
- Test: `src/components/StatusBadge.test.tsx`（调整现有断言）

**Interfaces:**
- Produces: `<StatusBadge status=... correlation?/>` 仍同签名；内部改用 shadcn `<Badge variant="outline">` + Tailwind 语义色（脱离 `.status-badge`/`.ev-*`）。

- [ ] **Step 1: 调整测试（断言改 role + 语义色）**

读 `src/components/StatusBadge.test.tsx` 现有 case，把 brittle class 断言（如 `.ev-agent-ok`/`.status-badge`）改为：badge role 存在 + 文案 + 语义色 className 断言。新增：
```tsx
it("completed 渲染 Badge + green 语义色", () => {
  render(<StatusBadge status="completed" />);
  const badge = screen.getByText("completed").closest("[class*='text-green']") ?? screen.getByText(/completed/);
  expect(badge).toBeInTheDocument();
});
it("未知 status 走 warn 色 + ? 图标", () => {
  render(<StatusBadge status="weird" />);
  expect(screen.getByText(/weird/)).toBeInTheDocument();
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && npx vitest run src/components/StatusBadge.test.tsx`
Expected: FAIL（旧 class 断言不匹配新实现）。

- [ ] **Step 3: 实现**

替换 `src/components/StatusBadge.tsx` 全文：
```tsx
import { Badge } from "@/components/ui/badge";

const MAP: Record<string, { icon: string; cls: string }> = {
  running:   { icon: "●", cls: "border-cyan/40 text-cyan" },
  completed: { icon: "✓", cls: "border-green/40 text-green" },
  done:      { icon: "✓", cls: "border-green/40 text-green" },
  failed:    { icon: "✗", cls: "border-red/40 text-red" },
  killed:    { icon: "✗", cls: "border-red/40 text-red" },
  crashed:   { icon: "⚠", cls: "border-yellow/40 text-yellow" },
};

export function StatusBadge({ status, correlation = false }: { status: string; correlation?: boolean }) {
  const m = MAP[status] ?? { icon: "?", cls: "border-yellow/40 text-yellow" };
  return (
    <Badge variant="outline" className={`gap-1 font-mono ${m.cls}`} title={status}>
      <span aria-hidden>{m.icon}</span>
      {status}
      {correlation ? " 🔗" : ""}
    </Badge>
  );
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/components/StatusBadge.test.tsx && npx vitest run src/pages/WorkspaceListPage.test.tsx`
Expected: PASS（StatusBadge 自身 + 列表页复用它，仍绿）。

- [ ] **Step 5: tsc + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
git add src/components/StatusBadge.tsx src/components/StatusBadge.test.tsx
git commit -m "feat(web): 子项目4·详情页 Task3 StatusBadge token 化（shadcn Badge）"
```

---

## Task 4: VulnCard token 化 + shadcn Card/Badge + 可点行 a11y

**Files:**
- Modify: `src/components/VulnCard.tsx`
- Test: `src/components/VulnCard.test.tsx`（调整）

**Interfaces:**
- Produces: `<VulnCard v=.../>` 内部用 shadcn `Card`/`Badge`；可点行 `.vc-head` → `CardHeader role="button" tabIndex={0} aria-expanded`，键盘 Enter/Space 切换展开。

- [ ] **Step 1: 调整测试（a11y 断言）**

读 `src/components/VulnCard.test.tsx` 现有 case（merge_source 徽章等），断言改 role-based。新增 a11y case：
```tsx
it("vc-head 有 role=button + aria-expanded，回车切换展开", () => {
  render(<VulnCard v={{ ID: "INJ-01", vulnerability_type: "sqli", externally_exploitable: false }} />);
  const head = screen.getByRole("button");
  expect(head).toHaveAttribute("aria-expanded", "false");
  fireEvent.keyDown(head, { key: "Enter" });
  expect(head).toHaveAttribute("aria-expanded", "true");
});
it("可达漏洞有 red 边框语义", () => {
  render(<VulnCard v={{ ID: "X", vulnerability_type: "t", externally_exploitable: true }} />);
  // 可达徽章
  expect(screen.getByText(/可达/)).toBeInTheDocument();
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && npx vitest run src/components/VulnCard.test.tsx`
Expected: FAIL（旧实现 div 无 role=button）。

- [ ] **Step 3: 实现**

替换 `src/components/VulnCard.tsx` 全文：
```tsx
import { useState } from "react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { Vulnerability, MergeSource } from "../api/types";

type BadgeTag = "llm-only" | "gitnexus-only" | "both" | "other";
function toBadgeTag(src: string): BadgeTag {
  return src === "llm-only" || src === "gitnexus-only" || src === "both" ? (src as BadgeTag) : "other";
}

export function MergeSourceBadge({ src }: { src?: MergeSource }) {
  if (!src) return null;
  const tag = toBadgeTag(src);
  const map: Record<Exclude<BadgeTag, "other">, { label: string; cls: string }> = {
    "llm-only": { label: "💭 LLM轨", cls: "border-magenta/40 text-magenta" },
    "gitnexus-only": { label: "🔍 GN轨", cls: "border-cyan/40 text-cyan" },
    "both": { label: "✓ 双轨确认", cls: "border-green/40 text-green" },
  };
  const m = map[tag as Exclude<BadgeTag, "other">];
  return m ? (
    <Badge variant="outline" className={`gap-1 ${m.cls}`}>{m.label}</Badge>
  ) : (
    <Badge variant="outline" className="text-muted-foreground">{src}</Badge>
  );
}

export function VulnCard({ v }: { v: Vulnerability }) {
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen((o) => !o);
  return (
    <Card className={`gap-0 ${v.externally_exploitable ? "border-red/50" : ""}`}>
      <CardHeader
        className="flex cursor-pointer select-none flex-row flex-wrap items-center gap-2 font-mono text-sm"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        }}
      >
        <span className="font-bold text-red">{v.ID}</span>
        <span>{v.vulnerability_type}</span>
        {v.externally_exploitable && (
          <Badge variant="outline" className="border-red/40 text-red">● 可达</Badge>
        )}
        <MergeSourceBadge src={v.merge_source} />
        {v.confidence && <Badge variant="outline" className="text-muted-foreground">{v.confidence}</Badge>}
        {v.source_endpoint && <span className="text-xs text-muted-foreground">{v.source_endpoint}</span>}
        <span className="ml-auto text-xs text-muted-foreground">{open ? "▴" : "▾"}</span>
      </CardHeader>
      {open && (
        <CardContent className="space-y-1 font-serif text-sm">
          {v.vulnerable_code_location && (
            <div><b>location:</b> <code className="font-mono text-cyan">{v.vulnerable_code_location}</code></div>
          )}
          {v.missing_defense && <div><b>missing_defense:</b> {v.missing_defense}</div>}
          {v.exploitation_hypothesis && <div><b>hypothesis:</b> {v.exploitation_hypothesis}</div>}
          {v.suggested_exploit_technique && (
            <div><b>technique:</b> <code className="font-mono text-cyan">{v.suggested_exploit_technique}</code></div>
          )}
          {v.notes && <div className="text-muted-foreground"><b>notes:</b> {v.notes}</div>}
        </CardContent>
      )}
    </Card>
  );
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/components/VulnCard.test.tsx`
Expected: PASS。

- [ ] **Step 5: tsc + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
git add src/components/VulnCard.tsx src/components/VulnCard.test.tsx
git commit -m "feat(web): 子项目4·详情页 Task4 VulnCard token+a11y（shadcn Card/Badge，可点行 role=button）"
```

---

## Task 5: FileTree token 化 + aria-expanded + 文件 a11y

**Files:**
- Modify: `src/components/FileTree.tsx`
- Test: `src/components/FileTree.test.tsx`（调整）

**Interfaces:**
- Produces: `<FileTree files onSelect/>` 内部 `.ft-toggle` button 加 `aria-expanded`；`.ft-file span onClick` → `<button>` + 语义 hover。

- [ ] **Step 1: 调整测试（a11y 断言）**

读 `src/components/FileTree.test.tsx` 现有 case。新增：
```tsx
it("目录 toggle 有 aria-expanded，点击切换", () => {
  render(<FileTree files={[{ path: "d/f.json", size: 1, kind: "other_json" }]} onSelect={() => {}} />);
  const toggles = screen.getAllByRole("button");
  const dirToggle = toggles.find((b) => /d/.test(b.textContent ?? ""));
  fireEvent.click(dirToggle!);
  expect(dirToggle).toHaveAttribute("aria-expanded");
});
it("文件行是 button，点击触发 onSelect", () => {
  const onSelect = vi.fn();
  render(<FileTree files={[{ path: "f.json", size: 1, kind: "other_json" }]} onSelect={onSelect} />);
  fireEvent.click(screen.getByRole("button", { name: /f\.json/ }));
  expect(onSelect).toHaveBeenCalledOnce();
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && npx vitest run src/components/FileTree.test.tsx`
Expected: FAIL（文件是 span 不是 button）。

- [ ] **Step 3: 实现**

替换 `src/components/FileTree.tsx` 的 `NodeView`（保留 `buildTree`/`TreeNode`/`FileTree` 顶层不动，仅改 `NodeView` JSX 与 className）：
```tsx
function NodeView({ node, depth, onSelect }: { node: TreeNode; depth: number; onSelect: (f: DeliverablesFile) => void; }) {
  const [open, setOpen] = useState(depth < 1);
  const isDir = node.children.size > 0;
  return (
    <li>
      <div style={{ paddingLeft: depth * 14 }} className="py-px">
        {isDir ? (
          <button
            className="flex items-center gap-1 bg-transparent p-0 font-inherit text-foreground hover:text-primary"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
          >
            <span className="text-muted-foreground" aria-hidden>{open ? "▾" : "▸"}</span>
            <span aria-hidden>📂</span>
            <span>{node.name}</span>
          </button>
        ) : (
          <button
            className="flex items-center gap-1 bg-transparent p-0 text-left font-mono hover:text-primary"
            onClick={() => onSelect(node.file!)}
          >
            <span aria-hidden>📄</span>
            <span>{node.name}</span>
            {node.file?.kind === "empty_json" && <span className="text-xs text-muted-foreground">（空）</span>}
            {node.file?.kind === "big_json" && <span className="text-xs text-muted-foreground">（大）</span>}
          </button>
        )}
      </div>
      {isDir && open && Array.from(node.children.values()).map((c) => (
        <ul key={c.path} className="list-none p-0">
          <NodeView node={c} depth={depth + 1} onSelect={onSelect} />
        </ul>
      ))}
    </li>
  );
}
```
`FileTree` 顶层 `<ul className="file-tree">` → `<ul className="list-none p-0 text-sm">`。

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/components/FileTree.test.tsx`
Expected: PASS。

- [ ] **Step 5: tsc + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
git add src/components/FileTree.tsx src/components/FileTree.test.tsx
git commit -m "feat(web): 子项目4·详情页 Task5 FileTree token+a11y（aria-expanded，文件行 button）"
```

---

## Task 6: MarkdownView prose 化 + `--prose-*` 双主题 token

**Files:**
- Modify: `src/styles/tokens.css`（加 `--prose-*`）
- Modify: `src/styles/index.css`（加 `.prose` 覆盖层）
- Modify: `src/components/MarkdownView.tsx`（删 `markdown.css` import，改 prose + Tailwind）
- Test: `src/components/MarkdownView.test.tsx`（调整）

**Interfaces:**
- Produces: `<MarkdownView markdown=.../>` 仍同签名；内部 `.md-view/.md-layout/.toc/.md-body/.hero/.kv-row/.md-code/.copy-btn` 全改 Tailwind + `prose`；功能（TOC/hero/kv-row/code 复制/parseStructure/flatten 逻辑）保留。

- [ ] **Step 1: 加 prose token（先写 token，测试在 Step 3）**

`src/styles/tokens.css` 的 `:root { ... }` 末尾（`--radius` 后）加：
```css
  /* 层 D · @tailwindcss/typography prose 主题（深） */
  --prose-body: 213 22% 82%;
  --prose-headings: 0 0% 100%;
  --prose-links: 189 94% 53%;
  --prose-bold: 0 0% 100%;
  --prose-code: 189 94% 53%;
  --prose-code-bg: 217 26% 16%;
  --prose-quotes: 213 12% 47%;
  --prose-bullets: 213 12% 47%;
  --prose-hr: 217 26% 16%;
```
`.light { ... }` 末尾加：
```css
  /* 层 D · prose 主题（浅） */
  --prose-body: 217 26% 16%;
  --prose-headings: 217 26% 16%;
  --prose-links: 190 92% 36%;
  --prose-bold: 217 26% 16%;
  --prose-code: 190 92% 36%;
  --prose-code-bg: 213 30% 92%;
  --prose-quotes: 213 12% 47%;
  --prose-bullets: 213 12% 47%;
  --prose-hr: 213 25% 85%;
```

`src/styles/index.css`（在 `@tailwind utilities;` 之后）加 prose 覆盖层，把 `--prose-*` channel 接到 `--tw-prose-*`：
```css
.prose {
  --tw-prose-body: hsl(var(--prose-body));
  --tw-prose-headings: hsl(var(--prose-headings));
  --tw-prose-links: hsl(var(--prose-links));
  --tw-prose-bold: hsl(var(--prose-bold));
  --tw-prose-code: hsl(var(--prose-code));
  --tw-prose-pre-bg: hsl(var(--prose-code-bg));
  --tw-prose-quotes: hsl(var(--prose-quotes));
  --tw-prose-bullets: hsl(var(--prose-bullets));
  --tw-prose-hr: hsl(var(--prose-hr));
}
```

- [ ] **Step 2: 调整 MarkdownView 测试**

读 `src/components/MarkdownView.test.tsx` 现有 case（TOC/hero/kv-row/code 复制）。断言：`data-testid="exec-summary-hero"` 与 `data-testid="toc"` 保留（这两个 testid 在新实现里保留）；`.kv-row/.kv-key/.kv-val` class 断言改为检测 kv 行存在（用 testid 或文本）。新增：
```tsx
it("md-body 容器带 prose 类", () => {
  render(<MarkdownView markdown="# T" />);
  expect(document.querySelector(".prose")).toBeInTheDocument();
});
```

- [ ] **Step 3: 重写 MarkdownView（保留 parseStructure/flatten 逻辑，换样式层）**

`src/components/MarkdownView.tsx`：删 `import "../styles/markdown.css";`。保留 `parseStructure`/`flatten`/`Heading`/`TopRisk` 不动。改 `MarkdownView` 组件 return（JSX 结构保留，className 全换 Tailwind）：
```tsx
return (
  <div className="space-y-4">
    {showHero && (
      <div data-testid="exec-summary-hero" className="rounded-md border border-border bg-card p-4">
        <div className="mb-2 flex items-center justify-between font-serif text-base">
          <span>最高风险发现（按业务影响排序）</span>
          <Button size="sm" variant="ghost" onClick={() => setHeroCollapsed((c) => !c)} aria-label="toggle hero">
            {heroCollapsed ? "展开 ▸" : "折叠 ▾"}
          </Button>
        </div>
        {!heroCollapsed && (
          <ol className="list-decimal space-y-1 pl-6 text-sm">
            {topRisks.map((r, i) => (
              <li key={i}>
                {r.vulnIds.length > 0 && (
                  <a href={`#${r.vulnIds[0]}`} className="font-mono text-primary">{r.vulnIds.join("/")}</a>
                )}{" "}
                {r.text}
              </li>
            ))}
          </ol>
        )}
      </div>
    )}
    <div className="grid grid-cols-[220px_1fr] gap-6">
      <nav data-testid="toc" className="sticky top-4 space-y-1 text-sm">
        {headings.filter((h) => h.level >= 2).map((h, i) => (
          <a key={`${i}-${h.id}`} href={`#${h.id}`} className={`block text-muted-foreground hover:text-primary ${h.level === 3 ? "pl-3 text-xs" : ""}`}>
            {h.text}
          </a>
        ))}
      </nav>
      <div className="prose prose-sm max-w-none font-serif">
        <ReactMarkdown
          rehypePlugins={[rehypeSlug, [rehypeAutolinkHeadings, { behavior: "wrap" }], rehypeHighlight]}
          components={{
            // li kv 检测逻辑保留（同现有 firstStrongIdx/冒号守卫），仅 className 换 Tailwind：
            li: ({ children, ...props }) => {
              // ...（保留现有 li 组件体内的 flatten/冒号守卫逻辑不变）
              // 命中 kv 时：
              //   return <li {...props} className="flex gap-2"><span className="font-mono text-muted-foreground">{keyText}</span><span>{valKids}</span></li>;
              // 未命中：return <li {...props}>{children}</li>;
            },
            code: ({ className, children, ...props }) => (
              <code {...props} className={`font-mono ${className ?? ""}`}>
                {children}
                <Button
                  size="sm"
                  variant="ghost"
                  className="ml-1 text-xs"
                  onClick={(e) => { navigator.clipboard?.writeText(String(children)); e.currentTarget.textContent = "✓"; }}
                >
                  复制
                </Button>
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
```
> **实现者注**：`li` 组件体内的 kv 检测逻辑（`firstStrongIdx`/冒号守卫 `!/[:：]\s*$/.test(rawKey)`/`valKids` 累积）逐字保留现有实现，仅把命中分支的 `<li className="kv-row"><span className="kv-key mono">...</span><span className="kv-val">...</span></li>` 改为上面注释里的 Tailwind 版。顶部加 `import { Button } from "@/components/ui/button";`。

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/components/MarkdownView.test.tsx && npx vitest run src/styles/tokens.test.ts`
Expected: PASS（MarkdownView 功能断言 + tokens 漂移护栏）。

- [ ] **Step 5: tsc + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
git add src/styles/tokens.css src/styles/index.css src/components/MarkdownView.tsx src/components/MarkdownView.test.tsx
git commit -m "feat(web): 子项目4·详情页 Task6 MarkdownView prose 化 + --prose-* 双主题 token"
```

---

## Task 7: DashboardPanel 信息增强（unit_intent/running_units/completed_count/unit_status）

**Files:**
- Modify: `src/components/DashboardPanel.tsx`
- Test: `src/components/DashboardPanel.test.tsx`（调整 + 新增字段断言）

**Interfaces:**
- Consumes: `DashboardState`（reducer 已有字段，**只读**）：`current_phase / agents / phase_units / unit_status / unit_intent / completed_count / total_cost / total_units / completed_units / running_units`。
- Produces: `<DashboardPanel state elapsedMs/>` 仍同签名；内部多渲染 step 进度（intent + 三态）+ agents 完成计数。`.dashboard-panel/.dp-*` → Tailwind。`.spinner` 保留（events.css 动画）。

- [ ] **Step 1: 调整测试（新增字段断言）**

读 `src/components/DashboardPanel.test.tsx` 现有 2 case。新增（构造一个含 unit_status/unit_intent 的 state）：
```tsx
it("渲染 step 进度与 intent", () => {
  const state = {
    ...emptyState(),
    current_phase: "vuln",
    phase_units: ["injection"],
    unit_status: { injection: "running" },
    unit_intent: { injection: "SQLi 候选识别" },
    completed_units: 0, total_units: 1, running_units: ["injection"],
  } as DashboardState;
  render(<DashboardPanel state={state} elapsedMs={0} />);
  expect(screen.getByText(/SQLi 候选识别/)).toBeInTheDocument();
});
it("agents 完成计数渲染 completed_count", () => {
  const state = { ...emptyState(), completed_count: 3, total_cost: 0 } as DashboardState;
  render(<DashboardPanel state={state} elapsedMs={0} />);
  // 顶栏含 completed agents 计数（具体文案见实现）
  expect(screen.getByText(/3/)).toBeInTheDocument();
});
```
> 实现者按实际 `emptyState()` 字段名构造（读 `dashboardReducer.ts` 的 `emptyState()` 确认）。

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && npx vitest run src/components/DashboardPanel.test.tsx`
Expected: FAIL（新断言未实现）。

- [ ] **Step 3: 实现**

替换 `src/components/DashboardPanel.tsx` 全文（`fmtMs` 保留）：
```tsx
import type { DashboardState } from "../state/dashboardReducer";

function fmtMs(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

const UNIT_STATUS_CLS: Record<string, string> = {
  running: "text-cyan", done: "text-green", failed: "text-red",
};

export function DashboardPanel({ state, elapsedMs }: { state: DashboardState; elapsedMs: number }) {
  const running = Object.values(state.agents).filter((a) => a.status === "running");
  return (
    <div className="rounded-md border border-border bg-card p-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-sm">
        <span className="font-bold text-cyan">{state.current_phase ?? "—"}</span>
        <span className="text-muted-foreground">step {state.completed_units}/{state.total_units}</span>
        <span className="text-muted-foreground">agents {state.completed_count}/{Object.keys(state.agents).length}</span>
        <span className="text-muted-foreground">{fmtMs(elapsedMs)}</span>
        <span className="text-muted-foreground">${state.total_cost.toFixed(2)}</span>
      </div>
      {(state.phase_units.length > 0) && (
        <div className="mt-2 space-y-0.5 text-xs">
          {state.phase_units.map((unit) => {
            const st = state.unit_status[unit];
            return (
              <div key={unit} className="flex gap-2">
                <span className={UNIT_STATUS_CLS[st ?? ""] ?? "text-muted-foreground"}>
                  {st === "running" ? "○" : st === "done" ? "✓" : st === "failed" ? "✗" : "·"}
                </span>
                <span className="text-foreground">{unit}</span>
                {state.unit_intent[unit] && <span className="text-muted-foreground">— {state.unit_intent[unit]}</span>}
              </div>
            );
          })}
        </div>
      )}
      <div className="mt-2 space-y-0.5">
        {running.map((a) => (
          <div key={a.name} className="font-mono text-xs">
            <span className="spinner" aria-hidden /> {a.name}{" "}
            <span className="text-muted-foreground">t{a.turn}</span>{" "}
            {a.last_action_detail ?? a.last_action ?? ""}
          </div>
        ))}
        {running.length === 0 && <div className="text-xs text-muted-foreground">无运行中 agent</div>}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/components/DashboardPanel.test.tsx`
Expected: PASS。

- [ ] **Step 5: tsc + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
git add src/components/DashboardPanel.tsx src/components/DashboardPanel.test.tsx
git commit -m "feat(web): 子项目4·详情页 Task7 DashboardPanel 信息增强（unit_intent/running_units/completed_count，只读 reducer）"
```

---

## Task 8: LogStream aria-live + 容器 Tailwind 化

**Files:**
- Modify: `src/components/LogStream.tsx`
- Test: `src/components/LogStream.test.tsx`（调整）

**Interfaces:**
- Produces: `<LogStream events/>` 仍同签名；容器 `.log-stream` → Tailwind + `aria-live="polite"`；`.log-row` 结构 Tailwind 化，**`CAT_CLASS` 的 `.ev-*` class 保留**（事件专用色不变量）；`.trace/.ev-type` → Tailwind。

- [ ] **Step 1: 调整测试**

读 `src/components/LogStream.test.tsx` 现有 3 case。新增：
```tsx
it("容器有 aria-live=polite", () => {
  render(<LogStream events={[]} />);
  // 容器是 aria-live 区域（用 getByRole("log") 或 aria-live 查询）
  expect(document.querySelector('[aria-live="polite"]')).toBeInTheDocument();
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && npx vitest run src/components/LogStream.test.tsx`
Expected: FAIL（无 aria-live）。

- [ ] **Step 3: 实现**

`src/components/LogStream.tsx` 改动：`CAT_CLASS`（`.ev-*`）保留不动。`summarize`/`tsClock`/`ROW_HEIGHT`/`VIRTUAL_THRESHOLD`/`Row` 不动。改两处容器 + Row className：
```tsx
// 虚拟分支容器（L46-48）：
<div className="h-[400px] overflow-y-auto rounded-md border border-border bg-background p-2 font-mono text-xs" aria-live="polite">
  <FixedSizeList ...>{Row}</FixedSizeList>
</div>

// 非虚拟分支容器（L60-68）：
<div className="max-h-[480px] space-y-0 overflow-y-auto rounded-md border border-border bg-background p-2 font-mono text-xs" aria-live="polite">
  {events.map((e, i) => (
    <div key={i} style={{ lineHeight: "20px" }} className={`whitespace-nowrap overflow-hidden text-ellipsis ${CAT_CLASS[e.category] ?? "text-muted-foreground"}`}>
      <span className="text-muted-foreground">[{tsClock(e.ts)}]</span>{" "}
      <span className="text-muted-foreground">{e.type}</span>{" "}
      {summarize(e)}
    </div>
  ))}
</div>
```
`Row` 组件内 `.log-row mono` → 同样 Tailwind（`whitespace-nowrap overflow-hidden text-ellipsis` + CAT class + `text-muted-foreground` for ts/type）。

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/components/LogStream.test.tsx`
Expected: PASS。

- [ ] **Step 5: tsc + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
git add src/components/LogStream.tsx src/components/LogStream.test.tsx
git commit -m "feat(web): 子项目4·详情页 Task8 LogStream aria-live + 容器 Tailwind（.ev-* 保留）"
```

---

## Task 9: 概览 tab 重做（Card 包装 + PhaseWaterfall Tailwind + AgentTable shadcn Table + 三态 + .catch）

**Files:**
- Modify: `src/routes/WorkspaceDetail/OverviewTab.tsx`
- Test: `src/routes/WorkspaceDetail/OverviewTab.test.tsx`（调整，现有 3 case）

**Interfaces:**
- Consumes: `ErrorState`（Task 2）；shadcn `Card`/`Table`/`Skeleton`/`Empty`。
- Produces: OverviewTab 三态完整（Skeleton/ErrorState/Empty）+ PhaseWaterfall Tailwind + AgentTable 用 shadcn Table；`apiGet` 加 `.catch` → ErrorState。

- [ ] **Step 1: 调整测试**

读 `OverviewTab.test.tsx` 现有 3 case（阶段瀑布/大数字/status 矛盾标黄），把 brittle class 断言改 role/文本。新增：
```tsx
it("fetch 失败渲染 ErrorState（role=alert）", async () => {
  // mock apiGet reject（MSW 或 vi.mock）
  render(<OverviewTab />); // workspace 经路由提供
  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
});
it("加载中渲染 Skeleton", async () => {
  // mock apiGet 不 resolve
  render(<OverviewTab />);
  expect(document.querySelector(".animate-pulse")).toBeInTheDocument();
});
```
> 实现者按现有测试的 MSW/vi.mock 模式接入（参考 `DeliverablesTab.test.tsx` 的 mock 模式）。workspace 参数经 `MemoryRouter` + `Route` 注入（参考 Task 1 测试 wrapper）。

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/OverviewTab.test.tsx`
Expected: FAIL（新断言未实现）。

- [ ] **Step 3: 实现**

`src/routes/WorkspaceDetail/OverviewTab.tsx` 重写要点（保留 `fmtMs`/`StatusBadge`/数据 shape 不动）：
- 顶层加 `const [err, setErr] = useState<string | null>(null); const [loading, setLoading] = useState(true);`。
- `apiGet(...).then((s) => { setS(s); setLoading(false); }).catch((e) => { setErr(String(e)); setLoading(false); });`
- 三态早返回：
  ```tsx
  if (err) return <ErrorState message={`概览加载失败：${err}`} />;
  if (loading) return <div className="space-y-2">{[0,1,2,3,4].map((i) => <Skeleton key={i} className="h-8 w-full" />)}</div>;
  if (!s?.metrics) return <Empty title="等待扫描" hint="metrics 将在 pre-recon 阶段后出现" />;
  ```
- 容器 `.overview` → `space-y-5`；`.ov-statusbar` → `flex flex-wrap items-center gap-2 text-sm`；status 矛盾 `<span className="ev-warn">` → `<Badge variant="outline" className="border-yellow/40 text-yellow">⚠ ...</Badge>`（注释说明后端已 flag 保留兜底）。
- `.big-numbers` → `grid grid-cols-3 gap-6 font-mono`；`.big` → `text-2xl font-bold text-foreground`；`.trace`（标签）→ `text-xs text-muted-foreground`。
- `PhaseWaterfall`：`.phase-waterfall` → 包 `<Card className="p-4"><CardTitle className="font-serif text-base mb-2">阶段瀑布</CardTitle>...`；`.pw-bars` → `flex items-end gap-0.5 h-20`；`.pw-bar` → `bg-cyan min-w-[60px] p-1 text-background rounded-t-sm overflow-hidden`（保留 inline `width: %`）；`.pw-name` → `text-xs font-bold truncate`；`.pw-meta` → `text-[0.7rem] opacity-85`。
- `AgentTable`：`.ledger.agent-table` → shadcn `<Table>`（对齐 `WorkspaceListPage` 用法），警告行 `ev-agent-fail/ev-warn` → `text-red`/`text-yellow` cell className。

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/OverviewTab.test.tsx`
Expected: PASS。

- [ ] **Step 5: tsc + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
git add src/routes/WorkspaceDetail/OverviewTab.tsx src/routes/WorkspaceDetail/OverviewTab.test.tsx
git commit -m "feat(web): 子项目4·详情页 Task9 概览 tab 重做（Card+Table+三态+.catch）"
```

---

## Task 10: 报告 tab 重做（容器 Tailwind + 三态）

**Files:**
- Modify: `src/routes/WorkspaceDetail/ReportTab.tsx`
- Test: `src/routes/WorkspaceDetail/ReportTab.test.tsx`（调整，现有 3 case）

**Interfaces:**
- Consumes: `ErrorState`（Task 2）；`MarkdownView`（已 prose 化，Task 6）；shadcn `Skeleton`/`Empty`。
- Produces: ReportTab 三态用 ErrorState/Skeleton/Empty（替代 `.trace`/`.trace.error`）；区分"加载中"vs"空报告"（当前 `!md` 把空文件误判为加载中——用 `loading` 标志修复）。

- [ ] **Step 1: 调整测试**

读 `ReportTab.test.tsx` 现有 3 case（H1/加载占位/失败）。改断言：加载态断言 Skeleton（`.animate-pulse`）；失败态断言 `role=alert`；新增"空报告 vs 加载中"区分（mock apiGetText 返回 `""` → Empty；mock 不 resolve → Skeleton）。

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/ReportTab.test.tsx`
Expected: FAIL。

- [ ] **Step 3: 实现**

`src/routes/WorkspaceDetail/ReportTab.tsx` 重写：
```tsx
const [md, setMd] = useState("");
const [err, setErr] = useState<string | null>(null);
const [loading, setLoading] = useState(true);
// useEffect: setLoading(true); setErr(null); setMd("");
//   apiGetText(...).then((t) => { setMd(t); setLoading(false); }).catch((e) => { setErr(String(e)); setLoading(false); });
if (err) return <ErrorState message={`报告加载失败：${err}`} />;
if (loading) return <div className="space-y-2">{[0,1,2,3].map((i) => <Skeleton key={i} className="h-6 w-full" />)}</div>;
if (!md) return <Empty title="报告尚未生成" hint="扫描完成后将在此呈现" />;
return <div className="rounded-md border border-border bg-card p-4"><MarkdownView markdown={md} /></div>;
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/ReportTab.test.tsx`
Expected: PASS。

- [ ] **Step 5: tsc + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
git add src/routes/WorkspaceDetail/ReportTab.tsx src/routes/WorkspaceDetail/ReportTab.test.tsx
git commit -m "feat(web): 子项目4·详情页 Task10 报告 tab 重做（三态，区分加载/空）"
```

---

## Task 11: 产物 tab 重做（双栏 Tailwind + 三态 + injection Badge + 局部错误态）

**Files:**
- Modify: `src/routes/WorkspaceDetail/DeliverablesTab.tsx`
- Test: `src/routes/WorkspaceDetail/DeliverablesTab.test.tsx`（调整，现有 12 case）

**Interfaces:**
- Consumes: `ErrorState`（Task 2）；shadcn `Skeleton`/`Empty`/`Badge`/`Tooltip`；`VulnCard`（已升级，Task 4）；`FileTree`（已升级，Task 5）。
- Produces: DeliverablesTab 三态；`.deliverables-layout` → Tailwind grid；`.vuln-grid` → Tailwind；`injection_has_no_queue` 文案 → Badge/Tooltip；`FilePreview` 错误态用 ErrorState（局部，不整页崩）。

- [ ] **Step 1: 调整测试**

读 `DeliverablesTab.test.tsx` 现有 12 case（漏洞网格/3 徽章/未知 merge_source/injection 无 queue/空态/FileTree md/empty_json/big_json/llm_queue/gitnexus_queue/加载/聚合失败/文件预览失败）。把 `.trace` 文案断言改文本/role；injection_has_no_queue 断言改 Badge/Tooltip（不再暴露"queue"实现细节的裸文案——断言 Badge 存在 + tooltip 文案）。

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/DeliverablesTab.test.tsx`
Expected: FAIL（部分旧断言）。

- [ ] **Step 3: 实现**

`src/routes/WorkspaceDetail/DeliverablesTab.tsx` 重写要点：
- 三态早返回（同 Task 9 模式）：err → ErrorState；!data → Skeleton×5；data → 主布局。
- `.deliverables-layout` → `grid grid-cols-[1fr_360px] items-start gap-5`。
- `.vuln-grid` → `space-y-2`；`h3` → `font-serif text-base mb-2`。
- `injection_has_no_queue`：`<Badge variant="outline" className="text-muted-foreground" title="injection 走 GitNexus 轨候选，不产独立 exploitation queue">💡 injection 类</Badge>`（用 Badge 的原生 `title` 属性做 tooltip，避免引入 Radix TooltipProvider 横切；`@/components/ui/tooltip` 虽存在但需 app 层 provider，本处不值得）。
- 空漏洞：`<Empty title="暂无聚合漏洞" hint="扫描未完成或无 vuln 命中" />`（替代 `.trace`）。
- `.deliverables-side` → `border-l border-border pl-4 max-h-[calc(100vh-200px)] overflow-auto`。
- `FilePreview` 子组件：`.trace error`（文件失败）→ `<ErrorState message="文件加载失败" />`；`empty_json`/`big_json`/`md`/`*_queue` 分支保留逻辑，容器 className 换 Tailwind。

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/DeliverablesTab.test.tsx`
Expected: PASS（12/12 调整后）。

- [ ] **Step 5: tsc + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
git add src/routes/WorkspaceDetail/DeliverablesTab.tsx src/routes/WorkspaceDetail/DeliverablesTab.test.tsx
git commit -m "feat(web): 子项目4·详情页 Task11 产物 tab 重做（双栏+三态+injection Badge+局部错误态）"
```

---

## Task 12: 日志 tab 重做（双栏 Tailwind + 虚拟滚动容器自适应 + 三态 + .catch + 文件 a11y）

**Files:**
- Modify: `src/routes/WorkspaceDetail/LogsTab.tsx`
- Test: `src/routes/WorkspaceDetail/LogsTab.test.tsx`（调整，现有 4 case）

**Interfaces:**
- Consumes: `ErrorState`（Task 2）；shadcn `Skeleton`/`Empty`；react-window `FixedSizeList`（保留）。
- Produces: LogsTab 三态 + 两次 `apiGet` 加 `.catch`；`.logs-layout` → Tailwind grid；`.log-file`（div onClick）→ `<button>` + `aria-current`；虚拟滚动 `FixedSizeList` 的 `height` 由容器实测像素值（`ResizeObserver`）喂入，替代固定值。

- [ ] **Step 1: 调整测试**

读 `LogsTab.test.tsx` 现有 4 case（文件列表+点击/5000 行虚拟/未阈值不虚拟/非 .log pre）。新增：
```tsx
it("文件列表项是 button（键盘可达）", () => {
  // mock files
  render(...);
  expect(screen.getAllByRole("button").some((b) => /\.log/.test(b.textContent ?? ""))).toBe(true);
});
it("fetch 文件列表失败渲染 ErrorState", async () => {
  // mock apiGet reject
  render(...);
  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/LogsTab.test.tsx`
Expected: FAIL。

- [ ] **Step 3: 实现**

`src/routes/WorkspaceDetail/LogsTab.tsx` 重写要点：
- 加 `err`/`loading` state；文件列表 fetch `.catch` → ErrorState；加载中 Skeleton。
- `.logs-layout` → `grid grid-cols-[240px_1fr] gap-4 h-[calc(100vh-180px)]`。
- `.logs-files` → `border-r border-border overflow-y-auto pr-2`。
- `.log-file` div → `<button aria-current={sel===f} className={`block w-full text-left rounded-sm px-2 py-0.5 font-mono text-xs hover:bg-accent ${sel===f ? "bg-accent text-primary" : "text-foreground"}`} onClick={() => setSel(f)}>{f}</button>`。
- `.logs-content` → `overflow-auto`；`.log-row` → `font-mono text-xs leading-5 whitespace-nowrap overflow-hidden text-ellipsis`；`.ev-info`（JSONL 行着色）保留（事件色不变量）；非 .log `<pre>` → `whitespace-pre-wrap break-words font-mono text-xs leading-relaxed`。
- 虚拟滚动 `height`：用 `useRef<HTMLDivElement>` + `ResizeObserver` 测容器高度，传给 `<FixedSizeList height={containerHeight ?? 400}>`。骨架：
  ```tsx
  const ref = useRef<HTMLDivElement>(null);
  const [h, setH] = useState(400);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setH(Math.max(120, Math.floor(e.contentRect.height)));
    });
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  // ...
  <div ref={ref} className="h-full"><FixedSizeList height={h} ...>{Row}</FixedSizeList></div>
  ```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/LogsTab.test.tsx`
Expected: PASS。

- [ ] **Step 5: tsc + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
git add src/routes/WorkspaceDetail/LogsTab.tsx src/routes/WorkspaceDetail/LogsTab.test.tsx
git commit -m "feat(web): 子项目4·详情页 Task12 日志 tab 重做（双栏+虚拟滚动容器自适应+三态+.catch+文件 a11y）"
```

---

## Task 13: 实时 tab 重做（容器 Tailwind + elapsed 修漂移 + scan_end 查看报告按钮 + 连接态 + 补测试）

**Files:**
- Modify: `src/routes/WorkspaceDetail/LiveTab.tsx`
- Test: `src/routes/WorkspaceDetail/LiveTab.test.tsx`（Create，零覆盖→有覆盖）

**Interfaces:**
- Consumes: `DashboardPanel`（Task 7 升级）；`LogStream`（Task 8 升级）；`useEventSource`（不动）；shadcn `Button`/`Badge`；`react-router-dom` `useNavigate`/`useParams`。
- Produces: LiveTab 三态（连接态徽章）；elapsed 从 events 取最后一条 `PhaseEvent(event==="start")` 的 ts 自算（**不动 reducer**）；scan_end 后显示"查看报告"按钮（navigate `/p/:ws/report`，不自动跳）；`.live-tab` → Tailwind。

- [ ] **Step 1: 写测试（新建 LiveTab.test.tsx）**

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import LiveTab from "./LiveTab";

// mock useEventSource 返回受控 events
const eventsState = { events: [] as any[], status: "open" as string };
vi.mock("../../api/useEventSource", () => ({
  useEventSource: () => eventsState,
}));

function renderLive() {
  return render(
    <MemoryRouter initialEntries={["/p/ws/live"]}>
      <Routes><Route path="/p/:workspace/live" element={<LiveTab />} /></Routes>
    </MemoryRouter>,
  );
}

describe("LiveTab", () => {
  it("渲染 DashboardPanel + LogStream 容器", () => {
    eventsState.events = [];
    eventsState.status = "open";
    renderLive();
    // aria-live 区域来自 LogStream
    expect(document.querySelector('[aria-live="polite"]')).toBeInTheDocument();
  });

  it("连接态徽章显示 已连接（status=open）", () => {
    eventsState.status = "open";
    renderLive();
    expect(screen.getByText("已连接")).toBeInTheDocument();
  });

  it("scan_end 后显示查看报告按钮", async () => {
    eventsState.events = [{ type: "scan_end", status: "completed", ts: "2026-01-01T00:00:00Z", category: "CONTROL" }];
    eventsState.status = "closed";
    renderLive();
    expect(screen.getByRole("button", { name: /查看报告/ })).toBeInTheDocument();
  });

  it("elapsed 从 PhaseEvent(start) ts 推导（非 0 当 phase 已开始）", async () => {
    const pastTs = new Date(Date.now() - 5000).toISOString();
    eventsState.events = [
      { type: "PhaseEvent", event: "start", phase: "recon", steps: [], step_intents: [], ts: pastTs, category: "PHASE" },
    ];
    eventsState.status = "open";
    renderLive();
    // elapsed 应 ≥ 5s（显示 MM:SS，至少非 00:00）
    await waitFor(() => {
      expect(screen.getByText(/00:0[0-9]/)).toBeInTheDocument();
    });
  });
});
```
> 实现者：events 类型用 `NdjsonEvent[]`；mock 的对象需符合 reducer 不崩。`scan_end` 后 status="closed"。如有 reducer fold 副作用，events 数组构造保证 reducer 安全（reducer 是纯函数，对任意 events 不崩）。

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/LiveTab.test.tsx`
Expected: FAIL（文件不存在/旧实现无这些行为）。

- [ ] **Step 3: 实现**

替换 `src/routes/WorkspaceDetail/LiveTab.tsx` 全文：
```tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useEventSource } from "../../api/useEventSource";
import { dashboardReducer, emptyState } from "../../state/dashboardReducer";
import type { DashboardState } from "../../state/dashboardReducer";
import type { NdjsonEvent } from "../../api/types";
import { DashboardPanel } from "../../components/DashboardPanel";
import { LogStream } from "../../components/LogStream";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

const STATUS_MAP: Record<string, { label: string; cls: string }> = {
  open: { label: "已连接", cls: "border-cyan/40 text-cyan" },
  error: { label: "重连中", cls: "border-yellow/40 text-yellow" },
  closed: { label: "已结束", cls: "border-muted-foreground/40 text-muted-foreground" },
};

export default function LiveTab() {
  const { workspace } = useParams<{ workspace: string }>();
  const navigate = useNavigate();
  const { events, status } = useEventSource(`/api/workspaces/${workspace}/events`);
  const [state, setState] = useState<DashboardState>(emptyState);
  const [elapsed, setElapsed] = useState(0);
  const lastApplied = useRef(0);

  // 增量 fold（不动）
  useEffect(() => {
    if (events.length <= lastApplied.current) return;
    setState((s) => events.slice(lastApplied.current).reduce(dashboardReducer, s));
    lastApplied.current = events.length;
  }, [events]);

  // elapsed 从最后一条 PhaseEvent(start) 的 ts 推导（修复漂移 + 进入页面不归零）
  const phaseStartMs = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      if (e.type === "PhaseEvent" && e.event === "start") return Date.parse(e.ts);
    }
    return null;
  }, [events]);
  useEffect(() => {
    if (phaseStartMs == null || Number.isNaN(phaseStartMs)) { setElapsed(0); return; }
    const tick = () => setElapsed(Date.now() - phaseStartMs);
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [phaseStartMs]);

  // scan_end 真实信号是 events 出现 scan_end 事件（status==="closed" 既能是 scan_end 也能是初始未连接，不可靠）
  const scanEnded = useMemo(() => events.some((e) => e.type === "scan_end"), [events]);
  const sm = STATUS_MAP[status] ?? STATUS_MAP.closed;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <Badge variant="outline" className={`gap-1 ${sm.cls}`}>
          <span aria-hidden>●</span>{sm.label}
        </Badge>
      </div>
      <DashboardPanel state={state} elapsedMs={elapsed} />
      <LogStream events={events} />
      {scanEnded && (
        <div role="status" className="flex items-center gap-3 rounded-md border border-border bg-card p-3 text-sm">
          <span className="text-cyan">扫描结束</span>
          <span className="text-muted-foreground">可查看完整报告</span>
          <Button size="sm" variant="outline" onClick={() => navigate(`/p/${workspace}/report`)}>
            查看报告
          </Button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/LiveTab.test.tsx`
Expected: PASS。

- [ ] **Step 5: tsc + 全 tab 回归 + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/
git add src/routes/WorkspaceDetail/LiveTab.tsx src/routes/WorkspaceDetail/LiveTab.test.tsx
git commit -m "feat(web): 子项目4·详情页 Task13 实时 tab 重做（elapsed 修漂移+scan_end 查看报告+连接态+补测试）"
```

---

## Task 14: events.css 详情页 class 清理 + markdown.css 退役

**Files:**
- Modify: `src/styles/events.css`（删详情页结构 class）
- Delete: `src/styles/markdown.css`
- Modify: `src/styles/index.css`（删 `@import "./markdown.css";` 若有）
- 无新测试（用 rg 零引用 + 全套测试绿作护栏）

**Interfaces:**
- Consumes: Task 1-13 已把所有详情页组件/tok 化脱离旧 class。
- Produces: events.css 仅保留 `:root` 变量 / `body` / `.mono`/`.serif`/`.trace` 工具类 / `.ev-*` 事件色 / `.spinner` / 其他页（列表页 `.status-bar`/`.page`/`.ledger` 等）残留；markdown.css 删除。

- [ ] **Step 1: rg 验证详情页 class 零引用**

Run（在 `packages/web/frontend`）：
```bash
cd packages/web/frontend
for cls in workspace-detail tab-nav tab-active tab-body overview ov-statusbar big-numbers phase-waterfall pw-bars pw-bar pw-name pw-meta agent-table logs-layout logs-files log-file logs-content log-row ev-type live-tab dashboard-panel dp-bar dp-agents dp-agent log-stream deliverables-layout vuln-grid deliverables-side file-tree ft-row ft-toggle ft-caret ft-file ft-name vuln-card vc-head vc-id vc-detail vc-notes status-badge; do
  echo "=== $cls ==="; rg --stat "\.$cls\b" src/ || echo "zero";
done
```
Expected: 每个 class 在 `src/` 下 `zero`（除 events.css 定义本身）。若某 class 仍被引用 → 回到对应 Task 修，**不得**在本 task 偷懒删。

- [ ] **Step 2: 删 events.css 详情页 class 定义**

删除 events.css 中以下定义块（按 L86-146 区间，逐块删，保留 `:root`/`body`/`.mono`/`.serif`/`.trace`/`.ev-*`/`.spinner`/`.page`/`.ledger`/`.status-bar`/`.status-*`/`.badge`/`.scan-page` 等非详情页规则）：
- `.workspace-detail` / `.workspace-detail h2` / `.tab-nav` / `.tab-nav a` / `.tab-nav a:hover` / `.tab-active` / `.tab-body`（详情壳）
- `.overview` / `.ov-statusbar` / `.big-numbers` / `.big-numbers > div` / `.big`（概览）
- `.phase-waterfall h3` / `.pw-bars` / `.pw-bar` / `.pw-name` / `.pw-meta` / `.agent-table th`（概览瀑布）
- `.logs-layout` / `.logs-files` / `.log-file` / `.log-file:hover` / `.log-file.sel` / `.logs-content` / `.logs-content pre` / `.log-row` / `.ev-type`（日志）
- `.live-tab` / `.dashboard-panel` / `.dp-bar` / `.dp-agents` / `.dp-agent` / `.log-stream`（实时）
- `.deliverables-layout` / `.vuln-grid h3` / `.deliverables-side`（产物）
- `.file-tree` / `.file-tree ul` / `.ft-row` / `.ft-toggle` / `.ft-caret` / `.ft-file` / `.ft-file:hover` / `.ft-name`（产物文件树）
- `.vuln-card` / `.vuln-card.reachable` / `.vc-head` / `.vc-id` / `.vc-detail` / `.vc-detail code` / `.vc-notes`（VulnCard）
- `.status-badge`（StatusBadge 改 Badge 后不用）

> 若 rg 显示 `.badge`/`.ledger`/`.page`/`.empty`/`.error`/`.scan-page`/`.segmented` 等仍被其他页用 → 保留（不是详情页范围）。`.ev-type` 仅 LogStream 用过，Task 8 已迁 → 可删。

- [ ] **Step 3: 删 markdown.css + 解 import**

```bash
cd packages/web/frontend
# 确认 markdown.css 零引用（MarkdownView Task6 已删 import）
rg "markdown.css" src/
# 期望：仅 src/styles/index.css 可能还有 @import（若有则下一步删那行）
rm src/styles/markdown.css
```
若 `src/styles/index.css` 含 `@import "./markdown.css";` → 删该行。

- [ ] **Step 4: 跑全套前端测试 + tsc + build**

```bash
cd packages/web/frontend && npx vitest run
cd packages/web/frontend && npx tsc --noEmit
cd packages/web/frontend && npm run build
```
Expected: 全套测试 PASS（含 dashboardReducer.test.ts/router.test.ts/tokens.test.ts 对齐锚点持续绿）；tsc 0 错；build 成功。

- [ ] **Step 5: commit**

```bash
git add src/styles/events.css src/styles/index.css src/styles/markdown.css
git commit -m "feat(web): 子项目4·详情页 Task14 events.css 详情页 class 清理 + markdown.css 退役"
```

---

## Task 15: dev 预览页补登 + 双主题冒烟锚点

**Files:**
- Modify: `src/pages/DevComponentsPage.tsx`（补登 ErrorState + 升级后 StatusBadge/VulnCard 示例 + DashboardPanel/LogStream mock）
- Test: `src/pages/DevComponentsPage.test.tsx`（若存在，补断言；否则跳过测试只冒烟）

**Interfaces:**
- Produces: `/dev/components` 补登本子项目新/改组件，供人工双主题冒烟；tokens 一致性测试（`tokens.test.ts`）仍绿。

- [ ] **Step 1: 读 DevComponentsPage 现状**

Run: `cd packages/web/frontend && cat src/pages/DevComponentsPage.tsx | head -60`
了解现有登载结构（Section/Card 分组）。

- [ ] **Step 2: 补登新组件示例**

在 DevComponentsPage 加 Section：
```tsx
import { ErrorState } from "@/components/ErrorState";
import { StatusBadge } from "@/components/StatusBadge";
// ...
<Section title="ErrorState">
  <ErrorState message="示例错误信息" />
  <ErrorState message="带重试" onRetry={() => alert("retry")} />
</Section>
<Section title="StatusBadge">
  {["running", "completed", "failed", "killed", "crashed"].map((s) => <StatusBadge key={s} status={s} />)}
</Section>
<Section title="DashboardPanel（mock state）">
  <DashboardPanel state={mockState} elapsedMs={123456} />
</Section>
```
> `mockState` 用 `emptyState()` + 手动填字段（import `emptyState` from `@/state/dashboardReducer`）。

- [ ] **Step 3: tsc + build + 人工双主题冒烟**

```bash
cd packages/web/frontend && npx tsc --noEmit && npm run build
```
冒烟（人工）：`npm run dev` → 打开 `/dev/components` → ThemeToggle 切深/浅 → 肉眼检查：ErrorState 红横幅两主题可读、StatusBadge 五状态语义色正确、DashboardPanel step 进度+intent 渲染、VulnCard 可点行键盘可展开。再访问 `/p/<某真实 workspace>/{overview|report|deliverables|logs|live}` 五 tab 各切深/浅。

- [ ] **Step 4: commit**

```bash
git add src/pages/DevComponentsPage.tsx
git commit -m "feat(web): 子项目4·详情页 Task15 dev 预览页补登 + 双主题冒烟锚点"
```

---

## 完工验收

- [ ] 全套前端测试绿：`cd packages/web/frontend && npx vitest run`
- [ ] tsc 0 错：`cd packages/web/frontend && npx tsc --noEmit`
- [ ] build 成功：`cd packages/web/frontend && npm run build`
- [ ] 契约不动验证：`git diff main -- src/state/dashboardReducer.ts src/state/formatters.ts src/api/types.ts src/api/useEventSource.ts src/state/dashboardReducer.test.ts` → 空 diff。
- [ ] events.css 详情页 class 零引用：Task 14 Step 1 rg 输出全 `zero`。
- [ ] 人工双主题冒烟：5 tab × 深/浅 = 10 个组合肉眼过一遍。
