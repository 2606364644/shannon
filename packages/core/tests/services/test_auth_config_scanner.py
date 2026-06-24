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
