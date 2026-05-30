from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_auth_validation_no_config():
    """When config_path is None, skip validation and return success."""
    from shannon_core.services.validate_authentication import validate_authentication

    mock_pm = MagicMock()
    mock_executor = MagicMock()

    result = await validate_authentication(
        web_url="https://example.com",
        config_path=None,
        prompt_manager=mock_pm,
        executor=mock_executor,
    )
    assert result.success is True
    mock_executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_auth_validation_config_no_auth_section():
    """When config exists but has no authentication section, return success without calling executor."""
    from shannon_core.services.validate_authentication import validate_authentication

    mock_pm = MagicMock()
    mock_executor = MagicMock()

    mock_dist_config = MagicMock()
    mock_dist_config.authentication = None

    with patch("shannon_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("shannon_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    assert result.success is True
    mock_executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_auth_validation_with_config_calls_executor():
    """When authentication config exists, executor.execute is called with prompt_override."""
    from shannon_core.services.validate_authentication import validate_authentication

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=MagicMock(
        duration_ms=5000, cost_usd=0.01, num_turns=3, model="claude-sonnet-4-6",
    ))
    mock_pm = MagicMock()

    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin", "password": "pass123"}

    with patch("shannon_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("shannon_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    assert result.success is True
    mock_executor.execute.assert_called_once()
    call_kwargs = mock_executor.execute.call_args.kwargs
    assert call_kwargs.get("prompt_override") == "validate-authentication"
