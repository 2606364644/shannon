"""Engines subpackage – concrete BrowserEngine implementations.

Engine classes are registered with ``BrowserEngineFactory`` here so that
the rest of the codebase can look them up by name.
"""

from __future__ import annotations

from shannon_core.services.browser_engine import BrowserEngineFactory

# ---------------------------------------------------------------------------
# Register concrete engine implementations.
# AgentBrowserEngine will be uncommented once it is implemented:
#
# from shannon_core.services.engines.agent_browser_engine import AgentBrowserEngine
# BrowserEngineFactory.register("agent-browser", AgentBrowserEngine)
# ---------------------------------------------------------------------------

from shannon_core.services.engines.playwright_engine import PlaywrightEngine

BrowserEngineFactory.register("playwright", PlaywrightEngine)

__all__ = ["BrowserEngineFactory"]
