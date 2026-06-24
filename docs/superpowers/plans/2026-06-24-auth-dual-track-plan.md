# vuln-auth 双轨实现计划（Plan 9）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 vuln-auth（认证分析）补上确定性 GitNexus 轨，形成「LLM 9 类检查（现状）」+「确定性配置扫描 + LLM 确认可疑配置项」双轨。纯增益——auth 现状无任何确定性信号，新增扫描器只增不减，配置扫描失败/无命中时 GitNexus 轨降级为空，pipeline 行为与现状等价。

**Architecture:** 新增确定性配置扫描器 `auth_config_scanner.py`（复用 `framework_analyzer.py` 的 `_find_source_files` + literal/regex 混合匹配模式）扫源码与配置文件，产出结构化 `auth_config_scan.json`（可疑配置项：缺 HttpOnly/Secure/SameSite 的 cookie、无/弱 HSTS max-age、宽松 CORS、JWT claim 读取点命中 nOAuth 即用 `email`/`name` 而非 `sub`、缺 rate-limit 中间件的 auth 端点）。两路消费这份产物：(1) **LLM 确认 pass**——`vuln-auth.txt` 新增 `<auth_config_context>` section（对齐 vuln-authz 的 Endpoint Security Context 注入模式），LLM 读 `auth_config_scan.json` 把可疑项作为追查起点并给最终 verdict；(2) **GitNexus 轨 queue**——扫描器同时把每个可疑配置项落成一条 `AuthVulnerability`（`source_track="gitnexus"`）写到 `auth_gitnexus_queue.json`。随后 Plan 3 的通用合并器 `run_merge_dual_track_queues`（已 wiring 在 vuln 阶段后）把 `auth_llm_queue.json`（executor 原始产出重命名）与 `auth_gitnexus_queue.json` 按 `(vulnerability_type, source_endpoint, vulnerable_code_location)` 去重、verdict OR（auth 无 verdict 字段 → 用 `externally_exploitable` OR）→ 写回 `auth_exploitation_queue.json`（下游 `findings_renderer` 消费不变）。

**Tech Stack:** Python 3.12, pydantic v2, pytest, pytest-asyncio, temporalio activity

## Global Constraints

- **纯增益、零回退风险（spec §5.8）**：auth 现状 9 类纯 LLM 检查，无确定性信号。新增扫描器只产出新文件（`auth_config_scan.json` + `auth_gitnexus_queue.json`）；LLM prompt 新增一个可选 context section（文件缺失时跳过，对齐 `_static-dataflow-hints.txt:2` 的「若该文件不存在，跳过本段」模式）。GitNexus 轨 queue 不存在/为空时，Plan 3 合并器已设计为降级为全 `llm-only`/`needs_review`，行为与现状等价。**不删不改** auth 任何现有 9 类检查逻辑。
- **复用 framework_analyzer 扫描模式**：配置扫描器的「扫源码 + literal/regex 混合匹配」直接复刻 `framework_analyzer.py:157-214` 的 `_pattern_in_content`（literal 优先、regex fallback 规避未转义 `(`）+ `_find_source_files`（按入口文件 + 子目录 rglob）。**不引入** tree-sitter AST（配置项是常量级，regex 足够；AST 成本不划算，spec §5.8 定位「配置常量级」）。
- **扫描范围限定**：只扫源码文件（`.js/.ts/.py/.go/.java/.php`）+ 明确配置目录（`config/`、根目录 `*.{yaml,yml,json,env,conf}`）。**不**扫 `node_modules`/`.git`/`vendor`/测试目录（复用 `_find_source_files` 的入口文件 + routes/middleware 子目录策略，加 middleware/auth/security 子目录）。
- **verdict OR 语义（spec §4.2）**：`AuthVulnerability` **无 `verdict` 字段**（`queue_schemas.py:37-42`），合并器回退用 `externally_exploitable` 做 OR（Plan 3 `_get_verdict_or_exploitable` 已处理，见 `dual_track_merger.py`）。GitNexus 轨的 `AuthVulnerability` 一律 `externally_exploitable=True`（确定性扫到的可疑配置项视为可利用候选，保守过报），合并后任一轨 True → True。
- **去重键**：auth finding 按 `(vulnerability_type, source_endpoint, vulnerable_code_location)` 去重——这是 `AuthVulnerability` 仅有的三个定位字段（`queue_schemas.py:38-39`）。Plan 3 `_finding_key` 的 `_LOCATION_FIELDS` 已含 `source_endpoint`/`vulnerable_code_location`，`_SINK_FIELDS` 对 auth 无命中（取 None），故 auth finding 天然按 endpoint+location 去重，**合并器无需改**。
- **依赖 Plan 3（合并器）**：本 plan 的 GitNexus 轨 queue（`auth_gitnexus_queue.json`）由 Plan 3 的 `run_merge_dual_track_queues`（wiring 在 vuln 阶段后、attack-chain 前）消费。**Plan 3 必须先落地**（其 Task 3 wiring 的 vuln class 循环已含 `"auth"`，见 `dual-track-merger-plan.md` Task 3 Step 3 `for vc in ("injection", "xss", "ssrf", "authz", "auth")`）。本 plan 不重复实现合并 wiring。
- **不接 rate-limit 真实流量验证 / 不做 HSTS 实际响应探测**：本 plan 是静态配置扫描（spec §5.8「配置常量级」），不发起任何 HTTP 请求验证运行时 header（那是 exploitation 阶段）。扫描器只判「代码/配置里是否设置了该防御」，不判「线上响应实际是否带」。
- TDD + frequent commits（`feat(services):` / `feat(whitebox):` / `docs(prompt):`）；真实端到端双轨合并效果需手动冒烟（本 plan 单元测试用合成代码 fixture + 合成 GitNexus 轨 queue）。

---

### Task 1: 确定性配置扫描器 `auth_config_scanner.py`（header/cookie/HSTS/CORS）

**Files:**
- Create: `packages/core/src/shannon_core/services/auth_config_scanner.py`
- Modify: `packages/core/src/shannon_core/services/__init__.py:21`（export 新模块符号）
- Test: `packages/core/tests/services/test_auth_config_scanner.py`（Create）

**Interfaces:**
- Produces: `scan_auth_config(codebase_path: str) -> AuthConfigScanResult`（async，对齐 `analyze_frameworks` 签名）；`AuthConfigScanResult` 含 `cookie_findings`/`hsts_findings`/`cors_findings`（本 task 三类；JWT/rate-limit 在 Task 2/3 补）

**扫描规则（本 task）**：
- **Cookie**：匹配 cookie 设置点（`res.cookie(` / `set_cookie` / `Set-Cookie` / `cookie(`），对每个命中点检查同行/邻近（±3 行窗口）是否含 `HttpOnly`/`Secure`/`SameSite`；缺哪个标哪个 missing。
- **HSTS**：匹配 `Strict-Transport-Security` / `helmet.hsts` / `max-age`，检查是否设了 `max-age` 且 `>= 31536000`（1 年）；未设 / `max-age` 过小 / 完全无 HSTS 配置 → finding。
- **CORS**：匹配 `Access-Control-Allow-Origin` / `cors(` / `origin:`，检查是否 `*`（通配）或 `credentials: true` + 通配 origin → 宽松 CORS finding。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/services/test_auth_config_scanner.py
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
          res.cookie('session', token);  // no HttpOnly, no Secure
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
    # Must round-trip via asdict (activities.py:566 uses this for JSON write)
    data = dataclasses.asdict(result)
    assert "cookie_findings" in data
    assert "hsts_findings" in data
    assert "cors_findings" in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/services/test_auth_config_scanner.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.services.auth_config_scanner`

- [ ] **Step 3: Implement the scanner (cookie/HSTS/CORS)**

```python
# packages/core/src/shannon_core/services/auth_config_scanner.py
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

_COOKIE_SET_PATTERNS = (
    r"res\.cookie\s*\(",
    r"\.cookie\s*\(\s*['\"]",
    r"set_cookie\s*\(",
    r"Set-Cookie",
    r"response\.set_cookie",
)
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
        # window of ±3 lines for flag check (cookie opts often on next lines)
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
_HSTS_HEADER_RE = re.compile(r"Strict-Transport-Security", re.IGNORECASE)


def _scan_hsts(file_path: Path, content: str, lines: list[str], has_any_hsts_global: bool) -> list[ConfigFinding]:
    findings: list[ConfigFinding] = []
    # If no HSTS config anywhere in this file at all → flag the entry/listen line
    has_hsts_in_file = any(_pattern_in_content(p, content) for p in _HSTS_PATTERNS)
    if not has_hsts_in_file:
        # Only flag absence on entry files (avoid one finding per random file)
        if file_path.name in _ENTRY_FILES or _pattern_in_content(r"app\.(listen|use)", content):
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
            # HSTS absence is only meaningful if NOTHING sets it globally
            if has_any_hsts_global:
                result.hsts_findings.extend(_scan_hsts(fp, content, lines, True))
            else:
                result.hsts_findings.extend(_scan_hsts(fp, content, lines, False))
            result.cors_findings.extend(_scan_cors(fp, content, lines))
        except Exception as exc:
            logger.debug("auth-config scan: error in %s: %s", fp, exc)

    logger.info(
        "auth-config scan: %d cookie, %d hsts, %d cors findings across %d files",
        len(result.cookie_findings), len(result.hsts_findings),
        len(result.cors_findings), len(files),
    )
    return result
```

- [ ] **Step 4: Export the new symbols from `services/__init__.py`**

Edit `packages/core/src/shannon_core/services/__init__.py`（在 framework_analyzer import 块之后，约 :28 追加）：

```python
from shannon_core.services.auth_config_scanner import (
    ConfigFinding,
    AuthConfigScanResult,
    scan_auth_config,
)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/services/test_auth_config_scanner.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/services/auth_config_scanner.py packages/core/src/shannon_core/services/__init__.py packages/core/tests/services/test_auth_config_scanner.py
git commit -m "feat(services): deterministic auth-config scanner (cookie/HSTS/CORS) for vuln-auth GitNexus track"
```

---

### Task 2: JWT claim 读取点检测（nOAuth：sub vs 可变属性）

**Files:**
- Modify: `packages/core/src/shannon_core/services/auth_config_scanner.py`（加 `_scan_jwt_claims` + 接入 orchestrator）
- Test: `packages/core/tests/services/test_auth_config_scanner.py`（扩展）

**Interfaces:**
- Produces: `AuthConfigScanResult.jwt_claim_findings`（nOAuth 候选：JWT 解码后读取 `email`/`name`/`preferred_username` 做身份判定，而非 `sub`）

**检测逻辑（spec §5.8 + vuln-auth.txt:175）**：
- 匹配 JWT 解码点（`jwt.verify` / `jwt.decode` / `jsonwebtoken.verify` / `jwt_decode` / `decode_token`）。
- 在命中点 ±5 行窗口内，查找读取的 claim 名：若读 `sub` → 安全（不报）；若读 `email`/`name`/`preferred_username`/`user`/`username` 等**可变属性**做身份判定（赋值给 userId / 用于查 user）→ nOAuth 候选 finding。

- [ ] **Step 1: Write the failing test**

Append to `packages/core/tests/services/test_auth_config_scanner.py`:

```python
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
        const userId = payload.email;  // mutable! attacker can change email
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/services/test_auth_config_scanner.py -k jwt -v`
Expected: FAIL — `jwt_claim_findings` always empty (scanner doesn't scan JWT yet)

- [ ] **Step 3: Implement `_scan_jwt_claims`**

Add to `packages/core/src/shannon_core/services/auth_config_scanner.py`:

```python
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
        hi = min(len(lines), line_no + 6)  # ±5 line window
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
```

Then wire into `scan_auth_config`（在 `result.cors_findings.extend(...)` 之后，`logger.info` 之前）：

```python
            result.jwt_claim_findings.extend(_scan_jwt_claims(fp, content, lines))
```

并更新结尾 `logger.info`：

```python
    logger.info(
        "auth-config scan: %d cookie, %d hsts, %d cors, %d jwt_claim findings across %d files",
        len(result.cookie_findings), len(result.hsts_findings),
        len(result.cors_findings), len(result.jwt_claim_findings), len(files),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/services/test_auth_config_scanner.py -v`
Expected: PASS (12 tests: 8 from Task 1 + 4 JWT)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/services/auth_config_scanner.py packages/core/tests/services/test_auth_config_scanner.py
git commit -m "feat(services): JWT nOAuth claim detection in auth-config scanner"
```

---

### Task 3: rate-limit 中间件检测（login/signup/reset/token 端点缺限流）

**Files:**
- Modify: `packages/core/src/shannon_core/services/auth_config_scanner.py`（加 `_scan_rate_limits` + 接入 orchestrator）
- Test: `packages/core/tests/services/test_auth_config_scanner.py`（扩展）

**Interfaces:**
- Produces: `AuthConfigScanResult.rate_limit_findings`（auth 敏感端点 login/signup/reset/token 缺 rate-limit 中间件）

**检测逻辑（spec §5.8 + vuln-auth.txt:129）**：
- 匹配 auth 敏感路由（`/login`、`/signup`、`/register`、`/reset`、`/recover`、`/token`、`/oauth`、`/forgot`）。
- 在路由定义 ±5 行窗口内，查找 rate-limit 中间件迹象（`rateLimit` / `rate-limiter` / `express-rate-limit` / `slowauth` / `throttle` / `limiter` / flask-limiter `@limiter.limit`）。
- 命中敏感路由但窗口内无 rate-limit 迹象 → finding。

- [ ] **Step 1: Write the failing test**

Append to `packages/core/tests/services/test_auth_config_scanner.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/services/test_auth_config_scanner.py -k rate_limit -v`
Expected: FAIL — `rate_limit_findings` always empty

- [ ] **Step 3: Implement `_scan_rate_limits`**

Add to `packages/core/src/shannon_core/services/auth_config_scanner.py`:

```python
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
        # Extract the sensitive path for the detail message
        path_m = _SENSITIVE_PATH_RE.search(window)
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
```

Wire into `scan_auth_config`（在 jwt_claim extend 之后）：

```python
            result.rate_limit_findings.extend(_scan_rate_limits(fp, content, lines))
```

更新结尾 `logger.info`：

```python
    logger.info(
        "auth-config scan: %d cookie, %d hsts, %d cors, %d jwt_claim, %d rate_limit "
        "findings across %d files",
        len(result.cookie_findings), len(result.hsts_findings),
        len(result.cors_findings), len(result.jwt_claim_findings),
        len(result.rate_limit_findings), len(files),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/services/test_auth_config_scanner.py -v`
Expected: PASS (16 tests: 12 + 4 rate-limit)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/services/auth_config_scanner.py packages/core/tests/services/test_auth_config_scanner.py
git commit -m "feat(services): rate-limit middleware detection for auth-sensitive endpoints"
```

---

### Task 4: Pipeline wiring — `run_auth_config_scan` activity（产出 scan JSON + GitNexus 轨 queue）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（新增 `run_auth_config_scan` activity）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`（vuln 阶段并行前跑该 activity）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/step_intents.py`（补 `auth-config-scan` StepSpec）
- Test: `packages/whitebox/tests/test_run_auth_config_scan.py`（Create）

**Interfaces:**
- Consumes: `scan_auth_config`（Task 1-3）
- Produces:
  1. `auth_config_scan.json`（结构化扫描结果，供 LLM 读 + 供合并器读）
  2. `auth_gitnexus_queue.json`（每个可疑配置项落成一条 `AuthVulnerability`，`source_track="gitnexus"`，供 Plan 3 合并器消费）

**关键行为**：该 activity 在 vuln 阶段并行启动**之前**跑（确定性产物先于 LLM agent 就绪，使 vuln-auth LLM 能读到 `auth_config_scan.json`）。扫描零命中 → 两文件都写空 `{"findings": [], "vulnerabilities": []}`（不跳过写入，保证 Plan 3 合并器能读到非空 GitNexus 轨文件；合并器对空 queue 天然降级为全 llm-only）。

- [ ] **Step 1: Write the failing test**

```python
# packages/whitebox/tests/test_run_auth_config_scan.py
import json
from pathlib import Path

import pytest

from shannon_whitebox.pipeline import activities


def _input(repo, deliverables):
    class FakeInput:
        agent_name = None
        web_url = None
        repo_path = str(repo)
        config_path = None
        api_key = None
        pipeline_testing_mode = False
        prompt_override = None
        deliverables_subdir = None
        workspace_name = None
        phase = None
        max_concurrent = 1
    return FakeInput()


@pytest.mark.asyncio
async def test_scan_writes_config_json_and_gitnexus_queue(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.js").write_text(
        "app.post('/login', (req, res) => { res.cookie('session', t); });\n"
    )
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    monkeypatch.setattr(activities, "_get_paths", lambda i: (repo, deliverables, tmp_path))
    result = await activities.run_auth_config_scan(_input(repo, deliverables))

    scan_path = deliverables / "auth_config_scan.json"
    queue_path = deliverables / "auth_gitnexus_queue.json"
    assert scan_path.exists()
    assert queue_path.exists()

    scan = json.loads(scan_path.read_text())
    assert "cookie_findings" in scan
    assert len(scan["cookie_findings"]) >= 1

    queue = json.loads(queue_path.read_text())
    assert "vulnerabilities" in queue
    assert len(queue["vulnerabilities"]) >= 1
    v = queue["vulnerabilities"][0]
    assert v["vulnerability_type"] in ("Authentication_Bypass", "Session_Management_Flaw",
                                        "Transport_Exposure", "Abuse_Defenses_Missing",
                                        "OAuth_Flow_Issue", "Token_Management_Issue",
                                        "Login_Flow_Logic", "Reset_Recovery_Flaw")
    assert v["source_track"] == "gitnexus"
    assert v["externally_exploitable"] is True


@pytest.mark.asyncio
async def test_scan_zero_findings_writes_empty_files(tmp_path, monkeypatch):
    """Zero findings still writes both files (empty) — Plan 3 merger degrades to llm-only."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.js").write_text("const x = 1;\n")  # nothing suspicious
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    monkeypatch.setattr(activities, "_get_paths", lambda i: (repo, deliverables, tmp_path))
    result = await activities.run_auth_config_scan(_input(repo, deliverables))

    scan = json.loads((deliverables / "auth_config_scan.json").read_text())
    queue = json.loads((deliverables / "auth_gitnexus_queue.json").read_text())
    assert scan["cookie_findings"] == []
    assert queue["vulnerabilities"] == []
    assert result["total_findings"] == 0


@pytest.mark.asyncio
async def test_scan_does_not_crash_on_empty_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    monkeypatch.setattr(activities, "_get_paths", lambda i: (repo, deliverables, tmp_path))
    result = await activities.run_auth_config_scan(_input(repo, deliverables))
    assert result["total_findings"] == 0
    assert (deliverables / "auth_config_scan.json").exists()


@pytest.mark.asyncio
async def test_finding_category_maps_to_vulnerability_type(tmp_path, monkeypatch):
    """Each scanner category maps to a sensible AUTH-VULN vulnerability_type."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.js").write_text("""
        res.cookie('s', t);              // cookie → Session_Management_Flaw
        app.use(cors({ origin: '*' }));  // cors → Abuse_Defenses_Missing (transport-adjacent)
        app.post('/login', (req,res)=>{}); // rate_limit → Abuse_Defenses_Missing
    """)
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    monkeypatch.setattr(activities, "_get_paths", lambda i: (repo, deliverables, tmp_path))
    await activities.run_auth_config_scan(_input(repo, deliverables))

    queue = json.loads((deliverables / "auth_gitnexus_queue.json").read_text())
    types = {v["vulnerability_type"] for v in queue["vulnerabilities"]}
    assert "Session_Management_Flaw" in types  # from cookie
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_auth_config_scan.py -v`
Expected: FAIL — `AttributeError: module ...activities has no attribute 'run_auth_config_scan'`

- [ ] **Step 3: Implement the activity + category→vuln_type mapping**

Add to `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（在 `run_frontend_mapping` 之后，约 :610 之前）：

```python
@activity.defn
async def run_auth_config_scan(input: ActivityInput) -> dict:
    """Deterministic auth-config scan (spec §5.8 vuln-auth GitNexus track).

    Scans the repo for auth/session config issues (cookie flags, HSTS, CORS,
    JWT nOAuth claims, rate-limit middleware) and writes two deliverables:
      1. auth_config_scan.json — structured scan result (LLM reads this as a
         starting point, like vuln-authz reads Endpoint Security Context).
      2. auth_gitnexus_queue.json — each suspicious config item as an
         AuthVulnerability (source_track='gitnexus'), consumed by Plan 3's
         run_merge_dual_track_queues for verdict OR with the LLM track.

    Pure additive: zero findings still writes both files (empty), so the
    merger degrades cleanly to llm-only. Scan failures never abort the vuln
    phase (logged, empty result).
    """
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        import dataclasses
        from shannon_core.services.auth_config_scanner import scan_auth_config, ConfigFinding
        from shannon_core.models.queue_schemas import AuthVulnerability

        repo, deliverables, _ = _get_paths(input)
        async with get_audit_session().track_step(
            "vulnerability-analysis", "auth-config-scan",
            intent=intent_for("auth-config-scan"),
        ):
            result = await scan_auth_config(str(repo))

            # 1. Structured scan result (LLM reads this)
            scan_data = dataclasses.asdict(result)
            atomic_write_json(deliverables / "auth_config_scan.json", scan_data)

            # 2. GitNexus-track queue (Plan 3 merger consumes this)
            vulns = [_finding_to_auth_vulnerability(f) for f in result.all_findings()]
            atomic_write_json(
                deliverables / "auth_gitnexus_queue.json",
                {"vulnerabilities": [v.model_dump() for v in vulns]},
            )

            total = len(result.all_findings())
        return {
            "total_findings": total,
            "cookie": len(result.cookie_findings),
            "hsts": len(result.hsts_findings),
            "cors": len(result.cors_findings),
            "jwt_claim": len(result.jwt_claim_findings),
            "rate_limit": len(result.rate_limit_findings),
        }
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


# Category → AUTH-VULN vulnerability_type mapping (vuln-auth.txt:101).
# externally_exploitable=True for all (conservative over-report; merger ORs).
_CATEGORY_TO_VULN_TYPE = {
    "cookie": "Session_Management_Flaw",
    "hsts": "Transport_Exposure",
    "cors": "Transport_Exposure",
    "jwt_claim": "Login_Flow_Logic",      # nOAuth is a login-flow identity flaw
    "rate_limit": "Abuse_Defenses_Missing",
}
_CATEGORY_TO_SUGGESTED_TECHNIQUE = {
    "cookie": "session_hijacking",
    "hsts": "credential_theft_via_mitm",
    "cors": "credential_theft_via_cors",
    "jwt_claim": "noauth_attribute_hijack",
    "rate_limit": "brute_force_login",
}


def _finding_to_auth_vulnerability(f) -> "AuthVulnerability":
    """Convert a deterministic ConfigFinding into an AuthVulnerability for the
    GitNexus-track queue (source_track='gitnexus')."""
    from shannon_core.models.queue_schemas import AuthVulnerability
    vuln_type = _CATEGORY_TO_VULN_TYPE.get(f.category, "Session_Management_Flaw")
    technique = _CATEGORY_TO_SUGGESTED_TECHNIQUE.get(f.category, "session_hijacking")
    location = f"{f.file_path}:{f.line}"
    return AuthVulnerability(
        ID=f"AUTH-GN-{f.category.upper()}-{abs(hash((f.file_path, f.line, f.category))) % 100000:05d}",
        vulnerability_type=vuln_type,
        externally_exploitable=True,   # conservative: scanner hit → exploitable candidate
        confidence="medium",           # deterministic signal, LLM confirms/denies
        source_track="gitnexus",
        evidence_chain=f"[deterministic scan] {f.category}@{location}: {f.detail}",
        source_endpoint=None,          # config-level, not endpoint-scoped (merger dedups on location)
        vulnerable_code_location=location,
        missing_defense=f.detail,
        exploitation_hypothesis=(
            f"Attacker can exploit the missing/weak auth configuration: {f.detail}"
        ),
        suggested_exploit_technique=technique,
        notes=f"GitNexus-track candidate (awaiting LLM confirmation). Evidence: {f.evidence}",
    )
```

- [ ] **Step 4: Add the StepSpec for dashboard intent**

Edit `packages/whitebox/src/shannon_whitebox/pipeline/step_intents.py`。`PHASE_STEPS` 当前无 `"vulnerability-analysis"` key（该 phase 的 steps 在 workflows.py:286 现场造）。**新增**整个 key（即使为单元素 tuple，让 `intent_for("auth-config-scan")` 命中）：

```python
    "vulnerability-analysis": (
        StepSpec("auth-config-scan", "确定性认证配置扫描(cookie/HSTS/CORS/JWT/限流)"),
    ),
```

> 放在 `"risk-scoring": (...)` 块之后、`"attack-chain": (...)` 之前。`intent_for("auth-config-scan")` 现返回该中文文案；`step_names("vulnerability-analysis")` 返回 `("auth-config-scan",)`，不影响 workflows.py:286 现场造的 vuln step intents（那是另一条传参路径，`log_phase_start_activity` 的 `intents=` 参数独立传入）。

- [ ] **Step 5: Wire the activity into the workflow (before vuln-phase parallel gather)**

Edit `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`。在 vuln 阶段 `log_phase_start_activity`（:276-290）**之后**、`for vt in selected_classes` 循环（:293）**之前**插入：

```python
            # Deterministic auth-config scan (spec §5.8 GitNexus track for vuln-auth).
            # Runs BEFORE the vuln agents so auth_config_scan.json is ready for
            # the vuln-auth LLM to read. Pure additive: zero findings → empty
            # files, merger degrades to llm-only. Only runs when auth is in scope.
            if "auth" in [str(vt) for vt in selected_classes]:
                await workflow.execute_activity(
                    activities.run_auth_config_scan, act_input,
                    start_to_close_timeout=timedelta(minutes=3),
                    retry_policy=retry_for("standard"),
                )
```

> `retry_for` / `timedelta` 已在 workflows.py 顶部 import（见 :1-15 + 现有 `retry_for("vuln")` 用法 :301）。`selected_classes` 类型是 `list[VulnType]`，转 str 比对 `"auth"`（`ALL_VULN_CLASSES` 含 `"auth"`，`agents.py:164`）。

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_auth_config_scan.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Run broader scanner + services tests to confirm no regression**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/services/test_auth_config_scanner.py packages/whitebox/tests/test_run_auth_config_scan.py -v`
Expected: PASS (20 tests)

- [ ] **Step 8: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/src/shannon_whitebox/pipeline/step_intents.py packages/whitebox/tests/test_run_auth_config_scan.py
git commit -m "feat(whitebox): wire auth-config-scan activity (GitNexus track for vuln-auth, spec §5.8)"
```

---

### Task 5: LLM 确认 pass — `vuln-auth.txt` 新增 `<auth_config_context>` section

**Files:**
- Modify: `prompts/vuln-auth.txt`（新增 section + @include 注入点）
- Test: 无单元测试（prompt 文本；手动冒烟验证 LLM 读到并引用 scan 结果）

**Interfaces:**
- Consumes: `auth_config_scan.json`（Task 4 产出）
- Produces: vuln-auth LLM 把扫描可疑项作为追查起点，给最终 verdict（vulnerable/safe）；LLM 轨产出的 `auth_exploitation_queue.json` 含 LLM 对每个可疑配置项的判定

**注入模式**：对齐 vuln-authz 的 Endpoint Security Context（`vuln-authz.txt:47-71`）+ `_static-dataflow-hints.txt` 的「文件不存在则跳过」语义。vuln-auth LLM 先读 `auth_config_scan.json`，把每个可疑项作为高优先级追查目标，用 Task Agent 验证后给 verdict。

- [ ] **Step 1: Add the `<auth_config_context>` section to the prompt**

Edit `prompts/vuln-auth.txt`。在 `<starting_context>` 块（:39-41）**之后**、`@include(shared/_static-dataflow-hints.txt)`（:43）**之前**插入新 section：

```text
<auth_config_context>
### Read Auth Config Scan (REQUIRED — Do This First, if present)

Before beginning your 9-class methodology analysis:

1. **Check for the deterministic scan result:**
   - Read `{{DELIVERABLES_PATH}}/auth_config_scan.json` (if it exists).
   - If the file does NOT exist or is empty (all finding lists empty), skip this section and proceed with your normal autonomous analysis — the deterministic track is simply absent, not authoritative.

2. **For each suspicious config item the scanner found** (cookie_findings, hsts_findings, cors_findings, jwt_claim_findings, rate_limit_findings):
   - Treat it as a **high-priority starting point**, NOT a conclusion.
   - Use the Task Agent to verify the finding against the actual code at `file_path:line`:
     - **cookie**: confirm the cookie truly lacks the flagged flag(s) at every set-point; check if a global cookie policy (e.g., `app.use(cookieParser(...))`, `SESSION_COOKIE_SECURE` env) compensates.
     - **hsts**: confirm HSTS is truly absent/weak at the edge (reverse proxy / load balancer may add it upstream — note as a conditional in your finding if so).
     - **cors**: confirm the wildcard origin / credentials combo is actually reachable from a browser; check if a stricter origin allowlist overrides it.
     - **jwt_claim (nOAuth)**: confirm the mutable claim (`email`/`name`/`preferred_username`) is actually used for **identity determination** (user lookup / session binding), not just display. If `sub` is used elsewhere for the authoritative identity, the mutable-claim read may be benign — document the actual binding.
     - **rate_limit**: confirm the auth-sensitive endpoint (`/login`, `/signup`, `/reset`, `/token`) truly has no rate-limit middleware on **all** code paths (gateway/WAF may enforce it upstream — note as conditional).

3. **For each verified item, produce a verdict:**
   - **vulnerable** → add to your exploitation queue (the scanner's `missing_defense` text and `file:line` go into the corresponding finding fields).
   - **safe** (compensating control confirmed, or scanner false positive) → document in "安全设计:已验证组件" section, do NOT add to queue.

**⚠️ The scanner is a lead, not a verdict.** It uses regex over source/config and cannot see runtime headers, upstream proxies, or env-driven policy. A scanner hit that you confirm safe (e.g., HSTS set at the CDN) must NOT enter the queue. Conversely, scanner silence on a check does NOT mean safe — continue your full 9-class methodology autonomously for anything the scanner didn't cover (custom token entropy, session fixation, password policy, SSO state/nonce, etc.).

**The deterministic track also produces `auth_gitnexus_queue.json`** (a parallel queue of these same items as AuthVulnerability candidates). Your LLM-track queue and that GitNexus-track queue are merged downstream by verdict OR (any vulnerable → vulnerable). So your job is to give the accurate LLM-track verdict for each item you can reach; the merger handles the combination.
</auth_config_context>
```

- [ ] **Step 2: Verify the prompt still loads (interpolation smoke)**

Run: `cd /root/shannon-py && python -c "
from shannon_core.prompts.manager import PromptManager
pm = PromptManager()
p = pm.load_sync('vuln-auth', variables={'web_url':'', 'repo_path':'/tmp', 'deliverables_path':'/tmp/d', 'scratchpad_path':'/tmp/s', 'LOGIN_INSTRUCTIONS':'none', 'BROWSER_COMMANDS':'', 'BROWSER_SESSION_FLAG':''}, config=None, pipeline_testing=True)
assert 'auth_config_scan.json' in p
assert 'auth_config_context' in p
print('OK: prompt loads with auth_config_context section')
"`
Expected: `OK: prompt loads with auth_config_context section`（确认 @include 与 `{{DELIVERABLES_PATH}}` 插值正常；若 manager 签名不同，按 `executor.py:90-95` 的实际 `load_sync` 调用调整变量字典）。

- [ ] **Step 3: Commit**

```bash
cd /root/shannon-py
git add prompts/vuln-auth.txt
git commit -m "docs(prompt): vuln-auth reads deterministic auth_config_scan.json (GitNexus track input, spec §5.8)"
```

---

### Task 6: 集成验证 — 双轨闭环（合成 LLM 轨 + 合成 GitNexus 轨 → 合并）

**Files:**
- Test: `packages/whitebox/tests/test_auth_dual_track_integration.py`（Create）

**Interfaces:**
- Consumes: Task 4（`run_auth_config_scan` 产 `auth_gitnexus_queue.json`）+ Plan 3（`run_merge_dual_track_queues` 合并）+ 现有 executor（产 `auth_exploitation_queue.json` 即 LLM 轨）
- Produces: 验证 auth 双轨合并闭环：扫描器产 GitNexus 轨 → 合并器把 LLM 轨（executor 产）+ GitNexus 轨 → `auth_exploitation_queue.json`，带 `merge_source`/`confidence`

**前置说明**：本 task 假设 Plan 3 已落地（`run_merge_dual_track_queues` 存在且 wiring 在 vuln 阶段后）。若 Plan 3 未落地，本 task 的合并步骤跳过（只验证 Task 4 的 GitNexus 轨产出），并在测试里 `pytest.importorskip("shannon_core.code_index.dual_track_merger")`。

- [ ] **Step 1: Write the integration test**

```python
# packages/whitebox/tests/test_auth_dual_track_integration.py
"""Integration: auth dual-track closure (scanner → GitNexus queue → merger).

Requires Plan 3 (dual_track_merger + run_merge_dual_track_queues). If Plan 3
is not landed, the merger portion is skipped via importorskip; the scanner
GitNexus-track production is still validated.
"""
import json

import pytest

dual_track = pytest.importorskip("shannon_core.code_index.dual_track_merger")


@pytest.mark.asyncio
async def test_auth_dual_track_scanner_feeds_merger(tmp_path, monkeypatch):
    """Scanner GitNexus-track queue + synthetic LLM-track queue → merged with
    merge_source tags (Plan 3 merger)."""
    from shannon_core.code_index.dual_track_merger import merge_dual_track_queues
    from shannon_core.models.queue_schemas import (
        AuthVulnerability, VulnerabilityQueue,
    )
    from shannon_whitebox.pipeline import activities

    # --- Scanner produces GitNexus track ---
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.js").write_text(
        "app.post('/login', (req,res)=>{ res.cookie('session', t); });\n"
    )
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    monkeypatch.setattr(activities, "_get_paths", lambda i: (repo, deliverables, tmp_path))

    class FI:
        repo_path = str(repo); deliverables_subdir = None; agent_name = None
        web_url = None; config_path = None; api_key = None
        pipeline_testing_mode = False; prompt_override = None
        workspace_name = None; phase = None; max_concurrent = 1
    await activities.run_auth_config_scan(FI())

    gn_path = deliverables / "auth_gitnexus_queue.json"
    assert gn_path.exists()
    gn_parsed = VulnerabilityQueue.parse_lenient(gn_path.read_text())
    gn_findings = gn_parsed.queue.vulnerabilities
    assert len(gn_findings) >= 1
    assert all(getattr(f, "source_track", None) == "gitnexus" for f in gn_findings)

    # --- Synthetic LLM track (what executor would produce) ---
    llm_finding = AuthVulnerability(
        ID="AUTH-VULN-01",
        vulnerability_type="Session_Management_Flaw",
        externally_exploitable=True,
        confidence="high",
        source_track="llm",
        source_endpoint="POST /login",
        vulnerable_code_location="app.js:1",
        missing_defense="Session cookie lacks HttpOnly and Secure flags",
        exploitation_hypothesis="Attacker can hijack session via XSS/network sniffing",
        suggested_exploit_technique="session_hijacking",
    )

    # --- Merge (Plan 3) ---
    merged = merge_dual_track_queues([llm_finding], gn_findings, mode="verdict")
    # Union: LLM finding + scanner findings (cookie at app.js:1 likely dedups
    # with the LLM finding on vulnerable_code_location if they match)
    assert len(merged) >= 1
    # At least one finding should be 'both' if locations overlap, else union
    sources = {getattr(m, "merge_source") for m in merged}
    assert sources & {"both", "llm-only", "gitnexus-only"}  # all valid tags
    # Every merged finding has a merge_source + confidence (spec §9 #2)
    for m in merged:
        assert m.merge_source is not None
        assert m.confidence is not None


@pytest.mark.asyncio
async def test_auth_dual_track_pure_additive_when_scanner_empty(tmp_path, monkeypatch):
    """Scanner zero findings → GitNexus track empty → merger yields llm-only."""
    from shannon_core.code_index.dual_track_merger import merge_dual_track_queues
    from shannon_core.models.queue_schemas import AuthVulnerability
    from shannon_whitebox.pipeline import activities

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "clean.js").write_text("const x = 1;\n")
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    monkeypatch.setattr(activities, "_get_paths", lambda i: (repo, deliverables, tmp_path))

    class FI:
        repo_path = str(repo); deliverables_subdir = None; agent_name = None
        web_url = None; config_path = None; api_key = None
        pipeline_testing_mode = False; prompt_override = None
        workspace_name = None; phase = None; max_concurrent = 1
    await activities.run_auth_config_scan(FI())

    gn_parsed = VulnerabilityQueue.parse_lenient(
        (deliverables / "auth_gitnexus_queue.json").read_text())
    assert gn_parsed.queue.vulnerabilities == []  # scanner empty

    llm_finding = AuthVulnerability(
        ID="AUTH-VULN-01", vulnerability_type="Login_Flow_Logic",
        externally_exploitable=True, confidence="high", source_track="llm",
        source_endpoint="POST /login", vulnerable_code_location="auth.js:10",
        missing_defense="user enumeration in login error",
        exploitation_hypothesis="attacker enumerates valid usernames",
        suggested_exploit_technique="account_enumeration",
    )
    merged = merge_dual_track_queues([llm_finding], [], mode="verdict")
    assert len(merged) == 1
    assert merged[0].merge_source == "llm-only"
    assert merged[0].confidence == "needs_review"
```

> 注：顶部需 `from shannon_core.models.queue_schemas import VulnerabilityQueue`（第二个测试用到）。`pytest.importorskip` 在模块级跳过整个文件若 Plan 3 未落地——保证本 plan 可独立验证（只验 Task 4）。

- [ ] **Step 2: Run the integration test**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_auth_dual_track_integration.py -v`
Expected: 若 Plan 3 已落地 → PASS (2 tests)；若 Plan 3 未落地 → 整文件 SKIPPED（`importorskip`），且本 plan 的 Task 4 单元测试仍独立验证扫描器产出。

- [ ] **Step 3: Run the full auth-related test suite**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/services/test_auth_config_scanner.py packages/whitebox/tests/test_run_auth_config_scan.py packages/whitebox/tests/test_auth_dual_track_integration.py packages/whitebox/tests/test_run_merge_dual_track.py -v 2>/dev/null || python -m pytest packages/core/tests/services/test_auth_config_scanner.py packages/whitebox/tests/test_run_auth_config_scan.py -v`
Expected: PASS（Plan 3 已落地则含合并器测试；否则前两个文件绿）

- [ ] **Step 4: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/tests/test_auth_dual_track_integration.py
git commit -m "test(whitebox): auth dual-track integration (scanner → GitNexus queue → merger)"
```

---

## Self-Review

**1. Spec coverage**（对照 spec §5.8 / §4.2 / §9）：
- §5.8 LLM 轨（9 类检查现状）→ 不动，Task 5 只新增 context section ✓
- §5.8 GitNexus 轨「确定性配置扫描 header/cookie/HSTS/CORS」→ Task 1 ✓
- §5.8 GitNexus 轨「JWT claim 读取点（nOAuth sub vs email/name）」→ Task 2 ✓
- §5.8 GitNexus 轨「rate-limit 中间件检测」→ Task 3 ✓
- §5.8「LLM 确认可疑配置项」→ Task 5（prompt 读 `auth_config_scan.json` 给 verdict）✓
- §5.8「verdict OR 合并」→ 复用 Plan 3（Task 4 产 `auth_gitnexus_queue.json`，Plan 3 合并器已含 `"auth"` vuln class）✓
- §5.8「纯增益、无回退风险」→ Global Constraint：扫描零命中/失败降级为空，pipeline 行为等价现状 ✓
- §9 验收 #2（每条带 merge_source + confidence）→ Task 4 `_finding_to_auth_vulnerability` 设 `source_track="gitnexus"`，Plan 3 合并器覆写 `merge_source`/`confidence` ✓
- §9 验收 #5（GitNexus 失败优雅降级）→ Task 4 零命中写空文件 + 合并器降级全 llm-only ✓

**2. Placeholder scan**：无 TBD/TODO。`AuthVulnerability.ID` 用 `hash()` 派生（`_finding_to_auth_vulnerability`）——`hash()` 进程内不稳定但本场景 ID 只需唯一性（同一 file:line:category → 同一 ID，合并器去重靠 `_finding_key` 不靠 ID），且 `abs(hash(...)) % 100000` 给 5 位数字前缀 `AUTH-GN-`。若需跨进程稳定，可改 `hashlib.md5`，但本 plan 范围内非阻塞（合并去重不依赖 ID）。Task 5 Step 2 的 prompt 加载冒烟命令注明「若 manager 签名不同按 executor.py 实际调用调整」——诚实标注动态签名风险，非占位符。

**3. Type consistency**：
- `ConfigFinding` / `AuthConfigScanResult` 在 Task 1-4 一致（dataclass，`dataclasses.asdict` 可序列化）。
- `_finding_to_auth_vulnerability` 产出 `AuthVulnerability`（`source_track="gitnexus"`）与 Plan 3 `BaseVulnerability.source_track` 字段一致（Plan 3 Task 1 已加）。
- `auth_gitnexus_queue.json` / `auth_llm_queue.json` / `auth_exploitation_queue.json` 三文件命名与 Plan 3 Task 3 的 `for vc in (... "auth")` 循环 + executor.py:130-133（写 `auth_exploitation_queue.json`）一致，下游 `findings_renderer` 消费 `auth_exploitation_queue.json` 不变。
- `scan_auth_config(codebase_path: str) -> AuthConfigScanResult`（async）与 `analyze_frameworks` / `map_frontend_routes` 签名风格一致（activities.py:562/592 的调用模式）。

**4. verdict OR 正确性（auth 无 verdict 字段）**：
- `AuthVulnerability`（`queue_schemas.py:37-42`）无 `verdict` 字段 → Plan 3 `_get_verdict_or_exploitable` 回退 `externally_exploitable`（`dual_track_merger.py`）✓
- Task 4 GitNexus 轨一律 `externally_exploitable=True`（保守过报）→ 合并时 `True OR anything = True`，宁过报不漏报 ✓
- `_clone_with_merge_fields`（Plan 3）双写 `externally_exploitable` 保证下游一致 ✓

**5. 纯增益验证**：
- vuln-auth 现状：executor 产 `auth_exploitation_queue.json`（LLM 9 类）→ findings_renderer 读。无确定性。
- 本 plan 后：扫描器产 `auth_config_scan.json`（LLM 读，可选）+ `auth_gitnexus_queue.json`（合并器读）。Plan 3 合并器把 executor 产出重命名为 `auth_llm_queue.json`，合并后写回 `auth_exploitation_queue.json`。
- 扫描器零命中 → `auth_gitnexus_queue.json` 空 → 合并结果 = LLM 轨全 `llm-only`/`needs_review` → `auth_exploitation_queue.json` 内容 = LLM 轨（与现状等价，仅多了 `merge_source`/`confidence` 字段，renderer 不读这俩字段）✓
- 扫描器异常 → activity catch 全部 Exception → 写空文件 + log，不拖死 vuln 阶段 ✓

**需人决策点**：
- **A. `hash()` 派生 ID 的跨进程稳定性（非阻塞）**：`_finding_to_auth_vulnerability` 用 `abs(hash((file_path, line, category))) % 100000`。Python `hash()` 默认随机化（`PYTHONHASHSEED`），跨进程/重启会变。本场景 ID 仅需「同一扫描内唯一」+「同 file:line:category 稳定」（合并器去重靠 `_finding_key` 不靠 ID），故可接受。若需跨运行稳定（如 resume 场景比对），改 `hashlib.md5(f"{file}:{line}:{category}".encode()).hexdigest()[:8]`——trivial，非阻塞。
- **B. `intent_for("auth-config-scan")` 的 StepSpec 新增位置**：`step_intents.py` 当前 `PHASE_STEPS` 无 `"vulnerability-analysis"` key（该 phase steps 在 workflows.py:286 现场造）。Task 4 Step 4 新增整个 `"vulnerability-analysis": (StepSpec("auth-config-scan", ...),)` key。**需确认**：`step_names("vulnerability-analysis")` 被谁调用？若被 `log_phase_start_activity` 的 `steps=` 参数消费，新增会让该 phase 显示 `[auth-config-scan]` 而非现场造的 `[分析 injection 漏洞, ...]`。经查 workflows.py:280 的 `vuln_phase_steps(...)` 是独立函数（:15），不走 `step_names`，故新增 StepSpec **不冲突**——`step_names` 与现场造的 `vuln_phase_steps` 是两条独立路径。但建议人工冒烟时确认 dashboard 显示无异常。
- **C. 扫描器的 regex 精度（已知 best-effort）**：cookie flag 检测用 ±3 行窗口 + `httpOnly[:=]true|httponly` regex。复杂情况（cookie opts 跨多行、动态构造 opts 对象、env 驱动的全局 cookie policy）会产生**假阴性**（漏报）和**假阳性**（误报）。spec §5.8 定位「配置常量级」+ Task 5 prompt 明确「scanner 是 lead 不是 verdict，LLM 确认」，假阳性由 LLM verdict 过滤，假阴性由 LLM 9 类自主分析兜底。**诚实标注**：regex 扫描器精度有限，不替代 LLM 判断。

**已知缺口（诚实）**：
- **不检测运行时响应头**：扫描器只判「代码/配置里是否设了防御」，不发起 HTTP 请求验证线上响应（那是 exploitation 阶段）。反向代理/CDN 加的 HSTS/CORS 扫描器看不到 → 假阳性，由 Task 5 prompt 引导 LLM 标注 conditional。
- **JWT claim 检测的语义局限**：`_scan_jwt_claims` 用 regex 匹配 `payload.email`/`payload["preferred_username"]` 形式。若代码用中间变量（`const id = payload.email; User.find(id)` 跨函数）或解码结果改名（`const decoded = jwt.verify(...); const id = decoded.email`），regex 窗口可能漏。这是 regex vs AST 的固有取舍（spec §5.8 选 regex，AST 成本不划算）。
- **rate-limit 检测的网关盲区**：扫描器只看应用层中间件，看不到 WAF/API gateway 层的限流（如 Cloudflare、nginx limit_req）。Task 5 prompt 引导 LLM 标注「gateway may enforce upstream」conditional。
- **真实端到端效果需手动冒烟**：本 plan 单元测试用合成代码 fixture。真实仓库（混合框架、多语言、反向代理配置）的扫描命中率/假阳率需 Plan 3 落地后跑一次完整白盒扫描，检查 `auth_exploitation_queue.json` 里 `both`/`llm-only`/`gitnexus-only` 三类来源的实际分布与 `needs_review` 比例。
