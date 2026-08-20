"""D（2026-06-27 双引擎解耦修复）：双引擎语义对齐测试护栏。

锁定不变量（spec §3）：AnthropicProvider 与 OpenAIProvider 对「同类结果场景」
产出语义一致的 ClaudeRunResult 关键字段（success / error_code / retryable /
structured_output 非 None）。两 provider 各自 mock 各自 SDK，但断言字段对齐。
"""
import logging

import pytest


# ---------- D3: isinstance 契约锁定 ----------

def test_both_providers_are_baseprovider():
    from supernova_core.agents.providers import BaseProvider
    from supernova_core.agents.providers_anthropic import AnthropicProvider
    from supernova_core.agents.providers_openai import OpenAIProvider
    from supernova_core.agents.runner import ProviderConfig
    assert isinstance(AnthropicProvider(ProviderConfig(type="anthropic_api", api_key="k")), BaseProvider)
    assert isinstance(OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k")), BaseProvider)


# ---------- D2: 场景化语义对齐 ----------

def test_max_turns_alignment_both_engines_marked_failed():
    """场景 MAX_TURNS：两引擎都须 success=False + error_code=ExecutionLimitError + retryable=False。

    Anthropic: SDK 返回 subtype=error_max_turns。
    OpenAI: Runner.run_streamed 抛 MaxTurnsExceeded。
    """
    # Anthropic 侧：通过 _classify_result_failure 直接验证 max_turns 分类
    from supernova_core.agents.providers_anthropic import AnthropicProvider
    from supernova_core.agents.runner import ProviderConfig
    anthropic = AnthropicProvider(ProviderConfig(type="anthropic_api", api_key="k"))
    code, retryable = anthropic._classify_result_failure(
        subtype="error_max_turns", is_error=False, api_error_status=None, errors=[]
    )
    assert code == "ExecutionLimitError"
    assert retryable is False

    # OpenAI 侧：通过 map_run_result 验证 stop_reason=max_turns
    from unittest.mock import MagicMock
    from supernova_core.agents.openai_result_mapper import map_run_result
    rr = MagicMock()
    rr.final_output = "partial"
    rr.context_wrapper.usage.input_tokens = 0
    rr.context_wrapper.usage.output_tokens = 0
    rr.context_wrapper.usage.input_tokens_details = None
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
    from supernova_core.agents.providers_openai import OpenAIProvider
    from supernova_core.agents.runner import ProviderConfig
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
    from supernova_core.agents.openai_result_mapper import map_run_result
    rr = MagicMock()
    rr.final_output = '{"verdict": "pass"}'
    rr.context_wrapper.usage.input_tokens = 1
    rr.context_wrapper.usage.output_tokens = 1
    rr.context_wrapper.usage.input_tokens_details = None
    res = map_run_result(rr, duration_ms=10, model="m", turns=1, output_format={"type": "object"})
    assert res.structured_output == {"verdict": "pass"}


def test_structured_output_dict_final_path():
    """B2 dict final_output 路径：openai output_type 生效后 final_output 是 dict。"""
    from unittest.mock import MagicMock
    from supernova_core.agents.openai_result_mapper import map_run_result
    rr = MagicMock()
    rr.final_output = {"verdict": "pass"}  # dict（RawJsonSchemaOutputSchema.validate_json 返回）
    rr.context_wrapper.usage.input_tokens = 1
    rr.context_wrapper.usage.output_tokens = 1
    rr.context_wrapper.usage.input_tokens_details = None
    res = map_run_result(rr, duration_ms=10, model="m", turns=1, output_format={"type": "object"})
    assert res.structured_output == {"verdict": "pass"}
    assert isinstance(res.text, str)  # dict → json.dumps 成 str


def test_structured_output_extracts_from_markdown_fence():
    """openai 引擎不设 output_type -> final_output 是含 ```json 围栏的纯文本；
    map_run_result L0 容错解析（_extract_json_payload）剥围栏出 dict（2026-07-24）。"""
    from unittest.mock import MagicMock
    from supernova_core.agents.openai_result_mapper import map_run_result
    rr = MagicMock()
    rr.final_output = '```json\n{"verdict": "vulnerable"}\n```'
    rr.context_wrapper.usage.input_tokens = 1
    rr.context_wrapper.usage.output_tokens = 1
    rr.context_wrapper.usage.input_tokens_details = None
    res = map_run_result(rr, duration_ms=10, model="m", turns=1, output_format={"type": "object"})
    assert res.structured_output == {"verdict": "vulnerable"}


def test_build_agent_openai_no_output_type():
    """openai 引擎不设 output_type（第三方端点不支持 response_format json_schema，传之必 400，2026-07-24）；结构化输出靠 map_run_result 本地 L0 解析 + call L1 兜底。"""
    from supernova_core.agents.providers_openai import OpenAIProvider
    from supernova_core.agents.runner import ProviderConfig
    provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k", medium_model="m"))
    agent = provider.build_agent("m", output_format={"type": "object"})
    assert agent.output_type is None

def test_both_engines_same_cost_for_same_usage():
    """cost 定价(spec 2026-07-09): 两引擎对相同 model+token 经 compute_cost 算出相同 CostAmount。

    claude _extract_cost 自算(不再读 SDK total_cost_usd)、openai map_run_result 用 compute_cost,
    两者同输入同输出 → 锁定双引擎 cost 路径对齐。
    """
    from unittest.mock import MagicMock
    from claude_agent_sdk import ResultMessage
    from supernova_core.agents.providers_anthropic import AnthropicProvider
    from supernova_core.agents.openai_result_mapper import map_run_result
    from supernova_core.agents.runner import ProviderConfig

    # claude 侧：total_cost_usd=999 必须被忽略（自算）
    anthropic = AnthropicProvider(ProviderConfig(type="anthropic_api"))
    rm_usage = MagicMock(input_tokens=1_000_000, output_tokens=500_000,
                         cache_creation_input_tokens=0, cache_read_input_tokens=0)
    rm = ResultMessage(subtype="result", duration_ms=10, duration_api_ms=5,
                       is_error=False, num_turns=1, session_id="s",
                       total_cost_usd=999.0, usage=rm_usage, result="ok")
    claude_res = anthropic._extract_result(rm, duration=10, model="glm-5.2")

    # openai 侧（input_tokens_details=None → 无 cache 命中, billable=raw）
    o_usage = MagicMock(input_tokens=1_000_000, output_tokens=500_000, input_tokens_details=None)
    rr = MagicMock()
    rr.final_output = "ok"
    rr.context_wrapper.usage = o_usage
    openai_res = map_run_result(rr, duration_ms=10, model="glm-5.2", turns=1)

    assert claude_res.cost == openai_res.cost
    assert claude_res.cost_currency == openai_res.cost_currency == "CNY"
    assert claude_res.cost > 0


def test_truncated_final_text_recovered_both_engines(caplog):
    """截断修复双引擎对称（spec 2026-08-19 §3.1）：半截最终文本 →
    structured_output 救回 N-1 条。anthropic _extract_result 与 openai
    map_run_result 的兜底分支同生共死，必须一起接入；两引擎兜底各自发
    truncation repair warning（engine 标识 + recovered_items——spec §3.1
    排障第一现场是 agents/*.log），一并用 caplog 锁定。"""
    import json
    from unittest.mock import MagicMock
    from supernova_core.agents.providers_anthropic import AnthropicProvider
    from supernova_core.agents.openai_result_mapper import map_run_result
    from supernova_core.agents.runner import ProviderConfig

    full = json.dumps({"vulnerabilities": [
        {"ID": f"AUTH-VULN-{i:02d}", "title": f"t{i}"} for i in range(1, 13)]})
    truncated = full[: full.index('"AUTH-VULN-12"') + 5]  # 第 12 条 ID 字符串中途

    # anthropic 侧：_extract_result 兜底分支（collected_text）
    provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))
    rm = MagicMock()
    rm.collected_text = truncated
    rm.result = ""
    rm.content = []
    rm.structured_output = None  # SDK 第一道（--json-schema）解析失败
    rm.usage = {"input_tokens": 1, "output_tokens": 1,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    rm.result_is_error = False
    rm.result_subtype = None
    rm.stop_reason = None
    with caplog.at_level(logging.WARNING,
                         logger="supernova_core.agents.providers_anthropic"):
        claude_res = provider._extract_result(
            rm, duration=10, model="m", turn_count=1, output_format={"type": "object"})
    assert claude_res.structured_output is not None
    assert len(claude_res.structured_output["vulnerabilities"]) == 11
    assert claude_res.structured_output["vulnerabilities"][-1]["ID"] == "AUTH-VULN-11"
    assert any("truncation repair" in r.getMessage() and "anthropic engine" in r.getMessage()
               and "recovered_items" in r.getMessage()
               for r in caplog.records)

    # openai 侧：map_run_result 兜底分支（final_output 纯文本）
    o_usage = MagicMock(input_tokens=1, output_tokens=1, input_tokens_details=None)
    rr = MagicMock()
    rr.final_output = truncated
    rr.context_wrapper.usage = o_usage
    with caplog.at_level(logging.WARNING,
                         logger="supernova_core.agents.openai_result_mapper"):
        openai_res = map_run_result(
            rr, duration_ms=10, model="m", turns=1, output_format={"type": "object"})
    assert openai_res.structured_output is not None
    assert openai_res.structured_output == claude_res.structured_output
    assert any("truncation repair" in r.getMessage() and "openai engine" in r.getMessage()
               and "recovered_items" in r.getMessage()
               for r in caplog.records)
