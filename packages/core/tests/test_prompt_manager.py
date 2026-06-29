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


# --- Engine-aware interpolation tests ---


def test_browser_session_flag_injected(prompts_dir):
    """BROWSER_SESSION_FLAG placeholder should be replaced with engine session flag."""
    (prompts_dir / "browser-flag-test.txt").write_text(
        "Session: {{BROWSER_SESSION_FLAG}} End"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("browser-flag-test", {"web_url": "https://example.com", "repo_path": "/r"})
    assert "{{BROWSER_SESSION_FLAG}}" not in result
    # Default engine is agent-browser, so flag should contain --session
    assert "--session" in result
    assert "Session:" in result
    assert "End" in result


def test_browser_commands_injected(prompts_dir):
    """BROWSER_COMMANDS placeholder should be replaced with engine command reference."""
    (prompts_dir / "browser-cmd-test.txt").write_text(
        "Commands:\n{{BROWSER_COMMANDS}}\nDone"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("browser-cmd-test", {"web_url": "https://example.com", "repo_path": "/r"})
    assert "{{BROWSER_COMMANDS}}" not in result
    # Default engine is agent-browser, so reference should mention agent-browser
    assert "agent-browser" in result.lower()
    assert "Commands:" in result
    assert "Done" in result


def test_browser_session_id_variable(prompts_dir):
    """Passing browser_session_id in variables should use that session ID."""
    (prompts_dir / "sid-test.txt").write_text("Flag: {{BROWSER_SESSION_FLAG}}")
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("sid-test", {
        "web_url": "https://example.com",
        "repo_path": "/r",
        "browser_engine": "playwright",
        "browser_session_id": "custom-sess",
    })
    # playwright engine uses -s=<id> format
    assert "-s=custom-sess" in result


def test_playwright_session_backward_compat(prompts_dir):
    """Old playwright_session variable should still work for session ID resolution."""
    (prompts_dir / "pw-compat-test.txt").write_text("Flag: {{BROWSER_SESSION_FLAG}}")
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("pw-compat-test", {
        "web_url": "https://example.com",
        "repo_path": "/r",
        "browser_engine": "playwright",
        "playwright_session": "legacy-sess",
    })
    assert "-s=legacy-sess" in result


def test_browser_engine_variable_selects_engine(prompts_dir):
    """Passing browser_engine in variables should change the engine used."""
    (prompts_dir / "engine-select-test.txt").write_text(
        "Flag: {{BROWSER_SESSION_FLAG}}\nCommands: {{BROWSER_COMMANDS}}"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("engine-select-test", {
        "web_url": "https://example.com",
        "repo_path": "/r",
        "browser_engine": "agent-browser",
        "browser_session_id": "sess-abc",
    })
    # Agent-browser uses --session format (space-separated) instead of -s=
    assert "--session sess-abc" in result
    # Commands reference should be agent-browser specific (mentions snapshot, no playwright)
    assert "snapshot" in result.lower()
    assert "agent-browser" in result.lower()


def test_unresolved_placeholder_logs_warning(prompts_dir, caplog):
    """漏传的 {{UPPER_CASE}} 变量应触发 warning。"""
    import logging
    (prompts_dir / "unresolved-test.txt").write_text("Hello {{WEB_URL}} and {{MISSING_VAR}} world")
    manager = PromptManager(prompts_dir)
    with caplog.at_level(logging.WARNING, logger="shannon_core.prompts.manager"):
        result = manager.load_sync("unresolved-test", {"web_url": "https://x.com", "repo_path": "/r"})
    assert "https://x.com" in result  # 已知变量正常替换
    # 残留的 MISSING_VAR 应被报告
    assert any("MISSING_VAR" in r.message for r in caplog.records), \
        "残留的 {{MISSING_VAR}} 应触发 warning"


def test_natural_language_placeholder_not_flagged(prompts_dir, caplog):
    """合法的自然语言填空提示(含空格/小写)不应被误报。"""
    import logging
    (prompts_dir / "fillin-test.txt").write_text(
        "Count: {{number of confirmed vulnerabilities}}\nURL: {{WEB_URL}}"
    )
    manager = PromptManager(prompts_dir)
    with caplog.at_level(logging.WARNING, logger="shannon_core.prompts.manager"):
        result = manager.load_sync("fillin-test", {"web_url": "https://x.com", "repo_path": "/r"})
    # 自然语言填空提示保留在结果里(给 agent 看的占位词)
    assert "{{number of confirmed vulnerabilities}}" in result
    # 不应有任何 warning(自然语言占位符不是真变量)
    unresolved_warnings = [r for r in caplog.records if "Unresolved" in r.message or "placeholder" in r.message.lower()]
    assert unresolved_warnings == [], f"自然语言填空提示不应触发 warning: {unresolved_warnings}"


# --- Code-path rules rendering (CODE_RULES_AVOID / CODE_RULES_FOCUS) ---
# 见 prompts/shared/_code-path-rules.txt：每条 code_path 规则应渲染成
# [FILE]/[GLOB] 标签行,避免占位符原样残留进 LLM prompt(并触发 unresolved warning)。


def test_render_code_path_rules_tags_glob_and_file():
    """code_path 规则按通配符标 [GLOB]/[FILE]。"""
    from shannon_core.models.config import Rule
    manager = PromptManager(Path("/tmp"))
    rules = [
        Rule(description="secrets dir", type="code_path", value="secrets/**"),
        Rule(description="auth route", type="code_path", value="src/routes/auth.js"),
    ]
    result = manager._render_code_path_rules(rules)
    assert "- [GLOB] secrets/**" in result
    assert "- [FILE] src/routes/auth.js" in result


def test_render_code_path_rules_filters_non_code_path_types():
    """非 code_path 类型(如 url_path)规则不进 CODE_RULES 渲染。"""
    from shannon_core.models.config import Rule
    manager = PromptManager(Path("/tmp"))
    rules = [
        Rule(description="admin url", type="url_path", value="/admin"),
        Rule(description="secrets", type="code_path", value="secrets/**"),
    ]
    result = manager._render_code_path_rules(rules)
    assert "/admin" not in result
    assert "secrets/**" in result


def test_render_code_path_rules_appends_description_when_distinct():
    """description 与 value 不同时附 # 注释；相同时省略。"""
    from shannon_core.models.config import Rule
    manager = PromptManager(Path("/tmp"))
    rules = [
        Rule(description="secrets directory", type="code_path", value="secrets/**"),
        Rule(description="secrets/**", type="code_path", value="secrets/**"),
    ]
    result = manager._render_code_path_rules(rules)
    assert "- [GLOB] secrets/**  # secrets directory" in result
    assert "- [GLOB] secrets/**\n" in result + "\n"  # 第二条 description==value 不带注释


def test_render_code_path_rules_empty_returns_none():
    """无 code_path 规则时返回 'None'(与 RULES_AVOID else 分支惯例一致)。"""
    manager = PromptManager(Path("/tmp"))
    assert manager._render_code_path_rules([]) == "None"


def test_code_path_rules_placeholders_resolved_no_warning(prompts_dir, caplog):
    """含 {{CODE_RULES_*}} 的模板经 config 渲染后占位符被替换且不触发 unresolved warning。"""
    import logging
    from shannon_core.models.config import Rule
    (prompts_dir / "code-rules-test.txt").write_text(
        "Avoid:\n{{CODE_RULES_AVOID}}\nFocus:\n{{CODE_RULES_FOCUS}}\n"
    )
    config = _make_dist_config(
        avoid=[Rule(description="secrets", type="code_path", value="secrets/**")],
        focus=[Rule(description="core module", type="code_path", value="src/core/**")],
    )
    manager = PromptManager(prompts_dir)
    with caplog.at_level(logging.WARNING, logger="shannon_core.prompts.manager"):
        result = manager.load_sync(
            "code-rules-test",
            {"web_url": "", "repo_path": "/r"},
            config=config,
        )
    assert "{{CODE_RULES_AVOID}}" not in result
    assert "{{CODE_RULES_FOCUS}}" not in result
    assert "[GLOB] secrets/**" in result
    assert "[GLOB] src/core/**" in result
    assert "# core module" in result
    unresolved = [r for r in caplog.records if "Unresolved" in r.message]
    assert unresolved == [], f"CODE_RULES_* 占位符不应触发 warning: {unresolved}"


def test_code_path_rules_placeholders_none_when_no_rules(prompts_dir, caplog):
    """config 无 code_path 规则时占位符渲染成 'None' 且不触发 warning。"""
    import logging
    (prompts_dir / "code-rules-none.txt").write_text(
        "Avoid:\n{{CODE_RULES_AVOID}}\n"
    )
    config = _make_dist_config()  # avoid=[], focus=[]
    manager = PromptManager(prompts_dir)
    with caplog.at_level(logging.WARNING, logger="shannon_core.prompts.manager"):
        result = manager.load_sync(
            "code-rules-none",
            {"web_url": "", "repo_path": "/r"},
            config=config,
        )
    assert "{{CODE_RULES_AVOID}}" not in result
    assert "None" in result
    unresolved = [r for r in caplog.records if "Unresolved" in r.message]
    assert unresolved == []


def test_code_path_rules_placeholders_resolved_without_config(prompts_dir, caplog):
    """无 config(load_sync 默认 config=None)时占位符渲染成 'None' 且不触发 warning。"""
    import logging
    (prompts_dir / "code-rules-noconfig.txt").write_text(
        "Avoid:\n{{CODE_RULES_AVOID}}\nFocus:\n{{CODE_RULES_FOCUS}}\n"
    )
    manager = PromptManager(prompts_dir)
    with caplog.at_level(logging.WARNING, logger="shannon_core.prompts.manager"):
        result = manager.load_sync(
            "code-rules-noconfig",
            {"web_url": "", "repo_path": "/r"},
        )
    assert "{{CODE_RULES_AVOID}}" not in result
    assert "{{CODE_RULES_FOCUS}}" not in result
    unresolved = [r for r in caplog.records if "Unresolved" in r.message]
    assert unresolved == []


def test_renders_auth_save_command_agent_browser(tmp_path):
    """{{AUTH_SAVE_COMMAND}} resolves to agent-browser `state save <path>`."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "probe.txt").write_text("save: {{AUTH_SAVE_COMMAND}}")
    manager = PromptManager(prompts)
    result = manager.load_sync("probe", {
        "browser_engine": "agent-browser",
        "browser_session_id": "sess-1",
        "AUTH_STATE_FILE": "/tmp/auth.json",
    })
    assert "state save /tmp/auth.json" in result


def test_renders_auth_load_command_playwright(tmp_path):
    """{{AUTH_LOAD_COMMAND}} resolves to playwright `state-load <path>`."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "probe.txt").write_text("load: {{AUTH_LOAD_COMMAND}}")
    manager = PromptManager(prompts)
    result = manager.load_sync("probe", {
        "browser_engine": "playwright",
        "browser_session_id": "sess-1",
        "AUTH_STATE_FILE": "/tmp/auth.json",
    })
    assert "state-load /tmp/auth.json" in result


def test_auth_save_load_command_empty_without_state_file(tmp_path):
    """No AUTH_STATE_FILE → both placeholders empty (no auth path in scope)."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "probe.txt").write_text("[{{AUTH_SAVE_COMMAND}}][{{AUTH_LOAD_COMMAND}}]")
    manager = PromptManager(prompts)
    result = manager.load_sync("probe", {"browser_engine": "agent-browser"})
    assert result == "[][]"


def test_validate_auth_prompt_emits_save_command():
    """validate-authentication prompt renders a concrete save command."""
    prompts_dir = Path(__file__).resolve().parents[3] / "prompts"
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("validate-authentication", {
        "browser_engine": "agent-browser",
        "browser_session_id": "sess-1",
        "AUTH_STATE_FILE": "/tmp/auth.json",
        "AUTH_CONTEXT": "(auth context)",
        "LOGIN_INSTRUCTIONS": "(login steps)",
        "BROWSER_COMMANDS": "(browser ref)",
        "BROWSER_SESSION_FLAG": "--session sess-1",
    })
    assert "state save /tmp/auth.json" in result
    # 泛指文字应已不在（被变量替换语义取代）
    assert "browser's session state save command" not in result

