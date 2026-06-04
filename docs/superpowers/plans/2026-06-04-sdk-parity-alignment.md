# SDK Parity Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Align shannon-py's claude-agent-sdk usage with the original TS project across 4 areas: env var passthrough, message stream processing, tool call auditing, and spending cap detection.

**Architecture:** Four independent stages, each producing working, testable code. New files (`message_dispatcher.py`, `tool_audit_logger.py`) follow the Null Object pattern. The existing `providers_anthropic.py` is modified incrementally across stages.

**Tech Stack:** Python 3.12+, claude-agent-sdk, pytest, pytest-asyncio, unittest.mock

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `packages/core/src/shannon_core/agents/tool_audit_logger.py` | **Create** | ABC + NullToolAuditLogger + ActivityToolAuditLogger |
| `packages/core/src/shannon_core/agents/message_dispatcher.py` | **Create** | Stream event dispatcher with turn counting, text collection, spending cap detection |
| `packages/core/src/shannon_core/agents/providers_anthropic.py` | **Modify** | Replace env handling with `_build_sdk_env()`, wire dispatcher into `_execute_query()` and `call()` |
| `packages/core/tests/agents/test_tool_audit_logger.py` | **Create** | Tests for audit logger interface and implementations |
| `packages/core/tests/agents/test_message_dispatcher.py` | **Create** | Tests for all event dispatch paths |
| `packages/core/tests/agents/test_providers.py` | **Modify** | Update/add tests for env passthrough, dispatcher integration, spending cap layers |

---

### Task 1: ToolAuditLogger ABC and NullToolAuditLogger

**Files:**
- Create: `packages/core/src/shannon_core/agents/tool_audit_logger.py`
- Test: `packages/core/tests/agents/test_tool_audit_logger.py`

- [x] **Step 1: Write the failing test for NullToolAuditLogger**

```python
"""Tests for tool_audit_logger module."""

import pytest

from shannon_core.agents.tool_audit_logger import (
    NullToolAuditLogger,
    ToolAuditLogger,
)


class TestToolAuditLoggerInterface:
    """Verify the ABC contract."""

    def test_cannot_instantiate_abc(self):
        """ToolAuditLogger is abstract and cannot be instantiated directly."""
        with pytest.raises(TypeError):
            ToolAuditLogger()


class TestNullToolAuditLogger:
    """NullToolAuditLogger is a no-op implementation of ToolAuditLogger."""

    def test_is_subclass(self):
        assert issubclass(NullToolAuditLogger, ToolAuditLogger)

    @pytest.mark.asyncio
    async def test_log_tool_start_does_nothing(self):
        logger = NullToolAuditLogger()
        # Should not raise
        await logger.log_tool_start("bash", {"command": "ls"})

    @pytest.mark.asyncio
    async def test_log_tool_end_does_nothing(self):
        logger = NullToolAuditLogger()
        await logger.log_tool_end("output text")

    @pytest.mark.asyncio
    async def test_log_error_does_nothing(self):
        logger = NullToolAuditLogger()
        await logger.log_error("something broke", turn_count=3, duration_ms=500)
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/agents/test_tool_audit_logger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.agents.tool_audit_logger'`

- [x] **Step 3: Write minimal implementation**

Create `packages/core/src/shannon_core/agents/tool_audit_logger.py`:

```python
"""
Tool call audit logger — Null Object pattern.

Provides an ABC for tool call auditing with a no-op default implementation
so callers never need to null-check the logger.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ToolAuditLogger(ABC):
    """Tool call audit logger interface."""

    @abstractmethod
    async def log_tool_start(self, tool_name: str, parameters: Any) -> None: ...

    @abstractmethod
    async def log_tool_end(self, result: Any) -> None: ...

    @abstractmethod
    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None: ...


class NullToolAuditLogger(ToolAuditLogger):
    """No-op implementation — safe default when auditing is disabled."""

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        pass

    async def log_tool_end(self, result: Any) -> None:
        pass

    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None:
        pass
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/agents/test_tool_audit_logger.py -v`
Expected: PASS — all 5 tests

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/tool_audit_logger.py packages/core/tests/agents/test_tool_audit_logger.py
git commit -m "feat: add ToolAuditLogger ABC and NullToolAuditLogger"
```

---

### Task 2: ActivityToolAuditLogger

**Files:**
- Modify: `packages/core/src/shannon_core/agents/tool_audit_logger.py`
- Modify: `packages/core/tests/agents/test_tool_audit_logger.py`

- [x] **Step 1: Write the failing test for ActivityToolAuditLogger**

Append to `packages/core/tests/agents/test_tool_audit_logger.py`:

```python
from unittest.mock import MagicMock

from shannon_core.logging.activity_logger import ActivityLogger


class TestActivityToolAuditLogger:
    """ActivityToolAuditLogger bridges to ActivityLogger."""

    def test_is_subclass(self):
        assert issubclass(ActivityToolAuditLogger, ToolAuditLogger)

    @pytest.mark.asyncio
    async def test_log_tool_start_delegates(self):
        mock_activity = MagicMock(spec=ActivityLogger)
        logger = ActivityToolAuditLogger(mock_activity)

        await logger.log_tool_start("bash", {"command": "ls -la"})

        mock_activity.info.assert_called_once()
        call_kwargs = mock_activity.info.call_args
        assert call_kwargs[0][0] == "tool_start"
        assert call_kwargs[1]["tool_name"] == "bash"
        # Parameters are stringified and truncated to 500 chars
        assert "ls -la" in call_kwargs[1]["parameters"]

    @pytest.mark.asyncio
    async def test_log_tool_end_delegates(self):
        mock_activity = MagicMock(spec=ActivityLogger)
        logger = ActivityToolAuditLogger(mock_activity)

        await logger.log_tool_end("file contents here")

        mock_activity.info.assert_called_once()
        call_kwargs = mock_activity.info.call_args
        assert call_kwargs[0][0] == "tool_end"
        assert "file contents here" in call_kwargs[1]["result"]

    @pytest.mark.asyncio
    async def test_log_error_delegates(self):
        mock_activity = MagicMock(spec=ActivityLogger)
        logger = ActivityToolAuditLogger(mock_activity)

        await logger.log_error("timeout", turn_count=2, duration_ms=3000)

        mock_activity.error.assert_called_once()
        call_kwargs = mock_activity.error.call_args
        assert call_kwargs[0][0] == "agent_error"
        assert call_kwargs[1]["error"] == "timeout"
        assert call_kwargs[1]["turn_count"] == 2
        assert call_kwargs[1]["duration_ms"] == 3000

    @pytest.mark.asyncio
    async def test_log_tool_start_truncates_long_params(self):
        mock_activity = MagicMock(spec=ActivityLogger)
        logger = ActivityToolAuditLogger(mock_activity)

        long_params = {"data": "x" * 1000}
        await logger.log_tool_start("write", long_params)

        call_kwargs = mock_activity.info.call_args
        assert len(call_kwargs[1]["parameters"]) <= 500
```

Update the imports at the top of `test_tool_audit_logger.py`:

```python
"""Tests for tool_audit_logger module."""

import pytest
from unittest.mock import MagicMock

from shannon_core.agents.tool_audit_logger import (
    ActivityToolAuditLogger,
    NullToolAuditLogger,
    ToolAuditLogger,
)
from shannon_core.logging.activity_logger import ActivityLogger
```

Remove the now-duplicate imports from the test class bodies.

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/agents/test_tool_audit_logger.py::TestActivityToolAuditLogger -v`
Expected: FAIL — `ImportError: cannot import name 'ActivityToolAuditLogger'`

- [x] **Step 3: Write minimal implementation**

Append `ActivityToolAuditLogger` to `packages/core/src/shannon_core/agents/tool_audit_logger.py`, adding the import:

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shannon_core.logging.activity_logger import ActivityLogger


class ToolAuditLogger(ABC):
    """Tool call audit logger interface."""

    @abstractmethod
    async def log_tool_start(self, tool_name: str, parameters: Any) -> None: ...

    @abstractmethod
    async def log_tool_end(self, result: Any) -> None: ...

    @abstractmethod
    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None: ...


class NullToolAuditLogger(ToolAuditLogger):
    """No-op implementation — safe default when auditing is disabled."""

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        pass

    async def log_tool_end(self, result: Any) -> None:
        pass

    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None:
        pass


class ActivityToolAuditLogger(ToolAuditLogger):
    """Bridges tool audit events to ActivityLogger."""

    def __init__(self, logger: ActivityLogger) -> None:
        self._logger = logger

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        self._logger.info("tool_start", tool_name=tool_name, parameters=str(parameters)[:500])

    async def log_tool_end(self, result: Any) -> None:
        self._logger.info("tool_end", result=str(result)[:500])

    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None:
        self._logger.error("agent_error", error=error, turn_count=turn_count, duration_ms=duration_ms)
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/agents/test_tool_audit_logger.py -v`
Expected: PASS — all 10 tests

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/tool_audit_logger.py packages/core/tests/agents/test_tool_audit_logger.py
git commit -m "feat: add ActivityToolAuditLogger bridging to ActivityLogger"
```

---

### Task 3: MessageDispatcher core (event dispatch + text collection + turn counting)

**Files:**
- Create: `packages/core/src/shannon_core/agents/message_dispatcher.py`
- Create: `packages/core/tests/agents/test_message_dispatcher.py`

- [x] **Step 1: Write the failing tests**

Create `packages/core/tests/agents/test_message_dispatcher.py`:

```python
"""Tests for message_dispatcher module."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from claude_agent_sdk import ResultMessage

from shannon_core.agents.message_dispatcher import (
    SPENDING_CAP_PATTERNS,
    MessageDispatcher,
)
from shannon_core.agents.tool_audit_logger import NullToolAuditLogger


def _make_text_event(text: str) -> object:
    """Create a minimal text-type event."""
    event = MagicMock()
    event.type = "text"
    event.text = text
    return event


def _make_assistant_event(
    texts: list[str] | None = None,
    error: str | None = None,
) -> object:
    """Create a minimal assistant-type event."""
    event = MagicMock()
    event.type = "assistant"
    blocks = []
    for t in (texts or []):
        block = MagicMock()
        block.text = t
        blocks.append(block)
    event.content = blocks
    event.error = error
    return event


def _make_tool_use_event(name: str = "bash", input_params: dict | None = None) -> object:
    """Create a minimal tool_use-type event."""
    event = MagicMock()
    event.type = "tool_use"
    event.name = name
    event.input = input_params or {"command": "ls"}
    return event


def _make_tool_result_event(content: str = "file.txt") -> object:
    """Create a minimal tool_result-type event."""
    event = MagicMock()
    event.type = "tool_result"
    event.content = content
    return event


class TestMessageDispatcherDefaults:
    """Default construction and initial state."""

    def test_initial_state(self):
        d = MessageDispatcher()
        assert d.turn_count == 0
        assert d.collected_text == ""
        assert d.spending_cap_detected is False

    def test_default_audit_logger_is_null(self):
        d = MessageDispatcher()
        assert isinstance(d.audit_logger, NullToolAuditLogger)


class TestTextCollection:
    """Text events and assistant text blocks accumulate."""

    @pytest.mark.asyncio
    async def test_text_event_appends(self):
        d = MessageDispatcher()
        result = await d.dispatch(_make_text_event("hello "))
        assert result == "continue"
        assert d.collected_text == "hello "

    @pytest.mark.asyncio
    async def test_multiple_text_events(self):
        d = MessageDispatcher()
        await d.dispatch(_make_text_event("hello "))
        await d.dispatch(_make_text_event("world"))
        assert d.collected_text == "hello world"

    @pytest.mark.asyncio
    async def test_assistant_event_collects_text(self):
        d = MessageDispatcher()
        event = _make_assistant_event(texts=["response text"])
        result = await d.dispatch(event)
        assert result == "continue"
        assert d.collected_text == "response text"

    @pytest.mark.asyncio
    async def test_assistant_event_increments_turn_count(self):
        d = MessageDispatcher()
        await d.dispatch(_make_assistant_event(texts=["one"]))
        assert d.turn_count == 1
        await d.dispatch(_make_assistant_event(texts=["two"]))
        assert d.turn_count == 2


class TestResultMessage:
    """ResultMessage events signal completion."""

    @pytest.mark.asyncio
    async def test_result_message_returns_complete(self):
        d = MessageDispatcher()
        msg = ResultMessage(
            subtype="result",
            duration_ms=1000,
            duration_api_ms=500,
            is_error=False,
            num_turns=1,
            session_id="test",
        )
        result = await d.dispatch(msg)
        assert result == "complete"


class TestToolEvents:
    """Tool use and tool result events delegate to audit logger."""

    @pytest.mark.asyncio
    async def test_tool_use_calls_audit_logger(self):
        mock_logger = AsyncMock()
        d = MessageDispatcher(audit_logger=mock_logger)
        event = _make_tool_use_event("bash", {"command": "ls"})
        result = await d.dispatch(event)
        assert result == "continue"
        mock_logger.log_tool_start.assert_awaited_once_with("bash", {"command": "ls"})

    @pytest.mark.asyncio
    async def test_tool_result_calls_audit_logger(self):
        mock_logger = AsyncMock()
        d = MessageDispatcher(audit_logger=mock_logger)
        event = _make_tool_result_event("file.txt")
        result = await d.dispatch(event)
        assert result == "continue"
        mock_logger.log_tool_end.assert_awaited_once_with("file.txt")

    @pytest.mark.asyncio
    async def test_tool_use_progress_callback(self):
        progress = MagicMock()
        d = MessageDispatcher(progress_callback=progress)
        await d.dispatch(_make_tool_use_event("edit"))
        progress.assert_called_once_with("tool: edit")


class TestSpendingCapDetection:
    """Layer 1: message-level keyword detection in assistant text."""

    @pytest.mark.asyncio
    async def test_detects_spending_limit(self):
        d = MessageDispatcher()
        await d.dispatch(_make_assistant_event(texts=["your spending limit has been reached"]))
        assert d.spending_cap_detected is True

    @pytest.mark.asyncio
    async def test_detects_credit_limit(self):
        d = MessageDispatcher()
        await d.dispatch(_make_assistant_event(texts=["credit limit exceeded"]))
        assert d.spending_cap_detected is True

    @pytest.mark.asyncio
    async def test_detects_quota_exceeded(self):
        d = MessageDispatcher()
        await d.dispatch(_make_assistant_event(texts=["quota exceeded for this account"]))
        assert d.spending_cap_detected is True

    @pytest.mark.asyncio
    async def test_normal_text_no_detection(self):
        d = MessageDispatcher()
        await d.dispatch(_make_assistant_event(texts=["here is your code review"]))
        assert d.spending_cap_detected is False

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        d = MessageDispatcher()
        await d.dispatch(_make_assistant_event(texts=["SPENDING LIMIT REACHED"]))
        assert d.spending_cap_detected is True


class TestErrorCallback:
    """Error callback fires on assistant events with errors."""

    @pytest.mark.asyncio
    async def test_error_callback_invoked(self):
        errors = []
        d = MessageDispatcher(error_callback=lambda e: errors.append(e))
        await d.dispatch(_make_assistant_event(error="rate limited"))
        assert errors == ["rate limited"]

    @pytest.mark.asyncio
    async def test_no_error_callback_when_none(self):
        d = MessageDispatcher()
        # Should not raise
        await d.dispatch(_make_assistant_event(error="something"))


class TestUnknownEvents:
    """Unknown event types are silently continued."""

    @pytest.mark.asyncio
    async def test_unknown_event_type(self):
        d = MessageDispatcher()
        event = MagicMock()
        event.type = "something_else"
        result = await d.dispatch(event)
        assert result == "continue"


class TestSpendingCapPatterns:
    """Spending cap keyword list is correct."""

    def test_patterns_list(self):
        assert "spending limit" in SPENDING_CAP_PATTERNS
        assert "credit limit" in SPENDING_CAP_PATTERNS
        assert "quota exceeded" in SPENDING_CAP_PATTERNS
        assert "budget exceeded" in SPENDING_CAP_PATTERNS
        assert "maximum spend" in SPENDING_CAP_PATTERNS
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/agents/test_message_dispatcher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.agents.message_dispatcher'`

- [x] **Step 3: Write minimal implementation**

Create `packages/core/src/shannon_core/agents/message_dispatcher.py`:

```python
"""
Message stream processor for Claude Agent SDK events.

Processes 7+ event types (assistant, tool_use, tool_result, result, text, etc.)
with real-time dispatch, turn counting, text collection, and spending cap detection.
Aligned with TS message-handlers.ts capabilities.
"""

from __future__ import annotations

from typing import Any, Callable

from claude_agent_sdk import ResultMessage

from .tool_audit_logger import NullToolAuditLogger, ToolAuditLogger

SPENDING_CAP_PATTERNS = [
    "spending limit",
    "credit limit",
    "quota exceeded",
    "budget exceeded",
    "maximum spend",
]


class MessageDispatcher:
    """Processes Claude Agent SDK streaming events."""

    def __init__(
        self,
        audit_logger: ToolAuditLogger | None = None,
        progress_callback: Callable[[str], None] | None = None,
        error_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.turn_count = 0
        self.text_parts: list[str] = []
        self.spending_cap_detected = False
        self.audit_logger: ToolAuditLogger = audit_logger or NullToolAuditLogger()
        self._progress = progress_callback
        self._on_error = error_callback

    async def dispatch(self, event: Any) -> str:
        """Dispatch a single SDK event. Returns 'continue' or 'complete'."""
        if isinstance(event, ResultMessage):
            return "complete"

        event_type = getattr(event, "type", None)

        if event_type == "assistant":
            return await self._handle_assistant(event)
        elif event_type == "tool_use":
            return await self._handle_tool_use(event)
        elif event_type == "tool_result":
            return await self._handle_tool_result(event)
        elif event_type == "text":
            self.text_parts.append(event.text)
            return "continue"
        else:
            return "continue"

    async def _handle_assistant(self, event: Any) -> str:
        self.turn_count += 1
        for block in getattr(event, "content", []):
            if hasattr(block, "text"):
                text = block.text
                self.text_parts.append(text)
                if self._is_spending_cap_in_text(text):
                    self.spending_cap_detected = True
        error = getattr(event, "error", None)
        if error and self._on_error:
            self._on_error(str(error))
        return "continue"

    async def _handle_tool_use(self, event: Any) -> str:
        tool_name = getattr(event, "name", "unknown")
        params = getattr(event, "input", {})
        await self.audit_logger.log_tool_start(tool_name, params)
        if self._progress:
            self._progress(f"tool: {tool_name}")
        return "continue"

    async def _handle_tool_result(self, event: Any) -> str:
        content = getattr(event, "content", "")
        await self.audit_logger.log_tool_end(content)
        return "continue"

    @staticmethod
    def _is_spending_cap_in_text(text: str) -> bool:
        text_lower = text.lower()
        return any(kw in text_lower for kw in SPENDING_CAP_PATTERNS)

    @property
    def collected_text(self) -> str:
        return "".join(self.text_parts)
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/agents/test_message_dispatcher.py -v`
Expected: PASS — all tests

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/message_dispatcher.py packages/core/tests/agents/test_message_dispatcher.py
git commit -m "feat: add MessageDispatcher for SDK stream event processing"
```

---

### Task 4: Environment variable passthrough (`_build_sdk_env`)

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers_anthropic.py`
- Modify: `packages/core/tests/agents/test_providers.py`

- [x] **Step 1: Write the failing tests for `_build_sdk_env`**

Append to `packages/core/tests/agents/test_providers.py`:

```python
class TestBuildSdkEnv:
    """Test AnthropicProvider._build_sdk_env() env var passthrough."""

    def test_anthropic_api_with_config_api_key(self):
        """Config api_key is forwarded as ANTHROPIC_API_KEY."""
        config = ProviderConfig(type="anthropic_api", api_key="cfg-key")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {}, clear=True):
            env = provider._build_sdk_env()

        assert env.get("ANTHROPIC_API_KEY") == "cfg-key"

    def test_anthropic_api_passthrough_from_process_env(self):
        """Without config override, inherits ANTHROPIC_API_KEY from process env."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "env-key"}, clear=True):
            env = provider._build_sdk_env()

        assert env.get("ANTHROPIC_API_KEY") == "env-key"

    def test_anthropic_api_config_overrides_env(self):
        """Config api_key takes precedence over process env ANTHROPIC_API_KEY."""
        config = ProviderConfig(type="anthropic_api", api_key="cfg-key")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "env-key"}, clear=True):
            env = provider._build_sdk_env()

        assert env["ANTHROPIC_API_KEY"] == "cfg-key"

    def test_bedrock_sets_flags(self):
        """Bedrock provider sets CLAUDE_CODE_USE_BEDROCK and AWS_REGION."""
        config = ProviderConfig(type="bedrock", region="us-west-2")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {}, clear=True):
            env = provider._build_sdk_env()

        assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
        assert env["AWS_REGION"] == "us-west-2"

    def test_vertex_sets_flags(self):
        """Vertex provider sets CLAUDE_CODE_USE_VERTEX, CLOUD_ML_REGION, ANTHROPIC_VERTEX_PROJECT_ID."""
        config = ProviderConfig(type="vertex", region="europe-west1", project_id="proj-123")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {}, clear=True):
            env = provider._build_sdk_env()

        assert env["CLAUDE_CODE_USE_VERTEX"] == "1"
        assert env["CLOUD_ML_REGION"] == "europe-west1"
        assert env["ANTHROPIC_VERTEX_PROJECT_ID"] == "proj-123"

    def test_litellm_router_sets_base_url_and_auth_token(self):
        """LiteLLM router forwards base_url and auth_token."""
        config = ProviderConfig(
            type="litellm_router",
            base_url="https://router.example.com",
            auth_token="tok-abc",
        )
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {}, clear=True):
            env = provider._build_sdk_env()

        assert env["ANTHROPIC_BASE_URL"] == "https://router.example.com"
        assert env["ANTHROPIC_AUTH_TOKEN"] == "tok-abc"

    def test_passthrough_inherits_home_and_path(self):
        """HOME and PATH are always inherited from process env."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"HOME": "/home/test", "PATH": "/usr/bin"}, clear=True):
            env = provider._build_sdk_env()

        assert env["HOME"] == "/home/test"
        assert env["PATH"] == "/usr/bin"

    def test_passthrough_inherits_oauth_token(self):
        """CLAUDE_CODE_OAUTH_TOKEN is inherited from process env."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "oauth-tok"}, clear=True):
            env = provider._build_sdk_env()

        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-tok"

    def test_passthrough_inherits_playwright_path(self):
        """PLAYWRIGHT_MCP_EXECUTABLE_PATH is inherited from process env."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"PLAYWRIGHT_MCP_EXECUTABLE_PATH": "/usr/local/bin/npx"}, clear=True):
            env = provider._build_sdk_env()

        assert env["PLAYWRIGHT_MCP_EXECUTABLE_PATH"] == "/usr/local/bin/npx"

    def test_max_output_tokens_forwarded(self):
        """CLAUDE_CODE_MAX_OUTPUT_TOKENS is forwarded when set."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "128000"}, clear=True):
            env = provider._build_sdk_env()

        assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "128000"

    def test_default_max_output_tokens(self):
        """CLAUDE_CODE_MAX_OUTPUT_TOKENS defaults to 64000."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {}, clear=True):
            env = provider._build_sdk_env()

        assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "64000"

    def test_bedrock_inherits_bearer_token(self):
        """Bedrock inherits AWS_BEARER_TOKEN_BEDROCK from process env."""
        config = ProviderConfig(type="bedrock", region="us-east-1")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "bearer-tok"}, clear=True):
            env = provider._build_sdk_env()

        assert env["AWS_BEARER_TOKEN_BEDROCK"] == "bearer-tok"

    def test_vertex_inherits_google_credentials(self):
        """Vertex inherits GOOGLE_APPLICATION_CREDENTIALS from process env."""
        config = ProviderConfig(type="vertex", region="us-central1", project_id="proj")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": "/path/to/creds.json"}, clear=True):
            env = provider._build_sdk_env()

        assert env["GOOGLE_APPLICATION_CREDENTIALS"] == "/path/to/creds.json"

    def test_no_empty_values(self):
        """No empty-string values appear in the result."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {}, clear=True):
            env = provider._build_sdk_env()

        for key, val in env.items():
            assert val != "", f"Empty value for {key}"
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/agents/test_providers.py::TestBuildSdkEnv -v`
Expected: FAIL — `AttributeError: 'AnthropicProvider' object has no attribute '_build_sdk_env'`

- [x] **Step 3: Write minimal implementation**

Add `_build_sdk_env` method to `AnthropicProvider` in `packages/core/src/shannon_core/agents/providers_anthropic.py`. Replace lines 82–132 (`_build_options`) with:

```python
    def _build_sdk_env(self) -> dict[str, str]:
        """Build SDK subprocess environment variables (aligned with TS claude-executor.ts)."""
        sdk_env: dict[str, str] = {}

        # Base config
        max_tokens = os.getenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "64000")
        if max_tokens:
            sdk_env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = max_tokens

        # Provider-specific config
        if self.type == "anthropic_api":
            if self.config.api_key:
                sdk_env["ANTHROPIC_API_KEY"] = self.config.api_key
        elif self.type == "bedrock":
            sdk_env["CLAUDE_CODE_USE_BEDROCK"] = "1"
            if self.config.region:
                sdk_env["AWS_REGION"] = self.config.region
        elif self.type == "vertex":
            sdk_env["CLAUDE_CODE_USE_VERTEX"] = "1"
            if self.config.region:
                sdk_env["CLOUD_ML_REGION"] = self.config.region
            if self.config.project_id:
                sdk_env["ANTHROPIC_VERTEX_PROJECT_ID"] = self.config.project_id
        elif self.type == "litellm_router":
            if self.config.base_url:
                sdk_env["ANTHROPIC_BASE_URL"] = self.config.base_url
            if self.config.auth_token:
                sdk_env["ANTHROPIC_AUTH_TOKEN"] = self.config.auth_token

        # Conditional passthrough: inherit from process env if not set above
        PASSTHROUGH_VARS = [
            "ANTHROPIC_API_KEY",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_USE_BEDROCK",
            "AWS_REGION",
            "AWS_BEARER_TOKEN_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLOUD_ML_REGION",
            "ANTHROPIC_VERTEX_PROJECT_ID",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "HOME",
            "PATH",
            "PLAYWRIGHT_MCP_EXECUTABLE_PATH",
        ]

        for var in PASSTHROUGH_VARS:
            if var not in sdk_env:
                val = os.getenv(var)
                if val:
                    sdk_env[var] = val

        return sdk_env

    def _build_options(
        self,
        cwd: str,
        model: str,
        output_format: dict | None = None,
    ) -> ClaudeAgentOptions:
        """构建 ClaudeAgentOptions"""
        options = ClaudeAgentOptions(
            model=model,
            cwd=cwd,
            permission_mode="bypassPermissions",  # 无交互环境必需
        )

        # 添加结构化输出
        if output_format:
            options.output_format = output_format

        # 添加 adaptive thinking
        if self._is_adaptive_thinking_enabled():
            from claude_agent_sdk.types import ThinkingConfigAdaptive
            options.thinking = ThinkingConfigAdaptive()

        # Environment variables via _build_sdk_env
        options.env = self._build_sdk_env()

        return options
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/agents/test_providers.py -v`
Expected: PASS — all tests including new `TestBuildSdkEnv` and existing `TestAnthropicProviderBuildOptions`

Note: The existing `TestAnthropicProviderBuildOptions` tests need to be verified for compatibility. The key behavioral change is:
- Before: anthropic_api with no SHANNON_* vars → `options.env is None or {}`
- After: anthropic_api with no SHANNON_* vars → `options.env` inherits from process env (always has entries)

The existing tests `test_no_env_override_with_anthropic_key_only` expects `options.env is None or options.env == {}`. This test needs updating because the new behavior always passes through env vars:

Update `test_no_env_override_with_anthropic_key_only`:

```python
    def test_no_env_override_with_anthropic_key_only(self):
        """当只有 ANTHROPIC_API_KEY 时，options.env 应包含从进程继承的 key（SDK 不再自动读取）"""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True):
            options = provider._build_options(
                cwd="/tmp",
                model="claude-sonnet-4-6",
            )

        assert options.env is not None
        assert options.env.get("ANTHROPIC_API_KEY") == "sk-ant-test"
```

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/providers_anthropic.py packages/core/tests/agents/test_providers.py
git commit -m "feat: replace _build_options env handling with _build_sdk_env passthrough"
```

---

### Task 5: Wire MessageDispatcher into `_execute_query`

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers_anthropic.py`
- Modify: `packages/core/tests/agents/test_providers.py`

- [x] **Step 1: Write the failing test for dispatcher integration in `_execute_query`**

Append to `packages/core/tests/agents/test_providers.py`:

```python
from shannon_core.agents.message_dispatcher import MessageDispatcher


class TestExecuteQueryWithDispatcher:
    """Test _execute_query uses MessageDispatcher for event processing."""

    @pytest.mark.asyncio
    async def test_dispatcher_collects_text_from_events(self):
        """_execute_query collects text via dispatcher from mixed events."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        text_event = MagicMock()
        text_event.type = "text"
        text_event.text = "partial "

        assistant_event = MagicMock()
        assistant_event.type = "assistant"
        block = MagicMock()
        block.text = "response"
        assistant_event.content = [block]
        assistant_event.error = None

        mock_result = ResultMessage(
            subtype="result",
            duration_ms=1000,
            duration_api_ms=500,
            is_error=False,
            num_turns=1,
            session_id="test",
        )

        events = [text_event, assistant_event, mock_result]

        async def mock_query(*, prompt, options):
            for e in events:
                yield e

        with patch("shannon_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider._execute_query(
                prompt="test",
                options=ClaudeAgentOptions(model="claude-sonnet-4-6", cwd="/tmp"),
            )

        assert result.collected_text == "partial response"
        assert result.turn_count == 1

    @pytest.mark.asyncio
    async def test_dispatcher_with_custom_logger(self):
        """_execute_query accepts a custom dispatcher with injected audit logger."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        mock_audit = AsyncMock()
        dispatcher = MessageDispatcher(audit_logger=mock_audit)

        tool_use_event = MagicMock()
        tool_use_event.type = "tool_use"
        tool_use_event.name = "bash"
        tool_use_event.input = {"command": "ls"}

        mock_result = ResultMessage(
            subtype="result",
            duration_ms=500,
            duration_api_ms=200,
            is_error=False,
            num_turns=1,
            session_id="test",
        )

        events = [tool_use_event, mock_result]

        async def mock_query(*, prompt, options):
            for e in events:
                yield e

        with patch("shannon_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider._execute_query(
                prompt="test",
                options=ClaudeAgentOptions(model="claude-sonnet-4-6", cwd="/tmp"),
                dispatcher=dispatcher,
            )

        mock_audit.log_tool_start.assert_awaited_once_with("bash", {"command": "ls"})
```

Add `from claude_agent_sdk import ClaudeAgentOptions` to the top-level imports of `test_providers.py` (if not already present).

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/agents/test_providers.py::TestExecuteQueryWithDispatcher -v`
Expected: FAIL — `TypeError: _execute_query() got an unexpected keyword argument 'dispatcher'`

- [x] **Step 3: Write minimal implementation**

Replace `_execute_query` in `packages/core/src/shannon_core/agents/providers_anthropic.py` (lines 139–170) with:

```python
    async def _execute_query(
        self,
        prompt: str,
        options: ClaudeAgentOptions,
        dispatcher: MessageDispatcher | None = None,
    ) -> ResultMessage:
        """执行 query 调用并返回最终结果"""
        from .message_dispatcher import MessageDispatcher

        dispatcher = dispatcher or MessageDispatcher()
        final_result: ResultMessage | None = None

        async for event in query(prompt=prompt, options=options):
            action = await dispatcher.dispatch(event)
            if isinstance(event, ResultMessage):
                final_result = event
            if action == "complete":
                break

        if final_result is None:
            final_result = ResultMessage()

        final_result.collected_text = dispatcher.collected_text
        final_result.turn_count = dispatcher.turn_count
        return final_result
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/agents/test_providers.py -v`
Expected: PASS — all tests

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/providers_anthropic.py packages/core/tests/agents/test_providers.py
git commit -m "feat: wire MessageDispatcher into _execute_query for stream processing"
```

---

### Task 6: Update `_extract_result` to use dispatcher's turn_count

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers_anthropic.py`
- Modify: `packages/core/tests/agents/test_providers.py`

- [x] **Step 1: Write the failing test**

Append to `packages/core/tests/agents/test_providers.py`:

```python
class TestCallWithTurnCount:
    """Test that call() passes dispatcher turn_count to _extract_result."""

    @pytest.mark.asyncio
    async def test_call_returns_correct_turn_count(self):
        """call() returns turn_count from dispatcher, not hardcoded 1."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        # Create 3 assistant events to simulate 3 turns
        events = []
        for i in range(3):
            event = MagicMock()
            event.type = "assistant"
            block = MagicMock()
            block.text = f"turn {i + 1}"
            event.content = [block]
            event.error = None
            events.append(event)

        mock_result = ResultMessage(
            subtype="result",
            duration_ms=3000,
            duration_api_ms=1500,
            is_error=False,
            num_turns=3,
            session_id="test",
            total_cost_usd=0.01,
            result="done",
        )
        events.append(mock_result)

        async def mock_query(*, prompt, options):
            for e in events:
                yield e

        with patch("shannon_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider.call(
                prompt="multi-turn test",
                cwd="/tmp",
                model_tier="medium",
            )

        assert result.success is True
        assert result.turns == 3
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/agents/test_providers.py::TestCallWithTurnCount -v`
Expected: FAIL — `assert 1 == 3` (current `_extract_result` hardcodes `turns = 1`)

- [x] **Step 3: Write minimal implementation**

Update `_extract_result` in `packages/core/src/shannon_core/agents/providers_anthropic.py` to accept `turn_count` parameter:

```python
    def _extract_result(
        self,
        result_message: ResultMessage,
        duration: int,
        model: str,
        turn_count: int = 1,
    ) -> ClaudeRunResult:
        """从 ResultMessage 提取结果"""
        # 提取文本内容
        text = getattr(result_message, "collected_text", "")
        if not text and hasattr(result_message, "result"):
            text = result_message.result or ""

        # 如果有 content 属性，尝试从中提取文本
        if not text and hasattr(result_message, "content"):
            for block in result_message.content:
                if hasattr(block, "text"):
                    text += block.text

        # 提取 token 统计
        tokens = self._extract_tokens(result_message)

        # 提取成本
        cost = self._extract_cost(result_message)

        # 提取结构化输出
        structured_output = None
        if hasattr(result_message, "structured_output") and result_message.structured_output:
            structured_output = result_message.structured_output

        return ClaudeRunResult(
            text=text,
            success=True,
            duration=duration,
            turns=turn_count,
            cost=cost,
            model=model,
            structured_output=structured_output,
            tokens=tokens,
        )
```

Update `call()` to pass `turn_count` from dispatcher:

```python
    async def call(
        self,
        prompt: str,
        cwd: str,
        model_tier: str = "medium",
        output_format: dict | None = None,
        deliverables_subdir: str | None = None,
    ) -> ClaudeRunResult:
        """调用 Claude Agent SDK 执行 prompt"""
        start_time = time.time()
        model = self._get_model(model_tier)

        try:
            # 构建 SDK 配置
            options = self._build_options(cwd, model, output_format)

            # 执行调用
            result_message = await self._execute_query(prompt, options)

            # 计算耗时
            duration = int((time.time() - start_time) * 1000)

            # 提取结果（使用 dispatcher 的 turn_count）
            turn_count = getattr(result_message, "turn_count", 1)
            return self._extract_result(result_message, duration, model, turn_count)

        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            return self._handle_error(e, duration, model)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/agents/test_providers.py -v`
Expected: PASS — all tests

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/providers_anthropic.py packages/core/tests/agents/test_providers.py
git commit -m "feat: use dispatcher turn_count in _extract_result and call"
```

---

### Task 7: 3-layer spending cap detection

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers_anthropic.py`
- Modify: `packages/core/tests/agents/test_providers.py`

- [x] **Step 1: Write the failing tests**

Append to `packages/core/tests/agents/test_providers.py`:

```python
class TestSpendingCapDetection:
    """Test 3-layer spending cap detection."""

    def test_detect_spending_cap_behavior_trigger(self):
        """Low turns + zero cost + not successful triggers behavioral detection."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = ClaudeRunResult(
            text="",
            success=False,
            cost=0.0,
            turns=0,
        )
        assert provider._detect_spending_cap_behavior(result, turn_count=1) is True

    def test_detect_spending_cap_behavior_no_trigger_success(self):
        """Successful result does not trigger behavioral detection."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = ClaudeRunResult(
            text="done",
            success=True,
            cost=0.0,
            turns=0,
        )
        assert provider._detect_spending_cap_behavior(result, turn_count=1) is False

    def test_detect_spending_cap_behavior_no_trigger_high_turns(self):
        """Multiple turns do not trigger behavioral detection."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = ClaudeRunResult(
            text="",
            success=False,
            cost=0.0,
            turns=3,
        )
        assert provider._detect_spending_cap_behavior(result, turn_count=3) is False

    def test_detect_spending_cap_behavior_no_trigger_nonzero_cost(self):
        """Non-zero cost does not trigger behavioral detection."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = ClaudeRunResult(
            text="",
            success=False,
            cost=0.05,
            turns=0,
        )
        assert provider._detect_spending_cap_behavior(result, turn_count=1) is False

    @pytest.mark.asyncio
    async def test_layer1_message_level_detection(self):
        """Layer 1: spending cap keywords in assistant text set success=False, retryable=True."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        assistant_event = MagicMock()
        assistant_event.type = "assistant"
        block = MagicMock()
        block.text = "your spending limit has been reached"
        assistant_event.content = [block]
        assistant_event.error = None

        mock_result = ResultMessage(
            subtype="result",
            duration_ms=100,
            duration_api_ms=50,
            is_error=False,
            num_turns=1,
            session_id="test",
            total_cost_usd=0.0,
        )

        events = [assistant_event, mock_result]

        async def mock_query(*, prompt, options):
            for e in events:
                yield e

        with patch("shannon_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider.call(
                prompt="do work",
                cwd="/tmp",
                model_tier="medium",
            )

        assert result.success is False
        assert result.retryable is True
        assert "spending cap" in result.error
        assert "message-level" in result.error

    @pytest.mark.asyncio
    async def test_layer2_behavioral_detection(self):
        """Layer 2: low turns + zero cost + failure triggers behavioral detection."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        mock_result = ResultMessage(
            subtype="result",
            duration_ms=100,
            duration_api_ms=50,
            is_error=False,
            num_turns=1,
            session_id="test",
            total_cost_usd=0.0,
        )

        events = [mock_result]

        async def mock_query(*, prompt, options):
            for e in events:
                yield e

        with patch("shannon_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider.call(
                prompt="do work",
                cwd="/tmp",
                model_tier="medium",
            )

        assert result.success is False
        assert result.retryable is True
        assert "behavioral" in result.error

    @pytest.mark.asyncio
    async def test_layer3_exception_detection(self):
        """Layer 3: exception with spending cap keyword triggers _handle_error detection."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        async def mock_query(*, prompt, options):
            raise Exception("spending limit reached")
            yield  # make it a generator

        with patch("shannon_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider.call(
                prompt="do work",
                cwd="/tmp",
                model_tier="medium",
            )

        assert result.success is False
        assert result.retryable is True
        assert "花费上限" in result.error

    @pytest.mark.asyncio
    async def test_no_false_positive_on_success(self):
        """Successful execution is not flagged as spending cap."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        mock_result = ResultMessage(
            subtype="result",
            duration_ms=2000,
            duration_api_ms=1000,
            is_error=False,
            num_turns=3,
            session_id="test",
            total_cost_usd=0.05,
            result="completed successfully",
        )

        events = [mock_result]

        async def mock_query(*, prompt, options):
            for e in events:
                yield e

        with patch("shannon_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider.call(
                prompt="do work",
                cwd="/tmp",
                model_tier="medium",
            )

        assert result.success is True
        assert result.error is None
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/agents/test_providers.py::TestSpendingCapDetection -v`
Expected: FAIL — `AttributeError: 'AnthropicProvider' object has no attribute '_detect_spending_cap_behavior'`

- [x] **Step 3: Write minimal implementation**

Add `_detect_spending_cap_behavior` method and update `call()` in `providers_anthropic.py`:

Add the method after `_is_adaptive_thinking_enabled`:

```python
    def _detect_spending_cap_behavior(self, result: ClaudeRunResult, turn_count: int) -> bool:
        """Layer 2: behavioral heuristic — low turns + zero cost = suspected cap."""
        if turn_count <= 1 and result.cost == 0.0 and not result.success:
            return True
        return False
```

Update `call()` to add layers 1 and 2:

```python
    async def call(
        self,
        prompt: str,
        cwd: str,
        model_tier: str = "medium",
        output_format: dict | None = None,
        deliverables_subdir: str | None = None,
    ) -> ClaudeRunResult:
        """调用 Claude Agent SDK 执行 prompt"""
        start_time = time.time()
        model = self._get_model(model_tier)

        try:
            # 构建 SDK 配置
            options = self._build_options(cwd, model, output_format)

            # 执行调用 (dispatcher inside _execute_query)
            result_message = await self._execute_query(prompt, options)

            # 计算耗时
            duration = int((time.time() - start_time) * 1000)

            # 提取结果（使用 dispatcher 的 turn_count）
            turn_count = getattr(result_message, "turn_count", 1)
            result = self._extract_result(result_message, duration, model, turn_count)

            # Layer 1: message-level spending cap detection
            dispatcher_cap_detected = getattr(result_message, "_dispatcher_spending_cap", False)
            if dispatcher_cap_detected:
                result.success = False
                result.retryable = True
                result.error = result.error or "spending cap (message-level detection)"
                return result

            # Layer 2: behavioral spending cap detection
            if self._detect_spending_cap_behavior(result, turn_count):
                result.success = False
                result.retryable = True
                result.error = result.error or "spending cap (behavioral detection)"
                return result

            return result

        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            return self._handle_error(e, duration, model)  # Layer 3 inside _handle_error
```

Now update `_execute_query` to store dispatcher's spending cap state on the result message:

```python
    async def _execute_query(
        self,
        prompt: str,
        options: ClaudeAgentOptions,
        dispatcher: MessageDispatcher | None = None,
    ) -> ResultMessage:
        """执行 query 调用并返回最终结果"""
        from .message_dispatcher import MessageDispatcher

        dispatcher = dispatcher or MessageDispatcher()
        final_result: ResultMessage | None = None

        async for event in query(prompt=prompt, options=options):
            action = await dispatcher.dispatch(event)
            if isinstance(event, ResultMessage):
                final_result = event
            if action == "complete":
                break

        if final_result is None:
            final_result = ResultMessage()

        final_result.collected_text = dispatcher.collected_text
        final_result.turn_count = dispatcher.turn_count
        final_result._dispatcher_spending_cap = dispatcher.spending_cap_detected
        return final_result
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/agents/test_providers.py -v`
Expected: PASS — all tests

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/providers_anthropic.py packages/core/tests/agents/test_providers.py
git commit -m "feat: add 3-layer spending cap detection (message, behavioral, exception)"
```

---

### Task 8: Run full test suite and verify no regressions

**Files:**
- None (verification only)

- [x] **Step 1: Run the complete test suite**

Run: `uv run pytest packages/core/tests/ -v`
Expected: All tests PASS, no failures, no errors.

- [x] **Step 2: Check for any import issues or circular dependencies**

Run: `uv run python -c "from shannon_core.agents.providers_anthropic import AnthropicProvider; from shannon_core.agents.message_dispatcher import MessageDispatcher; from shannon_core.agents.tool_audit_logger import ToolAuditLogger, NullToolAuditLogger, ActivityToolAuditLogger; print('All imports OK')"`
Expected: `All imports OK`

- [x] **Step 3: Commit (only if any fixups were needed)**

```bash
git add -A
git commit -m "fix: address test suite regressions from SDK parity alignment"
```
