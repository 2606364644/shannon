# Dual Browser Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `agent-browser` as a fully interchangeable alternative to `playwright-cli`, selected via `Config.browser_engine` field or `SHANNON_BROWSER_ENGINE` env var.

**Architecture:** Define a `BrowserEngine` protocol with two implementations (`PlaywrightEngine`, `AgentBrowserEngine`). A `BrowserEngineFactory` resolves the engine from config. `PromptManager` injects engine-specific variables (`BROWSER_SESSION_FLAG`, `BROWSER_COMMANDS`, `BROWSER_AUTH_RESTORE`, `BROWSER_AUTH_SAVE`) into all prompt templates, replacing hardcoded `playwright-cli` commands.

**Tech Stack:** Python 3.12+, pydantic, existing prompt template system

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `packages/core/src/shannon_core/services/browser_engine.py` | `BrowserEngine` Protocol + `BrowserEngineFactory` |
| Create | `packages/core/src/shannon_core/services/engines/__init__.py` | Engine subpackage, registers both engines |
| Create | `packages/core/src/shannon_core/services/engines/playwright_engine.py` | Playwright engine implementation |
| Create | `packages/core/src/shannon_core/services/engines/agent_browser_engine.py` | AgentBrowser engine implementation |
| Create | `packages/core/tests/test_browser_engine.py` | Protocol, factory, and integration tests |
| Create | `packages/core/tests/test_agent_browser_engine.py` | AgentBrowser engine tests |
| Modify | `packages/core/src/shannon_core/models/config.py` | Add `browser_engine` field |
| Modify | `packages/core/src/shannon_core/config/parser.py` | Add env var override for `browser_engine` |
| Modify | `packages/core/src/shannon_core/prompts/manager.py` | Add engine variable injection |
| Modify | `packages/core/src/shannon_core/services/playwright_config_writer.py` | Delegate to `PlaywrightEngine`, keep public API as facade |
| Modify | `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py` | Use `browser_session_id` variable |
| Modify | `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | Use engine interface |
| Modify | `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | Use engine interface |
| Modify | `packages/core/src/shannon_core/services/__init__.py` | Export browser engine symbols |
| Modify | All 24 prompt template files | Replace `playwright-cli` references with engine-agnostic placeholders |
| Modify | `packages/core/tests/test_playwright_config_writer.py` | Verify facade still works |
| Modify | `packages/core/tests/test_prompt_manager.py` | Add browser variable injection tests |
| Modify | `.env.example` | Document `SHANNON_BROWSER_ENGINE` |

---

### Task 1: BrowserEngine Protocol + Factory + Config Extension

**Files:**
- Create: `packages/core/src/shannon_core/services/browser_engine.py`
- Modify: `packages/core/src/shannon_core/models/config.py`
- Modify: `packages/core/src/shannon_core/config/parser.py`
- Create: `packages/core/tests/test_browser_engine.py`

- [ ] **Step 1: Write failing tests for BrowserEngineFactory**

```python
# packages/core/tests/test_browser_engine.py
import pytest


class TestBrowserEngineFactory:
    def test_get_unknown_engine_raises(self):
        from shannon_core.services.browser_engine import BrowserEngineFactory
        with pytest.raises(ValueError, match="Unknown browser engine"):
            BrowserEngineFactory.get_engine("nonexistent")

    def test_get_engine_returns_registered_instance(self):
        from shannon_core.services.browser_engine import BrowserEngineFactory
        from shannon_core.services.engines import _ensure_registered
        _ensure_registered()
        engine = BrowserEngineFactory.get_engine("playwright")
        assert engine.name == "playwright"

    def test_get_agent_browser_engine(self):
        from shannon_core.services.browser_engine import BrowserEngineFactory
        from shannon_core.services.engines import _ensure_registered
        _ensure_registered()
        engine = BrowserEngineFactory.get_engine("agent-browser")
        assert engine.name == "agent-browser"

    def test_factory_returns_new_instance_each_time(self):
        from shannon_core.services.browser_engine import BrowserEngineFactory
        from shannon_core.services.engines import _ensure_registered
        _ensure_registered()
        a = BrowserEngineFactory.get_engine("playwright")
        b = BrowserEngineFactory.get_engine("playwright")
        assert a is not b


class TestConfigBrowserEngine:
    def test_default_is_playwright(self):
        from shannon_core.models.config import Config
        config = Config()
        assert config.browser_engine == "playwright"

    def test_accepts_agent_browser(self):
        from shannon_core.models.config import Config
        config = Config(browser_engine="agent-browser")
        assert config.browser_engine == "agent-browser"

    def test_rejects_invalid_engine(self):
        from shannon_core.models.config import Config
        import pytest
        with pytest.raises(Exception):
            Config(browser_engine="selenium")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_browser_engine.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Create BrowserEngine Protocol + BrowserEngineFactory**

```python
# packages/core/src/shannon_core/services/browser_engine.py
"""Browser engine abstraction layer.

Provides a Protocol-based interface for browser automation engines
(playwright-cli, agent-browser) with a factory for resolution.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BrowserEngine(Protocol):
    """Unified interface for browser automation engines."""

    @property
    def name(self) -> str:
        """Engine identifier: 'playwright' | 'agent-browser'."""
        ...

    def session_flag(self, session_id: str) -> str:
        """CLI flag string for session isolation.

        playwright:     "-s={session_id}"
        agent-browser:  "--session {session_id} --profile .agent-browser/profiles/{session_id}"
        """
        ...

    def commands_reference(self) -> str:
        """Engine-specific command reference text for prompt injection."""
        ...

    def auth_restore_block(self, session_flag: str, auth_state_file: str) -> str:
        """Full text block for auth restore instructions.

        playwright:    instructions including "state-load" command
        agent-browser: instructions noting profile auto-persists
        """
        ...

    def auth_save_block(self, session_flag: str, auth_state_file: str) -> str:
        """Full text block for auth save instructions.

        playwright:    instructions including "state-save" command
        agent-browser: instructions noting profile auto-persists
        """
        ...

    def write_config(self, source_dir: str, session_id: str | None = None) -> dict:
        """Write engine config files.

        Returns {'result': 'wrote'|'skipped-existing', 'configPath': str}.
        """
        ...

    def cleanup_config(self, source_dir: str, session_id: str | None = None) -> None:
        """Remove engine config files and state."""
        ...

    def cleanup_all(self, source_dir: str) -> None:
        """Remove all engine artifacts for a source directory."""
        ...

    def check_available(self) -> bool:
        """Check if the engine CLI is installed and usable."""
        ...


class BrowserEngineFactory:
    """Registry and factory for browser engine implementations."""

    _engines: dict[str, type] = {}

    @classmethod
    def register(cls, name: str, engine_class: type) -> None:
        cls._engines[name] = engine_class

    @classmethod
    def get_engine(cls, engine_name: str) -> BrowserEngine:
        if engine_name not in cls._engines:
            available = ", ".join(sorted(cls._engines.keys())) or "(none registered)"
            raise ValueError(
                f"Unknown browser engine: {engine_name!r}. Available: {available}"
            )
        return cls._engines[engine_name]()

    @classmethod
    def available_engines(cls) -> list[str]:
        return sorted(cls._engines.keys())
```

- [ ] **Step 4: Add `browser_engine` field to Config model**

Add to `packages/core/src/shannon_core/models/config.py`:

```python
# Add to the Literal imports line or add a new type:
BrowserEngineType = Literal["playwright", "agent-browser"]

# Add field to Config class (after auto_detect_whitebox):
class Config(BaseModel):
    rules: Rules | None = None
    authentication: Authentication | None = None
    pipeline: PipelineConfig | None = None
    description: str | None = None
    vuln_classes: list[VulnClass] | None = None
    exploit: bool = True
    report: ReportConfig | None = None
    rules_of_engagement: str | None = None
    auto_detect_whitebox: bool = True
    browser_engine: BrowserEngineType = "playwright"
```

- [ ] **Step 5: Add env var override to config parser**

In `packages/core/src/shannon_core/config/parser.py`, inside `parse_config()`, after `raw = yaml.safe_load(content)` and before constructing `Config`, add:

```python
    # Environment variable override for browser engine
    if env_engine := os.environ.get("SHANNON_BROWSER_ENGINE"):
        raw["browser_engine"] = env_engine
```

Also add `import os` at the top of the file if not present.

- [ ] **Step 6: Create engines subpackage with registration**

```python
# packages/core/src/shannon_core/services/engines/__init__.py
"""Browser engine implementations and registration."""

from shannon_core.services.browser_engine import BrowserEngineFactory

_registered = False


def _ensure_registered() -> None:
    """Register engine implementations (idempotent)."""
    global _registered
    if _registered:
        return
    from .playwright_engine import PlaywrightEngine
    from .agent_browser_engine import AgentBrowserEngine

    BrowserEngineFactory.register("playwright", PlaywrightEngine)
    BrowserEngineFactory.register("agent-browser", AgentBrowserEngine)
    _registered = True


_ensure_registered()
```

- [ ] **Step 7: Create minimal PlaywrightEngine stub (placeholder to make factory tests pass)**

```python
# packages/core/src/shannon_core/services/engines/playwright_engine.py
"""Playwright browser engine implementation."""

from shannon_core.services.browser_engine import BrowserEngine


class PlaywrightEngine:
    """Playwright-cli browser engine."""

    @property
    def name(self) -> str:
        return "playwright"

    def session_flag(self, session_id: str) -> str:
        return f"-s={session_id}"

    def commands_reference(self) -> str:
        return ""  # Implemented in Task 2

    def auth_restore_block(self, session_flag: str, auth_state_file: str) -> str:
        return ""  # Implemented in Task 2

    def auth_save_block(self, session_flag: str, auth_state_file: str) -> str:
        return ""  # Implemented in Task 2

    def write_config(self, source_dir: str, session_id: str | None = None) -> dict:
        return {"result": "skipped-existing", "configPath": ""}  # Implemented in Task 2

    def cleanup_config(self, source_dir: str, session_id: str | None = None) -> None:
        pass  # Implemented in Task 2

    def cleanup_all(self, source_dir: str) -> None:
        pass  # Implemented in Task 2

    def check_available(self) -> bool:
        import shutil
        return shutil.which("playwright-cli") is not None
```

- [ ] **Step 8: Create minimal AgentBrowserEngine stub (placeholder to make factory tests pass)**

```python
# packages/core/src/shannon_core/services/engines/agent_browser_engine.py
"""Agent-browser (Vercel Labs) browser engine implementation."""

from shannon_core.services.browser_engine import BrowserEngine


class AgentBrowserEngine:
    """agent-browser CLI browser engine."""

    @property
    def name(self) -> str:
        return "agent-browser"

    def session_flag(self, session_id: str) -> str:
        return f"--session {session_id} --profile .agent-browser/profiles/{session_id}"

    def commands_reference(self) -> str:
        return ""  # Implemented in Task 3

    def auth_restore_block(self, session_flag: str, auth_state_file: str) -> str:
        return ""  # Implemented in Task 3

    def auth_save_block(self, session_flag: str, auth_state_file: str) -> str:
        return ""  # Implemented in Task 3

    def write_config(self, source_dir: str, session_id: str | None = None) -> dict:
        return {"result": "skipped-existing", "configPath": ""}  # Implemented in Task 3

    def cleanup_config(self, source_dir: str, session_id: str | None = None) -> None:
        pass  # Implemented in Task 3

    def cleanup_all(self, source_dir: str) -> None:
        pass  # Implemented in Task 3

    def check_available(self) -> bool:
        import shutil
        return shutil.which("agent-browser") is not None
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_browser_engine.py -v`
Expected: All 7 tests PASS

- [ ] **Step 10: Run existing tests to verify no regressions**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/ -v --tb=short`
Expected: All existing tests still PASS

- [ ] **Step 11: Commit**

```bash
git add packages/core/src/shannon_core/services/browser_engine.py \
        packages/core/src/shannon_core/services/engines/__init__.py \
        packages/core/src/shannon_core/services/engines/playwright_engine.py \
        packages/core/src/shannon_core/services/engines/agent_browser_engine.py \
        packages/core/src/shannon_core/models/config.py \
        packages/core/src/shannon_core/config/parser.py \
        packages/core/tests/test_browser_engine.py
git commit -m "feat(core): add BrowserEngine protocol, factory, and config field"
```

---

### Task 2: PlaywrightEngine Full Implementation

**Files:**
- Modify: `packages/core/src/shannon_core/services/engines/playwright_engine.py`
- Modify: `packages/core/src/shannon_core/services/playwright_config_writer.py`

- [ ] **Step 1: Write failing test for PlaywrightEngine commands_reference**

```python
# Append to packages/core/tests/test_browser_engine.py

class TestPlaywrightEngine:
    def test_commands_reference_contains_navigate(self):
        from shannon_core.services.engines.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()
        ref = engine.commands_reference()
        assert "playwright-cli" in ref
        assert "navigate" in ref
        assert "screenshot" in ref
        assert "state-save" in ref
        assert "state-load" in ref
        assert "eval" in ref

    def test_session_flag_format(self):
        from shannon_core.services.engines.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()
        assert engine.session_flag("agent-xss") == "-s=agent-xss"

    def test_auth_restore_block_contains_state_load(self):
        from shannon_core.services.engines.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()
        block = engine.auth_restore_block("-s=agent1", "/tmp/auth.json")
        assert "state-load" in block
        assert "/tmp/auth.json" in block

    def test_auth_save_block_contains_state_save(self):
        from shannon_core.services.engines.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()
        block = engine.auth_save_block("-s=agent1", "/tmp/auth.json")
        assert "state-save" in block
        assert "/tmp/auth.json" in block

    def test_write_config_creates_files(self, tmp_path):
        from shannon_core.services.engines.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()
        result = engine.write_config(str(tmp_path))
        assert result["result"] == "wrote"
        assert (tmp_path / ".playwright" / "cli.config.json").exists()

    def test_write_config_with_session(self, tmp_path):
        from shannon_core.services.engines.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()
        result = engine.write_config(str(tmp_path), session_id="agent-xss")
        assert result["result"] == "wrote"
        assert "agent-xss" in result["configPath"]

    def test_cleanup_config(self, tmp_path):
        from shannon_core.services.engines.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()
        engine.write_config(str(tmp_path), session_id="agent-xss")
        engine.cleanup_config(str(tmp_path), session_id="agent-xss")
        config_path = tmp_path / ".playwright" / "cli.config.agent-xss.json"
        assert not config_path.exists()

    def test_cleanup_all(self, tmp_path):
        from shannon_core.services.engines.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()
        engine.write_config(str(tmp_path))
        assert (tmp_path / ".playwright").exists()
        engine.cleanup_all(str(tmp_path))
        assert not (tmp_path / ".playwright").exists()

    def test_check_available_returns_bool(self):
        from shannon_core.services.engines.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()
        assert isinstance(engine.check_available(), bool)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_browser_engine.py::TestPlaywrightEngine -v`
Expected: FAIL (methods return empty strings/stubs)

- [ ] **Step 3: Implement PlaywrightEngine with full logic**

Replace the stub in `packages/core/src/shannon_core/services/engines/playwright_engine.py`:

```python
"""Playwright browser engine implementation.

Wraps the existing playwright_config_writer logic into the BrowserEngine interface.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from shannon_core.services import playwright_config_writer


class PlaywrightEngine:
    """Playwright-cli browser engine.

    Delegates config writing/cleanup to playwright_config_writer module.
    """

    @property
    def name(self) -> str:
        return "playwright"

    def session_flag(self, session_id: str) -> str:
        return f"-s={session_id}"

    def commands_reference(self) -> str:
        return """\
Browser Automation (playwright-cli):
- Navigate:     playwright-cli {session_flag} navigate <url>
- Click:        playwright-cli {session_flag} click <selector>
- Fill:         playwright-cli {session_flag} fill <selector> <value>
- Type:         playwright-cli {session_flag} type <selector> <value>
- Screenshot:   playwright-cli {session_flag} screenshot --filename <path>
- Get content:  playwright-cli {session_flag} content
- Eval JS:      playwright-cli {session_flag} eval <js-code>
- State save:   playwright-cli {session_flag} state-save <path>
- State load:   playwright-cli {session_flag} state-load <path>

Always pass {session_flag} to every command for session isolation.
Selectors: CSS selectors or XPath."""

    def auth_restore_block(self, session_flag: str, auth_state_file: str) -> str:
        return (
            "The preflight already logged in and saved the authenticated browser\n"
            "session to:\n"
            f"\n  {auth_state_file}\n"
            "\nRestore it before doing anything else:\n"
            "\n"
            f"  playwright-cli {session_flag} state-load {auth_state_file}\n"
            "\nThen run verification (per the success_condition in your authentication\n"
            "config) to confirm the restored session is still valid:\n"
            "\n"
            "- If verification passes → SKIP the login flow below entirely and\n"
            "  proceed with your primary task. You are authenticated.\n"
            "- If verification fails → the saved session is stale. Fall through to\n"
            "  the full login flow below and perform it on your own browser session.\n"
            f"  Do NOT overwrite {auth_state_file}."
        )

    def auth_save_block(self, session_flag: str, auth_state_file: str) -> str:
        return (
            "After verification confirms login_success, save the authenticated browser "
            "session so the rest of the pipeline can reuse it instead of logging in again:\n"
            "\n"
            f"  playwright-cli {session_flag} state-save {auth_state_file}\n"
            "\nRun this only when login_success is true. Skip it on failure."
        )

    def write_config(self, source_dir: str, session_id: str | None = None) -> dict:
        return playwright_config_writer.write_stealth_config(source_dir, session_id=session_id)

    def cleanup_config(self, source_dir: str, session_id: str | None = None) -> None:
        if session_id:
            playwright_config_writer.cleanup_session_config(source_dir, session_id)

    def cleanup_all(self, source_dir: str) -> None:
        playwright_config_writer.cleanup_stealth_config(source_dir)

    def check_available(self) -> bool:
        return shutil.which("playwright-cli") is not None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_browser_engine.py::TestPlaywrightEngine -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Verify existing playwright tests still pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_playwright_config_writer.py -v`
Expected: All existing tests PASS (playwright_config_writer unchanged)

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/services/engines/playwright_engine.py \
        packages/core/tests/test_browser_engine.py
git commit -m "feat(core): implement PlaywrightEngine with full commands and auth blocks"
```

---

### Task 3: AgentBrowserEngine Full Implementation

**Files:**
- Modify: `packages/core/src/shannon_core/services/engines/agent_browser_engine.py`
- Create: `packages/core/tests/test_agent_browser_engine.py`

- [ ] **Step 1: Write failing tests for AgentBrowserEngine**

```python
# packages/core/tests/test_agent_browser_engine.py
import pytest

from shannon_core.services.engines.agent_browser_engine import AgentBrowserEngine


class TestAgentBrowserEngineBasic:
    def test_name(self):
        engine = AgentBrowserEngine()
        assert engine.name == "agent-browser"

    def test_session_flag_format(self):
        engine = AgentBrowserEngine()
        flag = engine.session_flag("agent-xss")
        assert "--session agent-xss" in flag
        assert "--profile .agent-browser/profiles/agent-xss" in flag

    def test_commands_reference_contains_key_commands(self):
        engine = AgentBrowserEngine()
        ref = engine.commands_reference()
        assert "agent-browser" in ref
        assert "open" in ref
        assert "snapshot" in ref
        assert "click @<ref>" in ref
        assert "fill @<ref>" in ref
        assert "screenshot" in ref
        assert "eval" in ref

    def test_commands_reference_mentions_refs(self):
        engine = AgentBrowserEngine()
        ref = engine.commands_reference()
        assert "@e1" in ref or "@ref" in ref

    def test_auth_restore_block_no_state_load(self):
        engine = AgentBrowserEngine()
        block = engine.auth_restore_block("--session agent1 --profile .agent-browser/profiles/agent1", "/tmp/auth.json")
        assert "state-load" not in block
        assert "profile" in block.lower() or "persistent" in block.lower() or "authenticated" in block.lower()

    def test_auth_save_block_no_state_save(self):
        engine = AgentBrowserEngine()
        block = engine.auth_save_block("--session agent1 --profile .agent-browser/profiles/agent1", "/tmp/auth.json")
        assert "state-save" not in block

    def test_check_available_returns_bool(self):
        engine = AgentBrowserEngine()
        assert isinstance(engine.check_available(), bool)


class TestAgentBrowserEngineConfig:
    def test_write_config_creates_profile_dir(self, tmp_path):
        engine = AgentBrowserEngine()
        result = engine.write_config(str(tmp_path), session_id="agent-xss")
        assert result["result"] == "wrote"
        profile_dir = tmp_path / ".agent-browser" / "profiles" / "agent-xss"
        assert profile_dir.exists()

    def test_write_config_skips_existing(self, tmp_path):
        engine = AgentBrowserEngine()
        engine.write_config(str(tmp_path), session_id="agent-xss")
        result = engine.write_config(str(tmp_path), session_id="agent-xss")
        assert result["result"] == "skipped-existing"

    def test_write_config_default_session(self, tmp_path):
        engine = AgentBrowserEngine()
        result = engine.write_config(str(tmp_path))
        assert result["result"] == "wrote"

    def test_cleanup_config(self, tmp_path):
        engine = AgentBrowserEngine()
        engine.write_config(str(tmp_path), session_id="agent-xss")
        engine.cleanup_config(str(tmp_path), session_id="agent-xss")
        profile_dir = tmp_path / ".agent-browser" / "profiles" / "agent-xss"
        assert not profile_dir.exists()

    def test_cleanup_all(self, tmp_path):
        engine = AgentBrowserEngine()
        engine.write_config(str(tmp_path), session_id="agent-xss")
        engine.cleanup_all(str(tmp_path))
        assert not (tmp_path / ".agent-browser").exists()

    def test_cleanup_noop_when_no_dir(self, tmp_path):
        engine = AgentBrowserEngine()
        engine.cleanup_all(str(tmp_path))  # Should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_agent_browser_engine.py -v`
Expected: FAIL (methods return empty strings)

- [ ] **Step 3: Implement AgentBrowserEngine**

Replace the stub in `packages/core/src/shannon_core/services/engines/agent_browser_engine.py`:

```python
"""Agent-browser (Vercel Labs) browser engine implementation.

Uses agent-browser CLI for browser automation with AI-optimized
output (accessibility tree snapshots, @ref selectors).
"""

from __future__ import annotations

import shutil
from pathlib import Path


class AgentBrowserEngine:
    """agent-browser CLI browser engine.

    Uses --session for isolation and --profile for persistent auth state.
    """

    @property
    def name(self) -> str:
        return "agent-browser"

    def session_flag(self, session_id: str) -> str:
        return f"--session {session_id} --profile .agent-browser/profiles/{session_id}"

    def commands_reference(self) -> str:
        return """\
Browser Automation (agent-browser):
- Open/Navigate:  agent-browser {session_flag} open <url>
- Snapshot:       agent-browser {session_flag} snapshot
- Click:          agent-browser {session_flag} click @<ref>
- Fill:           agent-browser {session_flag} fill @<ref> <text>
- Type:           agent-browser {session_flag} type @<ref> <text>
- Screenshot:     agent-browser {session_flag} screenshot [path]
- Get text:       agent-browser {session_flag} get text @<ref>
- Get HTML:       agent-browser {session_flag} get html @<ref>
- Eval JS:        agent-browser {session_flag} eval <js-code>
- Cookies:        agent-browser {session_flag} cookies get
- Back/Forward:   agent-browser {session_flag} back / forward
- Scroll:         agent-browser {session_flag} scroll down [px]

IMPORTANT: Always snapshot first to get element refs (@e1, @e2...),
then interact using those refs. Example:
  1. agent-browser {session_flag} snapshot
  2. agent-browser {session_flag} click @e3
  3. agent-browser {session_flag} fill @e7 "search term"

Always pass {session_flag} to every command for session isolation."""

    def auth_restore_block(self, session_flag: str, auth_state_file: str) -> str:
        return (
            "The preflight already logged in using a persistent browser profile.\n"
            "Your session is already authenticated via the profile — no restore step needed.\n"
            "\n"
            "Verify authentication by navigating to a protected page and confirming\n"
            "you are logged in (check for username, dashboard content, etc.):\n"
            "\n"
            "- If verification passes → SKIP the login flow below entirely and\n"
            "  proceed with your primary task. You are authenticated.\n"
            "- If verification fails → the profile session is stale. Fall through to\n"
            "  the full login flow below and perform it on your own browser session."
        )

    def auth_save_block(self, session_flag: str, auth_state_file: str) -> str:
        return (
            "After verification confirms login_success, the browser profile automatically\n"
            "persists the authentication state. No explicit save command is needed.\n"
            "Other agents will reuse the same profile and be automatically authenticated.\n"
            "\n"
            "Run verification only when login_success is true. Skip it on failure."
        )

    def write_config(self, source_dir: str, session_id: str | None = None) -> dict:
        base_dir = Path(source_dir) / ".agent-browser"
        sid = session_id or "default"
        profile_dir = base_dir / "profiles" / sid
        if profile_dir.exists():
            return {"result": "skipped-existing", "configPath": str(profile_dir)}
        profile_dir.mkdir(parents=True, exist_ok=True)
        return {"result": "wrote", "configPath": str(profile_dir)}

    def cleanup_config(self, source_dir: str, session_id: str | None = None) -> None:
        if not session_id:
            return
        profile_dir = Path(source_dir) / ".agent-browser" / "profiles" / session_id
        if profile_dir.exists():
            shutil.rmtree(profile_dir)

    def cleanup_all(self, source_dir: str) -> None:
        ab_dir = Path(source_dir) / ".agent-browser"
        if ab_dir.exists():
            shutil.rmtree(ab_dir)

    def check_available(self) -> bool:
        return shutil.which("agent-browser") is not None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_agent_browser_engine.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/services/engines/agent_browser_engine.py \
        packages/core/tests/test_agent_browser_engine.py
git commit -m "feat(core): implement AgentBrowserEngine with profile-based auth"
```

---

### Task 4: PromptManager Integration

**Files:**
- Modify: `packages/core/src/shannon_core/prompts/manager.py`
- Modify: `packages/core/tests/test_prompt_manager.py`

The PromptManager currently resolves `{{PLAYWRIGHT_SESSION}}` directly. We replace this with engine-aware variable injection: `{{BROWSER_SESSION_ID}}`, `{{BROWSER_SESSION_FLAG}}`, `{{BROWSER_COMMANDS}}`, `{{BROWSER_AUTH_RESTORE}}`, `{{BROWSER_AUTH_SAVE}}`.

The `load_sync` method gets a new optional `browser_engine_name` parameter (default `"playwright"` for backward compat).

- [ ] **Step 1: Write failing tests for browser variable injection**

```python
# Append to packages/core/tests/test_prompt_manager.py

def test_browser_session_flag_injected(prompts_dir):
    """{{BROWSER_SESSION_FLAG}} is replaced with engine-specific flag."""
    from shannon_core.services.engines import _ensure_registered
    _ensure_registered()
    (prompts_dir / "browser-test.txt").write_text(
        "Flag: {{BROWSER_SESSION_FLAG}}"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync(
        "browser-test",
        {"web_url": "https://example.com", "repo_path": "/r", "browser_session_id": "agent-xss"},
        browser_engine_name="playwright",
    )
    assert "Flag: -s=agent-xss" in result


def test_browser_session_flag_agent_browser(prompts_dir):
    """{{BROWSER_SESSION_FLAG}} uses agent-browser format."""
    from shannon_core.services.engines import _ensure_registered
    _ensure_registered()
    (prompts_dir / "browser-test.txt").write_text(
        "Flag: {{BROWSER_SESSION_FLAG}}"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync(
        "browser-test",
        {"web_url": "https://example.com", "repo_path": "/r", "browser_session_id": "agent-xss"},
        browser_engine_name="agent-browser",
    )
    assert "--session agent-xss" in result


def test_browser_commands_injected(prompts_dir):
    """{{BROWSER_COMMANDS}} is replaced with engine command reference."""
    from shannon_core.services.engines import _ensure_registered
    _ensure_registered()
    (prompts_dir / "browser-cmd.txt").write_text(
        "Commands:\n{{BROWSER_COMMANDS}}"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync(
        "browser-cmd",
        {"web_url": "https://example.com", "repo_path": "/r", "browser_session_id": "agent1"},
        browser_engine_name="playwright",
    )
    assert "navigate" in result
    assert "screenshot" in result


def test_browser_session_id_injected(prompts_dir):
    """{{BROWSER_SESSION_ID}} is replaced with the raw session ID."""
    (prompts_dir / "sid-test.txt").write_text(
        "Session: {{BROWSER_SESSION_ID}}"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync(
        "sid-test",
        {"web_url": "https://example.com", "repo_path": "/r", "browser_session_id": "agent-injection"},
        browser_engine_name="playwright",
    )
    assert "Session: agent-injection" in result


def test_browser_defaults_to_playwright(prompts_dir):
    """When browser_engine_name not specified, defaults to playwright."""
    from shannon_core.services.engines import _ensure_registered
    _ensure_registered()
    (prompts_dir / "default-eng.txt").write_text(
        "Flag: {{BROWSER_SESSION_FLAG}}"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync(
        "default-eng",
        {"web_url": "", "repo_path": "/r", "browser_session_id": "agent1"},
    )
    assert "-s=agent1" in result


def test_browser_default_session_is_agent1(prompts_dir):
    """When browser_session_id not in variables, falls back to template_name mapping."""
    (prompts_dir / "recon.txt").write_text("Flag: {{BROWSER_SESSION_FLAG}}")
    manager = PromptManager(prompts_dir)
    result = manager.load_sync(
        "recon",
        {"web_url": "https://example.com", "repo_path": "/r"},
        browser_engine_name="playwright",
    )
    # recon maps to agent2 in PLAYWRIGHT_SESSION_MAPPING
    assert "-s=agent" in result


def test_browser_auth_restore_injected(prompts_dir):
    """{{BROWSER_AUTH_RESTORE}} is replaced with engine-specific restore block."""
    from shannon_core.services.engines import _ensure_registered
    _ensure_registered()
    (prompts_dir / "auth-restore.txt").write_text(
        "{{BROWSER_AUTH_RESTORE}}"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync(
        "auth-restore",
        {"web_url": "https://example.com", "repo_path": "/r", "browser_session_id": "agent1", "auth_state_file": "/tmp/auth.json"},
        browser_engine_name="playwright",
    )
    assert "state-load" in result
    assert "/tmp/auth.json" in result


def test_browser_auth_save_injected(prompts_dir):
    """{{BROWSER_AUTH_SAVE}} is replaced with engine-specific save block."""
    from shannon_core.services.engines import _ensure_registered
    _ensure_registered()
    (prompts_dir / "auth-save.txt").write_text(
        "{{BROWSER_AUTH_SAVE}}"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync(
        "auth-save",
        {"web_url": "https://example.com", "repo_path": "/r", "browser_session_id": "agent1", "auth_state_file": "/tmp/auth.json"},
        browser_engine_name="playwright",
    )
    assert "state-save" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_prompt_manager.py::test_browser_session_flag_injected -v`
Expected: FAIL (load_sync doesn't accept browser_engine_name parameter)

- [ ] **Step 3: Update PromptManager.load_sync signature and _interpolate**

Modify `packages/core/src/shannon_core/prompts/manager.py`:

**Update `load_sync` signature** — add `browser_engine_name: str = "playwright"` parameter:

```python
def load_sync(
    self,
    template_name: str,
    variables: dict[str, str],
    config: DistributedConfig | None = None,
    pipeline_testing: bool = False,
    browser_engine_name: str = "playwright",   # NEW
) -> str:
```

**In `load_sync` body**, before calling `self._interpolate`, inject browser engine variables:

```python
    # Inject browser engine variables
    from shannon_core.services.browser_engine import BrowserEngineFactory
    from shannon_core.services.engines import _ensure_registered
    _ensure_registered()
    engine = BrowserEngineFactory.get_engine(browser_engine_name)
    session_id = variables.get("browser_session_id") or PLAYWRIGHT_SESSION_MAPPING.get(template_name, "agent1")
    variables["browser_session_id"] = session_id
    variables["browser_session_flag"] = engine.session_flag(session_id)
    variables["browser_commands"] = engine.commands_reference()
    variables["browser_auth_restore"] = engine.auth_restore_block(
        engine.session_flag(session_id),
        variables.get("auth_state_file", ""),
    )
    variables["browser_auth_save"] = engine.auth_save_block(
        engine.session_flag(session_id),
        variables.get("auth_state_file", ""),
    )

    template = self._interpolate(template, variables, config, template_name)
```

**In `_interpolate`**, replace the existing `{{PLAYWRIGHT_SESSION}}` handling. Remove these two lines:

```python
# REMOVE THESE:
playwright_session = variables.get("playwright_session") or PLAYWRIGHT_SESSION_MAPPING.get(template_name, "agent1")
result = result.replace("{{PLAYWRIGHT_SESSION}}", playwright_session)
```

The generic variable loop at the bottom of `_interpolate` already handles `{{BROWSER_SESSION_ID}}`, `{{BROWSER_SESSION_FLAG}}`, `{{BROWSER_COMMANDS}}`, `{{BROWSER_AUTH_RESTORE}}`, `{{BROWSER_AUTH_SAVE}}` — it converts any key in `variables` to `{{KEY}}` and replaces.

- [ ] **Step 4: Run the new browser injection tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_prompt_manager.py -k "browser_" -v`
Expected: All 8 new tests PASS

- [ ] **Step 5: Run ALL prompt manager tests to check backward compat**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_prompt_manager.py -v`
Expected: All tests PASS (existing tests use default `browser_engine_name="playwright"`, backward compat maintained)

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/prompts/manager.py \
        packages/core/tests/test_prompt_manager.py
git commit -m "feat(core): add browser engine variable injection to PromptManager"
```

---

### Task 5: Update ExploitExecutor

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`

- [ ] **Step 1: Update ExploitExecutor to use browser_session_id**

The change is minimal: rename `playwright_session` to `browser_session_id` in prompt_variables.

Current code at `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py:33`:
```python
prompt_variables["playwright_session"] = get_session_id(agent_name.value)
```

Replace with:
```python
prompt_variables["browser_session_id"] = get_session_id(agent_name.value)
```

The full file becomes:

```python
from pathlib import Path

from shannon_core.models.agents import AgentName
from shannon_core.models.metrics import AgentMetrics
from shannon_core.utils.file_io import async_path_exists, async_read_file

from shannon_core.agents.executor import AgentExecutor
from shannon_core.services.playwright_config_writer import get_session_id


class ExploitExecutor:
    def __init__(self, agent_executor: AgentExecutor):
        self._executor = agent_executor

    async def execute(
        self,
        agent_name: AgentName,
        vuln_type: str,
        workspace_path: Path,
        deliverables_path: Path,
        web_url: str,
        config_path: str | None = None,
        api_key: str | None = None,
        pipeline_testing: bool = False,
    ) -> AgentMetrics:
        queue_path = deliverables_path / f"{vuln_type}_exploitation_queue.json"
        prompt_variables: dict[str, str] = {}
        if await async_path_exists(queue_path):
            content = await async_read_file(queue_path)
            prompt_variables["vulnerability_entries"] = content

        # Pass session ID so the agent uses its isolated browser session
        prompt_variables["browser_session_id"] = get_session_id(agent_name.value)

        return await self._executor.execute(
            agent_name=agent_name,
            repo_path=str(deliverables_path),
            web_url=web_url,
            deliverables_path=str(deliverables_path),
            config_path=config_path,
            api_key=api_key,
            pipeline_testing=pipeline_testing,
            prompt_variables=prompt_variables,
        )
```

- [ ] **Step 2: Run blackbox tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py
git commit -m "refactor(blackbox): rename playwright_session to browser_session_id in ExploitExecutor"
```

---

### Task 6: Update Workflow Integration

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`

This is the most delicate change — workflow files use `write_stealth_config`, `cleanup_stealth_config`, `get_session_id`, `cleanup_session_config`, and `AGENT_SESSION_MAPPING`.

The engine is resolved once at pipeline start and used throughout.

- [ ] **Step 1: Update blackbox workflow**

In `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`:

**Replace the playwright imports** (lines 22-27):
```python
    # OLD:
    from shannon_core.services.playwright_config_writer import (
        write_stealth_config,
        cleanup_stealth_config,
        get_session_id,
        AGENT_SESSION_MAPPING,
    )

    # NEW:
    from shannon_core.services.browser_engine import BrowserEngineFactory
    from shannon_core.services.engines import _ensure_registered
    from shannon_core.services.playwright_config_writer import (
        get_session_id,
        AGENT_SESSION_MAPPING,
    )
    _ensure_registered()
```

**Update write_stealth_config calls** — resolve engine from config:

After `act_input` construction (around line 63), add engine resolution:
```python
        # Resolve browser engine from config
        engine_name = "playwright"  # default
        if input.config_path:
            from shannon_core.config.parser import parse_config
            try:
                cfg = parse_config(input.config_path)
                engine_name = cfg.browser_engine
            except Exception:
                pass
        engine = BrowserEngineFactory.get_engine(engine_name)
```

Note: this engine resolution duplicates code with the existing `parse_config` call on line 77-78. Refactor to use the same `cfg` object:

```python
        # Parse config once for both code path rules and browser engine
        parsed_config = None
        if input.config_path:
            from shannon_core.config.parser import parse_config
            parsed_config = parse_config(input.config_path)

        # Write code path deny rules (S6)
        if parsed_config and parsed_config.rules and parsed_config.rules.avoid:
            sync_code_path_deny_rules(parsed_config.rules.avoid)

        # Resolve browser engine
        engine_name = parsed_config.browser_engine if parsed_config else "playwright"
        engine = BrowserEngineFactory.get_engine(engine_name)

        # Write browser config (S5) — only if repo path provided
        if input.repo_path:
            engine.write_config(input.repo_path)
```

**Replace all `write_stealth_config(...)` calls** with `engine.write_config(...)`:
- Line 83: `engine.write_config(input.repo_path)`
- Line 161: `engine.write_config(input.repo_path, session_id=session_id)`

**Replace cleanup in finally block** (lines 274-279):
```python
            # Clean up session-specific configs
            for session_id in set(AGENT_SESSION_MAPPING.values()):
                engine.cleanup_config(input.repo_path, session_id)
            engine.cleanup_all(input.repo_path)
            cleanup_auth_state_sync(act_input.workspace_path or input.repo_path)
```

**Remove the `cleanup_session_config` import** that was on line 277.

- [ ] **Step 2: Run blackbox tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 3: Update whitebox workflow**

In `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`:

**Replace the playwright imports** (line 17):
```python
    # OLD:
    from shannon_core.services.playwright_config_writer import write_stealth_config, cleanup_stealth_config

    # NEW:
    from shannon_core.services.browser_engine import BrowserEngineFactory
    from shannon_core.services.engines import _ensure_registered
    _ensure_registered()
```

**Add engine resolution** after `act_input` construction (around line 50). The whitebox workflow also has a `parse_config` call at line 81-83. Consolidate:

```python
        # Parse config once
        parsed_config = None
        if input.config_path:
            from shannon_core.config.parser import parse_config
            parsed_config = parse_config(input.config_path)

        # Write code path deny rules (S6)
        if parsed_config and parsed_config.rules and parsed_config.rules.avoid:
            sync_code_path_deny_rules(parsed_config.rules.avoid)

        # Resolve browser engine and write config (S5)
        engine_name = parsed_config.browser_engine if parsed_config else "playwright"
        engine = BrowserEngineFactory.get_engine(engine_name)
        engine.write_config(input.repo_path)
```

Remove the old `parse_config` + `write_stealth_config` calls at lines 80-87.

**Replace cleanup in finally block** (line 174):
```python
    # OLD:
    cleanup_stealth_config(input.repo_path)

    # NEW:
    engine.cleanup_all(input.repo_path)
```

- [ ] **Step 4: Run whitebox tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/workflows.py \
        packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
git commit -m "refactor: update workflows to use BrowserEngine interface"
```

---

### Task 7: Update services __init__.py

**Files:**
- Modify: `packages/core/src/shannon_core/services/__init__.py`

- [ ] **Step 1: Add browser engine exports**

Append to `packages/core/src/shannon_core/services/__init__.py`:

```python
from shannon_core.services.browser_engine import BrowserEngine, BrowserEngineFactory
```

Also ensure engines are registered on import:

```python
from shannon_core.services.engines import _ensure_registered
```

- [ ] **Step 2: Run full test suite**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add packages/core/src/shannon_core/services/__init__.py
git commit -m "feat(core): export BrowserEngine symbols from services package"
```

---

### Task 8: Update Prompt Templates (24 files)

**Files:**
- Modify: `prompts/shared/_shared-session.txt`
- Modify: `prompts/validate-authentication.txt`
- Modify: 10 vuln/exploit prompt files
- Modify: 8 pipeline-testing prompt files
- Modify: `prompts/recon.txt`, `prompts/recon-blackbox.txt`

All templates change from hardcoded `playwright-cli` references to engine-agnostic `{{BROWSER_*}}` placeholders. The PromptManager injects the correct engine-specific content at render time.

**Variable mapping:**

| Old | New |
|-----|-----|
| `playwright-cli -s={{PLAYWRIGHT_SESSION}}` (in commands) | `{{BROWSER_COMMANDS}}` |
| `{{PLAYWRIGHT_SESSION}}` (as display value) | `{{BROWSER_SESSION_ID}}` |
| `playwright-cli -s={{PLAYWRIGHT_SESSION}} state-load {{AUTH_STATE_FILE}}` | `{{BROWSER_AUTH_RESTORE}}` |
| `playwright-cli -s={{PLAYWRIGHT_SESSION}} state-save {{AUTH_STATE_FILE}}` | `{{BROWSER_AUTH_SAVE}}` |

- [ ] **Step 1: Update `prompts/shared/_shared-session.txt`**

Replace entire file content:

```
<shared_authenticated_session>
{{BROWSER_AUTH_RESTORE}}
</shared_authenticated_session>
```

- [ ] **Step 2: Update `prompts/validate-authentication.txt`**

The file has two browser-related sections: `<cli_tools>` and `<publish_session>`.

Replace the browser tool line (line 14):
```
- **Browser Automation (playwright-cli skill):** Invoke the `playwright-cli` skill to learn available commands. Always pass `-s={{PLAYWRIGHT_SESSION}}` to every command for session isolation.
```
With:
```
- **Browser Automation:**
{{BROWSER_COMMANDS}}
```

Replace `<publish_session>` block (lines 22-27):
```
<publish_session>
After verification confirms login_success, save the authenticated browser session so the rest of the pipeline can reuse it instead of logging in again:

  playwright-cli -s={{PLAYWRIGHT_SESSION}} state-save {{AUTH_STATE_FILE}}

Run this only when login_success is true. Skip it on failure.
</publish_session>
```
With:
```
<publish_session>
{{BROWSER_AUTH_SAVE}}
</publish_session>
```

- [ ] **Step 3: Update all vuln prompt files** (6 files)

For each of these files, the pattern is identical — replace the `playwright-cli` tool reference line:

Files:
- `prompts/vuln-xss.txt` (line 87)
- `prompts/vuln-injection.txt` (line 94)
- `prompts/vuln-auth.txt` (line 90)
- `prompts/vuln-authz.txt` (line 94)
- `prompts/vuln-ssrf.txt` (line 90)

In each file, find the line matching:
```
- **Browser Automation (playwright-cli skill):** ... Invoke the `playwright-cli` skill ... `-s={{PLAYWRIGHT_SESSION}}` ...
```

Replace with:
```
- **Browser Automation:**
{{BROWSER_COMMANDS}}
```

- [ ] **Step 4: Update all exploit prompt files** (5 files)

Files:
- `prompts/injection-exploit.txt` (line 121)
- `prompts/xss-exploit.txt` (line 138)
- `prompts/authz-exploit.txt` (line 136)
- `prompts/ssrf-exploit.txt` (line 142)
- `prompts/auth-exploit.txt` (contains `{{PLAYWRIGHT_SESSION}}` on line 265)

Same pattern as Step 3 — replace the `playwright-cli` tool reference with `{{BROWSER_COMMANDS}}`.

Additionally, in `prompts/xss-exploit.txt` (line 339), `prompts/auth-exploit.txt` (line 265), and `prompts/ssrf-exploit.txt` (line 406), replace standalone `{{PLAYWRIGHT_SESSION}}` with `{{BROWSER_SESSION_ID}}`.

- [ ] **Step 5: Update `prompts/recon.txt`**

Two references (lines 76 and 151):

Line 76 — replace:
```
- **Browser Automation (playwright-cli skill):** For all browser interactions, invoke the `playwright-cli` skill to learn available commands. Always pass `-s={{PLAYWRIGHT_SESSION}}` to every command for session isolation.
```
With:
```
- **Browser Automation:**
{{BROWSER_COMMANDS}}
```

Line 151 — replace:
```
    - Invoke the `playwright-cli` skill, then use it with `-s={{PLAYWRIGHT_SESSION}}` to navigate to the target.
```
With:
```
    - Use the browser tool with your session flag to navigate to the target.
```

- [ ] **Step 6: Update `prompts/recon-blackbox.txt`**

Line 101 — replace:
```
- Use browser session {{PLAYWRIGHT_SESSION}} for all automated interactions
```
With:
```
- Use browser session {{BROWSER_SESSION_ID}} for all automated interactions
```

- [ ] **Step 7: Update pipeline-testing vuln templates** (5 files)

Files:
- `prompts/pipeline-testing/vuln-xss.txt` (lines 6-8)
- `prompts/pipeline-testing/vuln-injection.txt` (lines 6-8)
- `prompts/pipeline-testing/vuln-auth.txt` (lines 6-8)
- `prompts/pipeline-testing/vuln-authz.txt` (lines 6-8)
- `prompts/pipeline-testing/vuln-ssrf.txt` (lines 6-8)

Each has the same pattern (lines 6-8):
```
   - Invoke the `playwright-cli` skill to learn the available commands
   - Use `playwright-cli -s={{PLAYWRIGHT_SESSION}}` to navigate to https://example.com
   - Use `playwright-cli -s={{PLAYWRIGHT_SESSION}}` to take a screenshot
```

Replace with:
```
   - Use the browser tool to navigate to https://example.com
   - Use the browser tool to take a screenshot
```

The `{{BROWSER_COMMANDS}}` block is automatically injected by the PromptManager — no need to repeat it here since pipeline-testing templates are minimal.

- [ ] **Step 8: Update pipeline-testing exploit templates** (5 files)

Files:
- `prompts/pipeline-testing/exploit-injection.txt`
- `prompts/pipeline-testing/exploit-xss.txt`
- `prompts/pipeline-testing/exploit-auth.txt`
- `prompts/pipeline-testing/exploit-authz.txt`
- `prompts/pipeline-testing/exploit-ssrf.txt`

Each follows the same pattern. For each file:

Line 5 — replace:
```
**Playwright Session:** Using session `{{PLAYWRIGHT_SESSION}}` for browser automation testing.
```
With:
```
**Browser Session:** Using session `{{BROWSER_SESSION_ID}}` for browser automation testing.
```

Lines 8-15 — replace:
```
1. Invoke the `playwright-cli` skill to learn the available commands, then navigate to the test site using your assigned session:

   playwright-cli -s={{PLAYWRIGHT_SESSION}} navigate https://example.com
   ...
   playwright-cli -s={{PLAYWRIGHT_SESSION}} screenshot --filename "..."
```
With:
```
1. Use the browser tool to navigate to the test site and take a screenshot.
```

Lines 20, 26, 32 — replace all `{{PLAYWRIGHT_SESSION}}` with `{{BROWSER_SESSION_ID}}`.

- [ ] **Step 9: Verify no remaining playwright references in templates**

Run: `grep -rn "playwright-cli\|PLAYWRIGHT_SESSION" prompts/ --include="*.txt"`
Expected: No output (zero matches)

- [ ] **Step 10: Run prompt manager tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_prompt_manager.py -v`
Expected: All tests PASS

- [ ] **Step 11: Commit**

```bash
git add prompts/
git commit -m "refactor(prompts): replace playwright-cli references with engine-agnostic placeholders"
```

---

### Task 9: Update test_prompt_manager.py Shared Session Tests

**Files:**
- Modify: `packages/core/tests/test_prompt_manager.py`

The shared session tests reference `playwright-cli state-load` in their test fixtures. These need updating to use the new `{{BROWSER_AUTH_RESTORE}}` placeholder.

- [ ] **Step 1: Update test_shared_session_include_resolves** (line 272-295)

Change the session_partial fixture:
```python
    # OLD:
    session_partial = (
        "<shared_authenticated_session>\n"
        "The preflight already logged in.\n"
        "Restore session: playwright-cli state-load {{AUTH_STATE_FILE}}\n"
        "</shared_authenticated_session>\n"
    )

    # NEW:
    session_partial = (
        "<shared_authenticated_session>\n"
        "{{BROWSER_AUTH_RESTORE}}\n"
        "</shared_authenticated_session>\n"
    )
```

Update the assertion — check for auth_state_file content being present (which comes from BROWSER_AUTH_RESTORE):
```python
    assert "shared_authenticated_session" in result
    assert "/tmp/auth-state.json" in result
```

- [ ] **Step 2: Update test_shared_session_include_removed_without_auth** (line 298-316)

Same change to session_partial:
```python
    session_partial = (
        "<shared_authenticated_session>\n"
        "{{BROWSER_AUTH_RESTORE}}\n"
        "</shared_authenticated_session>\n"
    )
```

- [ ] **Step 3: Run all prompt manager tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_prompt_manager.py -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add packages/core/tests/test_prompt_manager.py
git commit -m "test(core): update shared session tests for engine-agnostic placeholders"
```

---

### Task 10: Update .env.example + Final Integration

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add SHANNON_BROWSER_ENGINE to .env.example**

After the Temporal section, add:

```
# =============================================================================
# Browser Engine Configuration
# =============================================================================
# Choose browser engine: playwright (default) or agent-browser
# agent-browser requires: npm install -g agent-browser && agent-browser install
# SHANNON_BROWSER_ENGINE=playwright
```

- [ ] **Step 2: Run full test suite**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/ packages/blackbox/tests/ packages/whitebox/tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs: add SHANNON_BROWSER_ENGINE to .env.example"
```
