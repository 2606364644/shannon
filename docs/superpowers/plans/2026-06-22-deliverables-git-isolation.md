# Deliverables git 仓库隔离修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `workspaces/<session>/deliverables/` 建独立 git 仓库,使扫描的 `checkpoint:`/`deliverable:` commit 落在独立仓库而非 shannon-py 主仓库,并修复 resume 扫错仓库的 bug。

**Architecture:** 对齐 TS(`activities.ts:544`/`runner.ts:244`)—— 在 GitManager 新增幂等的 `ensure_repository`(检查 `deliverables/.git` 是否直接存在,不存在则 `git init` + 首次空 commit),executor 在 `deliverables.mkdir` 后、任何 git 操作前调用它;resume 的 `get_completed_agents` 改传 `deliverables`。GitManager 本体(add -A 等)不动 —— 有了独立 `.git` 后操作天然隔离。

**Tech Stack:** Python 3.12、asyncio、temporalio、pytest(asyncio mode)、git CLI(经 `GitManager._run_git`)。

## Global Constraints

- **对齐 TS 权威做法**:恢复 TS `activities.ts:544` 的 `git init` 机制,不新造隔离方案。
- **deliverables 级隔离**(非 session 级):只追踪 deliverables,不追踪过程文件。
- **GitManager 本体不改**:保留 `add -A`/`reset --hard`/`clean -fd`,独立仓库下无害。
- **`ensure_repository` 用 `stat(repo_path/.git)` 判断,不用 `rev-parse`**(后者会匹配父仓库 `.git`,正是 bug 根源)。
- **pytest 不跑全套**:`feat/fork-py` 分支有预存挂起/失败(`test_worker_progress` 收集错误、`test_audit_injection` 失败、integration 挂起)。每个 task 只单跑涉及的测试文件。
- **commit 用精确 `git add <files>`**,不用 `git add -A`(避免卷走无关改动;后台扫描虽已停,仍保持习惯)。
- **分支**:`feat/fork-py`(本地,未 push)。

## File Structure

| 文件 | 责任 | 本次动作 |
|---|---|---|
| `packages/core/src/shannon_core/git_manager.py` | GitManager:checkpoint/commit/rollback/ensure_repository | 新增 `ensure_repository` 静态方法 |
| `packages/core/src/shannon_core/agents/executor.py` | agent 执行入口,串起 git 操作 | `deliverables.mkdir` 后插入一行 `ensure_repository` 调用 |
| `packages/whitebox/src/shannon_whitebox/pipeline/whitebox_resume.py` | resume 判断已完成 agent | `get_completed_agents(repo_path)` → `get_completed_agents(deliverables)` |
| `packages/core/tests/test_git_manager.py` | GitManager 单测(已有 `git_repo` fixture) | 加 `ensure_repository` 测试 |
| `packages/core/tests/test_executor_git_isolation.py` | executor 接入 + 隔离守门(新建) | 新建,集成测试 |
| `packages/whitebox/tests/test_whitebox_resume.py` | resume 单测 | 加 spy 测试验证传 deliverables |

---

### Task 1: GitManager.ensure_repository(幂等 git init)

**Files:**
- Modify: `packages/core/src/shannon_core/git_manager.py`(在 `is_git_repository` 之后、`create_checkpoint` 之前插入新方法)
- Test: `packages/core/tests/test_git_manager.py`(加测试)

**Interfaces:**
- Consumes: `GitManager._run_git(repo_path, *args)`、`GitManager._run_git_with_retry(repo_path, *args)`(已有)
- Produces: `GitManager.ensure_repository(repo_path: Path) -> GitResult` —— 幂等确保 `repo_path` 是独立 git 仓库。后续 task 的 executor/resume 依赖它。

- [ ] **Step 1: 写失败测试(append 到 test_git_manager.py 末尾)**

在 `packages/core/tests/test_git_manager.py` 末尾追加:

```python
# ---- ensure_repository ----


@pytest.mark.asyncio
async def test_ensure_repository_inits_new_repo(tmp_path: Path):
    """deliverables 无 .git 时,ensure_repository 建 .git + 首次 commit。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    result = await GitManager.ensure_repository(deliverables)

    assert result.success is True
    assert (deliverables / ".git").exists()  # .git 直接在 deliverables 内
    # 首次 commit 存在
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=deliverables, capture_output=True, text=True,
    )
    assert "Initial deliverables checkpoint" in log.stdout


@pytest.mark.asyncio
async def test_ensure_repository_idempotent(tmp_path: Path):
    """deliverables 已有 .git 时,ensure_repository 幂等跳过(不重复 init/commit)。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    await GitManager.ensure_repository(deliverables)
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=deliverables, capture_output=True, text=True,
    ).stdout.strip()

    result = await GitManager.ensure_repository(deliverables)  # 二次调用

    assert result.success is True
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=deliverables, capture_output=True, text=True,
    ).stdout.strip()
    assert head_before == head_after  # HEAD 不变(未新增 commit)


@pytest.mark.asyncio
async def test_ensure_repository_dotgit_is_inside_not_parent(tmp_path: Path):
    """关键:.git 必须直接在 deliverables 内,而非沿用父仓库的 .git(这是原 bug 根源)。"""
    parent = tmp_path / "parent"
    parent.mkdir()
    subprocess.run(["git", "init"], cwd=parent, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "p@p.com"], cwd=parent, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "P"], cwd=parent, capture_output=True, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "parent-initial"], cwd=parent, capture_output=True, check=True)
    deliverables = parent / "deliverables"  # 嵌套在父仓库内(模拟 workspaces/<session>/deliverables)
    deliverables.mkdir()

    await GitManager.ensure_repository(deliverables)

    assert (deliverables / ".git").exists()  # deliverables 自己的 .git
    # deliverables 的 HEAD 与父仓库不同(独立仓库)
    deliv_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=deliverables, capture_output=True, text=True,
    ).stdout.strip()
    parent_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=parent, capture_output=True, text=True,
    ).stdout.strip()
    assert deliv_head != parent_head
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/test_git_manager.py -k ensure_repository -v`
Expected: FAIL —— `AttributeError: type object 'GitManager' has no attribute 'ensure_repository'`

- [ ] **Step 3: 实现 ensure_repository**

在 `packages/core/src/shannon_core/git_manager.py` 的 `is_git_repository` 方法之后(约 line 83)、`create_checkpoint` 之前插入:

```python
    @staticmethod
    async def ensure_repository(repo_path: Path) -> GitResult:
        """幂等确保 repo_path 是独立 git 仓库(对齐 TS activities.ts:535-552)。

        用 stat(repo_path/.git) 判断 .git 是否“直接存在于 repo_path 内”——
        刻意不用 is_git_repository(后者用 rev-parse,会匹配父仓库的 .git,
        正是迁移后 deliverables 污染主仓库的 bug 根源)。不存在则 git init +
        设 local 身份(避免无全局 git config 的环境 commit 失败)+ 首次空 commit。
        """
        dot_git = repo_path / ".git"
        if dot_git.exists():
            return GitResult(success=True)

        await GitManager._run_git(repo_path, "init")
        # local 身份:TS 依赖全局 config,这里设 local 以在 CI/容器等无全局环境稳健
        await GitManager._run_git(repo_path, "config", "user.email", "shannon-deliverables@local")
        await GitManager._run_git(repo_path, "config", "user.name", "shannon-deliverables")
        await GitManager._run_git_with_retry(
            repo_path, "commit", "--allow-empty", "-m", "Initial deliverables checkpoint",
        )
        return GitResult(success=True)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/test_git_manager.py -k ensure_repository -v`
Expected: PASS(3 个测试全绿)

- [ ] **Step 5: 跑整个 test_git_manager.py 确认无回归**

Run: `uv run pytest packages/core/tests/test_git_manager.py -v`
Expected: PASS(原有测试 + 新增 3 个全绿)

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/git_manager.py packages/core/tests/test_git_manager.py
git commit -m "feat(git-manager): add ensure_repository for deliverables git isolation

幂等确保目标目录是独立 git 仓库(stat .git 而非 rev-parse,避免匹配父仓库)。
对齐 TS activities.ts:544。为 executor/resume 隔离铺路。"
```

---

### Task 2: executor 接入 ensure_repository + 隔离守门测试

**Files:**
- Modify: `packages/core/src/shannon_core/agents/executor.py`(line 50 `deliverables.mkdir(...)` 之后插入一行)
- Test: `packages/core/tests/test_executor_git_isolation.py`(新建)

**Interfaces:**
- Consumes: `GitManager.ensure_repository`(Task 1 产出)、`AgentExecutor.execute(...)`(已有)
- Produces: executor 执行后 `deliverables` 拥有独立 `.git`,所有后续 `create_checkpoint`/`commit` 落在独立仓库。

- [ ] **Step 1: 写失败测试(新建 test_executor_git_isolation.py)**

创建 `packages/core/tests/test_executor_git_isolation.py`:

```python
"""隔离守门:executor.execute 必须先为 deliverables 建独立 git 仓库,
使 checkpoint/commit 落在独立仓库,不污染父(主)仓库。

这是本次修复的核心回归测试 —— 防止 ensure_repository 接入被移除后污染再现。
"""
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from shannon_core.agents.executor import AgentExecutor
from shannon_core.agents.runner import ClaudeRunResult
from shannon_core.git_manager import GitManager
from shannon_core.models.agents import AgentName


def _init_parent_repo(repo: Path) -> None:
    """把 repo 初始化成 git 仓库(模拟 shannon-py 主仓库)。"""
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "parent@local"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "parent"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "parent-initial"], cwd=repo, capture_output=True, check=True)


def _log_grep(repo: Path, pattern: str) -> str:
    return subprocess.run(
        ["git", "log", "--grep", pattern, "--oneline"],
        cwd=repo, capture_output=True, text=True,
    ).stdout


@pytest.mark.asyncio
async def test_execute_isolates_deliverables_from_parent_repo(tmp_path: Path, monkeypatch):
    """executor.execute 后:deliverables 有独立 .git;父仓库无 deliverable/checkpoint commit。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_parent_repo(repo)  # repo 模拟 shannon-py 主仓库
    deliverables = repo / "workspaces" / "session" / "deliverables"  # 嵌套在父仓库内

    # mock 重依赖,避免真 agent / 真校验;GitManager 用真的(测真 .git 隔离)
    async def fake_run(**kw):
        return ClaudeRunResult(text="ok", success=True, turns=1)
    monkeypatch.setattr("shannon_core.agents.executor.run_claude_prompt", fake_run)
    monkeypatch.setattr(
        "shannon_core.agents.executor.validate_deliverable", AsyncMock(),
    )

    prompt_manager = MagicMock()
    prompt_manager.load_sync.return_value = "prompt"
    executor = AgentExecutor(prompt_manager)

    await executor.execute(
        agent_name=AgentName.PRE_RECON,
        repo_path=str(repo),
        deliverables_path=str(deliverables),
    )

    # 1) deliverables 拥有独立 .git
    assert (deliverables / ".git").exists()
    # 2) deliverable/checkpoint commit 在 deliverables 独立仓库
    assert _log_grep(deliverables, "^deliverable:").strip() != ""
    # 3) 父仓库(repo)未被污染 —— 无 deliverable/checkpoint commit
    assert _log_grep(repo, "^deliverable:").strip() == ""
    assert _log_grep(repo, "^checkpoint:").strip() == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/test_executor_git_isolation.py -v`
Expected: FAIL —— 断言 2/3 失败:executor 未调 `ensure_repository`,`create_checkpoint`/`commit` 落在父仓库 `repo`(deliverables 无独立 `.git`,`_log_grep(repo, "^deliverable:")` 非空)。

- [ ] **Step 3: 接入 ensure_repository**

修改 `packages/core/src/shannon_core/agents/executor.py`,在 `deliverables.mkdir(parents=True, exist_ok=True)`(line 50)之后立即插入:

```python
        deliverables = Path(deliverables_path)
        deliverables.mkdir(parents=True, exist_ok=True)
        await GitManager.ensure_repository(deliverables)
```

(即在 `mkdir` 与 `config: Config | None = None` 之间加这一行。`GitManager` 已在 executor.py 顶部 import,无需新增 import。)

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/test_executor_git_isolation.py -v`
Expected: PASS —— deliverables 有独立 `.git`,父仓库无污染。

- [ ] **Step 5: 回归确认 git_manager 测试仍绿**

Run: `uv run pytest packages/core/tests/test_git_manager.py packages/core/tests/test_executor_git_isolation.py -v`
Expected: PASS(Task 1 + Task 2 全绿)

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/agents/executor.py packages/core/tests/test_executor_git_isolation.py
git commit -m "feat(executor): ensure deliverables git repo before checkpoint

executor.execute 在 mkdir 后、create_checkpoint 前调 ensure_repository,
使后续 git 操作落在 deliverables 独立仓库,不再污染 shannon-py 主仓库。
对齐 TS temporal activity 入口先 init 的时序。"
```

---

### Task 3: resume 改扫 deliverables 独立仓库

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/whitebox_resume.py`(line 128)
- Test: `packages/whitebox/tests/test_whitebox_resume.py`(加 spy 测试)

**Interfaces:**
- Consumes: `GitManager.ensure_repository`(Task 1)、`GitManager.get_completed_agents`、`WhiteboxResumeStateBuilder.build(workspace, deliverables, repo_path, ...)`
- Produces: resume 的 `git_completed` 信号恢复正确(扫 deliverables 独立仓库)。

- [ ] **Step 1: 写失败测试(append 到 test_whitebox_resume.py 末尾)**

在 `packages/whitebox/tests/test_whitebox_resume.py` 末尾追加。**不 patch `get_completed_agents`**,改用 spy 验证 build 传的是 `deliverables` 而非 `repo_path`:

```python
@pytest.mark.asyncio
async def test_resume_build_scans_deliverables_not_repo(tmp_path, monkeypatch):
    """resume 的 get_completed_agents 应扫 deliverables 独立仓库(非被扫 repo)。

    修复前传 repo_path(deliverable commit 实际落 shannon-py,扫不到)。
    """
    from shannon_core.git_manager import GitManager
    from shannon_whitebox.pipeline.whitebox_resume import WhiteboxResumeStateBuilder

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "session.json").write_text("{}")  # _session_success 最小输入
    deliverables = workspace / "deliverables"
    deliverables.mkdir()
    repo = tmp_path / "repo"  # 无关路径,不应被扫
    repo.mkdir()

    scanned_paths = []

    async def spy(path):
        scanned_paths.append(path)
        return set()

    monkeypatch.setattr(
        "shannon_whitebox.pipeline.whitebox_resume.GitManager.get_completed_agents", spy,
    )

    builder = WhiteboxResumeStateBuilder()
    await builder.build(
        mode="auto", workspace=workspace, deliverables=deliverables, repo_path=repo,
    )

    assert scanned_paths == [deliverables], (
        f"expected get_completed_agents called with deliverables, got {scanned_paths}"
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/whitebox/tests/test_whitebox_resume.py::test_resume_build_scans_deliverables_not_repo -v`
Expected: FAIL —— `scanned_paths == [repo]`(改动前传 `repo_path`),断言 `[deliverables]` 不成立。

- [ ] **Step 3: 改 resume 传 deliverables**

修改 `packages/whitebox/src/shannon_whitebox/pipeline/whitebox_resume.py` line 128:

```python
        git_completed = await GitManager.get_completed_agents(deliverables)
```

(将原 `repo_path` 改为 `deliverables`。`deliverables` 已是 `build()` 的参数,无需改签名或 `worker.py` 调用方。)

- [ ] **Step 4: 跑新测试确认通过**

Run: `uv run pytest packages/whitebox/tests/test_whitebox_resume.py::test_resume_build_scans_deliverables_not_repo -v`
Expected: PASS —— `scanned_paths == [deliverables]`

- [ ] **Step 5: 回归确认整个 test_whitebox_resume.py 仍绿**

Run: `uv run pytest packages/whitebox/tests/test_whitebox_resume.py -v`
Expected: PASS(原有 patch 测试 + 新 spy 测试全绿;原有测试 patch 了 `get_completed_agents`,改动不影响它们)

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/whitebox_resume.py packages/whitebox/tests/test_whitebox_resume.py
git commit -m "fix(resume): scan deliverables repo instead of scanned repo

get_completed_agents 改传 deliverables(deliverable commit 落点)而非 repo_path。
修复迁移后 git_completed 信号失效(三信号不一致)。"
```

---

## Self-Review(写完后自查,已修正)

**1. Spec coverage:**
- ✅ 改动 1(GitManager.ensure_repository)→ Task 1
- ✅ 改动 2(executor 接入)→ Task 2
- ✅ 改动 3(resume 改扫 deliverables)→ Task 3
- ✅ 隔离回归测试(spec「测试」表的「隔离回归测试」关键项)→ Task 2 的 `test_execute_isolates_deliverables_from_parent_repo`(端到端守门:父仓库无污染 + deliverables 有 commit)
- ✅ `ensure_repository` 幂等 / .git 在内 / 首次 commit → Task 1 三个测试
- ✅ resume 扫 deliverables → Task 3 spy 测试
- 注:spec「resume 测试」还提到「首次扫描(deliverables 无 .git)返回空 set」—— `get_completed_agents` 内部 `is_git_repository` 在无 `.git` 时返回 `False` → 返回空 set(已有行为,`get_completed_agents` line 172-175),无需新增测试。

**2. Placeholder scan:** 无 TBD/TODO/"适当处理";每个代码步骤都有完整代码;测试命令都有 expected。

**3. Type consistency:** `ensure_repository(repo_path: Path) -> GitResult` 在 Task 1 定义,Task 2/3 调用一致;`GitResult(success=True)` 与 `models/result.py:23` 定义一致;`ClaudeRunResult(text=..., success=..., turns=...)` 与 `runner.py` 一致;`build(workspace, deliverables, repo_path, ...)` 与 `whitebox_resume.py:114` 签名一致。

**4. 已知陷阱规避:** 所有 pytest 命令单跑具体文件/测试,不跑全套(规避 `test_worker_progress`/`test_audit_injection`/integration 挂起);commit 用 `git add <files>`。
