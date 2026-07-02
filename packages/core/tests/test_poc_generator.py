# packages/core/tests/test_poc_generator.py
import pytest
from shannon_core.services.poc_generator import (
    HttpRequestSpec, ConfidenceBand, AuthState,
    extract_method_path, extract_param_name, derive_method_path,
    classify_confidence, resolve_host, derive_auth_state, auth_header,
)


def test_extract_method_path_from_source():
    assert extract_method_path("GET /share?locale=PAYLOAD → page.js") == ("GET", "/share")
    assert extract_method_path(None) == (None, None)
    assert extract_method_path("no http here") == (None, None)


def test_extract_param_name():
    assert extract_param_name("GET /share?locale=PAYLOAD") == "locale"
    assert extract_param_name("GET /api/users?id=1") == "id"
    assert extract_param_name(None) is None


def test_resolve_host_real_and_placeholder():
    assert resolve_host("https://invite-code.moomoo.com") == "https://invite-code.moomoo.com"
    assert resolve_host("") == "https://TARGET[:PORT]"
    assert resolve_host(None) == "https://TARGET[:PORT]"


def test_classify_confidence_verdict_overrides_confidence():
    class V:
        verdict = "vulnerable"
        confidence = "needs_review"
    assert classify_confidence(V(), is_accepted=False) == ConfidenceBand.CONFIRMED

    class H:
        verdict = None
        confidence = "high"
    assert classify_confidence(H(), is_accepted=False) == ConfidenceBand.HIGH

    class S:
        verdict = None
        confidence = "needs_review"
    assert classify_confidence(S(), is_accepted=False) == ConfidenceBand.SUSPECTED

    class Acc:
        verdict = None
        confidence = "low"
    assert classify_confidence(Acc(), is_accepted=True) == ConfidenceBand.CONFIRMED


def test_derive_auth_state_and_header():
    assert derive_auth_state({"auth": "anon", "middleware": ""}) == AuthState.NONE
    assert derive_auth_state({"auth": "user", "middleware": "oa-login"}) == AuthState.REQUIRED
    assert derive_auth_state(None) == AuthState.UNKNOWN
    # jwt/token → Bearer
    assert auth_header(AuthState.REQUIRED, {"middleware": "jwt-verify"}) == {"Authorization": "Bearer <AUTH_TOKEN>"}
    # session/cookie → Cookie
    assert auth_header(AuthState.REQUIRED, {"middleware": "koa-session"}) == {"Cookie": "session=<SESSION_COOKIE>"}
    # 无需登录 → 无头
    assert auth_header(AuthState.NONE, None) == {}


# Task 2: recon 端点表解析
from pathlib import Path
from shannon_core.services.poc_generator import parse_recon_endpoints, find_endpoint_info

RECON_SAMPLE = """# Recon

Some intro.

## 2. Endpoint Map

| Method | Path | Handler | Auth (policy) | Parameters | Notes |
|--------|------|---------|---------------|------------|-------|
| GET | `/api/code/payload/share` | share | GUEST | none | public |

## 2.1 Endpoint Security Context

| Method | Path | Auth | Middleware | Framework Origin | Ownership Check | Notes |
|--------|------|------|------------|------------------|-----------------|-------|
| GET | `/api/code/payload/share` | anon | ticketFNS | manual | n/a | public |
| GET | `/api/score/:staffId` | user | oa-login, validator | manual | none | IDOR |
"""


def test_parse_recon_endpoints_finds_security_context(tmp_path):
    recon = tmp_path / "recon_deliverable.md"
    recon.write_text(RECON_SAMPLE, encoding="utf-8")
    eps = parse_recon_endpoints(recon)
    assert "/api/code/payload/share" in eps
    assert eps["/api/code/payload/share"]["auth"] == "anon"
    assert eps["/api/score/:staffid"]["middleware"] == "oa-login, validator"


def test_parse_recon_endpoints_missing_file(tmp_path):
    assert parse_recon_endpoints(tmp_path / "nope.md") == {}


def test_find_endpoint_info_exact_and_prefix():
    eps = {"/api/score/:staffid": {"auth": "user"}, "/api/code/payload/share": {"auth": "anon"}}
    assert find_endpoint_info(eps, "/api/score/:staffId") is not None
    assert find_endpoint_info(eps, "/unknown") is None


# Task 3: curl / Burp raw 双格式化
from shannon_core.services.poc_generator import to_curl, to_burp_raw


def test_to_curl_url_encodes_query():
    spec = HttpRequestSpec(
        method="GET", path="/api/users",
        query={"id": "' OR '1'='1"},
        headers={"Authorization": "Bearer <AUTH_TOKEN>"},
    )
    curl = to_curl(spec, "https://invite-code.moomoo.com")
    assert curl.startswith("curl -i -X GET 'https://invite-code.moomoo.com/api/users?")
    assert "%27" in curl  # 单引号被编码
    assert "Authorization: Bearer <AUTH_TOKEN>" in curl


def test_to_curl_placeholder_host():
    spec = HttpRequestSpec(method="GET", path="/share", query={"locale": "<script>"})
    curl = to_curl(spec, "https://TARGET[:PORT]")
    assert "https://TARGET[:PORT]/share?" in curl


def test_to_burp_raw_keeps_raw_payload():
    spec = HttpRequestSpec(
        method="GET", path="/api/users",
        query={"id": "' OR '1'='1"},
        headers={"Authorization": "Bearer <AUTH_TOKEN>"},
    )
    raw = to_burp_raw(spec, "https://invite-code.moomoo.com")
    assert raw.startswith("GET /api/users?id=' OR '1'='1 HTTP/1.1")
    assert "Host: invite-code.moomoo.com" in raw
    assert "' OR '1'='1" in raw  # 原始未编码


def test_to_burp_raw_post_body():
    spec = HttpRequestSpec(method="POST", path="/api/fetch", body="url=http://127.0.0.1:8080")
    raw = to_burp_raw(spec, "https://t.example.com")
    assert "POST /api/fetch HTTP/1.1" in raw
    assert "Content-Length:" in raw
    assert "url=http://127.0.0.1:8080" in raw
