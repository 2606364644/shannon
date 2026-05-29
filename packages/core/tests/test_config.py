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
