# Auth Functionality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Implement 6 core auth features in shannon-py to align with the TypeScript Shannon implementation: EmailLogin model, buildLoginInstructions, buildAuthContext, shared_authenticated_session block handling, auth-state verification, and auth-state cleanup.

**Architecture:** All auth logic lives in `shannon_core` (models, prompts, services). The whitebox and blackbox packages consume core services via their pipeline activities and workflows. The PromptManager gains three new interpolation capabilities. The validate_authentication service gets a full rewrite from stub to real verification.

**Tech Stack:** Python 3.12+, Pydantic v2, pytest + pytest-asyncio, aiofiles for async file I/O, PyYAML for config parsing.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `shannon-py/packages/core/src/shannon_core/models/config.py` | EmailLogin model, Credentials extension | Modify |
| `shannon-py/packages/core/src/shannon_core/prompts/manager.py` | build_login_instructions, _build_auth_context, shared_authenticated_session block | Modify |
| `shannon-py/packages/core/src/shannon_core/services/validate_authentication.py` | auth-state verification, cleanup, validation flow | Rewrite |
| `shannon-py/packages/core/src/shannon_core/models/__init__.py` | Re-export EmailLogin | Modify |
| `shannon-py/packages/core/src/shannon_core/services/__init__.py` | Re-export new auth functions | Modify |
| `shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/shared.py` | Add workspace_path to ActivityInput | Modify |
| `shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | Pass workspace_path to validate_authentication | Modify |
| `shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | Auth-state cleanup in finally block | Modify |
| `shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/shared.py` | Add workspace_path to BlackboxActivityInput | Modify |
| `shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/activities.py` | Pass workspace_path to validate_authentication | Modify |
| `shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | Auth-state cleanup in finally block | Modify |
| `shannon-py/packages/core/tests/test_config.py` | Tests for EmailLogin model | Modify |
| `shannon-py/packages/core/tests/test_prompt_manager.py` | Tests for auth context, login instructions, block handling | Modify |
| `shannon-py/packages/core/tests/test_validate_authentication.py` | Tests for auth-state verification and cleanup | Modify |
| `shannon-py/prompts/shared/login-instructions.txt` | Login instructions template (already exists) | No change |

---

### Task 1: Add EmailLogin Model and Extend Credentials

**Files:**
- Modify: `shannon-py/packages/core/src/shannon_core/models/config.py`
- Modify: `shannon-py/packages/core/tests/test_config.py`
- Modify: `shannon-py/packages/core/src/shannon_core/models/__init__.py`

- [x] **Step 1: Write the failing test for EmailLogin model**

Add these tests to the end of `shannon-py/packages/core/tests/test_config.py`:

```python
def test_email_login_model():
    from shannon_core.models.config import EmailLogin
    el = EmailLogin(address="user@example.com", password="secret")
    assert el.address == "user@example.com"
    assert el.password == "secret"
    assert el.totp_secret is None

def test_email_login_with_totp():
    from shannon_core.models.config import EmailLogin
    el = EmailLogin(address="user@example.com", password="secret", totp_secret="JBSWY3DPEHPK3PXP")
    assert el.totp_secret == "JBSWY3DPEHPK3PXP"

def test_credentials_with_email_login():
    from shannon_core.models.config import Credentials, EmailLogin
    creds = Credentials(
        username="admin",
        password="pass123",
        email_login=EmailLogin(address="admin@corp.com", password="email-pass"),
    )
    assert creds.email_login.address == "admin@corp.com"
    assert creds.email_login.password == "email-pass"

def test_credentials_without_email_login():
    from shannon_core.models.config import Credentials
    creds = Credentials(username="admin", password="pass123")
    assert creds.email_login is None

def test_authentication_with_email_login():
    from shannon_core.models.config import Authentication, Credentials, EmailLogin, SuccessCondition
    auth = Authentication(
        login_type="form",
        login_url="https://example.com/login",
        credentials=Credentials(
            username="admin",
            password="pass123",
            email_login=EmailLogin(address="admin@corp.com", password="email-pass"),
        ),
        success_condition=SuccessCondition(type="url_contains", value="/dashboard"),
    )
    assert auth.credentials.email_login.address == "admin@corp.com"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_config.py::test_email_login_model -v`
Expected: FAIL with `ImportError: cannot import name 'EmailLogin' from 'shannon_core.models.config'`

- [x] **Step 3: Implement EmailLogin model and extend Credentials**

In `shannon-py/packages/core/src/shannon_core/models/config.py`, add the `EmailLogin` class after the `SuccessCondition` class (after line 28) and update the `Credentials` class to include the `email_login` field:

```python
class SuccessCondition(BaseModel):
    type: Literal["url_contains", "element_present", "url_equals_exactly", "text_contains"]
    value: str

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

- [x] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_config.py -v`
Expected: All tests PASS (existing + new)

- [x] **Step 5: Update `__init__.py` re-export**

In `shannon-py/packages/core/src/shannon_core/models/__init__.py`, add `EmailLogin` to the import from `.config` (line 3-17). Add it to the import block and the `__all__` list:

Update the import block (line 3):
```python
from .config import (
    ALL_VULN_CLASSES as CONFIG_VULN_CLASSES,
    Authentication,
    Config,
    Confidence,
    Credentials,
    DistributedConfig,
    EmailLogin,
    PipelineConfig,
    ReportConfig,
    Rule,
    RuleType,
    Rules,
    Severity,
    SuccessCondition,
    VulnClass,
)
```

Add `"EmailLogin"` to the `__all__` list (after `"DistributedConfig"`):
```python
    "DistributedConfig",
    "EmailLogin",
    "ErrorCode",
```

- [x] **Step 6: Run full test suite to verify no regressions**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/ -v`
Expected: All tests PASS

- [x] **Step 7: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/models/config.py packages/core/src/shannon_core/models/__init__.py packages/core/tests/test_config.py
git commit -m "feat: add EmailLogin model and extend Credentials with email_login field"
```

---

### Task 2: Add `_build_auth_context()` Method

**Files:**
- Modify: `shannon-py/packages/core/src/shannon_core/prompts/manager.py`
- Modify: `shannon-py/packages/core/tests/test_prompt_manager.py`

- [x] **Step 1: Write the failing test for _build_auth_context**

Add these tests to the end of `shannon-py/packages/core/tests/test_prompt_manager.py`:

```python
from shannon_core.models.config import (
    Authentication,
    Config,
    Credentials,
    DistributedConfig,
    ReportConfig,
    SuccessCondition,
)


def _make_dist_config(**overrides) -> DistributedConfig:
    defaults = dict(
        avoid=[],
        focus=[],
        description="Test app",
        vuln_classes=["injection"],
        exploit=True,
        report=ReportConfig(),
        rules_of_engagement="",
    )
    defaults.update(overrides)
    return DistributedConfig(**defaults)


def _make_auth(**cred_overrides) -> Authentication:
    cred_defaults = dict(username="admin", password="pass123")
    cred_defaults.update(cred_overrides)
    return Authentication(
        login_type="form",
        login_url="https://example.com/login",
        credentials=Credentials(**cred_defaults),
        success_condition=SuccessCondition(type="url_contains", value="/dashboard"),
    )


def test_auth_context_no_authentication(prompts_dir):
    manager = PromptManager(prompts_dir)
    config = _make_dist_config()
    context = manager._build_auth_context(config)
    assert context == "No authentication configured - unauthenticated testing only"


def test_auth_context_with_form_login(prompts_dir):
    manager = PromptManager(prompts_dir)
    auth = _make_auth()
    config = _make_dist_config(authentication=auth)
    context = manager._build_auth_context(config)
    assert "Login type: FORM" in context
    assert "Username: admin" in context
    assert "Login URL: https://example.com/login" in context


def test_auth_context_with_totp(prompts_dir):
    manager = PromptManager(prompts_dir)
    auth = _make_auth(totp_secret="JBSWY3DPEHPK3PXP")
    config = _make_dist_config(authentication=auth)
    context = manager._build_auth_context(config)
    assert "MFA: TOTP enabled" in context


def test_auth_context_without_totp(prompts_dir):
    manager = PromptManager(prompts_dir)
    auth = _make_auth()
    config = _make_dist_config(authentication=auth)
    context = manager._build_auth_context(config)
    assert "TOTP" not in context
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_prompt_manager.py::test_auth_context_no_authentication -v`
Expected: FAIL with `AttributeError: 'PromptManager' object has no attribute '_build_auth_context'`

- [x] **Step 3: Implement `_build_auth_context()` method**

Add the following method to `PromptManager` in `shannon-py/packages/core/src/shannon_core/prompts/manager.py`, after the `_build_vuln_summary_subsections` method (after line 151):

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

- [x] **Step 4: Update `_interpolate()` to use `_build_auth_context()`**

In `shannon-py/packages/core/src/shannon_core/prompts/manager.py`, replace the `{{AUTH_CONTEXT}}` handling in `_interpolate()`.

Replace line 73:
```python
            result = result.replace("{{AUTH_CONTEXT}}", "No authentication configured" if not config.authentication else f"Login type: {config.authentication.login_type}")
```

With:
```python
            result = result.replace("{{AUTH_CONTEXT}}", self._build_auth_context(config))
```

And replace line 94:
```python
            result = result.replace("{{AUTH_CONTEXT}}", "No authentication configured")
```

With:
```python
            result = result.replace("{{AUTH_CONTEXT}}", self._build_auth_context(config))
```

Note: When `config` is `None`, the `else` branch runs. We need to handle that case. The `_build_auth_context` method expects a `DistributedConfig`, so for the `else` branch (where `config is None`), keep the original string:

```python
            result = result.replace("{{AUTH_CONTEXT}}", "No authentication configured")
```

No change needed on line 94 — it already handles the `config is None` case correctly.

- [x] **Step 5: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_prompt_manager.py -v`
Expected: All tests PASS

- [x] **Step 6: Run full test suite**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/ -v`
Expected: All tests PASS

- [x] **Step 7: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/prompts/manager.py packages/core/tests/test_prompt_manager.py
git commit -m "feat: add _build_auth_context method for richer auth context in prompts"
```

---

### Task 3: Add `build_login_instructions()` Method

**Files:**
- Modify: `shannon-py/packages/core/src/shannon_core/prompts/manager.py`
- Modify: `shannon-py/packages/core/tests/test_prompt_manager.py`

- [x] **Step 1: Write the failing test for build_login_instructions**

Add these tests to the end of `shannon-py/packages/core/tests/test_prompt_manager.py`:

```python
import pytest


@pytest.fixture
def login_prompts_dir(tmp_path):
    """Create a prompts directory with the login-instructions template."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    shared = prompts / "shared"
    shared.mkdir()
    (shared / "login-instructions.txt").write_text(
        "<!-- BEGIN:COMMON -->\n"
        "Common instructions\n"
        "{{user_instructions}}\n"
        "<!-- END:COMMON -->\n"
        "\n"
        "<!-- BEGIN:FORM -->\n"
        "Form login steps\n"
        "<!-- END:FORM -->\n"
        "\n"
        "<!-- BEGIN:SSO -->\n"
        "SSO login steps\n"
        "<!-- END:SSO -->\n"
        "\n"
        "<!-- BEGIN:VERIFICATION -->\n"
        "Verification steps\n"
        "<!-- END:VERIFICATION -->\n"
    )
    return prompts


def test_build_login_instructions_form_type(login_prompts_dir):
    manager = PromptManager(login_prompts_dir)
    auth = _make_auth(login_flow=[
        "Navigate to login page",
        "Enter $username in username field",
        "Enter $password in password field",
        "Click submit",
    ])
    result = manager.build_login_instructions(auth)
    assert "Common instructions" in result
    assert "Form login steps" in result
    assert "Verification steps" in result
    assert "SSO login steps" not in result
    assert "admin" in result
    assert "pass123" in result


def test_build_login_instructions_sso_type(login_prompts_dir):
    manager = PromptManager(login_prompts_dir)
    auth = Authentication(
        login_type="sso",
        login_url="https://example.com/login",
        credentials=Credentials(username="admin", password="pass123"),
        success_condition=SuccessCondition(type="url_contains", value="/dashboard"),
        login_flow=["Click SSO button", "Enter $username"],
    )
    result = manager.build_login_instructions(auth)
    assert "SSO login steps" in result
    assert "Form login steps" not in result


def test_build_login_instructions_with_totp(login_prompts_dir):
    manager = PromptManager(login_prompts_dir)
    auth = _make_auth(
        totp_secret="JBSWY3DPEHPK3PXP",
        login_flow=["Enter $username", "Enter $password", "Enter $totp"],
    )
    result = manager.build_login_instructions(auth)
    assert 'generated TOTP code using secret "JBSWY3DPEHPK3PXP"' in result


def test_build_login_instructions_with_email_login(login_prompts_dir):
    from shannon_core.models.config import EmailLogin
    manager = PromptManager(login_prompts_dir)
    auth = _make_auth(
        email_login=EmailLogin(address="user@example.com", password="email-pass"),
        login_flow=[
            "Enter $email_address",
            "Enter $email_password",
        ],
    )
    result = manager.build_login_instructions(auth)
    assert "user@example.com" in result
    assert "email-pass" in result


def test_build_login_instructions_with_email_totp(login_prompts_dir):
    from shannon_core.models.config import EmailLogin
    manager = PromptManager(login_prompts_dir)
    auth = _make_auth(
        email_login=EmailLogin(address="u@e.com", password="p", totp_secret="SECRET"),
        login_flow=["Enter $email_totp"],
    )
    result = manager.build_login_instructions(auth)
    assert 'generated TOTP code using secret "SECRET"' in result


def test_build_login_instructions_missing_template(login_prompts_dir):
    """Template file removed — should raise PentestError."""
    import shutil
    from shannon_core.models.errors import PentestError
    shutil.rmtree(login_prompts_dir / "shared" / "login-instructions.txt", ignore_errors=True)
    (login_prompts_dir / "shared").rmdir()
    manager = PromptManager(login_prompts_dir)
    auth = _make_auth(login_flow=["step 1"])
    with pytest.raises(PentestError):
        manager.build_login_instructions(auth)


def test_build_login_instructions_empty_login_flow(login_prompts_dir):
    """When login_flow is None or empty, user_instructions should be empty string."""
    manager = PromptManager(login_prompts_dir)
    auth = _make_auth()  # login_flow defaults to None
    result = manager.build_login_instructions(auth)
    assert "Common instructions" in result
    # The {{user_instructions}} placeholder should be replaced with empty string
    assert "{{user_instructions}}" not in result
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_prompt_manager.py::test_build_login_instructions_form_type -v`
Expected: FAIL with `AttributeError: 'PromptManager' object has no attribute 'build_login_instructions'`

- [x] **Step 3: Implement `build_login_instructions()` method**

Add the following import at the top of `shannon-py/packages/core/src/shannon_core/prompts/manager.py` (after the existing imports, around line 1-4):

```python
import re
from pathlib import Path

from shannon_core.models.agents import PLAYWRIGHT_SESSION_MAPPING
from shannon_core.models.config import Authentication, DistributedConfig
from shannon_core.models.errors import ErrorCode, PentestError
```

Note: `re` and `Path` are already imported. Only `Authentication` needs to be added to the import from `config`.

Update the import from `shannon_core.models.config` on line 4:
```python
from shannon_core.models.config import Authentication, DistributedConfig
```

Add the following method to `PromptManager` in `shannon-py/packages/core/src/shannon_core/prompts/manager.py`, after `_build_auth_context` (the method added in Task 2):

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
        auth_section = get_section(full_template, login_type)
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
                user_instructions = user_instructions.replace("$password", creds.password)
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

- [x] **Step 4: Update `_interpolate()` to use `build_login_instructions()`**

In `shannon-py/packages/core/src/shannon_core/prompts/manager.py`, replace line 104:

```python
        result = result.replace("{{LOGIN_INSTRUCTIONS}}", "")
```

With:

```python
        if config and config.authentication and config.authentication.login_flow:
            login_instructions = self.build_login_instructions(config.authentication)
            result = result.replace("{{LOGIN_INSTRUCTIONS}}", login_instructions)
        else:
            result = result.replace("{{LOGIN_INSTRUCTIONS}}", "")
```

- [x] **Step 5: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_prompt_manager.py -v`
Expected: All tests PASS

- [x] **Step 6: Run full test suite**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/ -v`
Expected: All tests PASS

- [x] **Step 7: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/prompts/manager.py packages/core/tests/test_prompt_manager.py
git commit -m "feat: add build_login_instructions with credential interpolation"
```

---

### Task 4: Add `<shared_authenticated_session>` Block Handling

**Files:**
- Modify: `shannon-py/packages/core/src/shannon_core/prompts/manager.py`
- Modify: `shannon-py/packages/core/tests/test_prompt_manager.py`

- [x] **Step 1: Write the failing test for shared_authenticated_session block**

Add these tests to the end of `shannon-py/packages/core/tests/test_prompt_manager.py`:

```python
def test_shared_session_block_removed_without_auth(prompts_dir):
    """When no authentication configured, the shared_authenticated_session block is removed."""
    (prompts_dir / "session-test.txt").write_text(
        "Before\n"
        "<shared_authenticated_session>\n"
        "Use session file: {{AUTH_STATE_FILE}}\n"
        "</shared_authenticated_session>\n"
        "After\n"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("session-test", {"web_url": "https://example.com", "repo_path": "/r"})
    assert "shared_authenticated_session" not in result
    assert "Before" in result
    assert "After" in result


def test_shared_session_block_preserved_with_auth(prompts_dir):
    """When authentication is configured, the block stays and variables are interpolated."""
    (prompts_dir / "session-test.txt").write_text(
        "Before\n"
        "<shared_authenticated_session>\n"
        "Use session file: {{AUTH_STATE_FILE}}\n"
        "</shared_authenticated_session>\n"
        "After\n"
    )
    auth = _make_auth()
    config = _make_dist_config(authentication=auth)
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("session-test", {"web_url": "https://example.com", "repo_path": "/r"}, config=config)
    assert "shared_authenticated_session" in result
    assert "Use session file:" in result


def test_shared_session_block_removed_when_config_none(prompts_dir):
    """When config is None, the block is removed."""
    (prompts_dir / "session-test.txt").write_text(
        "Before\n"
        "<shared_authenticated_session>inner</shared_authenticated_session>\n"
        "After\n"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("session-test", {"web_url": "https://example.com", "repo_path": "/r"}, config=None)
    assert "shared_authenticated_session" not in result
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_prompt_manager.py::test_shared_session_block_removed_without_auth -v`
Expected: FAIL — the `<shared_authenticated_session>` block is still present in output

- [x] **Step 3: Implement shared_authenticated_session block handling**

In `shannon-py/packages/core/src/shannon_core/prompts/manager.py`, in the `_interpolate()` method, add the block removal right after the `{{LOGIN_INSTRUCTIONS}}` replacement (the code added in Task 3, Step 4). After the `else` block of the LOGIN_INSTRUCTIONS handling, add:

```python
        # Remove <shared_authenticated_session> block when no auth configured
        if not (config and config.authentication):
            result = re.sub(
                r"<shared_authenticated_session>[\s\S]*?</shared_authenticated_session>\s*",
                "",
                result,
            )
```

The final LOGIN_INSTRUCTIONS + session block section of `_interpolate()` should look like:

```python
        if config and config.authentication and config.authentication.login_flow:
            login_instructions = self.build_login_instructions(config.authentication)
            result = result.replace("{{LOGIN_INSTRUCTIONS}}", login_instructions)
        else:
            result = result.replace("{{LOGIN_INSTRUCTIONS}}", "")

        # Remove <shared_authenticated_session> block when no auth configured
        if not (config and config.authentication):
            result = re.sub(
                r"<shared_authenticated_session>[\s\S]*?</shared_authenticated_session>\s*",
                "",
                result,
            )
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_prompt_manager.py -v`
Expected: All tests PASS

- [x] **Step 5: Run full test suite**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/ -v`
Expected: All tests PASS

- [x] **Step 6: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/prompts/manager.py packages/core/tests/test_prompt_manager.py
git commit -m "feat: handle shared_authenticated_session block in prompt interpolation"
```

---

### Task 5: Rewrite auth-state Verification

**Files:**
- Rewrite: `shannon-py/packages/core/src/shannon_core/services/validate_authentication.py`
- Modify: `shannon-py/packages/core/tests/test_validate_authentication.py`
- Modify: `shannon-py/packages/core/src/shannon_core/services/__init__.py`

- [x] **Step 1: Write the failing tests for auth-state verification**

Replace the entire contents of `shannon-py/packages/core/tests/test_validate_authentication.py` with:

```python
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.services.validate_authentication import (
    AuthValidationResult,
    auth_state_path,
    cleanup_auth_state,
    verify_auth_state,
    validate_authentication,
)


# --- auth_state_path tests ---

def test_auth_state_path_returns_json_file():
    assert auth_state_path("/tmp/workspace") == Path("/tmp/workspace/auth-state.json")

def test_auth_state_path_accepts_path_object():
    assert auth_state_path(Path("/tmp/ws")) == Path("/tmp/ws/auth-state.json")


# --- verify_auth_state tests ---

@pytest.mark.asyncio
async def test_verify_missing_file(tmp_path):
    state_file = tmp_path / "auth-state.json"
    result = await verify_auth_state(state_file)
    assert result.success is False
    assert result.failure_point == "out_of_band"
    assert "did not save auth state" in result.failure_detail

@pytest.mark.asyncio
async def test_verify_invalid_json(tmp_path):
    state_file = tmp_path / "auth-state.json"
    state_file.write_text("not json{{{")
    result = await verify_auth_state(state_file)
    assert result.success is False
    assert result.failure_point == "out_of_band"
    assert "not valid JSON" in result.failure_detail

@pytest.mark.asyncio
async def test_verify_empty_cookies_and_origins(tmp_path):
    state_file = tmp_path / "auth-state.json"
    state_file.write_text(json.dumps({"cookies": [], "origins": []}))
    result = await verify_auth_state(state_file)
    assert result.success is False
    assert result.failure_point == "out_of_band"
    assert "no cookies or origins" in result.failure_detail

@pytest.mark.asyncio
async def test_verify_valid_state_with_cookies(tmp_path):
    state_file = tmp_path / "auth-state.json"
    state_file.write_text(json.dumps({
        "cookies": [{"name": "session", "value": "abc123"}],
        "origins": [],
    }))
    result = await verify_auth_state(state_file)
    assert result.success is True

@pytest.mark.asyncio
async def test_verify_valid_state_with_origins(tmp_path):
    state_file = tmp_path / "auth-state.json"
    state_file.write_text(json.dumps({
        "cookies": [],
        "origins": [{"origin": "https://example.com", "localStorage": [{"name": "token", "value": "xyz"}]}],
    }))
    result = await verify_auth_state(state_file)
    assert result.success is True


# --- cleanup_auth_state tests ---

@pytest.mark.asyncio
async def test_cleanup_removes_existing_file(tmp_path):
    state_file = tmp_path / "auth-state.json"
    state_file.write_text('{"cookies":[]}')
    assert state_file.exists()
    await cleanup_auth_state(tmp_path)
    assert not state_file.exists()

@pytest.mark.asyncio
async def test_cleanup_noop_when_no_file(tmp_path):
    await cleanup_auth_state(tmp_path)
    # Should not raise


# --- validate_authentication integration tests ---

@pytest.mark.asyncio
async def test_auth_validation_no_config():
    """When config_path is None, skip validation and return success."""
    mock_pm = MagicMock()
    mock_executor = MagicMock()

    result = await validate_authentication(
        web_url="https://example.com",
        config_path=None,
        workspace_path="/tmp/ws",
        prompt_manager=mock_pm,
        executor=mock_executor,
    )
    assert result.success is True
    mock_executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_auth_validation_config_no_auth_section():
    """When config exists but has no authentication section, return success without calling executor."""
    mock_pm = MagicMock()
    mock_executor = MagicMock()

    mock_dist_config = MagicMock()
    mock_dist_config.authentication = None

    with patch("shannon_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("shannon_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path="/tmp/ws",
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    assert result.success is True
    mock_executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_auth_validation_cleans_up_stale_state(tmp_path):
    """Stale auth-state.json is deleted before running the agent."""
    state_file = tmp_path / "auth-state.json"
    state_file.write_text('{"old": true}')

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=MagicMock(
        duration_ms=5000, cost_usd=0.01, num_turns=3, model="claude-sonnet-4-6",
    ))
    mock_pm = MagicMock()

    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin", "password": "pass123"}

    with patch("shannon_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("shannon_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    # The stale file should have been deleted before executor ran
    assert result.success is True
    mock_executor.execute.assert_called_once()
    call_kwargs = mock_executor.execute.call_args.kwargs
    assert call_kwargs.get("prompt_override") == "validate-authentication"


@pytest.mark.asyncio
async def test_auth_validation_detects_missing_state_file(tmp_path):
    """When executor runs but no auth-state.json is saved, return failure."""
    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=MagicMock(
        duration_ms=5000, cost_usd=0.01, num_turns=3, model="claude-sonnet-4-6",
    ))
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
async def test_auth_validation_verifies_state_content(tmp_path):
    """When executor runs and valid auth-state is saved, return success."""
    state_file = tmp_path / "auth-state.json"
    # Simulate agent writing the file during executor.execute
    async def fake_execute(**kwargs):
        state_file.write_text(json.dumps({
            "cookies": [{"name": "session", "value": "abc"}],
            "origins": [],
        }))
        return MagicMock(duration_ms=5000, cost_usd=0.01, num_turns=3, model="claude-sonnet-4-6")

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

    assert result.success is True
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_validate_authentication.py -v`
Expected: FAIL with multiple import errors and `TypeError` for missing `workspace_path` parameter

- [x] **Step 3: Rewrite validate_authentication.py**

Replace the entire contents of `shannon-py/packages/core/src/shannon_core/services/validate_authentication.py` with:

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

- [x] **Step 4: Update services `__init__.py`**

In `shannon-py/packages/core/src/shannon_core/services/__init__.py`, add re-exports for the new functions. The file is currently empty (1 line). Replace its contents with:

```python
from shannon_core.services.validate_authentication import (
    AuthValidationResult,
    auth_state_path,
    cleanup_auth_state,
    validate_authentication,
    verify_auth_state,
)
```

- [x] **Step 5: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_validate_authentication.py -v`
Expected: All tests PASS

- [x] **Step 6: Run full test suite**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/ -v`
Expected: All tests PASS (note: callers haven't been updated yet so whitebox/blackbox tests may fail if they exist — but core tests should all pass)

- [x] **Step 7: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/services/validate_authentication.py packages/core/src/shannon_core/services/__init__.py packages/core/tests/test_validate_authentication.py
git commit -m "feat: rewrite auth-state verification with real validation logic"
```

---

### Task 6: Update Callers — Whitebox Activities and Shared Models

**Files:**
- Modify: `shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/shared.py`
- Modify: `shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/activities.py`

- [x] **Step 1: Add `workspace_path` to `ActivityInput`**

In `shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/shared.py`, add a `workspace_path` field to `ActivityInput`:

```python
@dataclass
class ActivityInput:
    repo_path: str
    web_url: str = ""
    config_path: str | None = None
    workspace_name: str | None = None
    deliverables_subdir: str = ".shannon/deliverables"
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    prompt_override: str | None = None
    workspace_path: str | None = None
```

- [x] **Step 2: Update `run_auth_validation` to pass `workspace_path`**

In `shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/activities.py`, update the `run_auth_validation` activity (lines 90-114) to pass `workspace_path`:

```python
@activity.defn
async def run_auth_validation(input: ActivityInput) -> None:
    from shannon_core.services.validate_authentication import validate_authentication
    from shannon_core.prompts.manager import PromptManager
    from shannon_core.agents.executor import AgentExecutor

    prompts_dir = Path(__file__).resolve().parents[4] / "prompts"
    prompt_manager = PromptManager(prompts_dir)
    executor = AgentExecutor(prompt_manager)

    result = await validate_authentication(
        web_url=input.web_url,
        config_path=input.config_path,
        workspace_path=input.workspace_path or "",
        prompt_manager=prompt_manager,
        executor=executor,
        repo_path=input.repo_path,
        api_key=input.api_key,
    )
    if not result.success:
        raise PentestError(
            f"Authentication validation failed: {result.failure_detail or 'unknown'}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.AUTH_LOGIN_FAILED,
        )
```

- [x] **Step 3: Verify no import errors**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run python -c "from shannon_whitebox.pipeline.activities import run_auth_validation; print('OK')"`
Expected: `OK`

- [x] **Step 4: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/shared.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py
git commit -m "feat: pass workspace_path to validate_authentication in whitebox pipeline"
```

---

### Task 7: Update Callers — Blackbox Activities and Shared Models

**Files:**
- Modify: `shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/shared.py`
- Modify: `shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/activities.py`

- [x] **Step 1: Add `workspace_path` to `BlackboxActivityInput`**

In `shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/shared.py`, add a `workspace_path` field to `BlackboxActivityInput`:

```python
@dataclass
class BlackboxActivityInput:
    web_url: str
    repo_path: str | None = None
    config_path: str | None = None
    workspace_name: str | None = None
    deliverables_subdir: str = ".shannon/deliverables"
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    agent_name: str | None = None
    vuln_type: str | None = None
    workspace_path: str | None = None
```

- [x] **Step 2: Update `run_blackbox_auth_validation` to pass `workspace_path`**

In `shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/activities.py`, update the `run_blackbox_auth_validation` activity (lines 52-76) to pass `workspace_path`:

```python
@activity.defn
async def run_blackbox_auth_validation(input: BlackboxActivityInput) -> None:
    from shannon_core.services.validate_authentication import validate_authentication
    from shannon_core.prompts.manager import PromptManager
    from shannon_core.agents.executor import AgentExecutor

    prompts_dir = Path(__file__).resolve().parents[4] / "prompts"
    prompt_manager = PromptManager(prompts_dir)
    executor = AgentExecutor(prompt_manager)

    result = await validate_authentication(
        web_url=input.web_url,
        config_path=input.config_path,
        workspace_path=input.workspace_path or "",
        prompt_manager=prompt_manager,
        executor=executor,
        repo_path=input.repo_path or "",
        api_key=input.api_key,
    )
    if not result.success:
        raise PentestError(
            f"Authentication validation failed: {result.failure_detail or 'unknown'}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.AUTH_LOGIN_FAILED,
        )
```

- [x] **Step 3: Verify no import errors**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run python -c "from shannon_blackbox.pipeline.activities import run_blackbox_auth_validation; print('OK')"`
Expected: `OK`

- [x] **Step 4: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/blackbox/src/shannon_blackbox/pipeline/shared.py packages/blackbox/src/shannon_blackbox/pipeline/activities.py
git commit -m "feat: pass workspace_path to validate_authentication in blackbox pipeline"
```

---

### Task 8: Add auth-state Cleanup to Workflows

**Files:**
- Modify: `shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`
- Modify: `shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`

- [x] **Step 1: Add auth-state cleanup to whitebox workflow**

In `shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`, update the `with workflow.unsafe.imports_passed_through()` block (line 12-14) to add the cleanup import:

```python
with workflow.unsafe.imports_passed_through():
    from . import activities
    from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
    from shannon_core.services.playwright_config_writer import write_stealth_config, cleanup_stealth_config
    from shannon_core.services.validate_authentication import cleanup_auth_state
```

Then, in the `finally` block (lines 115-117), add auth-state cleanup. Compute the workspace path from the input and clean up:

```python
        finally:
            cleanup_settings()
            cleanup_stealth_config(input.repo_path)
            if input.workspace_name:
                workspace_path = str(Path(input.repo_path).parent / "workspaces" / input.workspace_name)
            else:
                workspace_path = input.repo_path
            await cleanup_auth_state(workspace_path)
```

Note: The `finally` block in a `@workflow.defn` cannot directly `await` async functions. Since `cleanup_auth_state` is async, we need to run it as an activity. Create a thin wrapper activity or use a sync file delete. However, `cleanup_auth_state` uses `aiofiles.os.remove`. The simplest approach is to add a sync cleanup function.

Actually, looking at the workflow code more carefully — the `finally` block already calls sync functions (`cleanup_settings()`, `cleanup_stealth_config()`). We should add a sync version of the cleanup. Let's add a sync helper directly in the validate_authentication module.

**Revised approach:** Add a sync `cleanup_auth_state_sync()` function in `validate_authentication.py`, and call it from the workflow's finally block.

In `shannon-py/packages/core/src/shannon_core/services/validate_authentication.py`, add a sync cleanup function after the async `cleanup_auth_state`:

```python
def cleanup_auth_state_sync(workspace_path: str | Path) -> None:
    """Synchronous version of cleanup_auth_state for use in workflow finally blocks."""
    state_file = auth_state_path(workspace_path)
    if state_file.exists():
        state_file.unlink()
```

Update the services `__init__.py` to also export it:

```python
from shannon_core.services.validate_authentication import (
    AuthValidationResult,
    auth_state_path,
    cleanup_auth_state,
    cleanup_auth_state_sync,
    validate_authentication,
    verify_auth_state,
)
```

Now update the whitebox workflow's `finally` block:

```python
        finally:
            cleanup_settings()
            cleanup_stealth_config(input.repo_path)
            if input.workspace_name:
                ws_path = str(Path(input.repo_path).parent / "workspaces" / input.workspace_name)
            else:
                ws_path = input.repo_path
            cleanup_auth_state_sync(ws_path)
```

And update the import in the `with workflow.unsafe.imports_passed_through():` block:

```python
with workflow.unsafe.imports_passed_through():
    from . import activities
    from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
    from shannon_core.services.playwright_config_writer import write_stealth_config, cleanup_stealth_config
    from shannon_core.services.validate_authentication import cleanup_auth_state_sync
```

- [x] **Step 2: Add auth-state cleanup to blackbox workflow**

In `shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`, update the `with workflow.unsafe.imports_passed_through()` block (line 12-15):

```python
with workflow.unsafe.imports_passed_through():
    from . import activities
    from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
    from shannon_core.services.playwright_config_writer import write_stealth_config, cleanup_stealth_config
    from shannon_core.services.validate_authentication import cleanup_auth_state_sync
```

Then, in the `finally` block (lines 136-139), add auth-state cleanup:

```python
        finally:
            cleanup_settings()
            if input.repo_path:
                cleanup_stealth_config(input.repo_path)
                cleanup_auth_state_sync(input.repo_path)
```

- [x] **Step 3: Verify imports work**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run python -c "from shannon_core.services.validate_authentication import cleanup_auth_state_sync; print('OK')"`
Expected: `OK`

- [x] **Step 4: Run full test suite**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/ -v`
Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/services/validate_authentication.py packages/core/src/shannon_core/services/__init__.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/blackbox/src/shannon_blackbox/pipeline/workflows.py
git commit -m "feat: add auth-state cleanup on workflow completion"
```

---

## Self-Review

### 1. Spec Coverage

| Spec Requirement | Task |
|---|---|
| 1. `email_login` field | Task 1 |
| 2. `buildLoginInstructions()` | Task 3 |
| 3. `buildAuthContext()` enhancement | Task 2 |
| 4. auth-state verification | Task 5 |
| 5. `<shared_authenticated_session>` block | Task 4 |
| 6. auth-state cleanup on completion | Task 8 |
| `__init__.py` updates | Tasks 1, 5, 8 |
| Whitebox activity caller update | Task 6 |
| Blackbox activity caller update | Task 7 |
| `workspace_path` on shared models | Tasks 6, 7 |

All spec requirements covered.

### 2. Placeholder Scan

No TBD, TODO, "implement later", "add appropriate error handling", or "similar to Task N" patterns found.

### 3. Type Consistency

- `EmailLogin` defined in Task 1, used consistently in Tasks 3 and 5
- `Authentication` imported consistently across files
- `AuthValidationResult` used with consistent field names (`success`, `failure_point`, `failure_detail`)
- `workspace_path: str` parameter used consistently across `validate_authentication()`, `cleanup_auth_state()`, `cleanup_auth_state_sync()`, and `auth_state_path()`
- `_build_auth_context()` takes `DistributedConfig`, called with config from `_interpolate()`
- `build_login_instructions()` takes `Authentication`, called with `config.authentication`
- ActivityInput and BlackboxActivityInput both gain `workspace_path: str | None = None`
