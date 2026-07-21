# packages/whitebox/tests/pipeline/test_workflows_llm_track_gating.py
"""LLM-track gating 不变量: 源码级 AST 断言, 无需起 Temporal.

改后语义(2026-07-14, plan smooth-wandering-dolphin): `enable_llm_track` 只 gate
inj/xss/ssrf vuln agent(taint, GitNexus chain_verdict 主干兜底)。pre-recon / recon /
merge_sink_reports / authz/auth vuln agent 始终跑 —— GitNexus 做不了 recon 的角色模型 /
工作流语义(§7/§8.3, 是 authz Vertical/Context 的输入), 也做不了 authz Vertical/Context
本身 + auth 无轨。故「关 LLM 轨」从「关全部 LLM 分析」收窄为「只关 inj/xss/ssrf vuln agent」。
(部分回退 2026-07-09 gating spec 的「GitNexus 兜底 recon」前提。)

断言 workflows.py:
  - Gate A/B/C(pre-recon / recon / merge_sink_reports)移出门控: 不在任何 if enable_llm_track 块
  - Gate D(vuln tasks)保留: run_vuln_agent 在 body(开轨全跑) + else(关轨保 authz/auth)
  - Gate D else 用 DEGRADABLE_VULN_CLASSES 过滤掉 inj/xss/ssrf(结构断言; vt 是动态
    f-string 无法静态提取,故靠「else 引用 DEGRADABLE_VULN_CLASSES」保证过滤,行为靠真机冒烟)
  - 2 个 GitNexus judge + dual-track merge 仍在 gate 外(本就不受门控, 防回退)

spec: docs/superpowers/specs/2026-07-09-recon-llm-track-gating-design.md(部分回退, 见 plan)
"""
import ast
import inspect

from supernova_whitebox.pipeline import workflows


def _src() -> str:
    return inspect.getsource(workflows)


def _is_llm_track_test(test: ast.expr) -> bool:
    """True for `input.enable_llm_track` (ast.Attribute with attr enable_llm_track)."""
    return isinstance(test, ast.Attribute) and test.attr == "enable_llm_track"


def _execute_activity_call(node: ast.AST):
    """Return the Call node if it is `workflow.execute_activity(...)`, else None."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "execute_activity":
        return node
    return None


def _literal_agent_name(node: ast.AST) -> str | None:
    """Extract agent marker from `AgentName.PRE_RECON.value` -> 'PRE_RECON',
    or from a string literal. None if not statically extractable."""
    if isinstance(node, ast.Attribute) and node.attr == "value":
        inner = node.value
        if isinstance(inner, ast.Attribute) \
                and isinstance(inner.value, ast.Name) \
                and inner.value.id == "AgentName":
            return inner.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _activity_input_field(call: ast.Call, field: str) -> str | None:
    """Extract a field from the ActivityInput(...) in an execute_activity call.
    Handles BOTH literal keyword (ActivityInput(agent_name=X)) AND **dict unpack
    (ActivityInput(**{..., 'agent_name': X})) — the workflow uses the latter."""
    for a in list(call.args) + [kw.value for kw in call.keywords]:
        if not (isinstance(a, ast.Call) and isinstance(a.func, ast.Name)
                and a.func.id == "ActivityInput"):
            continue
        for kw in a.keywords:  # literal keyword
            if kw.arg == field:
                return _literal_agent_name(kw.value)
        for kw in a.keywords:  # **dict unpack: arg=None, value=Dict
            if kw.arg is None and isinstance(kw.value, ast.Dict):
                for k, v in zip(kw.value.keys, kw.value.values):
                    if isinstance(k, ast.Constant) and k.value == field:
                        return _literal_agent_name(v)
    return None


def _destructure_activity_call(call: ast.Call):
    """Return (activity_attr|None, agent_marker|None) for an execute_activity call."""
    attr = None
    if call.args and isinstance(call.args[0], ast.Attribute) \
            and isinstance(call.args[0].value, ast.Name) \
            and call.args[0].value.id == "activities":
        attr = call.args[0].attr
    return attr, _activity_input_field(call, "agent_name")


def _llm_track_branch_activity_calls(src: str) -> list[tuple[str, str | None, str]]:
    """For EVERY `if input.enable_llm_track:` block, return
    [(activity_attr, agent_marker|None, branch)] for each
    workflow.execute_activity(activities.<attr>, ...) call, branch in {'body','else'}.
    Multiple such `if` blocks are all scanned.
    """
    tree = ast.parse(src)
    out: list[tuple[str, str | None, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_llm_track_test(node.test):
            for branch, stmts in (("body", node.body), ("else", node.orelse)):
                for stmt in stmts:
                    for sub in ast.walk(stmt):
                        call = _execute_activity_call(sub)
                        if call:
                            attr, agent = _destructure_activity_call(call)
                            out.append((attr, agent, branch))
    return out


def _names_in_branch(src: str, branch: str) -> set[str]:
    """For every `if input.enable_llm_track:` block, return Name ids referenced
    in the given branch ('body' or 'else'). Used to assert the else branch
    references DEGRADABLE_VULN_CLASSES (the inj/xss/ssrf filter)."""
    tree = ast.parse(src)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_llm_track_test(node.test):
            stmts = node.body if branch == "body" else node.orelse
            for stmt in stmts:
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.Name):
                        names.add(sub.id)
    return names


def _extract_info_message(call: ast.Call) -> str | None:
    """Extract info_message from the ActivityInput(...) (handles **dict unpack)."""
    return _activity_input_field(call, "info_message")


def _info_messages_by_branch(src: str) -> list[tuple[str | None, str]]:
    """For every `if input.enable_llm_track:` block, return [(info_message|None, branch)]
    for each log_info_activity call's ActivityInput.info_message."""
    tree = ast.parse(src)
    out: list[tuple[str | None, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_llm_track_test(node.test):
            for branch, stmts in (("body", node.body), ("else", node.orelse)):
                for stmt in stmts:
                    for sub in ast.walk(stmt):
                        call = _execute_activity_call(sub)
                        if not call:
                            continue
                        attr, _ = _destructure_activity_call(call)
                        if attr != "log_info_activity":
                            continue
                        out.append((_extract_info_message(call), branch))
    return out


# --- Gate A/B/C: pre-recon / recon / merge_sink_reports 移出门控(始终跑) ---
# 这些是 authz Vertical/Context 的输入链(recon 的角色模型 §7 / 工作流 §8.3),
# GitNexus 完全不产 —— 关 LLM 轨不能停, 否则 authz 巧妇难为无米之炊。

def test_pre_recon_agent_outside_llm_gate():
    """pre-recon LLM 始终跑 —— recon 的前置, GitNexus 不产 pre_recon_deliverable 语义."""
    calls = _llm_track_branch_activity_calls(_src())
    in_gate = {b for attr, agent, b in calls
               if attr == "run_agent" and agent == "PRE_RECON"}
    assert in_gate == set(), (
        "PRE_RECON agent 不应在 enable_llm_track gate 内(关轨也跑, 是 authz 输入链), "
        f"实际出现在分支: {in_gate}")


def test_recon_agent_outside_llm_gate():
    """recon LLM 始终跑 —— authz Vertical/Context 的输入(角色模型/工作流), GitNexus 完全不产."""
    calls = _llm_track_branch_activity_calls(_src())
    in_gate = {b for attr, agent, b in calls
               if attr == "run_agent" and agent == "RECON"}
    assert in_gate == set(), (
        "RECON agent 不应在 gate 内(关轨也跑, 是 authz Vertical/Context 输入), "
        f"实际: {in_gate}")


def test_code_index_outside_llm_gate():
    """code_index 始终跑(GitNexus 确定性兜底根基, 不该被门控)."""
    calls = _llm_track_branch_activity_calls(_src())
    in_gate = {b for attr, _, b in calls if attr == "run_code_index"}
    assert in_gate == set(), (
        f"run_code_index 不应在 gate 内(始终跑), 实际: {in_gate}")


def test_merge_sink_reports_outside_llm_gate():
    """merge_sink_reports 始终跑(依赖 pre-recon deliverable; 保 pre-recon 则保它)."""
    calls = _llm_track_branch_activity_calls(_src())
    in_gate = {attr for attr, _, _ in calls if attr == "run_merge_sink_reports"}
    assert not in_gate, "run_merge_sink_reports 不应在 enable_llm_track gate 内"


# --- Gate D: vuln tasks 保留门控 —— body 开轨全跑 / else 关轨只保 authz/auth ---

def test_vuln_agent_in_both_branches():
    """run_vuln_agent 在 body(开轨全跑 5 类) + else(关轨保 authz/auth)。
    inj/xss/ssrf 在关轨靠 GitNexus chain_verdict 兜底(GitNexus 是 taint 主干)。"""
    calls = _llm_track_branch_activity_calls(_src())
    branches = {b for attr, _, b in calls if attr == "run_vuln_agent"}
    assert branches == {"body", "else"}, (
        "run_vuln_agent 应在 body(开轨全跑) + else(关轨保 authz/auth), "
        f"实际: {branches}")


def test_vuln_else_filters_degradable():
    """关轨 else 必须用 DEGRADABLE_VULN_CLASSES 过滤掉 inj/xss/ssrf。
    vt 是动态 f-string(AST 无法静态提取), 故用「else 引用 DEGRADABLE_VULN_CLASSES」
    做结构断言; 关轨实际只跑 authz/auth 的行为靠真机冒烟验证。"""
    names = _names_in_branch(_src(), "else")
    assert "DEGRADABLE_VULN_CLASSES" in names, (
        "关轨 else 必须引用 DEGRADABLE_VULN_CLASSES 过滤 inj/xss/ssrf, "
        f"实际 else 引用的 Name: {names}")


def test_vuln_skip_logs_info():
    """关轨 else 分支打 log_info 提示 inj/xss/ssrf skipped + authz/auth retained."""
    msgs = _info_messages_by_branch(_src())
    else_msgs = {m for m, b in msgs if b == "else" and m}
    assert any("inj/xss/ssrf" in m and "skipped" in m for m in else_msgs), (
        "关轨(else)须含 log_info 提示 'inj/xss/ssrf ... skipped', "
        f"实际 else 消息: {else_msgs}")


# --- GitNexus 轨防回退: 误关塌双轨 (characterization, 锁既有正确行为) ---

def test_gitnexus_judges_outside_llm_gate():
    """2 个 GitNexus judge 是确定性轨, 绝不能被 enable_llm_track gate(误关塌双轨)."""
    calls = _llm_track_branch_activity_calls(_src())
    gated = {attr for attr, _, b in calls}
    for judge in ("run_authz_gitnexus_judge",
                  "run_gitnexus_chain_verdict"):
        assert judge not in gated, (
            f"{judge} 是 GitNexus 确定性轨, 不得在 enable_llm_track gate 内(会塌双轨)")


def test_merge_dual_track_outside_llm_gate():
    """merge 是纯合并(容忍空轨), 不受 gate 控制."""
    calls = _llm_track_branch_activity_calls(_src())
    gated = {attr for attr, _, b in calls}
    assert "run_merge_dual_track_queues" not in gated, (
        "run_merge_dual_track_queues 不得在 enable_llm_track gate 内")


# --- 显示层对齐: 关轨 phase start 反映实际调度的 agent ---

def test_vuln_phase_start_uses_display_classes():
    """vulnerability-analysis phase start 的 step/intent 列表对齐实际调度: 用 vuln_display
    (关轨时排除 DEGRADABLE_VULN_CLASSES 的 inj/xss/ssrf), 不直接用 selected_classes ——
    避免关轨时前端显示 inj-vuln 等不跑的 agent(step/intent 长度可变, args 数量仍为 3)。"""
    tree = ast.parse(_src())
    found = False
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "vuln_phase_steps"):
            for arg in node.args:
                if any(isinstance(s, ast.Name) and s.id == "vuln_display"
                       for s in ast.walk(arg)):
                    found = True
    assert found, (
        "vuln_phase_steps 应引用 vuln_display(关轨对齐实际调度的 authz/auth), "
        "而非直接 selected_classes(会显示不跑的 inj-vuln 等)")
