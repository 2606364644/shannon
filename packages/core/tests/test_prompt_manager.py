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
