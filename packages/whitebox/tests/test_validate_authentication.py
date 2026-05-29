from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.models.errors import PentestError


@dataclass
class AuthValidationResult:
    success: bool
    failure_point: str | None = None
    failure_detail: str | None = None


@pytest.mark.asyncio
async def test_auth_validation_success():
    from shannon_whitebox.services.validate_authentication import validate_authentication

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=MagicMock(
        duration_ms=5000, cost_usd=0.01, num_turns=3, model="claude-sonnet-4-6",
    ))

    mock_pm = MagicMock()
    mock_pm.load_sync.return_value = "Validate auth for https://example.com"

    result = await validate_authentication(
        web_url="https://example.com",
        config_path=None,
        prompt_manager=mock_pm,
        executor=mock_executor,
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_auth_validation_no_config():
    """When no authentication config exists, skip validation and return success."""
    from shannon_whitebox.services.validate_authentication import validate_authentication

    mock_pm = MagicMock()
    mock_executor = MagicMock()

    result = await validate_authentication(
        web_url="https://example.com",
        config_path=None,
        prompt_manager=mock_pm,
        executor=mock_executor,
    )
    assert result.success is True
