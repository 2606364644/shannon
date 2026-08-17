# 前端性能优化四件套 + React 19 升级 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 spec 落地前端四项性能优化（路由代码分割 / React Compiler / SWR / SSE store）并将 React 升级到 19.2.8。

**Architecture:** 五个串行阶段 A→E：先升级 React 与依赖基线，再做路由 lazy 分割 + highlight 语言子集，接入 React Compiler，SWR 化三处轮询场景（useWorkspaces / Dashboard / WorkspaceDetail+ScanList 共享 key 去重），最后以 useSyncExternalStore 外部 store 重写 SSE 层（rAF 批量 + 5000 环形缓冲）。

**Tech Stack:** React 19.2.8 + Vite 5 + react-router-dom 6.30 + SWR 2.5 + babel-plugin-react-compiler 1.0 + Vitest 2。

**Spec:** `docs/superpowers/specs/2026-08-17-frontend-perf-react19-design.md`

## Global Constraints

- 所有路径相对 `packages/web/frontend/`；npm 命令都在该目录执行。
- **只跑** `npm test`（vitest）与 `npm run build`（含 tsc -b）。**禁止跑 pytest**（CLAUDE.md §3：预存挂起）。
- **已知基线（非回归）**：`npm test` 基线为 **852 通过 / 2 个文件级失败**（`src/lib/juice-shop-smoke.test.ts`、`src/lib/vuln-block.smoke.test.ts`——读取本地真实报告 fixture，环境缺文件）。每个任务的「测试通过」都以此为标准。
- **构建基线**：单 chunk JS 1150.47 kB（gzip 349.96 kB）+ CSS 78.73 kB（gzip 14.23 kB）。
- 版本（精确）：`react`/`react-dom` ^**19.2.8**；`@types/react`/`@types/react-dom` ^19；`react-window` ^**1.8.11**（不进 v2）；`@testing-library/react` ^**16.3.2**；`react-router-dom` ^**6.30.4**（不进 v7）；`swr` ^**2.5.1**；`babel-plugin-react-compiler` ^**1.0.0**（devDep）。
- SSE 环形缓冲 **CAP = 5000**；workspaces 轮询 **5000ms**；scans 条件轮询 **10_000ms**。
- 路由 lazy 范围：**Login 与 Dashboard 保持 eager**，其余按 Task 3 清单。
- 分支 `feat/pre-scan-local-agent`；commit 前缀沿用仓库惯例（`feat(web):` / `chore:` / `refactor(web):`）。
- **spec §3.3 的 `<title>` 子项按 spec 决策规则判为不适用**（核查结论）：`BrandContext.tsx:64-66` 全局 `document.title = brand` 且 `LoginPage.test.tsx:80-81` 锁定该继承行为，无既有 per-page title 需求——本计划不动 title，此项 no-op。

---

### Task 1: React 19.2.8 升级 + 未使用依赖清理

**Files:**
- Modify: `package.json`
- Modify: `package-lock.json`（npm 自动）
- Modify: `src/pages/ScanNewPage.test.tsx`（删 monaco mock）
- Modify: `src/components/__tests__/ScanNewPageCombined.test.tsx`（删 monaco mock）

**Interfaces:**
- Consumes: 无
- Produces: React 19.2.8 运行时基线（后续所有任务的前置）

- [ ] **Step 1: 改 package.json 依赖**

`dependencies` 中修改版本：
```json
"react": "^19.2.8",
"react-dom": "^19.2.8",
"react-router-dom": "^6.30.4",
"react-window": "^1.8.11"
```
`dependencies` 中删除整行：`"@monaco-editor/react"`、`"js-yaml"`。
`devDependencies` 中修改：`"@testing-library/react": "^16.3.2"`；删除整行：`"@types/js-yaml"`、`"@tanstack/react-table"`。

- [ ] **Step 2: 删除两处无用的 monaco vi.mock**

`src/pages/ScanNewPage.test.tsx:11` 与 `src/components/__tests__/ScanNewPageCombined.test.tsx:22` 各有一段：
```ts
vi.mock("@monaco-editor/react", () => ({ ... }));
```
整段 `vi.mock("@monaco-editor/react", ...)` 语句删除（依赖已移除，vitest 无法解析该模块 ID 会直接报错）。

- [ ] **Step 3: 安装并验证类型与测试**

Run: `npm install`
Run: `npm test`
Expected: 852 passed，仅 2 个已知基线文件失败（juice-shop-smoke / vuln-block.smoke）。React 19 类型变更若引发 tsc 报错，报错点应只在 `forwardRef` 相关（Task 2 处理前不应出现——forwardRef 在 19 中仍可用）；出现其它类型错误则逐个修复（不得跳过）。

Run: `npm run build`
Expected: 构建成功，体积与基线相当（±5%）。

- [ ] **Step 4: Commit**

```bash
git add package.json package-lock.json src/pages/ScanNewPage.test.tsx src/components/__tests__/ScanNewPageCombined.test.tsx
git commit -m "chore(web): upgrade react 18.3.1 -> 19.2.8 + drop unused deps (monaco/js-yaml/tanstack-table)"
```

---

### Task 2: forwardRef → ref-as-prop（14 个 ui 原语）

**Files:**
- Modify: `src/components/ui/{button,card,checkbox,command,dialog,input,label,popover,select,switch,table,tabs,textarea,tooltip}.tsx`

**Interfaces:**
- Consumes: Task 1 的 React 19 类型（`React.ComponentProps` 含 `ref`）。
- Produces: 14 个原语的对外导出名与 props 完全不变（`ref` 经 props 透传，消费者零改动）。

**变换规则（每个 forwardRef 组件统一适用）：**
1. `const X = React.forwardRef<Ref, Props>(({ ... }, ref) => body)` → `function X(props: Props) { body }`，解构保持原样。
2. JSX 里删除 `ref={ref}`（`ref` 已在 `...props` 中透传）。
3. Props 类型：`extends React.HTMLAttributes<HTMLDivElement>` → `extends React.ComponentProps<"div">`（其余 intrinsic 同理：`"button"` / `"input"` / `"textarea"` / `"table"` / `"thead"` / `"tbody"` / `"tr"` / `"th"` / `"td"`）；Radix 包装的 `React.forwardRef<React.ElementRef<typeof P.Root>, React.ComponentPropsWithoutRef<typeof P.Root>>` → 直接 `React.ComponentProps<typeof P.Root>`。
4. 删除全部 `X.displayName = ...` 行。

**逐文件清单（组件 → 新 props 基类型）：**

| 文件 | 组件 | 基类型 |
|---|---|---|
| button | Button | `React.ComponentProps<"button"> & VariantProps<typeof buttonVariants>`（`asChild` 保留在接口里） |
| card | Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter | `React.ComponentProps<"div">` |
| checkbox | Checkbox | `React.ComponentProps<typeof CheckboxPrimitive.Root>` |
| command | Command, CommandInput, CommandList, CommandItem, CommandSeparator（文件内全部 forwardRef 项） | `React.ComponentProps<typeof CommandPrimitive(.X 对应项)>` |
| dialog | DialogOverlay, DialogContent（DialogHeader/DialogFooter 本就是普通 div，不动） | `React.ComponentProps<typeof DialogPrimitive.Overlay/Content>` |
| input | Input | `React.ComponentProps<"input">` |
| label | Label | `React.ComponentProps<typeof LabelPrimitive.Root>` |
| popover | PopoverContent | `React.ComponentProps<typeof PopoverPrimitive.Content>` |
| select | SelectTrigger, SelectScrollUpButton, SelectScrollDownButton, SelectContent, SelectItem, SelectLabel, SelectSeparator | `React.ComponentProps<typeof SelectPrimitive.X>` |
| switch | Switch | `React.ComponentProps<typeof SwitchPrimitives.Root>` |
| table | Table, TableHeader, TableBody, TableRow, TableHead, TableCell | `React.ComponentProps<"table"/"thead"/"tbody"/"tr"/"th"/"td">` |
| tabs | TabsList, TabsTrigger, TabsContent | `React.ComponentProps<typeof TabsPrimitive.X>` |
| textarea | Textarea | `React.ComponentProps<"textarea">` |
| tooltip | TooltipContent | `React.ComponentProps<typeof TooltipPrimitive.Content>` |

- [ ] **Step 1: 完整示例——button.tsx 全量改后形态**

```tsx
import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const buttonVariants = cva(/* 原样保留，不改 */)

export interface ButtonProps
  extends React.ComponentProps<"button">,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button"
  return (
    <Comp
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
```
其余 13 个文件按「变换规则 + 逐文件清单」机械应用。

- [ ] **Step 2: 类型检查 + 全量测试**

Run: `npx tsc -b`
Expected: 0 error。

Run: `npm test`
Expected: 基线（852 通过 / 2 已知文件失败）。Radix 组件的 ref 透传由现有测试覆盖（Dialog/Select/Tooltip 等交互测试）。

- [ ] **Step 3: Commit**

```bash
git add src/components/ui
git commit -m "refactor(web): forwardRef -> ref-as-prop across 14 ui primitives (react 19 idiom)"
```

---

### Task 3: 路由 React.lazy + Suspense + vendor manualChunks

**Files:**
- Modify: `src/router.tsx`（全量重写 import 区，路由表不变）
- Modify: `src/components/layout/AppShell.tsx`
- Modify: `vite.config.ts`

**Interfaces:**
- Consumes: 无
- Produces: 按需 chunk（report/markdown 栈独立）；路由表结构与路径完全不变。

- [ ] **Step 1: router.tsx 头部改为 lazy 导入**

删除对下列模块的静态 import，替换为（`DefaultScanTab`/`LegacyWsTabRedirect` 的定义体与整个路由表**不变**）：

```tsx
import { lazy } from "react";
import { createBrowserRouter, useNavigate, useParams, Navigate } from "react-router-dom";
import { useEffect } from "react";
import { DashboardPage } from "./pages/DashboardPage";
import { getScan, listScans } from "./api/client";
import { AppShell } from "./components/layout/AppShell";
import LoginPage from "./pages/LoginPage";
import { RequireAuth } from "./auth/RequireAuth";
import { RequireAdmin } from "./auth/RequireAdmin";

// 重页面按需加载（spec §B）：Login/Dashboard 是最高频首屏路径保持 eager；
// ReportTab → MarkdownView → react-markdown/micromark/highlight 栈随动态 import 独立成 chunk。
const ScanNewPage = lazy(() => import("./pages/ScanNewPage").then(m => ({ default: m.ScanNewPage })));
const SettingsPage = lazy(() => import("./pages/SettingsPage").then(m => ({ default: m.SettingsPage })));
const UsersPage = lazy(() => import("./pages/UsersPage").then(m => ({ default: m.UsersPage })));
const WorkspaceDetail = lazy(() => import("./routes/WorkspaceDetail"));
const ScanList = lazy(() => import("./routes/WorkspaceDetail/ScanList").then(m => ({ default: m.ScanList })));
const ScanDetail = lazy(() => import("./routes/WorkspaceDetail/ScanDetail"));
const OverviewTab = lazy(() => import("./routes/WorkspaceDetail/OverviewTab").then(m => ({ default: m.OverviewTab })));
const ReportTab = lazy(() => import("./routes/WorkspaceDetail/ReportTab").then(m => ({ default: m.ReportTab })));
const DeliverablesTab = lazy(() => import("./routes/WorkspaceDetail/DeliverablesTab").then(m => ({ default: m.DeliverablesTab })));
const LogsTab = lazy(() => import("./routes/WorkspaceDetail/LogsTab").then(m => ({ default: m.LogsTab })));
const LiveTab = lazy(() => import("./routes/WorkspaceDetail/LiveTab"));
const ReposTab = lazy(() => import("./routes/WorkspaceDetail/ReposTab").then(m => ({ default: m.ReposTab })));
const WsSettingsTab = lazy(() => import("./routes/WorkspaceDetail/WsSettingsTab"));
const AuthProfilesPage = lazy(() => import("./pages/AuthProfilesPage").then(m => ({ default: m.AuthProfilesPage })));
const AuthProfileTestPage = lazy(() => import("./pages/AuthProfileTestPage").then(m => ({ default: m.AuthProfileTestPage })));
const VerifyProcessPage = lazy(() => import("./pages/VerifyProcessPage").then(m => ({ default: m.VerifyProcessPage })));
const HostProfilesPage = lazy(() => import("./pages/HostProfilesPage").then(m => ({ default: m.HostProfilesPage })));
const WorkspacesEntry = lazy(() => import("./components/WorkspacesEntry").then(m => ({ default: m.WorkspacesEntry })));
const DevComponentsPage = lazy(() => import("./pages/DevComponentsPage").then(m => ({ default: m.DevComponentsPage })));
```

- [ ] **Step 2: AppShell 的 Outlet 包 Suspense**

`src/components/layout/AppShell.tsx`：
```tsx
import { Suspense, useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import { TopBar } from "./TopBar";
import { ChangePasswordDialog } from "@/components/ChangePasswordDialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/auth/AuthContext";
```
`<main>` 内改为：
```tsx
      <main className="mx-auto w-full max-w-[2400px] px-7 py-5">
        {/* lazy 路由 chunk 加载期的页面级骨架（spec §B）：高度贴近典型页首屏，避免布局跳动 */}
        <Suspense fallback={<div className="space-y-4"><Skeleton className="h-28 w-full" /><Skeleton className="h-44 w-full" /></div>}>
          <Outlet />
        </Suspense>
      </main>
```

- [ ] **Step 3: vite.config.ts 加 manualChunks**

在 `defineConfig` 中新增（与现有 `server`/`test` 平级）：
```ts
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          "react-vendor": ["react", "react-dom", "react-router", "react-router-dom", "scheduler"],
        },
      },
    },
  },
```

- [ ] **Step 4: 测试 + 构建 + 记录体积**

Run: `npm test`
Expected: 基线（页面测试直接 import 页面组件，不经 router，不受 lazy 影响；若有测试经 RouterProvider 渲染并因 lazy 时序失败，把断言改 `findBy*`/`waitFor`）。

Run: `npm run build`
Expected: 多 chunk 产出（react-vendor + 主入口 + 各 lazy 页面 chunk）；记录各 chunk 尺寸（写入 commit message）。

- [ ] **Step 5: Commit**

```bash
git add src/router.tsx src/components/layout/AppShell.tsx vite.config.ts
git commit -m "feat(web): route-level React.lazy code splitting + react-vendor manualChunks"
```

---

### Task 4: rehype-highlight 语言子集

**Files:**
- Modify: `src/components/MarkdownView.tsx`

**Interfaces:**
- Consumes: 无
- Produces: 高亮行为不变（报告实际语言全覆盖），bundle 减小。

- [ ] **Step 1: 加语言子集导入与常量**

在 MarkdownView.tsx 头部（`import rehypeHighlight from "rehype-highlight";` 之后）加：
```tsx
import bash from "highlight.js/lib/languages/bash";
import json from "highlight.js/lib/languages/json";
import python from "highlight.js/lib/languages/python";
import javascript from "highlight.js/lib/languages/javascript";
import typescript from "highlight.js/lib/languages/typescript";
import java from "highlight.js/lib/languages/java";
import sql from "highlight.js/lib/languages/sql";
import http from "highlight.js/lib/languages/http";
import yaml from "highlight.js/lib/languages/yaml";
import xml from "highlight.js/lib/languages/xml";
import ini from "highlight.js/lib/languages/ini";
import css from "highlight.js/lib/languages/css";

// 语言子集（spec §4.3）：rehype-highlight 的 languages 选项整体替换默认 lowlight common
// 全集（源码 `settings.languages || common`），按报告实际语言注册，砍掉未用的高亮语法。
const HL_LANGS = { bash, json, python, javascript, typescript, java, sql, http, yaml, xml, ini, css };
const HIGHLIGHT_PLUGIN = [rehypeHighlight, { languages: HL_LANGS }] as const;
```

- [ ] **Step 2: 四处使用点换用 HIGHLIGHT_PLUGIN**

文件内共 4 处 `rehypePlugins={[..., rehypeHighlight] as never}`（prose 段 / 漏洞卡 body / PoC / orphan PoC），统一改为：
```tsx
rehypePlugins={[makeSegmentSlugPlugin(i), ...HIGHLIGHT_PLUGIN] as never}
```
（无 `makeSegmentSlugPlugin` 的三处为 `rehypePlugins={[...HIGHLIGHT_PLUGIN] as never}`。）

- [ ] **Step 3: 测试 + 构建**

Run: `npm test`
Expected: 基线（`ReportTab.test.tsx`、`__tests__/ReportTabCombined.test.tsx` 覆盖 `language-*` class 与 code-block 渲染）。

Run: `npm run build`
Expected: report chunk 进一步缩小；记录数字。

- [ ] **Step 4: Commit**

```bash
git add src/components/MarkdownView.tsx
git commit -m "perf(web): rehype-highlight registered-language subset instead of lowlight common"
```

---

### Task 5: React Compiler 接入

**Files:**
- Modify: `package.json`（devDep）
- Modify: `vite.config.ts`

**Interfaces:**
- Consumes: Task 1 的 React 19（零 runtime）。
- Produces: 全应用自动 memo 化编译（源码不改）。

- [ ] **Step 1: 安装并配置**

Run: `npm install -D babel-plugin-react-compiler@^1.0.0`

`vite.config.ts` 的 react 插件改为：
```ts
  plugins: [
    react({
      babel: {
        // React Compiler（spec §C）：编译期自动 memo 化。panicThreshold=none——编译失败
        // 的组件自动回退原实现，不挂构建。React 19 下无需 runtime 包。
        plugins: [["babel-plugin-react-compiler", { panicThreshold: "none" }]],
      },
    }),
  ],
```

- [ ] **Step 2: 测试 + 构建**

Run: `npm test`
Expected: 基线（vitest 走同一 vite 管道，编译器对测试同样生效，行为应等价）。

Run: `npm run build`
Expected: 构建成功（任何组件编译失败只回退，不报错）。

- [ ] **Step 3: Commit**

```bash
git add package.json package-lock.json vite.config.ts
git commit -m "perf(web): enable react compiler (auto memoization, panicThreshold none)"
```

---

### Task 6: SWR 基建 + useWorkspaces 重写（TDD）

**Files:**
- Modify: `package.json`（加 `swr`）
- Create: `src/test/swr-render.tsx`
- Modify: `src/App.tsx`
- Test: `src/api/useWorkspaces.test.ts`（新建）
- Modify: `src/api/useWorkspaces.ts`

**Interfaces:**
- Consumes: `apiGet`（`src/api/client.ts:70`）。
- Produces:
  - `useWorkspaces(intervalMs = 5000): { data: Workspace[]; loading: boolean; error: string | null; refresh: () => Promise<void> }`（**`lastUpdated` 删除**——全仓无消费者，已核查）。
  - `renderWithSwr(ui, options?)`（测试助手，Task 7/8 复用）。

- [ ] **Step 1: 安装**

Run: `npm install swr@^2.5.1`

- [ ] **Step 2: 写失败的测试 `src/api/useWorkspaces.test.ts`**

```tsx
import { renderHook, waitFor, act } from "@testing-library/react";
import type { ReactNode } from "react";
import { SWRConfig } from "swr";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useWorkspaces } from "./useWorkspaces";
import { apiGet } from "./client";
import type { Workspace } from "./types";

vi.mock("./client", () => ({
  apiGet: vi.fn(),
  // useWorkspaces 的 error 分支 instanceof ApiError；给个最小形状即可（本测试不打错误分支）。
  ApiError: class ApiError extends Error { status: number },
}));

const ws: Workspace[] = [{ name: "alpha" } as Workspace];
const wrapper = ({ children }: { children: ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map() }}>{children}</SWRConfig>
);

describe("useWorkspaces (swr)", () => {
  beforeEach(() => { vi.mocked(apiGet).mockReset(); });

  it("初值 loading，解析后出 data 且 loading=false", async () => {
    vi.mocked(apiGet).mockResolvedValue(ws);
    const { result } = renderHook(() => useWorkspaces(), { wrapper });
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual(ws);
    expect(result.current.error).toBeNull();
  });

  it("refresh() 触发重新请求", async () => {
    vi.mocked(apiGet).mockResolvedValue(ws);
    const { result } = renderHook(() => useWorkspaces(), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(() => result.current.refresh());
    expect(apiGet).toHaveBeenCalledTimes(2);
  });
});
```

- [ ] **Step 3: 跑测试确认失败**

Run: `npx vitest run src/api/useWorkspaces.test.tsx`
Expected: FAIL（lastUpdated 语义不存在/loading 初始为 false 等，取决于现状——核心是红）。

- [ ] **Step 4: 实现 `src/api/useWorkspaces.ts`（全量替换）**

```ts
import { useCallback } from "react";
import useSWR from "swr";
import { apiGet, ApiError } from "./client";
import i18n from "@/i18n";
import type { Workspace } from "./types";

export interface UseWorkspacesResult {
  data: Workspace[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

/** SWR 版（spec §D）：refreshInterval 轮询 + SWR 默认 refreshWhenHidden=false
 *  （后台 tab 自动停轮询）+ revalidateOnFocus（回前台刷新）——取代手写 setInterval。
 *  lastUpdated 无消费者，已删。 */
export function useWorkspaces(intervalMs = 5000): UseWorkspacesResult {
  const { data, isLoading, error, mutate } = useSWR<Workspace[]>(
    "/workspaces",
    (path: string) => apiGet<Workspace[]>(path),
    { refreshInterval: intervalMs },
  );
  const refresh = useCallback(async () => { await mutate(); }, [mutate]);
  return {
    data: data ?? [],
    loading: isLoading && data === undefined,
    error: error
      ? error instanceof ApiError
        ? i18n.t("common.loadFailedStatus", { status: error.status })
        : i18n.t("common.loadFailed")
      : null,
    refresh,
  };
}
```

- [ ] **Step 5: 测试助手 + App.tsx 全局 SWRConfig**

Create `src/test/swr-render.tsx`：
```tsx
import { render } from "@testing-library/react";
import type { RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { SWRConfig } from "swr";

/** SWR 组件测试渲染：每次 render 独立 cache（SWR 全局缓存跨测试会泄漏数据与状态）。 */
export function renderWithSwr(ui: ReactElement, options?: RenderOptions) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <SWRConfig value={{ provider: () => new Map() }}>{children}</SWRConfig>
  );
  return render(ui, { wrapper, ...options });
}
```

`src/App.tsx` 在 ThemeProvider 内层包：
```tsx
import { SWRConfig } from "swr";
import { apiGet } from "@/api/client";
// ...
        <SWRConfig value={{ fetcher: (path: string) => apiGet(path) }}>
          <RouterProvider router={router} />
        </SWRConfig>
```

- [ ] **Step 6: 验证 + Commit**

Run: `npx vitest run src/api/useWorkspaces.test.tsx`
Expected: PASS。

Run: `npm test`
Expected: 基线。若 `WorkspaceSwitcher`/`WorkspacesEntry` 相关既有测试因 SWR 时序失败，把其 render 换成 `renderWithSwr`（import 自 `@/test/swr-render`）并按需 `findBy*`。

```bash
git add package.json package-lock.json src/App.tsx src/api/useWorkspaces.ts src/api/useWorkspaces.test.tsx src/test/swr-render.tsx
git commit -m "feat(web): swr infra + useWorkspaces rewrite (hidden-tab polling pause, focus revalidate)"
```

---

### Task 7: DashboardPage SWR 化（条件轮询）

**Files:**
- Modify: `src/pages/DashboardPage.tsx`
- Modify: `src/pages/DashboardPage.test.tsx`（render 适配）

**Interfaces:**
- Consumes: `listAllScans`（`src/api/client.ts:160`）；`renderWithSwr`（Task 6）。
- Produces: Dashboard 数据层走 SWR（key `"all-scans"`），`hasRunning` 存在时 10s 轮询、后台 tab 自动暂停。

- [ ] **Step 1: 替换数据层（DashboardPage.tsx）**

删除 `useAsync` 导入与手写轮询 effect；`isRun` 常量保留。原：
```tsx
const { data, loading, error, refresh } = useAsync(listAllScans, []);
```
及 `hasRunning` 轮询 effect（`useEffect(... setInterval ... 10_000 ...)`）替换为：
```tsx
import useSWR from "swr";
// ...
  const { data: raw, isLoading, error: rawError, mutate } = useSWR<ScanSummary[]>(
    "all-scans",
    () => listAllScans(),
    // 条件轮询（spec §6.3）：有运行中扫描才 10s 轮询；SWR 默认后台 tab 暂停 + 回前台刷新。
    { refreshInterval: (latest?: ScanSummary[]) => (latest?.some(isRun) ? 10_000 : 0) },
  );
  const data = raw ?? [];
  const loading = isLoading && raw === undefined;
  const error = rawError ? (rawError instanceof Error ? rawError.message : String(rawError)) : null;
  const refresh = useCallback(async () => { await mutate(); }, [mutate]);
  const hasRunning = data.some(isRun);
```
`refreshedAt`/`doRefresh`/10s tick 逻辑保持原样（`doRefresh` 继续调 `refresh()`，签名兼容）。

- [ ] **Step 2: 测试适配**

`src/pages/DashboardPage.test.tsx`：`render(<DashboardPage />)` → `renderWithSwr(<DashboardPage />)`（import 自 `@/test/swr-render`）。断言不变（`listAllScans` 已被 `vi.mock("@/api/client")` 接管，SWR 走同一 mock）。

- [ ] **Step 3: 验证 + Commit**

Run: `npx vitest run src/pages/DashboardPage.test.tsx`
Expected: PASS（若个别断言因 SWR 首帧 undefined 数据闪 skeleton 失败，用 `waitFor/findBy*` 包裹）。

Run: `npm test` → 基线。

```bash
git add src/pages/DashboardPage.tsx src/pages/DashboardPage.test.tsx
git commit -m "feat(web): dashboard on swr with conditional refreshInterval"
```

---

### Task 8: WorkspaceDetail + ScanList 共享 SWR key（双份请求去重）

**Files:**
- Create: `src/routes/WorkspaceDetail/useScans.ts`
- Modify: `src/routes/WorkspaceDetail/index.tsx`
- Modify: `src/routes/WorkspaceDetail/ScanList.tsx`
- Modify（render 适配）: `src/routes/WorkspaceDetail/index.test.tsx`、`ScanList.test.tsx`、`WorkspaceDetail.test.tsx`、`ScanDetail.test.tsx`、`__tests__/ScanListCombined.test.tsx`、`__tests__/ScanDetailRuns.test.tsx`、`__tests__/ScanDetailTaskFailure.test.tsx`、`__tests__/ReportTabCombined.test.tsx`（凡直接 render 这两个组件/其树的测试）

**Interfaces:**
- Consumes: `listScans`（`src/api/client.ts:156`）；`renderWithSwr`。
- Produces: `useScans(workspace?: string): { scans: ScanSummary[]; loading: boolean; notFound: boolean; error: string | null; refresh: () => void }`——同 key `["scans", workspace]`，父子组件自动共享单请求单轮询。

- [ ] **Step 1: 新建 `useScans.ts`**

```ts
import useSWR from "swr";
import { listScans, ApiError } from "@/api/client";
import type { ScanSummary } from "@/api/types";

const isRunning = (s: ScanSummary) => s.is_running || s.status === "running";

export interface UseScansResult {
  scans: ScanSummary[];
  loading: boolean;
  notFound: boolean;
  error: string | null;
  refresh: () => void;
}

/** ws 扫描列表（spec §6.3）：WorkspaceDetail 容器与 ScanList 共用同一 key
 *  （["scans", workspace]）→ SWR 去重为单请求 + 单份 10s 条件轮询（运行中才轮询，
 *  后台 tab 暂停），取代此前父子各拉各轮询的双份流量。 */
export function useScans(workspace: string | undefined): UseScansResult {
  const { data, error, isLoading, mutate } = useSWR(
    workspace ? ["scans", workspace] : null,
    () => listScans(workspace!),
    { refreshInterval: (latest?: ScanSummary[]) => (latest?.some(isRunning) ? 10_000 : 0) },
  );
  return {
    scans: data ?? [],
    loading: isLoading && data === undefined,
    notFound: error instanceof ApiError && error.status === 404,
    error: error ? (error instanceof Error ? error.message : String(error)) : null,
    refresh: () => { void mutate(); },
  };
}
```

- [ ] **Step 2: index.tsx 接入**

删除 `scans/loading/notFound` 三个 useState、`load` 函数、两个 `useEffect`（加载 + 轮询）；`listScans, ApiError` 的 import 若仅剩 `setPinnedWorkspace` 用途则相应收窄。替换为：
```tsx
import { useScans } from "./useScans";
// ...
  const { workspace } = useParams<{ workspace: string }>();
  const { scans, loading, notFound, refresh } = useScans(workspace);
```
Outlet context 改：
```tsx
      <ErrorBoundary><Outlet context={{ refresh } satisfies WsOverviewCtx} /></ErrorBoundary>
```

- [ ] **Step 3: ScanList.tsx 接入**

删除其自身的 `scans/err/loading` state、`load` 函数、`useEffect(load...)` 与 `hasRunning` 轮询 effect、`POLL_INTERVAL_MS` 常量。替换为：
```tsx
import { useScans } from "./useScans";
// ...
  const { scans, loading, error: err, refresh: refreshScans } = useScans(workspace);
```
`reload`（原 `const reload = () => { load(); wsCtx?.refresh?.(); }`）改为：
```tsx
  // 同 key mutate 两次 → SWR 去重为一次请求（spec §6.3）。
  const reload = () => { refreshScans(); wsCtx?.refresh?.(); };
```
渲染分支沿用 `loading` / `err` 变量名，页面行为不变。

- [ ] **Step 4: 测试适配 + 验证**

上列测试文件中的 `render(<WorkspaceDetail />)` / `render(<ScanList />)` 等 → `renderWithSwr(...)`。`listScans` 已被各测试 `vi.mock("@/api/client")` 接管，SWR 走同一 mock；时序断言按需 `findBy*`/`waitFor`。

Run: `npx vitest run src/routes/WorkspaceDetail`
Expected: 全 PASS。

Run: `npm test` → 基线。

- [ ] **Step 5: Commit**

```bash
git add src/routes/WorkspaceDetail
git commit -m "feat(web): workspace scans on shared swr key (dedup parent+child polling)"
```

---

### Task 9: scanEventStore（TDD——rAF 批量 + 5000 环形缓冲 + 引用计数）

**Files:**
- Test: `src/api/scanEventStore.test.ts`（新建）
- Create: `src/api/scanEventStore.ts`

**Interfaces:**
- Consumes: `NdjsonEvent`（`src/api/types.ts`）。
- Produces（Task 10 消费）:
  - `getScanEventStore(url: string, stopType: string): ScanEventStore`（按 `${stopType}::${url}` 单例）
  - `ScanEventStore.subscribe(cb: () => void): () => void`（useSyncExternalStore 协议；引用计数归零自动关连接）
  - `ScanEventStore.getSnapshot(): SseSnapshot`，`SseSnapshot = { events: NdjsonEvent[]; status: "open" | "closed" | "error"; lastEventId?: string; version: number }`——两次 flush 之间引用恒定
  - `_resetScanEventStoresForTests(): void`（测试隔离）

- [ ] **Step 1: 写失败的测试 `src/api/scanEventStore.test.ts`**

```ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  getScanEventStore,
  _resetScanEventStoresForTests,
} from "./scanEventStore";

// —— 受控 rAF：手动驱动 flush ——
let rafCb: (() => void) | null = null;
// —— fake EventSource：记录实例、手动 emit ——
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  closed = false;
  onmessage: ((e: { data: string; lastEventId?: string }) => void) | null = null;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { FakeEventSource.instances.push(this); }
  close() { this.closed = true; }
  emit(type: string, extra?: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify({ type, ts: "t1", ...extra }), lastEventId: undefined });
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  rafCb = null;
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("requestAnimationFrame", (cb: () => void) => { rafCb = cb; return 1; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => {
  _resetScanEventStoresForTests();
  vi.unstubAllGlobals();
});

const flush = () => { const cb = rafCb; rafCb = null; cb?.(); };

describe("scanEventStore", () => {
  it("rAF 批量：多条事件一次 flush、一次通知", () => {
    const store = getScanEventStore("/sse", "scan_end");
    const listener = vi.fn();
    store.subscribe(listener);
    const es = FakeEventSource.instances[0]!;
    es.emit("InfoEvent"); es.emit("InfoEvent"); es.emit("StepEvent");
    expect(listener).not.toHaveBeenCalled();          // flush 前零通知
    expect(store.getSnapshot().events).toHaveLength(0);
    flush();
    expect(listener).toHaveBeenCalledTimes(1);          // 一帧一通知
    expect(store.getSnapshot().events).toHaveLength(3);
  });

  it("getSnapshot 引用稳定：两次 flush 之间恒定", () => {
    const store = getScanEventStore("/sse", "scan_end");
    store.subscribe(vi.fn());
    const es = FakeEventSource.instances[0]!;
    es.emit("InfoEvent"); flush();
    const s1 = store.getSnapshot();
    expect(store.getSnapshot()).toBe(s1);              // 同引用
    es.emit("InfoEvent"); flush();
    expect(store.getSnapshot()).not.toBe(s1);          // flush 后才换新
  });

  it("环形缓冲：超 5000 截尾保新", () => {
    const store = getScanEventStore("/sse", "scan_end");
    store.subscribe(vi.fn());
    const es = FakeEventSource.instances[0]!;
    for (let i = 0; i < 5002; i++) es.emit("InfoEvent", { seq: i });
    flush();
    const events = store.getSnapshot().events as Array<{ seq: number }>;
    expect(events).toHaveLength(5000);
    expect(events[0]!.seq).toBe(2);                    // 头部两条被裁
    expect(events[4999]!.seq).toBe(5001);              // 尾部保新
  });

  it("引用计数：最后一个 unsubscribe 关连接；再 subscribe 复用/重建", () => {
    const store = getScanEventStore("/sse", "scan_end");
    const un1 = store.subscribe(vi.fn());
    const un2 = store.subscribe(vi.fn());
    expect(FakeEventSource.instances).toHaveLength(1); // 同 store 单连接
    un1();
    expect(FakeEventSource.instances[0]!.closed).toBe(false);
    un2();
    expect(FakeEventSource.instances[0]!.closed).toBe(true); // 归零关连接
  });

  it("scan_end：status=closed + 关连接 + 事件仍入列", () => {
    const store = getScanEventStore("/sse", "scan_end");
    store.subscribe(vi.fn());
    const es = FakeEventSource.instances[0]!;
    es.emit("scan_end", { status: "completed" });
    flush();
    const snap = store.getSnapshot();
    expect(snap.status).toBe("closed");
    expect(es.closed).toBe(true);
    expect(snap.events.at(-1)!.type).toBe("scan_end");
  });

  it("同 url+stopType 单例；不同 stopType 不同 store", () => {
    expect(getScanEventStore("/sse", "scan_end")).toBe(getScanEventStore("/sse", "scan_end"));
    expect(getScanEventStore("/sse", "scan_end")).not.toBe(getScanEventStore("/sse", "clone_end"));
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/api/scanEventStore.test.ts`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现 `src/api/scanEventStore.ts`**

```ts
import type { NdjsonEvent } from "./types";

export type SseStatus = "open" | "closed" | "error";
export interface SseSnapshot {
  events: NdjsonEvent[];
  status: SseStatus;
  lastEventId?: string;
  version: number;
}

/** 尾部保留条数（spec §7.1）：LogStream 虚拟化阈值 500 的 10 倍余量。 */
const CAP = 5000;

const EMPTY_SNAPSHOT: SseSnapshot = { events: [], status: "closed", version: 0 };

/** jsdom 无 rAF 时的降级（setTimeout 宏任务）。 */
const scheduleRaf = (cb: () => void): number =>
  typeof requestAnimationFrame === "function"
    ? requestAnimationFrame(cb)
    : (setTimeout(cb, 0) as unknown as number);
const cancelRaf = (id: number): void => {
  if (typeof cancelAnimationFrame === "function") cancelAnimationFrame(id);
  else clearTimeout(id as unknown as ReturnType<typeof setTimeout>);
};

/** useSyncExternalStore 协议的 SSE 外部 store（spec §E）：
 *  - rAF 批量：onmessage 只入 pending 并调度一次，flush 时合并追加 + 重建快照
 *    （两次 flush 之间 getSnapshot 引用恒定——useSyncExternalStore 的硬要求）。
 *  - 环形缓冲：events 尾部截断至 CAP，消除逐条数组复制与无界增长。
 *  - 引用计数：订阅归零自动关连接并出 Map；StrictMode 双挂载安全。 */
class ScanEventStore {
  private listeners = new Set<() => void>();
  private pending: NdjsonEvent[] = [];
  private events: NdjsonEvent[] = [];
  private status: SseStatus = "closed";
  private lastEventId?: string;
  private snapshot: SseSnapshot = EMPTY_SNAPSHOT;
  private es: EventSource | null = null;
  private rafId = 0;
  private refs = 0;

  constructor(private url: string, private stopType: string) {}

  subscribe(cb: () => void): () => void {
    this.refs++;
    this.listeners.add(cb);
    this.connect();
    return () => {
      this.listeners.delete(cb);
      this.refs--;
      if (this.refs <= 0) {
        this.es?.close();
        this.es = null;
        stores.delete(`${this.stopType}::${this.url}`);
      }
    };
  }

  getSnapshot(): SseSnapshot {
    return this.snapshot;
  }

  private connect(): void {
    if (this.es) return;
    const Es = (globalThis as { EventSource?: typeof EventSource }).EventSource;
    if (!Es) return;
    const es = new Es(this.url);
    this.es = es;
    this.status = "open";
    this.flush(); // 发布初始 open 状态
    es.onopen = () => { this.status = "open"; this.flush(); };
    es.onerror = () => { this.status = "error"; this.flush(); };
    es.onmessage = (e: MessageEvent) => {
      let ev: NdjsonEvent;
      try { ev = JSON.parse(String(e.data)) as NdjsonEvent; } catch { return; }
      if (e.lastEventId) this.lastEventId = e.lastEventId;
      if (ev.type === this.stopType) { this.status = "closed"; es.close(); }
      this.pending.push(ev);
      this.scheduleFlush();
    };
  }

  private scheduleFlush(): void {
    if (this.rafId) return;
    this.rafId = scheduleRaf(() => { this.rafId = 0; this.flush(); });
  }

  private flush(): void {
    if (this.rafId) { cancelRaf(this.rafId); this.rafId = 0; }
    if (this.pending.length) {
      const merged = this.events.concat(this.pending);
      this.events = merged.length > CAP ? merged.slice(merged.length - CAP) : merged;
      this.pending = [];
    }
    this.snapshot = {
      events: this.events,
      status: this.status,
      lastEventId: this.lastEventId,
      version: this.snapshot.version + 1,
    };
    for (const cb of this.listeners) cb();
  }
}

const stores = new Map<string, ScanEventStore>();

/** 按 `${stopType}::${url}` 取/建单例 store（连接在首个 subscribe 时惰性建立）。 */
export function getScanEventStore(url: string, stopType: string): ScanEventStore {
  const key = `${stopType}::${url}`;
  let s = stores.get(key);
  if (!s) { s = new ScanEventStore(url, stopType); stores.set(key, s); }
  return s;
}

/** 测试隔离：强制清空全部 store（同文件多测试共享模块级 Map）。 */
export function _resetScanEventStoresForTests(): void {
  stores.clear();
}

export { EMPTY_SNAPSHOT };
```

- [ ] **Step 4: 验证**

Run: `npx vitest run src/api/scanEventStore.test.ts`
Expected: 6 个测试全 PASS。

- [ ] **Step 5: Commit**

```bash
git add src/api/scanEventStore.ts src/api/scanEventStore.test.ts
git commit -m "feat(web): scanEventStore — useSyncExternalStore SSE store with rAF batching + 5000 ring buffer"
```

---

### Task 10: useEventSource 重写为 store 薄包装

**Files:**
- Modify: `src/api/useEventSource.ts`（全量替换）

**Interfaces:**
- Consumes: Task 9 的 `getScanEventStore` / `SseSnapshot` / `EMPTY_SNAPSHOT`。
- Produces: `useEventSource(url: string, stopType = "scan_end"): { events: NdjsonEvent[]; status: SseStatus; lastEventId?: string }`——**签名与返回形状不变**，`LiveTab` / `ScanProgressOverview` / `VerifyLivePanel` / `ScanList` / `CloneProgress`（stopType `clone_end`）零改动；所有消费者测试在模块层 mock 本 hook，不受内部实现影响。

- [ ] **Step 1: 全量替换 `src/api/useEventSource.ts`**

```ts
import { useCallback, useSyncExternalStore } from "react";
import { getScanEventStore, EMPTY_SNAPSHOT, type SseSnapshot } from "./scanEventStore";
import type { NdjsonEvent } from "./types";

export type { SseStatus } from "./scanEventStore";
export interface UseEventSource {
  events: NdjsonEvent[];
  status: SseSnapshot["status"];
  lastEventId?: string;
}

/** SSE 订阅 hook（spec §E）：scanEventStore 的薄包装。url 为空（scanId 未就绪）时
 *  不连接。快照由 store 维持引用稳定，useSyncExternalStore 不会空转。 */
export function useEventSource(url: string, stopType: string = "scan_end"): UseEventSource {
  // getScanEventStore 是按 key 幂等的纯 Map 访问（连接在 subscribe 时才建立），
  // 渲染期调用安全；url 为空时不取 store。
  const store = url ? getScanEventStore(url, stopType) : null;
  const subscribe = useCallback(
    (cb: () => void) => (store ? store.subscribe(cb) : () => {}),
    [store],
  );
  const getSnapshot = useCallback(
    () => (store ? store.getSnapshot() : EMPTY_SNAPSHOT),
    [store],
  );
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
```

- [ ] **Step 2: 验证 + Commit**

Run: `npm test`
Expected: 基线（消费者测试全部 mock 本 hook 模块，内部实现替换对其透明）。

Run: `npm run build`
Expected: 构建成功。

```bash
git add src/api/useEventSource.ts
git commit -m "feat(web): useEventSource on useSyncExternalStore + scanEventStore (api unchanged)"
```

---

### Task 11: 终验与收尾

**Files:**
- Modify: `docs/superpowers/specs/2026-08-17-frontend-perf-react19-design.md`（状态行）

**Interfaces:**
- Consumes: 全部前序任务。
- Produces: 验收记录。

- [ ] **Step 1: 全量验证**

Run: `npm test`
Expected: 852+ 通过 / 仅 2 个已知基线文件失败。

Run: `npm run build`
Expected: 记录 chunk 清单与尺寸，与基线（单 chunk 1150.47 kB / gzip 349.96 kB）对比写入下方 spec 更新。

- [ ] **Step 2: 更新 spec 状态**

spec 头部 `状态：` 行改为 `已实施（2026-08-17，实施记录见 git log / 计划文档）`，并在文末追加一节「实施结果」：chunk 前后对比表（入口 / react-vendor / report chunk / 其余 lazy chunk 的 kB 与 gzip kB）与测试结论一行。

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-08-17-frontend-perf-react19-design.md
git commit -m "docs(superpowers): frontend-perf-react19 spec -> implemented, record build deltas"
```

---

## Self-Review 记录

- **Spec 覆盖**：§3.1 依赖→Task 1；§3.3 forwardRef→Task 2、`<title>`→按 spec 决策规则判不适用（见 Global Constraints）；§4→Task 3/4；§5→Task 5；§6.1-6.3→Task 6/7/8；§6.4 范围外→未设任务（正确）；§7→Task 9/10；§8→各任务验证步 + Task 11。无缺口。
- **占位符**：无 TBD/TODO；所有代码步骤给出完整代码或精确变换规则。
- **类型一致性**：`useScans` 返回形状在 Task 8 定义并在 index/ScanList 接入处一致；`SseSnapshot`/`getScanEventStore`/`_resetScanEventStoresForTests` 在 Task 9 定义、Task 10 消费一致；`renderWithSwr` 在 Task 6 定义、Task 7/8 消费一致。
