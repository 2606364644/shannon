import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shannon_core.models.metrics import AgentMetrics
from shannon_core.logging.activity_logger import ConsoleActivityLogger
from shannon_blackbox.pipeline.shared import BlackboxActivityInput


@pytest.mark.asyncio
async def test_run_recon_injects_activity_logger(tmp_path):
    """run_recon calls create_activity_logger() and passes it through ReconExecutor."""
    from shannon_blackbox.pipeline import activities

    deliverables_root = tmp_path
    sentinel = ConsoleActivityLogger()

    mock_recon = MagicMock()
    mock_recon.execute = AsyncMock(
        return_value=AgentMetrics(duration_ms=1, cost_usd=0.0, num_turns=1, model="m")
    )
    with patch("shannon_blackbox.pipeline.activities.resolve_deliverables_path",
               return_value=deliverables_root / "deliverables"), \
         patch("shannon_blackbox.agents.recon_executor.ReconExecutor", return_value=mock_recon), \
         patch.object(activities, "create_activity_logger", return_value=sentinel):
        inp = BlackboxActivityInput(web_url="https://example.com", workspace_name="recon-blackbox")
        await activities.run_recon(inp)

    assert mock_recon.execute.call_args.kwargs["audit_logger"] is sentinel
