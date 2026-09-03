# Web 关联仓库 checkout / pull —— 设计文档

- **日期**:2026-09-04
- **分支**:`feat/fork-py`
- **状态**:方向已确认(用户已批 §1-§8),待 plan + 实现
- **依赖**:关联仓库机制已落地(`specs/2026-07-29-workspace-linked-repos-design.md`)
- **关联**:`specs/2026-07-29-workspace-linked-repos-design.md`(本设计**推翻其 §7 决策 1**,见 §2)、`specs/2026-08-11-repos-batch-unlink-delete-design.md`

## 1. 背景

关联仓库(linked)v1 定为只读:三端点(`repos.py` branches :310 / pull :350 / checkout :366)命中 `_is_linked` → 405「关联仓库为共享路径,不可在此修改」,前端两处(`ReposTab.tsx:366`、`RepoQuickActions.tsx:27`)对 linked 不渲染切换/更新 UI。

用户诉求:**批量关联进来的仓库,直接在界面切分支/更新**,不想跑去本地终端;不叠 git worktree。

**用户确认的决策(2026-09-04 brainstorming)**:

1. **直接切目标目录分支**,不叠 worktree;
2. **admin-only**(与 link 对齐,避免 member 借道改 admin 关联的宿主任意路径);
3. **多 ws 共享同一路径时照常切换**(接受跨 ws 影响——linked view 每次现读 `.git` 推断 branch,其他 ws 刷新即见新分支);
4. **checkout 与 pull 都放开**(不只 checkout)。

## 2. 目标 / 非目标

### 目标

- linked 仓库支持:界面**切换分支**(checkout)、**更新**(pull --ff-only)、**分支列表**(branches);
- 权限:**admin-only**(三端点对 linked;私有仓维持 `workspace_member` 零变化);
- **共享目录零写入**:不落 `.supernova-repo.json` / `clone.ndjson`、不算 `_dir_size`;
- 私有克隆(clone/upload)现有行为**完全不变**。

### 非目标

- git worktree(per-ws 独立工作树)—— 用户已否(2026-07-29 §7 决策 1 保留的演进线,本设计不取);
- checkout 前 `fetch origin`——linked 远端凭据不在 supernova 凭据体系(非本系统 clone,`.git/config` 存原始 URL),fetch 会交互式挂起;新远端分支经 pull 获取(见 §5.4);
- member / manager 开放 —— 现角色模型仅 admin/user 两档(`users.ts:6`),admin-only 与 link 权限对齐;
- 跨 ws 引用检测 / 警告确认 —— 用户拍板「照常切换」;
- `--force` / 丢弃本地改动 / rebase / merge —— 一律不做。

## 3. 决策变更记录(对 2026-07-29 spec)

- **§7 决策 1「关联仓库 = 只读」被推翻**:v1 禁写的主因是「ws-A 切分支影响 ws-B 的扫描目标」+ 权限边界。2026-09-04 用户决策:接受跨 ws 影响、admin-only 收权,**直接可写**。
- §7 决策 2(link/unlink admin-only)**不变**;本设计把 checkout/pull/branches 对 linked 收紧到同一级别,保持「对共享路径的写操作全在 admin 层」的一致边界。
- 其余(linked_repos.json 存储、path 解析、unlink 语义)不变。

## 4. RepoManager 改动(`repo_manager.py`)

### 4.1 linked 路径解析

三方法在现有 `_repo_dir(ws, name)` 前先分叉:`_is_linked(ws, name)` 命中 → 复用**既有**模块级 `resolve_linked_repo_path(workspaces_dir, ws, name)`(2026-07-29 §3.3 落地,`ScanManager` 解析同款)取真实 path;未命中走现有逻辑不变。

### 4.2 `checkout`(同步)

- 命令:`git -C <linked_path> checkout <branch>` —— **跳过 fetch**(私有 clone 的 fetch 分支不走);本地无此分支但存在 `origin/<branch>` 远端跟踪分支时,git 的 DWIM 自动建本地跟踪分支,零额外代码。
- **错误如实化**(修私有仓路径的既有误导):现 `rm.checkout` 一律报「分支不存在」。改为 checkout 失败时读 stderr:`would be overwritten` / `Please commit` → 报「本地有未提交改动与目标分支冲突:<git 原文摘要>」;否则维持「分支不存在:<branch>」。两类都 422(端点映射不变)。
- 忙碌互斥:`(ws, name) in self._jobs` 检查保留(防与其它 job 撞);linked checkout 自身同步执行不入 `_jobs`。
- 成功后**不回写 meta**(linked 无 meta;`_linked_repo_view` 每次现读 `.git` 自动见新分支)。

### 4.3 `pull`(同步,不走 202 后台)

- 命令:`git -C <linked_path> pull --ff-only`,**请求内同步执行**,`asyncio.wait_for` 上限 **120s**(超时 → 502 带超时说明,不悬挂)。
- **不复用**私有仓的 202 + `_run_git_with_progress` 后台模式:该管线写 `clone.ndjson` / `_write_meta` / `_mark_failed` / `_dir_size` 全部落私有 clone 目录 —— 对 linked 意味着**往用户共享目录写文件**(污染,且 linked 无 meta、失败无处落)。同步 + 失败 502 带 stderr 摘要,错误即时可见,零新状态存储。
- 并发保护:linked pull 同步执行不入 `_jobs`;与 checkout 并发时靠 git 自身 `index.lock` 串行化,后者失败如实报错(可接受,不引入锁)。
- 端点响应:沿用装饰器 `status_code=202`,同步完成后返回 `{"pulled": name}`(前端不依赖响应体)。

### 4.4 `list_branches`

- linked 分叉走**本地枚举**(同 upload 的 `for-each-ref` 模式),但列 **`refs/heads` ∪ `refs/remotes/origin`**(remotes 条目 strip `origin/` 前缀,与本地同名去重、本地名优先)。
- 理由:pull(fetch)后新分支只存在于 `refs/remotes/origin/*`,仅列 `refs/heads` 则「pull 完下拉看不到新分支」,功能闭环断裂。
- 枚举失败 → `RuntimeError`(端点映射 502,前端降级手输,与现有约定一致)。

### 4.5 进程护栏(新增,linked 全部 git 子进程)

- **`GIT_TERMINAL_PROMPT=0`**(`create_subprocess_exec` 的 `env` 合并传入):linked `.git/config` 里的原始远端若需凭据,git 默认等终端输入 → 进程挂死。私有 clone 不触发(凭据已在 URL),故此护栏此前未设;linked 必须设。
- 不设 `GIT_ASKPASS` / 不注入凭据:绝不把 supernova 凭据体系外泄给任意宿主远端。

## 5. API 改动(`api/repos.py`)

### 5.1 撤除三处 405

branches(:310)/ pull(:350)/ checkout(:366)的 `if rm._is_linked(...)` → 405 分支**删除**。pull 端点 `_is_upload` 405 保留(不动)。

### 5.2 权限:自定义依赖 `repo_write_guard`

三个端点的 `Depends(workspace_member)` 替换为自定义依赖(依赖内可读 path params `ws`/`name`):

- `_is_linked(ws, name)` → 执行 `require_admin` 同款校验(admin 不过 → 403);
- 否则 → `workspace_member` 行为。

私有仓路径权限零变化。前端 403 提示走现有错误 toast 通道。

### 5.3 错误分档(沿既有约定)

| 场景 | 码 | 响应 |
|---|---|---|
| member 对 linked 调三端点 | 403 | 现有 auth 文案 |
| 分支不存在 / 本地改动冲突 | 422 | git 语义化消息(见 §4.2) |
| 扫描引用中 checkout | 409 | 「仓库正被扫描引用」(现逻辑,对 linked 生效) |
| pull 失败 / 超时 / branches 枚举失败 | 502 | stderr 摘要 / 超时说明 |

- checkout 端点既有 `active_repo_sources()` 409 检查对 linked 生效(扫描 worker 直读工作树,运行中切换会读到混合分支代码 —— 与私有仓同理由,2026-08-21 §2b)。
- pull 对齐私有仓现状:**无** 409 引用锁(不额外发明)。

### 5.4 行为闭环说明

「切到远端新分支」路径 = 先 pull(`git pull --ff-only` 隐含 fetch,新分支落 `refs/remotes/origin/*`)→ branches 下拉可见(§4.4 合并)→ checkout DWIM 建本地分支。全程不 fetch origin 于 checkout 单步,不碰凭据体系。

## 6. 前端改动

- `routes/WorkspaceDetail/ReposTab.tsx:366`:分支列渲染条件 `!r.linked && r.state === "ready" && (git|upload)` 改为 `(git|upload || (linked && isAdmin)) && r.state === "ready"`(linked 恒 ready);linked 且非 admin → 维持只读文本。
- `ReposTab.tsx:390`:pull 按钮条件 `!r.linked && state!=="empty" && kind!=="upload"` 放开 linked(admin 时)。
- `components/RepoQuickActions.tsx:27`:`if (repo.linked) return null` 改为「linked 且非 admin 才不渲染」;pull 按钮(`!isUpload`)对 linked 自动出现。成功文案时点:linked pull 同步完成,toast 直发完成语义(非「更新中」)。
- admin 判定复用 `user?.role === "admin"`(`AddRepoDialog.tsx:26` 既有模式)。
- i18n:新增「本地有未提交改动与目标分支冲突」「需管理员权限」等 key;移除三端点 405 相关注释/文案分支。

## 7. 边界与不变量

- **共享目录零写入**:linked checkout/pull/branches 前后,目标目录文件清单不变(测试断言)。绝不写 meta / events / size。
- `--ff-only`:不产生 merge commit、不重写历史;dirty 冲突一律拒绝并报 git 原文。
- **私有仓零回归**:三方法分叉在 `_is_linked` 未命中时走原路径,原测试全数保持绿。
- 多 ws 共享同一路径:照常切换,其他 ws 经刷新(`_linked_repo_view` 现读 `.git`)即见新分支;不做引用计数。
- `GIT_TERMINAL_PROMPT=0` 只影响 linked 子进程;私有 clone 管线不设(行为不变,避免无谓扰动)。

## 8. 测试策略(TDD)

后端(`packages/web`):
- `RepoManager` 单测:linked path 分叉命中/未命中;checkout DWIM(仅 remotes 有该分支时成功);dirty 冲突错误消息含 git 原文;pull 超时 502;branches 合并去重(heads 优先);**git 子进程 env 含 `GIT_TERMINAL_PROMPT=0`**(子进程 spy);**共享目录零写入**(操作前后 `os.listdir` 快照相等)。
- API(`test_repos_api`):矩阵 = {admin, member} × {branches, pull, checkout} × {linked, 私有 clone};member+linked → 403;admin+linked → 成功;422 分支不存在 / dirty 冲突;409 扫描引用(checkout);502 pull 失败;私有 clone 全路径回归(权限仍是 member 可用)。
- 前端(vitest):`ReposTab` linked×admin 渲染 BranchCombobox + pull 按钮、linked×member 只读文本;`RepoQuickActions` 同矩阵;`BranchCombobox` 数据源含 remotes 合并结果(mock)。

## 9. 演进 / 后续

- 若日后 member 也需切换:引入 ws 级授权(如「路径白名单成员可写」),在本设计之上收紧/放宽,不动核心管线。
- per-ws 独立分支诉求仍在 → worktree 路线(2026-07-29 §10)保持可叠加,与本设计正交。
- linked pull 若出现超长阻塞(超大仓),可演进为「同步 + 缩短超时提示本地操作」或引入 ws 级(非共享目录)任务状态存储 —— 当前不做。
