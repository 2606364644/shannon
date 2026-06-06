# Dual Browser Engine Design

**Date**: 2026-06-06
**Status**: Approved
**Scope**: Add agent-browser as an alternative to playwright-cli, with configurable engine selection

## Background

Shannon-py currently uses `playwright-cli` as its sole browser automation engine. Browser commands are embedded in agent prompt templates, and session isolation is managed through `AGENT_SESSION_MAPPING`. The goal is to add Vercel Labs' `agent-browser` as a fully interchangeable alternative, selected via global configuration.

## Requirements

- Two browser engines: `playwright` (default) and `agent-browser`, fully interchangeable
- Engine selection at global level via `Config.browser_engine` field + `SHANNON_BROWSER_ENGINE` env var
- Abstract browser operations behind a `BrowserEngine` protocol
- Prompt templates use engine-agnostic placeholders, not hardcoded CLI commands
- Reuse existing `AGENT_SESSION_MAPPING` for session isolation across both engines
- Backward compatible: default is `playwright`, no config change needed for existing users
- Agent-browser is NOT a 100% feature-equivalent replacement for playwright. Key differences:
  - **Auth state sharing**: Playwright uses `state-save`/`state-load` (JSON file). Agent-browser uses `--profile` (persistent Chrome profile directory). Both achieve cross-agent auth sharing but via different mechanisms.
  - **Selector model**: Playwright uses CSS/XPath selectors. Agent-browser uses `@ref` selectors from accessibility tree snapshots (snapshot → get refs → interact).
  - **Anti-detection**: Playwright requires manual `stealth.js` injection. Agent-browser has built-in anti-detection.

## Architecture

```
Config (browser_engine field)
        │
        ▼
BrowserEngineFactory.get_engine(config)
        │
   ┌────┴────┐
   ▼         ▼
PlaywrightEngine  AgentBrowserEngine
   │         │
   └────┬────┘
        ▼
  PromptManager injects:
  {{BROWSER_SESSION_FLAG}}  — e.g. "-s=agent-xss" or "--session agent-xss"
  {{BROWSER_COMMANDS}}      — engine-specific command reference text
```

## Component Design

### 1. BrowserEngine Protocol

File: `packages/core/src/shannon_core/services/browser_engine.py`

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class BrowserEngine(Protocol):
    @property
    def name(self) -> str:
        """Engine identifier: 'playwright' | 'agent-browser'."""
        ...

    def session_flag(self, session_id: str) -> str:
        """CLI flag string for session isolation.
        playwright:   "-s={session_id}"
        agent-browser: "--session {session_id}"
        """
        ...

    def commands_reference(self) -> str:
        """Engine-specific command reference text for prompt injection."""
        ...

    def auth_save_command(self, session_id: str, path: str) -> str:
        """Command to save current auth state.
        playwright:   "state-save {path}"
        agent-browser: No explicit save needed (--profile auto-persists)
        """
        ...

    def auth_load_command(self, session_id: str, path: str) -> str:
        """Command to load saved auth state.
        playwright:   "state-load {path}"
        agent-browser: No explicit load needed (--profile auto-restores)
        """
        ...

    def write_config(self, source_dir: str, session_id: str | None = None) -> dict:
        """Write engine config files. Returns {'result': 'wrote'|'skipped-existing', 'configPath': str}."""
        ...

    def cleanup_config(self, source_dir: str, session_id: str | None = None) -> None:
        """Remove engine config files and state."""
        ...

    def check_available(self) -> bool:
        """Check if the engine CLI is installed and usable."""
        ...
```

### 2. BrowserEngineFactory

File: `packages/core/src/shannon_core/services/browser_engine.py` (same file)

```python
class BrowserEngineFactory:
    _engines: dict[str, type] = {}  # registered engine classes

    @classmethod
    def register(cls, name: str, engine_class: type):
        cls._engines[name] = engine_class

    @classmethod
    def get_engine(cls, engine_name: str) -> BrowserEngine:
        if engine_name not in cls._engines:
            raise ValueError(f"Unknown browser engine: {engine_name}")
        return cls._engines[engine_name]()
```

Registration happens at import time:

```python
# engines/__init__.py
from .playwright_engine import PlaywrightEngine
from .agent_browser_engine import AgentBrowserEngine
from ..browser_engine import BrowserEngineFactory

BrowserEngineFactory.register("playwright", PlaywrightEngine)
BrowserEngineFactory.register("agent-browser", AgentBrowserEngine)
```

### 3. PlaywrightEngine

File: `packages/core/src/shannon_core/services/engines/playwright_engine.py`

Encapsulates all current `playwright_config_writer.py` logic:

- `name` → `"playwright"`
- `session_flag(sid)` → `"-s={sid}"`
- `commands_reference()` → playwright-cli command reference text
- `write_config()` → delegates to existing stealth config + init script generation
- `cleanup_config()` → delegates to existing cleanup logic
- `check_available()` → `shutil.which("playwright-cli") is not None`

The existing `playwright_config_writer.py` becomes a thin facade that delegates to `PlaywrightEngine` for backward compatibility.

### 4. AgentBrowserEngine

File: `packages/core/src/shannon_core/services/engines/agent_browser_engine.py`

- `name` → `"agent-browser"`
- `session_flag(sid)` → `"--session {sid}"` + `"--profile .agent-browser/profiles/{sid}"` for auth state persistence
- `commands_reference()` → agent-browser command reference text, including:
  - `open <url>` for navigation
  - `snapshot` for accessibility tree + refs (@e1, @e2...)
  - `click @<ref>`, `fill @<ref> <text>` for interaction
  - `screenshot`, `get text/html`, `eval <js>` for information retrieval
  - `cookies set/clear` for cookie management
  - `--profile <path>` for auth state persistence (replaces playwright's state-save/state-load)
- `write_config()` → create profile directory structure under `.agent-browser/profiles/{session_id}/`
- `cleanup_config()` → remove profile directories and session artifacts
- `check_available()` → `shutil.which("agent-browser") is not None`

### 5. Config Model Extension

File: `packages/core/src/shannon_core/models/config.py`

```python
BrowserEngineType = Literal["playwright", "agent-browser"]

class Config(BaseModel):
    # ... existing fields ...
    browser_engine: BrowserEngineType = "playwright"
```

Environment variable override at config load time:

```python
import os

def load_config(...) -> Config:
    config_data = ...
    if env_engine := os.environ.get("SHANNON_BROWSER_ENGINE"):
        config_data["browser_engine"] = env_engine
    return Config(**config_data)
```

### 6. PromptManager Integration

File: `packages/core/src/shannon_core/prompts/manager.py`

The `load_sync` method receives engine instance and injects variables:

```python
def load_sync(self, template_name: str, variables: dict, ...) -> str:
    engine = self._get_browser_engine()  # resolved once per session

    # Inject browser variables into every prompt
    session_id = variables.get("browser_session_id", "default")
    variables["BROWSER_SESSION_FLAG"] = engine.session_flag(session_id)
    variables["BROWSER_COMMANDS"] = engine.commands_reference()

    # ... existing template rendering logic ...
```

### 7. Prompt Template Changes

All prompt templates that currently reference `playwright-cli` commands change to use abstract placeholders.

**Before:**
```
Use playwright-cli -s={{PLAYWRIGHT_SESSION}} navigate {{WEB_URL}}
```

**After:**
```
{{BROWSER_COMMANDS}}
```

The `{{BROWSER_COMMANDS}}` placeholder injects the **complete** command reference for the selected engine, including all necessary command names, flags, and usage patterns. Prompt templates should NOT reference any engine-specific CLI binary name (no `playwright-cli` or `agent-browser` literal strings). The `BROWSER_COMMANDS` text contains everything the AI agent needs to know about how to operate the browser.

If a template needs to reference the session flag inline (e.g., in an example), use `{{BROWSER_SESSION_FLAG}}` which resolves to `-s=<session>` or `--session <session>` depending on engine.

### 8. Workflow Integration

**Existing workflow calls** change from direct playwright calls to engine interface:

```python
# Before
from shannon_core.services.playwright_config_writer import write_stealth_config
result = write_stealth_config(source_dir, session_id)

# After
from shannon_core.services.browser_engine import BrowserEngineFactory
engine = BrowserEngineFactory.get_engine(config.browser_engine)
result = engine.write_config(source_dir, session_id)
```

**ExploitExecutor** changes from passing `playwright_session` to passing engine-aware variables:

```python
# Before
prompt_variables["playwright_session"] = get_session_id(agent_name.value)

# After
engine = BrowserEngineFactory.get_engine(config.browser_engine)
session_id = get_session_id(agent_name.value)
prompt_variables["browser_session_id"] = session_id
```

The PromptManager handles generating `BROWSER_SESSION_FLAG` and `BROWSER_COMMANDS` from the session ID and engine.

### 9. Session Mapping

`AGENT_SESSION_MAPPING` remains unchanged and external to engine implementations:

```python
AGENT_SESSION_MAPPING: dict[str, str] = {
    "injection-exploit": "agent-injection",
    "xss-exploit": "agent-xss",
    "auth-exploit": "agent-auth",
    "ssrf-exploit": "agent-ssrf",
    "authz-exploit": "agent-authz",
    "misconfig-exploit": "agent-misconfig",
}
```

Both engines accept the same session ID strings via their respective `session_flag()` methods.

## File Change List

| Action | File | Description |
|--------|------|-------------|
| New | `services/browser_engine.py` | `BrowserEngine` Protocol + `BrowserEngineFactory` |
| New | `services/engines/__init__.py` | Engine subpackage + registration |
| New | `services/engines/playwright_engine.py` | Playwright engine implementation |
| New | `services/engines/agent_browser_engine.py` | AgentBrowser engine implementation |
| Modify | `models/config.py` | Add `browser_engine` field |
| Modify | `prompts/manager.py` | Add engine variable injection |
| Modify | `agents/exploit_executor.py` | Use engine interface |
| Modify | All prompt template files | Replace `{{PLAYWRIGHT_SESSION}}` with `{{BROWSER_SESSION_FLAG}}` |
| Refactor | `services/playwright_config_writer.py` | Logic moved to PlaywrightEngine, keep as facade |
| New | `tests/test_browser_engine.py` | Factory and interface tests |
| New | `tests/test_agent_browser_engine.py` | AgentBrowser engine tests |
| Modify | `tests/test_playwright_config_writer.py` | Update to test via PlaywrightEngine |

## Error Handling

1. **Engine not available**: At pipeline start, `check_available()` runs. If the selected engine is missing:
   ```
   Error: browser engine 'agent-browser' is not available.
   Please run: npm install -g agent-browser && agent-browser install
   ```

2. **Config conflict**: `SHANNON_BROWSER_ENGINE` env var overrides `Config.browser_engine` field. Documented in `.env.example`.

3. **Session state incompatibility**: Switching engines mid-pipeline is not supported. The pipeline initializes the engine once at start and uses it throughout.

4. **Unknown engine name**: `BrowserEngineFactory.get_engine()` raises `ValueError` with the list of available engine names.

## Testing Strategy

1. **Unit tests**: Each engine class tested independently with mocked filesystem
2. **Integration tests**: Factory resolution, config → engine → prompt variable flow
3. **Existing tests**: All existing playwright tests continue passing via PlaywrightEngine facade
4. **Engine availability tests**: `check_available()` mocked for CI environments without agent-browser installed

## Migration Path

1. `playwright_config_writer.py` kept as a thin facade that delegates to `PlaywrightEngine` for backward compat
2. Default engine is `playwright` — zero config change needed for existing users
3. Prompt templates updated in-place — no parallel template versions
4. Migration is all-or-nothing per template file (no mixing old/new variables)

## Open Questions

None. All design decisions resolved through discussion.
