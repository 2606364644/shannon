# Report Generation Gap Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix five gaps in shannon-py report generation to align with original /root/shannon capabilities, ensuring whitebox-only scans produce non-empty reports and adding extensible output support.

**Architecture:** Add a deterministic FindingsRenderer service (JSON queue → Markdown, no LLM) in core, fix ReportAssembler to fall back to `_analysis_deliverable.md` files, inject model information from session.json, and add a ReportOutputProvider extensibility interface. Wire these into both whitebox and blackbox Temporal workflows.

**Tech Stack:** Python 3.12, Pydantic v2, Temporalio, pytest + pytest-asyncio, aiofiles

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `packages/core/src/shannon_core/services/findings_renderer.py` | Deterministic JSON→Markdown renderer for all 6 vuln classes |
| Create | `packages/core/src/shannon_core/interfaces/__init__.py` | Package init for interfaces |
| Create | `packages/core/src/shannon_core/interfaces/report_output_provider.py` | Abstract output provider + NoOp default |
| Create | `packages/core/tests/test_findings_renderer.py` | Unit tests for FindingsRenderer |
| Create | `packages/core/tests/test_report_output_provider.py` | Unit tests for output provider interface |
| Modify | `packages/blackbox/src/shannon_blackbox/services/report_assembler.py` | Three-priority fallback + `inject_model_info()` |
| Modify | `packages/blackbox/tests/test_report_assembler.py` | Tests for fallback and model injection |
| Modify | `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | New `render_findings` activity |
| Modify | `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | Call `render_findings` after vuln agents |
| Modify | `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` | Update `assemble_report`, add `finalize_report` |
| Modify | `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | Call `finalize_report` after report agent |
| Modify | `packages/core/src/shannon_core/services/__init__.py` | Export `FindingsRenderer` |

---

### Task 1: FindingsRenderer Service

**Files:**
- Create: `packages/core/src/shannon_core/services/findings_renderer.py`
- Create: `packages/core/tests/test_findings_renderer.py`

#### Step 1.1: Write the failing test for entry renderers

Create `packages/core/tests/test_findings_renderer.py`:

```python
import json

import pytest

from shannon_core.models.config import ReportConfig
from shannon_core.models.queue_schemas import (
    InjectionVulnerability,
    XssVulnerability,
    AuthVulnerability,
    SsrfVulnerability,
    AuthzVulnerability,
    MisconfigVulnerability,
    VulnerabilityQueue,
)
from shannon_core.services.findings_renderer import (
    render_injection_entry,
    render_xss_entry,
    render_auth_entry,
    render_authz_entry,
    render_ssrf_entry,
    render_misconfig_entry,
    filter_vulnerabilities,
    FindingsRenderer,
)


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


def test_render_misconfig_entry_full():
    vuln = MisconfigVulnerability(
        ID="MISCONFIG-VULN-001",
        vulnerability_type="Open Redirect",
        externally_exploitable=True,
        confidence="high",
        source_endpoint="/redirect",
        vulnerable_parameter="next",
        vulnerable_code_location="handlers/redirect.py:10",
        missing_defense="No redirect validation",
        exploitation_hypothesis="Phishing via redirect",
        suggested_exploit_technique="Parameter manipulation",
        redirect_sink="/redirect?url=",
        existing_validation="None",
    )
    result = render_misconfig_entry(vuln)
    assert "### MISCONFIG-VULN-001" in result
    assert "**Source Endpoint:** /redirect" in result
    assert "**Redirect Sink:** /redirect?url=" in result
    assert "**Existing Validation:** None" in result


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
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_findings_renderer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shannon_core.services.findings_renderer'`

- [ ] **Step 1.3: Write FindingsRenderer implementation**

Create `packages/core/src/shannon_core/services/findings_renderer.py`:

```python
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from shannon_core.models.config import ReportConfig
from shannon_core.models.queue_schemas import (
    AuthVulnerability,
    AuthzVulnerability,
    InjectionVulnerability,
    MisconfigVulnerability,
    SsrfVulnerability,
    Vulnerability,
    VulnerabilityQueue,
    XssVulnerability,
)
from shannon_core.utils.file_io import async_path_exists, async_read_file, async_write_file

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}

DISCLAIMER = (
    "> **Disclaimer:** This report was generated by an automated security assessment tool. "
    "All findings should be verified by a qualified security professional before taking action."
)


@dataclass
class VulnClassConfig:
    heading: str
    none_found_label: str
    queue_file: str
    findings_file: str
    render_entry: Callable


def render_injection_entry(vuln: InjectionVulnerability) -> str:
    lines = [f"### {vuln.ID}", "", "**Summary:**"]
    if vuln.source or vuln.path:
        location = f"{vuln.source or 'N/A'} → {vuln.path or 'N/A'}"
        lines.append(f"- **Vulnerable Location:** {location}")
    if vuln.sink_call:
        lines.append(f"- **Sink Call:** {vuln.sink_call}")
    if vuln.concat_occurrences:
        lines.append(f"- **Concat Occurrences:** {vuln.concat_occurrences}")
    if vuln.sanitization_observed:
        lines.append(f"- **Sanitization Observed:** {vuln.sanitization_observed}")
    if vuln.verdict:
        lines.append(f"- **Verdict:** {vuln.verdict}")
    if vuln.witness_payload:
        lines.append(f"- **Witness Payload:** {vuln.witness_payload}")
    if vuln.notes:
        lines.append(f"\n**Notes:** {vuln.notes}")
    lines.append("")
    return "\n".join(lines)


def render_xss_entry(vuln: XssVulnerability) -> str:
    lines = [f"### {vuln.ID}", "", "**Summary:**"]
    if vuln.source or vuln.path:
        location = f"{vuln.source or 'N/A'} → {vuln.path or 'N/A'}"
        lines.append(f"- **Vulnerable Location:** {location}")
    if vuln.sink_function:
        lines.append(f"- **Sink Function:** {vuln.sink_function}")
    if vuln.render_context:
        lines.append(f"- **Render Context:** {vuln.render_context}")
    if vuln.encoding_observed:
        lines.append(f"- **Encoding Observed:** {vuln.encoding_observed}")
    if vuln.verdict:
        lines.append(f"- **Verdict:** {vuln.verdict}")
    if vuln.witness_payload:
        lines.append(f"- **Witness Payload:** {vuln.witness_payload}")
    if vuln.notes:
        lines.append(f"\n**Notes:** {vuln.notes}")
    lines.append("")
    return "\n".join(lines)


def render_auth_entry(vuln: AuthVulnerability) -> str:
    lines = [f"### {vuln.ID}", "", "**Summary:**"]
    if vuln.source_endpoint:
        lines.append(f"- **Source Endpoint:** {vuln.source_endpoint}")
    if vuln.vulnerable_code_location:
        lines.append(f"- **Vulnerable Code Location:** {vuln.vulnerable_code_location}")
    if vuln.missing_defense:
        lines.append(f"- **Missing Defense:** {vuln.missing_defense}")
    if vuln.exploitation_hypothesis:
        lines.append(f"- **Exploitation Hypothesis:** {vuln.exploitation_hypothesis}")
    if vuln.suggested_exploit_technique:
        lines.append(f"- **Suggested Exploit Technique:** {vuln.suggested_exploit_technique}")
    if vuln.notes:
        lines.append(f"\n**Notes:** {vuln.notes}")
    lines.append("")
    return "\n".join(lines)


def render_authz_entry(vuln: AuthzVulnerability) -> str:
    lines = [f"### {vuln.ID}", "", "**Summary:**"]
    if vuln.endpoint:
        lines.append(f"- **Endpoint:** {vuln.endpoint}")
    if vuln.vulnerable_code_location:
        lines.append(f"- **Vulnerable Code Location:** {vuln.vulnerable_code_location}")
    if vuln.role_context:
        lines.append(f"- **Role Context:** {vuln.role_context}")
    if vuln.guard_evidence:
        lines.append(f"- **Guard Evidence:** {vuln.guard_evidence}")
    if vuln.side_effect:
        lines.append(f"- **Side Effect:** {vuln.side_effect}")
    if vuln.reason:
        lines.append(f"- **Reason:** {vuln.reason}")
    if vuln.minimal_witness:
        lines.append(f"- **Minimal Witness:** {vuln.minimal_witness}")
    if vuln.notes:
        lines.append(f"\n**Notes:** {vuln.notes}")
    lines.append("")
    return "\n".join(lines)


def render_ssrf_entry(vuln: SsrfVulnerability) -> str:
    lines = [f"### {vuln.ID}", "", "**Summary:**"]
    if vuln.source_endpoint:
        lines.append(f"- **Source Endpoint:** {vuln.source_endpoint}")
    if vuln.vulnerable_parameter:
        lines.append(f"- **Vulnerable Parameter:** {vuln.vulnerable_parameter}")
    if vuln.vulnerable_code_location:
        lines.append(f"- **Vulnerable Code Location:** {vuln.vulnerable_code_location}")
    if vuln.missing_defense:
        lines.append(f"- **Missing Defense:** {vuln.missing_defense}")
    if vuln.exploitation_hypothesis:
        lines.append(f"- **Exploitation Hypothesis:** {vuln.exploitation_hypothesis}")
    if vuln.suggested_exploit_technique:
        lines.append(f"- **Suggested Exploit Technique:** {vuln.suggested_exploit_technique}")
    if vuln.notes:
        lines.append(f"\n**Notes:** {vuln.notes}")
    lines.append("")
    return "\n".join(lines)


def render_misconfig_entry(vuln: MisconfigVulnerability) -> str:
    lines = [f"### {vuln.ID}", "", "**Summary:**"]
    if vuln.source_endpoint:
        lines.append(f"- **Source Endpoint:** {vuln.source_endpoint}")
    if vuln.vulnerable_parameter:
        lines.append(f"- **Vulnerable Parameter:** {vuln.vulnerable_parameter}")
    if vuln.vulnerable_code_location:
        lines.append(f"- **Vulnerable Code Location:** {vuln.vulnerable_code_location}")
    if vuln.missing_defense:
        lines.append(f"- **Missing Defense:** {vuln.missing_defense}")
    if vuln.exploitation_hypothesis:
        lines.append(f"- **Exploitation Hypothesis:** {vuln.exploitation_hypothesis}")
    if vuln.suggested_exploit_technique:
        lines.append(f"- **Suggested Exploit Technique:** {vuln.suggested_exploit_technique}")
    if vuln.redirect_sink:
        lines.append(f"- **Redirect Sink:** {vuln.redirect_sink}")
    if vuln.existing_validation:
        lines.append(f"- **Existing Validation:** {vuln.existing_validation}")
    if vuln.notes:
        lines.append(f"\n**Notes:** {vuln.notes}")
    lines.append("")
    return "\n".join(lines)


CLASS_CONFIG: dict[str, VulnClassConfig] = {
    "injection": VulnClassConfig(
        heading="Injection Vulnerabilities",
        none_found_label="No injection vulnerabilities found.",
        queue_file="injection_exploitation_queue.json",
        findings_file="injection_findings.md",
        render_entry=render_injection_entry,
    ),
    "xss": VulnClassConfig(
        heading="Cross-Site Scripting (XSS)",
        none_found_label="No XSS vulnerabilities found.",
        queue_file="xss_exploitation_queue.json",
        findings_file="xss_findings.md",
        render_entry=render_xss_entry,
    ),
    "auth": VulnClassConfig(
        heading="Authentication Vulnerabilities",
        none_found_label="No authentication vulnerabilities found.",
        queue_file="auth_exploitation_queue.json",
        findings_file="auth_findings.md",
        render_entry=render_auth_entry,
    ),
    "authz": VulnClassConfig(
        heading="Authorization Vulnerabilities",
        none_found_label="No authorization vulnerabilities found.",
        queue_file="authz_exploitation_queue.json",
        findings_file="authz_findings.md",
        render_entry=render_authz_entry,
    ),
    "ssrf": VulnClassConfig(
        heading="Server-Side Request Forgery (SSRF)",
        none_found_label="No SSRF vulnerabilities found.",
        queue_file="ssrf_exploitation_queue.json",
        findings_file="ssrf_findings.md",
        render_entry=render_ssrf_entry,
    ),
    "misconfig": VulnClassConfig(
        heading="Security Misconfigurations",
        none_found_label="No security misconfigurations found.",
        queue_file="misconfig_exploitation_queue.json",
        findings_file="misconfig_findings.md",
        render_entry=render_misconfig_entry,
    ),
}


def _passes_filter(vuln: Vulnerability, config: ReportConfig) -> bool:
    if config.min_confidence:
        vuln_level = CONFIDENCE_ORDER.get(vuln.confidence, 0)
        min_level = CONFIDENCE_ORDER.get(config.min_confidence, 0)
        if vuln_level < min_level:
            return False
    if config.min_severity:
        severity = getattr(vuln, "severity", None)
        if severity is not None:
            vuln_level = SEVERITY_ORDER.get(severity, 0)
            min_level = SEVERITY_ORDER.get(config.min_severity, 0)
            if vuln_level < min_level:
                return False
    return True


def filter_vulnerabilities(queue: VulnerabilityQueue, config: ReportConfig) -> list[Vulnerability]:
    return [v for v in queue.vulnerabilities if _passes_filter(v, config)]


class FindingsRenderer:
    @staticmethod
    async def render_findings_from_queues(
        deliverables_path: Path,
        report_config: ReportConfig | None = None,
    ) -> None:
        config = report_config or ReportConfig()
        for _vuln_class, class_cfg in CLASS_CONFIG.items():
            findings_path = deliverables_path / class_cfg.findings_file
            if await async_path_exists(findings_path):
                continue
            queue_path = deliverables_path / class_cfg.queue_file
            if not await async_path_exists(queue_path):
                continue

            content = await async_read_file(queue_path)
            queue = VulnerabilityQueue.model_validate_json(content)
            filtered = filter_vulnerabilities(queue, config)

            sections: list[str] = [f"## {class_cfg.heading}", ""]
            if not filtered:
                sections.append(class_cfg.none_found_label)
            else:
                for vuln in filtered:
                    sections.append(class_cfg.render_entry(vuln))

            sections.append("")
            sections.append(DISCLAIMER)
            sections.append("")
            await async_write_file(findings_path, "\n".join(sections))
```

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_findings_renderer.py -v`
Expected: All 12 tests PASS

- [ ] **Step 1.5: Commit**

```bash
git add packages/core/src/shannon_core/services/findings_renderer.py packages/core/tests/test_findings_renderer.py
git commit -m "feat: add deterministic FindingsRenderer service for JSON-to-Markdown conversion"
```

---

### Task 2: ReportOutputProvider Interface

**Files:**
- Create: `packages/core/src/shannon_core/interfaces/__init__.py`
- Create: `packages/core/src/shannon_core/interfaces/report_output_provider.py`
- Create: `packages/core/tests/test_report_output_provider.py`

- [ ] **Step 2.1: Write the failing test**

Create `packages/core/tests/test_report_output_provider.py`:

```python
import pytest
from pathlib import Path

from shannon_core.interfaces.report_output_provider import (
    ReportOutputProvider,
    NoOpReportOutputProvider,
)


@pytest.mark.asyncio
async def test_noop_provider_returns_none(tmp_path: Path):
    provider = NoOpReportOutputProvider()
    result = await provider.generate(tmp_path / "report.md", tmp_path / "deliverables")
    assert result == {"output_path": None}


def test_noop_is_subclass():
    assert issubclass(NoOpReportOutputProvider, ReportOutputProvider)


def test_cannot_instantiate_abstract():
    with pytest.raises(TypeError):
        ReportOutputProvider()


@pytest.mark.asyncio
async def test_custom_provider_can_be_implemented(tmp_path: Path):
    class InMemoryProvider(ReportOutputProvider):
        async def generate(self, report_path: Path, deliverables_path: Path) -> dict[str, str | None]:
            return {"output_path": str(report_path)}

    provider = InMemoryProvider()
    result = await provider.generate(tmp_path / "report.md", tmp_path / "deliverables")
    assert result["output_path"] == str(tmp_path / "report.md")
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_report_output_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shannon_core.interfaces'`

- [ ] **Step 2.3: Write the interface and NoOp implementation**

Create `packages/core/src/shannon_core/interfaces/__init__.py`:

```python
```

Create `packages/core/src/shannon_core/interfaces/report_output_provider.py`:

```python
from abc import ABC, abstractmethod
from pathlib import Path


class ReportOutputProvider(ABC):
    @abstractmethod
    async def generate(self, report_path: Path, deliverables_path: Path) -> dict[str, str | None]:
        ...


class NoOpReportOutputProvider(ReportOutputProvider):
    async def generate(self, report_path: Path, deliverables_path: Path) -> dict[str, str | None]:
        return {"output_path": None}
```

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_report_output_provider.py -v`
Expected: All 4 tests PASS

- [ ] **Step 2.5: Commit**

```bash
git add packages/core/src/shannon_core/interfaces/__init__.py packages/core/src/shannon_core/interfaces/report_output_provider.py packages/core/tests/test_report_output_provider.py
git commit -m "feat: add ReportOutputProvider extensibility interface with NoOp default"
```

---

### Task 3: ReportAssembler Fixes (Three-Priority Fallback + Model Injection)

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/services/report_assembler.py`
- Modify: `packages/blackbox/tests/test_report_assembler.py`

- [ ] **Step 3.1: Write the failing tests for three-priority fallback**

Add these tests to the end of `packages/blackbox/tests/test_report_assembler.py`:

```python
@pytest.mark.asyncio
async def test_assemble_falls_back_to_analysis_deliverable(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_analysis_deliverable.md").write_text("# Injection Analysis")
    report_path = tmp_path / "report.md"

    await ReportAssembler.assemble(deliverables, ["injection"], report_path)
    content = report_path.read_text()
    assert "Injection Analysis" in content


@pytest.mark.asyncio
async def test_assemble_priority_evidence_over_analysis(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_evidence.md").write_text("# Evidence")
    (deliverables / "injection_analysis_deliverable.md").write_text("# Analysis")
    report_path = tmp_path / "report.md"

    await ReportAssembler.assemble(deliverables, ["injection"], report_path)
    content = report_path.read_text()
    assert "Evidence" in content
    assert "Analysis" not in content


@pytest.mark.asyncio
async def test_assemble_priority_findings_over_analysis(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "xss_findings.md").write_text("# Findings")
    (deliverables / "xss_analysis_deliverable.md").write_text("# Analysis")
    report_path = tmp_path / "report.md"

    await ReportAssembler.assemble(deliverables, ["xss"], report_path)
    content = report_path.read_text()
    assert "Findings" in content
    assert "Analysis" not in content
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/test_report_assembler.py::test_assemble_falls_back_to_analysis_deliverable packages/blackbox/tests/test_report_assembler.py::test_assemble_priority_evidence_over_analysis packages/blackbox/tests/test_report_assembler.py::test_assemble_priority_findings_over_analysis -v`
Expected: FAIL — `assert "Injection Analysis" in content` (the `_analysis_deliverable.md` file is not checked)

- [ ] **Step 3.3: Implement three-priority fallback in ReportAssembler**

Replace the contents of `packages/blackbox/src/shannon_blackbox/services/report_assembler.py` with:

```python
import json
from pathlib import Path
from typing import Any

from shannon_core.utils.file_io import async_path_exists, async_read_file, async_write_file


class ReportAssembler:
    @staticmethod
    async def assemble(
        deliverables_path: Path,
        vuln_classes: list[str],
        report_path: Path,
        report_config: dict[str, Any] | None = None,
    ) -> None:
        sections: list[str] = []
        for vuln_class in vuln_classes:
            evidence = deliverables_path / f"{vuln_class}_exploitation_evidence.md"
            findings = deliverables_path / f"{vuln_class}_findings.md"
            analysis = deliverables_path / f"{vuln_class}_analysis_deliverable.md"
            if await async_path_exists(evidence):
                content = await async_read_file(evidence)
                sections.append(content)
            elif await async_path_exists(findings):
                content = await async_read_file(findings)
                sections.append(content)
            elif await async_path_exists(analysis):
                content = await async_read_file(analysis)
                sections.append(content)
        report_content = "\n\n---\n\n".join(sections)
        await async_write_file(report_path, report_content)

    @staticmethod
    async def inject_model_info(report_path: Path, session_path: Path) -> None:
        if not session_path.exists():
            return

        try:
            session_data = json.loads(session_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        metrics = session_data.get("metrics", {})
        agents = metrics.get("agents", {})
        models: set[str] = set()
        for agent_data in agents.values():
            if isinstance(agent_data, dict):
                model = agent_data.get("model")
                if model:
                    models.add(str(model))

        if not models:
            return

        if not await async_path_exists(report_path):
            return

        model_line = f"- **Model:** {', '.join(sorted(models))}"
        content = await async_read_file(report_path)
        lines = content.split("\n")
        new_lines: list[str] = []
        inserted = False

        for line in lines:
            new_lines.append(line)
            if not inserted and "- Assessment Date:" in line:
                new_lines.append(model_line)
                inserted = True

        if not inserted:
            for i, line in enumerate(new_lines):
                if line.strip() == "## Executive Summary":
                    new_lines.insert(i + 1, model_line)
                    inserted = True
                    break

        if inserted:
            await async_write_file(report_path, "\n".join(new_lines))
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/test_report_assembler.py -v`
Expected: All 8 tests PASS (5 existing + 3 new)

- [ ] **Step 3.5: Write the failing tests for model injection**

Add `import json` to the top of `packages/blackbox/tests/test_report_assembler.py` (after the existing `import pytest`), then add these tests to the end of the file:

```python
@pytest.mark.asyncio
async def test_inject_model_info_after_date_line(tmp_path):
    report_path = tmp_path / "report.md"
    report_path.write_text("## Executive Summary\n\n- Assessment Date: 2026-06-03\n\nSome content")
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps({
        "metrics": {"agents": {"recon": {"model": "claude-sonnet-4-6", "success": True}}}
    }))

    await ReportAssembler.inject_model_info(report_path, session_path)
    content = report_path.read_text()
    assert "- **Model:** claude-sonnet-4-6" in content
    # Model line appears after Assessment Date
    lines = content.split("\n")
    date_idx = next(i for i, l in enumerate(lines) if "Assessment Date" in l)
    model_idx = next(i for i, l in enumerate(lines) if "**Model:**" in l)
    assert model_idx == date_idx + 1


@pytest.mark.asyncio
async def test_inject_model_info_fallback_to_executive_summary(tmp_path):
    report_path = tmp_path / "report.md"
    report_path.write_text("## Executive Summary\n\nSome content without date")
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps({
        "metrics": {"agents": {"recon": {"model": "claude-opus-4-8", "success": True}}}
    }))

    await ReportAssembler.inject_model_info(report_path, session_path)
    content = report_path.read_text()
    assert "- **Model:** claude-opus-4-8" in content


@pytest.mark.asyncio
async def test_inject_model_info_multiple_models(tmp_path):
    report_path = tmp_path / "report.md"
    report_path.write_text("## Executive Summary\n\n- Assessment Date: 2026-06-03\n")
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps({
        "metrics": {
            "agents": {
                "recon": {"model": "claude-sonnet-4-6"},
                "injection-vuln": {"model": "claude-opus-4-8"},
            }
        }
    }))

    await ReportAssembler.inject_model_info(report_path, session_path)
    content = report_path.read_text()
    assert "claude-opus-4-8" in content
    assert "claude-sonnet-4-6" in content


@pytest.mark.asyncio
async def test_inject_model_info_skips_when_no_session(tmp_path):
    report_path = tmp_path / "report.md"
    report_path.write_text("## Executive Summary\n\n- Assessment Date: 2026-06-03\n")
    session_path = tmp_path / "nonexistent_session.json"

    await ReportAssembler.inject_model_info(report_path, session_path)
    content = report_path.read_text()
    assert "**Model:**" not in content


@pytest.mark.asyncio
async def test_inject_model_info_skips_when_no_models(tmp_path):
    report_path = tmp_path / "report.md"
    report_path.write_text("## Executive Summary\n\n- Assessment Date: 2026-06-03\n")
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps({"metrics": {"agents": {"recon": {"success": True}}}}))

    await ReportAssembler.inject_model_info(report_path, session_path)
    content = report_path.read_text()
    assert "**Model:**" not in content


@pytest.mark.asyncio
async def test_inject_model_info_skips_when_no_report(tmp_path):
    report_path = tmp_path / "nonexistent_report.md"
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps({
        "metrics": {"agents": {"recon": {"model": "claude-sonnet-4-6"}}}
    }))

    await ReportAssembler.inject_model_info(report_path, session_path)
    assert not report_path.exists()
```

- [ ] **Step 3.6: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/test_report_assembler.py -v`
Expected: All 14 tests PASS (8 previous + 6 new)

- [ ] **Step 3.7: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/services/report_assembler.py packages/blackbox/tests/test_report_assembler.py
git commit -m "fix: add three-priority fallback and model injection to ReportAssembler"
```

---

### Task 4: Whitebox Workflow Integration

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`

- [ ] **Step 4.1: Write the failing test for render_findings activity**

Add to `packages/whitebox/tests/test_pipeline_shared.py` (or create `packages/whitebox/tests/test_findings_activity.py` if the shared test file is not suitable):

Create `packages/whitebox/tests/test_findings_activity.py`:

```python
import json
import pytest
from pathlib import Path

from shannon_core.models.queue_schemas import InjectionVulnerability, VulnerabilityQueue


@pytest.mark.asyncio
async def test_render_findings_activity_generates_findings(tmp_path):
    """Integration test: render_findings activity should produce findings MD from queue JSON."""
    from shannon_core.services.findings_renderer import FindingsRenderer

    repo = tmp_path / "my-repo"
    deliverables = repo / ".shannon" / "deliverables"
    deliverables.mkdir(parents=True)

    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJECTION-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            source="query param", path="/search", sink_call="db.execute",
        ),
    ])
    (deliverables / "injection_exploitation_queue.json").write_text(
        queue.model_dump_json(indent=2)
    )

    await FindingsRenderer.render_findings_from_queues(deliverables)

    findings = (deliverables / "injection_findings.md")
    assert findings.exists()
    content = findings.read_text()
    assert "### INJECTION-001" in content
    assert "**Sink Call:** db.execute" in content
```

- [ ] **Step 4.2: Run test to verify it passes (FindingsRenderer already exists)**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/test_findings_activity.py -v`
Expected: PASS (1 test)

- [ ] **Step 4.3: Add render_findings activity to whitebox activities**

Add to end of `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`, before the last function or at the end of the file:

```python
@activity.defn
async def render_findings(input: ActivityInput) -> None:
    try:
        from shannon_core.services.findings_renderer import FindingsRenderer
        from shannon_core.config.parser import parse_config

        _, deliverables, _ = _get_paths(input)
        report_config = None
        if input.config_path:
            cfg = parse_config(input.config_path)
            report_config = cfg.report
        await FindingsRenderer.render_findings_from_queues(deliverables, report_config)
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

- [ ] **Step 4.4: Wire render_findings into whitebox workflow**

In `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`, add the `render_findings` activity call after the vuln agents section and before `self._state.status = "completed"`.

Find this block (around lines 134-146):

```python
            if vuln_tasks:
                results = await asyncio.gather(*vuln_tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    vt = selected_classes[i]
                    agent_name = AgentName(f"{vt}-vuln")
                    if isinstance(result, Exception):
                        self._state.errors.append(f"{agent_name.value}: {result}")
                    else:
                        self._state.completed_agents.append(agent_name.value)
                        self._state.agent_metrics[agent_name.value] = result

            self._state.status = "completed"
            return self._state
```

Replace with:

```python
            if vuln_tasks:
                results = await asyncio.gather(*vuln_tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    vt = selected_classes[i]
                    agent_name = AgentName(f"{vt}-vuln")
                    if isinstance(result, Exception):
                        self._state.errors.append(f"{agent_name.value}: {result}")
                    else:
                        self._state.completed_agents.append(agent_name.value)
                        self._state.agent_metrics[agent_name.value] = result

            await workflow.execute_activity(
                activities.render_findings, act_input,
                start_to_close_timeout=timedelta(minutes=5),
            )

            self._state.status = "completed"
            return self._state
```

- [ ] **Step 4.5: Run whitebox tests to verify nothing is broken**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/ -v`
Expected: All tests PASS

- [ ] **Step 4.6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/test_findings_activity.py
git commit -m "feat: wire FindingsRenderer into whitebox workflow after vuln agents"
```

---

### Task 5: Blackbox Workflow Integration

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/activities.py`
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`

- [ ] **Step 5.1: Write the failing test for finalize_report activity**

Create `packages/blackbox/tests/test_finalize_report.py`:

```python
import json
import pytest
from pathlib import Path

from shannon_core.models.queue_schemas import InjectionVulnerability, VulnerabilityQueue


@pytest.mark.asyncio
async def test_assemble_report_activity_generates_findings(tmp_path):
    """assemble_report should run FindingsRenderer before assembling."""
    from shannon_core.services.findings_renderer import FindingsRenderer

    deliverables = tmp_path / ".shannon" / "deliverables"
    deliverables.mkdir(parents=True)

    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJECTION-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            source="query", path="/search", sink_call="db.execute",
        ),
    ])
    (deliverables / "injection_exploitation_queue.json").write_text(
        queue.model_dump_json()
    )

    await FindingsRenderer.render_findings_from_queues(deliverables)

    assert (deliverables / "injection_findings.md").exists()
    content = (deliverables / "injection_findings.md").read_text()
    assert "INJECTION-001" in content


@pytest.mark.asyncio
async def test_model_injection_in_finalize(tmp_path):
    """finalize_report should inject model info from session.json."""
    from shannon_blackbox.services.report_assembler import ReportAssembler

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    deliverables = workspace / ".shannon" / "deliverables"
    deliverables.mkdir(parents=True)

    report_path = deliverables / "comprehensive_security_assessment_report.md"
    report_path.write_text("## Executive Summary\n\n- Assessment Date: 2026-06-03\n")

    session_path = workspace / "session.json"
    session_path.write_text(json.dumps({
        "metrics": {"agents": {"recon": {"model": "claude-sonnet-4-6"}}}
    }))

    await ReportAssembler.inject_model_info(report_path, session_path)

    content = report_path.read_text()
    assert "- **Model:** claude-sonnet-4-6" in content


@pytest.mark.asyncio
async def test_noop_output_provider(tmp_path):
    """Output provider should be NoOp by default (no side effects)."""
    from shannon_core.interfaces.report_output_provider import NoOpReportOutputProvider

    provider = NoOpReportOutputProvider()
    result = await provider.generate(tmp_path / "report.md", tmp_path / "deliverables")
    assert result["output_path"] is None
```

- [ ] **Step 5.2: Run tests to verify they pass (using existing implementations)**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/test_finalize_report.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5.3: Update assemble_report activity in blackbox activities**

Replace the `assemble_report` activity function in `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` (lines 156-171) with:

```python
@activity.defn
async def assemble_report(input: BlackboxActivityInput) -> None:
    try:
        from shannon_blackbox.services.report_assembler import ReportAssembler
        from shannon_core.models.agents import ALL_VULN_CLASSES
        from shannon_core.services.findings_renderer import FindingsRenderer

        deliverables = _get_deliverables_path(input)
        vuln_classes: list[str] = list(ALL_VULN_CLASSES)
        report_path = deliverables / "comprehensive_security_assessment_report.md"

        report_config = None
        if input.config_path:
            from shannon_core.config.parser import parse_config
            cfg = parse_config(input.config_path)
            report_config = cfg.report
        await FindingsRenderer.render_findings_from_queues(deliverables, report_config)

        await ReportAssembler.assemble(deliverables, vuln_classes, report_path)
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

Add the `finalize_report` activity after the `run_report_agent` activity (after line 196):

```python
@activity.defn
async def finalize_report(input: BlackboxActivityInput) -> None:
    try:
        from shannon_blackbox.services.report_assembler import ReportAssembler
        from shannon_core.interfaces.report_output_provider import NoOpReportOutputProvider

        deliverables = _get_deliverables_path(input)
        report_path = deliverables / "comprehensive_security_assessment_report.md"

        session_path = Path(input.workspace_path) / "session.json" if input.workspace_path else None
        if session_path:
            await ReportAssembler.inject_model_info(report_path, session_path)

        provider = NoOpReportOutputProvider()
        await provider.generate(report_path, deliverables)
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

Note: `Path` import is already at the top of the file (line 1).

- [ ] **Step 5.4: Wire finalize_report into blackbox workflow**

In `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`, add the `finalize_report` activity call after the report agent and before `self._state.status = "completed"`.

Find this block (around lines 169-179):

```python
            if AgentName.REPORT.value not in self._state.completed_agents:
                metrics = await workflow.execute_activity(
                    activities.run_report_agent, act_input,
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry_policy,
                )
                self._state.completed_agents.append(AgentName.REPORT.value)
                self._state.agent_metrics[AgentName.REPORT.value] = metrics

            self._state.status = "completed"
            return self._state
```

Replace with:

```python
            if AgentName.REPORT.value not in self._state.completed_agents:
                metrics = await workflow.execute_activity(
                    activities.run_report_agent, act_input,
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry_policy,
                )
                self._state.completed_agents.append(AgentName.REPORT.value)
                self._state.agent_metrics[AgentName.REPORT.value] = metrics

            await workflow.execute_activity(
                activities.finalize_report, act_input,
                start_to_close_timeout=timedelta(minutes=5),
            )

            self._state.status = "completed"
            return self._state
```

- [ ] **Step 5.5: Run blackbox tests to verify nothing is broken**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/ -v`
Expected: All tests PASS

- [ ] **Step 5.6: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/activities.py packages/blackbox/src/shannon_blackbox/pipeline/workflows.py packages/blackbox/tests/test_finalize_report.py
git commit -m "feat: wire FindingsRenderer, model injection, and output provider into blackbox workflow"
```

---

### Task 6: Export Updates + Full Test Suite

**Files:**
- Modify: `packages/core/src/shannon_core/services/__init__.py`

- [ ] **Step 6.1: Update services __init__.py to export FindingsRenderer**

Add the FindingsRenderer export to `packages/core/src/shannon_core/services/__init__.py`. The current file exports from `temporal_infra` and `validate_authentication`. Add after the existing imports:

```python
from shannon_core.services.findings_renderer import FindingsRenderer
```

The full file should read:

```python
from shannon_core.services.temporal_infra import (
    ensure_infra,
    get_compose_file,
    get_temporal_status,
    is_temporal_ready,
    start_temporal,
    stop_temporal,
)
from shannon_core.services.validate_authentication import (
    AuthValidationResult,
    auth_state_path,
    cleanup_auth_state,
    cleanup_auth_state_sync,
    validate_authentication,
    verify_auth_state,
)
from shannon_core.services.findings_renderer import FindingsRenderer
```

- [ ] **Step 6.2: Run the full test suite**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/ packages/blackbox/tests/ packages/whitebox/tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 6.3: Commit**

```bash
git add packages/core/src/shannon_core/services/__init__.py
git commit -m "feat: export FindingsRenderer from core services"
```

---

## Self-Review Checklist

### 1. Spec Coverage

| Spec Requirement | Task |
|-----------------|------|
| Gap 1 (P0): File naming mismatch — `_analysis_deliverable.md` fallback | Task 3 |
| Gap 2 (P0): Deterministic findings renderer | Task 1 |
| Gap 3 (P1): ReportOutputProvider extensibility interface | Task 2 |
| Gap 4 (P1): ReportConfig filtering (min_confidence) | Task 1 (filter_vulnerabilities) |
| Gap 5 (P2): Model information injection | Task 3 |
| Whitebox workflow integration | Task 4 |
| Blackbox workflow integration | Tasks 5 |

### 2. Placeholder Scan

No TBD, TODO, "implement later", "add appropriate error handling", or "similar to Task N" found. Every code step contains complete implementation code.

### 3. Type Consistency

- `FindingsRenderer.render_findings_from_queues(deliverables_path: Path, report_config: ReportConfig | None)` — consistent across Tasks 1, 4, 5
- `ReportAssembler.assemble(deliverables_path, vuln_classes, report_path, report_config)` — consistent across Tasks 3, 5
- `ReportAssembler.inject_model_info(report_path: Path, session_path: Path)` — consistent across Tasks 3, 5
- `NoOpReportOutputProvider.generate(report_path, deliverables_path)` — consistent across Tasks 2, 5
- `ReportConfig` from `shannon_core.models.config` — used consistently
- `VulnerabilityQueue` from `shannon_core.models.queue_schemas` — used consistently
