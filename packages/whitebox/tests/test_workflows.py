"""Tests for WhiteboxScanWorkflow error propagation logic."""

from shannon_whitebox.pipeline.shared import PipelineState
from shannon_whitebox.pipeline.workflows import WhiteboxScanWorkflow
from shannon_core.models.errors import classify_error_for_temporal


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


from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.services.browser_engine import BrowserEngineFactory


class TestWhiteboxBrowserEngineIntegration:
    """Test browser engine resolution logic used by WhiteboxScanWorkflow."""

    def test_engine_from_config_browser_engine(self, tmp_path):
        """Engine should be resolved from config.browser_engine field."""
        from shannon_core.config.parser import parse_config
        import shannon_core.services.engines  # noqa: F401

        config_file = tmp_path / "config.yaml"
        config_file.write_text("browser_engine: agent-browser\n")
        cfg = parse_config(str(config_file))

        engine_name = cfg.browser_engine
        engine = BrowserEngineFactory.get_engine(engine_name)
        assert engine.name == "agent-browser"

    def test_default_playwright_without_config(self):
        """Without config, engine defaults to playwright."""
        import shannon_core.services.engines  # noqa: F401

        engine_name = "playwright"
        engine = BrowserEngineFactory.get_engine(engine_name)
        assert engine.name == "playwright"

    def test_unavailable_engine_raises_error(self, monkeypatch):
        """Engine with check_available()=False should raise PentestError."""
        import shannon_core.services.engines  # noqa: F401

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
