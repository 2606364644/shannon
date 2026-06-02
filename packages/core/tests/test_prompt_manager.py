import pytest
from pathlib import Path
from shannon_core.prompts.manager import PromptManager

@pytest.fixture
def prompts_dir(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "pre-recon-code.txt").write_text("Analyze {{REPO_PATH}} for {{WEB_URL}}")
    (prompts / "recon.txt").write_text("Recon for {{WEB_URL}}")
    shared = prompts / "shared"
    shared.mkdir()
    (shared / "_target.txt").write_text("Target: {{WEB_URL}}")
    include_prompt = prompts / "with-include.txt"
    include_prompt.write_text("Header\n@include(shared/_target.txt)\nFooter")
    return prompts

def test_load_simple_template(prompts_dir):
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("pre-recon-code", {"web_url": "https://example.com", "repo_path": "/repo"})
    assert "https://example.com" in result
    assert "/repo" in result

def test_variable_substitution(prompts_dir):
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("recon", {"web_url": "https://test.com", "repo_path": "/app"})
    assert "https://test.com" in result

def test_include_directive(prompts_dir):
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("with-include", {"web_url": "https://inc.com", "repo_path": "/r"})
    assert "Target: https://inc.com" in result
    assert "Header" in result
    assert "Footer" in result

def test_missing_template_raises(prompts_dir):
    manager = PromptManager(prompts_dir)
    with pytest.raises(Exception):
        manager.load_sync("nonexistent", {"web_url": "https://x.com", "repo_path": "/r"})


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


def _make_auth(login_flow=None, **cred_overrides) -> Authentication:
    cred_defaults = dict(username="admin", password="pass123")
    cred_defaults.update(cred_overrides)
    return Authentication(
        login_type="form",
        login_url="https://example.com/login",
        credentials=Credentials(**cred_defaults),
        success_condition=SuccessCondition(type="url_contains", value="/dashboard"),
        login_flow=login_flow,
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
    """Template file removed -- should raise PentestError."""
    from shannon_core.models.errors import PentestError
    (login_prompts_dir / "shared" / "login-instructions.txt").unlink()
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


# --- Conditional block tests ---

def test_if_live_block_kept_when_web_url_present(prompts_dir):
    """When WEB_URL is provided, <if-live> content stays, <if-static> is removed."""
    (prompts_dir / "cond-test.txt").write_text(
        "<if-live>URL: {{WEB_URL}}</if-live><if-static>Mode: Offline</if-static>\nFooter"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("cond-test", {"web_url": "https://example.com", "repo_path": "/r"})
    assert "URL: https://example.com" in result
    assert "Offline" not in result
    assert "Footer" in result
    assert "<if-live>" not in result
    assert "<if-static>" not in result


def test_if_static_block_kept_when_no_web_url(prompts_dir):
    """When WEB_URL is empty, <if-static> content stays, <if-live> is removed."""
    (prompts_dir / "cond-test.txt").write_text(
        "<if-live>URL: {{WEB_URL}}</if-live><if-static>Mode: Offline</if-static>\nFooter"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("cond-test", {"web_url": "", "repo_path": "/r"})
    assert "Mode: Offline" in result
    assert "URL:" not in result
    assert "Footer" in result
    assert "<if-live>" not in result
    assert "<if-static>" not in result


def test_no_conditional_blocks_unchanged(prompts_dir):
    """Templates without conditional blocks are not affected."""
    (prompts_dir / "plain-test.txt").write_text("Hello {{WEB_URL}} world")
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("plain-test", {"web_url": "https://x.com", "repo_path": "/r"})
    assert result == "Hello https://x.com world"


def test_conditional_blocks_in_included_file(prompts_dir):
    """Conditional blocks work inside @include'd shared files."""
    (prompts_dir / "shared" / "_cond.txt").write_text(
        "<if-live>LIVE</if-live><if-static>STATIC</if-static>"
    )
    (prompts_dir / "inc-cond-test.txt").write_text("Start @include(shared/_cond.txt) End")
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("inc-cond-test", {"web_url": "", "repo_path": "/r"})
    assert "STATIC" in result
    assert "LIVE" not in result
    assert "Start" in result
    assert "End" in result


def test_multiline_conditional_block(prompts_dir):
    """Multi-line <if-static> blocks are stripped correctly."""
    (prompts_dir / "multi-cond.txt").write_text(
        "<if-live>URL: {{WEB_URL}}</if-live><if-static>Mode: Offline static code analysis\nLine 2\nLine 3</if-static>"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("multi-cond", {"web_url": "", "repo_path": "/r"})
    assert "Line 2" in result
    assert "Line 3" in result
    assert "URL:" not in result
