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


def _index_with_group():
    handler = "c.js:index:32"
    return json.dumps(
        {
            "repository": "r",
            "language": "typescript",
            "total_blocks": 0,
            "total_entry_points": 3,
            "total_chains": 0,
            "blocks": [],
            "edges": [],
            "entry_points": [
                {
                    "func_block_id": handler,
                    "entry_type": "http_route",
                    "route": "/preview",
                    "http_method": "GET",
                    "confidence": 0.9,
                    "evidence": "",
                    "needs_llm_review": False,
                    "authentication": "required",
                    "source": "code_index",
                },
                {
                    "func_block_id": handler,
                    "entry_type": "http_route",
                    "route": "/preview/v2",
                    "http_method": "GET",
                    "confidence": 0.9,
                    "evidence": "",
                    "needs_llm_review": False,
                    "authentication": "required",
                    "source": "code_index",
                },
                {
                    "func_block_id": handler,
                    "entry_type": "http_route",
                    "route": "/preview/iframe-demo",
                    "http_method": "GET",
                    "confidence": 0.9,
                    "evidence": "",
                    "needs_llm_review": False,
                    "authentication": None,
                    "source": "code_index",
                },
            ],
            "chains": [],
        }
    )


class _FakeInput:
    web_url = None
    config_path = None
    api_key = None
    pipeline_testing_mode = False
    prompt_override = None

    def __init__(self, agent_name, repo_path):
        self.agent_name = agent_name
        self.repo_path = str(repo_path)


@pytest.mark.asyncio
async def test_recon_agent_gets_gitnexus_track(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "code_index.json").write_text(_index_with_group())

    captured = {}
    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)):
        await _run_with_runtime_patches(_FakeInput("recon", tmp_path), captured)

    track = (captured.get("prompt_variables") or {}).get("recon_gitnexus_track", "")
    assert "c.js:index:32" in track
    assert "/preview/iframe-demo" in track


@pytest.mark.asyncio
async def test_non_recon_agent_not_injected(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "code_index.json").write_text(_index_with_group())

    captured = {}
    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)):
        await _run_with_runtime_patches(_FakeInput("injection-vuln", tmp_path), captured)

    prompt_variables = captured.get("prompt_variables")
    assert prompt_variables is None or "recon_gitnexus_track" not in (prompt_variables or {})


@pytest.mark.asyncio
async def test_recon_agent_without_code_index_still_injects_notice(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    captured = {}
    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)):
        await _run_with_runtime_patches(_FakeInput("recon", tmp_path), captured)

    prompt_variables = captured.get("prompt_variables") or {}
    assert "recon_gitnexus_track" in prompt_variables
    assert "无" in prompt_variables["recon_gitnexus_track"]
