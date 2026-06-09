import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shannon_core.models.metrics import AgentMetrics
from shannon_core.logging.activity_logger import ConsoleActivityLogger
from shannon_whitebox.pipeline.shared import ActivityInput


@pytest.mark.asyncio
async def test_run_agent_injects_activity_logger(tmp_path):
    """run_agent calls create_activity_logger() and passes it to executor.execute."""
    from shannon_whitebox.pipeline import activities

    repo = tmp_path / "repo"
    repo.mkdir()

    sentinel = ConsoleActivityLogger()
    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(
        return_value=AgentMetrics(duration_ms=1, cost_usd=0.0, num_turns=1, model="m")
    )
    with patch.object(activities, "AgentExecutor", return_value=mock_executor), \
         patch.object(activities, "create_activity_logger", return_value=sentinel):
        inp = ActivityInput(repo_path=str(repo), web_url="", workspace_name="recon")
        await activities.run_agent(inp)

    assert mock_executor.execute.call_args.kwargs["audit_logger"] is sentinel
