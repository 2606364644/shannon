"""env 文本 ↔ WsConfig 字段转换（parse / render）单元测试。

spec: docs/superpowers/specs/2026-08-10-ws-config-env-textarea-design.md
"""
import pytest

from supernova_web.components.ws_env_codec import parse_env_text, render_env_text
from supernova_web.components.ws_config_store import (
    WsConfig, WsProviderFields, WsGitFields,
)


# ---- parse: 生效字段（存 config.yaml，真 ws 覆盖）----

def test_parse_extracts_ai_provider():
    parsed = parse_env_text("SUPERNOVA_AI_PROVIDER=openai_compatible\n")
    assert parsed.fields["ai_provider"] == "openai_compatible"


def test_parse_extracts_anthropic_credential_to_api_key():
    parsed = parse_env_text("ANTHROPIC_AUTH_TOKEN=sk-secret\n")
    assert parsed.fields["api_key"] == "sk-secret"


def test_parse_extracts_openai_credential_to_api_key():
    parsed = parse_env_text("SUPERNOVA_OPENAI_API_KEY=sk-oai\n")
    assert parsed.fields["api_key"] == "sk-oai"


def test_parse_extracts_base_url_variants():
    assert parse_env_text("ANTHROPIC_BASE_URL=http://a\n").fields["base_url"] == "http://a"
    assert parse_env_text("SUPERNOVA_OPENAI_BASE_URL=http://b\n").fields["base_url"] == "http://b"


def test_parse_extracts_tier_models():
    text = "SUPERNOVA_LARGE_MODEL=GLM-5.2\nSUPERNOVA_SMALL_MODEL=GLM-4.5-Air\n"
    parsed = parse_env_text(text)
    assert parsed.fields["large_model"] == "GLM-5.2"
    assert parsed.fields["small_model"] == "GLM-4.5-Air"


def test_parse_max_turns_as_int():
    assert parse_env_text("SUPERNOVA_MAX_TURNS=42\n").fields["max_turns"] == 42


def test_parse_adaptive_thinking_as_bool():
    assert parse_env_text("SUPERNOVA_ADAPTIVE_THINKING=true\n").fields["adaptive_thinking"] is True
    assert parse_env_text("SUPERNOVA_ADAPTIVE_THINKING=false\n").fields["adaptive_thinking"] is False


def test_parse_git_fields():
    parsed = parse_env_text("GITLAB_USER=bot\nGITLAB_TOKEN=glpat-x\n")
    assert parsed.fields["gitlab_user"] == "bot"
    assert parsed.fields["gitlab_token"] == "glpat-x"


# ---- parse: 注释 / 空行 / 空白 ----

def test_parse_skips_comments_and_blank_lines():
    text = "# a comment\n\nSUPERNOVA_AI_PROVIDER=openai_compatible\n  \n"
    parsed = parse_env_text(text)
    assert parsed.fields == {"ai_provider": "openai_compatible"}


def test_parse_strips_whitespace_around_kv():
    parsed = parse_env_text("  SUPERNOVA_AI_PROVIDER  =  openai_compatible  \n")
    assert parsed.fields["ai_provider"] == "openai_compatible"


# ---- parse: warnings（进程级 ineffective / 未知 unknown）----

def test_parse_collects_ineffective_keys():
    parsed = parse_env_text("SUPERNOVA_MAX_CONCURRENT=8\nSUPERNOVA_LLM_TRACK_ENABLED=0\n")
    assert parsed.ineffective == ["SUPERNOVA_MAX_CONCURRENT", "SUPERNOVA_LLM_TRACK_ENABLED"]
    assert "max_turns" not in parsed.fields  # 不进 fields


def test_parse_collects_unknown_keys_and_keeps_pricing_ineffective():
    parsed = parse_env_text("TOTALLY_UNKNOWN_KEY=x\nSUPERNOVA_PRICING_OVERRIDE=p.json\n")
    assert parsed.unknown == ["TOTALLY_UNKNOWN_KEY"]
    assert parsed.ineffective == ["SUPERNOVA_PRICING_OVERRIDE"]


def test_parse_empty_text_returns_empty():
    parsed = parse_env_text("")
    assert parsed.fields == {}
    assert parsed.ineffective == []
    assert parsed.unknown == []


# ---- parse: 格式错误 → ValueError（API 层转 422）----

def test_parse_missing_equals_raises():
    with pytest.raises(ValueError):
        parse_env_text("THIS_LINE_HAS_NO_EQUALS\n")


def test_parse_invalid_max_turns_raises():
    with pytest.raises(ValueError):
        parse_env_text("SUPERNOVA_MAX_TURNS=not-a-number\n")


def test_parse_invalid_adaptive_thinking_raises():
    with pytest.raises(ValueError):
        parse_env_text("SUPERNOVA_ADAPTIVE_THINKING=maybe\n")


# ---- render: config → env 文本（按 provider 选 key 名 / 凭据掩码 / 只渲染非 None）----

def test_render_anthropic_basic_fields():
    cfg = WsConfig(provider=WsProviderFields(
        ai_provider="anthropic_api", base_url="http://a", large_model="GLM-5.2",
    ))
    text = render_env_text(cfg, ai_provider="anthropic_api")
    assert "SUPERNOVA_AI_PROVIDER=anthropic_api" in text
    assert "ANTHROPIC_BASE_URL=http://a" in text
    assert "SUPERNOVA_LARGE_MODEL=GLM-5.2" in text


def test_render_openai_uses_openai_key_names():
    cfg = WsConfig(provider=WsProviderFields(
        ai_provider="openai_compatible", base_url="http://b", api_key="sk-oai",
        large_model="gpt-x",
    ))
    text = render_env_text(cfg, ai_provider="openai_compatible")
    assert "SUPERNOVA_OPENAI_BASE_URL=http://b" in text
    assert "SUPERNOVA_OPENAI_API_KEY=••••" in text  # 凭据掩码
    assert "SUPERNOVA_OPENAI_LARGE_MODEL=gpt-x" in text
    assert "sk-oai" not in text


def test_render_anthropic_credential_uses_auth_token_masked():
    cfg = WsConfig(provider=WsProviderFields(ai_provider="anthropic_api", api_key="sk-secret"))
    text = render_env_text(cfg, ai_provider="anthropic_api")
    assert "ANTHROPIC_AUTH_TOKEN=••••" in text
    assert "sk-secret" not in text


def test_render_credential_omitted_when_unset():
    cfg = WsConfig(provider=WsProviderFields(ai_provider="openai_compatible"))
    text = render_env_text(cfg, ai_provider="openai_compatible")
    assert "API_KEY" not in text  # 无凭据 → 不渲染该行


def test_render_only_non_none_fields():
    cfg = WsConfig(provider=WsProviderFields(
        ai_provider="openai_compatible", base_url="http://b",
    ))
    text = render_env_text(cfg, ai_provider="openai_compatible")
    assert "SUPERNOVA_OPENAI_BASE_URL=http://b" in text
    assert "SUPERNOVA_MODEL" not in text     # 未设 → 不渲染
    assert "SMALL_MODEL" not in text


def test_render_max_turns_and_adaptive_thinking():
    cfg = WsConfig(provider=WsProviderFields(
        ai_provider="openai_compatible", max_turns=42, adaptive_thinking=True,
    ))
    text = render_env_text(cfg, ai_provider="openai_compatible")
    assert "SUPERNOVA_MAX_TURNS=42" in text
    assert "SUPERNOVA_ADAPTIVE_THINKING=true" in text


def test_render_git_section_token_masked():
    cfg = WsConfig(
        provider=WsProviderFields(ai_provider="openai_compatible"),
        git=WsGitFields(gitlab_user="bot", gitlab_token="glpat-x"),
    )
    text = render_env_text(cfg, ai_provider="openai_compatible")
    assert "GITLAB_USER=bot" in text
    assert "GITLAB_TOKEN=••••" in text
    assert "glpat-x" not in text


def test_render_empty_config_returns_empty():
    assert render_env_text(WsConfig(), ai_provider="anthropic_api") == ""
