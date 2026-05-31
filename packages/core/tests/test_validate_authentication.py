import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.services.validate_authentication import (
    AuthValidationResult,
    auth_state_path,
    cleanup_auth_state,
    verify_auth_state,
    validate_authentication,
)


# --- auth_state_path tests ---

def test_auth_state_path_returns_json_file():
    assert auth_state_path("/tmp/workspace") == Path("/tmp/workspace/auth-state.json")

def test_auth_state_path_accepts_path_object():
    assert auth_state_path(Path("/tmp/ws")) == Path("/tmp/ws/auth-state.json")


# --- verify_auth_state tests ---

@pytest.mark.asyncio
async def test_verify_missing_file(tmp_path):
    state_file = tmp_path / "auth-state.json"
    result = await verify_auth_state(state_file)
    assert result.success is False
    assert result.failure_point == "out_of_band"
    assert "did not save auth state" in result.failure_detail

@pytest.mark.asyncio
async def test_verify_invalid_json(tmp_path):
    state_file = tmp_path / "auth-state.json"
    state_file.write_text("not json{{{")
    result = await verify_auth_state(state_file)
    assert result.success is False
    assert result.failure_point == "out_of_band"
    assert "not valid JSON" in result.failure_detail

@pytest.mark.asyncio
async def test_verify_empty_cookies_and_origins(tmp_path):
    state_file = tmp_path / "auth-state.json"
    state_file.write_text(json.dumps({"cookies": [], "origins": []}))
    result = await verify_auth_state(state_file)
    assert result.success is False
    assert result.failure_point == "out_of_band"
    assert "no cookies or origins" in result.failure_detail

@pytest.mark.asyncio
async def test_verify_valid_state_with_cookies(tmp_path):
    state_file = tmp_path / "auth-state.json"
    state_file.write_text(json.dumps({
        "cookies": [{"name": "session", "value": "abc123"}],
        "origins": [],
    }))
    result = await verify_auth_state(state_file)
    assert result.success is True

@pytest.mark.asyncio
async def test_verify_valid_state_with_origins(tmp_path):
    state_file = tmp_path / "auth-state.json"
    state_file.write_text(json.dumps({
        "cookies": [],
        "origins": [{"origin": "https://example.com", "localStorage": [{"name": "token", "value": "xyz"}]}],
    }))
    result = await verify_auth_state(state_file)
    assert result.success is True


# --- cleanup_auth_state tests ---

@pytest.mark.asyncio
async def test_cleanup_removes_existing_file(tmp_path):
    state_file = tmp_path / "auth-state.json"
    state_file.write_text('{"cookies":[]}')
    assert state_file.exists()
    await cleanup_auth_state(tmp_path)
    assert not state_file.exists()

@pytest.mark.asyncio
async def test_cleanup_noop_when_no_file(tmp_path):
    await cleanup_auth_state(tmp_path)
    # Should not raise


# --- validate_authentication integration tests ---

@pytest.mark.asyncio
async def test_auth_validation_no_config():
    """When config_path is None, skip validation and return success."""
    mock_pm = MagicMock()
    mock_executor = MagicMock()

    result = await validate_authentication(
        web_url="https://example.com",
        config_path=None,
        workspace_path="/tmp/ws",
        prompt_manager=mock_pm,
        executor=mock_executor,
    )
    assert result.success is True
    mock_executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_auth_validation_config_no_auth_section():
    """When config exists but has no authentication section, return success without calling executor."""
    mock_pm = MagicMock()
    mock_executor = MagicMock()

    mock_dist_config = MagicMock()
    mock_dist_config.authentication = None

    with patch("shannon_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("shannon_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path="/tmp/ws",
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    assert result.success is True
    mock_executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_auth_validation_cleans_up_stale_state(tmp_path):
    """Stale auth-state.json is deleted before running the agent."""
    state_file = tmp_path / "auth-state.json"
    state_file.write_text('{"old": true}')

    # Simulate agent writing a valid state file during executor.execute
    async def fake_execute(**kwargs):
        state_file.write_text(json.dumps({
            "cookies": [{"name": "session", "value": "new"}],
            "origins": [],
        }))
        return MagicMock(duration_ms=5000, cost_usd=0.01, num_turns=3, model="claude-sonnet-4-6")

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()

    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin", "password": "pass123"}

    with patch("shannon_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("shannon_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    # The stale file should have been deleted before executor ran, then replaced with valid state
    assert result.success is True
    mock_executor.execute.assert_called_once()
    call_kwargs = mock_executor.execute.call_args.kwargs
    assert call_kwargs.get("prompt_override") == "validate-authentication"


@pytest.mark.asyncio
async def test_auth_validation_detects_missing_state_file(tmp_path):
    """When executor runs but no auth-state.json is saved, return failure."""
    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=MagicMock(
        duration_ms=5000, cost_usd=0.01, num_turns=3, model="claude-sonnet-4-6",
    ))
    mock_pm = MagicMock()

    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}

    with patch("shannon_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("shannon_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    assert result.success is False
    assert result.failure_point == "out_of_band"


@pytest.mark.asyncio
async def test_auth_validation_verifies_state_content(tmp_path):
    """When executor runs and valid auth-state is saved, return success."""
    state_file = tmp_path / "auth-state.json"
    # Simulate agent writing the file during executor.execute
    async def fake_execute(**kwargs):
        state_file.write_text(json.dumps({
            "cookies": [{"name": "session", "value": "abc"}],
            "origins": [],
        }))
        return MagicMock(duration_ms=5000, cost_usd=0.01, num_turns=3, model="claude-sonnet-4-6")

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()

    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}

    with patch("shannon_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("shannon_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    assert result.success is True
