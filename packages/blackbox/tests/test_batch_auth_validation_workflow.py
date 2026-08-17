"""BatchAuthValidationWorkflow 编排:串行跑 N 个 cred(各独立 setup/log_phase/probe/finalize),
失败 cred 不阻断后续;batch_progress query 返回 per-cred 进度供 web watcher 回填 verify_status。

语义(对齐 spec §2):认证测试批量选角色 = 逐个独立验证每个角色能否登录(非越权对比)。
本 workflow 串行 N 次 Branch A 单次登录,各 cred 独立 probe/events/进度。"""
import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from supernova_core.services.validate_authentication import AuthValidationResult
from supernova_blackbox.pipeline.shared import (
    BlackboxAuthValidationBatchItem, BlackboxAuthValidationBatchInput,
)
from supernova_blackbox.pipeline.workflows import BatchAuthValidationWorkflow


def _mock_activities(probe_results):
    """构造 mock activity 集合,run_auth_validation_probe 按 probe_results 顺序返回。
    calls 记录每步的 workspace_path(标记串行顺序 + 每 cred 独立调用)。"""
    calls = {"setup": [], "log_phase": [], "probe": [], "finalize": []}
    probe_iter = iter(probe_results)

    def _wp(i):
        return i.get("workspace_path") if isinstance(i, dict) else getattr(i, "workspace_path", None)

    @activity.defn
    async def setup_display(i):
        calls["setup"].append(_wp(i))

    @activity.defn
    async def log_phase_start_activity(i, steps=None, intents=None):
        calls["log_phase"].append(
            getattr(i, "phase", None) or (i.get("phase") if isinstance(i, dict) else None))

    @activity.defn
    async def run_auth_validation_probe(i):
        calls["probe"].append(_wp(i))
        return next(probe_iter)

    @activity.defn
    async def finalize_summary(i, summary):
        calls["finalize"].append(_wp(i))

    return setup_display, log_phase_start_activity, run_auth_validation_probe, finalize_summary, calls


def _items(n, **overrides):
    return [BlackboxAuthValidationBatchItem(
        cred_id=overrides.get("cred_id", f"c{i}"), web_url="http://t/",
        config_path=f"/c{i}.yaml", workspace_path=f"/wp{i}",
        event_file=f"/wp{i}/events.ndjson",
    ) for i in range(n)]


@pytest.mark.asyncio
async def test_batch_workflow_runs_items_serially():
    """3 cred 串行:probe 调用顺序 = items 顺序;每 cred 独立 setup/log_phase/probe/finalize。"""
    setup, log_phase, probe, finalize, calls = _mock_activities(
        [AuthValidationResult(success=True) for _ in range(3)])
    inp = BlackboxAuthValidationBatchInput(items=_items(3))
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-batch1", workflows=[BatchAuthValidationWorkflow],
                          activities=[setup, log_phase, probe, finalize]):
            results = await env.client.execute_workflow(
                BatchAuthValidationWorkflow.run, inp, id="w-batch1", task_queue="tq-batch1")
    # 串行:probe 顺序 = items 顺序(workspace_path 标记)
    assert calls["probe"] == ["/wp0", "/wp1", "/wp2"]
    # 每 cred 完整四件套(setup/log_phase/probe/finalize 各 3 次)
    assert len(calls["setup"]) == 3
    assert len(calls["log_phase"]) == 3
    assert len(calls["finalize"]) == 3
    # 返回 per-cred 终态
    assert len(results) == 3
    assert all(r["state"] == "success" for r in results)


@pytest.mark.asyncio
async def test_batch_workflow_failed_item_does_not_block_rest():
    """中间 cred 失败(success=False)不阻断后续 cred——对齐 Branch B 非 primary 失败不阻断语义。"""
    setup, log_phase, probe, finalize, calls = _mock_activities([
        AuthValidationResult(success=True),
        AuthValidationResult(success=False, failure_point="username_or_password", failure_detail="bad pw"),
        AuthValidationResult(success=True),
    ])
    inp = BlackboxAuthValidationBatchInput(items=_items(3))
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-batch2", workflows=[BatchAuthValidationWorkflow],
                          activities=[setup, log_phase, probe, finalize]):
            results = await env.client.execute_workflow(
                BatchAuthValidationWorkflow.run, inp, id="w-batch2", task_queue="tq-batch2")
    # 三个 cred 都跑了(失败的没阻断)
    assert calls["probe"] == ["/wp0", "/wp1", "/wp2"]
    assert [r["state"] for r in results] == ["success", "failed", "success"]
    assert results[1]["failure_point"] == "username_or_password"


@pytest.mark.asyncio
async def test_batch_workflow_probe_exception_does_not_block_rest():
    """probe activity 抛异常(重试耗尽等)→ 该 cred 标 failed,不阻断后续(activity 异常隔离 per-cred)。

    用 non_retryable ApplicationError 确定性失败(AUTH_VALIDATION_RETRY max 3,普通异常会重试到
    success 致断言挂);workflow 的 per-cred except 捕获后标 failed,继续跑 c1。
    """

    @activity.defn
    async def setup_display(i):
        pass

    @activity.defn
    async def log_phase_start_activity(i, steps=None, intents=None):
        pass

    @activity.defn
    async def run_auth_validation_probe(i):
        wp = i.get("workspace_path") if isinstance(i, dict) else getattr(i, "workspace_path", None)
        if wp == "/wp0":
            raise ApplicationError("probe crashed", non_retryable=True)
        return AuthValidationResult(success=True)

    @activity.defn
    async def finalize_summary(i, summary):
        pass

    inp = BlackboxAuthValidationBatchInput(items=_items(2))
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-batch3", workflows=[BatchAuthValidationWorkflow],
                          activities=[setup_display, log_phase_start_activity,
                                      run_auth_validation_probe, finalize_summary]):
            results = await env.client.execute_workflow(
                BatchAuthValidationWorkflow.run, inp, id="w-batch3", task_queue="tq-batch3")
    # c0 异常标 failed,c1 仍跑成功
    assert results[0]["state"] == "failed"
    assert results[1]["state"] == "success"


@pytest.mark.asyncio
async def test_batch_workflow_declares_four_steps_per_cred():
    """每 cred 声明 4 步 PhaseEvent(步骤条前提),step key 与 log_milestone 工具同源。"""
    declared = []

    @activity.defn
    async def setup_display(i):
        pass

    @activity.defn
    async def log_phase_start_activity(i, steps=None, intents=None):
        declared.append(list(steps or []))

    @activity.defn
    async def run_auth_validation_probe(i):
        return AuthValidationResult(success=True)

    @activity.defn
    async def finalize_summary(i, summary):
        pass

    inp = BlackboxAuthValidationBatchInput(items=_items(2))
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-batch4", workflows=[BatchAuthValidationWorkflow],
                          activities=[setup_display, log_phase_start_activity,
                                      run_auth_validation_probe, finalize_summary]):
            await env.client.execute_workflow(
                BatchAuthValidationWorkflow.run, inp, id="w-batch4", task_queue="tq-batch4")
    assert len(declared) == 2
    assert all(s == ["navigate", "fill_credentials", "submit", "verify_session"] for s in declared)


@pytest.mark.asyncio
async def test_batch_progress_query_returns_per_cred_state():
    """batch_progress query 返回各 cred state + all_done,供 web watcher 回填 verify_status。"""
    setup, log_phase, probe, finalize, _calls = _mock_activities([
        AuthValidationResult(success=True),
        AuthValidationResult(success=False, failure_point="totp_secret", failure_detail="bad totp"),
    ])
    inp = BlackboxAuthValidationBatchInput(items=_items(2))
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-batch5", workflows=[BatchAuthValidationWorkflow],
                          activities=[setup, log_phase, probe, finalize]):
            handle = await env.client.start_workflow(
                BatchAuthValidationWorkflow.run, inp, id="w-batch5", task_queue="tq-batch5")
            await handle.result()
            progress = await handle.query(BatchAuthValidationWorkflow.batch_progress)
    items = {it["cred_id"]: it for it in progress["items"]}
    assert items["c0"]["state"] == "success"
    assert items["c1"]["state"] == "failed"
    assert items["c1"]["failure_point"] == "totp_secret"
    assert progress["all_done"] is True


@pytest.mark.asyncio
async def test_batch_workflow_requires_nonempty_items():
    """空 items(non_retryable ApplicationError)——防 web 层误传空选中起空 workflow。"""

    @activity.defn
    async def setup_display(i):
        pass

    @activity.defn
    async def log_phase_start_activity(i, steps=None, intents=None):
        pass

    @activity.defn
    async def run_auth_validation_probe(i):
        return AuthValidationResult(success=True)

    @activity.defn
    async def finalize_summary(i, summary):
        pass

    inp = BlackboxAuthValidationBatchInput(items=[])
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-batch6", workflows=[BatchAuthValidationWorkflow],
                          activities=[setup_display, log_phase_start_activity,
                                      run_auth_validation_probe, finalize_summary]):
            with pytest.raises(Exception):
                await env.client.execute_workflow(
                    BatchAuthValidationWorkflow.run, inp, id="w-batch6", task_queue="tq-batch6")


@pytest.mark.asyncio
async def test_batch_workflow_threads_provider_config_to_probe():
    """完整 provider 配置穿线：profile 级 provider_config 灌入每个 cred 的 probe activity input。

    2026-08-17 根因：仅传 api_key 时 base_url/模型回落 worker env profile，
    key 与端点来自两套配置 → LLM 401 被误记为登录失败。"""
    seen = []

    @activity.defn
    async def setup_display(i):
        pass

    @activity.defn
    async def log_phase_start_activity(i, steps=None, intents=None):
        pass

    @activity.defn
    async def run_auth_validation_probe(i):
        seen.append(i.get("provider_config") if isinstance(i, dict) else getattr(i, "provider_config", None))
        return AuthValidationResult(success=True)

    @activity.defn
    async def finalize_summary(i, summary):
        pass

    provider_config = {"type": "openai_compatible", "base_url": "https://llm-proxy.example/v1",
                       "api_key": "user-key-x", "medium_model": "m"}
    inp = BlackboxAuthValidationBatchInput(
        items=_items(2), provider_config=provider_config)
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-batch-pc",
                          workflows=[BatchAuthValidationWorkflow],
                          activities=[setup_display, log_phase_start_activity,
                                      run_auth_validation_probe, finalize_summary]):
            await env.client.execute_workflow(
                BatchAuthValidationWorkflow.run, inp, id="w-batch-pc", task_queue="tq-batch-pc")
    # 每个 cred 的 probe 都拿到同一份完整配置（profile 级共享）
    assert seen == [provider_config, provider_config]
