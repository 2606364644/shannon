# packages/web/tests/test_auth_sso.py
"""auth/sso.py 单测（spec 2026-08-25 §5.2/§5.3）：URL 拼接/编码、next 防护、响应校验、transport 注入。"""
import time
from urllib.parse import quote, urlencode

import httpx
import pytest

from supernova_web.auth import sso


def test_safe_next_table():
    assert sso.safe_next("/p/foo?x=1") == "/p/foo?x=1"
    assert sso.safe_next("/") == "/"
    assert sso.safe_next(None) == "/"
    assert sso.safe_next("") == "/"
    assert sso.safe_next("//evil.com") == "/"       # 协议相对
    assert sso.safe_next("/\\evil.com") == "/"      # 反斜杠绕过
    assert sso.safe_next("https://evil.com") == "/"
    assert sso.safe_next("p/foo") == "/"


def test_build_passport_login_url_double_encoding():
    """returnUrl 整体编码；其内的 next 再编码一层（嵌套正确性，spec §5.2）。"""
    url = sso.build_passport_login_url(
        "https://passport.futuoa.com", "https://codescan.test.local", "/p/ws-1?tab=live")
    callback = "https://codescan.test.local/api/auth/sso/callback?" + urlencode({"next": "/p/ws-1?tab=live"})
    expected = "https://passport.futuoa.com/site/login.html?" + urlencode({"returnUrl": callback})
    assert url == expected


def test_build_passport_logout_url():
    url = sso.build_passport_logout_url("https://passport.futuoa.com", "https://codescan.test.local")
    assert url == ("https://passport.futuoa.com/site/logout.html?returnUrl="
                   + quote("https://codescan.test.local/login", safe=""))


def _payload(**over):
    base = {
        "result": 0, "code": 0, "message": "success",
        "data": {
            "oaToken": "tok", "oaTokenInitTime": 1000, "oaTokenInvalidTime": 2000,
            "userInfo": {"uid": 8537, "nick": "牛同学", "avatarUrl": "https://cdn.test/a.png"},
        },
    }
    base.update(over)
    return base


def test_parse_success():
    info = sso.parse_validate_response(_payload(), now=1500)
    assert info.nick == "牛同学"
    assert info.avatar_url == "https://cdn.test/a.png"
    assert info.uid == 8537


def test_parse_rejects_nonzero_result():
    with pytest.raises(sso.SsoTicketError) as ei:
        sso.parse_validate_response(_payload(result=1), now=1500)
    assert ei.value.code == "invalid_response"


def test_parse_rejects_missing_nick():
    p = _payload()
    p["data"]["userInfo"] = {"uid": 1, "nick": "  "}
    with pytest.raises(sso.SsoTicketError) as ei:
        sso.parse_validate_response(p, now=1500)
    assert ei.value.code == "missing_nick"


def test_parse_rejects_expired_and_not_yet_valid():
    with pytest.raises(sso.SsoTicketError) as ei:
        sso.parse_validate_response(_payload(), now=2500)
    assert ei.value.code == "token_expired"
    with pytest.raises(sso.SsoTicketError) as ei:
        sso.parse_validate_response(_payload(), now=500)
    assert ei.value.code == "token_not_yet_valid"


def test_parse_rejects_nonnumeric_times():
    p = _payload()
    p["data"]["oaTokenInitTime"] = "1000"
    with pytest.raises(sso.SsoTicketError) as ei:
        sso.parse_validate_response(p, now=1500)
    assert ei.value.code == "invalid_response"


def test_validate_ticket_via_mock_transport():
    now = int(time.time())
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["authTicket"] == "T-1"
        assert request.url.params["authDomain"] == "codescan.test.local"
        p = _payload()
        p["data"]["oaTokenInitTime"] = now - 60
        p["data"]["oaTokenInvalidTime"] = now + 3600
        return httpx.Response(200, json=p)
    info = sso.validate_ticket("https://passport.futuoa.com", "codescan.test.local", "T-1",
                               transport=httpx.MockTransport(handler))
    assert info.nick == "牛同学"


def test_validate_ticket_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)
    with pytest.raises(sso.SsoTicketError) as ei:
        sso.validate_ticket("https://passport.futuoa.com", "d", "T-1",
                            transport=httpx.MockTransport(handler))
    assert ei.value.code == "upstream_error"
