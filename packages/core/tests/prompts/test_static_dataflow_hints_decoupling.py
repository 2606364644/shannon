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


# CLAUDE.md §1 铁律：LLM 轨 prompt 正文不得出现确定性 track 占位符（过程层注入）。
# 这些占位符由 renderer 把确定性 JSON 加工成 markdown 喂给 LLM 轨，破坏独立性。
FORBIDDEN_PLACEHOLDERS = {
    "PRE_RECON_GITNEXUS_TRACK",      # #1 pre-recon ← 本任务
    "RECON_GITNEXUS_TRACK",          # #2 recon
    "FRAMEWORK_ENDPOINTS_SUMMARY",   # #3 recon
    "TAINT_FLOW_SUMMARY",            # #4 recon（死占位符）
    "CHAIN_AUDIT_INPUT",             # #7 audit-tier1（死占位符）
    "VULN_CLASSES_TESTED",           # #7 audit-tier1（死占位符）
    # #5 pre-recon Phase 0 元数据占位符群
    "TOTAL_CHAINS", "AVG_CHAIN_DEPTH", "MAX_CHAIN_DEPTH", "UNRESOLVED_COUNT",
    "TOTAL_FILES", "INDEXED_SOURCE_FILES", "TEMPLATE_FILE_COUNT",
    "SCHEMA_FILE_COUNT", "CONFIG_FILE_COUNT", "DEGRADATION_WARNING_OR_NONE",
}

# 白名单：GitNexus 轨内部 LLM 判定（authz_gitnexus_judge）合法消费确定性 IDOR 候选，
# 属轨内判定（等同 chain_verdict），不是"确定性→LLM 轨"跨轨注入。
WHITELISTED_PLACEHOLDERS = {"AUTHZ_GITNEXUS_CANDIDATES"}


def test_no_llm_track_prompt_has_forbidden_placeholders():
    offenders = []
    for p in PROMPTS_DIR.rglob("*.txt"):
        text = p.read_text()
        for token in FORBIDDEN_PLACEHOLDERS:
            if "{{" + token + "}}" in text:
                offenders.append(f"{p.relative_to(PROMPTS_DIR)}: {{{{{token}}}}}")
    assert not offenders, (
        f"CLAUDE.md §1 violation — LLM-track prompts still embed deterministic "
        f"track placeholders (process-layer coupling): {sorted(offenders)}"
    )
