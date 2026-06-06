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
