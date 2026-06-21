from shannon_core.models.queue_schemas import VulnerabilityQueue
from shannon_blackbox.services.exploitation_checker import CoverageResult
from shannon_blackbox.services.coverage_renderer import render_unverified_section


def _queue(*vulns) -> VulnerabilityQueue:
    return VulnerabilityQueue(vulnerabilities=list(vulns))


def test_render_unverified_section_auth_fields():
    from shannon_core.models.queue_schemas import AuthVulnerability
    queue = _queue(AuthVulnerability(
        ID="AUTH-VULN-08", vulnerability_type="Transport_Exposure",
        externally_exploitable=True, confidence="high",
        source_endpoint="ALL auth responses",
        missing_defense="No Cache-Control: no-store",
        suggested_exploit_technique="credential_session_theft",
    ))
    result = CoverageResult(
        vuln_class="auth", total=1,
        covered_ids=frozenset(), uncovered_ids=frozenset({"AUTH-VULN-08"}),
    )
    section = render_unverified_section(result, queue)
    assert "## Unverified Findings (Not Dynamically Exploited)" in section
    assert "### AUTH-VULN-08" in section
    assert "No Cache-Control: no-store" in section
    assert "credential_session_theft" in section
    assert "ALL auth responses" in section


def test_render_unverified_section_authz_fields():
    from shannon_core.models.queue_schemas import AuthzVulnerability
    queue = _queue(AuthzVulnerability(
        ID="AUTHZ-VULN-09", vulnerability_type="IDOR",
        externally_exploitable=True, confidence="high",
        endpoint="GET /api/Users/:id", guard_evidence="no ownership check",
    ))
    result = CoverageResult(
        vuln_class="authz", total=1,
        covered_ids=frozenset(), uncovered_ids=frozenset({"AUTHZ-VULN-09"}),
    )
    section = render_unverified_section(result, queue)
    assert "### AUTHZ-VULN-09" in section
    assert "GET /api/Users/:id" in section
    assert "no ownership check" in section


def test_render_unverified_section_injection_fields():
    from shannon_core.models.queue_schemas import InjectionVulnerability
    queue = _queue(InjectionVulnerability(
        ID="INJECTION-VULN-12", vulnerability_type="SQLi",
        externally_exploitable=True, confidence="medium",
        source="req.body.q", path="/search", verdict="vulnerable",
    ))
    result = CoverageResult(
        vuln_class="injection", total=1,
        covered_ids=frozenset(), uncovered_ids=frozenset({"INJECTION-VULN-12"}),
    )
    section = render_unverified_section(result, queue)
    assert "### INJECTION-VULN-12" in section
    assert "req.body.q" in section
    assert "/search" in section
    assert "vulnerable" in section


def test_render_unverified_section_sorted_and_header_count():
    from shannon_core.models.queue_schemas import AuthVulnerability
    queue = _queue(
        AuthVulnerability(ID="AUTH-VULN-24", vulnerability_type="t",
                          externally_exploitable=True, confidence="high"),
        AuthVulnerability(ID="AUTH-VULN-08", vulnerability_type="t",
                          externally_exploitable=True, confidence="high"),
    )
    result = CoverageResult(
        vuln_class="auth", total=2,
        covered_ids=frozenset(), uncovered_ids=frozenset({"AUTH-VULN-24", "AUTH-VULN-08"}),
    )
    section = render_unverified_section(result, queue)
    # 排序：08 在 24 前
    assert section.index("AUTH-VULN-08") < section.index("AUTH-VULN-24")
    assert "2 条漏洞" in section


import json

import pytest

from shannon_blackbox.services.coverage_renderer import close_coverage_gaps


def _write_queue(tmp_path, vuln_class, ids):
    data = {"vulnerabilities": [
        {"ID": i, "vulnerability_type": "t", "externally_exploitable": True, "confidence": "high"}
        for i in ids
    ]}
    (tmp_path / f"{vuln_class}_exploitation_queue.json").write_text(json.dumps(data))


@pytest.mark.asyncio
async def test_close_coverage_gaps_appends_section(tmp_path):
    _write_queue(tmp_path, "auth", ["AUTH-VULN-01", "AUTH-VULN-02", "AUTH-VULN-03"])
    (tmp_path / "auth_exploitation_evidence.md").write_text(
        "# Ev\n## Successfully Exploited\n### AUTH-VULN-01: a\n"
    )

    results = await close_coverage_gaps(tmp_path, ["auth"])

    assert len(results) == 1
    assert results[0].uncovered_ids == frozenset({"AUTH-VULN-02", "AUTH-VULN-03"})
    ev = (tmp_path / "auth_exploitation_evidence.md").read_text()
    assert "## Unverified Findings (Not Dynamically Exploited)" in ev
    assert "### AUTH-VULN-02" in ev and "### AUTH-VULN-03" in ev


@pytest.mark.asyncio
async def test_close_coverage_gaps_no_section_when_full(tmp_path):
    _write_queue(tmp_path, "auth", ["AUTH-VULN-01"])
    (tmp_path / "auth_exploitation_evidence.md").write_text("### AUTH-VULN-01: a\n")

    results = await close_coverage_gaps(tmp_path, ["auth"])

    assert results == []  # 全覆盖，无未覆盖结果
    ev = (tmp_path / "auth_exploitation_evidence.md").read_text()
    assert "Unverified Findings" not in ev


@pytest.mark.asyncio
async def test_close_coverage_gaps_skips_missing_evidence(tmp_path):
    _write_queue(tmp_path, "auth", ["AUTH-VULN-01"])
    # 不写 evidence

    results = await close_coverage_gaps(tmp_path, ["auth"])

    assert results == []  # evidence 缺失 → 跳过


@pytest.mark.asyncio
async def test_close_coverage_gaps_idempotent(tmp_path):
    _write_queue(tmp_path, "auth", ["AUTH-VULN-01", "AUTH-VULN-02"])
    (tmp_path / "auth_exploitation_evidence.md").write_text("### AUTH-VULN-01: a\n")

    await close_coverage_gaps(tmp_path, ["auth"])
    first = (tmp_path / "auth_exploitation_evidence.md").read_text()
    await close_coverage_gaps(tmp_path, ["auth"])  # 重跑
    second = (tmp_path / "auth_exploitation_evidence.md").read_text()

    assert first == second  # 幂等：不重复追加
    assert second.count("## Unverified Findings") == 1


@pytest.mark.asyncio
async def test_uncovered_section_reaches_final_report(tmp_path):
    """组合：close_coverage_gaps 写 evidence 节 → ReportAssembler 把 evidence 全文带进报告。"""
    from shannon_blackbox.services.report_assembler import ReportAssembler

    _write_queue(tmp_path, "auth", ["AUTH-VULN-01", "AUTH-VULN-02"])
    (tmp_path / "auth_exploitation_evidence.md").write_text(
        "# Auth Evidence\n## Successfully Exploited\n### AUTH-VULN-01: a\n"
    )

    await close_coverage_gaps(tmp_path, ["auth"])
    report_path = tmp_path / "comprehensive_security_assessment_report.md"
    await ReportAssembler.assemble(tmp_path, ["auth"], report_path)

    report = report_path.read_text()
    # 未覆盖节进入最终报告（ReportAssembler 读 evidence 全文）
    assert "Unverified Findings" in report
    assert "AUTH-VULN-02" in report
    # 已覆盖条目也在
    assert "AUTH-VULN-01" in report
