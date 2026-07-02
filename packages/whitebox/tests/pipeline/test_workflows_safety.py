# packages/whitebox/tests/pipeline/test_workflows_safety.py
"""Workflow safety anchors: source-level invariants that the workflow module
must satisfy without spinning up Temporal.

These tests guard invariants that are awkward or expensive to assert via a live
workflow run (which would need Temporal). They inspect the workflow source
directly — the same pattern used by the authz judge ordering anchor in
``test_workflow_authz_judge_ordering.py``.
"""
import inspect
import re

from shannon_whitebox.pipeline import workflows


def _activity_timeout(src: str, activity_attr: str) -> str | None:
    """Return the ``start_to_close_timeout=...`` literal passed to the
    ``execute_activity`` call that references ``activity_attr``.

    Anchors on the activity symbol so the assertion can't match an unrelated
    timeout literal elsewhere in the workflow (e.g. the report-agent 15min).
    Returns ``None`` if the activity / timeout can't be located.
    """
    pat = re.compile(
        r"execute_activity\(\s*activities\."
        + re.escape(activity_attr)
        + r"[\s\S]*?start_to_close_timeout=(timedelta\([^)]+\))",
        re.MULTILINE,
    )
    m = pat.search(src)
    return m.group(1) if m else None


def test_authz_judge_timeout_increased_for_multi_turn():
    """authz_judge 超时须 = timedelta(minutes=30)（多轮 agent 窗口）。

    spec-0 Task 4: ``run_authz_gitnexus_judge`` 的
    ``start_to_close_timeout`` 从 10min 增到 30min，为多轮 verdict agent
    留出窗口。retry_policy 不在此 task 范围（仍 ``retry_for("standard")``）。

    Anchored on the activity symbol (not a bare substring) so it can't be
    satisfied by an unrelated ``timedelta(minutes=...)`` elsewhere.
    """
    src = inspect.getsource(workflows)
    got = _activity_timeout(src, "run_authz_gitnexus_judge")
    assert got is not None, "run_authz_gitnexus_judge execute_activity 未找到"
    assert got == "timedelta(minutes=30)", (
        f"authz_judge 超时应为 timedelta(minutes=30)（spec-0 Task 4，多轮 agent "
        f"窗口），实际为 {got}"
    )


def test_chain_verdict_timeout_increased_for_multi_turn():
    """chain_verdict 超时须 = timedelta(minutes=15)（多轮 agent 窗口）。

    spec-0 Task 4: ``run_gitnexus_chain_verdict`` 的
    ``start_to_close_timeout`` 从 5min 增到 15min。retry_policy 不变。

    Anchored on the activity symbol so it can't match the report-agent's
    existing 15min timeout (a false-green the naive substring test would hit).
    """
    src = inspect.getsource(workflows)
    got = _activity_timeout(src, "run_gitnexus_chain_verdict")
    assert got is not None, "run_gitnexus_chain_verdict execute_activity 未找到"
    assert got == "timedelta(minutes=15)", (
        f"chain_verdict 超时应为 timedelta(minutes=15)（spec-0 Task 4，多轮 agent "
        f"窗口），实际为 {got}"
    )


def test_authz_judge_uses_gitnexus_verdict_retry():
    """authz_judge retry 切 gitnexus-verdict（多轮 agent，max 3）。

    spec-1a Task 5: ``run_authz_gitnexus_judge`` 启用多轮 verdict agent
    后，standard（PRODUCTION_RETRY max 50）会把 agent 超时放大成数小时
    卡死；切 gitnexus-verdict（GITNEXUS_VERDICT_RETRY max 3）做有界重试。
    Anchored on the activity symbol so it can't match an unrelated retry_for
    elsewhere in the workflow.
    """
    src = inspect.getsource(workflows)
    m = re.search(
        r"run_authz_gitnexus_judge[\s\S]*?retry_policy=retry_for\(\"(\w[\w-]*)\"\)",
        src,
    )
    assert m is not None, "找不到 authz_judge retry_policy"
    assert m.group(1) == "gitnexus-verdict", (
        f"authz_judge 应切 gitnexus-verdict（spec-1a 多轮 agent，max 3），"
        f"实际为 {m.group(1)}"
    )


def test_chain_verdict_keeps_standard_retry():
    """chain_verdict (inj/xss/ssrf) 仍 standard（spec-1a 只切 authz_judge）。

    spec-1a 只把 authz_judge 切到 gitnexus-verdict；chain_verdict 不在本
    plan 切。Anchored on the activity symbol.
    """
    src = inspect.getsource(workflows)
    m = re.search(
        r"run_gitnexus_chain_verdict[\s\S]*?retry_policy=retry_for\(\"(\w[\w-]*)\"\)",
        src,
    )
    assert m is not None, "找不到 chain_verdict retry_policy"
    assert m.group(1) == "standard", (
        f"chain_verdict 应保持 retry_for('standard')（spec-1a 只切 authz_judge），"
        f"实际为 {m.group(1)}"
    )


def test_entry_point_fusion_not_gated_by_llm_track():
    """G6: run_entry_point_fusion 不被 enable_llm_track 门控（schema 源关 LLM 轨时仍跑）。

    断言 workflows.py 中 run_entry_point_fusion 调用在 if enable_llm_track 块外。
    """
    src = inspect.getsource(workflows)

    # 找 run_entry_point_fusion 的 execute_activity 调用块的 await 行（真正
    # 决定门控的缩进层级——`activities.run_X` 参数行始终比 await 多一级缩进，
    # 无法区分"在 if 内（await=20, activities=24）"与"同级（await=16,
    # activities=20）"）。锚 await 行才稳。
    fusion_line = None
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if "run_entry_point_fusion" in line and "activities." in line:
            # 回溯到对应的 await workflow.execute_activity( 行
            for k in range(i - 1, -1, -1):
                if "execute_activity(" in lines[k] and "workflow." in lines[k]:
                    fusion_line = k
                    break
            break

    assert fusion_line is not None, "找不到 run_entry_point_fusion 的 await execute_activity 调用"

    fusion_indent = len(lines[fusion_line]) - len(lines[fusion_line].lstrip())
    # 找 fusion 之前最近的 if enable_llm_track
    prev_enable_indent = None
    for j in range(fusion_line - 1, -1, -1):
        if "if input.enable_llm_track:" in lines[j]:
            prev_enable_indent = len(lines[j]) - len(lines[j].lstrip())
            break
    if prev_enable_indent is not None:
        assert fusion_indent <= prev_enable_indent, (
            f"run_entry_point_fusion 应在 enable_llm_track 块外"
            f"（await 缩进 {fusion_indent} <= if {prev_enable_indent}），"
            "G6 要求 schema 源关 LLM 轨时仍跑"
        )


def test_auth_gitnexus_judge_runs_after_config_scan():
    """spec-2b T6: run_auth_gitnexus_judge 在 run_auth_config_scan 之后编排。

    config_scan 先产 auth_gitnexus_queue.json 的 config 类条目（cookie/HSTS/
    CORS/JWT/限流），auth_judge 再追加逻辑类 verdict（session 固定/明文密码等）。
    源序锚点（inspect.getsource）保证二者顺序，避免被重排。
    """
    src = inspect.getsource(workflows)
    lines = src.splitlines()

    # 锚 activities.<name> 参数行（与 test_entry_point_fusion_not_gated 同手法），
    # 回溯到对应 await workflow.execute_activity( 行做源序比较。
    def _await_line(name: str) -> int | None:
        for i, line in enumerate(lines):
            if f"activities.{name}" in line:
                for k in range(i - 1, -1, -1):
                    if "execute_activity(" in lines[k] and "workflow." in lines[k]:
                        return k
                break
        return None

    i_config = _await_line("run_auth_config_scan")
    i_judge = _await_line("run_auth_gitnexus_judge")
    assert i_config is not None, "找不到 run_auth_config_scan 的 execute_activity 调用"
    assert i_judge is not None, "找不到 run_auth_gitnexus_judge 的 execute_activity 调用"
    assert i_config < i_judge, (
        f"run_auth_gitnexus_judge 应在 run_auth_config_scan 之后"
        f"（config_scan 先产 config 类 queue，auth_judge 再追加逻辑类），"
        f"源序 config={i_config} >= judge={i_judge}"
    )
