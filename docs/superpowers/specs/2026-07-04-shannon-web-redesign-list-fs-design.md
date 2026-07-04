# 子项目 2 · 列表页重做 + 文件浏览器

> 上位 spec：`docs/superpowers/specs/2026-07-04-shannon-web-redesign-design.md`（IA / 视觉 / 四条 shadcn 约束 / 子项目分解 / 迁移策略 / 跨子项目约束）。DSF（子项目 1）已落地：shadcn 组件库（`@/components/ui/*` 含 button/dialog/table/badge/skeleton/tooltip/...）、`<AppShell>`+`<TopBar>` 已全局套、双主题 token 层、`cn()` / `theme` lib 就绪。本子 spec 聚焦列表页重做 + 文件浏览器 + 配套后端改动，复用 DSF 组件库，遵循增量迁移（不动其他业务页内部）。

## 范围与完成定义

**做**：
1. 后端 `GET /api/fs/browse`（列目录）+ `WebConfig.fs_roots`（env `SHANNON_FS_ROOTS`）
2. 后端 `DELETE /api/workspaces/{ws}`（删 workspace 目录）
3. 后端 `list_workspaces` 补返字段（cost/duration/links/vuln_count）对齐前端 type
4. 前端 `<FileSystemPicker>` 组件（Dialog 模态，可复用，子项目 3 ScanNewPage 消费）
5. 前端 WorkspaceListPage 用 TanStack Table + shadcn Table 重做（搜索/筛选/排序/取消/删除/expandable/轮询保留 state/空态/loading/上次刷新）

**不做**：
- ScanNewPage 集成 FileSystemPicker（留子项目 3）
- 其他业务页（详情 5 tab / Dashboard / Settings）内部重做
- 后端 list_workspaces 分页/搜索 query（client-side 过滤够用，YAGNI）

**完成定义**：
- `GET /api/fs/browse?path=/` 返根目录 entries；路径穿越/越界/不存在/无权限各有正确状态码。
- `DELETE /api/workspaces/{ws}` 删目录后 list 不再返；运行中 ws 拒绝 409。
- `GET /api/workspaces` 返字段含 `total_cost_usd` / `total_duration_ms` / `vuln_count` / `links`。
- 列表页：搜索/筛选/排序/expandable/取消/删除全可用；5s 轮询不冲掉 table state；空态/loading/上次刷新显示。
- `<FileSystemPicker>` 在 `/dev/components` 预览页可手选目录（冒烟）。
- DSF 测试持续绿；新测试全绿。

---

## 1. 后端改动

### 1.1 `list_workspaces` 补字段（`components/workspaces_indexer.py`）

现有返字段（`list_workspaces`）：`{name, scan_type, status, vuln_counts(dict), created_at, completed_at, is_correlation}`。**缺**：`total_cost_usd` / `total_duration_ms` / `vuln_count(number)` / `links`。

改 `list_workspaces`：对每个 ws 取 `data = mgr.get_session_data(ws_path)`，补字段：
- `total_cost_usd`：`data.get("metrics", {}).get("total_cost_usd")`（float 或 None）
- `total_duration_ms`：`data.get("metrics", {}).get("total_duration_ms")`（int 或 None）
- `links`：`data.get("links", {})`（`{parent_workspace?, child_workspaces?}`，默认 `{}`）
- `vuln_count`：`sum(vuln.values()) if vuln else 0`（聚合 `vuln_counts` dict → number）

保留现有字段（`vuln_counts` dict 详情页用、`is_correlation`、`created_at`、`completed_at`、`scan_type`、`status`）。

> 性能：list 已对每个 ws 调 `get_session_data`（触发读，当前丢弃返回值）。本次改为取返回值字段，**无额外 I/O**。

### 1.2 `GET /api/fs/browse`（新 `api/fs.py`）

新 router `APIRouter(prefix="/api/fs", tags=["fs"])`，注册到 `app.py`（`app.include_router(fs.router)`，在 `/health` 之前、`_mount_frontend` 之前）。

```
GET /api/fs/browse?path=<abs>
→ 200 {
     path: str,                       # 规范化后绝对路径
     parent: str | null,              # 上级目录绝对路径（根目录则 null）
     entries: [
       { name: str, type: "dir" | "file", size?: int, mtime?: int }
     ],
     truncated?: bool                 # entries > 5000 时 true
   }
→ 400 非绝对路径 / 穿越尝试（含 .. 未规范化）
→ 404 路径不存在
→ 403 无权限读（PermissionError）
→ 409 越界（path 不在 fs_roots 内，仅配了 SHANNON_FS_ROOTS 时）
```

实现要点：
- 入参 `path: str`（query）。
- **规范化 + 穿越防护**：`Path(path).is_absolute()` 为 False → 400（跨平台绝对路径判定）；`resolved = Path(path).resolve()`（消除 `..`）；后续以 `resolved` 为准。
- **allowlist**：`cfg = request.app.state.config`；`roots = cfg.fs_roots`（list[Path]）；若 `roots` 非空，检查 `resolved` 是否在任一 root 下（`resolved == root or root in resolved.parents`）；不在 → 409。
- **存在性 + 类型**：`resolved.is_dir()` 否则（不存在 → 404，是文件 → 400 "not a directory"）。
- **列目录**：`os.scandir(resolved)`，每 entry `{name, type: "dir" if is_dir else "file", size: stat.st_size if file, mtime: int(stat.st_mtime)}`；包 dotfiles。
- **排序**：目录优先，同类按 name 字母（`sorted(entries, key=lambda e: (e["type"] != "dir", e["name"]))`）。
- **截断**：`MAX_ENTRIES = 5000`；超则截断 + `truncated: true`。
- **parent**：`str(resolved.parent)` 若 `resolved != resolved.parent`（即非根），否则 `None`。
- **PermissionError** → 403；**FileNotFoundError** → 404；**OSError 其他** → 400。

### 1.3 `WebConfig.fs_roots`（`config.py`）

`WebConfig.__init__` 加：
```python
self.fs_roots: list[Path] = [
    Path(p).resolve() for p in os.environ.get("SHANNON_FS_ROOTS", "").split(",") if p.strip()
]
```
默认空 list = 整机可见（本地直跑场景）；配了则约束。

### 1.4 `DELETE /api/workspaces/{ws}`（`api/workspaces.py` 加端点）

```
DELETE /api/workspaces/{ws}
→ 200 { deleted: ws }
→ 404 workspace 不存在
→ 409 该 workspace 仍在运行（提示先 DELETE /api/scan/{ws} 取消）
```

实现：
- `p = _workspace_path(request, ws)`（已存在辅助，404 if 不存在）。
- **运行中拒绝**：`active = request.app.state.scan_manager.active_pids()`（dict[str,int]）；`if ws in active and WorkspacesIndexer._pid_alive(active[ws])` → 409 `{"detail": "workspace running, cancel scan first"}`。（`WorkspacesIndexer._pid_alive` 是 staticmethod，`workspaces.py` 顶部 `from ..components.workspaces_indexer import WorkspacesIndexer`。）
- 删目录：`shutil.rmtree(p)`。
- 清 indexer pid 缓存：`request.app.state.indexer.set_active_pid(ws, None)`。
- 返 `{"deleted": ws}`。

> `list_workspaces` 是实时扫目录，删目录后下次 list 自然不列；indexer._active_pids 显式清避免残留。

---

## 2. 前端 types / api 对齐

### 2.1 `api/types.ts` 新增

```ts
export interface FsEntry {
  name: string;
  type: "dir" | "file";
  size?: number;
  mtime?: number;
}
export interface FsBrowseResult {
  path: string;
  parent: string | null;
  entries: FsEntry[];
  truncated?: boolean;
}
```

Workspace type 已含 `total_cost_usd?` / `total_duration_ms?` / `links?`（DSF 定义）；补 `vuln_count?`（已含）+ 新增可选 `is_correlation?: boolean`（后端返，列表页 expandable 判定用，等价 `scan_type === "correlation"` 但避免重复判断）。

### 2.2 `api/client.ts` 新增

```ts
export async function browseFs(path: string): Promise<FsBrowseResult> {
  return apiGet<FsBrowseResult>(`/fs/browse?path=${encodeURIComponent(path)}`);
}
export async function deleteWorkspace(ws: string): Promise<{ deleted: string }> {
  return apiDelete(`/workspaces/${encodeURIComponent(ws)}`);
}
export async function cancelScan(ws: string): Promise<{ cancelled: string }> {
  return apiDelete(`/scan/${encodeURIComponent(ws)}`);
}
```

> `apiDelete` 已存在（`api/client.ts:23`），直接复用。

---

## 3. `<FileSystemPicker>` 组件

`src/components/FileSystemPicker.tsx`：

```ts
interface Props {
  value: string;                          // 当前绝对路径（受控）
  onChange: (abs: string) => void;        // 确认时回填
  title?: string;                         // 默认 "选择代码目录"
  triggerLabel?: string;                  // 默认 "📁 浏览"
}
```

**结构**（shadcn `<Dialog>`）：
- Trigger：`<Button variant="outline" size="sm">{triggerLabel}</Button>`（ScanNewPage 放路径输入框旁）。
- Dialog 内容：
  - Header：`<DialogHeader><DialogTitle>{title}</DialogTitle></DialogHeader>`
  - 工具行：🏠 按钮（跳 home）+ 面包屑（当前 path 按 `/` 分段，每段可点跳上级）+ ↻ 刷新（重新 GET browseFs(currentPath)）
  - 最近书签：从 localStorage `shannon-fs-recent` 读最近 5 个确认过的路径，渲染 chip；点击 → setCurrentPath + GET
  - 列表区：entries 渲染（`📁 name` 目录 / `📄 name` 文件，文件灰显）；双击目录 → 进入（setCurrentPath + GET）；单击 → 选中（highlight）；空目录显 `<Empty title="空目录" />`；error（403/404/409）inline 显 `<div class="text-red">` 不关 Dialog
  - 路径输入：`<Input value={manualPath} ... />`，失焦或 Enter → setCurrentPath(manualPath) + GET（若 404 inline 提示）
  - Footer：`<DialogFooter><Button variant="ghost">取消</Button><Button variant="default" disabled={selectedType !== "dir"} onClick={confirm}>选择此目录</Button></DialogFooter>`

**状态机**：
- `currentPath: string`（当前浏览目录，初值 = `value` 或 home 或 "/")
- `result: FsBrowseResult | null`（currentPath 的 browse 结果）
- `error: string | null`（inline 错误）
- `selected: string | null`（单击选中的 entry full path）
- `manualPath: string`（路径输入框值）

**初始路径**：Dialog 打开时，`currentPath` 初值 = `value`（非空）或 localStorage 最近第一个或 `"/"`。home（`os.path.expanduser("~")`）是后端概念——前端通过 GET `/api/fs/browse?path=~/` 不行（后端 Path 不展开 ~）；home 由后端 1.2 browse 时若 path="~" 展开为 home。**简化**：前端 home 按钮直接 GET `path=~`，后端 browse 特判 `~` → `os.path.expanduser("~")`（加一行）。

**确认（confirm）**：
1. `onChange(selectedPath)`
2. 写 localStorage `shannon-fs-recent`：去重 + 最新在前 + 截断 5 个
3. close Dialog

**localStorage**：key `shannon-fs-recent`，value `JSON.stringify(string[])`。

**测试**（MSW mock `GET /fs/browse`）：
- 打开 → GET currentPath → 渲染 entries
- 双击目录 → 进入子目录（新 GET）
- 面包屑点段 → 跳上级
- 单击目录选中 + "选择此目录"启用；单击文件 → 不启用
- 确认 → onChange 回填 + localStorage 写书签 + 关 Dialog
- 重开 Dialog → 书签 chip 显示
- 路径输入框敲不存在路径 → 404 inline
- 403（无权限目录）→ inline 不关

---

## 4. WorkspaceListPage 重做（DataTable）

`src/pages/WorkspaceListPage.tsx` 重写（删旧裸 table）。

**依赖**：`@tanstack/react-table`（子项目 2 新装：`npm install @tanstack/react-table`）。

**数据 hook**：`useWorkspaces()` 自定义 hook（`src/api/useWorkspaces.ts`）：
- 5s 轮询 `GET /api/workspaces`
- 返 `{ data: Workspace[]; loading: boolean; lastUpdated: Date | null; refresh: () => void; error: string | null }`
- 实现：`useEffect` setInterval 5s + 手动 refresh；`lastUpdated` 每次 fetch 成功更新

**DataTable 列定义**（`ColumnDef<Workspace>[]`）：
| 列 | 取值 | 排序 | 备注 |
|---|---|---|---|
| workspace | `name`（`<Link to={/p/${name}}>`） | ✓ | 行首 status-bar 色（`<span class="status-bar status-{status}">`，复用旧 class） |
| status | `<StatusBadge status correlation={is_correlation} />` | ✓ | 复用 DSF 前已存在的 StatusBadge（旧组件，迁移期内） |
| type | `scan_type` | ✓ | 白盒/黑盒/联动 |
| vulns | `vuln_count ?? "—"` | ✓ | |
| cost | `total_cost_usd != null ? $${toFixed(2)} : "—"` | ✓ | |
| time | `created_at`(unix → `toLocaleString()`) | ✓ | 默认降序 |
| 操作 | 行内按钮 | — | running → "取消"；其余 → "删除" |

**工具栏**（表格上方）：
- 搜索框：`<Input placeholder="搜索 workspace..." />` → 全局过滤 name（`useState` + `globalFilter`）
- status 筛选：`<Select>`（all / running / completed / failed / killed / crashed / interrupted）
- type 筛选：`<Select>`（all / whitebox / blackbox / correlation）
- 右侧：`上次刷新 HH:MM:SS`（trace 色）+ 手动刷新 `<Button variant="ghost" size="icon">↻</Button>`
- "+ 新建扫描" `<Button>` → `/scan/new`

**expandable row**（correlation）：
- `getExpandedRowModel` 启用；`is_correlation` 行可展开（`enableExpanding`）
- 展开内容：`links.child_workspaces` 列表，每项 `<Link to={/p/${c}}>` ；无子 ws 显 "无子白盒"

**操作列**：
- "取消"（running）→ 点开 shadcn `<Dialog>`："取消扫描 {ws}？进度会丢失。" → 确认 `cancelScan(ws)` → 成功后 refresh + toast
- "删除"（非 running）→ shadcn `<Dialog>`："删除 workspace {ws}？目录和产物永久删除。" → 确认 `deleteWorkspace(ws)` → 成功 refresh + toast；409（仍在跑）→ toast 提示先取消

**轮询保留 state**：
- DataTable state（`sorting` / `columnFilters` / `globalFilter` / `expanded` / `pagination`）用 `useState` 持久化（`useMemo(() => ({ sorting, ... }), [...])`）
- 轮询只换 `data`（useWorkspaces 返），table state 不重置

**空态**：data 空且 loading=false → `<Empty icon="∅" title="no workspaces" hint="新建一个扫描开始"><Button>+ new scan</Button></Empty>`（Empty CTA 跳 `/scan/new`）

**loading**：首次 loading 且 data 空 → `<Skeleton>` 行 × 5

**错误**：fetch error → 顶部 `<div class="text-red">` 横幅 + 重试按钮（不静默吞）

> 旧 `<table class="ledger">` + `fmtMs` 等删除（迁移期内本页是首个迁到 Tailwind 的业务页；旧 `.ledger` class 随本页迁除，但其他页仍消费 `.ledger` 时 events.css 保留——实际 OverviewTab 的 agent-table 用 `.ledger`，故 events.css 仍留）。

---

## 5. 测试

### 后端
- `tests/test_fs_browse.py`（新）：
  - tmp_path fixture 造 `{root, root/sub, root/sub/file.txt, root/.hidden, root/empty_dir}`
  - 测：list 返 entries 含 dir+file+dotfile；parent 正确；根目录 parent=null；排序目录优先；path 含 `..` → 400；不存在 → 404；非绝对 → 400；`SHANNON_FS_ROOTS` 配了 + path 越界 → 409；`path=~` 展开为 home
  - `truncated`：mock scandir 返 >5000 → truncated=true（用 monkeypatch 或小阈值 fixture 造 100 个 + 临时改 MAX_ENTRIES）
- `tests/test_workspaces_delete.py`（新）：
  - 造 ws 目录 + session.json；DELETE → 200 + 目录删 + list 不返
  - 运行中 ws（mock scan_manager.active_pids 返 {ws: pid} + pid alive）→ 409
  - 不存在 ws → 404
- `tests/test_workspaces_indexer.py`（扩）：
  - fixture 造 ws 含 session.json（metrics.total_cost_usd / total_duration_ms / links.child_workspaces）
  - 断言 list_workspaces 返 total_cost_usd / total_duration_ms / vuln_count / links 字段对齐

### 前端
- `src/components/FileSystemPicker.test.tsx`（新，MSW mock GET /fs/browse）：
  - 打开 → entries 渲染；双击目录进入；面包屑跳上级；单击选中；文件不可确认；确认回填 + 书签；路径输入 404 inline
- `src/pages/WorkspaceListPage.test.tsx`（重写，MSW mock GET /workspaces + DELETE）：
  - DataTable 渲染列；搜索过滤；status/type 筛选；列排序；correlation expandable；取消 Dialog → cancelScan；删除 Dialog → deleteWorkspace；409 提示；空态；loading skeleton；上次刷新时间显示

---

## 6. 任务拆解（writing-plans 种子）

1. 后端 `WebConfig.fs_roots`（env）+ `api/fs.py` browse 端点 + app.py 注册（TDD：test_fs_browse）
2. 后端 `DELETE /api/workspaces/{ws}`（删目录 + 运行中拒绝 + indexer 清）（TDD：test_workspaces_delete）
3. 后端 `list_workspaces` 补字段（cost/duration/links/vuln_count）（TDD：扩 test_workspaces_indexer）
4. 前端 `api/types.ts`（FsEntry/FsBrowseResult）+ `api/client.ts`（browseFs/deleteWorkspace/cancelScan/apiDelete）
5. 装 `@tanstack/react-table`
6. 前端 `<FileSystemPicker>` 组件 + 测试（MSW）
7. 前端 `useWorkspaces` hook（轮询 + lastUpdated + refresh）
8. 前端 WorkspaceListPage DataTable 重写 + 工具栏 + expandable + 取消/删除 Dialog + 空态/loading/错误（MSW）
9. `/dev/components` 预览页加 FileSystemPicker demo
10. 冒烟回归（DSF 测试绿 + 列表页全功能 + 手选目录）

---

## 7. 风险

| 风险 | 缓解 |
|---|---|
| `list_workspaces` 补字段需读 session.json metrics（ws 多时成本） | §1.1 已无额外 I/O（复用已读的 get_session_data 返回值）；ws 数 < 几百实践可接受 |
| TanStack Table state 在 5s 轮询重渲染时重置 | §4 state 用 useState 持久化；轮询只换 data 不重置 state |
| fs/browse 路径穿越 | `Path.resolve()` 消除 `..` + `fs_roots` allowlist 双重防护；非绝对输入 400 |
| 删除 workspace 误删用户数据 | Dialog 二次确认 + 拒删运行中 ws（409） |
| 后端 list 字段对齐后前端旧 type 偏差暴露 | §2 已对齐 type（vuln_count 已可选、cost/duration/links 已含） |
| FileSystemPicker home 路径（`~` 展开） | 后端 browse 特判 `path=~` → expanduser；前端 home 按钮发 `path=~` |
| `.ledger` class 随本页迁移移除，其他页（OverviewTab）仍用 | events.css 保留（迁移期）；仅本页改用 Tailwind，不动 events.css 规则 |
