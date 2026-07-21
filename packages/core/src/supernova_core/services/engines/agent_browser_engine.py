"""AgentBrowserEngine – concrete BrowserEngine backed by Vercel Labs' agent-browser.

Encapsulates all agent-browser-specific config generation and CLI command
formatting so the rest of supernova can treat it uniformly through the
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

from supernova_core.services.browser_engine import BrowserEngine

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
        """Best-effort 回收 agent-browser daemon + Chrome 子进程。

        优先优雅 ``agent-browser close``(按 session / --all),失败/残留再兜底。
        全程 try/except,绝不 raise(清理不能反过来崩扫描/阻塞退出)。

        兜底策略(close 失败时):

        - **Chrome** : ``pkill -f "headless.*profiles/{sid} "`` —— **尾随空格**
          是关键:真实 session ID 里 ``agent-auth`` 是 ``agent-authz`` 的前缀
          (见 ``AGENT_SESSION_MAPPING``),无尾空格的 ``profiles/agent-auth`` 会
          连杀并发 ``agent-authz`` 的 Chrome。Chrome cmdline 里 ``profiles/{sid}``
          后跟一个空格(``--user-data-dir=...profiles/{sid} --window-size=...``),
          故尾随空格精准隔离。
        - **daemon** : agent-browser 的 daemon(``agent-browser-linux-x64``)
          daemon 化后 cmdline 裸(零参数),``pkill -f`` 无法按 profile 匹配(旧
          ``agent-browser.*profiles/{sid}`` pattern 是死代码,已删)。per-session
          杀 daemon 改沿残留 Chrome 的 PPID 链:``pgrep`` 拿 Chrome PID →
          ``ps -o ppid=`` 找父 → 父 ``comm`` 以 ``agent-browser`` 开头则 kill
          (per-session 不误并发,每 session 有独立 daemon + 独立 profile 路径的
          Chrome)。pgrep/ps/kill 任一不可用则安全跳过(边缘泄漏由强退路径
          ``close --all`` 兜底清所有 daemon)。
        """
        import logging

        log = logging.getLogger(__name__)
        closed: list[str] = []
        killed: list[str] = []
        killed_daemons: list[str] = []
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

        def _run_capture(cmd: list[str], timeout: float = 5.0) -> tuple[int, str]:
            """跑命令返回 (returncode, stdout);异常吞掉返回 (-1, '')。"""
            try:
                proc = subprocess.run(
                    cmd,
                    timeout=timeout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                return proc.returncode, proc.stdout or ""
            except Exception as exc:  # noqa: BLE001 - best-effort,绝不抛
                errors.append(f"{' '.join(cmd)}: {exc}")
                return -1, ""

        def _kill_daemon_via_ppid(sid: str) -> None:
            """沿残留 Chrome 的 PPID 链杀 per-session daemon(close 失败兜底)。

            daemon cmdline 裸,pkill -f 匹配不到;改用 pgrep 残留 Chrome → ps 父
            → 父 comm 以 agent-browser 开头则 kill。pgrep 的 pattern 同样带尾随
            空格隔离前缀(agent-auth vs agent-authz)。
            """
            rc, out = _run_capture(
                ["pgrep", "-f", f"headless.*profiles/{sid} "], timeout=5.0
            )
            if rc != 0 or not out:
                return
            for token in out.split():
                try:
                    chrome_pid = int(token)
                except ValueError:
                    continue
                # 查父进程 PID
                prc, ppid_out = _run_capture(
                    ["ps", "-o", "ppid=", "-p", str(chrome_pid)], timeout=3.0
                )
                ppid_s = ppid_out.strip()
                if prc != 0 or not ppid_s:
                    continue
                # 确认父进程是 agent-browser daemon(避免误杀非 daemon 父,如 init)
                crc, comm_out = _run_capture(
                    ["ps", "-o", "comm=", "-p", ppid_s], timeout=3.0
                )
                if crc != 0 or not comm_out.strip().startswith("agent-browser"):
                    continue
                _run(["kill", ppid_s], timeout=3.0)
                killed_daemons.append(ppid_s)

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

            # 2. close 失败兜底
            if sid is None:
                # 强退路径:粗粒度清所有 daemon + Chrome(close --all 已尝试过)
                _run(["pkill", "-f", "agent-browser"], timeout=5.0)
                _run(["pkill", "-f", "headless.*agent-browser"], timeout=5.0)
            else:
                # Chrome 子进程:尾随空格隔离前缀(agent-auth vs agent-authz)
                _run(
                    ["pkill", "-f", f"headless.*profiles/{sid} "],
                    timeout=5.0,
                )
                killed.append(close_tag)
                # daemon:cmdline 裸,pkill 匹配不到;沿 PPID 链精准杀 per-session daemon
                _kill_daemon_via_ppid(sid)

        log.debug(
            "agent-browser cleanup: closed=%s killed=%s killed_daemons=%s errors=%s",
            closed, killed, killed_daemons, errors,
        )
        return {
            "closed": closed,
            "killed": killed,
            "killed_daemons": killed_daemons,
            "errors": errors,
        }
