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


def test_auth_state_path_default_is_primary():
    from supernova_core.services.validate_authentication import auth_state_path
    assert auth_state_path("/ws").name == "auth-state.json"

def test_auth_state_path_with_account_id():
    from supernova_core.services.validate_authentication import auth_state_path
    assert auth_state_path("/ws", "victim-b").name == "auth-state-victim-b.json"

def test_auth_state_path_primary_explicit():
    from supernova_core.services.validate_authentication import auth_state_path
    assert auth_state_path("/ws", None).name == "auth-state.json"


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


def test_cleanup_auth_state_sync_removes_all_identity_files(tmp_path):
    from supernova_core.services.validate_authentication import cleanup_auth_state_sync
    (tmp_path / "auth-state.json").write_text("{}")
    (tmp_path / "auth-state-victim-b.json").write_text("{}")
    (tmp_path / "auth-state-admin-1.json").write_text("{}")
    (tmp_path / "other.json").write_text("{}")
    cleanup_auth_state_sync(str(tmp_path))
    assert not (tmp_path / "auth-state.json").exists()
    assert not (tmp_path / "auth-state-victim-b.json").exists()
    assert not (tmp_path / "auth-state-admin-1.json").exists()
    assert (tmp_path / "other.json").exists()  # 不误删


@pytest.mark.asyncio
async def test_cleanup_auth_state_removes_all_identity_files(tmp_path):
    """Async cleanup must also glob all identity files (parity with sync)."""
    (tmp_path / "auth-state.json").write_text("{}")
    (tmp_path / "auth-state-victim-b.json").write_text("{}")
    (tmp_path / "auth-state-admin-1.json").write_text("{}")
    (tmp_path / "other.json").write_text("{}")
    await cleanup_auth_state(tmp_path)
    assert not (tmp_path / "auth-state.json").exists()
    assert not (tmp_path / "auth-state-victim-b.json").exists()
    assert not (tmp_path / "auth-state-admin-1.json").exists()
    assert (tmp_path / "other.json").exists()  # 不误删


# --- validate_authentication integration tests ---

@pytest.mark.asyncio
async def test_auth_validation_config_parse_failure_returns_config_failure(tmp_path):
    """parse_config/distribute_config 抛错（非法 YAML/校验失败）→ 失败(config)，绝不 success。

    2026-08-17 真机 bug：原实现 except Exception → success=True，probe scan-config.yaml
    任何解析失败都让「测试登录」2ms 秒过显示已认证（浏览器根本没登录）。锁死该回归。"""
    mock_pm = MagicMock()
    mock_executor = MagicMock()

    cfg = tmp_path / "scan-config.yaml"
    cfg.write_text("authentication: 42\n", encoding="utf-8")

    result = await validate_authentication(
        web_url="https://example.com",
        config_path=str(cfg),
        workspace_path=str(tmp_path),
        prompt_manager=mock_pm,
        executor=mock_executor,
    )

    assert result.success is False
    assert result.failure_point == "config"
    assert result.failure_detail and "parse failed" in result.failure_detail
    mock_executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_auth_validation_invalid_browser_engine_env_returns_config_failure(tmp_path, monkeypatch):
    """worker env 注入非法 SUPERNOVA_BROWSER_ENGINE → Config 校验失败 → 失败(config)。

    真机触发面：parse_config 把 env 值写进 raw（BrowserEngineType 只接受
    playwright/agent-browser），env 填错值即抛 PentestError——不能被吞成 success。"""
    mock_pm = MagicMock()
    mock_executor = MagicMock()

    cfg = tmp_path / "scan-config.yaml"
    cfg.write_text(
        "authentication:\n  login_type: form\n  login_url: https://example.com/l\n"
        "  credentials:\n    username: u\n    password: p\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SUPERNOVA_BROWSER_ENGINE", "chrome")

    result = await validate_authentication(
        web_url="https://example.com",
        config_path=str(cfg),
        workspace_path=str(tmp_path),
        prompt_manager=mock_pm,
        executor=mock_executor,
    )

    assert result.success is False
    assert result.failure_point == "config"
    mock_executor.execute.assert_not_called()


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
        return AgentMetrics(duration_ms=5000, structured_output={"login_success": True})

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
    """D3: no structured output → failure (fail-fast, no auth-state fallback).

    failure_point="no_verdict" makes this distinguishable from a deterministic
    login rejection (username_or_password/totp_secret/out_of_band): the combined
    t0 precheck lets no_verdict pass through instead of killing the whole scan
    (2026-08-14 NodeGoat: GLM logged in successfully but emitted Markdown, the
    missing verdict was misread as auth_failed and the combined scan fail-fasted
    before whitebox even started)."""
    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=AgentMetrics(duration_ms=5000))
    mock_pm = MagicMock()

    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}
    mock_dist_config.accounts = []  # Branch A (single-identity) — this test's intent;
    # an unmocked MagicMock accounts attr is truthy and silently routes to Branch B.

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
    assert result.failure_point == "no_verdict"


@pytest.mark.asyncio
async def test_auth_validation_verifies_state_content(tmp_path):
    """D3: structured login_success=True → success (state still saved for reuse)."""
    state_file = tmp_path / "auth-state.json"
    # Simulate agent writing the file during executor.execute
    async def fake_execute(**kwargs):
        state_file.write_text(json.dumps({
            "cookies": [{"name": "session", "value": "abc"}],
            "origins": [],
        }))
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(duration_ms=5000, structured_output={"login_success": True})

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
    """D3: login_success=False fails even when auth-state.json holds a cookie.

    Cookie presence is a weak signal (CSRF / anonymous session / rate-limit / bot
    cookies are set on the login page before any successful login), so it no longer
    overrides the model's structured verdict. A failure verdict → failure, period.
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

    # D3: trust the structured field over cookies — failure holds even with a cookie
    assert result.success is False


@pytest.mark.asyncio
async def test_auth_validation_fallback_when_no_structured_output(tmp_path):
    """D3: no structured output → fail-fast (do not fall back to auth-state)."""
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
    mock_dist_config.accounts = []  # Branch A (single-identity) — this test's intent

    with patch("supernova_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("supernova_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    # D3: no structured output is a provider anomaly → fail-fast (no cookie fallback),
    # marked "no_verdict" so callers can tell it apart from a deterministic rejection
    assert result.success is False
    assert result.failure_point == "no_verdict"


@pytest.mark.asyncio
async def test_auth_validation_structured_success_but_missing_state_file(tmp_path):
    """D3: login_success=True is trusted directly — no auth-state file re-check.

    The structured verdict is authoritative; we no longer reverse-override a success by
    re-checking auth-state.json (which turned genuine sessionStorage / in-memory token
    logins into false negatives).
    """
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

    # D3: structured login_success=True is trusted; no auth-state file re-check
    assert result.success is True


# --- deliverables_path forwarding (不再 fallback 到 repo) ---

@pytest.mark.asyncio
async def test_auth_validation_forwards_deliverables_path(tmp_path):
    """validate_authentication forwards deliverables_path to executor.execute.

    防止 validate-authentication agent 在被扫仓库 mkdir .supernova/deliverables 污染源。
    """
    state_file = tmp_path / "auth-state.json"

    async def fake_execute(**kwargs):
        state_file.write_text(json.dumps({"cookies": [{"name": "s", "value": "v"}]}))
        return AgentMetrics(duration_ms=5000, structured_output={"login_success": True})

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


# --- 多身份 preflight 登录循环 (子项目2 T4) ---

@pytest.mark.asyncio
async def test_multi_identity_login_loop_writes_manifest(tmp_path):
    """accounts 非空时，每个 identity 各登录一次，产 identity-manifest.json。"""
    from supernova_core.models.config import Config, Authentication, Credentials, Account
    from supernova_core.services.validate_authentication import load_identity_manifest

    async def fake_execute(**kwargs):
        sf = kwargs["prompt_variables"]["AUTH_STATE_FILE"]
        Path(sf).write_text(json.dumps({"cookies": [{"name": "s"}], "origins": []}))
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(duration_ms=100, structured_output={"login_success": True})

    mock_executor = MagicMock(); mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()
    cfg = Config(authentication=Authentication(login_type="form", login_url="https://x/login",
        credentials=Credentials(username="userA", password="p")),
        accounts=[
            Account(id="victim-b", role="user", tier="low", credentials=Credentials(username="userB")),
            Account(id="admin-1", role="admin", tier="high", credentials=Credentials(username="admin")),
        ])
    with patch("supernova_core.config.parser.parse_config", return_value=cfg), \
         patch("supernova_core.config.parser.distribute_config",
               return_value=MagicMock(authentication=cfg.authentication, accounts=cfg.accounts)):
        result = await validate_authentication(
            web_url="https://x", config_path="/c.yaml", workspace_path=str(tmp_path),
            prompt_manager=mock_pm, executor=mock_executor)
    assert result.success is True
    assert mock_executor.execute.call_count == 3  # primary + 2 accounts
    manifest = load_identity_manifest(tmp_path)
    assert manifest is not None
    ids = {r.account_id for r in manifest.identities}
    assert ids == {"primary", "victim-b", "admin-1"}
    assert all(r.available for r in manifest.identities)
    # 每个 identity 落不同 auth-state 文件
    call_paths = [c.kwargs["prompt_variables"]["AUTH_STATE_FILE"] for c in mock_executor.execute.call_args_list]
    assert len(set(call_paths)) == 3


@pytest.mark.asyncio
async def test_victim_failure_degrades_but_scan_continues(tmp_path):
    """victim/baseline 登录失败 → available=False，整体仍 success（不 fail-fast）。"""
    from supernova_core.models.config import Config, Authentication, Credentials, Account
    from supernova_core.services.validate_authentication import load_identity_manifest
    async def fake_execute(**kwargs):
        sf = kwargs["prompt_variables"]["AUTH_STATE_FILE"]
        Path(sf).write_text(json.dumps({"cookies":[{}],"origins":[]}))
        ok = "victim-b" not in sf  # victim 那次失败
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(duration_ms=10, structured_output={"login_success": ok})
    me = MagicMock(); me.execute = AsyncMock(side_effect=fake_execute); mp = MagicMock()
    cfg = Config(authentication=Authentication(login_type="form", login_url="https://x/l",
        credentials=Credentials(username="u")), accounts=[
        Account(id="victim-b", role="user", tier="low", credentials=Credentials(username="v"))])
    with patch("supernova_core.config.parser.parse_config", return_value=cfg), \
         patch("supernova_core.config.parser.distribute_config",
               return_value=MagicMock(authentication=cfg.authentication, accounts=cfg.accounts)):
        r = await validate_authentication(web_url="https://x", config_path="/c.yaml",
            workspace_path=str(tmp_path), prompt_manager=mp, executor=me)
    assert r.success is True  # victim 死不拖垮
    manifest = load_identity_manifest(tmp_path)
    vic = [x for x in manifest.identities if x.account_id == "victim-b"][0]
    assert vic.available is False


@pytest.mark.asyncio
async def test_primary_failure_is_fail_fast(tmp_path):
    """primary(attacker) 登录失败 → success=False，扫描终止信号。"""
    from supernova_core.models.config import Config, Authentication, Credentials, Account
    async def fake_execute(**kwargs):
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(duration_ms=10, structured_output={"login_success": False,
            "failure_point":"username_or_password","failure_detail":"bad"})
    me = MagicMock(); me.execute = AsyncMock(side_effect=fake_execute); mp = MagicMock()
    cfg = Config(authentication=Authentication(login_type="form", login_url="https://x/l",
        credentials=Credentials(username="u")), accounts=[
        Account(id="v", role="user", tier="low", credentials=Credentials(username="v2"))])
    with patch("supernova_core.config.parser.parse_config", return_value=cfg), \
         patch("supernova_core.config.parser.distribute_config",
               return_value=MagicMock(authentication=cfg.authentication, accounts=cfg.accounts)):
        r = await validate_authentication(web_url="https://x", config_path="/c.yaml",
            workspace_path=str(tmp_path), prompt_manager=mp, executor=me)
    assert r.success is False
    assert me.execute.call_count == 1  # primary 一失败就停，不登 victim


@pytest.mark.asyncio
async def test_primary_no_verdict_marked_no_verdict(tmp_path):
    """Branch B: primary 无结构化判定（provider 异常，非确定性登录拒绝）→
    failure_point="no_verdict"（对齐 Branch A；t0 组合预验证据此放行，不再把
    "agent 跑完但没 verdict" 误读成 auth_failed 误杀组合扫描）。"""
    from supernova_core.models.config import Config, Authentication, Credentials, Account
    async def fake_execute(**kwargs):
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(duration_ms=10)  # structured_output=None
    me = MagicMock(); me.execute = AsyncMock(side_effect=fake_execute); mp = MagicMock()
    cfg = Config(authentication=Authentication(login_type="form", login_url="https://x/l",
        credentials=Credentials(username="u")), accounts=[
        Account(id="v", role="user", tier="low", credentials=Credentials(username="v2"))])
    with patch("supernova_core.config.parser.parse_config", return_value=cfg), \
         patch("supernova_core.config.parser.distribute_config",
               return_value=MagicMock(authentication=cfg.authentication, accounts=cfg.accounts)):
        r = await validate_authentication(web_url="https://x", config_path="/c.yaml",
            workspace_path=str(tmp_path), prompt_manager=mp, executor=me)
    assert r.success is False
    assert r.failure_point == "no_verdict"
    assert me.execute.call_count == 1  # primary fail-fast，不登 victim


@pytest.mark.asyncio
async def test_no_accounts_skips_manifest_byte_identical(tmp_path):
    """Step 7 regression: 无 accounts 时不落 identity-manifest.json (byte-identical with pre-T4)."""
    from supernova_core.models.config import Config, Authentication, Credentials
    from supernova_core.services.validate_authentication import load_identity_manifest

    async def fake_execute(**kwargs):
        sf = kwargs["prompt_variables"]["AUTH_STATE_FILE"]
        Path(sf).write_text(json.dumps({"cookies": [{"name": "s"}], "origins": []}))
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(duration_ms=100, structured_output={"login_success": True})

    mock_executor = MagicMock(); mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()
    cfg = Config(authentication=Authentication(login_type="form", login_url="https://x/login",
        credentials=Credentials(username="userA", password="p")))
    with patch("supernova_core.config.parser.parse_config", return_value=cfg), \
         patch("supernova_core.config.parser.distribute_config",
               return_value=MagicMock(authentication=cfg.authentication, accounts=[])):
        result = await validate_authentication(
            web_url="https://x", config_path="/c.yaml", workspace_path=str(tmp_path),
            prompt_manager=mock_pm, executor=mock_executor)
    assert result.success is True
    assert mock_executor.execute.call_count == 1
    # 关键 byte-identical 不变量：无 accounts → 无 manifest
    assert (tmp_path / "identity-manifest.json").exists() is False
    assert load_identity_manifest(tmp_path) is None



@pytest.mark.asyncio
async def test_auth_validation_forwards_proxy_url_to_executor(tmp_path):
    """HOST proxy must reach the validate-authentication agent executor."""
    from supernova_core.models.metrics import AgentMetrics

    async def fake_execute(**kwargs):
        return AgentMetrics(duration_ms=1, structured_output={"login_success": True})

    executor = MagicMock()
    executor.execute = AsyncMock(side_effect=fake_execute)
    dist_config = MagicMock(authentication={"username": "admin"}, accounts=[])

    with patch("supernova_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("supernova_core.config.parser.distribute_config", return_value=dist_config):
        result = await validate_authentication(
            web_url="https://target.internal/login",
            config_path="/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=MagicMock(),
            executor=executor,
            proxy_url="http://127.0.0.1:19090",
        )

    assert result.success is True
    assert executor.execute.call_args.kwargs["proxy_url"] == "http://127.0.0.1:19090"


@pytest.mark.asyncio
async def test_validate_authentication_creates_sandbox_cwd(tmp_path):
    """Auth-validation 无真实 repo,用占位 repo_path 作 agent 沙箱 cwd;该目录必须在
    agent 启动(executor.execute)前创建,否则 bash 工具 chdir 失败 FileNotFoundError →
    所有命令死锁(2026-08-14 组合扫描 NodeGoat 白盒不跑根因)。白盒/黑盒正常扫描的
    repo_path 是真实 repo(天然存在),唯独 auth-validation 用占位目录却没 mkdir。"""
    repo_path = tmp_path / "shannon-auth-check"
    assert not repo_path.exists()  # 起点:占位目录不存在

    cwd_existed: dict = {}

    async def fake_execute(**kwargs):
        # agent 启动瞬间 cwd 必须已是目录(修复前:不存在 → bash 第一条命令就 FileNotFoundError)
        cwd_existed["is_dir"] = Path(kwargs["repo_path"]).is_dir()
        return AgentMetrics(duration_ms=1, structured_output={"login_success": True})

    executor = MagicMock()
    executor.execute = AsyncMock(side_effect=fake_execute)
    dist_config = MagicMock(authentication={"username": "admin"}, accounts=[])

    with patch("supernova_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("supernova_core.config.parser.distribute_config", return_value=dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=MagicMock(),
            executor=executor,
            repo_path=str(repo_path),
        )

    assert result.success is True
    assert repo_path.is_dir()  # 最终也应是目录
    assert cwd_existed["is_dir"] is True  # ← 关键:agent 跑时 cwd 已预创建


@pytest.mark.asyncio
async def test_auth_validation_forwards_provider_config_to_executor(tmp_path):
    """provider_config 完整穿线到 executor.execute（2026-08-17 key/端点错配根因：
    仅 api_key 时 base_url 回落 worker env，两套配置混用必 401）。"""
    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=AgentMetrics(
        duration_ms=100, structured_output={"login_success": True}))
    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin", "password": "pw"}
    mock_dist_config.accounts = []

    provider_config = {"type": "openai_compatible",
                       "base_url": "https://llm-proxy.example/v1",
                       "api_key": "user-key-x", "medium_model": "m"}
    with patch("supernova_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("supernova_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=MagicMock(),
            executor=mock_executor,
            provider_config=provider_config,
        )

    assert result.success is True
    call_kwargs = mock_executor.execute.call_args.kwargs
    assert call_kwargs.get("provider_config") == provider_config


# ---------------------------------------------------------------------------
# 修复 A（2026-09-03）：playwright 引擎下认证登录走 per-scan HOST 代理。
# 认证链路此前从不写引擎 config，而 playwright 代理唯一注入通道是 per-session
# config 的 launchOptions.proxy（认证 session 固定 agent1）→ 登录 Chrome 直连 DNS。
# agent-browser 代理走 session_flag --proxy（prompt 层已生效），无需准备。
# ---------------------------------------------------------------------------

def _auth_config_yaml(engine: str) -> str:
    return (
        f"browser_engine: {engine}\n"
        "authentication: { login_type: form, login_url: https://x/login, "
        "credentials: { username: u, password: p } }\n"
    )


def _read_agent1_config(repo_dir: Path) -> dict:
    return json.loads(
        (repo_dir / ".playwright" / "cli.config.agent1.json").read_text())


class TestPrepareAuthEngineConfig:
    def test_playwright_writes_agent1_config_with_proxy(self, tmp_path):
        from supernova_core.services.validate_authentication import (
            _prepare_auth_engine_config,
        )
        from supernova_core.config.parser import parse_config
        from supernova_core.models.config import Config

        cfg_file = tmp_path / "c.yaml"
        cfg_file.write_text(_auth_config_yaml("playwright"))
        cfg = parse_config(str(cfg_file))

        _prepare_auth_engine_config(
            config=cfg, repo_path=str(tmp_path),
            proxy_url="http://127.0.0.1:8899")

        data = _read_agent1_config(tmp_path)
        assert data["browser"]["launchOptions"]["proxy"]["server"] == "http://127.0.0.1:8899"

    def test_playwright_stale_config_rewritten_per_call(self, tmp_path):
        """Batch 每 cred 各起独立 proxy：残留 config 指向已停代理 → 先清后写（force）。"""
        from supernova_core.services.validate_authentication import (
            _prepare_auth_engine_config,
        )
        from supernova_core.config.parser import parse_config

        cfg_file = tmp_path / "c.yaml"
        cfg_file.write_text(_auth_config_yaml("playwright"))
        cfg = parse_config(str(cfg_file))

        _prepare_auth_engine_config(
            config=cfg, repo_path=str(tmp_path),
            proxy_url="http://127.0.0.1:1111")
        _prepare_auth_engine_config(
            config=cfg, repo_path=str(tmp_path),
            proxy_url="http://127.0.0.1:2222")

        data = _read_agent1_config(tmp_path)
        assert data["browser"]["launchOptions"]["proxy"]["server"] == "http://127.0.0.1:2222"

    def test_playwright_without_proxy_still_writes_config(self, tmp_path):
        """无 HOST 档案（proxy=None）→ 仍写 agent1 config（stealth 等其余配置照旧）。"""
        from supernova_core.services.validate_authentication import (
            _prepare_auth_engine_config,
        )
        from supernova_core.config.parser import parse_config

        cfg_file = tmp_path / "c.yaml"
        cfg_file.write_text(_auth_config_yaml("playwright"))
        cfg = parse_config(str(cfg_file))

        _prepare_auth_engine_config(
            config=cfg, repo_path=str(tmp_path), proxy_url=None)

        data = _read_agent1_config(tmp_path)
        assert "proxy" not in data["browser"]["launchOptions"]

    def test_agent_browser_writes_no_playwright_config(self, tmp_path):
        from supernova_core.services.validate_authentication import (
            _prepare_auth_engine_config,
        )
        from supernova_core.config.parser import parse_config

        cfg_file = tmp_path / "c.yaml"
        cfg_file.write_text(_auth_config_yaml("agent-browser"))
        cfg = parse_config(str(cfg_file))

        _prepare_auth_engine_config(
            config=cfg, repo_path=str(tmp_path),
            proxy_url="http://127.0.0.1:8899")

        assert not (tmp_path / ".playwright").exists()


@pytest.mark.asyncio
async def test_validate_authentication_prepares_playwright_engine_config(tmp_path):
    """集成：validate_authentication 走 playwright + proxy 时，agent 执行前已写好
    agent1 session 引擎 config（此前认证链路从不写引擎 config）。"""
    from supernova_core.prompts.manager import PromptManager

    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(_auth_config_yaml("playwright"))
    ws = tmp_path / "ws"
    ws.mkdir()

    executor = MagicMock()
    executor.execute = AsyncMock(return_value=AgentMetrics(
        agent_name="validate-authentication", duration_ms=1,
        input_tokens=0, output_tokens=0))
    executor.execute.return_value.structured_output = {"login_success": True}

    result = await validate_authentication(
        web_url="https://x.com",
        config_path=str(cfg_file),
        workspace_path=str(ws),
        prompt_manager=MagicMock(),
        executor=executor,
        repo_path=str(tmp_path / "repo"),
        proxy_url="http://127.0.0.1:8899",
    )

    assert result.success is True
    data = _read_agent1_config(tmp_path / "repo")
    assert data["browser"]["launchOptions"]["proxy"]["server"] == "http://127.0.0.1:8899"
