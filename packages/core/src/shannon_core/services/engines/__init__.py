"""Engines subpackage – concrete BrowserEngine implementations.

Engine classes are registered with ``BrowserEngineFactory`` here so that
the rest of the codebase can look them up by name.
"""

from __future__ import annotations

from shannon_core.services.browser_engine import BrowserEngineFactory

# ---------------------------------------------------------------------------
# Register concrete engine implementations.
# ---------------------------------------------------------------------------

from shannon_core.services.engines.agent_browser_engine import AgentBrowserEngine
from shannon_core.services.engines.playwright_engine import PlaywrightEngine

BrowserEngineFactory.register("playwright", PlaywrightEngine)
BrowserEngineFactory.register("agent-browser", AgentBrowserEngine)

__all__ = ["BrowserEngineFactory"]
