"""PlaywrightEngine – concrete BrowserEngine backed by playwright-cli.

Encapsulates all Playwright-specific config generation, stealth scripting,
and CLI command formatting so the rest of shannon-py can treat it uniformly
through the BrowserEngine protocol.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from shannon_core.services.browser_engine import BrowserEngine

# ---------------------------------------------------------------------------
# Stealth init script – anti-detection JavaScript injected into every context
# ---------------------------------------------------------------------------

_STEALTH_INIT_SCRIPT = """\
// Remove navigator.webdriver flag set by Playwright/Chrome automation
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
delete Object.getPrototypeOf(navigator).webdriver;

// Override navigator.plugins to appear as a real browser
Object.defineProperty(navigator, 'plugins', {
  get: () => {
    const arr = [
      { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
      { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
    ];
    arr.__proto__ = PluginArray.prototype;
    return arr;
  },
});

window.chrome = window.chrome || {};
window.chrome.runtime = window.chrome.runtime || {
  PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
  PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
  PlatformNaclArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
  RequestUpdateCheckStatus: { THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' },
  OnInstalledReason: { INSTALL: 'install', UPDATE: 'update', CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update' },
  OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
};
"""

# ---------------------------------------------------------------------------
# Command reference – injected into LLM prompts
# ---------------------------------------------------------------------------

_COMMANDS_REFERENCE = """\
Playwright CLI Commands (use these for browser automation):
- Navigation: playwright-cli -s=<session> navigate <url>
- Screenshots: playwright-cli -s=<session> screenshot --filename <path>
- JavaScript eval: playwright-cli -s=<session> eval "<js>"
- State management: playwright-cli -s=<session> state-save <path> / state-load <path>
- Element interaction: playwright-cli -s=<session> click <selector>, fill <selector> <text>
- Get content: playwright-cli -s=<session> get text <selector>, get html <selector>
Always pass -s=<session> to every command for session isolation."""


# ---------------------------------------------------------------------------
# Config builder helper
# ---------------------------------------------------------------------------


def _build_stealth_config(init_script_path: str, session_id: str | None = None) -> dict:
    """Build Playwright stealth config dict.

    When *session_id* is provided, adds a session-specific ``storageState`` path
    so each agent gets isolated cookies/localStorage.
    """
    config: dict = {
        "browser": {
            "browserName": "chromium",
            "launchOptions": {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled"],
                "ignoreDefaultArgs": ["--enable-automation"],
            },
            "contextOptions": {
                "viewport": {"width": 1920, "height": 1080},
                "locale": "en-US",
                "extraHTTPHeaders": {"Accept-Language": "en-US,en;q=0.9"},
                "userAgent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            },
            "initScript": [init_script_path],
        },
    }
    if session_id and session_id != "default":
        config["browser"]["contextOptions"]["storageState"] = (
            f".playwright/state/{session_id}/storage.json"
        )
    return config


# ---------------------------------------------------------------------------
# PlaywrightEngine
# ---------------------------------------------------------------------------


class PlaywrightEngine:
    """BrowserEngine implementation backed by ``playwright-cli``."""

    # -- Engine identity -----------------------------------------------------

    @property
    def name(self) -> str:
        """Engine identifier string."""
        return "playwright"

    def session_flag(self, session_id: str) -> str:
        """Return the CLI flag string for session isolation."""
        return f"-s={session_id}"

    def commands_reference(self) -> str:
        """Return playwright-cli command reference text for prompt injection."""
        return _COMMANDS_REFERENCE

    # -- Auth helpers --------------------------------------------------------

    def auth_save_command(self, session_id: str, path: str) -> str:
        """Return the CLI command string that saves auth state to *path*."""
        return f"state-save {path}"

    def auth_load_command(self, session_id: str, path: str) -> str:
        """Return the CLI command string that loads saved auth state from *path*."""
        return f"state-load {path}"

    # -- Config management ---------------------------------------------------

    def write_config(self, source_dir: str, session_id: str | None = None) -> dict:
        """Write Playwright stealth config under *source_dir*.

        When *session_id* is provided, writes a session-specific config file
        (e.g., ``.playwright/cli.config.agent-injection.json``) with isolated storage.
        When *session_id* is ``None`` or ``"default"``, writes the shared default config.

        Returns ``{"result": "wrote"|"skipped-existing", "configPath": str}``.
        """
        playwright_dir = Path(source_dir) / ".playwright"

        if session_id and session_id != "default":
            config_filename = f"cli.config.{session_id}.json"
        else:
            config_filename = "cli.config.json"

        config_path = playwright_dir / config_filename

        if config_path.exists():
            return {"result": "skipped-existing", "configPath": str(config_path)}

        init_script_path = playwright_dir / "scripts" / "stealth.js"
        init_script_path.parent.mkdir(parents=True, exist_ok=True)
        init_script_path.write_text(_STEALTH_INIT_SCRIPT)

        # Ensure storage directory exists for session-specific configs
        if session_id and session_id != "default":
            state_dir = playwright_dir / "state" / session_id
            state_dir.mkdir(parents=True, exist_ok=True)

        config = _build_stealth_config(str(init_script_path), session_id=session_id)
        config_path.write_text(json.dumps(config, indent=2))

        return {"result": "wrote", "configPath": str(config_path)}

    def cleanup_config(self, source_dir: str, session_id: str | None = None) -> None:
        """Remove Playwright config files and state.

        If *session_id* is provided, removes only that session's config and state
        directory.  If ``None``, removes the entire ``.playwright/`` directory.
        """
        if session_id is None:
            # Remove entire .playwright/ directory
            pw_dir = Path(source_dir) / ".playwright"
            if pw_dir.exists():
                shutil.rmtree(pw_dir)
        else:
            # Remove session-specific config + state
            pw_dir = Path(source_dir) / ".playwright"
            config_path = pw_dir / f"cli.config.{session_id}.json"
            if config_path.exists():
                config_path.unlink()
            state_dir = pw_dir / "state" / session_id
            if state_dir.exists():
                shutil.rmtree(state_dir)

    # -- Availability check --------------------------------------------------

    def check_available(self) -> bool:
        """Check whether ``playwright-cli`` is installed and reachable on PATH."""
        return shutil.which("playwright-cli") is not None
