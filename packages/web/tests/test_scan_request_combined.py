"""ScanRequest 组合扫描 validator（_whitebox_combined_optional）。

spec §6.1：
- type=="whitebox" 且带 url → 组合模式（认证字段走与黑盒相同的互斥校验）。
- type=="whitebox" 无 url 但有认证字段 → 非法（纯白盒禁认证）。
- type=="whitebox" 无 url 无认证 → 纯白盒（现状，零回归）。

纯黑盒路径（_auth_profile_xor_inline）不受影响——见 test_scan_request_auth_profile.py。
"""
import pytest
from pydantic import ValidationError

from supernova_web.models import ScanRequest


def _wb(**kw):
    """白盒请求基础（repo source + workspace）。"""
    base = {
        "type": "whitebox",
        "source": {"kind": "repo", "value": "demo-repo"},
        "workspace": "ws-1",
    }
    base.update(kw)
    return ScanRequest(**base)


# ── 组合模式（whitebox + url）合法 ──────────────────────────────────────────

def test_whitebox_with_url_and_inline_auth_is_combined():
    """whitebox + url + inline authentication → 组合模式，合法。"""
    r = _wb(url="http://target.example/",
            authentication={"login_type": "form", "login_url": "http://t/",
                            "credentials": {"username": "a"}})
    assert r.url == "http://target.example/"
    assert r.authentication is not None


def test_whitebox_with_url_and_profile_auth_is_combined():
    """whitebox + url + 档案认证 → 组合模式，合法（三子模式之一）。"""
    r = _wb(url="http://target.example/",
            auth_profile_id="prof_1", auth_credential_id="cred_a")
    assert r.auth_profile_id == "prof_1"


def test_whitebox_combined_auth_xor_enforced_profile_vs_inline():
    """组合模式下 profile + inline 互斥（复用黑盒同款校验）。"""
    with pytest.raises(ValidationError):
        _wb(url="http://target.example/",
            auth_profile_id="prof_1", auth_credential_id="cred_a",
            authentication={"login_type": "form", "login_url": "http://t/",
                            "credentials": {"username": "a"}})


def test_whitebox_combined_auth_accounts_without_authentication_rejected():
    """组合模式下 auth_accounts 必须依附 authentication（与黑盒同款规则）。"""
    with pytest.raises(ValidationError):
        _wb(url="http://target.example/",
            auth_accounts=[{"role": "user", "username": "b", "password": "pw"}])


def test_whitebox_combined_credential_without_profile_rejected():
    """组合模式下 cred_id 无 profile_id 非法（与黑盒同款）。"""
    with pytest.raises(ValidationError):
        _wb(url="http://target.example/", auth_credential_id="cred_a")


def test_whitebox_combined_no_auth_is_valid():
    """whitebox + url 但无认证（公开目标，不登录）合法——认证字段可选，非必填。"""
    r = _wb(url="http://target.example/")
    assert r.url == "http://target.example/"
    assert r.authentication is None
    assert r.auth_profile_id is None


# ── 纯白盒（无 url）禁认证 ─────────────────────────────────────────────────

def test_whitebox_without_url_rejects_inline_auth():
    """无 url 的纯白盒禁 inline authentication（防误传）。"""
    with pytest.raises(ValidationError):
        _wb(authentication={"login_type": "form", "login_url": "http://t/",
                            "credentials": {"username": "a"}})


def test_whitebox_without_url_rejects_profile_auth():
    """无 url 的纯白盒禁 profile 认证字段（防误传）。"""
    with pytest.raises(ValidationError):
        _wb(auth_profile_id="prof_1", auth_credential_id="cred_a")


def test_whitebox_without_url_rejects_auth_accounts():
    """无 url 的纯白盒禁 auth_accounts（防误传）。"""
    with pytest.raises(ValidationError):
        _wb(auth_accounts=[{"role": "user", "username": "b", "password": "pw"}])


# ── 纯白盒（无 url 无认证）零回归 ──────────────────────────────────────────

def test_whitebox_pure_no_url_no_auth_is_valid():
    """纯白盒（无 url、无认证）= 现状，合法（零回归）。"""
    r = _wb()
    assert r.url is None
    assert r.authentication is None
    assert r.source is not None
