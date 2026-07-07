# Web 仓库分组识别改造

> 日期：2026-07-07 ｜ 分支：feat/fork-py ｜ 关联 memory：web-repos-resource-split-status

## 问题与根因

`/repos` 页面把 `repos/` 下的 `frontend` / `backend` / `20260615` 三个**分组目录**当成了仓库。

根因在 `RepoManager.list_repos()`（`packages/web/src/shannon_web/components/repo_manager.py:60-67`）：它只遍历 `repos_dir` 下一层目录，把每个子目录都当仓库。而用户的真实结构是：

```
repos/
  frontend/      ← 分组目录（无 .git，被误判为仓库）
    act_customer_moomoo/  ← 真仓库（有 .git）
    honor/
    ...（44 个）
  backend/       ← 分组目录
    honor/        ← 与 frontend/honor 同名！
    ...（18 个）
  20260615/      ← 分组目录
    futu_auth_svr/  ...（2 个）
```

**关键约束**：`frontend/honor` 与 `backend/honor` 同名 → 扁平命名必然冲突，仓库名必须带分组前缀 `group/repo`。

## 设计决策（已与用户确认）

1. **仓库标识符**：`group/repo`（如 `frontend/honor`），自然映射 `repos_dir/frontend/honor`，`Path` 拼接天然支持嵌套。扁平旧仓库仍用 `repo`。
2. **识别标志**：目录是仓库 iff `(有 .git) or (有 .shannon-repo.json)`。分组目录两者皆无 → 跳过。并集策略保证现有测试 fixture（仅 meta 无 .git）仍被识别。
3. **嵌套深度**：固定两层 `group/repo` + 兼容一层 `repo`。不递归更深（避免 git submodule `.git` 误判 + 性能）。
4. **前端列表**：按分组折叠（section 标题 = 分组名 + 计数，下面表格行）。
5. **clone 支持选分组**：AddRepoDialog 加可选「分组」输入，clone 到 `repos_dir/<group>/<name>`。

## 后端改动

### `components/repo_manager.py`

**a. `_validate_repo_name(name)` 重写**（放开单层 `/`，保留 path-traversal 防护）：
- 允许格式：`group/repo` 或 `repo`，正则 `^[^\x00/\\]+(/[^\x00/\\]+)?$`
- 禁止：`..` 分量 / `.` 分量 / 空分量（`a//b`）/ 首尾 `/`（`/a`、`a/`）/ `\` / NUL / 多层（`a/b/c`）
- 拒绝样例：`../evil`、`a/../b`、`a//b`、`/a`、`a/`、`a/b/c`
- 合法样例：`foo`、`frontend/honor`

**b. 新增模块级函数 `_resolve_repo_dir(repos_dir, name) -> Path`**（repo_manager 与 scan_manager 共用，path-traversal 双重防护）：
```
_validate_repo_name(name)
p = (Path(repos_dir) / name).resolve()
if not p.is_relative_to(Path(repos_dir).resolve()):
    raise ValueError(f"非法仓库名：{name!r}")
return p
```
即便正则有漏，`resolve().is_relative_to()` 兜底防越界。

**c. `list_repos()` 重写**（扫两层 + 识别标志）：
```
for sub in sorted(self._dir.iterdir()):
    if not sub.is_dir() or sub.name.startswith("."): continue
    if _is_repo(sub):           # 扁平仓库
        out.append(self._repo_view(sub.name))
    else:                        # 可能分组目录，深入一层
        for sub2 in sorted(sub.iterdir()):
            if not sub2.is_dir() or sub2.name.startswith("."): continue
            if _is_repo(sub2):
                out.append(self._repo_view(f"{sub.name}/{sub2.name}"))
```
`_is_repo(d)` = `(d/".git").exists() or (d/".shannon-repo.json").exists()`。

**d. `_repo_view(name)` 加 `group` 字段**：`group = name.split("/")[0] if "/" in name else None`。

**e. `get_repo` / `pull` / `checkout` / `delete` / `_read_meta` / `_write_meta` / `_recent_events` / `_last_progress` / `_mark_failed` / `_append_event`**：把 `self._dir / name` 统一替换为 `self._repo_dir(name)`（内部调 `_resolve_repo_dir`）。逻辑不变，仅路径获取 + 校验收口。

**f. `clone(url, branch, commit, name, group=None)`**：
- `name = name or self._git.repo_name(url)`
- 若 `group`：`final_name = f"{group}/{name}"`，分别校验 group/name 合法性
- `target = self._repo_dir(final_name)`，`target.exists()` → 409
- `target.mkdir(parents=True, exist_ok=False)`
- 其余流程不变，返回 `final_name`

**g. `migrate_legacy()` 重写**：扫两层，只给「有 .git 但无 meta」的目录写 meta 纳入管理；跳过无 .git 的分组目录（修复当前把分组目录当旧仓库 migrate 的 bug）。

### `components/scan_manager.py`

`_resolve_repo_path(name)`：改用 `_resolve_repo_dir(self._repos_dir, name)` 取路径（复用 repo_manager 模块函数），保留 state 校验逻辑。`_validate_repo_name` import 可移除（被 `_resolve_repo_dir` 内含）。

### `api/repos.py`

- 所有 `/{name}` 路径参数 → `/{name:path}`（FastAPI path converter 吃含 `/` 的剩余路径）
- `CreateRepoBody` 加 `group: str | None = None`，传 `rm.clone(body.git_url, body.branch, body.commit, body.name, body.group)`

### `models.py`

- `RepoSource.value` 注释更新：仓库名（可为 `group/repo`）

## 前端改动

### `api/types.ts`
- `Repo` 加 `group?: string | null`

### `api/client.ts`
- `createRepo` body 加 `group?: string`
- `getRepo`/`deleteRepo`/`pullRepo`/`checkoutRepo` 已 `encodeURIComponent`。**真机验证**：`%2F`（encoded slash）经 Starlette path converter decode 是否正确还原为 `frontend/foo`；若 `%2F` 被 path converter 拒，降级为不 encode 直接拼 `/repos/${name}`（splat 路由吃 `/`）。

### `router.tsx`
- `{ path: "/repos/:name", ... }` → `{ path: "/repos/*", ... }`（react-router v6 splat，匹配含 `/` 的剩余路径）

### `pages/RepoDetailPage.tsx`
- `useParams<{name:string}>()` → 取 splat：`const name = useParams()["*"] ?? ""`

### `pages/ReposPage.tsx`（按分组折叠）
- `repos` 按 `group`（`null` → "未分组"）分组，保序
- `useState<Set<string>>` 记录折叠的分组（默认全展开）
- 每个 group 渲染 section：标题行（分组名 + 计数 + 折叠 chevron）+ 表格行（名称/来源/分支/大小/状态/操作）
- Link `to={`/repos/${r.name}`}`（**不** encode，让 `frontend/honor` 的 `/` 自然成路径段，splat 吃掉）
- `CloneProgress` / `doPull` / `delete` 用 `r.name`

### `components/AddRepoDialog.tsx`
- 加「分组」可选 Input（placeholder "如 frontend / backend，留空则放顶层"）
- `createRepo({ git_url, branch, commit, group: group.trim() || undefined })`
- 成功 `onCreated(r.name)`（r.name 为 `group/repo` 或 `repo`）

### `components/ScanFormFields.tsx`
- 仓库下拉 `SelectItem` value=`r.name`（已含 `group/repo`），显示 `{r.name} — {r.source?.url ?? r.state}`
- 可选增强：按 group 排序 + 组间分隔。先简单显示 `group/repo`。

## 测试改动

### `tests/test_repo_manager.py`
- `test_clone_rejects_path_traversal_name`：`"a/b"`、`"x/y"` 从拒绝列表移除（现合法，单独断言成功 clone 到 `repos/a/b`）。保留 `"../evil"`、`.`、`..`、`x\y`。新增拒绝：`a//b`、`/a`、`a/`、`a/b/c`、`a/../b`。
- 新增 `test_clone_with_group`：`clone(url, None, None, None, group="frontend")` → target `repos/frontend/foo`，返回 name=`frontend/foo`。
- 新增 `test_list_repos_groups`：造 `repos/frontend/foo/.git`、`repos/backend/honor/.git`、`repos/frontend/honor/.git`，断言 list 含三个 `group/repo` 名，**不含** `frontend`/`backend` 分组目录。
- 新增 `test_list_repos_skips_group_dirs`：分组目录无 .git 无 meta 不入列表。
- 新增 path-traversal 兜底测试：`_resolve_repo_dir` 对 `../evil` 即便绕过正则也拒（`is_relative_to`）。

### `tests/test_api_repos.py`
- 现有扁平 fixture（`_app` 造 `repos/<name>` 仅 meta）仍 pass（识别策略兼容）。
- 新增 `test_get_repo_grouped`：`GET /api/repos/frontend/foo`（`{name:path}`）。

### `frontend/src/pages/ReposPage.test.tsx`
- 加分组场景：mock 返回 `[{name:"frontend/foo", group:"frontend",...},{name:"backend/foo", group:"backend",...}]`，断言两个分组 section 都渲染、`frontend/foo` 与 `backend/foo` 同时可见不冲突。

## 兼容性

- 扁平仓库（`repos/foo/.git`）仍识别为 `name="foo"`。
- 现有 test fixture（仅 `.shannon-repo.json` 无 .git）仍识别。
- `migrate_legacy` 只动有 .git 的，不再误 migrate 分组目录。
- 用户已 clone 的 64 个分组仓库无需任何数据迁移，重启即正确识别。

## 验证

1. `pytest packages/web/tests/test_repo_manager.py packages/web/tests/test_api_repos.py packages/web/tests/test_scan_manager.py -q`
2. 前端 `cd packages/web/frontend && npx vitest run src/pages/ReposPage.test.tsx && npx tsc --noEmit`
3. 真机：浏览器开 `/repos`，确认三个分组折叠 section、64 个仓库正确列出、无 `frontend`/`backend`/`20260615` 假仓库；点进详情；发起扫描选 `frontend/honor`；添加新仓库选分组。

## 不在本次范围

- 递归 >2 层分组（暂不支持，避免 submodule 误判）。
- 仓库重命名 / 跨分组移动。
