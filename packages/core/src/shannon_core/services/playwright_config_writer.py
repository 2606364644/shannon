"""Write stealth Playwright config + anti-detection init script.

Direct port of shannon/apps/worker/src/ai/playwright-config-writer.ts.
"""

from __future__ import annotations

import json
from pathlib import Path

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


def _build_stealth_config(init_script_path: str) -> dict:
    return {
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


def write_stealth_config(source_dir: str) -> dict:
    """Write .playwright/cli.config.json + scripts/stealth.js under *source_dir*.

    Returns ``{"result": "wrote"|"skipped-existing", "configPath": str}``.
    """
    playwright_dir = Path(source_dir) / ".playwright"
    config_path = playwright_dir / "cli.config.json"

    if config_path.exists():
        return {"result": "skipped-existing", "configPath": str(config_path)}

    init_script_path = playwright_dir / "scripts" / "stealth.js"
    init_script_path.parent.mkdir(parents=True, exist_ok=True)
    init_script_path.write_text(_STEALTH_INIT_SCRIPT)

    config = _build_stealth_config(str(init_script_path))
    config_path.write_text(json.dumps(config, indent=2))

    return {"result": "wrote", "configPath": str(config_path)}


def cleanup_stealth_config(source_dir: str) -> None:
    """Remove the .playwright/ directory created by write_stealth_config."""
    import shutil

    pw_dir = Path(source_dir) / ".playwright"
    if pw_dir.exists():
        shutil.rmtree(pw_dir)
