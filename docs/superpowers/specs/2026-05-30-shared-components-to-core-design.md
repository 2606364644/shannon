# Extract Shared Components from Whitebox to Core — Design Spec

## Overview

Extract all shared infrastructure components from `shannon-whitebox` into `shannon-core` so that `shannon-blackbox` can run independently without depending on the whitebox package. This is sub-project 1 of 3 in the broader effort to enable independent black-box scanning with authentication support.

## Problem

Current architecture forces blackbox to depend on whitebox:

```
shannon-core (models, config, utils)
    ↑
shannon-whitebox (AgentExecutor, PromptManager, SessionManager, GitManager,
                  validate_authentication, runner, validators, services,
                  pipeline, audit, cli, worker)
    ↑
shannon-blackbox (imports 9 components from whitebox)
```

Blackbox imports from whitebox in 5 files:
- `agents/exploit_executor.py` → `AgentExecutor`
- `agents/recon_executor.py` → `AgentExecutor`
- `pipeline/activities.py` → `AgentExecutor`, `PromptManager`, `validate_authentication`
- `pipeline/workflows.py` → `sync_code_path_deny_rules`, `cleanup_settings`, `write_stealth_config`, `cleanup_stealth_config`
- `cli/main.py` → `SessionManager`

## Target Architecture

```
shannon-core
├── models/          (unchanged)
├── config/          (unchanged)
├── utils/           (unchanged)
├── agents/          (NEW) — executor.py, runner.py, validators.py
├── prompts/         (NEW) — manager.py
├── services/        (NEW) — validate_authentication.py,
│                        playwright_config_writer.py,
│                        settings_writer.py
├── session.py       (NEW) — SessionManager
└── git_manager.py   (NEW) — GitManager

shannon-whitebox         shannon-blackbox
├── pipeline/            ├── agents/
│   ├── workflows        ├── pipeline/
│   ├── activities       ├── services/
│   └── shared           ├── worker.py
├── audit/               └── cli/
├── worker.py
└── cli/
```

Both whitebox and blackbox depend only on core. They do not depend on each other.

## Components to Move

| Component | From (whitebox) | To (core) | Core Deps | Whitebox Deps |
|---|---|---|---|---|
| `AgentExecutor` | `agents/executor.py` | `shannon_core/agents/executor.py` | `distribute_config`, `parse_config`, `AgentName`, `AGENTS`, `Config`, `ErrorCode`, `PentestError`, `AgentMetrics`, `is_spending_cap_behavior` | `runner`, `validators`, `GitManager`, `PromptManager` |
| `ClaudeRunResult` + `run_claude_prompt` | `agents/runner.py` | `shannon_core/agents/runner.py` | none | none |
| `validate_deliverable` + `get_queue_filename` + `get_vuln_type` | `agents/validators.py` | `shannon_core/agents/validators.py` | `AgentName`, `AGENTS`, `ErrorCode`, `PentestError` | none |
| `PromptManager` | `prompts/manager.py` | `shannon_core/prompts/manager.py` | `PLAYWRIGHT_SESSION_MAPPING`, `DistributedConfig`, `ErrorCode`, `PentestError` | none |
| `SessionManager` | `session.py` | `shannon_core/session.py` | `AgentName` | none |
| `GitManager` | `git_manager.py` | `shannon_core/git_manager.py` | `AgentName`, `ErrorCode`, `PentestError` | none |
| `validate_authentication` + `AuthValidationResult` | `services/validate_authentication.py` | `shannon_core/services/validate_authentication.py` | `AgentName`, `parse_config`, `distribute_config` | `AgentExecutor` (TYPE_CHECKING), `PromptManager` (TYPE_CHECKING) |
| `write_stealth_config` + `cleanup_stealth_config` | `services/playwright_config_writer.py` | `shannon_core/services/playwright_config_writer.py` | none | none |
| `sync_code_path_deny_rules` + `cleanup_settings` | `services/settings_writer.py` | `shannon_core/services/settings_writer.py` | `Rule` | none |

## Core Package Structure After Migration

```
packages/core/src/shannon_core/
├── __init__.py
├── config/
│   ├── __init__.py
│   └── parser.py
├── models/
│   ├── __init__.py
│   ├── agents.py
│   ├── config.py
│   ├── deliverables.py
│   ├── errors.py
│   ├── metrics.py
│   ├── queue_schemas.py
│   └── result.py
├── agents/
│   ├── __init__.py
│   ├── executor.py
│   ├── runner.py
│   └── validators.py
├── prompts/
│   ├── __init__.py
│   └── manager.py
├── services/
│   ├── __init__.py
│   ├── validate_authentication.py
│   ├── playwright_config_writer.py
│   └── settings_writer.py
├── session.py
├── git_manager.py
└── utils/
    ├── __init__.py
    ├── billing.py
    ├── concurrency.py
    ├── credential_validator.py
    ├── file_io.py
    ├── formatting.py
    └── security.py
```

## Whitebox Package After Migration

### Retained Files

Only whitebox-specific pipeline, audit, worker, and CLI remain:

```
packages/whitebox/src/shannon_whitebox/
├── __init__.py
├── pipeline/
│   ├── __init__.py
│   ├── workflows.py
│   ├── activities.py
│   └── shared.py
├── audit/
│   ├── __init__.py
│   ├── session.py
│   └── log_stream.py
├── worker.py
└── cli/
    ├── __init__.py
    └── main.py
```

### Deleted Files (moved to core)

- `agents/executor.py`
- `agents/runner.py`
- `agents/validators.py`
- `agents/__init__.py` (empty directory)
- `prompts/manager.py`
- `prompts/__init__.py` (empty directory)
- `session.py`
- `git_manager.py`
- `services/validate_authentication.py`
- `services/playwright_config_writer.py`
- `services/settings_writer.py`
- `services/__init__.py` (empty directory)

### Import Changes

All whitebox files change imports from `shannon_whitebox.xxx` to `shannon_core.xxx` for the moved components:

| File | Old Import | New Import |
|---|---|---|
| `pipeline/activities.py` | `from shannon_whitebox.agents.executor import AgentExecutor` | `from shannon_core.agents.executor import AgentExecutor` |
| `pipeline/activities.py` | `from shannon_whitebox.prompts.manager import PromptManager` | `from shannon_core.prompts.manager import PromptManager` |
| `pipeline/activities.py` | `from shannon_whitebox.session import SessionManager` | `from shannon_core.session import SessionManager` |
| `pipeline/workflows.py` | `from shannon_whitebox.services.settings_writer import ...` | `from shannon_core.services.settings_writer import ...` |
| `pipeline/workflows.py` | `from shannon_whitebox.services.playwright_config_writer import ...` | `from shannon_core.services.playwright_config_writer import ...` |
| `cli/main.py` | `from shannon_whitebox.session import SessionManager` | `from shannon_core.session import SessionManager` |

Whitebox-only imports (`AuditSession`, `LogStream`, pipeline types) remain unchanged.

## Blackbox Package Changes

### Dependency Change

```toml
# pyproject.toml — remove shannon-whitebox dependency
dependencies = [
    "shannon-core",
    # "shannon-whitebox" — REMOVED
    "temporalio>=1.0",
    "click>=8.0",
    "aiofiles>=23.0",
]
```

### Import Changes

| File | Old Import | New Import |
|---|---|---|
| `agents/exploit_executor.py` | `from shannon_whitebox.agents.executor import AgentExecutor` | `from shannon_core.agents.executor import AgentExecutor` |
| `agents/recon_executor.py` | `from shannon_whitebox.agents.executor import AgentExecutor` | `from shannon_core.agents.executor import AgentExecutor` |
| `pipeline/activities.py` | `from shannon_whitebox.agents.executor import AgentExecutor` | `from shannon_core.agents.executor import AgentExecutor` |
| `pipeline/activities.py` | `from shannon_whitebox.prompts.manager import PromptManager` | `from shannon_core.prompts.manager import PromptManager` |
| `pipeline/activities.py` | `from shannon_whitebox.services.validate_authentication import ...` | `from shannon_core.services.validate_authentication import ...` |
| `pipeline/workflows.py` | `from shannon_whitebox.services.settings_writer import ...` | `from shannon_core.services.settings_writer import ...` |
| `pipeline/workflows.py` | `from shannon_whitebox.services.playwright_config_writer import ...` | `from shannon_core.services.playwright_config_writer import ...` |
| `cli/main.py` | `from shannon_whitebox.session import SessionManager` | `from shannon_core.session import SessionManager` |

Blackbox file structure is unchanged — only import paths change.

## Core pyproject.toml Changes

```toml
[project]
name = "shannon-core"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "httpx>=0.27",
    "aiofiles>=23.0",    # NEW — required by AgentExecutor
]
```

## Internal Import Changes in Moved Files

Files moved into core must update their internal imports. Currently `executor.py` uses relative imports:

```python
# Before (in whitebox)
from .runner import ClaudeRunResult, run_claude_prompt
from .validators import get_queue_filename, get_vuln_type, validate_deliverable
from ..git_manager import GitManager
from ..prompts.manager import PromptManager
```

After moving into core, these become same-package references. Use absolute imports:

```python
# After (in core)
from shannon_core.agents.runner import ClaudeRunResult, run_claude_prompt
from shannon_core.agents.validators import get_queue_filename, get_vuln_type, validate_deliverable
from shannon_core.git_manager import GitManager
from shannon_core.prompts.manager import PromptManager
```

Other moved files (`runner.py`, `validators.py`, `session.py`, `git_manager.py`, `prompt_manager.py`, `playwright_config_writer.py`, `settings_writer.py`) already import only from `shannon_core.models` or have no dependencies — no internal import changes needed beyond confirming they work at the new path.

## Migration Steps

Execute in strict order to avoid broken intermediate states:

### Phase 1: Create directories in core
1. Create `shannon_core/agents/` with `__init__.py`
2. Create `shannon_core/prompts/` with `__init__.py`
3. Create `shannon_core/services/` with `__init__.py`

### Phase 2: Move dependency-free files (parallel)
4. Move `agents/runner.py` → `shannon_core/agents/runner.py`
5. Move `agents/validators.py` → `shannon_core/agents/validators.py`
6. Move `services/playwright_config_writer.py` → `shannon_core/services/playwright_config_writer.py`
7. Move `services/settings_writer.py` → `shannon_core/services/settings_writer.py`

### Phase 3: Move core-models-only files (parallel)
8. Move `session.py` → `shannon_core/session.py`
9. Move `git_manager.py` → `shannon_core/git_manager.py`
10. Move `prompts/manager.py` → `shannon_core/prompts/manager.py`

### Phase 4: Move files that depend on Phase 2-3 outputs
11. Move `agents/executor.py` → `shannon_core/agents/executor.py` and update internal imports to absolute `shannon_core.*` paths
12. Move `services/validate_authentication.py` → `shannon_core/services/validate_authentication.py` and update internal imports

### Phase 5: Cleanup and update references
13. Delete empty directories from whitebox: `agents/`, `prompts/`, `services/`
14. Update whitebox import paths (pipeline/activities, pipeline/workflows, cli/main)
15. Update blackbox import paths (all 5 files)
16. Update blackbox `pyproject.toml` to remove `shannon-whitebox` dependency
17. Update core `pyproject.toml` to add `aiofiles>=23.0`

### Phase 6: Verify
19. Run core unit tests
20. Run whitebox unit tests
21. Run blackbox unit tests
22. Verify `python -c "from shannon_core.agents.executor import AgentExecutor"` works
23. Verify blackbox `pyproject.toml` does not list `shannon-whitebox`
24. Verify `AuditSession` and `LogStream` remain in `shannon_whitebox.audit`

## What Does NOT Move

These components stay in whitebox because they are whitebox-specific:

| Component | Reason |
|---|---|
| `AuditSession` | Whitebox-only audit logging |
| `LogStream` | Whitebox-only log stream |
| `PipelineInput` / `PipelineState` / `ActivityInput` | Whitebox-specific pipeline shapes |
| `WhiteboxScanWorkflow` | Whitebox-specific Temporal workflow |
| Whitebox activities | Whitebox-specific Temporal activities |
| `worker.py` | Whitebox Temporal worker entry point |
| Whitebox CLI | Whitebox-specific Click commands |

## `__init__.py` Exports

Update core's `__init__.py` and new module `__init__.py` files to re-export the moved components:

### `shannon_core/agents/__init__.py`

```python
from shannon_core.agents.executor import AgentExecutor
from shannon_core.agents.runner import ClaudeRunResult, run_claude_prompt
from shannon_core.agents.validators import validate_deliverable, get_queue_filename, get_vuln_type
```

### `shannon_core/prompts/__init__.py`

```python
from shannon_core.prompts.manager import PromptManager
```

### `shannon_core/services/__init__.py`

```python
from shannon_core.services.validate_authentication import (
    AuthValidationResult,
    validate_authentication,
    auth_state_path,
    cleanup_auth_state,
    verify_auth_state,
)
from shannon_core.services.playwright_config_writer import write_stealth_config, cleanup_stealth_config
from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
```

### `shannon_core/__init__.py`

Add top-level convenience re-exports:

```python
from shannon_core.session import SessionManager
from shannon_core.git_manager import GitManager
```

## Internal Import Fix: `validate_authentication.py`

After moving to core, `validate_authentication.py`'s `TYPE_CHECKING` imports must change:

```python
# Before (in whitebox)
if TYPE_CHECKING:
    from shannon_whitebox.agents.executor import AgentExecutor
    from shannon_whitebox.prompts.manager import PromptManager

# After (in core)
if TYPE_CHECKING:
    from shannon_core.agents.executor import AgentExecutor
    from shannon_core.prompts.manager import PromptManager
```

## Whitebox `run_auth_validation` Activity Update

`shannon_whitebox/pipeline/activities.py` contains a `run_auth_validation` activity that also imports from the moved modules. The same import path changes apply:

```python
# Before
from shannon_whitebox.agents.executor import AgentExecutor
from shannon_whitebox.prompts.manager import PromptManager
from shannon_whitebox.services.validate_authentication import validate_authentication

# After
from shannon_core.agents.executor import AgentExecutor
from shannon_core.prompts.manager import PromptManager
from shannon_core.services.validate_authentication import validate_authentication
```

This was already listed in the Import Changes table but deserves explicit mention since `run_auth_validation` is the whitebox counterpart to blackbox's `run_blackbox_auth_validation`.

## Testing Strategy

| Test Type | Scope | Details |
|---|---|---|
| **Import smoke tests** | All three packages | Verify all moved components are importable from their new locations: `python -c "from shannon_core.agents.executor import AgentExecutor"` etc. |
| **Existing unit tests** | All three packages | All existing tests must pass without modification (import paths in test files may need updating) |
| **Import regression** | Whitebox + Blackbox | Verify no file still imports from old `shannon_whitebox` paths for moved components (`grep -r "from shannon_whitebox.agents" shannon-py/` should return nothing) |
| **Dependency check** | Blackbox | Verify `shannon-blackbox` can be installed and run without `shannon-whitebox` in the environment |

### Migration Phase 6 Verification (expanded)

19. Run `python -c "from shannon_core.agents.executor import AgentExecutor"` — smoke test
20. Run `python -c "from shannon_core.prompts.manager import PromptManager"` — smoke test
21. Run `python -c "from shannon_core.services.validate_authentication import validate_authentication"` — smoke test
22. Run core unit tests: `pytest packages/core/`
23. Run whitebox unit tests: `pytest packages/whitebox/`
24. Run blackbox unit tests: `pytest packages/blackbox/`
25. Verify blackbox `pyproject.toml` does not list `shannon-whitebox`
26. Verify `AuditSession` and `LogStream` remain in `shannon_whitebox.audit`
27. Grep for stale imports: `grep -r "from shannon_whitebox.agents\|from shannon_whitebox.prompts\|from shannon_whitebox.services\|from shannon_whitebox.session\|from shannon_whitebox.git_manager" packages/` — should return nothing

## Scope

This spec covers ONLY the mechanical extraction of shared components from whitebox to core. It does NOT:

- Add new authentication functionality (sub-project 2)
- Design blackbox independent mode integration (sub-project 3)
- Modify any business logic or prompt templates
- Change the public API of any moved component
