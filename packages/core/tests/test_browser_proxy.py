"""Task 5: per-scan proxy wiring into browser engine session_flag / stealth config.

Covers:
- agent-browser ``session_flag(session_id, proxy_url=...)`` appends ``--proxy <url>``.
- agent-browser ``session_flag(session_id)`` without proxy stays backward-compatible.
- playwright ``_build_stealth_config(..., proxy_url=...)`` writes ``launchOptions.proxy``.
- playwright ``_build_stealth_config(...)`` without proxy omits the ``proxy`` key.
- (nice-to-have) ``PromptManager.load_sync`` threads ``variables["proxy_url"]`` into
  the engine's ``session_flag`` call (uses a stub engine to avoid registering real ones).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from supernova_core.services.engines.agent_browser_engine import AgentBrowserEngine
from supernova_core.services.engines.playwright_engine import _build_stealth_config


# ---------------------------------------------------------------------------
# agent-browser session_flag
# ---------------------------------------------------------------------------


class TestAgentBrowserSessionFlagProxy:
    def test_session_flag_appends_proxy(self):
        """When proxy_url is given, session_flag appends ``--proxy <url>``."""
        engine = AgentBrowserEngine()
        flag = engine.session_flag("scanA", proxy_url="http://127.0.0.1:9090")
        assert "--session scanA" in flag
        assert "--profile .agent-browser/profiles/scanA" in flag
        assert "--proxy http://127.0.0.1:9090" in flag

    def test_session_flag_no_proxy_backward_compat(self):
        """Without proxy_url, flag is identical to pre-Task-5 behavior (no --proxy)."""
        engine = AgentBrowserEngine()
        flag = engine.session_flag("scanA")  # proxy_url=None
        assert "--proxy" not in flag
        assert "--session scanA" in flag
        assert "--profile .agent-browser/profiles/scanA" in flag

    def test_session_flag_default_kwargs_equivalent(self):
        """Calling session_flag(sid) == session_flag(sid, proxy_url=None)."""
        engine = AgentBrowserEngine()
        assert engine.session_flag("x") == engine.session_flag("x", proxy_url=None)


# ---------------------------------------------------------------------------
# playwright _build_stealth_config
# ---------------------------------------------------------------------------


class TestPlaywrightStealthConfigProxy:
    def test_launchoptions_proxy_emitted_when_proxy_url_given(self):
        """proxy_url writes launchOptions.proxy.server = <url>."""
        cfg = _build_stealth_config(
            "/tmp/init.js", session_id="s1", proxy_url="http://127.0.0.1:9090"
        )
        assert (
            cfg["browser"]["launchOptions"]["proxy"]["server"]
            == "http://127.0.0.1:9090"
        )

    def test_launchoptions_proxy_omitted_when_no_proxy_url(self):
        """Without proxy_url, the launchOptions.proxy key MUST be absent."""
        cfg = _build_stealth_config("/tmp/init.js", session_id="s1")  # proxy_url=None
        assert "proxy" not in cfg["browser"]["launchOptions"]

    def test_launchoptions_proxy_omitted_default_kwargs(self):
        """_build_stealth_config(path) (no kwargs) omits proxy."""
        cfg = _build_stealth_config("/tmp/init.js")
        assert "proxy" not in cfg["browser"]["launchOptions"]


# ---------------------------------------------------------------------------
# Optional: manager threads variables["proxy_url"] -> engine.session_flag
# ---------------------------------------------------------------------------


def test_manager_threads_proxy_url_into_session_flag(tmp_path):
    """PromptManager.load_sync must pass variables["proxy_url"] to engine.session_flag.

    Uses a stub engine registered under a unique name to avoid clashing with the
    real factory registrations. Verifies the manager-level wiring (L146).
    """
    from supernova_core.prompts.manager import PromptManager
    from supernova_core.services.browser_engine import BrowserEngineFactory

    captured: dict[str, object] = {}

    class _StubEngine:
        @property
        def name(self) -> str:
            return "stub-proxy-engine"

        @property
        def cli_binary(self) -> str:
            return "stub-cli"

        def session_flag(self, session_id: str, proxy_url: str | None = None) -> str:
            captured["session_id"] = session_id
            captured["proxy_url"] = proxy_url
            return f"--stub-session {session_id} --stub-proxy {proxy_url}"

        def commands_reference(self) -> str:
            return "stub commands"

        def auth_save_command(self, session_id: str, path: str) -> str:
            return ""

        def auth_load_command(self, session_id: str, path: str) -> str:
            return ""

        def write_config(self, source_dir, session_id=None, proxy_url=None) -> dict:
            return {"result": "wrote", "configPath": source_dir}

        def cleanup_config(self, source_dir, session_id=None) -> None:
            return None

        def cleanup_processes(self, source_dir=None, session_ids=None) -> dict:
            return {"closed": [], "killed": [], "errors": []}

        def check_available(self) -> bool:
            return True

    stub_name = "stub-proxy-engine-t5"
    # Register only if not already (other test runs in same session)
    if stub_name not in BrowserEngineFactory._engines:
        BrowserEngineFactory.register(stub_name, _StubEngine)

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "recon.txt").write_text("flag={{BROWSER_SESSION_FLAG}}")

    manager = PromptManager(prompts)
    manager.load_sync(
        "recon",
        {
            "web_url": "https://example.com",
            "repo_path": "/r",
            "browser_engine": stub_name,
            "proxy_url": "http://127.0.0.1:9090",
        },
    )

    assert captured.get("proxy_url") == "http://127.0.0.1:9090"


def test_manager_no_proxy_url_passes_none(tmp_path):
    """When variables has no proxy_url, session_flag receives proxy_url=None."""
    from supernova_core.prompts.manager import PromptManager
    from supernova_core.services.browser_engine import BrowserEngineFactory

    captured: dict[str, object] = {}

    class _StubEngine2:
        @property
        def name(self) -> str:
            return "stub-proxy-engine-2"

        @property
        def cli_binary(self) -> str:
            return "stub-cli"

        def session_flag(self, session_id: str, proxy_url: str | None = None) -> str:
            captured["proxy_url"] = proxy_url
            return "--stub"

        def commands_reference(self) -> str:
            return ""

        def auth_save_command(self, session_id, path) -> str:
            return ""

        def auth_load_command(self, session_id, path) -> str:
            return ""

        def write_config(self, source_dir, session_id=None, proxy_url=None) -> dict:
            return {"result": "wrote", "configPath": source_dir}

        def cleanup_config(self, source_dir, session_id=None) -> None:
            return None

        def cleanup_processes(self, source_dir=None, session_ids=None) -> dict:
            return {"closed": [], "killed": [], "errors": []}

        def check_available(self) -> bool:
            return True

    stub_name = "stub-proxy-engine-t5-none"
    if stub_name not in BrowserEngineFactory._engines:
        BrowserEngineFactory.register(stub_name, _StubEngine2)

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "recon.txt").write_text("flag={{BROWSER_SESSION_FLAG}}")

    manager = PromptManager(prompts)
    manager.load_sync(
        "recon",
        {
            "web_url": "https://example.com",
            "repo_path": "/r",
            "browser_engine": stub_name,
        },
    )

    assert captured.get("proxy_url") is None
