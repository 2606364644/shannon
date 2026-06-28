# Shannon-Py Authentication Functionality — Design Spec

## Overview

Sub-project 2 of 3. Completes the core authentication functionality in the Python shannon-py project to align with the TypeScript Shannon implementation. This spec covers 6 core auth features; deferred features (structured output validation, failure classification, `_shared-session.txt` creation, `login_flow` security validation) will be addressed in sub-project 3.

**Prerequisite:** Sub-project 1 (shared components extracted to core) must be complete.

## Gap Analysis

Current Python state vs TypeScript:

| # | Gap | TS Implementation | Python Status |
|---|---|---|---|
| 1 | `email_login` field | `Credentials.email_login: EmailLogin` | Missing |
| 2 | `buildLoginInstructions()` | Section-based template assembly from `login-instructions.txt` | Missing — `{{LOGIN_INSTRUCTIONS}}` always empty |
| 3 | `buildAuthContext()` | Full context (type + username + URL + MFA) | Minimal — only `f"Login type: {type}"` |
| 4 | auth-state verification | File exists, valid JSON, cookies/origins > 0 | Not implemented — always returns success |
| 5 | `<shared_authenticated_session>` block | Conditional keep/remove based on auth config | Not handled |
| 6 | auth-state cleanup | Delete on workflow completion | Not implemented |

## Design

### 1. `email_login` Model

**File:** `shannon_core/models/config.py`

Add `EmailLogin` model and extend `Credentials`:

```python
class EmailLogin(BaseModel):
    address: str
    password: str
    totp_secret: str | None = None

class Credentials(BaseModel):
    username: str
    password: str | None = None
    totp_secret: str | None = None
    email_login: EmailLogin | None = None
```

Matches TS `EmailLogin` interface exactly.

### 2. `buildLoginInstructions()`

**File:** `shannon_core/prompts/manager.py`

Add method to `PromptManager`:

```python
def build_login_instructions(self, authentication: Authentication) -> str:
    """Assemble login instructions from the shared template based on login_type."""
    template_path = self.prompts_dir / "shared" / "login-instructions.txt"
    if not template_path.exists():
        raise PentestError(
            f"Login instructions template not found: {template_path}",
            "prompt",
            error_code=ErrorCode.PROMPT_LOAD_FAILED,
        )

    full_template = template_path.read_text(encoding="utf-8")

    def get_section(content: str, section_name: str) -> str:
        pattern = rf"<!-- BEGIN:{section_name} -->([\s\S]*?)<!-- END:{section_name} -->"
        match = re.search(pattern, content)
        return match.group(1).strip() if match else ""

    login_type = authentication.login_type.upper()
    common = get_section(full_template, "COMMON")
    auth_section = get_section(full_template, login_type)  # FORM or SSO
    verification = get_section(full_template, "VERIFICATION")

    if not common and not auth_section and not verification:
        login_instructions = full_template
    else:
        login_instructions = "\n\n".join(filter(None, [common, auth_section, verification]))

    # Interpolate credential placeholders in login_flow steps
    user_instructions = "\n".join(authentication.login_flow or [])
    creds = authentication.credentials

    if creds:
        user_instructions = user_instructions.replace("$username", creds.username)
        if creds.password:
            user_instructions = user_instructions.replace(
                "$password", creds.password
            )
        if creds.totp_secret:
            user_instructions = user_instructions.replace(
                "$totp", f'generated TOTP code using secret "{creds.totp_secret}"'
            )
        if creds.email_login:
            user_instructions = user_instructions.replace(
                "$email_address", creds.email_login.address
            )
            user_instructions = user_instructions.replace(
                "$email_password", creds.email_login.password
            )
            if creds.email_login.totp_secret:
                user_instructions = user_instructions.replace(
                    "$email_totp",
                    f'generated TOTP code using secret "{creds.email_login.totp_secret}"',
                )

    login_instructions = login_instructions.replace("{{user_instructions}}", user_instructions)

    if creds and creds.totp_secret:
        login_instructions = login_instructions.replace("{{totp_secret}}", creds.totp_secret)

    return login_instructions
```

**`_interpolate()` change** — replace the current stub (line 104):

```python
# Before:
result = result.replace("{{LOGIN_INSTRUCTIONS}}", "")

# After:
if config and config.authentication and config.authentication.login_flow:
    login_instructions = self.build_login_instructions(config.authentication)
    result = result.replace("{{LOGIN_INSTRUCTIONS}}", login_instructions)
else:
    result = result.replace("{{LOGIN_INSTRUCTIONS}}", "")
```

### 3. `buildAuthContext()` Enhancement

**File:** `shannon_core/prompts/manager.py`

Add private method and update `_interpolate()`:

```python
def _build_auth_context(self, config: DistributedConfig) -> str:
    if not config.authentication:
        return "No authentication configured - unauthenticated testing only"
    auth = config.authentication
    lines = [
        f"- Login type: {auth.login_type.upper()}",
        f"- Username: {auth.credentials.username}",
        f"- Login URL: {auth.login_url}",
    ]
    if auth.credentials.totp_secret:
        lines.append("- MFA: TOTP enabled")
    return "\n".join(lines)
```

Replace the inline `{{AUTH_CONTEXT}}` replacement in `_interpolate()`:

```python
# Before:
result = result.replace("{{AUTH_CONTEXT}}", "No authentication configured" if not config.authentication else f"Login type: {config.authentication.login_type}")

# After:
result = result.replace("{{AUTH_CONTEXT}}", self._build_auth_context(config))
```

### 4. `<shared_authenticated_session>` Block Handling

**File:** `shannon_core/prompts/manager.py`

Add to `_interpolate()`, after the `{{LOGIN_INSTRUCTIONS}}` replacement:

```python
if not (config and config.authentication):
    result = re.sub(
        r"<shared_authenticated_session>[\s\S]*?</shared_authenticated_session>\s*",
        "",
        result,
    )
```

When authentication IS configured, the block stays and `{{AUTH_STATE_FILE}}` is already replaced by the generic variable pass-through (lines 106-109).

### 5. auth-state Verification

**File:** `shannon_core/services/validate_authentication.py`

Replace the current stub with real verification logic:

```python
import json
from pathlib import Path

from shannon_core.models.agents import AgentName
from shannon_core.models.config import Authentication
from shannon_core.utils.file_io import async_path_exists, async_read_file


def auth_state_path(workspace_path: str | Path) -> Path:
    return Path(workspace_path) / "auth-state.json"


async def cleanup_auth_state(workspace_path: str | Path) -> None:
    state_file = auth_state_path(workspace_path)
    if await async_path_exists(state_file):
        import aiofiles.os
        await aiofiles.os.remove(state_file)


async def verify_auth_state(state_file: Path) -> AuthValidationResult:
    """Verify the auth-state.json file was saved correctly."""
    if not await async_path_exists(state_file):
        return AuthValidationResult(
            success=False,
            failure_point="out_of_band",
            failure_detail=f"Agent did not save auth state to {state_file}",
        )

    contents = await async_read_file(state_file)
    try:
        parsed = json.loads(contents)
    except json.JSONDecodeError as e:
        return AuthValidationResult(
            success=False,
            failure_point="out_of_band",
            failure_detail=f"Auth state file is not valid JSON: {e}",
        )

    cookie_count = len(parsed.get("cookies", []))
    origin_count = len(parsed.get("origins", []))
    if cookie_count == 0 and origin_count == 0:
        return AuthValidationResult(
            success=False,
            failure_point="out_of_band",
            failure_detail="Auth state contains no cookies or origins — browser was not actually logged in",
        )

    return AuthValidationResult(success=True)


async def validate_authentication(
    *,
    web_url: str,
    config_path: str | None,
    workspace_path: str,
    prompt_manager: PromptManager,
    executor: AgentExecutor,
    repo_path: str = "",
    api_key: str | None = None,
) -> AuthValidationResult:
    # 1. Parse config and check for authentication
    if not config_path:
        return AuthValidationResult(success=True)

    try:
        from shannon_core.config.parser import parse_config, distribute_config
        config = parse_config(config_path)
        dist_config = distribute_config(config)
    except Exception:
        return AuthValidationResult(success=True)

    if not dist_config.authentication:
        return AuthValidationResult(success=True)

    # 2. Delete stale auth-state file from prior run
    state_file = auth_state_path(workspace_path)
    await cleanup_auth_state(workspace_path)

    # 3. Execute validate-authentication agent
    metrics = await executor.execute(
        agent_name=AgentName.PRE_RECON,  # Borrow — actual prompt overridden
        repo_path=repo_path or "/tmp/shannon-auth-check",
        web_url=web_url,
        config_path=config_path,
        api_key=api_key,
        prompt_override="validate-authentication",
        prompt_variables={"AUTH_STATE_FILE": str(state_file)},
    )

    # 4. Verify auth-state was saved correctly
    return await verify_auth_state(state_file)
```

Key changes from current stub:
- New `workspace_path` parameter for auth-state file location
- Deletes stale auth-state before running agent
- Verifies file exists, is valid JSON, and contains cookies/origins
- `cleanup_auth_state()` utility for both pre-cleanup and post-workflow cleanup

### 6. auth-state Cleanup on Workflow Completion

**Files:**
- `shannon_whitebox/pipeline/workflows.py`
- `shannon_blackbox/pipeline/workflows.py`

In each workflow's completion/cleanup phase, add:

```python
from shannon_core.services.validate_authentication import cleanup_auth_state

# In the finally block or workflow completion handler:
await cleanup_auth_state(workspace_path)
```

## Breaking Changes

`validate_authentication()` signature changes — adds required `workspace_path` parameter. All callers must be updated:

| Caller File | Change |
|---|---|
| `shannon_whitebox/pipeline/activities.py` | Pass `workspace_path` to `validate_authentication()` |
| `shannon_blackbox/pipeline/activities.py` | Pass `workspace_path` to `validate_authentication()` |

## Files Changed

| File | Change |
|---|---|
| `shannon_core/models/config.py` | Add `EmailLogin` model, extend `Credentials` |
| `shannon_core/prompts/manager.py` | Add `build_login_instructions()`, `_build_auth_context()`, `<shared_authenticated_session>` block handling |
| `shannon_core/services/validate_authentication.py` | Full rewrite: add `auth_state_path()`, `cleanup_auth_state()`, `verify_auth_state()`, real validation logic |
| `shannon_whitebox/pipeline/workflows.py` | Add auth-state cleanup on completion |
| `shannon_blackbox/pipeline/workflows.py` | Add auth-state cleanup on completion |

## Not In Scope (Deferred to Sub-project 3)

- Structured output validation (Zod schema equivalent for Claude SDK)
- Failure classification (username_or_password / totp_secret / out_of_band via structured output)
- `_shared-session.txt` shared partial creation
- `login_flow` security validation (max length checks per step)
- `VALIDATE_AUTH` agent name registration (currently borrows PRE_RECON)

## `__init__.py` Updates

### `shannon_core/models/__init__.py`

Add re-export for the new `EmailLogin` model:

```python
from shannon_core.models.config import EmailLogin
```

### `shannon_core/services/__init__.py`

Ensure new functions are re-exported:

```python
from shannon_core.services.validate_authentication import (
    AuthValidationResult,
    validate_authentication,
    auth_state_path,
    cleanup_auth_state,
    verify_auth_state,
)
```

## Whitebox Auth Activity Update

`shannon_whitebox/pipeline/activities.py` has a `run_auth_validation` activity that calls `validate_authentication()`. It must pass the new `workspace_path` parameter:

```python
async def run_auth_validation(input: ActivityInput) -> AuthValidationResult:
    return await validate_authentication(
        web_url=input.web_url,
        config_path=input.config_path,
        workspace_path=input.workspace_path,  # NEW
        prompt_manager=...,
        executor=...,
        api_key=input.api_key,
    )
```

`ActivityInput` in `shannon_whitebox/pipeline/shared.py` must also gain a `workspace_path` field if it doesn't already have one.

## Whitebox Pipeline Auth Cleanup

`shannon_whitebox/pipeline/workflows.py` already has auth validation in its pipeline. Add auth-state cleanup in its completion/finally block:

```python
# In the finally block or workflow completion handler:
from shannon_core.services.validate_authentication import cleanup_auth_state
if workspace_path:
    await cleanup_auth_state(workspace_path)
```

## Whitebox Agent Prompts: `_shared-session.txt` Include

The following whitebox agent prompts (in `shannon-py/prompts/`) should include `@include(shared/_shared-session.txt)` before their `<login_instructions>` block — matching the TypeScript project's behavior:

| Agent Prompt | Include? | Reason |
|---|---|---|
| `recon.txt` | Yes | Reuse preflight session for recon |
| `vuln-injection.txt` | Yes | Reuse session |
| `vuln-xss.txt` | Yes | Reuse session |
| `vuln-ssrf.txt` | Yes | Reuse session |
| `vuln-authz.txt` | Yes | Reuse session |
| `vuln-auth.txt` | **No** | Owns its own login (tests auth vulnerabilities) |
| `exploit-injection.txt` | Yes | Reuse session |
| `exploit-xss.txt` | Yes | Reuse session |
| `exploit-ssrf.txt` | Yes | Reuse session |
| `exploit-authz.txt` | Yes | Reuse session |
| `exploit-auth.txt` | **No** | Owns its own login |

Note: `_shared-session.txt` file creation is in sub-project 3, so these include additions should be done as part of sub-project 3 implementation. Listed here for completeness.

## Testing Strategy

| Test Type | Scope | Details |
|---|---|---|
| **Unit: `EmailLogin` model** | Core | Parse YAML with `email_login` field, validate required/optional fields |
| **Unit: `buildLoginInstructions()`** | Core | Test with each `login_type` (form, sso), test section extraction, test credential interpolation |
| **Unit: `buildAuthContext()`** | Core | Test with/without auth config, with/without TOTP |
| **Unit: `<shared_authenticated_session>` block handling** | Core | Test block removal when no auth, block preservation when auth present |
| **Unit: `verify_auth_state()`** | Core | Test with missing file, invalid JSON, empty cookies/origins, valid state |
| **Unit: `cleanup_auth_state()`** | Core | Test with existing file (deleted) and non-existing file (no-op) |
| **Integration: `validate_authentication()`** | Core | Full flow with mocked executor: stale cleanup → agent execution → state verification |
| **Integration: prompt interpolation** | Core | Load a real prompt template, verify `{{LOGIN_INSTRUCTIONS}}` is populated when auth + login_flow present |
| **Regression: no auth** | Core | Verify all functions handle the no-authentication case gracefully (return early/skip) |
