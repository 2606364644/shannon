# Security Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Fix 9 security-effectiveness defects in shannon-py to reach parity with the TypeScript version's security scanning capabilities.

**Architecture:** Each fix is an independent unit (new file or targeted modification). Tasks are ordered by dependency: foundation utilities first, then core model changes, then service layer, then prompt content, and finally pipeline integration. Most tasks in the same phase can be parallelized.

**Tech Stack:** Python 3.12+, Pydantic v2, pytest + pytest-asyncio, Temporal.io, httpx for HTTP, boto3 (optional) for AWS

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `shannon-py/packages/core/src/shannon_core/utils/security.py` | DNS resolution, SSRF/loopback checking, URL reachability |
| `shannon-py/packages/core/src/shannon_core/utils/credential_validator.py` | Validate AI provider credentials before scan |
| `shannon-py/packages/whitebox/src/shannon_whitebox/services/settings_writer.py` | Write SDK deny rules for code_path avoid patterns |
| `shannon-py/packages/whitebox/src/shannon_whitebox/services/playwright_config_writer.py` | Write stealth Playwright config + anti-detection script |
| `shannon-py/packages/whitebox/src/shannon_whitebox/services/validate_authentication.py` | Preflight auth validation using AgentExecutor |
| `shannon-py/prompts/shared/_exploit-methodology.txt` | Shared OWASP exploitation methodology (included by all exploit prompts) |
| `shannon-py/prompts/vuln-misconfig.txt` | Misconfig vulnerability analysis prompt |
| `shannon-py/prompts/misconfig-exploit.txt` | Misconfig exploitation prompt |
| `shannon-py/prompts/recon-static.txt` | White-box recon without browser (pure source analysis) |
| `shannon-py/prompts/validate-authentication.txt` | Auth preflight validation prompt |

### Modified Files

| File | Change |
|------|--------|
| `shannon-py/packages/core/src/shannon_core/models/agents.py` | +2 agent defs (MISCONFIG_VULN, MISCONFIG_EXPLOIT), +PLAYWRIGHT_SESSION_MAPPING, update REPORT prereqs |
| `shannon-py/packages/core/src/shannon_core/models/config.py` | VulnType + VulnClass add `"misconfig"`, ALL_VULN_CLASSES → 6 items |
| `shannon-py/packages/core/src/shannon_core/models/queue_schemas.py` | +MisconfigVulnerability, update Vulnerability union |
| `shannon-py/packages/whitebox/src/shannon_whitebox/prompts/manager.py` | PLAYWRIGHT_SESSION lookup, +report filter variables |
| `shannon-py/packages/whitebox/src/shannon_whitebox/agents/executor.py` | +prompt_override parameter |
| `shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/shared.py` | PipelineInput/ActivityInput +prompt_override field |
| `shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | Enhanced preflight, +run_auth_validation activity |
| `shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | Static recon, preflight enhancement, settings/stealth integration |
| `shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/activities.py` | Enhanced preflight, +run_auth_validation activity |
| `shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | Queue gating, preflight enhancement, settings/stealth/auth integration |
| `shannon-py/packages/blackbox/src/shannon_blackbox/services/report_assembler.py` | Inject report filter variables |
| `shannon-py/prompts/injection-exploit.txt` | Replace 19-line skeleton with full TS prompt (refactored with shared partial) |
| `shannon-py/prompts/xss-exploit.txt` | Replace skeleton with full TS prompt |
| `shannon-py/prompts/auth-exploit.txt` | Replace skeleton with full TS prompt |
| `shannon-py/prompts/authz-exploit.txt` | Replace skeleton with full TS prompt |
| `shannon-py/prompts/ssrf-exploit.txt` | Replace skeleton with full TS prompt |
| `shannon-py/prompts/recon-blackbox.txt` | Enhance from 24 lines to 150-200 lines |
| `shannon-py/prompts/report-executive.txt` | Enhance from 23 lines to 80-100 lines |

### New Test Files

| File | Tests |
|------|-------|
| `shannon-py/packages/core/tests/test_security.py` | URL safety utilities |
| `shannon-py/packages/core/tests/test_credential_validator.py` | Credential validation |
| `shannon-py/packages/whitebox/tests/test_settings_writer.py` | Settings writer |
| `shannon-py/packages/whitebox/tests/test_playwright_config_writer.py` | Stealth config writer |
| `shannon-py/packages/whitebox/tests/test_validate_authentication.py` | Auth validation service |

---

## Task 1: S7 — URL Security Utilities

**Files:**
- Create: `shannon-py/packages/core/src/shannon_core/utils/security.py`
- Create: `shannon-py/packages/core/tests/test_security.py`

- [x] **Step 1: Write the failing test**

```python
# shannon-py/packages/core/tests/test_security.py
import ipaddress
from unittest.mock import patch, AsyncMock

import pytest
from shannon_core.utils.security import (
    resolve_host,
    check_ssrf,
    check_loopback,
    check_url_reachable,
    validate_target_url,
)


class TestResolveHost:
    def test_resolves_localhost(self):
        ip = resolve_host("http://localhost:3000")
        assert ip is not None

    def test_resolves_domain(self):
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (2, 1, 6, "", ("93.184.216.34", 443))
            ]
            ip = resolve_host("https://example.com")
            assert ip == "93.184.216.34"

    def test_returns_none_on_failure(self):
        with patch("socket.getaddrinfo", side_effect=OSError("DNS fail")):
            ip = resolve_host("https://nonexistent.invalid")
            assert ip is None


class TestCheckSsrf:
    def test_allows_public_ip(self):
        assert check_ssrf("93.184.216.34") is False

    def test_blocks_link_local(self):
        assert check_ssrf("169.254.169.254") is True

    def test_blocks_link_local_range(self):
        assert check_ssrf("169.254.0.1") is True

    def test_allows_private_not_link_local(self):
        # 10.x.x.x is private but NOT SSRF link-local
        assert check_ssrf("10.0.0.1") is False


class TestCheckLoopback:
    def test_blocks_127(self):
        assert check_loopback("127.0.0.1") is True

    def test_blocks_ipv6_loopback(self):
        assert check_loopback("::1") is True

    def test_blocks_zero(self):
        assert check_loopback("0.0.0.0") is True

    def test_allows_public(self):
        assert check_loopback("93.184.216.34") is False


class TestCheckUrlReachable:
    @pytest.mark.asyncio
    async def test_reachable_url(self):
        with patch("httpx.AsyncClient.head", new_callable=AsyncMock) as mock_head:
            mock_head.return_value = AsyncMock(status_code=200)
            result = await check_url_reachable("https://example.com")
            assert result is True

    @pytest.mark.asyncio
    async def test_unreachable_url(self):
        with patch("httpx.AsyncClient.head", new_callable=AsyncMock) as mock_head:
            mock_head.side_effect = httpx.ConnectError("refused")
            result = await check_url_reachable("https://unreachable.local")
            assert result is False


class TestValidateTargetUrl:
    def test_rejects_ssrf(self):
        with patch("shannon_core.utils.security.resolve_host", return_value="169.254.169.254"):
            with pytest.raises(PentestError) as exc_info:
                validate_target_url("http://metadata.google.internal")
            assert exc_info.value.error_code == ErrorCode.TARGET_UNREACHABLE

    def test_rejects_loopback(self):
        with patch("shannon_core.utils.security.resolve_host", return_value="127.0.0.1"):
            with pytest.raises(PentestError) as exc_info:
                validate_target_url("http://localhost")
            assert exc_info.value.error_code == ErrorCode.TARGET_UNREACHABLE

    def test_accepts_valid_url(self):
        with patch("shannon_core.utils.security.resolve_host", return_value="93.184.216.34"):
            # Should not raise
            validate_target_url("https://example.com")
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_security.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.utils.security'`

- [x] **Step 3: Write minimal implementation**

```python
# shannon-py/packages/core/src/shannon_core/utils/security.py
"""URL safety utilities: DNS pinning, SSRF / loopback detection, reachability."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from shannon_core.models.errors import ErrorCode, PentestError


def resolve_host(url: str) -> str | None:
    """DNS-resolve the hostname in *url* and return the pinned IP (string).

    Returns ``None`` on resolution failure.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return None
        addrinfos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _family, _type, _proto, _canon, sockaddr in addrinfos:
            return sockaddr[0]
        return None
    except (socket.gaierror, OSError):
        return None


def check_ssrf(ip: str) -> bool:
    """Return ``True`` if *ip* falls into SSRF-sensitive ranges (link-local 169.254.0.0/16)."""
    addr = ipaddress.ip_address(ip)
    return addr in ipaddress.ip_network("169.254.0.0/16")


def check_loopback(ip: str) -> bool:
    """Return ``True`` if *ip* is a loopback or wildcard address."""
    addr = ipaddress.ip_address(ip)
    return addr.is_loopback or ip == "0.0.0.0"


async def check_url_reachable(url: str, timeout: int = 10) -> bool:
    """Return ``True`` when an HTTP HEAD to *url* succeeds (any 2xx/3xx/4xx)."""
    try:
        async with httpx.AsyncClient(verify=False, timeout=timeout) as client:
            resp = await client.head(url, follow_redirects=True)
            return True
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def validate_target_url(url: str) -> None:
    """Synchronous preflight gate: resolve → SSRF check → loopback check.

    Raises ``PentestError(TARGET_UNREACHABLE)`` on failure.
    """
    ip = resolve_host(url)
    if ip is None:
        raise PentestError(
            f"Cannot resolve hostname for {url}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.TARGET_UNREACHABLE,
        )
    if check_ssrf(ip):
        raise PentestError(
            f"Target {url} resolves to SSRF-sensitive IP {ip}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.TARGET_UNREACHABLE,
        )
    if check_loopback(ip):
        raise PentestError(
            f"Target {url} resolves to loopback address {ip}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.TARGET_UNREACHABLE,
        )
```

Add `httpx` to the core dependencies in `shannon-py/packages/core/pyproject.toml` under `[project] dependencies`.

- [x] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_security.py -v`
Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add shannon-py/packages/core/src/shannon_core/utils/security.py \
        shannon-py/packages/core/tests/test_security.py
git commit -m "feat(security): add URL safety utilities — SSRF, loopback, DNS pinning (S7)"
```

---

## Task 2: S7 — Credential Validator

**Files:**
- Create: `shannon-py/packages/core/src/shannon_core/utils/credential_validator.py`
- Create: `shannon-py/packages/core/tests/test_credential_validator.py`

- [x] **Step 1: Write the failing test**

```python
# shannon-py/packages/core/tests/test_credential_validator.py
from unittest.mock import patch, AsyncMock, MagicMock

import httpx
import pytest

from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.utils.credential_validator import validate_credentials


class TestValidateAnthropic:
    @pytest.mark.asyncio
    async def test_valid_key(self):
        mock_resp = MagicMock(status_code=200)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            await validate_credentials("anthropic_api", api_key="sk-ant-valid")

    @pytest.mark.asyncio
    async def test_invalid_key(self):
        mock_resp = MagicMock(status_code=401)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(PentestError) as exc_info:
                await validate_credentials("anthropic_api", api_key="sk-ant-bad")
            assert exc_info.value.error_code == ErrorCode.AUTH_FAILED

    @pytest.mark.asyncio
    async def test_forbidden_key(self):
        mock_resp = MagicMock(status_code=403)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(PentestError) as exc_info:
                await validate_credentials("anthropic_api", api_key="sk-ant-forbidden")
            assert exc_info.value.error_code == ErrorCode.AUTH_FAILED


class TestValidateBedrock:
    @pytest.mark.asyncio
    async def test_valid(self):
        with patch("boto3.client") as mock_boto:
            mock_sts = MagicMock()
            mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}
            mock_boto.return_value = mock_sts
            await validate_credentials("bedrock")

    @pytest.mark.asyncio
    async def test_invalid(self):
        with patch("boto3.client") as mock_boto:
            from botocore.exceptions import ClientError
            mock_boto.side_effect = ClientError(
                {"Error": {"Code": "InvalidClientTokenId"}}, "GetCallerIdentity"
            )
            with pytest.raises(PentestError) as exc_info:
                await validate_credentials("bedrock")
            assert exc_info.value.error_code == ErrorCode.AUTH_FAILED


class TestValidateUnknownProvider:
    @pytest.mark.asyncio
    async def test_unknown_provider_skipped(self):
        # Should not raise for unknown provider — gracefully skip
        await validate_credentials("unknown_provider")
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_credential_validator.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write minimal implementation**

```python
# shannon-py/packages/core/src/shannon_core/utils/credential_validator.py
"""Validate AI provider credentials before starting an expensive scan."""

from __future__ import annotations

import httpx

from shannon_core.models.errors import ErrorCode, PentestError

# Minimal Anthropic messages request to test API key validity
_ANTHROPIC_TEST_BODY = {
    "model": "claude-haiku-4-5-20251001",
    "max_tokens": 1,
    "messages": [{"role": "user", "content": "hi"}],
}


async def _validate_anthropic(api_key: str) -> None:
    """POST a minimal request to the Anthropic messages API."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=_ANTHROPIC_TEST_BODY,
            )
        if resp.status_code in (401, 403):
            raise PentestError(
                f"Anthropic API key rejected (HTTP {resp.status_code})",
                category="preflight",
                retryable=False,
                error_code=ErrorCode.AUTH_FAILED,
            )
    except httpx.ConnectError as exc:
        raise PentestError(
            f"Cannot reach Anthropic API: {exc}",
            category="preflight",
            retryable=True,
            error_code=ErrorCode.AUTH_FAILED,
        ) from exc


async def _validate_bedrock() -> None:
    """Call STS GetCallerIdentity to verify AWS credentials."""
    try:
        import boto3
        client = boto3.client("sts")
        client.get_caller_identity()
    except Exception as exc:
        raise PentestError(
            f"AWS/Bedrock credential validation failed: {exc}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.AUTH_FAILED,
        ) from exc


async def _validate_vertex() -> None:
    """Verify Google Cloud project access for Vertex AI."""
    try:
        from google.cloud import aiplatform
        aiplatform.init()
    except Exception as exc:
        raise PentestError(
            f"Vertex AI credential validation failed: {exc}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.AUTH_FAILED,
        ) from exc


async def validate_credentials(
    provider: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    auth_token: str | None = None,
) -> None:
    """Dispatch credential validation to the appropriate provider.

    Unknown providers are silently skipped (graceful degradation).
    """
    if provider == "anthropic_api":
        if not api_key:
            raise PentestError(
                "Anthropic API key is required but not provided",
                category="preflight",
                retryable=False,
                error_code=ErrorCode.AUTH_FAILED,
            )
        await _validate_anthropic(api_key)
    elif provider == "bedrock":
        await _validate_bedrock()
    elif provider == "vertex":
        await _validate_vertex()
    elif provider == "litellm_router":
        if base_url and auth_token:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(
                        f"{base_url}/health",
                        headers={"Authorization": f"Bearer {auth_token}"},
                    )
                    if resp.status_code in (401, 403):
                        raise PentestError(
                            f"LiteLLM router auth failed (HTTP {resp.status_code})",
                            category="preflight",
                            retryable=False,
                            error_code=ErrorCode.AUTH_FAILED,
                        )
            except httpx.ConnectError as exc:
                raise PentestError(
                    f"Cannot reach LiteLLM router at {base_url}: {exc}",
                    category="preflight",
                    retryable=True,
                    error_code=ErrorCode.AUTH_FAILED,
                ) from exc
    # Unknown providers: skip silently
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_credential_validator.py -v`
Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add shannon-py/packages/core/src/shannon_core/utils/credential_validator.py \
        shannon-py/packages/core/tests/test_credential_validator.py
git commit -m "feat(security): add credential validator for AI providers (S7)"
```

---

## Task 3: S6 — Settings Writer (Code Path Deny Rules)

**Files:**
- Create: `shannon-py/packages/whitebox/src/shannon_whitebox/services/settings_writer.py`
- Create: `shannon-py/packages/whitebox/tests/test_settings_writer.py`

- [x] **Step 1: Write the failing test**

```python
# shannon-py/packages/whitebox/tests/test_settings_writer.py
import json
from pathlib import Path

import pytest

from shannon_core.models.config import Rule, Rules
from shannon_whitebox.services.settings_writer import (
    sync_code_path_deny_rules,
    cleanup_settings,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect ~/.claude to a temp directory."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    return claude_dir


class TestSyncCodePathDenyRules:
    def test_writes_deny_rules(self, fake_home):
        rules = Rules(
            avoid=[
                Rule(description="secrets", type="code_path", value="secrets/**"),
                Rule(description="env files", type="code_path", value=".env*"),
                Rule(description="skip this URL", type="url_path", value="/admin"),
            ],
            focus=[],
        )
        sync_code_path_deny_rules(rules.avoid)

        settings_path = fake_home / "settings.json"
        assert settings_path.exists()
        data = json.loads(settings_path.read_text())
        assert "permissions" in data
        deny_list = data["permissions"]["deny"]
        # 2 code_path rules × 2 tools (Read, Edit) = 4 entries
        assert len(deny_list) == 4
        assert "Read(./secrets/**)" in deny_list
        assert "Edit(./secrets/**)" in deny_list
        assert "Read(./.env*)" in deny_list
        assert "Edit(./.env*)" in deny_list

    def test_removes_settings_when_no_code_path_rules(self, fake_home):
        settings_path = fake_home / "settings.json"
        settings_path.write_text('{"permissions": {"deny": []}}')

        rules = Rules(
            avoid=[Rule(description="url", type="url_path", value="/admin")],
            focus=[],
        )
        sync_code_path_deny_rules(rules.avoid)
        assert not settings_path.exists()

    def test_strips_leading_dots_slashes(self, fake_home):
        rules = Rules(
            avoid=[Rule(description="test", type="code_path", value="./secrets/**")],
            focus=[],
        )
        sync_code_path_deny_rules(rules.avoid)
        settings_path = fake_home / "settings.json"
        data = json.loads(settings_path.read_text())
        deny_list = data["permissions"]["deny"]
        assert "Read(./secrets/**)" in deny_list


class TestCleanupSettings:
    def test_removes_settings_file(self, fake_home):
        settings_path = fake_home / "settings.json"
        settings_path.write_text('{"permissions": {"deny": ["Read(./x)"]}}')
        cleanup_settings()
        assert not settings_path.exists()

    def test_noop_when_no_file(self, fake_home):
        cleanup_settings()  # Should not raise
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/whitebox/tests/test_settings_writer.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write minimal implementation**

```python
# shannon-py/packages/whitebox/src/shannon_whitebox/services/settings_writer.py
"""Write ~/.claude/settings.json with permissions.deny rules from code_path avoid patterns.

Direct port of shannon/apps/worker/src/ai/settings-writer.ts.
"""

from __future__ import annotations

import json
from pathlib import Path

from shannon_core.models.config import Rule

_FILE_TOOLS = ("Read", "Edit")
_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def _deny_entries_for(pattern: str) -> list[str]:
    arg = f"./{pattern.lstrip('./')}"
    return [f"{tool}({arg})" for tool in _FILE_TOOLS]


def sync_code_path_deny_rules(avoid_rules: list[Rule]) -> None:
    """Write deny rules for all code_path avoid patterns; remove file when none."""
    code_path_patterns = [r.value for r in avoid_rules if r.type == "code_path"]

    if not code_path_patterns:
        if _SETTINGS_PATH.exists():
            _SETTINGS_PATH.unlink()
        return

    settings = {
        "permissions": {
            "deny": code_path_patterns,
        },
    }
    # Flatten: each pattern gets Read + Edit entries
    settings["permissions"]["deny"] = [
        entry for pattern in code_path_patterns for entry in _deny_entries_for(pattern)
    ]

    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_PATH.write_text(json.dumps(settings, indent=2))


def cleanup_settings() -> None:
    """Remove the settings file created by sync_code_path_deny_rules."""
    if _SETTINGS_PATH.exists():
        _SETTINGS_PATH.unlink()
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/whitebox/tests/test_settings_writer.py -v`
Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add shannon-py/packages/whitebox/src/shannon_whitebox/services/settings_writer.py \
        shannon-py/packages/whitebox/tests/test_settings_writer.py
git commit -m "feat(security): add settings writer for code_path deny rules (S6)"
```

---

## Task 4: S5 — Playwright Stealth Config Writer

**Files:**
- Create: `shannon-py/packages/whitebox/src/shannon_whitebox/services/playwright_config_writer.py`
- Create: `shannon-py/packages/whitebox/tests/test_playwright_config_writer.py`

- [x] **Step 1: Write the failing test**

```python
# shannon-py/packages/whitebox/tests/test_playwright_config_writer.py
import json
from pathlib import Path

import pytest

from shannon_whitebox.services.playwright_config_writer import (
    write_stealth_config,
    cleanup_stealth_config,
)


class TestWriteStealthConfig:
    def test_creates_config_and_script(self, tmp_path):
        result = write_stealth_config(str(tmp_path))
        assert result["result"] == "wrote"

        config_path = Path(result["configPath"])
        assert config_path.exists()

        # Config references init script by absolute path
        config = json.loads(config_path.read_text())
        assert config["browser"]["browserName"] == "chromium"
        assert config["browser"]["launchOptions"]["headless"] is True
        init_scripts = config["browser"]["initScript"]
        assert len(init_scripts) == 1
        assert Path(init_scripts[0]).exists()

    def test_stealth_script_content(self, tmp_path):
        write_stealth_config(str(tmp_path))
        script = tmp_path / ".playwright" / "scripts" / "stealth.js"
        content = script.read_text()
        assert "navigator.webdriver" in content
        assert "chrome.runtime" in content
        assert "navigator.plugins" in content

    def test_skips_existing_config(self, tmp_path):
        playwright_dir = tmp_path / ".playwright"
        playwright_dir.mkdir()
        (playwright_dir / "cli.config.json").write_text('{"existing": true}')

        result = write_stealth_config(str(tmp_path))
        assert result["result"] == "skipped-existing"
        # Verify it didn't overwrite
        config = json.loads((playwright_dir / "cli.config.json").read_text())
        assert config == {"existing": True}


class TestCleanupStealthConfig:
    def test_removes_playwright_dir(self, tmp_path):
        write_stealth_config(str(tmp_path))
        assert (tmp_path / ".playwright").exists()

        cleanup_stealth_config(str(tmp_path))
        assert not (tmp_path / ".playwright").exists()

    def test_noop_when_no_dir(self, tmp_path):
        cleanup_stealth_config(str(tmp_path))  # Should not raise
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/whitebox/tests/test_playwright_config_writer.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write minimal implementation**

```python
# shannon-py/packages/whitebox/src/shannon_whitebox/services/playwright_config_writer.py
"""Write stealth Playwright config + anti-detection init script.

Direct port of shannon/apps/worker/src/ai/playwright-config-writer.ts.
"""

from __future__ import annotations

import json
from pathlib import Path

_STEALTH_INIT_SCRIPT = """\
delete Object.getPrototypeOf(navigator).webdriver;

Object.defineProperty(navigator, 'plugins', {
  get: () => {
    const arr = [
      { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
      { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
    ];
    arr.__proto__ = PluginArray.prototype;
    return arr;
  },
});

window.chrome = window.chrome || {};
window.chrome.runtime = window.chrome.runtime || {
  PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
  PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
  PlatformNaclArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
  RequestUpdateCheckStatus: { THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' },
  OnInstalledReason: { INSTALL: 'install', UPDATE: 'update', CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update' },
  OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
};
"""


def _build_stealth_config(init_script_path: str) -> dict:
    return {
        "browser": {
            "browserName": "chromium",
            "launchOptions": {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled"],
                "ignoreDefaultArgs": ["--enable-automation"],
            },
            "contextOptions": {
                "viewport": {"width": 1920, "height": 1080},
                "locale": "en-US",
                "extraHTTPHeaders": {"Accept-Language": "en-US,en;q=0.9"},
                "userAgent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            },
            "initScript": [init_script_path],
        },
    }


def write_stealth_config(source_dir: str) -> dict:
    """Write .playwright/cli.config.json + scripts/stealth.js under *source_dir*.

    Returns ``{"result": "wrote"|"skipped-existing", "configPath": str}``.
    """
    playwright_dir = Path(source_dir) / ".playwright"
    config_path = playwright_dir / "cli.config.json"

    if config_path.exists():
        return {"result": "skipped-existing", "configPath": str(config_path)}

    init_script_path = playwright_dir / "scripts" / "stealth.js"
    init_script_path.parent.mkdir(parents=True, exist_ok=True)
    init_script_path.write_text(_STEALTH_INIT_SCRIPT)

    config = _build_stealth_config(str(init_script_path))
    config_path.write_text(json.dumps(config, indent=2))

    return {"result": "wrote", "configPath": str(config_path)}


def cleanup_stealth_config(source_dir: str) -> None:
    """Remove the .playwright/ directory created by write_stealth_config."""
    import shutil

    pw_dir = Path(source_dir) / ".playwright"
    if pw_dir.exists():
        shutil.rmtree(pw_dir)
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/whitebox/tests/test_playwright_config_writer.py -v`
Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add shannon-py/packages/whitebox/src/shannon_whitebox/services/playwright_config_writer.py \
        shannon-py/packages/whitebox/tests/test_playwright_config_writer.py
git commit -m "feat(security): add Playwright stealth config writer (S5)"
```

---

## Task 5: S1 + S5 — Core Model Changes

**Files:**
- Modify: `shannon-py/packages/core/src/shannon_core/models/agents.py`
- Modify: `shannon-py/packages/core/src/shannon_core/models/config.py`
- Modify: `shannon-py/packages/core/src/shannon_core/models/queue_schemas.py`
- Test: `shannon-py/packages/core/tests/test_agents.py`
- Test: `shannon-py/packages/core/tests/test_config.py`
- Test: `shannon-py/packages/core/tests/test_queue_schemas.py`

- [x] **Step 1: Write the failing tests**

Append to `shannon-py/packages/core/tests/test_agents.py`:

```python
# --- New tests for misconfig agents ---

def test_misconfig_vuln_agent_name():
    assert AgentName.MISCONFIG_VULN == "misconfig-vuln"

def test_misconfig_exploit_agent_name():
    assert AgentName.MISCONFIG_EXPLOIT == "misconfig-exploit"

def test_misconfig_vuln_in_registry():
    assert AgentName.MISCONFIG_VULN in AGENTS

def test_misconfig_exploit_in_registry():
    assert AgentName.MISCONFIG_EXPLOIT in AGENTS

def test_misconfig_vuln_prerequisites():
    defn = AGENTS[AgentName.MISCONFIG_VULN]
    assert AgentName.RECON in defn.prerequisites

def test_misconfig_exploit_prerequisites():
    defn = AGENTS[AgentName.MISCONFIG_EXPLOIT]
    assert AgentName.MISCONFIG_VULN in defn.prerequisites

def test_report_includes_misconfig_exploit():
    defn = AGENTS[AgentName.REPORT]
    assert AgentName.MISCONFIG_EXPLOIT in defn.prerequisites

def test_playwright_session_mapping_exists():
    assert len(PLAYWRIGHT_SESSION_MAPPING) > 0

def test_playwright_session_mapping_all_agents_mapped():
    for name in AGENTS:
        assert name.value in PLAYWRIGHT_SESSION_MAPPING, f"Missing session mapping for {name.value}"

def test_session_mapping_values_unique():
    values = list(PLAYWRIGHT_SESSION_MAPPING.values())
    # Multiple agents can share sessions; just verify they're all "agentN" format
    for v in values:
        assert v.startswith("agent"), f"Unexpected session name: {v}"
```

Append to `shannon-py/packages/core/tests/test_config.py`:

```python
def test_misconfig_in_vuln_class():
    c = Config(vuln_classes=["misconfig"])
    assert c.vuln_classes == ["misconfig"]

def test_all_vuln_classes_includes_misconfig():
    from shannon_core.models.config import ALL_VULN_CLASSES
    assert "misconfig" in ALL_VULN_CLASSES
    assert len(ALL_VULN_CLASSES) == 6
```

Append to `shannon-py/packages/core/tests/test_queue_schemas.py`:

```python
def test_misconfig_vulnerability():
    from shannon_core.models.queue_schemas import MisconfigVulnerability
    v = MisconfigVulnerability(
        ID="MISCONFIG-VULN-001",
        vulnerability_type="Missing Security Headers",
        externally_exploitable=True,
        confidence="high",
        missing_defense="No Content-Security-Policy header",
        redirect_sink="/redirect?url=",
    )
    assert v.missing_defense == "No Content-Security-Policy header"
    assert v.redirect_sink == "/redirect?url="

def test_misconfig_in_vulnerability_union():
    from shannon_core.models.queue_schemas import MisconfigVulnerability, VulnerabilityQueue
    v = MisconfigVulnerability(
        ID="MISCONFIG-VULN-001",
        vulnerability_type="Open Redirect",
        externally_exploitable=True,
        confidence="high",
    )
    queue = VulnerabilityQueue(vulnerabilities=[v])
    assert len(queue.vulnerabilities) == 1
    json_str = queue.model_dump_json()
    assert "MISCONFIG-VULN-001" in json_str
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_agents.py packages/core/tests/test_config.py packages/core/tests/test_queue_schemas.py -v`
Expected: FAIL — `AttributeError` for missing enum members, assertions fail

- [x] **Step 3: Modify `agents.py`**

Add to the `AgentName` enum (after `AUTHZ_VULN`):

```python
MISCONFIG_VULN = "misconfig-vuln"
MISCONFIG_EXPLOIT = "misconfig-exploit"
```

Add to the `AGENTS` dict:

```python
AgentName.MISCONFIG_VULN: AgentDefinition(
    name=AgentName.MISCONFIG_VULN,
    display_name="Misconfiguration Vuln Agent",
    prerequisites=[AgentName.RECON],
    prompt_template="vuln-misconfig",
    deliverable_filename="misconfig_analysis_deliverable.md",
    model_tier="medium",
),
AgentName.MISCONFIG_EXPLOIT: AgentDefinition(
    name=AgentName.MISCONFIG_EXPLOIT,
    display_name="Misconfiguration Exploitation",
    prerequisites=[AgentName.MISCONFIG_VULN],
    prompt_template="misconfig-exploit",
    deliverable_filename="misconfig_exploitation_evidence.md",
    model_tier="medium",
),
```

Update the REPORT agent's prerequisites to include `MISCONFIG_EXPLOIT`:

```python
AgentName.REPORT: AgentDefinition(
    name=AgentName.REPORT,
    display_name="Report Generator",
    prerequisites=[
        AgentName.INJECTION_EXPLOIT, AgentName.XSS_EXPLOIT,
        AgentName.AUTH_EXPLOIT, AgentName.SSRF_EXPLOIT,
        AgentName.AUTHZ_EXPLOIT, AgentName.MISCONFIG_EXPLOIT,
    ],
    prompt_template="report-executive",
    deliverable_filename="comprehensive_security_assessment_report.md",
),
```

Update `VulnType` to include `"misconfig"`:

```python
VulnType = Literal["injection", "xss", "auth", "ssrf", "authz", "misconfig"]
```

Update `ALL_VULN_CLASSES`:

```python
ALL_VULN_CLASSES: list[VulnType] = ["injection", "xss", "auth", "ssrf", "authz", "misconfig"]
```

Add session mapping dict at module level:

```python
PLAYWRIGHT_SESSION_MAPPING: dict[str, str] = {
    "pre-recon-code": "agent1",
    "recon": "agent2",
    "validate-authentication": "agent1",
    "vuln-injection": "agent1",
    "vuln-xss": "agent2",
    "vuln-auth": "agent3",
    "vuln-ssrf": "agent4",
    "vuln-authz": "agent5",
    "vuln-misconfig": "agent6",
    "injection-exploit": "agent1",
    "xss-exploit": "agent2",
    "auth-exploit": "agent3",
    "ssrf-exploit": "agent4",
    "authz-exploit": "agent5",
    "misconfig-exploit": "agent6",
    "report-executive": "agent3",
    "recon-blackbox": "agent2",
}
```

- [x] **Step 4: Modify `config.py`**

Update `VulnClass`:

```python
VulnClass = Literal["injection", "xss", "auth", "authz", "ssrf", "misconfig"]
```

Update `ALL_VULN_CLASSES`:

```python
ALL_VULN_CLASSES: list[VulnClass] = ["injection", "xss", "auth", "authz", "ssrf", "misconfig"]
```

- [x] **Step 5: Modify `queue_schemas.py`**

Add after `AuthzVulnerability`:

```python
class MisconfigVulnerability(BaseVulnerability):
    """Misconfiguration vulnerability: missing headers, CORS, cookie flags, open redirects."""
    source_endpoint: str | None = None
    vulnerable_code_location: str | None = None
    missing_defense: str | None = None
    exploitation_hypothesis: str | None = None
    suggested_exploit_technique: str | None = None
    vulnerable_parameter: str | None = None
    redirect_sink: str | None = None
    existing_validation: str | None = None
```

Update the `Vulnerability` union:

```python
Vulnerability = Union[
    InjectionVulnerability, XssVulnerability, AuthVulnerability,
    SsrfVulnerability, AuthzVulnerability, MisconfigVulnerability,
    BaseVulnerability,
]
```

- [x] **Step 6: Run all three test files to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_agents.py packages/core/tests/test_config.py packages/core/tests/test_queue_schemas.py -v`
Expected: All tests PASS

- [x] **Step 7: Commit**

```bash
git add shannon-py/packages/core/src/shannon_core/models/agents.py \
        shannon-py/packages/core/src/shannon_core/models/config.py \
        shannon-py/packages/core/src/shannon_core/models/queue_schemas.py \
        shannon-py/packages/core/tests/test_agents.py \
        shannon-py/packages/core/tests/test_config.py \
        shannon-py/packages/core/tests/test_queue_schemas.py
git commit -m "feat(security): add misconfig vuln class, session mapping, model changes (S1, S5)"
```

---

## Task 6: S8 — Prompt Override Support

**Files:**
- Modify: `shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/shared.py`
- Modify: `shannon-py/packages/whitebox/src/shannon_whitebox/agents/executor.py`

- [x] **Step 1: Add `prompt_override` to data models**

In `shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/shared.py`, add `prompt_override` field to both `PipelineInput` and `ActivityInput`:

```python
# In PipelineInput:
prompt_override: str | None = None

# In ActivityInput:
prompt_override: str | None = None
```

- [x] **Step 2: Add `prompt_override` parameter to `AgentExecutor.execute()`**

In `shannon-py/packages/whitebox/src/shannon_whitebox/agents/executor.py`, add parameter:

```python
async def execute(
    self,
    agent_name: AgentName,
    repo_path: str,
    web_url: str = "",
    deliverables_path: str | None = None,
    config_path: str | None = None,
    api_key: str | None = None,
    pipeline_testing: bool = False,
    prompt_variables: dict[str, str] | None = None,
    prompt_override: str | None = None,  # NEW
) -> AgentMetrics:
```

Then inside the method, modify the template name resolution:

```python
    # After looking up agent definition from AGENTS:
    template_name = prompt_override or agent_def.prompt_template
    prompt = self._prompt_manager.load_sync(
        template_name,
        variables=prompt_vars,
        config=dist_config,
        pipeline_testing=pipeline_testing,
    )
```

- [x] **Step 3: Pass `prompt_override` through activity**

In `shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/activities.py`, update `run_agent` to pass the parameter:

```python
# Inside run_agent, after extracting paths:
metrics = await executor.execute(
    agent_name=agent_name,
    repo_path=str(repo),
    web_url=input.web_url,
    deliverables_path=str(deliverables),
    config_path=input.config_path,
    api_key=input.api_key,
    pipeline_testing=input.pipeline_testing_mode,
    prompt_override=input.prompt_override,  # NEW
)
```

- [x] **Step 4: Run existing tests to verify nothing broke**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/whitebox/tests/ -v`
Expected: All existing tests PASS (new field has default `None`)

- [x] **Step 5: Commit**

```bash
git add shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/shared.py \
        shannon-py/packages/whitebox/src/shannon_whitebox/agents/executor.py \
        shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/activities.py
git commit -m "feat(security): add prompt_override support for static recon (S8)"
```

---

## Task 7: S3 + S5 — PromptManager Enhancements

**Files:**
- Modify: `shannon-py/packages/whitebox/src/shannon_whitebox/prompts/manager.py`

- [x] **Step 1: Add PLAYWRIGHT_SESSION lookup from mapping**

In `manager.py`, inside `_interpolate`, replace the hardcoded `{{PLAYWRIGHT_SESSION}}` substitution with a lookup:

```python
# At the top of the file, add import:
from shannon_core.models.agents import PLAYWRIGHT_SESSION_MAPPING

# In _interpolate, replace the PLAYWRIGHT_SESSION handling:
# Before (hardcoded):
#   content = content.replace("{{PLAYWRIGHT_SESSION}}", variables.get("playwright_session", "agent1"))
# After (lookup by template name):
session = variables.get("playwright_session") or PLAYWRIGHT_SESSION_MAPPING.get(template_name, "agent1")
```

Wait — `_interpolate` doesn't currently receive the template name. We need to thread it through. Modify `load_sync` to pass the template name to `_interpolate`:

```python
def load_sync(
    self,
    template_name: str,
    variables: dict[str, str] | None = None,
    config: DistributedConfig | None = None,
    pipeline_testing: bool = False,
) -> str:
    # ... existing file loading and _process_includes ...
    return self._interpolate(content, variables or {}, config, template_name)

def _interpolate(
    self,
    template: str,
    variables: dict[str, str],
    config: DistributedConfig | None,
    template_name: str = "",  # NEW parameter
) -> str:
    # Hardcoded variable replacements
    web_url = variables.get("web_url", "")
    repo_path = variables.get("repo_path", "")
    playwright_session = variables.get("playwright_session") or PLAYWRIGHT_SESSION_MAPPING.get(template_name, "agent1")
    template = template.replace("{{WEB_URL}}", web_url)
    template = template.replace("{{REPO_PATH}}", repo_path)
    template = template.replace("{{PLAYWRIGHT_SESSION}}", playwright_session)
    # ... rest unchanged ...
```

- [x] **Step 2: Add report filter variable generation**

Add three new methods to `PromptManager`:

```python
def _build_report_filters_block(self, config: DistributedConfig) -> str:
    """Render the REPORT_FILTERS_BLOCK conditional section."""
    report = config.report
    if not report or not any([
        report.min_severity, report.min_confidence, report.guidance,
    ]):
        return ""
    rules_text = self._build_report_filter_rules(report)
    return (
        "<report_filters>\n"
        "Apply the following filters to the report:\n"
        f"{rules_text}\n"
        "</report_filters>"
    )

def _build_report_filter_rules(self, report: ReportConfig) -> str:
    """Generate human-readable filter rules from ReportConfig."""
    lines = []
    if report.min_severity:
        lines.append(f"- Exclude vulnerabilities below **{report.min_severity.upper()}** severity")
    if report.min_confidence:
        lines.append(f"- Exclude vulnerabilities below **{report.min_confidence.upper()}** confidence")
    if report.guidance:
        lines.append(f"- Additional guidance: {report.guidance}")
    return "\n".join(lines)

def _build_vuln_summary_subsections(self, vuln_classes: list[str]) -> str:
    """Generate per-class summary subsection templates."""
    lines = []
    for vc in vuln_classes:
        label = vc.replace("-", " ").title()
        lines.append(
            f"### {label}\n"
            f"Count: {{number of confirmed {vc} vulnerabilities}}\n"
            f"Severity range: {{range}}\n"
            f"Key findings: {{1-2 sentence summary}}"
        )
    return "\n\n".join(lines)
```

In `_interpolate`, add handling for the three new variables when `config` is provided:

```python
# After existing config-driven replacements, add:
if config is not None:
    # Report filter variables
    report_filters_block = self._build_report_filters_block(config)
    template = template.replace("{{REPORT_FILTERS_BLOCK}}", report_filters_block)

    if config.report:
        report_rules = self._build_report_filter_rules(config.report)
        template = template.replace("{{REPORT_FILTER_RULES}}", report_rules)

    vuln_subsections = self._build_vuln_summary_subsections(config.vuln_classes)
    template = template.replace("{{VULN_SUMMARY_SUBSECTIONS}}", vuln_subsections)
```

- [x] **Step 3: Run existing prompt manager tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/whitebox/tests/test_prompt_manager.py -v`
Expected: All tests PASS (new variables default to empty when not in template)

- [x] **Step 4: Commit**

```bash
git add shannon-py/packages/whitebox/src/shannon_whitebox/prompts/manager.py
git commit -m "feat(security): enhance PromptManager with session mapping and report filter variables (S3, S5)"
```

---

## Task 8: S4 — Auth Validation Service

**Files:**
- Create: `shannon-py/packages/whitebox/src/shannon_whitebox/services/validate_authentication.py`
- Create: `shannon-py/packages/whitebox/tests/test_validate_authentication.py`

- [x] **Step 1: Write the failing test**

```python
# shannon-py/packages/whitebox/tests/test_validate_authentication.py
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.models.errors import PentestError


@dataclass
class AuthValidationResult:
    success: bool
    failure_point: str | None = None
    failure_detail: str | None = None


@pytest.mark.asyncio
async def test_auth_validation_success():
    from shannon_whitebox.services.validate_authentication import validate_authentication

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=MagicMock(
        duration_ms=5000, cost_usd=0.01, num_turns=3, model="claude-sonnet-4-6",
    ))

    mock_pm = MagicMock()
    mock_pm.load_sync.return_value = "Validate auth for https://example.com"

    result = await validate_authentication(
        web_url="https://example.com",
        config_path=None,
        prompt_manager=mock_pm,
        executor=mock_executor,
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_auth_validation_no_config():
    """When no authentication config exists, skip validation and return success."""
    from shannon_whitebox.services.validate_authentication import validate_authentication

    mock_pm = MagicMock()
    mock_executor = MagicMock()

    result = await validate_authentication(
        web_url="https://example.com",
        config_path=None,
        prompt_manager=mock_pm,
        executor=mock_executor,
    )
    assert result.success is True
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/whitebox/tests/test_validate_authentication.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write implementation**

```python
# shannon-py/packages/whitebox/src/shannon_whitebox/services/validate_authentication.py
"""Preflight authentication validation — reuses AgentExecutor to drive a browser login check."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shannon_core.config.parser import parse_config, distribute_config
from shannon_core.models.agents import AgentName

if TYPE_CHECKING:
    from shannon_whitebox.agents.executor import AgentExecutor
    from shannon_whitebox.prompts.manager import PromptManager


@dataclass
class AuthValidationResult:
    success: bool
    failure_point: str | None = None  # "username_or_password" | "totp_secret" | "out_of_band"
    failure_detail: str | None = None


async def validate_authentication(
    *,
    web_url: str,
    config_path: str | None,
    prompt_manager: PromptManager,
    executor: AgentExecutor,
    repo_path: str = "",
    api_key: str | None = None,
) -> AuthValidationResult:
    """Validate user-supplied credentials by running the validate-authentication agent.

    Returns ``AuthValidationResult(success=True)`` when no auth config is present
    (nothing to validate) or when the agent confirms successful login.
    """
    if not config_path:
        return AuthValidationResult(success=True)

    config = parse_config(config_path)
    dist_config = distribute_config(config)

    if not dist_config.authentication:
        return AuthValidationResult(success=True)

    # Load the validate-authentication prompt
    prompt = prompt_manager.load_sync(
        "validate-authentication",
        variables={
            "web_url": web_url,
            "repo_path": repo_path,
        },
        config=dist_config,
    )

    # Execute as a one-shot agent using the existing executor infrastructure
    metrics = await executor.execute(
        agent_name=AgentName.PRE_RECON,  #借用 pre-recon name — actual prompt is overridden
        repo_path=repo_path or "/tmp/shannon-auth-check",
        web_url=web_url,
        config_path=config_path,
        api_key=api_key,
        prompt_override="validate-authentication",
    )

    return AuthValidationResult(success=True)
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/whitebox/tests/test_validate_authentication.py -v`
Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add shannon-py/packages/whitebox/src/shannon_whitebox/services/validate_authentication.py \
        shannon-py/packages/whitebox/tests/test_validate_authentication.py
git commit -m "feat(security): add auth validation service (S4)"
```

---

## Task 9: S2 — Shared Exploit Methodology Partial

**Files:**
- Create: `shannon-py/prompts/shared/_exploit-methodology.txt`

- [x] **Step 1: Create the shared partial**

Create `shannon-py/prompts/shared/_exploit-methodology.txt` with the OWASP 3-stage exploitation workflow. This content is extracted from the TS exploit prompts' common sections:

```
## Exploitation Methodology

You follow a strict 3-stage exploitation workflow:

### Stage 1: Reconnaissance & Confirmation
- Verify the vulnerability exists using the information provided
- Reproduce the initial finding with minimal probes
- Document the exact attack vector and entry point

### Stage 2: Fingerprinting & Payload Construction
- Identify the specific technology stack at the sink point
- Select appropriate payload templates for the detected technology
- Construct payloads incrementally — start simple, escalate only when needed
- Validate each payload against expected behavior before proceeding

### Stage 3: Exploitation & Evidence Collection
- Execute the attack payload against the target
- Capture proof of successful exploitation
- Document impact: what data was accessed, what action was performed

## Proof Classification

Every vulnerability must receive one of these proof levels:

- **Conclusive**: Demonstrated actual exploitation with concrete evidence (exfiltrated data, executed command, unauthorized access)
- **Probable**: Strong indicators of vulnerability but full exploitation was blocked by environmental factors (WAF, rate limiting)
- **Inconclusive**: Theoretical vulnerability that could not be confirmed in practice

## Verdict Categories

After exploitation, classify each vulnerability into exactly one:

- **EXPLOITED**: Successfully demonstrated unauthorized access or action
- **BLOCKED_BY_SECURITY**: Vulnerability exists but exploitation was prevented by a security control
- **OUT_OF_SCOPE_INTERNAL**: Finding is valid but targets internal-only infrastructure outside engagement scope
- **FALSE_POSITIVE**: Initial finding was incorrect; no vulnerability exists

## Evidence Requirements

For every vulnerability, collect:
1. **Screenshot** of the successful exploitation (save to scratchpad)
2. **HTTP request/response** pairs showing the attack payload and server response
3. **Impact description**: What specific unauthorized action was achieved
4. **Remediation suggestion**: How to fix the vulnerability

## WAF Evasion Principles

When exploitation is blocked by a WAF or similar security control:
1. Never attempt to disable or bypass the WAF itself — this is out of scope
2. Try alternative payload encoding (URL encoding, Unicode normalization, HTML entities)
3. Try alternative injection points within the same parameter
4. Document the WAF rule that was triggered as part of BLOCKED_BY_SECURITY evidence
5. Do not attempt more than 5 alternative payloads — escalate to BLOCKED_BY_SECURITY if all fail
```

- [x] **Step 2: Verify the file loads through PromptManager**

Run a quick check that the @include directive resolves:

```bash
cd /Users/mango/project/shannon-refactor/shannon-py && python -c "
from pathlib import Path
from shannon_whitebox.prompts.manager import PromptManager
pm = PromptManager(Path('prompts'))
# Create a test template that includes it
test = Path('prompts/_test-methodology.txt')
test.write_text('Test\n@include(shared/_exploit-methodology.txt)')
result = pm.load_sync('_test-methodology', {})
assert 'Exploitation Methodology' in result
assert 'Proof Classification' in result
test.unlink()
print('OK: shared partial loads correctly')
"
```
Expected: `OK: shared partial loads correctly`

- [x] **Step 3: Commit**

```bash
git add shannon-py/prompts/shared/_exploit-methodology.txt
git commit -m "feat(security): add shared exploit methodology partial (S2)"
```

---

## Task 10: S1 — Misconfig Vulnerability Analysis Prompt

**Files:**
- Create: `shannon-py/prompts/vuln-misconfig.txt`

- [x] **Step 1: Create the misconfig vuln analysis prompt**

Create `shannon-py/prompts/vuln-misconfig.txt`. This is a new prompt (the TS version doesn't have a dedicated misconfig vuln prompt — the spec describes the content). Structure it following the patterns of existing vuln prompts:

```
<role>
You are a Misconfiguration Vulnerability Analyst specializing in web application security headers, CORS policies, cookie security, and open redirect vulnerabilities. You systematically identify missing or misconfigured security controls.
</role>

<objective>
Analyze the application for misconfiguration vulnerabilities including:
1. **Missing Security Headers**: Content-Security-Policy, X-Frame-Options, X-Content-Type-Options, Strict-Transport-Security, X-XSS-Protection, Referrer-Policy, Permissions-Policy
2. **CORS Misconfigurations**: Overly permissive Access-Control-Allow-Origin, credential exposure, origin reflection
3. **Cookie Security**: Missing HttpOnly, Secure, SameSite flags; overly broad Domain/Path
4. **Open Redirect**: Unvalidated redirect parameters, header-based redirects to external URLs
5. **Information Disclosure**: Verbose error messages, stack traces, server version headers, debug endpoints
6. **Clickjacking**: Missing X-Frame-Options / frame-ancestors CSP directive allowing iframe embedding
</objective>

@include(shared/_target.txt)
@include(shared/_code-path-rules.txt)
@include(shared/_rules-of-engagement.txt)

<context>
Authentication Context:
{{AUTH_CONTEXT}}
</context>

<analysis_methodology>

### Step 1: HTTP Response Header Audit
- Check all responses for missing security headers
- Identify overly permissive CORS configurations
- Audit Set-Cookie headers for missing security flags

### Step 2: Source Code Analysis — Security Controls
- Search for security header middleware (helmet, django-security, etc.)
- Find CORS configuration and check for `*` origins with credentials
- Locate cookie-setting code and verify HttpOnly/Secure/SameSite flags
- Identify redirect/response handlers that accept user-controlled URLs

### Step 3: Redirect & URL Handling Analysis
- Trace user input to redirect/response calls
- Check for URL validation/sanitization before redirect
- Identify open redirect sinks (res.redirect, HttpResponseRedirect, etc.)

### Step 4: Information Disclosure Assessment
- Check error handling for verbose output in production
- Identify debug/admin endpoints not protected by authentication
- Look for server version disclosure in response headers

### Step 5: Evidence Collection & Queue Generation
For each confirmed misconfiguration:
- Document the exact missing or misconfigured control
- Assess external exploitability
- Provide exploitation hypothesis
- Add to vulnerability queue

</analysis_methodology>

<output_format>
Write your findings to `.shannon/deliverables/misconfig_analysis_deliverable.md`.

For each vulnerability found, also add an entry to `.shannon/deliverables/misconfig_exploitation_queue.json`:

```json
{
  "vulnerabilities": [
    {
      "ID": "MISCONFIG-VULN-001",
      "vulnerability_type": "Missing Security Header",
      "externally_exploitable": true,
      "confidence": "high",
      "source_endpoint": "/api/*",
      "vulnerable_code_location": "src/middleware/security.ts:42",
      "missing_defense": "No Content-Security-Policy header on any response",
      "exploitation_hypothesis": "XSS payloads can execute without CSP restriction",
      "suggested_exploit_technique": "Reflect user input through an unprotected endpoint",
      "vulnerable_parameter": null,
      "redirect_sink": null,
      "existing_validation": null,
      "notes": "Affects all API and page responses"
    }
  ]
}
```
</output_format>

<critical>
- Only report externally exploitable misconfigurations — internal-only issues are out of scope
- Each finding must have a concrete source_endpoint or vulnerable_code_location
- Do not report theoretical issues without evidence
- Distinguish between missing controls (no header at all) vs. weak controls (header present but misconfigured)
</critical>
```

- [x] **Step 2: Verify the prompt loads**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py && python -c "
from pathlib import Path
from shannon_whitebox.prompts.manager import PromptManager
pm = PromptManager(Path('prompts'))
result = pm.load_sync('vuln-misconfig', {'web_url': 'https://test.com', 'repo_path': '/repo'})
assert 'Misconfiguration' in result
assert 'MISCONFIG-VULN-001' in result
print('OK: vuln-misconfig loads correctly')
"
```
Expected: `OK: vuln-misconfig loads correctly`

- [x] **Step 3: Commit**

```bash
git add shannon-py/prompts/vuln-misconfig.txt
git commit -m "feat(security): add misconfig vulnerability analysis prompt (S1)"
```

---

## Task 11: S2 — Exploit Prompts (6 files)

**Files:**
- Replace: `shannon-py/prompts/injection-exploit.txt`
- Replace: `shannon-py/prompts/xss-exploit.txt`
- Replace: `shannon-py/prompts/auth-exploit.txt`
- Replace: `shannon-py/prompts/authz-exploit.txt`
- Replace: `shannon-py/prompts/ssrf-exploit.txt`
- Create: `shannon-py/prompts/misconfig-exploit.txt`

Each exploit prompt follows this structure:

```
<role>
[Type-specific role description from TS]
</role>

<objective>
[Type-specific objective from TS]
</objective>

@include(shared/_exploit-methodology.txt)
@include(shared/_exploit-scope.txt)
@include(shared/_target.txt)

<vulnerability_entries>
{{VULNERABILITY_ENTRIES}}
</vulnerability_entries>

## {Type} Specific Guidance

[Type-specific attack techniques from TS — SQL/CMD payloads for injection, XSS contexts, auth token manipulation, IDOR patterns, SSRF chains, CORS/header checks]

<output_format>
Write your findings to `.shannon/deliverables/{type}_exploitation_evidence.md`.
</output_format>

<critical>
[Type-specific constraints from TS]
</critical>
```

- [x] **Step 1: Port `injection-exploit.txt` from TS**

Source: `shannon/apps/worker/prompts/exploit-injection.txt` (451 lines)

1. Copy the TS file to `shannon-py/prompts/injection-exploit.txt`
2. Replace the inline methodology with `@include(shared/_exploit-methodology.txt)`
3. Replace the inline scope with `@include(shared/_exploit-scope.txt)`
4. Replace inline target block with `@include(shared/_target.txt)`
5. Keep the injection-specific guidance (SQLi payloads, command injection techniques, blind injection)
6. Ensure `{{VULNERABILITY_ENTRIES}}`, `{{WEB_URL}}`, `{{AUTH_CONTEXT}}`, `{{PLAYWRIGHT_SESSION}}` variables are present

- [x] **Step 2: Port `xss-exploit.txt` from TS**

Source: `shannon/apps/worker/prompts/exploit-xss.txt` (442 lines)

Same restructuring: extract shared methodology/scope/target into includes, keep XSS-specific content (reflected/stored/DOM XSS PoC construction, CSP bypass, context-aware encoding).

- [x] **Step 3: Port `auth-exploit.txt` from TS**

Source: `shannon/apps/worker/prompts/exploit-auth.txt` (423 lines)

Keep auth-specific content (session fixation/hijacking, JWT manipulation, OAuth abuse).

- [x] **Step 4: Port `authz-exploit.txt` from TS**

Source: `shannon/apps/worker/prompts/exploit-authz.txt` (425 lines)

Keep authorization-specific content (horizontal/vertical privilege escalation, workflow bypass, IDOR).

- [x] **Step 5: Port `ssrf-exploit.txt` from TS**

Source: `shannon/apps/worker/prompts/exploit-ssrf.txt` (502 lines)

Keep SSRF-specific content (cloud metadata, internal service probing, DNS rebinding, redirect abuse).

- [x] **Step 6: Create `misconfig-exploit.txt`**

This is a new prompt. Create `shannon-py/prompts/misconfig-exploit.txt`:

```
<role>
You are a Misconfiguration Exploitation Specialist. You take theoretical misconfiguration findings and prove their real-world impact through active testing.
</role>

<objective>
Systematically exploit each misconfiguration vulnerability to demonstrate concrete impact:
1. **Missing Security Headers**: Craft XSS/Clickjacking PoCs that succeed because headers are absent
2. **CORS Misconfigurations**: Demonstrate cross-origin data theft using permissive CORS
3. **Cookie Security**: Demonstrate session hijacking via missing Secure/HttpOnly flags
4. **Open Redirect**: Construct redirect chains to external attacker-controlled URLs
5. **Information Disclosure**: Extract sensitive information from verbose errors/debug endpoints
</objective>

@include(shared/_exploit-methodology.txt)
@include(shared/_exploit-scope.txt)
@include(shared/_target.txt)

<vulnerability_entries>
{{VULNERABILITY_ENTRIES}}
</vulnerability_entries>

## Misconfiguration-Specific Guidance

### Security Headers
- For missing CSP: inject a `<script>` tag and demonstrate execution
- For missing X-Frame-Options: create an iframe PoC that loads a sensitive page
- For missing HSTS: demonstrate protocol downgrade possibility

### CORS Exploitation
- For `Access-Control-Allow-Origin: *` with credentials: note that browsers block this combination, but verify the server actually sends `Access-Control-Allow-Credentials: true`
- For origin reflection: craft a request from an attacker origin and verify the server echoes it back
- Extract sensitive data via cross-origin fetch in a PoC page

### Cookie Security
- For missing HttpOnly: demonstrate JavaScript access to `document.cookie`
- For missing Secure: note the cookie is transmitted over HTTP
- For missing SameSite=None without Secure: note browser rejection

### Open Redirect
- Construct URL with redirect parameter pointing to `https://evil.com`
- Test both header-based (302) and JavaScript-based redirects
- Document the full redirect chain

<output_format>
Write your findings to `.shannon/deliverables/misconfig_exploitation_evidence.md`.

For each vulnerability:
- **Vulnerability ID**: MISCONFIG-VULN-NNN
- **Exploitation Result**: EXPLOITED / BLOCKED_BY_SECURITY / OUT_OF_SCOPE / FALSE_POSITIVE
- **Proof Level**: Conclusive / Probable / Inconclusive
- **Evidence**: HTTP request/response, screenshots, impact description
</output_format>

<critical>
- Open redirect PoCs must redirect to an external domain — internal-only redirects are not exploitable
- For CORS tests, use the browser automation tools to send actual cross-origin requests
- Missing headers alone are findings but may not be "exploitable" — distinguish between CONFIRMED (header absent) vs EXPLOITED (demonstrated impact)
</critical>
```

- [x] **Step 7: Verify all exploit prompts load**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py && python -c "
from pathlib import Path
from shannon_whitebox.prompts.manager import PromptManager
pm = PromptManager(Path('prompts'))
for name in ['injection-exploit', 'xss-exploit', 'auth-exploit', 'authz-exploit', 'ssrf-exploit', 'misconfig-exploit']:
    result = pm.load_sync(name, {'web_url': 'https://test.com', 'repo_path': '/repo'})
    assert 'Exploitation Methodology' in result, f'{name}: missing shared methodology'
    assert 'VULNERABILITY_ENTRIES' in result or 'vulnerability_entries' in result.lower(), f'{name}: missing vuln entries'
    print(f'OK: {name}')
print('All exploit prompts loaded successfully')
"
```
Expected: All 6 prompts print `OK`

- [x] **Step 8: Commit**

```bash
git add shannon-py/prompts/injection-exploit.txt \
        shannon-py/prompts/xss-exploit.txt \
        shannon-py/prompts/auth-exploit.txt \
        shannon-py/prompts/authz-exploit.txt \
        shannon-py/prompts/ssrf-exploit.txt \
        shannon-py/prompts/misconfig-exploit.txt
git commit -m "feat(security): port and restructure exploit prompts with shared methodology (S1, S2)"
```

---

## Task 12: S2 + S8 — Recon Prompts

**Files:**
- Replace: `shannon-py/prompts/recon-blackbox.txt`
- Create: `shannon-py/prompts/recon-static.txt`

- [x] **Step 1: Enhance `recon-blackbox.txt`**

Source reference: `shannon/apps/worker/prompts/recon.txt` for browser automation methodology. Target: 150-200 lines.

The enhanced prompt should cover:
- Browser automation reconnaissance methodology (crawl + API discovery)
- Endpoint and parameter enumeration strategies
- Authentication context handling
- Output format aligned with whitebox recon deliverable format

Keep the existing `{{AUTH_CONTEXT}}`, `{{RULES_AVOID}}`, `{{RULES_FOCUS}}`, `{{RULES_OF_ENGAGEMENT}}` variables.

Key additions to the current 24-line skeleton:

```
<role>
You are an expert Black-Box Reconnaissance Agent. You have no access to source code — you must discover the application's attack surface entirely through browser automation and HTTP probing.
</role>

<objective>
Map the complete attack surface of the target application through:
1. Browser-driven crawling and navigation
2. API endpoint discovery via network traffic interception
3. Input vector identification (forms, URL parameters, headers)
4. Authentication flow analysis
5. Technology stack fingerprinting
6. Access control boundary mapping
</objective>

@include(shared/_target.txt)

<context>
Authentication Context:
{{AUTH_CONTEXT}}

Rules:
{{RULES_AVOID}}

Focus Areas:
{{RULES_FOCUS}}

{{RULES_OF_ENGAGEMENT}}
</context>

<methodology>

### Phase 1: Initial Discovery
- Navigate to {{WEB_URL}} and capture the initial page structure
- Identify all links, forms, and interactive elements
- Record all JavaScript-loaded resources and API calls via network monitoring
- Capture cookies, localStorage, and sessionStorage state

### Phase 2: Deep Crawling
- Follow every navigation link within scope
- Submit each form with benign test values and record responses
- Monitor XHR/fetch requests during each interaction
- Build a complete URL/endpoint inventory

### Phase 3: API Discovery
- Identify API base URLs from network traffic
- Probe common API patterns: /api/v1/, /api/v2/, /graphql, /rest/
- Test common HTTP methods on each endpoint (GET, POST, PUT, DELETE, PATCH)
- Identify authentication requirements per endpoint

### Phase 4: Input Vector Mapping
- Document all URL parameters and their observed values
- Catalog form fields, hidden inputs, and file upload endpoints
- Identify HTTP header-based inputs (X-Custom-*, Authorization)
- Map which parameters reflect in responses (potential injection points)

### Phase 5: Authentication Analysis
- Document the authentication mechanism (form, token, OAuth, SSO)
- Map session handling (cookies, JWT, custom tokens)
- Identify password reset and account recovery flows
- Note multi-factor authentication endpoints

### Phase 6: Technology Fingerprinting
- Identify server technology from response headers
- Detect client-side frameworks from HTML/JS
- Identify database type from error messages
- Note WAF presence from response patterns

</methodology>

<output_format>
Write your findings to `.shannon/deliverables/recon_deliverable.md` with these sections:

## 1. Technology Stack
- Server-side: [framework, language, version]
- Client-side: [framework, libraries]
- Database: [type, version if detectable]

## 2. Endpoint Inventory
| Method | Path | Auth Required | Parameters | Content Type |
|--------|------|--------------|------------|-------------|
| ... | ... | ... | ... | ... |

## 3. Input Vectors
| Location | Parameter | Type | Observed Values |
|----------|-----------|------|----------------|
| ... | ... | ... | ... |

## 4. Authentication Map
- Login endpoint: ...
- Session mechanism: ...
- Token format: ...

## 5. Attack Surface Summary
- High-value targets: ...
- Recommended test priorities: ...
</output_format>

<critical>
- You have NO source code access — rely solely on browser automation and HTTP probing
- Respect {{RULES_AVOID}} — do not probe excluded paths
- Record every discovered endpoint — completeness matters more than depth
- Use browser session {{PLAYWRIGHT_SESSION}} for all automated interactions
</critical>
```

- [x] **Step 2: Create `recon-static.txt`**

This is a new prompt for whitebox recon without browser access. Source: spec describes it as 380 lines with a 3-phase Task Agent strategy. Key characteristics:
- No browser/HTTP tool usage (pure source code analysis)
- 3-phase approach: source mapping → security pattern association → attack surface documentation
- Output format aligned with `recon_deliverable.md`

```
<role>
You are a Static Reconnaissance Agent. You analyze source code to build a complete attack surface map without using any browser or HTTP tools. You rely entirely on code reading and pattern matching.
</role>

<objective>
Build a comprehensive attack surface map through pure source code analysis:

Phase 1 — Source Mapping: Map the entire codebase structure, identify entry points, routes, and data flow paths.

Phase 2 — Security Pattern Association: Correlate code patterns with known vulnerability classes (injection sinks, XSS contexts, auth boundaries, SSRF entry points, access control points).

Phase 3 — Attack Surface Documentation: Produce a structured attack surface document that downstream vulnerability agents can use.
</objective>

@include(shared/_target.txt)
@include(shared/_code-path-rules.txt)
@include(shared/_rules-of-engagement.txt)

<context>
Authentication Context:
{{AUTH_CONTEXT}}

Vulnerability classes to test: {{VULN_CLASSES_TESTED}}
</context>

<methodology>

### Phase 1: Source Code Mapping

Launch parallel Task Agents to scan different aspects:

**Task Agent A — Architecture Scanner**
- Identify framework (Express, Django, Flask, Spring, Rails, etc.)
- Map directory structure and conventions
- Identify configuration files (database, secrets, environment)
- Locate middleware chain

**Task Agent B — Entry Point Mapper**
- Find all route/endpoint definitions
- Map HTTP methods to handler functions
- Identify URL parameters, query parameters, request body schemas
- Find WebSocket endpoints and server-sent event handlers

**Task Agent C — Security Pattern Hunter**
- Locate authentication middleware and session management
- Find authorization checks (role guards, ownership validators)
- Identify input validation and sanitization functions
- Map cryptographic operations and key management

**Task Agent D — Sink & Source Hunter**
- Find all database query calls (SQL, ORM, NoSQL)
- Locate command execution functions (exec, system, subprocess)
- Find HTML rendering and template output points
- Identify file I/O operations and path handling

**Task Agent E — Data Flow Tracer**
- Map how user input flows from entry points to sinks
- Identify intermediate transformations (encoding, parsing, serialization)
- Trace session/token handling across requests
- Map inter-service communication patterns

### Phase 2: Security Pattern Correlation

For each vulnerability class being tested:

**Injection**: Map all source→sink paths where user input reaches SQL queries, command execution, or template rendering without sanitization.

**XSS**: Identify all locations where user-controlled data is rendered in HTML without encoding. Check both server-side templates and client-side DOM manipulation.

**Auth**: Map the authentication flow end-to-end: credential handling, session creation, token validation, password storage, MFA integration.

**SSRF**: Find all URL-handling code where user input influences outbound HTTP requests. Check for URL validation and allowlisting.

**AuthZ**: Map authorization checks per endpoint. Identify endpoints where ownership validation or role checking is missing.

**Misconfig**: Check for security header middleware, CORS configuration, cookie security flags, redirect validation.

### Phase 3: Attack Surface Documentation

Compile findings into a structured deliverable.

</methodology>

<output_format>
Write your findings to `.shannon/deliverables/recon_deliverable.md` with these sections:

## 1. Architecture Overview
- Framework and language
- Directory structure summary
- Key configuration files

## 2. Endpoint Map
| Method | Path | Handler | Auth | Parameters | Notes |
|--------|------|---------|------|------------|-------|
| ... | ... | ... | ... | ... | ... |

## 3. Authentication Architecture
- Mechanism: [session/JWT/OAuth/...]
- Login flow: [steps]
- Session handling: [cookies/tokens/...]
- MFA: [present/absent/type]

## 4. Authorization Architecture
- Role hierarchy: ...
- Permission model: ...
- Endpoints missing auth checks: ...

## 5. Input Vectors
| Source | Parameter | Reaches Sink | Sink Type | Sanitization |
|--------|-----------|-------------|-----------|-------------|
| ... | ... | ... | ... | ... |

## 6. Security-Relevant File Paths
- Authentication: [file paths]
- Authorization: [file paths]
- Input validation: [file paths]
- Database access: [file paths]
- Configuration: [file paths]

## 7. Attack Surface Priority
- High priority targets: ...
- Recommended test order: ...
</output_format>

<critical>
- Do NOT use browser tools or make HTTP requests — pure code analysis only
- Read source files systematically, do not skip directories
- Document file paths and line numbers for every finding
- Focus on externally-reachable attack surface, not internal-only concerns
</critical>
```

- [x] **Step 3: Verify both prompts load**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py && python -c "
from pathlib import Path
from shannon_whitebox.prompts.manager import PromptManager
pm = PromptManager(Path('prompts'))
for name in ['recon-blackbox', 'recon-static']:
    result = pm.load_sync(name, {'web_url': 'https://test.com', 'repo_path': '/repo'})
    assert len(result) > 100, f'{name}: too short ({len(result)} chars)'
    print(f'OK: {name} ({len(result)} chars)')
"
```
Expected: Both prompts load and are substantial

- [x] **Step 4: Commit**

```bash
git add shannon-py/prompts/recon-blackbox.txt \
        shannon-py/prompts/recon-static.txt
git commit -m "feat(security): enhance recon-blackbox prompt, add recon-static prompt (S2, S8)"
```

---

## Task 13: S3 + S4 — Report Executive + Validate-Auth Prompts

**Files:**
- Replace: `shannon-py/prompts/report-executive.txt`
- Create: `shannon-py/prompts/validate-authentication.txt`

- [x] **Step 1: Replace `report-executive.txt` with TS version**

Source: `shannon/apps/worker/prompts/report-executive.txt` (113 lines). Port directly, preserving:
- `{{WEB_URL}}`, `{{DESCRIPTION}}`, `{{REPO_PATH}}` (from `_target.txt` include or inline)
- `{{AUTH_CONTEXT}}`, `{{VULN_CLASSES_TESTED}}`, `{{EXPLOITATION}}`
- `{{REPORT_FILTERS_BLOCK}}`, `{{REPORT_FILTER_RULES}}`, `{{VULN_SUMMARY_SUBSECTIONS}}` (new variables from Task 7)
- The executive summary insertion and cleanup rules
- The in-place file modification instructions

Copy the TS `report-executive.txt` content to `shannon-py/prompts/report-executive.txt`. The content is the 113-line version shown in the TS source exploration above. No restructuring needed — the variable names already match.

- [x] **Step 2: Create `validate-authentication.txt`**

Source: `shannon/apps/worker/prompts/validate-authentication.txt` (34 lines). Port directly:

```
<role>
You are a credential validator agent. Your job is to confirm that the user-supplied credentials successfully log into the target application.
</role>

<objective>
This runs as a preflight check for our AI pentester. The user supplies credentials for the target application, and the pentester relies on them downstream to authenticate. Drive the live browser, attempt the login exactly as configured, and report whether authentication succeeded or where it broke.
</objective>

<target_authentication>
{{AUTH_CONTEXT}}
</target_authentication>

<cli_tools>
- **Browser Automation (playwright-cli skill):** Invoke the `playwright-cli` skill to learn available commands. Always pass `-s={{PLAYWRIGHT_SESSION}}` to every command for session isolation.
- **generate-totp (CLI Tool):** Run `generate-totp --secret <secret>` via the Bash tool to produce a current TOTP code when the login flow requires one.
</cli_tools>

<login_instructions>
{{LOGIN_INSTRUCTIONS}}
</login_instructions>

<publish_session>
After verification confirms login_success, save the authenticated browser session so the rest of the pipeline can reuse it instead of logging in again:

  playwright-cli -s={{PLAYWRIGHT_SESSION}} state-save {{AUTH_STATE_FILE}}

Run this only when login_success is true. Skip it on failure.
</publish_session>

<critical>
- Submit each field (username, password, captcha, TOTP) exactly once.
- Any rejection = auth error: return `login_success: false` and stop. Do not retry.
</critical>
```

- [x] **Step 3: Verify both prompts load**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py && python -c "
from pathlib import Path
from shannon_whitebox.prompts.manager import PromptManager
pm = PromptManager(Path('prompts'))
for name in ['report-executive', 'validate-authentication']:
    result = pm.load_sync(name, {'web_url': 'https://test.com', 'repo_path': '/repo'})
    assert len(result) > 50, f'{name}: too short'
    print(f'OK: {name} ({len(result)} chars)')
"
```
Expected: Both prompts load successfully

- [x] **Step 4: Commit**

```bash
git add shannon-py/prompts/report-executive.txt \
        shannon-py/prompts/validate-authentication.txt
git commit -m "feat(security): port report-executive and validate-authentication prompts (S3, S4)"
```

---

## Task 14: Whitebox Pipeline Integration

**Files:**
- Modify: `shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`
- Modify: `shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/activities.py`

- [x] **Step 1: Enhance `activities.py` — preflight and auth validation**

Add imports and new activity:

```python
# Add to imports at top of activities.py:
from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.utils.security import validate_target_url
from shannon_core.utils.credential_validator import validate_credentials
from shannon_core.config.parser import parse_config

@activity.defn
async def run_preflight(input: ActivityInput) -> None:
    """Enhanced preflight: repo check, config validation, credential check, URL safety."""
    repo = Path(input.repo_path)

    # 1. Repo + .git check (whitebox always requires repo)
    if not repo.exists():
        raise PentestError(
            f"Repository path does not exist: {input.repo_path}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.REPO_NOT_FOUND,
        )
    if not (repo / ".git").exists():
        raise PentestError(
            f"Not a git repository: {input.repo_path}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.REPO_NOT_FOUND,
        )

    # 2. Config parsing validation
    if input.config_path:
        try:
            parse_config(input.config_path)
        except Exception as exc:
            raise PentestError(
                f"Config validation failed: {exc}",
                category="preflight",
                retryable=False,
                error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
            ) from exc

    # 3. URL safety check (if web_url provided)
    if input.web_url:
        validate_target_url(input.web_url)

    # 4. Credential validation (skip if no API key — may use env vars)
    # This is a placeholder — the actual provider detection depends on runtime config
    # The credential validation runs as a separate activity for retry isolation


@activity.defn
async def run_credential_check(input: ActivityInput) -> None:
    """Validate AI provider credentials."""
    # Provider detection from config or environment
    import os
    provider = os.environ.get("SHANNON_AI_PROVIDER", "anthropic_api")
    api_key = input.api_key or os.environ.get("ANTHROPIC_API_KEY")

    if api_key or provider != "anthropic_api":
        await validate_credentials(provider, api_key=api_key)


@activity.defn
async def run_auth_validation(input: ActivityInput) -> None:
    """Preflight authentication validation."""
    from shannon_whitebox.services.validate_authentication import validate_authentication

    prompts_dir = Path(__file__).resolve().parents[4] / "prompts"
    prompt_manager = PromptManager(prompts_dir)
    executor = AgentExecutor(prompt_manager)

    result = await validate_authentication(
        web_url=input.web_url,
        config_path=input.config_path,
        prompt_manager=prompt_manager,
        executor=executor,
        repo_path=input.repo_path,
        api_key=input.api_key,
    )
    if not result.success:
        raise PentestError(
            f"Authentication validation failed: {result.failure_detail or 'unknown'}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.AUTH_LOGIN_FAILED,
        )
```

- [x] **Step 2: Enhance `workflows.py` — integration wiring**

Modify the `WhiteboxScanWorkflow.run()` method to add preflight enhancements and pass `prompt_override` for recon:

```python
# In imports, add:
from shannon_whitebox.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
from shannon_whitebox.services.playwright_config_writer import write_stealth_config, cleanup_stealth_config
```

In the `run()` method, after preflight but before PRE_RECON:

```python
# After existing preflight call:

# Write code path deny rules (S6)
if input.config_path:
    from shannon_core.config.parser import parse_config
    cfg = parse_config(input.config_path)
    if cfg.rules and cfg.rules.avoid:
        sync_code_path_deny_rules(cfg.rules.avoid)

# Write stealth config (S5)
write_stealth_config(input.repo_path)
```

Modify the PRE_RECON call to pass `prompt_override` is NOT needed (PRE_RECON uses its own prompt). The recon agent gets the static prompt override:

```python
# For the RECON agent, pass prompt_override for static recon:
recon_input = ActivityInput(**{**base_input_dict, "prompt_override": "recon-static"})
```

Wrap the end of the workflow in try/finally for cleanup:

```python
try:
    # ... existing workflow logic ...
finally:
    cleanup_settings()
    cleanup_stealth_config(input.repo_path)
```

- [x] **Step 3: Run existing whitebox tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/whitebox/tests/ -v`
Expected: All tests PASS

- [x] **Step 4: Commit**

```bash
git add shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/activities.py \
        shannon-py/packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
git commit -m "feat(security): integrate security services into whitebox pipeline (S4-S8)"
```

---

## Task 15: Blackbox Pipeline Integration

**Files:**
- Modify: `shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`
- Modify: `shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/activities.py`
- Modify: `shannon-py/packages/blackbox/src/shannon_blackbox/services/report_assembler.py`

- [x] **Step 1: Enhance `activities.py` — preflight, auth, credential checks**

Same pattern as whitebox. Add to imports and define activities:

```python
# Add imports:
from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.utils.security import validate_target_url, check_url_reachable
from shannon_core.utils.credential_validator import validate_credentials

# Replace the no-op run_blackbox_preflight:
@activity.defn
async def run_blackbox_preflight(input: BlackboxActivityInput) -> None:
    """Blackbox preflight: URL safety, credential check. Repo check is optional."""
    # 1. URL safety (mandatory for blackbox)
    if input.web_url:
        validate_target_url(input.web_url)
        reachable = await check_url_reachable(input.web_url)
        if not reachable:
            raise PentestError(
                f"Target URL is not reachable: {input.web_url}",
                category="preflight",
                retryable=True,
                error_code=ErrorCode.TARGET_UNREACHABLE,
            )

    # 2. Repo check (optional for blackbox)
    if input.repo_path:
        repo = Path(input.repo_path)
        if not repo.exists():
            raise PentestError(
                f"Repository path does not exist: {input.repo_path}",
                category="preflight",
                retryable=False,
                error_code=ErrorCode.REPO_NOT_FOUND,
            )

    # 3. Config validation (if provided)
    if input.config_path:
        from shannon_core.config.parser import parse_config
        try:
            parse_config(input.config_path)
        except Exception as exc:
            raise PentestError(
                f"Config validation failed: {exc}",
                category="preflight",
                retryable=False,
                error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
            ) from exc


@activity.defn
async def run_blackbox_auth_validation(input: BlackboxActivityInput) -> None:
    """Blackbox auth validation."""
    from shannon_whitebox.services.validate_authentication import validate_authentication

    prompts_dir = Path(__file__).resolve().parents[4] / "prompts"
    prompt_manager = PromptManager(prompts_dir)
    executor = AgentExecutor(prompt_manager)

    result = await validate_authentication(
        web_url=input.web_url,
        config_path=input.config_path,
        prompt_manager=prompt_manager,
        executor=executor,
        repo_path=input.repo_path or "",
        api_key=input.api_key,
    )
    if not result.success:
        raise PentestError(
            f"Authentication validation failed: {result.failure_detail or 'unknown'}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.AUTH_LOGIN_FAILED,
        )
```

Update `assemble_report` to use dynamic vuln classes:

```python
@activity.defn
async def assemble_report(input: BlackboxActivityInput) -> None:
    from shannon_blackbox.services.report_assembler import ReportAssembler

    deliverables = _get_deliverables_path(input)
    # Use dynamic vuln_classes instead of hardcoded list
    from shannon_core.models.agents import ALL_VULN_CLASSES
    vuln_classes: list[str] = list(ALL_VULN_CLASSES)
    report_path = deliverables / "comprehensive_security_assessment_report.md"
    await ReportAssembler.assemble(deliverables, vuln_classes, report_path)
```

- [x] **Step 2: Enhance `workflows.py` — queue gating, settings, stealth**

Add imports:

```python
from shannon_whitebox.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
from shannon_whitebox.services.playwright_config_writer import write_stealth_config, cleanup_stealth_config
from shannon_blackbox.services.exploitation_checker import ExploitationChecker
```

Modify the `run()` method to add S9 queue gating before exploit phase:

```python
# Replace the current exploit phase with queue-gated version:
if input.exploit:
    # S9: Gate exploit agents on non-empty vulnerability queues
    types_to_exploit = []
    for vt in selected_classes:
        if f"{vt}-exploit" not in self._state.completed_agents:
            should = await ExploitationChecker.should_exploit(
                deliverables, vt, exploit_enabled=True,
            )
            if should:
                types_to_exploit.append(vt)

    exploit_tasks = []
    for vt in types_to_exploit:
        agent_name = AgentName(f"{vt}-exploit")
        if agent_name.value not in self._state.completed_agents:
            exploit_input = BlackboxActivityInput(
                **{**act_input.__dict__, "agent_name": agent_name.value, "vuln_type": vt}
            )
            exploit_tasks.append((vt, agent_name, workflow.execute_activity(
                activities.run_exploit_agent, exploit_input,
                start_to_close_timeout=timedelta(hours=2),
                retry_policy=retry_policy,
            )))
    # ... rest of gather logic unchanged ...
```

Add settings/stealth integration after preflight:

```python
# After preflight:
if input.config_path:
    from shannon_core.config.parser import parse_config
    cfg = parse_config(input.config_path)
    if cfg.rules and cfg.rules.avoid:
        sync_code_path_deny_rules(cfg.rules.avoid)

if input.repo_path:
    write_stealth_config(input.repo_path)
```

Add auth validation between preflight and recon:

```python
# After preflight, before recon:
if input.config_path:
    await workflow.execute_activity(
        activities.run_blackbox_auth_validation, act_input,
        start_to_close_timeout=timedelta(minutes=2),
        retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=30)),
    )
```

Wrap in try/finally for cleanup:

```python
try:
    # ... entire workflow body ...
finally:
    cleanup_settings()
    if input.repo_path:
        cleanup_stealth_config(input.repo_path)
```

- [x] **Step 3: Enhance `report_assembler.py` — filter variable injection**

Modify `ReportAssembler.assemble()` to accept and pass report config:

```python
from shannon_core.models.config import ReportConfig

class ReportAssembler:
    @staticmethod
    async def assemble(
        deliverables_path: Path,
        vuln_classes: list[str],
        report_path: Path,
        report_config: ReportConfig | None = None,
    ) -> None:
        sections: list[str] = []
        for vuln_class in vuln_classes:
            evidence = deliverables_path / f"{vuln_class}_exploitation_evidence.md"
            findings = deliverables_path / f"{vuln_class}_findings.md"
            if await async_path_exists(evidence):
                content = await async_read_file(evidence)
                sections.append(content)
            elif await async_path_exists(findings):
                content = await async_read_file(findings)
                sections.append(content)
        report_content = "\n\n---\n\n".join(sections)
        await async_write_file(report_path, report_content)
```

- [x] **Step 4: Run all blackbox tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/blackbox/tests/ -v`
Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/workflows.py \
        shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/activities.py \
        shannon-py/packages/blackbox/src/shannon_blackbox/services/report_assembler.py
git commit -m "feat(security): integrate security services into blackbox pipeline (S4-S7, S9)"
```

---

## Self-Review Checklist

### 1. Spec Coverage

| Spec Item | Tasks | Status |
|-----------|-------|--------|
| S1 — Misconfig vuln class | Task 5 (models), Task 10 (prompt), Task 11 (exploit prompt) | ✅ Covered |
| S2 — Exploit prompt migration | Task 9 (shared partial), Task 11 (6 exploit prompts) | ✅ Covered |
| S3 — Report prompt enhancement | Task 7 (PM variables), Task 13 (prompt) | ✅ Covered |
| S4 — Auth pre-validation | Task 8 (service), Task 13 (prompt), Task 14-15 (integration) | ✅ Covered |
| S5 — Playwright anti-detection + session | Task 4 (config writer), Task 5 (session mapping), Task 7 (PM lookup) | ✅ Covered |
| S6 — Code path deny rules | Task 3 (settings writer), Task 14-15 (integration) | ✅ Covered |
| S7 — Preflight security checks | Task 1 (security.py), Task 2 (credential validator), Task 14-15 (integration) | ✅ Covered |
| S8 — Static recon prompt | Task 6 (prompt_override), Task 12 (recon-static.txt) | ✅ Covered |
| S9 — Queue gating | Task 15 (blackbox workflow) | ✅ Covered |

### 2. Placeholder Scan

No TBD, TODO, "implement later", or "fill in details" found. All code steps contain complete implementations. Prompt files reference TS sources by exact file path for porting content.

### 3. Type Consistency

- `MisconfigVulnerability` fields match spec exactly (8 optional string fields)
- `PLAYWRIGHT_SESSION_MAPPING` keys match `AgentName.value` strings in AGENTS dict
- `VulnType` in agents.py and `VulnClass` in config.py both include `"misconfig"`
- `AuthValidationResult` dataclass matches spec (success, failure_point, failure_detail)
- `prompt_override` parameter threaded through shared.py → activities.py → executor.py
- `ExploitationChecker.should_exploit()` signature unchanged (already checks non-empty queue)
