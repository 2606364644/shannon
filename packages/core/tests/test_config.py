import pytest
from supernova_core.models.config import Config, Rule, Rules, ReportConfig
from supernova_core.config.parser import distribute_config

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


def test_email_login_model():
    from supernova_core.models.config import EmailLogin
    el = EmailLogin(address="user@example.com", password="secret")
    assert el.address == "user@example.com"
    assert el.password == "secret"
    assert el.totp_secret is None

def test_email_login_with_totp():
    from supernova_core.models.config import EmailLogin
    el = EmailLogin(address="user@example.com", password="secret", totp_secret="JBSWY3DPEHPK3PXP")
    assert el.totp_secret == "JBSWY3DPEHPK3PXP"

def test_credentials_with_email_login():
    from supernova_core.models.config import Credentials, EmailLogin
    creds = Credentials(
        username="admin",
        password="pass123",
        email_login=EmailLogin(address="admin@corp.com", password="email-pass"),
    )
    assert creds.email_login.address == "admin@corp.com"
    assert creds.email_login.password == "email-pass"

def test_credentials_without_email_login():
    from supernova_core.models.config import Credentials
    creds = Credentials(username="admin", password="pass123")
    assert creds.email_login is None

def test_authentication_with_email_login():
    from supernova_core.models.config import Authentication, Credentials, EmailLogin
    auth = Authentication(
        login_type="form",
        login_url="https://example.com/login",
        credentials=Credentials(
            username="admin",
            password="pass123",
            email_login=EmailLogin(address="admin@corp.com", password="email-pass"),
        ),
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


def test_config_rejects_unknown_vuln_class():
    """F1 防回退: YAML vuln_classes 非法值 → pydantic Literal 在加载时拒绝（parse_config 当场报错），
    不流到 AgentName("foo-vuln") 运行中崩溃。CLI/env 经 resolve_vuln_classes→_parse_and_validate 校验，
    YAML 经 pydantic Literal 校验——两侧都 fail fast。锁住 Literal 不被未来放宽。

    背景：final review 曾误报 YAML 值未校验（F1）；实测 pydantic 对 list[VulnClass]（VulnClass=Literal[...]）
    逐元素校验，非法值在 Config 构造时即 ValidationError。本测试锁住该行为。
    """
    from pydantic import ValidationError
    with pytest.raises(ValidationError) as exc:
        Config(vuln_classes=["injection", "foo"])
    msg = str(exc.value)
    assert "foo" in msg
    for legal in ("injection", "xss", "auth", "authz", "ssrf"):
        assert legal in msg
