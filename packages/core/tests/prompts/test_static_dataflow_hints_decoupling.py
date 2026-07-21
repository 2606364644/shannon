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
WORKFLOWS_PY = REPO_ROOT / "packages/whitebox/src/supernova_whitebox/pipeline/workflows.py"
ACTIVITIES_PY = REPO_ROOT / "packages/whitebox/src/supernova_whitebox/pipeline/activities.py"


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


# 新增禁用项时同步：{{占位符}} 加进 FORBIDDEN_PLACEHOLDERS；prompt_variables 键名加进 forbidden_keys；两者都属则两处都加。
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
    """反方向 fusion run_merge_sink_reports 必须受 enable_llm_track 显式守卫——
    用 AST 确认它的 Call 节点有 input.enable_llm_track 的 If 祖先（不是靠
    PRE_RECON 不产出文件间接降级）。

    CLAUDE.md §1 / Task 5: 它把 LLM pre-recon 产出的 sink 与确定性层合并，语义上
    依赖 LLM 轨产物，应在 LLM 轨关闭时显式跳过整个 activity，而非靠文件不存在
    间接降级。

    G6 例外（commit eb60763d, 2026-07-02）：run_entry_point_fusion 不在此列——它有
    确定性 OpenAPI schema 源，关 LLM 轨时也要跑（融合 schema 入口兜底，LLM 源靠
    deliverable 不存在内部 skip），故移出 enable_llm_track 守卫。该"必须在守卫外"
    由白盒侧 test_workflows_safety.py::test_entry_point_fusion_not_gated_by_llm_track
    锁定，此处不重复断言。

    注：workflows.py 另有 `if input.enable_llm_track:` 守卫（vuln-agent 块，预存），
    朴素 grep 该字符串会 assert-nothing（守卫在、fusion 不在守卫内也绿）。AST 遍历
    锁定 fusion Call 节点的 If 祖先 test 必须是 input.enable_llm_track。"""
    import ast

    tree = ast.parse(WORKFLOWS_PY.read_text())
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    # 仅守 run_merge_sink_reports（语义依赖 LLM sink deliverable，关 LLM 轨该停）。
    # run_entry_point_fusion 因 G6 有确定性 schema 源、移出守卫，见上面 docstring
    # 与白盒侧 test_entry_point_fusion_not_gated_by_llm_track。
    FUSION_ATTRS = ("run_merge_sink_reports",)

    # fusion activity 作为 execute_activity(activities.<name>, ...) 的首参传入
    # （不是 node.func）；扫每个 Call 的 args / keywords 里出现的 Attribute 引用。
    def fusion_refs(call):
        cands = list(call.args) + [kw.value for kw in call.keywords]
        return [c for c in cands
                if isinstance(c, ast.Attribute) and c.attr in FUSION_ATTRS]

    # 注意：parent map 必须按 Attribute 引用节点本身记，断言时向上找 If 祖先。
    fusion_refs_by_call = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            refs = fusion_refs(node)
            if refs:
                fusion_refs_by_call[id(node)] = refs
    assert fusion_refs_by_call, (
        "未找到 fusion activity 调用——workflows.py 结构变了？"
    )

    for call_id, refs in fusion_refs_by_call.items():
        for ref in refs:
            cur, guarded = parents.get(ref), False
            while cur is not None:
                if isinstance(cur, ast.If):
                    t = cur.test
                    if (isinstance(t, ast.Attribute)
                            and t.attr == "enable_llm_track"
                            and isinstance(t.value, ast.Name)
                            and t.value.id == "input"):
                        guarded = True
                        break
                cur = parents.get(cur)
            assert guarded, (
                f"CLAUDE.md §1 / Task 5: {ref.attr} 引用未在 "
                f"if input.enable_llm_track: 守卫内"
            )


def test_no_prompt_variables_inject_deterministic_track():
    """activities.py 不得给 LLM 轨 agent 的 prompt_variables 注入确定性 track 产物。
    白名单：authz_gitnexus_candidates（GitNexus 轨内部 IDOR 判定，合法）。"""
    text = ACTIVITIES_PY.read_text()
    # 新增禁用项时同步：{{占位符}} 加进 FORBIDDEN_PLACEHOLDERS；prompt_variables 键名加进 forbidden_keys；两者都属则两处都加。
    forbidden_keys = (
        "pre_recon_gitnexus_track",
        "recon_gitnexus_track",
        "framework_endpoints_summary",
        "taint_flow_summary",
        "chain_audit_input",
    )
    offenders = [k for k in forbidden_keys if f'prompt_variables["{k}"]' in text or f'"{k}":' in text]
    assert not offenders, (
        f"CLAUDE.md §1 violation — activities.py still injects deterministic "
        f"track into LLM-track prompt_variables: {offenders}"
    )
    # 白名单：authz_gitnexus_candidates 必须保留（轨内合法）
    assert "authz_gitnexus_candidates" in text, (
        "authz_gitnexus_candidates 白名单被误删（authz GitNexus 轨内判定需要它）"
    )


def test_forbidden_and_whitelisted_placeholders_are_disjoint():
    """白名单（GitNexus 轨内合法）与 forbidden（确定性→LLM 耦合）不得重叠。"""
    assert not (FORBIDDEN_PLACEHOLDERS & WHITELISTED_PLACEHOLDERS), \
        "白名单与黑名单重叠：无法判定合法性"


def test_pre_recon_prompt_does_not_reference_deterministic_code_index():
    """CLAUDE.md §1: pre-recon LLM 轨 prompt 不得引导 agent 读确定性 code_index.json
    （含 <starting_context> 类语义耦合，非仅 {{}} 占位符）。LLM 轨须纯自给。"""
    pre_recon = (PROMPTS_DIR / "pre-recon-code.txt").read_text()
    assert "code_index.json" not in pre_recon, (
        "pre-recon prompt 仍引用确定性 code_index.json（语义耦合，违背 LLM 轨自给）"
    )


def test_no_llm_track_prompt_references_auth_config_scan():
    """CLAUDE.md §1: LLM 轨 vuln-*.txt 不得引用 auth_config_scan(确定性 config 扫描产物)。

    2026-07-14 删除 auth GitNexus 轨(auth_config_scanner)后锁定——auth 回纯 LLM 轨,
    不得重建"确定性 config 扫描 → 喂 LLM 轨 lead"的桥(scanner lead 曾踩 §1 铁律:
    确定性产物喂 LLM 轨 prompt)。对标 test_pre_recon_prompt_does_not_reference_
    deterministic_code_index(语义耦合层守卫,非仅 {{}} 占位符)。"""
    offenders = sorted(
        str(p.relative_to(PROMPTS_DIR))
        for p in PROMPTS_DIR.glob("vuln-*.txt")
        if "auth_config_scan" in p.read_text()
    )
    assert not offenders, (
        f"CLAUDE.md §1: LLM 轨 vuln-*.txt 引用 auth_config_scan(确定性产物, 踩铁律): {offenders}"
    )
