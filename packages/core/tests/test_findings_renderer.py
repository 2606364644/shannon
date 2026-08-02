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
    render_injection_entry,
    render_xss_entry,
    render_auth_entry,
    render_authz_entry,
    render_ssrf_entry,
    filter_vulnerabilities,
    FindingsRenderer,
)


@pytest.fixture(autouse=True)
def _en_lang_default(monkeypatch):
    """现有断言基于英文渲染（i18n 前的行为），默认 en。
    zh 行为由显式 setenv("zh") 的测试覆盖。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")


def test_render_injection_entry_full():
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
    result = render_injection_entry(vuln)
    assert "### INJECTION-VULN-001" in result
    assert "**Vulnerable Location:** user input → /api/users" in result
    assert "**Sink Call:** sqlite3.execute" in result
    assert "**Concat Occurrences:** query + user_input" in result
    assert "**Sanitization Observed:** None" in result
    assert "**Verdict:** Exploitable" in result
    assert "**Witness Payload:** ' OR 1=1 --" in result
    assert "**Notes:** Critical finding" in result


def test_render_injection_entry_minimal():
    vuln = InjectionVulnerability(
        ID="INJECTION-VULN-002",
        vulnerability_type="SQL Injection",
        externally_exploitable=True,
        confidence="medium",
    )
    result = render_injection_entry(vuln)
    assert "### INJECTION-VULN-002" in result
    assert "Sink Call" not in result
    assert "Notes" not in result


def test_render_injection_entry_llm_output_fields():
    """LLM 实际输出的 injection vuln(携带 sink_function/render_context/encoding_observed/
    source_detail 等 XSS 风格字段)必须正确渲染并显示 sink_function。

    回归 crAPI-20260731:injection vuln agent 输出 XSS 风格字段,smart-union 把 entry
    误判为 XssVulnerability,render_injection_entry 访问 vuln.sink_call 时 AttributeError
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
    result = render_injection_entry(vuln)
    assert "### INJ-VULN-01" in result
    assert "cursor.execute" in result  # sink_function rendered
    assert "vulnerable" in result
    assert "' UNION SELECT version()--" in result


def test_render_xss_entry_full():
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
    result = render_xss_entry(vuln)
    assert "### XSS-VULN-001" in result
    assert "**Vulnerable Location:** query param → /search" in result
    assert "**Sink Function:** innerHTML" in result
    assert "**Render Context:** HTML context" in result
    assert "**Encoding Observed:** None" in result
    assert "**Verdict:** Exploitable" in result
    assert "**Witness Payload:** <script>alert(1)</script>" in result


def test_render_auth_entry_full():
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
    result = render_auth_entry(vuln)
    assert "### AUTH-VULN-001" in result
    assert "**Source Endpoint:** /api/login" in result
    assert "**Vulnerable Code Location:** auth/handlers.py:42" in result
    assert "**Missing Defense:** No rate limiting" in result
    assert "**Exploitation Hypothesis:** Brute force possible" in result
    assert "**Suggested Exploit Technique:** Dictionary attack" in result


def test_render_authz_entry_full():
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
    result = render_authz_entry(vuln)
    assert "### AUTHZ-VULN-001" in result
    assert "**Endpoint:** /api/users/{id}" in result
    assert "**Role Context:** Authenticated user" in result
    assert "**Guard Evidence:** No ownership check" in result
    assert "**Side Effect:** Access other users' data" in result
    assert "**Reason:** Missing authorization middleware" in result
    assert "**Minimal Witness:** GET /api/users/1234 → 200 OK" in result


def test_render_ssrf_entry_full():
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
    result = render_ssrf_entry(vuln)
    assert "### SSRF-VULN-001" in result
    assert "**Source Endpoint:** /api/fetch" in result
    assert "**Vulnerable Parameter:** url" in result
    assert "**Missing Defense:** No URL allowlist" in result




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


def test_render_injection_entry_zh_labels(monkeypatch):
    """zh 模式：漏洞卡标签为中文。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    vuln = InjectionVulnerability(
        ID="INJECTION-VULN-ZH", vulnerability_type="SQL Injection",
        externally_exploitable=True, confidence="high",
        sink_call="sqlite3.execute", notes="测试备注",
    )
    result = render_injection_entry(vuln)
    assert "**摘要:**" in result
    assert "**Sink 调用:** sqlite3.execute" in result
    assert "**备注:** 测试备注" in result
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
    XssVulnerability,render_injection_entry 访问 sink_call 崩)。"""
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
