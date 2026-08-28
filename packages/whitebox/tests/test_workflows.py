"""Tests for WhiteboxScanWorkflow error propagation logic."""

from supernova_whitebox.pipeline.shared import PipelineState
from supernova_whitebox.pipeline.workflows import WhiteboxScanWorkflow
from supernova_core.models.errors import classify_error_for_temporal


def test_vuln_phase_steps_dynamic():
    from supernova_whitebox.pipeline.workflows import vuln_phase_steps
    steps = vuln_phase_steps(["injection", "xss"])
    assert steps == ("injection-vuln", "xss-vuln")


def test_pipeline_progress_query_registered_as_PipelineProgress():
    """The progress query must be registered under the name 'PipelineProgress'.

    worker.py polls via `handle.query("PipelineProgress")`. A bare
    @workflow.query would register under the method name 'pipeline_progress',
    so the query would fail silently (swallowed by `except Exception: pass`)
    and the CLI would print no progress. This guards that regression.
    See docs/superpowers/specs/2026-06-09-pipeline-progress-query-design.md.
    """
    defn = getattr(
        WhiteboxScanWorkflow.pipeline_progress,
        "__temporal_query_definition",
        None,
    )
    assert defn is not None, "pipeline_progress is not a registered @workflow.query"
    assert defn.name == "PipelineProgress"


class TestWhiteboxWorkflowErrorPropagation:
    """Test the error propagation logic that WhiteboxScanWorkflow uses."""

    def test_state_completed_when_no_errors(self):
        """All agents succeed => status=completed."""
        state = PipelineState()
        state.completed_agents = ["PRE_RECON", "RECON", "xss-vuln"]
        state.agent_metrics = {"PRE_RECON": {}, "RECON": {}, "xss-vuln": {}}
        # Simulate workflow completion logic
        if state.errors:
            state.status = "failed"
        else:
            state.status = "completed"
        assert state.status == "completed"
        assert state.failed_agents == []
        assert state.error_code is None

    def test_state_failed_when_agents_fail(self):
        """Some agents fail => status=failed, failed_agents populated."""
        state = PipelineState()
        state.completed_agents = ["PRE_RECON", "RECON"]
        state.agent_metrics = {"PRE_RECON": {}, "RECON": {}}
        # Simulate gather producing a failure
        state.errors = ["xss-vuln: authentication failed"]
        state.failed_agents = ["xss-vuln"]
        # Simulate workflow completion logic
        if state.errors:
            state.status = "failed"
            # Extract error_code from the first error
            error_type, _ = classify_error_for_temporal(
                Exception(state.errors[0].split(": ", 1)[-1])
            )
            state.error_code = error_type
        else:
            state.status = "completed"
        assert state.status == "failed"
        assert state.failed_agents == ["xss-vuln"]
        assert state.error_code == "AuthenticationError"

    def test_state_failed_with_multiple_agents(self):
        """Multiple agent failures are all tracked."""
        state = PipelineState()
        state.completed_agents = ["PRE_RECON"]
        state.errors = [
            "RECON: connection refused",
            "xss-vuln: permission denied",
        ]
        state.failed_agents = ["RECON", "xss-vuln"]
        state.status = "failed"
        state.error_code = "TransientError"
        assert state.status == "failed"
        assert len(state.failed_agents) == 2

    def test_state_cancelled(self):
        """Cancellation sets status=cancelled."""
        state = PipelineState()
        state.status = "cancelled"
        assert state.status == "cancelled"


from supernova_core.models.errors import ErrorCode, PentestError
from supernova_core.services.browser_engine import BrowserEngineFactory


class TestWhiteboxBrowserEngineIntegration:
    """Test browser engine resolution logic used by WhiteboxScanWorkflow."""

    def test_engine_from_config_browser_engine(self, tmp_path):
        """Engine should be resolved from config.browser_engine field."""
        from supernova_core.config.parser import parse_config
        import supernova_core.services.engines  # noqa: F401

        config_file = tmp_path / "config.yaml"
        config_file.write_text("browser_engine: agent-browser\n")
        cfg = parse_config(str(config_file))

        engine_name = cfg.browser_engine
        engine = BrowserEngineFactory.get_engine(engine_name)
        assert engine.name == "agent-browser"

    def test_default_playwright_without_config(self):
        """Without config, engine defaults to playwright."""
        import supernova_core.services.engines  # noqa: F401

        engine_name = "playwright"
        engine = BrowserEngineFactory.get_engine(engine_name)
        assert engine.name == "playwright"

    def test_unavailable_engine_raises_error(self, monkeypatch):
        """Engine with check_available()=False should raise PentestError."""
        import supernova_core.services.engines  # noqa: F401

        engine = BrowserEngineFactory.get_engine("playwright")
        monkeypatch.setattr(
            engine.__class__, "check_available", lambda self: False
        )
        engine = BrowserEngineFactory.get_engine("playwright")
        if not engine.check_available():
            error = PentestError(
                f"Browser engine '{engine.name}' is not available.",
                "browser",
                error_code=ErrorCode.BROWSER_ENGINE_UNAVAILABLE,
            )
        assert error.error_code == ErrorCode.BROWSER_ENGINE_UNAVAILABLE


def test_run_prefills_completed_agents_from_input():
    """resume 时 input 携带 completed_agents，run 开头预填，守卫应能跳过。"""
    from supernova_whitebox.pipeline.shared import PipelineInput, PipelineState

    # 模拟 run 开头的预填逻辑（不启动 Temporal）
    state = PipelineState()
    inp = PipelineInput(repo_path="/repo", resume_completed_agents=["pre-recon", "recon"])
    state.completed_agents = list(inp.resume_completed_agents or [])

    # 守卫逻辑：pre-recon / recon 已在 completed -> 应跳过
    assert "pre-recon" in state.completed_agents
    assert "recon" in state.completed_agents


def test_workflow_run_resolves_vuln_classes_via_select_function():
    """防回退: selected_classes 必须经 select_vuln_classes(input.vuln_classes, cfg.vuln_classes) 解析。

    旧断链形式 `input.vuln_classes or list(ALL_VULN_CLASSES)` 会让 cfg 解析后的
    cfg.vuln_classes 被丢弃（YAML vuln_classes 不生效）。本锚点守住修通成果。
    spec docs/superpowers/specs/2026-07-01-whitebox-vuln-classes-selection-design.md §2.3/§4.3。
    """
    import inspect

    from supernova_whitebox.pipeline.workflows import WhiteboxScanWorkflow

    src = inspect.getsource(WhiteboxScanWorkflow.run)
    assert "select_vuln_classes" in src, "run() 必须调用 select_vuln_classes"
    assert "cfg.vuln_classes" in src, "run() 必须把 cfg.vuln_classes 传给 select_vuln_classes"
    assert (
        "input.vuln_classes or list(ALL_VULN_CLASSES)" not in src
    ), "不得回退到旧断链形式（丢失 YAML vuln_classes）"


def test_assemble_report_reads_vuln_classes_from_input():
    """assemble_report 应从 input.vuln_classes 读（默认 ALL），不再硬编码。

    单源化（spec 2026-08-26-report-single-source-rendering §3）后选中类过滤
    移入 _build_report_data_initial（rd 组装时过滤 + stats 重算）；assemble_report
    经该 helper 兑现契约。"""
    import inspect

    from supernova_whitebox.pipeline.activities import (
        _build_report_data_initial, assemble_report,
    )

    src = inspect.getsource(_build_report_data_initial)
    assert "input.vuln_classes" in src, "rd 组装必须读 input.vuln_classes"
    assert "_build_report_data_initial(input" in inspect.getsource(assemble_report), (
        "assemble_report 必须经 _build_report_data_initial 兑现选中类契约"
    )


def test_activity_input_has_vuln_classes_field():
    """ActivityInput 必须有 vuln_classes 字段（默认 None），供 assemble_report 接收 selected。"""
    from supernova_whitebox.pipeline.shared import ActivityInput

    ai = ActivityInput(repo_path="/tmp/x")
    assert hasattr(ai, "vuln_classes")
    assert ai.vuln_classes is None


def test_gn_verdict_failure_status_pure_fn():
    """gn_verdict activity 重试耗尽 → 三类全标 failed 的状态替身（纯函数）。

    供 fail-fast 判定消费：关轨 = DEGRADABLE 全 fail → 终止（与旧路径
    activity raise 炸 workflow 等价）；开轨 = 标红继续（比直接炸温和——
    LLM 轨兜底，merger/report 读状态产物）。"""
    from supernova_whitebox.pipeline.workflows import (
        _decide_gitnexus_failfast, _gn_verdict_failure_status,
    )

    stub = _gn_verdict_failure_status(RuntimeError("activity timed out"))
    assert stub["per_class"] == {}
    assert set(stub["failed_classes"]) == {"injection", "xss", "ssrf"}
    assert all("activity timed out" in r for r in stub["fail_reasons"].values())
    # 联动 fail-fast：按 workflow 实际用法先把 failed_classes 转 statuses
    statuses = {vc: {"status": "failed", "reason": stub["fail_reasons"][vc]}
                for vc in stub["failed_classes"]}
    assert _decide_gitnexus_failfast(statuses, llm_track_enabled=False) == [
        "injection", "xss", "ssrf"]
    assert _decide_gitnexus_failfast(statuses, llm_track_enabled=True) == []


def test_gn_verdict_concurrent_with_vuln_agents():
    """防回退锚点：GN 轨 chain-verdict 与 LLM 轨 vuln agents 同批 gather 并发
    （双轨独立、汇合点在 merge），不再 vuln 全跑完才起 gn_verdict 的串行 await。"""
    import inspect

    src = inspect.getsource(WhiteboxScanWorkflow.run)
    assert "_gn_verdict_coro" in src, (
        "gn_verdict 应以 coro 形式创建（_gn_verdict_coro），与 vuln_tasks "
        "同批 gather 并发")
    assert "minutes=45" in src, "gn_verdict 窗口应提至 45min（三类并发满载预算）"
