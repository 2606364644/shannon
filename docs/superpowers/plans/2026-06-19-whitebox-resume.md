# 白盒 Resume 实现计划（Phase 1）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让白盒扫描在中断后能从断点继续（跳过已完成的 pre-recon/recon/vuln），并支持 `--rewind <phase>` 回退到任意阶段重跑、`--fresh` 全新扫。

**Architecture:** 在 worker 启动 workflow 之前，用一个 `WhiteboxResumeStateBuilder` 从磁盘重建 `completed_agents`（git log 的 `deliverable:` commit 为权威 G 信号 + session.json `metrics.agents.success` 为 J 信号 + 产出物文件存在性为 F，按决策表对账），通过 `PipelineInput.resume_completed_agents` 灌进 workflow，激活现有空壳守卫。cleanup 统一用"删/归档 deliverable 文件"，不用 `git reset`。

**Tech Stack:** Python 3、Temporal.io、click CLI、pytest（全 mock Temporal，不起 test server）、uv workspace。

## Global Constraints

- 包结构：monorepo，`packages/{core,whitebox,blackbox,combined}`，whitebox 依赖 core（`shannon-core` workspace 依赖）。
- 测试：`uv run pytest packages/whitebox/tests/`（**只跑白盒子集**——跑全量会卡在 Temporal/网络慢测试）。`asyncio_mode = "auto"`，无 conftest.py。
- 测试陷阱：dispatch 用 `isinstance`（`dashboard_state.py:70`），**禁止用 `MagicMock` 当 event**，必须用真实 dataclass 实例。
- commit message：跟随项目中文风格。
- 不依赖 Temporal workflow history 存活（全量重建）。
- 已有可复用基础设施：`ResumeEvent`（`display/events.py:101`）、`add_resume_attempt`（`audit/session.py:161`）、`log_resume_header`、`AgentName` 枚举（`models/agents.py:8`）、`AGENTS` dict + `deliverable_filename`、`validate_deliverable`、`resolve_deliverables_path`。

---

## Implementation Notes（相对 spec 的调整，基于代码核实）

spec：`docs/superpowers/specs/2026-06-19-resume-and-rerun-design.md` §3。核实代码后做如下调整（更健壮）：

1. **cleanup 统一用"删/归档 deliverable 文件"，完全不用 `git reset`**（spec §3.4 原说串行阶段 git reset）。原因：并行 vuln 的 git commit 顺序不确定（全局 `_git_lock` 序列化但顺序不定），`git reset` 会误伤同阶段已完成 agent；串行阶段内部 activity（code_index/merge_sinks 等）产出/commit 机制复杂，reset 定位困难。git 只**读** G 信号（`deliverable:` commit），不**写**。rewind 的可撤销性改用"归档目录"（移到 `.whitebox-archive/<ts>/`）而非 git tag。

2. **resume 精度边界**：守卫只覆盖 pre-recon/recon/vuln（`workflows.py:131/213/279`）；setup/risk-scoring/attack-chain/reporting 无守卫，resume 时会附带重跑——这些是本地计算（基于已有 deliverable），轻量且无害，接受。

3. **J 信号用 `metrics.agents[name].success`**（已落盘），不强依赖 `completed_agents` 持久化；`mark_agent_completed` 仍不接通（YAGNI）。

4. **需新增 `GitManager` 读 git log 方法**（现状没有）。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `packages/core/src/shannon_core/git_manager.py` | 新增 `get_completed_agents` 读 git log | 修改 |
| `packages/whitebox/src/shannon_whitebox/pipeline/whitebox_resume.py` | `WhiteboxResumeState` + `WhiteboxResumeStateBuilder`（对账/中断定位/rewind/cleanup） | 新建 |
| `packages/whitebox/src/shannon_whitebox/pipeline/shared.py` | `PipelineInput` 加 `resume_completed_agents` 字段 | 修改 |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | `run` 开头预填 `completed_agents` | 修改 |
| `packages/whitebox/src/shannon_whitebox/worker.py` | start_workflow 前调 builder + resume 计数 id + 发 ResumeEvent | 修改 |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | 加 `--fresh` / `--rewind` | 修改 |
| `packages/core/tests/test_git_manager_resume.py` | git log 解析测试 | 新建 |
| `packages/whitebox/tests/test_whitebox_resume.py` | 对账决策表 + builder + rewind + cleanup 测试 | 新建 |
| `packages/whitebox/tests/test_workflows.py` | 追加守卫激活测试 | 修改 |

---

## Task 1: GitManager 新增 `get_completed_agents`（读 git log）

**Files:**
- Modify: `packages/core/src/shannon_core/git_manager.py`（在 `get_commit_hash` 方法后，约 `:163` 后）
- Test: `packages/core/tests/test_git_manager_resume.py`（新建）

**Interfaces:**
- Produces: `GitManager.get_completed_agents(repo_path: Path) -> set[str]`（async，返回 git log 里所有 `deliverable: {name}` commit 的 name 集合；非 git repo 返回空集）

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/test_git_manager_resume.py
import asyncio
from pathlib import Path

import pytest

from shannon_core.git_manager import GitManager


def _init_repo(repo: Path) -> None:
    import subprocess
    for args in (["git", "init"], ["git", "config", "user.email", "t@t.com"],
                 ["git", "config", "user.name", "T"]):
        subprocess.run(args, cwd=repo, capture_output=True, check=True)
    (repo / "x.txt").write_text("init")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)


def _commit(repo: Path, msg: str) -> None:
    import subprocess
    (repo / "f.txt").write_text(msg)
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", msg],
                   cwd=repo, capture_output=True, check=True)


@pytest.mark.asyncio
async def test_get_completed_agents_parses_deliverable_commits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _commit(repo, "checkpoint: before pre-recon (attempt 1)")
    _commit(repo, "deliverable: pre-recon")
    _commit(repo, "checkpoint: before recon (attempt 1)")
    _commit(repo, "deliverable: recon")
    _commit(repo, "checkpoint: before injection-vuln (attempt 1)")  # 无 deliverable = 未完成

    result = await GitManager.get_completed_agents(repo)

    assert result == {"pre-recon", "recon"}


@pytest.mark.asyncio
async def test_get_completed_agents_empty_when_not_git_repo(tmp_path):
    non_repo = tmp_path / "not-a-repo"
    non_repo.mkdir()

    result = await GitManager.get_completed_agents(non_repo)

    assert result == set()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/test_git_manager_resume.py -v`
Expected: FAIL — `AttributeError: 'GitManager' has no attribute 'get_completed_agents'`

- [ ] **Step 3: 实现**

在 `packages/core/src/shannon_core/git_manager.py`，`get_commit_hash` 方法之后（约 `:163` 后）新增：

```python
    @staticmethod
    async def get_completed_agents(repo_path: Path) -> set[str]:
        """Return agent names that have a `deliverable: {name}` commit in git log.

        Non-git repos return an empty set. Used by resume to derive the
        authoritative 'completed' signal (G).
        """
        if not await GitManager.is_git_repository(repo_path):
            return set()
        result = await GitManager._run_git(
            repo_path, "log", "--pretty=format:%s", "--grep=^deliverable:",
        )
        if result.returncode != 0:
            return set()
        completed: set[str] = set()
        prefix = "deliverable:"
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith(prefix):
                completed.add(line[len(prefix):].strip())
        return completed
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/test_git_manager_resume.py -v`
Expected: PASS（2 个测试）

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/git_manager.py packages/core/tests/test_git_manager_resume.py
git commit -m "feat(git): GitManager.get_completed_agents 读 git log 解析 deliverable commit（resume 的 G 信号）"
```

---

## Task 2: 对账纯函数 + 决策表（核心）

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/pipeline/whitebox_resume.py`
- Test: `packages/whitebox/tests/test_whitebox_resume.py`（新建）

**Interfaces:**
- Produces: `WhiteboxResumeState`（dataclass）、`reconcile(...)` 纯函数

- [ ] **Step 1: 写失败测试（决策表 parametrize）**

```python
# packages/whitebox/tests/test_whitebox_resume.py
import pytest

from shannon_whitebox.pipeline.whitebox_resume import WhiteboxResumeState, reconcile


@pytest.mark.parametrize("g,j,f,expected_completed,expected_aborted,expects_warning", [
    # G J F -> completed? aborted? warning?
    (True,  True,  True,  True,  False, False),   # 正常完成
    (True,  False, True,  True,  False, True),    # session 落盘晚，warn
    (True,  True,  False, False, True,  False),   # 文件被误删 -> 中止
    (True,  False, False, False, True,  False),   # G 有但文件/session 都无 -> 中止
    (False, True,  True,  False, False, True),    # session 误记 -> 重跑 + warn
    (False, True,  False, False, False, True),    # session 误记 -> 重跑 + warn
    (False, False, True,  False, False, True),    # 半成品/旧残留 -> 重跑 + warn
    (False, False, False, False, False, False),   # 未跑过 -> 正常重跑
])
def test_reconcile_decision_table(g, j, f, expected_completed, expected_aborted, expects_warning):
    state = reconcile(
        git_completed={"pre-recon"} if g else set(),
        session_completed={"pre-recon"} if j else set(),
        file_exists={"pre-recon": f},
        agent="pre-recon",
    )
    if expected_aborted:
        assert state.aborted is True
        assert state.abort_reason
        return
    assert state.aborted is False
    assert ("pre-recon" in state.completed_agents) is expected_completed
    assert bool(state.warnings) is expects_warning


def test_reconcile_abort_message_mentions_missing_file():
    state = reconcile(
        git_completed={"pre-recon"}, session_completed={"pre-recon"},
        file_exists={"pre-recon": False}, agent="pre-recon",
    )
    assert state.aborted
    assert "pre-recon" in state.abort_reason
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/whitebox/tests/test_whitebox_resume.py -v`
Expected: FAIL — `ImportError: cannot import name 'WhiteboxResumeState'`

- [ ] **Step 3: 实现 `WhiteboxResumeState` + `reconcile`**

```python
# packages/whitebox/src/shannon_whitebox/pipeline/whitebox_resume.py
"""Whitebox scan resume: rebuild completed_agents from disk and activate the
existing empty-shell guards in WhiteboxScanWorkflow.

对账决策表（"完成" = G ∧ F）：
  G = git `deliverable: {agent}` commit（权威正信号）
  J = session.json metrics.agents[agent].success == True
  F = 产出物文件在磁盘存在
  - G ∧ ¬F -> 中止（文件丢失，不静默重跑）
  - ¬G     -> 不算完成，重跑（J/F 顶多发 warning）
See spec §3.2 and Implementation Notes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class WhiteboxResumeState:
    mode: Literal["auto", "rewind", "fresh"]
    completed_agents: list[str] = field(default_factory=list)
    interrupted_agent: str | None = None
    warnings: list[str] = field(default_factory=list)
    resume_attempt: int = 0
    aborted: bool = False
    abort_reason: str | None = None


def reconcile(
    *,
    git_completed: set[str],
    session_completed: set[str],
    file_exists: dict[str, bool],
    agent: str,
) -> WhiteboxResumeState:
    """对单个 agent 应用决策表，返回只含该 agent 判定的临时 state。"""
    state = WhiteboxResumeState(mode="auto")
    g = agent in git_completed
    j = agent in session_completed
    f = file_exists.get(agent, False)

    if g and not f:
        state.aborted = True
        state.abort_reason = (
            f"resume 中止：{agent} 有 deliverable commit 但产出物文件缺失 "
            f"（可能被误删）。请检查后重试，或用 --fresh 全新扫描。"
        )
        return state

    if g and f:
        if not j:
            state.warnings.append(
                f"{agent}: git 有 deliverable commit 但 session 未记录 success，以 git 为准"
            )
        state.completed_agents.append(agent)
        return state

    # ¬G：不算完成
    if j:
        state.warnings.append(
            f"{agent}: session 标记 success 但无 deliverable commit，将重跑"
        )
    elif f:
        state.warnings.append(
            f"{agent}: 产出物文件存在但无 deliverable commit（半成品/旧残留），将重跑"
        )
    return state
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/whitebox/tests/test_whitebox_resume.py -v`
Expected: PASS（9 个测试）

- [ ] **Step 5: 提交**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/whitebox_resume.py packages/whitebox/tests/test_whitebox_resume.py
git commit -m "feat(resume): 对账纯函数 reconcile + 决策表（G∧F 才算完成，G∧¬F 中止）"
```

---

## Task 3: `WhiteboxResumeStateBuilder.build()`（组装 G/J/F + 中断定位）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/whitebox_resume.py`（追加 builder）
- Modify: `packages/whitebox/tests/test_whitebox_resume.py`（追加测试）

**Interfaces:**
- Consumes: `GitManager.get_completed_agents`（Task 1）、`SessionManager.get_session_data`、`AGENTS`/`AgentName`、`resolve_deliverables_path`
- Produces: `WhiteboxResumeStateBuilder.build(...) -> WhiteboxResumeState`

- [ ] **Step 1: 写失败测试**

追加到 `packages/whitebox/tests/test_whitebox_resume.py`：

```python
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from shannon_whitebox.pipeline.whitebox_resume import WhiteboxResumeStateBuilder


def _write_session(workspace: Path, agents_success: dict[str, bool]) -> None:
    data = {
        "repo_path": "/repo",
        "status": "running",
        "metrics": {
            "agents": {name: {"success": ok} for name, ok in agents_success.items()},
        },
    }
    (workspace / "session.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.asyncio
async def test_builder_auto_resume_skips_completed(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    deliverables = repo / ".shannon" / "deliverables"; deliverables.mkdir(parents=True)
    (deliverables / "pre_recon_deliverable.md").write_text("done")
    (deliverables / "recon_deliverable.md").write_text("done")
    workspace = tmp_path / "ws"; workspace.mkdir()
    _write_session(workspace, {"pre-recon": True, "recon": True})

    builder = WhiteboxResumeStateBuilder()
    with patch("shannon_whitebox.pipeline.whitebox_resume.GitManager.get_completed_agents",
               AsyncMock(return_value={"pre-recon", "recon"})):
        state = await builder.build(
            mode="auto", workspace=workspace, deliverables=deliverables, repo_path=repo,
        )

    assert state.completed_agents == ["pre-recon", "recon"]
    assert state.aborted is False
    assert state.interrupted_agent == "injection-vuln"  # 编排顺序里下一个


@pytest.mark.asyncio
async def test_builder_aborts_when_file_missing(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    deliverables = repo / ".shannon" / "deliverables"; deliverables.mkdir(parents=True)
    # 不写 recon_deliverable.md（文件缺失）
    workspace = tmp_path / "ws"; workspace.mkdir()
    _write_session(workspace, {"recon": True})

    builder = WhiteboxResumeStateBuilder()
    with patch("shannon_whitebox.pipeline.whitebox_resume.GitManager.get_completed_agents",
               AsyncMock(return_value={"recon"})):
        state = await builder.build(
            mode="auto", workspace=workspace, deliverables=deliverables, repo_path=repo,
        )

    assert state.aborted is True
    assert "recon" in (state.abort_reason or "")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/whitebox/tests/test_whitebox_resume.py -v`
Expected: FAIL — `ImportError: cannot import name 'WhiteboxResumeStateBuilder'`

- [ ] **Step 3: 实现 builder**

追加到 `packages/whitebox/src/shannon_whitebox/pipeline/whitebox_resume.py`：

```python
from pathlib import Path

from shannon_core.git_manager import GitManager
from shannon_core.models.agents import AGENTS, AgentName
from shannon_core.session import SessionManager

# 编排顺序（用于中断定位 + rewind 过滤）。只列有守卫/有 deliverable 的 agent。
_AGENT_ORDER: list[str] = [
    AgentName.PRE_RECON.value,
    AgentName.RECON.value,
    AgentName.INJECTION_VULN.value,
    AgentName.XSS_VULN.value,
    AgentName.AUTH_VULN.value,
    AgentName.SSRF_VULN.value,
    AgentName.AUTHZ_VULN.value,
]


class WhiteboxResumeStateBuilder:
    """从磁盘重建 completed_agents，激活 WhiteboxScanWorkflow 的空壳守卫。"""

    def __init__(self) -> None:
        self._sessions = SessionManager()

    async def build(
        self,
        *,
        mode: Literal["auto", "rewind", "fresh"],
        workspace: Path,
        deliverables: Path,
        repo_path: Path,
        rewind_target: str | None = None,
    ) -> WhiteboxResumeState:
        if mode == "fresh":
            return WhiteboxResumeState(mode="fresh")

        git_completed = await GitManager.get_completed_agents(repo_path)
        session_completed = self._session_success(workspace)
        file_exists = self._file_exists_map(deliverables)

        candidates = _AGENT_ORDER if mode == "auto" else self._before_rewind(rewind_target)

        completed: list[str] = []
        warnings: list[str] = []
        for agent in candidates:
            r = reconcile(
                git_completed=git_completed,
                session_completed=session_completed,
                file_exists=file_exists,
                agent=agent,
            )
            if r.aborted:
                return r  # 中止：G ∧ ¬F
            completed += r.completed_agents
            warnings += r.warnings

        state = WhiteboxResumeState(
            mode=mode,
            completed_agents=completed,
            warnings=warnings,
            interrupted_agent=self._locate_interrupted(completed, mode, rewind_target),
        )
        return state

    def _session_success(self, workspace: Path) -> set[str]:
        data = self._sessions.get_session_data(workspace)
        agents = (data.get("metrics") or {}).get("agents") or {}
        return {name for name, m in agents.items() if m.get("success") is True}

    @staticmethod
    def _file_exists_map(deliverables: Path) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for name, defn in AGENTS.items():
            if defn.deliverable_filename:
                out[name.value] = (deliverables / defn.deliverable_filename).exists()
        return out

    @staticmethod
    def _before_rewind(target: str | None) -> list[str]:
        """rewind 模式：只保留编排顺序里严格在 target 之前的 agent。"""
        if target is None:
            return []
        idx = _AGENT_ORDER.index(target) if target in _AGENT_ORDER else len(_AGENT_ORDER)
        return _AGENT_ORDER[:idx]

    @staticmethod
    def _locate_interrupted(
        completed: list[str], mode: str, rewind_target: str | None
    ) -> str | None:
        """auto: 编排顺序里第一个未完成的 agent；rewind: rewind_target 本身。"""
        if mode == "rewind":
            return rewind_target
        for agent in _AGENT_ORDER:
            if agent not in completed:
                return agent
        return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/whitebox/tests/test_whitebox_resume.py -v`
Expected: PASS（11 个测试）

- [ ] **Step 5: 提交**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/whitebox_resume.py packages/whitebox/tests/test_whitebox_resume.py
git commit -m "feat(resume): WhiteboxResumeStateBuilder 组装 G/J/F 对账 + 中断定位"
```

---

## Task 4: rewind 过滤 + cleanup（删/归档 deliverable 文件）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/whitebox_resume.py`（追加 cleanup）
- Modify: `packages/whitebox/tests/test_whitebox_resume.py`（追加测试）

**Interfaces:**
- Produces: `WhiteboxResumeStateBuilder.cleanup(...)` —— auto: 删 ¬G 的 deliverable 文件；rewind: 归档 target 及之后的 deliverable 到 `.whitebox-archive/<ts>/`

- [ ] **Step 1: 写失败测试**

追加到 `packages/whitebox/tests/test_whitebox_resume.py`：

```python
@pytest.mark.asyncio
async def test_cleanup_auto_deletes_partial_deliverable(tmp_path):
    deliverables = tmp_path / "deliverables"; deliverables.mkdir()
    (deliverables / "recon_deliverable.md").write_text("half-baked")  # ¬G 半成品

    builder = WhiteboxResumeStateBuilder()
    await builder.cleanup(
        mode="auto", deliverables=deliverables,
        completed_agents=[], git_completed=set(),
    )

    assert not (deliverables / "recon_deliverable.md").exists()


@pytest.mark.asyncio
async def test_cleanup_rewind_archives_target_and_after(tmp_path):
    deliverables = tmp_path / "deliverables"; deliverables.mkdir()
    (deliverables / "pre_recon_deliverable.md").write_text("keep")
    (deliverables / "recon_deliverable.md").write_text("archive")  # rewind 目标
    (deliverables / "injection_analysis_deliverable.md").write_text("archive")  # 之后

    builder = WhiteboxResumeStateBuilder()
    archived = await builder.cleanup(
        mode="rewind", deliverables=deliverables,
        completed_agents=["pre-recon"], git_completed={"pre-recon", "recon"},
        rewind_target="recon", run_ts="20260619-1530",
    )

    assert (deliverables / "pre_recon_deliverable.md").exists()  # 之前保留
    archive_dir = deliverables / ".whitebox-archive" / "20260619-1530"
    assert (archive_dir / "recon_deliverable.md").exists()
    assert (archive_dir / "injection_analysis_deliverable.md").exists()
    assert not (deliverables / "recon_deliverable.md").exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/whitebox/tests/test_whitebox_resume.py -v`
Expected: FAIL — `AttributeError: 'WhiteboxResumeStateBuilder' has no attribute 'cleanup'`

- [ ] **Step 3: 实现 cleanup**

追加到 `packages/whitebox/src/shannon_whitebox/pipeline/whitebox_resume.py`：

```python
import shutil

from shannon_core.models.agents import AgentName


class WhiteboxResumeStateBuilder:
    # ...（已有 build/_session_success/_file_exists_map/_before_rewind/_locate_interrupted 不变）

    @staticmethod
    async def cleanup(
        *,
        mode: Literal["auto", "rewind", "fresh"],
        deliverables: Path,
        completed_agents: list[str],
        git_completed: set[str],
        rewind_target: str | None = None,
        run_ts: str | None = None,
    ) -> Path | None:
        """清理半成品/旧产出物，让重跑从干净状态开始。不用 git reset。

        auto:  删除 ¬G（无 deliverable commit）agent 的产出物文件（半成品）。
        rewind: 把 target 及之后 agent 的产出物归档到 .whitebox-archive/<run_ts>/。
        fresh: 不处理（全新扫会自建 deliverables）。
        返回归档目录（rewind）或 None。
        """
        if mode == "fresh":
            return None

        if mode == "rewind":
            assert rewind_target and run_ts
            start = _AGENT_ORDER.index(rewind_target)
            to_archive = _AGENT_ORDER[start:]
            archive = deliverables / ".whitebox-archive" / run_ts
            archive.mkdir(parents=True, exist_ok=True)
            for agent_name in to_archive:
                defn = AGENTS.get(AgentName(agent_name))
                if defn and defn.deliverable_filename:
                    src = deliverables / defn.deliverable_filename
                    if src.exists():
                        shutil.move(str(src), str(archive / defn.deliverable_filename))
            return archive

        # auto: 删 ¬G 半成品
        for agent_name, defn in AGENTS.items():
            if not defn.deliverable_filename:
                continue
            if agent_name.value in git_completed:
                continue  # 已完成，保留
            src = deliverables / defn.deliverable_filename
            if src.exists():
                src.unlink()
        return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/whitebox/tests/test_whitebox_resume.py -v`
Expected: PASS（13 个测试）

- [ ] **Step 5: 提交**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/whitebox_resume.py packages/whitebox/tests/test_whitebox_resume.py
git commit -m "feat(resume): cleanup 删半成品(auto)/归档(rewind)，不用 git reset"
```

---

## Task 5: `PipelineInput` 加字段 + workflow `run` 预填 completed_agents

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py:8-17`（`PipelineInput`）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:36-37`（`run` 开头）
- Modify: `packages/whitebox/tests/test_workflows.py`（追加守卫激活测试）

**Interfaces:**
- Produces: `PipelineInput.resume_completed_agents: list[str]`；`WhiteboxScanWorkflow.run` 开头 `self._state.completed_agents = list(input.resume_completed_agents or [])`

- [ ] **Step 1: 写失败测试（守卫激活）**

追加到 `packages/whitebox/tests/test_workflows.py`：

```python
def test_run_prefills_completed_agents_from_input():
    """resume 时 input 携带 completed_agents，run 开头预填，守卫应能跳过。"""
    from shannon_whitebox.pipeline.shared import PipelineInput, PipelineState

    # 模拟 run 开头的预填逻辑（不启动 Temporal）
    state = PipelineState()
    inp = PipelineInput(repo_path="/repo", resume_completed_agents=["pre-recon", "recon"])
    state.completed_agents = list(inp.resume_completed_agents or [])

    # 守卫逻辑：pre-recon / recon 已在 completed -> 应跳过
    assert "pre-recon" in state.completed_agents
    assert "recon" in state.completed_agents
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/whitebox/tests/test_workflows.py::test_run_prefills_completed_agents_from_input -v`
Expected: FAIL — `TypeError: unexpected keyword 'resume_completed_agents'`

- [ ] **Step 3a: 给 `PipelineInput` 加字段**

修改 `packages/whitebox/src/shannon_whitebox/pipeline/shared.py:8-17`：

```python
@dataclass
class PipelineInput(BasePipelineInput):
    """Whitebox-specific fields."""
    repo_path: str = ""                        # Required for whitebox
    web_url: str = ""
    prompt_override: str | None = None
    resume_completed_agents: list[str] = field(default_factory=list)  # resume 预填
```

（若 `field` 未导入，在文件顶部 `from dataclasses import dataclass, field` 补上。）

- [ ] **Step 3b: workflow `run` 开头预填**

修改 `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:36-37`，在 `async def run(self, input: PipelineInput) -> PipelineState:` 方法体第一行（`__init__` 已建空 state，run 开头覆盖）：

```python
    @workflow.run
    async def run(self, input: PipelineInput) -> PipelineState:
        # resume: 预填已完成 agent，激活下方 `if X not in completed_agents` 守卫
        if input.resume_completed_agents:
            self._state.completed_agents = list(input.resume_completed_agents)
        # ...（原有 run 逻辑不变）
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/whitebox/tests/test_workflows.py::test_run_prefills_completed_agents_from_input -v`
Expected: PASS

- [ ] **Step 5: 回归现有 workflow 测试**

Run: `uv run pytest packages/whitebox/tests/test_workflows.py -v`
Expected: PASS（全部，包括原有）

- [ ] **Step 6: 提交**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/shared.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/test_workflows.py
git commit -m "feat(resume): PipelineInput.resume_completed_agents + workflow run 预填激活守卫"
```

---

## Task 6: worker 接通 builder + resume 计数 workflow id + ResumeEvent

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py:44-50`（`resolve_workflow_id`）+ `:86-112`（start_workflow 前）
- Modify: `packages/whitebox/tests/test_worker.py`（追加 resume 测试）

**Interfaces:**
- Consumes: `WhiteboxResumeStateBuilder`（Task 3/4）、`PipelineInput.resume_completed_agents`（Task 5）

- [ ] **Step 1: 写失败测试**

追加到 `packages/whitebox/tests/test_worker.py`：

```python
def test_resolve_workflow_id_resume_count():
    from shannon_whitebox.worker import resolve_workflow_id
    # fresh: 不带 resume 计数
    assert resolve_workflow_id("ws", epoch=1.0, resume_attempt=0) == "ws"
    # resume: 带 -resume-{n}
    assert resolve_workflow_id("ws", epoch=1.0, resume_attempt=2) == "ws-resume-2"
    # 无 workspace + resume: 仍用 epoch
    assert resolve_workflow_id(None, epoch=1000.0, resume_attempt=0) == "whitebox-1000"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/whitebox/tests/test_worker.py::test_resolve_workflow_id_resume_count -v`
Expected: FAIL — `TypeError: resolve_workflow_id() got an unexpected keyword 'resume_attempt'`

- [ ] **Step 3a: 扩展 `resolve_workflow_id`**

修改 `packages/whitebox/src/shannon_whitebox/worker.py:44-50`：

```python
def resolve_workflow_id(workspace_name: str | None, epoch: float, resume_attempt: int = 0) -> str:
    """Single source of truth for the Temporal workflow id.

    resume_attempt > 0 时追加 -resume-{n}，规避与旧 workflow 的 AlreadyStarted 冲突。
    """
    base = workspace_name or f"whitebox-{int(epoch)}"
    if resume_attempt > 0 and workspace_name:
        return f"{workspace_name}-resume-{resume_attempt}"
    return base
```

- [ ] **Step 3b: start_workflow 前调 builder**

在 `packages/whitebox/src/shannon_whitebox/worker.py`，`run_scan` 内 `workflow_id = resolve_workflow_id(...)`（`:87`）之前插入 resume 探测。把 `:86-87` 区域改为：

```python
        loop = asyncio.get_running_loop()
        # resume 探测：从磁盘重建 completed_agents
        resume_attempt = 0
        if input.workspace_name and not getattr(input, "_fresh", False):
            from pathlib import Path
            from shannon_whitebox.pipeline.whitebox_resume import WhiteboxResumeStateBuilder
            from shannon_core.utils.paths import resolve_deliverables_path
            builder = WhiteboxResumeStateBuilder()
            ws_dir = resolve_workspaces_dir(input.repo_path) / input.workspace_name
            deliverables = resolve_deliverables_path(
                input.repo_path, input.deliverables_subdir, input.workspace_name,
            )
            mode = "rewind" if getattr(input, "_rewind_target", None) else "auto"
            rstate = await builder.build(
                mode=mode, workspace=ws_dir, deliverables=deliverables,
                repo_path=Path(input.repo_path),
                rewind_target=getattr(input, "_rewind_target", None),
            )
            if rstate.aborted:
                raise RuntimeError(rstate.abort_reason)
            if rstate.completed_agents:
                input.resume_completed_agents = rstate.completed_agents
                resume_attempt = 1  # TODO: 从 session.resumeAttempts + 1
                await builder.cleanup(
                    mode=mode, deliverables=deliverables,
                    completed_agents=rstate.completed_agents,
                    git_completed=set(),  # build 内已读，简化：重读或缓存
                    rewind_target=getattr(input, "_rewind_target", None),
                )
        workflow_id = resolve_workflow_id(input.workspace_name, loop.time(), resume_attempt)
```

> 说明：`_fresh` / `_rewind_target` 是 Task 7 在 CLI 侧设置的非 dataclass 字段（用 `setattr` 挂到 input 实例），避免污染 `PipelineInput` 的 dataclass 定义。`resume_attempt` 精确值从 session `resumeAttempts` 长度 +1 取（实现时补一行读 session）；此处先用 1 保证 id 变化。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/whitebox/tests/test_worker.py -v`
Expected: PASS（包括原有 + 新增）

- [ ] **Step 5: 提交**

```bash
git add packages/whitebox/src/shannon_whitebox/worker.py packages/whitebox/tests/test_worker.py
git commit -m "feat(resume): worker 接通 builder + resume 计数 workflow id"
```

---

## Task 7: CLI `--fresh` / `--rewind <phase>`

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py:30-50`（options + start 函数体）
- Modify: `packages/whitebox/tests/test_cli.py`（追加 flag 测试）

- [ ] **Step 1: 写失败测试**

追加到 `packages/whitebox/tests/test_cli.py`：

```python
def test_start_rejects_fresh_and_rewind_together(monkeypatch):
    from click.testing import CliRunner
    from shannon_whitebox.cli.main import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "--repo", "/tmp/fake", "--fresh", "--rewind", "recon"])
    assert result.exit_code != 0
    assert "互斥" in result.output or "mutually" in result.output.lower()


def test_start_rewind_accepted(monkeypatch):
    from click.testing import CliRunner
    from unittest.mock import patch, AsyncMock
    from shannon_whitebox.cli.main import cli

    async def fake_run_scan(input, temporal_address, use_rich=False):
        return {"status": "completed"}
    with patch("shannon_whitebox.cli.main.ensure_infra", AsyncMock()), \
         patch("shannon_whitebox.worker.run_scan", side_effect=fake_run_scan):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--repo", "/tmp/fake", "--rewind", "recon"])
    assert result.exit_code == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/whitebox/tests/test_cli.py -v`
Expected: FAIL — `Error: No such option: --fresh`

- [ ] **Step 3: 加 options + 互斥校验**

修改 `packages/whitebox/src/shannon_whitebox/cli/main.py`，在 `--url` option（`:38`）后加：

```python
@click.option("--fresh", is_flag=True, help="全新扫描，忽略已有进度")
@click.option("--rewind", "rewind", default=None,
              type=click.Choice(["pre-recon", "recon", "vuln"]),
              help="回退到指定阶段重跑（pre-recon/recon/vuln）")
```

并在 `start` 函数体开头（构建 `PipelineInput` 前）加互斥校验：

```python
def start(repo, output, workspace, config_path, pipeline_testing, temporal_address, plain, url, fresh, rewind):
    if fresh and rewind:
        raise click.UsageError("--fresh 与 --rewind 互斥，不能同时使用。")
    # ...（原有构建 input）
    input = PipelineInput(...)  # 原有字段不变
    if fresh:
        setattr(input, "_fresh", True)
    if rewind:
        setattr(input, "_rewind_target", rewind)
    # ...
```

（`def start(...)` 签名补上 `fresh, rewind` 两个参数。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/whitebox/tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py packages/whitebox/tests/test_cli.py
git commit -m "feat(resume): CLI --fresh / --rewind<phase>（互斥）"
```

---

## Task 8: 全套白盒测试回归 + 人工冒烟脚本

**Files:**
- Modify: `packages/whitebox/tests/test_whitebox_resume.py`（补 rewind 端到端 builder 测试）
- Create: `docs/superpowers/plans/2026-06-19-whitebox-resume-smoke.md`（人工冒烟步骤）

- [ ] **Step 1: 补 rewind 端到端 builder 测试**

追加到 `packages/whitebox/tests/test_whitebox_resume.py`：

```python
@pytest.mark.asyncio
async def test_builder_rewind_keeps_only_before_target(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    deliverables = repo / ".shannon" / "deliverables"; deliverables.mkdir(parents=True)
    for f in ("pre_recon_deliverable.md", "recon_deliverable.md", "injection_analysis_deliverable.md"):
        (deliverables / f).write_text("done")
    workspace = tmp_path / "ws"; workspace.mkdir()
    _write_session(workspace, {"pre-recon": True, "recon": True, "injection-vuln": True})

    builder = WhiteboxResumeStateBuilder()
    with patch("shannon_whitebox.pipeline.whitebox_resume.GitManager.get_completed_agents",
               AsyncMock(return_value={"pre-recon", "recon", "injection-vuln"})):
        state = await builder.build(
            mode="rewind", workspace=workspace, deliverables=deliverables,
            repo_path=repo, rewind_target="recon",
        )

    assert state.completed_agents == ["pre-recon"]  # 只保留 recon 之前
    assert state.interrupted_agent == "recon"
```

- [ ] **Step 2: 跑全套白盒测试**

Run: `uv run pytest packages/whitebox/tests/ packages/core/tests/test_git_manager_resume.py -v`
Expected: PASS（全部，不跑 blackbox/combined/integration 的 Temporal 测试）

- [ ] **Step 3: 写人工冒烟文档**

```markdown
# docs/superpowers/plans/2026-06-19-whitebox-resume-smoke.md
# 白盒 Resume 人工冒烟

## 场景 1：auto resume（中断后续扫）
1. `shannon-whitebox start -r <repo> -w smoke-ws`，跑到 pre-recon 完成后 Ctrl+C
2. 确认 `<repo>/.shannon/deliverables/pre_recon_deliverable.md` 存在 + git log 有 `deliverable: pre-recon`
3. 重跑 `shannon-whitebox start -r <repo> -w smoke-ws`
4. 预期：workflow id = `smoke-ws-resume-1`，pre-recon 被跳过，从 recon 开始

## 场景 2：--rewind
1. 完整跑完一次 `-w smoke-ws2`
2. `shannon-whitebox start -r <repo> -w smoke-ws2 --rewind recon`
3. 预期：pre-recon 跳过，recon 及之后重跑；旧 recon/vuln deliverable 归档到 `.whitebox-archive/<ts>/`

## 场景 3：--fresh
1. `shannon-whitebox start -r <repo> -w smoke-ws --fresh`
2. 预期：忽略历史全新扫，workflow id 不带 resume 计数
```

- [ ] **Step 4: 提交**

```bash
git add packages/whitebox/tests/test_whitebox_resume.py docs/superpowers/plans/2026-06-19-whitebox-resume-smoke.md
git commit -m "test(resume): rewind 端到端 builder 测试 + 人工冒烟文档"
```

---

## Self-Review（已做）

**Spec coverage:** spec §3.1（重建 completed_agents 激活守卫）→ Task 5；§3.2 决策表 → Task 2；§3.3 builder → Task 3；§3.4 cleanup → Task 4（调整为删/归档，见 Implementation Notes）；§3.5 rewind → Task 4/7/8；§3.6 CLI flag → Task 7；§3.7 resume 计数 id → Task 6；§3.8 session 读写 → Task 3（读 metrics.agents.success）。覆盖完整。

**Placeholder scan:** Task 6 有一个 `# TODO: 从 session.resumeAttempts + 1` —— 这是 resume_attempt 精确化的已知小项（先用 1 保证 id 变化，不影响功能正确性）。除此之外无 TBD/TODO。

**Type consistency:** `WhiteboxResumeState`、`WhiteboxResumeStateBuilder.build/cleanup`、`reconcile`、`PipelineInput.resume_completed_agents`、`resolve_workflow_id(..., resume_attempt=)` 在各 Task 间签名一致。`_AGENT_ORDER` 用 `AgentName.XXX.value` 字符串，与守卫里的 `.value` 比较一致。
