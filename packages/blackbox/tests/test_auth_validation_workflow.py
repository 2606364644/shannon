"""AuthValidationWorkflow 编排:log_phase(auth-validation) → probe → 透传 result。"""
import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from supernova_core.services.validate_authentication import AuthValidationResult
from supernova_blackbox.pipeline import activities
from supernova_blackbox.pipeline.shared import BlackboxAuthValidationInput
from supernova_blackbox.pipeline.workflows import AuthValidationWorkflow


@pytest.mark.asyncio
async def test_workflow_orchestration_returns_probe_result():
    phases = []

    @activity.defn
    async def log_phase_start_activity(i):
        phases.append(getattr(i, "phase", None) or (i.get("phase") if isinstance(i, dict) else None))

    @activity.defn
    async def setup_display(i):
        pass

    @activity.defn
    async def run_auth_validation_probe(i):
        return AuthValidationResult(success=True)

    @activity.defn
    async def finalize_summary(i, summary):
        pass

    inp = BlackboxAuthValidationInput(
        web_url="http://target/", config_path="/c.yaml", workspace_path="/wp"
    )
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client, task_queue="tq-auth",
            workflows=[AuthValidationWorkflow],
            activities=[log_phase_start_activity, setup_display,
                        run_auth_validation_probe, finalize_summary],
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


@pytest.mark.asyncio
async def test_workflow_runs_setup_display_and_finalize_for_observability():
    """块1: AuthValidationWorkflow 必须在 probe 前调 setup_display（挂 AuditSession 写
    events.ndjson）、probe 后调 finalize_summary（drain+收尾），并把 event_file 透传给
    setup_display——这是验证过程可见（落盘）的前提。

    当前 workflow 只调 log_phase + probe，故 setup_display/finalize 不被调 → 断言失败（RED）。
    """
    calls = []

    @activity.defn
    async def log_phase_start_activity(i):
        calls.append(("log_phase", getattr(i, "phase", None)))

    @activity.defn
    async def setup_display(i):
        # mock 无类型注解 → temporalio 反序列化成 dict（真实 setup_display 有注解收到实例），
        # 故兼容 dict/对象两种形态（对齐 log_phase mock 的 isinstance(i, dict) 处理）。
        ef = i.get("event_file") if isinstance(i, dict) else getattr(i, "event_file", None)
        calls.append(("setup_display", ef))

    @activity.defn
    async def run_auth_validation_probe(i):
        calls.append(("probe", None))
        return AuthValidationResult(success=True)

    @activity.defn
    async def finalize_summary(i, summary):
        calls.append(("finalize", None))

    inp = BlackboxAuthValidationInput(
        web_url="http://target/", config_path="/c.yaml",
        workspace_path="/wp", event_file="/wp/events.ndjson",
    )
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client, task_queue="tq-obs",
            workflows=[AuthValidationWorkflow],
            activities=[log_phase_start_activity, setup_display,
                        run_auth_validation_probe, finalize_summary],
        ):
            result = await env.client.execute_workflow(
                AuthValidationWorkflow.run, inp, id="w-obs", task_queue="tq-obs",
            )
    names = [c[0] for c in calls]
    assert "setup_display" in names, "workflow 必须调 setup_display 落 events"
    assert "finalize" in names, "workflow 必须调 finalize_summary 收尾"
    # event_file 透传到 setup_display（agent 过程落盘的落点）
    setup_call = next(c for c in calls if c[0] == "setup_display")
    assert setup_call[1] == "/wp/events.ndjson"
    # 编排顺序：setup_display → probe → finalize
    assert names.index("setup_display") < names.index("probe") < names.index("finalize")
    assert result.success is True


@pytest.mark.asyncio
async def test_workflow_writes_events_file_via_real_setup_display(tmp_path):
    """块1 集成（spec 块5）：真实 setup_display + finalize_summary + mock probe → events.ndjson
    落盘非空。证明 wiring 正确接线：workflow 编排（上一测试已证）+ 真实 setup_display 写 event_file。
    agent 登录步骤由真实 validate_authentication 产生（留真机冒烟），此处 mock probe 只验证
    setup/finalize 把 events 文件建出来且非空（scan_start/scan_end 等结构化事件）。
    """
    event_file = tmp_path / "events.ndjson"

    @activity.defn
    async def log_phase_start_activity(i):
        pass

    @activity.defn
    async def run_auth_validation_probe(i):
        return AuthValidationResult(success=True)

    inp = BlackboxAuthValidationInput(
        web_url="http://target/", config_path="/c.yaml",
        workspace_path=str(tmp_path), event_file=str(event_file),
    )
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client, task_queue="tq-real",
            workflows=[AuthValidationWorkflow],
            activities=[log_phase_start_activity,
                        activities.setup_display, activities.finalize_summary,
                        run_auth_validation_probe],
        ):
            result = await env.client.execute_workflow(
                AuthValidationWorkflow.run, inp, id="w-real", task_queue="tq-real",
            )
    assert result.success is True
    assert event_file.exists(), "events.ndjson 应被 setup_display 创建"
    assert event_file.read_text("utf-8").strip(), "events.ndjson 不应为空"
