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
import subprocess
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
  agent-browser --session <session> state save <path>
    Save cookies, localStorage, and auth state to a portable JSON file.
  agent-browser --session <session> state load <path>
    Restore saved auth state from a JSON file into the current session.

  Auth state also auto-persists via the --profile flag, but use
  `state save/load` to share auth across sessions (save in one, load in
  another). When a prompt gives an explicit AUTH_SAVE/AUTH_LOAD command,
  run it verbatim against {{AUTH_STATE_FILE}}.

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

    @property
    def cli_binary(self) -> str:
        """PATH binary name for availability checks."""
        return "agent-browser"

    def session_flag(self, session_id: str) -> str:
        """Return the CLI flag string for session isolation.

        agent-browser uses space-separated ``--session <id>`` plus
        ``--profile`` for persistent Chrome profile (auth state auto-persists).
        """
        return f"--session {session_id} --profile .agent-browser/profiles/{session_id}"

    def commands_reference(self) -> str:
        """Return agent-browser command reference text for prompt injection."""
        return _COMMANDS_REFERENCE

    # -- Auth helpers --------------------------------------------------------

    def auth_save_command(self, session_id: str, path: str) -> str:
        """Return the CLI command that saves auth state (cookies/localStorage) to *path*.

        agent-browser's native `state save <path>` writes a portable JSON file
        (cookies + storage + auth state), mirroring playwright's `state-save`.
        Used so auth-validation can hand login state to concurrent exploit agents
        via the shared auth-state.json (profile isolation alone can't cross sessions).
        """
        return f"state save {path}"

    def auth_load_command(self, session_id: str, path: str) -> str:
        """Return the CLI command that restores auth state from *path*."""
        return f"state load {path}"

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

    # -- Process lifecycle ---------------------------------------------------

    def cleanup_processes(
        self,
        source_dir: str | None = None,
        session_ids: list[str] | None = None,
    ) -> dict:
        """Best-effort 回收 agent-browser + Chrome 子进程。

        优先优雅 ``agent-browser close``(按 session / --all),失败/残留再
        ``pkill -f`` 兜底(匹配 profile 路径以精准隔离,不误杀并发扫描)。
        全程 try/except,绝不 raise(清理不能反过来崩扫描/阻塞退出)。
        """
        import logging

        log = logging.getLogger(__name__)
        closed: list[str] = []
        killed: list[str] = []
        errors: list[str] = []

        def _run(cmd: list[str], timeout: float = 5.0) -> int:
            """同步跑命令,返回 returncode;异常吞掉填 errors。"""
            try:
                proc = subprocess.run(
                    cmd,
                    timeout=timeout,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return proc.returncode
            except Exception as exc:  # noqa: BLE001 - best-effort,绝不抛
                errors.append(f"{' '.join(cmd)}: {exc}")
                return -1

        targets = session_ids if session_ids is not None else [None]

        for sid in targets:
            # 1. 优雅 close
            if sid is None:
                close_cmd = ["agent-browser", "close", "--all"]
                close_tag = "all"
            else:
                close_cmd = ["agent-browser", "--session", sid, "close"]
                close_tag = sid
            rc = _run(close_cmd, timeout=5.0)
            if rc == 0:
                closed.append(close_tag)
                continue  # close 成功 -> 不 pkill 该 session

            # 2. pkill 兜底(匹配 profile 路径以精准隔离)
            if sid is None:
                _run(["pkill", "-f", "agent-browser"], timeout=5.0)
            else:
                profile = f".agent-browser/profiles/{sid}"
                _run(["pkill", "-f", f"agent-browser.*{profile}"], timeout=5.0)
                killed.append(close_tag)
            # Chrome 子进程(headless chrome 带 profile user-data-dir)
            chrome_profile = "agent-browser" if sid is None else f"profiles/{sid}"
            _run(["pkill", "-f", f"headless.*{chrome_profile}"], timeout=5.0)

        log.debug(
            "agent-browser cleanup: closed=%s killed=%s errors=%s",
            closed, killed, errors,
        )
        return {"closed": closed, "killed": killed, "errors": errors}
