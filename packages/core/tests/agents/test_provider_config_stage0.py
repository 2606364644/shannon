"""P3c 阶段 0：ProviderConfig 运行时调参字段 + build_provider_config 透传。

方案 Y：字段 None = 未覆盖（引擎回落 env）；build 不主动读 env，只透传显式参数。
"""
from supernova_core.agents.runner import ProviderConfig
from supernova_core.agents.providers import build_provider_config


def test_provider_config_new_fields_default_none():
    """5 个新字段默认 None（= 未覆盖，引擎将回落 env）。"""
    cfg = ProviderConfig()
    assert cfg.max_turns is None
    assert cfg.subagent_max_turns is None
    assert cfg.max_output_tokens is None
    assert cfg.call_timeout is None
    assert cfg.adaptive_thinking is None


def test_build_provider_config_passes_runtime_params_openai():
    """openai_compatible：build 接受运行时调参并透传（不读 env）。"""
    cfg = build_provider_config(
        provider_type="openai_compatible",
        max_turns=999,
        subagent_max_turns=88,
        max_output_tokens=32000,
        call_timeout=600.0,
        adaptive_thinking=False,
    )
    assert cfg.max_turns == 999
    assert cfg.subagent_max_turns == 88
    assert cfg.max_output_tokens == 32000
    assert cfg.call_timeout == 600.0
    assert cfg.adaptive_thinking is False


def test_build_provider_config_passes_runtime_params_anthropic():
    """anthropic_api：同样透传。"""
    cfg = build_provider_config(
        provider_type="anthropic_api",
        max_turns=777,
        adaptive_thinking=True,
    )
    assert cfg.max_turns == 777
    assert cfg.adaptive_thinking is True
    # 未传的仍 None
    assert cfg.max_output_tokens is None


def test_build_provider_config_runtime_params_default_none_even_if_env_set(monkeypatch):
    """关键不变量：build 不主动读 env —— 即使 env 设了，不传参数 → 字段仍 None。

    引擎负责 None 时回落 env；build 只透传。这是阶段 2 ws 覆盖语义的前提。
    """
    monkeypatch.setenv("CLAUDE_MAX_TURNS", "777")
    monkeypatch.setenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "99999")
    monkeypatch.setenv("SUPERNOVA_OPENAI_MAX_TURNS", "555")
    cfg_anthropic = build_provider_config(provider_type="anthropic_api")
    cfg_openai = build_provider_config(provider_type="openai_compatible")
    # build 不读这些 env；字段 None，引擎稍后回落
    assert cfg_anthropic.max_turns is None
    assert cfg_anthropic.max_output_tokens is None
    assert cfg_openai.max_turns is None


def test_build_provider_config_legacy_path_passes_runtime_params():
    """bedrock/vertex/litellm_router（_build_legacy 分支）也透传新字段（一致性）。"""
    cfg = build_provider_config(
        provider_type="litellm_router",
        max_turns=432,
        call_timeout=300.0,
    )
    assert cfg.max_turns == 432
    assert cfg.call_timeout == 300.0
