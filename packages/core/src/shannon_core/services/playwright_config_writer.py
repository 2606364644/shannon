"""Write stealth Playwright config + anti-detection init script.

Direct port of shannon/apps/worker/src/ai/playwright-config-writer.ts.
Enhanced with per-agent session isolation for concurrent exploit agents.

This module is a thin backward-compatible facade that delegates all logic
to :class:`PlaywrightEngine`.
"""

from __future__ import annotations

from shannon_core.services.engines.playwright_engine import PlaywrightEngine

_engine = PlaywrightEngine()

# ---------------------------------------------------------------------------
# Session mapping (unchanged public API)
# ---------------------------------------------------------------------------

AGENT_SESSION_MAPPING: dict[str, str] = {
    "injection-exploit": "agent-injection",
    "xss-exploit": "agent-xss",
    "auth-exploit": "agent-auth",
    "ssrf-exploit": "agent-ssrf",
    "authz-exploit": "agent-authz",
}


def get_session_id(agent_name: str) -> str:
    """Return the browser session ID for a given agent name."""
    return AGENT_SESSION_MAPPING.get(agent_name, "default")


# ---------------------------------------------------------------------------
# Facade functions – delegate to the engine
# ---------------------------------------------------------------------------


def write_stealth_config(source_dir: str, session_id: str | None = None) -> dict:
    """Write Playwright stealth config under *source_dir*.

    Delegates to :meth:`PlaywrightEngine.write_config`.
    """
    return _engine.write_config(source_dir, session_id=session_id)


def cleanup_stealth_config(source_dir: str) -> None:
    """Remove the .playwright/ directory created by write_stealth_config.

    Delegates to :meth:`PlaywrightEngine.cleanup_config` with ``session_id=None``.
    """
    _engine.cleanup_config(source_dir, session_id=None)


def cleanup_session_config(source_dir: str, session_id: str) -> None:
    """Remove a session-specific config file (not the entire .playwright/ dir).

    Delegates to :meth:`PlaywrightEngine.cleanup_config` with the given *session_id*.
    """
    _engine.cleanup_config(source_dir, session_id=session_id)
