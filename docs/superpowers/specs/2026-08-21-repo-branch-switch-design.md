# 工作区仓库列表切换分支设计

- 日期：2026-08-21
- 状态：已定稿（与用户逐节确认：下拉枚举+手输兜底 / checkout 409 扫描锁 / 报告一并做快照 / 分支列行内 combobox 复用 RepoCombobox 模式）
- 关联：`packages/web/src/supernova_web/api/repos.py`（checkout 端点已在）、`packages/web/frontend/src/components/RepoCombobox.tsx`（复用模式）

---

## 1. 问题与目标

### 现状

Web「工作区-仓库列表」（`ReposTab.tsx`）的分支列是只读文本（`r.source?.branch ?? "-"`），用户想扫同一仓库的不同分支只能重新 clone 或去服务器手切。而后端能力其实已存在：

- `POST /repos/{name}/checkout`（`repos.py:198`）已实现：`git fetch origin <branch>` + `git checkout` + 回写 meta（`repo_manager.py:469-489`）；
- 前端 `checkoutRepo()`（`client.ts:142`）已定义但**零调用**——旧 RepoDetailPage 的切换 UI 在 P2 重构中被删（`392f5f27`），i18n 文案（`repoDetail.*`）残留在 locale 可回收。

缺的是：① 前端 UI；② 分支枚举能力（全仓无 `ls-remote`/`branch -a`）；③ checkout 无扫描锁（delete 有 409 引用保护，checkout 没有，而 worker 直接读仓库工作树——扫描中切分支会让 worker 读到混合分支代码）；④ 扫描报告不记录 branch/commit 快照（scan_id = `<repo>-<时间戳>`，切分支后报告无法区分来源）。

### 目标

- 仓库列表分支列从只读文本升级为**行内可搜索下拉**（combobox）：点开拉远端分支列表、关键字筛选、手输兜底，选中即切。
- checkout 前置扫描引用锁，杜绝扫描运行中切换导致的脏结果。
- 提交扫描时快照 repo 当前 branch/commit 进 scan 记录，报告列表/详情可见。

### 非目标

- 不做分支/tag 全量管理（建分支、删分支、列 tag）——只切分支。
- 不动 core/workflow/worker——快照纯 web 侧落盘。
- 不回填存量报告的 branch 信息（缺失即不显示）。
- 不泛化重构 `RepoCombobox`（扫描页在用，回归风险不值）。

---

## 2. 后端设计

### 2a. 新增分支枚举端点

```
GET /api/workspaces/{ws}/repos/{name:path}/branches
→ 200 {"branches": ["main", "develop", "feat/x", ...]}
```

- **实现**：repo 目录内 `git ls-remote --heads origin`（`repo_manager.py` 新增 `list_branches(ws, name)`，asyncio subprocess + `asyncio.wait_for` 15s 超时），解析 `refs/heads/<name>` 尾段去重排序；**本地未 fetch 过的远端分支也能列出**（ls-remote 问远端，不依赖本地 ref）。
- **凭据**：零新工作——clone 时 `_inject_auth` 注入的带凭据 URL 已被 git 写进 `.git/config` remote origin，`ls-remote origin` 复用（与 checkout 的 `fetch origin` 同机制）。
- **只列分支不列 tag**：`--heads` 天然过滤。
- **权限**：`workspace_member`（对齐 pull/checkout）。
- **错误**：仓库不存在 404；linked 405（共享路径只读，同 checkout）；clone/pull 忙碌 409；ls-remote 失败（网络/凭据失效/超时）502 带原因——前端据此降级为纯手输。
- **路由声明坑**：必须声明在 `GET /{ws}/repos/{name:path}`（`repos.py:160`）**之前**——`{name:path}` 贪婪匹配会吞掉 `/branches` 后缀（`repos.py:117-119` 注释已警示 events 的同款坑）。

### 2b. checkout 补扫描引用锁

`repos.py` checkout 端点（`repos.py:198-208`）在调 `rm.checkout` 前加：

```python
sm = request.app.state.scan_manager
if (ws, name) in sm.active_repo_sources():
    raise HTTPException(409, "仓库正被扫描引用")
```

与 delete（`repos.py:176-177`）同款。`active_repo_sources()`（`scan_manager.py:136`）返回在跑 scan 的 `(ws, repo 名)` 集合，一次性快照无竞态窗口。

---

## 3. 前端设计

### 3a. 新组件 `components/BranchCombobox.tsx`

与 `RepoCombobox` **同模式不泛化**（Popover + `ui/command` + `shouldFilter={false}` 自管过滤）：

- **数据**：点开触发器时 lazy 拉枚举端点（SWR，key `["repo-branches", ws, name]`），列表页初次渲染零额外请求；下拉打开期间显示 loading 态。
- **触发器**：紧凑型适配表格行高——显示当前分支名 + ChevronsUpDown 小箭头（hover 才显现），视觉延续现有只读文本列。
- **下拉**：输入框关键字筛选（前端子串模糊匹配）+ 当前分支 `Check` ✓；**无匹配时追加「使用 "<关键字>"」项 = 手输兜底**（覆盖枚举失败、离线、远端新建分支场景）。
- **选中即切**：目标分支 == 当前分支 → no-op 关闭；否则 `checkoutRepo()` → 成功 toast（回收 `repoDetail.checkoutSuccess`）+ `mutate(["repos", ws])`；失败按 `ApiError.status` 出 toast（409 扫描中 / 422 分支不存在 / 405 关联仓）。
- **禁用态**（退化为现状只读文本，不渲染交互件）：`linked`（后端 405）、`state != "ready"`（cloning/pulling/failed/empty）、`source.kind != "git"`。

### 3b. `ReposTab.tsx` 分支列改造

`ReposTab.tsx:324-328` 的只读 `<span>` 替换为：满足可用条件 → `<BranchCombobox>`；否则保留原只读渲染。表头不变。

---

## 4. 报告快照（纯 web 侧）

复用「提交时写 scan_dir、列表时读回」既有模式（`repo_url=mgr.get_web_url(scan_dir)` 同款，`scan_store.py:678`）：

- **写入**：`create_scan` 解析 repo 路径后（`_resolve_repo_path` 已读 `.supernova-repo.json` 取 state，顺手取 `source.branch/commit`），往 scan_dir 写 `repo-snapshot.json`：`{"branch": "...", "commit": "..."}`。仅 repo 来源（白盒/组合的白盒腿）写；黑盒（无 repo）、legacy、url/上传来源不写。
- **读回**：`ScanSummary` 加可选字段 `repo_branch: str | None`、`repo_commit: str | None`（仓库维度字段区 `scan_store.py:349-353`，`to_dict` 一并输出）；构造时（`scan_store.py:655-680` 一带）从 scan_dir 读快照文件，缺失/损坏 → None。
- **前端**：报告列表与详情的仓库信息处展示 `repo@branch`（commit 前 8 位 hover tooltip）；字段缺失（存量报告/黑盒）不显示，布局零破坏。
- **语义**：快照 = 扫描提交时点的仓库态；提交后切分支不影响该记录。

---

## 5. 测试

| 层 | 用例 |
|---|---|
| 后端 pytest | 枚举：正常解析/失败 502/超时/linked 405/busy 409/404/路由不被 `{name:path}` 吞；checkout：被 scan 引用 409、无引用放行；快照：写入/repo 来源不写/ScanSummary 读回/缺失回退 None |
| 前端 vitest | BranchCombobox：筛选/手输兜底项/当前分支 ✓/同分支 no-op/枚举失败降级；ReposTab：ready→combobox、linked/非 ready→只读；toast 分支（成功/409/422） |
| i18n | zh/en 新 key 双语补齐，优先回收 `repoDetail.*` 既有文案 |
| 手工 | 真仓切分支 → 列表分支列更新 → 发起扫描 → 报告显示 `repo@branch` |
| 模式沿用 | 后端 git 测试用临时仓/fake git（现有模式），不做真机 git 集成测试 |

---

## 6. 风险与边界

- **worker 与 web 共享 volume**：409 锁是唯一防线，锁窗口 = `active_repo_sources()` 快照时刻，与 delete 同档可靠性，不额外加锁机制。
- **ls-remote 是网络调用**：大仓/慢网可能秒级延迟——已兜底 15s 超时 + 前端 loading 态 + 手输兜底，不影响列表页渲染。
- **枚举含远端新建分支**：`ls-remote` 权威，本地未 fetch 也能列；checkout 自带 `fetch origin <branch>`，选中即切闭环成立。
- **`.git/config` 凭据残留**：现状已如此（clone 机制决定），本设计不新增暴露面。
