# 子项目 3 · 扫描页重做

> 上位 spec：`docs/superpowers/specs/2026-07-04-shannon-web-redesign-design.md`（IA / 视觉 / 四条 shadcn 约束 / 子项目分解 / 迁移策略 / 跨子项目约束）。DSF（子项目 1）已落地：shadcn 组件库（`@/components/ui/*` 含 button/card/input/select/checkbox/label/dialog/tabs/textarea/sonner/skeleton…）、`<AppShell>`+`<TopBar>` 已在 `router.tsx` 全局套、双主题 token 层、`cn()`/`theme` lib 就绪。子项目 2（列表页 + 文件浏览器）spec/plan 已写：`<FileSystemPicker>` 组件 + `GET /api/fs/browse` + `DELETE /api/workspaces/{ws}`。本子 spec 聚焦 `ScanNewPage` 重做——shadcn `<Tabs>` + `<Card>` 分组 + 全控件替换 + 文件浏览器集成 + 即时校验 + workspace 名预览 + 续扫 `<Dialog>` + toast 错误；**纯前端，无后端改动**。

## 范围与完成定义

**做**：
1. `ScanNewPage` segmented → shadcn `<Tabs>`（白盒/黑盒/联动），`onValueChange` 驱动 `type`。
2. 白盒/黑盒表单：单 `<Card>` + fieldset 分组（代码来源 / 目标 + 命名），控件全换 shadcn `<Input>`/`<Select>`/`<Checkbox>`/`<Label>`/`<Button>`。
3. `sourceKind=path` 时集成子项目 2 `<FileSystemPicker>`。
4. 即时校验：path / git URL / URL 格式 + wsName 冲突 debounce + yaml → 提交按钮按 `isValid` disable。
5. workspace 名预览（前端推算 basename + 本地时间戳，标注"预览"）。
6. 续扫确认 inline 横幅 → shadcn `<Dialog>`（冲突时点提交才弹）。
7. 错误反馈 inline banner → toast（sonner），`renderError` 逻辑保留。
8. App 根挂 `<Toaster />`（DSF 装了组件但漏挂到树，本子项目补）。
9. `<ScanFormFields>` 复用组件抽取（白盒/黑盒共享，blackbox 多 `reuse_latest_whitebox` 字段）。

**不做**：
- 后端任何改动（`/scan` POST、`/workspaces` GET 契约稳定；workspace 名预览前端推算，不新增端点）。
- 其他业务页（列表页子项目 2 / 详情 5 tab 子项目 4 / Dashboard + 设置子项目 5）内部重做。
- 最近扫描模板 / 预设保存（超出上位 spec §5，留子项目 5 或独立增强）。
- `events.css` 移除（迁移期保留，仅本页内部样式改 Tailwind；其他页仍消费 `.ledger`/`.trace` 等）。

**完成定义**：
- 白盒/黑盒/联动三 Tabs 切换正常；切 tab 已填字段不丢（`FormState` 单 state 跨 tab）。
- `path` 时显「📁 浏览」trigger → 点击 → `<FileSystemPicker>` Dialog 打开（MSW mock `/fs/browse`）→ 选目录回填 `sourceValue`；`git` 时无 trigger。
- 即时校验：path 空/非绝对、git URL 格式错、URL 格式错 → Input 下方红字 + 提交 disabled；wsName 冲突 → 黄字"已存在→将续扫"。
- wsName 空 + sourceValue 填 → 显预览名（basename + `_YYYYMMDD-HHMMSS`，标注"预览"）。
- wsName 冲突 + 点提交 → `<Dialog>` 二次确认；取消清空名，确认续扫真提交。
- 提交 400/409/422/其他 → toast 对应友好消息（`renderError` 保留）；提交成功 → `nav /p/{ws}/live`（不变）。
- 现有 8 行为断言全绿（选择器调整为 shadcn）+ 新增断言全绿。
- DSF 测试 + 列表页测试（子项目 2）未回归。

---

## 1. 页面骨架 + Tabs

### 1.1 Tabs 结构
`src/pages/ScanNewPage.tsx` 重写顶层：
```tsx
<Tabs defaultValue="whitebox" onValueChange={(v) => setType(v as ScanType)}>
  <TabsList>
    <TabsTrigger value="whitebox">白盒</TabsTrigger>
    <TabsTrigger value="blackbox">黑盒</TabsTrigger>
    <TabsTrigger value="correlation">联动</TabsTrigger>
  </TabsList>
  <TabsContent value="whitebox"><ScanFormFields type="whitebox" {...props} /></TabsContent>
  <TabsContent value="blackbox"><ScanFormFields type="blackbox" {...props} /></TabsContent>
  <TabsContent value="correlation"><CorrelationForm {...props} /></TabsContent>
</Tabs>
```
`type` state 由 `onValueChange` 驱动（初值 `"whitebox"` 与 `defaultValue` 对齐）。

### 1.2 FormState 跨 tab 保留
`FormState`（`sourceKind`/`sourceValue`/`branch`/`commit`/`forceReclone`/`url`/`wsName`/`reuseLatest`/`yaml`）单一 `useState` 提在 `ScanNewPage` 顶层，props 下传给 `ScanFormFields`/`CorrelationForm`。切 tab 只换 `type`，不重置 `FormState` → 用户在白盒填一半切黑盒/联动再切回，字段保留。

### 1.3 `<ScanFormFields>` 复用组件
`src/components/ScanFormFields.tsx`（新）：
```ts
interface Props {
  type: "whitebox" | "blackbox";
  f: FormState;
  set: (patch: Partial<FormState>) => void;
  conflict: string | null;        // wsName 冲突检测结果（父层传）
  derivedName: string;            // 父层算好的预览名
  validity: Validity;             // 父层算好的校验态
  loadingConflict: boolean;       // 冲突检测 loading
}
```
渲染白盒/黑盒共享的 `<Card>` 表单；`type==="blackbox"` 时多渲染 `reuse_latest_whitebox` 字段（含 `--latest` 陷阱 trace 说明，现有契约保留）。

---

## 2. 白盒/黑盒表单（主体）

### 2.1 Card + fieldset 分组
单 `<Card>`：
```
<Card>
  <CardHeader><CardTitle>{type === "blackbox" ? "黑盒扫描" : "白盒扫描"}</CardTitle></CardHeader>
  <CardContent>
    <fieldset>①代码来源：sourceKind Select + sourceValue Input [+ FileSystemPicker trigger | git-extra]</fieldset>
    <fieldset>②扫描目标 + 命名：url Input + wsName Input + 预览</fieldset>
    {type === "blackbox" && <fieldset>③复用：reuse_latest_whitebox Checkbox + --latest 陷阱说明</fieldset>}
  </CardContent>
</Card>
```
fieldset 用语义分组（`<fieldset><legend>代码来源</legend>…`），Tailwind 加 spacing（`space-y-*` / `divide-y`）。

### 2.2 控件映射（裸 HTML → shadcn）
| 现有 | 替换为 |
|---|---|
| `<input>` sourceValue/branch/commit/url/wsName | `<Input>` |
| `<select>` sourceKind | `<Select>`+`SelectTrigger`/`SelectContent`/`SelectItem` |
| `<input type=checkbox>` forceReclone/reuseLatest | `<Checkbox>` |
| `<label>` | `<Label>` |
| `<button class=submit-btn>` | `<Button>` |
| `.trace` 灰字 | 保留 `.trace` class（迁移期共用，非本页独有） |

### 2.3 文件浏览器集成
`sourceKind === "path"` 时，sourceValue Input 旁挂子项目 2 组件：
```tsx
<div className="flex gap-2">
  <Input value={f.sourceValue} onChange={(e) => set({ sourceValue: e.target.value })} placeholder="/root/code/foo" />
  <FileSystemPicker value={f.sourceValue} onChange={(v) => set({ sourceValue: v })} triggerLabel="📁 浏览" />
</div>
```
`sourceKind === "git"` 时无 `FileSystemPicker`，展开 git-extra 子块（branch / commit / force_reclone）。

> 依赖：子项目 2 `<FileSystemPicker>` 必须先落地（实现顺序 1→2→3）。若并行开发，本页 trigger 可先桩化（点击 noop），但完成定义要求真实集成。

### 2.4 即时校验
父层（`ScanNewPage`）用 `useMemo` 算 `validity`：
```ts
interface Validity {
  sourceValue: string | null;     // 错误消息，null = 通过
  url: string | null;
  yaml: string | null;            // 来自 YamlEditor onError（联动 tab 用）
  wsNameConflict: string | null;
}
```
校验规则（前端纯函数）：
| 字段 | 规则 |
|---|---|
| sourceValue(path) | 非空 + 绝对路径（跨平台：`/^/` 或 `/^[A-Z]:[\\/]/`） |
| sourceValue(git) | 非空 + 宽松正则：`/^(https?:|git@|ssh:)/`（`.git$` 可选） |
| url | 非空 + `/^https?:\/\//` |
| yaml | `YamlEditor` onError 透传（联动 tab） |
| wsName | 冲突检测（见下） |

wsName 冲突检测（现有逻辑 + debounce + loading 态）：
```ts
useEffect(() => {
  if (!f.wsName) { setConflict(null); setLoadingConflict(false); return; }
  setLoadingConflict(true);
  const t = setTimeout(() => {
    apiGet<Workspace[]>("/workspaces").then((ws) => {
      setConflict(ws.some((w) => w.name === f.wsName) ? f.wsName : null);
    }).finally(() => setLoadingConflict(false));
  }, 300);
  return () => clearTimeout(t);
}, [f.wsName]);
```

提交按钮 `<Button disabled={!isValid}>`，`isValid` 在父层算：
- 白盒/黑盒：`sourceValue` 通过 + `url` 通过 + `loadingConflict===false`
- 联动：`yaml` 通过
- 三者都额外要求：无提交中态（`submitting`）

> 校验只 warn（红字）+ 控制 disable，不 sanitize 用户输入；git URL 正则宽松（https/ssh/git@）避免误拒合法值。
>
> **色 token（接子项目 2 F3）**：错误红字用 `text-destructive`（DSF `--destructive←red`），**不用裸 `text-red`**——子项目 2 F3 标 plan-wide color-token 迁移时清所有非 DSF token 色，本子项目作为新页不加重负债。表单 warn 黄字暂借 `ev-warn`（事件 class），spec §3 未定义 warn 的 shadcn token → 留本子项目 follow-up。

### 2.5 workspace 名预览
wsName 为空时，wsName Input 下方显：
```tsx
{!f.wsName && derivedName && (
  <span className="trace">预览名：{derivedName}（预览，实际由后端生成）</span>
)}
```
`derivedName` 推算（`useMemo`，依赖 `sourceValue`/`sourceKind`）：
- path：`basename(sourceValue)`（`/root/code/foo` → `foo`）
- git：URL 末段去 `.git`（`https://gitlab.example/foo.git` → `foo`；`git@host:bar/baz.git` → `baz`）
- + 本地时间戳后缀 `_{YYYYMMDD-HHMMSS}`（`new Date()` 格式化）
- `sourceValue` 空 / correlation → 预览隐藏

> 预览名不保证与后端 `{repo}_{timestamp}` 1:1（时区 / 清洗 / 格式差异），仅 hint；提交后 redirect `/p/{实际名}/live` 自然知晓真实名。

---

## 3. 联动 tab
`<CorrelationForm>`（`ScanNewPage` 内联小组件，不必独立文件）：
```tsx
<Card>
  <CardHeader><CardTitle>联动扫描</CardTitle></CardHeader>
  <CardContent>
    <YamlEditor value={f.yaml} onChange={(v) => set({ yaml: v })} onError={setYamlErr} />
    <div className="trace">{yamlErr ? `⚠ ${yamlErr}` : "yaml 合法"}</div>
  </CardContent>
</Card>
```
`YamlEditor`（现有 48 行组件）沿用，仅外层套 `<Card>` 对齐视觉。提交 disable：`yamlErr` 非空 → disabled。

---

## 4. 反馈

### 4.1 续扫确认 `<Dialog>`
冲突（wsName 已存在）时**提交按钮仍可用**（不再 disabled）。用户点"开始扫描"：
```tsx
const [confirmOpen, setConfirmOpen] = useState(false);

function onSubmit() {
  if (conflict) { setConfirmOpen(true); return; }   // 冲突 → 弹 Dialog
  doSubmit();                                        // 否则直接提交
}

<Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
  <DialogContent>
    <DialogHeader>
      <DialogTitle>断点续扫确认</DialogTitle>
      <DialogDescription>
        workspace「{conflict}」已存在。CLI -w 语义=存在则恢复，将断点续扫（恢复已有进度）。
      </DialogDescription>
    </DialogHeader>
    <DialogFooter>
      <Button variant="ghost" onClick={() => { set({ wsName: "" }); setConfirmOpen(false); }}>取消（清空名）</Button>
      <Button variant="default" onClick={() => { setConfirmOpen(false); doSubmit(); }}>确认续扫</Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
```
> 用现有 shadcn `<Dialog>`（DSF 已装），**不新增 alert-dialog 依赖**；Dialog 禁外点 / ESC 关闭（语义需明确选择，非误点；shadcn Dialog 上加 `onInteractOutside={(e) => e.preventDefault()}`）。与列表页删除/取消 Dialog 风格统一。

### 4.2 错误 toast（sonner）
删除现有 `.err-banner`，提交 catch 改：
```tsx
catch (e) {
  if (e instanceof ApiError) toast.error(renderError(e));
}
```
`renderError` 逻辑保留（400 → Temporal 未就绪 / 409 → 并发超限 / 422 → yaml 友好消息 / 其他 → 提交失败）。

### 4.3 `<Toaster />` 挂载（DSF 漏挂补）
`src/App.tsx` 根（`RouterProvider` 同级）挂：
```tsx
import { Toaster } from "@/components/ui/sonner";
export default function App() {
  return (<><RouterProvider router={router} /><Toaster /></>);
}
```
> DSF 装了 sonner 组件但未挂载到树（`grep Toaster src/` 仅命中 `sonner.tsx` 定义，无 import 消费）。本子项目补挂，全站 toast 通道打通（列表页子项目 2 的 toast 亦复用此实例）。

---

## 5. 数据流 / 接口（不改后端）

复用现有接口（契约稳定）：
- `POST /api/scan`（`buildBody` 不变）→ 202 `ScanResponse{workspace}` → `nav /p/{ws}/live`
- `GET /api/workspaces`（wsName 冲突检测，debounce 300ms）
- `GET /api/fs/browse`（文件浏览器，子项目 2 提供）
- `ScanRequest` type 不变（`type`/`source`/`url`/`workspace_name`/`config_yaml`/`reuse_latest_whitebox`）

**无新增 / 修改后端端点。**

---

## 6. 测试策略

### 现有 8 断言（保留行为，调选择器）
`src/pages/ScanNewPage.test.tsx` 重写选择器：
1. 默认白盒 Tabs：白盒 tab 内容显代码来源，无 reuse；切黑盒 tab 显 reuse。
2. 切联动 tab：显 `YamlEditor`，隐藏白盒字段。
3. 黑盒 `--latest` 陷阱：reuse 旁有 trace 说明（不勾选 = standalone）。
4. wsName 冲突 + 点提交 → 续扫 `<Dialog>`（断言 Dialog 出现 + 文案）。
5. 提交 400 → toast 提示 Temporal 未就绪（断言 `toast.error` 文案）。
6. 提交 409 → toast 并发超限。
7. 提交 422 → toast yaml 友好消息（不含原始 JSON 数组）。
8. 422 无 detail → toast 回退纯标签。

### 新增断言
9. `sourceKind=path` 时显「📁 浏览」trigger；`git` 时无。
10. 点「📁 浏览」→ `<FileSystemPicker>` Dialog 打开（MSW mock `/fs/browse`）→ 选目录 → `sourceValue` 回填。
11. path 空 → Input 下方红字 + 提交 disabled。
12. git URL 格式错 → 红字 + disabled。
13. URL 格式错 → 红字 + disabled。
14. wsName 空 + sourceValue 填 → 显预览名（basename + `_数字时间戳` 模式）。
15. 联动 yaml 错 → 提交 disabled。
16. wsName 冲突 → 点提交弹 Dialog → 取消 → 名清空；确认续扫 → 真提交（mock `/scan` 202）。

### MSW handlers
- `GET /workspaces` → 返若干 ws（含/不含冲突名）。
- `GET /fs/browse` → 返固定 entries。
- `POST /scan` → 各状态码（202 / 400 / 409 / 422 / 422 无 detail）。

---

## 7. 任务拆解（writing-plans 种子）

1. `App.tsx` 挂 `<Toaster />`（toast 通道打通）+ sonner 依赖确认。
2. `ScanNewPage` 顶层 segmented → shadcn `<Tabs>`（8 现有测试断言调 Tabs 选择器，保持绿）。
3. `<ScanFormFields>` 组件抽取（白盒/黑盒共享 + blackbox reuse 字段）+ `<Card>` fieldset 分组。
4. 表单控件全换 shadcn（Input/Select/Checkbox/Label/Button）。
5. 集成 `<FileSystemPicker>`（`sourceKind=path` 时）+ MSW `/fs/browse` 测试。
6. 即时校验（path/git/url 规则 + wsName 冲突 debounce + loading 态）+ 提交 disable。
7. workspace 名预览（前端推算 + trace 标注）。
8. 续扫确认 inline → `<Dialog>`（冲突点提交才弹）+ 错误 → `toast.error`（`renderError` 保留）。
9. 新增测试断言全绿 + 冒烟回归（DSF 测试 + 子项目 2 列表页测试未回归）。

---

## 8. 风险

| 风险 | 缓解 |
|---|---|
| workspace 名预览与后端实际不一致 | §2.5 标注"预览，实际由后端生成"；预览仅 hint 非承诺 |
| `<FileSystemPicker>` 在子项目 2 未落地时不可用 | 实现顺序 1→2→3（上位 spec §5 已明）；并行时 trigger 桩化，但完成定义要求真实集成 |
| Tabs 切换时表单 state 丢失 | §1.2 `FormState` 单一 `useState` 跨 tab，切 tab 只换 `type` |
| 即时校验过严阻塞合法输入 | §2.4 校验只 warn + 控 disable，不 sanitize；git URL 正则宽松（https/ssh/git@） |
| toast 与列表页 toast 通道冲突 | §4.3 全站共享一个 `<Toaster />`（App 根），sonner 单实例无冲突 |
| 续扫 Dialog 外点 / ESC 误关 | §4.1 Dialog 禁外点（`onInteractOutside preventDefault`），需明确选择 |
| events.css 共存 | 本页内部样式全迁 Tailwind；`events.css` 保留（其他页消费 `.ledger`/`.trace`） |
