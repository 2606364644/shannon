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
