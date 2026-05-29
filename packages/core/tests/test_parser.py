import pytest
from shannon_core.config.parser import parse_config, distribute_config
from shannon_core.models.config import Config
from shannon_core.models.errors import PentestError, ErrorCode

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
    assert len(d.vuln_classes) == 6
    assert d.exploit is True

def test_distribute_config_full():
    c = Config(description="My app", vuln_classes=["injection"])
    d = distribute_config(c)
    assert d.description == "My app"
    assert d.vuln_classes == ["injection"]
