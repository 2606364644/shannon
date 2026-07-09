# packages/whitebox/tests/pipeline/test_workflows_llm_track_gating.py
"""LLM-track gating 不变量: 源码级 AST 断言, 无需起 Temporal.

对齐 pipeline/test_workflows_safety.py 的 source-level 模式。断言
workflows.py 里 `if input.enable_llm_track:` 的编排:
  - PRE_RECON / RECON / vuln agent 只在 gate body(开轨)调度
  - run_code_index 在 body + else 两分支都调度(无条件兜底)
  - 3 个 GitNexus judge + merge 完全在 gate 外(误关塌双轨)
  - completed_agents.append(PRE_RECON/RECON) 只在 body(关轨不标 completed → resume 语义)
  - 关轨 else 分支打 log_info_activity 提示 skip

spec: docs/superpowers/specs/2026-07-09-recon-llm-track-gating-design.md
"""
import ast
import inspect

from shannon_whitebox.pipeline import workflows


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
    Multiple such `if` blocks (pre-recon / merge_sink_reports / vuln) are all scanned.
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


def _completed_appends_by_branch(src: str) -> list[tuple[str | None, str]]:
    """For every `if input.enable_llm_track:` block, return [(agent_marker, branch)]
    for each `self._state.completed_agents.append(...)` call."""
    tree = ast.parse(src)
    out: list[tuple[str | None, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_llm_track_test(node.test):
            for branch, stmts in (("body", node.body), ("else", node.orelse)):
                for stmt in stmts:
                    for sub in ast.walk(stmt):
                        if (isinstance(sub, ast.Call)
                                and isinstance(sub.func, ast.Attribute)
                                and sub.func.attr == "append"
                                and isinstance(sub.func.value, ast.Attribute)
                                and sub.func.value.attr == "completed_agents"):
                            marker = _literal_agent_name(sub.args[0]) if sub.args else None
                            out.append((marker, branch))
    return out


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


# --- pre-recon / recon / vuln agent: 只在开轨(body)调度 ---

def test_pre_recon_agent_only_in_llm_on_body():
    calls = _llm_track_branch_activity_calls(_src())
    branches = {b for attr, agent, b in calls
                if attr == "run_agent" and agent == "PRE_RECON"}
    assert branches == {"body"}, (
        "PRE_RECON agent 必须只在 enable_llm_track body(开轨)调度, "
        f"实际出现在分支: {branches}")


def test_code_index_runs_in_both_branches():
    """code_index 是 GitNexus 确定性兜底根基, 必须开轨/关轨都跑."""
    calls = _llm_track_branch_activity_calls(_src())
    branches = {b for attr, _, b in calls if attr == "run_code_index"}
    assert branches == {"body", "else"}, (
        "run_code_index 必须在 body+else 两分支都调度(无条件兜底), "
        f"实际: {branches}")


def test_pre_recon_completion_only_in_body():
    """resume 语义: 关轨 skip 时不标 completed, 开轨重跑会补."""
    appends = _completed_appends_by_branch(_src())
    branches = {b for m, b in appends if m == "PRE_RECON"}
    assert branches == {"body"}, (
        "completed_agents.append(PRE_RECON) 必须只在 body(关轨不标 completed), "
        f"实际: {branches}")


def test_pre_recon_skip_logs_info():
    """关轨 else 分支必须打 log_info_activity, 消息含 'pre-recon' + 'skipped'."""
    msgs = _info_messages_by_branch(_src())
    else_msgs = {m for m, b in msgs if b == "else" and m}
    assert any("pre-recon" in m and "skipped" in m for m in else_msgs), (
        "关轨(else)分支必须含 log_info_activity 提示 'pre-recon ... skipped', "
        f"实际 else 消息: {else_msgs}")


# --- recon agent: 只在开轨(body)调度 ---

def test_recon_agent_only_in_llm_on_body():
    calls = _llm_track_branch_activity_calls(_src())
    branches = {b for attr, agent, b in calls
                if attr == "run_agent" and agent == "RECON"}
    assert branches == {"body"}, (
        "RECON agent 必须只在 enable_llm_track body(开轨)调度, "
        f"实际出现在分支: {branches}")


def test_recon_completion_only_in_body():
    """resume 语义: 关轨 skip 时不标 RECON completed."""
    appends = _completed_appends_by_branch(_src())
    branches = {b for m, b in appends if m == "RECON"}
    assert branches == {"body"}, (
        "completed_agents.append(RECON) 必须只在 body(关轨不标 completed), "
        f"实际: {branches}")


def test_recon_skip_logs_info():
    """关轨 else 分支必须打 log_info_activity, 消息含 'recon ... skipped' (非 pre-recon)."""
    msgs = _info_messages_by_branch(_src())
    else_msgs = {m for m, b in msgs if b == "else" and m}
    assert any("recon" in m and "skipped" in m and "pre-recon" not in m
               for m in else_msgs), (
        "关轨(else)分支必须含 log_info_activity 提示 'recon ... skipped', "
        f"实际 else 消息: {else_msgs}")
