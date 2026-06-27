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


def test_structured_output_openai_produces_nonNone():
    """场景 STRUCTURED_OUTPUT：传入 output_format 时，OpenAI 引擎 structured_output 非 None。

    仅 OpenAI 侧——Anthropic structured_output 走 providers_anthropic 原生
    result_message.structured_output，由 TestAnthropicProvider 覆盖。
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
