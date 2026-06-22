# Deliverables git 仓库隔离修复设计

- **日期**:2026-06-22
- **分支**:`feat/fork-py`
- **状态**:待实现
- **关联**:`2026-06-19-deliverables-to-session-design.md`(迁移设计,本次修复其遗漏点)、`2026-06-22-glm-529-retry-resilience-design.md`(并行搁置的 spec)

## 背景与问题

在一次 whitebox 扫描调试中观察到:`shannon-whitebox start` 后台运行期间,`shannon-py` 主仓库的 `feat/fork-py` 分支被持续注入大量与代码无关的 commit:

```
00022df checkpoint: before ssrf-vuln (attempt 1)
2ec4d91 deliverable: xss-vuln              ← 实际只改了 docs/ 下一个 design doc
4081942 checkpoint: before auth-vuln (attempt 1)
6fedea8 deliverable: injection-vuln        ← 实际只改了另一个 design doc
...
```

这些 `checkpoint:`/`deliverable:` commit 本应只追踪扫描产物(deliverables),却卷走了主仓库里所有未被 `.gitignore` 的改动(如 `docs/` 下的 design doc、未来的代码改动),污染提交历史。

## 根因分析

### 两个独立 bug,同一根因

GitManager 的整套机制(`create_checkpoint`/`commit`/`rollback`/`get_completed_agents`)都依赖「操作目录是独立 git 仓库」。但当前 deliverables 目录没有独立 `.git`,导致:

**Bug 1 — 污染主仓库**:`GitManager.commit(deliverables)`(`executor.py:113`)在 `workspaces/<session>/deliverables/` 跑 `git add -A`。该目录无 `.git`,git 往上找到 **shannon-py 的 `.git`**,`add -A` 于是卷走主仓库所有非 ignored 改动。`workspaces/` 虽被 `.gitignore` 忽略(deliverable 内容不进 commit),但 `docs/`、`packages/src/` 等路径不被忽略,被一并卷走。

**Bug 2 — resume 的 git 信号失效**:`worker.py:129` 向 resume builder 传 `repo_path=被扫仓库`;`whitebox_resume.py:128` 的 `get_completed_agents(repo_path)` 扫**被扫仓库**的 `git log --grep=^deliverable:`。但 `deliverable:` commit 实际落在 shannon-py,被扫仓库里根本没有 → `git_completed` 永远为空(被 `session_completed`/`file_exists` 兜底,但三信号不一致;`worker.py:70` 注释所述「repo 的 git deliverable commit 对账」已失效)。

### 根因:迁移遗漏了 `git init`

`deliverables-to-session` 迁移(2026-06-19)把 deliverables 从 `<repo>/.shannon/deliverables` 移到 `workspaces/<session>/deliverables`,但**只迁移了文件位置,没给新位置 `git init`**。该迁移设计文档通篇只讨论文件位置,完全没提 GitManager 的仓库上下文 —— 这就是遗漏点。

迁移前,早期 session(`workspaces/testheader-futunn-com_*/deliverables/.git`)的 `.git` 仍在,证明历史上 deliverables 是有独立仓库的;迁移到新位置后丢失。

## TS 权威做法(设计依据)

原始 TS 项目(`/root/shannon`,PY 的 fork 蓝本)对 deliverables 显式 `git init`,在两处(`apps/worker/src/local/runner.ts:244`、`apps/worker/src/temporal/activities.ts:544`):

```ts
// 检查 deliverablesPath/.git 是否存在(强调"directly inside deliverables, not parent repo's .git")
const dotGitPath = path.join(deliverablesPath, '.git');
try { await fs.stat(dotGitPath); return; }   // 已存在则幂等跳过
catch { /* 不存在,init */ }
await executeGitCommandWithRetry(['git', 'init'], deliverablesPath, 'init deliverables repo');
await executeGitCommandWithRetry(
  ['git', 'commit', '--allow-empty', '-m', 'Initial deliverables checkpoint'],
  deliverablesPath, 'initial checkpoint',
);
```

TS 的 `commitGitSuccess`(`git-manager.ts:254`,`git add -A` + commit)与 PY 的 `GitManager.commit` 逐字一致 —— 区别只在于 TS 先 `git init` 了 deliverables,所以 `add -A` 落在独立仓库里。**PY fork 时照搬了 GitManager,却丢了这步 `git init`。**

> 附带优势:PY 迁移后 deliverables 落在 `workspaces/`(已被 shannon-py `.gitignore` 忽略),在此 `git init` 后,shannon-py 的 git 完全不感知它(ignored + 独立 `.git`)。隔离比 TS 当年(deliverables 嵌套在被扫 repo 内)更干净。

## 目标

1. **消除主仓库污染**:扫描的 `checkpoint:`/`deliverable:` commit 只落在 deliverables 独立仓库,不进 shannon-py 主仓库历史。
2. **修复 resume 的 git 信号**:`get_completed_agents` 扫 deliverables 独立仓库,`git_completed` 恢复正确。
3. **对齐 TS**:恢复 TS 已验证的 `git init` 机制,而非新造方案。

## 非目标(YAGNI)

- ❌ GitManager 改 `add` 策略(TS 保留 `add -A`,独立仓库下无害,不偏离)
- ❌ 整个 session workspace 做 git 仓库(已确认对齐 TS 的 deliverables 级;session 级会引入 rollback 清过程文件的问题)
- ❌ 追踪过程文件(`workflow.log`/`agents/`/`prompts`/`scratchpad`;TS 只追踪 deliverables)
- ❌ 历史 `deliverable:`/`checkpoint:` commit 清理(独立的 git 卫生操作,本次不做)
- ❌ 旧 `<repo>/.shannon/deliverables` 迁移(迁移设计已决定不迁)

## 设计

三处改动,对齐 TS。

### 改动 1:GitManager 新增 `ensure_repository`

**文件**:`packages/core/src/shannon_core/git_manager.py`

新增静态方法,幂等确保目标目录是独立 git 仓库:

```python
@staticmethod
async def ensure_repository(repo_path: Path) -> GitResult:
    """幂等确保 repo_path 是独立 git 仓库(对齐 TS activities.ts:535-552)。

    检查 repo_path/.git 是否“直接存在于 repo_path 内”(用 stat,非 rev-parse ——
    rev-parse 会匹配父仓库的 .git,正是本次 bug 的来源)。不存在则 git init +
    首次空 commit 建立基线。
    """
    dot_git = repo_path / ".git"
    if dot_git.exists():
        return GitResult(success=True)
    await GitManager._run_git(repo_path, "init")
    await GitManager._run_git_with_retry(
        repo_path, "commit", "--allow-empty", "-m", "Initial deliverables checkpoint",
    )
    return GitResult(success=True)
```

**关键**:用 `stat(repo_path/.git)` 判断,而非 `is_git_repository`(后者用 `rev-parse`,会匹配父仓库 shannon-py 的 `.git` → 永远返回 True → 永不 init,正是 bug 根源)。这与 TS 的 `fs.stat(path.join(deliverablesPath, '.git'))` 一致。

### 改动 2:executor 在 deliverables 建好后调用 `ensure_repository`

**文件**:`packages/core/src/shannon_core/agents/executor.py`

在 `deliverables.mkdir(parents=True, exist_ok=True)`(line 48)之后、`create_checkpoint`(line 73)之前,插入:

```python
deliverables.mkdir(parents=True, exist_ok=True)
await GitManager.ensure_repository(deliverables)   # ← 新增:确保独立仓库先于任何 git 操作
```

此后所有 `create_checkpoint`/`rollback`/`commit` 均在 deliverables 拥有独立 `.git` 之后执行。executor 是 core 共享入口,**blackbox agent 执行同样自动获得隔离**。

### 改动 3:resume 改扫 deliverables 独立仓库

**文件**:`packages/whitebox/src/shannon_whitebox/pipeline/whitebox_resume.py:128`

```python
# 改前:git_completed = await GitManager.get_completed_agents(repo_path)
git_completed = await GitManager.get_completed_agents(deliverables)
```

`build()` 签名已有 `deliverables` 参数(`worker.py:128` 传入),无需改调用方。`get_completed_agents` 内部 `is_git_repository` 现在命中 deliverables 自己的 `.git`,扫到 `deliverable:` commit。

**GitManager 本体不动**:`create_checkpoint`/`commit`/`rollback` 的 `add -A` 全部保留 —— deliverables 有独立 `.git` 后,这些操作天然落在独立仓库。

## 隔离效果(修复后)

```
workspaces/<session>/deliverables/.git   ← 独立仓库(ensure_repository 建立)
└─ commit 链:
     Initial deliverables checkpoint
     checkpoint: before pre-recon (attempt 1)
     deliverable: pre-recon
     checkpoint: before recon (attempt 1)
     deliverable: recon
     ...
```

- ✅ shannon-py 主仓库 `git log` 不再出现 `deliverable:`/`checkpoint:` commit(污染消失)
- ✅ resume 的 `git_completed` 正确反映 deliverables 仓库里已完成的 agent(三信号重新一致)
- ✅ rollback 的 `reset --hard` + `clean -fd` 只影响 deliverables 独立仓库,不波及主仓库

## 历史污染处理(不在本次范围)

`feat/fork-py` 分支已有的那些被污染的 `deliverable:`/`checkpoint:` commit,**本次不清理**。理由:

1. 不影响代码正确性(只是 `git log` 乱,`git blame` 仍按文件行有效)
2. 清理(`git rebase`/`cherry-pick`)是独立的 git 卫生操作,且需在后台扫描已停的干净环境下进行
3. 修复机制(本次)与清理历史(后续)是两件事

建议:本次先实现 `git init` 修复 → 验证新扫描不再污染 → 再单独决定是否 rebase 清理历史(或直接接受)。

## 测试

| 文件 | 断言 |
|---|---|
| `test_git_manager.py` | `ensure_repository`:首次调用建 `deliverables/.git` + 首次 commit;二次调用幂等跳过;`.git` **直接在 deliverables 内**(用 `stat`,断言不是父仓库的) |
| `test_runner` / executor 测试 | agent 执行后 deliverables 拥有独立 `.git`;`create_checkpoint`/`commit` 的改动进 deliverables 仓库(`git -C deliverables log` 有记录) |
| resume 测试 | `get_completed_agents(deliverables)` 扫到独立仓库的 `deliverable:` commit;首次扫描(deliverables 无 .git)返回空 set |
| **隔离回归测试**(关键新增) | 模拟一次扫描(建临时 deliverables + 跑 checkpoint/commit),断言**主仓库 `git log` 无新增** `^deliverable:`/`^checkpoint:` commit —— 防污染再现的守门测试 |

## 风险与权衡

- **风险低**:改动 1 是新增方法;改动 2 是单行调用插入;改动 3 是参数替换。无现有逻辑重写。
- **幂等性**:`ensure_repository` 用 `stat` 检查 `.git`,resume 场景(deliverables 已有 `.git`)直接跳过,不破坏已有仓库。
- **时序安全**:`ensure_repository` 在 `create_checkpoint` 之前,保证所有 GitManager 操作时 deliverables 已是独立仓库。
- **blackbox 覆盖**:executor 是 core 共享,blackbox agent 执行自动隔离;blackbox resume 不用 `get_completed_agents`(用 session.json/rerun 机制),改动 3 不影响 blackbox。
- **权衡**:沿用 TS 的 `add -A`(而非限定路径)—— 在独立仓库内 `add -A` 只 add deliverables 内容,语义正确,且与 TS 一致;不引入偏离。

## 未来工作(非本次范围)

- 历史 `deliverable:`/`checkpoint:` commit 的 rebase 清理(待本次修复验证后单独评估)。
- 评估是否需要给 deliverables 独立仓库设置 `user.email`/`user.name`(某些环境 git commit 需要身份;TS 未显式设置,依赖全局配置)。
