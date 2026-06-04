"""Tests for shannon_core.utils.progress — AgentOutcome and format_exploit_summary."""

from shannon_core.utils.progress import (
    AgentOutcome,
    _format_duration,
    format_exploit_summary,
)


# ---------------------------------------------------------------------------
# AgentOutcome construction
# ---------------------------------------------------------------------------


class TestAgentOutcome:
    def test_completed_outcome_with_metrics(self):
        o = AgentOutcome(
            agent_name="injection-exploit",
            vuln_type="injection",
            status="completed",
            duration_s=272.0,
            cost_usd=0.1234,
            turns=12,
        )
        assert o.agent_name == "injection-exploit"
        assert o.status == "completed"
        assert o.duration_s == 272.0
        assert o.cost_usd == 0.1234
        assert o.turns == 12
        assert o.error == ""

    def test_failed_outcome_with_error(self):
        o = AgentOutcome(
            agent_name="xss-exploit",
            vuln_type="xss",
            status="failed",
            error="API rate limit exceeded",
        )
        assert o.status == "failed"
        assert o.error == "API rate limit exceeded"

    def test_skipped_outcome_defaults(self):
        o = AgentOutcome(
            agent_name="ssrf-exploit",
            vuln_type="ssrf",
            status="skipped",
        )
        assert o.status == "skipped"
        assert o.duration_s == 0.0
        assert o.cost_usd == 0.0
        assert o.turns == 0
        assert o.error == ""


# ---------------------------------------------------------------------------
# _format_duration
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_seconds_only(self):
        assert _format_duration(45) == "45s"

    def test_minutes_and_seconds(self):
        assert _format_duration(272) == "4m 32s"

    def test_hours_and_minutes(self):
        assert _format_duration(3725) == "1h 02m"

    def test_exact_minute(self):
        assert _format_duration(60) == "1m 00s"

    def test_exact_hour(self):
        assert _format_duration(3600) == "1h 00m"

    def test_zero(self):
        assert _format_duration(0) == "0s"


# ---------------------------------------------------------------------------
# format_exploit_summary
# ---------------------------------------------------------------------------


class TestFormatExploitSummary:
    def test_empty_outcomes(self):
        result = format_exploit_summary([])
        assert "No exploit outcomes" in result

    def test_all_completed(self):
        outcomes = [
            AgentOutcome("injection-exploit", "injection", "completed", duration_s=100, cost_usd=0.5, turns=8),
            AgentOutcome("xss-exploit", "xss", "completed", duration_s=200, cost_usd=0.3, turns=10),
            AgentOutcome("ssrf-exploit", "ssrf", "completed", duration_s=50, cost_usd=0.1, turns=3),
        ]
        result = format_exploit_summary(outcomes)
        assert "3/3 completed" in result
        assert "✅" in result
        assert "Totals" in result
        # No failure section
        assert "❌" not in result

    def test_mixed_results(self):
        outcomes = [
            AgentOutcome("injection-exploit", "injection", "completed", duration_s=100, cost_usd=0.5, turns=8),
            AgentOutcome("xss-exploit", "xss", "completed", duration_s=200, cost_usd=0.3, turns=10),
            AgentOutcome("ssrf-exploit", "ssrf", "failed", error="API rate limit exceeded"),
            AgentOutcome("auth-exploit", "auth", "skipped"),
        ]
        result = format_exploit_summary(outcomes)
        assert "2/4 completed, 1 failed" in result
        assert "✅" in result
        assert "❌" in result
        assert "⏭️" in result
        assert "API rate limit exceeded" in result

    def test_totals_aggregation(self):
        outcomes = [
            AgentOutcome("a-exploit", "a", "completed", duration_s=100, cost_usd=0.5, turns=8),
            AgentOutcome("b-exploit", "b", "completed", duration_s=200, cost_usd=0.3, turns=10),
        ]
        result = format_exploit_summary(outcomes)
        assert "turns=18" in result
        assert "$0.8000" in result

    def test_skipped_no_metrics_in_row(self):
        outcomes = [
            AgentOutcome("a-exploit", "a", "completed", duration_s=100, cost_usd=0.5, turns=8),
            AgentOutcome("b-exploit", "b", "skipped"),
        ]
        result = format_exploit_summary(outcomes)
        # Skipped row should show dashes, not 0s
        lines = result.split("\n")
        skipped_line = [l for l in lines if "⏭️" in l][0]
        assert "dur=-" in skipped_line
        assert "cost=-" in skipped_line
