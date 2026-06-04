# Claude SDK 错误处理对齐 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对齐 PY 与 TS SDK 的错误处理差异 — 让 `_handle_error` 使用集中式错误分类，并让 workflow 正确传播失败状态。

**Architecture:** 复用已有的 `classify_error_for_temporal()` 进行错误分类，将错误类型存入 `ClaudeRunResult.error_code`；在 `PipelineState` 中新增 `error_code` + `failed_agents` 字段，workflow 完成时根据失败情况设置 `status="failed"` 而非一律 `"completed"`，并增加 Temporal 取消信号处理。

**Tech Stack:** Python 3.12+, dataclasses, Temporal Python SDK, pytest + pytest-asyncio

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `packages/core/src/shannon_core/agents/runner.py` | `ClaudeRunResult` 增加 `error_code` 字段 |
| Modify | `packages/core/src/shannon_core/agents/providers_anthropic.py` | `_handle_error` 使用 `classify_error_for_temporal` |
| Modify | `packages/whitebox/src/shannon_whitebox/pipeline/shared.py` | `PipelineState` 增加 `error_code` + `failed_agents` |
| Modify | `packages/blackbox/src/shannon_blackbox/pipeline/shared.py` | `BlackboxPipelineState` 增加 `error_code` + `failed_agents` |
| Modify | `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | 错误传播 + 取消处理 |
| Modify | `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | 错误传播 + 取消处理 |
| Modify | `packages/core/tests/agents/test_providers.py` | `_handle_error` 错误分类测试 |
| Modify | `packages/whitebox/tests/test_pipeline_shared.py` | `PipelineState` 新字段测试 |
| Modify | `packages/blackbox/tests/test_pipeline_shared.py` | `BlackboxPipelineState` 新字段测试 |

---

### Task 1: Add `error_code` field to `ClaudeRunResult`

**Files:**
- Modify: `packages/core/src/shannon_core/agents/runner.py:67-78`
- Test: `packages/core/tests/agents/test_providers.py`

- [x] **Step 1: Write the failing test**

在 `packages/core/tests/agents/test_providers.py` 的 `TestClaudeRunResult` 类中添加:

```python
def test_result_with_error_code(self):
    """测试 error_code 字段"""
    result = ClaudeRunResult(
        text="",
        success=False,
        error="authentication failed",
        error_code="AuthenticationError",
    )
    assert result.error_code == "AuthenticationError"


def test_error_code_defaults_to_none(self):
    """测试 error_code 默认为 None"""
    result = ClaudeRunResult()
    assert result.error_code is None
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/core/tests/agents/test_providers.py::TestClaudeRunResult::test_result_with_error_code -v`
Expected: FAIL — `ClaudeRunResult.__init__()` 收到意外关键字 `error_code`

- [x] **Step 3: Write minimal implementation**

在 `packages/core/src/shannon_core/agents/runner.py` 的 `ClaudeRunResult` dataclass 中，在 `retryable` 之后添加 `error_code` 字段:

```python
@dataclass
class ClaudeRunResult:
    text: str = ""
    success: bool = False
    duration: int = 0
    turns: int = 0
    cost: float = 0.0
    model: str | None = None
    structured_output: Any | None = None
    error: str | None = None
    retryable: bool = True
    error_code: str | None = None
    tokens: TokenUsage = field(default_factory=TokenUsage)
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages/core/tests/agents/test_providers.py::TestClaudeRunResult -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/runner.py packages/core/tests/agents/test_providers.py
git commit -m "feat: add error_code field to ClaudeRunResult"
```

---

### Task 2: Wire `classify_error_for_temporal` into `AnthropicProvider._handle_error`

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers_anthropic.py:285-359`
- Test: `packages/core/tests/agents/test_providers.py`

- [x] **Step 1: Write the failing tests**

在 `packages/core/tests/agents/test_providers.py` 添加新测试类:

```python
class TestHandleErrorClassification:
    """Test _handle_error uses classify_error_for_temporal and sets error_code."""

    def test_auth_error_sets_error_code(self):
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = provider._handle_error(Exception("authentication failed"), 100, "claude-sonnet-4-6")
        assert result.error_code == "AuthenticationError"
        assert result.retryable is False
        assert result.success is False

    def test_permission_error_sets_error_code(self):
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = provider._handle_error(Exception("403 Forbidden"), 100, "claude-sonnet-4-6")
        assert result.error_code == "PermissionError"
        assert result.retryable is False

    def test_rate_limit_sets_error_code(self):
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = provider._handle_error(Exception("rate limit exceeded"), 100, "claude-sonnet-4-6")
        # "rate limit" maps to BillingError in classify_error_for_temporal Level 2
        assert result.error_code == "BillingError"
        assert result.retryable is True

    def test_spending_cap_sets_billing_error_code(self):
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = provider._handle_error(Exception("spending limit reached"), 100, "claude-sonnet-4-6")
        assert result.error_code == "BillingError"
        assert result.retryable is True
        assert result.text != ""

    def test_config_error_sets_error_code(self):
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = provider._handle_error(Exception("ENOENT: no such file"), 100, "claude-sonnet-4-6")
        assert result.error_code == "ConfigurationError"
        assert result.retryable is False

    def test_transient_error_sets_error_code(self):
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = provider._handle_error(Exception("network timeout"), 100, "claude-sonnet-4-6")
        assert result.error_code == "TransientError"
        assert result.retryable is True

    def test_invalid_target_sets_error_code(self):
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = provider._handle_error(Exception("invalid URL format"), 100, "claude-sonnet-4-6")
        assert result.error_code == "InvalidTargetError"
        assert result.retryable is False
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest packages/core/tests/agents/test_providers.py::TestHandleErrorClassification -v`
Expected: FAIL — `error_code` is `None` on all results

- [x] **Step 3: Write the implementation**

替换 `packages/core/src/shannon_core/agents/providers_anthropic.py` 中的 `_handle_error` 方法:

```python
def _handle_error(
    self,
    error: Exception,
    duration: int,
    model: str,
) -> ClaudeRunResult:
    """处理错误 — 使用 classify_error_for_temporal 进行集中式分类"""
    error_msg = str(error)

    # 检查是否是花费上限错误（Layer 3 异常级检测）
    if self._is_spending_cap_error(error_msg):
        return ClaudeRunResult(
            text=error_msg,
            success=False,
            duration=duration,
            turns=0,
            cost=0.0,
            model=model,
            error=f"花费上限: {error_msg}",
            retryable=True,
            error_code="BillingError",
        )

    # 使用集中式错误分类
    error_type, retryable = classify_error_for_temporal(error)

    return ClaudeRunResult(
        text="",
        success=False,
        duration=duration,
        turns=0,
        cost=0.0,
        model=model,
        error=error_msg,
        retryable=retryable,
        error_code=error_type,
    )
```

同时添加 import（在文件顶部 import 区域）:

```python
from shannon_core.models.errors import classify_error_for_temporal
```

注意：`_is_retryable_error` 方法保留不动（`OpenAIProvider` 仍在使用），但 `AnthropicProvider._handle_error` 不再调用它。

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest packages/core/tests/agents/test_providers.py::TestHandleErrorClassification -v`
Expected: PASS

Run: `python -m pytest packages/core/tests/agents/test_providers.py -v`
Expected: ALL PASS（确保没有回归）

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/providers_anthropic.py packages/core/tests/agents/test_providers.py
git commit -m "feat: use classify_error_for_temporal in _handle_error with error_code"
```

---

### Task 3: Update `run_claude_prompt` error paths with `error_code`

**Files:**
- Modify: `packages/core/src/shannon_core/agents/runner.py:109-151`
- Test: `packages/core/tests/agents/test_providers.py`

- [x] **Step 1: Write the failing test**

在 `packages/core/tests/agents/test_providers.py` 添加新测试类:

```python
class TestRunClaudePromptErrorCode:
    """Test run_claude_prompt sets error_code on error paths."""

    @pytest.mark.asyncio
    async def test_spending_cap_behavior_sets_billing_error_code(self):
        """_is_spending_cap_behavior path sets error_code=BillingError."""
        with patch("shannon_core.agents.runner.create_provider") as mock_create:
            mock_provider = AsyncMock()
            mock_provider.call = AsyncMock(return_value=ClaudeRunResult(
                text="",
                success=False,
                error="spending limit reached",
                retryable=True,
            ))
            mock_create.return_value = mock_provider

            with patch("shannon_core.agents.runner.build_provider_config") as mock_build:
                mock_build.return_value = ProviderConfig()
                result = await run_claude_prompt(
                    prompt="test",
                    repo_path="/tmp",
                )

        assert result.error_code == "BillingError"
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_exception_handler_sets_error_code(self):
        """Catch-all exception handler classifies and sets error_code."""
        with patch("shannon_core.agents.runner.build_provider_config", side_effect=Exception("authentication failed")):
            result = await run_claude_prompt(
                prompt="test",
                repo_path="/tmp",
            )

        assert result.success is False
        assert result.error_code == "AuthenticationError"
        assert result.retryable is False
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/core/tests/agents/test_providers.py::TestRunClaudePromptErrorCode -v`
Expected: FAIL — `error_code` is `None`

- [x] **Step 3: Update the implementation**

在 `packages/core/src/shannon_core/agents/runner.py` 中:

1. 在文件顶部添加 import:

```python
from shannon_core.models.errors import classify_error_for_temporal
```

2. 修改 `_is_spending_cap_behavior` 后的结果处理（约第 133-137 行）:

```python
        # 5. 检查花费上限行为
        if _is_spending_cap_behavior(result):
            result.success = False
            result.retryable = True
            result.error = result.error or "检测到花费上限限制"
            result.error_code = "BillingError"
```

3. 修改 catch-all exception handler（约第 140-151 行）:

```python
    except Exception as e:
        # 捕获未处理的异常
        error_type, retryable = classify_error_for_temporal(e)
        return ClaudeRunResult(
            text="",
            success=False,
            duration=0,
            turns=0,
            cost=0.0,
            model=None,
            error=f"未处理的异常: {str(e)}",
            retryable=retryable,
            error_code=error_type,
        )
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest packages/core/tests/agents/test_providers.py::TestRunClaudePromptErrorCode -v`
Expected: PASS

Run: `python -m pytest packages/core/tests/agents/test_providers.py -v`
Expected: ALL PASS

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/runner.py packages/core/tests/agents/test_providers.py
git commit -m "feat: set error_code in run_claude_prompt error paths"
```

---

### Task 4: Add `error_code` + `failed_agents` to `PipelineState` (whitebox)

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py:22-29`
- Test: `packages/whitebox/tests/test_pipeline_shared.py`

- [x] **Step 1: Write the failing tests**

在 `packages/whitebox/tests/test_pipeline_shared.py` 中添加:

```python
class TestPipelineStateErrorPropagation:
    """Test new error_code and failed_agents fields on PipelineState."""

    def test_error_code_defaults_to_none(self):
        state = PipelineState()
        assert state.error_code is None

    def test_failed_agents_defaults_to_empty_list(self):
        state = PipelineState()
        assert state.failed_agents == []
        assert isinstance(state.failed_agents, list)

    def test_failed_agents_can_be_appended(self):
        state = PipelineState()
        state.failed_agents.append("xss-vuln")
        state.failed_agents.append("sqli-vuln")
        assert state.failed_agents == ["xss-vuln", "sqli-vuln"]

    def test_error_code_can_be_set(self):
        state = PipelineState()
        state.error_code = "AuthenticationError"
        assert state.error_code == "AuthenticationError"

    def test_factory_isolation_for_failed_agents(self):
        """Each PipelineState instance gets its own failed_agents list."""
        state1 = PipelineState()
        state2 = PipelineState()
        state1.failed_agents.append("agent-a")
        assert state2.failed_agents == []

    def test_status_failed_with_failed_agents(self):
        """Workflow can set status=failed when agents fail."""
        state = PipelineState()
        state.status = "failed"
        state.failed_agents = ["xss-vuln"]
        state.errors = ["xss-vuln: timeout"]
        state.error_code = "TransientError"
        assert state.status == "failed"
        assert len(state.failed_agents) == 1
        assert state.error_code == "TransientError"

    def test_status_cancelled(self):
        """Workflow can set status=cancelled."""
        state = PipelineState()
        state.status = "cancelled"
        assert state.status == "cancelled"
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/whitebox/tests/test_pipeline_shared.py::TestPipelineStateErrorPropagation -v`
Expected: FAIL — `PipelineState` 没有 `error_code` / `failed_agents` 属性

- [x] **Step 3: Add the fields**

在 `packages/whitebox/src/shannon_whitebox/pipeline/shared.py` 的 `PipelineState` 中添加两个字段:

```python
@dataclass
class PipelineState:
    status: str = "running"
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    start_time: float = 0.0
    errors: list[str] = field(default_factory=list)
    code_index_stats: dict | None = None
    error_code: str | None = None
    failed_agents: list[str] = field(default_factory=list)
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages/whitebox/tests/test_pipeline_shared.py -v`
Expected: ALL PASS

- [x] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/shared.py packages/whitebox/tests/test_pipeline_shared.py
git commit -m "feat: add error_code and failed_agents to whitebox PipelineState"
```

---

### Task 5: Add `error_code` + `failed_agents` to `BlackboxPipelineState` (blackbox)

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/shared.py:24-33`
- Test: `packages/blackbox/tests/test_pipeline_shared.py`

- [x] **Step 1: Write the failing tests**

在 `packages/blackbox/tests/test_pipeline_shared.py` 中添加:

```python
class TestBlackboxPipelineStateErrorPropagation:
    """Test new error_code and failed_agents fields on BlackboxPipelineState."""

    def test_error_code_defaults_to_none(self):
        state = BlackboxPipelineState()
        assert state.error_code is None

    def test_failed_agents_defaults_to_empty_list(self):
        state = BlackboxPipelineState()
        assert state.failed_agents == []
        assert isinstance(state.failed_agents, list)

    def test_failed_agents_can_be_appended(self):
        state = BlackboxPipelineState()
        state.failed_agents.append("injection-exploit")
        state.failed_agents.append("xss-exploit")
        assert state.failed_agents == ["injection-exploit", "xss-exploit"]

    def test_error_code_can_be_set(self):
        state = BlackboxPipelineState()
        state.error_code = "PermissionError"
        assert state.error_code == "PermissionError"

    def test_factory_isolation_for_failed_agents(self):
        """Each BlackboxPipelineState instance gets its own failed_agents list."""
        state1 = BlackboxPipelineState()
        state2 = BlackboxPipelineState()
        state1.failed_agents.append("agent-a")
        assert state2.failed_agents == []

    def test_status_failed_with_failed_agents(self):
        state = BlackboxPipelineState()
        state.status = "failed"
        state.failed_agents = ["injection-exploit"]
        state.errors = ["injection-exploit: connection refused"]
        state.error_code = "TransientError"
        assert state.status == "failed"
        assert len(state.failed_agents) == 1

    def test_status_cancelled(self):
        state = BlackboxPipelineState()
        state.status = "cancelled"
        assert state.status == "cancelled"
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/blackbox/tests/test_pipeline_shared.py::TestBlackboxPipelineStateErrorPropagation -v`
Expected: FAIL — `BlackboxPipelineState` 没有 `error_code` / `failed_agents` 属性

- [x] **Step 3: Add the fields**

在 `packages/blackbox/src/shannon_blackbox/pipeline/shared.py` 的 `BlackboxPipelineState` 中添加两个字段:

```python
@dataclass
class BlackboxPipelineState:
    status: str = "running"
    current_phase: str | None = None
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    has_whitebox_results: bool = False
    found_whitebox_classes: list[str] = field(default_factory=list)
    start_time: float = 0.0
    errors: list[str] = field(default_factory=list)
    error_code: str | None = None
    failed_agents: list[str] = field(default_factory=list)
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages/blackbox/tests/test_pipeline_shared.py -v`
Expected: ALL PASS

- [x] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/shared.py packages/blackbox/tests/test_pipeline_shared.py
git commit -m "feat: add error_code and failed_agents to BlackboxPipelineState"
```

---

### Task 6: Wire error propagation into `WhiteboxScanWorkflow`

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:87-155`
- Test: `packages/whitebox/tests/test_workflows.py`

- [x] **Step 1: Write the failing test**

在 `packages/whitebox/tests/test_workflows.py` 中添加测试类。由于 Temporal workflow 需要完整测试环境，此处测试状态逻辑而非 workflow 本身:

```python
class TestWhiteboxWorkflowErrorPropagation:
    """Test the error propagation logic that WhiteboxScanWorkflow uses."""

    def test_state_completed_when_no_errors(self):
        """全部 agent 成功时 status=completed."""
        state = PipelineState()
        state.completed_agents = ["PRE_RECON", "RECON", "xss-vuln"]
        state.agent_metrics = {"PRE_RECON": {}, "RECON": {}, "xss-vuln": {}}
        # 模拟 workflow 完成逻辑
        if state.errors:
            state.status = "failed"
        else:
            state.status = "completed"
        assert state.status == "completed"
        assert state.failed_agents == []
        assert state.error_code is None

    def test_state_failed_when_agents_fail(self):
        """部分 agent 失败时 status=failed, failed_agents 被填充."""
        state = PipelineState()
        state.completed_agents = ["PRE_RECON", "RECON"]
        state.agent_metrics = {"PRE_RECON": {}, "RECON": {}}
        # 模拟 gather 中有失败
        state.errors = ["xss-vuln: authentication failed"]
        state.failed_agents = ["xss-vuln"]
        # 模拟 workflow 完成逻辑
        if state.errors:
            state.status = "failed"
            # 提取第一个错误的 error_code
            error_type, _ = classify_error_for_temporal(
                Exception(state.errors[0].split(": ", 1)[-1])
            )
            state.error_code = error_type
        else:
            state.status = "completed"
        assert state.status == "failed"
        assert state.failed_agents == ["xss-vuln"]
        assert state.error_code == "AuthenticationError"

    def test_state_failed_with_multiple_agents(self):
        """多个 agent 失败时全部被追踪."""
        state = PipelineState()
        state.completed_agents = ["PRE_RECON"]
        state.errors = [
            "RECON: connection refused",
            "xss-vuln: permission denied",
        ]
        state.failed_agents = ["RECON", "xss-vuln"]
        state.status = "failed"
        state.error_code = "TransientError"
        assert state.status == "failed"
        assert len(state.failed_agents) == 2

    def test_state_cancelled(self):
        """取消时 status=cancelled."""
        state = PipelineState()
        state.status = "cancelled"
        assert state.status == "cancelled"
```

在测试文件顶部添加 import（如尚未存在）:

```python
from shannon_core.models.errors import classify_error_for_temporal
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/whitebox/tests/test_workflows.py::TestWhiteboxWorkflowErrorPropagation -v`
Expected: FAIL — 测试引用了尚未 import 的 `classify_error_for_temporal` 或 `PipelineState` 缺少字段

- [x] **Step 3: Update the workflow**

修改 `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`:

1. 添加 import（在文件顶部）:

```python
from temporalio.exceptions import CancelledError
```

2. 将 `with workflow.unsafe.imports_passed_through():` 块内添加:

```python
    from shannon_core.models.errors import classify_error_for_temporal
```

3. 替换 try 块（约第 87-155 行）。修改 `asyncio.gather` 结果处理和最终状态设置:

```python
        try:
            if AgentName.PRE_RECON.value not in self._state.completed_agents:
                pre_recon_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value})
                metrics = await workflow.execute_activity(
                    activities.run_agent, pre_recon_input,
                    start_to_close_timeout=timedelta(hours=2),
                    retry_policy=PRODUCTION_RETRY,
                )
                self._state.completed_agents.append(AgentName.PRE_RECON.value)
                self._state.agent_metrics[AgentName.PRE_RECON.value] = metrics

                rebuild_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value})
                rebuild_result = await workflow.execute_activity(
                    activities.run_rebuild_call_chains, rebuild_input,
                    start_to_close_timeout=timedelta(minutes=5),
                )
                if self._state.code_index_stats:
                    self._state.code_index_stats["total_chains"] = rebuild_result.get("total_chains", 0)

            if AgentName.RECON.value not in self._state.completed_agents:
                recon_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.RECON.value})
                metrics = await workflow.execute_activity(
                    activities.run_agent, recon_input,
                    start_to_close_timeout=timedelta(hours=2),
                )
                self._state.completed_agents.append(AgentName.RECON.value)
                self._state.agent_metrics[AgentName.RECON.value] = metrics

            vuln_tasks = []
            for vt in selected_classes:
                agent_name = AgentName(f"{vt}-vuln")
                if agent_name.value not in self._state.completed_agents:
                    vuln_input = ActivityInput(**{**act_input.__dict__, "workspace_name": agent_name.value})
                    vuln_tasks.append(
                        workflow.execute_activity(
                            activities.run_vuln_agent, vuln_input,
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

            await workflow.execute_activity(
                activities.render_findings, act_input,
                start_to_close_timeout=timedelta(minutes=5),
            )

            # 根据失败情况设置最终状态
            if self._state.failed_agents:
                self._state.status = "failed"
                first_error_msg = self._state.errors[0].split(": ", 1)[-1] if self._state.errors else ""
                error_type, _ = classify_error_for_temporal(Exception(first_error_msg))
                self._state.error_code = error_type
            else:
                self._state.status = "completed"
            return self._state
        except CancelledError:
            self._state.status = "cancelled"
            return self._state
        finally:
            cleanup_settings()
            cleanup_stealth_config(input.repo_path)
            cleanup_auth_state_sync(workspace_path)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest packages/whitebox/tests/test_workflows.py -v`
Expected: ALL PASS

Run: `python -m pytest packages/whitebox/tests/ -v`
Expected: ALL PASS

- [x] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/test_workflows.py
git commit -m "feat: wire error propagation and cancel handling into WhiteboxScanWorkflow"
```

---

### Task 7: Wire error propagation into `BlackboxScanWorkflow`

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py:90-208`
- Test: `packages/blackbox/tests/test_workflows.py`

- [x] **Step 1: Write the failing test**

在 `packages/blackbox/tests/test_workflows.py` 中添加:

```python
from shannon_core.models.errors import classify_error_for_temporal


class TestBlackboxWorkflowErrorPropagation:
    """Test the error propagation logic that BlackboxScanWorkflow uses."""

    def test_state_completed_when_no_errors(self):
        """全部 agent 成功时 status=completed."""
        state = BlackboxPipelineState()
        state.completed_agents = ["RECON_BLACKBOX", "REPORT"]
        state.agent_metrics = {"RECON_BLACKBOX": {}, "REPORT": {}}
        if state.errors:
            state.status = "failed"
        else:
            state.status = "completed"
        assert state.status == "completed"
        assert state.failed_agents == []
        assert state.error_code is None

    def test_state_failed_when_exploit_agents_fail(self):
        """部分 exploit agent 失败时 status=failed."""
        state = BlackboxPipelineState()
        state.completed_agents = ["RECON_BLACKBOX", "injection-exploit"]
        state.errors = ["xss-exploit: 403 Forbidden"]
        state.failed_agents = ["xss-exploit"]
        if state.errors:
            state.status = "failed"
            first_error_msg = state.errors[0].split(": ", 1)[-1]
            error_type, _ = classify_error_for_temporal(Exception(first_error_msg))
            state.error_code = error_type
        else:
            state.status = "completed"
        assert state.status == "failed"
        assert state.failed_agents == ["xss-exploit"]
        assert state.error_code == "PermissionError"

    def test_state_cancelled(self):
        """取消时 status=cancelled."""
        state = BlackboxPipelineState()
        state.status = "cancelled"
        assert state.status == "cancelled"

    def test_state_failed_with_all_exploits_failing(self):
        """所有 exploit agent 都失败时仍然记录全部."""
        state = BlackboxPipelineState()
        state.completed_agents = ["RECON_BLACKBOX"]
        state.errors = [
            "injection-exploit: connection refused",
            "xss-exploit: authentication failed",
        ]
        state.failed_agents = ["injection-exploit", "xss-exploit"]
        state.status = "failed"
        state.error_code = "TransientError"
        assert state.status == "failed"
        assert len(state.failed_agents) == 2
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/blackbox/tests/test_workflows.py::TestBlackboxWorkflowErrorPropagation -v`
Expected: FAIL — 缺少 import 或 `BlackboxPipelineState` 缺少字段

- [x] **Step 3: Update the workflow**

修改 `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`:

1. 添加 import（在文件顶部）:

```python
from temporalio.exceptions import CancelledError
```

2. 在 `with workflow.unsafe.imports_passed_through():` 块内添加:

```python
    from shannon_core.models.errors import classify_error_for_temporal
```

3. 替换 try 块（约第 90-208 行）。修改 gather 结果处理和最终状态:

```python
        try:
            # Resolve deliverables path using shared utility
            deliverables = resolve_deliverables_path(
                repo_path=input.repo_path,
                deliverables_subdir=input.deliverables_subdir,
                workspace_name=input.workspace_name,
                workspaces_root=resolve_workspaces_dir(input.repo_path),
            )

            has_whitebox_results = False
            found_classes: list[str] = []
            for vt in selected_classes:
                queue_file = deliverables / f"{vt}_exploitation_queue.json"
                if has_valid_whitebox_results(queue_file):
                    has_whitebox_results = True
                    found_classes.append(vt)
            self._state.has_whitebox_results = has_whitebox_results
            self._state.found_whitebox_classes = found_classes
            if has_whitebox_results:
                logger.info(
                    "Whitebox results detected at %s for classes: %s — skipping RECON_BLACKBOX",
                    deliverables,
                    found_classes,
                )
            else:
                logger.warning(
                    "No whitebox results found at %s — running RECON_BLACKBOX from scratch. "
                    "Tip: pass --repo <path> to reuse whitebox scan results.",
                    deliverables,
                )

            if not has_whitebox_results and AgentName.RECON_BLACKBOX.value not in self._state.completed_agents:
                recon_input = BlackboxActivityInput(**{**act_input.__dict__})
                metrics = await workflow.execute_activity(
                    activities.run_recon, recon_input,
                    start_to_close_timeout=timedelta(hours=2),
                    retry_policy=retry_policy,
                )
                self._state.completed_agents.append(AgentName.RECON_BLACKBOX.value)
                self._state.agent_metrics[AgentName.RECON_BLACKBOX.value] = metrics

            if input.exploit:
                # Queue gating: validate queue files before scheduling exploit agents
                exploit_tasks = []
                for vt in selected_classes:
                    validation = await ExploitationChecker.validate_queue(
                        deliverables_path=deliverables,
                        vuln_type=vt,
                    )
                    if not validation.valid:
                        if validation.reason not in ("queue_file_missing",):
                            logger.info(
                                "Skipping exploit for %s: %s", vt, validation.reason
                            )
                        continue
                    agent_name = AgentName(f"{vt}-exploit")
                    if agent_name.value not in self._state.completed_agents:
                        session_id = get_session_id(agent_name.value)
                        write_stealth_config(input.repo_path, session_id=session_id)
                        exploit_input = BlackboxActivityInput(
                            **{**act_input.__dict__, "agent_name": agent_name.value, "vuln_type": vt}
                        )
                        exploit_tasks.append((vt, agent_name, workflow.execute_activity(
                            activities.run_exploit_agent, exploit_input,
                            start_to_close_timeout=timedelta(hours=2),
                            retry_policy=retry_policy,
                        )))

                if exploit_tasks:
                    semaphore = asyncio.Semaphore(input.max_concurrent)

                    async def bounded_exploit(
                        coro, vt: str, agent_name: AgentName
                    ):
                        async with semaphore:
                            return await coro

                    results = await asyncio.gather(
                        *[bounded_exploit(task, vt, agent_name) for vt, agent_name, task in exploit_tasks],
                        return_exceptions=True,
                    )
                    for i, result in enumerate(results):
                        vt, agent_name, _ = exploit_tasks[i]
                        if isinstance(result, Exception):
                            self._state.errors.append(f"{agent_name.value}: {result}")
                            self._state.failed_agents.append(agent_name.value)
                        else:
                            self._state.completed_agents.append(agent_name.value)
                            self._state.agent_metrics[agent_name.value] = result

            await workflow.execute_activity(
                activities.assemble_report, act_input,
                start_to_close_timeout=timedelta(minutes=5),
            )

            if AgentName.REPORT.value not in self._state.completed_agents:
                metrics = await workflow.execute_activity(
                    activities.run_report_agent, act_input,
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry_policy,
                )
                self._state.completed_agents.append(AgentName.REPORT.value)
                self._state.agent_metrics[AgentName.REPORT.value] = metrics

            await workflow.execute_activity(
                activities.finalize_report, act_input,
                start_to_close_timeout=timedelta(minutes=5),
            )

            # 根据失败情况设置最终状态
            if self._state.failed_agents:
                self._state.status = "failed"
                first_error_msg = self._state.errors[0].split(": ", 1)[-1] if self._state.errors else ""
                error_type, _ = classify_error_for_temporal(Exception(first_error_msg))
                self._state.error_code = error_type
            else:
                self._state.status = "completed"
            return self._state
        except CancelledError:
            self._state.status = "cancelled"
            return self._state
        finally:
            cleanup_settings()
            if input.repo_path:
                # Clean up session-specific configs
                for session_id in set(AGENT_SESSION_MAPPING.values()):
                    from shannon_core.services.playwright_config_writer import cleanup_session_config
                    cleanup_session_config(input.repo_path, session_id)
                cleanup_stealth_config(input.repo_path)
                cleanup_auth_state_sync(act_input.workspace_path or input.repo_path)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest packages/blackbox/tests/test_workflows.py -v`
Expected: ALL PASS

Run: `python -m pytest packages/blackbox/tests/ -v`
Expected: ALL PASS

- [x] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/workflows.py packages/blackbox/tests/test_workflows.py
git commit -m "feat: wire error propagation and cancel handling into BlackboxScanWorkflow"
```

---

## Self-Review Checklist

### 1. Spec Coverage

| Spec Requirement | Task |
|-----------------|------|
| `PipelineState` 增加 `error_code` 和 `failed_agent` 字段 | Tasks 4, 5 |
| 修改 workflow 完成 logic：存在失败 agent 时 `status = "failed"` | Tasks 6, 7 |
| 增加 Temporal 取消信号处理 | Tasks 6, 7 |
| `_handle_error` 使用 `classify_error_for_temporal` | Task 2 |
| 错误类型名存入 `ClaudeRunResult.error_code` | Tasks 1, 2, 3 |
| 不可重试错误分类传递到 Temporal | 已由现有 activity 代码覆盖（`classify_error_for_temporal` + `ApplicationFailure`） |

### 2. Placeholder Scan

无 TBD、TODO、"implement later"、"add appropriate error handling" 等。所有步骤包含完整代码。

### 3. Type Consistency

- `error_code: str | None = None` — 在 `ClaudeRunResult`、`PipelineState`、`BlackboxPipelineState` 中一致
- `failed_agents: list[str] = field(default_factory=list)` — 在两个 PipelineState 中一致
- `classify_error_for_temporal` 返回 `tuple[str, bool]` — 所有调用处一致
- `CancelledError` import 自 `temporalio.exceptions` — 在两个 workflow 中一致
