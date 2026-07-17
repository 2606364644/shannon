# packages/core/tests/code_index/test_track_status_decoupling.py
"""铁律:gitnexus_track_status 只给 workflow/merger/report 编排用,
绝不喂 LLM 轨 prompt / 不被 vuln collector 或 LLM 轨 agent import(守 CLAUDE.md §1)。"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
LLM_TRACK_DOMAINS = [
    "packages/core/src/shannon_core/collectors",   # vuln collector(LLM 轨 set_* 工具)
    "packages/core/src/shannon_core/renderers",     # LLM 轨 deliverable 渲染
    "prompts",                                       # LLM 轨 prompt(含 partial)
]
ALLOWED = {"pipeline/workflows.py", "pipeline/activities.py",
           "code_index/gitnexus_track_status.py", "code_index/dual_track_merger.py"}

def _python_files(root: Path):
    return [p for p in root.rglob("*.py") if p.is_file()]

def test_track_status_not_imported_in_llm_track():
    bad = []
    for domain in LLM_TRACK_DOMAINS:
        d = REPO / domain
        if not d.exists():
            continue
        for py in _python_files(d):
            rel = py.relative_to(REPO).as_posix()
            if rel in ALLOWED:
                continue
            txt = py.read_text(encoding="utf-8", errors="ignore")
            if "gitnexus_track_status" in txt or "track_statuses" in txt:
                bad.append(rel)
    assert not bad, f"铁律违反:gitnexus_track_status 泄漏进 LLM 轨域 {bad}"

def test_track_status_not_in_prompts():
    prompts_dir = REPO / "prompts"
    if not prompts_dir.exists():
        return
    bad = []
    for p in prompts_dir.rglob("*.txt"):
        if "gitnexus_track_status" in p.read_text(encoding="utf-8", errors="ignore"):
            bad.append(str(p.relative_to(REPO)))
    assert not bad, f"铁律违反:track_status 出现在 LLM 轨 prompt {bad}"
