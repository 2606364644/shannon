# 工作区页：命令栏升级为「区段导航」，解决子页面回不到扫描列表的问题

## 问题根因

`/p/:ws` 工作区页的命令栏（`index.tsx:201-229`）只有「仓库 / 认证 / HOST / 设置」四个出口按钮，而**扫描列表是 index 路由，命令栏里根本没有它的入口**。点进任一子页后：

- 子页（ReposTab / AuthProfilesPage / HostProfilesPage）自身没有返回按钮
- 顶部「返回工作区列表」链接跳的是工作区入口页，不是扫描列表
- 唯一出路是浏览器后退键

## 方案：命令栏 = 区段导航（segmented nav）+ 当前区段高亮

不新增面包屑或 tab 条，把命令栏分隔符右侧的导航组变成一组完整的区段切换按钮——补上「扫描」入口，并让当前所在区段高亮，同时解决「回得去」和「知道自己在哪」两件事：

```
r1:  ws-name [徽标...]        [📌] [切换] [成员] ‖ [▣ 扫描] [仓库] [认证] [HOST] [⚙]
                                                    └─ 新增，index 路由     └─ 当前区段高亮
```

- **新增「扫描」按钮**：`NavLink to={/p/${workspace}}` + `end`，icon 用 `ScanLine`，排在导航组首位
- **仓库/认证/HOST/设置四个按钮**从 `<Link>` 改为 `NavLink`（不设 `end`）——嵌套路由 `auth-profiles/:pid`、`auth-profiles/:pid/credentials/:cid` 下「认证」按钮自动保持高亮
- **高亮样式复用现有 pinned 先例**（`index.tsx:196` 的 `variant={isPinned ? "secondary" : "toolbar"}`）：active 用 `secondary` 实底，非 active 维持 `toolbar`，与置顶按钮的激活视觉完全同一语言
- 写法沿用 TopBar 已有的 NavLink render-prop 模式（`TopBar.tsx:52-67`），并带 `data-active` 便于测试：

```tsx
<NavLink to="repos" className="inline-flex">
  {({ isActive }) => (
    <Button variant={isActive ? "secondary" : "toolbar"} data-active={isActive}>
      <FolderGit2 className="size-4" /> {t("workspaceDetail.tabs.repos")}
    </Button>
  )}
</NavLink>
```

NavLink 自动输出 `aria-current="page"`，可访问性顺带解决。不再需要 `asChild`。

**为什么不选其他方案**：面包屑要在 r1 再挤一层层级、与顶部已有的「返回工作区列表」链接语义重叠；给三个子页各自加返回按钮则改动分散、且不解决「我在哪」的定位问题。区段导航零新概念、改动集中在一个文件。

## 改动清单

1. **`src/routes/WorkspaceDetail/index.tsx`**
   - import `NavLink`（react-router-dom）、`ScanLine`（lucide-react）
   - 分隔符后新增「扫描」NavLink 按钮（`end` 精确匹配，仅在 index 高亮）
   - 仓库 / 认证 / HOST / 设置四按钮改为 NavLink render-prop + `variant={isActive ? "secondary" : "toolbar"}`（设置保持 `size="icon"`）

2. **`src/locales/zh.json` / `en.json`**
   - `workspaceDetail.tabs` 下新增 `scans`：zh `"扫描"`，en `"Scans"`（现有 tabs 里没有可复用的合适键；`nav.scan` 是顶部「发起扫描」动作，语义不同）

3. **`src/routes/WorkspaceDetail/index.test.tsx`**
   - 测试路由表补注册 `host-profiles` 占位子路由（目前缺失）
   - 新增用例：在 `/p/ws/repos` 下点「扫描」链接 → 回到扫描列表占位内容
   - 新增用例：区段高亮断言（index 下「扫描」`data-active="true"` / `aria-current="page"`；repos 下「仓库」高亮且「扫描」不高亮）
   - 现有按钮 label/role 不变（NavLink 仍渲染 `<a>`，`getByRole("link")` 断言不受影响）

## 验收

- 前端 vitest：WorkspaceDetail 相关测试文件全绿，再跑整个 frontend 测试套件确认无回归
- 手动路径：`/p/ws` 点仓库/认证/HOST → 命令栏点「扫描」回到任务列表；`auth-profiles/:pid` 详情页下「认证」按钮保持高亮