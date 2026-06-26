import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.models.metrics import AgentMetrics
from shannon_whitebox.pipeline import activities


def _runtime_patches(captured: dict):
    session = MagicMock()
    session.start_agent = AsyncMock()
    session.end_agent = AsyncMock()
    session.log_error = AsyncMock()

    logger = MagicMock()
    logger.initialize = AsyncMock()
    logger.close = AsyncMock()

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return AgentMetrics(duration_ms=1, cost_usd=0.0, num_turns=1, model="test")

    executor = MagicMock()
    executor.execute = fake_execute

    return (
        patch.object(activities.activity, "info", return_value=MagicMock(attempt=1)),
        patch("shannon_whitebox.audit.session_registry.get_audit_session", return_value=session),
        patch("shannon_whitebox.audit.session_tool_audit_logger.SessionToolAuditLogger", return_value=logger),
        patch.object(activities, "AgentExecutor", return_value=executor),
    )


async def _run_with_runtime_patches(input_obj, captured: dict):
    with ExitStack() as stack:
        for runtime_patch in _runtime_patches(captured):
            stack.enter_context(runtime_patch)
        await activities.run_agent(input_obj)


@pytest.mark.asyncio
async def test_recon_agent_does_not_get_framework_endpoints_summary(tmp_path):
    """CLAUDE.md §1: RECON is an LLM-track agent and must NOT be fed the
    deterministic framework_analysis.json output (decoupling invariant).
    The `framework_endpoints_summary` renderer was deleted in Task 3 (commit
    7cf066a); even when framework_analysis.json is present and well-formed,
    the RECON branch must NOT inject any `framework_endpoints_summary`
    variable into prompt_variables."""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "framework_analysis.json").write_text(
        json.dumps(
            {
                "detected_framework": None,
                "inferred_endpoints": [
                    {
                        "method": "DELETE",
                        "path": "/api/Feedbacks/:id",
                        "source": "framework-auto-generated",
                        "model": "Feedback",
                        "middleware": ["isAuthenticated"],
                        "vulnerability_indicators": ["no-ownership-check"],
                    },
                ],
                "recommendations": [],
            }
        )
    )

    captured = {}

    class FakeInput:
        agent_name = "recon"
        web_url = None
        repo_path = str(tmp_path)
        config_path = None
        api_key = None
        pipeline_testing_mode = False
        prompt_override = None

    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)):
        await _run_with_runtime_patches(FakeInput(), captured)

    prompt_variables = captured.get("prompt_variables") or {}
    # Decoupling invariant: no deterministic framework analysis must reach the
    # RECON (LLM-track) prompt variables, even when the JSON is present.
    assert "framework_endpoints_summary" not in prompt_variables


@pytest.mark.asyncio
async def test_non_recon_agent_not_injected(tmp_path):
    """Non-RECON agent -> no framework injection."""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "framework_analysis.json").write_text(json.dumps({"inferred_endpoints": []}))

    captured = {}

    class FakeInput:
        agent_name = "injection-vuln"
        web_url = None
        repo_path = str(tmp_path)
        config_path = None
        api_key = None
        pipeline_testing_mode = False
        prompt_override = None

    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)):
        await _run_with_runtime_patches(FakeInput(), captured)

    prompt_variables = captured.get("prompt_variables")
    assert prompt_variables is None or "framework_endpoints_summary" not in (prompt_variables or {})


@pytest.mark.asyncio
async def test_recon_agent_without_framework_json_skips(tmp_path):
    """RECON agent + missing framework_analysis.json -> no crash, no framework injection."""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    captured = {}

    class FakeInput:
        agent_name = "recon"
        web_url = None
        repo_path = str(tmp_path)
        config_path = None
        api_key = None
        pipeline_testing_mode = False
        prompt_override = None

    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)):
        await _run_with_runtime_patches(FakeInput(), captured)

    prompt_variables = captured.get("prompt_variables") or {}
    assert "framework_endpoints_summary" not in prompt_variables
