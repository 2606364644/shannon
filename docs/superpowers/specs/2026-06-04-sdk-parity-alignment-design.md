# SDK Parity Alignment Design

**Date:** 2026-06-04
**Status:** Approved
**Scope:** Align shannon-py's `claude-agent-sdk` usage with the original TS project

## Background

shannon-py already uses `claude-agent-sdk>=0.2.87` (Python) to spawn Claude Code subprocesses — the same fundamental approach as the original TS project (`@anthropic-ai/claude-agent-sdk`). However, the Python implementation has significant gaps in env var passthrough, message stream processing, tool call auditing, and error detection.

## Approach

Phased alignment across 4 stages, each independently testable:

1. Environment variable passthrough + Provider config
2. Message stream processor (MessageDispatcher)
3. Tool call audit logger
4. 3-layer spending cap detection

---

## Stage 1: Environment Variable Passthrough

### Problem

`AnthropicProvider._build_options()` only forwards `SHANNON_API_KEY` and `SHANNON_BASE_URL`. The TS version conditionally passes through 16 environment variables, enabling OAuth tokens, custom endpoint auth, Bedrock/Vertex flags, and Playwright paths.

### Solution

Replace `_build_options()` env handling with a new `_build_sdk_env()` method.

**File:** `packages/core/src/shannon_core/agents/providers_anthropic.py`

```python
def _build_sdk_env(self) -> dict[str, str]:
    """Build SDK subprocess environment variables (aligned with TS claude-executor.ts)"""
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
```

`_build_options()` then calls `self._build_sdk_env()` and assigns to `options.env`.

### Files changed

- `agents/providers_anthropic.py` — replace `_build_options()` env section with `_build_sdk_env()`

---

## Stage 2: Message Stream Processor

### Problem

`_execute_query()` only collects text fragments and waits for `ResultMessage`. The TS version processes 7+ event types (assistant, tool_use, tool_result, result, system, user, tool_progress) with real-time dispatch.

### Solution

New file `agents/message_dispatcher.py`.

```python
from __future__ import annotations
from typing import Any, Callable

from claude_agent_sdk import ResultMessage


class MessageDispatcher:
    """
    Processes Claude Agent SDK streaming events.
    Aligned with TS message-handlers.ts capabilities.
    """

    def __init__(
        self,
        audit_logger: ToolAuditLogger | None = None,
        progress_callback: Callable[[str], None] | None = None,
        error_callback: Callable[[str], None] | None = None,
    ):
        self.turn_count = 0
        self.text_parts: list[str] = []
        self.spending_cap_detected = False
        self.audit_logger = audit_logger or NullToolAuditLogger()
        self._progress = progress_callback
        self._on_error = error_callback

    async def dispatch(self, event: Any) -> str:
        """Dispatch a single SDK event. Returns 'continue' | 'complete'."""
        if isinstance(event, ResultMessage):
            return await self._handle_result(event)

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

    async def _handle_result(self, event: ResultMessage) -> str:
        return "complete"

    @staticmethod
    def _is_spending_cap_in_text(text: str) -> bool:
        text_lower = text.lower()
        return any(kw in text_lower for kw in SPENDING_CAP_PATTERNS)

    @property
    def collected_text(self) -> str:
        return "".join(self.text_parts)


SPENDING_CAP_PATTERNS = [
    "spending limit",
    "credit limit",
    "quota exceeded",
    "budget exceeded",
    "maximum spend",
]
```

### Integration

Modify `AnthropicProvider._execute_query()` to use the dispatcher:

```python
async def _execute_query(
    self, prompt: str, options: ClaudeAgentOptions,
    dispatcher: MessageDispatcher | None = None,
) -> ResultMessage:
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

### Files changed

- `agents/message_dispatcher.py` — new file
- `agents/providers_anthropic.py` — modify `_execute_query()` to accept and use dispatcher

---

## Stage 3: Tool Call Audit Logger

### Problem

No tool call logging exists. The TS version logs every tool_start/tool_end event through a Null Object pattern audit logger.

### Solution

New file `agents/tool_audit_logger.py`.

```python
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class ToolAuditLogger(ABC):
    """Tool call audit logger interface — Null Object pattern."""

    @abstractmethod
    async def log_tool_start(self, tool_name: str, parameters: Any) -> None: ...

    @abstractmethod
    async def log_tool_end(self, result: Any) -> None: ...

    @abstractmethod
    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None: ...


class ActivityToolAuditLogger(ToolAuditLogger):
    """Bridges to ActivityLogger for tool audit logging."""

    def __init__(self, logger: ActivityLogger):
        self._logger = logger

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        self._logger.info("tool_start", tool_name=tool_name, parameters=str(parameters)[:500])

    async def log_tool_end(self, result: Any) -> None:
        self._logger.info("tool_end", result=str(result)[:500])

    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None:
        self._logger.error("agent_error", error=error, turn_count=turn_count, duration_ms=duration_ms)


class NullToolAuditLogger(ToolAuditLogger):
    """Null Object — no-op."""

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        pass

    async def log_tool_end(self, result: Any) -> None:
        pass

    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None:
        pass
```

### Integration

`MessageDispatcher` constructor accepts `ToolAuditLogger` (default `NullToolAuditLogger`). Already described in Stage 2 design — the dispatcher calls `audit_logger.log_tool_start()` and `log_tool_end()` in the respective event handlers.

### Files changed

- `agents/tool_audit_logger.py` — new file
- `agents/message_dispatcher.py` — import and use `ToolAuditLogger` (already in Stage 2)

---

## Stage 4: 3-Layer Spending Cap Detection

### Problem

Current implementation has only 1 layer of spending cap detection (keyword matching in `_handle_error`). The TS version has 3 layers: message-level, behavioral, and final.

### Solution

| Layer | Location | Detection |
|-------|----------|-----------|
| **Layer 1: Message** | `MessageDispatcher._handle_assistant()` | Keyword match in assistant text |
| **Layer 2: Behavioral** | `AnthropicProvider.call()` | Low turns (<=1) + zero cost + not successful |
| **Layer 3: Final** | `AnthropicProvider._handle_error()` | Existing keyword match (unchanged) |

Layer 1 is already built into `MessageDispatcher` (Stage 2). Layer 3 already exists.

Layer 2 is new:

```python
def _detect_spending_cap_behavior(self, result: ClaudeRunResult, turn_count: int) -> bool:
    """Layer 2: behavioral heuristic — low turns + zero cost = suspected cap."""
    if turn_count <= 1 and result.cost == 0.0 and not result.success:
        return True
    return False
```

Integration in `AnthropicProvider.call()`:

```python
async def call(self, prompt, cwd, model_tier="medium", ...) -> ClaudeRunResult:
    start_time = time.time()
    model = self._get_model(model_tier)
    try:
        options = self._build_options(cwd, model, output_format)
        dispatcher = MessageDispatcher(audit_logger=...)
        result_message = await self._execute_query(prompt, options, dispatcher)
        duration = int((time.time() - start_time) * 1000)
        result = self._extract_result(result_message, duration, model, dispatcher.turn_count)

        # Layer 1: message-level
        if dispatcher.spending_cap_detected:
            result.success = False
            result.retryable = True
            result.error = result.error or "spending cap (message-level detection)"
            return result

        # Layer 2: behavioral
        if self._detect_spending_cap_behavior(result, dispatcher.turn_count):
            result.success = False
            result.retryable = True
            result.error = result.error or "spending cap (behavioral detection)"
            return result

        return result
    except Exception as e:
        duration = int((time.time() - start_time) * 1000)
        return self._handle_error(e, duration, model)  # Layer 3 inside _handle_error
```

### Files changed

- `agents/providers_anthropic.py` — add `_detect_spending_cap_behavior()`, integrate into `call()`

---

## Files Changed Summary

| File | Action | Stage |
|------|--------|-------|
| `agents/providers_anthropic.py` | Modify `_build_options()`, add `_build_sdk_env()`, modify `_execute_query()`, add `_detect_spending_cap_behavior()`, modify `call()` | 1, 2, 4 |
| `agents/message_dispatcher.py` | New file | 2 |
| `agents/tool_audit_logger.py` | New file | 3 |

## Testing Strategy

Each stage has independent tests:

- **Stage 1:** Unit tests verifying env var forwarding for each provider type, OAuth token passthrough, conditional override behavior
- **Stage 2:** Unit tests for each event type dispatch, turn counting, text collection
- **Stage 3:** Unit tests verifying audit logger receives tool_start/tool_end calls, NullToolAuditLogger produces no side effects
- **Stage 4:** Unit tests for each detection layer, integration test verifying all 3 layers cooperate

## Out of Scope

- Output formatting (agent prefix system, tool call filtering) — lower priority
- Progress management (context-aware progress indicators) — lower priority
- Structured output validation (Zod-equivalent schema validation) — SDK handles this
