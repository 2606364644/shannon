# Browser Engine Configuration Wiring

**Date**: 2026-06-06
**Status**: Approved
**Scope**: Wire up the config-to-runtime path for browser engine selection
**Depends on**: `2026-06-06-dual-browser-engine-design.md` (implemented)

## Background

The dual browser engine abstraction layer is complete (Protocol, Factory, PlaywrightEngine, AgentBrowserEngine, PromptManager integration). However, the runtime wiring is missing: user config cannot flow from `Config.browser_engine` to the actual engine used by workflows and agents. This spec covers the remaining integration work.

## Requirements

- `SHANNON_BROWSER_ENGINE` env var overrides `Config.browser_engine` at config parse time
- Workflows resolve engine from config via `BrowserEngineFactory` instead of calling `playwright_config_writer` directly
- Pipeline fails fast at startup if the selected engine CLI is not installed
- `AgentExecutor` passes `browser_engine` to `prompt_variables` so PromptManager selects the correct engine
- `.env.example` documents `SHANNON_BROWSER_ENGINE`

## File Changes

| Action | File | Description |
|--------|------|-------------|
| Modify | `packages/core/src/shannon_core/config/parser.py` | Add `SHANNON_BROWSER_ENGINE` env var override |
| Modify | `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | Use engine factory, add startup check, inject browser_engine to variables |
| Modify | `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | Use engine factory, add startup check, inject browser_engine to variables |
| Modify | `.env.example` | Document `SHANNON_BROWSER_ENGINE` |

## Component Design

### 1. Config Parser: Environment Variable Override

File: `packages/core/src/shannon_core/config/parser.py`

In `parse_config()`, after reading the raw config dict, add:

```python
import os

if env_engine := os.environ.get("SHANNON_BROWSER_ENGINE"):
    raw["browser_engine"] = env_engine
```

This follows the existing pattern where env vars override file config.

### 2. Workflow Engine Integration

Files: `packages/blackbox/.../workflows.py` and `packages/whitebox/.../workflows.py`

**Startup check** — at the beginning of the pipeline entry point, before any agent runs:

```python
from shannon_core.services.browser_engine import BrowserEngineFactory

engine = BrowserEngineFactory.get_engine(config.browser_engine)
if not engine.check_available():
    raise PentestError(
        f"Browser engine '{engine.name}' is not available. "
        f"Install it with: npm install -g {engine.name} && {engine.name} install",
        "browser",
        error_code=ErrorCode.BROWSER_ENGINE_UNAVAILABLE,
    )
```

**Config writing** — replace `write_stealth_config(source_dir, session_id)` calls with:

```python
engine = BrowserEngineFactory.get_engine(config.browser_engine)
engine.write_config(source_dir, session_id)
```

**Config cleanup** — replace `cleanup_stealth_config(source_dir)` calls with:

```python
engine = BrowserEngineFactory.get_engine(config.browser_engine)
engine.cleanup_config(source_dir)
```

### 3. Workflow Engine Variable Injection

Files: `packages/blackbox/.../workflows.py` and `packages/whitebox/.../workflows.py`

The workflow layer has access to the parsed `Config` object (which contains `browser_engine`). Before calling `executor.execute()`, add the engine selection to `prompt_variables`:

```python
# In the workflow, when building prompt_variables for executor calls:
prompt_variables["browser_engine"] = config.browser_engine
```

This flows through to `PromptManager._interpolate()` which reads `variables.get("browser_engine", "playwright")`.

Note: `AgentExecutor.execute()` itself does NOT need changes — it already passes `prompt_variables` through to `PromptManager` unchanged.

### 4. Error Code

Add `BROWSER_ENGINE_UNAVAILABLE` to `packages/core/src/shannon_core/models/errors.py` error codes.

### 5. Environment Variable Documentation

Add to `.env.example`:

```
# Browser engine selection: "playwright" (default) or "agent-browser"
# Overrides browser_engine setting in shannon.yaml
# SHANNON_BROWSER_ENGINE=playwright
```

## Error Handling

1. **Engine not installed**: Pipeline fails at startup with clear install instructions. No partial execution.
2. **Unknown engine name**: `BrowserEngineFactory.get_engine()` raises `KeyError` with list of available engines. This happens at startup before any agent runs.
3. **Env var typo**: Invalid `SHANNON_BROWSER_ENGINE` value (e.g., `"chromium"`) triggers Pydantic validation error at config parse time.

## Testing

- Unit test: `parse_config` respects `SHANNON_BROWSER_ENGINE` env var
- Unit test: `parse_config` env var overrides yaml config value
- Unit test: Workflow passes `browser_engine` to prompt_variables
- Integration test: Pipeline startup check fails with unavailable engine (mocked)
- Existing workflow tests updated to use engine factory

## Migration

- Default is `playwright` — zero config change needed for existing users
- `SHANNON_BROWSER_ENGINE` is optional — only needed when overriding yaml config
- All existing `write_stealth_config` / `cleanup_stealth_config` callers in workflows are replaced
