# packages/whitebox/tests/test_workflow_gitnexus_failfast.py
"""Task 4: GitNexus 轨 fail-fast 编排测试.

策略组合 (Strategy C + A):
  - C: 决策逻辑抽到纯函数 `_decide_gitnexus_failfast` -> 单测无 Temporal.
  - A: 源码级断言 (activity 存在 / 旧降级文案删 / fail-fast wiring / ordering 不破).

语义覆盖 (brief 三场景):
  1. 关轨 + DEGRADABLE(inj/xss/ssrf) fail -> 终止 (raise ApplicationFailure).
  2. 开轨 + fail -> 继续 (标红, 写状态产物).
  3. 关轨 + 仅 authz fail -> 不终止 (authz-vuln LLM 轨关轨仍跑, 做 Vertical/Context).
"""
import inspect
from pathlib import Path

import pytest

from shannon_whitebox.pipeline import workflows


# ── Strategy C: _decide_gitnexus_failfast 纯函数单测 ─────────────────────


def test_decide_disabled_track_with_degradable_failure_terminates():
    """场景 1: 关轨 + xss failed -> ['xss'] (-> workflow raise 终止)."""
    statuses = {
        "injection": {"status": "ok", "findings": 0},
        "xss": {"status": "failed", "reason": "builder raised: boom"},
        "ssrf": {"status": "ok", "findings": 0},
    }
    failed = workflows._decide_gitnexus_failfast(statuses, llm_track_enabled=False)
    assert failed == ["xss"], (
        f"关轨 + xss failed 应返 ['xss'] (无 LLM 兜底 -> 终止), 实际: {failed}")


def test_decide_disabled_track_authz_failure_does_not_terminate():
    """场景 3: 关轨 + 仅 authz failed (inj/xss/ssrf 全 ok) -> [] 不终止.

    authz-vuln LLM 轨关轨时仍跑 (DEGRADABLE 只含 inj/xss/ssrf),
    做 GitNexus 做不了的 Vertical/Context, 故 authz GitNexus fail 仅标红.
    """
    statuses = {
        "injection": {"status": "ok", "findings": 0},
        "xss": {"status": "ok", "findings": 0},
        "ssrf": {"status": "ok", "findings": 0},
        "authz": {"status": "failed", "reason": "verdict agent failed: timeout"},
    }
    failed = workflows._decide_gitnexus_failfast(statuses, llm_track_enabled=False)
    assert failed == [], (
        f"关轨 + 仅 authz failed 不应终止 (authz-vuln LLM 兜底), 实际: {failed}")


def test_decide_enabled_track_continues_regardless_of_failure():
    """场景 2: 开轨 + 多个 failed -> [] (继续, merger/report 读状态产物标红)."""
    statuses = {
        "injection": {"status": "failed", "reason": "boom"},
        "xss": {"status": "failed", "reason": "boom"},
        "ssrf": {"status": "ok", "findings": 0},
        "authz": {"status": "failed", "reason": "boom"},
    }
    failed = workflows._decide_gitnexus_failfast(statuses, llm_track_enabled=True)
    assert failed == [], (
        f"开轨即使全 fail 也不终止 (LLM 轨兜底), 实际: {failed}")


def test_decide_disabled_track_all_ok_continues():
    """关轨 + 全 ok -> [] (正常继续, 不终止)."""
    statuses = {
        "injection": {"status": "ok", "findings": 3},
        "xss": {"status": "ok", "findings": 1},
        "ssrf": {"status": "ok", "findings": 0},
        "authz": {"status": "ok", "findings": 2},
    }
    failed = workflows._decide_gitnexus_failfast(statuses, llm_track_enabled=False)
    assert failed == [], f"关轨 + 全 ok 不应终止, 实际: {failed}"


def test_decide_disabled_track_multiple_degradable_failures():
    """关轨 + inj+xss+ssrf 全 fail -> 3 项 (都无 LLM 兜底)."""
    statuses = {
        "injection": {"status": "failed", "reason": "a"},
        "xss": {"status": "failed", "reason": "b"},
        "ssrf": {"status": "failed", "reason": "c"},
    }
    failed = workflows._decide_gitnexus_failfast(statuses, llm_track_enabled=False)
    assert set(failed) == {"injection", "xss", "ssrf"}, (
        f"关轨 + 3 类全 fail 应返 3 项, 实际: {failed}")


def test_decide_missing_status_treated_as_ok():
    """关轨 + 某 DEGRADABLE 类状态缺失 (activity 未写) -> 不视作 failed."""
    statuses = {"authz": {"status": "failed", "reason": "boom"}}
    failed = workflows._decide_gitnexus_failfast(statuses, llm_track_enabled=False)
    assert failed == [], (
        f"关轨 + DEGRADABLE 状态缺失不应视作 failed, 实际: {failed}")


# ── Strategy A: 源码级 wiring 断言 ─────────────────────────────────────


def _wf_src() -> str:
    return inspect.getsource(workflows)


def _activities_src() -> str:
    from shannon_whitebox.pipeline import activities
    return inspect.getsource(activities)


def test_write_track_status_activity_defined():
    """activities.py 必须定义 write_track_status_activity (@activity.defn)."""
    from shannon_whitebox.pipeline import activities
    assert hasattr(activities, "write_track_status_activity"), (
        "activities.write_track_status_activity 必须存在 (Task 1 helper 的 activity 包装)")
    # temporalio @activity.defn 装饰的函数挂 __temporal_activity_definition 属性
    assert hasattr(activities.write_track_status_activity, "__temporal_activity_definition"), (
        "write_track_status_activity 必须是 @activity.defn 装饰")


def test_workflow_calls_write_track_status_activity():
    """workflow 必须调 write_track_status_activity (在两 GitNexus activity 之后)."""
    src = _wf_src()
    assert "write_track_status_activity" in src, (
        "workflow 必须调 activities.write_track_status_activity 写状态产物")
    j = src.find("run_authz_gitnexus_judge")
    v = src.find("run_gitnexus_chain_verdict")
    w = src.find("write_track_status_activity")
    m = src.find("run_merge_dual_track_queues")
    assert j != -1 and v != -1 and w != -1 and m != -1, (
        "四个 activity symbol 都应在 workflow 源码中出现")
    assert j < v < w < m, (
        "调用顺序必须: authz_judge -> chain_verdict -> write_track_status -> merge, "
        f"实际位置: judge={j}, verdict={v}, write={w}, merge={m}")


def test_workflow_old_degradation_strings_removed():
    """删两处 try/except 降级吞异常: 旧 'non-fatal, LLM-only track continues' 文案应消失.

    注: 其他 phase (attack-chain LLM/assembly v2) 的 non-fatal 文案与本任务无关, 保留.
    本断言只针对 GitNexus 编排段 (authz_judge / chain_verdict), 用 'GitNexus' 锚定.
    """
    src = _wf_src()
    # 旧文案形如:
    #   "Authz GitNexus judge failed (non-fatal, LLM-only track continues): ..."
    #   "GitNexus chain verdict failed (non-fatal, LLM-only track continues): ..."
    assert "non-fatal, LLM-only track continues" not in src, (
        "GitNexus 编排的 try/except 降级吞异常文案应已删除 (Task 4 改返回值驱动编排)")


def test_workflow_has_failfast_application_failure():
    """workflow 必须含 fail-fast raise: ApplicationFailure + type=GitNexusTrackFailure."""
    src = _wf_src()
    assert "GitNexusTrackFailure" in src, (
        "fail-fast raise 必须用 type='GitNexusTrackFailure' 标识")
    assert "ApplicationFailure" in src, (
        "workflow 必须直接 import + raise ApplicationFailure (Task 4 关轨终止)")


def test_workflow_uses_degradable_vuln_classes_for_failfast():
    """fail-fast 决策应用 DEGRADABLE_VULN_CLASSES (已 import, 勿用字面量).

    Strategy C 把决策抽到 _decide_gitnexus_failfast 纯函数; 该函数必须引用
    DEGRADABLE_VULN_CLASSES (而非字面量 "injection","xss","ssrf").
    """
    helper_src = inspect.getsource(workflows._decide_gitnexus_failfast)
    assert "DEGRADABLE_VULN_CLASSES" in helper_src, (
        "_decide_gitnexus_failfast 必须引用 DEGRADABLE_VULN_CLASSES (已 import, DRY), "
        f"实际 helper 源码: {helper_src}")


def test_workflow_track_statuses_field_passed_to_activity():
    """workflow 传 track_statuses 给 write_track_status_activity (via ActivityInput)."""
    src = _wf_src()
    assert "track_statuses" in src, (
        "workflow 必须把 _statuses dict 经 ActivityInput.track_statuses 传给 activity")


def test_activity_input_has_track_statuses_field():
    """ActivityInput dataclass 必须有 track_statuses 可选字段."""
    from shannon_whitebox.pipeline.shared import ActivityInput
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(ActivityInput)}
    assert "track_statuses" in field_names, (
        f"ActivityInput 必须有 track_statuses 字段, 实际字段: {field_names}")
    # 默认值必须是空 dict (not shared mutable default)
    inp = ActivityInput(repo_path="/tmp/x")
    assert inp.track_statuses == {}, (
        f"track_statuses 默认应为空 dict, 实际: {inp.track_statuses!r}")


def test_write_track_status_activity_calls_helper():
    """write_track_status_activity 必须 import + 调 Task 1 的 write_track_status helper.

    铁律 (CLAUDE.md §1): 状态产物只给 workflow/merger/report 用, 绝不喂 LLM 轨 prompt.
    activity 是 helper 的薄包装 (不直接写文件), 守「不假估算」.
    """
    src = _activities_src()
    # 函数体内 import + 调 write_track_status
    assert "from shannon_core.code_index.gitnexus_track_status import write_track_status" in src, (
        "write_track_status_activity 必须 import Task 1 的 write_track_status helper (薄包装)")
    assert "write_track_status(" in src, (
        "write_track_status_activity 必须调 write_track_status(deliverables, ...)")
