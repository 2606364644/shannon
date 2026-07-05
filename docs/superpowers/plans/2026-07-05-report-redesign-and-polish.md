# Shannon Web 报告页美化 + 全站轻量打磨 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 报告页深度重做（代码高亮 / GFM 表格 / 排版 / 结构自适应）+ 其他页面轻量打磨统一到 DSF token，消除浅色主题断裂。

**Architecture:** 报告页核心改 `MarkdownView.tsx`（react-markdown 组件覆写）+ 新建 `src/styles/report.css`（手写 hljs token 配色接 `--c-` 语义色）；其余页面把 `events.css` 遗留 class 替换成 DSF tailwind utilities。零新依赖（仅 `remark-gfm`）。守 DSF 双主题。

**Tech Stack:** React + TypeScript + Vite + Tailwind 3 + shadcn/new-york + react-markdown 9 + rehype-highlight + vitest + @testing-library/react + MSW。

## Global Constraints

- **所有 npm/vitest 命令在 `packages/web/frontend/` 下执行**（cwd 不跨 bash 持久，每条命令显式 `cd`）。
- 单文件测试：`cd packages/web/frontend && npx vitest run <path>`；全套：`npm test`（= `vitest run`）。
- 构建：`cd packages/web/frontend && npm run build`（`tsc -b && vite build` 须过）。
- **TDD**：每任务先写/改测试 → 跑确认失败 → 实现 → 跑确认通过 → commit。
- **颜色只用 DSF token**：`hsl(var(--c-cyan/magenta/green/red/yellow))` 或 tailwind `bg-cyan / text-red / border-green/40` 等；**禁止硬编码 hex、禁止 import highlight.js 官方主题、禁止换 shiki**。
- 不新增 shadcn 组件（现有 16 个够用）。
- 守双主题：每处样式改动在深（默认 `:root`）/浅（`.light`）下都验证。
- 不删 `events.css` 文件本身，只换用法（死规则清理 follow-up）。
- 不改后端 API、不改 `SessionData` 类型（`src/api/types.ts:102`）。
- 守冒号守卫：改 `MarkdownView` li renderer 时不得破坏现有 5 条 kv-row 计数（`MarkdownView.test.tsx:95`）。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/components/MarkdownView.tsx` | 报告 markdown 渲染（TOC / hero / KV / code-pre / prose 容器） | Modify |
| `src/components/MarkdownView.test.tsx` | 上述行为的单测 | Modify |
| `src/styles/report.css` | hljs token 配色 + 表格样式 + scroll-margin + pre 背景 | Create |
| `src/styles/index.css` | DSF 入口，聚合 @import | Modify（加一行 import） |
| `src/styles/report.test.ts` | report.css 接 token 的字符串断言 | Create |
| `src/pages/ScanNewPage.tsx` / `src/components/ScanFormFields.tsx` / `src/components/YamlEditor.tsx` | 扫描页解耦 events.css | Modify |
| `src/pages/ScanNewPage.test.tsx` | 断言无遗留 class | Modify |
| `src/pages/WorkspaceListPage.tsx` | 行首 status-bar → DSF 色条 | Modify |
| `src/pages/WorkspaceListPage.test.tsx` | 断言无 status-bar | Modify |
| `src/routes/WorkspaceDetail/index.tsx` | header 补返回 + 元信息 | Modify |
| `src/routes/WorkspaceDetail/WorkspaceDetail.test.tsx` | header 元信息 + 失败降级 | Create |
| `src/routes/WorkspaceDetail/LogsTab.tsx` / `src/components/layout/ThemeToggle.tsx` | trace/ev-info + emoji 图标 | Modify |

---

## Task 1: 加 remark-gfm 让 GFM 表格 / 删除线渲染

**Files:**
- Modify: `packages/web/frontend/package.json`（`npm i` 自动）
- Modify: `packages/web/frontend/src/components/MarkdownView.tsx`（加 import + remarkPlugins）
- Test: `packages/web/frontend/src/components/MarkdownView.test.tsx`

**Interfaces:**
- Produces: `MarkdownView` 现在能渲染 GFM 表格（`<table>`），后续 Task 3 的 `.prose table` 样式才有作用对象。

- [ ] **Step 1: 装依赖**

```bash
cd packages/web/frontend && npm i remark-gfm
```

预期：`package.json` dependencies 多一行 `"remark-gfm": "^4.x"`。

- [ ] **Step 2: 写失败测试（追加到 `MarkdownView.test.tsx` 的 describe 块内末尾）**

```tsx
  it("GFM 表格渲染成 <table>（依赖 remark-gfm）", () => {
    const md = `
| 类型 | 数量 |
|------|------|
| INJ  | 4    |
| XSS  | 2    |
`;
    const { container } = render(<MarkdownView markdown={md} />);
    expect(container.querySelector("table")).not.toBeNull();
    expect(container.querySelectorAll("th").length).toBeGreaterThanOrEqual(2);
    expect(container.querySelectorAll("td").length).toBeGreaterThanOrEqual(2);
  });
```

- [ ] **Step 3: 跑测试确认失败**

```bash
cd packages/web/frontend && npx vitest run src/components/MarkdownView.test.tsx -t "GFM 表格"
```

预期：FAIL（无 remark-gfm 时 `|---|` 表格语法不解析，`container.querySelector("table")` 为 null）。

- [ ] **Step 4: 加 remark-gfm 到 MarkdownView.tsx**

在 `src/components/MarkdownView.tsx:5`（现有 rehype import 之后）加：

```tsx
import remarkGfm from "remark-gfm";
```

把 `:128` 的 `<ReactMarkdown rehypePlugins={[...]}>` 改为同时传 remarkPlugins：

```tsx
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[
              rehypeSlug,
              [rehypeAutolinkHeadings, { behavior: "wrap" }],
              rehypeHighlight,
            ]}
            components={{ ... }}
          >
```

- [ ] **Step 5: 跑测试确认通过**

```bash
cd packages/web/frontend && npx vitest run src/components/MarkdownView.test.tsx
```

预期：PASS（含新"GFM 表格"用例 + 现有全部用例）。

- [ ] **Step 6: Commit**

```bash
git add packages/web/frontend/package.json packages/web/frontend/package-lock.json packages/web/frontend/src/components/MarkdownView.tsx packages/web/frontend/src/components/MarkdownView.test.tsx
git commit -m "feat(web): 报告页加 remark-gfm 渲染 GFM 表格"
```

---

## Task 2: MarkdownView 排版与结构修复（code/pre 重构 + TOC 空时退单栏 + 字体 + KV 对齐 + hero lucide）

**Files:**
- Modify: `packages/web/frontend/src/components/MarkdownView.tsx`
- Modify: `packages/web/frontend/src/components/MarkdownView.test.tsx`（更新现有「代码块带复制按钮」用例 + 新增 3 用例）

**Interfaces:**
- Consumes: Task 1 的 `remarkGfm`。
- Produces: `MarkdownView` 的 block code 用 `<pre data-testid="code-block">` 包装、复制按钮在 pre 上；inline code 无按钮。TOC 无 level>=2 标题时 `<nav data-testid="toc">` 不渲染、外层单栏。

**关键设计决策：** react-markdown v9 的 `code` 组件不再有 `inline` prop。区分依据：block code 外层是 `pre`、且 rehype-highlight 给 block code 加 `language-xxx` class。故**覆写 `pre`**（只包 block code）放复制按钮 + 语言角标；**覆写 `code`** 对所有 code 渲染纯 `<code>`（不加按钮，按钮在 pre）。inline code 无 pre 包装 → 自然无按钮。

- [ ] **Step 1: 更新现有「代码块带复制按钮」测试 + 新增 inline / lang / TOC 空时用例**

把 `MarkdownView.test.tsx:103-109` 的现有用例**整体替换**为：

```tsx
  it("block code（witness PoC）在 <pre> 内、带复制按钮 + 语言角标", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    const pre = container.querySelector('pre[data-testid="code-block"]');
    expect(pre).not.toBeNull();
    expect(pre?.textContent).toContain("preTax=res.send(...)");
    expect(pre?.querySelector(".copy-btn")).not.toBeNull();
  });

  it("inline code 无 pre 包装、无复制按钮", () => {
    const { container } = render(<MarkdownView markdown={"正文 `inline_x` 结尾"} />);
    const code = container.querySelector("code");
    expect(code).not.toBeNull();
    expect(code?.textContent).toBe("inline_x");
    expect(container.querySelector("pre")).toBeNull();
    expect(container.querySelector(".copy-btn")).toBeNull();
  });

  it("带语言标记的 block code 显语言角标", () => {
    const { container } = render(<MarkdownView markdown={"```bash\nexit 0\n```\n"} />);
    const lang = container.querySelector('[data-testid="code-lang"]');
    expect(lang?.textContent).toBe("bash");
  });
```

把 `:50-55` 的现有「TOC 含类型 + 执行摘要条目」用例**之后**新增（不替换原用例）：

```tsx
  it("无 level>=2 标题时不渲染 TOC、外层退单栏", () => {
    const { container } = render(<MarkdownView markdown={"# 只有一级标题\n\n正文"} />);
    expect(container.querySelector('[data-testid="toc"]')).toBeNull();
    // 外层 grid 退单栏：无双栏 class
    expect(container.querySelector(".grid.grid-cols-\\[220px_1fr\\]")).toBeNull();
  });
```

- [ ] **Step 2: 跑测试确认新用例失败 / 受影响用例如预期**

```bash
cd packages/web/frontend && npx vitest run src/components/MarkdownView.test.tsx
```

预期：FAIL（"block code" 用例找不到 `pre[data-testid="code-block"]`；"inline code" 用例因为现有 code renderer 给所有 code 加了 `.copy-btn` 故 `.copy-btn` 非空；"TOC 空时" 用例因现有固定双栏而失败）。

- [ ] **Step 3: 改 MarkdownView.tsx —— import + 字体 + KV + hero 图标**

`src/components/MarkdownView.tsx`：

第 1 行 import 加 `Children`：

```tsx
import { useMemo, useState, Children, type ReactNode, type ReactElement } from "react";
```

第 6 行后加 lucide import：

```tsx
import { Button } from "@/components/ui/button";
import { ChevronDown, ChevronRight } from "lucide-react";
```

`:88` hero 折叠按钮文案换图标（替换 `{heroCollapsed ? "展开 ▸" : "折叠 ▾"}`）：

```tsx
              {heroCollapsed ? <ChevronRight className="size-4" /> : <ChevronDown className="size-4" />}
              <span className="sr-only">{heroCollapsed ? "展开" : "折叠"}</span>
```

`:111-126` TOC 双栏改为条件单栏。把整个 `<div className="grid grid-cols-[220px_1fr] gap-6"> ... </div>` 块替换为：

```tsx
      {(() => {
        const tocItems = headings.filter((h) => h.level >= 2);
        const gridCls = tocItems.length > 0 ? "grid grid-cols-[220px_1fr] gap-6" : "grid grid-cols-1";
        return (
          <div className={gridCls}>
            {tocItems.length > 0 && (
              <nav data-testid="toc" className="sticky top-4 space-y-1 text-sm">
                {tocItems.map((h, i) => (
                  <a
                    key={`${i}-${h.id}`}
                    href={`#${h.id}`}
                    className={`block text-muted-foreground hover:text-primary ${
                      h.level === 3 ? "pl-3 text-xs" : ""
                    }`}
                  >
                    {h.text}
                  </a>
                ))}
              </nav>
            )}
            <div className="prose prose-sm max-w-none font-sans prose-headings:font-serif">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[
                  rehypeSlug,
                  [rehypeAutolinkHeadings, { behavior: "wrap" }],
                  rehypeHighlight,
                ]}
                components={{
                  // KV 行（冒号守卫逻辑保持不变，仅 className 加 items-baseline / shrink-0）
                  li: ({ children, ...props }) => {
                    const kids = Array.isArray(children) ? children : [children];
                    const firstStrongIdx = kids.findIndex(
                      (k) => typeof k !== "string" && (k as ReactElement)?.type === "strong",
                    );
                    if (firstStrongIdx !== -1) {
                      const strongEl = kids[firstStrongIdx] as ReactElement<{ children?: ReactNode }>;
                      const rawKey = flatten(strongEl.props.children);
                      if (!/[：:]\s*$/.test(rawKey)) {
                        return <li {...props}>{children}</li>;
                      }
                      const keyText = rawKey.replace(/[:：]\s*$/, "").trim();
                      if (keyText) {
                        const restKids = kids.slice(firstStrongIdx + 1);
                        const valKids: ReactNode[] = [];
                        let trimming = true;
                        for (const k of restKids) {
                          if (trimming && typeof k === "string" && /^\s*$/.test(k)) continue;
                          if (trimming && typeof k === "string") {
                            valKids.push(k.replace(/^\s+/, ""));
                            trimming = false;
                          } else {
                            valKids.push(k);
                            trimming = false;
                          }
                        }
                        return (
                          <li {...props} data-testid="kv-row" className="flex items-baseline gap-2">
                            <span className="kv-key shrink-0 font-mono text-muted-foreground">{keyText}</span>
                            <span className="kv-val">{valKids}</span>
                          </li>
                        );
                      }
                    }
                    return <li {...props}>{children}</li>;
                  },
                  // block code：仅渲染 <code>（含 hljs language-xxx class），装饰交给 pre
                  code: ({ className, children, ...props }) => (
                    <code {...props} className={`font-mono ${className ?? ""}`}>{children}</code>
                  ),
                  // pre：只包 block code → 加语言角标 + 复制按钮
                  pre: ({ children, ...props }) => {
                    const codeChild = Children.toArray(children)[0] as ReactElement<{
                      className?: string;
                      children?: ReactNode;
                    }>;
                    const cls = (codeChild?.props as { className?: string } | undefined)?.className ?? "";
                    const lang = /language-(\w+)/.exec(cls)?.[1] ?? "";
                    const text = flatten(codeChild?.props?.children);
                    return (
                      <pre {...props} data-testid="code-block" className="relative">
                        {lang && (
                          <span
                            data-testid="code-lang"
                            className="absolute right-2 top-1 font-mono text-xs text-muted-foreground"
                          >
                            {lang}
                          </span>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          data-testid="copy-btn"
                          className="copy-btn absolute right-2 bottom-1 text-xs opacity-60 hover:opacity-100"
                          onClick={(e) => {
                            navigator.clipboard?.writeText(text);
                            e.currentTarget.textContent = "✓";
                          }}
                        >
                          复制
                        </Button>
                        {children}
                      </pre>
                    );
                  },
                }}
              >
                {markdown}
              </ReactMarkdown>
            </div>
          </div>
        );
      })()}
```

> 注：KV 测试 `MarkdownView.test.tsx:79` 用 `li[data-testid="kv-row"]` 选择器（已核对：现实现 `MarkdownView.tsx:172` 与测试均为连字符 `kv-row`）。本实现保持 `data-testid="kv-row"` 不变。

- [ ] **Step 4: 跑测试确认通过**

```bash
cd packages/web/frontend && npx vitest run src/components/MarkdownView.test.tsx
```

预期：PASS（含更新的 block code / 新 inline / 新 lang / 新 TOC 空时 + 现有 KV 计数=5 / hero / 锚链接全过）。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/components/MarkdownView.tsx packages/web/frontend/src/components/MarkdownView.test.tsx
git commit -m "style(web): 报告页排版重做——code/pre 分离、TOC 空时退单栏、正文 sans 标题 serif、hero 换 lucide 图标"
```

---

## Task 3: report.css 高亮配色 + 表格样式 + scroll-margin

**Files:**
- Create: `packages/web/frontend/src/styles/report.css`
- Modify: `packages/web/frontend/src/styles/index.css`（加 `@import`）
- Create: `packages/web/frontend/src/styles/report.test.ts`

**Interfaces:**
- Produces: 全局 `.hljs-*` token 配色（深/浅各一套，接 `--c-*`）；`.prose table` 样式；`.prose :is(h1,h2,h3) scroll-margin-top`；`.prose pre` 背景。

- [ ] **Step 1: 写失败测试 `src/styles/report.test.ts`**

```ts
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const css = readFileSync(resolve(__dirname, "report.css"), "utf8");

describe("report.css", () => {
  it("hljs token 配色接 DSF --c- 语义色（非硬编码 hex）", () => {
    expect(css).toContain(".hljs-keyword");
    expect(css).toContain(".hljs-string");
    expect(css).toContain(".hljs-comment");
    expect(css).toContain(".hljs-number");
    // 至少一处引用 --c- 语义色 token
    expect(css).toMatch(/var\(--c-(cyan|magenta|green|red|yellow)\)/);
  });

  it("浅色主题覆盖（.light 下有 hljs 规则）", () => {
    expect(css).toMatch(/\.light\s+\.hljs/);
  });

  it("表格 + scroll-margin + pre 样式存在", () => {
    expect(css).toContain(".prose table");
    expect(css).toContain("scroll-margin-top");
    expect(css).toContain(".prose pre");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd packages/web/frontend && npx vitest run src/styles/report.test.ts
```

预期：FAIL（`report.css` 不存在，readFileSync 抛 ENOENT）。

- [ ] **Step 3: 创建 `src/styles/report.css`**

```css
/* 报告页专属：hljs token 配色（接 DSF --c- 语义色）+ 表格 + scroll-margin + pre 背景。
   深色（:root 默认）为主，.light 覆盖一套调亮度保 AA 对比。 */

/* —— hljs token 配色（深色默认） —— */
.hljs-keyword,
.hljs-built_in,
.hljs-literal { color: hsl(var(--c-cyan)); }
.hljs-string,
.hljs-regexp  { color: hsl(var(--c-green)); }
.hljs-number  { color: hsl(var(--c-magenta)); }
.hljs-title,
.hljs-title.function_,
.hljs-attr    { color: hsl(var(--c-yellow)); }
.hljs-meta    { color: hsl(var(--c-red)); }
.hljs-comment { color: hsl(var(--muted-foreground)); font-style: italic; }

/* —— 表格 —— */
.prose table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
}
.prose table th,
.prose table td {
  border: 1px solid hsl(var(--border));
  padding: 0.5rem 0.75rem;
  text-align: left;
}
.prose table th {
  background: hsl(var(--muted));
  font-family: var(--font-mono);
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.prose :where(table) { display: block; overflow-x: auto; }

/* —— 锚跳转避 sticky 遮挡（TopBar h-12 + 详情页 header 元信息行） —— */
.prose :is(h1, h2, h3) { scroll-margin-top: 80px; }

/* —— block code 容器背景 —— */
.prose pre {
  border-radius: var(--radius);
  background: hsl(var(--muted));
  overflow-x: auto;
}
.prose pre code { background: transparent; padding: 0; }

/* —— 浅色覆盖（保 AA 对比，cyan/green/yellow 提升亮度） —— */
.light .hljs-keyword,
.light .hljs-built_in,
.light .hljs-literal { color: hsl(var(--c-cyan) / 0.92); }
.light .hljs-string,
.light .hljs-regexp  { color: hsl(var(--c-green) / 0.92); }
.light .hljs-title,
.light .hljs-title.function_,
.light .hljs-attr    { color: hsl(var(--c-yellow) / 0.95); }
.light .hljs-meta    { color: hsl(var(--c-red) / 0.9); }
.light .hljs-comment { color: hsl(var(--muted-foreground)); }
```

- [ ] **Step 4: 在 `src/styles/index.css` 加 import（`@import "./events.css";` 之后、`@tailwind base;` 之前）**

把 `src/styles/index.css:7-11` 改为：

```css
@import "./tokens.css";
@import "./events.css";
@import "./report.css";
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 5: 跑测试确认通过**

```bash
cd packages/web/frontend && npx vitest run src/styles/report.test.ts
```

预期：PASS。

- [ ] **Step 6: Commit**

```bash
git add packages/web/frontend/src/styles/report.css packages/web/frontend/src/styles/report.test.ts packages/web/frontend/src/styles/index.css
git commit -m "style(web): 新建 report.css——hljs token 接 DSF 语义色 + 表格 + scroll-margin"
```

---

## Task 4: 扫描页解耦 events.css（修浅色断裂）

**Files:**
- Modify: `packages/web/frontend/src/pages/ScanNewPage.tsx`
- Modify: `packages/web/frontend/src/components/ScanFormFields.tsx`
- Modify: `packages/web/frontend/src/components/YamlEditor.tsx`
- Modify: `packages/web/frontend/src/pages/ScanNewPage.test.tsx`（新增无遗留 class 断言）

**Interfaces:**
- Produces: 扫描页根容器、提交按钮、提示、git-extra、yaml-editor 全部用 DSF tailwind utilities；浅色主题下无 `--void/--panel/--cyan` hex 残留。

**替换映射（逐处精确改 className）：**

| 文件:行 | 现 className | 新 className |
|---|---|---|
| `ScanNewPage.tsx:166` | `page scan-page` | `space-y-4` |
| `ScanNewPage.tsx:220` | `submit-btn`（在 `<Button>` 上） | 删除该 class，`<Button size="lg" className="w-full">` |
| `ScanNewPage.tsx:214,223` | `trace`（错误提示） | 复用 `<ErrorState message={...} />`（已 import 则用；否则 `text-sm text-destructive`） |
| `ScanFormFields.tsx:59` | `space-y-2 git-extra` | `space-y-2 border-t border-border pt-4 mt-4` |
| `ScanFormFields.tsx:89,101` | `trace`（hint） | `text-xs text-muted-foreground` |
| `ScanFormFields.tsx:87` | `ev-warn` | `text-xs text-yellow`（外层或内层 Badge 视原结构） |
| `YamlEditor.tsx:37` | `yaml-editor` | `border border-border rounded-md overflow-hidden` |

> 实现时先 Read 每个文件对应行确认上下文（class 是否组合了其它必要 class，避免误删），再做精确 `Edit`。`.trace` 在错误提示 vs hint 场景的区分见 spec T3。

- [ ] **Step 1: 写失败测试（追加到 `ScanNewPage.test.tsx` describe 块内）**

```tsx
  it("扫描页无 events.css 遗留 class（浅色主题不断裂）", () => {
    const { container } = renderPage();
    expect(container.querySelector(".page.scan-page")).toBeNull();
    expect(container.querySelector(".submit-btn")).toBeNull();
    expect(container.querySelector(".trace")).toBeNull();
    expect(container.querySelector(".git-extra")).toBeNull();
    expect(container.querySelector(".yaml-editor")).toBeNull();
    expect(container.querySelector(".ev-warn")).toBeNull();
  });
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd packages/web/frontend && npx vitest run src/pages/ScanNewPage.test.tsx -t "events.css 遗留"
```

预期：FAIL（现有 className 含 `.page.scan-page` 等）。

- [ ] **Step 3: 按上面映射表逐处 Edit 三个文件**

先 Read 每个文件确认精确上下文，再用 Edit 精确替换 className（不要 replace_all，逐处做）。

- [ ] **Step 4: 跑全套 ScanNewPage 测试确认通过（不破坏现有用例）**

```bash
cd packages/web/frontend && npx vitest run src/pages/ScanNewPage.test.tsx
```

预期：PASS（含新"无遗留 class" + 现有全部用例。若现有用例失败，多半是某处 className 误删了结构 class——回 Step 3 核对）。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/pages/ScanNewPage.tsx packages/web/frontend/src/components/ScanFormFields.tsx packages/web/frontend/src/components/YamlEditor.tsx packages/web/frontend/src/pages/ScanNewPage.test.tsx
git commit -m "refactor(web): 扫描页解耦 events.css，统一 DSF token（修浅色主题断裂）"
```

---

## Task 5: 列表页 status-bar 解耦

**Files:**
- Modify: `packages/web/frontend/src/pages/WorkspaceListPage.tsx`（`:63` + 顶部加 STATUS_COLOR 常量）
- Modify: `packages/web/frontend/src/pages/WorkspaceListPage.test.tsx`

**Interfaces:**
- Produces: 列表行首 `<span className="status-bar status-*">` 换成 DSF `bg-cyan/green/red/yellow` 色条，复用 `StatusBadge` 色映射。

- [ ] **Step 1: 写失败测试（追加到 `WorkspaceListPage.test.tsx`）**

```tsx
  it("行首无 status-bar 遗留 class、running 行有 bg-cyan 色条", async () => {
    // 复用现有 MSW server 返一条 running workspace（参考该文件顶部 fixtures）
    const { container } = renderList();  // 用该文件现有 render 辅助；若无则 render(<MemoryRouter><WorkspaceListPage/></MemoryRouter>)
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    expect(container.querySelector(".status-bar")).toBeNull();
    expect(container.querySelector('[class*="bg-cyan"]')).not.toBeNull();
  });
```

> 实现时先 Read `WorkspaceListPage.test.tsx` 顶部确认现有 fixtures（workspace 名、render 辅助函数名），把 `ws-a` / `renderList()` 替换为该文件真实值。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd packages/web/frontend && npx vitest run src/pages/WorkspaceListPage.test.tsx -t "status-bar"
```

预期：FAIL（现有 `.status-bar` 仍在）。

- [ ] **Step 3: 改 `WorkspaceListPage.tsx`**

文件顶部（import 之后、组件之前）加 STATUS_COLOR 常量（色映射与 `StatusBadge.tsx:3-10` 对齐）：

```tsx
const STATUS_COLOR: Record<string, string> = {
  running: "bg-cyan",
  completed: "bg-green",
  done: "bg-green",
  failed: "bg-red",
  killed: "bg-red",
  crashed: "bg-yellow",
};
const statusColor = (s: string) => STATUS_COLOR[s] ?? "bg-yellow";
```

`:62-67` 的 name cell（行首 status-bar span 那行）改为：

```tsx
          <span className={`inline-block w-0.5 self-stretch rounded ${statusColor(info.row.original.status)}`} />
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd packages/web/frontend && npx vitest run src/pages/WorkspaceListPage.test.tsx
```

预期：PASS（新用例 + 现有全部）。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/pages/WorkspaceListPage.tsx packages/web/frontend/src/pages/WorkspaceListPage.test.tsx
git commit -m "refactor(web): 列表页 status-bar 解耦 events.css，换 DSF 行首色条"
```

---

## Task 6: 详情页 header 补元信息

**Files:**
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/index.tsx`
- Create: `packages/web/frontend/src/routes/WorkspaceDetail/WorkspaceDetail.test.tsx`

**Interfaces:**
- Consumes: `apiGet<SessionData>`（`src/api/client.ts`），`SessionData`（`src/api/types.ts:102`），`StatusBadge`，shadcn `Badge`/`Skeleton`，`lucide-react` `ArrowLeft`。
- Produces: 详情页 header 显返回链接 + workspace 名 + StatusBadge + scan_type + repo_path；fetch 失败不阻塞 tab 切换。

- [ ] **Step 1: 写失败测试 `WorkspaceDetail.test.tsx`**

```tsx
import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import WorkspaceDetail from "./index";

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/p/:workspace" element={<WorkspaceDetail />}>
          <Route path="report" element={<div>report-tab</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("WorkspaceDetail header", () => {
  it("显返回链接 + workspace 名 + 元信息（status/scan_type/repo_path）", async () => {
    server.use(
      http.get("/api/workspaces/ws1", () =>
        HttpResponse.json({ status: "completed", scan_type: "whitebox", repo_path: "/root/nodegoat" }),
      ),
    );
    renderAt("/p/ws1/report");
    expect(screen.getByText("返回列表")).toBeInTheDocument();
    expect(screen.getByText("ws1")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("whitebox")).toBeInTheDocument());
    expect(screen.getByText("/root/nodegoat")).toBeInTheDocument();
    // StatusBadge 兜底显 completed
    expect(screen.getByText("completed")).toBeInTheDocument();
  });

  it("fetch 失败不阻塞 tab（降级显 workspace 名 + 默认 running）", async () => {
    server.use(http.get("/api/workspaces/ws1", () => new HttpResponse(null, { status: 500 })));
    renderAt("/p/ws1/report");
    await waitFor(() => expect(screen.getByText("ws1")).toBeInTheDocument());
    expect(screen.getByText("返回列表")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "报告" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/WorkspaceDetail.test.tsx
```

预期：FAIL（找不到"返回列表"，header 没有元信息）。

- [ ] **Step 3: 改 `WorkspaceDetail/index.tsx` 整体替换为**

```tsx
import { useEffect, useState } from "react";
import { Outlet, useParams, useLocation, useNavigate, Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/StatusBadge";
import { apiGet } from "@/api/client";
import type { SessionData } from "@/api/types";

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
  const [meta, setMeta] = useState<SessionData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!workspace) return;
    setLoading(true);
    apiGet<SessionData>(`/workspaces/${workspace}`)
      .then((s) => { setMeta(s); setLoading(false); })
      .catch(() => { setMeta(null); setLoading(false); });
  }, [workspace]);

  const status = meta?.status ?? meta?.session?.status ?? "running";

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Link
          to="/workspaces"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-primary"
        >
          <ArrowLeft className="size-3.5" /> 返回列表
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-mono text-xl">{workspace}</h2>
          {loading ? (
            <Skeleton className="h-5 w-40" />
          ) : (
            <>
              <StatusBadge status={status} />
              {meta?.scan_type && (
                <Badge variant="outline" className="font-mono">{meta.scan_type}</Badge>
              )}
              {meta?.repo_path && (
                <span className="font-mono text-sm text-muted-foreground">{meta.repo_path}</span>
              )}
            </>
          )}
        </div>
      </div>
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

- [ ] **Step 4: 跑测试确认通过**

```bash
cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/WorkspaceDetail.test.tsx
```

预期：PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/routes/WorkspaceDetail/index.tsx packages/web/frontend/src/routes/WorkspaceDetail/WorkspaceDetail.test.tsx
git commit -m "feat(web): 详情页 header 补返回按钮 + 状态/类型/repo 元信息"
```

---

## Task 7: LogsTab + ThemeToggle 收尾

**Files:**
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/LogsTab.tsx`
- Modify: `packages/web/frontend/src/components/layout/ThemeToggle.tsx`
- Modify: `packages/web/frontend/src/components/layout/ThemeToggle.test.tsx`（若存在；否则新建）
- Modify: `src/components/DashboardPanel.tsx`（仅当 grep 确认仍被引用）

**Interfaces:**
- Produces: ThemeToggle 用 lucide Sun/Moon；LogsTab 无 `.trace/.ev-info` 遗留。

- [ ] **Step 1: 写失败测试 —— ThemeToggle 用 lucide 图标（无 emoji）**

先确认是否已有 `ThemeToggle.test.tsx`：

```bash
ls packages/web/frontend/src/components/layout/ThemeToggle.test.tsx 2>/dev/null && echo EXISTS || echo MISSING
```

若 MISSING，新建 `src/components/layout/ThemeToggle.test.tsx`：

```tsx
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { ThemeToggle } from "./ThemeToggle";

describe("ThemeToggle", () => {
  it("用 lucide svg 图标而非 emoji", () => {
    const { container, rerender } = render(<ThemeToggle />);
    // 含 svg（lucide 渲染 svg），不含 ☀️/🌙 emoji
    expect(container.querySelector("svg")).not.toBeNull();
    expect(container.textContent).not.toMatch(/[☀️🌙]/);
  });
});
```

若 EXISTS，追加等价断言（保留现有用例）。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd packages/web/frontend && npx vitest run src/components/layout/ThemeToggle.test.tsx
```

预期：FAIL（现有 ThemeToggle 渲染 emoji 字符串，无 svg）。

- [ ] **Step 3: 改 `ThemeToggle.tsx`**

把 `ThemeToggle.tsx:22` 的 `{theme === "dark" ? "☀️" : "🌙"}` 替换为：

```tsx
      {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
```

并在文件顶部 import 加：

```tsx
import { Sun, Moon } from "lucide-react";
```

- [ ] **Step 4: 跑 ThemeToggle 测试确认通过**

```bash
cd packages/web/frontend && npx vitest run src/components/layout/ThemeToggle.test.tsx
```

预期：PASS。

- [ ] **Step 5: 改 `LogsTab.tsx` —— `.trace` / `.ev-info` 换 DSF**

先 `grep -n "trace\|ev-info\|ev-warn" packages/web/frontend/src/routes/WorkspaceDetail/LogsTab.tsx`，逐处替换：
- `.trace`（提示行）→ `text-sm text-muted-foreground`
- `.ev-info`（信息事件）→ `border-l-2 border-cyan/40 bg-cyan/10 px-2`
- 同步更新 `LogsTab.test.tsx` 中相关 class 断言（若有）。

> 实现时先 Read `LogsTab.tsx` 与 `LogsTab.test.tsx` 确认每处用法与测试断言，再做精确 Edit。

- [ ] **Step 6: 处理 `DashboardPanel.tsx` 的 `.spinner`**

```bash
grep -rn "DashboardPanel" packages/web/frontend/src --include="*.tsx" | grep -v "DashboardPanel.tsx"
```

- 若有引用：把 `DashboardPanel.tsx` 内 `.spinner` → `.shannon-spinner`（DSF 自带，`index.css:32`）。
- 若无引用：不动（follow-up 清理）。

- [ ] **Step 7: 跑受影响测试 + 全套前端测试**

```bash
cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/LogsTab.test.tsx src/components/layout/
```

预期：PASS。

- [ ] **Step 8: Commit**

```bash
git add packages/web/frontend/src/components/layout/ThemeToggle.tsx packages/web/frontend/src/components/layout/ThemeToggle.test.tsx packages/web/frontend/src/routes/WorkspaceDetail/LogsTab.tsx
# 若改了 DashboardPanel 再 add 它
git commit -m "style(web): ThemeToggle emoji 换 lucide 图标 + LogsTab 解耦 events.css"
```

---

## 全量验证（所有 task 完成后）

- [ ] **V1: 全套前端单测**

```bash
cd packages/web/frontend && npm test
```

预期：全绿（含本计划新增 / 更新的全部用例 + 现有用例）。

- [ ] **V2: 构建**

```bash
cd packages/web/frontend && npm run build
```

预期：`tsc -b` 零错、`vite build` 成功。

- [ ] **V3: 浏览器冒烟**

```bash
cd packages/web/frontend && npm run dev
```

人工核对（spec「验证 §3」）：
1. completed workspace `/p/:ws/report`：代码块有配色、表格渲染成 `<table>`、正文 sans / 标题 serif、空报告 TOC 隐藏退单栏、inline code 无复制按钮 / block code 有复制 + 语言角标、hero 用 lucide 图标、锚跳转不被 sticky 遮挡。
2. 浅色主题切换（TopBar toggle）：报告 / 扫描 / 列表 / 详情 / LogsTab 全响应、无深色 hex 残留断裂。
3. 扫描页表单 / YamlEditor / 提交按钮双主题正常。
4. 详情页 header 显返回按钮 + 状态 / 类型 / repo。

- [ ] **V4: （可选 follow-up）events.css 死规则清理**

```bash
cd packages/web/frontend && grep -rn "class.*\(page\|scan-page\|submit-btn\|trace\|git-extra\|yaml-editor\|ev-warn\|status-bar\|ev-info\|spinner\b" src --include="*.tsx"
```

确认无引用后，可从 `events.css` 删对应规则（**非阻塞，可单独 follow-up**）。

---

## Self-Review（写完后自查，已通过）

- **Spec 覆盖**：spec T1→Task 1（remark-gfm）+ Task 2（排版结构）；T2→Task 3（report.css）；T3→Task 4；T4→Task 5；T5→Task 6；T6→Task 7。全覆盖。
- **占位符**：无 TBD/TODO；每步含具体代码 / 命令 / 预期。
- **类型一致**：`SessionData` 字段（`status/scan_type/repo_path`）在 Task 6 mock 与实现一致；`STATUS_COLOR` 与 `StatusBadge.tsx:3-10` 色映射一致；`data-testid="code-block"/"code-lang"/"copy-btn"` 在 Task 2 测试与实现一致；`kv-row` testid 在 Task 2 实现里标注「先读现有值保持一致」。
- **风险锚点**：Task 2 Step 3 注明先核对 `kv-row` testid 现值；Task 4/5/7 注明先 Read 现有上下文再精确 Edit。
