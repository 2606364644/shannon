# Web 工作区「关联仓库」- 设计文档

- **日期**：2026-07-29
- **分支**：`feat/fork-py`
- **状态**：方向已确认，待 plan + 实现
- **依赖**：P2（repos 资源化 / per-ws 隔离）+ ws-scan 解耦（1:N）+ IA 重设计均已落地
- **关联**：`specs/2026-07-27-web-workspace-scan-decoupling-design.md`、`specs/2026-07-27-workspace-scan-ia-redesign-design.md`、`web-repos-resource-split-status`

## 1. 背景

P2 把仓库提升为 per-ws 一等资源后，仓库**物理隔离在 `workspaces/<ws>/repos/<group>/<name>`**（`repo_manager.py:108-120` `_repos_root(ws)`），由 `RepoManager` 管 clone/pull/checkout/delete，每个仓库一份 `.supernova-repo.json`。

由此产生一个 gap：**两个工作区想扫同一个仓库，今天必须各自 clone 一份**（磁盘与 clone 时间双倍浪费）。`workspace.json` 只存 `{name, created_at, owner, description?}`（`scan_store.py:125-133`），**不记录任何仓库字段**；扫描时在 `ScanNewPage` 里先选 ws、再 `listRepos(workspace)` 拉该 ws 的私有仓库（`ScanFormFields.tsx:78-84`）。

用户诉求：**admin 能给工作区「关联」一个已存在的仓库目录（绝对路径），一个 ws 可关联多个仓库目录，多个 ws 可关联同一条路径**（共享一份磁盘克隆，免重复 clone）。

**用户确认的心智模型**：
- 关联模型 = **按路径关联（增量）**——保留每 ws 私有克隆不动，新增「关联」机制；**不**做共享仓库注册表 / 多对多成员表（那改动大），也**不**做 git worktree（那最复杂）。
- 关联路径来源 = **任意已存在路径**（admin 直接指定绝对路径，校验存在+目录；不新增共享克隆入口）。
- 一个 ws 可关联**多个**仓库目录。

## 2. 目标 / 非目标

### 目标
- ws 的仓库集合 = **私有克隆**（`<ws>/repos/<name>`，现状零改）∪ **关联仓库**（指向任意已存在目录路径）。
- admin 可把一个绝对路径关联进某 ws；该关联仓库出现在该 ws 的仓库列表与扫描源下拉里。
- **一个 ws 可关联多个**仓库目录；**多个 ws 可关联同一条**路径（共享一份磁盘拷贝）。
- 关联仓库可作为 scan 的 `source.kind=repo` 目标，解析到其存储路径。
- 私有克隆的现有行为（clone/pull/checkout/delete）完全不变。

### 非目标
- git worktree（per-ws 独立工作树 / 独立分支）—— 用户已否。
- 共享仓库注册表 / 仓库多对多成员表（全局仓库 + membership）—— 用户已否。
- 关联仓库的 **pull / checkout / 切分支**（共享路径下会跨 ws 互相干扰）—— v1 关联仓库只读（见 §7 决策 1）。
- 按 repo 反查 scan 列表 / repo↔scan 反向索引。
- 关联仓库的 clone.ndjson 进度流（关联非 clone 产物，无进度）。

## 3. 数据模型

### 3.1 存储位置：独立 `linked_repos.json`

关联记录**不放进 `workspace.json`**。理由：`write_workspace_meta`（`scan_store.py:118-133`）是**全量重写**（不读旧值合并），而 lifespan 的 legacy 迁移会重写已存在 ws 的 `workspace.json` —— 放进去会在重启迁移时被擦除。

落在 ws 级独立文件，`RepoManager` 独占读写：

```
workspaces/<ws>/linked_repos.json
```

### 3.2 schema

```json
{
  "links": [
    {
      "name": "ftoa-gateway",
      "path": "/abs/path/to/repo",
      "linked_at": "2026-07-29T08:00:00+00:00"
    }
  ]
}
```

- `name`：ws 内唯一，`repo` 或 `group/repo`（复用 `_validate_repo_name` `repo_manager.py:38-52`）。**与私有克隆共用命名空间**，禁碰撞。
- `path`：admin 提供的绝对路径，存 `.resolve()` 后的规范形式；允许符号链接。
- `linked_at`：关联时刻 ISO（UTC）。
- 文件缺失 / 损坏 → 降级为空 `links`（与其它 meta 读法一致，绝不抛）。

### 3.3 状态 IO：模块级函数（零互相注入）

在 `repo_manager.py` 内新增模块级读写函数（`read_linked_repos(ws_dir)` / `write_linked_repos(ws_dir, links)` / `resolve_linked_repo_path(workspaces_dir, ws, name) -> str | None`），`RepoManager`（list/link/unlink）与 `ScanManager`（解析）**共用**，避免 `ScanManager → RepoManager` 实例注入。`scan_manager.py` 已从 `repo_manager` 模块 import `_resolve_repo_dir` / `_validate_ws_segment`，沿用同一模式。

## 4. RepoManager 与解析

### 4.1 新增方法

- `link_repo(ws, name, path) -> dict`：
  1. `_validate_ws_segment(ws)` + `_validate_repo_name(name)`；
  2. **去重**：name 不得与该 ws 现有 repo（私有克隆 ∪ 既有关联 = `list_repos` 返回名集合）重复 → 抛 `RepoExists`；
  3. path 校验：`Path(path).resolve()`，须 `.is_dir()`（不存在 / 非目录 → `ValueError`）；
  4. 追加写 `linked_repos.json`（含 `linked_at`）；返回 `_linked_repo_view`。
- `unlink_repo(ws, name)`：从 json 移除该记录；不存在 → `ValueError`。**绝不碰磁盘文件**。
- `_linked_repo_view(ws, entry) -> dict`：`{name, group, linked: True, source: {kind: "linked"}, state: "ready", path?, cloned_at: entry.linked_at, ...}`；若 `path/.git` 存在，复用 `_infer_from_git`（`repo_manager.py:494-508`）补 `source.branch` / `commit` / `size_bytes`。
- `list_linked_repos(ws) -> list[dict]`：读 json 逐条 `_linked_repo_view`。
- `list_repos(ws)`（`repo_manager.py:130-156`）扩展：返回 **私有克隆 ∪ list_linked_repos**。
- `get_repo(ws, name)`（`repo_manager.py:158-167`）扩展：先查私有克隆，未中再查关联；关联项不附 `recent_events`（无 clone.ndjson）。
- `delete(ws, name)`（`repo_manager.py:343-349`）扩展：**按类型分叉**——若为关联 → `unlink_repo`（不删文件）；若为私有克隆 → 现有 `rmtree`；都不是 → no-op（404 由路由给）。

### 4.2 扫描解析

`ScanManager._resolve_repo_path`（`scan_manager.py:376-398`）改两段式：

1. 先查关联：`resolve_linked_repo_path(workspaces_dir, ws, name)` 命中 → 返回其 `path`（**无 state 校验**，关联仓库无 clone 状态）；
2. 否则现有私有克隆路径：`_resolve_repo_dir(<ws>/repos, name)` + `state==ready` 校验（不变）；
3. 都不中 → `ValueError("仓库不存在：{name}")`。

`active_repo_sources()`（`scan_manager.py:82-92`）无需改：派生 `(ws, name)` 对关联仓库同样生效（name 维度引用锁，扫描中禁 unlink/rmtree）。

## 5. API（`api/repos.py`）

新增端点：

- `POST /api/workspaces/{ws}/repos/link` —— body `{name: str, path: str}` → `repo_manager.link_repo`；**admin-only**（`Depends(require_admin)`）；成功 201 返回 view。
  - 校验失败（非法 name / 路径不存在 / 非目录）`ValueError` → **422**；
  - 重名 `RepoExists` → **409**（与 clone「仓库已存在」同语义，`repos.py:42-43`）。

复用端点（行为自动含关联，无需改路由签名）：

- `GET /{ws}/repos` —— list 自动含关联（RepoManager 已合并）。
- `GET /{ws}/repos/{name:path}` —— get 兼顾关联。
- `DELETE /{ws}/repos/{name:path}` —— `rm.delete` 自动按类型 unlink / rmtree；扫描引用中（`(ws,name) in active_repo_sources()`）→ 409（现有逻辑，对关联同样生效）。

受限端点（关联仓库只读）：

- `POST /{ws}/repos/{name:path}/pull` 与 `.../checkout` —— 命中关联仓库 → **405「关联仓库为共享路径，不可在此修改」**（`rm.pull`/`rm.checkout` 内识别关联 → 抛特定异常，路由映射 405）。
- `GET /{ws}/repos/{name:path}/events` —— 关联仓库无 clone.ndjson → 返回**空流（200，不产事件即结束）**，不报 404（`get_repo` 对关联成功，保持端点行为一致）。

路由声明顺序不变：`POST /{ws}/repos/link` 与现有 `POST /{ws}/repos`（clone）、`POST /{ws}/repos/{name}/pull|checkout` 不冲突（无 `POST /{ws}/repos/{name:path}` 通配路由）。

## 6. 前端

- `components/AddRepoDialog.tsx`：加**模式切换**（分段控件 "克隆" / "关联已有目录"）。
  - 关联模式：两栏 `name` + `path`（path 文本框，placeholder 提示绝对路径）→ 调 `linkRepo(ws, {name, path})`；成功后关闭并刷新列表。
  - 复用现有 ESC/ busy 防误关逻辑。
- `routes/WorkspaceDetail/ReposTab.tsx`（仓库列表行）：
  - 关联仓库显「关联」徽标（区分私有克隆）；
  - 关联仓库行**隐藏 / 禁用** pull、checkout 按钮；
  - 删除按钮文案在关联项上改「取消关联」（仍调 `deleteRepo`，后端自动 unlink）。
- `api/client.ts`：加 `linkRepo(ws, body)`；`Repo` / `RepoDetail` 类型加 `linked?: boolean`，`source.kind` 联合类型加 `"linked"`。
- `components/ScanFormFields.tsx`：`listRepos(workspace)` 已返合并列表 → 关联仓库**自动**出现在扫描源下拉（可加同款「关联」徽标）。零或极小改动。

## 7. 关键决策（brainstorming 拍板）

1. **关联仓库 = 只读**：共享路径下禁 pull/checkout（避免 ws-A 切分支影响 ws-B 的扫描目标）。要 per-ws 独立分支 = worktree 方案，用户已否。
2. **link / unlink = admin-only**：关联任意磁盘路径较敏感（可指向任意目录，含其它 ws 的私有克隆），比 clone（走配置好的 git 远端）提一级权限。现有 clone/pull/checkout/delete 仍 `workspace_member`。
3. **独立 `linked_repos.json`**：不污染 `workspace.json`、抗 legacy 迁移全量重写擦除（见 §3.1）。
4. **同一路径可在不同 ws 关联**（甚至同 ws 用不同 name 关联两次，无害）；**同名则禁**（ws 内 name 唯一）。

## 8. 边界与不变量

- **关联 = 仅记录引用，绝不复制 / 不删源文件**。unlink 只移 json 记录。
- **ws 内 repo name 唯一**（私有克隆 ∪ 关联）；link 时强校验。
- **path 必须存在且是目录**（link 时校验）；后续扫描时若 path 已被外部删除，scan 解析报「仓库不存在」（与私有克隆被删同处理，不特殊兜底）。
- **扫描引用锁跨类型生效**：`(ws, name)` 在跑则禁 unlink / rmtree。
- **共享路径并发扫描安全**：扫描对 repo 目录是只读消费，多 ws / 多 scan 并发扫同一路径互不破坏（与今日同 ws 多 scan 扫同一私有克隆等价）。
- **路径安全**：name 仍走 `_validate_repo_name` 双重防线（正则 + `is_relative_to`）；path 为 admin 提供的任意绝对目标（admin-only 已是防线），存 `.resolve()` 规范形式，不注入到 shell（`_infer_from_git` 只读 `.git/config`）。

## 9. 测试策略（TDD）

后端：
- `RepoManager`：link 校验（非法 name / 路径不存在 / 非目录 / 与私有克隆重名 / 与既有关联重名）；unlink 不删文件（rmtree spy 不被调）；`list_repos` 合并私有+关联且去重；`get_repo` 查关联；`delete` 关联→unlink 不 rmtree、私有→rmtree；`resolve_linked_repo_path` 命中/未命中。
- `ScanManager`：`_resolve_repo_path` 关联源→返回存储 path（不要求 state）；私有源仍走 ready 校验；都不中报错。
- API（`test_repos_api`）：`POST /link` admin 201 / 非 admin 403 / 校验（非法 name、path 不存在、非目录）422 / 重名 409；`DELETE` 关联仅 unlink；`pull`/`checkout` 命中关联→405；`GET /repos` 含关联；`events` 关联→200 空流。

前端（vitest）：`AddRepoDialog` 关联模式提交 `linkRepo` + 成功刷新；`ReposTab` 关联徽标 + pull/checkout 隐藏 + 删除文案「取消关联」；`ScanFormFields` 下拉含关联源（mock `listRepos`）。

## 10. 演进 / 后续

- 若日后需 per-ws 独立分支：在关联之上叠 git worktree（每 ws 一份工作树，共享 `.git`）—— 不破坏本设计的关联记录（path 指向 worktree 目录即可）。
- 若需共享克隆入口：可加一个共享克隆区 + 关联复用，仍是「按路径关联」的超集。
- 二期可考虑 scan 记录里显式存 repo 标识（现仅存 `repo_path` 绝对路径），支持按 repo 反查 scan。
