# shannon-web 前端 i18n 设计

- **日期**:2026-07-09
- **分支**:feat/fork-py
- **状态**:设计已确认,待写实现计划
- **范围**:仅前端 UI 文案(层 1),中英双语可切换,全站核心页一次性铺满

---

## 1. 背景与动机

当前 shannon-web 前端(`packages/web/frontend`)**零 i18n**:所有界面文字都是硬编码的中文字符串,直接写在 JSX/TS 字面量里。例如仓库页 `ReposPage.tsx` 单个文件就有 30+ 处硬编码中文(表头「名称/来源/分支/大小/状态/操作」、按钮「+ 添加仓库/更新/删除」、确认框、空状态等)。

证据:`package.json` 无任何 i18n 库;`src/` 下无 `locales/` 目录;无 `useTranslation` / `t()` / `FormattedMessage` 调用。前端源码约 4100 个中文字符散布在 42 个文件,其中真正需 i18n 的 UI 文案约 3000 字符,集中在 `pages/`、`components/`(非 `ui/`)、`routes/WorkspaceDetail/`。

附带现状问题:`WorkspaceListPage`(扫描任务列表)已是**中英混排**——表头英文(`workspace/status/type/vulns/cost`)、按钮中文(`操作/取消/删除`),从未统一。

目标:引入标准 i18n,把前端 UI 文案做成**中英双语可切换**,跟随浏览器语言、用户选择持久化。

---

## 2. 目标与非目标

### 目标(本次范围)
- 引入 react-i18next,搭好 i18n 基础设施(provider/init/locale 文件/语言检测/切换器)。
- 把**全站核心页面** + TopBar 导航 + 共享组件的硬编码 UI 中文一次性抽成 i18n key。
- 提供中(zh)/英(en)两套 locale,语言切换器,跟随浏览器语言 + localStorage 持久化。
- 状态枚举展示值经 i18n 映射,中英 UI 一致。

### 非目标(明确不做)
- **不动后端**:后端 HTTPException 中文 detail(层 2)不在范围;前端直接展示后端 detail 的 toast 保持原样。
- **不翻译报告内容**:LLM 生成的漏洞报告正文(MarkdownView 渲染的报告主体)是「数据」不是「UI」,保持原语言。
- **不动 `lib/vuln-block.ts`**:其中的中文关键词(`开放重定向`/`越权`/`弱口令`等)用于匹配后端中文报告做分类,翻译会破坏匹配逻辑。
- **动态数据不翻译**:仓库名、来源 URL、分支、大小数值、时间戳等后端返回值原样展示。
- **不做**:namespace 懒加载、ICU 复数/性别、RTL(中英皆 LTR)、SSR 语言协商、`/dev/components` 页(dev-only,优先级低,后续可选)。

---

## 3. 已确认的关键决策

| 决策点 | 选择 |
|---|---|
| 国际化层 | 仅前端 UI 文案(层 1) |
| 语言形态 | 中英双语可切换 |
| 覆盖范围 | 全站核心页一次性 |
| 默认语言 | 跟随浏览器语言(`navigator.language`),用户选择持久化到 localStorage |
| i18n 方案 | react-i18next(+ i18next-browser-languagedetector + i18next-parser) |

---

## 4. 架构设计

### 4.1 依赖
新增:
- `i18next`(运行时)
- `react-i18next`(React 绑定,`useTranslation` Hook)
- `i18next-browser-languagedetector`(语言检测)
- `i18next-parser`(devDependency,key 提取工具)

### 4.2 初始化
新增 `src/i18n/index.ts`,一次性 `i18n.use(initReactI18next).use(LanguageDetector).init({...})`:
- `resources`:zh/en 内联 import(同步加载,文件小,无首屏闪烁,无需 Suspense 懒加载)。
- `fallbackLng:'zh'`。
- `detection`:`order:['localStorage','navigator']`、`lookupLocalStorage:'shannon.lang'`、`cacheUserLanguage:true`。
- `react:{ useSuspense:false }`(资源已内联同步,关掉 Suspense 避免满树加 Suspense 边界)。
- `interpolation:{ escapeValue:false }`(React 已转义)。

### 4.3 挂载
`main.tsx` 顶部新增 `import './i18n'`(副作用 init)。**不需要** `I18nextProvider`——react-i18next 用全局 i18n 实例,`useTranslation` 直接消费。

---

## 5. Locale 文件组织

`src/locales/zh.json` 与 `src/locales/en.json`,按 namespace 嵌套,命名统一 camelCase:

```jsonc
{
  "common": {
    "cancel": "取消", "confirm": "确认", "delete": "删除", "update": "更新",
    "loading": "加载中...", "retry": "重试"
  },
  "nav": {
    "dashboard": "Dashboard", "workspaces": "Workspaces", "repos": "仓库",
    "scan": "Scan", "settings": "Settings", "mainAria": "主导航"
  },
  "repos": {
    "title": "仓库",
    "addRepo": "+ 添加仓库",
    "searchPlaceholder": "搜索仓库名",
    "empty": "暂无仓库。点「+ 添加仓库」clone 一个。",
    "table": { "name": "名称", "source": "来源", "branch": "分支", "size": "大小", "state": "状态", "actions": "操作" },
    "states": { "ready": "✓ 就绪", "failed": "✗ 失败", "stale": "⚠ 未完成", "cloning": "clone 中", "pulling": "pull 中" },
    "deleteDialog": { "title": "删除仓库", "confirm": "确认", "cancel": "取消" },
    "errors": { "loadFailed": "加载仓库列表失败", "inUse": "仓库正被使用", "deleteFailed": "删除失败({{status}})" }
  }
  // ... 其余 namespace:workspaces / scan / dashboard / settings / repoDetail / workspaceDetail
}
```

顶层分组(均位于 i18next 默认 `translation` namespace 内,**非独立 namespace**):`common`(共享按钮/状态词)、`nav`(导航)、`repos`、`workspaces`、`scan`、`dashboard`、`settings`、`repoDetail`、`workspaceDetail`(下分 `overview`/`report`/`deliverables`/`logs`/`live`)。访问形如 `t('repos.title')`。

**插值**用 i18next 标准 `{{var}}`:`t('repos.errors.deleteFailed', { status: e.status })`。

---

## 6. 组件改动清单

### 6.1 新增文件
| 文件 | 作用 |
|---|---|
| `src/i18n/index.ts` | i18n init + 检测 + resources 内联 |
| `src/locales/zh.json` | 中文文案字典 |
| `src/locales/en.json` | 英文文案字典 |
| `src/components/LanguageSwitcher.tsx` | zh/en 切换器(放 TopBar) |
| `i18next-parser.config.js` | key 提取配置 |
| `src/test/i18n-test-setup.ts`(或并入现有 setup) | 测试环境加载 i18n |

### 6.2 改动文件(硬编码中文 → `t()`)
| 文件 | 说明 |
|---|---|
| `src/main.tsx` | `import './i18n'` |
| `src/components/layout/TopBar.tsx` | NAV label + aria-label 走 `t('nav.*')`;挂 LanguageSwitcher |
| `src/pages/ReposPage.tsx` | 标题/表头/按钮/空状态/确认框/toast/STATE_BADGE |
| `src/components/AddRepoDialog.tsx` | 对话框文案 + toast |
| `src/components/CloneProgress.tsx` | clone/pull 进度文案 |
| `src/pages/RepoDetailPage.tsx` | 详情页 UI 文案 |
| `src/pages/WorkspaceListPage.tsx` | 表头(原英文)+ 按钮/确认框统一走字典,消除混排 |
| `src/pages/ScanNewPage.tsx` | 扫描表单文案(密度最高) |
| `src/components/ScanFormFields.tsx` | 表单字段文案 |
| `src/pages/DashboardPage.tsx` | 概览页 UI 文案 |
| `src/pages/SettingsPage.tsx` | 设置页 UI 文案 |
| `src/routes/WorkspaceDetail/OverviewTab.tsx` | 概览 tab 文案 |
| `src/routes/WorkspaceDetail/ReportTab.tsx` | **仅页面 UI 文案**(标题/按钮/加载态);报告正文不动 |
| `src/routes/WorkspaceDetail/DeliverablesTab.tsx` | 产物 tab 文案 |
| `src/routes/WorkspaceDetail/LogsTab.tsx` | 日志 tab 文案(日志内容本身是数据,不动) |
| `src/routes/WorkspaceDetail/LiveTab.tsx` | 实时 tab UI 文案(事件流内容是数据,不动) |
| `src/components/StatusBadge.tsx` | 状态枚举值经 i18n 映射 + 未知值 fallback |
| `src/components/MarkdownVulnCard.tsx` | 漏洞卡片 UI 文案 |
| `src/components/MarkdownView.tsx` | **仅 UI 模板文案**走字典;渲染的报告 Markdown 正文不动 |

> `src/pages/DevComponentsPage.tsx`(dev-only)本次**不动**,列为后续可选。

---

## 7. 边界规则

### ✗ 不动(翻译会破坏功能或越出层 1 范围)
- `lib/vuln-block.ts`:中文关键词(`开放重定向`/`越权`/`弱口令`...)匹配后端中文报告,属层 3,翻译破坏匹配。
- 后端 HTTPException 中文 detail(层 2)。
- LLM 报告正文、日志内容、实时事件流内容(均为运行时数据)。
- 动态数据:仓库名、来源 URL、分支、大小、时间戳等。

### ✓ 要动
- 所有前端 UI 硬编码中文:标题、表头、按钮、空状态、确认框、toast、占位符、导航、aria-label、状态徽章。

---

## 8. 状态值本地化映射

`StatusBadge` 现状:直接渲染后端英文状态字符串(`running`/`completed`/`failed`/...)。

改为:经 i18n 映射(`t('workspaces.status.running')` → `运行中`),**未知枚举 fallback 渲染原值**(防后端新增状态值时前端显示空)。`ReposPage` 的 `STATE_BADGE`(ready/failed/stale/cloning/pulling)同样处理,emoji 前缀保留在文案内。

理由:中文 UI 里状态显示英文会拧巴;映射后中英都一致。

---

## 9. 默认语言与持久化

- 检测顺序 `['localStorage','navigator']`:先读 `localStorage['shannon.lang']`,无记录则看 `navigator.language`(`zh*`→中文,否则英文)。
- `LanguageSwitcher` 调 `i18n.changeLanguage(lng)` → detector 自动写 localStorage。
- 首屏无闪烁:resources 内联同步 + `lng` 在 init 阶段同步确定。
- 复用 `next-themes` 的 localStorage 偏好模式(项目已有先例)。

---

## 10. key 规范与维护

- 命名:`namespace[.section].key`,统一 camelCase(与现有 `STATE_BADGE` 等命名一致)。
- `i18next-parser`:`i18next-parser.config.js` 配置输出到 `src/locales/zh.json`,加 npm script `"i18n:scan": "i18next-parser"`。提取后人工翻译 `en.json`。
- 缺失 key 行为:`fallbackLng='zh'`,en 缺某 key 时回退显示中文,便于发现漏翻。

---

## 11. 测试策略

- 测试 setup(现有 vitest setup)加载 `./i18n`,默认 `lng='zh'`,确保组件测试中 `useTranslation` 可用。
- 含中文文案断言的现有组件测试:默认 `lng='zh'`,文案迁移到字典后渲染结果仍是同一中文文案,断言值无需修改即通过。
- 新增测试:
  - `LanguageSwitcher`:点切英文 → 文案变英文;localStorage 写入。
  - `StatusBadge`:已知状态渲染本地化标签;未知状态 fallback 原值。
  - 插值:`t('repos.errors.deleteFailed', { status: 500 })` 正确渲染 `删除失败(500)`。
- 现有测试约定遵循 memory:前端测试须 `cd packages/web/frontend` 再跑 `vitest`/`tsc`/`build`(cwd 不持久)。

---

## 12. 风险与注意事项

1. **MarkdownView 文案区分**:该文件 387 字含「UI 模板文案」与「报告正文渲染」两类,只迁移前者,后者保持不动——实现时逐处判断,勿误伤报告内容渲染。
2. **WorkspaceListPage 混排**:表头原本英文,纳入字典后中英都从字典取(中:工作区/状态/类型...,英:workspace/status/type...),顺手消除现状混排。
3. **状态枚举 fallback**:`StatusBadge`/`STATE_BADGE` 必须对未知值 fallback 原值,否则后端新增状态会显示空白。
4. **toast 文案来源**:部分 toast 是前端硬编码(→ i18n),部分可能直接展示后端 detail(→ 不动)。实现时区分,勿把后端 detail 也裹进 i18n。
5. **测试环境 i18n**:必须确保 init 在测试中执行,否则 `useTranslation` 报错。

---

## 13. 验收标准

- 仓库页及全站核心页面切换 zh/en 时,所有 UI 文案(标题/表头/按钮/空状态/确认框/toast/占位符/导航/状态徽章)正确切换。
- 首次访问按浏览器语言显示,切换后刷新仍保持选择。
- 状态徽章已知值本地化、未知值 fallback 不空白。
- 报告正文、日志内容、仓库名等动态数据/后端内容语言不变。
- `lib/vuln-block.ts` 关键词匹配行为不变(回归)。
- `vitest` 改动相关测试 + 新增 i18n 测试全绿;`tsc -b` + `vite build` 通过。
