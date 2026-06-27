# LLM 轨 vuln agent 对齐 TS 修复 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补回 LLM 轨两处被删的 prompt 方法论 + 把 vuln agent 的 max_turns / VULN_RETRY / openai 子代理 turn 从偏紧值调到锚定 TS 经验的保守值，并补 turn 消耗观测，使重构项目的 LLM 轨不再弱于原始 TS 项目。

**Architecture:** B1 是纯 prompt 文本补回（两段）。B2 走"为 vuln 单独配置 + override 透传链"：`run_claude_prompt(max_turns=)` → `provider.call(max_turns=)` → provider 内部 `max_turns or env默认`；vuln 的 500 由 `run_agent` 经 `executor.execute` 传入，其他 agent 不传（零行为变更）。观测复用 `AgentMetrics.num_turns`（已存在）+ `AgentEndResult` 新增字段。openai 子代理 turn 调大无递归风险（结构层已硬限单层）。

**Tech Stack:** Python 3, temporalio, claude-agent-sdk, openai-agents, pydantic, pytest（asyncio）

## Global Constraints

- **数值锁定（来自 spec §3）**：`SHANNON_VULN_MAX_TURNS` 默认 **500**；`VULN_RETRY.maximum_attempts` **8**；`SHANNON_OPENAI_SUBAGENT_MAX_TURNS` 默认 **40**。所有数值经 env / 常量，不硬编码到调用点。
- **只跑改动相关测试文件**（守 memory `pytest-whitebox-hang`：shannon-py 跑全量会 hang 在 Temporal/网络慢测试）。每个 task 的 Steps 给出精确的 `pytest <file>::<test>` 命令，**不跑 `pytest` 全量**。
- **守 LLM 轨铁律**：B1 补回的两段是纯方法论文本，**不含** `{{...}}` 占位符、不引任何确定性产物（`@include` / `parameter_graph` / `sink_call_sites` 等）。补回后须通过既有 `test_static_dataflow_hints_decoupling.py` 守卫。
- **分支**：`feat/fork-py`。每个 task 末尾 commit。
- **vuln 判定复用现有**：`agent_retry_category(agent_name.value) == "vuln"`（`models/retry.py:103-112`，已存在）。
- **执行链事实**：`run_vuln_agent → run_agent → executor.execute → run_claude_prompt → provider.call`（**不是** activity 直调 run_claude_prompt）。

---

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `prompts/vuln-injection.txt` | LLM 轨 injection agent prompt | 补 Branch Path Exhaustion 段 |
| `prompts/vuln-xss.txt` | LLM 轨 xss agent prompt | 补 server-rendered templates 段 |
| `packages/core/src/shannon_core/models/retry.py` | VULN_RETRY 常量 | `maximum_attempts 5→8` |
| `packages/core/src/shannon_core/agents/providers_openai.py` | openai 引擎 provider | 子代理 max_turns 20→40（提取 `_subagent_max_turns()`）；`call` 增 `max_turns` 参 |
| `packages/core/src/shannon_core/agents/providers_anthropic.py` | anthropic 引擎 provider | `_build_options` 增 `max_turns_override`；`call` 增 `max_turns` 参 |
| `packages/core/src/shannon_core/agents/runner.py` | `run_claude_prompt` 统一入口 | 增 `max_turns` 参，透传 `provider.call` |
| `packages/core/src/shannon_core/agents/executor.py` | `AgentExecutor.execute` | 增 `max_turns` 参，透传 `run_claude_prompt` |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | `run_agent` activity | 新增 `_vuln_max_turns(agent_name)`，传给 `executor.execute`；`AgentEndResult` 传 `num_turns` |
| `packages/core/src/shannon_core/models/audit.py` | `AgentEndResult` | 增 `num_turns` 字段 |
| 测试（见各 task） | TDD 锚点 | — |

---

## Task 1: B1 — 补回 vuln-injection Branch Path Exhaustion 段

**Files:**
- Modify: `prompts/vuln-injection.txt`（第 138 行 `**Path Forking:**` 段之后插入）
- Test: `packages/core/tests/prompts/test_vuln_injection_prompt.py`（追加测试函数）

**Interfaces:**
- Consumes: 无
- Produces: prompt 含 `Branch Path Exhaustion` 方法论段（供本 task 锚点测试断言）

- [ ] **Step 1: 写失败测试** — 在 `test_vuln_injection_prompt.py` 末尾追加：

```python
def test_prompt_has_branch_path_exhaustion():
    """B1 补回：分支独立 trace 方法论（防漏报分支间校验不一致的注入）。"""
    text = PROMPT.read_text()
    assert "Branch Path Exhaustion" in text
    assert "conditional branches" in text
    assert "trace every branch independently" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/core/tests/prompts/test_vuln_injection_prompt.py::test_prompt_has_branch_path_exhaustion -v`
Expected: FAIL（`AssertionError: assert 'Branch Path Exhaustion' in '...'`）

- [ ] **Step 3: 补回 prompt 段** — 在 `prompts/vuln-injection.txt` 中，定位 `**Path Forking:**` 那一行（约第 138 行，结尾 `... two distinct units.`）与下一行 `**For each distinct path, you must record:**`（约第 139 行）。在两者**之间**插入一个新列表项，**缩进与 `**Path Forking:**` 项完全一致**（与该行行首的 tab/空格对齐），内容：

```
- **Branch Path Exhaustion:** When a controller method contains conditional branches (if/else, early returns) that lead to different data transformations for the same output variable, you MUST trace every branch independently. Do NOT assume a parameter is safe because one branch validates it — another branch may read the same parameter directly from user input without validation.
```

> 文本逐字取自 TS `shannon/apps/worker/prompts/vuln-injection.txt:145`。用 Edit 工具：`old_string` 取 Path Forking 行尾片段 ` two distinct units.` 起到 `For each distinct path` 行首的唯一串，`new_string` 在中间插入上述 Branch Path Exhaustion 行（含同等缩进）。若对 tab 缩进不确定，先 Read 该文件第 136–142 行确认缩进字节。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest packages/core/tests/prompts/test_vuln_injection_prompt.py -v`
Expected: PASS（全部，含新测试 + 既有守卫）

- [ ] **Step 5: commit**

```bash
git add prompts/vuln-injection.txt packages/core/tests/prompts/test_vuln_injection_prompt.py
git commit -m "feat(prompt): 补回 vuln-injection Branch Path Exhaustion 方法论 (B1)"
```

---

## Task 2: B1 — 补回 vuln-xss server-rendered templates 段

**Files:**
- Modify: `prompts/vuln-xss.txt`（第 134 行 `Read {{DELIVERABLES_PATH}}/pre_recon_deliverable.md ...` 之后、第 136 行 `### **2)` 之前插入）
- Test: `packages/core/tests/prompts/test_vuln_xss_prompt.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces: prompt 含 server-rendered templates 段；新测试文件 `test_vuln_xss_prompt.py`

- [ ] **Step 1: 写失败测试** — 新建 `packages/core/tests/prompts/test_vuln_xss_prompt.py`：

```python
"""B1 补回：vuln-xss prompt 方法论锚点。

Asserts the LLM-track xss prompt carries the server-rendered templates note
(reflected XSS in render calls + JSON.stringify/</script> bypass), restored
from the original TS project (apps/worker/prompts/vuln-xss.txt:138).
"""
from pathlib import Path

PROMPT = Path(__file__).resolve().parents[4] / "prompts" / "vuln-xss.txt"


def test_prompt_has_server_rendered_templates_note():
    """B1 补回：render-context reflected XSS 方法论 + JSON.stringify 绕过示例。"""
    text = PROMPT.read_text()
    assert "server-rendered templates" in text
    assert "ctx.render" in text and "res.render" in text
    assert "JSON.stringify" in text and "</script>" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/core/tests/prompts/test_vuln_xss_prompt.py::test_prompt_has_server_rendered_templates_note -v`
Expected: FAIL（`assert 'server-rendered templates' in '...'`）

- [ ] **Step 3: 补回 prompt 段** — 在 `prompts/vuln-xss.txt` 中，定位第 134 行 `Read {{DELIVERABLES_PATH}}/pre_recon_deliverable.md section ##9. XSS Sinks and Render Contexts## ... needs analysis.`，其下是空行（135），再下是 `### **2) Trace Each Sink Backward (Backward Taint Analysis)**`（136）。在第 134 行内容之后、`### **2)` 之前插入（保留前后各一空行，与 TS 结构一致）：

```
**Note on server-rendered templates:** Pay special attention to template render calls (`ctx.render`, `res.render`) where template context variables originate from URL query parameters (`ctx.query.*`). These are reflected XSS candidates even when the injection agent has already analyzed SSTI for the same template — the xss agent provides independent render-context analysis (e.g., `JSON.stringify()` inside a `<script>` tag does not escape `</script>`, making it unsafe for JAVASCRIPT_STRING context).
```

> 文本逐字取自 TS `shannon/apps/worker/prompts/vuln-xss.txt:138`。Edit：`old_string` 用第 134 行结尾 `...needs analysis.` + 空行 + `### **2) Trace Each Sink Backward` 的唯一串，`new_string` 在空行处插入 Note 段（前后留空行）。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest packages/core/tests/prompts/test_vuln_xss_prompt.py -v`
Expected: PASS

- [ ] **Step 5: 跑铁律守卫确认未破坏** — 确认补回段未引入确定性产物引用：

Run: `pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py -v`
Expected: PASS（全绿；补回段纯方法论，无 `@include` / 占位符）

- [ ] **Step 6: commit**

```bash
git add prompts/vuln-xss.txt packages/core/tests/prompts/test_vuln_xss_prompt.py
git commit -m "feat(prompt): 补回 vuln-xss server-rendered templates 方法论 (B1)"
```

---

## Task 3: B2 — VULN_RETRY maximum_attempts 5→8

**Files:**
- Modify: `packages/core/src/shannon_core/models/retry.py:56-62`
- Test: `packages/core/tests/test_retry_profiles.py:50`

**Interfaces:**
- Consumes: 无
- Produces: `VULN_RETRY.maximum_attempts == 8`（封顶 ~12min→~20min）

- [ ] **Step 1: 改测试断言** — `test_retry_profiles.py` 第 50 行：

```python
    def test_vuln_retry_params(self):
        assert VULN_RETRY.maximum_attempts == 8
        assert VULN_RETRY.initial_interval == timedelta(minutes=1)
        assert VULN_RETRY.maximum_interval == timedelta(minutes=5)
        assert VULN_RETRY.backoff_coefficient == 2.0
        assert VULN_RETRY.non_retryable_error_types == NON_RETRYABLE  # 共享 NON_RETRYABLE
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/core/tests/test_retry_profiles.py::TestVulnRetry -v`
Expected: FAIL（`assert 5 == 8`）

- [ ] **Step 3: 改 retry.py** — `models/retry.py` VULN_RETRY 定义（54–62 行）：

```python
# vuln agent 专用:per-vt fan-out 下封顶 ~20min,有意分歧于 TS PRODUCTION_RETRY。
# 详见 docs/superpowers/specs/2026-06-22-retry-policy-alignment-design.md §2.3
# 及 2026-06-28-llm-track-vuln-parity-restoration-design.md §4.3。
VULN_RETRY = RetryPolicy(
    maximum_attempts=8,
    initial_interval=timedelta(minutes=1),
    maximum_interval=timedelta(minutes=5),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE,
)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest packages/core/tests/test_retry_profiles.py -v`
Expected: PASS（全部 12 函数）

- [ ] **Step 5: commit**

```bash
git add packages/core/src/shannon_core/models/retry.py packages/core/tests/test_retry_profiles.py
git commit -m "feat(retry): VULN_RETRY maximum_attempts 5→8 (B2, 封顶 ~20min)"
```

---

## Task 4: B2 — openai 子代理 max_turns 20→40（提取 `_subagent_max_turns()`）

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers_openai.py`（加 `_subagent_max_turns()`，`_make_subagent_runner` 用它）
- Test: `packages/core/tests/agents/test_providers.py`（追加 `TestOpenAISubagentMaxTurns`）

**Interfaces:**
- Consumes: 无
- Produces: `OpenAIProvider._subagent_max_turns()`（默认 40，env 可覆盖）；`_make_subagent_runner` 用它

- [ ] **Step 1: 写失败测试** — 在 `test_providers.py` 追加（参照 `TestAnthropicProviderBuildOptions` 的实例化模式）：

```python
class TestOpenAISubagentMaxTurns:
    """B2: openai 子代理 max_turns 默认 40（对称主 agent _max_turns()）。"""

    def test_default_is_40(self, monkeypatch):
        monkeypatch.delenv("SHANNON_OPENAI_SUBAGENT_MAX_TURNS", raising=False)
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible"))
        assert provider._subagent_max_turns() == 40

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SHANNON_OPENAI_SUBAGENT_MAX_TURNS", "60")
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible"))
        assert provider._subagent_max_turns() == 60
```

> 若 `test_providers.py` 顶部未 import `OpenAIProvider` / `ProviderConfig`，在该文件现有 import 区补上：`from shannon_core.agents.providers_openai import OpenAIProvider` 与 `from shannon_core.agents.runner import ProviderConfig`（按现有 import 风格调整路径）。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/core/tests/agents/test_providers.py::TestOpenAISubagentMaxTurns -v`
Expected: FAIL（`AttributeError: 'OpenAIProvider' object has no attribute '_subagent_max_turns'`）

- [ ] **Step 3: 实现** — 在 `providers_openai.py` 中：

(a) 在 `_max_turns()` 方法（约 73–74 行）之后新增对称方法：

```python
    def _subagent_max_turns(self) -> int:
        # 子代理（Task 委派）max_turns。结构层已硬限单层（子代理无 subagent_run
        # + 只读工具集 [read_file, glob, grep]），调大无递归风险，仅增单次 token。
        # B2: 20→40,锚定更复杂的追链子任务。
        return int(os.getenv("SHANNON_OPENAI_SUBAGENT_MAX_TURNS", "40"))
```

(b) 把 `_make_subagent_runner` 中的 `max_turns = int(os.getenv("SHANNON_OPENAI_SUBAGENT_MAX_TURNS", "20"))`（约 122 行）替换为：

```python
        max_turns = self._subagent_max_turns()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest packages/core/tests/agents/test_providers.py::TestOpenAISubagentMaxTurns -v`
Expected: PASS（2 函数）

- [ ] **Step 5: 跑既有 openai provider 套件确认未回归**

Run: `pytest packages/core/tests/agents/test_providers.py -k "OpenAI" -v`
Expected: PASS（既有 OpenAI 相关测试全绿）

- [ ] **Step 6: commit**

```bash
git add packages/core/src/shannon_core/agents/providers_openai.py packages/core/tests/agents/test_providers.py
git commit -m "feat(provider): openai 子代理 max_turns 20→40 via _subagent_max_turns() (B2)"
```

---

## Task 5: B2 — max_turns override 透传链底层（`run_claude_prompt` + 两 provider）

**Files:**
- Modify: `packages/core/src/shannon_core/agents/runner.py`（`run_claude_prompt` 增 `max_turns` 参 + 透传 `provider.call`）
- Modify: `packages/core/src/shannon_core/agents/providers_anthropic.py`（`call` 增 `max_turns`；`_build_options` 增 `max_turns_override`）
- Modify: `packages/core/src/shannon_core/agents/providers_openai.py`（`call` 增 `max_turns`；`Runner.run_streamed` 用 override）
- Test: `packages/core/tests/test_runner.py`（透传测试）+ `packages/core/tests/agents/test_providers.py`（两 provider override 测试）

**Interfaces:**
- Consumes: 无
- Produces: `run_claude_prompt(..., max_turns: int | None = None)`；`provider.call(..., max_turns: int | None = None)` —— provider 内部 `max_turns or <env默认>`（外部传入优先）。Task 6 依赖此签名。

- [ ] **Step 1: 写失败测试（runner 透传）** — 在 `test_runner.py` 的 `TestRunClaudePrompt` 类内追加：

```python
    @pytest.mark.asyncio
    async def test_run_claude_prompt_forwards_max_turns_to_provider(self):
        """B2: run_claude_prompt(max_turns=N) 透传到 provider.call(max_turns=N)。"""
        mock_provider = MagicMock()
        mock_result = ClaudeRunResult(text="ok", success=True, duration=10, turns=1)
        mock_provider.call = AsyncMock(return_value=mock_result)

        with patch("shannon_core.agents.providers.create_provider", return_value=mock_provider):
            await run_claude_prompt(prompt="p", repo_path="/tmp/repo", max_turns=500)

        mock_provider.call.assert_awaited_once()
        assert mock_provider.call.call_args.kwargs["max_turns"] == 500

    @pytest.mark.asyncio
    async def test_run_claude_prompt_max_turns_none_when_omitted(self):
        """不传 max_turns 时透传 None（provider 沿用 env 默认）。"""
        mock_provider = MagicMock()
        mock_result = ClaudeRunResult(text="ok", success=True, duration=10, turns=1)
        mock_provider.call = AsyncMock(return_value=mock_result)

        with patch("shannon_core.agents.providers.create_provider", return_value=mock_provider):
            await run_claude_prompt(prompt="p", repo_path="/tmp/repo")

        assert mock_provider.call.call_args.kwargs["max_turns"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/core/tests/test_runner.py::TestRunClaudePrompt::test_run_claude_prompt_forwards_max_turns_to_provider -v`
Expected: FAIL（`TypeError: run_claude_prompt() got an unexpected keyword argument 'max_turns'` 或 `KeyError 'max_turns'`）

- [ ] **Step 3: 实现 runner 透传** — `runner.py`：

(a) `run_claude_prompt` 签名（104–115 行）增参，在 `tool_audit_logger` 之后加：

```python
    max_turns: int | None = None,
```

(b) `provider.call(...)` 调用（161–168 行）增 `max_turns=max_turns,`：

```python
        result = await provider.call(
            prompt=prompt,
            cwd=repo_path,
            model_tier=model_tier,
            output_format=output_format,
            deliverables_subdir=deliverables_subdir,
            audit_logger=active_tool_logger,
            max_turns=max_turns,
        )
```

- [ ] **Step 4: 跑 runner 测试确认通过**

Run: `pytest packages/core/tests/test_runner.py::TestRunClaudePrompt -v`
Expected: PASS（含新 2 个 + 既有）

- [ ] **Step 5: 写失败测试（anthropic _build_options override）** — 在 `test_providers.py` 的 `TestAnthropicProviderBuildOptions` 类内追加：

```python
    def test_build_options_uses_max_turns_override(self, monkeypatch):
        """B2: _build_options(max_turns_override=N) → options.max_turns == N。"""
        monkeypatch.setenv("CLAUDE_MAX_TURNS", "200")  # 默认值
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        with patch.object(provider, "_is_adaptive_thinking_enabled", return_value=False):
            options = provider._build_options(
                cwd="/tmp", model="claude-sonnet-4-6", output_format=None, max_turns_override=500,
            )
        assert options.max_turns == 500

    def test_build_options_falls_back_to_env_when_override_none(self, monkeypatch):
        """override=None → 沿用 CLAUDE_MAX_TURNS env。"""
        monkeypatch.setenv("CLAUDE_MAX_TURNS", "200")
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        with patch.object(provider, "_is_adaptive_thinking_enabled", return_value=False):
            options = provider._build_options(
                cwd="/tmp", model="claude-sonnet-4-6", output_format=None, max_turns_override=None,
            )
        assert options.max_turns == 200
```

- [ ] **Step 6: 跑测试确认失败**

Run: `pytest packages/core/tests/agents/test_providers.py::TestAnthropicProviderBuildOptions::test_build_options_uses_max_turns_override -v`
Expected: FAIL（`_build_options() got an unexpected keyword argument 'max_turns_override'`）

- [ ] **Step 7: 实现 anthropic** — `providers_anthropic.py`：

(a) `_build_options` 签名（227–232 行）增参：

```python
    def _build_options(
        self,
        cwd: str,
        model: str,
        output_format: dict | None = None,
        max_turns_override: int | None = None,
    ) -> ClaudeAgentOptions:
```

(b) 把 `max_turns = int(os.getenv("CLAUDE_MAX_TURNS", "200"))`（242 行）替换为：

```python
        # max_turns: 外部 override 优先（vuln 专用），否则沿用 CLAUDE_MAX_TURNS。
        max_turns = max_turns_override or int(os.getenv("CLAUDE_MAX_TURNS", "200"))
```

(c) `call` 方法签名（78 行起）增 `max_turns: int | None = None`，并在调 `_build_options` 处传 `max_turns_override=max_turns`（找到 `call` 内 `_build_options(...)` 调用，加该 kwarg）。

- [ ] **Step 8: 实现 openai** — `providers_openai.py`：

(a) `call` 方法签名（135 行起）增 `max_turns: int | None = None`。

(b) `Runner.run_streamed(...)` 调用（151–156 行）的 `max_turns=self._max_turns()` 改为：

```python
                    max_turns=max_turns or self._max_turns(),
```

- [ ] **Step 9: 跑 provider 测试确认通过**

Run: `pytest packages/core/tests/agents/test_providers.py::TestAnthropicProviderBuildOptions -v`
Expected: PASS（含新 2 个 + 既有）

- [ ] **Step 10: 跑双引擎对齐套件确认未回归**

Run: `pytest packages/core/tests/agents/test_dual_engine_alignment.py -v`
Expected: PASS

- [ ] **Step 11: commit**

```bash
git add packages/core/src/shannon_core/agents/runner.py packages/core/src/shannon_core/agents/providers_anthropic.py packages/core/src/shannon_core/agents/providers_openai.py packages/core/tests/test_runner.py packages/core/tests/agents/test_providers.py
git commit -m "feat(agents): max_turns override 透传链 (run_claude_prompt→provider.call) (B2)"
```

---

## Task 6: B2 — vuln 专用 max_turns 上层（`_vuln_max_turns` + executor + run_agent 接线）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（新增 `_vuln_max_turns(agent_name)`；`run_agent` 传给 `executor.execute`）
- Modify: `packages/core/src/shannon_core/agents/executor.py`（`execute` 增 `max_turns` 参，透传 `run_claude_prompt`）
- Test: `packages/whitebox/tests/test_vuln_max_turns.py`（新建，测 `_vuln_max_turns` 纯函数）

**Interfaces:**
- Consumes: Task 5 的 `run_claude_prompt(..., max_turns=)` 签名
- Produces: vuln agent 经 `executor.execute(max_turns=500)`；非 vuln agent 传 None（零行为变更）

- [ ] **Step 1: 写失败测试** — 新建 `packages/whitebox/tests/test_vuln_max_turns.py`：

```python
"""B2: vuln agent 专用 max_turns 决策（纯函数）。

vuln agent 用 SHANNON_VULN_MAX_TURNS(默认 500);其他 agent 返回 None,
executor/run_claude_prompt 收到 None 时沿用全局 env 默认,行为零变更。
"""
import pytest

from shannon_whitebox.pipeline.activities import _vuln_max_turns


class TestVulnMaxTurns:
    def test_vuln_agent_returns_500_default(self, monkeypatch):
        monkeypatch.delenv("SHANNON_VULN_MAX_TURNS", raising=False)
        assert _vuln_max_turns("injection-vuln") == 500
        assert _vuln_max_turns("xss-vuln") == 500

    def test_non_vuln_agent_returns_none(self, monkeypatch):
        monkeypatch.delenv("SHANNON_VULN_MAX_TURNS", raising=False)
        assert _vuln_max_turns("pre-recon") is None
        assert _vuln_max_turns("recon") is None
        assert _vuln_max_turns("report") is None

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SHANNON_VULN_MAX_TURNS", "800")
        assert _vuln_max_turns("ssrf-vuln") == 800
```

> `_vuln_max_turns` 接受 `agent_name: str`（与 `agent_retry_category` 一致，后者取 `.value` 字符串）。若 import 路径因 `activities.py` 内 `@activity.defn` 装饰器有副作用而报错，改为 `from shannon_whitebox.pipeline.activities import _vuln_max_turns` 仍应可用（纯函数定义在模块顶层）。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/whitebox/tests/test_vuln_max_turns.py -v`
Expected: FAIL（`ImportError: cannot import name '_vuln_max_turns'`）

- [ ] **Step 3: 实现 `_vuln_max_turns`** — 在 `activities.py` 顶部 import 区确认有 `from shannon_core.models.retry import retry_for, agent_retry_category`（`run_agent` 已用 `agent_retry_category`，:105）。在 `run_agent` 函数**之前**新增：

```python
def _vuln_max_turns(agent_name: str) -> int | None:
    """vuln agent 用专用 max_turns(SHANNON_VULN_MAX_TURNS,默认 500);其他返回 None。

    返回 None 时,executor → run_claude_prompt → provider 沿用各引擎全局 env 默认
    (CLAUDE_MAX_TURNS / SHANNON_OPENAI_MAX_TURNS = 200),行为零变更。
    B2: 仅 vuln 单独配,不污染 pre-recon/recon/report。
    """
    if agent_retry_category(agent_name) == "vuln":
        return int(os.getenv("SHANNON_VULN_MAX_TURNS", "500"))
    return None
```

> 先在 `activities.py` import 区补 `import os`：当前文件第 1–3 行是 `import json / logging / time`，**未导入 os**。在 `import time`（第 3 行）之后加一行 `import os`。`agent_retry_category` 已在第 13 行 import，可直接用。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest packages/whitebox/tests/test_vuln_max_turns.py -v`
Expected: PASS（3 函数）

- [ ] **Step 5: executor.execute 增参透传** — `executor.py`：

(a) `execute` 签名（47–61 行）在 `tool_audit_logger` 之后增：

```python
        max_turns: int | None = None,
```

(b) `run_claude_prompt(...)` 调用（100–109 行）增 `max_turns=max_turns,`：

```python
        result = await run_claude_prompt(
            prompt=prompt,
            repo_path=str(repo),
            model_tier=defn.model_tier,
            api_key=api_key,
            deliverables_subdir=str(deliverables.relative_to(repo)) if deliverables.is_relative_to(repo) else None,
            structured_output_schema=structured_output_schema,
            audit_logger=audit_logger,
            tool_audit_logger=tool_audit_logger,
            max_turns=max_turns,
        )
```

- [ ] **Step 6: run_agent 接线** — `activities.py` 的 `run_agent` 中，`executor.execute(...)` 调用（125–136 行）增 `max_turns=_vuln_max_turns(agent_name.value),`：

```python
        metrics = await executor.execute(
            agent_name=agent_name,
            repo_path=str(repo),
            web_url=input.web_url,
            deliverables_path=str(deliverables),
            config_path=input.config_path,
            api_key=input.api_key,
            pipeline_testing=input.pipeline_testing_mode,
            prompt_override=input.prompt_override,
            prompt_variables=prompt_variables,
            tool_audit_logger=tool_audit_logger,
            max_turns=_vuln_max_turns(agent_name.value),
        )
```

- [ ] **Step 7: 跑既有 whitebox agent 测试确认未回归**

Run: `pytest packages/whitebox/tests/test_run_agent_framework_injection.py -v`
Expected: PASS（既有断言：activities 不传确定性 framework_analysis 给 LLM-track agent —— 本 task 仅加 max_turns，不影响该不变量）

- [ ] **Step 8: commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/core/src/shannon_core/agents/executor.py packages/whitebox/tests/test_vuln_max_turns.py
git commit -m "feat(whitebox): vuln agent 专用 max_turns(500) via _vuln_max_turns (B2)"
```

---

## Task 7: B2 — 观测：AgentEndResult 增 num_turns + run_agent 传入

**Files:**
- Modify: `packages/core/src/shannon_core/models/audit.py`（`AgentEndResult` 增 `num_turns`）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（`run_agent` 三处 `AgentEndResult(...)` 传 `num_turns`）
- Test: `packages/core/tests/test_audit_models.py`（或现有 audit 测试文件；若不存在则新建）

**Interfaces:**
- Consumes: `AgentMetrics.num_turns`（executor.py:142，已存在 = `result.turns`）
- Produces: `AgentEndResult.num_turns`（每个 vuln agent 完成记录 turn 消耗；未来调 `SHANNON_VULN_MAX_TURNS` 有数据）

- [ ] **Step 1: 写失败测试** — 在 `packages/core/tests/test_audit_types.py`（已存在，含 AgentEndResult 断言）追加。先 Read 该文件确认 import 区已有 `from shannon_core.models.audit import AgentEndResult`（若无则补）：

```python
def test_agent_end_result_has_num_turns_field():
    """B2 观测:AgentEndResult 记录 turn 消耗(默认 None,向后兼容)。"""
    r = AgentEndResult(success=True, duration_ms=100, cost_usd=0.0, attempt_number=1)
    assert r.num_turns is None  # 默认 None,既有调用零破坏
    r2 = AgentEndResult(success=True, duration_ms=100, cost_usd=0.0, attempt_number=1, num_turns=42)
    assert r2.num_turns == 42
```

> import：`from shannon_core.models.audit import AgentEndResult`。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/core/tests/test_audit_types.py::test_agent_end_result_has_num_turns_field -v`（路径按 Step 1 确认）
Expected: FAIL（`TypeError: ... unexpected keyword argument 'num_turns'` 或 AttributeError）

- [ ] **Step 3: AgentEndResult 增字段** — `audit.py` 的 `AgentEndResult`（6–14 行）增：

```python
class AgentEndResult(BaseModel):
    success: bool
    duration_ms: int
    cost_usd: float
    attempt_number: int = 1
    model: str | None = None
    error: str | None = None
    is_final_attempt: bool = True
    checkpoint: str | None = None
    num_turns: int | None = None  # B2 观测:agent turn 消耗(来自 AgentMetrics.num_turns)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest packages/core/tests/test_audit_types.py::test_agent_end_result_has_num_turns_field -v`
Expected: PASS

- [ ] **Step 5: run_agent 三处传入** — `activities.py` 的 `run_agent`，三处 `AgentEndResult(...)`（成功 :138、PentestError :149、Exception :161）增 `num_turns=`：
- 成功分支（:138）有 `metrics`，传 `num_turns=metrics.num_turns`
- 两处 except 分支没有 metrics（agent 失败前未返回），传 `num_turns=None`（即省略，用默认）

成功分支改为：

```python
        await session.end_agent(agent_name.value, AgentEndResult(
            success=True,
            duration_ms=metrics.duration_ms,
            cost_usd=metrics.cost_usd or 0.0,
            attempt_number=attempt,
            model=metrics.model,
            num_turns=metrics.num_turns,
        ))
```

> 两处 except 分支无需改动（`num_turns` 默认 None）。撞 max_turns 的 case 已被 `result.success=False → executor raise PentestError → except → session.log_error`（activities.py:152/164）覆盖，失败原因经 `log_error` surface 到 live display。

- [ ] **Step 6: 跑既有 agent activity 测试确认未回归**

Run: `pytest packages/whitebox/tests/test_run_agent_framework_injection.py packages/whitebox/tests/test_cli.py -v -k "agent or Agent"`
Expected: PASS（AgentEndResult 新增可选字段，既有构造零破坏）

> 若上述测试因 fixture 重而难跑，至少跑 `packages/core/tests/` 下涉及 AgentEndResult 的测试文件（`grep -rl "AgentEndResult" packages/core/tests/` 后逐个跑）。

- [ ] **Step 7: commit**

```bash
git add packages/core/src/shannon_core/models/audit.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/core/tests/test_audit_types.py
git commit -m "feat(audit): AgentEndResult 增 num_turns 观测字段 + run_agent 传入 (B2)"
```

---

## 完成验收（全部 task 后）

- [ ] 跑本计划涉及的全部测试文件（非全量）：

```bash
pytest packages/core/tests/prompts/test_vuln_injection_prompt.py \
       packages/core/tests/prompts/test_vuln_xss_prompt.py \
       packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py \
       packages/core/tests/test_retry_profiles.py \
       packages/core/tests/test_runner.py \
       packages/core/tests/agents/test_providers.py \
       packages/core/tests/agents/test_dual_engine_alignment.py \
       packages/whitebox/tests/test_vuln_max_turns.py \
       packages/whitebox/tests/test_run_agent_framework_injection.py \
       -v
```
Expected: 全绿。

- [ ] 人工冒烟（记为 follow-up，非本计划阻塞项）：真实 whitebox 跑一次 vuln 阶段，观察 `num_turns` 是否落 audit log、vuln agent 是否用 ~500 turn 上限。此条与 memory 中多条"待人工冒烟"一致，不阻塞 merge。
