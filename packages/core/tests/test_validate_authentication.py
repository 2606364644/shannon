import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from supernova_core.models.metrics import AgentMetrics
from supernova_core.services.validate_authentication import (
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

@pytest.mark.asyncio
async def test_verify_accepts_storagestate_with_cookies(tmp_path):
    """agent-browser `state save` ≈ Playwright storageState {cookies, origins}.
    verify_auth_state must accept it when cookies present."""
    state_file = tmp_path / "auth-state.json"
    state_file.write_text(json.dumps({
        "cookies": [{"name": "s", "value": "v", "domain": "example.com"}],
        "origins": [],
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

    with patch("supernova_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("supernova_core.config.parser.distribute_config", return_value=mock_dist_config):
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
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(duration_ms=5000)  # Fallback path: no structured_output

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()

    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin", "password": "pass123"}

    with patch("supernova_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("supernova_core.config.parser.distribute_config", return_value=mock_dist_config):
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
    mock_executor.execute = AsyncMock(return_value=AgentMetrics(duration_ms=5000))
    mock_pm = MagicMock()

    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}

    with patch("supernova_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("supernova_core.config.parser.distribute_config", return_value=mock_dist_config):
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
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(duration_ms=5000)  # Fallback path: no structured_output

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()

    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}

    with patch("supernova_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("supernova_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    assert result.success is True


# --- AUTH_VALIDATION_SCHEMA tests ---

def test_auth_validation_schema_constant():
    """AUTH_VALIDATION_SCHEMA has the expected structure."""
    from supernova_core.services.validate_authentication import AUTH_VALIDATION_SCHEMA
    assert AUTH_VALIDATION_SCHEMA["type"] == "object"
    assert "login_success" in AUTH_VALIDATION_SCHEMA["properties"]
    assert AUTH_VALIDATION_SCHEMA["properties"]["login_success"]["type"] == "boolean"
    assert "login_success" in AUTH_VALIDATION_SCHEMA["required"]
    fp = AUTH_VALIDATION_SCHEMA["properties"]["failure_point"]
    assert set(fp["enum"]) == {"username_or_password", "totp_secret", "out_of_band"}


# --- Structured output integration tests ---

@pytest.mark.asyncio
async def test_auth_validation_uses_validate_auth_agent(tmp_path):
    """validate_authentication uses AgentName.VALIDATE_AUTH, not PRE_RECON."""
    state_file = tmp_path / "auth-state.json"

    async def fake_execute(**kwargs):
        state_file.write_text(json.dumps({
            "cookies": [{"name": "session", "value": "abc"}],
            "origins": [],
        }))
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(
            duration_ms=5000,
            structured_output={"login_success": True},
        )

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()
    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}

    with patch("supernova_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("supernova_core.config.parser.distribute_config", return_value=mock_dist_config):
        from supernova_core.models.agents import AgentName
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    assert result.success is True
    call_kwargs = mock_executor.execute.call_args.kwargs
    assert call_kwargs["agent_name"] == AgentName.VALIDATE_AUTH
    assert call_kwargs.get("structured_output_schema") is not None


@pytest.mark.asyncio
async def test_auth_validation_structured_output_failure_username(tmp_path):
    """Structured output with login_success=False and failure_point=username_or_password."""
    async def fake_execute(**kwargs):
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(
            duration_ms=5000,
            structured_output={
                "login_success": False,
                "failure_point": "username_or_password",
                "failure_detail": "Invalid username or password",
            },
        )

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()
    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}

    with patch("supernova_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("supernova_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    assert result.success is False
    assert result.failure_point == "username_or_password"
    assert "Invalid username or password" in result.failure_detail


@pytest.mark.asyncio
async def test_auth_validation_structured_output_failure_totp(tmp_path):
    """Structured output with failure_point=totp_secret."""
    async def fake_execute(**kwargs):
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(
            duration_ms=5000,
            structured_output={
                "login_success": False,
                "failure_point": "totp_secret",
                "failure_detail": "TOTP code rejected",
            },
        )

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()
    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}

    with patch("supernova_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("supernova_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    assert result.success is False
    assert result.failure_point == "totp_secret"


@pytest.mark.asyncio
async def test_auth_validation_structured_output_failure_out_of_band(tmp_path):
    """Structured output with failure_point=out_of_band."""
    async def fake_execute(**kwargs):
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(
            duration_ms=5000,
            structured_output={
                "login_success": False,
                "failure_point": "out_of_band",
                "failure_detail": "Email verification required",
            },
        )

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()
    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}

    with patch("supernova_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("supernova_core.config.parser.distribute_config", return_value=mock_dist_config):
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
async def test_auth_validation_structured_failure_but_state_has_cookies(tmp_path):
    """LLM fills login_success=False but auth-state has cookies → trust objective evidence.

    Regression (~2 blackbox scan): GLM under CLI protocol-level structured output
    (--json_schema) occasionally fills login_success=false even when the browser
    actually logged in — natural-language turns report success AND auth-state.json is
    saved with cookies. The objective auth-state (cookies saved this run) must override
    the LLM's mis-filled boolean, otherwise a genuinely successful login is
    misclassified as AUTH_LOGIN_FAILED and the whole scan fails. Mirror of
    test_auth_validation_structured_success_but_missing_state_file (which catches the
    opposite lie).
    """
    state_file = tmp_path / "auth-state.json"

    async def fake_execute(**kwargs):
        # Agent actually logged in and saved a valid state with a cookie...
        state_file.write_text(json.dumps({
            "cookies": [{"name": "connect.sid", "value": "abc"}],
            "origins": [],
        }))
        # ...but the structured-output field was mis-filled as failure (no detail),
        # exactly like the GLM --json_schema misclassification observed in ~2.
        return AgentMetrics(
            duration_ms=5000,
            structured_output={"login_success": False},
        )

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()
    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}

    with patch("supernova_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("supernova_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    # Objective cookies override the LLM's mis-filled login_success=false
    assert result.success is True


@pytest.mark.asyncio
async def test_auth_validation_fallback_when_no_structured_output(tmp_path):
    """When structured output is None, fall back to verify_auth_state."""
    state_file = tmp_path / "auth-state.json"

    async def fake_execute(**kwargs):
        # Simulate agent writing a valid state file
        state_file.write_text(json.dumps({
            "cookies": [{"name": "session", "value": "abc"}],
            "origins": [],
        }))
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(duration_ms=5000)  # No structured_output

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()
    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}

    with patch("supernova_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("supernova_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    # Falls back to verify_auth_state, which checks the file
    assert result.success is True


@pytest.mark.asyncio
async def test_auth_validation_structured_success_but_missing_state_file(tmp_path):
    """Agent claims login_success=True via structured output but fails to write auth state."""
    async def fake_execute(**kwargs):
        # Agent returns success but does NOT write the state file
        return AgentMetrics(
            duration_ms=5000,
            structured_output={"login_success": True},
        )

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()
    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}

    with patch("supernova_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("supernova_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    # Double-verification catches the lie: structured output says success but file is missing
    assert result.success is False
    assert result.failure_point == "out_of_band"


# --- deliverables_path forwarding (不再 fallback 到 repo) ---

@pytest.mark.asyncio
async def test_auth_validation_forwards_deliverables_path(tmp_path):
    """validate_authentication forwards deliverables_path to executor.execute.

    防止 validate-authentication agent 在被扫仓库 mkdir .supernova/deliverables 污染源。
    """
    state_file = tmp_path / "auth-state.json"

    async def fake_execute(**kwargs):
        state_file.write_text(json.dumps({"cookies": [{"name": "s", "value": "v"}]}))
        return AgentMetrics(duration_ms=5000)

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()
    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}

    deliverables_dir = tmp_path / "session-deliverables"
    with patch("supernova_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("supernova_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
            deliverables_path=str(deliverables_dir),
        )

    assert result.success is True
    call_kwargs = mock_executor.execute.call_args.kwargs
    assert call_kwargs.get("deliverables_path") == str(deliverables_dir)


@pytest.mark.asyncio
async def test_executor_raises_when_deliverables_path_missing(tmp_path):
    """AgentExecutor.execute raises ValueError when deliverables_path 未传.

    防止 executor 内部 fallback 到 repo/.supernova/deliverables 复活污染源。
    """
    from supernova_core.agents.executor import AgentExecutor
    from supernova_core.models.agents import AgentName

    mock_prompt_manager = MagicMock()
    executor = AgentExecutor(mock_prompt_manager)

    with pytest.raises(ValueError, match="deliverables_path is required"):
        await executor.execute(
            agent_name=AgentName.VALIDATE_AUTH,
            repo_path=str(tmp_path / "repo"),
        )

