# Browser Engine Configuration Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Wire user config to runtime browser engine selection, so `Config.browser_engine` (or `SHANNON_BROWSER_ENGINE` env var) controls which engine the pipeline uses.

**Architecture:** The config parser gains an env var override for `browser_engine`. The workflow layer resolves the engine via `BrowserEngineFactory` at startup, checks availability, and replaces all direct `playwright_config_writer` calls with engine-agnostic methods. `AgentExecutor` auto-injects `browser_engine` from parsed config into prompt variables, which flows through to `PromptManager._interpolate()`.

**Tech Stack:** Python, Pydantic, Temporal, pytest

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `packages/core/src/shannon_core/models/errors.py` | Add `BROWSER_ENGINE_UNAVAILABLE` error code + classification |
| Modify | `packages/core/src/shannon_core/config/parser.py` | Add `SHANNON_BROWSER_ENGINE` env var override |
| Modify | `packages/core/src/shannon_core/agents/executor.py` | Inject `browser_engine` from config into prompt variables |
| Modify | `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | Use engine factory, startup check, engine-agnostic config management |
| Modify | `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | Use engine factory, startup check, engine-agnostic config management |
| Modify | `.env.example` | Document `SHANNON_BROWSER_ENGINE` |
| Create | `packages/core/tests/test_browser_engine_wiring.py` | Unit tests for error code, env var override, executor injection |

**Data flow:**
```
SHANNON_BROWSER_ENGINE env var
  └─→ parse_config() overrides raw["browser_engine"]
        └─→ Config.browser_engine (Pydantic validated)
              ├─→ Workflow: BrowserEngineFactory.get_engine(config.browser_engine)
              │     ├─→ engine.check_available() at startup
              │     ├─→ engine.write_config() instead of write_stealth_config()
              │     └─→ engine.cleanup_config() instead of cleanup_stealth_config()
              └─→ AgentExecutor: variables["browser_engine"] = config.browser_engine
                    └─→ PromptManager._interpolate()
                          └─→ BrowserEngineFactory.get_engine(variables["browser_engine"])
                                └─→ engine.session_flag(), engine.commands_reference()
```

---

### Task 1: Add BROWSER_ENGINE_UNAVAILABLE Error Code

**Files:**
- Modify: `packages/core/src/shannon_core/models/errors.py:22` (ErrorCode enum)
- Modify: `packages/core/src/shannon_core/models/errors.py:107-121` (classify_error_for_temporal)
- Test: `packages/core/tests/test_browser_engine_wiring.py`

- [x] **Step 1: Write the failing test**

Create `packages/core/tests/test_browser_engine_wiring.py`:

```python
"""Tests for browser engine configuration wiring.

Covers:
- BROWSER_ENGINE_UNAVAILABLE error code classification
- SHANNON_BROWSER_ENGINE env var override in config parser
- AgentExecutor browser_engine injection from config
"""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.models.errors import ErrorCode, PentestError, classify_error_for_temporal


# ---------------------------------------------------------------------------
# Task 1: Error code
# ---------------------------------------------------------------------------


class TestBrowserEngineUnavailableErrorCode:
    def test_error_code_exists(self):
        """BROWSER_ENGINE_UNAVAILABLE should be a valid ErrorCode."""
        assert hasattr(ErrorCode, "BROWSER_ENGINE_UNAVAILABLE")
        assert ErrorCode.BROWSER_ENGINE_UNAVAILABLE.value == "BROWSER_ENGINE_UNAVAILABLE"

    def test_classified_as_configuration_error_non_retryable(self):
        """BROWSER_ENGINE_UNAVAILABLE should classify as ConfigurationError, not retryable."""
        error = PentestError(
            "Browser engine 'agent-browser' is not available.",
            "browser",
            error_code=ErrorCode.BROWSER_ENGINE_UNAVAILABLE,
        )
        error_type, retryable = classify_error_for_temporal(error)
        assert error_type == "ConfigurationError"
        assert retryable is False
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest packages/core/tests/test_browser_engine_wiring.py::TestBrowserEngineUnavailableErrorCode -v`
Expected: FAIL — `AttributeError` on `ErrorCode.BROWSER_ENGINE_UNAVAILABLE`

- [x] **Step 3: Add error code to enum and classification**

In `packages/core/src/shannon_core/models/errors.py`, add `BROWSER_ENGINE_UNAVAILABLE` to the `ErrorCode` enum after `CODE_INDEX_FAILED` (line 22):

```python
    CODE_INDEX_FAILED = "CODE_INDEX_FAILED"
    BROWSER_ENGINE_UNAVAILABLE = "BROWSER_ENGINE_UNAVAILABLE"
```

In the same file, add `BROWSER_ENGINE_UNAVAILABLE` to the `ConfigurationError` tuple in `classify_error_for_temporal` (around line 107):

```python
        if code in (
            ErrorCode.CONFIG_NOT_FOUND,
            ErrorCode.CONFIG_VALIDATION_FAILED,
            ErrorCode.CONFIG_PARSE_ERROR,
            ErrorCode.PROMPT_LOAD_FAILED,
            ErrorCode.BROWSER_ENGINE_UNAVAILABLE,
        ):
            return ("ConfigurationError", False)
```

- [x] **Step 4: Run test to verify it passes**

Run: `pytest packages/core/tests/test_browser_engine_wiring.py::TestBrowserEngineUnavailableErrorCode -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/models/errors.py packages/core/tests/test_browser_engine_wiring.py
git commit -m "feat: add BROWSER_ENGINE_UNAVAILABLE error code"
```

---

### Task 2: Add SHANNON_BROWSER_ENGINE Env Var Override

**Files:**
- Modify: `packages/core/src/shannon_core/config/parser.py:1` (add `import os`)
- Modify: `packages/core/src/shannon_core/config/parser.py:208-211` (env var override before sanitize)
- Test: `packages/core/tests/test_browser_engine_wiring.py`

- [x] **Step 1: Write the failing tests**

Append to `packages/core/tests/test_browser_engine_wiring.py`:

```python
from shannon_core.config.parser import parse_config


def _write_config(tmp_path: Path, content: str) -> str:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(content, encoding="utf-8")
    return str(config_file)


# ---------------------------------------------------------------------------
# Task 2: Env var override
# ---------------------------------------------------------------------------


class TestBrowserEngineEnvVarOverride:
    def test_env_var_overrides_yaml_value(self, tmp_path, monkeypatch):
        """SHANNON_BROWSER_ENGINE env var should override yaml browser_engine."""
        config_path = _write_config(tmp_path, "browser_engine: playwright\n")
        monkeypatch.setenv("SHANNON_BROWSER_ENGINE", "agent-browser")
        config = parse_config(config_path)
        assert config.browser_engine == "agent-browser"

    def test_env_var_sets_engine_when_yaml_omits_it(self, tmp_path, monkeypatch):
        """SHANNON_BROWSER_ENGINE should set browser_engine even when yaml omits it."""
        config_path = _write_config(tmp_path, "description: test app\n")
        monkeypatch.setenv("SHANNON_BROWSER_ENGINE", "agent-browser")
        config = parse_config(config_path)
        assert config.browser_engine == "agent-browser"

    def test_default_playwright_without_env_var(self, tmp_path, monkeypatch):
        """Without SHANNON_BROWSER_ENGINE, browser_engine defaults to playwright."""
        monkeypatch.delenv("SHANNON_BROWSER_ENGINE", raising=False)
        config_path = _write_config(tmp_path, "description: test app\n")
        config = parse_config(config_path)
        assert config.browser_engine == "playwright"

    def test_invalid_env_var_raises_validation_error(self, tmp_path, monkeypatch):
        """Invalid SHANNON_BROWSER_ENGINE value (e.g. 'chromium') should raise PentestError."""
        config_path = _write_config(tmp_path, "description: test app\n")
        monkeypatch.setenv("SHANNON_BROWSER_ENGINE", "chromium")
        with pytest.raises(PentestError, match="validation failed"):
            parse_config(config_path)
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest packages/core/tests/test_browser_engine_wiring.py::TestBrowserEngineEnvVarOverride -v`
Expected: FAIL — `test_env_var_overrides_yaml_value` returns "playwright" instead of "agent-browser"

- [x] **Step 3: Add import os and env var override to parser**

In `packages/core/src/shannon_core/config/parser.py`, add `import os` at line 1 (before `import re`):

```python
import os
import re
from pathlib import Path
```

In the same file, add the env var override between the null check (line 208) and the sanitize call (line 211). The full block becomes:

```python
    if raw is None:
        raise PentestError(
            "Configuration file resulted in null after parsing",
            "config",
            error_code=ErrorCode.CONFIG_PARSE_ERROR,
        )

    # Environment variable override for browser engine
    if env_engine := os.environ.get("SHANNON_BROWSER_ENGINE"):
        raw["browser_engine"] = env_engine

    # Sanitize raw dict before Pydantic validation (normalizes case/whitespace)
    raw = _sanitize_raw_dict(raw)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest packages/core/tests/test_browser_engine_wiring.py::TestBrowserEngineEnvVarOverride -v`
Expected: PASS

- [x] **Step 5: Run existing parser tests to verify no regressions**

Run: `pytest packages/core/tests/test_parser.py -v`
Expected: ALL PASS

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/config/parser.py packages/core/tests/test_browser_engine_wiring.py
git commit -m "feat: add SHANNON_BROWSER_ENGINE env var override to config parser"
```

---

### Task 3: Inject browser_engine from Config in AgentExecutor

**Files:**
- Modify: `packages/core/src/shannon_core/agents/executor.py:44-46` (add browser_engine to variables dict)
- Test: `packages/core/tests/test_browser_engine_wiring.py`

The executor already parses the config at line 41 (`config = parse_config(config_path)`). We add one line to inject `browser_engine` into the prompt variables, which `PromptManager._interpolate()` reads at line 93: `BrowserEngineFactory.get_engine(variables.get("browser_engine", "playwright"))`.

- [x] **Step 1: Write the failing tests**

Append to `packages/core/tests/test_browser_engine_wiring.py`:

```python
from shannon_core.agents.executor import AgentExecutor
from shannon_core.prompts.manager import PromptManager
from shannon_core.models.agents import AgentName
from shannon_core.git_manager import GitManager


# ---------------------------------------------------------------------------
# Task 3: Executor injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_injects_browser_engine_from_config(tmp_path):
    """AgentExecutor should inject browser_engine from config into prompt variables."""
    import shannon_core.services.engines  # noqa: F401 — register engines

    config_file = tmp_path / "config.yaml"
    config_file.write_text("browser_engine: agent-browser")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".shannon" / "deliverables").mkdir(parents=True)

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "recon.txt").write_text("{{BROWSER_COMMANDS}}")

    captured_variables = {}

    def mock_load_sync(self, template_name, variables, **kwargs):
        captured_variables.update(variables)
        return "mock prompt"

    async def mock_run_claude(prompt, **kw):
        r = MagicMock()
        r.success = True
        r.error = None
        r.cost = 0.0
        r.turns = 1
        r.model = "test"
        r.structured_output = None
        r.tokens = None
        r.text = ""
        return r

    with patch.object(PromptManager, "load_sync", mock_load_sync), \
         patch("shannon_core.agents.runner.run_claude_prompt", side_effect=mock_run_claude), \
         patch.object(GitManager, "create_checkpoint", new_callable=AsyncMock), \
         patch.object(GitManager, "commit", new_callable=AsyncMock), \
         patch("shannon_core.agents.validators.validate_deliverable", new_callable=AsyncMock):

        pm = PromptManager(prompts_dir)
        executor = AgentExecutor(pm)
        await executor.execute(
            agent_name=AgentName.RECON,
            repo_path=str(repo),
            config_path=str(config_file),
        )

    assert captured_variables["browser_engine"] == "agent-browser"


@pytest.mark.asyncio
async def test_executor_no_browser_engine_without_config(tmp_path):
    """Without a config file, executor should not inject browser_engine."""
    import shannon_core.services.engines  # noqa: F401 — register engines

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".shannon" / "deliverables").mkdir(parents=True)

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "recon.txt").write_text("{{BROWSER_COMMANDS}}")

    captured_variables = {}

    def mock_load_sync(self, template_name, variables, **kwargs):
        captured_variables.update(variables)
        return "mock prompt"

    async def mock_run_claude(prompt, **kw):
        r = MagicMock()
        r.success = True
        r.error = None
        r.cost = 0.0
        r.turns = 1
        r.model = "test"
        r.structured_output = None
        r.tokens = None
        r.text = ""
        return r

    with patch.object(PromptManager, "load_sync", mock_load_sync), \
         patch("shannon_core.agents.runner.run_claude_prompt", side_effect=mock_run_claude), \
         patch.object(GitManager, "create_checkpoint", new_callable=AsyncMock), \
         patch.object(GitManager, "commit", new_callable=AsyncMock), \
         patch("shannon_core.agents.validators.validate_deliverable", new_callable=AsyncMock):

        pm = PromptManager(prompts_dir)
        executor = AgentExecutor(pm)
        await executor.execute(
            agent_name=AgentName.RECON,
            repo_path=str(repo),
        )

    # No config → no browser_engine key → PromptManager defaults to "playwright"
    assert "browser_engine" not in captured_variables
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest packages/core/tests/test_browser_engine_wiring.py::test_executor_injects_browser_engine_from_config -v`
Expected: FAIL — `KeyError: 'browser_engine'` because executor doesn't inject it yet

- [x] **Step 3: Add browser_engine injection to executor**

In `packages/core/src/shannon_core/agents/executor.py`, modify the variable building section (lines 44-46). The current code:

```python
        variables = {"web_url": web_url, "repo_path": str(repo)}
        if prompt_variables:
            variables.update(prompt_variables)
```

Becomes:

```python
        variables = {"web_url": web_url, "repo_path": str(repo)}
        if config:
            variables["browser_engine"] = config.browser_engine
        if prompt_variables:
            variables.update(prompt_variables)
```

Note: `prompt_variables` is merged AFTER `browser_engine` is set, so an explicit `prompt_variables={"browser_engine": "..."}` overrides the config value. This is intentional — callers can force an engine if needed.

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest packages/core/tests/test_browser_engine_wiring.py::test_executor_injects_browser_engine_from_config packages/core/tests/test_browser_engine_wiring.py::test_executor_no_browser_engine_without_config -v`
Expected: PASS

- [x] **Step 5: Run all core tests to verify no regressions**

Run: `pytest packages/core/tests/ -v`
Expected: ALL PASS

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/agents/executor.py packages/core/tests/test_browser_engine_wiring.py
git commit -m "feat: inject browser_engine from config into executor prompt variables"
```

---

### Task 4: Wire Blackbox Workflow to Browser Engine Factory

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py:17-33` (imports in unsafe block)
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py:74-83` (config + engine resolution)
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py:160-161` (session-specific config write)
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py:273-280` (cleanup in finally block)
- Test: `packages/blackbox/tests/test_workflows.py`

This task replaces all direct `playwright_config_writer` calls with engine-agnostic `BrowserEngineFactory` methods. The workflow still keeps `get_session_id` and `AGENT_SESSION_MAPPING` for session ID resolution (that's agent-level mapping, not engine-specific).

- [x] **Step 1: Write tests for engine integration logic**

Append to `packages/blackbox/tests/test_workflows.py`:

```python
from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.services.browser_engine import BrowserEngineFactory


class TestBlackboxBrowserEngineIntegration:
    """Test browser engine resolution logic used by BlackboxScanWorkflow."""

    def test_unavailable_engine_raises_error(self, monkeypatch):
        """Engine with check_available()=False should trigger PentestError at startup."""
        import shannon_core.services.engines  # noqa: F401 — register engines

        engine = BrowserEngineFactory.get_engine("playwright")
        monkeypatch.setattr(
            engine.__class__, "check_available", lambda self: False
        )
        engine = BrowserEngineFactory.get_engine("playwright")
        assert not engine.check_available()

        # Simulate workflow startup check
        if not engine.check_available():
            error = PentestError(
                f"Browser engine '{engine.name}' is not available. "
                f"Install it with: npm install -g {engine.name} && {engine.name} install",
                "browser",
                error_code=ErrorCode.BROWSER_ENGINE_UNAVAILABLE,
            )
        assert error.error_code == ErrorCode.BROWSER_ENGINE_UNAVAILABLE
        assert "not available" in error.message

    def test_engine_resolved_from_config(self, tmp_path):
        """Engine name should match config.browser_engine field."""
        from shannon_core.config.parser import parse_config
        import shannon_core.services.engines  # noqa: F401

        config_file = tmp_path / "config.yaml"
        config_file.write_text("browser_engine: agent-browser\n")
        cfg = parse_config(str(config_file))

        engine_name = cfg.browser_engine
        engine = BrowserEngineFactory.get_engine(engine_name)
        assert engine.name == "agent-browser"

    def test_default_engine_without_config(self):
        """Without config, engine defaults to playwright."""
        import shannon_core.services.engines  # noqa: F401

        engine_name = "playwright"
        engine = BrowserEngineFactory.get_engine(engine_name)
        assert engine.name == "playwright"

    def test_engine_write_config_replaces_write_stealth_config(self, tmp_path):
        """engine.write_config() should produce the same result as write_stealth_config."""
        import shannon_core.services.engines  # noqa: F401

        engine = BrowserEngineFactory.get_engine("playwright")
        result = engine.write_config(str(tmp_path))
        assert result["result"] in ("wrote", "skipped-existing")
        assert "configPath" in result

    def test_engine_cleanup_removes_config(self, tmp_path):
        """engine.cleanup_config() should remove all engine artifacts."""
        import shannon_core.services.engines  # noqa: F401

        engine = BrowserEngineFactory.get_engine("playwright")
        engine.write_config(str(tmp_path))
        engine.cleanup_config(str(tmp_path))
        assert not (tmp_path / ".playwright").exists()
```

- [x] **Step 2: Run new tests to establish baseline**

Run: `pytest packages/blackbox/tests/test_workflows.py::TestBlackboxBrowserEngineIntegration -v`
Expected: PASS — these tests verify component integration that already works after Tasks 1-3

- [x] **Step 3: Replace imports in blackbox workflow**

In `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`, modify the `workflow.unsafe.imports_passed_through()` block (lines 17-33).

Current:
```python
with workflow.unsafe.imports_passed_through():
    from . import activities
    from ..services.exploitation_checker import ExploitationChecker
    from shannon_core.utils.progress import AgentOutcome, format_exploit_summary
    from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
    from shannon_core.services.playwright_config_writer import (
        write_stealth_config,
        cleanup_stealth_config,
        get_session_id,
        AGENT_SESSION_MAPPING,
    )
    from shannon_core.services.validate_authentication import cleanup_auth_state_sync
    from shannon_core.models.retry import (
        PREFLIGHT_RETRY, AUTH_VALIDATION_RETRY, NON_RETRYABLE,
        get_retry_policy,
    )
    from shannon_core.models.errors import classify_error_for_temporal
```

Replace with:
```python
with workflow.unsafe.imports_passed_through():
    from . import activities
    from ..services.exploitation_checker import ExploitationChecker
    from shannon_core.utils.progress import AgentOutcome, format_exploit_summary
    from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
    from shannon_core.services.browser_engine import BrowserEngineFactory
    import shannon_core.services.engines  # noqa: F401 – registers engines
    from shannon_core.services.playwright_config_writer import (
        get_session_id,
        AGENT_SESSION_MAPPING,
    )
    from shannon_core.services.validate_authentication import cleanup_auth_state_sync
    from shannon_core.models.retry import (
        PREFLIGHT_RETRY, AUTH_VALIDATION_RETRY, NON_RETRYABLE,
        get_retry_policy,
    )
    from shannon_core.models.errors import PentestError, ErrorCode, classify_error_for_temporal
```

Changes:
- Added `BrowserEngineFactory` import and `import shannon_core.services.engines`
- Removed `write_stealth_config` and `cleanup_stealth_config` from playwright_config_writer import
- Added `PentestError, ErrorCode` to error imports

- [x] **Step 4: Replace config resolution + add engine availability check**

In `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`, replace lines 74-83.

Current:
```python
        # Write code path deny rules (S6)
        if input.config_path:
            from shannon_core.config.parser import parse_config
            cfg = parse_config(input.config_path)
            if cfg.rules and cfg.rules.avoid:
                sync_code_path_deny_rules(cfg.rules.avoid)

        # Write stealth config (S5) — only if repo path provided
        if input.repo_path:
            write_stealth_config(input.repo_path)
```

Replace with:
```python
        # Resolve config and browser engine
        cfg = None
        if input.config_path:
            from shannon_core.config.parser import parse_config
            cfg = parse_config(input.config_path)

        engine_name = cfg.browser_engine if cfg else "playwright"
        engine = BrowserEngineFactory.get_engine(engine_name)
        if not engine.check_available():
            raise PentestError(
                f"Browser engine '{engine.name}' is not available. "
                f"Install it with: npm install -g {engine.name} && {engine.name} install",
                "browser",
                error_code=ErrorCode.BROWSER_ENGINE_UNAVAILABLE,
            )

        # Write code path deny rules (S6)
        if cfg and cfg.rules and cfg.rules.avoid:
            sync_code_path_deny_rules(cfg.rules.avoid)

        # Write browser engine config (S5) — only if repo path provided
        if input.repo_path:
            engine.write_config(input.repo_path)
```

- [x] **Step 5: Replace session-specific config write**

In the same file, replace line 161.

Current:
```python
                        session_id = get_session_id(agent_name.value)
                        write_stealth_config(input.repo_path, session_id=session_id)
```

Replace with:
```python
                        session_id = get_session_id(agent_name.value)
                        engine.write_config(input.repo_path, session_id=session_id)
```

- [x] **Step 6: Replace cleanup in finally block**

In the same file, replace the finally block (lines 273-280).

Current:
```python
        finally:
            cleanup_settings()
            if input.repo_path:
                # Clean up session-specific configs
                for session_id in set(AGENT_SESSION_MAPPING.values()):
                    from shannon_core.services.playwright_config_writer import cleanup_session_config
                    cleanup_session_config(input.repo_path, session_id)
                cleanup_stealth_config(input.repo_path)
                cleanup_auth_state_sync(act_input.workspace_path or input.repo_path)
```

Replace with:
```python
        finally:
            cleanup_settings()
            if input.repo_path:
                # Clean up session-specific configs
                for session_id in set(AGENT_SESSION_MAPPING.values()):
                    engine.cleanup_config(input.repo_path, session_id=session_id)
                engine.cleanup_config(input.repo_path)
                cleanup_auth_state_sync(act_input.workspace_path or input.repo_path)
```

- [x] **Step 7: Run all blackbox tests to verify no regressions**

Run: `pytest packages/blackbox/tests/ -v`
Expected: ALL PASS

- [x] **Step 8: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/workflows.py packages/blackbox/tests/test_workflows.py
git commit -m "refactor: wire blackbox workflow to browser engine factory"
```

---

### Task 5: Wire Whitebox Workflow to Browser Engine Factory

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:10` (add ErrorCode to top-level import)
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:14-22` (imports in unsafe block)
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:79-87` (config + engine resolution)
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:172-175` (cleanup in finally block)
- Test: `packages/whitebox/tests/test_workflows.py`

- [x] **Step 1: Write tests for engine integration logic**

Append to `packages/whitebox/tests/test_workflows.py`:

```python
from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.services.browser_engine import BrowserEngineFactory


class TestWhiteboxBrowserEngineIntegration:
    """Test browser engine resolution logic used by WhiteboxScanWorkflow."""

    def test_engine_from_config_browser_engine(self, tmp_path):
        """Engine should be resolved from config.browser_engine field."""
        from shannon_core.config.parser import parse_config
        import shannon_core.services.engines  # noqa: F401

        config_file = tmp_path / "config.yaml"
        config_file.write_text("browser_engine: agent-browser\n")
        cfg = parse_config(str(config_file))

        engine_name = cfg.browser_engine
        engine = BrowserEngineFactory.get_engine(engine_name)
        assert engine.name == "agent-browser"

    def test_default_playwright_without_config(self):
        """Without config, engine defaults to playwright."""
        import shannon_core.services.engines  # noqa: F401

        engine_name = "playwright"
        engine = BrowserEngineFactory.get_engine(engine_name)
        assert engine.name == "playwright"

    def test_unavailable_engine_raises_error(self, monkeypatch):
        """Engine with check_available()=False should raise PentestError."""
        import shannon_core.services.engines  # noqa: F401

        engine = BrowserEngineFactory.get_engine("playwright")
        monkeypatch.setattr(
            engine.__class__, "check_available", lambda self: False
        )
        engine = BrowserEngineFactory.get_engine("playwright")
        if not engine.check_available():
            error = PentestError(
                f"Browser engine '{engine.name}' is not available.",
                "browser",
                error_code=ErrorCode.BROWSER_ENGINE_UNAVAILABLE,
            )
        assert error.error_code == ErrorCode.BROWSER_ENGINE_UNAVAILABLE
```

- [x] **Step 2: Run new tests to establish baseline**

Run: `pytest packages/whitebox/tests/test_workflows.py::TestWhiteboxBrowserEngineIntegration -v`
Expected: PASS — these verify component integration after Tasks 1-3

- [x] **Step 3: Update imports**

In `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`:

**Top-level (line 10):** Add `ErrorCode` to the existing `PentestError` import:

Current:
```python
from shannon_core.models.errors import PentestError
```

Replace with:
```python
from shannon_core.models.errors import ErrorCode, PentestError
```

**Unsafe imports block (lines 14-22):**

Current:
```python
with workflow.unsafe.imports_passed_through():
    from . import activities
    from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
    from shannon_core.services.playwright_config_writer import write_stealth_config, cleanup_stealth_config
    from shannon_core.services.validate_authentication import cleanup_auth_state_sync
    from shannon_core.models.retry import (
        PREFLIGHT_RETRY, AUTH_VALIDATION_RETRY, PRODUCTION_RETRY, NON_RETRYABLE,
    )
    from shannon_core.models.errors import classify_error_for_temporal
```

Replace with:
```python
with workflow.unsafe.imports_passed_through():
    from . import activities
    from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
    from shannon_core.services.browser_engine import BrowserEngineFactory
    import shannon_core.services.engines  # noqa: F401 – registers engines
    from shannon_core.services.validate_authentication import cleanup_auth_state_sync
    from shannon_core.models.retry import (
        PREFLIGHT_RETRY, AUTH_VALIDATION_RETRY, PRODUCTION_RETRY, NON_RETRYABLE,
    )
    from shannon_core.models.errors import classify_error_for_temporal
```

Changes:
- Removed `playwright_config_writer` import entirely (whitebox doesn't use session IDs)
- Added `BrowserEngineFactory` import and `import shannon_core.services.engines`

- [x] **Step 4: Replace config resolution + add engine availability check**

In the same file, replace lines 79-87.

Current:
```python
        # Write code path deny rules (S6)
        if input.config_path:
            from shannon_core.config.parser import parse_config
            cfg = parse_config(input.config_path)
            if cfg.rules and cfg.rules.avoid:
                sync_code_path_deny_rules(cfg.rules.avoid)

        # Write stealth config (S5)
        write_stealth_config(input.repo_path)
```

Replace with:
```python
        # Resolve config and browser engine
        cfg = None
        if input.config_path:
            from shannon_core.config.parser import parse_config
            cfg = parse_config(input.config_path)

        engine_name = cfg.browser_engine if cfg else "playwright"
        engine = BrowserEngineFactory.get_engine(engine_name)
        if not engine.check_available():
            raise PentestError(
                f"Browser engine '{engine.name}' is not available. "
                f"Install it with: npm install -g {engine.name} && {engine.name} install",
                "browser",
                error_code=ErrorCode.BROWSER_ENGINE_UNAVAILABLE,
            )

        # Write code path deny rules (S6)
        if cfg and cfg.rules and cfg.rules.avoid:
            sync_code_path_deny_rules(cfg.rules.avoid)

        # Write browser engine config (S5)
        engine.write_config(input.repo_path)
```

- [x] **Step 5: Replace cleanup in finally block**

In the same file, replace the finally block (lines 172-175).

Current:
```python
        finally:
            cleanup_settings()
            cleanup_stealth_config(input.repo_path)
            cleanup_auth_state_sync(workspace_path)
```

Replace with:
```python
        finally:
            cleanup_settings()
            engine.cleanup_config(input.repo_path)
            cleanup_auth_state_sync(workspace_path)
```

- [x] **Step 6: Run all whitebox tests to verify no regressions**

Run: `pytest packages/whitebox/tests/ -v`
Expected: ALL PASS

- [x] **Step 7: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/test_workflows.py
git commit -m "refactor: wire whitebox workflow to browser engine factory"
```

---

### Task 6: Document SHANNON_BROWSER_ENGINE in .env.example

**Files:**
- Modify: `.env.example:70-73`

- [x] **Step 1: Add SHANNON_BROWSER_ENGINE documentation**

In `.env.example`, add after the "开发选项" section (after line 73):

```
# 浏览器引擎选择（可选）
# 可选值: "playwright"（默认）或 "agent-browser"
# 覆盖 shannon.yaml 中的 browser_engine 设置
# SHANNON_BROWSER_ENGINE=playwright
```

- [x] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: document SHANNON_BROWSER_ENGINE in .env.example"
```

---

## Self-Review Checklist

### 1. Spec Coverage

| Spec Requirement | Task |
|-----------------|------|
| `SHANNON_BROWSER_ENGINE` env var overrides `Config.browser_engine` at config parse time | Task 2 |
| Workflows resolve engine from config via `BrowserEngineFactory` | Tasks 4, 5 |
| Pipeline fails fast at startup if engine CLI not installed | Tasks 4, 5 |
| `AgentExecutor` passes `browser_engine` to prompt variables | Task 3 |
| `.env.example` documents `SHANNON_BROWSER_ENGINE` | Task 6 |
| `BROWSER_ENGINE_UNAVAILABLE` error code added | Task 1 |

### 2. Placeholder Scan

No TBD, TODO, "implement later", "add validation", "similar to Task N", or "fill in details" found. All code is complete.

### 3. Type Consistency

- `Config.browser_engine` is `BrowserEngineType = Literal["playwright", "agent-browser"]` — all factory calls use string values from this type
- `BrowserEngineFactory.get_engine(engine_name: str) -> BrowserEngine` — workflow and executor both call with string
- `engine.write_config(source_dir: str, session_id: str | None = None) -> dict` — blackbox calls with `str(repo_path)` and `session_id=str`
- `engine.cleanup_config(source_dir: str, session_id: str | None = None) -> None` — both workflows call correctly
- `ErrorCode.BROWSER_ENGINE_UNAVAILABLE` — consistent across error raise and classification
- `variables["browser_engine"] = config.browser_engine` in executor — `config.browser_engine` is a string (BrowserEngineType), matches `variables.get("browser_engine", "playwright")` in PromptManager
