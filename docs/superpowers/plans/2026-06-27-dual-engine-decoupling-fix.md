# 双引擎解耦修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 shannon-py 双引擎（claude-agent-sdk / openai-agents）的统一返回类型 `ClaudeRunResult` 在两引擎下语义一致，消除 OpenAI 侧三处「静默失真」真 bug，并加双引擎对齐测试护栏 + openai 真机冒烟闭环。

**Architecture:** 只动 `packages/core/src/shannon_core/agents/` 集成层。`BaseProvider`(ABC) 契约硬化（两 provider 都继承）→ OpenAI 侧 `map_run_result`/`_handle_error`/`build_agent` 补齐 `success`/`error_code`/`structured_output` 语义 → 新建双引擎对齐测试锁定不变量 → 真机冒烟验证。不改业务侧、prompt、确定性层、合并器。

**Tech Stack:** Python 3.13 + uv + pytest（asyncio）+ openai-agents（`agents` 包）+ claude-agent-sdk + Pydantic v2。

**上游 spec:** `docs/superpowers/specs/2026-06-27-dual-engine-decoupling-fix-design.md`（commit e0158d7，已通过用户 review）。

## Global Constraints

- **分支：** `feat/fork-py`（非默认分支，直接提交）。
- **只跑改动相关测试子集：** 全套 pytest 会 hang（Temporal/网络慢测试，见 memory `pytest-whitebox-hang`）。每个任务只跑该任务触及的测试文件，命令统一前缀 `uv run pytest --no-header -x`。**禁止** `uv run pytest`（无参数跑全套）。
- **只动 `agents/` 集成层：** 不碰 `packages/whitebox/`、`packages/blackbox/`、`packages/multi/` 业务侧代码，不碰 prompt、确定性层（`code_index/`）、合并器（`dual_track_merger.py`）。
- **SDK 隔离不变量：** `claude_agent_sdk` / `openai` / `agents` 的 import 只能在 `packages/core/src/shannon_core/agents/` 目录内。本次改动不新增跨目录 SDK import。
- **error_code：retryable 严格对齐 + 字符串语义化**（spec §1.4：error_code 上游消费弱，Temporal retry 靠异常类型不靠它；Pre-Flight 裁定放宽）。`retryable` 必须与 `classify_error_for_temporal` 真值对齐（max_turns→False / rate_limit→True / timeout→True / 50x→True / auth→False / permission→False）；`error_code` 字符串语义化、**允许两引擎差异**（OpenAI 用 `"ExecutionLimitError"`/`"RateLimitError"`/`"TimeoutError"`/`"ServiceUnavailableError"`/`"AuthenticationError"`/`"PermissionError"`/`"AgentExecutionError"`；Claude 侧 rate limit 走 classify 既有的 `BillingError`）。对齐测试只锁 `retryable` 一致，不强求 error_code 字符串逐字相同。
- **不重命名** `run_claude_prompt` / `ClaudeRunResult`（CLAUDE.md 全程沿用，改名是独立大改，超出本计划）。
- **每个 task 结尾必须 commit**（frequent commits）。

## File Structure

| 文件 | 责任 | 本计划动作 |
|---|---|---|
| `providers_anthropic.py` | Claude 引擎 provider | A1：继承 BaseProvider |
| `providers.py` | `BaseProvider`(ABC) 抽象层 | A2：`call` docstring 不变量 |
| `runner.py` | `ClaudeRunResult` dataclass | A2：字段语义注释 |
| `openai_result_mapper.py` | openai `RunResult`→`ClaudeRunResult` 纯函数 | B1：max_turns 语义；B2：dict final 处理；C1：cost 注释 |
| `providers_openai.py` | OpenAI 引擎 provider | B2：`build_agent` 接 output_type；B3：`_handle_error` 补 error_code |
| `tests/agents/test_dual_engine_alignment.py` | 双引擎对齐测试护栏 | D：新建 |
| `tests/agents/test_providers.py` | provider 单元测试 | 各 task 同步断言 |
| `tests/agents/test_openai_result_mapper.py` | mapper 单元测试 | B1/B2 同步断言 |
| `scripts/validate_openai_task_probe.py` | openai 真机冒烟脚本 | E：跑（已就绪，122 行） |

---

### Task 1: 契约硬化 — AnthropicProvider 继承 BaseProvider + 不变量文档化（A1 + A2 + C2）

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers_anthropic.py:10-21`（import）、`:37-42`（类声明 + `__init__`）
- Modify: `packages/core/src/shannon_core/agents/providers.py:59-90`（`BaseProvider.call` docstring）
- Modify: `packages/core/src/shannon_core/agents/runner.py:76-88`（`ClaudeRunResult` 字段注释）
- Test: `packages/core/tests/agents/test_providers.py`（加到 `TestCreateProvider` 类后，新测试函数）

**Interfaces:**
- Consumes: `BaseProvider`（`providers.py:59`，已有）
- Produces: `AnthropicProvider` 成为 `BaseProvider` 子类（`isinstance` 为 True）；`ClaudeRunResult` 字段语义不变量以 docstring 形式确立（供后续 task 的测试断言引用）

- [ ] **Step 1: 写失败测试（isinstance 契约锁定）**

在 `packages/core/tests/agents/test_providers.py` 的 `TestCreateProvider` 类之后（约 `:325` 行之后）新增测试函数：

```python
class TestBaseProviderContract:
    """D3: 两 provider 都必须是 BaseProvider 的实例（A1 契约硬化锁定）。"""

    def test_anthropic_provider_is_baseprovider_instance(self):
        from shannon_core.agents.providers import BaseProvider
        from shannon_core.agents.providers_anthropic import AnthropicProvider
        from shannon_core.agents.runner import ProviderConfig
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api", api_key="k"))
        assert isinstance(provider, BaseProvider), "AnthropicProvider 必须继承 BaseProvider"

    def test_openai_provider_is_baseprovider_instance(self):
        from shannon_core.agents.providers import BaseProvider
        from shannon_core.agents.providers_openai import OpenAIProvider
        from shannon_core.agents.runner import ProviderConfig
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k"))
        assert isinstance(provider, BaseProvider)

    def test_anthropic_provider_inherits_init_from_base(self):
        """A1: super().__init__ 应设置 config/type，不再手动赋值。"""
        from shannon_core.agents.providers_anthropic import AnthropicProvider
        from shannon_core.agents.runner import ProviderConfig
        cfg = ProviderConfig(type="anthropic_api", api_key="k", base_url="http://x")
        provider = AnthropicProvider(cfg)
        assert provider.config is cfg
        assert provider.type == "anthropic_api"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest --no-header -x packages/core/tests/agents/test_providers.py::TestBaseProviderContract -v`
Expected: FAIL — `test_anthropic_provider_is_baseprovider_instance` 断言失败（`AnthropicProvider` 当前未继承 `BaseProvider`，`isinstance` 为 False）。

- [ ] **Step 3: 实现 A1 — AnthropicProvider 继承 BaseProvider**

修改 `packages/core/src/shannon_core/agents/providers_anthropic.py`。

3a. 在 import 块（`:10-21` 附近）加 `BaseProvider` import（与 `providers_openai.py:24` 同款，已验证不构成破坏性循环）：

```python
from .providers import BaseProvider
```

3b. 改类声明 + `__init__`（`:37-42`）：

```python
class AnthropicProvider(BaseProvider):
    """使用 Claude Agent SDK 的 Provider。

    A1（2026-06-27 双引擎解耦修复）：继承 BaseProvider，使 ABC 的 @abstractmethod
    约束与 isinstance 契约对两引擎同时生效（此前为鸭子类型，isinstance 为 False）。
    """

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
```

（删除原 `self.config = config` / `self.type = config.type` 两行——`BaseProvider.__init__`（`providers.py:62-63`）已做。保留既有的 `_is_retryable_error` override（`:463`），override 合法。）

- [ ] **Step 4: 实现 A2 + C2 — 不变量文档化**

4a. `packages/core/src/shannon_core/agents/runner.py:76`，给 `ClaudeRunResult` 加字段语义 docstring（在 `class ClaudeRunResult:` 与第一字段之间插入）：

```python
@dataclass
class ClaudeRunResult:
    """provider 统一返回类型。字段语义不变量（A2，两引擎 provider 实现义务）：

    - success: 必须真实反映完成/失败。跑满 max_turns / 异常 → False（不得恒 True）。
    - error_code + retryable: 必须分类。成功路径 (None, True)；max_turns →
      ("ExecutionLimitError", False)；限流 → ("RateLimitError", True)；
      超时 → ("TimeoutError", True)；鉴权 → ("AuthenticationError", False)。
      字符串与 models/errors.py:classify_error_for_temporal 对齐。
    - structured_output: 调用方传入 output_format 时，provider 有义务产出非 None
      （走原生结构化输出，不靠 json.loads 兜底）。
    - cost: best-effort 归集。不支持计费的 provider 允许 0.0（spending-cap 兜底
      不受影响——见 utils/billing.is_spending_cap_behavior 的 cost>0→False 早退）。
    - spending-cap 检测: best-effort，provider 可差异。openai 复用 provider 无关的
      utils/billing.is_spending_cap_behavior，不补 Claude CLI 专属的 dispatcher 层（C2）。
    """
    text: str = ""
```

4b. `packages/core/src/shannon_core/agents/providers.py:67`，给 `BaseProvider.call` 的 docstring 末尾追加一行（在 `Returns:` 之后）：

```python
        Returns:
            ClaudeRunResult: 执行结果（字段语义不变量见 runner.ClaudeRunResult docstring）
        """
```

- [ ] **Step 5: 跑测试验证通过**

Run: `uv run pytest --no-header -x packages/core/tests/agents/test_providers.py::TestBaseProviderContract -v`
Expected: PASS（3 个测试全绿）。

再回归 AnthropicProvider 既有测试不受影响：
Run: `uv run pytest --no-header -x packages/core/tests/agents/test_providers.py::TestAnthropicProvider packages/core/tests/agents/test_providers.py::TestAnthropicProviderBuildOptions -v`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/agents/providers_anthropic.py \
        packages/core/src/shannon_core/agents/providers.py \
        packages/core/src/shannon_core/agents/runner.py \
        packages/core/tests/agents/test_providers.py
git commit -m "refactor(agents): AnthropicProvider 继承 BaseProvider + ClaudeRunResult 不变量文档化

A1: AnthropicProvider 此前为鸭子类型（isinstance(x, BaseProvider)=False），
改为继承 BaseProvider + super().__init__，使 ABC 约束对两引擎同时生效。
A2/C2: ClaudeRunResult 字段语义不变量 + spending-cap best-effort 文档化。"
```

---

### Task 2: B1 — map_run_result 的 max_turns 语义对齐 + C1 cost 注释

**Files:**
- Modify: `packages/core/src/shannon_core/agents/openai_result_mapper.py:25-55`（`map_run_result`）+ `:11-12`（cost 注释）
- Test: `packages/core/tests/agents/test_openai_result_mapper.py`（`test_map_stop_reason_max_turns`）
- Test: `packages/core/tests/agents/test_providers.py:566`（`test_call_handles_max_turns`）

**Interfaces:**
- Consumes: `ClaudeRunResult`（`runner.py:76`，字段 `success`/`error_code`/`retryable`/`stop_reason`）
- Produces: `map_run_result` 在 `stop_reason=="max_turns"` 时产出 `success=False, error_code="ExecutionLimitError", retryable=False`（对齐 Claude `providers_anthropic.py:389-390`）

- [ ] **Step 1: 写/改失败测试**

1a. `packages/core/tests/agents/test_openai_result_mapper.py` 的 `test_map_stop_reason_max_turns`（约 `:30`）改为：

```python
def test_map_stop_reason_max_turns():
    """B1: max_turns → success=False + error_code=ExecutionLimitError + retryable=False。"""
    rr = _run_result("partial", _usage(1, 1))
    res = map_run_result(rr, duration_ms=10, model="m", turns=200, stop_reason="max_turns")
    assert res.stop_reason == "max_turns"
    assert res.success is False
    assert res.error_code == "ExecutionLimitError"
    assert res.retryable is False
```

1b. `packages/core/tests/agents/test_providers.py:566` 的 `test_call_handles_max_turns` 末尾（`assert res.stop_reason == "max_turns"` 之后）追加：

```python
        # B1: max_turns 必须反映为失败（对齐 Claude subtype=error_max_turns）
        assert res.success is False
        assert res.error_code == "ExecutionLimitError"
        assert res.retryable is False
```

同时在该文件加一个正常路径不受影响的回归断言（确保 `stop_reason=None` 时 `success=True` 不变）——在 `test_map_plain_text`（约 `:22`）末尾已有 `assert res.success is True`，无需改动，仅需跑通确认。

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest --no-header -x packages/core/tests/agents/test_openai_result_mapper.py::test_map_stop_reason_max_turns packages/core/tests/agents/test_providers.py::TestOpenAIProvider::test_call_handles_max_turns -v`
Expected: FAIL — `assert res.success is False` 失败（当前 `map_run_result` 恒 `success=True`）。

- [ ] **Step 3: 实现 B1 + C1**

修改 `packages/core/src/shannon_core/agents/openai_result_mapper.py`。

3a. 改 `:11-12` cost 注释（C1）：

```python
# GLM/openai endpoint 不支持计费归集，cost 留 0.0（不假估算），以 provider 账单为准。
# 此 0 值对 spending-cap 兜底无害：utils/billing.is_spending_cap_behavior 的
# cost>0→False 早退逻辑意味着 cost=0 时继续走 text 关键词匹配（C1，已核验）。
```

3b. 改 `map_run_result`（`:25-55`），在 return 前基于 `stop_reason` 判定失败语义：

```python
def map_run_result(
    run_result: RunResult,
    *,
    duration_ms: int,
    model: str,
    turns: int,
    stop_reason: str | None = None,
    output_format: dict | None = None,
) -> ClaudeRunResult:
    final = getattr(run_result, "final_output", "")
    text = final if isinstance(final, str) else str(final)
    tokens = _usage_from(run_result)

    structured_output: Any | None = None
    if output_format and text:
        try:
            structured_output = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            structured_output = final if not isinstance(final, str) else None

    # B1: max_turns 对齐 Claude subtype=error_max_turns → 失败 + 不可重试（spec §1.2）
    is_max_turns = stop_reason == "max_turns"

    return ClaudeRunResult(
        text=text,
        success=not is_max_turns,
        duration=duration_ms,
        turns=turns,
        cost=0.0,  # 见文件头注释（C1）
        model=model,
        structured_output=structured_output,
        tokens=tokens,
        stop_reason=stop_reason,
        error_code="ExecutionLimitError" if is_max_turns else None,
        retryable=False if is_max_turns else True,
    )
```

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest --no-header -x packages/core/tests/agents/test_openai_result_mapper.py packages/core/tests/agents/test_providers.py::TestOpenAIProvider -v`
Expected: PASS（`test_map_stop_reason_max_turns`、`test_call_handles_max_turns`、`test_map_plain_text` 正常路径 `success=True` 不受影响、`test_call_maps_result_and_audits` 正常路径不受影响，全绿）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/openai_result_mapper.py \
        packages/core/tests/agents/test_openai_result_mapper.py \
        packages/core/tests/agents/test_providers.py
git commit -m "fix(agents): openai map_run_result max_turns → success=False/error_code=ExecutionLimitError

B1: 此前 map_run_result 恒 success=True，导致 openai 引擎下跑满 max_turns
被当成功（活动不重试/报告标 OK/dashboard 标 done）。现对齐 Claude
subtype=error_max_turns 语义。C1: cost=0 注释升级（spending-cap 兜底无害）。"
```

---

### Task 3: B3 — _handle_error 补 error_code 分类（DRY 重构 _classify_error）

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers_openai.py:164-195`（`_handle_error` + `_is_retryable_error`）
- Test: `packages/core/tests/agents/test_providers.py::TestOpenAIProvider`（新增 `test_classify_error_*` + 改 `_handle_error` 断言）

**Interfaces:**
- Consumes: 无（self-contained）
- Produces: `_classify_error(error) -> tuple[str | None, bool]`（`(error_code, retryable)`），供 `_is_retryable_error` 与 `_handle_error` 共用（DRY）

- [ ] **Step 1: 写失败测试**

在 `packages/core/tests/agents/test_providers.py` 的 `TestOpenAIProvider` 类内（`test_is_retryable_classifies_rate_limit` 之后，约 `:595`）新增：

```python
    def test_classify_error_rate_limit(self):
        from shannon_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k"))
        code, retryable = provider._classify_error(Exception("Rate limit exceeded"))
        assert code == "RateLimitError"
        assert retryable is True

    def test_classify_error_timeout(self):
        from shannon_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k"))
        code, retryable = provider._classify_error(TimeoutError("request timed out"))
        assert code == "TimeoutError"
        assert retryable is True

    def test_classify_error_auth(self):
        from shannon_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k"))
        code, retryable = provider._classify_error(Exception("invalid_api_key (401)"))
        assert code == "AuthenticationError"
        assert retryable is False

    def test_classify_error_permission(self):
        from shannon_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k"))
        code, retryable = provider._classify_error(Exception("permission denied (403)"))
        assert code == "PermissionError"
        assert retryable is False

    def test_classify_error_default_agent_execution(self):
        from shannon_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k"))
        code, retryable = provider._classify_error(Exception("some transient error"))
        assert code == "AgentExecutionError"
        assert retryable is True

    @pytest.mark.asyncio
    async def test_handle_error_sets_error_code(self, monkeypatch, tmp_path):
        """B3: _handle_error 必须填 error_code（此前恒 None）。"""
        from unittest.mock import MagicMock
        from shannon_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k", medium_model="m"))
        res = provider._handle_error(Exception("Rate limit exceeded"), duration=100, model="m")
        assert res.success is False
        assert res.error_code == "RateLimitError"
        assert res.retryable is True
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest --no-header -x packages/core/tests/agents/test_providers.py::TestOpenAIProvider -k "classify_error or handle_error_sets" -v`
Expected: FAIL — `AttributeError: 'OpenAIProvider' object has no attribute '_classify_error'`。

- [ ] **Step 3: 实现 B3 — 抽 `_classify_error`，`_handle_error` 填 error_code**

修改 `packages/core/src/shannon_core/agents/providers_openai.py:164-195`，把 `_is_retryable_error` 的分类逻辑抽成 `_classify_error`，二者共用：

```python
    def _handle_error(self, error: Exception, duration: int, model: str) -> ClaudeRunResult:
        error_code, retryable = self._classify_error(error)
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

    def _classify_error(self, error: Exception) -> tuple[str | None, bool]:
        """分类异常 → (error_code, retryable)。error_code 字符串与
        models/errors.py:classify_error_for_temporal 对齐（B3）。

        BaseProvider._is_retryable_error 只匹配自定义异常类；openai/httpx/agents
        抛的是普通异常，需基于消息和类型名分类。
        """
        error_msg = str(error).lower()
        error_type = type(error).__name__.lower()
        # 速率限制 → 可重试
        if "rate" in error_msg or "limit" in error_msg or error_type == "ratelimiterror":
            return ("RateLimitError", True)
        # 超时 → 可重试
        if "timeout" in error_msg or error_type in ("timeouterror", "timeoutexception", "connecttimeout"):
            return ("TimeoutError", True)
        # 服务不可用 → 可重试
        if "unavailable" in error_msg or "503" in error_msg or "502" in error_msg or "504" in error_msg or error_type == "serviceunavailable":
            return ("ServiceUnavailableError", True)
        # 认证 → 不可重试
        if "auth" in error_msg or "401" in error_msg or error_type == "authenticationerror":
            return ("AuthenticationError", False)
        # 权限 → 不可重试
        if "permission" in error_msg or "403" in error_msg or error_type == "permissiondeniederror":
            return ("PermissionError", False)
        # 默认可重试（与旧行为一致）
        return ("AgentExecutionError", True)

    def _is_retryable_error(self, error: Exception) -> bool:
        """判断错误是否可重试（BaseProvider 契约，委托 _classify_error，DRY）。"""
        return self._classify_error(error)[1]
```

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest --no-header -x packages/core/tests/agents/test_providers.py::TestOpenAIProvider -v`
Expected: PASS（新增 6 个测试 + 既有 `test_is_retryable_classifies_rate_limit` 仍绿——`_is_retryable_error` 委托 `_classify_error`，行为不变）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/providers_openai.py \
        packages/core/tests/agents/test_providers.py
git commit -m "fix(agents): openai _handle_error 补 error_code 分类（_classify_error DRY 重构）

B3: 此前 OpenAIProvider._handle_error 不设 error_code（恒 None），workflow state
错误分类粗化。抽 _classify_error(error)->(error_code,retryable)，与
classify_error_for_temporal 字符串对齐；_is_retryable_error 委托它（行为不变）。"
```

---

### Task 4: B2 — build_agent 走原生 output_type 强制结构化输出（RawJsonSchemaOutputSchema）

**Files:**
- Create: `packages/core/src/shannon_core/agents/openai_output_schema.py`（`RawJsonSchemaOutputSchema`）
- Modify: `packages/core/src/shannon_core/agents/providers_openai.py:74-82`（`build_agent`）+ import 块
- Modify: `packages/core/src/shannon_core/agents/openai_result_mapper.py:34-43`（处理 dict `final_output`）
- Test: `packages/core/tests/agents/test_openai_output_schema.py`（新建）
- Test: `packages/core/tests/agents/test_providers.py::TestOpenAIProvider::test_build_agent_wires_chatcompletions_model_and_tools`（同步）

**Interfaces:**
- Consumes: `output_format: dict | None`（Claude 风格 JSON Schema，来自 `call()` 参数）
- Produces: `RawJsonSchemaOutputSchema(AgentOutputSchemaBase)`（持有原始 JSON Schema，绕过 Pydantic 建模）；`build_agent(model, output_format)` 在 `output_format` 非空时给 `Agent(output_type=RawJsonSchemaOutputSchema(output_format))`

**降级策略（spec §5）：** 若真机（Task 6）验证 GLM openai endpoint 不支持 `response_format` json_schema → final_output 仍走 `map_run_result` 的 `json.loads` 兜底（本 task 保留），结构化输出退化为 best-effort，并在 Task 6 记录 follow-up。代码层面本 task 无需分支——`RawJsonSchemaOutputSchema.validate_json` 用 `json.loads`，GLM 输出纯 JSON 即生效。

- [ ] **Step 1: 写失败测试（RawJsonSchemaOutputSchema 单元）**

新建 `packages/core/tests/agents/test_openai_output_schema.py`：

```python
from shannon_core.agents.openai_output_schema import RawJsonSchemaOutputSchema


def test_is_plain_text_false_when_schema_given():
    schema = {"type": "object", "properties": {"k": {"type": "string"}}}
    s = RawJsonSchemaOutputSchema(schema)
    assert s.is_plain_text() is False


def test_json_schema_returns_raw_schema_unchanged():
    """B2: 直接持有 Claude 风格 JSON Schema，不 round-trip Pydantic。"""
    schema = {"type": "object", "properties": {"verdict": {"type": "string"}}, "required": ["verdict"]}
    s = RawJsonSchemaOutputSchema(schema)
    assert s.json_schema() == schema


def test_is_strict_json_schema_false_for_glm_compat():
    """GLM 第三方 endpoint 用 non-strict，避免 strict 模式拒收。"""
    s = RawJsonSchemaOutputSchema({"type": "object"})
    assert s.is_strict_json_schema() is False


def test_validate_json_parses_valid_json():
    s = RawJsonSchemaOutputSchema({"type": "object"})
    assert s.validate_json('{"k": "v"}') == {"k": "v"}


def test_validate_json_raises_on_invalid():
    import pytest
    s = RawJsonSchemaOutputSchema({"type": "object"})
    with pytest.raises(Exception):
        s.validate_json("not json")


def test_name_is_stable():
    s = RawJsonSchemaOutputSchema({"type": "object"})
    assert isinstance(s.name(), str)
    assert len(s.name()) > 0
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest --no-header -x packages/core/tests/agents/test_openai_output_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.agents.openai_output_schema'`。

- [ ] **Step 3: 实现 RawJsonSchemaOutputSchema**

新建 `packages/core/src/shannon_core/agents/openai_output_schema.py`：

```python
"""B2（2026-06-27 双引擎解耦修复）：openai-agents 结构化输出适配器。

问题：openai-agents 的 Agent(output_type=...) 只接受 Python type 或
AgentOutputSchemaBase，不吃 Claude 风格的 JSON Schema dict。providers_openai
此前的 build_agent 丢弃 output_format，structured_output 纯靠 json.loads 兜底，
模型输出非纯 JSON 时 auth verdict 静默失败。

解法：RawJsonSchemaOutputSchema 直接持有原始 JSON Schema dict，实现
AgentOutputSchemaBase 的 5 个抽象方法，绕过 Pydantic 建模（无 round-trip 损耗）。
openai-agents 会把 json_schema() 透传给 OpenAI response_format，约束模型输出。

降级：GLM 若不支持 response_format json_schema，validate_json 仍用 json.loads，
final_output 走 map_run_result 兜底（best-effort，见 spec §5）。
"""
from __future__ import annotations

import json
from typing import Any

from agents import AgentOutputSchemaBase


class RawJsonSchemaOutputSchema(AgentOutputSchemaBase):
    """持有原始 JSON Schema 的 AgentOutputSchemaBase 实现（non-strict）。"""

    def __init__(self, schema: dict[str, Any]):
        self._schema = schema

    def is_plain_text(self) -> bool:
        return False

    def is_strict_json_schema(self) -> bool:
        # GLM 第三方 endpoint 用 non-strict，避免 strict 模式对额外字段拒收
        return False

    def json_schema(self) -> dict[str, Any]:
        return self._schema

    def name(self) -> str:
        return "shannon_raw_json_schema"

    def validate_json(self, json_str: str) -> Any:
        # best-effort：仅解析 JSON，不做 schema 完整校验（GLM 已被 response_format 约束）
        return json.loads(json_str)
```

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest --no-header -x packages/core/tests/agents/test_openai_output_schema.py -v`
Expected: PASS（6 个测试全绿）。

- [ ] **Step 5: 写失败测试（build_agent 接入）**

在 `packages/core/tests/agents/test_providers.py::TestOpenAIProvider` 内，改/加 `test_build_agent_wires_chatcompletions_model_and_tools`（约 `:527`）——保留原断言，追加 output_type 断言：

```python
    def test_build_agent_wires_chatcompletions_model_and_tools(self):
        from shannon_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k", medium_model="m"))
        agent = provider.build_agent("m", output_format=None)
        assert agent.name == "shannon-openai-agent"
        # 原：工具集非空
        assert len(agent.tools) > 0

    def test_build_agent_wires_output_type_when_output_format_given(self):
        """B2: output_format 非空时，Agent 必须带 output_type（强制结构化输出）。"""
        from shannon_core.agents.openai_output_schema import RawJsonSchemaOutputSchema
        from shannon_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k", medium_model="m"))
        schema = {"type": "object", "properties": {"verdict": {"type": "string"}}, "required": ["verdict"]}
        agent = provider.build_agent("m", output_format=schema)
        assert agent.output_type is not None
        assert isinstance(agent.output_type, RawJsonSchemaOutputSchema)
        assert agent.output_type.json_schema() == schema

    def test_build_agent_output_type_none_when_no_output_format(self):
        """B2: output_format 为 None 时，output_type 必须为 None（兼容纯文本路径）。"""
        from shannon_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k", medium_model="m"))
        agent = provider.build_agent("m", output_format=None)
        assert agent.output_type is None
```

- [ ] **Step 6: 跑测试验证失败**

Run: `uv run pytest --no-header -x packages/core/tests/agents/test_providers.py::TestOpenAIProvider::test_build_agent_wires_output_type_when_output_format_given -v`
Expected: FAIL — `assert agent.output_type is not None` 失败（当前 `build_agent` 丢弃 `output_format`，`output_type` 恒 None）。

- [ ] **Step 7: 实现 build_agent 接入 output_type**

修改 `packages/core/src/shannon_core/agents/providers_openai.py`。

7a. import 块加：

```python
from .openai_output_schema import RawJsonSchemaOutputSchema
```

7b. 改 `build_agent`（`:74-82`）：

```python
    def build_agent(self, model: str, output_format: dict | None) -> Agent:
        client = self._get_client()
        chat_model = OpenAIChatCompletionsModel(model=model, openai_client=client)
        # B2: output_format 非空时强制结构化输出（对齐 Claude options.output_format）
        output_type = RawJsonSchemaOutputSchema(output_format) if output_format else None
        return Agent(
            name="shannon-openai-agent",
            instructions=None,  # prompt 已含 system prompt，整段当 user input
            tools=build_tools(),
            model=chat_model,
            model_settings=ModelSettings(include_usage=True),
            output_type=output_type,
        )
```

- [ ] **Step 8: 改 map_run_result 处理 dict final_output**

当 `output_type` 生效时，openai-agents 的 `final_output` 是 `validate_json` 返回的 dict（非 str）。修改 `packages/core/src/shannon_core/agents/openai_result_mapper.py:34-43`：

```python
    final = getattr(run_result, "final_output", "")
    # B2: 结构化输出路径下 final_output 可能是 dict（RawJsonSchemaOutputSchema.validate_json 返回）
    if isinstance(final, str):
        text = final
    else:
        text = json.dumps(final, ensure_ascii=False) if not isinstance(final, (int, float, bool)) else str(final)
    tokens = _usage_from(run_result)

    structured_output: Any | None = None
    if output_format:
        # 结构化输出：优先用已解析的 dict；退化到 json.loads 文本
        if isinstance(final, (dict, list)):
            structured_output = final
        elif isinstance(final, str) and text:
            try:
                structured_output = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                structured_output = None
```

- [ ] **Step 9: 跑测试验证通过**

Run: `uv run pytest --no-header -x packages/core/tests/agents/test_providers.py::TestOpenAIProvider packages/core/tests/agents/test_openai_result_mapper.py packages/core/tests/agents/test_openai_output_schema.py -v`
Expected: PASS（build_agent output_type 测试 + mapper 既有测试 + output_schema 测试全绿）。

若 `test_map_plain_text`（final="hello" str 路径）因 Step 8 改动失败：检查 `isinstance(final, str)` 分支仍走 `text=final`，应不受影响。

- [ ] **Step 10: Commit**

```bash
git add packages/core/src/shannon_core/agents/openai_output_schema.py \
        packages/core/src/shannon_core/agents/providers_openai.py \
        packages/core/src/shannon_core/agents/openai_result_mapper.py \
        packages/core/tests/agents/test_openai_output_schema.py \
        packages/core/tests/agents/test_providers.py \
        packages/core/tests/agents/test_openai_result_mapper.py
git commit -m "fix(agents): openai build_agent 走原生 output_type 强制结构化输出（B2）

B2: 此前 build_agent 丢弃 output_format，structured_output 靠 json.loads 兜底，
模型输出非纯 JSON 时 auth verdict 静默失败。新增 RawJsonSchemaOutputSchema
（AgentOutputSchemaBase 子类，直接持有 JSON Schema dict，绕过 Pydantic 建模），
build_agent 在 output_format 非空时传 output_type；map_run_result 处理 dict final。"
```

---

### Task 5: D — 双引擎对齐测试护栏（test_dual_engine_alignment.py）

**Files:**
- Create: `packages/core/tests/agents/test_dual_engine_alignment.py`
- Test: 本身就是测试文件

**Interfaces:**
- Consumes: Task 1-4 成果（`AnthropicProvider`/`OpenAIProvider` 都 isinstance BaseProvider；max_turns 语义对齐；`_classify_error`；`RawJsonSchemaOutputSchema`）
- Produces: 双引擎语义一致性锁定测试（防回退护栏，spec §3 不变量 2/3/4）

**说明：** 本 task 不改实现，只写对照测试。两 provider 各自 mock 各自 SDK（Anthropic mock `claude_agent_sdk.query`；OpenAI mock `Runner.run_streamed`），但断言**产出 `ClaudeRunResult` 的关键字段语义一致**。

- [ ] **Step 1: 写测试文件**

新建 `packages/core/tests/agents/test_dual_engine_alignment.py`：

```python
"""D（2026-06-27 双引擎解耦修复）：双引擎语义对齐测试护栏。

锁定不变量（spec §3）：AnthropicProvider 与 OpenAIProvider 对「同类结果场景」
产出语义一致的 ClaudeRunResult 关键字段（success / error_code / retryable /
structured_output 非 None）。两 provider 各自 mock 各自 SDK，但断言字段对齐。
"""
import pytest


# ---------- D3: isinstance 契约锁定 ----------

def test_both_providers_are_baseprovider():
    from shannon_core.agents.providers import BaseProvider
    from shannon_core.agents.providers_anthropic import AnthropicProvider
    from shannon_core.agents.providers_openai import OpenAIProvider
    from shannon_core.agents.runner import ProviderConfig
    assert isinstance(AnthropicProvider(ProviderConfig(type="anthropic_api", api_key="k")), BaseProvider)
    assert isinstance(OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k")), BaseProvider)


# ---------- D2: 场景化语义对齐 ----------

def test_max_turns_alignment_both_engines_marked_failed():
    """场景 MAX_TURNS：两引擎都须 success=False + error_code=ExecutionLimitError + retryable=False。

    Anthropic: SDK 返回 subtype=error_max_turns。
    OpenAI: Runner.run_streamed 抛 MaxTurnsExceeded。
    """
    # Anthropic 侧：通过 _classify_result_failure 直接验证 max_turns 分类
    from shannon_core.agents.providers_anthropic import AnthropicProvider
    from shannon_core.agents.runner import ProviderConfig
    anthropic = AnthropicProvider(ProviderConfig(type="anthropic_api", api_key="k"))
    code, retryable = anthropic._classify_result_failure(
        subtype="error_max_turns", is_error=False, api_error_status=None, errors=[]
    )
    assert code == "ExecutionLimitError"
    assert retryable is False

    # OpenAI 侧：通过 map_run_result 验证 stop_reason=max_turns
    from unittest.mock import MagicMock
    from shannon_core.agents.openai_result_mapper import map_run_result
    rr = MagicMock()
    rr.final_output = "partial"
    rr.context_wrapper.usage.input_tokens = 0
    rr.context_wrapper.usage.output_tokens = 0
    res = map_run_result(rr, duration_ms=10, model="m", turns=200, stop_reason="max_turns")
    assert res.success is False
    assert res.error_code == "ExecutionLimitError"
    assert res.retryable is False


def test_retryable_alignment_rate_limit_openai():
    """场景 RATE_LIMIT（OpenAI 侧）：retryable=True / error_code=RateLimitError。

    两引擎 retryable 对齐（都 True）——Claude 侧 rate limit 走 classify_error_for_temporal
    → BillingError(True)（既有行为，models/errors.py），retryable 同为 True。error_code 字符串
    允许差异（spec §1.4，Pre-Flight 裁定），故此处只锁 OpenAI 侧 + retryable，不调 anthropic 内部。
    """
    from shannon_core.agents.providers_openai import OpenAIProvider
    from shannon_core.agents.runner import ProviderConfig
    openai = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k"))
    o_code, o_retry = openai._classify_error(Exception("rate limit exceeded"))
    assert o_code == "RateLimitError"
    assert o_retry is True


def test_structured_output_alignment_both_engines_produce_nonNone():
    """场景 STRUCTURED_OUTPUT：传入 output_format 时，两引擎 structured_output 都非 None。

    OpenAI: map_run_result + output_format → json.loads 解析。
    锁定 spec §3 不变量 3（structured_output 可靠性）。
    """
    from unittest.mock import MagicMock
    from shannon_core.agents.openai_result_mapper import map_run_result
    rr = MagicMock()
    rr.final_output = '{"verdict": "pass"}'
    rr.context_wrapper.usage.input_tokens = 1
    rr.context_wrapper.usage.output_tokens = 1
    res = map_run_result(rr, duration_ms=10, model="m", turns=1, output_format={"type": "object"})
    assert res.structured_output == {"verdict": "pass"}
    assert res.structured_output is not None


def test_structured_output_dict_final_path():
    """B2 dict final_output 路径：openai output_type 生效后 final_output 是 dict。"""
    from unittest.mock import MagicMock
    from shannon_core.agents.openai_result_mapper import map_run_result
    rr = MagicMock()
    rr.final_output = {"verdict": "pass"}  # dict（RawJsonSchemaOutputSchema.validate_json 返回）
    rr.context_wrapper.usage.input_tokens = 1
    rr.context_wrapper.usage.output_tokens = 1
    res = map_run_result(rr, duration_ms=10, model="m", turns=1, output_format={"type": "object"})
    assert res.structured_output == {"verdict": "pass"}
    assert isinstance(res.text, str)  # dict → json.dumps 成 str


def test_build_agent_openai_wires_output_type():
    """B2: OpenAI build_agent 在 output_format 非空时带 output_type（与 Claude options.output_format 对齐）。"""
    from shannon_core.agents.openai_output_schema import RawJsonSchemaOutputSchema
    from shannon_core.agents.providers_openai import OpenAIProvider
    from shannon_core.agents.runner import ProviderConfig
    provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k", medium_model="m"))
    agent = provider.build_agent("m", output_format={"type": "object"})
    assert isinstance(agent.output_type, RawJsonSchemaOutputSchema)
```

- [ ] **Step 2: 跑测试验证（应直接通过——本 task 是护栏，依赖 Task 1-4 已完成）**

Run: `uv run pytest --no-header -x packages/core/tests/agents/test_dual_engine_alignment.py -v`
Expected: PASS（全部测试绿）。

**若有失败：** 说明 Task 1-4 某个对齐没做对，回到对应 task 修复（不要在本 task 放宽断言）。

- [ ] **Step 3: Commit**

```bash
git add packages/core/tests/agents/test_dual_engine_alignment.py
git commit -m "test(agents): 双引擎语义对齐测试护栏（D）

D: 新建 test_dual_engine_alignment.py，锁定两 provider 对同类场景（MAX_TURNS/
RATE_LIMIT/STRUCTURED_OUTPUT）产出语义一致的 ClaudeRunResult 关键字段 + isinstance
BaseProvider 契约。防回退护栏（spec §3 不变量 2/3/4）。"
```

---

### Task 6: E — openai 真机冒烟闭环（validate_openai_task_probe.py）

**Files:**
- Run: `scripts/validate_openai_task_probe.py`（已就绪，122 行，不改代码）

**前置约束：**
- 需 `.env.profiles/glm-openai.env`（含 `SHANNON_OPENAI_API_KEY` / `SHANNON_OPENAI_BASE_URL`）。
- 需网络访问 GLM openai endpoint。
- 会消耗真实 GLM token（小额）。
- **执行方式由用户确认（spec §2.E）：agent 在本会话跑 / 用户人工跑。**

- [ ] **Step 1: 确认 profile 存在**

Run: `ls -la .env.profiles/glm-openai.env 2>/dev/null && head -5 .env.profiles/glm-openai.env | grep -E "SHANNON_OPENAI_(API_KEY|BASE_URL)" | sed 's/=.*/=<REDACTED>/'`
Expected: 文件存在且含 `SHANNON_OPENAI_API_KEY` / `SHANNON_OPENAI_BASE_URL`（值脱敏）。

**若文件不存在：** 停止，请用户提供 profile 路径或凭证。本 task 跳过，记为「待人工冒烟」（不阻塞 plan 完成，但 spec §0 的「闭环」目标未达成，需在交付说明里标注）。

- [ ] **Step 2: 跑真机冒烟脚本**

Run: `uv run python scripts/validate_openai_task_probe.py`
Expected（脚本内 PASS 标准）: GLM 经 openai-agents 发起 ≥1 次 `task` tool call（子代理读码），audit 录到 `toolName=task`，并产出 SQLi 判定。脚本输出 `PASS`。

- [ ] **Step 3: 验证 B2 结构化输出真机（手动核对）**

脚本输出里核对：openai 引擎下调用是否触发了结构化输出（response_format json_schema）。若脚本未覆盖 structured_output 场景，临时追加一次最小调用（不改脚本主逻辑，仅手动核对 GLM 是否接受 `response_format`）：

Run（探查 GLM 是否支持 json_schema response_format，不改仓库代码）:
```bash
uv run python -c "
import asyncio, os
from openai import AsyncOpenAI
async def main():
    c = AsyncOpenAI(api_key=os.environ['SHANNON_OPENAI_API_KEY'], base_url=os.environ['SHANNON_OPENAI_BASE_URL'])
    r = await c.chat.completions.create(
        model=os.environ.get('SHANNON_OPENAI_MEDIUM_MODEL', 'glm-4.6'),
        messages=[{'role':'user','content':'返回 {\"verdict\":\"pass\"}'}],
        response_format={'type':'json_schema','json_schema':{'name':'v','schema':{'type':'object','properties':{'verdict':{'type':'string'}},'required':['verdict']},'strict':False}},
    )
    print('GLM json_schema supported:', r.choices[0].message.content)
asyncio.run(main())
" 2>&1 | tail -5
```
Expected: 输出合法 JSON `{"verdict": ...}` → GLM 支持 json_schema response_format，B2 原生路径生效。

**若失败（GLM 不支持 response_format json_schema）：** 触发降级——B2 的 `RawJsonSchemaOutputSchema.validate_json` 仍用 `json.loads`（已实现），结构化输出退化为 best-effort。在 commit message / 交付说明记录 follow-up：「GLM openai endpoint 不支持 response_format json_schema，B2 走 json.loads 兜底，prompt 层需强化『必须输出纯 JSON』（spec §5 降级）」。

- [ ] **Step 4: 记录冒烟结果（无代码改动则空提交说明）**

若 Step 2/3 全 PASS，无需代码改动，仅在最终交付说明标注「openai 真机冒烟 PASS」。

若触发降级，在 spec 文件追加 follow-up 段落并提交：
```bash
git add docs/superpowers/specs/2026-06-27-dual-engine-decoupling-fix-design.md
git commit -m "docs(spec): E 真机冒烟结果 — GLM json_schema 支持情况 + B2 降级 follow-up"
```

---

## Self-Review

**1. Spec coverage（逐条对照 spec §2 改动）：**
- A1（AnthropicProvider 继承）→ Task 1 ✅
- A2（ClaudeRunResult/BaseProvider 不变量文档）→ Task 1 ✅
- B1（map_run_result max_turns 对齐）→ Task 2 ✅
- B2（build_agent 原生 output_type）→ Task 4 ✅
- B3（_handle_error 补 error_code）→ Task 3 ✅
- C1（cost=0 注释）→ Task 2 ✅
- C2（spending-cap best-effort 文档）→ Task 1（并入 A2 docstring）✅
- D1/D2/D3（双引擎对齐测试护栏）→ Task 5 ✅
- E（真机冒烟）→ Task 6 ✅

**2. Placeholder scan：** 无 TBD/TODO；每个代码 step 都给了完整实现代码；error_code 字符串与 `classify_error_for_temporal` 逐字核对。✅

**3. Type consistency：** `_classify_error(error) -> tuple[str | None, bool]` 在 Task 3 定义、Task 5 调用一致；`RawJsonSchemaOutputSchema` 在 Task 4 定义、Task 5 调用一致；`map_run_result` 签名在 Task 2/4/5 调用一致。✅

**4. 依赖顺序：** Task 1（契约）→ Task 2（B1 mapper）→ Task 3（B3 _handle_error）→ Task 4（B2 build_agent，依赖 mapper 改 dict 处理）→ Task 5（D 对齐测试，依赖 1-4）→ Task 6（E 真机，验证 B2）。✅

---

## 执行说明

本计划 6 个 task，每个自带 TDD cycle（写失败测试→验证失败→实现→验证通过→commit）。Task 1-5 是代码改动（纯 `agents/` 集成层 + 测试），Task 6 是真机冒烟（依赖凭证/网络，可能需用户人工跑）。
