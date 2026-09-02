import pytest
from supernova_core.config.parser import (
    parse_config,
    distribute_config,
    _sanitize_authentication,
    _sanitize_rule,
)
from supernova_core.models.config import (
    Authentication,
    Config,
    Credentials,
    EmailLogin,
    Rule,
    Rules,
)
from supernova_core.models.errors import PentestError, ErrorCode

def test_parse_valid_config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text('description: "Test app"\nvuln_classes:\n  - injection\n  - xss\nrules:\n  avoid:\n    - description: "skip logout"\n      type: url_path\n      value: "/logout"\n')
    config = parse_config(str(config_file))
    assert config.description == "Test app"
    assert len(config.vuln_classes) == 2

def test_parse_empty_config_file(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("")
    with pytest.raises(PentestError) as exc_info:
        parse_config(str(config_file))
    assert exc_info.value.error_code == ErrorCode.CONFIG_VALIDATION_FAILED

def test_parse_missing_file():
    with pytest.raises(PentestError) as exc_info:
        parse_config("/nonexistent/config.yaml")
    assert exc_info.value.error_code == ErrorCode.CONFIG_NOT_FOUND

def test_parse_dangerous_description(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text('description: "<script>alert(1)</script>"')
    with pytest.raises(PentestError) as exc_info:
        parse_config(str(config_file))
    assert "dangerous pattern" in str(exc_info.value.message)

def test_parse_url_path_without_slash(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text('rules:\n  avoid:\n    - description: "bad path"\n      type: url_path\n      value: "no-slash"\n')
    with pytest.raises(PentestError) as exc_info:
        parse_config(str(config_file))
    assert "must start with '/'" in str(exc_info.value.message)

def test_distribute_config_none():
    d = distribute_config(None)
    assert d.description == ""
    assert len(d.vuln_classes) == 5  # inj/xss/auth/authz/ssrf (misconfig 裁剪,见 memory)
    assert d.exploit is True

def test_distribute_config_full():
    c = Config(description="My app", vuln_classes=["injection"])
    d = distribute_config(c)
    assert d.description == "My app"
    assert d.vuln_classes == ["injection"]


from pathlib import Path


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
""")
    with pytest.raises(PentestError, match="login_flow step 1 contains potentially dangerous pattern"):
        parse_config(config_path)


def test_legacy_success_condition_ignored(tmp_path):
    """D1 (2026-08-05): success_condition was removed. A legacy scan-config.yaml that
    still carries a success_condition block must parse without error (pydantic ignores
    unknown fields) — old user configs keep working, the dead field is silently dropped.
    Locks the backward-compat behavior so a future extra='forbid' won't break configs."""
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
    assert not hasattr(config.authentication, "success_condition")



# ---------------------------------------------------------------------------
# Sanitization tests
# ---------------------------------------------------------------------------

class TestSanitizeRule:
    """Tests for _sanitize_rule (post-Pydantic whitespace stripping)."""

    def test_strips_whitespace(self):
        rule = Rule(description="  skip logout  ", type="url_path", value="  /logout  ")
        sanitized = _sanitize_rule(rule)
        assert sanitized.description == "skip logout"
        assert sanitized.type == "url_path"
        assert sanitized.value == "/logout"

    def test_no_trailing_whitespace(self):
        rule = Rule(description="test rule", type="domain", value="example.com")
        sanitized = _sanitize_rule(rule)
        assert sanitized.description == "test rule"
        assert sanitized.type == "domain"
        assert sanitized.value == "example.com"


class TestSanitizeAuthentication:
    """Tests for _sanitize_authentication (post-Pydantic whitespace stripping)."""

    def _make_auth(self, **overrides):
        defaults = dict(
            login_type="form",
            login_url=" https://example.com/login ",
            credentials=Credentials(
                username="  admin  ",
                password="  pass123  ",
                totp_secret="  SECRET  ",
            ),
        )
        defaults.update(overrides)
        return Authentication(**defaults)

    def test_strips_whitespace_on_all_fields(self):
        auth = self._make_auth()
        sanitized = _sanitize_authentication(auth)
        assert sanitized.login_url == "https://example.com/login"
        assert sanitized.credentials.username == "admin"
        assert sanitized.credentials.password == "pass123"
        assert sanitized.credentials.totp_secret == "SECRET"

    def test_password_none(self):
        auth = self._make_auth(
            credentials=Credentials(username="admin"),
        )
        sanitized = _sanitize_authentication(auth)
        assert sanitized.credentials.password is None

    def test_totp_secret_none(self):
        auth = self._make_auth(
            credentials=Credentials(username="admin", password="pass"),
        )
        sanitized = _sanitize_authentication(auth)
        assert sanitized.credentials.totp_secret is None

    def test_email_login_none(self):
        auth = self._make_auth()
        sanitized = _sanitize_authentication(auth)
        assert sanitized.credentials.email_login is None

    def test_email_login_with_fields_stripped(self):
        auth = self._make_auth(
            credentials=Credentials(
                username="admin",
                password="pass",
                email_login=EmailLogin(
                    address="  admin@corp.com  ",
                    password="  email-pass  ",
                    totp_secret="  TOTPKEY  ",
                ),
            ),
        )
        sanitized = _sanitize_authentication(auth)
        assert sanitized.credentials.email_login.address == "admin@corp.com"
        assert sanitized.credentials.email_login.password == "email-pass"
        assert sanitized.credentials.email_login.totp_secret == "TOTPKEY"

    def test_email_login_totp_secret_none(self):
        auth = self._make_auth(
            credentials=Credentials(
                username="admin",
                password="pass",
                email_login=EmailLogin(address="a@b.com", password="pw"),
            ),
        )
        sanitized = _sanitize_authentication(auth)
        assert sanitized.credentials.email_login.totp_secret is None

    def test_login_flow_stripped(self):
        auth = self._make_auth(
            login_flow=["  step one  ", "  step two  "],
        )
        sanitized = _sanitize_authentication(auth)
        assert sanitized.login_flow == ["step one", "step two"]

    def test_login_flow_none(self):
        auth = self._make_auth()
        sanitized = _sanitize_authentication(auth)
        assert sanitized.login_flow is None


class TestSanitizeRaw:
    """Tests for _sanitize_raw_dict / _sanitize_raw_auth / _sanitize_raw_rule.

    These test case normalization on raw dicts before Pydantic validation.
    """

    def test_raw_rule_lowercase_and_strip(self):
        from supernova_core.config.parser import _sanitize_raw_rule
        rule = {"description": "  test  ", "type": "  URL_PATH  ", "value": "  /admin  "}
        result = _sanitize_raw_rule(rule)
        assert result["description"] == "test"
        assert result["type"] == "url_path"
        assert result["value"] == "/admin"

    def test_raw_auth_lowercase_login_type(self):
        from supernova_core.config.parser import _sanitize_raw_auth
        auth = {
            "login_type": " FORM ",
            "login_url": "  https://example.com  ",
            "credentials": {"username": " admin "},
        }
        result = _sanitize_raw_auth(auth)
        assert result["login_type"] == "form"
        assert result["login_url"] == "https://example.com"
        assert result["credentials"]["username"] == "admin"

    def test_raw_auth_login_flow_stripped(self):
        from supernova_core.config.parser import _sanitize_raw_auth
        auth = {
            "login_type": "form",
            "login_url": "https://example.com",
            "credentials": {"username": "admin"},
            "login_flow": ["  step one  ", "  step two  "],
        }
        result = _sanitize_raw_auth(auth)
        assert result["login_flow"] == ["step one", "step two"]

    def test_raw_auth_email_login_stripped(self):
        from supernova_core.config.parser import _sanitize_raw_auth
        auth = {
            "login_type": "form",
            "login_url": "https://example.com",
            "credentials": {
                "username": "admin",
                "email_login": {
                    "address": "  a@b.com  ",
                    "password": "  pw  ",
                    "totp_secret": "  KEY  ",
                },
            },
        }
        result = _sanitize_raw_auth(auth)
        el = result["credentials"]["email_login"]
        assert el["address"] == "a@b.com"
        assert el["password"] == "pw"
        assert el["totp_secret"] == "KEY"

    def test_raw_dict_full_sanitize(self):
        from supernova_core.config.parser import _sanitize_raw_dict
        raw = {
            "authentication": {
                "login_type": "FORM",
                "login_url": "  https://example.com  ",
                "credentials": {"username": " admin "},
            },
            "rules": {
                "avoid": [{"description": "  test  ", "type": " URL_PATH ", "value": " /admin "}],
            },
        }
        result = _sanitize_raw_dict(raw)
        assert result["authentication"]["login_type"] == "form"
        assert result["authentication"]["login_url"] == "https://example.com"
        assert result["rules"]["avoid"][0]["type"] == "url_path"
        assert result["rules"]["avoid"][0]["value"] == "/admin"

    def test_raw_dict_no_auth_no_rules(self):
        from supernova_core.config.parser import _sanitize_raw_dict
        raw = {"description": "simple config"}
        result = _sanitize_raw_dict(raw)
        assert result == {"description": "simple config"}


class TestSanitizeIntegration:
    """Integration tests: parse_config sanitizes before validation."""

    def test_config_with_whitespace_auth_stripped(self, tmp_path):
        config_path = _write_config(tmp_path, """
authentication:
  login_type: form
  login_url: "  https://example.com/login  "
  credentials:
    username: "  admin  "
    password: "  pass123  "
""")
        config = parse_config(config_path)
        assert config.authentication.login_url == "https://example.com/login"
        assert config.authentication.credentials.username == "admin"
        assert config.authentication.credentials.password == "pass123"

    def test_config_with_whitespace_rules_stripped(self, tmp_path):
        config_path = _write_config(tmp_path, """
rules:
  avoid:
    - description: "  skip logout  "
      type: url_path
      value: "  /logout  "
  focus:
    - description: "  test API  "
      type: url_path
      value: "  /api  "
""")
        config = parse_config(config_path)
        assert config.rules.avoid[0].description == "skip logout"
        assert config.rules.avoid[0].value == "/logout"
        assert config.rules.focus[0].description == "test API"
        assert config.rules.focus[0].value == "/api"

    def test_config_with_uppercase_login_type_normalized(self, tmp_path):
        config_path = _write_config(tmp_path, """
authentication:
  login_type: FORM
  login_url: "https://example.com/login"
  credentials:
    username: admin
    password: pass123
""")
        config = parse_config(config_path)
        assert config.authentication.login_type == "form"

    def test_config_with_uppercase_rule_type_normalized(self, tmp_path):
        config_path = _write_config(tmp_path, """
rules:
  avoid:
    - description: "skip logout"
      type: URL_PATH
      value: "/logout"
""")
        config = parse_config(config_path)
        assert config.rules.avoid[0].type == "url_path"


# ---------------------------------------------------------------------------
# Multi-identity accounts (子项目2 T1)
# ---------------------------------------------------------------------------

def test_parse_config_valid_accounts(tmp_path):
    from supernova_core.config.parser import parse_config
    config_path = _write_config(tmp_path, """
authentication:
  login_type: form
  login_url: https://example.com/login
  credentials: { username: userA, password: "***" }
accounts:
  - id: victim-b
    role: user
    tier: low
    credentials: { username: userB, password: "***" }
  - id: admin-1
    role: admin
    tier: high
    credentials: { username: admin, password: "***" }
""")
    config = parse_config(config_path)
    assert len(config.accounts) == 2
    assert config.accounts[0].tier == "low"

def test_parse_config_rejects_bad_account_slug(tmp_path):
    config_path = _write_config(tmp_path, """
authentication:
  login_type: form
  login_url: https://x/login
  credentials: { username: u, password: p }
accounts:
  - { id: "Bad ID!", role: user, tier: low, credentials: { username: u2 } }
""")
    with pytest.raises(PentestError, match="must match"):
        parse_config(config_path)

def test_parse_config_rejects_duplicate_account_id(tmp_path):
    config_path = _write_config(tmp_path, """
authentication: { login_type: form, login_url: https://x/login, credentials: { username: u, password: p } }
accounts:
  - { id: dup, role: user, tier: low, credentials: { username: a } }
  - { id: dup, role: admin, tier: high, credentials: { username: b } }
""")
    with pytest.raises(PentestError, match="duplicate"):
        parse_config(config_path)

def test_parse_config_rejects_accounts_without_authentication(tmp_path):
    config_path = _write_config(tmp_path, """
accounts:
  - { id: a1, role: user, tier: low, credentials: { username: u } }
""")
    with pytest.raises(PentestError, match="authentication"):
        parse_config(config_path)


# ---------------------------------------------------------------------------
# 修复 B（2026-09-03）：SUPERNOVA_BROWSER_ENGINE 读取经 ws_getenv——ws 级引擎
# 覆盖（ws 设置页 env 段 → worker set_scan_env）此前进不了 parse_config
# （裸 os.environ），认证/扫描引擎与 ws 配置脱节。
# ---------------------------------------------------------------------------

def test_parse_config_browser_engine_ws_env_override(tmp_path):
    """ws 级 SUPERNOVA_BROWSER_ENGINE 覆盖进 parse_config。"""
    from supernova_core.config.scan_env import set_scan_env, clear_scan_env

    config_path = _write_config(tmp_path, 'description: "x"\n')
    set_scan_env({"SUPERNOVA_BROWSER_ENGINE": "playwright"})
    try:
        config = parse_config(config_path)
        assert config.browser_engine == "playwright"
    finally:
        clear_scan_env()


def test_parse_config_browser_engine_ws_env_beats_process_env(tmp_path, monkeypatch):
    """ws 覆盖压过进程 env（ws_getenv 语义：覆盖层优先于 os.environ）。"""
    from supernova_core.config.scan_env import set_scan_env, clear_scan_env

    config_path = _write_config(tmp_path, 'description: "x"\n')
    monkeypatch.setenv("SUPERNOVA_BROWSER_ENGINE", "agent-browser")
    set_scan_env({"SUPERNOVA_BROWSER_ENGINE": "playwright"})
    try:
        config = parse_config(config_path)
        assert config.browser_engine == "playwright"
    finally:
        clear_scan_env()


def test_parse_config_browser_engine_process_env_without_ws_layer(tmp_path, monkeypatch):
    """无覆盖层时进程 env 照旧生效（CLI 回归保护，行为不变）。"""
    config_path = _write_config(tmp_path, 'description: "x"\n')
    monkeypatch.setenv("SUPERNOVA_BROWSER_ENGINE", "playwright")
    config = parse_config(config_path)
    assert config.browser_engine == "playwright"
