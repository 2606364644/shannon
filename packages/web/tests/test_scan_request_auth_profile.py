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


def test_blackbox_credential_ids_subset_with_profile_ok():
    """2026-08-06 子集模式：profile_id + cred_ids[] 合法（多角色子集）。"""
    r = _bb(auth_profile_id="prof_1", auth_credential_ids=["cred_a", "cred_b"])
    assert r.auth_profile_id == "prof_1"
    assert r.auth_credential_ids == ["cred_a", "cred_b"]


def test_blackbox_credential_ids_without_profile_rejected():
    """cred_ids 无 profile_id 非法（必须依附 profile）。"""
    with pytest.raises(ValidationError):
        _bb(auth_credential_ids=["cred_a"])


def test_blackbox_credential_ids_with_inline_rejected():
    """cred_ids + inline authentication 仍非法（互斥）。"""
    with pytest.raises(ValidationError):
        _bb(auth_profile_id="prof_1", auth_credential_ids=["cred_a"],
            authentication={"login_type": "form", "login_url": "http://t/",
                            "credentials": {"username": "a"}})


def test_blackbox_auth_accounts_with_authentication_ok():
    """inline 多角色：auth_accounts（附加角色）与 authentication 同时提供，合法。"""
    r = _bb(authentication={"login_type": "form", "login_url": "http://t/",
                            "credentials": {"username": "a"}},
            auth_accounts=[{"role": "user", "username": "b", "password": "pw"}])
    assert r.auth_accounts == [{"role": "user", "username": "b", "password": "pw"}]


def test_blackbox_auth_accounts_without_authentication_rejected():
    """auth_accounts 必须依附 authentication（inline 多角色附加账号），单独发非法。"""
    with pytest.raises(ValidationError):
        _bb(auth_accounts=[{"role": "user", "username": "b", "password": "pw"}])


def test_blackbox_auth_accounts_with_profile_rejected():
    """auth_accounts 属 inline 侧，与认证档案互斥。"""
    with pytest.raises(ValidationError):
        _bb(auth_profile_id="prof_1",
            authentication={"login_type": "form", "login_url": "http://t/",
                            "credentials": {"username": "a"}},
            auth_accounts=[{"role": "user", "username": "b"}])
