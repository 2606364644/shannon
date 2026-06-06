"""Write stealth Playwright config + anti-detection init script.

Direct port of shannon/apps/worker/src/ai/playwright-config-writer.ts.
Enhanced with per-agent session isolation for concurrent exploit agents.
"""

from __future__ import annotations

import json
from pathlib import Path

# Maps agent names to isolated browser session IDs.
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
        config["browser"]["contextOptions"]["storageState"] = f".playwright/state/{session_id}/storage.json"
    return config


def write_stealth_config(source_dir: str, session_id: str | None = None) -> dict:
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


def cleanup_stealth_config(source_dir: str) -> None:
    """Remove the .playwright/ directory created by write_stealth_config."""
    import shutil

    pw_dir = Path(source_dir) / ".playwright"
    if pw_dir.exists():
        shutil.rmtree(pw_dir)


def cleanup_session_config(source_dir: str, session_id: str) -> None:
    """Remove a session-specific config file (not the entire .playwright/ dir)."""
    pw_dir = Path(source_dir) / ".playwright"
    config_path = pw_dir / f"cli.config.{session_id}.json"
    if config_path.exists():
        config_path.unlink()
    # Clean up session state dir
    state_dir = pw_dir / "state" / session_id
    if state_dir.exists():
        import shutil
        shutil.rmtree(state_dir)
