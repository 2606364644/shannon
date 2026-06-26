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
async def test_pre_recon_agent_does_not_get_gitnexus_track(tmp_path):
    """CLAUDE.md §1: PRE_RECON is an LLM-track agent and must NOT be fed the
    deterministic GitNexus track (process-layer coupling removed). The
    `pre_recon_gitnexus_track` renderer has been deleted; the PRE_RECON branch
    in activities.py must not inject any deterministic-track variable."""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "code_index.json").write_text(
        json.dumps(
            {
                "repository": "r",
                "language": "python",
                "total_blocks": 0,
                "total_entry_points": 1,
                "total_chains": 0,
                "blocks": [],
                "edges": [],
                "chains": [],
                "entry_points": [
                    {
                        "func_block_id": "app.py:h:1",
                        "entry_type": "http_route",
                        "route": "/api/x",
                        "http_method": "GET",
                        "confidence": 0.9,
                        "evidence": "router.get",
                        "needs_llm_review": False,
                        "authentication": "public",
                    }
                ],
                "sink_call_sites": [],
            }
        )
    )

    captured = {}

    class FakeInput:
        agent_name = "pre-recon"
        web_url = None
        repo_path = str(tmp_path)
        config_path = None
        api_key = None
        pipeline_testing_mode = False
        prompt_override = None

    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)):
        await _run_with_runtime_patches(FakeInput(), captured)

    prompt_variables = captured.get("prompt_variables") or {}
    # Decoupling invariant: no deterministic GitNexus track must reach the
    # PRE_RECON (LLM-track) prompt variables.
    assert "pre_recon_gitnexus_track" not in prompt_variables


@pytest.mark.asyncio
async def test_non_pre_recon_not_injected(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "code_index.json").write_text("{}")

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
    assert prompt_variables is None or "pre_recon_gitnexus_track" not in (prompt_variables or {})
