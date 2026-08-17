# 前端性能优化四件套 + React 19 升级 设计

> 日期：2026-08-17
> 状态：已实施（2026-08-17，实施记录见 git log / 计划文档；§4.3 有一处实施期修正，见文末「实施结果」）
> 主题：修复 `packages/web/frontend` 的四个已实证性能问题——①路由零代码分割（单 chunk 1.2MB）②MarkdownView 滚动重复解析 ③WorkspaceSwitcher 全局 5s 无条件轮询 ④SSE 事件数组无界增长——并以 React 官方机制为主轴落地：React.lazy + Suspense、React Compiler、useSyncExternalStore；数据层引入 SWR（用户指定）；同时升级 React 18.3.1 → 19.2.8 并顺手清理 forwardRef / `<title>` 样板。
> 关联：无前置 spec；本 spec 只动 `packages/web/frontend`，后端 / Python 侧零改动。

---

## 1. 背景与动机（四个已实证的问题）

产物现状：`vite build` 产出**单个 JS chunk 1.2MB（gzip 356KB）+ 77KB CSS**，无任何代码分割。

| # | 问题 | 实证位置 | 后果 |
|---|---|---|---|
| ① | 路由无代码分割 | `src/router.tsx` 全部页面静态 import；ReportTab → MarkdownView → react-markdown + rehype-highlight（lowlight common 全集）+ micromark 全家桶全部进主包 | 首屏拉全量；markdown 栈（约占 1/3）只在看报告时才需要 |
| ② | MarkdownView 滚动重复解析 | react-markdown 默认导出是**零缓存同步组件**——每次渲染执行 `createProcessor` + `parse` + `runSync`（`node_modules/react-markdown/lib/index.js:163`）；而 scroll-spy 的 `setActiveId` 是 MarkdownView 自身 state（`MarkdownView.tsx:564`） | 每次滚动命中新章节 / 折叠卡片 / 切主题，都同步重新 parse + 高亮**全部** ReactMarkdown 实例（所有 prose 段 + 漏洞卡 body + PoC）；大报告滚动掉帧 |
| ③ | 全局 5s 无条件轮询 + 双份轮询 | `useWorkspaces.ts` 默认 `intervalMs=5000` 无 `document.hidden` 守卫，`WorkspaceSwitcher` 挂在 AppShell → **所有认证页面**永久轮询；另 `WorkspaceDetail/index.tsx` 与 `ScanList.tsx` 父子各自 `listScans(workspace)` + 各自 10s 轮询 | 后台 tab 也打请求；同一份数据**双倍请求** |
| ④ | SSE 事件数组无界增长 | `useEventSource.ts:26` `setEvents(prev => [...prev, ev])` 每条事件复制全数组（累计 O(n²) 分配）、永不封顶 | 长扫描（数千条 LLM/tool 事件）内存与 GC 压力累积；每条消息一次全组件重渲染 |

**用户决策（2026-08-17 对话确认）**：
1. ①② 按 React 官方设计做（React.lazy + Suspense；React Compiler）。
2. ③ 用 SWR（Vercel）。
3. ④ 无官方组件，参照官方原语（useSyncExternalStore）自行设计。
4. React 升级到最新版（19.2.8），新版本优秀设计顺手用上（幅度：顺手清理样板，不强用无场景特性）。
5. SWR 迁移范围：`useWorkspaces` + 轮询页（Dashboard / ScanList / WorkspaceDetail），`useAsync` 各页留作后续渐进迁移。

---

## 2. 设计决策总表

| 维度 | 决定 |
|---|---|
| React 版本 | 18.3.1 → **19.2.8**（`@types/react(-dom)` 同步 19） |
| 代码分割 | `React.lazy` + `<Suspense>`（官方机制）；Login/Dashboard 保持 eager，其余路由全 lazy |
| 自动 memo 化 | **React Compiler 1.0.0**（`babel-plugin-react-compiler`，React 19 下零 runtime），全应用编译，`panicThreshold: 'none'` 保底 |
| 数据层 | **SWR 2.5.1**；全局 `<SWRConfig>` 绑 api client fetcher |
| 轮询治理 | SWR 默认 `refreshWhenHidden: false`（后台 tab 自动停）+ `revalidateOnFocus`；条件轮询用函数式 `refreshInterval` |
| 请求去重 | `WorkspaceDetail/index.tsx` 与 `ScanList.tsx` 统一 SWR key `["scans", workspace]`，双份请求+轮询自动合一 |
| SSE 层 | 新建 `scanEventStore.ts`：useSyncExternalStore 协议的外部 store + rAF 批量合并 + 5000 条环形缓冲 + 引用计数连接管理 |
| 19 特性顺手清理 | 14 个文件 forwardRef → ref-as-prop；页面标题 `<title>` 元数据（hoist）替换手写 document.title（先核 BrandContext 无冲突） |
| 依赖清理 | 删除未使用：`@monaco-editor/react`、`js-yaml`、`@types/js-yaml`、`@tanstack/react-table` |
| 明确不动 | react-window 留 1.8.x（**不进 v2** API 重写）；react-router-dom 留 v6.30.x（**不进 v7**）；`useAsync` 与其余 10+ 页面不迁移 |

---

## 3. §A React 19.2.8 升级（其余各项的前置）

### 3.1 依赖变更（npm registry 已逐一核实 peer 兼容）

| 包 | 现版本 | 目标 | 说明 |
|---|---|---|---|
| `react` / `react-dom` | 18.3.1 | ^19.2.8 | |
| `@types/react` / `@types/react-dom` | 18.x | ^19 | |
| `react-window` | 1.8.10 | ^1.8.11 | 1.8.11 起加 React 19 peer；API 不变 |
| `@testing-library/react` | 16.0.0 | ^16.3.2 | 16.0 仅支持 18；16.3.x peer 含 ^19 |
| `react-router-dom` | 6.26 | ^6.30.4 | v6 线 peer 宽松（`>=16.8`）；v7 另立任务 |
| `swr` | — | ^2.5.1 | 新增 |
| `babel-plugin-react-compiler` | — | ^1.0.0 | 新增（devDep） |
| `@monaco-editor/react` / `js-yaml` / `@types/js-yaml` / `@tanstack/react-table` | — | 删除 | src 零引用（仅测试 mock 提到 monaco，同步删两处 `vi.mock`） |

已验证**原版本即兼容 19、不动**：radix 全系、next-themes 0.4.6、sonner 2.x、react-i18next 17、react-markdown 9。

### 3.2 19 破坏性变更核查（本代码库影响面）

- `createRoot` + `StrictMode` 用法不变；无 string ref / `defaultProps`（函数组件）/ `ReactDOM.render` 遗留（核查过）。
- 测试：`@testing-library/react` 16.3 处理 React 19 的 `act` 归属变化。
- `forwardRef` 在 19 仍工作（deprecated 不删除）——但按用户决策顺手清理为 ref-as-prop（§3.3）。

### 3.3 顺手清理（用户确认幅度：清样板，不强用无场景特性）

- **forwardRef → ref-as-prop**：14 个文件（shadcn ui 原语为主）去掉 `React.forwardRef` 包装，`ref` 变普通 prop（19 官方设计）。
- **`<title>` 元数据**：React 19 支持组件树内 `<title>` 自动 hoist 到 head。实施前先核 `BrandContext` 现有 document.title 管理逻辑，冲突则仅对 BrandContext 未覆盖的增量页面应用。
- **不强用**：`use()`、`useOptimistic`、`<Activity>`（未稳定）、ViewTransition——本代码库无自然场景。

---

## 4. §B 路由代码分割（问题①，官方设计）

### 4.1 lazy 范围

- **保持 eager**：`LoginPage`（未认证首屏）、`DashboardPage`（登录后着陆页，最高频路径）。
- **React.lazy**：`WorkspaceDetail` 整树（含 ScanDetail、各 tab——ReportTab/MarkdownView/markdown 栈随之独立成 chunk）、`ScanNewPage`、`SettingsPage`、`UsersPage`、`AuthProfilesPage`、`AuthProfileTestPage`、`VerifyProcessPage`、`HostProfilesPage`、`WorkspacesEntry`、`DevComponentsPage`（仅 dev）。
- **保持静态**：`DefaultScanTab` / `LegacyWsTabRedirect`（定义在 router.tsx 内部，极小）。

### 4.2 Suspense 与 fallback

- `AppShell` 的 `<Outlet>` 外包一层 `<Suspense>`，fallback 为页面级 Skeleton（复用现有 `Skeleton` 组件，`min-h` 占位避免布局跳动）。

### 4.3 Vite 配套

- `build.rollupOptions.output.manualChunks`：拆 `react-vendor`（react / react-dom / scheduler / react-router-dom）。
- markdown 栈随 ReportTab 的动态 import **自然**落入独立 chunk，不手工分组。
- `rehypeHighlight` 传 `languages` 子集（**替换**默认 lowlight common 全集——已核源码 `settings.languages || common`）：按报告实际语言逐个 `import bash from "highlight.js/lib/languages/bash"` 等（bash / json / python / javascript / typescript / java / sql / http / yaml / xml / ini）。

### 4.4 预期与验收

入口 chunk 1.2MB（gzip 356KB）→ 预计 ~600KB 量级；report chunk 独立按需。验收以 `vite build` 产物大小记录前后对比（写进 PR/commit 描述）。

---

## 5. §C React Compiler（问题②，官方设计）

- 接入：`@vitejs/plugin-react` 的 `babel.plugins` 加 `babel-plugin-react-compiler`，`panicThreshold: 'none'`（编译失败组件自动回退原实现，不挂构建）。React 19 无需 runtime 包。
- 范围：全应用编译（官方默认路径）；node_modules 默认不编。
- 与现有手写 `useMemo`/`useCallback` 共存（编译器兼容，无需删除）。
- **效果锚点**：MarkdownView 因 `setActiveId`（scroll-spy）/ 折叠卡片 / hero 折叠触发重渲染时，编译器缓存的 `<ReactMarkdown>` 元素引用稳定 → React 跳过子树重渲染 → 不再重复 parse。验收：大报告滚动时 Performance 面板无明显长任务（主观 + Profiler 抽查）。
- vitest 走同一 vite 管道，编译器对测试同样生效（行为等价，不额外配置）。

---

## 6. §D SWR（问题③，用户指定）

### 6.1 全局配置

`App.tsx` 挂 `<SWRConfig value={{ fetcher: apiGet }}>`（复用 api client 的鉴权头 / 错误语义；`apiGet<T>(path)` 签名天然匹配 SWR fetcher）。

### 6.2 useWorkspaces（保持对外 API 不变）

内部改 `useSWR("/workspaces", { refreshInterval: 5000 })`，返回 `{ data, loading, error, refresh, lastUpdated }` 形状不变 → `WorkspaceSwitcher` / `WorkspacesEntry` 零改动。核心修复来自 SWR 语义：默认 `refreshWhenHidden: false`（后台 tab 停轮询）+ `revalidateOnFocus: true`（回前台刷新）。

### 6.3 Dashboard / ScanList / WorkspaceDetail（条件轮询 + 去重）

- `DashboardPage`：`useSWR`（key `/api/scans` 聚合接口对应 listAllScans），`refreshInterval: (latest) => 存在运行中扫描 ? 10_000 : 0`（函数式条件轮询，跑完自动停）；手动刷新按钮接 `mutate()`；`refreshedAt` 语义保留。
- `WorkspaceDetail/index.tsx` + `ScanList.tsx`：**同一 key `["scans", workspace]`** → SWR 自动合并为单请求单轮询（消除当前父子双份）；轮询同样函数式条件；SWR 缓存让 ws 内 tab 切换即时显示缓存再后台 revalidate（优于现在的重复 loading skeleton）。
- 404 / 错误分支语义保持（`notFound` 等）。

### 6.4 范围外（显式）

`useAsync` 及 Users / Settings / AuthProfiles / HostProfiles 等 10+ 页面**不迁移**，留作后续渐进任务。

### 6.5 测试适配

涉及 SWR 的组件测试 render 时包 `<SWRConfig value={{ provider: () => new Map() }}>` 隔离缓存（每测试独立 store）；现有 msw / vi.mock(api client) 拦截方式不变（SWR 走同一 fetcher）。

---

## 7. §E SSE 层：scanEventStore（问题④，参照官方原语自设计）

### 7.1 架构

新建 `src/api/scanEventStore.ts`；重写 `src/api/useEventSource.ts` 为薄包装。

```
模块级 Map<url, ScanEventStore>   ← 按 URL 单例
  ├─ 引用计数（subscribe ++ / unsubscribe --，归零关连接 + 出 Map）
  ├─ EventSource（原生自动重连 + Last-Event-ID 透传，语义同现状）
  ├─ pending: NdjsonEvent[]       ← onmessage 只入 pending + 调度一次 rAF
  ├─ events: NdjsonEvent[]        ← flush 时合并追加，尾部截断至 CAP=5000
  ├─ status / lastEventId
  └─ snapshot 缓存对象            ← 仅 flush 时重建一次
```

- **useSyncExternalStore 协议**：`subscribe(cb)` / `getSnapshot()`。**硬要求**：两次 flush 之间 `getSnapshot()` 返回**同一引用**（否则 React 渲染循环）——由「仅 flush 重建 snapshot」保证。
- **StrictMode 安全**：双挂载的第二次 subscribe 复用同 store 实例（引用计数吸收）。
- **rAF 批量**：突发日志从「一条一渲染」变「一帧一渲染」；`lastEventId` 仍逐事件更新（进 snapshot）。
- **环形缓冲 CAP=5000**：LogStream 虚拟化阈值 500 的 10 倍余量；消除 O(n²) 复制与无界内存。
- **scan_end 语义保持**：命中 `stopType` → `status: "closed"` + `es.close()`，事件本身仍入列。

### 7.2 对外接口（不变）

`useEventSource(url, stopType?) → { events, status, lastEventId }` —— `LiveTab` / `LogsTab` / `VerifyLivePanel` / `VerifyProcessPage` 等消费者**零改动**。

### 7.3 有意的行为差异（显式声明）

1. `events` 上限 5000 条（回看窗口有界）。
2. 突发事件按帧合并（`events` 引用每帧变一次，而非每条变一次）——LogStream 自动滚底、LiveTab `scanEnd` useMemo 均兼容且更省。

---

## 8. 测试与验证

- **新增单测**：`scanEventStore.test.ts`——rAF 合并（一帧一次通知）、5000 截断、引用计数归零关连接、scan_end closed 语义、getSnapshot 引用稳定。
- **现有测试全绿**：`useEventSource` 消费者（LogStream / LiveTab / VerifyProcess 等）、Dashboard / ScanList / WorkspaceDetail 相关测试（按 §6.5 适配 SWR 隔离后保持断言语义）。
- **只跑前端**：`npm run test`（vitest run）+ `tsc -b`。**不跑 pytest**（CLAUDE.md §3：预存挂起）。
- **产物核对**：`vite build` 记录前后 chunk 数量 / 大小对比，量化 §4.4 预期。

---

## 9. 非目标

- 不做 react-window v2 / react-router v7 迁移。
- 不迁移 `useAsync` 全站数据层（仅 §6.3 列出的轮询页）。
- 不引入 eslint-plugin-react-compiler（repo 无 eslint 配置，编译器 panic 回退已兜底）。
- 不做 CSS / 字体 / 图片侧优化（77KB CSS 与字体不在本次范围）。
- 后端 / Python 侧零改动。

---

## 10. 实施顺序

**A（升级）→ B（分割）→ C（编译器）→ D（SWR）→ E（SSE store）**，A 是其余各项的前置；每步独立可验证（测试 + build 通过才进下一步）。

---

## 11. 实施结果（2026-08-17）

### chunk 前后对比（vite build）

| chunk | 实施前（单 chunk） | 实施后 | gzip 后 |
|---|---|---|---|
| 全部 JS | 1150.47 kB 单文件（gzip 349.96 kB） | 多 chunk 按需加载 | — |
| 主入口 index | —（含在上面） | 531.88 kB（含 React Compiler ~30 kB 运行时） | 177.22 kB |
| react-vendor | —（含在上面） | 81.62 kB（长缓存独立） | 27.71 kB |
| MarkdownView（报告栈） | —（含在上面，首屏必拉） | 282.55 kB（仅打开报告时加载） | 89.23 kB |
| ScanNewPage / ScanDetail / Settings 等其余 | —（含在上面） | 0.24–63.10 kB / 页 | — |

首屏（Dashboard 路由）JS 由 1150.47 kB（gzip 349.96）降至约 630 kB（gzip 约 210，入口+vendor+共享小 chunk），减约 45%；报告栈 282.55 kB 延迟到 ReportTab 打开。

### §4.3 实施期修正（rehype-highlight 语言子集）

计划中的 `languages` 选项方案只影响运行时注册，**减不了 bundle**：`rehype-highlight/lib/index.js` 的 fallback `settings.languages || common` 引用使 rollup 无法摇掉 lowlight 的 common 全集 re-export。实施改为 vendored 精简插件 `src/lib/rehype-highlight-subset.ts`（只 import `createLowlight`，languages 必传、无 common 引用），common 全集（kotlin/objectivec/swift 等 35 个未用语法，~94 kB）被整体摇除；`rehype-highlight` 依赖移除，`lowlight` + `hast-util-to-text` 转直接依赖。

### 测试结论

`npm test`：864 通过（新增 `scanEventStore.test.ts` 6 个；`useWorkspaces`/`useEventSource` 既有直测按新语义适配——SWR 独立 cache wrapper、rAF 同步桩），仅 2 个已知基线文件失败（本地 fixture 缺失，非回归）。`tsc -b` 0 error；`npm run build` 成功。

### 其余交付

- React 18.3.1 → 19.2.8；`@types/react` 19；14 个 ui 原语 forwardRef → ref-as-prop。
- SWR 2.5.1：`useWorkspaces`（后台 tab 停轮询 + 回前台刷新）、Dashboard 条件轮询（`refreshInterval` 函数式）、`useScans` 共享 key 去重（WorkspaceDetail + ScanList 单请求单轮询）。
- SSE：`scanEventStore`（rAF 批量 + 5000 环形缓冲 + 引用计数单例）+ `useEventSource` 改 `useSyncExternalStore` 薄包装（对外 API 不变）。
- React Compiler 1.0.0（`panicThreshold: "none"`，失败回退不挂构建）。
