# Remove Misconfig Vulnerability Class — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Completely remove the misconfig vulnerability detection and exploitation feature from Shannon-Py, leaving zero residual references.

**Architecture:** This is a pure deletion. Misconfig is an independent vulnerability class with its own agents (`misconfig-vuln`, `misconfig-exploit`), prompts, schema, renderer, and session mapping. We remove each layer bottom-up: prompts → schemas → config → agents → services → tests → verification.

**Tech Stack:** Python, Pydantic, pytest

---

## File Map

| Action | File | What changes |
|--------|------|-------------|
| Delete | `prompts/vuln-misconfig.txt` | Entire file (359 lines) |
| Delete | `prompts/misconfig-exploit.txt` | Entire file (383 lines) |
| Delete | `prompts/vuln-misconfig.txt.backup` | Entire file (89 lines) |
| Modify | `packages/core/src/shannon_core/models/config.py:16` | Remove `"misconfig"` from `VulnClass` and `ALL_VULN_CLASSES` |
| Modify | `packages/core/src/shannon_core/models/queue_schemas.py:59-69` | Remove `MisconfigVulnerability` class and Union member |
| Modify | `packages/core/src/shannon_core/models/agents.py:6,22-23,130-143,149,171,186,193` | Remove VulnType value, AgentName members, AGENTS entries, prerequisites, phase mappings |
| Modify | `packages/core/src/shannon_core/services/findings_renderer.py:10,138-159,198-204` | Remove import, render function, CLASS_CONFIG entry |
| Modify | `packages/core/src/shannon_core/services/playwright_config_writer.py:19` | Remove session mapping entry |
| Modify | `packages/core/tests/test_config.py:46-53` | Remove 2 misconfig tests |
| Modify | `packages/core/tests/test_queue_schemas.py:117-141` | Remove 2 misconfig tests |
| Modify | `packages/core/tests/test_agents.py:84-106` | Remove 7 misconfig tests |
| Modify | `packages/core/tests/test_findings_renderer.py:12,21,155-174` | Remove import, test function |
| Modify | `packages/core/tests/test_agent_phase_map.py:24-26` | Remove 1 test |
| Modify | `packages/blackbox/tests/test_integration.py:38` | Remove `"misconfig"` from hardcoded list |

---

### Task 1: Delete prompt files

**Files:**
- Delete: `prompts/vuln-misconfig.txt`
- Delete: `prompts/misconfig-exploit.txt`
- Delete: `prompts/vuln-misconfig.txt.backup`

- [ ] **Step 1: Delete the three prompt files**

```bash
rm prompts/vuln-misconfig.txt prompts/misconfig-exploit.txt prompts/vuln-misconfig.txt.backup
```

- [ ] **Step 2: Verify files are gone**

Run: `ls prompts/*misconfig* 2>&1`
Expected: `No such file or directory` (exit code non-zero, "No such file or directory" in stderr)

- [ ] **Step 3: Commit**

```bash
git add -u prompts/vuln-misconfig.txt prompts/misconfig-exploit.txt prompts/vuln-misconfig.txt.backup
git commit -m "chore: remove misconfig prompt files"
```

---

### Task 2: Remove misconfig from config models

**Files:**
- Modify: `packages/core/src/shannon_core/models/config.py`

- [ ] **Step 1: Edit VulnClass literal on line 16**

Change line 16 from:

```python
VulnClass = Literal["injection", "xss", "auth", "authz", "ssrf", "misconfig"]
```

to:

```python
VulnClass = Literal["injection", "xss", "auth", "authz", "ssrf"]
```

- [ ] **Step 2: Edit ALL_VULN_CLASSES on line 72**

Change line 72 from:

```python
ALL_VULN_CLASSES: list[VulnClass] = ["injection", "xss", "auth", "authz", "ssrf", "misconfig"]
```

to:

```python
ALL_VULN_CLASSES: list[VulnClass] = ["injection", "xss", "auth", "authz", "ssrf"]
```

- [ ] **Step 3: Verify module imports cleanly**

Run: `cd packages/core && python -c "from shannon_core.models.config import ALL_VULN_CLASSES; print(len(ALL_VULN_CLASSES))"`
Expected: `5`

- [ ] **Step 4: Commit**

```bash
git add packages/core/src/shannon_core/models/config.py
git commit -m "refactor(core): remove misconfig from VulnClass and ALL_VULN_CLASSES"
```

---

### Task 3: Remove MisconfigVulnerability from queue schemas

**Files:**
- Modify: `packages/core/src/shannon_core/models/queue_schemas.py`

- [ ] **Step 1: Remove MisconfigVulnerability class (lines 59–68)**

Delete these lines entirely:

```python
class MisconfigVulnerability(BaseVulnerability):
    source_endpoint: str | None = None
    vulnerable_code_location: str | None = None
    missing_defense: str | None = None
    exploitation_hypothesis: str | None = None
    suggested_exploit_technique: str | None = None
    vulnerable_parameter: str | None = None
    redirect_sink: str | None = None
    existing_validation: str | None = None
```

- [ ] **Step 2: Remove MisconfigVulnerability from Vulnerability Union (line 69)**

Change:

```python
Vulnerability = Union[InjectionVulnerability, XssVulnerability, AuthVulnerability, SsrfVulnerability, AuthzVulnerability, MisconfigVulnerability, BaseVulnerability]
```

to:

```python
Vulnerability = Union[InjectionVulnerability, XssVulnerability, AuthVulnerability, SsrfVulnerability, AuthzVulnerability, BaseVulnerability]
```

- [ ] **Step 3: Verify module imports cleanly**

Run: `cd packages/core && python -c "from shannon_core.models.queue_schemas import Vulnerability; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add packages/core/src/shannon_core/models/queue_schemas.py
git commit -m "refactor(core): remove MisconfigVulnerability from queue schemas"
```

---

### Task 4: Remove misconfig from agent definitions

**Files:**
- Modify: `packages/core/src/shannon_core/models/agents.py`

- [ ] **Step 1: Remove "misconfig" from VulnType (line 6)**

Change:

```python
VulnType = Literal["injection", "xss", "auth", "ssrf", "authz", "misconfig"]
```

to:

```python
VulnType = Literal["injection", "xss", "auth", "ssrf", "authz"]
```

- [ ] **Step 2: Remove MISCONFIG_VULN and MISCONFIG_EXPLOIT from AgentName enum (lines 22–23)**

Delete these two lines:

```python
    MISCONFIG_VULN = "misconfig-vuln"
    MISCONFIG_EXPLOIT = "misconfig-exploit"
```

- [ ] **Step 3: Remove both misconfig agent definitions from AGENTS dict (lines 130–143)**

Delete these entries entirely:

```python
    AgentName.MISCONFIG_VULN: AgentDefinition(
        name=AgentName.MISCONFIG_VULN,
        display_name="Misconfig Vuln agent",
        prerequisites=[AgentName.RECON],
        prompt_template="vuln-misconfig",
        deliverable_filename="misconfig_analysis_deliverable.md",
    ),
    AgentName.MISCONFIG_EXPLOIT: AgentDefinition(
        name=AgentName.MISCONFIG_EXPLOIT,
        display_name="Misconfig Exploitation",
        prerequisites=[AgentName.MISCONFIG_VULN],
        prompt_template="misconfig-exploit",
        deliverable_filename="misconfig_exploitation_evidence.md",
    ),
```

- [ ] **Step 4: Remove MISCONFIG_EXPLOIT from REPORT prerequisites (line 147–149)**

Change:

```python
    AgentName.REPORT: AgentDefinition(
        name=AgentName.REPORT,
        display_name="Report Generator",
        prerequisites=[AgentName.INJECTION_EXPLOIT, AgentName.XSS_EXPLOIT,
                        AgentName.AUTH_EXPLOIT, AgentName.SSRF_EXPLOIT,
                        AgentName.AUTHZ_EXPLOIT, AgentName.MISCONFIG_EXPLOIT],
```

to:

```python
    AgentName.REPORT: AgentDefinition(
        name=AgentName.REPORT,
        display_name="Report Generator",
        prerequisites=[AgentName.INJECTION_EXPLOIT, AgentName.XSS_EXPLOIT,
                        AgentName.AUTH_EXPLOIT, AgentName.SSRF_EXPLOIT,
                        AgentName.AUTHZ_EXPLOIT],
```

- [ ] **Step 5: Remove "misconfig" from ALL_VULN_CLASSES (line 171)**

Change:

```python
ALL_VULN_CLASSES: list[VulnType] = ["injection", "xss", "auth", "ssrf", "authz", "misconfig"]
```

to:

```python
ALL_VULN_CLASSES: list[VulnType] = ["injection", "xss", "auth", "ssrf", "authz"]
```

- [ ] **Step 6: Remove misconfig entries from AGENT_PHASE_MAP (lines 186, 193)**

Delete:

```python
    "misconfig-vuln": "vulnerability-analysis",
```

and:

```python
    "misconfig-exploit": "exploitation",
```

- [ ] **Step 7: Verify module imports cleanly**

Run: `cd packages/core && python -c "from shannon_core.models.agents import AGENTS, ALL_VULN_CLASSES, AGENT_PHASE_MAP; print(len(ALL_VULN_CLASSES), len(AGENTS), len(AGENT_PHASE_MAP))"`
Expected: `5` followed by two counts (should be fewer than before — 16 agents, 14 phase-map entries).

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/shannon_core/models/agents.py
git commit -m "refactor(core): remove misconfig agents from definitions and phase map"
```

---

### Task 5: Remove misconfig from services

**Files:**
- Modify: `packages/core/src/shannon_core/services/findings_renderer.py`
- Modify: `packages/core/src/shannon_core/services/playwright_config_writer.py`

- [ ] **Step 1: Remove MisconfigVulnerability from findings_renderer imports (line 10)**

Change:

```python
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
```

to:

```python
from shannon_core.models.queue_schemas import (
    AuthVulnerability,
    AuthzVulnerability,
    InjectionVulnerability,
    SsrfVulnerability,
    Vulnerability,
    VulnerabilityQueue,
    XssVulnerability,
)
```

- [ ] **Step 2: Remove render_misconfig_entry function (lines 138–159)**

Delete the entire function:

```python
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
```

- [ ] **Step 3: Remove "misconfig" from CLASS_CONFIG (lines 198–204)**

Delete the entire entry:

```python
    "misconfig": VulnClassConfig(
        heading="Security Misconfigurations",
        none_found_label="No security misconfigurations found.",
        queue_file="misconfig_exploitation_queue.json",
        findings_file="misconfig_findings.md",
        render_entry=render_misconfig_entry,
    ),
```

- [ ] **Step 4: Remove misconfig from playwright_config_writer session mapping (line 19)**

Change:

```python
AGENT_SESSION_MAPPING: dict[str, str] = {
    "injection-exploit": "agent-injection",
    "xss-exploit": "agent-xss",
    "auth-exploit": "agent-auth",
    "ssrf-exploit": "agent-ssrf",
    "authz-exploit": "agent-authz",
    "misconfig-exploit": "agent-misconfig",
}
```

to:

```python
AGENT_SESSION_MAPPING: dict[str, str] = {
    "injection-exploit": "agent-injection",
    "xss-exploit": "agent-xss",
    "auth-exploit": "agent-auth",
    "ssrf-exploit": "agent-ssrf",
    "authz-exploit": "agent-authz",
}
```

- [ ] **Step 5: Verify both modules import cleanly**

Run: `cd packages/core && python -c "from shannon_core.services.findings_renderer import CLASS_CONFIG; print(len(CLASS_CONFIG))" && python -c "from shannon_core.services.playwright_config_writer import AGENT_SESSION_MAPPING; print(len(AGENT_SESSION_MAPPING))"`
Expected: `5` (CLASS_CONFIG) and `5` (AGENT_SESSION_MAPPING)

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/services/findings_renderer.py packages/core/src/shannon_core/services/playwright_config_writer.py
git commit -m "refactor(core): remove misconfig from findings renderer and playwright config"
```

---

### Task 6: Remove misconfig test functions

**Files:**
- Modify: `packages/core/tests/test_config.py`
- Modify: `packages/core/tests/test_queue_schemas.py`
- Modify: `packages/core/tests/test_agents.py`
- Modify: `packages/core/tests/test_findings_renderer.py`
- Modify: `packages/core/tests/test_agent_phase_map.py`

- [ ] **Step 1: Remove misconfig tests from test_config.py (lines 46–53)**

Delete:

```python
def test_misconfig_in_vuln_class():
    c = Config(vuln_classes=["misconfig"])
    assert c.vuln_classes == ["misconfig"]

def test_all_vuln_classes_includes_misconfig():
    from shannon_core.models.config import ALL_VULN_CLASSES
    assert "misconfig" in ALL_VULN_CLASSES
    assert len(ALL_VULN_CLASSES) == 6
```

- [ ] **Step 2: Remove misconfig tests from test_queue_schemas.py (lines 117–141)**

Delete:

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

- [ ] **Step 3: Remove misconfig tests from test_agents.py (lines 84–106)**

Delete:

```python
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
```

- [ ] **Step 4: Remove misconfig from test_findings_renderer.py**

Remove `MisconfigVulnerability` from imports on line 12:

Change:

```python
from shannon_core.models.queue_schemas import (
    InjectionVulnerability,
    XssVulnerability,
    AuthVulnerability,
    SsrfVulnerability,
    AuthzVulnerability,
    MisconfigVulnerability,
    VulnerabilityQueue,
)
```

to:

```python
from shannon_core.models.queue_schemas import (
    InjectionVulnerability,
    XssVulnerability,
    AuthVulnerability,
    SsrfVulnerability,
    AuthzVulnerability,
    VulnerabilityQueue,
)
```

Remove `render_misconfig_entry` from imports on line 21:

Change:

```python
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
```

to:

```python
from shannon_core.services.findings_renderer import (
    render_injection_entry,
    render_xss_entry,
    render_auth_entry,
    render_authz_entry,
    render_ssrf_entry,
    filter_vulnerabilities,
    FindingsRenderer,
)
```

Delete the test function `test_render_misconfig_entry_full` (lines 155–174):

```python
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
```

- [ ] **Step 5: Remove misconfig test from test_agent_phase_map.py (lines 24–26)**

Delete:

```python
def test_misconfig_agents_mapped():
    assert AGENT_PHASE_MAP["misconfig-vuln"] == "vulnerability-analysis"
    assert AGENT_PHASE_MAP["misconfig-exploit"] == "exploitation"
```

- [ ] **Step 6: Run core tests to verify nothing is broken**

Run: `cd packages/core && python -m pytest tests/ -v`
Expected: All tests PASS. Count should be reduced by the removed tests.

- [ ] **Step 7: Commit**

```bash
git add packages/core/tests/test_config.py packages/core/tests/test_queue_schemas.py packages/core/tests/test_agents.py packages/core/tests/test_findings_renderer.py packages/core/tests/test_agent_phase_map.py
git commit -m "test(core): remove misconfig-related test functions"
```

---

### Task 7: Update integration tests

**Files:**
- Modify: `packages/blackbox/tests/test_integration.py`

- [ ] **Step 1: Remove "misconfig" from hardcoded vuln class list in prompts_dir fixture (line 38)**

Change:

```python
    for vt in ["injection", "xss", "auth", "ssrf", "authz", "misconfig"]:
```

to:

```python
    for vt in ["injection", "xss", "auth", "ssrf", "authz"]:
```

- [ ] **Step 2: Run blackbox integration tests**

Run: `cd packages/blackbox && python -m pytest tests/test_integration.py -v`
Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add packages/blackbox/tests/test_integration.py
git commit -m "test(blackbox): remove misconfig from integration tests"
```

---

### Task 8: Final verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/ -v`
Expected: All tests PASS.

- [ ] **Step 2: Grep for residual misconfig references**

Run: `grep -r "misconfig" --include="*.py" --include="*.txt" packages/ prompts/`
Expected: Zero output (no matches). If any matches remain, fix them.

- [ ] **Step 3: Verify ALL_VULN_CLASSES count is 5**

Run: `cd packages/core && python -c "from shannon_core.models.config import ALL_VULN_CLASSES; assert len(ALL_VULN_CLASSES) == 5; print('OK: 5 vuln classes')"`
Expected: `OK: 5 vuln classes`

- [ ] **Step 4: Final commit (if any fixes were needed)**

Only if Step 2 required fixes:

```bash
git add -A
git commit -m "fix: clean up residual misconfig references"
```
