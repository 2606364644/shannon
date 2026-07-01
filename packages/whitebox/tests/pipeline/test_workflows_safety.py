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


def test_verdict_activities_keep_standard_retry_policy():
    """guardrail: authz_judge / chain_verdict 仍用 retry_for('standard')。

    spec-0 Task 4 范围界定：只改超时，不改 retry_policy。切
    ``"gitnexus-verdict"`` 是 spec-1 的事。若有人提前切了 retry policy，
    这条测试会先红——提醒那是 spec-1 的改动，本 task 不应一起做。
    """
    src = inspect.getsource(workflows)
    # 两个 verdict activity 都应紧跟 retry_for("standard")，而非 gitnexus-verdict
    assert 'retry_for("standard")' in src, (
        "authz_judge/chain_verdict retry_policy 应保持 retry_for('standard')；"
        "切 'gitnexus-verdict' 是 spec-1 的事"
    )
    assert "gitnexus-verdict" not in src, (
        "workflows.py 中不应出现 'gitnexus-verdict' retry policy；"
        "那是 spec-1 启用多轮时的改动，本 task 不碰"
    )
