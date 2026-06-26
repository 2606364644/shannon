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

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOWS_PY = REPO_ROOT / "packages/whitebox/src/shannon_whitebox/pipeline/workflows.py"


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
    # NOTE: VULN_CLASSES_TESTED is NOT forbidden — it is a user-config placeholder
    # (manager.py:114 reads config.vuln_classes, same category as {{WEB_URL}} /
    # {{REPO_PATH}} / {{DELIVERABLES_PATH}}), not a deterministic-layer product.
    # Task 4 误判它为确定性耦合并删除；本 fix (controller 识别) 已恢复。
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


def test_fusion_guarded_by_enable_llm_track():
    """反方向 fusion（run_merge_sink_reports / run_entry_point_fusion）必须受
    enable_llm_track 显式守卫，而非靠 PRE_RECON 不产出文件间接降级。

    这两个 fusion activity 把 LLM pre-recon 产出的 sink/entry-point 与确定性层
    合并——它们语义上依赖 LLM 轨产物，应在 LLM 轨关闭时显式跳过整个 activity，
    而不是靠文件不存在间接降级。静态 grep 守卫（workflow 编排逻辑单元测成本高，
    真机行为靠 Task 7 冒烟验证）。"""
    text = WORKFLOWS_PY.read_text()
    # 找到两个 fusion activity 调用，确认它们都在 if input.enable_llm_track: 块内。
    for fusion_activity in ("run_merge_sink_reports", "run_entry_point_fusion"):
        assert fusion_activity in text, f"{fusion_activity} 调用点消失？"
    # 守卫模式：两个 fusion 调用前应有 if input.enable_llm_track:
    assert "if input.enable_llm_track:" in text, (
        "workflows.py 缺 enable_llm_track 守卫（CLAUDE.md §1：fusion 需显式守卫）"
    )
