"""AuthValidationWorkflow 编排:log_phase(auth-validation) → probe → 透传 result。"""
import json

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
    async def log_phase_start_activity(i, steps=None, intents=None):
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
async def test_workflow_declares_four_progress_steps():
    """步骤条前提：AuthValidationWorkflow 经 log_phase_start_activity 声明 4 步 PhaseEvent
    （navigate/fill_credentials/submit/verify_session），与 log_milestone 工具的 step key 同源。"""
    declared = []

    @activity.defn
    async def log_phase_start_activity(i, steps=None, intents=None):
        phase = getattr(i, "phase", None) or (i.get("phase") if isinstance(i, dict) else None)
        declared.append((phase, list(steps or [])))

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
            env.client, task_queue="tq-steps",
            workflows=[AuthValidationWorkflow],
            activities=[log_phase_start_activity, setup_display,
                        run_auth_validation_probe, finalize_summary],
        ):
            await env.client.execute_workflow(
                AuthValidationWorkflow.run, inp, id="w-steps", task_queue="tq-steps"
            )
    # auth-validation 阶段声明了 4 步
    assert declared, "workflow 必须调 log_phase_start_activity"
    phase, steps = declared[0]
    assert phase == "auth-validation"
    assert steps == ["navigate", "fill_credentials", "submit", "verify_session"]


@pytest.mark.asyncio
async def test_workflow_requires_web_url():
    @activity.defn
    async def log_phase_start_activity(i, steps=None, intents=None):
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
    async def log_phase_start_activity(i, steps=None, intents=None):
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
    async def log_phase_start_activity(i, steps=None, intents=None):
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


@pytest.mark.asyncio
async def test_probe_wires_agent_observability(tmp_path, monkeypatch):
    """块2: standalone run_auth_validation_probe 必须接可观测性——传 tool_audit_logger 给
    validate_authentication（→ agent tool call 落 events.ndjson）+ 调 start_agent/end_agent
    （→ AgentEvent 落盘）。否则测试登录过程对前端完全不可见（只有 PhaseEvent/SummaryEvent）。

    真实 setup_display（注册 session 写 event_file）+ 真实 probe + mock validate_authentication
    （只验接线，agent 真实动作留真机）。
    """
    event_file = tmp_path / "events.ndjson"
    captured: dict = {}

    async def fake_validate(**kwargs):
        captured["tool_audit_logger"] = kwargs.get("tool_audit_logger")
        return AuthValidationResult(success=True)

    monkeypatch.setattr(activities, "validate_authentication", fake_validate)

    inp = BlackboxAuthValidationInput(
        web_url="http://target/", config_path="/c.yaml",
        workspace_path=str(tmp_path), event_file=str(event_file),
    )
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client, task_queue="tq-probe-obs",
            workflows=[AuthValidationWorkflow],
            activities=[activities.log_phase_start_activity,
                        activities.setup_display,
                        activities.run_auth_validation_probe,
                        activities.finalize_summary],
        ):
            result = await env.client.execute_workflow(
                AuthValidationWorkflow.run, inp, id="w-probe-obs", task_queue="tq-probe-obs",
            )

    assert result.success is True
    # probe 把 tool_audit_logger 透传给 validate_authentication（→ ToolCallEvent 落盘通道）
    assert captured.get("tool_audit_logger") is not None
    # start_agent/end_agent → AgentEvent(start) + AgentEvent(end) 落 events.ndjson
    events = [json.loads(ln) for ln in event_file.read_text("utf-8").splitlines() if ln.strip()]
    agent_events = [e for e in events if e.get("type") == "AgentEvent"]
    assert any(e.get("event") == "start" for e in agent_events)
    assert any(e.get("event") == "end" for e in agent_events)


@pytest.mark.asyncio
async def test_workflow_auth_precheck_starts_and_cleans_host_proxy():
    """独立组合 auth workflow 与黑盒 workflow 一样使用 HOST snapshot。"""
    calls = []

    def get(input_value, key):
        return input_value.get(key) if isinstance(input_value, dict) else getattr(input_value, key)

    @activity.defn
    async def setup_display(i):
        calls.append(("setup_display", get(i, "host_mappings"), get(i, "proxy_url")))

    @activity.defn
    async def run_host_proxy_setup(i):
        calls.append(("setup_proxy", dict(get(i, "host_mappings"))))
        return "http://127.0.0.1:19090"

    @activity.defn
    async def log_phase_start_activity(i, steps=None, intents=None):
        calls.append(("phase", get(i, "proxy_url")))

    @activity.defn
    async def run_auth_validation_probe(i):
        calls.append(("probe", get(i, "proxy_url"), dict(get(i, "host_mappings"))))
        return AuthValidationResult(success=True)

    @activity.defn
    async def stop_host_proxy(proxy_url):
        calls.append(("stop_proxy", proxy_url))

    @activity.defn
    async def finalize_summary(i, summary):
        calls.append(("finalize", get(i, "proxy_url")))

    inp = BlackboxAuthValidationInput(
        web_url="https://target.internal/",
        config_path="/cfg.yaml",
        workspace_path="/wp",
        host_mappings={"target.internal": "10.0.0.2"},
    )
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client,
            task_queue="tq-auth-host",
            workflows=[AuthValidationWorkflow],
            activities=[setup_display, run_host_proxy_setup,
                        log_phase_start_activity, run_auth_validation_probe,
                        stop_host_proxy, finalize_summary],
        ):
            result = await env.client.execute_workflow(
                AuthValidationWorkflow.run, inp, id="w-auth-host", task_queue="tq-auth-host",
            )

    assert result.success is True
    assert ("setup_proxy", {"target.internal": "10.0.0.2"}) in calls
    assert ("probe", "http://127.0.0.1:19090", {"target.internal": "10.0.0.2"}) in calls
    assert ("stop_proxy", "http://127.0.0.1:19090") in calls
    assert calls.index(next(c for c in calls if c[0] == "setup_proxy")) < \
        calls.index(next(c for c in calls if c[0] == "probe"))
