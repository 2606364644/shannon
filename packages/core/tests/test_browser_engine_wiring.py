"""Tests for browser engine configuration wiring.

Covers:
- BROWSER_ENGINE_UNAVAILABLE error code classification
- SHANNON_BROWSER_ENGINE env var override in config parser
- AgentExecutor browser_engine injection from config
"""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.models.errors import ErrorCode, PentestError, classify_error_for_temporal


# ---------------------------------------------------------------------------
# Task 1: Error code
# ---------------------------------------------------------------------------


class TestBrowserEngineUnavailableErrorCode:
    def test_error_code_exists(self):
        """BROWSER_ENGINE_UNAVAILABLE should be a valid ErrorCode."""
        assert hasattr(ErrorCode, "BROWSER_ENGINE_UNAVAILABLE")
        assert ErrorCode.BROWSER_ENGINE_UNAVAILABLE.value == "BROWSER_ENGINE_UNAVAILABLE"

    def test_classified_as_configuration_error_non_retryable(self):
        """BROWSER_ENGINE_UNAVAILABLE should classify as ConfigurationError, not retryable."""
        error = PentestError(
            "Browser engine 'agent-browser' is not available.",
            "browser",
            error_code=ErrorCode.BROWSER_ENGINE_UNAVAILABLE,
        )
        error_type, retryable = classify_error_for_temporal(error)
        assert error_type == "ConfigurationError"
        assert retryable is False


from shannon_core.config.parser import parse_config


def _write_config(tmp_path: Path, content: str) -> str:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(content, encoding="utf-8")
    return str(config_file)


# ---------------------------------------------------------------------------
# Task 2: Env var override
# ---------------------------------------------------------------------------


class TestBrowserEngineEnvVarOverride:
    def test_env_var_overrides_yaml_value(self, tmp_path, monkeypatch):
        """SHANNON_BROWSER_ENGINE env var should override yaml browser_engine."""
        config_path = _write_config(tmp_path, "browser_engine: playwright\n")
        monkeypatch.setenv("SHANNON_BROWSER_ENGINE", "agent-browser")
        config = parse_config(config_path)
        assert config.browser_engine == "agent-browser"

    def test_env_var_sets_engine_when_yaml_omits_it(self, tmp_path, monkeypatch):
        """SHANNON_BROWSER_ENGINE should set browser_engine even when yaml omits it."""
        config_path = _write_config(tmp_path, "description: test app\n")
        monkeypatch.setenv("SHANNON_BROWSER_ENGINE", "agent-browser")
        config = parse_config(config_path)
        assert config.browser_engine == "agent-browser"

    def test_default_playwright_without_env_var(self, tmp_path, monkeypatch):
        """Without SHANNON_BROWSER_ENGINE, browser_engine defaults to playwright."""
        monkeypatch.delenv("SHANNON_BROWSER_ENGINE", raising=False)
        config_path = _write_config(tmp_path, "description: test app\n")
        config = parse_config(config_path)
        assert config.browser_engine == "playwright"

    def test_invalid_env_var_raises_validation_error(self, tmp_path, monkeypatch):
        """Invalid SHANNON_BROWSER_ENGINE value (e.g. 'chromium') should raise PentestError."""
        config_path = _write_config(tmp_path, "description: test app\n")
        monkeypatch.setenv("SHANNON_BROWSER_ENGINE", "chromium")
        with pytest.raises(PentestError, match="validation failed"):
            parse_config(config_path)


from shannon_core.agents.executor import AgentExecutor
from shannon_core.prompts.manager import PromptManager
from shannon_core.models.agents import AgentName
from shannon_core.git_manager import GitManager


# ---------------------------------------------------------------------------
# Task 3: Executor injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_injects_browser_engine_from_config(tmp_path):
    """AgentExecutor should inject browser_engine from config into prompt variables."""
    import shannon_core.services.engines  # noqa: F401 — register engines

    config_file = tmp_path / "config.yaml"
    config_file.write_text("browser_engine: agent-browser")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".shannon" / "deliverables").mkdir(parents=True)

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "recon.txt").write_text("{{BROWSER_COMMANDS}}")

    captured_variables = {}

    def mock_load_sync(self, template_name, variables, **kwargs):
        captured_variables.update(variables)
        return "mock prompt"

    async def mock_run_claude(prompt, **kw):
        r = MagicMock()
        r.success = True
        r.error = None
        r.cost = 0.0
        r.turns = 1
        r.model = "test"
        r.structured_output = None
        r.tokens = None
        r.text = ""
        return r

    with patch.object(PromptManager, "load_sync", mock_load_sync), \
         patch("shannon_core.agents.executor.run_claude_prompt", side_effect=mock_run_claude), \
         patch.object(GitManager, "create_checkpoint", new_callable=AsyncMock), \
         patch.object(GitManager, "commit", new_callable=AsyncMock), \
         patch("shannon_core.agents.executor.validate_deliverable", new_callable=AsyncMock):

        pm = PromptManager(prompts_dir)
        executor = AgentExecutor(pm)
        await executor.execute(
            agent_name=AgentName.RECON,
            repo_path=str(repo),
            config_path=str(config_file),
        )

    assert captured_variables["browser_engine"] == "agent-browser"


@pytest.mark.asyncio
async def test_executor_no_browser_engine_without_config(tmp_path):
    """Without a config file, executor should not inject browser_engine."""
    import shannon_core.services.engines  # noqa: F401 — register engines

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".shannon" / "deliverables").mkdir(parents=True)

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "recon.txt").write_text("{{BROWSER_COMMANDS}}")

    captured_variables = {}

    def mock_load_sync(self, template_name, variables, **kwargs):
        captured_variables.update(variables)
        return "mock prompt"

    async def mock_run_claude(prompt, **kw):
        r = MagicMock()
        r.success = True
        r.error = None
        r.cost = 0.0
        r.turns = 1
        r.model = "test"
        r.structured_output = None
        r.tokens = None
        r.text = ""
        return r

    with patch.object(PromptManager, "load_sync", mock_load_sync), \
         patch("shannon_core.agents.executor.run_claude_prompt", side_effect=mock_run_claude), \
         patch.object(GitManager, "create_checkpoint", new_callable=AsyncMock), \
         patch.object(GitManager, "commit", new_callable=AsyncMock), \
         patch("shannon_core.agents.executor.validate_deliverable", new_callable=AsyncMock):

        pm = PromptManager(prompts_dir)
        executor = AgentExecutor(pm)
        await executor.execute(
            agent_name=AgentName.RECON,
            repo_path=str(repo),
        )

    # No config → no browser_engine key → PromptManager defaults to "playwright"
    assert "browser_engine" not in captured_variables
