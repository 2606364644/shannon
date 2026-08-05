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


def test_blackbox_profile_without_credential_rejected():
    with pytest.raises(ValidationError):
        _bb(auth_profile_id="prof_1")  # 缺 auth_credential_id
