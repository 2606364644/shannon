# Provider 无关逐轮日志 + workspace 落盘修正 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让所有 provider 实时上报逐轮 assistant 文本/工具调用到已实现的 display 管道，并修正 workspace 落盘到 shannon-py 项目根。

**Architecture:** (1) 修 `MessageDispatcher.dispatch` 从 `.type` 字符串分派改为 `isinstance(AssistantMessage/UserMessage/ResultMessage)` + 遍历 `content` blocks；把 `ToolAuditLogger`（现有接口，已含 turn+tool 方法）从 `AnthropicProvider.call` 提升到 `BaseProvider.call`，`OpenAIProvider.call` 接入单 turn。(2) `resolve_workspaces_dir` 去掉 `repo_path.parent` 优先级，统一 `find_project_root()/workspaces`。

**Tech Stack:** Python 3.13, claude_agent_sdk, temporalio, pytest, rich, click。

**Spec:** `docs/superpowers/specs/2026-06-17-provider-agnostic-turn-logging-design.md`

---

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `packages/core/src/shannon_core/agents/message_dispatcher.py` | SDK 事件→逐轮事件翻译 | 改 `dispatch`/`_handle_assistant` 用 isinstance + 遍历 blocks |
| `packages/core/src/shannon_core/agents/providers.py` | Provider 抽象基类 + 工厂 | `BaseProvider.call` 加 `audit_logger` 参数 |
| `packages/core/src/shannon_core/agents/providers_openai.py` | OpenAI 兼容 provider | `call` 加 `audit_logger` + 完成后上报单 turn |
| `packages/core/src/shannon_core/utils/paths.py` | 路径解析 | `resolve_workspaces_dir` 去掉 repo 父目录优先级 |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | CLI 子命令 | 5 处 `Path("workspaces")` → `resolve_workspaces_dir()` |
| `packages/core/tests/agents/test_message_dispatcher.py` | dispatcher 单测 | helper 改用真实 SDK 事件类 |
| `packages/core/tests/agents/test_providers.py` | provider 单测 | ~5 个用 mock `.type` 事件的测试改真实事件 |
| `packages/core/tests/utils/test_paths.py` | 路径解析单测 | 新增/补 workspace 根解析测试 |

> **关键约束**：`AnthropicProvider.call` 的参数名保持 `audit_logger`（不改名），否则破坏 `test_providers.py` 既有 `audit_logger=` 用法。`BaseProvider.call` 用同名 `audit_logger` 统一。

---

## Task 1: 修 MessageDispatcher.dispatch 用 isinstance（TDD，原子红绿）

**根因**：`dispatch` 用 `getattr(event,"type")=="assistant"` 字符串分派，但 `claude_agent_sdk` 的 `AssistantMessage`/`UserMessage`/`SystemMessage` 都没有 `type` 字段（靠类区分）。所有逐轮事件被 `else: continue` 吞掉。同时现有测试用 `MagicMock` 手动塞 `event.type="assistant"`，与真实事件脱节——所以测试绿、生产坏。本 task 把测试对齐到真实 SDK 事件 + 修 dispatch。

**Files:**
- Modify: `packages/core/tests/agents/test_message_dispatcher.py`
- Modify: `packages/core/src/shannon_core/agents/message_dispatcher.py`

- [ ] **Step 1: 改 test_message_dispatcher.py 的 helper 用真实 SDK 事件（签名兼容，测试体不动）**

替换文件顶部 import 和 4 个 `_make_*` helper + `_AssistantEvent`（`test_message_dispatcher.py:1-55` 和 `295-300`）：

```python
"""Tests for message_dispatcher module."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from claude_agent_sdk import (
    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock,
    UserMessage, ToolResultBlock,
)

from shannon_core.agents.message_dispatcher import (
    SPENDING_CAP_PATTERNS,
    MessageDispatcher,
)
from shannon_core.agents.tool_audit_logger import NullToolAuditLogger


def _make_text_event(text: str) -> object:
    """A text-bearing assistant turn (real SDK has no standalone 'text' event;
    text lives in AssistantMessage.content TextBlocks)."""
    return AssistantMessage(content=[TextBlock(text=text)], model="test-model")


def _make_assistant_event(
    texts: list[str] | None = None,
    error: str | None = None,
    tool_uses: list[tuple[str, dict]] | None = None,
) -> object:
    """A real AssistantMessage with TextBlocks and/or ToolUseBlocks."""
    blocks = []
    for t in (texts or []):
        blocks.append(TextBlock(text=t))
    for name, inp in (tool_uses or []):
        blocks.append(ToolUseBlock(id=f"call_{name}", name=name, input=inp))
    return AssistantMessage(content=blocks, model="test-model", error=error)


def _make_tool_use_event(name: str = "bash", input_params: dict | None = None) -> object:
    """A tool-use, modeled as an AssistantMessage whose only block is a ToolUseBlock."""
    return AssistantMessage(
        content=[ToolUseBlock(id=f"call_{name}", name=name, input=input_params or {"command": "ls"})],
        model="test-model",
    )


def _make_tool_result_event(content: str = "file.txt") -> object:
    """A tool result, modeled as a UserMessage whose content is a ToolResultBlock."""
    return UserMessage(content=[ToolResultBlock(tool_use_id="call_1", content=content)])
```

并删除旧的 `class _AssistantEvent`（约 `test_message_dispatcher.py:295-300`），把 `TestAssistantTurnLogging.test_assistant_event_logs_turn`（`:306-311`）里的 `_AssistantEvent("Analyzing sinks")` 改为 `_make_assistant_event(texts=["Analyzing sinks"])`：

```python
class TestAssistantTurnLogging:
    """LLM assistant turns are surfaced to the audit logger."""

    @pytest.mark.asyncio
    async def test_assistant_event_logs_turn(self):
        rec = _RecordingAuditLogger()
        d = MessageDispatcher(audit_logger=rec)
        await d.dispatch(_make_assistant_event(texts=["Analyzing sinks"]))
        assert rec.turns == [(1, "Analyzing sinks")]
```

- [ ] **Step 2: 运行 test_message_dispatcher 验证失败（dispatch 还没改）**

Run: `uv run pytest packages/core/tests/agents/test_message_dispatcher.py -x -q`
Expected: **FAIL** —— 例如 `test_assistant_event_logs_turn`、`test_tool_use_calls_audit_logger` 等断言 logger 被调的测试失败（真实 `AssistantMessage` 无 `.type`，被 dispatch 的 `else` 吞掉）。`TestResultMessage*`、`TestSpendingCapPatterns`、`TestMessageDispatcherDefaults` 仍过。

- [ ] **Step 3: 修 MessageDispatcher.dispatch（isinstance + 遍历 content blocks）**

替换 `packages/core/src/shannon_core/agents/message_dispatcher.py:13` 的 import 与 `dispatch`(49-67) + `_handle_assistant`(69-84) + `_handle_tool_use`(86-92) + `_handle_tool_result`(94-97)。

import 段（`:13`）改为：
```python
from claude_agent_sdk import (
    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock,
    UserMessage, ToolResultBlock,
)
```

`dispatch` + 三个 handler 改为：
```python
    async def dispatch(self, event: Any) -> str:
        """Dispatch a single SDK event. Returns 'continue' or 'complete'.

        claude_agent_sdk messages are discriminated by class (isinstance), NOT by
        a `.type` string field — AssistantMessage/UserMessage/SystemMessage have no
        `type` attribute. Tool use/result are content blocks inside messages, not
        top-level events.
        """
        if isinstance(event, ResultMessage):
            await self._handle_result_message(event)
            return "complete"

        if isinstance(event, AssistantMessage):
            return await self._handle_assistant(event)

        if isinstance(event, UserMessage):
            for block in getattr(event, "content", None) or []:
                if isinstance(block, ToolResultBlock):
                    await self.audit_logger.log_tool_end(getattr(block, "content", ""))
            return "continue"

        # SystemMessage / HookEventMessage / StreamEvent / unknown: ignored
        return "continue"

    async def _handle_assistant(self, event: AssistantMessage) -> str:
        self.turn_count += 1
        turn_text = ""
        for block in getattr(event, "content", None) or []:
            if isinstance(block, TextBlock):
                text = block.text
                self.text_parts.append(text)
                turn_text += text
                if self._is_spending_cap_in_text(text):
                    self.spending_cap_detected = True
            elif isinstance(block, ToolUseBlock):
                await self.audit_logger.log_tool_start(block.name, block.input)
                if self._progress:
                    self._progress(f"tool: {block.name}")
        if turn_text:
            await self.audit_logger.log_assistant_turn(self.turn_count, turn_text)
        error = getattr(event, "error", None)
        if error and self._on_error:
            self._on_error(str(error))
        return "continue"
```

删除旧的 `_handle_tool_use` 和 `_handle_tool_result` 方法（tool 分派已并入上面）。

- [ ] **Step 4: 运行 test_message_dispatcher 验证通过**

Run: `uv run pytest packages/core/tests/agents/test_message_dispatcher.py -q`
Expected: **PASS**（全部，包括 spending cap、turn count、tool、error callback、result metadata）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/message_dispatcher.py packages/core/tests/agents/test_message_dispatcher.py
git commit -m "fix(core): MessageDispatcher.dispatch 用 isinstance 匹配 claude_agent_sdk 事件模型

原 dispatch 用 event.type 字符串分派,但 SDK 的 AssistantMessage/UserMessage/
SystemMessage 无 type 字段(靠类区分),导致逐轮 assistant/tool 事件全被 else 吞掉
(workflow.log [LLM]/[TOOL] 各 0 条、live 屏 agent 阶段空白)。改用 isinstance +
遍历 content blocks:TextBlock→log_assistant_turn,ToolUseBlock→log_tool_start,
UserMessage 的 ToolResultBlock→log_tool_end。同步把单测 helper 从 MagicMock+.type
改成真实 SDK 事件类(此前测试与生产事件模型脱节是 bug 未被抓的共因)。"
```

---

## Task 2: 修 test_providers.py 里因 dispatch 改动而失败的 mock 事件测试

修 dispatch 后，`test_providers.py` 里用内联 `MagicMock` + `event.type="assistant"/"tool_use"/"text"` 的测试会失败（mock 不是真实 SDK 类，被 dispatch 忽略）。把它们改成真实 SDK 事件。

**Files:**
- Modify: `packages/core/tests/agents/test_providers.py`

- [ ] **Step 1: 改 `TestExecuteQueryWithDispatcher.test_dispatcher_collects_text_from_events`（约 :670-709）**

把内联 mock 事件换成真实 `AssistantMessage`。断言不变（`collected_text == "partial response"`、`turn_count == 1`）：

```python
    @pytest.mark.asyncio
    async def test_dispatcher_collects_text_from_events(self):
        """_execute_query collects text via dispatcher from a real AssistantMessage."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        from claude_agent_sdk import AssistantMessage, TextBlock
        assistant_event = AssistantMessage(
            content=[TextBlock(text="partial "), TextBlock(text="response")],
            model="test-model",
        )

        mock_result = ResultMessage(
            subtype="result", duration_ms=1000, duration_api_ms=500,
            is_error=False, num_turns=1, session_id="test",
        )

        async def mock_query(*, prompt, options):
            yield assistant_event
            yield mock_result

        with patch("shannon_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider._execute_query(
                prompt="test",
                options=ClaudeAgentOptions(model="claude-sonnet-4-6", cwd="/tmp"),
            )

        assert result.collected_text == "partial response"
        assert result.turn_count == 1
```

- [ ] **Step 2: 改 `test_dispatcher_with_custom_logger`（约 :711-747）**

把 `tool_use_event`（MagicMock type="tool_use"）换成真实 `AssistantMessage(content=[ToolUseBlock])`：

```python
        from claude_agent_sdk import AssistantMessage, ToolUseBlock
        mock_audit = AsyncMock()
        dispatcher = MessageDispatcher(audit_logger=mock_audit)

        tool_use_event = AssistantMessage(
            content=[ToolUseBlock(id="call_bash", name="bash", input={"command": "ls"})],
            model="test-model",
        )
        # ... mock_result 不变;events = [tool_use_event, mock_result]
```
断言不变：`mock_audit.log_tool_start.assert_awaited_once_with("bash", {"command": "ls"})`。

- [ ] **Step 3: 改 `TestCallWithTurnCount.test_call_returns_correct_turn_count`（约 :753-794）**

把 3 个 MagicMock assistant 事件换成真实 `AssistantMessage`：

```python
        from claude_agent_sdk import AssistantMessage, TextBlock
        events = []
        for i in range(3):
            events.append(AssistantMessage(
                content=[TextBlock(text=f"turn {i + 1}")], model="test-model",
            ))
        # ... mock_result (num_turns=3) append 不变
```
断言不变：`result.turns == 3`。

- [ ] **Step 4: 改 `TestSpendingCapDetection.test_layer1_message_level_detection`（约 :848-887）**

把 `assistant_event`（MagicMock）换成真实 `AssistantMessage(content=[TextBlock("your spending limit has been reached")])`：

```python
        from claude_agent_sdk import AssistantMessage, TextBlock
        assistant_event = AssistantMessage(
            content=[TextBlock(text="your spending limit has been reached")],
            model="test-model",
        )
```
断言不变（`success is False`、`"message-level" in result.error`）。

同理改 `test_no_false_positive_on_success`（约 :943-981）的 `assistant_event` 为真实 `AssistantMessage(content=[TextBlock("completed successfully")])`（断言不变）。

- [ ] **Step 5: 改 `TestProviderAuditLoggerInjection`（约 :1601-1644）**

把 `tool_use`/`tool_result` MagicMock 换成真实事件：

```python
    @pytest.mark.asyncio
    async def test_execute_query_uses_audit_logger_param(self):
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))
        mock_audit = AsyncMock()
        from claude_agent_sdk import AssistantMessage, ToolUseBlock, UserMessage, ToolResultBlock
        tool_use = AssistantMessage(
            content=[ToolUseBlock(id="call_bash", name="bash", input={"command": "ls"})],
            model="test-model",
        )
        tool_result = UserMessage(content=[ToolResultBlock(tool_use_id="call_bash", content="ok")])
        msg = ResultMessage(subtype="result", duration_ms=10, duration_api_ms=5,
                            is_error=False, num_turns=1, session_id="t")
        async def mock_query(*, prompt, options):
            yield tool_use; yield tool_result; yield msg
        with patch("shannon_core.agents.providers_anthropic.query", side_effect=mock_query):
            await provider._execute_query(
                prompt="t", options=ClaudeAgentOptions(model="m", cwd="/tmp"),
                audit_logger=mock_audit,
            )
        mock_audit.log_tool_start.assert_awaited_once_with("bash", {"command": "ls"})
        mock_audit.log_tool_end.assert_awaited_once_with("ok")
```

`test_call_forwards_audit_logger`（约 :1622）同理：把 `tool_use` MagicMock 换成真实 `AssistantMessage(content=[ToolUseBlock(id="call_edit", name="edit", input={"path": "a"})])`，断言 `log_tool_start("edit", {"path": "a"})` 不变。

- [ ] **Step 6: 运行 test_providers 验证通过**

Run: `uv run pytest packages/core/tests/agents/test_providers.py -q`
Expected: **PASS**（全部，含 `test_call_success`——它不断言 `collected_text`，text 来自 `result.result`，仍过）。

- [ ] **Step 7: Commit**

```bash
git add packages/core/tests/agents/test_providers.py
git commit -m "test(core): providers 单测改用真实 claude_agent_sdk 事件

dispatch 改 isinstance 后,内联 MagicMock(type='assistant'/'tool_use') 事件不再
被识别。把这些测试的事件构造改为真实 AssistantMessage/UserMessage,使测试反映
真实事件模型。"
```

---

## Task 3: BaseProvider.call 统一 audit_logger + OpenAIProvider 接入单 turn

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers.py`
- Modify: `packages/core/src/shannon_core/agents/providers_openai.py`
- Modify: `packages/core/tests/agents/test_providers.py`

- [ ] **Step 1: 写失败测试（OpenAI 上报单 turn）**

在 `test_providers.py` 的 `TestOpenAIProvider` 类（约 :434）内追加：

```python
    @pytest.mark.asyncio
    async def test_openai_call_logs_single_turn(self):
        """OpenAIProvider.call surfaces the assistant response as a single turn."""
        from unittest.mock import AsyncMock, MagicMock
        config = ProviderConfig(type="openai_compatible", api_key="k")
        provider = OpenAIProvider(config)

        mock_choice = MagicMock()
        mock_choice.message.content = "hello world"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock(prompt_tokens=5, completion_tokens=2)

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client

        rec = AsyncMock()
        await provider.call(prompt="hi", cwd="/tmp", model_tier="medium", audit_logger=rec)
        rec.log_assistant_turn.assert_awaited_once_with(1, "hello world")
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest packages/core/tests/agents/test_providers.py::TestOpenAIProvider::test_openai_call_logs_single_turn -x -q`
Expected: **FAIL** —— `TypeError: call() got an unexpected keyword argument 'audit_logger'`（OpenAIProvider.call 现在不接 audit_logger）。

- [ ] **Step 3: BaseProvider.call 加 audit_logger 参数**

`providers.py` 顶部 import 段（约 :13-15）后追加（运行时 import，避免循环）：
```python
from shannon_core.agents.tool_audit_logger import ToolAuditLogger
```

`BaseProvider.call` 抽象签名（`providers.py:63-71`）改为：
```python
    @abstractmethod
    async def call(
        self,
        prompt: str,
        cwd: str,
        model_tier: str = "medium",
        output_format: dict | None = None,
        deliverables_subdir: str | None = None,
        audit_logger: ToolAuditLogger | None = None,
    ) -> ClaudeRunResult:
```
（docstring 不变，仅在末尾加 `audit_logger` 参数说明即可。）

- [ ] **Step 4: OpenAIProvider.call 加 audit_logger + 上报单 turn**

`providers_openai.py` 顶部 import（约 :13-15）追加：
```python
from shannon_core.agents.tool_audit_logger import ToolAuditLogger
```

`OpenAIProvider.call` 签名（`:86-93`）改为：
```python
    async def call(
        self,
        prompt: str,
        cwd: str,
        model_tier: str = "medium",
        output_format: dict | None = None,
        deliverables_subdir: str | None = None,
        audit_logger: ToolAuditLogger | None = None,
    ) -> ClaudeRunResult:
```

try 块的 return（约 :124-130）改为先取 result、上报、再 return：
```python
            # 执行调用
            response = await client.chat.completions.create(**request_params)

            # 计算耗时
            duration = int((time.time() - start_time) * 1000)

            # 提取结果
            result = self._extract_result(response, duration, model, output_format is not None)

            # provider 无关的逐轮上报:OpenAI 单次 completion 作为单 turn
            if audit_logger is not None and result.text:
                await audit_logger.log_assistant_turn(1, result.text)

            return result
```

- [ ] **Step 5: 运行验证通过**

Run: `uv run pytest packages/core/tests/agents/test_providers.py -q`
Expected: **PASS**（新测试 + 既有全绿；`AnthropicProvider` 参数名未动，`TestProviderAuditLoggerInjection`/`TestRunClaudePromptAuditLogger` 不受影响）。

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/agents/providers.py packages/core/src/shannon_core/agents/providers_openai.py packages/core/tests/agents/test_providers.py
git commit -m "feat(core): BaseProvider.call 统一 audit_logger,OpenAI 接入单 turn 上报

把 ToolAuditLogger 参数从 AnthropicProvider 提升到 BaseProvider.call(参数名保持
audit_logger 以兼容既有调用/测试),OpenAIProvider.call 接受并在完成后上报单 turn。
消除 runner 对 openai provider 传 audit_logger 会 TypeError 的 latent bug。"
```

---

## Task 4: workspace 根统一到 shannon-py 项目根

**Files:**
- Modify: `packages/core/src/shannon_core/utils/paths.py`
- Create or Modify: `packages/core/tests/utils/test_paths.py`（若 `tests/utils/` 不存在则创建目录 + `__init__.py`）

- [ ] **Step 1: 写失败测试**

`packages/core/tests/utils/test_paths.py`：
```python
"""Tests for utils/paths.py workspace resolution."""
import os
from unittest.mock import patch
from pathlib import Path

from shannon_core.utils.paths import resolve_workspaces_dir


class TestResolveWorkspacesDir:
    def test_ignores_repo_parent(self, tmp_path):
        """repo_path 不再决定 workspace 根(不再落 repo 父目录)。"""
        repo = tmp_path / "NodeGoat"
        with patch.dict(os.environ, {}, clear=True):
            ws = resolve_workspaces_dir(repo_path=str(repo))
        # 不应是 <repo.parent>/workspaces
        assert ws != repo.parent / "workspaces"
        # 应是 find_project_root()/workspaces
        assert ws.name == "workspaces"

    def test_worker_root_env_overrides(self, tmp_path):
        with patch.dict(os.environ, {"SHANNON_WORKER_ROOT": str(tmp_path)}, clear=True):
            ws = resolve_workspaces_dir(repo_path="/some/repo")
        assert ws == tmp_path / "workspaces"

    def test_no_repo_no_env_uses_project_root(self):
        with patch.dict(os.environ, {}, clear=True):
            ws = resolve_workspaces_dir()
        assert ws.name == "workspaces"
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest packages/core/tests/utils/test_paths.py -x -q`
Expected: **FAIL** —— `test_ignores_repo_parent` 失败（当前 `resolve_workspaces_dir(repo_path=...)` 返回 `repo.parent/workspaces`）。

- [ ] **Step 3: 改 resolve_workspaces_dir**

替换 `paths.py:25-38`：
```python
def resolve_workspaces_dir(repo_path: str | None = None) -> Path:
    """解析 workspaces 根目录。

    优先级:
    1. SHANNON_WORKER_ROOT 环境变量 → worker_root / "workspaces"
    2. find_project_root() / "workspaces"  (shannon-py 项目根)

    注意: repo_path 不再用于定位 workspace 根(曾导致 workspace 落到 repo 父目录)。
    参数保留仅为调用方签名兼容;deliverables 仍落在 repo_path/.shannon/deliverables。
    """
    worker_root = os.getenv("SHANNON_WORKER_ROOT")
    if worker_root:
        return Path(worker_root) / "workspaces"
    return find_project_root() / "workspaces"
```

- [ ] **Step 4: 运行验证通过**

Run: `uv run pytest packages/core/tests/utils/test_paths.py -q`
Expected: **PASS**。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/utils/paths.py packages/core/tests/utils/test_paths.py
git commit -m "fix(core): workspace 根统一到 shannon-py 项目根

resolve_workspaces_dir 去掉 repo_path.parent/workspaces 优先级(它导致 workspace
落到 repo 父目录如 vuln-range/workspaces 而非 shannon-py 内),改为 SHANNON_WORKER_ROOT
> find_project_root()/workspaces。repo_path 参数保留签名兼容。"
```

---

## Task 5: CLI 子命令改用 resolve_workspaces_dir

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py`

- [ ] **Step 1: 改 5 处 `Path("workspaces")` / `SessionManager(Path("workspaces"))`**

`main.py` 顶部 import（约 :1-30 区，确认已有 paths import；若无则加）追加：
```python
from shannon_core.utils.paths import resolve_workspaces_dir
```

5 处替换（行号约 :140, :161, :211, :292, :336）：
- `workspaces_dir = Path("workspaces")` → `workspaces_dir = resolve_workspaces_dir()`
- `mgr = SessionManager(Path("workspaces"))` → `mgr = SessionManager(resolve_workspaces_dir())`（4 处）

> 注：`logs` 命令（:138-153）既读 `workspaces_dir` 又读 `workspace_name`，改 `workspaces_dir = resolve_workspaces_dir()` 即可，`ws = workspaces_dir / workspace_name` 保持。

- [ ] **Step 2: 运行 whitebox 相关单测回归**

Run: `uv run pytest packages/whitebox/tests -q -k "cli or workspace or logs" --ignore=packages/whitebox/tests/test_workflows.py`
Expected: PASS（若有 CLI 测试 mock 了路径，按需调整 mock 目标为 `resolve_workspaces_dir`）。

- [ ] **Step 3: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py
git commit -m "fix(whitebox): CLI 子命令用 resolve_workspaces_dir 与 start 一致

logs/workspace/show/delete/clean 原用相对 Path('workspaces')(按 cwd),与 start
(用 resolve_workspaces_dir)根不一致,可能读不到。统一为 resolve_workspaces_dir()。"
```

---

## Task 6: 全回归 + 手动冒烟

- [ ] **Step 1: core agents + display + utils 单测全绿**

Run（避开 Temporal/网络慢测，见 memory `pytest-whitebox-hang`）:
```bash
uv run pytest packages/core/tests/agents packages/core/tests/display packages/core/tests/utils -q
```
Expected: PASS（含 dispatcher、providers、paths、所有 display 回归未动且绿）。

- [ ] **Step 2: 手动冒烟（真仓库）**

Run:
```bash
uv run shannon-whitebox start -r /Users/mango/project/vuln-range/NodeGoat
```
预期（对比修复前的"15 分钟空白"）：
- workspace 落在 `shannon-py/workspaces/whitebox-<id>`（**不再**是 `vuln-range/workspaces`）；
- pre-recon 阶段**逐轮**滚 `💭 Turn N: …`（实时、每轮一行）；
- `shannon-whitebox logs <id> --follow` 可见连续 `[LLM]`/`[TOOL]`；
- 底部钉住第二行显示 `⠋ <步骤意图> · Turn N: <最新轮>`；
- setup/code-index 的 `STEP` 行仍实时（无回归）。

跑到 pre-recon 出 2-3 个 turn 即可 Ctrl-C（省钱）。

- [ ] **Step 3: 回填 memory**

把结论写回 memory `whitebox-display-clarity-redesign`（手动冒烟已验证）+ 更新/新增 `provider-agnostic-turn-logging`（dispatch 根因、修复要点）。

---

## Self-Review

**Spec coverage：**
- §4.1 BaseProvider 统一 tool_audit_logger → Task 3 ✓（参数名 audit_logger，复用 ToolAuditLogger，未新造协议——与 spec 一致）
- §4.2 修 dispatch isinstance + 遍历 blocks → Task 1 ✓
- §4.3 dispatcher 不 provider 专属（OpenAI 直接调）→ Task 3 Step 4 ✓
- §4.4 workspace 落盘修正 → Task 4 + Task 5 ✓
- §8 测试计划（dispatcher/providers/paths/CLI/display 回归）→ Task 1-6 ✓

**Placeholder scan：** 无 TBD/TODO；每个代码 step 都有完整代码。

**Type consistency：** `audit_logger` 参数名贯穿（BaseProvider/OpenAI/Anthropic/test 一致）；`log_assistant_turn(turn, content)` / `log_tool_start(name, input)` / `log_tool_end(result)` 签名与 `ToolAuditLogger` ABC（tool_audit_logger.py:21-30）一致；`TextBlock(text)` / `ToolUseBlock(id,name,input)` / `ToolResultBlock(tool_use_id,content)` 字段与 SDK types.py:921/936/945 一致。

**注意点（执行时）：**
- Task 1 Step 2 的"失败"是预期的 TDD 红；Task 2 的 mock 测试失败是 Task 1 的连带效应，两个 task 顺序执行。
- 若 `packages/core/tests/utils/` 目录不存在，Task 4 Step 1 先建目录 + `__init__.py`。
- `test_call_success`（test_providers.py:294）不断言 `collected_text`，text 来自 `result.result`，Task 2 无需改它。
