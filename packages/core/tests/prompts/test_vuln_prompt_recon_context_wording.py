"""vuln prompt RECON_CONTEXT 措辞锁定（spec 2026-09-01 §4.5）。

digest schema v2 后注入内容是六节分节摘要（endpoints/authz/injection/xss/
ssrf/auth），不再限于 recon §4+§8——说明措辞不得再引用具体节号，否则
vuln agent 会误以为摘要只覆盖那两节、忽略其余节的先验线索。
"""
from pathlib import Path

# parents[4] = repo root（同 test_static_dataflow_hints_decoupling.py 模式）。
PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"

VULN_PROMPTS = (
    "vuln-injection.txt",
    "vuln-xss.txt",
    "vuln-ssrf.txt",
    "vuln-authz.txt",
    "vuln-auth.txt",
)


def test_vuln_prompts_do_not_reference_specific_recon_sections():
    for name in VULN_PROMPTS:
        content = (PROMPTS_DIR / name).read_text(encoding="utf-8")
        assert "§4 + §8" not in content, (
            f"{name} 仍引用具体节号——六节摘要下措辞应与注入内容一致")
        assert "{{RECON_CONTEXT}}" in content, f"{name} 丢了占位符"


def test_vuln_prompts_keep_recon_as_source_of_truth_hint():
    """措辞保留「recon 原文为 SoT、空则直读」的指引。"""
    for name in VULN_PROMPTS:
        content = (PROMPTS_DIR / name).read_text(encoding="utf-8")
        assert "source of truth" in content, f"{name} 丢了 SoT 指引"
        assert "{{RECON_CONTEXT}}" in content
