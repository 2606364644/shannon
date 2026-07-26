"""P3c 阶段 0 防回退守卫：收编点的 env 回落分支必须保留。

不变量：引擎读 self.config.<字段>，且保留 os.getenv(...) 回落（字段 None 时走 env）。
若有人误删回落分支（让字段 None 时无默认），这些断言会失败。
"""
from pathlib import Path

# cwd 无关：锚定 repo root（对齐 test_static_dataflow_hints_decoupling.py 惯例）。
REPO_ROOT = Path(__file__).resolve().parents[4]

ANTHROPIC = (REPO_ROOT / "packages/core/src/supernova_core/agents/providers_anthropic.py").read_text()
OPENAI = (REPO_ROOT / "packages/core/src/supernova_core/agents/providers_openai.py").read_text()


def test_anthropic_reads_config_fields():
    """新路径（self.config）已接入。"""
    assert "self.config.max_output_tokens" in ANTHROPIC
    assert "self.config.max_turns" in ANTHROPIC
    assert "self.config.adaptive_thinking" in ANTHROPIC


def test_anthropic_keeps_env_fallback():
    """env 回落分支保留（不得删除）。"""
    assert 'os.getenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS"' in ANTHROPIC
    assert 'os.getenv("CLAUDE_MAX_TURNS"' in ANTHROPIC
    assert 'os.getenv("CLAUDE_ADAPTIVE_THINKING"' in ANTHROPIC


def test_anthropic_resolve_max_turns_extracted():
    """_resolve_max_turns 纯函数已提取（_build_options 不再内联）。"""
    assert "def _resolve_max_turns" in ANTHROPIC


def test_openai_reads_config_fields():
    assert "self.config.max_turns" in OPENAI
    assert "self.config.subagent_max_turns" in OPENAI
    assert "self.config.call_timeout" in OPENAI


def test_openai_keeps_env_fallback():
    assert 'os.getenv("SUPERNOVA_OPENAI_MAX_TURNS"' in OPENAI
    assert 'os.getenv("SUPERNOVA_OPENAI_SUBAGENT_MAX_TURNS"' in OPENAI
    assert 'os.getenv("SUPERNOVA_OPENAI_CALL_TIMEOUT"' in OPENAI


def test_pricing_and_model_caps_NOT_refactored_this_stage():
    """范围守卫：pricing_override / model_context_override 本阶段明确不收编
    （推迟——per-profile env 已够）。确保没人提前动它们进 ProviderConfig。"""
    runner = (REPO_ROOT / "packages/core/src/supernova_core/agents/runner.py").read_text()
    assert "pricing_override" not in runner  # 未进 ProviderConfig
    assert "model_context_override" not in runner
