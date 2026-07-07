# Shannon Web · 仓库资源化拆分（下载 vs 扫描分离）

> 背景：当前 `/scan/new` 把"代码仓库下载/选择"和"扫描配置/发起"合在**同一个表单、同一次 `POST /api/scan`**，后端 `ScanManager.start()` 在 `/scan` 请求处理路径内**同步 clone（阻塞 web 进程）再 spawn 扫描子进程**。痛点：(1) 一个仓库会被扫多次（白盒/黑盒/不同配置/不同时间点），每次都得重填源、重 pull，无法复用已下载仓库；(2) clone 大仓慢/失败时进度不可见、错误混在 scan 错误里；(3) "下载"与"扫描"语义混在一个功能里，易歧义。本 spec 把**仓库提升为一等资源**：`/repos` 管理 + `/scan/new` 选已下载仓库 + clone 异步 SSE 进度。
>
> 上位 spec：`docs/superpowers/specs/2026-07-04-shannon-web-redesign-design.md`（IA / 视觉 / shadcn 四约束 / 子项目分解）。本 spec 是其后续增强，新增 `/repos` 路由族 + 仓库管理。DSF（子项目 1）、列表页（子项目 2）、扫描页（子项目 3）均已落地，本 spec 复用其 shadcn 组件库 / `<Dialog>` / sonner / `EventTailer` 机制。

## 决策摘要（本次 brainstorming 拍板）

| 维度 | 决策 |
|---|---|
| 动机 | 复用已下载仓库 + clone 进度/失败独立可见 + 下载/扫描语义分离 |
| IA | `/repos` 管理页 + `/repos/:name` 详情子页 + `/scan/new` 选已下载仓库（两者都要） |
| 范围 | 单仓库（白盒/黑盒）；**联动保持现状**；**path 模式保留**；git clone 统一入库 |
| clone 进度通道 | **异步 clone task + SSE**（`GET /api/repos/{name}/events`，复用 `EventTailer` + clone ndjson） |
| scan source | **移除 `git` kind**，只留 `repo \| path`；scan 内不再 clone |
| 添加仓库入口 | `/repos` 页内 `<Dialog>` + 列表**行内 SSE 进度**（无独立 `/repos/new` 路由） |
| 数据持久化 | **纯磁盘无 DB**（对齐 `workspaces/` 现有模式） |

---

## 范围与完成定义

**做**：
1. 后端 `RepoManager`（异步 clone/pull + 进度 ndjson + 元数据 + 并发限流）。
2. `/api/repos` CRUD + SSE 进度端点。
3. `ScanRequest.source` 增 `RepoSource`、移除 `GitSource`；`ScanManager` 移除同步 clone、repo→路径解析 + state 校验。
4. `/repos` 列表页（表格 + 添加 Dialog + 行内 SSE 进度 + 删除）。
5. `/repos/:name` 详情页（元信息 + 分支切换 + clone 历史 + 发起扫描快捷入口）。
6. `/scan/new` 改造：第一块「选择仓库」（选已下载 repo / path / 内联添加新仓库）；移除原 git fieldset。
7. 旧 `repos/` 目录迁移（无元数据者补写）。
8. 后端 + 前端测试。

**不做**：
- 联动扫描接入仓库引用（`multi_repo_config` 模型不动；联动 yaml 的 `repos` 仍写 `path` 或 `git url`，由 multi CLI 自行 clone——现状不变）。
- 上传压缩包（`Source` 仍只 `PathSource | RepoSource`，无 archive kind）。
- path 模式入库（path 保持"扫任意本地路径，不入库"语义，与 repo 互斥并列）。
- 仓库级 RBAC / 多用户 / 仓库内容浏览（详情页只看元信息 + clone 历史，不做代码浏览）。

**完成定义**：
- `/repos` 列出 `repos_dir` 下所有仓库（含迁移后的旧目录），状态正确（`ready/cloning/pulling/failed/⚠未完成`）。
- 添加仓库：Dialog 填 git url（+branch/commit/name 可选）→ 提交 → 列表新增行 → 行内 SSE 显示 clone 进度（phase+%）→ ready 或失败红字+重试。
- clone 失败：stderr token 脱敏、错误独立可见、state=failed 持久化。
- 详情页：元信息、分支切换（checkout）、clone 历史、`[发起扫描]` 跳 `/scan/new?repo=<name>` 预选。
- `/scan/new`：选 repo → `buildBody` `source={kind:"repo",value:name}`；选 path → 不变；内联添加新仓库 → ①块内 SSE 进度 → 完成自动选中；原 git fieldset 移除。
- scan 选 repo 但 `state≠ready` → 422 友好提示；scan `source.kind=git` → 422「请改用 /repos 添加」。
- Web 重启后进行中 clone 落 `⚠未完成`，详情页提示重试。
- 后端新测试 + 前端新测试全绿；DSF / 列表页 / 扫描页现有测试不回归（扫描页 8 断言按本 spec 调整）。

---

## 1. 架构

**仓库 = 一等资源**：`repos/<name>/` 下目录（代码本体 + 元数据 + clone 日志），纯磁盘，无 DB，对齐 `workspaces/*/session.json` 模式。

**clone = 独立异步动作**：`POST /api/repos` → 202 + `RepoManager` 起 `asyncio.Task` 跑 `git clone --progress`，异步读 stderr 解析进度 → 写 `clone.ndjson` → 前端 SSE 订阅实时进度。clone 与 scan **解耦**：scan 只读已 ready 的仓库目录，不再自己 clone。

**scan source 简化**：`Source = PathSource | RepoSource`（移除 `GitSource`）。repo kind 由 `ScanManager._resolve_inputs` 解析为 `repos/<name>` 路径 + 校验 `state==ready`。git clone 一律经 `/repos` 入库，杜绝 scan 内同步 clone 阻塞。

**联动不动**：correlation 仍走 yaml（`repos` 写 path 或 git url，multi CLI 自行处理），不引入仓库名引用。

---

## 2. 数据模型（磁盘）

`SHANNON_REPOS_DIR`（默认 `repos`，复用现有 `WebConfig.repos_dir`）下每个仓库一个目录：

```
repos/<name>/
├── <代码本体 + .git>
├── .shannon-repo.json   仓库元数据（clone/pull 时写）
└── clone.ndjson         clone/pull 进度事件流（append，历史留存）
```

### 2.1 `.shannon-repo.json`
```json
{
  "name": "foo-service",
  "source": {"kind": "git", "url": "https://gitlab.example/foo.git", "branch": "main", "commit": "abc1234"},
  "cloned_at": "2026-07-07T10:00:00Z",
  "last_pull_at": "2026-07-07T12:00:00Z",
  "size_bytes": 12345678,
  "state": "ready",
  "last_error": null
}
```
- `state`：`ready | cloning | pulling | failed`（**持久态**，Web 重启后存活）。
- `source.kind`：`git`（正常）/ `unknown`（旧目录迁移时推断不出 remote）。
- `last_error`：`state=failed` 时的脱敏错误摘要。

### 2.2 `clone.ndjson`（每行一事件，append）
```json
{"ts":"...","phase":"cloning","progress":40,"message":"Receiving objects","status":"progress"}
{"ts":"...","type":"clone_end","status":"ready"}
```
- `phase`：`cloning | pulling`（checkout 不写 ndjson，见 §3.1）。
- `progress`：0–100（解析自 stderr `Receiving objects: N%` / `Resolving deltas: N%`）；解析失败则省略 `progress`，仅 `phase` + `status=progress`（降级显「clone 中」不卡）。
- 收尾行 `type=clone_end` + `status=ready|failed`（对齐 `scan_end` 语义，作 SSE 关流信号）；`failed` 附 `error`（脱敏）。

### 2.3 state 机
```
(新建) --POST /repos--> cloning --clone_end--> ready
                                   └--failed--> failed --重试/POST pull--> pulling --> ready|failed
ready --POST pull--> pulling --> ready|failed
ready --POST checkout{branch}--> (同步 checkout) --> ready（更新 source.branch/commit）
cloning/pulling 进行中 + Web 重启 --> `stale`（磁盘 state 滞留 cloning/pulling 但内存 job 已丢；列表/详情合成态，前端显 ⚠未完成）
```

> `state` 枚举：磁盘持久值 `ready | cloning | pulling | failed`；`stale` 为运行时合成态（磁盘 cloning/pulling 但内存无 job），不落盘。

---

## 3. 后端

### 3.1 `RepoManager`（`packages/web/src/shannon_web/components/repo_manager.py`）

类比 `ScanManager`：管 `asyncio.Task` + 内存 job 表（`name -> {task, state, started_at}`）+ 并发限流。

- **`clone(name, url, branch, commit)`**：
  - `asyncio.create_subprocess_exec("git", "clone", "--progress", url_with_token, target)`（token 注入逻辑从 `GitFetcher` 迁移；`branch` → `--branch`；`commit` → clone 后 `fetch --all && checkout`）。
  - 后台 task 异步读 stderr 行 → 正则提取 `Receiving objects: (\d+)%` / `Resolving deltas: (\d+)%` → 写 `clone.ndjson`（`progress` 事件）。
  - stderr token 脱敏（复用 `GitFetcher` 现有正则）。
  - 完：子进程 returncode==0 → 写 `clone_end{status:ready}` + `.shannon-repo.json{state:ready, cloned_at, size_bytes, source.commit=HEAD}`；≠0 → `clone_end{status:failed, error}` + `state:failed, last_error`。
  - 内存 job 表登记；task 结束移除（磁盘 state 留存）。
- **`pull(name)`**：同模式（`git -C target pull --ff-only`），phase=pulling，写 ndjson。
- **`checkout(name, branch)`**：**同步**（`git -C target fetch origin <branch> && git -C target checkout <branch>`，通常快，不写 ndjson）；成功更新 `.shannon-repo.json.source.branch/commit`。fetch 失败→抛 422「分支不存在」。
- **并发限流**：`asyncio.Semaphore(SHANNON_REPOS_MAX_CONCURRENT_CLONES, 默认 3)`，超限→409。
- **重启恢复**：lifespan 启动时扫 `repos_dir`，对 `state in {cloning,pulling}` 但无内存 task 的目录，列表/详情返回 `state=stale`（前端显 ⚠未完成，详情页提示重试；不自动续——git clone 中断状态不定，重试更安全）。

> `GitFetcher`（`components/git_fetcher.py`）的 token 注入 + 脱敏逻辑迁移/复用到 `RepoManager`；scan 不再调用 `GitFetcher`（scan 内同步 clone 路径删除）。`GitFetcher` 可保留为 `RepoManager` 的内部 helper 或合并，实现期定。

### 3.2 `/api/repos`（`packages/web/src/shannon_web/api/repos.py`，`prefix=/api/repos`）

| 端点 | 行为 |
|---|---|
| `GET /api/repos` | 列表：遍历 `repos_dir` + 读 `.shannon-repo.json` + 合并内存 job state（磁盘 cloning/pulling 且内存无 job → `state=stale`）→ `[{name, source, state, size_bytes, cloned_at, last_pull_at, branch, progress?(进行中时)}]` |
| `POST /api/repos` | body `{git_url, branch?, commit?, name?}`（name 空→推算 basename 去 `.git`）→ 校验 name 冲突(409) / git 凭证缺失(503) / 并发超限(409) → 写 `.shannon-repo.json{state:cloning}` + 起 clone task → 202 `{name}` |
| `GET /api/repos/{name}` | 详情：元数据 + 最近 N 条 `clone.ndjson` 事件 |
| `GET /api/repos/{name}/events` | **SSE**：`StreamingResponse` + `text/event-stream`，`EventTailer` tail `clone.ndjson`，`clone_end` 行→发完关流（复用 `EventTailer`，路径指向 `repos/<name>/clone.ndjson`） |
| `DELETE /api/repos/{name}` | 删目录；`state in {cloning,pulling,stale}` 或被进行中 scan 引用（查 `ScanManager` 活跃 scan：`source.kind=="repo" && source.value==name`）→409 |
| `POST /api/repos/{name}/pull` | → 202 起 pull task |
| `POST /api/repos/{name}/checkout` | body `{branch}` → 同步 checkout；分支不存在→422 |

注册到 `app.py` 的 router include（对齐现有 `api/scan.py` / `api/workspaces.py`）。

### 3.3 `ScanManager` 改动（`scan_manager.py` + `models.py`）

- `models.py`：`Source = Union[PathSource, RepoSource]`，**移除 `GitSource`**；新增 `RepoSource{kind:"repo", value: str}`（value=仓库名）。
- `scan_manager._resolve_inputs()`：
  - `source.kind=="repo"` → `path = repos_dir / source.value`，校验目录存在 + `.shannon-repo.json.state=="ready"`（否则 422「仓库未就绪：cloning/failed，请先完成 clone」）。
  - `source.kind=="path"` → 不变。
  - `source.kind=="git"` → 422「git 源已移除，请改用 /api/repos 添加仓库」（Pydantic Union 拒绝 + 友好 message；前端已同步不发送 git kind）。
- **删除 scan 内 `GitFetcher.fetch()` 同步 clone 调用**（`_resolve_inputs` 里 git 分支整段移除）。

---

## 4. 前端

### 4.1 `/repos` 列表页（`pages/ReposPage.tsx`，新路由）
- shadcn `<Table>`（对齐子项目 2 列表页风格）：列 = 名称 / 来源（git URL 缩略或「本地路径」或「unknown」）/ 分支 / 大小（人类可读）/ 最后更新 / 状态（`ready` ✓ / `cloning|pulling` spinner+行内 % / `failed` ✗ 红字 / `⚠未完成` 黄字）/ 操作（更新·删除·详情）。
- 顶部 `[+ 添加仓库]` → 弹 `<AddRepoDialog>`。
- **行内进度**：`state` 为 cloning/pulling 的行，用 `<CloneProgress name=...>`（订阅 `useRepoEvents(name)` SSE）显示 phase + %；完→变 ready；失败→红字 + `[重试]`。
- 删除：`<Dialog>` 二次确认（对齐列表页删除风格）；DELETE 409→toast「仓库正被使用」。
- 空态 / loading / toast 错误（sonner）。

### 4.2 `<AddRepoDialog>`（`components/AddRepoDialog.tsx`）
- 字段：git URL（必填，校验 `https:|git@|ssh:`）+ branch（可选）+ commit（可选）+ name（可选，空则预览推算 basename）。
- 提交 → `POST /api/repos` → 202 → 关 Dialog → 列表新行（`onCreated(name)` 回调触发父页 refetch + 该行行内 SSE 进度）。
- 409 name 冲突 → Dialog 内提示「已存在，是否改为更新(pull)」；503 凭证缺失 → 提示「未配置 git 凭证（GITLAB_USER/TOKEN）」。

### 4.3 `/repos/:name` 详情页（`pages/RepoDetailPage.tsx`，新路由）
- header：名 + 来源 URL（可复制）+ 分支切换 `<Select>`（当前分支 + 输入新分支 → `POST checkout`）+ `[发起扫描]`（跳 `/scan/new?repo=<name>`）+ `[更新 pull]`（触发 pull，本页 SSE 进度）+ `[删除]`。
- clone 进度区：进行中→SSE 进度条 + phase；空闲→显最近一次 clone_end 结果。
- clone 历史：`clone.ndjson` 近 N 条事件（时间 / phase / 进度 / 状态）。
- 元信息卡：source.url / branch / commit / cloned_at / last_pull_at / size / state。
- `⚠未完成` state → 横幅提示「上次 clone 未完成，建议重试」+ `[重试]`。

### 4.4 `/scan/new` 改造（`ScanFormFields.tsx` + `ScanNewPage.tsx`）
- 第一块 fieldset「**选择仓库**」：
  - sourceKind 切换：`repo`（默认）/ `path`（**移除 `git`**）。
  - `repo`：`<Select>` 列已下载仓库（`GET /repos`，显示「名称 — 来源缩略」），URL query `?repo=<name>` 命中则预选；下方 `[+ 添加新仓库]` 内联展开 `<AddRepoDialog>` 同款字段 → `POST /repos` → ①块内 `<CloneProgress>` SSE 进度 → 完成自动选中新建仓库。
  - `path`：`<FileSystemPicker>`（不变，扫任意本地路径，不入库）。
- **移除原 git fieldset**（branch/commit/force_reclone）——这些归 `/repos` 添加/更新。
- 第二块 fieldset「扫描配置」不变（url / wsName / 预览 / 黑盒 reuse）。
- `buildBody`：`source = {kind:"repo", value:name}` 或 `{kind:"path", value:path}`。
- 即时校验增：选 repo 但 `state≠ready` → 红字「仓库未就绪」+ 提交 disabled。
- 联动 tab（`YamlEditor`）不变。

### 4.5 复用 hook / 组件
- `useRepoEvents(name)`（`hooks/useRepoEvents.ts`）：`EventSource` 订阅 `/api/repos/{name}/events`，返回 `{phase, progress, status, error}`；卸载自动关连接。`<CloneProgress>` / 列表行 / 详情页 / scan 内联四处复用。
- `<CloneProgress name>`（`components/CloneProgress.tsx`）：phase + 进度条 + 错误态。
- 顶栏 `TopBar.tsx` 增「仓库」项（Dashboard / Workspaces / **仓库** / Scan / Settings）。

---

## 5. 错误处理

| 场景 | 行为 |
|---|---|
| clone 失败（returncode≠0） | stderr 脱敏 token → 写 `clone_end{failed,error}` + `state=failed`+`last_error` → SSE 推 → 行内红字 + `[重试]` |
| git 凭证缺失（`GITLAB_USER/TOKEN` 未配） | `POST /repos` → 503；path 模式不受影响 |
| name 冲突 | 409；前端提示「已存在，是否更新(pull)」 |
| Web 重启进行中 clone 丢失 | 磁盘 state 滞留 cloning/pulling 但无内存 task → `⚠未完成` → 详情页提示重试（不自动续） |
| DELETE 进行中 clone/scan | 409「仓库正被使用」 |
| scan 选 repo 但 `state≠ready` | 422「仓库未就绪：cloning/failed，请先完成 clone」 |
| scan `source.kind=git` | 422「git 源已移除，请改用 /api/repos 添加仓库」 |
| checkout 分支不存在 | 422 |
| clone 并发超限 | 409 |
| SSE 损坏行 | 跳过不中断（`EventTailer` 现有语义）；`clone_end` 关流；断连 `Last-Event-ID`（byte offset）续传 |

---

## 6. 测试策略（遵循 CLAUDE.md：只跑改动相关，不广跑全套）

后端（`packages/web/tests/`）：
- `test_repo_manager.py`：mock git 子进程（短脚本）→ clone 成功写 ndjson+元数据 / clone 失败写 clone_end failed+state=failed / 进度解析（喂 `Receiving objects: 40%` stderr → 断言 ndjson `progress=40`）/ token 脱敏 / pull / checkout（分支不存在→422）/ 并发限流(超 3→拒绝) / 重启 state 恢复（cloning 滞留→⚠未完成）。
- `test_api_repos.py`：TestClient 打 `GET` 列表 / `POST`(202/409 冲突/503 凭证/409 并发) / `GET` 详情 / `DELETE`(进行中→409) / `pull` / `checkout`；mock `RepoManager`。
- `test_api_scan_repo_source.py`：scan `source.kind=repo`+state=ready→通过 / state=cloning→422 / repo 不存在→422；scan `source.kind=git`→422。
- `test_event_tailer.py`：加 `clone.ndjson` 场景（tail repos 路径 + clone_end 关流），不新写 tailer。

前端（vitest + MSW + mock `EventSource`）：
- `ReposPage.test.tsx`：列表渲染 / 迁移旧目录 / 添加 Dialog 提交→POST /repos / 行内 SSE 进度（mock EventSource 推 progress+clone_end）/ 删除确认 / 空态 / 错误 toast。
- `RepoDetailPage.test.tsx`：元信息 / 分支切换→POST checkout / clone 历史 / `[发起扫描]` 跳转 query。
- `ScanNewPage.test.tsx`：现有 8 断言按本 spec 调整（git fieldset 移除→选 repo/path）+ 新增（选 repo→buildBody kind=repo / `?repo=` 预选 / 内联添加新仓库→①块进度 / repo state≠ready→提交 disabled）。

---

## 7. 迁移 / 兼容

- **旧 `repos/<name>/` 目录**（之前 scan git 留下、无 `.shannon-repo.json`）：首次 `GET /api/repos`（或 lifespan 启动）时，对无元数据的目录做迁移——读 `.git/config` 的 `remote.origin.url` + `.git/HEAD`/`refs` 推断 `source.url` + `branch`，补写 `.shannon-repo.json{state:ready, source.commit=HEAD, cloned_at=目录 mtime}`。推断不出 remote → `source.kind=unknown`，仍纳入列表（可删可扫，不可 pull）。
- **`ScanRequest` 契约破坏性变更**：移除 `GitSource`、增 `RepoSource`。web 前端是唯一消费方，同步改；后端对旧 `git` kind 请求返 422 友好提示（防外部脚本直调）。
- **path 模式**：完全不变（`PathSource` 契约稳定）。
- **联动**：`multi_repo_config` 不动，联动 yaml 仍写 path/git url。

---

## 8. 风险

| 风险 | 缓解 |
|---|---|
| Web 重启丢进行中 clone | 磁盘 state 持久 + `⚠未完成` 提示 + 重试；不静默丢失、不自动续（git 中断状态不定） |
| git 凭证缺失致 `/repos` git 模式不可用 | 启动检查 + 503 明确提示；path 模式独立可用 |
| clone stderr 进度解析不准（git 输出格式变 / 镜像差异） | 正则宽松 + 解析失败降级（仅 phase 不显 %，不阻塞 ready 判定） |
| 旧 repos 目录无元数据 | 迁移扫描补写；`unknown` source 不阻塞列表/扫描 |
| scan 契约破坏（移除 git kind） | web 唯一消费方同步改；后端 422 友好兜底 |
| `EventTailer` 复用于 clone.ndjson 与 ws events 路径混淆 | tailer 按传入路径定位（现有 ws 已按 workspace 路径），repos 路径独立不混淆 |
| 行内 SSE 多连接（多 clone 并发） | 并发限流 3；每行一个 EventSource，clone_end 自动关 |

---

## 9. 任务拆解（writing-plans 种子）

1. `models.py`：增 `RepoSource`、移除 `GitSource`。
2. `RepoManager`：clone/pull 异步 + ndjson + 元数据 + 进度解析 + token 脱敏 + 并发限流 + 重启 state 恢复。
3. `api/repos.py`：CRUD + SSE（复用 `EventTailer`）+ 注册到 `app.py`。
4. `ScanManager` 改：`_resolve_inputs` repo→路径+state 校验、移除同步 clone、git kind→422。
5. 旧 `repos/` 目录迁移扫描（lifespan 或首查触发）。
6. 后端测试（`test_repo_manager` / `test_api_repos` / `test_api_scan_repo_source` / `test_event_tailer` 加场景）。
7. 前端 `useRepoEvents` hook + `<CloneProgress>` + `<AddRepoDialog>`。
8. `ReposPage`：列表 + 行内进度 + 删除。
9. `RepoDetailPage`：详情 + 分支切换 + clone 历史 + 发起扫描。
10. `ScanNewPage` / `ScanFormFields` 改造（RepoSelect + 移除 git fieldset + 内联添加）。
11. 顶栏「仓库」+ 路由注册。
12. 前端测试 + 冒烟回归（DSF / 列表页 / 扫描页现有测试不回归）。
