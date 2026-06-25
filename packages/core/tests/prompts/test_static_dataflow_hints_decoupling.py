"""CLAUDE.md §1 invariant: LLM-track prompts must not @include deterministic
static_dataflow_hints (legacy coupling, being removed).

The dual-track architecture requires the LLM track (vuln-*.txt + recon.txt
agents) to be self-sufficient: it must NOT consume deterministic-layer products
(especially `static_dataflow_hints`), because feeding deterministic results to
the LLM track makes it depend on a layer (GitNexus) that is frequently
unavailable / times out, breaking the track's independence.

`prompts/shared/_static-dataflow-hints.txt`'s `@include` is explicitly called
out in CLAUDE.md §1 as legacy coupling that should be progressively removed.
Task 7 (commit f7e0220) removed it from `vuln-injection.txt`; this test locks
the invariant across ALL prompts so it cannot be silently re-introduced.
"""
from pathlib import Path

# Anchor on this file's location so the path resolves regardless of pytest's
# cwd. Mirrors the existing pattern in tests/prompts/test_static_hints_render.py
# and test_vuln_injection_prompt.py (parents[4] = repo root, which holds prompts/).
PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"
INCLUDE_LINE = "@include(shared/_static-dataflow-hints.txt)"


def test_no_prompt_includes_static_dataflow_hints():
    offenders = sorted(
        str(p.relative_to(PROMPTS_DIR))
        for p in PROMPTS_DIR.rglob("*.txt")
        if INCLUDE_LINE in p.read_text()
    )
    assert not offenders, (
        f"CLAUDE.md §1 violation — these prompts still @include static-dataflow-hints "
        f"(legacy deterministic coupling): {offenders}"
    )
