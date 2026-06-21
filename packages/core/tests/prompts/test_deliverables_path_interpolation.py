from pathlib import Path
from shannon_core.prompts.manager import PromptManager


def test_interpolate_deliverables_and_scratchpad_path(tmp_path):
    """{{DELIVERABLES_PATH}}/{{SCRATCHPAD_PATH}} 应被 variables 中对应值替换。"""
    (tmp_path / "t.txt").write_text(
        "out: {{DELIVERABLES_PATH}}/pre_recon_deliverable.md\n"
        "scratch: {{SCRATCHPAD_PATH}}/notes.md\n"
        "repo: {{REPO_PATH}}\n",
        encoding="utf-8",
    )
    mgr = PromptManager(tmp_path)
    result = mgr.load_sync(
        "t",
        variables={
            "web_url": "",
            "repo_path": "/data/NodeGoat",
            "deliverables_path": "/ws/NodeGoat_sess/deliverables",
            "scratchpad_path": "/ws/NodeGoat_sess/scratchpad",
        },
    )
    assert "/ws/NodeGoat_sess/deliverables/pre_recon_deliverable.md" in result
    assert "/ws/NodeGoat_sess/scratchpad/notes.md" in result
    assert "repo: /data/NodeGoat" in result
