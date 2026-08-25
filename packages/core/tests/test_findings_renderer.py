import json

import pytest

from supernova_core.models.config import ReportConfig
from supernova_core.models.queue_schemas import (
    InjectionVulnerability,
    XssVulnerability,
    AuthVulnerability,
    SsrfVulnerability,
    AuthzVulnerability,
    VulnerabilityQueue,
)
from supernova_core.services.findings_renderer import (
    render_vuln_card,
    filter_vulnerabilities,
    FindingsRenderer,
)


@pytest.fixture(autouse=True)
def _en_lang_default(monkeypatch):
    """现有断言基于英文渲染（i18n 前的行为），默认 en。
    zh 行为由显式 setenv("zh") 的测试覆盖。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")


def test_render_injection_card_full():
    vuln = InjectionVulnerability(
        ID="INJECTION-VULN-001",
        vulnerability_type="SQL Injection",
        externally_exploitable=True,
        confidence="high",
        source="user input",
        path="/api/users",
        sink_call="sqlite3.execute",
        concat_occurrences="query + user_input",
        sanitization_observed="None",
        verdict="Exploitable",
        witness_payload="' OR 1=1 --",
        notes="Critical finding",
    )
    result = render_vuln_card(vuln, "injection")
    assert "### INJECTION-VULN-001" in result
    # 判定字段全量降级收纳进「技术细节」区（spec §5，有意变更）
    assert "**Vulnerable Location:** user input → /api/users" in result
    assert "**Sink Call:** sqlite3.execute" in result
    assert "**Concat Occurrences:** query + user_input" in result
    assert "**Sanitization Observed:** None" in result
    assert "**Verdict:** Exploitable" in result
    assert "**Witness Payload:** ' OR 1=1 --" in result
    # notes 现作为「危害」叙述（impact 字段缺省时的 fallback）
    assert "**Impact**" in result
    assert "Critical finding" in result


def test_render_injection_card_minimal():
    vuln = InjectionVulnerability(
        ID="INJECTION-VULN-002",
        vulnerability_type="SQL Injection",
        externally_exploitable=True,
        confidence="medium",
    )
    result = render_vuln_card(vuln, "injection")
    assert "### INJECTION-VULN-002" in result
    assert "Sink Call" not in result
    assert "Notes" not in result


def test_render_injection_card_llm_output_fields():
    """LLM 实际输出的 injection vuln(携带 sink_function/render_context/encoding_observed/
    source_detail 等 XSS 风格字段)必须正确渲染并显示 sink_function。

    回归 crAPI-20260731:injection vuln agent 输出 XSS 风格字段,smart-union 把 entry
    误判为 XssVulnerability,渲染访问 vuln.sink_call 时 AttributeError
    → 整章渲染为 'render error' 占位。"""
    vuln = InjectionVulnerability(
        ID="INJ-VULN-01",
        vulnerability_type="SQLi",
        externally_exploitable=True,
        confidence="needs_review",
        source="coupon_code @ services/workshop/crapi/shop/views.py:379",
        source_detail="request.data['coupon_code']",
        sink_function="cursor.execute",
        encoding_observed="None",
        verdict="vulnerable",
        witness_payload="' UNION SELECT version()--",
    )
    result = render_vuln_card(vuln, "injection")
    assert "### INJ-VULN-01" in result
    assert "cursor.execute" in result  # sink_function rendered
    assert "vulnerable" in result
    assert "' UNION SELECT version()--" in result


def test_render_xss_card_full():
    vuln = XssVulnerability(
        ID="XSS-VULN-001",
        vulnerability_type="Reflected XSS",
        externally_exploitable=True,
        confidence="high",
        source="query param",
        path="/search",
        sink_function="innerHTML",
        render_context="HTML context",
        encoding_observed="None",
        verdict="Exploitable",
        witness_payload="<script>alert(1)</script>",
    )
    result = render_vuln_card(vuln, "xss")
    assert "### XSS-VULN-001" in result
    assert "**Vulnerable Location:** query param → /search" in result
    assert "**Sink Function:** innerHTML" in result
    assert "**Render Context:** HTML context" in result
    assert "**Encoding Observed:** None" in result
    assert "**Verdict:** Exploitable" in result
    assert "**Witness Payload:** <script>alert(1)</script>" in result


def test_render_auth_card_full():
    vuln = AuthVulnerability(
        ID="AUTH-VULN-001",
        vulnerability_type="Broken Authentication",
        externally_exploitable=True,
        confidence="high",
        source_endpoint="/api/login",
        vulnerable_code_location="auth/handlers.py:42",
        missing_defense="No rate limiting",
        exploitation_hypothesis="Brute force possible",
        suggested_exploit_technique="Dictionary attack",
    )
    result = render_vuln_card(vuln, "auth")
    assert "### AUTH-VULN-001" in result
    assert "**Source Endpoint:** /api/login" in result
    assert "**Vulnerable Code Location:** auth/handlers.py:42" in result
    assert "**Missing Defense:** No rate limiting" in result
    assert "**Exploitation Hypothesis:** Brute force possible" in result
    assert "**Suggested Exploit Technique:** Dictionary attack" in result


def test_render_authz_card_full():
    vuln = AuthzVulnerability(
        ID="AUTHZ-VULN-001",
        vulnerability_type="IDOR",
        externally_exploitable=True,
        confidence="high",
        endpoint="/api/users/{id}",
        vulnerable_code_location="api/users.py:15",
        role_context="Authenticated user",
        guard_evidence="No ownership check",
        side_effect="Access other users' data",
        reason="Missing authorization middleware",
        minimal_witness="GET /api/users/1234 → 200 OK",
    )
    result = render_vuln_card(vuln, "authz")
    assert "### AUTHZ-VULN-001" in result
    assert "**Endpoint:** /api/users/{id}" in result
    assert "**Role Context:** Authenticated user" in result
    assert "**Guard Evidence:** No ownership check" in result
    assert "**Side Effect:** Access other users' data" in result
    assert "**Reason:** Missing authorization middleware" in result
    assert "**Minimal Witness:** GET /api/users/1234 → 200 OK" in result


def test_render_ssrf_card_full():
    vuln = SsrfVulnerability(
        ID="SSRF-VULN-001",
        vulnerability_type="SSRF",
        externally_exploitable=True,
        confidence="high",
        source_endpoint="/api/fetch",
        vulnerable_parameter="url",
        vulnerable_code_location="api/fetch.py:20",
        missing_defense="No URL allowlist",
        exploitation_hypothesis="Internal network scan",
        suggested_exploit_technique="URL manipulation",
    )
    result = render_vuln_card(vuln, "ssrf")
    assert "### SSRF-VULN-001" in result
    assert "**Source Endpoint:** /api/fetch" in result
    assert "**Vulnerable Parameter:** url" in result
    assert "**Missing Defense:** No URL allowlist" in result


# --- title（spec 2026-08-25 §5）：### {ID} {类名}: {title} ---

def test_render_injection_card_with_title():
    """有 title → 渲染 ### {ID} {类名}: title。"""
    vuln = InjectionVulnerability(
        ID="INJ-VULN-01", vulnerability_type="SQLi",
        externally_exploitable=True, confidence="high",
        title="PostgreSQL SQL Injection via Coupon Validation",
    )
    result = render_vuln_card(vuln, "injection")
    assert result.startswith(
        "### INJ-VULN-01 Injection: PostgreSQL SQL Injection via Coupon Validation")


def test_render_injection_card_without_title_degrades_to_deterministic():
    """无 title → 确定性描述兜底；完全无线索时仍保类名（不再裸 ID，属有意变更）。"""
    vuln = InjectionVulnerability(
        ID="INJ-VULN-02", vulnerability_type="SQLi",
        externally_exploitable=True, confidence="high",
    )
    result = render_vuln_card(vuln, "injection")
    assert result.startswith("### INJ-VULN-02 Injection")


@pytest.mark.parametrize("cls,vuln_class,vtype,display", [
    (InjectionVulnerability, "injection", "SQLi", "Injection"),
    (XssVulnerability, "xss", "Reflected", "XSS"),
    (SsrfVulnerability, "ssrf", "SSRF", "SSRF"),
    (AuthVulnerability, "auth", "Auth", "Authentication"),
    (AuthzVulnerability, "authz", "IDOR", "Authorization"),
])
def test_all_classes_render_title_when_present(cls, vuln_class, vtype, display):
    """5 类卡片都在有 title 时拼 ### {ID} {类名}: title。"""
    vuln = cls(
        ID="X-VULN-01", vulnerability_type=vtype,
        externally_exploitable=True, confidence="high",
        title="descriptive one-liner",
    )
    result = render_vuln_card(vuln, vuln_class)
    assert result.startswith(f"### X-VULN-01 {display}: descriptive one-liner")



# --- 四要素统一卡片（spec 2026-08-25 §5/§6，brief Step 1 逐字断言） ---
# （额外补一行 monkeypatch zh：本文件 autouse fixture 默认 en，brief 断言基于 zh）

def _vuln(**kw):
    base = dict(ID="INJ-VULN-01", vulnerability_type="injection",
                externally_exploitable=True, confidence="high",
                title="命令注入：POST /contributions 直接 eval()（RCE）",
                source="preTax & req.body",
                path="POST /contributions → eval(req.body.preTax)",
                sink_function="eval", verdict="vulnerable", severity="critical",
                cwe_id="CWE-95", merge_source="both",
                affected_parameters=["preTax", "afterTax", "roth"],
                affected_entries=[
                    {"parameter": "preTax", "sink_location": "app/routes/contributions.js:32",
                     "chain_id": "INJ-GN-01", "track": "gitnexus"},
                    {"parameter": "afterTax", "sink_location": "app/routes/contributions.js:33",
                     "chain_id": "INJ-GN-04", "track": "gitnexus"}])
    base.update(kw)
    return InjectionVulnerability(**base)

SNIPPET = "preTax = eval(req.body.preTax);\ncontributions.preTax = preTax;"

def test_card_four_elements_and_meta_line(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    card = render_vuln_card(_vuln(), "injection", SNIPPET)
    assert card.startswith("### INJ-VULN-01 注入漏洞：命令注入")
    assert "严重程度：严重" in card and "CWE-95" in card
    assert "验证：静态分析" in card and "双轨确认" in card
    for section in ("**受影响入口**", "**漏洞说明**", "**危害**", "**问题代码**", "**修复建议**", "#### 技术细节"):
        assert section in card, section
    assert "| preTax | app/routes/contributions.js:32 |" in card
    assert SNIPPET in card  # 问题代码 fence 内

def test_card_no_internal_labels(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    v = _vuln(evidence_chain="preTax -> x (llm-pass-failed, needs_review)")
    card = render_vuln_card(v, "injection", None)
    assert "llm-pass-failed" not in card and "needs_review" not in card

def test_gn_only_card_degrades_gracefully(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    v = _vuln(ID="INJ-GN-01", source_track="gitnexus", confidence="low",
              title=None, severity=None, cwe_id=None, merge_source=None,
              source="preTax (app/routes/contributions.js:ContributionsHandler:7)",
              affected_entries=[{"parameter": "preTax",
                                 "sink_location": "app/routes/contributions.js:32",
                                 "chain_id": "INJ-GN-01", "track": "gitnexus"}])
    card = render_vuln_card(v, "injection", None)
    assert "待复核" in card
    assert "eval" in card  # 确定性说明含 sink 函数名
    assert "静态链路发现，建议人工确认" in card


def test_filter_by_confidence():
    vulns = [
        InjectionVulnerability(
            ID="INJECTION-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="low",
        ),
        InjectionVulnerability(
            ID="INJECTION-002", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="medium",
        ),
        InjectionVulnerability(
            ID="INJECTION-003", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
        ),
    ]
    queue = VulnerabilityQueue(vulnerabilities=vulns)
    config = ReportConfig(min_confidence="medium")
    result = filter_vulnerabilities(queue, config)
    assert len(result) == 2
    assert all(v.ID != "INJECTION-001" for v in result)


def test_filter_with_no_config():
    vulns = [
        InjectionVulnerability(
            ID="INJECTION-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="low",
        ),
    ]
    queue = VulnerabilityQueue(vulnerabilities=vulns)
    config = ReportConfig()
    result = filter_vulnerabilities(queue, config)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_render_findings_from_queues(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJECTION-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            source="user input", path="/api/search",
            sink_call="db.execute",
        ),
    ])
    (deliverables / "injection_exploitation_queue.json").write_text(
        queue.model_dump_json(indent=2)
    )

    await FindingsRenderer.render_findings_from_queues(deliverables)

    findings = (deliverables / "injection_findings.md").read_text()
    assert "## Injection Vulnerabilities" in findings
    assert "### INJECTION-001" in findings
    assert "**Sink Call:** db.execute" in findings
    assert "Disclaimer" in findings


@pytest.mark.asyncio
async def test_render_findings_skips_existing_findings(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    (deliverables / "injection_findings.md").write_text("Existing content")
    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJECTION-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
        ),
    ])
    (deliverables / "injection_exploitation_queue.json").write_text(
        queue.model_dump_json()
    )

    await FindingsRenderer.render_findings_from_queues(deliverables)

    content = (deliverables / "injection_findings.md").read_text()
    assert content == "Existing content"


@pytest.mark.asyncio
async def test_render_findings_empty_queue(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    queue = VulnerabilityQueue(vulnerabilities=[])
    (deliverables / "xss_exploitation_queue.json").write_text(
        queue.model_dump_json()
    )

    await FindingsRenderer.render_findings_from_queues(deliverables)

    findings = (deliverables / "xss_findings.md").read_text()
    assert "No XSS vulnerabilities found." in findings


@pytest.mark.asyncio
async def test_render_findings_skips_missing_queue(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    await FindingsRenderer.render_findings_from_queues(deliverables)

    assert not (deliverables / "injection_findings.md").exists()


@pytest.mark.asyncio
async def test_render_findings_reads_queue_from_intermediate_subdir(tmp_path):
    """tiering 回归（2026-08-18 f70bfc1c）：executor 写 queue 落桶内
    intermediate/（executor.py intermediate_path），白盒调用（queue_subdir=None）
    平铺读不到 → findings.md 静默缺失 → assemble 回落 analysis_deliverable，
    报告页出现「注入分析报告」等分节。读侧须 intermediate/ 优先 + 平铺兜底。"""
    deliverables = tmp_path / "deliverables"
    (deliverables / "intermediate").mkdir(parents=True)

    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJECTION-TIER-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            source="user input", path="/api/search",
            sink_call="db.execute",
        ),
    ])
    (deliverables / "intermediate" / "injection_exploitation_queue.json").write_text(
        queue.model_dump_json(indent=2)
    )

    await FindingsRenderer.render_findings_from_queues(deliverables)

    findings = deliverables / "injection_findings.md"
    assert findings.exists(), (
        "queue 在 intermediate/ 时 findings.md 必须产出（否则 assemble 回落 "
        "analysis_deliverable，报告页出现分分析报告）"
    )
    content = findings.read_text()
    assert "### INJECTION-TIER-001" in content
    assert "**Sink Call:** db.execute" in content


@pytest.mark.asyncio
async def test_render_findings_with_confidence_filter(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJECTION-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="low",
        ),
        InjectionVulnerability(
            ID="INJECTION-002", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
        ),
    ])
    (deliverables / "injection_exploitation_queue.json").write_text(
        queue.model_dump_json()
    )

    config = ReportConfig(min_confidence="high")
    await FindingsRenderer.render_findings_from_queues(deliverables, config)

    findings = (deliverables / "injection_findings.md").read_text()
    assert "INJECTION-002" in findings
    assert "INJECTION-001" not in findings


@pytest.mark.asyncio
async def test_render_recovers_bare_list_queue(tmp_path):
    """NodeGoat regression: bare-list queue renders instead of crashing."""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    bare_list = json.dumps([
        {"ID": "AUTH-1", "vulnerability_type": "Auth",
         "externally_exploitable": True, "confidence": "high",
         "source_endpoint": "POST /login"},
    ])
    (deliverables / "auth_exploitation_queue.json").write_text(bare_list)

    await FindingsRenderer.render_findings_from_queues(deliverables)

    findings = (deliverables / "auth_findings.md").read_text()
    assert "### AUTH-1" in findings
    assert "**Source Endpoint:** POST /login" in findings
    assert "auto-recovered" in findings.lower() or "bare-list" in findings.lower()


@pytest.mark.asyncio
async def test_render_isolates_bad_class(tmp_path):
    """A bad queue in one class must not block rendering of another."""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "auth_exploitation_queue.json").write_text("{not valid json")
    good = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJ-1", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            sink_call="db.execute",
        ),
    ])
    (deliverables / "injection_exploitation_queue.json").write_text(good.model_dump_json())

    await FindingsRenderer.render_findings_from_queues(deliverables)

    inj = (deliverables / "injection_findings.md").read_text()
    assert "### INJ-1" in inj
    auth = (deliverables / "auth_findings.md").read_text()
    assert "## Authentication Vulnerabilities" in auth
    assert "No authentication vulnerabilities found." not in auth  # not none_found
    assert "auth_exploitation_queue.json" in auth  # surfaces the bad file


@pytest.mark.asyncio
async def test_render_entry_isolation(tmp_path):
    """A single vuln that fails to render must not abort the whole class.

    Simulated by injecting a vuln whose render touches an attribute the entry
    lacks — covered indirectly via malformed entries being dropped by
    parse_lenient and good entries still rendering.
    """
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    content = json.dumps([
        {"ID": "AUTH-1", "vulnerability_type": "Auth",
         "externally_exploitable": True, "confidence": "high",
         "source_endpoint": "POST /login"},
        {"no_required_fields": True},  # dropped by parse_lenient
        {"ID": "AUTH-2", "vulnerability_type": "Auth",
         "externally_exploitable": True, "confidence": "medium"},
    ])
    (deliverables / "auth_exploitation_queue.json").write_text(content)

    await FindingsRenderer.render_findings_from_queues(deliverables)

    findings = (deliverables / "auth_findings.md").read_text()
    assert "### AUTH-1" in findings
    assert "### AUTH-2" in findings


@pytest.mark.asyncio
async def test_render_standard_empty_queue_still_none_found(tmp_path):
    """Regression guard: a well-formed empty queue still reads 'none found'."""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "xss_exploitation_queue.json").write_text(
        VulnerabilityQueue(vulnerabilities=[]).model_dump_json()
    )

    await FindingsRenderer.render_findings_from_queues(deliverables)

    findings = (deliverables / "xss_findings.md").read_text()
    assert "No XSS vulnerabilities found." in findings
    assert "auto-recovered" not in findings.lower()


@pytest.mark.asyncio
async def test_render_findings_with_subdirs_queue_in_whitebox_findings_in_blackbox(tmp_path):
    """Parameterized renderer: queue read from whitebox/, findings land in blackbox/.

    Blackbox caller passes deliverables_path=<root> with queue_subdir=WHITEBOX_SUBDIR
    and findings_subdir=BLACKBOX_SUBDIR. The renderer must read queues from <root>/whitebox/
    and write findings to <root>/blackbox/ — never colliding with whitebox findings
    written to <root>/whitebox/ by the whitebox caller.
    """
    from supernova_core.utils.paths import WHITEBOX_SUBDIR, BLACKBOX_SUBDIR

    root = tmp_path / "deliverables"
    (root / WHITEBOX_SUBDIR).mkdir(parents=True)
    # blackbox/ will be created by the renderer

    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJECTION-BB-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            source="query", path="/search", sink_call="db.execute",
        ),
    ])
    # queue lives in whitebox/ (Task 2 routing)
    (root / WHITEBOX_SUBDIR / "injection_exploitation_queue.json").write_text(
        queue.model_dump_json()
    )

    await FindingsRenderer.render_findings_from_queues(
        root,
        queue_subdir=WHITEBOX_SUBDIR,
        findings_subdir=BLACKBOX_SUBDIR,
    )

    # findings must land in blackbox/
    bb_findings = root / BLACKBOX_SUBDIR / "injection_findings.md"
    assert bb_findings.exists(), "findings must land in blackbox/ subdirectory"
    content = bb_findings.read_text()
    assert "### INJECTION-BB-001" in content
    assert "**Sink Call:** db.execute" in content

    # findings must NOT leak into root or whitebox/
    assert not (root / "injection_findings.md").exists(), \
        "findings must not leak to deliverables root"
    assert not (root / WHITEBOX_SUBDIR / "injection_findings.md").exists(), \
        "findings must not leak into whitebox/ (would collide with whitebox caller)"


@pytest.mark.asyncio
async def test_render_findings_with_subdirs_reads_legacy_queue_at_root(tmp_path):
    """Parameterized renderer: queue_subdir falls back to root when not in subdir.

    resolve_track_deliverable falls back to deliverables_dir/filename when the
  track subdir lacks the file — so a legacy root-level queue is still readable.
    """
    from supernova_core.utils.paths import WHITEBOX_SUBDIR, BLACKBOX_SUBDIR

    root = tmp_path / "deliverables"
    root.mkdir()

    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJECTION-LEGACY", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
        ),
    ])
    # legacy: queue at root, not in whitebox/
    (root / "injection_exploitation_queue.json").write_text(queue.model_dump_json())

    await FindingsRenderer.render_findings_from_queues(
        root,
        queue_subdir=WHITEBOX_SUBDIR,
        findings_subdir=BLACKBOX_SUBDIR,
    )

    bb_findings = root / BLACKBOX_SUBDIR / "injection_findings.md"
    assert bb_findings.exists()
    assert "### INJECTION-LEGACY" in bb_findings.read_text()


def test_render_injection_card_zh_labels(monkeypatch):
    """zh 模式：卡片标签为中文。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    vuln = InjectionVulnerability(
        ID="INJECTION-VULN-ZH", vulnerability_type="SQL Injection",
        externally_exploitable=True, confidence="high",
        sink_call="sqlite3.execute", notes="测试备注",
    )
    result = render_vuln_card(vuln, "injection")
    assert "**Sink 调用:** sqlite3.execute" in result
    assert "严重程度：" in result
    assert "**漏洞说明**" in result and "**危害**" in result and "#### 技术细节" in result
    assert "测试备注" in result  # notes 作为危害叙述
    assert "Summary" not in result


@pytest.mark.asyncio
async def test_render_findings_zh_heading_and_none_found(tmp_path, monkeypatch):
    """zh 模式：章节标题 + none_found 为中文。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "xss_exploitation_queue.json").write_text(
        VulnerabilityQueue(vulnerabilities=[]).model_dump_json()
    )
    await FindingsRenderer.render_findings_from_queues(deliverables)
    findings = (deliverables / "xss_findings.md").read_text()
    assert "## 跨站脚本 (XSS)" in findings
    assert "未发现 XSS 漏洞。" in findings
    assert "免责声明" in findings


@pytest.mark.asyncio
async def test_render_findings_injection_llm_queue_no_render_error(tmp_path):
    """端到端:injection queue 含 LLM 风格字段(sink_function/render_context/...) →
    injection_findings.md 无 'render error' 占位,含完整卡片。

    回归 crAPI-20260731:injection 7 张卡片全部显示 '### INJ-VULN-0X — render error',
    而底层 queue 数据完整正确(根因:smart-union 把 injection entry 误判为
    XssVulnerability,渲染访问 sink_call 崩)。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    content = json.dumps({"vulnerabilities": [
        {"ID": "INJ-VULN-01", "vulnerability_type": "SQLi",
         "externally_exploitable": True, "confidence": "needs_review",
         "source": "coupon_code @ services/workshop/crapi/shop/views.py:379",
         "source_detail": "request.data['coupon_code']",
         "sink_function": "cursor.execute",
         "encoding_observed": None, "render_context": None,
         "verdict": "vulnerable",
         "witness_payload": "' UNION SELECT version()--"},
        {"ID": "INJ-VULN-02", "vulnerability_type": "NoSQLi",
         "externally_exploitable": True, "confidence": "needs_review",
         "source": "coupon_code @ coupon_controller.go:77",
         "sink_function": "Collection.FindOne",
         "verdict": "vulnerable",
         "witness_payload": '{"coupon_code":{"$ne":null}}'},
    ]})
    (deliverables / "injection_exploitation_queue.json").write_text(content)

    await FindingsRenderer.render_findings_from_queues(deliverables)

    findings = (deliverables / "injection_findings.md").read_text()
    assert "### INJ-VULN-01" in findings
    assert "### INJ-VULN-02" in findings
    assert "render error" not in findings.lower()
    assert "cursor.execute" in findings
    assert "Collection.FindOne" in findings


@pytest.mark.asyncio
async def test_render_findings_with_repo_root_injects_snippet(tmp_path):
    """repo_root 提取链路（spec §10.4）：sink_location → extract_snippet ±3 行 →
    问题代码 fence + annotate_direct 回填（direct=False 的参数标疑似间接）。"""
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "routes.js").write_text(
        "const express = require('express');\n"
        "const preTax = eval(req.body.preTax);\n"
        "router.post('/contributions', handler);\n"
        "const afterTax = eval(req.body.afterTax);\n"
        "module.exports = router;\n"
    )
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJ-SNIP-01", vulnerability_type="injection",
            externally_exploitable=True, confidence="high",
            sink_function="eval",
            affected_entries=[
                {"parameter": "preTax", "sink_location": "app/routes.js:2",
                 "chain_id": "C1", "track": "gitnexus"},
                {"parameter": "nothere", "sink_location": "app/routes.js:4",
                 "chain_id": "C2", "track": "gitnexus"},
            ],
        ),
    ])
    (deliverables / "injection_exploitation_queue.json").write_text(
        queue.model_dump_json())

    await FindingsRenderer.render_findings_from_queues(deliverables, repo_root=repo)

    findings = (deliverables / "injection_findings.md").read_text()
    assert "**Vulnerable Code**" in findings
    assert "```js" in findings          # fence + 按扩展名语言标注
    assert "eval(req.body.preTax)" in findings
    # direct 回填：preTax 在 snippet 中（无标注）；nothere 不在（疑似间接）
    assert "| preTax | app/routes.js:2 | C1 |" in findings
    assert "| nothere (suspected indirect) | app/routes.js:4 | C2 |" in findings
