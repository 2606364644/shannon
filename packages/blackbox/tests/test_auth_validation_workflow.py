"""AuthValidationWorkflow 编排:log_phase(auth-validation) → probe → 透传 result。"""
import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from supernova_core.services.validate_authentication import AuthValidationResult
from supernova_blackbox.pipeline.shared import BlackboxAuthValidationInput
from supernova_blackbox.pipeline.workflows import AuthValidationWorkflow


@pytest.mark.asyncio
async def test_workflow_orchestration_returns_probe_result():
    phases = []

    @activity.defn
    async def log_phase_start_activity(i):
        phases.append(getattr(i, "phase", None) or (i.get("phase") if isinstance(i, dict) else None))

    @activity.defn
    async def run_auth_validation_probe(i):
        return AuthValidationResult(success=True)

    inp = BlackboxAuthValidationInput(
        web_url="http://target/", config_path="/c.yaml", workspace_path="/wp"
    )
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client, task_queue="tq-auth",
            workflows=[AuthValidationWorkflow],
            activities=[log_phase_start_activity, run_auth_validation_probe],
        ):
            result = await env.client.execute_workflow(
                AuthValidationWorkflow.run, inp, id="w-auth", task_queue="tq-auth"
            )
    assert phases == ["auth-validation"]
    # AuthValidationResult 是 dataclass,execute_workflow 反序列化为对象实例(非 dict),
    # 故用属性访问(对齐 test_auth_validation_probe.py:38 / test_exploit_only_stage1.py:199)。
    assert result.success is True


@pytest.mark.asyncio
async def test_workflow_requires_web_url():
    @activity.defn
    async def log_phase_start_activity(i):
        pass

    @activity.defn
    async def run_auth_validation_probe(i):
        return AuthValidationResult(success=True)

    inp = BlackboxAuthValidationInput(web_url="", config_path="/c.yaml", workspace_path="/wp")
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client, task_queue="tq-auth2",
            workflows=[AuthValidationWorkflow],
            activities=[log_phase_start_activity, run_auth_validation_probe],
        ):
            with pytest.raises(Exception):
                # workflow 对 web_url 缺失抛 ApplicationError(non_retryable=True),
                # workflow 执行即终态失败 → execute_workflow 抛(对齐 whitebox/workflows.py:475)。
                await env.client.execute_workflow(
                    AuthValidationWorkflow.run, inp, id="w-auth2", task_queue="tq-auth2",
                )
