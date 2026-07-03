# packages/core/tests/prompts/test_vuln_starting_context.py
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"


def _read(name: str) -> str:
    return (PROMPTS_DIR / name).read_text("utf-8")


def test_injection_starting_context_has_section_4_2_fields():
    content = _read("vuln-injection.txt")
    assert "Section 4.2" in content or "§4.2" in content
    for field in ("HTTP method", "ownership", "middleware"):
        assert field.lower() in content.lower(), f"injection starting_context 缺字段: {field}"
    # framework auto-gen 提示
    assert "framework auto-generated" in content.lower() or "finale-rest" in content.lower()


def test_xss_starting_context_has_section_4_2_fields():
    content = _read("vuln-xss.txt")
    assert "Section 4.2" in content or "§4.2" in content
    for field in ("HTTP method", "ownership", "middleware"):
        assert field.lower() in content.lower(), f"xss starting_context 缺字段: {field}"
    assert "framework auto-generated" in content.lower() or "finale-rest" in content.lower()


def test_starting_context_decoupled_from_deterministic():
    for name in ("vuln-injection.txt", "vuln-xss.txt"):
        content = _read(name)
        for tok in ("parameter_graph", "SinkCallSite", "static_dataflow_hints"):
            assert tok not in content, f"{name} 引确定性 token: {tok}"
