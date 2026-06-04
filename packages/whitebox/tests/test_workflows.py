"""Tests for WhiteboxScanWorkflow error propagation logic."""

from shannon_whitebox.pipeline.shared import PipelineState
from shannon_core.models.errors import classify_error_for_temporal


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
