import textwrap
from pathlib import Path

import pytest

from shannon_core.services.auth_config_scanner import (
    AuthConfigScanResult,
    scan_auth_config,
)


def _write(repo: Path, name: str, content: str) -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(textwrap.dedent(content))


@pytest.mark.asyncio
async def test_cookie_missing_httponly_and_secure_flagged(tmp_path):
    _write(tmp_path, "app.js", """
        app.post('/login', (req, res) => {
          res.cookie('session', token);  // bare cookie, no flags set
        });
    """)
    result = await scan_auth_config(str(tmp_path))
    assert len(result.cookie_findings) == 1
    f = result.cookie_findings[0]
    assert f.cookie_name == "session"
    assert "HttpOnly" in f.missing_flags
    assert "Secure" in f.missing_flags


@pytest.mark.asyncio
async def test_cookie_with_all_flags_not_flagged(tmp_path):
    _write(tmp_path, "app.js", """
        res.cookie('session', token, { httpOnly: true, secure: true, sameSite: 'lax' });
    """)
    result = await scan_auth_config(str(tmp_path))
    assert result.cookie_findings == []


@pytest.mark.asyncio
async def test_hsts_absent_flagged(tmp_path):
    # No HSTS anywhere → flagged per-endpoint (app entry)
    _write(tmp_path, "app.js", """
        const app = express();
        app.listen(3000);
    """)
    result = await scan_auth_config(str(tmp_path))
    assert len(result.hsts_findings) >= 1
    assert any("absent" in f.detail.lower() or "missing" in f.detail.lower()
               for f in result.hsts_findings)


@pytest.mark.asyncio
async def test_hsts_weak_max_age_flagged(tmp_path):
    _write(tmp_path, "app.js", """
        app.use(helmet.hsts({ maxAge: 3600 }));  // 1 hour, too short
    """)
    result = await scan_auth_config(str(tmp_path))
    assert len(result.hsts_findings) == 1
    assert "31536000" in result.hsts_findings[0].detail or "weak" in result.hsts_findings[0].detail.lower()


@pytest.mark.asyncio
async def test_cors_wildcard_origin_flagged(tmp_path):
    _write(tmp_path, "app.js", """
        app.use(cors({ origin: '*' }));
    """)
    result = await scan_auth_config(str(tmp_path))
    assert len(result.cors_findings) == 1
    assert "wildcard" in result.cors_findings[0].detail.lower() or "*" in result.cors_findings[0].detail


@pytest.mark.asyncio
async def test_cors_credentials_with_wildcard_flagged(tmp_path):
    _write(tmp_path, "app.js", """
        app.use(cors({ origin: '*', credentials: true }));
    """)
    result = await scan_auth_config(str(tmp_path))
    # wildcard already flags; credentials+wildcard is the severe variant
    assert len(result.cors_findings) >= 1
    assert any("credentials" in f.detail.lower() for f in result.cors_findings)


@pytest.mark.asyncio
async def test_scan_skips_node_modules_and_vendor(tmp_path):
    _write(tmp_path, "node_modules/lib/app.js", """
        res.cookie('session', x);  // should be ignored
    """)
    result = await scan_auth_config(str(tmp_path))
    assert result.cookie_findings == []


@pytest.mark.asyncio
async def test_scan_result_is_serializable_via_dataclasses_asdict(tmp_path):
    import dataclasses
    result = await scan_auth_config(str(tmp_path))
    # Must round-trip via asdict (activities.py uses this for JSON write)
    data = dataclasses.asdict(result)
    assert "cookie_findings" in data
    assert "hsts_findings" in data
    assert "cors_findings" in data


@pytest.mark.asyncio
async def test_jwt_uses_sub_not_flagged(tmp_path):
    _write(tmp_path, "auth.js", """
        const payload = jwt.verify(token, secret);
        const userId = payload.sub;  // correct: immutable subject
    """)
    result = await scan_auth_config(str(tmp_path))
    assert result.jwt_claim_findings == []


@pytest.mark.asyncio
async def test_jwt_uses_email_for_identity_flagged_noauth(tmp_path):
    """nOAuth: using mutable 'email' claim as identity instead of 'sub'."""
    _write(tmp_path, "auth.js", """
        const payload = jwt.decode(token);
        const userId = payload.email;  // attacker can change this
        const user = User.findByEmail(userId);
    """)
    result = await scan_auth_config(str(tmp_path))
    assert len(result.jwt_claim_findings) == 1
    f = result.jwt_claim_findings[0]
    assert "email" in f.detail
    assert "sub" in f.detail  # should mention the safe alternative


@pytest.mark.asyncio
async def test_jwt_uses_preferred_username_flagged(tmp_path):
    _write(tmp_path, "auth.py", """
        payload = jwt.decode(token, key, algorithms=['HS256'])
        username = payload['preferred_username']
        session['user'] = username
    """)
    result = await scan_auth_config(str(tmp_path))
    assert len(result.jwt_claim_findings) == 1
    assert "preferred_username" in result.jwt_claim_findings[0].detail


@pytest.mark.asyncio
async def test_jwt_decode_without_claim_access_not_flagged(tmp_path):
    """Just decoding without reading a mutable claim — not enough signal."""
    _write(tmp_path, "auth.js", """
        const payload = jwt.verify(token, secret);
        // no claim access in window
    """)
    result = await scan_auth_config(str(tmp_path))
    assert result.jwt_claim_findings == []


@pytest.mark.asyncio
async def test_login_endpoint_without_rate_limit_flagged(tmp_path):
    _write(tmp_path, "routes/auth.js", """
        router.post('/login', async (req, res) => {
          const user = await auth.login(req.body);
        });
    """)
    result = await scan_auth_config(str(tmp_path))
    assert len(result.rate_limit_findings) == 1
    assert "/login" in result.rate_limit_findings[0].detail


@pytest.mark.asyncio
async def test_login_endpoint_with_rate_limit_not_flagged(tmp_path):
    _write(tmp_path, "routes/auth.js", """
        const limiter = rateLimit({ windowMs: 60000, max: 5 });
        router.post('/login', limiter, async (req, res) => {
          const user = await auth.login(req.body);
        });
    """)
    result = await scan_auth_config(str(tmp_path))
    assert result.rate_limit_findings == []


@pytest.mark.asyncio
async def test_reset_and_token_endpoints_flagged(tmp_path):
    _write(tmp_path, "app.py", """
        @app.route('/reset', methods=['POST'])
        def reset(): pass
        @app.route('/token', methods=['POST'])
        def token(): pass
    """)
    result = await scan_auth_config(str(tmp_path))
    endpoints = {f.detail for f in result.rate_limit_findings}
    assert any("/reset" in e for e in endpoints)
    assert any("/token" in e for e in endpoints)


@pytest.mark.asyncio
async def test_non_auth_endpoint_not_flagged_for_rate_limit(tmp_path):
    _write(tmp_path, "routes/items.js", """
        router.get('/items', (req, res) => { res.json([]); });
    """)
    result = await scan_auth_config(str(tmp_path))
    assert result.rate_limit_findings == []
