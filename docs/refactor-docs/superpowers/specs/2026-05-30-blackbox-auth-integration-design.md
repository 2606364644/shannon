# Shannon-Py Blackbox Independent Auth Integration — Design Spec

## Overview

Sub-project 3 of 3. Integrates full authentication into the blackbox independent pipeline, completes deferred features from sub-project 2, and enables authenticated black-box scanning without any dependency on whitebox.

**Prerequisites:** Sub-project 1 (shared components in core) and Sub-project 2 (core auth functionality) must be complete.

## Scope

1. `_shared-session.txt` shared session restore partial
2. `VALIDATE_AUTH` agent name registration
3. Structured output validation (JSON schema for auth verdict)
4. Failure classification (`username_or_password` / `totp_secret` / `out_of_band`)
5. `login_flow` security validation (max length + dangerous pattern checks)
6. Blackbox independent pipeline auth integration

## Design

### 1. `_shared-session.txt` Shared Session Partial

**New file:** `shannon-py/prompts/shared/_shared-session.txt`

Identical to the TypeScript version:

```
<shared_authenticated_session>
The preflight already logged in and saved the authenticated browser
session to:

  {{AUTH_STATE_FILE}}

Restore it before doing anything else:

  playwright-cli -s={{PLAYWRIGHT_SESSION}} state-load {{AUTH_STATE_FILE}}

Then run verification (per the success_condition in your authentication
config) to confirm the restored session is still valid:

- If verification passes → SKIP the login flow below entirely and
  proceed with your primary task. You are authenticated.
- If verification fails → the saved session is stale. Fall through to
  the full login flow below and perform it on your own browser session.
  Do NOT overwrite {{AUTH_STATE_FILE}}.
</shared_authenticated_session>
```

Agent prompts that should `@include(shared/_shared-session.txt)` before their `<login_instructions>` block:

| Agent Prompt | Include Shared Session? | Reason |
|---|---|---|
| `recon-blackbox.txt` | Yes | Reuse preflight session for recon |
| `injection-exploit.txt` | Yes | Reuse session for exploitation |
| `xss-exploit.txt` | Yes | Reuse session |
| `ssrf-exploit.txt` | Yes | Reuse session |
| `authz-exploit.txt` | Yes | Reuse session |
| `auth-exploit.txt` | **No** | Owns its own login (tests auth itself) |

### 2. `VALIDATE_AUTH` Agent Registration

**File:** `shannon_core/models/agents.py`

Add to `AgentName` enum:

```python
VALIDATE_AUTH = "validate-authentication"
```

Add to `AGENTS` registry:

```python
AgentName.VALIDATE_AUTH: AgentDefinition(
    display_name="Authentication Validation",
    prerequisites=[],
    prompt_template="validate-authentication",
    deliverable_filename=None,
    model_tier="medium",
),
```

Add to `PLAYWRIGHT_SESSION_MAPPING`:

```python
AgentName.VALIDATE_AUTH: "agent1",
```

**Update:** `shannon_core/services/validate_authentication.py` — change from `AgentName.PRE_RECON` to `AgentName.VALIDATE_AUTH`.

### 3. Structured Output Validation

**File:** `shannon_core/agents/executor.py` + `shannon_core/agents/runner.py`

Add optional `structured_output_schema` parameter to `run_claude_prompt()` and `AgentExecutor.execute()`:

```python
# runner.py
async def run_claude_prompt(
    prompt: str,
    repo_path: str,
    ...
    structured_output_schema: dict | None = None,
) -> ClaudeRunResult:
    # If schema provided, configure Claude SDK to return structured JSON
    ...
```

```python
# executor.py — pass through to runner
async def execute(
    self,
    ...
    structured_output_schema: dict | None = None,
) -> AgentMetrics:
    ...
    result = await run_claude_prompt(
        ...,
        structured_output_schema=structured_output_schema,
    )
```

**Auth validation schema** in `validate_authentication.py`:

```python
AUTH_VALIDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "login_success": {"type": "boolean"},
        "failure_point": {
            "type": "string",
            "enum": ["username_or_password", "totp_secret", "out_of_band"],
        },
        "failure_detail": {"type": "string", "maxLength": 250},
    },
    "required": ["login_success"],
}
```

### 4. Failure Classification

**File:** `shannon_core/services/validate_authentication.py`

Update `validate_authentication()` to use structured output:

```python
metrics = await executor.execute(
    agent_name=AgentName.VALIDATE_AUTH,
    repo_path=repo_path or "/tmp/shannon-auth-check",
    web_url=web_url,
    config_path=config_path,
    api_key=api_key,
    prompt_override="validate-authentication",
    prompt_variables={"AUTH_STATE_FILE": str(state_file)},
    structured_output_schema=AUTH_VALIDATION_SCHEMA,
)

# Classify structured output
if metrics.structured_output:
    verdict = metrics.structured_output
    if verdict.get("login_success"):
        return await verify_auth_state(state_file)
    else:
        failure_point = verdict.get("failure_point", "out_of_band")
        failure_detail = verdict.get("failure_detail", "Login failed without diagnostic")
        return AuthValidationResult(
            success=False,
            failure_point=failure_point,
            failure_detail=failure_detail,
        )

# Fallback: if no structured output, rely on auth-state verification
return await verify_auth_state(state_file)
```

`AgentMetrics` model needs extending to carry structured output:

```python
# shannon_core/models/metrics.py
class AgentMetrics(BaseModel):
    ...
    structured_output: dict | None = None  # New field
```

### 5. `login_flow` Security Validation

**File:** `shannon_core/config/parser.py`

Add validation function:

```python
def _validate_login_flow(authentication: Authentication) -> None:
    if not authentication.login_flow:
        return
    for i, step in enumerate(authentication.login_flow):
        if len(step) > 500:
            raise ValueError(f"login_flow step {i + 1} exceeds 500 characters")
        _check_dangerous_patterns(step, f"login_flow step {i + 1}")
```

Call in `parse_config()` after existing security validation:

```python
if config.authentication:
    _validate_login_flow(config.authentication)
```

### 6. Blackbox Independent Pipeline Auth Integration

#### Pipeline Flow (with authentication)

```
1. Preflight
   - Validate target URL reachable
   - Parse config (including authentication)

2. Auth Validation (conditional)
   - Only when config.authentication exists
   - Run VALIDATE_AUTH agent with Playwright
   - Save auth-state.json to workspace
   - Verify auth-state (cookies/origins non-empty)
   - On failure → terminate pipeline with specific error

3. Recon
   - RECON_BLACKBOX agent
   - With auth: prompt includes @include(shared/_shared-session.txt)
     → restores preflight session, recon with authenticated state
   - Without auth: standard recon

4. Exploitation (5 parallel agents)
   - Each agent prompt includes @include(shared/_shared-session.txt)
     (except auth-exploit which owns its own login)
   - Restore preflight session before exploitation
   - Each agent uses isolated Playwright session

5. Report
   - Assemble report
   - Cleanup auth-state.json
```

#### File Changes

**`shannon_blackbox/pipeline/shared.py`:**

Ensure `BlackboxPipelineInput` and `ActivityInput` have `workspace_path` field:

```python
@dataclass
class BlackboxPipelineInput:
    web_url: str
    workspace_name: str | None = None
    config_path: str | None = None
    output_path: str | None = None
    ...
    workspace_path: str | None = None  # Resolved workspace directory

@dataclass
class BlackboxActivityInput:
    web_url: str
    config_path: str | None = None
    workspace_path: str | None = None
    api_key: str | None = None
    ...
```

**`shannon_blackbox/pipeline/workflows.py`:**

```python
# 2. Auth Validation
if input.config_path:
    auth_result = await workflow.execute_activity(
        activities.run_blackbox_auth_validation,
        act_input,
        start_to_close_timeout=timedelta(minutes=5),
    )
    if not auth_result.success:
        state.error = f"Authentication failed: {auth_result.failure_detail}"
        state.status = "failed"
        return state

# ... recon / exploit / report phases ...

# Cleanup in finally block
finally:
    from shannon_core.services.validate_authentication import cleanup_auth_state
    if state.workspace_path:
        await cleanup_auth_state(state.workspace_path)
```

**`shannon_blackbox/pipeline/activities.py`:**

Update `run_blackbox_auth_validation` to pass `workspace_path`:

```python
async def run_blackbox_auth_validation(input: BlackboxActivityInput) -> AuthValidationResult:
    from shannon_core.services.validate_authentication import validate_authentication
    from shannon_core.agents.executor import AgentExecutor
    from shannon_core.prompts.manager import PromptManager

    prompt_manager = PromptManager(...)
    executor = AgentExecutor(prompt_manager=prompt_manager)

    return await validate_authentication(
        web_url=input.web_url,
        config_path=input.config_path,
        workspace_path=input.workspace_path,
        prompt_manager=prompt_manager,
        executor=executor,
        api_key=input.api_key,
    )
```

**Blackbox agent prompts** — add `@include(shared/_shared-session.txt)`:

For each of these prompt files (in `shannon-py/prompts/`):
- `recon-blackbox.txt` — add `@include(shared/_shared-session.txt)` before any `<login_instructions>` block
- `injection-exploit.txt` — add include
- `xss-exploit.txt` — add include
- `ssrf-exploit.txt` — add include
- `authz-exploit.txt` — add include
- `auth-exploit.txt` — do NOT add include (owns its own auth)

## Files Changed

| File | Change |
|---|---|
| `prompts/shared/_shared-session.txt` | **New** — shared session restore partial |
| `shannon_core/models/agents.py` | Add `VALIDATE_AUTH` enum value, `AGENTS` entry, `PLAYWRIGHT_SESSION_MAPPING` entry |
| `shannon_core/models/metrics.py` | Add `structured_output` field to `AgentMetrics` |
| `shannon_core/agents/executor.py` | Add `structured_output_schema` parameter passthrough |
| `shannon_core/agents/runner.py` | Add `structured_output_schema` parameter, integrate with Claude SDK |
| `shannon_core/services/validate_authentication.py` | Use `VALIDATE_AUTH`, structured output, failure classification |
| `shannon_core/config/parser.py` | Add `_validate_login_flow()` |
| `shannon_blackbox/pipeline/shared.py` | Add `workspace_path` to input dataclasses |
| `shannon_blackbox/pipeline/workflows.py` | Auth validation phase, cleanup in finally |
| `shannon_blackbox/pipeline/activities.py` | Pass `workspace_path`, use `VALIDATE_AUTH` |
| `prompts/recon-blackbox.txt` | Add `@include(shared/_shared-session.txt)` |
| `prompts/injection-exploit.txt` | Add include |
| `prompts/xss-exploit.txt` | Add include |
| `prompts/ssrf-exploit.txt` | Add include |
| `prompts/authz-exploit.txt` | Add include |

## Dependency Chain

```
Sub-project 1 (shared components → core)
    ↓
Sub-project 2 (core auth functionality)
    ↓
Sub-project 3 (this spec — blackbox integration + deferred features)
```

Each sub-project has its own spec → plan → implementation cycle. Sub-project 3 can only begin after 1 and 2 are complete and tested.

## `__init__.py` Updates

### `shannon_core/models/__init__.py`

Ensure `VALIDATE_AUTH` is re-exported:

```python
from shannon_core.models.agents import AgentName  # VALIDATE_AUTH now in enum
```

### `shannon_core/models/metrics.py`

Add `structured_output` to `AgentMetrics`:

```python
class AgentMetrics(BaseModel):
    ...  # existing fields
    structured_output: dict | None = None
```

## Whitebox Auth Activity Updates

`shannon_whitebox/pipeline/activities.py` has a `run_auth_validation` activity that also needs the same updates as the blackbox activity:

1. Pass `workspace_path` parameter to `validate_authentication()`
2. Use structured output for failure classification
3. Handle `AuthValidationResult` failure cases

Additionally, `shannon_whitebox/pipeline/shared.py` must ensure `ActivityInput` has a `workspace_path` field.

## Whitebox Agent Prompts: `_shared-session.txt` Include

The following whitebox agent prompts should add `@include(shared/_shared-session.txt)` — matching the TypeScript project:

| Agent Prompt | Include? | Reason |
|---|---|---|
| `recon.txt` | Yes | Reuse preflight session |
| `vuln-injection.txt` | Yes | Reuse session |
| `vuln-xss.txt` | Yes | Reuse session |
| `vuln-ssrf.txt` | Yes | Reuse session |
| `vuln-authz.txt` | Yes | Reuse session |
| `vuln-auth.txt` | **No** | Owns its own login |
| `exploit-injection.txt` | Yes | Reuse session |
| `exploit-xss.txt` | Yes | Reuse session |
| `exploit-ssrf.txt` | Yes | Reuse session |
| `exploit-authz.txt` | Yes | Reuse session |
| `exploit-auth.txt` | **No** | Owns its own login |

## Testing Strategy

| Test Type | Scope | Details |
|---|---|---|
| **Unit: `VALIDATE_AUTH` agent registration** | Core | Verify `AgentName.VALIDATE_AUTH` in enum, `AGENTS` registry entry exists, `PLAYWRIGHT_SESSION_MAPPING` returns `"agent1"` |
| **Unit: `AUTH_VALIDATION_SCHEMA`** | Core | Validate schema accepts valid verdicts and rejects invalid ones |
| **Unit: failure classification** | Core | Test each failure point (`username_or_password`, `totp_secret`, `out_of_band`), test fallback to `verify_auth_state` when no structured output |
| **Unit: `_validate_login_flow()`** | Core | Test step > 500 chars raises, test dangerous patterns detected, test valid flow passes |
| **Unit: `_shared-session.txt` prompt processing** | Core | Verify `@include(shared/_shared-session.txt)` resolves correctly, `{{AUTH_STATE_FILE}}` and `{{PLAYWRIGHT_SESSION}}` interpolated |
| **Unit: `AgentMetrics.structured_output`** | Core | Test model accepts dict, None, and validates type |
| **Integration: blackbox pipeline auth flow** | Blackbox | Mock executor, run full pipeline: auth validation → recon (with session) → exploit → report → cleanup |
| **Integration: auth validation failure** | Blackbox | Verify pipeline terminates on auth failure with correct error message |
| **Integration: no-auth pipeline** | Blackbox | Verify pipeline runs normally when no authentication configured (skip auth validation phase) |
| **Integration: whitebox pipeline auth** | Whitebox | Verify whitebox pipeline also gets structured output + failure classification |
| **Prompt integration** | Both | Load each agent prompt with auth config, verify `_shared-session.txt` included, `{{LOGIN_INSTRUCTIONS}}` populated, `<shared_authenticated_session>` block present |
| **Regression: `@include` without auth** | Both | Load prompts without auth config, verify `<shared_authenticated_session>` block removed, no `_shared-session.txt` included |
