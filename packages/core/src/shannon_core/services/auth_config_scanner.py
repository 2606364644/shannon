"""Deterministic auth-config scanner (spec §5.8 GitNexus track for vuln-auth).

Scans source + config files for auth/session security configuration issues at
the constant level (no AST, no runtime probes):
  - Cookie set-points missing HttpOnly / Secure / SameSite
  - HSTS absent or weak (max-age < 31536000)
  - CORS wildcard origin / credentials+wildcard

Reuses the framework_analyzer.py scanning pattern: _find_source_files (entry
files + subdir rglob, skipping node_modules/vendor) + _pattern_in_content
(literal-first, regex-fallback to avoid unescaped `(` re.error).

This is the deterministic input that the vuln-auth LLM reads as a starting
point (like vuln-authz reads Endpoint Security Context) and that gets merged
into auth_gitnexus_queue.json for the dual-track verdict OR (Plan 3).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# --- Source file discovery (mirrors framework_analyzer._find_source_files) ---

_SOURCE_EXTS = (".js", ".ts", ".jsx", ".tsx", ".py", ".go", ".java", ".php")
_ENTRY_FILES = (
    "server.js", "server.ts", "app.js", "app.ts", "index.js", "index.ts",
    "main.go", "main.py", "app.py",
)
_SCAN_SUBDIRS = (
    "routes", "api", "middleware", "auth", "security", "config",
    "src/routes", "src/middleware", "src/auth", "src/config",
)
_SKIP_DIRS = {"node_modules", "vendor", ".git", "dist", "build", "test", "tests", "__pycache__"}


def _find_source_files(codebase_path: str) -> list[Path]:
    """Find source/config files to scan, mirroring framework_analyzer."""
    base = Path(codebase_path)
    files: list[Path] = []
    seen: set[Path] = set()

    def _add(p: Path) -> None:
        if p.exists() and p.is_file() and p not in seen:
            seen.add(p)
            files.append(p)

    for candidate in _ENTRY_FILES:
        _add(base / candidate)
    for subdir in _SCAN_SUBDIRS:
        d = base / subdir
        if d.exists():
            for p in d.rglob("*"):
                if p.is_file() and p.suffix in _SOURCE_EXTS:
                    _add(p)
    # Root-level config files
    for p in base.glob("*"):
        if p.is_file() and p.suffix in (".yaml", ".yml", ".json", ".env", ".conf"):
            _add(p)
    # Also scan top-level source files (not just subdirs)
    for p in base.glob("*"):
        if p.is_file() and p.suffix in _SOURCE_EXTS:
            _add(p)
    return files


def _pattern_in_content(pattern: str, content: str) -> bool:
    """Literal-first, regex-fallback match (ported from framework_analyzer)."""
    if pattern in content:
        return True
    try:
        return re.search(pattern, content) is not None
    except re.error:
        return False


# --- Data models ---

@dataclass(frozen=True)
class ConfigFinding:
    """One suspicious auth-config item found by the scanner."""
    category: str            # "cookie" | "hsts" | "cors" | "jwt_claim" | "rate_limit"
    file_path: str
    line: int
    detail: str              # human-readable description of the issue
    cookie_name: str | None = None
    missing_flags: tuple[str, ...] = ()   # cookie: which flags missing
    evidence: str = ""       # the matched source snippet


@dataclass
class AuthConfigScanResult:
    """Result of auth-config scan (spec §5.8 GitNexus track)."""
    cookie_findings: list[ConfigFinding] = field(default_factory=list)
    hsts_findings: list[ConfigFinding] = field(default_factory=list)
    cors_findings: list[ConfigFinding] = field(default_factory=list)
    jwt_claim_findings: list[ConfigFinding] = field(default_factory=list)   # Task 2
    rate_limit_findings: list[ConfigFinding] = field(default_factory=list)  # Task 3

    def all_findings(self) -> list[ConfigFinding]:
        return (self.cookie_findings + self.hsts_findings + self.cors_findings
                + self.jwt_claim_findings + self.rate_limit_findings)


# --- Cookie scanning ---

_COOKIE_NAME_RE = re.compile(
    r"(?:res\.cookie|\.cookie|set_cookie|response\.set_cookie)\s*\(\s*['\"]([A-Za-z0-9_\-]+)['\"]"
)
_FLAG_PRESENCE = {
    "HttpOnly": re.compile(r"httpOnly\s*[:=]\s*true|httponly", re.IGNORECASE),
    "Secure": re.compile(r"secure\s*[:=]\s*true|secure\s*[,)\s]", re.IGNORECASE),
    "SameSite": re.compile(r"sameSite\s*[:=]|samesite", re.IGNORECASE),
}


def _scan_cookies(file_path: Path, content: str, lines: list[str]) -> list[ConfigFinding]:
    findings: list[ConfigFinding] = []
    for m in re.finditer(r"(res\.cookie|\.cookie|set_cookie|response\.set_cookie)\s*\(", content):
        line_no = content[:m.start()].count("\n") + 1
        # window of +/-3 lines for flag check (cookie opts often on next lines)
        lo = max(0, line_no - 2)
        hi = min(len(lines), line_no + 4)
        window = "\n".join(lines[lo:hi])
        # extract cookie name
        name_m = _COOKIE_NAME_RE.search(window)
        cookie_name = name_m.group(1) if name_m else "unknown"
        missing = tuple(
            flag for flag, rx in _FLAG_PRESENCE.items() if not rx.search(window)
        )
        if missing:
            findings.append(ConfigFinding(
                category="cookie",
                file_path=str(file_path),
                line=line_no,
                detail=f"Cookie '{cookie_name}' set without: {', '.join(missing)}",
                cookie_name=cookie_name,
                missing_flags=missing,
                evidence=window.strip()[:200],
            ))
    return findings


# --- HSTS scanning ---

_HSTS_PATTERNS = (
    "Strict-Transport-Security",
    "helmet.hsts",
    "helmet(",  # helmet enables HSTS by default; absence of helmet is the signal
)
_MAX_AGE_RE = re.compile(r"max-age\s*=?\s*(\d+)|maxAge\s*[:=]\s*(\d+)", re.IGNORECASE)


def _scan_hsts(file_path: Path, content: str, lines: list[str], has_any_hsts_global: bool) -> list[ConfigFinding]:
    findings: list[ConfigFinding] = []
    # If no HSTS config anywhere in this file at all → flag the entry/listen line
    has_hsts_in_file = any(_pattern_in_content(p, content) for p in _HSTS_PATTERNS)
    if not has_hsts_in_file:
       # Only flag absence on entry files (avoid one finding per random file)
        if _pattern_in_content(r"app\.(listen|use)|createServer|app\s*=\s*express", content):
            listen_m = re.search(r"app\.(listen|use)\s*\(", content)
            line_no = content[:listen_m.start()].count("\n") + 1 if listen_m else 1
            findings.append(ConfigFinding(
                category="hsts",
                file_path=str(file_path),
                line=line_no,
                detail="HSTS absent: no Strict-Transport-Security / helmet.hsts configuration found",
                evidence="",
            ))
        return findings
    # HSTS present — check max-age strength
    for m in _MAX_AGE_RE.finditer(content):
        age = int(m.group(1) or m.group(2))
        line_no = content[:m.start()].count("\n") + 1
        if age < 31536000:
            findings.append(ConfigFinding(
                category="hsts",
                file_path=str(file_path),
                line=line_no,
                detail=f"HSTS max-age={age} is weak (< 31536000 = 1 year); recommended >= 31536000",
                evidence=lines[line_no - 1].strip() if line_no - 1 < len(lines) else "",
            ))
    return findings


# --- CORS scanning ---

_CORS_ORIGIN_RE = re.compile(
    r"(?:Access-Control-Allow-Origin|origin)\s*[:=]\s*['\"]\*['\"]", re.IGNORECASE
)
_CORS_CRED_RE = re.compile(r"credentials\s*[:=]\s*true", re.IGNORECASE)
_CORS_USAGE_RE = re.compile(r"cors\s*\(", re.IGNORECASE)


def _scan_cors(file_path: Path, content: str, lines: list[str]) -> list[ConfigFinding]:
    findings: list[ConfigFinding] = []
    wildcard_hits = list(_CORS_ORIGIN_RE.finditer(content))
    cred = bool(_CORS_CRED_RE.search(content))
    for m in wildcard_hits:
        line_no = content[:m.start()].count("\n") + 1
        detail = "CORS wildcard origin '*' allows any origin"
        if cred:
            detail += " combined with credentials:true (reflects credentialed requests — severe)"
        findings.append(ConfigFinding(
            category="cors",
            file_path=str(file_path),
            line=line_no,
            detail=detail,
            evidence=lines[line_no - 1].strip() if line_no - 1 < len(lines) else "",
        ))
    if cred and not wildcard_hits and _CORS_USAGE_RE.search(content):
        # credentials:true without explicit wildcard — still note for LLM review
        m = _CORS_CRED_RE.search(content)
        line_no = content[:m.start()].count("\n") + 1
        findings.append(ConfigFinding(
            category="cors",
            file_path=str(file_path),
            line=line_no,
            detail="CORS credentials:true enabled — verify origin is a strict allowlist (not wildcard)",
            evidence=lines[line_no - 1].strip() if line_no - 1 < len(lines) else "",
        ))
    return findings


# --- JWT claim scanning (nOAuth) ---

_JWT_DECODE_PATTERNS = (
    r"jwt\.verify\s*\(",
    r"jwt\.decode\s*\(",
    r"jsonwebtoken\.verify\s*\(",
    r"jsonwebtoken\.decode\s*\(",
    r"jwt_decode\s*\(",
    r"decode_token\s*\(",
    r"jwt\.decode\s*\[",
)
_JWT_DECODE_RE = re.compile("|".join(_JWT_DECODE_PATTERNS), re.IGNORECASE)

# Mutable identity attributes (nOAuth): using these for identity is the flaw.
_MUTABLE_CLAIM_RE = re.compile(
    r"payload\s*(?:\.\s*([A-Za-z_][A-Za-z0-9_]*)|\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\])"
)
_SUB_USE_RE = re.compile(r"payload\s*(?:\.\s*sub|\[\s*['\"]sub['\"]\s*\])", re.IGNORECASE)
_MUTABLE_CLAIMS = {"email", "name", "preferred_username", "username", "user", "displayName"}


def _scan_jwt_claims(file_path: Path, content: str, lines: list[str]) -> list[ConfigFinding]:
    findings: list[ConfigFinding] = []
    for m in _JWT_DECODE_RE.finditer(content):
        line_no = content[:m.start()].count("\n") + 1
        lo = max(0, line_no - 1)
        hi = min(len(lines), line_no + 6)  # +/-5 line window
        window = "\n".join(lines[lo:hi])
        # Safe: uses sub in the window
        if _SUB_USE_RE.search(window):
            continue
        # Dangerous: reads a mutable claim
        for cm in _MUTABLE_CLAIM_RE.finditer(window):
            claim = cm.group(1) or cm.group(2)
            if claim in _MUTABLE_CLAIMS:
                findings.append(ConfigFinding(
                    category="jwt_claim",
                    file_path=str(file_path),
                    line=line_no,
                    detail=(
                        f"nOAuth candidate: JWT payload uses mutable claim '{claim}' for identity; "
                        f"use the immutable 'sub' claim instead (attacker can control {claim} "
                        f"via their own IdP tenant to impersonate users)"
                    ),
                    evidence=window.strip()[:200],
                ))
                break  # one finding per decode point
    return findings


# --- Rate-limit scanning ---

_AUTH_ROUTE_PATTERNS = (
    r"(?:post|get|put|all|route)\s*\(\s*['\"](?:/?(?:login|signin|signup|register|reset|recover|forgot|token|oauth|auth))",
    r"@(?:app|router|bp)\.route\s*\(\s*['\"](?:/?(?:login|signup|reset|token|oauth|forgot))",
    r"@(app|router)\.(post|get)\s*\(\s*['\"]/(?:login|signup|reset|token)",
)
_AUTH_ROUTE_RE = re.compile("|".join(_AUTH_ROUTE_PATTERNS), re.IGNORECASE)
_RATE_LIMIT_HINTS = (
    "rateLimit", "rate-limiter", "express-rate-limit", "rate_limit",
    "slowauth", "throttle", "limiter", "@limiter", "flask_limiter",
    "slowapi", "RateLimiter",
)
_RATE_LIMIT_RE = re.compile("|".join(re.escape(h) for h in _RATE_LIMIT_HINTS), re.IGNORECASE)
_SENSITIVE_PATH_RE = re.compile(
    r"/?(login|signin|signup|register|reset|recover|forgot|token|oauth)", re.IGNORECASE
)


def _scan_rate_limits(file_path: Path, content: str, lines: list[str]) -> list[ConfigFinding]:
    findings: list[ConfigFinding] = []
    for m in _AUTH_ROUTE_RE.finditer(content):
        line_no = content[:m.start()].count("\n") + 1
        lo = max(0, line_no - 3)
        hi = min(len(lines), line_no + 6)  # window covers middleware in front of handler
        window = "\n".join(lines[lo:hi])
        if _RATE_LIMIT_RE.search(window):
            continue  # rate-limit middleware present
        # Extract the sensitive path from the match itself (the window may
        # span adjacent routes, so searching it can grab the wrong path).
        path_m = _SENSITIVE_PATH_RE.search(m.group(0))
        path = path_m.group(0) if path_m else "auth-endpoint"
        findings.append(ConfigFinding(
            category="rate_limit",
            file_path=str(file_path),
            line=line_no,
            detail=(
                f"Auth-sensitive endpoint '{path}' has no rate-limit middleware detected "
                f"in its handler window — vulnerable to brute force / credential stuffing"
            ),
            evidence=window.strip()[:200],
        ))
    return findings


# --- Orchestrator ---

async def scan_auth_config(codebase_path: str) -> AuthConfigScanResult:
    """Scan codebase for auth-config issues (spec §5.8 GitNexus track).

    Deterministic, constant-level. No runtime probes. Failures per-file are
    logged and skipped (never abort the whole scan).
    """
    result = AuthConfigScanResult()
    try:
        files = _find_source_files(codebase_path)
    except Exception as exc:
        logger.warning("auth-config scan: file discovery failed: %s", exc)
        return result

    # Pre-compute global HSTS presence (one absence finding per entry file, not per file)
    contents: dict[Path, str] = {}
    for fp in files:
        try:
            contents[fp] = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            logger.debug("auth-config scan: skip %s: %s", fp, exc)

    has_any_hsts_global = any(
        any(_pattern_in_content(p, c) for p in _HSTS_PATTERNS)
        for c in contents.values()
    )

    for fp, content in contents.items():
        try:
            lines = content.splitlines()
            result.cookie_findings.extend(_scan_cookies(fp, content, lines))
            result.hsts_findings.extend(_scan_hsts(fp, content, lines, has_any_hsts_global))
            result.cors_findings.extend(_scan_cors(fp, content, lines))
            result.jwt_claim_findings.extend(_scan_jwt_claims(fp, content, lines))
            result.rate_limit_findings.extend(_scan_rate_limits(fp, content, lines))
        except Exception as exc:
            logger.debug("auth-config scan: error in %s: %s", fp, exc)

    logger.info(
        "auth-config scan: %d cookie, %d hsts, %d cors, %d jwt_claim, %d rate_limit "
        "findings across %d files",
        len(result.cookie_findings), len(result.hsts_findings),
        len(result.cors_findings), len(result.jwt_claim_findings),
        len(result.rate_limit_findings), len(files),
    )
    return result
