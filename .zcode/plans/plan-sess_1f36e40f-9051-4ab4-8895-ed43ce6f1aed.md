# 概览页功能健壮性优化

范围：`packages/web/frontend`，4 个文件。已核实：`progress/cost/eventsCount/startedAt/totalDuration` 等键仍被 `DashboardPanel`/`ScanProgressOverview` 使用，不在清理范围。

## 1. 加载失败错误态 — `src/pages/DashboardPage.tsx`

- `useAsync(listAllScans, [])` 解构出被丢弃的 `error`。
- **首次加载失败**（`error && data.length === 0`）：在骨架屏判断之后、空态之前插入错误分支 — `Empty` 组件 + 重试按钮（复用 `doRefresh`）。文案新增 `dashboard.errors.title`（zh「加载失败」/ en "Failed to load"），hint 复用现有 `dashboard.errors.loadFailed`（带 `{{error}}`）。
- **有数据时后台刷新失败**：新鲜度行将「N 秒前更新」替换为 destructive 内联提示，新增 `dashboard.errors.stale`（zh「刷新失败 · 展示缓存数据」/ en "Refresh failed — showing cached data"）。
- 小 guard：admin 无 ws 空态分支加 `!wsError`，workspaces 拉取失败时不误显「新建工作区」入口。

## 2. 有运行中扫描时自动轮询 — `src/pages/DashboardPage.tsx`

```tsx
const hasRunning = data.some((s) => s.is_running || s.status === "running");
useEffect(() => {
  if (!hasRunning) return;
  const id = setInterval(() => { if (!document.hidden) void refresh(); }, 10_000);
  return () => clearInterval(id);
}, [hasRunning, refresh]);
```

- 10s 对齐现有新鲜度 tick；tab 隐藏暂停；无运行中扫描不轮询（回归安静）；最后一次轮询拿到完成态后自动停。
- 用未过滤的 `data` 判断（用户把 running 行筛掉时轮询不中断）。
- `useAsync.refresh` 不置 `loading=true` → 无骨架屏闪烁；`refreshedAt` 由现有 `useEffect([data])` 自动更新。运行中卡片的进度条、耗时、状态随之变新鲜。

## 3. i18n 死键清理 — `src/locales/zh.json` + `en.json`

删除（grep 验证 0 代码引用）：`dashboard.subtitle`、`dashboard.summaryAria`、`dashboard.stats.totalVulns`、`dashboard.viewAll`、`dashboard.noRunning`、`dashboard.noRunningAgents`、`dashboard.noWorkspace.{title,hint}`。

## 4. 测试 — `src/pages/DashboardPage.test.tsx`

- 新增：listAllScans reject → 错误态渲染 loadFailed 文案 + 重试按钮，点重试再次调用 listAllScans。
- 新增：fake timers — running 场景推进 10s 后 listAllScans 被再次调用（轮询生效）。
- 既有 7 个测试不应回归。

## 验证

- `npx vitest run src/pages/DashboardPage.test.tsx`
- `npm run build`（tsc 确认删键无引用破坏）