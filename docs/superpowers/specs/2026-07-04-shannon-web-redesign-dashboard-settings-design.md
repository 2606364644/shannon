# Shannon Web 前端重设计 · 子项目 5:Dashboard 首页 + 设置页(design)

> 上位 spec:`2026-07-04-shannon-web-redesign-design.md`(伞)。本子项目依赖子项目 1(DSF,已落地)、与子项目 2/3/4(已落地)模式对齐。
>
> 范围:新增 Dashboard 首页(`/`)+ 设置页(`/settings`),新增后端只读状态端点 `GET /api/system-status`,Workspaces 列表页路由从 `/` 迁至 `/workspaces`。**不动任何现有契约**。

---

## 1. 背景与现状基线

伞 spec §2 规划的 `/`(Dashboard 首页)与 `/settings`(设置页)至今未落地:

- **路由**:`router.tsx` 中 `/` 被 `WorkspaceListPage` 占用;`/settings` 未注册。
- **导航**:`TopBar` 已声明 Dashboard(→ `/`)、Settings(→ `/settings`)导航项,但均 `disabled: true`(DSF spec 导航迁移期约定标注"子项目 5 启用")。右侧 `ThemeToggle` 已就位。
- **后端 `/health`**:仅返回 `{status, git_available}`,不含引擎 / Temporal / worker 状态。无其他状态端点。
- **数据源就绪**:`GET /api/workspaces` 返回字段齐全(`name / scan_type / status / vuln_counts / vuln_count / total_cost_usd / total_duration_ms / links / created_at / completed_at / is_correlation`);前端 `useWorkspaces` hook 已封装 5s 轮询。
- **主题就绪**:`theme.ts` + `<html class="dark|light">` + localStorage `shannon-theme` + 防 FOUC 内联 script。
- **组件库就绪**:DSF 落地 15 个 shadcn 组件(Card / Table / Badge / Dialog / Button / Skeleton / Select / Tabs / Switch / Tooltip / Sonner / …)。
- **删除能力就绪**:`DELETE /api/workspaces/{ws}` + 前端 `deleteWorkspace()`(列表页已用)。

**范围缩减决策(brainstorming 2026-07-04)**:伞 spec §5 原列设置页含"清理归档"。经澄清,本子项目**不做 workspace 批量清理 / 归档**(列表页已有单删;批量清理延后或砍除)。本 spec 同步修订伞 spec §5 表述。

---

## 2. 目标与范围

**做**:

1. Dashboard 首页(`/`):进站概览 —— 汇总数字 + 正在运行卡片墙 + 最近扫描行 + 新建扫描入口 + 全空态。
2. 设置页(`/settings`):主题切换 + 系统状态只读面板 + 关于 / 版本信息。
3. 后端新增 `GET /api/system-status`(只读,`/health` 不动)。
4. Workspaces 列表页路由 `/` → `/workspaces`(内部代码不动)。
5. TopBar 启用 Dashboard / Settings 导航项,Workspaces 目标改 `/workspaces`。

**不做**(YAGNI / 铁律):

- 不做 workspace 批量清理 / 归档(范围缩减,§1 决策)。
- 不做统计图表(漏洞分布 / 趋势)—— 进站概览用数字 + Badge 即可。
- Dashboard **不接 SSE** —— 实时 phase 进详情 live tab 看,概览只轮询 list。
- 设置页不做引擎 / Temporal 运行时切换 —— 这些由启动 env 决定,只读展示。
- Temporal 不做实时 ping —— 只报配置 + 启动 / 最近提交探测结果。
- 不动 `dashboardReducer` / ndjson schema / 现有 API 契约 / `WorkspaceListPage` 内部 / `useWorkspaces` / `theme.ts`。

---

## 3. 契约不动边界(铁律)

**绝对不改**:

| 文件 / 契约 | 角色 | 不动理由 |
|---|---|---|
| `src/state/dashboardReducer.ts` / `formatters.ts` / `dashboardReducer.test.ts` | 数据层 | 与 core `DashboardState.apply` 1:1 对齐契约 |
| `src/api/types.ts`(`NdjsonEvent` union) | 事件线格式 | ndjson 契约 |
| `src/api/useEventSource.ts` | SSE 管道 | 数据管道独立 |
| ndjson 事件 schema / 现有所有 `/api/*` 端点 | 后端契约 | 只新增 `/api/system-status`,不改现有 |
| `WorkspaceListPage.tsx` 内部 | 列表页(子项目 2 产物) | 只改挂载路由 |
| `src/api/useWorkspaces.ts` | 列表 hook | 复用 |
| `src/lib/theme.ts` | 主题工具 | 复用(设置页与 TopBar 共用) |
| 跨媒介语义色(`.ev-*`) | 不变量 | 伞 §3 |

**新增**(`/api/system-status`)是纯加法,不改任何现有端点 shape。

---

## 4. 总体架构

### 4.1 IA / 路由迁移

路由表(改后):

```
/                 → DashboardPage     ★新(进站概览)
/workspaces       → WorkspaceListPage (从 / 迁来,内部代码不动)
/scan/new         → ScanNewPage       (不动)
/p/:ws/...        → WorkspaceDetail   (不动)
/settings         → SettingsPage      ★新
/dev/components   → DevComponentsPage  (不动,dev-only)
```

TopBar NavLink 改动(`TopBar.tsx` 导航项定义处):

- Dashboard:`disabled: true` → NavLink 指 `/`
- Workspaces:目标 `/` → `/workspaces`
- Settings:`disabled: true` → NavLink 指 `/settings`

### 4.2 文件布局

```
packages/web/frontend/src/
  routes/
    DashboardPage.tsx          ← 新(进站概览)
    DashboardPage.test.tsx
    SettingsPage.tsx           ← 新(主题 + 状态 + 关于)
    SettingsPage.test.tsx
  api/
    systemStatus.ts            ← 新(GET /api/system-status hook)
    systemStatus.test.ts
    client.ts                  ← 复用现有 apiGet,无需新方法
  components/
    (复用 DSF 15 组件,无新基础组件)
  router.tsx                   ← 改(/ + /workspaces + /settings)
  components/layout/TopBar.tsx ← 改(去 disabled + Workspaces 目标)
  pages/DevComponentsPage.tsx  ← 改(补登 Dashboard / Settings 示例)

packages/web/src/shannon_web/
  api/system_status.py         ← 新(GET /api/system-status)
  app.py                       ← 注册 router
packages/web/tests/
  test_app_system_status.py    ← 新
```

### 4.3 共存策略

- 遵循伞 §6.1 增量迁移 b:本子项目新页全 Tailwind,**不向 `events.css` 追加任何规则**(DSF spec Tailwind 优先约定)。
- `WorkspaceListPage` 内部样式不动(子项目 2 已完成),仅改挂载路由。
- 新页不重用旧 class 名(`.page / .ledger` 等)。

---

## 5. 各页设计

### 5.1 Dashboard 首页(`/`)

**定位**:进站概览(伞 §5)。与 Workspaces 列表页(完整管理表)职责错开 —— Dashboard 看"动态 + 汇总 + 入口",列表页管"全量 + 检索 + 删除"。

**区块**(自顶向下):

1. **顶栏**:标题"Shannon" + 右侧主按钮「新建扫描」(跳 `/scan/new`,shadcn `Button`)。
2. **汇总数字行**(4 个 `Card`):
   - 运行中 = `workspaces.filter(w => w.status === "running").length`
   - 今日完成 = `filter(w => w.status === "completed" && isToday(w.completed_at)).length`
   - 累计漏洞 = `sum(w.vuln_count)`
   - 累计 cost = `sum(w.total_cost_usd)`(`toFixed(2)`)
3. **正在运行区**:
   - running workspace 卡片墙(每张 `Card`,整张可点 → `/p/{ws}/live`)。
   - 卡片字段:`StatusBadge(status)` + name + scan_type Badge + cost + duration(前端从 `total_duration_ms` 格式化)+ 「查看实时 →」。
   - 空态(无 running):muted 文案"当前无运行中扫描"。
4. **最近扫描区**:
   - 最近 8 条非 running workspace(按 `created_at` / `completed_at` 降序),每行:StatusBadge + name + scan_type + vuln_count + cost + 相对时间 + 跳 `/p/{ws}`(由 DefaultTab 自决 report / live)。
   - 标题右侧「查看全部 →」跳 `/workspaces`。
5. **全空态**(workspace 列表为空):引导卡"还没有扫描" + 「新建扫描」按钮。

**数据源**:单一 `GET /api/workspaces` + `useWorkspaces` hook(5s 轮询)。**不接 SSE**。

**三态**:loading(Skeleton)/ error(`ErrorState` + 重试,复用子项目 4 组件)/ loaded。

**a11y**:汇总卡 `role="group"` + `aria-label`;可点卡片 `role="button"` + tabIndex + 键盘 Enter(参考 `VulnCard` 模式);空态有清晰指引。

### 5.2 设置页(`/settings`)

三模块,全部 Tailwind + 已有 shadcn 组件,无新基础组件。

**5.2.1 主题切换**

- shadcn `Switch` 或 Segmented 控件(深 / 浅)。
- onChange → `theme.ts` 的 `setTheme()`(与 TopBar `ThemeToggle` 共用同一函数 + 同一 `localStorage` key `shannon-theme`)。两边自动同步。
- 当前值读 `getTheme()`。

**5.2.2 系统状态只读面板**(`Card` + 键值表)

字段(只读,呈现为定义列表 / 表格):

| 字段 | 来源 |
|---|---|
| AI 引擎 | `ai_provider`(claude / openai) |
| 浏览器引擎 | `browser_engine`(agent-browser / playwright) |
| Temporal | `temporal.host` + `temporal.enabled` + `last_status`(connected / error / unknown,语义色 Badge:green / yellow / red) |
| Git | `git_available`(可用 / 不可用) |
| 版本 | `version` |

- 数据源:`GET /api/system-status`(打开页拉一次 + 手动「刷新」`Button`)。**不自动轮询**(状态几乎不变)。
- fetch 失败 → 局部 `ErrorState` + 重试(不整页崩)。
- 字段缺失(null)→ 兜底"—" / "未知" Badge,不崩。

**5.2.3 关于 / 版本**

- shannon-py 版本(同 5.2.2 的 `version`)。
- 文档链接(`docs/architecture.md` / `docs/superpowers/README.md`)+ repo 链接(hardcode)。
- 可并入 5.2.2 面板底部,不单列成区。

### 5.3 后端 `GET /api/system-status` 契约(新)

**端点**:`GET /api/system-status`(新增 router,注册到 app)。

**返回 shape**:

```jsonc
{
  "ai_provider": "claude" | "openai",                  // cfg.ai_provider (env SHANNON_AI_PROVIDER)
  "browser_engine": "agent-browser" | "playwright",    // cfg.browser_engine
  "temporal": {
    "enabled": true,                                   // cfg 决定
    "host": "localhost:7233",                          // cfg / SHANNON_TEMPORAL_HOST
    "last_status": "connected" | "error" | "unknown",
    "last_error": null | string
  },
  "git_available": true,                               // 同 /health 来源
  "version": "shannon-py 0.x.y"                        // importlib.metadata
}
```

**实现约束**:

- 复用现有 `cfg` 对象字段;**不引新依赖**。
- **Temporal 不做实时 ping**:`last_status` 取启动时探测结果 + 最近一次 scan 提交时的连接结果(`ScanManager` 已持有,`scan.py` 引入 `TemporalUnavailable`)。具体字段名 plan 阶段读 cfg / `ScanManager` 定义确认。
- `version` = `importlib.metadata.version("<shannon-py 包名>")`,包名 plan 阶段读 `pyproject.toml` 确认。
- `/health` 不动(保持 `{status, git_available}`)。

---

## 6. 错误处理

- **Dashboard list fetch 失败**:整页 `ErrorState`(复用子项目 4)+ 重试(调 `useWorkspaces.refresh()`)。
- **Dashboard running / 最近区为空**:区内 muted 空态,不报错。
- **设置页 status fetch 失败**:局部 `ErrorState`(状态面板内)+ 重试;主题切换与关于区不受影响(主题读 localStorage,版本可空)。
- **状态字段缺失**(后端某字段 null):前端兜底"—" / "未知" Badge,不崩。

---

## 7. 测试计划

**前端**:

- `DashboardPage.test.tsx`:
  - 全空态(workspace 列表空)→ 引导卡 + 新建扫描按钮
  - 汇总数字聚合(运行中 / 今日完成 / 累计漏洞,构造 mock workspaces 验算)
  - running 区(有 running → 卡片可点跳 live;无 → 空态)
  - 最近区(排序 + 上限 8 + 跳列表页)
  - loading(Skeleton)/ error(ErrorState + 重试)
  - 新建扫描入口跳 `/scan/new`
- `SettingsPage.test.tsx`:
  - 主题切换:操作后 `<html>` class 变化 + 与 ThemeToggle 同步(共用 `setTheme`)
  - 状态面板:mock systemStatus 各字段渲染(ai_provider / browser_engine / temporal / git / version)
  - status fetch 失败 → ErrorState + 重试
  - 关于区版本 / 链接存在
- `systemStatus.test.ts`:hook 返回 shape + loading / error 状态
- `router.test.ts`(更新):`/` → DashboardPage、`/workspaces` → WorkspaceListPage、`/settings` → SettingsPage

**后端**:

- `test_app_system_status.py`:返回 200 + shape + 各字段存在 + temporal 子对象

**回归**:现有测试全绿(`dashboardReducer.test.ts` / 列表页 / 详情页等不动)。

**a11y 断言**:汇总卡 `role`、可点卡片键盘可达、状态面板只读字段有 label。

---

## 8. 迁移执行顺序(plan task 切分预告)

1. 后端 `GET /api/system-status` 端点 + 测试(先立契约)。
2. 前端 `systemStatus` hook + 测试。
3. 路由迁移:`router.tsx`(`/` → Dashboard 占位、`/workspaces` ← List、`/settings` 占位)+ TopBar 改 + `router.test.ts` 更新。
4. DashboardPage 实现(区块逐个)+ 测试。
5. SettingsPage 实现(三模块)+ 测试。
6. 双主题冒烟 + dev 预览页补登(Dashboard / Settings 示例)。

每 task 独立 commit,`feat(web): 子项目5·<内容>`。

---

## 9. 风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| 1 | 路由迁移 `/` → `/workspaces` 破坏书签 / 深链 | 产品未发布(内部用);router 测试覆盖;TopBar NavLink 同步改 |
| 2 | Temporal `last_status` 来源:`ScanManager` 是否已暴露 | plan 阶段读 cfg / `ScanManager` 确认;若未暴露,后端小改(已纳入本子项目"动后端"范围);不做实时 ping |
| 3 | `version` 包名 / 字段名不确定 | plan 读 `pyproject.toml` 确认 `importlib.metadata` 包名 |
| 4 | 伞 spec §5"清理归档"被砍 → 文档不一致 | 本 spec §1 / §2 记录决策 + 同步修订伞 spec §5 |
| 5 | 设置页状态面板轮询成本 | 打开页拉一次 + 手动刷新,不自动轮询 |
| 6 | Dashboard 与列表页职责重叠 | 进站概览只看动态 / 汇总 / 入口,不做检索 / 全量;列表页保留完整管理 |

---

## 10. 跨子项目约束遵守(指向伞 spec)

- §3 语义色绑定:`.ev-*` 不动;cyan / magenta / green / red / yellow 跨媒介不变量。
- §4 四条 shadcn 不变量:语义色映射层 / 事件-日志-Markdown 自定义层 / Plex 字体 / operator 风(radius ≤ 4px)。
- §6.1 增量迁移 b:新页全 Tailwind,不写 `events.css`。
- §6.2 双范式共存:新页零旧 class 依赖。
- §6.3 测试纪律:新页配 vitest + Testing Library,保留现有测试绿,不引 Storybook。
- DSF spec 导航迁移期约定:TopBar Dashboard / Settings disabled → 本子项目启用。
