# openai 引擎 structured output 解析韧性修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 glm-openai 引擎下 `*-vuln` agent 因 GLM 最终输出非合法 JSON 触发 `Expecting value: line 1 column 1 (char 0)` 致整个 agent 判失败 + 8 次徒劳重试的 bug。

**Architecture:** 三层防线 —— L0 `validate_json` 容错解析（剥 fence + 子串提取）/ L1 provider 层轻量重输（模拟 Claude SDK 单次内部重试，1 turn）/ L2 Temporal 兜底（`OUTPUT_VALIDATION_FAILED` + executor error_code 透传）。核心语义：解析失败不该否定 agent 已完成的工作。

**Tech Stack:** Python 3.12、openai-agents SDK、openai AsyncOpenAI client、pytest、Temporal。

## Global Constraints

- **ErrorCode 是 `str` Enum**（`errors.py:4`）—— enum 值与字符串互通，但 executor 用 `isinstance(result.error_code, ErrorCode)` 守卫透传（非 enum 的字符串不透传，保持 `AGENT_EXECUTION_FAILED` 现有行为，避免破坏 RateLimit/Timeout 分类）。
- **`StructuredOutputParseError` 不继承 `ModelBehaviorError`** —— 避免被 openai-agents SDK 的 error handler 路径误吞，确保由 `providers_openai` 的 L1/L2 显式处理。
- **不改 retry 上限**（vuln 8，`retry.py:58`，有意分歧于 TS 的 3）；**不改 vuln prompt**（deliverable md 必须保留）；**不扩 blackbox**（独立 spec）。
- **L1 不传 `response_format`** —— GLM 第三方后端对 response_format json_schema 接受度不确定（spec §6 风险），L1 已有 `_extract_json_payload` 兜底，纯靠 prompt「只输出 JSON」更稳。
- **测试只跑改动相关文件**（CLAUDE.md §3）—— 勿广跑全套（Temporal / 网络慢测试会 hang）。
- 测试用 `asyncio.run` / monkeypatch 打桩，不真跑 LLM（仿 `test_executor_artifact_postprocess.py` 模式）。

---

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `packages/core/src/shannon_core/agents/openai_output_schema.py` | L0：`StructuredOutputParseError` + `_extract_json_payload` + 容错 `validate_json` | 修改 |
| `packages/core/src/shannon_core/agents/providers_openai.py` | L1+L2：`_classify_error`/`_handle_error` 识别 + `_ReparsedRunResult` + `_lightweight_reparse` + `call()` except | 修改 |
| `packages/core/src/shannon_core/agents/executor.py` | L2：error_code `isinstance(ErrorCode)` 守卫透传 | 修改 |
| `packages/core/tests/agents/test_openai_output_schema.py` | L0 单测 | 修改 |
| `packages/core/tests/agents/test_providers.py` | L2 分类/handle_error 单测 | 修改 |
| `packages/core/tests/agents/test_providers_openai_reparse.py` | L1 `_lightweight_reparse` 单测 | 创建 |
| `packages/core/tests/agents/test_providers_openai_call_l1.py` | L1 `call()` 集成（堵 task-probe 盲区） | 创建 |
| `packages/core/tests/test_executor_error_code_passthrough.py` | L2 executor 透传单测 | 创建 |

---

## Task 1: L0 基础 —— `_extract_json_payload` + `StructuredOutputParseError`

**Files:**
- Modify: `packages/core/src/shannon_core/agents/openai_output_schema.py`（import 后、`class RawJsonSchemaOutputSchema` 前插入）
- Test: `packages/core/tests/agents/test_openai_output_schema.py`

**Interfaces:**
- Produces: `StructuredOutputParseError(Exception)`、`_extract_json_payload(text: str) -> str | None`（供 Task 2 `validate_json` 与 Task 5 `_lightweight_reparse` 复用）

- [ ] **Step 1: 写失败测试**（追加到 `test_openai_output_schema.py` 顶部 import 块加 `_extract_json_payload, StructuredOutputParseError`，文件末尾追加）

```python
from shannon_core.agents.openai_output_schema import (
    RawJsonSchemaOutputSchema,
    StructuredOutputParseError,
    _extract_json_payload,
)


def test_extract_json_payload_plain():
    assert _extract_json_payload('{"k": "v"}') == '{"k": "v"}'


def test_extract_json_payload_markdown_fence_with_lang():
    assert _extract_json_payload('```json\n{"k": "v"}\n```') == '{"k": "v"}'


def test_extract_json_payload_markdown_fence_no_lang():
    assert _extract_json_payload('```\n{"k": "v"}\n```') == '{"k": "v"}'


def test_extract_json_payload_leading_prose():
    text = '分析完成，结论如下：\n{"vulnerabilities": []}\n以上。'
    assert _extract_json_payload(text) == '{"vulnerabilities": []}'


def test_extract_json_payload_empty_or_blank():
    assert _extract_json_payload("") is None
    assert _extract_json_payload("   ") is None


def test_extract_json_payload_no_braces():
    assert _extract_json_payload("纯叙述收尾，没有 JSON") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/agents/test_openai_output_schema.py -v`
Expected: FAIL（`ImportError: cannot import name '_extract_json_payload'`）

- [ ] **Step 3: 写实现**（`openai_output_schema.py`，在 `from agents import AgentOutputSchemaBase` 之后、`class RawJsonSchemaOutputSchema` 之前插入）

```python
class StructuredOutputParseError(Exception):
    """openai 引擎 structured output 解析失败（L0 容错后仍无法提取合法 JSON）。

    不继承 ModelBehaviorError：避免被 openai-agents SDK 的 error handler 路径
    误吞，确保由 providers_openai 的 L1/L2 显式处理。承载 OUTPUT_VALIDATION_FAILED
    语义（对齐 TS message-handlers.ts:355）。
    """


def _extract_json_payload(text: str) -> str | None:
    """从 LLM 输出文本提取 JSON 字符串（L0/L1 复用）。

    模拟 Claude SDK「把 LLM 文本变成合法 JSON」的契约（TS 侧 SDK 免费；
    openai-agents 无此层，Python 自己补）。处理 GLM 常见收尾形态：
      1. markdown fence 包裹（```json ... ``` / ``` ... ```）；
      2. 前导叙述 + JSON（取首个 { 到末个 } 的子串）。
    全无 { / } → 返回 None（调用方据此抛 StructuredOutputParseError）。
    """
    if not text:
        return None
    s = text.strip()
    if not s:
        return None
    if s.startswith("```"):
        lines = s.splitlines()
        if lines:
            lines = lines[1:]            # 去首行 ```（含可能的语言标签）
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]        # 去末行 ```
            s = "\n".join(lines).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return s[start : end + 1]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/agents/test_openai_output_schema.py -v`
Expected: PASS（新增 6 个测试 + 现有测试全绿）

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/agents/openai_output_schema.py packages/core/tests/agents/test_openai_output_schema.py
git commit -m "feat(openai): L0 structured output 容错解析基础 (StructuredOutputParseError + _extract_json_payload)"
```

---

## Task 2: L0 `validate_json` 容错解析

**Files:**
- Modify: `packages/core/src/shannon_core/agents/openai_output_schema.py:42-44`（`validate_json` 方法体）
- Test: `packages/core/tests/agents/test_openai_output_schema.py`

**Interfaces:**
- Consumes: Task 1 的 `_extract_json_payload`、`StructuredOutputParseError`
- Produces: `validate_json` 现在能解析 fence/前导叙述；纯非 JSON 抛 `StructuredOutputParseError`（供 Task 6 `call()` 捕获）

- [ ] **Step 1: 写失败测试**（追加到 `test_openai_output_schema.py`）

```python
def test_validate_json_parses_markdown_fence():
    s = RawJsonSchemaOutputSchema({"type": "object"})
    assert s.validate_json('```json\n{"k": "v"}\n```') == {"k": "v"}


def test_validate_json_parses_leading_prose():
    s = RawJsonSchemaOutputSchema({"type": "object"})
    assert s.validate_json('结论如下：{"k": "v"}') == {"k": "v"}


def test_validate_json_raises_structured_output_parse_error_on_prose():
    s = RawJsonSchemaOutputSchema({"type": "object"})
    with pytest.raises(StructuredOutputParseError):
        s.validate_json("纯叙述收尾，没有 JSON")
```

（顶部已需 `import pytest`，现有文件已 import。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/agents/test_openai_output_schema.py::test_validate_json_parses_markdown_fence -v`
Expected: FAIL（当前 `validate_json` 直接 `json.loads('```json...')` 抛 `JSONDecodeError`，非 `StructuredOutputParseError`；fence 测试因 JSONDecodeError 失败）

- [ ] **Step 3: 写实现**（替换 `openai_output_schema.py:42-44` 的 `validate_json` 方法体）

```python
    def validate_json(self, json_str: str) -> Any:
        # L0 容错解析：剥 fence + 子串提取（模拟 Claude SDK 的 LLM→JSON 接管契约；
        # TS 侧 SDK 免费，openai-agents 无此层，Python 自己补）。
        candidate = _extract_json_payload(json_str)
        if candidate is None:
            raise StructuredOutputParseError(json_str)
        return json.loads(candidate)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/agents/test_openai_output_schema.py -v`
Expected: PASS（含新增 3 个 + 现有 `test_validate_json_parses_valid_json` / `test_validate_json_raises_on_invalid` 仍绿——后者 `pytest.raises(Exception)` 捕获 `StructuredOutputParseError`，兼容）

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/agents/openai_output_schema.py packages/core/tests/agents/test_openai_output_schema.py
git commit -m "feat(openai): L0 validate_json 容错解析 (剥 fence + 子串提取)"
```

---

## Task 3: L2 provider 分类 —— `_classify_error` + `_handle_error` 识别 `StructuredOutputParseError`

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers_openai.py`（顶部 import + `_classify_error:201` + `_handle_error:187`）
- Test: `packages/core/tests/agents/test_providers.py`

**Interfaces:**
- Consumes: Task 1 的 `StructuredOutputParseError`
- Produces: provider 对 `StructuredOutputParseError` 设 `result.error_code = ErrorCode.OUTPUT_VALIDATION_FAILED`（供 Task 4 executor 透传）

- [ ] **Step 1: 写失败测试**（追加到 `test_providers.py`）

```python
from shannon_core.models.errors import ErrorCode
from shannon_core.agents.openai_output_schema import StructuredOutputParseError


def _openai_provider():
    return OpenAIProvider(ProviderConfig(
        type="openai_compatible", api_key="test", base_url="https://x.example.com"))


def test_classify_structured_output_parse_error():
    p = _openai_provider()
    code, retryable = p._classify_error(StructuredOutputParseError("bad json"))
    assert code == "OutputValidationError"
    assert retryable is True


def test_handle_error_sets_output_validation_failed_enum():
    p = _openai_provider()
    result = p._handle_error(StructuredOutputParseError("bad json"), 100, "m")
    assert result.error_code == ErrorCode.OUTPUT_VALIDATION_FAILED
    assert result.success is False
    assert result.retryable is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/agents/test_providers.py::test_classify_structured_output_parse_error -v`
Expected: FAIL（`_classify_error` 当前不识别 `StructuredOutputParseError`，返回默认 `("AgentExecutionError", True)`）

- [ ] **Step 3a: 补 import**（`providers_openai.py` 顶部）

把现有 `from .openai_output_schema import RawJsonSchemaOutputSchema` 改为：
```python
from .openai_output_schema import RawJsonSchemaOutputSchema, StructuredOutputParseError
```
并在 import 区加：
```python
from shannon_core.models.errors import ErrorCode
```

- [ ] **Step 3b: 改 `_classify_error`**（在方法体最前面加分支，`providers_openai.py:201` 的 `def _classify_error` 内、`error_msg = str(error).lower()` 之前）

```python
    def _classify_error(self, error: Exception) -> tuple[str | None, bool]:
        if isinstance(error, StructuredOutputParseError):
            return ("OutputValidationError", True)
        error_msg = str(error).lower()
        error_type = type(error).__name__.lower()
        # …（现有 rate/timeout/unavailable/auth/permission/default 分支不动）
```

- [ ] **Step 3c: 改 `_handle_error`**（`providers_openai.py:187`，StructuredOutputParseError 时用 ErrorCode enum 覆盖）

```python
    def _handle_error(self, error: Exception, duration: int, model: str) -> ClaudeRunResult:
        error_code, retryable = self._classify_error(error)
        # StructuredOutputParseError 走 ErrorCode enum（供 executor isinstance 守卫透传 →
        # classify_error_for_temporal Level 1 匹配 OUTPUT_VALIDATION_FAILED）；其他错误
        # 保留 _classify_error 的字符串（Temporal error type，executor 不透传，保持
        # AGENT_EXECUTION_FAILED 现有行为，避免破坏 RateLimit/Timeout 分类）。
        if isinstance(error, StructuredOutputParseError):
            error_code = ErrorCode.OUTPUT_VALIDATION_FAILED
        return ClaudeRunResult(
            text="",
            success=False,
            duration=duration,
            turns=0,
            cost=0.0,
            model=model,
            error=str(error),
            error_code=error_code,
            retryable=retryable,
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/agents/test_providers.py -v`
Expected: PASS（新增 2 个 + 现有 provider 测试不退化）

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/agents/providers_openai.py packages/core/tests/agents/test_providers.py
git commit -m "feat(openai): L2 provider 识别 StructuredOutputParseError → OUTPUT_VALIDATION_FAILED"
```

---

## Task 4: L2 executor error_code 透传（`isinstance(ErrorCode)` 守卫）

**Files:**
- Modify: `packages/core/src/shannon_core/agents/executor.py:124-131`
- Test: `packages/core/tests/test_executor_error_code_passthrough.py`（创建）

**Interfaces:**
- Consumes: Task 3 provider 设的 `result.error_code = ErrorCode.OUTPUT_VALIDATION_FAILED`
- Produces: executor 把合法 ErrorCode 透传到 `PentestError.error_code`（→ `classify_error_for_temporal` Level 1 匹配 → `("OutputValidationError", True)` → Temporal vuln 8 次兜底）

- [ ] **Step 1: 写失败测试**（创建 `test_executor_error_code_passthrough.py`）

```python
"""executor 对 provider 合法 ErrorCode 的透传守卫。

验证：result.error_code 是 ErrorCode enum 时透传到 PentestError.error_code；
是非 enum 字符串时保持 AGENT_EXECUTION_FAILED（避免破坏 RateLimit/Timeout 分类）。
"""
import asyncio

import pytest

from shannon_core.agents import executor as exec_mod
from shannon_core.models.errors import ErrorCode, PentestError


def _run(coro):
    return asyncio.run(coro)


def _patch_runtime(monkeypatch, tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    monkeypatch.setattr(exec_mod.GitManager, "ensure_repository",
                        classmethod(lambda cls, p: asyncio.sleep(0)))
    monkeypatch.setattr(exec_mod.GitManager, "create_checkpoint",
                        lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(exec_mod.GitManager, "rollback",
                        lambda *a, **k: asyncio.sleep(0))
    from shannon_core.prompts.manager import PromptManager
    pm = PromptManager.__new__(PromptManager)
    pm.prompts_dir = tmp_path
    monkeypatch.setattr(pm, "load_sync", lambda *a, **k: "PROMPT")
    return deliverables, exec_mod.AgentExecutor(pm)


def _stub_result(*, error_code):
    class _R:
        success = False
        turns = 1
        cost = 0.0
        text = ""
        error = "structured output parse failed"
        retryable = True
        model = "stub"
        stop_reason = "end_turn"
    r = _R()
    r.error_code = error_code
    return r


def test_executor_passes_output_validation_failed(tmp_path, monkeypatch):
    deliverables, ax = _patch_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(exec_mod, "run_claude_prompt",
                        lambda **kw: asyncio.sleep(0) or _stub_result(
                            error_code=ErrorCode.OUTPUT_VALIDATION_FAILED))
    with pytest.raises(PentestError) as exc:
        _run(ax.execute(
            agent_name=exec_mod.AgentName.INJECTION_VULN,
            repo_path=str(deliverables), deliverables_path=str(deliverables),
            skip_artifact_postprocess=True,
        ))
    assert exc.value.error_code == ErrorCode.OUTPUT_VALIDATION_FAILED


def test_executor_keeps_agent_execution_failed_for_string_code(tmp_path, monkeypatch):
    """provider 的字符串 error_code（Temporal error type，非 enum）不透传。"""
    deliverables, ax = _patch_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(exec_mod, "run_claude_prompt",
                        lambda **kw: asyncio.sleep(0) or _stub_result(error_code="RateLimitError"))
    with pytest.raises(PentestError) as exc:
        _run(ax.execute(
            agent_name=exec_mod.AgentName.INJECTION_VULN,
            repo_path=str(deliverables), deliverables_path=str(deliverables),
            skip_artifact_postprocess=True,
        ))
    assert exc.value.error_code == ErrorCode.AGENT_EXECUTION_FAILED
```

（文件顶部需 `import pytest`，加在 import 区。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_executor_error_code_passthrough.py -v`
Expected: FAIL（当前 executor line 130 硬编码 `error_code=ErrorCode.AGENT_EXECUTION_FAILED`，第一个测试拿不到 `OUTPUT_VALIDATION_FAILED`）

- [ ] **Step 3: 写实现**（替换 `executor.py:124-131`）

```python
        if not result.success:
            await GitManager.rollback(deliverables, "execution failure")
            # 透传 provider 设的合法 ErrorCode（如 OUTPUT_VALIDATION_FAILED）；
            # provider 的字符串 error_code（Temporal error type，非 enum）不透传，
            # 保持 AGENT_EXECUTION_FAILED 现有行为（避免破坏 RateLimit/Timeout 分类）。
            error_code = (
                result.error_code
                if isinstance(result.error_code, ErrorCode)
                else ErrorCode.AGENT_EXECUTION_FAILED
            )
            raise PentestError(
                result.error or f"Agent {agent_name.value} execution failed",
                "validation",
                retryable=result.retryable,
                error_code=error_code,
            )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_executor_error_code_passthrough.py -v`
Expected: PASS（2 个测试）

- [ ] **Step 5: 跑回归锚点**（确认 executor 改动不破坏现有 postprocess 测试）

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_executor_artifact_postprocess.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add packages/core/src/shannon_core/agents/executor.py packages/core/tests/test_executor_error_code_passthrough.py
git commit -m "feat(executor): L2 error_code isinstance(ErrorCode) 守卫透传"
```

---

## Task 5: L1 provider 轻量重输 —— `_ReparsedRunResult` + `_lightweight_reparse`

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers_openai.py`（顶部 import 加 `json` / `_extract_json_payload`；新增 `_ReparsedRunResult` 类 + `_lightweight_reparse` 方法）
- Test: `packages/core/tests/agents/test_providers_openai_reparse.py`（创建）

**Interfaces:**
- Consumes: Task 1 的 `_extract_json_payload`；`self._get_client()`
- Produces: `_lightweight_reparse(text, output_format, model) -> _ReparsedRunResult | None`、`_ReparsedRunResult`（供 Task 6 `call()` 捕获 `StructuredOutputParseError` 后恢复）

- [ ] **Step 1: 写失败测试**（创建 `test_providers_openai_reparse.py`）

```python
"""L1 provider 轻量重输：L0 容错失败后发单个 chat completion 让 GLM 把分析转纯 JSON。"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from shannon_core.agents.providers_openai import OpenAIProvider, _ReparsedRunResult
from shannon_core.agents.runner import ProviderConfig


def _provider_with_client(fake_client):
    p = OpenAIProvider(ProviderConfig(
        type="openai_compatible", api_key="test", base_url="https://x.example.com"))
    p._client = fake_client
    return p


def _fake_chat_response(content, prompt_tokens=5, completion_tokens=10):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage = MagicMock(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return resp


@pytest.mark.asyncio
async def test_lightweight_reparse_recovers_pure_json():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_chat_response('{"vulnerabilities": []}'))
    p = _provider_with_client(client)
    out = await p._lightweight_reparse("some analysis text", {"type": "object"}, "m")
    assert isinstance(out, _ReparsedRunResult)
    assert out.final_output == {"vulnerabilities": []}
    assert out.context_wrapper.usage.input_tokens == 5
    assert out.context_wrapper.usage.output_tokens == 10


@pytest.mark.asyncio
async def test_lightweight_reparse_recovers_fenced_json():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_chat_response('```json\n{"vulnerabilities": []}\n```'))
    p = _provider_with_client(client)
    out = await p._lightweight_reparse("text", {"type": "object"}, "m")
    assert out.final_output == {"vulnerabilities": []}


@pytest.mark.asyncio
async def test_lightweight_reparse_returns_none_on_garbage():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_chat_response("仍然不是 JSON"))
    p = _provider_with_client(client)
    assert await p._lightweight_reparse("text", {"type": "object"}, "m") is None


@pytest.mark.asyncio
async def test_lightweight_reparse_returns_none_on_api_error():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=RuntimeError("boom"))
    p = _provider_with_client(client)
    assert await p._lightweight_reparse("text", {"type": "object"}, "m") is None


@pytest.mark.asyncio
async def test_lightweight_reparse_skips_when_no_schema_or_text():
    client = MagicMock()
    p = _provider_with_client(client)
    assert await p._lightweight_reparse("text", None, "m") is None
    assert await p._lightweight_reparse("", {"type": "object"}, "m") is None
    client.chat.completions.create.assert_not_called()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/agents/test_providers_openai_reparse.py -v`
Expected: FAIL（`ImportError: cannot import name '_ReparsedRunResult'`）

- [ ] **Step 3a: 补 import**（`providers_openai.py` 顶部，`import os` / `import time` 旁加 `import json`；并把 openai_output_schema import 行扩展为）

```python
from .openai_output_schema import (
    RawJsonSchemaOutputSchema,
    StructuredOutputParseError,
    _extract_json_payload,
)
```

- [ ] **Step 3b: 新增 `_ReparsedRunResult` 类**（放在文件末尾 `_MaxTurnsStub` 类之后）

```python
class _ReparsedRunResult:
    """L1 轻量重输成功后的最小 RunResult stub。

    仅含 map_run_result 需要的 final_output（= recovered dict）+ context_wrapper.usage
    （带 L1 chat completion 的真实 token，避免统计失真；cost 仍走 GLM 0.0 早退）。
    usage 用普通类承载（不用 MagicMock），避免 map_run_result 的
    getattr(usage, "input_tokens", 0) 被 MagicMock 恒真干扰。
    """
    def __init__(self, final_output, input_tokens: int = 0, output_tokens: int = 0):
        self.final_output = final_output

        class _U:
            def __init__(self):
                self.input_tokens = input_tokens
                self.output_tokens = output_tokens

        class _CW:
            def __init__(self):
                self.usage = _U()

        self.context_wrapper = _CW()
```

- [ ] **Step 3c: 新增 `_lightweight_reparse` 方法**（`OpenAIProvider` 类内，`call` 方法之前）

```python
    async def _lightweight_reparse(self, text: str, output_format: dict | None, model: str):
        """L1：L0 容错失败后，发单个轻量 chat completion 让 GLM 把分析转纯 JSON。

        模拟 Claude SDK 单次内部重试（openai-agents 无此层）。仅 1 个 chat completion，
        无 agent loop / 工具 / narration directive。不传 response_format（GLM 第三方后端
        兼容不确定），靠 prompt + _extract_json_payload 兜底。任一步失败 → None（进 L2）。
        """
        if not output_format or not text or not text.strip():
            return None
        client = self._get_client()
        prompt = (
            "将以下分析结论转为符合 schema 的纯 JSON，只输出 JSON 本体，"
            "不要任何解释、前言或 markdown 代码围栏：\n" + text
        )
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception:
            return None
        choices = getattr(resp, "choices", None)
        content = ""
        if choices:
            content = getattr(choices[0].message, "content", "") or ""
        candidate = _extract_json_payload(content)
        if candidate is None:
            return None
        try:
            recovered = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            return None
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", 0) or 0
        out_tok = getattr(usage, "completion_tokens", 0) or 0
        return _ReparsedRunResult(recovered, in_tok, out_tok)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/agents/test_providers_openai_reparse.py -v`
Expected: PASS（5 个测试）

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/agents/providers_openai.py packages/core/tests/agents/test_providers_openai_reparse.py
git commit -m "feat(openai): L1 provider 轻量重输 (_lightweight_reparse + _ReparsedRunResult)"
```

---

## Task 6: L1 `call()` 集成 —— 捕获 `StructuredOutputParseError` 触发轻量重输（堵 task-probe 盲区）

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers_openai.py:157-172`（`call()` 内层 try/except）
- Test: `packages/core/tests/agents/test_providers_openai_call_l1.py`（创建）

**Interfaces:**
- Consumes: Task 2（`validate_json` 抛 `StructuredOutputParseError`）、Task 5（`_lightweight_reparse` / `_ReparsedRunResult`）、Task 3（L2 `_handle_error`，L1 失败时 re-raise 进入）
- Produces: `call()` 在 structured output 解析失败时逐级走 L1 → L2，不再因 `JSONDecodeError` 直接判 agent 死刑

- [ ] **Step 1: 写失败测试**（创建 `test_providers_openai_call_l1.py`）

```python
"""L1 call() 集成：validate_json 抛 StructuredOutputParseError 时触发轻量重输。

堵 task-probe 盲区（task probe 只覆盖无 output_type 的子代理，覆盖不到带 structured
output 的顶层 agent）。用 monkeypatch 让 Runner.run_streamed 的 stream_events 抛
StructuredOutputParseError，验证 call() 调 _lightweight_reparse。
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.agents.openai_output_schema import StructuredOutputParseError
from shannon_core.agents.providers_openai import OpenAIProvider, _ReparsedRunResult
from shannon_core.agents.runner import ProviderConfig


def _provider():
    return OpenAIProvider(ProviderConfig(
        type="openai_compatible", api_key="test", base_url="https://x.example.com"))


def _streaming_result_that_raises(exc):
    """伪造 Runner.run_streamed 返回的对象：stream_events 抛 exc。"""
    result = MagicMock()

    async def _stream():
        if False:  # 保证是 async generator
            yield
        raise exc

    result.stream_events = _stream
    return result


@pytest.mark.asyncio
async def test_call_l1_recovers_structured_output(monkeypatch):
    p = _provider()
    monkeypatch.setattr(p, "_lightweight_reparse", AsyncMock(return_value=_ReparsedRunResult(
        {"vulnerabilities": []}, input_tokens=3, output_tokens=7)))
    monkeypatch.setattr(
        "shannon_core.agents.providers_openai.Runner.run_streamed",
        MagicMock(return_value=_streaming_result_that_raises(StructuredOutputParseError("bad"))))

    result = await p.call(prompt="P", cwd="/tmp", model_tier="medium",
                          output_format={"type": "object"})
    assert result.success is True
    assert result.structured_output == {"vulnerabilities": []}
    assert result.tokens.input_tokens == 3
    assert result.tokens.output_tokens == 7


@pytest.mark.asyncio
async def test_call_l1_failure_raises_for_l2(monkeypatch):
    """L1 也失败 → re-raise StructuredOutputParseError → 外层 _handle_error → L2。"""
    p = _provider()
    monkeypatch.setattr(p, "_lightweight_reparse", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "shannon_core.agents.providers_openai.Runner.run_streamed",
        MagicMock(return_value=_streaming_result_that_raises(StructuredOutputParseError("bad"))))

    result = await p.call(prompt="P", cwd="/tmp", model_tier="medium",
                          output_format={"type": "object"})
    assert result.success is False
    from shannon_core.models.errors import ErrorCode
    assert result.error_code == ErrorCode.OUTPUT_VALIDATION_FAILED
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/agents/test_providers_openai_call_l1.py -v`
Expected: FAIL（当前 `call()` 内层 try 只有 `except MaxTurnsExceeded`，`StructuredOutputParseError` 冒泡到外层 `except Exception` → `_handle_error`，第一个测试 `result.success` 为 False 而非 True）

- [ ] **Step 3: 写实现**（`providers_openai.py` `call()` 内层 try/except，当前 line 157-172，在 `except MaxTurnsExceeded` 之后、方法返回 `map_run_result` 之前加 `except StructuredOutputParseError` 分支）

定位现有代码块：
```python
            try:
                result = Runner.run_streamed(
                    agent,
                    input=prompt,
                    context=ToolContext(cwd=cwd, subagent_run=self._make_subagent_runner(model, cwd)),
                    max_turns=max_turns or self._max_turns(),
                )
                async for event in result.stream_events():
                    await collector.on_event(event)
                await collector.close()
                run_result = result
            except MaxTurnsExceeded:
                await collector.close()
                # 无可用 RunResult，构造一个最小结果对象
                run_result = _MaxTurnsStub(collector.text)
                stop_reason = "max_turns"
```
在 `except MaxTurnsExceeded` 块之后追加：
```python
            except StructuredOutputParseError:
                # L1：L0 容错失败 → 轻量重输，模拟 Claude SDK 单次内部重试。
                # 失败（None）→ re-raise → 外层 except Exception → _handle_error → L2。
                await collector.close()
                reparsed = await self._lightweight_reparse(collector.text, output_format, model)
                if reparsed is None:
                    raise
                run_result = reparsed
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/agents/test_providers_openai_call_l1.py -v`
Expected: PASS（2 个测试）

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/agents/providers_openai.py packages/core/tests/agents/test_providers_openai_call_l1.py
git commit -m "feat(openai): L1 call() 集成轻量重输 + 堵 task-probe 盲区"
```

---

## Task 7: 回归验证 + 真机冒烟

**Files:** 无代码改动（验证 + 手册）

- [ ] **Step 1: 跑全部新增/改动测试**

Run:
```bash
cd /root/shannon-py && uv run pytest \
  packages/core/tests/agents/test_openai_output_schema.py \
  packages/core/tests/agents/test_providers.py \
  packages/core/tests/agents/test_providers_openai_reparse.py \
  packages/core/tests/agents/test_providers_openai_call_l1.py \
  packages/core/tests/test_executor_error_code_passthrough.py \
  -v
```
Expected: 全 PASS

- [ ] **Step 2: 跑回归锚点（确认不破坏既有）**

Run:
```bash
cd /root/shannon-py && uv run pytest \
  packages/core/tests/agents/test_openai_result_mapper.py \
  packages/core/tests/test_executor_artifact_postprocess.py \
  packages/core/tests/agents/test_narration_injection.py \
  packages/core/tests/agents/test_dual_engine_alignment.py \
  packages/whitebox/tests/test_run_agent_vuln_schema.py \
  -v
```
Expected: 全 PASS（`test_openai_result_mapper.py` 的 fallback 分支保留作第二道防线，不退化；vuln schema 透传不变；双引擎对齐不破坏）

- [ ] **Step 3: 真机冒烟（glm-openai profile，手动）**

确认 `SHANNON_PROFILE=glm-openai`（`.env`），重跑触发 bug 的扫描：
```bash
cd /root/shannon-py && uv run shannon-whitebox start --repo /root/code/frontend/invite_code_center/
```
Expected: injection-vuln / xss-vuln 不再因 `Expecting value: line 1 column 1 (char 0)` 全失败——
- 多数情况 L0（容错解析）或 L1（轻量重输）恢复 structured output，`{vt}_exploitation_queue.json` 正常落盘；
- 极端情况（L0+L1 都失败）走 L2 `OUTPUT_VALIDATION_FAILED`（retryable），Temporal 受控重试，不再 8 次徒劳。

观察 `workspaces/<run>/activity_failures.log`：不应再出现 `Expecting value: line 1 column 1 (char 0)`。

- [ ] **Step 4: 提交（如有探针/手册补充）**

本 task 无代码改动；若 Step 3 真机发现需微调（如 L1 prompt 调优），单独提交：
```bash
git commit -am "fix(openai): L1 真机冒烟微调"
```

---

## Self-Review 记录

- **Spec 覆盖**：spec §4.2（L0）→ Task 1+2；§4.3（L1）→ Task 5+6；§4.4（L2）→ Task 3+4；§5 测试 → 各 Task Step 1 + Task 7；§6 风险「L1 response_format GLM 接受度」→ Global Constraints + Task 5 决定不传 response_format 规避；spec §5 的「探针」→ Task 6 集成测试（自动化可回归，优于一次性 scripts 探针）+ Task 7 真机冒烟共同满足。
- **对 spec 的合理偏离（plan 细化）**：① L1 不传 `response_format`（spec §4.3 提到，plan 决定不传以消除 §6 最大风险，靠 `_extract_json_payload` 兜底）；② executor 用 `isinstance(ErrorCode)` 守卫透传（spec §4.4 写的 `result.error_code or ...` 会误透传字符串破坏 RateLimit/Timeout 分类，plan 修正）；③ spec §5 的 scripts 探针改为 Task 6 集成测试（可回归）。
- **类型一致**：`_extract_json_payload`、`StructuredOutputParseError`、`_ReparsedRunResult`、`_lightweight_reparse` 在各 Task 间命名/签名一致；`_ReparsedRunResult.context_wrapper.usage.input_tokens/output_tokens` 与 `openai_result_mapper._usage_from` 读取的属性一致。
