"""ScanRequest:auth_profile_id/credential_id 与 authentication 二选一(blackbox)。"""
import pytest
from pydantic import ValidationError
from supernova_web.models import ScanRequest


def _bb(**kw):
    base = {"type": "blackbox", "reuse_whitebox_scan_id": "wb-1"}
    base.update(kw)
    return ScanRequest(**base)


def test_blackbox_inline_auth_ok():
    r = _bb(authentication={"login_type": "form", "login_url": "http://t/",
                            "credentials": {"username": "a"}})
    assert r.authentication is not None


def test_blackbox_profile_auth_ok():
    r = _bb(auth_profile_id="prof_1", auth_credential_id="cred_a")
    assert r.auth_profile_id == "prof_1"


def test_blackbox_both_profile_and_inline_rejected():
    with pytest.raises(ValidationError):
        _bb(auth_profile_id="prof_1", auth_credential_id="cred_a",
            authentication={"login_type": "form", "login_url": "http://t/",
                            "credentials": {"username": "a"}})


def test_auth_profile_id_without_credential_id_is_valid_multi_identity():
    """只给 profile_id（无 cred_id）= 多身份模式，应合法。

    子项目2 T10：放宽原"_auth_profile_id 必须配 cred_id"硬约束——profile_id 单独
    即合法（scan_manager 展开所有 credentials 为 accounts[]）；profile_id + cred_id
    仍合法（单角色，现状）；profile_id + inline authentication 仍非法（互斥）。
    """
    req = _bb(auth_profile_id="prof_1")
    assert req.auth_profile_id == "prof_1"
    assert req.auth_credential_id is None


def test_blackbox_credential_id_without_profile_rejected():
    """cred_id 无 profile_id 仍非法（cred_id 必须依附 profile）。"""
    with pytest.raises(ValidationError):
        _bb(auth_credential_id="cred_a")
