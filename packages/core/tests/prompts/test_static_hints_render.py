"""Spec C: static dataflow hints partial included into vuln/recon prompts."""
from pathlib import Path

from shannon_core.prompts.manager import PromptManager

PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"


def _manager() -> PromptManager:
    return PromptManager(PROMPTS_DIR)


VULN_PROMPTS = [
    "vuln-injection", "vuln-xss", "vuln-ssrf", "vuln-auth", "vuln-authz",
]


class TestStaticHintsInclude:
    def test_vuln_prompts_include_static_hints_partial(self):
        for name in VULN_PROMPTS:
            rendered = _manager().load_sync(name, {"repo_path": "/repo"}, pipeline_testing=False)
            assert "<static_dataflow_hints>" in rendered, f"{name} missing static hints block"
            assert "线索，不是结论" in rendered, f"{name} missing disclaimer"

    def test_recon_prompt_includes_static_hints_partial(self):
        rendered = _manager().load_sync("recon", {"repo_path": "/repo"}, pipeline_testing=False)
        assert "<static_dataflow_hints>" in rendered

    def test_pipeline_testing_mode_excludes_hints(self):
        # pipeline-testing 下的 vuln prompt 不 include 该 partial → 摘要关闭（Spec §6.4）
        rendered = _manager().load_sync(
            "vuln-injection", {"repo_path": "/repo"}, pipeline_testing=True,
        )
        assert "<static_dataflow_hints>" not in rendered
