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
