# Blackbox Auth Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate full authentication into the blackbox independent pipeline by adding structured output validation, failure classification, login_flow security validation, shared session restore partials, and the VALIDATE_AUTH agent — enabling authenticated black-box scanning with no whitebox dependency.

**Architecture:** The blackbox pipeline already has auth validation and cleanup in its workflow. This plan adds the remaining deferred features: a dedicated VALIDATE_AUTH agent (instead of borrowing PRE_RECON), structured JSON output for auth verdicts with failure classification, login_flow security validation in the config parser, and a shared session restore partial (`_shared-session.txt`) included by agent prompts so they reuse the preflight-authenticated browser session.

**Tech Stack:** Python 3.12+, Pydantic, pytest + pytest-asyncio, Temporal.io

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `packages/core/src/shannon_core/models/agents.py` | Modify | Add `VALIDATE_AUTH` enum value, AGENTS entry, update PLAYWRIGHT_SESSION_MAPPING |
| `packages/core/src/shannon_core/models/metrics.py` | Modify | Add `structured_output: dict \| None = None` to `AgentMetrics` |
| `packages/core/src/shannon_core/agents/runner.py` | Modify | Add `structured_output_schema` parameter |
| `packages/core/src/shannon_core/agents/executor.py` | Modify | Add `structured_output_schema` passthrough, propagate `structured_output` to `AgentMetrics` |
| `packages/core/src/shannon_core/services/validate_authentication.py` | Modify | Use `VALIDATE_AUTH`, `AUTH_VALIDATION_SCHEMA`, structured output, failure classification |
| `packages/core/src/shannon_core/config/parser.py` | Modify | Add `_validate_login_flow()` security validation |
| `prompts/shared/_shared-session.txt` | Create | Shared session restore partial template |
| `prompts/recon-blackbox.txt` | Modify | Add `@include(shared/_shared-session.txt)` |
| `prompts/injection-exploit.txt` | Modify | Add include |
| `prompts/xss-exploit.txt` | Modify | Add include |
| `prompts/ssrf-exploit.txt` | Modify | Add include |
| `prompts/authz-exploit.txt` | Modify | Add include |
| `prompts/recon.txt` | Modify | Add include |
| `prompts/vuln-injection.txt` | Modify | Add include |
| `prompts/vuln-xss.txt` | Modify | Add include |
| `prompts/vuln-ssrf.txt` | Modify | Add include |
| `prompts/vuln-authz.txt` | Modify | Add include |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | Verify | Verify structured output flows (no code change needed) |

**Note:** The spec lists whitebox exploit prompts (`exploit-injection.txt`, `exploit-xss.txt`, etc.) for `_shared-session.txt` includes. These files do not exist on disk — the prompts directory only has the blackbox versions (`injection-exploit.txt`, etc.). The whitebox exploit prompts in the spec appear to reference the blackbox exploit prompts by another name. Task 7 covers the blackbox exploit prompts, Task 8 covers the whitebox vuln/recon prompts.
| `packages/core/tests/test_agents.py` | Modify | Add tests for VALIDATE_AUTH registration |
| `packages/core/tests/test_metrics.py` | Modify | Add tests for `structured_output` field |
| `packages/core/tests/test_parser.py` | Modify | Add tests for `_validate_login_flow()` |
| `packages/core/tests/test_validate_authentication.py` | Modify | Add tests for structured output + failure classification |
| `packages/core/tests/test_prompt_manager.py` | Modify | Add tests for `_shared-session.txt` include processing |

---

## Task 1: Register VALIDATE_AUTH Agent

**Files:**
- Modify: `packages/core/src/shannon_core/models/agents.py:8-24` (enum) and `:26-33` (model) and `:35-151` (registry) and `:155` (mapping)
- Modify: `packages/core/tests/test_agents.py`

- [ ] **Step 1: Make `deliverable_filename` optional in `AgentDefinition`**

The spec requires `deliverable_filename=None` for `VALIDATE_AUTH`, but the current model declares `deliverable_filename: str` (required). Update the model in `packages/core/src/shannon_core/models/agents.py` line 32:

```python
class AgentDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: AgentName
    display_name: str
    prerequisites: list[AgentName]
    prompt_template: str
    deliverable_filename: str | None = None
    model_tier: Literal["small", "medium", "large"] = "medium"
```

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_agents.py -v`
Expected: ALL PASS (existing definitions all provide `deliverable_filename` explicitly, so the default doesn't matter)

- [ ] **Step 2: Write the failing test for VALIDATE_AUTH**

Add to `packages/core/tests/test_agents.py` at the end of the file:

```python
def test_validate_auth_agent_name():
    assert AgentName.VALIDATE_AUTH == "validate-authentication"


def test_validate_auth_in_registry():
    assert AgentName.VALIDATE_AUTH in AGENTS


def test_validate_auth_definition():
    defn = AGENTS[AgentName.VALIDATE_AUTH]
    assert defn.display_name == "Authentication Validation"
    assert defn.prerequisites == []
    assert defn.prompt_template == "validate-authentication"
    assert defn.deliverable_filename is None
    assert defn.model_tier == "medium"


def test_validate_auth_in_session_mapping():
    assert AgentName.VALIDATE_AUTH.value in PLAYWRIGHT_SESSION_MAPPING
    assert PLAYWRIGHT_SESSION_MAPPING[AgentName.VALIDATE_AUTH.value] == "agent1"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_agents.py::test_validate_auth_agent_name -v`
Expected: FAIL with `AttributeError: 'AgentName' has no attribute 'VALIDATE_AUTH'`

- [ ] **Step 4: Add VALIDATE_AUTH to enum**

In `packages/core/src/shannon_core/models/agents.py`, add after line 24 (`REPORT = "report"`):

```python
    VALIDATE_AUTH = "validate-authentication"
```

So lines 23-25 become:

```python
    MISCONFIG_EXPLOIT = "misconfig-exploit"
    REPORT = "report"
    VALIDATE_AUTH = "validate-authentication"
```

- [ ] **Step 5: Add VALIDATE_AUTH to AGENTS registry**

In the same file, add after the REPORT entry (after line 150), before the closing `}`:

```python
    AgentName.VALIDATE_AUTH: AgentDefinition(
        name=AgentName.VALIDATE_AUTH,
        display_name="Authentication Validation",
        prerequisites=[],
        prompt_template="validate-authentication",
        deliverable_filename=None,
        model_tier="medium",
    ),
```

- [ ] **Step 6: Override PLAYWRIGHT_SESSION_MAPPING for VALIDATE_AUTH**

Line 155 uses a dict comprehension over all `AgentName` values:

```python
PLAYWRIGHT_SESSION_MAPPING: dict[str, str] = {name.value: f"agent{i}" for i, name in enumerate(AgentName, 1)}
```

This auto-generates sequential mappings (`agent1`, `agent2`, ...). Since `VALIDATE_AUTH` is added at the end, it would get a high number (e.g. `agent17`), but the spec requires `"agent1"`. Add an override after the comprehension:

```python
PLAYWRIGHT_SESSION_MAPPING: dict[str, str] = {
    name.value: f"agent{i}" for i, name in enumerate(AgentName, 1)
}
# VALIDATE_AUTH shares agent1 slot (same browser session as preflight)
PLAYWRIGHT_SESSION_MAPPING[AgentName.VALIDATE_AUTH.value] = "agent1"
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_agents.py -v`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/shannon_core/models/agents.py packages/core/tests/test_agents.py
git commit -m "feat(core): register VALIDATE_AUTH agent with dedicated enum, registry entry, and session mapping"
```

---

## Task 2: Add structured_output Field to AgentMetrics

**Files:**
- Modify: `packages/core/src/shannon_core/models/metrics.py:3-9`
- Modify: `packages/core/tests/test_metrics.py`

- [ ] **Step 1: Write the failing test**

Add to `packages/core/tests/test_metrics.py` at the end:

```python
def test_agent_metrics_structured_output_none():
    from shannon_core.models.metrics import AgentMetrics
    m = AgentMetrics(duration_ms=100)
    assert m.structured_output is None


def test_agent_metrics_structured_output_dict():
    from shannon_core.models.metrics import AgentMetrics
    m = AgentMetrics(
        duration_ms=100,
        structured_output={"login_success": True},
    )
    assert m.structured_output == {"login_success": True}


def test_agent_metrics_structured_output_nested():
    from shannon_core.models.metrics import AgentMetrics
    data = {
        "login_success": False,
        "failure_point": "totp_secret",
        "failure_detail": "TOTP code rejected",
    }
    m = AgentMetrics(duration_ms=200, structured_output=data)
    assert m.structured_output["failure_point"] == "totp_secret"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_metrics.py::test_agent_metrics_structured_output_none -v`
Expected: FAIL with `ValidationError: Extra inputs are not permitted` (or similar)

- [ ] **Step 3: Add structured_output field**

In `packages/core/src/shannon_core/models/metrics.py`, add the field to `AgentMetrics`:

```python
class AgentMetrics(BaseModel):
    duration_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    model: str | None = None
    structured_output: dict | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_metrics.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/models/metrics.py packages/core/tests/test_metrics.py
git commit -m "feat(core): add structured_output field to AgentMetrics for schema-validated agent responses"
```

---

## Task 3: Add structured_output_schema to Runner and Executor

**Files:**
- Modify: `packages/core/src/shannon_core/agents/runner.py:16-28`
- Modify: `packages/core/src/shannon_core/agents/executor.py:21-98`
- Modify: `packages/core/tests/test_runner.py`

- [ ] **Step 1: Write the failing test**

Add to `packages/core/tests/test_runner.py` at the end:

```python
def test_run_claude_prompt_accepts_structured_output_schema():
    """Verify run_claude_prompt signature accepts structured_output_schema parameter."""
    import inspect
    from shannon_core.agents.runner import run_claude_prompt
    sig = inspect.signature(run_claude_prompt)
    assert "structured_output_schema" in sig.parameters
    assert sig.parameters["structured_output_schema"].default is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_runner.py::test_run_claude_prompt_accepts_structured_output_schema -v`
Expected: FAIL with `AssertionError` (parameter not in signature)

- [ ] **Step 3: Add structured_output_schema to run_claude_prompt**

In `packages/core/src/shannon_core/agents/runner.py`, add the parameter:

```python
async def run_claude_prompt(
    prompt: str,
    repo_path: str,
    model_tier: str = "medium",
    output_format: dict | None = None,
    structured_output_schema: dict | None = None,
    api_key: str | None = None,
    deliverables_subdir: str | None = None,
    provider_config: dict | None = None,
) -> ClaudeRunResult:
    raise NotImplementedError(
        "Claude Agent SDK Python integration pending. "
        "Install claude-agent-sdk and implement this function."
    )
```

- [ ] **Step 4: Add structured_output_schema to AgentExecutor.execute**

In `packages/core/src/shannon_core/agents/executor.py`, update the `execute` method signature (line 21-32):

```python
    async def execute(
        self,
        agent_name: AgentName,
        repo_path: str,
        web_url: str = "",
        deliverables_path: str | None = None,
        config_path: str | None = None,
        api_key: str | None = None,
        pipeline_testing: bool = False,
        prompt_variables: dict[str, str] | None = None,
        prompt_override: str | None = None,
        structured_output_schema: dict | None = None,
    ) -> AgentMetrics:
```

Then update the `run_claude_prompt` call (line 57-63) to pass the schema:

```python
        result = await run_claude_prompt(
            prompt=prompt,
            repo_path=str(repo),
            model_tier=defn.model_tier,
            api_key=api_key,
            deliverables_subdir=str(deliverables.relative_to(repo)) if deliverables.is_relative_to(repo) else None,
            structured_output_schema=structured_output_schema,
        )
```

Then update the `AgentMetrics` return (line 93-98) to include `structured_output`:

```python
        return AgentMetrics(
            duration_ms=duration_ms,
            cost_usd=result.cost,
            num_turns=result.turns,
            model=result.model,
            structured_output=result.structured_output,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_runner.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/agents/runner.py packages/core/src/shannon_core/agents/executor.py packages/core/tests/test_runner.py
git commit -m "feat(core): add structured_output_schema passthrough to runner and executor"
```

---

## Task 4: Add login_flow Security Validation

**Files:**
- Modify: `packages/core/src/shannon_core/config/parser.py:33-49`
- Modify: `packages/core/tests/test_parser.py`

- [ ] **Step 1: Write the failing test**

Add to `packages/core/tests/test_parser.py` at the end:

```python
import tempfile
import pytest
from shannon_core.config.parser import parse_config
from shannon_core.models.errors import PentestError


def _write_config(tmp_path: Path, content: str) -> str:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(content, encoding="utf-8")
    return str(config_file)


def test_login_flow_step_exceeds_max_length(tmp_path):
    """A login_flow step > 500 characters raises PentestError."""
    long_step = "A" * 501
    config_path = _write_config(tmp_path, f"""
authentication:
  login_type: form
  login_url: https://example.com/login
  credentials:
    username: admin
    password: pass123
  login_flow:
    - "{long_step}"
  success_condition:
    type: url_contains
    value: /dashboard
""")
    with pytest.raises(PentestError, match="login_flow step 1 exceeds 500 characters"):
        parse_config(config_path)


def test_login_flow_step_dangerous_pattern(tmp_path):
    """A login_flow step with < or > raises PentestError."""
    config_path = _write_config(tmp_path, """
authentication:
  login_type: form
  login_url: https://example.com/login
  credentials:
    username: admin
    password: pass123
  login_flow:
    - "Click the <script>alert(1)</script> button"
  success_condition:
    type: url_contains
    value: /dashboard
""")
    with pytest.raises(PentestError, match="login_flow step 1 contains potentially dangerous pattern"):
        parse_config(config_path)


def test_login_flow_step_path_traversal(tmp_path):
    """A login_flow step with ../ raises PentestError."""
    config_path = _write_config(tmp_path, """
authentication:
  login_type: form
  login_url: https://example.com/login
  credentials:
    username: admin
    password: pass123
  login_flow:
    - "Navigate to ../../etc/passwd"
  success_condition:
    type: url_contains
    value: /dashboard
""")
    with pytest.raises(PentestError, match="login_flow step 1 contains potentially dangerous pattern"):
        parse_config(config_path)


def test_login_flow_valid_steps_pass(tmp_path):
    """Valid login_flow steps under 500 chars with no dangerous patterns pass."""
    config_path = _write_config(tmp_path, """
authentication:
  login_type: form
  login_url: https://example.com/login
  credentials:
    username: admin
    password: pass123
  login_flow:
    - "Navigate to login page"
    - "Enter $username in username field"
    - "Click submit"
  success_condition:
    type: url_contains
    value: /dashboard
""")
    config = parse_config(config_path)
    assert config.authentication is not None
    assert len(config.authentication.login_flow) == 3


def test_login_flow_none_is_ok(tmp_path):
    """When login_flow is not set, validation passes."""
    config_path = _write_config(tmp_path, """
authentication:
  login_type: form
  login_url: https://example.com/login
  credentials:
    username: admin
    password: pass123
  success_condition:
    type: url_contains
    value: /dashboard
""")
    config = parse_config(config_path)
    assert config.authentication is not None
    assert config.authentication.login_flow is None


def test_login_flow_javascript_uri_rejected(tmp_path):
    """A login_flow step with javascript: URI raises PentestError."""
    config_path = _write_config(tmp_path, """
authentication:
  login_type: form
  login_url: https://example.com/login
  credentials:
    username: admin
    password: pass123
  login_flow:
    - "Navigate to javascript:alert(1)"
  success_condition:
    type: url_contains
    value: /dashboard
""")
    with pytest.raises(PentestError, match="login_flow step 1 contains potentially dangerous pattern"):
        parse_config(config_path)
```

Note: add `from pathlib import Path` to the imports in `test_parser.py` if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_parser.py::test_login_flow_step_exceeds_max_length -v`
Expected: FAIL — the test expects a `PentestError` but `_validate_login_flow` doesn't exist yet so parsing succeeds

- [ ] **Step 3: Add `_validate_login_flow()` to parser**

In `packages/core/src/shannon_core/config/parser.py`, add after `_validate_config_security` (after line 40):

```python
def _validate_login_flow(authentication: Authentication) -> None:
    """Validate login_flow steps for length and dangerous patterns."""
    if not authentication.login_flow:
        return
    for i, step in enumerate(authentication.login_flow):
        if len(step) > 500:
            raise PentestError(
                f"login_flow step {i + 1} exceeds 500 characters",
                "config",
                error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
            )
        _check_dangerous_patterns(step, f"login_flow step {i + 1}")
```

Then call it from `parse_config()` after `_validate_config_security(config)` (after line 96):

```python
    _validate_config_security(config)
    if config.authentication:
        _validate_login_flow(config.authentication)
```

Also add the import for `Authentication` at the top of the file. The existing imports from `shannon_core.models.config` are on lines 5-12. Add `Authentication` to that import:

```python
from shannon_core.models.config import (
    ALL_VULN_CLASSES,
    Authentication,
    Config,
    DistributedConfig,
    ReportConfig,
    Rule,
    VulnClass,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_parser.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/config/parser.py packages/core/tests/test_parser.py
git commit -m "feat(core): add _validate_login_flow() security validation for login_flow steps"
```

---

## Task 5: Create _shared-session.txt Shared Session Partial

**Files:**
- Create: `prompts/shared/_shared-session.txt`
- Modify: `packages/core/tests/test_prompt_manager.py`

- [ ] **Step 1: Write the failing test**

Add to `packages/core/tests/test_prompt_manager.py` at the end:

```python
def test_shared_session_include_resolves(prompts_dir):
    """@include(shared/_shared-session.txt) resolves when the file exists."""
    session_partial = (
        "<shared_authenticated_session>\n"
        "The preflight already logged in.\n"
        "Restore session: playwright-cli state-load {{AUTH_STATE_FILE}}\n"
        "</shared_authenticated_session>\n"
    )
    (prompts_dir / "shared" / "_shared-session.txt").write_text(session_partial)
    (prompts_dir / "with-session.txt").write_text(
        "Before\n@include(shared/_shared-session.txt)\nAfter\n"
    )
    auth = _make_auth()
    config = _make_dist_config(authentication=auth)
    manager = PromptManager(prompts_dir)
    result = manager.load_sync(
        "with-session",
        {"web_url": "https://example.com", "repo_path": "/r", "auth_state_file": "/tmp/auth-state.json"},
        config=config,
    )
    assert "shared_authenticated_session" in result
    assert "/tmp/auth-state.json" in result
    assert "Before" in result
    assert "After" in result


def test_shared_session_include_removed_without_auth(prompts_dir):
    """When no auth configured, the included session block is removed."""
    session_partial = (
        "<shared_authenticated_session>\n"
        "Restore session: {{AUTH_STATE_FILE}}\n"
        "</shared_authenticated_session>\n"
    )
    (prompts_dir / "shared" / "_shared-session.txt").write_text(session_partial)
    (prompts_dir / "with-session.txt").write_text(
        "Before\n@include(shared/_shared-session.txt)\nAfter\n"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync(
        "with-session",
        {"web_url": "https://example.com", "repo_path": "/r"},
    )
    assert "shared_authenticated_session" not in result
    assert "Before" in result
    assert "After" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_prompt_manager.py::test_shared_session_include_resolves -v`
Expected: FAIL — `@include(shared/_shared-session.txt)` resolves to empty string because file doesn't exist yet

- [ ] **Step 3: Create the _shared-session.txt file**

Create `prompts/shared/_shared-session.txt`:

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_prompt_manager.py::test_shared_session_include_resolves packages/core/tests/test_prompt_manager.py::test_shared_session_include_removed_without_auth -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add prompts/shared/_shared-session.txt packages/core/tests/test_prompt_manager.py
git commit -m "feat(prompts): create _shared-session.txt shared session restore partial"
```

---

## Task 6: Update validate_authentication with Structured Output and Failure Classification

**Files:**
- Modify: `packages/core/src/shannon_core/services/validate_authentication.py`
- Modify: `packages/core/tests/test_validate_authentication.py`

- [ ] **Step 1: Write the failing test**

Add to `packages/core/tests/test_validate_authentication.py` at the end:

```python
# --- AUTH_VALIDATION_SCHEMA tests ---

def test_auth_validation_schema_constant():
    """AUTH_VALIDATION_SCHEMA has the expected structure."""
    from shannon_core.services.validate_authentication import AUTH_VALIDATION_SCHEMA
    assert AUTH_VALIDATION_SCHEMA["type"] == "object"
    assert "login_success" in AUTH_VALIDATION_SCHEMA["properties"]
    assert AUTH_VALIDATION_SCHEMA["properties"]["login_success"]["type"] == "boolean"
    assert "login_success" in AUTH_VALIDATION_SCHEMA["required"]
    fp = AUTH_VALIDATION_SCHEMA["properties"]["failure_point"]
    assert set(fp["enum"]) == {"username_or_password", "totp_secret", "out_of_band"}


# --- Structured output integration tests ---

@pytest.mark.asyncio
async def test_auth_validation_uses_validate_auth_agent(tmp_path):
    """validate_authentication uses AgentName.VALIDATE_AUTH, not PRE_RECON."""
    state_file = tmp_path / "auth-state.json"

    async def fake_execute(**kwargs):
        state_file.write_text(json.dumps({
            "cookies": [{"name": "session", "value": "abc"}],
            "origins": [],
        }))
        from shannon_core.models.metrics import AgentMetrics
        return AgentMetrics(
            duration_ms=5000,
            structured_output={"login_success": True},
        )

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()
    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}

    with patch("shannon_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("shannon_core.config.parser.distribute_config", return_value=mock_dist_config):
        from shannon_core.models.agents import AgentName
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    assert result.success is True
    call_kwargs = mock_executor.execute.call_args.kwargs
    assert call_kwargs["agent_name"] == AgentName.VALIDATE_AUTH
    assert call_kwargs.get("structured_output_schema") is not None


@pytest.mark.asyncio
async def test_auth_validation_structured_output_failure_username(tmp_path):
    """Structured output with login_success=False and failure_point=username_or_password."""
    async def fake_execute(**kwargs):
        from shannon_core.models.metrics import AgentMetrics
        return AgentMetrics(
            duration_ms=5000,
            structured_output={
                "login_success": False,
                "failure_point": "username_or_password",
                "failure_detail": "Invalid username or password",
            },
        )

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()
    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}

    with patch("shannon_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("shannon_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    assert result.success is False
    assert result.failure_point == "username_or_password"
    assert "Invalid username or password" in result.failure_detail


@pytest.mark.asyncio
async def test_auth_validation_structured_output_failure_totp(tmp_path):
    """Structured output with failure_point=totp_secret."""
    async def fake_execute(**kwargs):
        from shannon_core.models.metrics import AgentMetrics
        return AgentMetrics(
            duration_ms=5000,
            structured_output={
                "login_success": False,
                "failure_point": "totp_secret",
                "failure_detail": "TOTP code rejected",
            },
        )

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()
    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}

    with patch("shannon_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("shannon_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    assert result.success is False
    assert result.failure_point == "totp_secret"


@pytest.mark.asyncio
async def test_auth_validation_structured_output_failure_out_of_band(tmp_path):
    """Structured output with failure_point=out_of_band."""
    async def fake_execute(**kwargs):
        from shannon_core.models.metrics import AgentMetrics
        return AgentMetrics(
            duration_ms=5000,
            structured_output={
                "login_success": False,
                "failure_point": "out_of_band",
                "failure_detail": "Email verification required",
            },
        )

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()
    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}

    with patch("shannon_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("shannon_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    assert result.success is False
    assert result.failure_point == "out_of_band"


@pytest.mark.asyncio
async def test_auth_validation_fallback_when_no_structured_output(tmp_path):
    """When structured output is None, fall back to verify_auth_state."""
    state_file = tmp_path / "auth-state.json"

    async def fake_execute(**kwargs):
        # Simulate agent writing a valid state file
        state_file.write_text(json.dumps({
            "cookies": [{"name": "session", "value": "abc"}],
            "origins": [],
        }))
        from shannon_core.models.metrics import AgentMetrics
        return AgentMetrics(duration_ms=5000)  # No structured_output

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()
    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin"}

    with patch("shannon_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("shannon_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    # Falls back to verify_auth_state, which checks the file
    assert result.success is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_validate_authentication.py::test_auth_validation_schema_constant -v`
Expected: FAIL with `ImportError` (AUTH_VALIDATION_SCHEMA not exported yet)

- [ ] **Step 3: Add AUTH_VALIDATION_SCHEMA and update validate_authentication**

Replace the full content of `packages/core/src/shannon_core/services/validate_authentication.py` with:

```python
"""Authentication validation — verifies user-supplied credentials via browser login."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from shannon_core.models.agents import AgentName
from shannon_core.utils.file_io import async_path_exists, async_read_file

if TYPE_CHECKING:
    from shannon_core.agents.executor import AgentExecutor
    from shannon_core.prompts.manager import PromptManager


# Schema for structured output from the validate-authentication agent
AUTH_VALIDATION_SCHEMA: dict = {
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


@dataclass
class AuthValidationResult:
    success: bool
    failure_point: str | None = None  # "username_or_password" | "totp_secret" | "out_of_band"
    failure_detail: str | None = None


def auth_state_path(workspace_path: str | Path) -> Path:
    return Path(workspace_path) / "auth-state.json"


async def cleanup_auth_state(workspace_path: str | Path) -> None:
    state_file = auth_state_path(workspace_path)
    if await async_path_exists(state_file):
        import aiofiles.os
        await aiofiles.os.remove(state_file)


def cleanup_auth_state_sync(workspace_path: str | Path) -> None:
    """Synchronous version of cleanup_auth_state for use in workflow finally blocks."""
    state_file = auth_state_path(workspace_path)
    if state_file.exists():
        state_file.unlink()


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
    """Validate user-supplied credentials by running the validate-authentication agent.

    Returns ``AuthValidationResult(success=True)`` when no auth config is present
    (nothing to validate) or when the agent confirms successful login.
    """
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

    # 3. Execute validate-authentication agent with structured output schema
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

    # 4. Classify structured output
    if metrics.structured_output is not None:
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

    # 5. Fallback: if no structured output, rely on auth-state verification
    return await verify_auth_state(state_file)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_validate_authentication.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/services/validate_authentication.py packages/core/tests/test_validate_authentication.py
git commit -m "feat(core): add AUTH_VALIDATION_SCHEMA, structured output, and failure classification to validate_authentication"
```

---

## Task 7: Add _shared-session.txt Includes to Blackbox Agent Prompts

**Files:**
- Modify: `prompts/recon-blackbox.txt:16` (before `<context>`)
- Modify: `prompts/injection-exploit.txt:10` (after `@include(shared/_exploit-scope.txt)`)
- Modify: `prompts/xss-exploit.txt:10` (after `@include(shared/_exploit-scope.txt)`)
- Modify: `prompts/ssrf-exploit.txt:10` (after `@include(shared/_exploit-scope.txt)`)
- Modify: `prompts/authz-exploit.txt:10` (after `@include(shared/_exploit-scope.txt)`)
- **Do NOT modify:** `prompts/auth-exploit.txt` (owns its own auth flow)

- [ ] **Step 1: Add include to recon-blackbox.txt**

In `prompts/recon-blackbox.txt`, add `@include(shared/_shared-session.txt)` on a new line between line 16 (`@include(shared/_target.txt)`) and line 17 (empty line before `<context>`). The result should be:

```
@include(shared/_target.txt)
@include(shared/_shared-session.txt)

<context>
```

- [ ] **Step 2: Add include to injection-exploit.txt**

In `prompts/injection-exploit.txt`, add `@include(shared/_shared-session.txt)` on a new line after line 10 (`@include(shared/_exploit-scope.txt)`). The result should be:

```
@include(shared/_exploit-scope.txt)
@include(shared/_shared-session.txt)
```

- [ ] **Step 3: Add include to xss-exploit.txt**

In `prompts/xss-exploit.txt`, add `@include(shared/_shared-session.txt)` on a new line after line 10 (`@include(shared/_exploit-scope.txt)`). The result should be:

```
@include(shared/_exploit-scope.txt)
@include(shared/_shared-session.txt)
```

- [ ] **Step 4: Add include to ssrf-exploit.txt**

In `prompts/ssrf-exploit.txt`, add `@include(shared/_shared-session.txt)` on a new line after line 10 (`@include(shared/_exploit-scope.txt)`). The result should be:

```
@include(shared/_exploit-scope.txt)
@include(shared/_shared-session.txt)
```

- [ ] **Step 5: Add include to authz-exploit.txt**

In `prompts/authz-exploit.txt`, add `@include(shared/_shared-session.txt)` on a new line after line 10 (`@include(shared/_exploit-scope.txt)`). The result should be:

```
@include(shared/_exploit-scope.txt)
@include(shared/_shared-session.txt)
```

- [ ] **Step 6: Verify auth-exploit.txt is NOT modified**

Confirm that `prompts/auth-exploit.txt` does NOT contain `@include(shared/_shared-session.txt)`. This agent owns its own login flow and must not reuse the preflight session.

- [ ] **Step 7: Run prompt manager tests to verify includes work**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_prompt_manager.py -v`
Expected: ALL PASS (existing tests should still pass)

- [ ] **Step 8: Commit**

```bash
git add prompts/recon-blackbox.txt prompts/injection-exploit.txt prompts/xss-exploit.txt prompts/ssrf-exploit.txt prompts/authz-exploit.txt
git commit -m "feat(prompts): add _shared-session.txt include to blackbox agent prompts (not auth-exploit)"
```

---

## Task 8: Add _shared-session.txt Includes to Whitebox Agent Prompts

**Files:**
- Modify: `prompts/recon.txt:36` (before `<login_instructions>`)
- Modify: `prompts/vuln-injection.txt:24` (before `<login_instructions>`)
- Modify: `prompts/vuln-xss.txt:23` (before `<login_instructions>`)
- Modify: `prompts/vuln-ssrf.txt:23` (before `<login_instructions>`)
- Modify: `prompts/vuln-authz.txt:23` (before `<login_instructions>`)
- **Do NOT modify:** `prompts/vuln-auth.txt` (owns its own auth analysis)

- [ ] **Step 1: Add include to recon.txt**

In `prompts/recon.txt`, add `@include(shared/_shared-session.txt)` on a new line before the `<login_instructions>` block (before line 37). The result around that section should be:

```
@include(shared/_code-path-rules.txt)
@include(shared/_shared-session.txt)

<login_instructions>
{{LOGIN_INSTRUCTIONS}}
</login_instructions>
```

- [ ] **Step 2: Add include to vuln-injection.txt**

In `prompts/vuln-injection.txt`, add `@include(shared/_shared-session.txt)` on a new line before the `<login_instructions>` block (before line 25). The result around that section should be:

```
@include(shared/_code-path-rules.txt)
@include(shared/_shared-session.txt)

<login_instructions>
{{LOGIN_INSTRUCTIONS}}
</login_instructions>
```

- [ ] **Step 3: Add include to vuln-xss.txt**

In `prompts/vuln-xss.txt`, add `@include(shared/_shared-session.txt)` on a new line before the `<login_instructions>` block (before line 24). The result:

```
@include(shared/_code-path-rules.txt)
@include(shared/_shared-session.txt)

<login_instructions>
{{LOGIN_INSTRUCTIONS}}
</login_instructions>
```

- [ ] **Step 4: Add include to vuln-ssrf.txt**

In `prompts/vuln-ssrf.txt`, add `@include(shared/_shared-session.txt)` on a new line before the `<login_instructions>` block (before line 24). Same pattern as above.

- [ ] **Step 5: Add include to vuln-authz.txt**

In `prompts/vuln-authz.txt`, add `@include(shared/_shared-session.txt)` on a new line before the `<login_instructions>` block (before line 24). Same pattern as above.

- [ ] **Step 6: Verify vuln-auth.txt is NOT modified**

Confirm that `prompts/vuln-auth.txt` does NOT contain `@include(shared/_shared-session.txt)`. This agent analyzes authentication itself.

- [ ] **Step 7: Run tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_prompt_manager.py -v`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add prompts/recon.txt prompts/vuln-injection.txt prompts/vuln-xss.txt prompts/vuln-ssrf.txt prompts/vuln-authz.txt
git commit -m "feat(prompts): add _shared-session.txt include to whitebox agent prompts (not vuln-auth)"
```

---

## Task 9: Update Whitebox Pipeline for Structured Output

**Files:**
- Verify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:90-115`

The whitebox `run_auth_validation` activity already calls `validate_authentication()` but doesn't explicitly pass or handle structured output. The structured output handling is now inside `validate_authentication()` itself (added in Task 6), so the activity code doesn't need structural changes — the failure classification happens inside `validate_authentication()`.

**Note on spec's whitebox exploit prompts:** The spec's "Whitebox Agent Prompts" table lists `exploit-injection.txt`, `exploit-xss.txt`, etc. as needing the `_shared-session.txt` include. These files **do not exist** on disk — the prompts directory only has the blackbox versions (`injection-exploit.txt`, `xss-exploit.txt`, etc.). The whitebox pipeline uses the `vuln-*` prompts (covered in Task 8). The exploit prompts listed in the spec appear to be a naming error referencing the blackbox exploit prompts, which are already handled in Task 7. No action needed for non-existent files.

- [ ] **Step 1: Verify the activity works with the updated validate_authentication**

Read `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` lines 90-115. The current `run_auth_validation` calls `validate_authentication()` which now internally uses `AUTH_VALIDATION_SCHEMA` and structured output. No changes needed to the activity itself.

- [ ] **Step 2: Run existing whitebox tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/whitebox/tests/ -v`
Expected: ALL PASS

- [ ] **Step 3: Commit (if any changes were needed)**

Only commit if files were actually modified. If no changes needed, skip this step.

---

## Task 10: Final Integration Verification

**Files:**
- All modified files from Tasks 1-9

- [ ] **Step 1: Run the full test suite**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest -v`
Expected: ALL PASS

- [ ] **Step 2: Verify blackbox pipeline auth flow is wired correctly**

The blackbox pipeline (`packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`) already has:
- Auth validation phase (line 65-69) — calls `run_blackbox_auth_validation`
- Cleanup in finally block (line 142) — calls `cleanup_auth_state_sync`
- `workspace_path` in `BlackboxActivityInput` (line 41 of shared.py)

The blackbox activity (`packages/blackbox/src/shannon_blackbox/pipeline/activities.py`) already has:
- `run_blackbox_auth_validation` (lines 52-77) — calls `validate_authentication` and raises `PentestError` on failure

All of this was wired in a previous sub-project. The changes from Tasks 1-6 (VALIDATE_AUTH agent, structured output, failure classification) are now consumed automatically by the existing pipeline code. No additional pipeline changes needed.

- [ ] **Step 3: Verify the dependency chain is satisfied**

Run a quick import check:

```bash
cd /Users/mango/project/shannon-refactor/shannon-py && uv run python -c "
from shannon_core.models.agents import AgentName, AGENTS, PLAYWRIGHT_SESSION_MAPPING
from shannon_core.models.metrics import AgentMetrics
from shannon_core.services.validate_authentication import AUTH_VALIDATION_SCHEMA, validate_authentication
from shannon_core.config.parser import parse_config

# Verify VALIDATE_AUTH
assert AgentName.VALIDATE_AUTH == 'validate-authentication'
assert AgentName.VALIDATE_AUTH in AGENTS
assert PLAYWRIGHT_SESSION_MAPPING[AgentName.VALIDATE_AUTH.value] == 'agent1'

# Verify AgentMetrics has structured_output
m = AgentMetrics(duration_ms=100, structured_output={'key': 'value'})
assert m.structured_output == {'key': 'value'}

# Verify AUTH_VALIDATION_SCHEMA
assert 'login_success' in AUTH_VALIDATION_SCHEMA['required']

print('All dependency chain checks passed!')
"
```

Expected: "All dependency chain checks passed!"

- [ ] **Step 4: Commit (if any fixes were needed)**

Only if changes were made during verification.

---

## Spec Coverage Checklist

| Spec Section | Task | Status |
|---|---|---|
| 1. `_shared-session.txt` shared session partial | Task 5 | ☐ |
| 2. `VALIDATE_AUTH` agent registration | Task 1 | ☐ |
| 3. Structured output validation (schema + runner/executor) | Tasks 2, 3 | ☐ |
| 4. Failure classification | Task 6 | ☐ |
| 5. `login_flow` security validation | Task 4 | ☐ |
| 6. Blackbox pipeline auth integration | Already complete (pre-existing) | ☐ |
| Blackbox prompt includes (recon, injection, xss, ssrf, authz) | Task 7 | ☐ |
| Whitebox prompt includes (recon, vuln-*) | Task 8 | ☐ |
| Whitebox pipeline structured output | Task 9 | ☐ |
| Whitebox `__init__.py` updates | No changes needed (VALIDATE_AUTH auto-exported) | ☐ |
| Whitebox exploit prompts (`exploit-injection.txt` etc.) | Files don't exist on disk; spec naming error. Blackbox exploit prompts covered in Task 7. | N/A |
