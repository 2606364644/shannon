# Shannon Web 重设计 · 子项目 3（扫描页重做）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重做 `ScanNewPage`——shadcn `<Tabs>`（白盒/黑盒/联动）+ `<Card>` fieldset 分组 + 全控件替换 + 集成子项目 2 `<FileSystemPicker>` + 即时校验 + workspace 名预览 + 续扫 `<Dialog>` + toast 错误反馈。

**Architecture:** 纯前端重做，**无后端改动**。`FormState` 单一 useState 提在 `ScanNewPage` 顶层（切 tab 不重置），props 下传给抽取的 `<ScanFormFields>`（白盒/黑盒共享，blackbox 多 reuse 字段）。即时校验在父层 `useMemo` 算 `validity`，控制提交按钮 disable。续扫确认从 inline 横幅改为受控 `<Dialog>`（冲突时点提交才弹）；错误从 inline banner 改为 `toast.error`（sonner）。补挂 DSF 漏挂的 `<Toaster />` 并修 sonner.tsx 的坏依赖（去 next-themes）。

**Tech Stack:** React 18 + shadcn/ui（DSF 已 copy：tabs/dialog/card/input/select/checkbox/label/button/sonner）+ sonner toast + vitest（globals + jsdom）+ MSW + @testing-library/react。

## Global Constraints

- **复用 DSF**：`@/components/ui/*` 已 copy 全；`cn()` from `@/lib/utils`；Tailwind utility class + `hsl(var(--token))` 消费双主题变量。
- **路径别名 `@/ → src/`**（DSF Task 1 已建）。
- **shadcn 组件 export**（精确，本 plan 用到的）：
  - `Tabs={Tabs,TabsList,TabsTrigger,TabsContent}`（TabsTrigger `role="tab"`，天然 a11y）
  - `Dialog={Dialog,DialogTrigger,DialogContent,DialogHeader,DialogFooter,DialogTitle,DialogDescription,DialogClose}`（DialogContent 接 `onInteractOutside={(e)=>e.preventDefault()}` 禁外点）
  - `Card={Card,CardHeader,CardTitle,CardContent,CardFooter,CardDescription}`
  - `Input,Label,Button,Checkbox,Select={Select,SelectTrigger,SelectValue,SelectContent,SelectItem},Textarea`
- **增量迁移**：仅 `ScanNewPage` 内部样式迁 Tailwind；**不动其他业务页**；旧 `events.css` 保留（其他页仍消费 `.ledger`/`.trace`）；本页可继续用 `.trace` class（共用灰字）。
- **不动其他 DSF 产物**：tokens.css / tailwind.config / TopBar / AppShell / ThemeToggle / `@/lib/theme` 不改。**例外**：`ui/sonner.tsx` 因 DSF 漏挂 + next-themes 坏依赖，本子项目 Task 1 修（去 next-themes，读 `document.documentElement.classList`）。
- **前端测试栈**：vitest（globals + jsdom）+ MSW（`setupServer` + `http`/`HttpResponse`）+ @testing-library/react；lifecycle：`beforeAll listen / afterEach resetHandlers+cleanup / afterAll close`；Monaco mock：`vi.mock("@monaco-editor/react", () => ({ default: (props) => <textarea data-testid="monaco" .../> }))`。
- **前端命令必须 `cd packages/web/frontend`**（cwd 不持久，每条 bash 显式 cd）。
- **依赖前置**：子项目 2 `<FileSystemPicker>`（`src/components/FileSystemPicker.tsx`，Props `{value:string; onChange:(abs:string)=>void; title?:string; triggerLabel?:string}`）+ `browseFs`（`src/api/client.ts`）+ `FsBrowseResult` type 已落地，本 plan 直接消费。
- **sonner toast**：调用 `import { toast } from "sonner"; toast.error(msg)`；测试用 `vi.spyOn(toast, "error")`。
- **commit message 风格**：`feat(web): 子项目3·扫描页 TaskN <摘要>`。
- **spec 文档**：`docs/superpowers/specs/2026-07-04-shannon-web-redesign-scan-design.md`（契约来源）。

---

## File Structure

**Create:**
- `packages/web/frontend/src/components/ScanFormFields.tsx` — 白盒/黑盒共享表单（Card + fieldset 分组 + 全 shadcn 控件 + FileSystemPicker 集成 + 校验/预览展示）

**Modify:**
- `packages/web/frontend/src/components/ui/sonner.tsx` — 去 next-themes 坏依赖，读 `document.documentElement.classList` 决定 theme
- `packages/web/frontend/src/App.tsx` — 挂 `<Toaster />`（DSF 漏挂）
- `packages/web/frontend/src/pages/ScanNewPage.tsx` — 重写顶层（Tabs + FormState + 校验/预览 + 续扫 Dialog + 提交 toast）
- `packages/web/frontend/src/pages/ScanNewPage.test.tsx` — 现有 8 断言调整选择器 + 新增断言

**不改**：后端任何文件；其他业务页；events.css；DSF 产物（除 sonner.tsx）；router.tsx（AppShell 已全局套）。

---

## Task 1: `<Toaster />` 挂载 + sonner.tsx 去 next-themes

**Files:**
- Modify: `packages/web/frontend/src/components/ui/sonner.tsx`
- Modify: `packages/web/frontend/src/App.tsx`
- Modify: `packages/web/frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: `applyTheme`（`@/lib/theme`）已把主题写到 `document.documentElement.classList`（"dark"|"light"）
- Produces: `<Toaster />` 组件可挂载（无 next-themes 依赖）；App 根挂载后全站 `toast.error/success` 通道可用

**背景**：DSF copy 的 sonner.tsx 用 `useTheme` from `next-themes`，但本项目主题走自有 `@/lib/theme`（非 next-themes ThemeProvider）。因 sonner.tsx 此前从未被 import 到树，DSF 测试未暴露这个坏依赖。本 task 修复 + 挂载。

- [ ] **Step 1: 写失败测试（App.test.tsx 加 Toaster 挂载断言）**

读 `packages/web/frontend/src/App.test.tsx` 现有结构，在末尾加一个 describe（保留现有测试）：

```tsx
import { Toaster } from "@/components/ui/sonner";

describe("App Toaster 挂载", () => {
  it("App 根挂 <Toaster />（toast 通道）", () => {
    const { container } = render(<App />);
    // sonner Toaster 渲染一个 section[aria-label] 到 body；查 Toaster 实例存在
    expect(screen.getByLabelText(/notifications/i)).toBeInTheDocument();
  });
});
```

> sonner `<Toaster />` 默认渲染 `<section aria-label="Notifications">`。若 App.test.tsx 现有 render App 的方式不同，按现有方式适配。import `render, screen` 沿用文件顶部现有 import。

- [ ] **Step 2: 运行测试确认失败**

Run:
```bash
cd packages/web/frontend && npx vitest run src/App.test.tsx
```
Expected: FAIL（App 未挂 Toaster → `getByLabelText(/notifications/i)` 找不到）。

- [ ] **Step 3: 改 sonner.tsx 去 next-themes**

Replace `packages/web/frontend/src/components/ui/sonner.tsx` 全文：
```tsx
"use client";

import { Toaster as Sonner } from "sonner";

type ToasterProps = React.ComponentProps<typeof Sonner>;

// 本项目主题走 @/lib/theme（applyTheme 写 document.documentElement.classList），
// 非 next-themes。Toaster 渲染时读一次 html class 决定深/浅（toast 是短弹窗，
// 切主题时通常不在屏，一次性读够用）。
function readTheme(): "dark" | "light" {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.classList.contains("light") ? "light" : "dark";
}

const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <Sonner
      theme={readTheme()}
      className="toaster group"
      toastOptions={{
        classNames: {
          toast:
            "group toast group-[.toaster]:bg-background group-[.toaster]:text-foreground group-[.toaster]:border-border group-[.toaster]:shadow-lg",
          description: "group-[.toast]:text-muted-foreground",
          actionButton:
            "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground",
          cancelButton:
            "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
        },
      }}
      {...props}
    />
  );
};

export { Toaster };
```

- [ ] **Step 4: 改 App.tsx 挂 Toaster**

Replace `packages/web/frontend/src/App.tsx` 全文：
```tsx
import { RouterProvider } from "react-router-dom";
import { router } from "./router";
import { Toaster } from "@/components/ui/sonner";

export default function App() {
  return (
    <>
      <RouterProvider router={router} />
      <Toaster />
    </>
  );
}
```

- [ ] **Step 5: 运行测试确认通过**

Run:
```bash
cd packages/web/frontend && npx vitest run src/App.test.tsx
```
Expected: PASS（含新 Toaster 断言 + 原有 App 测试）。

- [ ] **Step 6: Commit**

```bash
cd packages/web/frontend && git add src/components/ui/sonner.tsx src/App.tsx src/App.test.tsx
git commit -m "feat(web): 子项目3·扫描页 Task1 挂 Toaster + sonner 去 next-themes 坏依赖"
```

---

## Task 2: segmented → shadcn `<Tabs>` + FormState 跨 tab

**Files:**
- Modify: `packages/web/frontend/src/pages/ScanNewPage.tsx`
- Test: `packages/web/frontend/src/pages/ScanNewPage.test.tsx`（不改，验证现有 8 断言仍绿）

**Interfaces:**
- Consumes: `Tabs/TabsList/TabsTrigger/TabsContent` from `@/components/ui/tabs`
- Produces: `ScanNewPage` 顶层 Tabs 骨架（白盒/黑盒/联动）；`type` state 由 `onValueChange` 驱动；`FormState` 单 useState 跨 tab（不重置）

**关键**：现有测试 1/2/3 用 `getByRole("tab", { name: "黑盒" })` —— shadcn `TabsTrigger` 天然 `role="tab"` + `aria-selected`，无需改测试即可通过。

- [ ] **Step 1: 跑现有测试确认基线绿**

Run:
```bash
cd packages/web/frontend && npx vitest run src/pages/ScanNewPage.test.tsx
```
Expected: PASS（8 个现有断言）。

- [ ] **Step 2: 改 ScanNewPage 顶层 segmented → Tabs**

Modify `packages/web/frontend/src/pages/ScanNewPage.tsx`：

顶部 import 加：
```tsx
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
```

把 `return (...)` 内最外层 `<div className="page scan-page">` 里的 `<div className="segmented">...</div>` 块（含三个 `<button role="tab">`）替换为 `<Tabs>`，并把后续 `{type !== "correlation" ? (...) : (...)}` 内容包进对应 TabsContent：

```tsx
  return (
    <div className="page scan-page">
      <Tabs
        defaultValue="whitebox"
        onValueChange={(v) => setType(v as ScanType)}
        className="w-full"
      >
        <TabsList>
          <TabsTrigger value="whitebox">白盒</TabsTrigger>
          <TabsTrigger value="blackbox">黑盒</TabsTrigger>
          <TabsTrigger value="correlation">联动</TabsTrigger>
        </TabsList>

        <TabsContent value="whitebox">
          {/* 原 {type !== "correlation" ? 白盒表单 : null} 的白盒分支内容（type 固定为 whitebox） */}
          {/* 临时：直接渲染原 form-area，保留现有行为；type 此时由 Tabs 驱动但内容仍按 type!=="correlation" 判定 */}
        </TabsContent>
        <TabsContent value="blackbox">
          {/* 黑盒分支：同白盒 form-area 结构，type=blackbox 多 reuse_latest 字段 */}
        </TabsContent>
        <TabsContent value="correlation">
          {/* 联动分支：原 correlation-area + YamlEditor */}
        </TabsContent>
      </Tabs>

      {err && <div className="err-banner ev-error">{err}</div>}
      <button className="submit-btn" onClick={submit} disabled={!!conflict}>
        开始扫描 ▶
      </button>
      <div className="trace">→ 202 → 跳 /p/{"{ws}"}/live · 错误：400(Temporal)/409(并发)/422(yaml)</div>
    </div>
  );
```

> **本 task 最小改动策略**：现有 `type !== "correlation" ? <form-area> : <correlation-area>` 三元，由于白盒/黑盒共用同一 form-area（仅 blackbox 多 reuse 字段，靠 `type === "blackbox"` 内联判断），可在三个 TabsContent 内分别渲染：
> - `value="whitebox"`：把原 form-area JSX 复制一份（其内 `type === "blackbox"` 分支不触发，但保留以减少改动）
> - `value="blackbox"`：同上复制（`type === "blackbox"` 分支触发 reuse_latest）
> - `value="correlation"`：原 correlation-area JSX
>
> 为避免重复 200 行，**更简洁的做法**：抽一个内部函数 `renderForm()` 返回 form-area JSX（依赖闭包 `type`），白盒/黑盒 TabsContent 都调 `renderForm()`，联动调原 correlation。但 `type` 此时由 Tabs `onValueChange` 驱动 —— 白盒 tab 打开时 type="whitebox"，黑盒 tab 打开时 type="blackbox"，`renderForm()` 读 `type` 自然正确。
>
> 实际改法（推荐）：保留原 `{type !== "correlation" ? <FormArea/> : <CorrelationArea/>}` 整体 JSX 不动，只把外层 `<div className="segmented">` 换成 `<Tabs>` + TabsList，TabsContent 三个都渲染**同一份** `{type !== "correlation" ? ... : ...}`（因为 type 由 onValueChange 实时驱动，切到联动时 type="correlation" 自动渲染联动分支）：

```tsx
      <Tabs defaultValue="whitebox" onValueChange={(v) => setType(v as ScanType)} className="w-full">
        <TabsList>
          <TabsTrigger value="whitebox">白盒</TabsTrigger>
          <TabsTrigger value="blackbox">黑盒</TabsTrigger>
          <TabsTrigger value="correlation">联动</TabsTrigger>
        </TabsList>
        <TabsContent value={type} forceMount>
          {type !== "correlation" ? (
            <div className="form-area">
              {/* ...原 form-area 内容完全保留，包括 type === "blackbox" 的 reuse_latest 块... */}
            </div>
          ) : (
            <div className="correlation-area">
              {/* ...原 correlation-area 内容完全保留... */}
            </div>
          )}
        </TabsContent>
      </Tabs>
```

> `forceMount` + `value={type}`：三个 tab 共用一个 TabsContent，内容由 `type` 三元驱动（Tabs 的 `onValueChange` 改 type → 内容重渲染）。`forceMount` 防止 Tabs 卸载未激活 tab（本设计内容跟 type 走，不需各 tab 独立 mount）。**注意 hidden**：forceMount 时未激活 tab 内容仍可见——但因只有一个 TabsContent 且 value 跟 type 走，激活的总是当前 type，无残留。若 Radix 强制隐藏非激活，改用三个 TabsContent 各自渲染对应分支（不依赖 forceMount）。

> **替代（更稳，推荐实际采用）**：用三个独立 TabsContent，各渲染对应分支（白盒/黑盒共享 form-area 的 `type` 由父 `type` state 定，联动单独）。这样无需 forceMount，且 Radix 自动隐藏非激活。本 step 实现如下：

最终实现（采用此版）：
```tsx
      <Tabs defaultValue="whitebox" onValueChange={(v) => setType(v as ScanType)} className="w-full">
        <TabsList>
          <TabsTrigger value="whitebox">白盒</TabsTrigger>
          <TabsTrigger value="blackbox">黑盒</TabsTrigger>
          <TabsTrigger value="correlation">联动</TabsTrigger>
        </TabsList>
        <TabsContent value="whitebox">
          <div className="form-area">{/* 原 form-area JSX，type 固定 whitebox 视角（reuse 块 type==="blackbox" 不触发） */}</div>
        </TabsContent>
        <TabsContent value="blackbox">
          <div className="form-area">{/* 同一份 form-area JSX；此时 type=blackbox，reuse 块触发 */}</div>
        </TabsContent>
        <TabsContent value="correlation">
          <div className="correlation-area">{/* 原 correlation-area JSX */}</div>
        </TabsContent>
      </Tabs>
```

> form-area JSX 重复问题：本 task 先内联复制（白盒/黑盒各一份相同 JSX，仅靠 `type` 判断 reuse），**Task 3 抽取 `<ScanFormFields>` 消除重复**。本 task 聚焦 Tabs 骨架 + 测试绿。

- [ ] **Step 3: 跑测试确认全绿（现有 8 断言）**

Run:
```bash
cd packages/web/frontend && npx vitest run src/pages/ScanNewPage.test.tsx
```
Expected: PASS（8 个断言全绿）。`getByRole("tab", { name: "黑盒" })` 命中 shadcn TabsTrigger。

- [ ] **Step 4: Commit**

```bash
cd packages/web/frontend && git add src/pages/ScanNewPage.tsx
git commit -m "feat(web): 子项目3·扫描页 Task2 segmented → shadcn Tabs（8 现有断言保持绿）"
```

---

## Task 3: 抽取 `<ScanFormFields>` + `<Card>` fieldset 分组 + 控件全换 shadcn

**Files:**
- Create: `packages/web/frontend/src/components/ScanFormFields.tsx`
- Modify: `packages/web/frontend/src/pages/ScanNewPage.tsx`
- Test: `packages/web/frontend/src/pages/ScanNewPage.test.tsx`（不改，验证现有 8 断言仍绿）

**Interfaces:**
- Consumes: `FormState`（from `ScanNewPage`，本 task 改为 `export`）；DSF shadcn Card/Input/Label/Checkbox/Select/Button
- Produces: `<ScanFormFields type f set conflict onConflictDismiss>` —— 白盒/黑盒共享表单，外层 `<Card>` + 三个 `<fieldset>`（代码来源 / 目标+命名 / 复用[仅黑盒]），全 shadcn 控件。Task 3 阶段 Props 最小，后续 task 扩。

- [ ] **Step 1: 导出 FormState type**

Modify `packages/web/frontend/src/pages/ScanNewPage.tsx`，把 `interface FormState { ... }` 改为 `export interface FormState { ... }`（供 ScanFormFields import）。

- [ ] **Step 2: 写 ScanFormFields 组件**

Create `packages/web/frontend/src/components/ScanFormFields.tsx`:
```tsx
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import type { FormState } from "../pages/ScanNewPage";

interface ScanFormFieldsProps {
  type: "whitebox" | "blackbox";
  f: FormState;
  set: (patch: Partial<FormState>) => void;
  conflict: string | null;            // wsName 冲突检测结果（Task 7 改 Dialog，Task 3 仍 inline）
  onConflictDismiss: () => void;      // inline 横幅"取消"清名
}

export function ScanFormFields({ type, f, set, conflict, onConflictDismiss }: ScanFormFieldsProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{type === "blackbox" ? "黑盒扫描" : "白盒扫描"}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">代码来源</legend>
          <div className="space-y-2">
            <Label htmlFor="sourceKind">来源类型</Label>
            <Select value={f.sourceKind} onValueChange={(v) => set({ sourceKind: v as "path" | "git" })}>
              <SelectTrigger id="sourceKind"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="path">本地路径</SelectItem>
                <SelectItem value="git">git URL</SelectItem>
              </SelectContent>
            </Select>
            <Label htmlFor="sourceValue">路径 / URL</Label>
            <Input
              id="sourceValue"
              value={f.sourceValue}
              onChange={(e) => set({ sourceValue: e.target.value })}
              placeholder={f.sourceKind === "path" ? "/root/code/foo" : "https://gitlab.example/foo.git"}
            />
          </div>
          {f.sourceKind === "git" && (
            <div className="space-y-2 git-extra">
              <div className="flex gap-2">
                <Input value={f.branch} onChange={(e) => set({ branch: e.target.value })} placeholder="分支(可选)" />
                <Input value={f.commit} onChange={(e) => set({ commit: e.target.value })} placeholder="commit(可选,优先)" />
              </div>
              <div className="flex items-center gap-2">
                <Checkbox id="forceReclone" checked={f.forceReclone} onCheckedChange={(v) => set({ forceReclone: !!v })} />
                <Label htmlFor="forceReclone">强制重新 clone</Label>
              </div>
            </div>
          )}
        </fieldset>

        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">扫描目标 + 命名</legend>
          <div className="space-y-2">
            <Label htmlFor="url">目标 URL</Label>
            <Input id="url" value={f.url} onChange={(e) => set({ url: e.target.value })} placeholder="http://example.com" />
          </div>
          <div className="space-y-2">
            <Label htmlFor="wsName">workspace 名</Label>
            <Input
              id="wsName"
              value={f.wsName}
              onChange={(e) => set({ wsName: e.target.value })}
              placeholder="空=自动 {repo}_{timestamp}"
            />
          </div>
        </fieldset>

        {type === "blackbox" && (
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">复用</legend>
            <div className="flex items-center gap-2">
              <Checkbox id="reuseLatest" checked={f.reuseLatest} onCheckedChange={(v) => set({ reuseLatest: !!v })} />
              <Label htmlFor="reuseLatest">复用最新白盒结果</Label>
            </div>
            <div className="trace">
              --latest 按 url 匹配；不勾选时后端传 --repo 显式 standalone，规避 CLI 软默认复用
            </div>
          </fieldset>
        )}

        {conflict && (
          <div className="confirm-dialog ev-warn">
            ⚠ workspace「{conflict}」已存在，CLI -w 语义=存在则恢复，将
            <b>断点续扫</b>（恢复已有进度）。
          </div>
        )}
      </CardContent>
    </Card>
  );
}
```

> Task 3 阶段：续扫仍 inline 横幅（保留"断点续扫"文字 → 现有测试 4 绿）；onConflictDismiss 暂保留 prop（Task 7 移 inline 时清）。本 step 横幅未放"取消/确认续扫"按钮（提交仍走外层 disabled={!!conflict}，保持现有行为），最小化改动。

- [ ] **Step 3: ScanNewPage 白盒/黑盒 TabsContent 改用 `<ScanFormFields>`**

Modify `packages/web/frontend/src/pages/ScanNewPage.tsx`：
- 顶部 import：`import { ScanFormFields } from "../components/ScanFormFields";`
- 删除 Step 2（Task 2）里白盒/黑盒 TabsContent 内联复制的 `<div className="form-area">...</div>` 块。
- 白盒 TabsContent 改为：
```tsx
<TabsContent value="whitebox">
  <ScanFormFields
    type="whitebox"
    f={f}
    set={set}
    conflict={conflict}
    onConflictDismiss={() => set({ wsName: "" })}
  />
</TabsContent>
```
- 黑盒 TabsContent 同上，`type="blackbox"`。
- 联动 TabsContent 把原 `<div className="correlation-area">` 套进 `<Card>`（spec §3 视觉对齐白盒/黑盒）：

```tsx
<TabsContent value="correlation">
  <Card>
    <CardHeader><CardTitle>联动扫描</CardTitle></CardHeader>
    <CardContent className="space-y-3">
      <YamlEditor value={f.yaml} onChange={(v) => set({ yaml: v })} onError={setYamlErr} />
      <div className="trace">{yamlErr ? `⚠ ${yamlErr}` : "yaml 合法"}</div>
    </CardContent>
  </Card>
</TabsContent>
```

> ScanNewPage 顶部补 import：`import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";`（`YamlEditor` import 现有已有）。`yamlErr`/`setYamlErr` 是 ScanNewPage 顶层现有 state，联动分支直接消费。

- [ ] **Step 4: 跑测试确认现有 8 断言全绿**

Run:
```bash
cd packages/web/frontend && npx vitest run src/pages/ScanNewPage.test.tsx
```
Expected: PASS（8 个）。关键：测试 1 `getByText(/代码来源/)` 命中 fieldset legend；测试 3 `getByText(/--latest/)`、`/standalone/` 命中 trace div；测试 4 `getByPlaceholderText(/自动/)` 命中 wsName Input + `getByText(/断点续扫/)` 命中 conflict 横幅；测试 5-8 点提交（提交按钮仍 `disabled={!!conflict}`，必填空时不 disabled——**注意**：Task 3 尚未加即时校验 disable，提交按钮仅 `disabled={!!conflict}`，必填空时仍可点击 → 测试 5-8 仍绿）。

> **关键**：Task 3 不改提交按钮 disabled 逻辑（保持 `disabled={!!conflict}`），即时校验 disable 在 Task 5 加。这样测试 5-8（必填空仍能点提交）在 Task 3 后仍绿。

- [ ] **Step 5: Commit**

```bash
cd packages/web/frontend && git add src/components/ScanFormFields.tsx src/pages/ScanNewPage.tsx
git commit -m "feat(web): 子项目3·扫描页 Task3 抽 ScanFormFields + Card/fieldset + 全控件 shadcn 化"
```

---

## Task 4: 集成 `<FileSystemPicker>`（sourceKind=path 时）

**Files:**
- Modify: `packages/web/frontend/src/components/ScanFormFields.tsx`
- Test: `packages/web/frontend/src/pages/ScanNewPage.test.tsx`（新增文件浏览器断言）

**Interfaces:**
- Consumes: `<FileSystemPicker value onChange title? triggerLabel?>`（子项目 2，`src/components/FileSystemPicker.tsx`）；MSW `/api/fs/browse`
- Produces: sourceKind=path 时 sourceValue Input 旁显「📁 浏览」trigger → 点击打开 `<FileSystemPicker>` 内部 Dialog → 选目录回填 `sourceValue`

- [ ] **Step 1: 写失败测试（新增 2 断言）**

在 `packages/web/frontend/src/pages/ScanNewPage.test.tsx` 末尾（`describe` 内最后）加：
```tsx
  it("path 时显「📁 浏览」trigger", () => {
    renderPage();
    expect(screen.getByRole("button", { name: /📁 浏览/ })).toBeInTheDocument();
  });

  it("点「📁 浏览」→ 打开文件浏览器 → 显目录 entry", async () => {
    server.use(
      http.get("/api/fs/browse", () =>
        HttpResponse.json({
          path: "/",
          parent: null,
          entries: [{ name: "code", type: "dir" }],
        }),
      ),
    );
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /📁 浏览/ }));
    // FileSystemPicker Dialog 打开（title 默认"选择代码目录"）
    await waitFor(() => expect(screen.getByText("选择代码目录")).toBeInTheDocument());
    expect(screen.getByText("code")).toBeInTheDocument();
  });
```

- [ ] **Step 2: 运行确认失败**

Run:
```bash
cd packages/web/frontend && npx vitest run src/pages/ScanNewPage.test.tsx
```
Expected: FAIL（path 时无「📁 浏览」按钮）。

- [ ] **Step 3: ScanFormFields sourceValue 区块集成 FileSystemPicker**

Modify `packages/web/frontend/src/components/ScanFormFields.tsx`：
- 顶部 import：`import { FileSystemPicker } from "./FileSystemPicker";`
- 把 sourceValue 区块的 `<Input id="sourceValue" .../>` 改为 Input + FileSystemPicker 并排：
```tsx
            <Label htmlFor="sourceValue">路径 / URL</Label>
            <div className="flex gap-2">
              <Input
                id="sourceValue"
                value={f.sourceValue}
                onChange={(e) => set({ sourceValue: e.target.value })}
                placeholder={f.sourceKind === "path" ? "/root/code/foo" : "https://gitlab.example/foo.git"}
              />
              {f.sourceKind === "path" && (
                <FileSystemPicker
                  value={f.sourceValue}
                  onChange={(v) => set({ sourceValue: v })}
                  triggerLabel="📁 浏览"
                />
              )}
            </div>
```

- [ ] **Step 4: 运行确认通过**

Run:
```bash
cd packages/web/frontend && npx vitest run src/pages/ScanNewPage.test.tsx
```
Expected: PASS（含 2 新断言 + 原 8 断言）。

- [ ] **Step 5: Commit**

```bash
cd packages/web/frontend && git add src/components/ScanFormFields.tsx src/pages/ScanNewPage.test.tsx
git commit -m "feat(web): 子项目3·扫描页 Task4 集成 FileSystemPicker（path 时选目录）"
```

---

## Task 5: 即时校验 + 提交 disable

**Files:**
- Modify: `packages/web/frontend/src/pages/ScanNewPage.tsx`
- Modify: `packages/web/frontend/src/components/ScanFormFields.tsx`
- Test: `packages/web/frontend/src/pages/ScanNewPage.test.tsx`（改测试 5-8 前置 fillValid + 新增校验断言）

**Interfaces:**
- Consumes: `apiGet`/`Workspace`（现有，wsName 冲突检测）
- Produces: ScanNewPage 父层算 `validity`（sourceValue/url/yaml 错误消息）+ `loadingConflict`（debounce 300ms）；提交按钮 `disabled={!isValid || submitting}`；ScanFormFields Props 扩 `validity` + `loadingConflict` 显字段级红字 / 黄字

- [ ] **Step 1: ScanNewPage 加校验纯函数 + validity/loadingConflict/isValid**

Modify `packages/web/frontend/src/pages/ScanNewPage.tsx`：

文件顶部（`renderError` 下方）加纯函数：
```tsx
function validateSourceValue(kind: "path" | "git", v: string): string | null {
  if (!v.trim()) return "代码来源不能为空";
  if (kind === "path") {
    return /^(\/|[A-Za-z]:[\\/])/.test(v) ? null : "本地路径需为绝对路径（如 /root/code/foo）";
  }
  return /^(https?:|git@|ssh:)/.test(v) ? null : "需为 git URL（https:// / git@ / ssh:）";
}

function validateUrl(v: string): string | null {
  if (!v.trim()) return "目标 URL 不能为空";
  return /^https?:\/\//.test(v) ? null : "目标 URL 需以 http(s):// 开头";
}
```

`ScanNewPage` 函数体内（现有 `const [conflict, setConflict] ...` + `useEffect` wsName 检测）改为 debounce + loadingConflict：
```tsx
  const [conflict, setConflict] = useState<string | null>(null);
  const [loadingConflict, setLoadingConflict] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!f.wsName) {
      setConflict(null);
      setLoadingConflict(false);
      return;
    }
    setLoadingConflict(true);
    const t = setTimeout(() => {
      apiGet<Workspace[]>("/workspaces").then((ws) => {
        setConflict(ws.some((w) => w.name === f.wsName) ? f.wsName : null);
      }).finally(() => setLoadingConflict(false));
    }, 300);
    return () => clearTimeout(t);
  }, [f.wsName]);

  const sourceValueErr = validateSourceValue(f.sourceKind, f.sourceValue);
  const urlErr = validateUrl(f.url);
  const isCorrelation = type === "correlation";
  const isValid =
    !sourceValueErr &&
    !urlErr &&
    !loadingConflict &&
    !(isCorrelation && yamlErr);
```

提交按钮（return 内最外层）改为：
```tsx
      <button className="submit-btn" onClick={submit} disabled={!isValid || submitting || !!conflict}>
        开始扫描 ▶
      </button>
```

> Task 5 阶段提交按钮仍保留 `|| !!conflict`（Task 7 续扫 Dialog 时移除 conflict disable，改为冲突点提交才弹）。本 task 聚焦必填校验 disable。

- [ ] **Step 2: ScanFormFields Props 扩 validity + loadingConflict，显字段级反馈**

Modify `packages/web/frontend/src/components/ScanFormFields.tsx`：
- Props interface 加：
```tsx
  sourceValueErr: string | null;
  urlErr: string | null;
  loadingConflict: boolean;
```
- sourceValue Input 下方加红字 + wsName Input 下方加黄字（冲突/loading）：
```tsx
            <div className="flex gap-2">
              <Input ... />
              {f.sourceKind === "path" && (<FileSystemPicker ... />)}
            </div>
            {sourceValueErr && <div className="text-red text-xs">{sourceValueErr}</div>}
```
```tsx
            <Input id="wsName" ... />
            {loadingConflict && <div className="ev-warn text-xs">检测重名中…</div>}
            {conflict && !loadingConflict && (
              <div className="ev-warn text-xs">workspace「{conflict}」已存在 → 将断点续扫</div>
            )}
```
- 把 ScanNewPage 调用处补传 props：
```tsx
<ScanFormFields
  type="whitebox"
  f={f}
  set={set}
  conflict={conflict}
  onConflictDismiss={() => set({ wsName: "" })}
  sourceValueErr={sourceValueErr}
  urlErr={urlErr}
  loadingConflict={loadingConflict}
/>
```
- url Input 下方加 `{urlErr && <div className="text-red text-xs">{urlErr}</div>}`。

> 移除原 conflict 大横幅（Task 3 内的 `<div className="confirm-dialog ev-warn">⚠ ... 断点续扫 ...</div>`）—— 改为 wsName 下的紧凑黄字提示。**注意**：现有测试 4 `getByText(/断点续扫/)` 仍需命中 → 保留黄字含"断点续扫"四字（上面 `将断点续扫` 含）。绿。

- [ ] **Step 3: 改测试 5-8 前置 fillValid + 新增校验断言**

Modify `packages/web/frontend/src/pages/ScanNewPage.test.tsx`：

顶部（`describe` 外）加辅助：
```tsx
function fillValid() {
  // path 默认 + 绝对路径 + 合法 URL
  fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "/root/code/foo" } });
  fireEvent.change(screen.getByPlaceholderText(/http:\/\/example\.com/), { target: { value: "http://example.com" } });
}
```

测试 5-8（提交 400/409/422/422无detail）每个在 `renderPage()` 后、点提交前加 `fillValid();`：
```tsx
  it("提交 400 → 提示 Temporal 未就绪", async () => {
    server.use(http.post("/api/scan", () => new HttpResponse(null, { status: 400 })));
    renderPage();
    fillValid();
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(screen.getByText(/Temporal/i)).toBeInTheDocument());
  });
```
（其余 409/422/422无detail 同样加 `fillValid();`）

> Task 5 阶段错误仍是 inline err-banner（Task 7 改 toast），故测试 5-8 仍用 `getByText` 断言，绿。

新增断言（`describe` 末尾）：
```tsx
  it("必填空 → 提交 disabled；填齐 → enabled", () => {
    renderPage();
    expect(screen.getByRole("button", { name: /开始扫描/ })).toBeDisabled();
    fillValid();
    expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled();
  });

  it("path 非绝对 → 红字 + 提交 disabled", () => {
    renderPage();
    fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "relative/path" } });
    expect(screen.getByText(/需为绝对路径/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /开始扫描/ })).toBeDisabled();
  });
```

- [ ] **Step 4: 运行确认通过**

Run:
```bash
cd packages/web/frontend && npx vitest run src/pages/ScanNewPage.test.tsx
```
Expected: PASS（8 现有 + 1 FileSystemPicker 时 Task4 加的 2 + 本 task 2 = 全绿）。

> 若测试 4（冲突 inline）因 loadingConflict debounce 时序 flaky：测试用 `waitFor(() => expect(screen.getByText(/断点续扫/)).toBeInTheDocument())` 已等 debounce，绿。

- [ ] **Step 5: Commit**

```bash
cd packages/web/frontend && git add src/pages/ScanNewPage.tsx src/components/ScanFormFields.tsx src/pages/ScanNewPage.test.tsx
git commit -m "feat(web): 子项目3·扫描页 Task5 即时校验 + 提交 disable（必填/格式/冲突 debounce）"
```

---

## Task 6: workspace 名预览

**Files:**
- Modify: `packages/web/frontend/src/pages/ScanNewPage.tsx`
- Modify: `packages/web/frontend/src/components/ScanFormFields.tsx`
- Test: `packages/web/frontend/src/pages/ScanNewPage.test.tsx`（新增预览断言）

**Interfaces:**
- Consumes: `useMemo`（react）；`new Date()`（浏览器运行时可用）
- Produces: ScanNewPage 父层算 `derivedName`（basename + `_{YYYYMMDD-HHMMSS}`）；ScanFormFields Props 扩 `derivedName`，wsName 空 + derivedName 非空时显预览 trace

- [ ] **Step 1: 写失败测试**

`packages/web/frontend/src/pages/ScanNewPage.test.tsx` `describe` 末尾加：
```tsx
  it("wsName 空 + sourceValue 填 → 显预览名（basename + _YYYYMMDD-HHMMSS）", () => {
    renderPage();
    fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "/root/code/foo" } });
    expect(screen.getByText(/预览名：foo_\d{8}-\d{6}/)).toBeInTheDocument();
  });

  it("wsName 填了 → 不显预览", () => {
    renderPage();
    fireEvent.change(screen.getByPlaceholderText(/root\/code\/foo/), { target: { value: "/root/code/foo" } });
    fireEvent.change(screen.getByPlaceholderText(/自动/), { target: { value: "myname" } });
    expect(screen.queryByText(/预览名/)).toBeNull();
  });
```

- [ ] **Step 2: 运行确认失败**

Run:
```bash
cd packages/web/frontend && npx vitest run src/pages/ScanNewPage.test.tsx
```
Expected: FAIL（无预览名）。

- [ ] **Step 3: ScanNewPage 加 deriveName 纯函数 + useMemo**

Modify `packages/web/frontend/src/pages/ScanNewPage.tsx`：

import 加 `useMemo`（`import { useEffect, useState, useMemo } from "react";`）。

文件顶部（`validateUrl` 下方）加：
```tsx
function deriveName(kind: "path" | "git", v: string): string {
  const trimmed = v.trim();
  if (!trimmed) return "";
  let base = "";
  if (kind === "path") {
    base = trimmed.replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? "";
  } else {
    base = trimmed.replace(/\.git$/, "").split(/[\/:]/).pop() ?? "";
  }
  if (!base) return "";
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const ts = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  return `${base}_${ts}`;
}
```

`ScanNewPage` 函数体内加：
```tsx
  const derivedName = useMemo(
    () => (type === "correlation" ? "" : deriveName(f.sourceKind, f.sourceValue)),
    [type, f.sourceKind, f.sourceValue],
  );
```

ScanFormFields 调用处加 `derivedName={derivedName}`：
```tsx
<ScanFormFields
  type="whitebox"
  f={f}
  set={set}
  conflict={conflict}
  onConflictDismiss={() => set({ wsName: "" })}
  sourceValueErr={sourceValueErr}
  urlErr={urlErr}
  loadingConflict={loadingConflict}
  derivedName={derivedName}
/>
```

- [ ] **Step 4: ScanFormFields 显预览**

Modify `packages/web/frontend/src/components/ScanFormFields.tsx`：
- Props 加 `derivedName: string;`
- wsName Input 区块（黄字下方）加：
```tsx
            {!f.wsName && derivedName && (
              <div className="trace">预览名：{derivedName}（预览，实际由后端生成）</div>
            )}
```

- [ ] **Step 5: 运行确认通过**

Run:
```bash
cd packages/web/frontend && npx vitest run src/pages/ScanNewPage.test.tsx
```
Expected: PASS（含 2 新预览断言）。

- [ ] **Step 6: Commit**

```bash
cd packages/web/frontend && git add src/pages/ScanNewPage.tsx src/components/ScanFormFields.tsx src/pages/ScanNewPage.test.tsx
git commit -m "feat(web): 子项目3·扫描页 Task6 workspace 名预览（前端推算 basename+时间戳）"
```

---

## Task 7: 续扫 `<Dialog>` + toast 错误

**Files:**
- Modify: `packages/web/frontend/src/pages/ScanNewPage.tsx`
- Modify: `packages/web/frontend/src/components/ScanFormFields.tsx`
- Test: `packages/web/frontend/src/pages/ScanNewPage.test.tsx`（改测试 4 续扫 Dialog + 测试 5-8 错误 toast）

**Interfaces:**
- Consumes: `Dialog/DialogContent/DialogHeader/DialogTitle/DialogDescription/DialogFooter` from `@/components/ui/dialog`；`Button` from `@/components/ui/button`；`toast` from `sonner`
- Produces: 续扫确认改为受控 `<Dialog>`（冲突时 `onSubmit` 弹 Dialog，禁外点）；提交错误改 `toast.error(renderError(e))`；移除 inline `.err-banner` + conflict disable

- [ ] **Step 1: ScanNewPage 改 toast 错误 + onSubmit 弹 Dialog**

Modify `packages/web/frontend/src/pages/ScanNewPage.tsx`：

import 加：
```tsx
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
```

`submit` 函数重命名为 `doSubmit` + 加 submitting + 错误改 toast：
```tsx
  const [confirmOpen, setConfirmOpen] = useState(false);

  async function doSubmit() {
    if (type === "correlation" && yamlErr) {
      toast.error("yaml 有错，无法运行");
      return;
    }
    try {
      setSubmitting(true);
      const r = await apiPost<ScanResponse>("/scan", buildBody(type, f));
      nav(`/p/${r.workspace}/live`);
    } catch (e) {
      if (e instanceof ApiError) toast.error(renderError(e));
    } finally {
      setSubmitting(false);
    }
  }

  function onSubmit() {
    if (conflict) {
      setConfirmOpen(true);
      return;
    }
    void doSubmit();
  }
```

> 移除原 `const [err, setErr] = useState("")` + `setErr(...)` 用法（错误走 toast，不再 inline）。

return 内：提交按钮 + 续扫 Dialog：
```tsx
      <Button className="submit-btn" onClick={onSubmit} disabled={!isValid || submitting}>
        开始扫描 ▶
      </Button>
      <div className="trace">→ 202 → 跳 /p/{"{ws}"}/live · 错误：400(Temporal)/409(并发)/422(yaml)</div>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent onInteractOutside={(e) => e.preventDefault()}>
          <DialogHeader>
            <DialogTitle>断点续扫确认</DialogTitle>
            <DialogDescription>
              workspace「{conflict ?? ""}」已存在。CLI -w 语义=存在则恢复，将断点续扫（恢复已有进度）。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => { set({ wsName: "" }); setConfirmOpen(false); }}>
              取消（清空名）
            </Button>
            <Button onClick={() => { setConfirmOpen(false); void doSubmit(); }}>
              确认续扫
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
```

> 移除原 `{err && <div className="err-banner ev-error">{err}</div>}`。提交按钮 `disabled={!isValid || submitting}`（移除 `|| !!conflict`——冲突时点提交弹 Dialog，不再 disable）。

- [ ] **Step 2: ScanFormFields 移除 conflict 黄字（已移 Dialog）**

Modify `packages/web/frontend/src/components/ScanFormFields.tsx`：删除 wsName 下的 `conflict && !loadingConflict` 黄字块（`workspace「{conflict}」已存在 → 将断点续扫`）。保留 `loadingConflict` 黄字（"检测重名中…"）。`conflict`/`onConflictDismiss` Props 可保留（无害）或移除——为最小改动保留 prop 定义不删，仅删消费的 JSX 块。

> 现有测试 4 `getByText(/断点续扫/)` 会因黄字移除断言失效——下一步改测试 4 为 Dialog 断言。

- [ ] **Step 3: 改测试 4（续扫 Dialog）+ 测试 5-8（toast）**

Modify `packages/web/frontend/src/pages/ScanNewPage.test.tsx`：

import 加：
```tsx
import { toast } from "sonner";
```

`afterEach` 内加 `vi.restoreAllMocks();`（在现有 `server.resetHandlers(); cleanup();` 后）。

测试 4 改为（填名 + 点提交 → Dialog）：
```tsx
  it("workspace 名冲突 + 点提交 → 弹断点续扫 Dialog", async () => {
    renderPage();
    fillValid();
    fireEvent.change(screen.getByPlaceholderText(/自动/), { target: { value: "existing-ws" } });
    // 等 debounce 冲突检测完
    await waitFor(() => expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    const dlg = await waitFor(() => screen.getByRole("dialog"));
    expect(dlg.textContent).toMatch(/断点续扫/);
    expect(dlg.textContent).toContain("existing-ws");
  });
```

测试 5-8 改为 spy toast.error（每个测试内 spyOn）：
```tsx
  it("提交 400 → toast 提示 Temporal 未就绪", async () => {
    server.use(http.post("/api/scan", () => new HttpResponse(null, { status: 400 })));
    const spy = vi.spyOn(toast, "error");
    renderPage();
    fillValid();
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(expect.stringMatching(/Temporal/i)));
  });

  it("提交 409 → toast 并发扫描超限", async () => {
    server.use(http.post("/api/scan", () => new HttpResponse(null, { status: 409 })));
    const spy = vi.spyOn(toast, "error");
    renderPage();
    fillValid();
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(expect.stringMatching(/并发扫描超限/)));
  });

  it("提交 422 → toast yaml 校验失败（友好消息，不含原始 JSON）", async () => {
    server.use(
      http.post("/api/scan", () =>
        HttpResponse.json(
          { detail: [{ loc: ["body", "config_yaml"], msg: "repo url required", type: "value_error" }] },
          { status: 422 },
        ),
      ),
    );
    const spy = vi.spyOn(toast, "error");
    renderPage();
    fillValid();
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    const arg = await waitFor(() => {
      expect(spy).toHaveBeenCalled();
      return (spy.mock.calls[0] as string[])[0];
    });
    expect(arg).toContain("yaml 校验失败");
    expect(arg).toContain("repo url required");
    expect(arg).not.toContain("value_error");
  });

  it("422 无 detail → toast 回退纯标签", async () => {
    server.use(
      http.post("/api/scan", () => HttpResponse.json({ something: "else" }, { status: 422 })),
    );
    const spy = vi.spyOn(toast, "error");
    renderPage();
    fillValid();
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const arg = (spy.mock.calls[0] as string[])[0];
    expect(arg).toContain("yaml 校验失败");
    expect(arg).not.toContain("{");
  });
```

新增断言（续扫 Dialog 取消/确认）：
```tsx
  it("续扫 Dialog：取消 → 清空 wsName", async () => {
    renderPage();
    fillValid();
    fireEvent.change(screen.getByPlaceholderText(/自动/), { target: { value: "existing-ws" } }));
    await waitFor(() => expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => screen.getByRole("dialog"));
    fireEvent.click(screen.getByRole("button", { name: /取消/ }));
    await waitFor(() => expect((screen.getByPlaceholderText(/自动/) as HTMLInputElement).value).toBe(""));
  });

  it("续扫 Dialog：确认续扫 → 提交 /scan 202", async () => {
    server.use(
      http.post("/api/scan", () => HttpResponse.json({ workspace: "existing-ws" }, { status: 202 })),
    );
    const spy = vi.spyOn(toast, "error");
    renderPage();
    fillValid();
    fireEvent.change(screen.getByPlaceholderText(/自动/), { target: { value: "existing-ws" } }));
    await waitFor(() => expect(screen.getByRole("button", { name: /开始扫描/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /开始扫描/ }));
    await waitFor(() => screen.getByRole("dialog"));
    fireEvent.click(screen.getByRole("button", { name: /确认续扫/ }));
    // 提交成功 → nav（useNavigate mock 验证）或无 toast.error
    await waitFor(() => expect(spy).not.toHaveBeenCalled());
  });
```

- [ ] **Step 4: 运行确认通过**

Run:
```bash
cd packages/web/frontend && npx vitest run src/pages/ScanNewPage.test.tsx
```
Expected: PASS（含改后的测试 4-8 + 新增续扫 Dialog 断言）。

> 若 toast.error spy 因 sonner 内部实现（toast 是对象，spyOn 需可写属性）失败：改用 `vi.mock("sonner", () => ({ toast: { error: vi.fn() } }))` 顶部 mock + 直接 `expect(toast.error).toHaveBeenCalledWith(...)`。两种方式按实际选其一，SpyOn 优先。

- [ ] **Step 5: Commit**

```bash
cd packages/web/frontend && git add src/pages/ScanNewPage.tsx src/components/ScanFormFields.tsx src/pages/ScanNewPage.test.tsx
git commit -m "feat(web): 子项目3·扫描页 Task7 续扫 Dialog + toast 错误（移 inline banner/conflict disable）"
```

---

## Task 8: 冒烟回归 + tsc

**Files:**（无新文件，全量验证）

- [ ] **Step 1: 全套前端测试 + tsc**

Run:
```bash
cd packages/web/frontend && npx vitest run
cd packages/web/frontend && npx tsc --noEmit
```
Expected: 全绿（ScanNewPage 全套 + DSF 测试 + 子项目 2 列表页测试未回归）；tsc 0 error。

- [ ] **Step 2: 手动冒烟（dev server）**

Run（后台或单独终端）:
```bash
cd packages/web/frontend && npx vite dev
```
人手验证（`/scan/new`）：
1. 三 Tabs（白盒/黑盒/联动）切换流畅，切 tab 字段不丢。
2. path 时「📁 浏览」打开文件浏览器 → 选目录回填；git 时无 trigger。
3. 必填空 → 提交 disabled；path 非绝对 → 红字；填齐 → enabled。
4. wsName 空 + path 填 → 显预览名；填 wsName → 预览消失。
5. wsName 冲突 + 点提交 → 续扫 Dialog；取消清名 / 确认续扫提交。
6. 提交 400/409/422 → toast 友好消息（需后端 + Temporal 跑起；无后端时提交会 network error，验证 toast 通道即可：注释里临时改 catch 强制 toast 一条验证视觉）。
7. 主题切换（TopBar ThemeToggle）→ toast 视觉跟随（弹一条验证深/浅）。

- [ ] **Step 3: 终态 Commit（如有手动冒烟发现的微调）**

```bash
cd packages/web/frontend && git add -A
git commit -m "feat(web): 子项目3·扫描页 Task8 冒烟回归收尾" || echo "无改动则跳过"
```

---

## Self-Review（plan 自审记录）

**Spec 覆盖**：
- spec §1（Tabs + FormState 跨 tab）→ Task 2 ✓
- spec §2.1-2.3（Card/fieldset + 控件全换 + FileSystemPicker）→ Task 3 + Task 4 ✓
- spec §2.4（即时校验 + 冲突 debounce + isValid）→ Task 5 ✓
- spec §2.5（workspace 名预览）→ Task 6 ✓
- spec §3（联动 tab YamlEditor 套 Card）→ Task 3 Step 3 联动 TabsContent 套 `<Card>` ✓
- spec §4.1（续扫 Dialog 禁外点）→ Task 7 ✓（`onInteractOutside preventDefault`）
- spec §4.2（toast 错误 renderError 保留）→ Task 7 ✓
- spec §4.3（Toaster 挂载 + sonner 去 next-themes）→ Task 1 ✓
- spec §5（不改后端）→ 全 plan 无后端改动 ✓
- spec §6（测试 + 现有 8 断言 + 新增）→ Task 2/3/5/7 调断言 + Task 4/6 新增 ✓
- spec §7（任务拆解 9 步）→ 本 plan 8 task 覆盖（spec 9 步合并 Task1=Toaster / Task2=Tabs / Task3=抽组件+Card+控件(spec③④合并) / Task4=FileSystemPicker / Task5=校验 / Task6=预览 / Task7=Dialog+toast / Task8=冒烟）✓

**类型一致性**：`ScanFormFields` Props 跨 task 增量扩展（Task 3 基础 → Task 5 加 sourceValueErr/urlErr/loadingConflict → Task 6 加 derivedName），各 task Step 显式列 Props 扩展 + 调用处补传，签名一致。`FormState` Task 3 导出，后续 task 复用。

**占位符**：无 TBD/TODO；每步代码完整。

**已知实现注意点**：
1. shadcn Select 在 testing-library 里操作需 `pointerDown`/`keyDown`——本 plan 测试避免直接操作 Select 切 sourceKind（默认 path 即可测多数断言），减少 Select 交互复杂度。
2. toast.error spy：`vi.spyOn(toast, "error")` 若 sonner 版本 toast 对象属性不可写，回退 `vi.mock("sonner", ...)`（Task 7 Step 4 注）。
3. 续扫 Dialog `screen.getByRole("dialog")` 依赖 Radix Dialog 渲染 role="dialog"——shadcn Dialog 基础即 Radix，确认 ✓。

