# packages/core/tests/prompts/test_vuln_recon_context_injection.py
"""断言 5 个 vuln prompts 含 {{RECON_CONTEXT}} / {{FRAMEWORK_ANALYSIS}} 占位符，
且不引确定性层 token（守铁律 CLAUDE.md §1）。

对应 activities.py:_build_vuln_prompt_variables 注入（Task 7）。
"""
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"


def _read(name: str) -> str:
    return (PROMPTS_DIR / name).read_text("utf-8")


VULN_PROMPTS = [
    "vuln-injection.txt",
    "vuln-xss.txt",
    "vuln-ssrf.txt",
    "vuln-authz.txt",
    "vuln-auth.txt",
]


def test_all_vuln_prompts_have_recon_context_placeholder():
    for name in VULN_PROMPTS:
        content = _read(name)
        assert "{{RECON_CONTEXT}}" in content, f"{name} 缺 {{RECON_CONTEXT}} 占位符"


def test_all_vuln_prompts_have_framework_analysis_placeholder():
    for name in VULN_PROMPTS:
        content = _read(name)
        assert "{{FRAMEWORK_ANALYSIS}}" in content, f"{name} 缺 {{FRAMEWORK_ANALYSIS}} 占位符"


def test_vuln_prompts_decoupled_from_deterministic():
    for name in VULN_PROMPTS:
        content = _read(name)
        for tok in ("parameter_graph", "SinkCallSite", "static_dataflow_hints"):
            assert tok not in content, f"{name} 引确定性 token: {tok}"
