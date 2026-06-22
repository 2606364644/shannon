# SHANNON_MAX_CONCURRENT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `SHANNON_MAX_CONCURRENT` env 变量(默认 3),统一控制白盒 vuln agents 与黑盒 exploit agents 的并发上限;用户设 `2` 即可把两路扫描并发都限到 2。

**Architecture:** 单一 env 变量经 `get_max_concurrent()` 读取一次,作为运行时参数注入到各 `PipelineInput`/`BlackboxPipelineInput`。Workflow 内部用 `asyncio.Semaphore(input.max_concurrent)` 限流(白盒照搬黑盒已生产验证的模式)。env 只在进程入口(CLI/orchestrator)读取,绝不进 Temporal workflow(保持 deterministic replay)。

**Tech Stack:** Python ≥3.12、uv workspace、pytest(asyncio_mode=auto, importlib 模式)、click、Temporal Python SDK、asyncio.Semaphore。

## Global Constraints

- **Python ≥3.12**,类型注解用 `int` / `| None` 现代语法。
- **测试运行**:`uv run pytest <精确文件路径> -v`。**绝不**全量 `uv run pytest`(仓库有预存挂起:`test_worker_progress`、`test_cli ... follow`、`test_audit_injection`、部分 integration;全跑会 hang)。每个测试命令都指定到单个文件。
- **import 路径**:`shannon_core.config.concurrency`、`shannon_whitebox.pipeline.*`、`shannon_blackbox.pipeline.*`、`shannon_combined.orchestrator`。
- **ruff line-length 120**。
- **Temporal workflow 禁止读 env**:并发值必须通过 `PipelineInput.max_concurrent` 字段传入,workflow 内只用 `input.max_concurrent`。env 读取只发生在 CLI/orchestrator 入口。
- **DRY**:限流用 `asyncio.Semaphore` 直接内联(与黑盒 `blackbox/pipeline/workflows.py:235` 同款),不抽新 helper。
- **YAGNI**:不动 Temporal Worker 层并发;不给白盒加 CLI flag;不做分级并发。

---

## File Structure

| 文件 | 责任 | 操作 |
|---|---|---|
| `packages/core/src/shannon_core/config/concurrency.py` | `get_max_concurrent()` —— 读 env、校验、回退默认 | **新建** |
| `packages/core/tests/test_concurrency_config.py` | `get_max_concurrent` 的纯单测 | **新建** |
| `packages/whitebox/src/shannon_whitebox/pipeline/shared.py` | `PipelineInput` 加 `max_concurrent` 字段 | 改 |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | vuln gather 加 semaphore 限流 | 改(line 281-297) |
| `packages/whitebox/tests/test_pipeline_input.py` | 白盒 input 字段测试 | **新建** |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | `--max-concurrent` default 改 callable 读 env | 改(line 41) |
| `packages/blackbox/tests/test_cli.py` | CLI env 透传 + 显式覆盖测试 | 改(追加) |
| `packages/combined/src/shannon_combined/orchestrator.py` | wb_input/bb_input 填 max_concurrent | 改(line 31, 59) |
| `packages/combined/tests/test_orchestrator.py` | combined 两边读 env 测试 | 改(追加) |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | 白盒 CLI 构造 PipelineInput 时读 env | 改(line 49) |
| `.env.example` | 文档加 `SHANNON_MAX_CONCURRENT` | 改 |

---

## Task 1: `get_max_concurrent()` 读取入口

**Files:**
- Create: `packages/core/src/shannon_core/config/concurrency.py`
- Test: `packages/core/tests/test_concurrency_config.py`

**Interfaces:**
- Produces: `get_max_concurrent() -> int`(读 `SHANNON_MAX_CONCURRENT`,合法 → 该值;未设 → `3`;非整数或 `<1` → warn + `3`)。后续 Task 4/5/6 通过 `from shannon_core.config.concurrency import get_max_concurrent` 使用。

- [ ] **Step 1: 写失败测试**

Create `packages/core/tests/test_concurrency_config.py`:

```python
"""Tests for get_max_concurrent — env-driven concurrency limit."""

import logging

from shannon_core.config.concurrency import get_max_concurrent


def test_default_when_unset(monkeypatch):
    monkeypatch.delenv("SHANNON_MAX_CONCURRENT", raising=False)
    assert get_max_concurrent() == 3


def test_valid_value(monkeypatch):
    monkeypatch.setenv("SHANNON_MAX_CONCURRENT", "2")
    assert get_max_concurrent() == 2


def test_non_int_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SHANNON_MAX_CONCURRENT", "abc")
    with caplog.at_level(logging.WARNING):
        assert get_max_concurrent() == 3
    assert "not an int" in caplog.text


def test_zero_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SHANNON_MAX_CONCURRENT", "0")
    with caplog.at_level(logging.WARNING):
        assert get_max_concurrent() == 3
    assert "must be >=1" in caplog.text


def test_negative_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SHANNON_MAX_CONCURRENT", "-1")
    with caplog.at_level(logging.WARNING):
        assert get_max_concurrent() == 3
    assert "must be >=1" in caplog.text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/test_concurrency_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.config.concurrency'`

- [ ] **Step 3: 写最小实现**

Create `packages/core/src/shannon_core/config/concurrency.py`:

```python
"""Env-driven concurrency limit shared by whitebox/blackbox scans."""

import logging
import os

_DEFAULT = 3
_log = logging.getLogger(__name__)


def get_max_concurrent() -> int:
    """Read SHANNON_MAX_CONCURRENT.

    Returns the env value when it is an int >= 1; otherwise falls back to the
    default (3) and logs a warning. A malformed value must NOT crash a scan.
    """
    raw = os.environ.get("SHANNON_MAX_CONCURRENT")
    if raw is None:
        return _DEFAULT
    try:
        val = int(raw)
    except ValueError:
        _log.warning("SHANNON_MAX_CONCURRENT=%r not an int; falling back to %d", raw, _DEFAULT)
        return _DEFAULT
    if val < 1:
        _log.warning("SHANNON_MAX_CONCURRENT=%d must be >=1; falling back to %d", val, _DEFAULT)
        return _DEFAULT
    return val
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/test_concurrency_config.py -v`
Expected: PASS — 5 passed

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/config/concurrency.py packages/core/tests/test_concurrency_config.py
git commit -m "feat(core): add get_max_concurrent env reader"
```

---

## Task 2: 白盒 PipelineInput 加 max_concurrent 字段

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`(`PipelineInput`,约 line 8-18)
- Test: `packages/whitebox/tests/test_pipeline_input.py`

**Interfaces:**
- Consumes: 无(纯 dataclass 字段)
- Produces: `PipelineInput.max_concurrent: int`(默认 3)。Task 3 的 workflow 读取 `input.max_concurrent`;Task 5/6 的构造点填充它。

- [ ] **Step 1: 写失败测试**

Create `packages/whitebox/tests/test_pipeline_input.py`:

```python
"""PipelineInput.max_concurrent field tests (mirrors blackbox test_workflows.py)."""

from shannon_whitebox.pipeline.shared import PipelineInput


def test_pipeline_input_max_concurrent_default():
    """Default max_concurrent should be 3."""
    input = PipelineInput(repo_path="/fake/repo")
    assert input.max_concurrent == 3


def test_pipeline_input_max_concurrent_custom():
    """Custom max_concurrent should be respected."""
    input = PipelineInput(repo_path="/fake/repo", max_concurrent=2)
    assert input.max_concurrent == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/whitebox/tests/test_pipeline_input.py -v`
Expected: FAIL — `AttributeError` / unexpected keyword `max_concurrent`(字段尚未存在,`PipelineInput(..., max_concurrent=2)` 报意外参数;default 测试 `input.max_concurrent` 报 AttributeError)

- [ ] **Step 3: 加字段**

Modify `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`,在 `PipelineInput` dataclass 末尾(`resume_completed_agents` 之后)加一行:

```python
@dataclass
class PipelineInput(BasePipelineInput):
    """Whitebox-specific fields.

    Note: vuln_classes accepts list[str] from the base class.
    Internally, VulnType enum values are used for type safety;
    conversion happens at the boundary (workflow entry).
    """
    repo_path: str = ""                        # Required for whitebox
    web_url: str = ""
    prompt_override: str | None = None
    resume_completed_agents: list[str] = field(default_factory=list)  # resume 预填
    max_concurrent: int = 3                    # SHANNON_MAX_CONCURRENT 注入;vuln agents 并发上限
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/whitebox/tests/test_pipeline_input.py -v`
Expected: PASS — 2 passed

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/shared.py packages/whitebox/tests/test_pipeline_input.py
git commit -m "feat(whitebox): add max_concurrent to PipelineInput"
```

---

## Task 3: 白盒 workflow vuln gather 加 semaphore 限流

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`(line 276-306 的 vuln gather 段)

**Interfaces:**
- Consumes: `input.max_concurrent`(Task 2 产出)
- Produces: 无新对外接口;白盒 vuln agents 峰值并发被 `asyncio.Semaphore(input.max_concurrent)` 限制。

**TDD 说明(诚实):** 项目无 Temporal `WorkflowEnvironment` 测试基础设施(黑盒 `max_concurrent` 同样只测字段层,不真跑 workflow 峰值)。本 task 的验证 = 现有 whitebox 测试套件不回归 + 手动冒烟(见 Step 4)。semaphore 限流本身的正确性由 Python 标准库保证,且与黑盒 `blackbox/pipeline/workflows.py:235` 同款(已生产验证)。

- [ ] **Step 1: 改造 vuln gather 段**

Modify `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`。把现有(line 276-306):

```python
            self._state.current_phase = "vulnerability-analysis"
            vuln_tasks = []
            for vt in selected_classes:
                agent_name = AgentName(f"{vt}-vuln")
                if agent_name.value not in self._state.completed_agents:
                    self._state.current_agent = agent_name.value
                    vuln_tasks.append(
                        workflow.execute_activity(
                            activities.run_vuln_agent,
                            ActivityInput(**{**act_input.__dict__, "agent_name": agent_name.value}),
                            start_to_close_timeout=timedelta(hours=2),
                            retry_policy=RetryPolicy(
                                maximum_attempts=3,
                                initial_interval=timedelta(seconds=30),
                                maximum_interval=timedelta(minutes=5),
                                backoff_coefficient=2.0,
                                non_retryable_error_types=NON_RETRYABLE,
                            ),
                        )
                    )

            if vuln_tasks:
                results = await asyncio.gather(*vuln_tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    vt = selected_classes[i]
                    agent_name = AgentName(f"{vt}-vuln")
                    if isinstance(result, Exception):
                        self._state.errors.append(f"{agent_name.value}: {result}")
                        self._state.failed_agents.append(agent_name.value)
                    else:
                        self._state.completed_agents.append(agent_name.value)
                        self._state.agent_metrics[agent_name.value] = result
```

改为(用 `(vt, agent_name, coro)` 三元组保留映射 + semaphore 包裹;照搬黑盒 `bounded_exploit` 模式):

```python
            self._state.current_phase = "vulnerability-analysis"
            vuln_tasks: list[tuple[VulnType, AgentName, object]] = []
            for vt in selected_classes:
                agent_name = AgentName(f"{vt}-vuln")
                if agent_name.value not in self._state.completed_agents:
                    self._state.current_agent = agent_name.value
                    coro = workflow.execute_activity(
                        activities.run_vuln_agent,
                        ActivityInput(**{**act_input.__dict__, "agent_name": agent_name.value}),
                        start_to_close_timeout=timedelta(hours=2),
                        retry_policy=RetryPolicy(
                            maximum_attempts=3,
                            initial_interval=timedelta(seconds=30),
                            maximum_interval=timedelta(minutes=5),
                            backoff_coefficient=2.0,
                            non_retryable_error_types=NON_RETRYABLE,
                        ),
                    )
                    vuln_tasks.append((vt, agent_name, coro))

            if vuln_tasks:
                semaphore = asyncio.Semaphore(input.max_concurrent)

                async def bounded(coro):
                    async with semaphore:
                        return await coro

                results = await asyncio.gather(
                    *[bounded(coro) for _, _, coro in vuln_tasks],
                    return_exceptions=True,
                )
                for (vt, agent_name, _), result in zip(vuln_tasks, results):
                    if isinstance(result, Exception):
                        self._state.errors.append(f"{agent_name.value}: {result}")
                        self._state.failed_agents.append(agent_name.value)
                    else:
                        self._state.completed_agents.append(agent_name.value)
                        self._state.agent_metrics[agent_name.value] = result
```

**关键点:**
- `retry_policy`/`start_to_close_timeout`/`ActivityInput` 构造**完全不变**(照抄原值)。
- 结果循环从 `selected_classes[i]` 索引改为 `zip(vuln_tasks, results)` —— 因为 `vuln_tasks` 现在只含未完成项(原代码 `results[i] ↔ selected_classes[i]` 在有跳过项时本就脆弱,zip 更正确且一一对应)。
- **不**在此文件 import 或调用 `get_max_concurrent`(workflow 不读 env)。

- [ ] **Step 2: 跑现有 whitebox 测试确认不回归**

Run: `uv run pytest packages/whitebox/tests/test_pipeline_input.py packages/whitebox/tests/test_workflows.py -v 2>&1 | tail -30`
Expected: PASS(test_pipeline_input 2 passed;test_workflows 若存在则其原有用例仍 pass)。
若 `test_workflows.py` 不存在或其中有用例失败,**先确认失败是否由本改动引起**——若改动前就 fail(memory 记录的预存挂起),记下并跳过该文件,仅以 `test_pipeline_input.py` 为 gate。

- [ ] **Step 3: 跑黑盒 workflow 测试确认对照基准(可选 sanity)**

Run: `uv run pytest packages/blackbox/tests/test_workflows.py::test_pipeline_input_max_concurrent_default packages/blackbox/tests/test_workflows.py::test_pipeline_input_max_concurrent_custom -v`
Expected: PASS —— 确认黑盒同款模式仍工作(我们没改黑盒,只是确认基准)。

- [ ] **Step 4: 手动冒烟(人工,记入验收)**

```bash
SHANNON_MAX_CONCURRENT=2 uv run python -m shannon_whitebox.cli.main start -r <repo>
```
观察 live dashboard / 日志:vuln 阶段 5 个 agent 分批,**同时运行的不超过 2**。(此项依赖人工观察,属 memory 记录的"手动冒烟待人工"惯例。)

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
git commit -m "feat(whitebox): bound vuln agent concurrency via asyncio.Semaphore"
```

---

## Task 4: 黑盒 CLI --max-concurrent default 从 env 读

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py`(line 41 的 option + 顶部 import)
- Test: `packages/blackbox/tests/test_cli.py`(追加)

**Interfaces:**
- Consumes: `get_max_concurrent`(Task 1)
- Produces: 黑盒 `--max-concurrent` 默认值 = env;CLI 显式传仍覆盖。`BlackboxPipelineInput.max_concurrent` 由 `start()` 注入(line 92 已存在,无需改)。

**关键实现点(陷阱):** 必须用 **callable default** —— `default=get_max_concurrent`(**无括号**)。click 在每次 parse 时调用 callable,这样 env 在 CLI 调用时读取(可被测试 monkeypatch,语义正确)。写成 `default=get_max_concurrent()` 会在模块 import 时冻结 env 值,测试无法覆盖且行为错误。

- [ ] **Step 1: 写失败测试**

Append to `packages/blackbox/tests/test_cli.py`(文件顶部已有 `from click.testing import CliRunner`、`from unittest.mock import AsyncMock, patch`、`from shannon_blackbox.cli.main import cli`、`from shannon_blackbox.pipeline.shared import BlackboxPipelineInput, BlackboxPipelineState`;无需新增 import):

```python
def _capture_input(monkeypatch, extra_args, env_value=None):
    """Invoke `blackbox start` with run_scan mocked; return captured BlackboxPipelineInput."""
    if env_value is None:
        monkeypatch.delenv("SHANNON_MAX_CONCURRENT", raising=False)
    else:
        monkeypatch.setenv("SHANNON_MAX_CONCURRENT", env_value)

    captured: list[BlackboxPipelineInput] = []

    async def fake_run_scan(input, temporal_address, use_rich=False):
        captured.append(input)
        return BlackboxPipelineState(status="completed")

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.cli.main.find_latest_workspace", return_value=None),
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com"] + extra_args)
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    return captured[0]


def test_max_concurrent_default_from_env(monkeypatch):
    """SHANNON_MAX_CONCURRENT=2 → BlackboxPipelineInput.max_concurrent == 2 (no CLI flag)."""
    input = _capture_input(monkeypatch, extra_args=[], env_value="2")
    assert input.max_concurrent == 2


def test_max_concurrent_cli_overrides_env(monkeypatch):
    """--max-concurrent 5 overrides SHANNON_MAX_CONCURRENT=2."""
    input = _capture_input(monkeypatch, extra_args=["--max-concurrent", "5"], env_value="2")
    assert input.max_concurrent == 5


def test_max_concurrent_default_3_when_unset(monkeypatch):
    """No env, no flag → default 3."""
    input = _capture_input(monkeypatch, extra_args=[], env_value=None)
    assert input.max_concurrent == 3
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_max_concurrent_default_from_env packages/blackbox/tests/test_cli.py::test_max_concurrent_cli_overrides_env packages/blackbox/tests/test_cli.py::test_max_concurrent_default_3_when_unset -v`
Expected: FAIL —— env 设 2 但 `input.max_concurrent == 3`(default 仍写死 3,未读 env)。

- [ ] **Step 3: 改 option default + 加 import**

Modify `packages/blackbox/src/shannon_blackbox/cli/main.py`。

在文件顶部 import 区(其它 `from shannon_core...` 附近)加:

```python
from shannon_core.config.concurrency import get_max_concurrent
```

把 line 41:

```python
@click.option("--max-concurrent", default=3, type=int, help="Max concurrent exploit agents (default: 3)")
```

改为:

```python
@click.option("--max-concurrent", default=get_max_concurrent, type=int, help="Max concurrent exploit agents (env: SHANNON_MAX_CONCURRENT, default: 3)")
```

(**无括号** —— callable default。)`start()` 函数体 line 92 的 `max_concurrent=max_concurrent` 不变。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_max_concurrent_default_from_env packages/blackbox/tests/test_cli.py::test_max_concurrent_cli_overrides_env packages/blackbox/tests/test_cli.py::test_max_concurrent_default_3_when_unset -v`
Expected: PASS — 3 passed

- [ ] **Step 5: 跑整个 test_cli.py 确认无回归**

Run: `uv run pytest packages/blackbox/tests/test_cli.py -v 2>&1 | tail -25`
Expected: 既有用例全 pass + 3 个新 pass。**注意**:若 `test_logs_*follow*` 等用例 hang(memory 记录的预存挂起),用 `--deselect packages/blackbox/tests/test_cli.py::<挂起用例名>` 排除,不算回归。

- [ ] **Step 6: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/test_cli.py
git commit -m "feat(blackbox): read --max-concurrent default from SHANNON_MAX_CONCURRENT"
```

---

## Task 5: combined orchestrator 两边填 max_concurrent

**Files:**
- Modify: `packages/combined/src/shannon_combined/orchestrator.py`(顶部 import + line 31 `wb_input` + line 59 `bb_input`)
- Test: `packages/combined/tests/test_orchestrator.py`(追加)

**Interfaces:**
- Consumes: `get_max_concurrent`(Task 1)
- Produces: combined 路径下 wb_input/bb_input 的 `max_concurrent` 都来自 env;两边一致。

- [ ] **Step 1: 写失败测试**

Append to `packages/combined/tests/test_orchestrator.py`(顶部已有 `from unittest.mock import AsyncMock, patch`、`import pytest`、`from shannon_combined.orchestrator import run_combined_scan`):

```python
async def test_combined_wires_env_concurrency_to_both_inputs(monkeypatch):
    """SHANNON_MAX_CONCURRENT=2 → both wb_input and bb_input get max_concurrent=2."""
    monkeypatch.setenv("SHANNON_MAX_CONCURRENT", "2")
    whitebox_result = {"status": "completed", "workspace_name": "ws-1"}
    blackbox_state = BlackboxPipelineState(status="completed")

    with (
        patch("shannon_combined.orchestrator.run_whitebox_scan", new_callable=AsyncMock, return_value=whitebox_result) as mock_wb,
        patch("shannon_combined.orchestrator.run_blackbox_scan", new_callable=AsyncMock, return_value=blackbox_state) as mock_bb,
    ):
        await run_combined_scan(repo_path="/fake/repo", url="http://example.com")

    wb_input = mock_wb.call_args.args[0]
    bb_input = mock_bb.call_args.args[0]
    assert wb_input.max_concurrent == 2
    assert bb_input.max_concurrent == 2
```

**import 补充:** 此测试用到 `BlackboxPipelineState`。在文件顶部加(若尚无):

```python
from shannon_blackbox.pipeline.shared import BlackboxPipelineState
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/combined/tests/test_orchestrator.py::test_combined_wires_env_concurrency_to_both_inputs -v`
Expected: FAIL —— `wb_input.max_concurrent == 3`(默认),`== 2` 断言失败(orchestrator 尚未填值)。

- [ ] **Step 3: 改 orchestrator**

Modify `packages/combined/src/shannon_combined/orchestrator.py`。

顶部 import 区(已有 `from shannon_blackbox.pipeline.shared import BlackboxPipelineInput`、`from shannon_whitebox.pipeline.shared import PipelineInput` 附近)加:

```python
from shannon_core.config.concurrency import get_max_concurrent
```

line 31 `wb_input = PipelineInput(...)` 加 `max_concurrent`:

```python
    wb_input = PipelineInput(
        repo_path=repo_path,
        web_url=url,
        config_path=config_path,
        pipeline_testing_mode=pipeline_testing,
        max_concurrent=get_max_concurrent(),
    )
```

line 59 `bb_input = BlackboxPipelineInput(...)` 加 `max_concurrent`:

```python
    bb_input = BlackboxPipelineInput(
        web_url=url,
        repo_path=repo_path,
        workspace_name=workspace_name,
        config_path=config_path,
        pipeline_testing_mode=pipeline_testing,
        max_concurrent=get_max_concurrent(),
    )
```

(两个都调 `get_max_concurrent()`,各读一次 env,值一致。)

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/combined/tests/test_orchestrator.py::test_combined_wires_env_concurrency_to_both_inputs -v`
Expected: PASS

- [ ] **Step 5: 跑整个 test_orchestrator.py 确认无回归**

Run: `uv run pytest packages/combined/tests/test_orchestrator.py -v 2>&1 | tail -20`
Expected: 既有用例(`test_run_combined_scan_calls_whitebox_then_blackbox` 等)+ 新用例全 pass。

- [ ] **Step 6: Commit**

```bash
git add packages/combined/src/shannon_combined/orchestrator.py packages/combined/tests/test_orchestrator.py
git commit -m "feat(combined): wire SHANNON_MAX_CONCURRENT into wb+bb inputs"
```

---

## Task 6: 白盒 CLI 构造点 + 文档 + 全构造点校验

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py`(line 49 `PipelineInput(...)`)
- Modify: `.env.example`

**Interfaces:**
- Consumes: `get_max_concurrent`(Task 1)、`PipelineInput.max_concurrent`(Task 2)
- Produces: 白盒独立 CLI 路径也读 env;`.env.example` 文档化变量。

- [ ] **Step 1: 白盒 CLI 填 max_concurrent**

Modify `packages/whitebox/src/shannon_whitebox/cli/main.py`。

顶部 import 区加(参照 `from shannon_whitebox.worker import run_scan` 附近):

```python
from shannon_core.config.concurrency import get_max_concurrent
```

line 49-56 的 `input = PipelineInput(...)` 加 `max_concurrent`。**原代码:**

```python
    input = PipelineInput(
        repo_path=str(Path(repo).resolve()),
        web_url=url or "",
        output_path=str(Path(output).resolve()) if output else None,
        workspace_name=workspace,
        config_path=config_path,
        pipeline_testing_mode=pipeline_testing,
    )
```

**改为**(末尾追加 `max_concurrent`;白盒无 `--max-concurrent` flag,直接读 env,符合 spec YAGNI 决策):

```python
    input = PipelineInput(
        repo_path=str(Path(repo).resolve()),
        web_url=url or "",
        output_path=str(Path(output).resolve()) if output else None,
        workspace_name=workspace,
        config_path=config_path,
        pipeline_testing_mode=pipeline_testing,
        max_concurrent=get_max_concurrent(),
    )
```

- [ ] **Step 2: 更新 .env.example**

Modify `.env.example`,在共享配置区(`SHANNON_BROWSER_ENGINE` 附近)加:

```
# 并发上限:白盒 vuln agents + 黑盒 exploit agents(默认 3;combined 串行不冲突)
SHANNON_MAX_CONCURRENT=3
```

- [ ] **Step 3: 全构造点校验 —— 确认无遗漏**

Run: `grep -rn "PipelineInput(\|BlackboxPipelineInput(" packages/*/src/`
Expected: 每个构造点都已填 `max_concurrent` 或由 dataclass 默认 3 兜底。已知点:
- `combined/orchestrator.py:31` (wb) ✓ Task 5
- `combined/orchestrator.py:59` (bb) ✓ Task 5
- `whitebox/cli/main.py:49` ✓ Step 1
- `blackbox/cli/main.py:83` (bb) ✓ Task 4(start 注入)
- `whitebox/worker.py:282`、`blackbox/worker.py:151` —— 这些是 `__main__` 直跑兜底,留 dataclass 默认 3 即可(非生产入口)。

对每个**生产路径**构造点确认有 `max_concurrent=` 或上游注入;仅 `__main__` 兜底点可留默认。

- [ ] **Step 4: 跑全部新增/相关测试做最终 gate**

Run:
```bash
uv run pytest \
  packages/core/tests/test_concurrency_config.py \
  packages/whitebox/tests/test_pipeline_input.py \
  packages/blackbox/tests/test_cli.py::test_max_concurrent_default_from_env \
  packages/blackbox/tests/test_cli.py::test_max_concurrent_cli_overrides_env \
  packages/blackbox/tests/test_cli.py::test_max_concurrent_default_3_when_unset \
  packages/combined/tests/test_orchestrator.py::test_combined_wires_env_concurrency_to_both_inputs \
  -v
```
Expected: 全 PASS(7 passed)。

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py .env.example
git commit -m "feat(whitebox): read max_concurrent from env in CLI; document SHANNON_MAX_CONCURRENT"
```

---

## 验收标准回顾(spec §7 对应)

- [x] `.env` 设 `SHANNON_MAX_CONCURRENT=2` → 白盒 vuln 峰值并发 2(Task 3 + Task 6 Step 1;手动冒烟 Task 3 Step 4)
- [x] 同 env → 黑盒 exploit 峰值并发 2(Task 4;黑盒 workflow semaphore 已存在)
- [x] 黑盒 `--max-concurrent N` 覆盖 env(Task 4 `test_max_concurrent_cli_overrides_env`)
- [x] 不设 env → 白盒默认 3、黑盒默认 3(Task 2 default;Task 4 `test_max_concurrent_default_3_when_unset`)
- [x] 非法 env 值不崩溃 → 默认 3 + warn(Task 1)
- [x] combined 两边读同一值(Task 5)
- [x] 自动化测试全 pass(Task 6 Step 4)

## 行为变化提醒

- **白盒默认并发从"全开(5)"变为 3**。需恢复全开则设 `SHANNON_MAX_CONCURRENT=5`。黑盒默认仍是 3,无变化。
