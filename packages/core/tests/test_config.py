import pytest
from shannon_core.models.config import Config, Rule, Rules, ReportConfig
from shannon_core.config.parser import distribute_config

def test_empty_config():
    c = Config()
    assert c.rules is None
    assert c.description is None
    assert c.vuln_classes is None
    assert c.exploit is True

def test_config_with_rules():
    c = Config(rules=Rules(
        avoid=[Rule(description="skip logout", type="url_path", value="/logout")],
        focus=[Rule(description="test API", type="url_path", value="/api")],
    ))
    assert len(c.rules.avoid) == 1
    assert c.rules.avoid[0].value == "/logout"

def test_config_with_vuln_classes():
    c = Config(vuln_classes=["injection", "xss"])
    assert len(c.vuln_classes) == 2

def test_report_config():
    r = ReportConfig(min_severity="medium", min_confidence="high")
    assert r.min_severity == "medium"

def test_invalid_rule_type():
    with pytest.raises(Exception):
        Rule(description="bad", type="invalid_type", value="test")

def test_distributed_config():
    c = Config(
        rules=Rules(
            avoid=[Rule(description="skip", type="url_path", value="/admin")],
        ),
        description="Test app",
        vuln_classes=["injection"],
    )
    d = distribute_config(c)
    assert d.description == "Test app"
    assert len(d.avoid) == 1
    assert d.vuln_classes == ["injection"]
    assert d.exploit is True

def test_misconfig_in_vuln_class():
    c = Config(vuln_classes=["misconfig"])
    assert c.vuln_classes == ["misconfig"]

def test_all_vuln_classes_includes_misconfig():
    from shannon_core.models.config import ALL_VULN_CLASSES
    assert "misconfig" in ALL_VULN_CLASSES
    assert len(ALL_VULN_CLASSES) == 6

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


def test_auto_detect_whitebox_default():
    """auto_detect_whitebox should default to True."""
    c = Config()
    assert c.auto_detect_whitebox is True


def test_auto_detect_whitebox_disabled():
    """auto_detect_whitebox can be set to False."""
    c = Config(auto_detect_whitebox=False)
    assert c.auto_detect_whitebox is False
