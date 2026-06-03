# Blackbox Scan Effectiveness Enhancement Design

> **Date**: 2026-06-03
> **Scope**: 7 security-effectiveness gaps between Python refactored and TypeScript original blackbox scanning
> **Status**: Draft

## Background

A detailed feature-by-feature comparison between the original TypeScript Shannon (`/root/shannon/`) and the refactored Python version (`/root/shannon-py/`) identified 17 differences in the blackbox scanning subsystem. This design addresses the 7 differences that **directly impact security effectiveness** — vulnerability discovery capability and exploitation success rate.

The remaining 10 differences (resume capability, DI container, progress query, phase logging, heartbeat, report metadata injection, spending cap detection, config validation, `@include()` directives, `<if-live>` conditional blocks) affect engineering quality but not security outcomes and are out of scope.

## Design Principles

1. **Align with TypeScript proven patterns** — TS prompts and security controls have been battle-tested; translate rather than reinvent.
2. **Minimal structural change** — modify existing files; avoid new packages or architectural changes.
3. **Backward compatible** — new parameters default to current behavior where possible.

---

## Enhancement 1: Exploit Prompt Quality Alignment

### Problem

Python exploit prompts are 52% shorter on average than their TS counterparts. They are missing critical exploitation methodology sections that directly affect exploitation success rate.

### Current State

| Prompt | PY Lines | TS Lines | Reduction |
|--------|---------|---------|-----------|
| `injection-exploit` | 176 | 453 | 61% |
| `xss-exploit` | 312 | 444 | 30% |
| `auth-exploit` | 351 | 423 | 17% |
| `authz-exploit` | 166 | 427 | 61% |
| `ssrf-exploit` | 178 | 504 | 65% |

### What's Missing (per prompt)

The shared `_exploit-methodology.txt` already covers some framework elements (3-stage workflow, proof levels, verdict categories, WAF evasion principles). However, each individual exploit prompt is missing:

1. **Vulnerability-specific attack patterns** — detailed attack trees for each vuln type (e.g., SQL injection union-based → boolean-based → time-based → out-of-band escalation)
2. **Technology fingerprinting guidance** — how to identify specific backend tech from error messages, response headers, timing differences
3. **Payload construction cookbook** — parameterized payload templates for common backends (MySQL, PostgreSQL, Oracle, SQL Server for injection; React, Angular, vanilla JS for XSS)
4. **Intelligence gathering phase** — explicit instructions to read recon deliverable + vuln analysis deliverable before constructing payloads
5. **Evidence chain of custody** — step-by-step evidence collection protocol ensuring legal/admissibility quality
6. **Escalation decision tree** — when to escalate from simple to advanced techniques, when to mark as BLOCKED_BY_SECURITY

### Solution

For each of the 5 exploit prompts, translate the corresponding TS prompt section by section:

1. Read TS prompt from `/root/shannon/apps/worker/prompts/exploit-*.txt`
2. Translate while preserving `{{variable}}` placeholders in Python format
3. Add missing sections in the same order as TS
4. Keep the shared `_exploit-methodology.txt` as-is (it already covers framework-level concepts)

### Files Modified

- `/root/shannon-py/prompts/injection-exploit.txt`
- `/root/shannon-py/prompts/xss-exploit.txt`
- `/root/shannon-py/prompts/auth-exploit.txt`
- `/root/shannon-py/prompts/authz-exploit.txt`
- `/root/shannon-py/prompts/ssrf-exploit.txt`

### Validation

- Each prompt reaches within 10% of its TS counterpart's line count
- All `{{variable}}` placeholders preserved and tested
- Prompt structure follows: scope → methodology → attack patterns → evidence → classification

---

## Enhancement 2: Misconfig Prompt Completion

### Problem

The `misconfig-exploit` prompt is a 65-line skeleton compared to the TS version's 369 lines. This vulnerability class is effectively non-functional.

### Current State

- 65 lines with basic guidance
- No misconfiguration-specific detection methodology
- No configuration analysis patterns (headers, TLS, cookies, CORS, CSP, etc.)

### Solution

Full translation of TS `exploit-misconfig` prompt (369 lines), covering:

1. **Server misconfiguration detection** — default credentials, debug modes, verbose errors, directory listing
2. **Security header analysis** — missing X-Frame-Options, CSP, HSTS, X-Content-Type-Options
3. **CORS misconfiguration** — overly permissive origins, credential exposure
4. **TLS configuration** — weak ciphers, protocol versions, certificate issues
5. **Cookie security** — missing Secure/HttpOnly/SameSite flags
6. **Cloud metadata exposure** — AWS/GCP/Azure metadata endpoints
7. **Information disclosure** — stack traces, version headers, error pages

### Files Modified

- `/root/shannon-py/prompts/misconfig-exploit.txt`

### Validation

- Prompt reaches ~370 lines
- Queue schema for misconfig validated in `packages/core/src/shannon_core/models/queue_schemas.py`

---

## Enhancement 3: Concurrency Control

### Problem

All exploit agents launch simultaneously via `asyncio.gather()` with no limit. This can trigger WAF rules, cause IP bans, and lead to rate-limiting that reduces overall exploitation success.

### Current State

```python
# workflows.py - current code
tasks = [self._run_exploit(vt) for vt in vuln_types]
results = await asyncio.gather(*tasks)  # ALL launch at once
```

### Solution

Add `asyncio.Semaphore`-based concurrency control to `BlackboxScanWorkflow`:

```python
class BlackboxScanWorkflow:
    def __init__(self):
        self._max_concurrent_exploits = 3  # configurable

    async def run(self, params: BlackboxWorkflowParams):
        # ...
        semaphore = asyncio.Semaphore(self._max_concurrent_exploits)

        async def bounded_exploit(vuln_type: str):
            async with semaphore:
                return await self._run_exploit(vuln_type)

        tasks = [bounded_exploit(vt) for vt in vuln_types]
        results = await asyncio.gather(*tasks, return_exceptions=True)
```

### Configuration Priority

1. CLI `--max-concurrent N` flag → `BlackboxWorkflowParams.max_concurrent`
2. Session config `maxConcurrentExploits` field
3. Default: `3`

### Files Modified

- `/root/shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` — add semaphore
- `/root/shannon-py/packages/blackbox/src/shannon_blackbox/models/workflow_params.py` — add `max_concurrent` field
- `/root/shannon-py/packages/blackbox/src/shannon_blackbox/cli/main.py` — add `--max-concurrent` CLI arg

### Validation

- Unit test: 6 vuln types with `max_concurrent=2` → verify at most 2 running at any time
- Integration test: full workflow runs with concurrency limit

---

## Enhancement 4: Browser Session Isolation

### Problem

All 6 exploit agents share a single browser session. During parallel exploitation, cookies, localStorage, and session state can interfere between agents, causing authentication state loss and exploitation failures.

### Current State

`write_stealth_config()` in `playwright_config_writer.py` writes a single `.playwright/cli.config.json`. All agents use the default session.

### Solution

Implement per-agent session mapping:

```python
# In playwright_config_writer.py
SESSION_MAPPING = {
    "injection-exploit": "agent1",
    "xss-exploit": "agent2",
    "auth-exploit": "agent3",
    "ssrf-exploit": "agent4",
    "authz-exploit": "agent5",
    "misconfig-exploit": "agent6",
}

def get_session_id(agent_name: str) -> str:
    return SESSION_MAPPING.get(agent_name, "default")
```

Modify `write_stealth_config()` to accept an optional `session_id` parameter. When provided, it writes a session-specific config file (e.g., `.playwright/cli.config.agent1.json`) with isolated storage paths.

The exploit executor passes its session ID through the prompt variables, so the agent knows which session to use.

### Files Modified

- `/root/shannon-py/packages/core/src/shannon_core/services/playwright_config_writer.py` — add session mapping and per-session config
- `/root/shannon-py/packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py` — pass session ID to prompt variables
- `/root/shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` — ensure each exploit activity gets its session ID

### Validation

- Verify 6 different config files are written when all exploit agents run
- Verify each agent's browser instance uses its own config

---

## Enhancement 5: DNS Rebinding Protection

### Problem

The current preflight resolves DNS to check SSRF/loopback, but the actual HTTP request may resolve DNS again. An attacker-controlled DNS server can return a safe IP on the first lookup and a dangerous IP on the second (DNS rebinding), bypassing SSRF protection.

### Current State

`resolve_host()` resolves DNS and checks safety. `check_url_reachable()` makes an HTTP request but does not use the pinned IP — it resolves DNS again.

### Solution

Pin the resolved IP to the HTTP connection:

```python
# In security.py
async def resolve_and_pin_host(url: str) -> tuple[str, str]:
    """Returns (pinned_ip, original_host)"""
    parsed = urlparse(url)
    original_host = parsed.hostname
    pinned_ip = await resolve_host(original_host)

    # Safety checks on the pinned IP
    check_ssrf(pinned_ip)
    check_loopback(pinned_ip)

    return (pinned_ip, original_host)

async def check_url_reachable(url: str, pinned_ip: str = None, original_host: str = None):
    """
    If pinned_ip provided, connect to it directly with Host header.
    """
    if pinned_ip and original_host:
        # Build URL with IP, set Host header
        parsed = urlparse(url)
        ip_url = url.replace(original_host, pinned_ip)
        headers = {"Host": original_host}
        # Also handle TLS SNI
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.head(ip_url, headers=headers, timeout=10, follow_redirects=False)
    else:
        # Original behavior
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.head(url, timeout=10, follow_redirects=False)
```

### Integration

Modify `validate_target_url()` to:
1. Call `resolve_and_pin_host()` first
2. Pass pinned IP to `check_url_reachable()`
3. Store pinned IP in workflow state for use by downstream activities

### Files Modified

- `/root/shannon-py/packages/core/src/shannon_core/utils/security.py` — add `resolve_and_pin_host()`, modify `check_url_reachable()` and `validate_target_url()`
- `/root/shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/activities.py` — pass pinned IP through preflight result

### Validation

- Unit test: DNS rebinding scenario (safe IP on first lookup, dangerous IP on second) → verify connection uses pinned safe IP
- Verify TLS SNI is preserved (Host header = original domain)

---

## Enhancement 6: Queue Validation Enhancement

### Problem

The current `ExploitationChecker.should_exploit()` only checks if the queue file exists and has a non-empty `vulnerabilities` array. This is insufficient — malformed JSON, missing deliverables, or inconsistent data can cause exploit agents to fail or produce incorrect results.

### Current State

```python
# exploitation_checker.py - 21 lines
class ExploitationChecker:
    async def should_exploit(self, vuln_type: str, workspace_dir: Path) -> bool:
        queue_path = workspace_dir / f"{vuln_type}_exploitation_queue.json"
        if not queue_path.exists():
            return False
        data = json.loads(queue_path.read_text())
        return bool(data.get("vulnerabilities"))
```

### Solution

Implement a multi-level validation pipeline:

```python
@dataclass
class QueueValidationResult:
    valid: bool
    reason: str = ""
    vuln_count: int = 0
    retryable: bool = True  # whether retrying might fix the issue

class ExploitationChecker:
    async def validate_queue(self, vuln_type: str, workspace_dir: Path) -> QueueValidationResult:
        queue_path = workspace_dir / f"{vuln_type}_exploitation_queue.json"
        deliverable_path = workspace_dir / f"{vuln_type}_analysis_deliverable.md"

        # Level 1: File existence
        if not queue_path.exists():
            return QueueValidationResult(False, "queue_file_missing", retryable=False)

        # Level 2: JSON parsing
        try:
            data = json.loads(queue_path.read_text())
        except json.JSONDecodeError:
            return QueueValidationResult(False, "json_parse_error", retryable=False)

        # Level 3: Structure validation
        vulns = data.get("vulnerabilities")
        if vulns is None or not isinstance(vulns, list):
            return QueueValidationResult(False, "invalid_vulnerabilities_array", retryable=False)

        # Level 4: Deliverable symmetry (queue and deliverable must both exist or both absent)
        if not deliverable_path.exists():
            return QueueValidationResult(False, "deliverable_missing", retryable=False)

        # Level 5: Non-empty check
        if len(vulns) == 0:
            return QueueValidationResult(False, "empty_vulnerabilities", retryable=False)

        return QueueValidationResult(True, vuln_count=len(vulns))

    async def should_exploit(self, vuln_type: str, workspace_dir: Path) -> bool:
        result = await self.validate_queue(vuln_type, workspace_dir)
        return result.valid
```

### Files Modified

- `/root/shannon-py/packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py` — replace with multi-level validation
- `/root/shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` — use `validate_queue()` for error reporting

### Validation

- Unit tests for each validation level
- Test malformed JSON, missing deliverable, empty vulnerabilities
- Backward compatibility: `should_exploit()` still returns `bool`

---

## Enhancement 7: Retry Policy Enhancement

### Problem

Current retry policy is 3 attempts with 30s/5min backoff — too weak for production use. Network fluctuations, temporary rate limiting, or transient failures cause premature abandonment of potentially successful exploits.

### Current State

```python
RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=30), maximum_interval=timedelta(minutes=5))
```

### Solution

Implement environment-specific retry profiles matching TS configuration:

```python
# New file: packages/core/src/shannon_core/config/retry_profiles.py
from dataclasses import dataclass
from datetime import timedelta
from temporalio.common import RetryPolicy

@dataclass
class RetryProfile:
    """Retry configuration for a specific deployment environment."""
    maximum_attempts: int
    initial_interval: timedelta
    maximum_interval: timedelta
    backoff_coefficient: float
    non_retryable_error_types: list[str]

PRODUCTION = RetryPolicy(
    maximum_attempts=50,
    initial_interval=timedelta(minutes=5),
    maximum_interval=timedelta(minutes=30),
    backoff_coefficient=2.0,
    non_retryable_error_types=[
        "AuthenticationError",
        "PermissionError",
        "ConfigurationError",
        "TargetUnreachableError",
        "InvalidURLError",
        "SSRFError",
        "LoopbackError",
        "CredentialExpiredError",
    ],
)

TESTING = RetryPolicy(
    maximum_attempts=5,
    initial_interval=timedelta(seconds=10),
    maximum_interval=timedelta(seconds=30),
    backoff_coefficient=2.0,
    non_retryable_error_types=[
        "AuthenticationError",
        "ConfigurationError",
        "InvalidURLError",
    ],
)

SUBSCRIPTION = RetryPolicy(
    maximum_attempts=100,
    initial_interval=timedelta(minutes=5),
    maximum_interval=timedelta(hours=6),
    backoff_coefficient=2.0,
    non_retryable_error_types=PRODUCTION.non_retryable_error_types,
)

def get_retry_policy(mode: str = "production") -> RetryPolicy:
    """Get retry policy by mode name."""
    profiles = {
        "production": PRODUCTION,
        "testing": TESTING,
        "subscription": SUBSCRIPTION,
    }
    return profiles.get(mode, PRODUCTION)
```

### Integration

- Read `retry_profile` from session config, falling back to `pipeline_testing_mode` flag
- Apply to all activity calls in `workflows.py`

### Files Modified

- `/root/shannon-py/packages/core/src/shannon_core/config/retry_profiles.py` — new file
- `/root/shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` — use `get_retry_policy()`
- `/root/shannon-py/packages/core/src/shannon_core/config/__init__.py` — export

### Validation

- Unit test: verify correct profile selected for each mode
- Integration test: verify retry behavior with simulated failures

---

## Implementation Order

| Phase | Enhancement | Dependencies | Estimated Effort |
|-------|-------------|-------------|-----------------|
| 1 | Retry Policy (E7) | None — foundational | Small (new file + wiring) |
| 2 | Queue Validation (E6) | None | Small (replace 21-line file) |
| 3 | DNS Rebinding (E5) | None | Medium (security.py changes) |
| 4 | Concurrency Control (E3) | None | Medium (workflow changes) |
| 5 | Session Isolation (E4) | E3 (concurrent agents need isolated sessions) | Medium |
| 6 | Exploit Prompts (E1) | None — can parallel with code changes | Large (5 prompts × ~300 lines each) |
| 7 | Misconfig Prompt (E2) | None — can parallel with E1 | Medium (1 prompt × ~300 lines) |

E1 and E2 are prompt-only changes with no code dependencies and can be done in parallel with code changes (E3-E5).

## Files Summary

### New Files
- `/root/shannon-py/packages/core/src/shannon_core/config/retry_profiles.py`

### Modified Files
- `/root/shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`
- `/root/shannon-py/packages/blackbox/src/shannon_blackbox/pipeline/activities.py`
- `/root/shannon-py/packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py`
- `/root/shannon-py/packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`
- `/root/shannon-py/packages/blackbox/src/shannon_blackbox/cli/main.py`
- `/root/shannon-py/packages/core/src/shannon_core/utils/security.py`
- `/root/shannon-py/packages/core/src/shannon_core/services/playwright_config_writer.py`
- `/root/shannon-py/prompts/injection-exploit.txt`
- `/root/shannon-py/prompts/xss-exploit.txt`
- `/root/shannon-py/prompts/auth-exploit.txt`
- `/root/shannon-py/prompts/authz-exploit.txt`
- `/root/shannon-py/prompts/ssrf-exploit.txt`
- `/root/shannon-py/prompts/misconfig-exploit.txt`

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Prompt translation introduces formatting errors | Validate each prompt with `PromptManager.load_sync()` |
| DNS pinning breaks TLS for some hosts | Fallback to normal resolution if pinned connection fails (with warning) |
| Semaphore causes deadlocks in Temporal | Use `asyncio.Semaphore` (cooperative, not lock-based) |
| Session isolation increases browser resource usage | Default 3 concurrent limits resource pressure |
| Stronger retry causes longer workflows on real failures | Non-retryable error types prevent retrying unrecoverable errors |
