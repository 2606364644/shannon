"""AgentBrowserEngine – concrete BrowserEngine backed by Vercel Labs' agent-browser.

Encapsulates all agent-browser-specific config generation and CLI command
formatting so the rest of shannon-py can treat it uniformly through the
BrowserEngine protocol.

Key differences from PlaywrightEngine:

- Session flag uses ``--session <id>`` (space-separated) instead of ``-s=<id>``
- Selector model uses @ref tokens from accessibility snapshots instead of CSS/XPath
- Anti-detection is built-in (no stealth.js injection needed)
- Auth persistence uses ``--profile <path>`` flag (auto-persists, no explicit save/load)
"""

from __future__ import annotations

import shutil
from pathlib import Path

from shannon_core.services.browser_engine import BrowserEngine

# ---------------------------------------------------------------------------
# Command reference – injected into LLM prompts
# ---------------------------------------------------------------------------

_COMMANDS_REFERENCE = """\
Agent-Browser CLI Commands (use these for browser automation):

All commands require --session <session_id> for session isolation.
Replace <session> with the current session ID in every command.

NAVIGATION:
  agent-browser --session <session> open <url>
    Navigate to a URL.

ACCESSIBILITY SNAPSHOT:
  agent-browser --session <session> snapshot
    Returns an accessibility tree of the current page. Elements are labeled
    with @ref selectors (e.g., @e1, @e2, @e3). Use these @ref selectors for
    all subsequent interactions (click, fill, etc.). Do NOT use CSS selectors
    or XPath — always snapshot first to discover @ref tokens.

CLICK:
  agent-browser --session <session> click @<ref>
    Click an element identified by its @ref selector from the snapshot.
    Example: agent-browser --session s1 click @e5

FILL / TYPE:
  agent-browser --session <session> fill @<ref> <text>
    Fill a text input identified by its @ref selector with the given text.
    Example: agent-browser --session s1 fill @e3 hello@example.com

SCREENSHOT:
  agent-browser --session <session> screenshot
    Capture a screenshot of the current page.

GET CONTENT:
  agent-browser --session <session> get text
    Get the visible text content of the current page.
  agent-browser --session <session> get html
    Get the HTML content of the current page.

JAVASCRIPT EVALUATION:
  agent-browser --session <session> eval "<js>"
    Evaluate a JavaScript expression in the page context.
    Example: agent-browser --session s1 eval "document.title"

COOKIES:
  agent-browser --session <session> cookies set <name> <value>
    Set a cookie in the current session.
  agent-browser --session <session> cookies clear
    Clear all cookies in the current session.

AUTH STATE:
  Agent-browser uses a persistent Chrome profile via the --profile <path> flag.
  Auth state (cookies, localStorage, etc.) is automatically persisted to the
  profile directory. No explicit save/load commands are needed.

ANTI-DETECTION:
  Anti-detection measures are built-in to agent-browser. No stealth scripts
  or extra configuration is required.

WORKFLOW:
  1. Use `snapshot` to get the accessibility tree and discover @ref selectors.
  2. Use @ref selectors (e.g., @e1, @e2) for click and fill operations.
  3. Always pass --session <session> to every command.
"""


# ---------------------------------------------------------------------------
# AgentBrowserEngine
# ---------------------------------------------------------------------------


class AgentBrowserEngine:
    """BrowserEngine implementation backed by Vercel Labs' ``agent-browser``."""

    # -- Engine identity -----------------------------------------------------

    @property
    def name(self) -> str:
        """Engine identifier string."""
        return "agent-browser"

    def session_flag(self, session_id: str) -> str:
        """Return the CLI flag string for session isolation.

        agent-browser uses space-separated ``--session <id>`` instead of
        Playwright's ``-s=<id>``.
        """
        return f"--session {session_id}"

    def commands_reference(self) -> str:
        """Return agent-browser command reference text for prompt injection."""
        return _COMMANDS_REFERENCE

    # -- Auth helpers --------------------------------------------------------

    def auth_save_command(self, session_id: str, path: str) -> str:
        """Return empty string — agent-browser auto-persists via --profile."""
        return ""

    def auth_load_command(self, session_id: str, path: str) -> str:
        """Return empty string — agent-browser auto-restores via --profile."""
        return ""

    # -- Config management ---------------------------------------------------

    def write_config(
        self,
        source_dir: str,
        session_id: str | None = None,
    ) -> dict:
        """Create profile directory structure for agent-browser.

        Creates ``.agent-browser/profiles/{session_id}/`` under *source_dir*.
        If *session_id* is ``None`` or ``"default"``, uses
        ``.agent-browser/profiles/default/``.

        Returns ``{"result": "wrote"|"skipped-existing", "configPath": str}``.
        """
        base_dir = Path(source_dir) / ".agent-browser" / "profiles"

        effective_session = session_id if session_id and session_id != "default" else "default"
        profile_dir = base_dir / effective_session

        if profile_dir.exists():
            return {"result": "skipped-existing", "configPath": str(profile_dir)}

        profile_dir.mkdir(parents=True, exist_ok=True)

        return {"result": "wrote", "configPath": str(profile_dir)}

    def cleanup_config(
        self,
        source_dir: str,
        session_id: str | None = None,
    ) -> None:
        """Remove agent-browser profile directories.

        If *session_id* is provided, removes only that session's profile dir.
        If ``None``, removes the entire ``.agent-browser/`` directory.
        """
        if session_id is None:
            ab_dir = Path(source_dir) / ".agent-browser"
            if ab_dir.exists():
                shutil.rmtree(ab_dir)
        else:
            effective_session = session_id if session_id != "default" else "default"
            profile_dir = (
                Path(source_dir) / ".agent-browser" / "profiles" / effective_session
            )
            if profile_dir.exists():
                shutil.rmtree(profile_dir)

    # -- Availability check --------------------------------------------------

    def check_available(self) -> bool:
        """Check whether ``agent-browser`` is installed and reachable on PATH."""
        return shutil.which("agent-browser") is not None
