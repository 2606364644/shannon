"""AuthValidationWorkflow finalize 真实 status（2026-08-28 authcheck 超时丢账修复 · 状态失真半）。

现场（NodeGoat-20260827-152204）：probe 3 次 start_to_close_timeout 全超时 →
ActivityError 穿 run() → workflow failed；但 finally 里 finalize_summary 硬编码
{"status": "completed"} → scan_end/SummaryEvent 报 completed——30 分钟白烧显示
"成功完成"。

修复语义：probe 正常返回（含业务性 success=False 结论）→ completed（生命周期完整，
拿到结论）；probe 抛异常（超时耗尽 / 引擎错）→ finalize 收 status=failed + error。
usage 聚合无需改：finalize_summary 已从 session.get_metrics()（MetricsTracker，Bug 1
cancel 落账后自动累积）取 total_cost_usd。
"""
import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from supernova_core.services.validate_authentication import AuthValidationResult
from supernova_blackbox.pipeline.shared import BlackboxAuthValidationInput
from supernova_blackbox.pipeline.workflows import AuthValidationWorkflow


def _input():
    return BlackboxAuthValidationInput(
        web_url="http://target/", config_path="/c.yaml", workspace_path="/wp"
    )


@pytest.mark.asyncio
async def test_workflow_probe_exception_finalizes_failed_status():
    """probe 抛异常（模拟超时耗尽）→ finalize 收 status=failed + error；workflow 失败。"""
    summaries = []

    @activity.defn
    async def log_phase_start_activity(i, steps=None, intents=None):
        pass

    @activity.defn
    async def setup_display(i):
        pass

    @activity.defn
    async def run_auth_validation_probe(i):
        # non_retryable：免 3 次重试 backoff，直接进入异常路径（语义等价超时耗尽）
        raise ApplicationError("simulated timeout exhaustion", non_retryable=True)

    @activity.defn
    async def finalize_summary(i, summary):
        summaries.append(dict(summary))

    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client, task_queue="tq-auth-fail",
            workflows=[AuthValidationWorkflow],
            activities=[log_phase_start_activity, setup_display,
                        run_auth_validation_probe, finalize_summary],
        ):
            with pytest.raises(Exception):
                await env.client.execute_workflow(
                    AuthValidationWorkflow.run, _input(),
                    id="w-auth-fail", task_queue="tq-auth-fail"
                )

    assert summaries, "失败路径 finally 也必须跑 finalize（写 scan_end）"
    assert summaries[-1].get("status") == "failed", \
        "probe 异常 → 真实 failed（修复前硬编码 completed）"
    assert summaries[-1].get("error"), "失败原因必须进 summary.error"


@pytest.mark.asyncio
async def test_workflow_probe_business_failure_stays_completed():
    """probe 正常返回 success=False（业务性登录失败结论）→ 生命周期完整 → completed。"""
    summaries = []

    @activity.defn
    async def log_phase_start_activity(i, steps=None, intents=None):
        pass

    @activity.defn
    async def setup_display(i):
        pass

    @activity.defn
    async def run_auth_validation_probe(i):
        return AuthValidationResult(success=False, failure_point="out_of_band")

    @activity.defn
    async def finalize_summary(i, summary):
        summaries.append(dict(summary))

    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client, task_queue="tq-auth-biz",
            workflows=[AuthValidationWorkflow],
            activities=[log_phase_start_activity, setup_display,
                        run_auth_validation_probe, finalize_summary],
        ):
            result = await env.client.execute_workflow(
                AuthValidationWorkflow.run, _input(),
                id="w-auth-biz", task_queue="tq-auth-biz"
            )
    assert result.success is False  # 结论透传
    assert summaries[-1].get("status") == "completed"


@pytest.mark.asyncio
async def test_workflow_probe_success_finalizes_completed():
    """成功路径回归：status=completed。"""
    summaries = []

    @activity.defn
    async def log_phase_start_activity(i, steps=None, intents=None):
        pass

    @activity.defn
    async def setup_display(i):
        pass

    @activity.defn
    async def run_auth_validation_probe(i):
        return AuthValidationResult(success=True)

    @activity.defn
    async def finalize_summary(i, summary):
        summaries.append(dict(summary))

    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client, task_queue="tq-auth-ok",
            workflows=[AuthValidationWorkflow],
            activities=[log_phase_start_activity, setup_display,
                        run_auth_validation_probe, finalize_summary],
        ):
            await env.client.execute_workflow(
                AuthValidationWorkflow.run, _input(),
                id="w-auth-ok", task_queue="tq-auth-ok"
            )
    assert summaries[-1].get("status") == "completed"
    assert "error" not in summaries[-1] or not summaries[-1].get("error")


@pytest.mark.asyncio
async def test_workflow_probe_timeout_seconds_input_controls_window():
    """probe_timeout_seconds 入参控制 probe 窗口（env 经 sandbox 外解析后由 input 传入）。

    容量铁律（CLAUDE.md §1）同型：authcheck 3×10min 全超时白烧 30 分钟，窗口须可按
    provider 实测重估。fake probe sleep 2s + 窗口 1s → ActivityError(timeout) →
    workflow failed + finalize 收 failed（顺带回归 timeout 路径的真实 status）。
    """
    summaries = []

    @activity.defn
    async def log_phase_start_activity(i, steps=None, intents=None):
        pass

    @activity.defn
    async def setup_display(i):
        pass

    @activity.defn
    async def run_auth_validation_probe(i):
        import asyncio as _asyncio
        await _asyncio.sleep(2)  # 超过 1s 窗口
        return AuthValidationResult(success=True)

    @activity.defn
    async def finalize_summary(i, summary):
        summaries.append(dict(summary))

    inp = _input()
    inp.probe_timeout_seconds = 1
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client, task_queue="tq-auth-tmo",
            workflows=[AuthValidationWorkflow],
            activities=[log_phase_start_activity, setup_display,
                        run_auth_validation_probe, finalize_summary],
        ):
            with pytest.raises(Exception):
                await env.client.execute_workflow(
                    AuthValidationWorkflow.run, inp,
                    id="w-auth-tmo", task_queue="tq-auth-tmo"
                )
    assert summaries, "timeout 路径 finally 也必须跑 finalize"
    assert summaries[-1].get("status") == "failed"
    assert "timed out" in (summaries[-1].get("error") or "")
