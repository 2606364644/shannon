# Shannon-Py Blackbox Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Implement the `shannon-blackbox` package that handles runtime/DAST scanning (recon + exploitation), reusing whitebox infrastructure and matching TS project output formats.

**Architecture:** Blackbox depends on `shannon-whitebox` and `shannon-core`. It reuses `AgentExecutor`, `PromptManager`, `SessionManager`, `GitManager`, and `AuditSession` from whitebox. New components: `ExploitExecutor`, `ReconExecutor`, `ExploitationChecker`, `ReportAssembler`. Temporal handles workflow orchestration. Claude Agent SDK drives LLM agents.

**Tech Stack:** Python 3.12, Pydantic v2, Temporal Python SDK, Click, aiofiles, pytest + pytest-asyncio

---

## File Structure

### Files to modify (existing)

| File | Change |
|------|--------|
| `packages/core/src/shannon_core/models/agents.py` | Add blackbox AgentName values + AGENTS entries |
| `packages/core/src/shannon_core/models/deliverables.py` | Add evidence + report DeliverableType values + filename mappings |
| `packages/core/src/shannon_core/models/__init__.py` | Update re-exports |
| `packages/core/src/shannon_core/models/result.py` | Add BlackboxScanResult model |
| `packages/whitebox/src/shannon_whitebox/agents/executor.py` | Add `prompt_variables` parameter to `execute()` |
| `packages/whitebox/src/shannon_whitebox/agents/validators.py` | Add exploit agent support in `get_vuln_type`/`get_queue_filename` |
| `packages/whitebox/src/shannon_whitebox/prompts/manager.py` | Support extra prompt variables |
| `packages/blackbox/pyproject.toml` | Add shannon-whitebox + temporalio + click + aiofiles deps |
| `pyproject.toml` (root) | Add shannon-blackbox uv source |

### Files to create (new)

| File | Responsibility |
|------|---------------|
| `packages/blackbox/src/shannon_blackbox/__init__.py` | Package init |
| `packages/blackbox/src/shannon_blackbox/pipeline/__init__.py` | Pipeline subpackage |
| `packages/blackbox/src/shannon_blackbox/pipeline/shared.py` | BlackboxPipelineInput, BlackboxPipelineState dataclasses |
| `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | BlackboxScanWorkflow Temporal workflow |
| `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` | Temporal activity wrappers |
| `packages/blackbox/src/shannon_blackbox/agents/__init__.py` | Agents subpackage |
| `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py` | Exploit-specific executor wrapper |
| `packages/blackbox/src/shannon_blackbox/agents/recon_executor.py` | Standalone recon executor |
| `packages/blackbox/src/shannon_blackbox/services/__init__.py` | Services subpackage |
| `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py` | Queue → should_exploit decision |
| `packages/blackbox/src/shannon_blackbox/services/report_assembler.py` | Concatenate evidence → final report |
| `packages/blackbox/src/shannon_blackbox/cli/__init__.py` | CLI subpackage |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | Click CLI commands |
| `packages/blackbox/src/shannon_blackbox/worker.py` | Temporal worker entry point |
| `packages/blackbox/tests/test_exploitation_checker.py` | Unit tests |
| `packages/blackbox/tests/test_report_assembler.py` | Unit tests |
| `packages/blackbox/tests/test_executors.py` | Exploit/Recon executor tests |
| `packages/blackbox/tests/test_cli.py` | CLI smoke tests |
| `packages/blackbox/tests/test_integration.py` | Full pipeline mocked |
| `prompts/recon-blackbox.txt` | Blackbox recon prompt template |
| `prompts/injection-exploit.txt` | Injection exploit prompt |
| `prompts/xss-exploit.txt` | XSS exploit prompt |
| `prompts/auth-exploit.txt` | Auth exploit prompt |
| `prompts/ssrf-exploit.txt` | SSRF exploit prompt |
| `prompts/authz-exploit.txt` | Authz exploit prompt |
| `prompts/report-executive.txt` | Final report prompt |

---

### Task 1: Extend core AgentName enum and AGENTS registry

**Files:**
- Modify: `packages/core/src/shannon_core/models/agents.py`
- Modify: `packages/core/src/shannon_core/models/__init__.py`
- Test: `packages/core/tests/test_agents.py`

- [x] **Step 1: Write the failing tests**

Add to `packages/core/tests/test_agents.py`:

```python
def test_blackbox_agent_name_values():
    assert AgentName.RECON_BLACKBOX == "recon-blackbox"
    assert AgentName.INJECTION_EXPLOIT == "injection-exploit"
    assert AgentName.XSS_EXPLOIT == "xss-exploit"
    assert AgentName.AUTH_EXPLOIT == "auth-exploit"
    assert AgentName.SSRF_EXPLOIT == "ssrf-exploit"
    assert AgentName.AUTHZ_EXPLOIT == "authz-exploit"
    assert AgentName.REPORT == "report"

def test_blackbox_agents_in_registry():
    expected_blackbox = [
        AgentName.RECON_BLACKBOX, AgentName.INJECTION_EXPLOIT,
        AgentName.XSS_EXPLOIT, AgentName.AUTH_EXPLOIT,
        AgentName.SSRF_EXPLOIT, AgentName.AUTHZ_EXPLOIT,
        AgentName.REPORT,
    ]
    for name in expected_blackbox:
        assert name in AGENTS, f"Missing blackbox agent: {name}"

def test_exploit_agents_have_correct_prerequisites():
    for agent_name in [AgentName.INJECTION_EXPLOIT, AgentName.XSS_EXPLOIT,
                        AgentName.AUTH_EXPLOIT, AgentName.SSRF_EXPLOIT,
                        AgentName.AUTHZ_EXPLOIT]:
        defn = AGENTS[agent_name]
        assert AgentName.RECON in defn.prerequisites or AgentName.RECON_BLACKBOX in defn.prerequisites

def test_recon_blackbox_has_no_prerequisites():
    assert AGENTS[AgentName.RECON_BLACKBOX].prerequisites == []

def test_report_agent_prerequisites():
    defn = AGENTS[AgentName.REPORT]
    assert len(defn.prerequisites) > 0
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_agents.py::test_blackbox_agent_name_values -v`
Expected: FAIL — `AgentName` has no `RECON_BLACKBOX` member

- [x] **Step 3: Update AgentName enum**

In `packages/core/src/shannon_core/models/agents.py`, add new enum values after `AUTHZ_VULN`:

```python
class AgentName(str, Enum):
    PRE_RECON = "pre-recon"
    RECON = "recon"
    INJECTION_VULN = "injection-vuln"
    XSS_VULN = "xss-vuln"
    AUTH_VULN = "auth-vuln"
    SSRF_VULN = "ssrf-vuln"
    AUTHZ_VULN = "authz-vuln"
    RECON_BLACKBOX = "recon-blackbox"
    INJECTION_EXPLOIT = "injection-exploit"
    XSS_EXPLOIT = "xss-exploit"
    AUTH_EXPLOIT = "auth-exploit"
    SSRF_EXPLOIT = "ssrf-exploit"
    AUTHZ_EXPLOIT = "authz-exploit"
    REPORT = "report"
```

- [x] **Step 4: Add blackbox agent definitions to AGENTS registry**

Append to the `AGENTS` dict in the same file:

```python
    AgentName.RECON_BLACKBOX: AgentDefinition(
        name=AgentName.RECON_BLACKBOX,
        display_name="Reconnaissance (Black-Box)",
        prerequisites=[],
        prompt_template="recon-blackbox",
        deliverable_filename="recon_deliverable.md",
    ),
    AgentName.INJECTION_EXPLOIT: AgentDefinition(
        name=AgentName.INJECTION_EXPLOIT,
        display_name="Injection Exploitation",
        prerequisites=[AgentName.RECON],
        prompt_template="injection-exploit",
        deliverable_filename="injection_exploitation_evidence.md",
    ),
    AgentName.XSS_EXPLOIT: AgentDefinition(
        name=AgentName.XSS_EXPLOIT,
        display_name="XSS Exploitation",
        prerequisites=[AgentName.RECON],
        prompt_template="xss-exploit",
        deliverable_filename="xss_exploitation_evidence.md",
    ),
    AgentName.AUTH_EXPLOIT: AgentDefinition(
        name=AgentName.AUTH_EXPLOIT,
        display_name="Auth Exploitation",
        prerequisites=[AgentName.RECON],
        prompt_template="auth-exploit",
        deliverable_filename="auth_exploitation_evidence.md",
    ),
    AgentName.SSRF_EXPLOIT: AgentDefinition(
        name=AgentName.SSRF_EXPLOIT,
        display_name="SSRF Exploitation",
        prerequisites=[AgentName.RECON],
        prompt_template="ssrf-exploit",
        deliverable_filename="ssrf_exploitation_evidence.md",
    ),
    AgentName.AUTHZ_EXPLOIT: AgentDefinition(
        name=AgentName.AUTHZ_EXPLOIT,
        display_name="Authz Exploitation",
        prerequisites=[AgentName.RECON],
        prompt_template="authz-exploit",
        deliverable_filename="authz_exploitation_evidence.md",
    ),
    AgentName.REPORT: AgentDefinition(
        name=AgentName.REPORT,
        display_name="Report Generator",
        prerequisites=[AgentName.INJECTION_EXPLOIT, AgentName.XSS_EXPLOIT,
                        AgentName.AUTH_EXPLOIT, AgentName.SSRF_EXPLOIT,
                        AgentName.AUTHZ_EXPLOIT],
        prompt_template="report-executive",
        deliverable_filename="comprehensive_security_assessment_report.md",
    ),
```

- [x] **Step 5: Run tests to verify they pass**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_agents.py -v`
Expected: All PASS

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/models/agents.py packages/core/tests/test_agents.py
git commit -m "feat(core): add blackbox AgentName values and AGENTS registry entries"
```

---

### Task 2: Extend core DeliverableType and DELIVERABLE_FILENAMES

**Files:**
- Modify: `packages/core/src/shannon_core/models/deliverables.py`
- Test: `packages/core/tests/test_deliverables.py`

- [x] **Step 1: Write the failing tests**

Add to `packages/core/tests/test_deliverables.py`:

```python
def test_blackbox_deliverable_type_values():
    assert DeliverableType.INJECTION_EVIDENCE == "INJECTION_EVIDENCE"
    assert DeliverableType.XSS_EVIDENCE == "XSS_EVIDENCE"
    assert DeliverableType.AUTH_EVIDENCE == "AUTH_EVIDENCE"
    assert DeliverableType.AUTHZ_EVIDENCE == "AUTHZ_EVIDENCE"
    assert DeliverableType.SSRF_EVIDENCE == "SSRF_EVIDENCE"
    assert DeliverableType.REPORT == "REPORT"

def test_evidence_filenames_match_ts():
    assert DELIVERABLE_FILENAMES[DeliverableType.INJECTION_EVIDENCE] == "injection_exploitation_evidence.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.XSS_EVIDENCE] == "xss_exploitation_evidence.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.AUTH_EVIDENCE] == "auth_exploitation_evidence.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.AUTHZ_EVIDENCE] == "authz_exploitation_evidence.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.SSRF_EVIDENCE] == "ssrf_exploitation_evidence.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.REPORT] == "comprehensive_security_assessment_report.md"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_deliverables.py::test_blackbox_deliverable_type_values -v`
Expected: FAIL

- [x] **Step 3: Update DeliverableType enum and DELIVERABLE_FILENAMES**

Replace the entire content of `packages/core/src/shannon_core/models/deliverables.py`:

```python
from enum import Enum

class DeliverableType(str, Enum):
    CODE_ANALYSIS = "CODE_ANALYSIS"
    RECON = "RECON"
    INJECTION_ANALYSIS = "INJECTION_ANALYSIS"
    XSS_ANALYSIS = "XSS_ANALYSIS"
    AUTH_ANALYSIS = "AUTH_ANALYSIS"
    AUTHZ_ANALYSIS = "AUTHZ_ANALYSIS"
    SSRF_ANALYSIS = "SSRF_ANALYSIS"
    INJECTION_EVIDENCE = "INJECTION_EVIDENCE"
    XSS_EVIDENCE = "XSS_EVIDENCE"
    AUTH_EVIDENCE = "AUTH_EVIDENCE"
    AUTHZ_EVIDENCE = "AUTHZ_EVIDENCE"
    SSRF_EVIDENCE = "SSRF_EVIDENCE"
    REPORT = "REPORT"

DELIVERABLE_FILENAMES: dict[DeliverableType, str] = {
    DeliverableType.CODE_ANALYSIS: "pre_recon_deliverable.md",
    DeliverableType.RECON: "recon_deliverable.md",
    DeliverableType.INJECTION_ANALYSIS: "injection_analysis_deliverable.md",
    DeliverableType.XSS_ANALYSIS: "xss_analysis_deliverable.md",
    DeliverableType.AUTH_ANALYSIS: "auth_analysis_deliverable.md",
    DeliverableType.AUTHZ_ANALYSIS: "authz_analysis_deliverable.md",
    DeliverableType.SSRF_ANALYSIS: "ssrf_analysis_deliverable.md",
    DeliverableType.INJECTION_EVIDENCE: "injection_exploitation_evidence.md",
    DeliverableType.XSS_EVIDENCE: "xss_exploitation_evidence.md",
    DeliverableType.AUTH_EVIDENCE: "auth_exploitation_evidence.md",
    DeliverableType.AUTHZ_EVIDENCE: "authz_exploitation_evidence.md",
    DeliverableType.SSRF_EVIDENCE: "ssrf_exploitation_evidence.md",
    DeliverableType.REPORT: "comprehensive_security_assessment_report.md",
}
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_deliverables.py -v`
Expected: All PASS

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/models/deliverables.py packages/core/tests/test_deliverables.py
git commit -m "feat(core): add evidence and report DeliverableType values"
```

---

### Task 3: Add BlackboxScanResult to core and update barrel exports

**Files:**
- Modify: `packages/core/src/shannon_core/models/result.py`
- Modify: `packages/core/src/shannon_core/models/__init__.py`

- [x] **Step 1: Add BlackboxScanResult model**

Append to `packages/core/src/shannon_core/models/result.py`:

```python
class BlackboxScanResult(BaseModel):
    status: str
    completed_agents: list[str]
    agent_metrics: dict[str, AgentMetrics]
    has_whitebox_results: bool = False
    error: str | None = None
    workspace_path: str | None = None
```

- [x] **Step 2: Update barrel exports in `__init__.py`**

In `packages/core/src/shannon_core/models/__init__.py`, update the import line:

```python
from .result import BlackboxScanResult, WhiteboxScanResult
```

And add `"BlackboxScanResult"` to the `__all__` list.

- [x] **Step 3: Run all core tests**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/core/tests/ -v`
Expected: All PASS

- [x] **Step 4: Commit**

```bash
git add packages/core/src/shannon_core/models/result.py packages/core/src/shannon_core/models/__init__.py
git commit -m "feat(core): add BlackboxScanResult model"
```

---

### Task 4: Extend whitebox AgentExecutor with prompt_variables support

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/agents/executor.py`
- Modify: `packages/whitebox/src/shannon_whitebox/prompts/manager.py`

- [x] **Step 1: Add prompt_variables parameter to AgentExecutor.execute()**

In `packages/whitebox/src/shannon_whitebox/agents/executor.py`, change the `execute` method signature to add the parameter, and pass it through:

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
    ) -> AgentMetrics:
```

Inside the method, merge the extra variables into the prompt call. Find the `self.prompt_manager.load_sync(...)` call and update it:

```python
        variables = {"web_url": web_url, "repo_path": str(repo)}
        if prompt_variables:
            variables.update(prompt_variables)
        prompt = self.prompt_manager.load_sync(
            defn.prompt_template,
            variables=variables,
            config=distributed,
            pipeline_testing=pipeline_testing,
        )
```

The rest of the method stays the same.

- [x] **Step 2: Update PromptManager to handle extra variables**

The existing `PromptManager._interpolate()` only handles hardcoded tokens. Add a final pass that interpolates any remaining `{{KEY}}` patterns from the `variables` dict, so `prompt_variables` like `vulnerability_entries` → `{{VULNERABILITY_ENTRIES}}` work.

In `packages/whitebox/src/shannon_whitebox/prompts/manager.py`, add after the `{{LOGIN_INSTRUCTIONS}}` line (before the newline collapse) inside `_interpolate()`:

```python
        result = result.replace("{{LOGIN_INSTRUCTIONS}}", "")

        for key, value in variables.items():
            token = "{{" + key.upper() + "}}"
            if token in result:
                result = result.replace(token, value)

        result = re.sub(r"\n{3,}", "\n\n", result)
```

This runs after all hardcoded replacements, so specific tokens take precedence. Extra variables from `prompt_variables` get interpolated by their uppercase key name.

- [x] **Step 3: Run existing whitebox tests to verify no regressions**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/ -v`
Expected: All PASS (the new parameter is optional with default `None`)

- [x] **Step 4: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/agents/executor.py
git commit -m "feat(whitebox): add prompt_variables parameter to AgentExecutor.execute()"
```

---

### Task 5: Update whitebox validators for exploit agents

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/agents/validators.py`
- Test: `packages/whitebox/tests/test_validators.py`

- [x] **Step 1: Write the failing tests**

Add to `packages/whitebox/tests/test_validators.py`:

```python
def test_get_vuln_type_exploit_agents():
    assert get_vuln_type(AgentName.INJECTION_EXPLOIT) == "injection"
    assert get_vuln_type(AgentName.XSS_EXPLOIT) == "xss"
    assert get_vuln_type(AgentName.AUTH_EXPLOIT) == "auth"
    assert get_vuln_type(AgentName.SSRF_EXPLOIT) == "ssrf"
    assert get_vuln_type(AgentName.AUTHZ_EXPLOIT) == "authz"

def test_get_queue_filename_exploit_agents():
    assert get_queue_filename(AgentName.INJECTION_EXPLOIT) == "injection_exploitation_queue.json"
    assert get_queue_filename(AgentName.XSS_EXPLOIT) == "xss_exploitation_queue.json"
    assert get_queue_filename(AgentName.AUTH_EXPLOIT) == "auth_exploitation_queue.json"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/test_validators.py::test_get_vuln_type_exploit_agents -v`
Expected: FAIL — `get_vuln_type` doesn't handle `-exploit` suffix

- [x] **Step 3: Update validators**

Replace the `get_vuln_type` and `get_queue_filename` functions in `packages/whitebox/src/shannon_whitebox/agents/validators.py`:

```python
def get_vuln_type(agent_name: AgentName) -> str | None:
    value = agent_name.value
    if value.endswith("-vuln"):
        return value.replace("-vuln", "")
    if value.endswith("-exploit"):
        return value.replace("-exploit", "")
    return None
```

`get_queue_filename` already calls `get_vuln_type`, so it will work for exploit agents too. No changes needed there.

- [x] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/test_validators.py -v`
Expected: All PASS

- [x] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/agents/validators.py packages/whitebox/tests/test_validators.py
git commit -m "feat(whitebox): support exploit agents in validators"
```

---

### Task 6: Update blackbox pyproject.toml and root workspace

**Files:**
- Modify: `packages/blackbox/pyproject.toml`
- Modify: `pyproject.toml` (root)

- [x] **Step 1: Update blackbox pyproject.toml**

Replace `packages/blackbox/pyproject.toml`:

```toml
[project]
name = "shannon-blackbox"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "shannon-core",
    "shannon-whitebox",
    "temporalio>=1.0",
    "click>=8.0",
    "aiofiles>=23.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/shannon_blackbox"]
```

- [x] **Step 2: Update root pyproject.toml to add shannon-blackbox source**

In `pyproject.toml` (root), add the blackbox source:

```toml
[tool.uv.sources]
shannon-core = { path = "packages/core" }
shannon-whitebox = { path = "packages/whitebox" }
shannon-blackbox = { path = "packages/blackbox" }
```

- [x] **Step 3: Create blackbox package directory structure**

```bash
mkdir -p packages/blackbox/src/shannon_blackbox/{pipeline,agents,services,cli}
mkdir -p packages/blackbox/tests
touch packages/blackbox/src/shannon_blackbox/__init__.py
touch packages/blackbox/src/shannon_blackbox/pipeline/__init__.py
touch packages/blackbox/src/shannon_blackbox/agents/__init__.py
touch packages/blackbox/src/shannon_blackbox/services/__init__.py
touch packages/blackbox/src/shannon_blackbox/cli/__init__.py
```

- [x] **Step 4: Commit**

```bash
git add packages/blackbox/ pyproject.toml
git commit -m "feat(blackbox): set up package structure and dependencies"
```

---

### Task 7: Implement ExploitationChecker service

**Files:**
- Create: `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py`
- Test: `packages/blackbox/tests/test_exploitation_checker.py`

- [x] **Step 1: Write the failing tests**

Create `packages/blackbox/tests/test_exploitation_checker.py`:

```python
import json
import pytest
from pathlib import Path

from shannon_blackbox.services.exploitation_checker import ExploitationChecker


@pytest.mark.asyncio
async def test_should_exploit_with_vulnerabilities(tmp_path):
    queue_data = {"vulnerabilities": [
        {"ID": "INJ-001", "vulnerability_type": "SQL Injection", "externally_exploitable": True, "confidence": "high"},
    ]}
    (tmp_path / "injection_exploitation_queue.json").write_text(json.dumps(queue_data))
    assert await ExploitationChecker.should_exploit(tmp_path, "injection") is True


@pytest.mark.asyncio
async def test_should_exploit_empty_queue(tmp_path):
    queue_data = {"vulnerabilities": []}
    (tmp_path / "xss_exploitation_queue.json").write_text(json.dumps(queue_data))
    assert await ExploitationChecker.should_exploit(tmp_path, "xss") is False


@pytest.mark.asyncio
async def test_should_exploit_missing_file(tmp_path):
    assert await ExploitationChecker.should_exploit(tmp_path, "auth") is False


@pytest.mark.asyncio
async def test_should_exploit_disabled(tmp_path):
    queue_data = {"vulnerabilities": [
        {"ID": "INJ-001", "vulnerability_type": "SQL Injection", "externally_exploitable": True, "confidence": "high"},
    ]}
    (tmp_path / "injection_exploitation_queue.json").write_text(json.dumps(queue_data))
    assert await ExploitationChecker.should_exploit(tmp_path, "injection", exploit_enabled=False) is False


@pytest.mark.asyncio
async def test_should_exploit_invalid_json(tmp_path):
    (tmp_path / "ssrf_exploitation_queue.json").write_text("not json")
    assert await ExploitationChecker.should_exploit(tmp_path, "ssrf") is False
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/test_exploitation_checker.py -v`
Expected: FAIL — module not found

- [x] **Step 3: Implement ExploitationChecker**

Create `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py`:

```python
import json
from pathlib import Path

from shannon_core.models.agents import VulnType
from shannon_core.utils.file_io import async_path_exists, async_read_file


class ExploitationChecker:
    @staticmethod
    async def should_exploit(
        deliverables_path: Path,
        vuln_type: VulnType,
        exploit_enabled: bool = True,
    ) -> bool:
        if not exploit_enabled:
            return False
        queue_path = deliverables_path / f"{vuln_type}_exploitation_queue.json"
        if not await async_path_exists(queue_path):
            return False
        try:
            content = await async_read_file(queue_path)
            data = json.loads(content)
        except (json.JSONDecodeError, OSError):
            return False
        return len(data.get("vulnerabilities", [])) > 0
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/test_exploitation_checker.py -v`
Expected: All PASS

- [x] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py packages/blackbox/tests/test_exploitation_checker.py
git commit -m "feat(blackbox): implement ExploitationChecker service"
```

---

### Task 8: Implement ReportAssembler service

**Files:**
- Create: `packages/blackbox/src/shannon_blackbox/services/report_assembler.py`
- Test: `packages/blackbox/tests/test_report_assembler.py`

- [x] **Step 1: Write the failing tests**

Create `packages/blackbox/tests/test_report_assembler.py`:

```python
import pytest
from pathlib import Path

from shannon_blackbox.services.report_assembler import ReportAssembler


@pytest.mark.asyncio
async def test_assemble_prefers_evidence_over_findings(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_evidence.md").write_text("# Injection Evidence")
    (deliverables / "injection_findings.md").write_text("# Injection Findings")
    report_path = tmp_path / "report.md"

    await ReportAssembler.assemble(deliverables, ["injection"], report_path)
    content = report_path.read_text()
    assert "Injection Evidence" in content
    assert "Injection Findings" not in content


@pytest.mark.asyncio
async def test_assemble_falls_back_to_findings(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "xss_findings.md").write_text("# XSS Findings")
    report_path = tmp_path / "report.md"

    await ReportAssembler.assemble(deliverables, ["xss"], report_path)
    content = report_path.read_text()
    assert "XSS Findings" in content


@pytest.mark.asyncio
async def test_assemble_multiple_vuln_classes(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_evidence.md").write_text("# Injection")
    (deliverables / "xss_exploitation_evidence.md").write_text("# XSS")
    report_path = tmp_path / "report.md"

    await ReportAssembler.assemble(deliverables, ["injection", "xss"], report_path)
    content = report_path.read_text()
    assert "Injection" in content
    assert "XSS" in content


@pytest.mark.asyncio
async def test_assemble_skips_missing_classes(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    report_path = tmp_path / "report.md"

    await ReportAssembler.assemble(deliverables, ["auth", "authz"], report_path)
    assert report_path.exists()
    content = report_path.read_text()
    assert content.strip() == ""


@pytest.mark.asyncio
async def test_assemble_with_no_vuln_classes(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    report_path = tmp_path / "report.md"

    await ReportAssembler.assemble(deliverables, [], report_path)
    assert report_path.exists()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/test_report_assembler.py -v`
Expected: FAIL — module not found

- [x] **Step 3: Implement ReportAssembler**

Create `packages/blackbox/src/shannon_blackbox/services/report_assembler.py`:

```python
from pathlib import Path

from shannon_core.models.agents import VulnType
from shannon_core.utils.file_io import async_path_exists, async_read_file, async_write_file


class ReportAssembler:
    @staticmethod
    async def assemble(
        deliverables_path: Path,
        vuln_classes: list[VulnType],
        report_path: Path,
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

- [x] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/test_report_assembler.py -v`
Expected: All PASS

- [x] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/services/report_assembler.py packages/blackbox/tests/test_report_assembler.py
git commit -m "feat(blackbox): implement ReportAssembler service"
```

---

### Task 9: Implement ExploitExecutor and ReconExecutor

**Files:**
- Create: `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`
- Create: `packages/blackbox/src/shannon_blackbox/agents/recon_executor.py`
- Test: `packages/blackbox/tests/test_executors.py`

- [x] **Step 1: Write the failing tests**

Create `packages/blackbox/tests/test_executors.py`:

```python
import json
import subprocess
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from shannon_core.models.agents import AgentName, VulnType
from shannon_core.models.metrics import AgentMetrics
from shannon_blackbox.agents.exploit_executor import ExploitExecutor
from shannon_blackbox.agents.recon_executor import ReconExecutor


@pytest.fixture
def mock_repo(tmp_path):
    repo = tmp_path / "target-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)
    (repo / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)
    deliverables = repo / ".shannon" / "deliverables"
    deliverables.mkdir(parents=True)
    return repo, deliverables


@pytest.mark.asyncio
async def test_exploit_executor_reads_queue(mock_repo):
    repo, deliverables = mock_repo
    queue_data = {"vulnerabilities": [
        {"ID": "INJ-001", "vulnerability_type": "SQL Injection",
         "externally_exploitable": True, "confidence": "high",
         "source_endpoint": "/api/search", "sink_call": "db.execute"},
    ]}
    (deliverables / "injection_exploitation_queue.json").write_text(json.dumps(queue_data))
    (deliverables / "injection_exploitation_evidence.md").write_text("# Evidence")

    mock_executor = AsyncMock()
    mock_executor.execute.return_value = AgentMetrics(duration_ms=1000, cost_usd=0.01, num_turns=3)

    ex = ExploitExecutor(mock_executor)
    metrics = await ex.execute(
        agent_name=AgentName.INJECTION_EXPLOIT,
        vuln_type="injection",
        workspace_path=repo,
        deliverables_path=deliverables,
        web_url="https://example.com",
    )
    assert isinstance(metrics, AgentMetrics)
    mock_executor.execute.assert_called_once()
    call_kwargs = mock_executor.execute.call_args
    assert call_kwargs.kwargs["agent_name"] == AgentName.INJECTION_EXPLOIT
    assert "vulnerability_entries" in call_kwargs.kwargs.get("prompt_variables", {})


@pytest.mark.asyncio
async def test_recon_executor_delegates(mock_repo):
    repo, deliverables = mock_repo
    (deliverables / "recon_deliverable.md").write_text("# Recon")

    mock_executor = AsyncMock()
    mock_executor.execute.return_value = AgentMetrics(duration_ms=2000, cost_usd=0.02, num_turns=5)

    recon = ReconExecutor(mock_executor)
    metrics = await recon.execute(
        workspace_path=repo,
        deliverables_path=deliverables,
        web_url="https://example.com",
    )
    assert isinstance(metrics, AgentMetrics)
    mock_executor.execute.assert_called_once_with(
        agent_name=AgentName.RECON_BLACKBOX,
        repo_path=str(deliverables),
        web_url="https://example.com",
        deliverables_path=str(deliverables),
        config_path=None,
        api_key=None,
        pipeline_testing=False,
    )
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/test_executors.py -v`
Expected: FAIL — module not found

- [x] **Step 3: Implement ExploitExecutor**

Create `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`:

```python
import json
from pathlib import Path

from shannon_core.models.agents import AgentName, VulnType
from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.models.metrics import AgentMetrics
from shannon_core.utils.file_io import async_path_exists, async_read_file

from shannon_whitebox.agents.executor import AgentExecutor


class ExploitExecutor:
    def __init__(self, agent_executor: AgentExecutor):
        self._executor = agent_executor

    async def execute(
        self,
        agent_name: AgentName,
        vuln_type: VulnType,
        workspace_path: Path,
        deliverables_path: Path,
        web_url: str,
        config_path: str | None = None,
        api_key: str | None = None,
        pipeline_testing: bool = False,
    ) -> AgentMetrics:
        queue_path = deliverables_path / f"{vuln_type}_exploitation_queue.json"
        prompt_variables: dict[str, str] = {}
        if await async_path_exists(queue_path):
            content = await async_read_file(queue_path)
            prompt_variables["vulnerability_entries"] = content
        return await self._executor.execute(
            agent_name=agent_name,
            repo_path=str(deliverables_path),
            web_url=web_url,
            deliverables_path=str(deliverables_path),
            config_path=config_path,
            api_key=api_key,
            pipeline_testing=pipeline_testing,
            prompt_variables=prompt_variables,
        )
```

- [x] **Step 4: Implement ReconExecutor**

Create `packages/blackbox/src/shannon_blackbox/agents/recon_executor.py`:

```python
from pathlib import Path

from shannon_core.models.agents import AgentName
from shannon_core.models.metrics import AgentMetrics

from shannon_whitebox.agents.executor import AgentExecutor


class ReconExecutor:
    def __init__(self, agent_executor: AgentExecutor):
        self._executor = agent_executor

    async def execute(
        self,
        workspace_path: Path,
        deliverables_path: Path,
        web_url: str,
        config_path: str | None = None,
        api_key: str | None = None,
        pipeline_testing: bool = False,
    ) -> AgentMetrics:
        return await self._executor.execute(
            agent_name=AgentName.RECON_BLACKBOX,
            repo_path=str(deliverables_path),
            web_url=web_url,
            deliverables_path=str(deliverables_path),
            config_path=config_path,
            api_key=api_key,
            pipeline_testing=pipeline_testing,
        )
```

- [x] **Step 5: Run tests to verify they pass**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/test_executors.py -v`
Expected: All PASS

- [x] **Step 6: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/agents/ packages/blackbox/tests/test_executors.py
git commit -m "feat(blackbox): implement ExploitExecutor and ReconExecutor"
```

---

### Task 10: Create blackbox pipeline shared models

**Files:**
- Create: `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`

- [x] **Step 1: Create pipeline shared dataclasses**

Create `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`:

```python
from dataclasses import dataclass, field

from shannon_core.models.agents import VulnType
from shannon_core.models.metrics import AgentMetrics


@dataclass
class BlackboxPipelineInput:
    web_url: str
    workspace_name: str | None = None
    config_path: str | None = None
    output_path: str | None = None
    repo_path: str | None = None
    resume_from_workspace: str | None = None
    vuln_classes: list[VulnType] | None = None
    exploit: bool = True
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    deliverables_subdir: str = ".shannon/deliverables"


@dataclass
class BlackboxPipelineState:
    status: str = "running"
    current_phase: str | None = None
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    has_whitebox_results: bool = False
    start_time: float = 0.0
    error: str | None = None


@dataclass
class BlackboxActivityInput:
    web_url: str
    repo_path: str | None = None
    config_path: str | None = None
    workspace_name: str | None = None
    deliverables_subdir: str = ".shannon/deliverables"
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    agent_name: str | None = None
    vuln_type: str | None = None
```

- [x] **Step 2: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/shared.py
git commit -m "feat(blackbox): add pipeline shared data models"
```

---

### Task 11: Create blackbox pipeline activities

**Files:**
- Create: `packages/blackbox/src/shannon_blackbox/pipeline/activities.py`

- [x] **Step 1: Create activity wrappers**

Create `packages/blackbox/src/shannon_blackbox/pipeline/activities.py`:

```python
from pathlib import Path

from temporalio import activity

from shannon_core.models.agents import AgentName, AGENTS, VulnType
from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.models.metrics import AgentMetrics
from shannon_whitebox.agents.executor import AgentExecutor
from shannon_whitebox.prompts.manager import PromptManager

from .shared import BlackboxActivityInput


def _get_deliverables_path(input: BlackboxActivityInput) -> Path:
    if input.repo_path:
        return Path(input.repo_path) / input.deliverables_subdir
    base = Path("workspaces") / (input.workspace_name or "default")
    return base / input.deliverables_subdir


@activity.defn
async def run_blackbox_preflight(input: BlackboxActivityInput) -> None:
    pass


@activity.defn
async def run_recon(input: BlackboxActivityInput) -> dict:
    from shannon_blackbox.agents.recon_executor import ReconExecutor

    deliverables = _get_deliverables_path(input)
    deliverables.mkdir(parents=True, exist_ok=True)
    prompts_dir = Path(__file__).resolve().parents[4] / "prompts"
    prompt_manager = PromptManager(prompts_dir)
    executor = AgentExecutor(prompt_manager)
    recon = ReconExecutor(executor)
    metrics = await recon.execute(
        workspace_path=deliverables.parent,
        deliverables_path=deliverables,
        web_url=input.web_url,
        config_path=input.config_path,
        api_key=input.api_key,
        pipeline_testing=input.pipeline_testing_mode,
    )
    return metrics.model_dump()


@activity.defn
async def run_exploit_agent(input: BlackboxActivityInput) -> dict:
    from shannon_blackbox.agents.exploit_executor import ExploitExecutor

    vuln_type: VulnType = input.vuln_type
    agent_name = AgentName(f"{vuln_type}-exploit")
    deliverables = _get_deliverables_path(input)
    prompts_dir = Path(__file__).resolve().parents[4] / "prompts"
    prompt_manager = PromptManager(prompts_dir)
    executor = AgentExecutor(prompt_manager)
    exploit = ExploitExecutor(executor)
    metrics = await exploit.execute(
        agent_name=agent_name,
        vuln_type=vuln_type,
        workspace_path=deliverables.parent,
        deliverables_path=deliverables,
        web_url=input.web_url,
        config_path=input.config_path,
        api_key=input.api_key,
        pipeline_testing=input.pipeline_testing_mode,
    )
    return metrics.model_dump()


@activity.defn
async def assemble_report(input: BlackboxActivityInput) -> None:
    from shannon_blackbox.services.report_assembler import ReportAssembler

    deliverables = _get_deliverables_path(input)
    vuln_classes: list[VulnType] = ["injection", "xss", "auth", "ssrf", "authz"]
    report_path = deliverables / "comprehensive_security_assessment_report.md"
    await ReportAssembler.assemble(deliverables, vuln_classes, report_path)


@activity.defn
async def run_report_agent(input: BlackboxActivityInput) -> dict:
    deliverables = _get_deliverables_path(input)
    prompts_dir = Path(__file__).resolve().parents[4] / "prompts"
    prompt_manager = PromptManager(prompts_dir)
    executor = AgentExecutor(prompt_manager)
    metrics = await executor.execute(
        agent_name=AgentName.REPORT,
        repo_path=str(deliverables),
        web_url=input.web_url,
        deliverables_path=str(deliverables),
        config_path=input.config_path,
        api_key=input.api_key,
        pipeline_testing=input.pipeline_testing_mode,
    )
    return metrics.model_dump()
```

- [x] **Step 2: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/activities.py
git commit -m "feat(blackbox): add Temporal activity wrappers"
```

---

### Task 12: Create BlackboxScanWorkflow

**Files:**
- Create: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`

- [x] **Step 1: Create the workflow**

Create `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`:

```python
import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from shannon_core.models.agents import AgentName, ALL_VULN_CLASSES, VulnType

from .shared import BlackboxActivityInput, BlackboxPipelineInput, BlackboxPipelineState

with workflow.unsafe.imports_passed_through():
    from . import activities


@workflow.defn
class BlackboxScanWorkflow:
    def __init__(self):
        self._state = BlackboxPipelineState()

    @workflow.run
    async def run(self, input: BlackboxPipelineInput) -> BlackboxPipelineState:
        self._state.start_time = workflow.time_ns() / 1e9

        selected_classes: list[VulnType] = input.vuln_classes or list(ALL_VULN_CLASSES)

        act_input = BlackboxActivityInput(
            web_url=input.web_url,
            repo_path=input.repo_path,
            config_path=input.config_path,
            workspace_name=input.workspace_name,
            deliverables_subdir=input.deliverables_subdir,
            pipeline_testing_mode=input.pipeline_testing_mode,
            api_key=input.api_key,
        )

        retry_policy = RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=30),
            maximum_interval=timedelta(minutes=5),
            backoff_coefficient=2.0,
        )

        await workflow.execute_activity(
            activities.run_blackbox_preflight, act_input,
            start_to_close_timeout=timedelta(minutes=2),
        )

        deliverables = Path(input.repo_path or "") / input.deliverables_subdir if input.repo_path else Path("workspaces") / (input.workspace_name or "default") / input.deliverables_subdir
        has_whitebox_results = False
        for vt in selected_classes:
            queue_file = deliverables / f"{vt}_exploitation_queue.json"
            if queue_file.exists():
                has_whitebox_results = True
                break
        self._state.has_whitebox_results = has_whitebox_results

        if not has_whitebox_results and AgentName.RECON_BLACKBOX.value not in self._state.completed_agents:
            recon_input = BlackboxActivityInput(**{**act_input.__dict__})
            metrics = await workflow.execute_activity(
                activities.run_recon, recon_input,
                start_to_close_timeout=timedelta(hours=2),
                retry_policy=retry_policy,
            )
            self._state.completed_agents.append(AgentName.RECON_BLACKBOX.value)
            self._state.agent_metrics[AgentName.RECON_BLACKBOX.value] = metrics

        if input.exploit:
            from shannon_blackbox.services.exploitation_checker import ExploitationChecker

            exploit_tasks = []
            for vt in selected_classes:
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

            if exploit_tasks:
                results = await asyncio.gather(
                    *[task for _, _, task in exploit_tasks],
                    return_exceptions=True,
                )
                for i, result in enumerate(results):
                    vt, agent_name, _ = exploit_tasks[i]
                    if isinstance(result, Exception):
                        self._state.error = f"{agent_name.value}: {result}"
                    else:
                        self._state.completed_agents.append(agent_name.value)
                        self._state.agent_metrics[agent_name.value] = result

        await workflow.execute_activity(
            activities.assemble_report, act_input,
            start_to_close_timeout=timedelta(minutes=5),
        )

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

- [x] **Step 2: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/workflows.py
git commit -m "feat(blackbox): implement BlackboxScanWorkflow"
```

---

### Task 13: Create Temporal worker entry point

**Files:**
- Create: `packages/blackbox/src/shannon_blackbox/worker.py`

- [x] **Step 1: Create worker**

Create `packages/blackbox/src/shannon_blackbox/worker.py`:

```python
import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from .pipeline.activities import (
    run_blackbox_preflight,
    run_recon,
    run_exploit_agent,
    assemble_report,
    run_report_agent,
)
from .pipeline.workflows import BlackboxScanWorkflow
from .pipeline.shared import BlackboxPipelineInput

TASK_QUEUE = "shannon-blackbox"


async def run_scan(input: BlackboxPipelineInput, temporal_address: str = "localhost:7233") -> dict:
    client = await Client.connect(temporal_address)

    worker = Worker(
        client=client,
        task_queue=TASK_QUEUE,
        workflows=[BlackboxScanWorkflow],
        activities=[run_blackbox_preflight, run_recon, run_exploit_agent, assemble_report, run_report_agent],
    )

    async with worker:
        result = await client.execute_workflow(
            BlackboxScanWorkflow.run,
            input,
            id=input.workspace_name or f"blackbox-{int(asyncio.get_event_loop().time())}",
            task_queue=TASK_QUEUE,
        )
        return result


def main():
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000"
    asyncio.run(run_scan(BlackboxPipelineInput(web_url=url)))
```

- [x] **Step 2: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/worker.py
git commit -m "feat(blackbox): add Temporal worker entry point"
```

---

### Task 14: Create CLI

**Files:**
- Create: `packages/blackbox/src/shannon_blackbox/cli/main.py`
- Test: `packages/blackbox/tests/test_cli.py`

- [x] **Step 1: Write the failing tests**

Create `packages/blackbox/tests/test_cli.py`:

```python
from click.testing import CliRunner
from shannon_blackbox.cli.main import cli


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Shannon Black-Box Scanner" in result.output


def test_start_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "--help"])
    assert result.exit_code == 0
    assert "--url" in result.output


def test_workspaces_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["workspaces", "--help"])
    assert result.exit_code == 0


def test_logs_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["logs", "--help"])
    assert result.exit_code == 0
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/test_cli.py -v`
Expected: FAIL — module not found

- [x] **Step 3: Implement CLI**

Create `packages/blackbox/src/shannon_blackbox/cli/main.py`:

```python
import asyncio
from pathlib import Path

import click

from shannon_core.models.agents import ALL_VULN_CLASSES
from shannon_whitebox.session import SessionManager


@click.group()
def cli():
    """Shannon Black-Box Scanner - Runtime vulnerability verification."""


@cli.command()
@click.option("--url", required=True, help="Target URL to scan")
@click.option("-o", "--output", default=None, help="Output directory for deliverables")
@click.option("-w", "--workspace", default=None, help="Workspace name (resume if exists)")
@click.option("-c", "--config", "config_path", default=None, help="YAML configuration file")
@click.option("--vuln-classes", multiple=True, help="Vuln classes to test (default: all)")
@click.option("--no-exploit", is_flag=True, help="Skip exploitation phase")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
@click.option("--temporal-address", default="localhost:7233", help="Temporal server address")
def start(url, output, workspace, config_path, vuln_classes, no_exploit, pipeline_testing, temporal_address):
    """Start a black-box security scan."""
    from shannon_blackbox.worker import run_scan
    from shannon_blackbox.pipeline.shared import BlackboxPipelineInput

    selected = list(vuln_classes) if vuln_classes else list(ALL_VULN_CLASSES)

    input = BlackboxPipelineInput(
        web_url=url,
        workspace_name=workspace,
        config_path=config_path,
        output_path=str(Path(output).resolve()) if output else None,
        vuln_classes=selected,
        exploit=not no_exploit,
        pipeline_testing_mode=pipeline_testing,
    )
    click.echo(f"Starting black-box scan on {url}")
    result = asyncio.run(run_scan(input, temporal_address))
    if result.get("status") == "completed":
        click.echo("Scan completed successfully")
    else:
        click.echo(f"Scan failed: {result.get('error', 'unknown error')}")
        raise SystemExit(1)


@cli.command()
@click.argument("workspace_name")
def logs(workspace_name):
    """View workspace execution logs."""
    workspaces_dir = Path("workspaces")
    ws = workspaces_dir / workspace_name
    if not ws.exists():
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)
    log_file = ws / "workflow.log"
    if log_file.exists():
        click.echo(log_file.read_text())
    else:
        click.echo("No logs found")


@cli.command()
def workspaces():
    """List all workspaces."""
    mgr = SessionManager(Path("workspaces"))
    for ws in mgr.list_workspaces():
        data = mgr.get_session_data(ws)
        url = data.get("web_url", "unknown")
        agents = len(data.get("completed_agents", []))
        click.echo(f"  {ws.name}  url={url}  agents={agents}")


def main():
    cli()
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/test_cli.py -v`
Expected: All PASS

- [x] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/test_cli.py
git commit -m "feat(blackbox): add Click CLI"
```

---

### Task 15: Create prompt templates

**Files:**
- Create: `prompts/recon-blackbox.txt`
- Create: `prompts/injection-exploit.txt`
- Create: `prompts/xss-exploit.txt`
- Create: `prompts/auth-exploit.txt`
- Create: `prompts/ssrf-exploit.txt`
- Create: `prompts/authz-exploit.txt`
- Create: `prompts/report-executive.txt`

- [x] **Step 1: Create recon-blackbox.txt**

Create `prompts/recon-blackbox.txt`:

```
You are a security reconnaissance agent performing black-box analysis of a web application.

Target URL: {{WEB_URL}}

Your task is to discover and map the attack surface of this application using only external access (no source code).

Perform the following:

1. **Technology Detection**: Identify the technology stack (frameworks, server, CMS, etc.)
2. **Endpoint Discovery**: Find all accessible endpoints, API routes, and pages
3. **Input Vector Identification**: Locate all user-controllable inputs (forms, parameters, headers, cookies)
4. **Authentication Analysis**: Map the authentication flow and session management
5. **Access Control Mapping**: Identify different user roles and permission boundaries

Use browser tools and HTTP requests to explore the application systematically.

{{AUTH_CONTEXT}}
{{RULES_AVOID}}
{{RULES_FOCUS}}
{{RULES_OF_ENGAGEMENT}}

Output a comprehensive recon deliverable in markdown format covering all findings above.
Save it as `recon_deliverable.md` in the deliverables directory.
```

- [x] **Step 2: Create exploit prompt templates**

Create `prompts/injection-exploit.txt`:

```
You are an injection exploitation agent. Your task is to verify and exploit injection vulnerabilities.

Target URL: {{WEB_URL}}

Vulnerability data from white-box analysis:
{{VULNERABILITY_ENTRIES}}

For each vulnerability in the data above:
1. Verify the injection point exists by testing the identified source/sink
2. Attempt to exploit it using the suggested technique
3. Document the evidence of successful or failed exploitation

{{AUTH_CONTEXT}}
{{RULES_AVOID}}
{{RULES_FOCUS}}
{{RULES_OF_ENGAGEMENT}}

Output your findings as `injection_exploitation_evidence.md` in the deliverables directory.
Include for each vulnerability: proof of exploitation, HTTP requests/responses, and impact assessment.
```

Create `prompts/xss-exploit.txt`:

```
You are a cross-site scripting (XSS) exploitation agent. Your task is to verify and exploit XSS vulnerabilities.

Target URL: {{WEB_URL}}

Vulnerability data from white-box analysis:
{{VULNERABILITY_ENTRIES}}

For each vulnerability in the data above:
1. Verify the XSS sink exists by testing the identified source
2. Craft and execute XSS payloads appropriate for the render context
3. Document the evidence of successful or failed exploitation

{{AUTH_CONTEXT}}
{{RULES_AVOID}}
{{RULES_FOCUS}}
{{RULES_OF_ENGAGEMENT}}

Output your findings as `xss_exploitation_evidence.md` in the deliverables directory.
Include for each vulnerability: proof of exploitation, payloads used, and impact assessment.
```

Create `prompts/auth-exploit.txt`:

```
You are an authentication exploitation agent. Your task is to verify and exploit authentication vulnerabilities.

Target URL: {{WEB_URL}}

Vulnerability data from white-box analysis:
{{VULNERABILITY_ENTRIES}}

For each vulnerability in the data above:
1. Test the identified authentication weakness against the live application
2. Attempt to exploit it using the suggested technique
3. Document the evidence of successful or failed exploitation

{{AUTH_CONTEXT}}
{{RULES_AVOID}}
{{RULES_FOCUS}}
{{RULES_OF_ENGAGEMENT}}

Output your findings as `auth_exploitation_evidence.md` in the deliverables directory.
Include for each vulnerability: proof of exploitation, steps taken, and impact assessment.
```

Create `prompts/ssrf-exploit.txt`:

```
You are a server-side request forgery (SSRF) exploitation agent. Your task is to verify and exploit SSRF vulnerabilities.

Target URL: {{WEB_URL}}

Vulnerability data from white-box analysis:
{{VULNERABILITY_ENTRIES}}

For each vulnerability in the data above:
1. Test the identified SSRF vector by crafting appropriate requests
2. Attempt to access internal resources or sensitive endpoints
3. Document the evidence of successful or failed exploitation

{{AUTH_CONTEXT}}
{{RULES_AVOID}}
{{RULES_FOCUS}}
{{RULES_OF_ENGAGEMENT}}

Output your findings as `ssrf_exploitation_evidence.md` in the deliverables directory.
Include for each vulnerability: proof of exploitation, payloads used, and impact assessment.
```

Create `prompts/authz-exploit.txt`:

```
You are an authorization exploitation agent. Your task is to verify and exploit authorization vulnerabilities.

Target URL: {{WEB_URL}}

Vulnerability data from white-box analysis:
{{VULNERABILITY_ENTRIES}}

For each vulnerability in the data above:
1. Test the authorization bypass against the live application
2. Attempt to access resources or perform actions beyond the intended scope
3. Document the evidence of successful or failed exploitation

{{AUTH_CONTEXT}}
{{RULES_AVOID}}
{{RULES_FOCUS}}
{{RULES_OF_ENGAGEMENT}}

Output your findings as `authz_exploitation_evidence.md` in the deliverables directory.
Include for each vulnerability: proof of exploitation, role escalation steps, and impact assessment.
```

- [x] **Step 3: Create report-executive.txt**

Create `prompts/report-executive.txt`:

```
You are a security report analyst. Review the comprehensive security assessment report and enhance it.

Target URL: {{WEB_URL}}

Read the file `comprehensive_security_assessment_report.md` from the deliverables directory.

Your task:
1. Add an Executive Summary at the top with:
   - Overall risk rating (Critical/High/Medium/Low)
   - Total vulnerabilities found and exploited
   - Key recommendations
2. Ensure each vulnerability section has clear:
   - Severity rating
   - Reproduction steps
   - Remediation guidance
3. Remove any redundancy or inconsistencies
4. Maintain all technical evidence and findings

{{DESCRIPTION}}
{{RULES_OF_ENGAGEMENT}}

Save the enhanced report as `comprehensive_security_assessment_report.md` in the deliverables directory.
```

- [x] **Step 4: Commit**

```bash
git add prompts/recon-blackbox.txt prompts/injection-exploit.txt prompts/xss-exploit.txt prompts/auth-exploit.txt prompts/ssrf-exploit.txt prompts/authz-exploit.txt prompts/report-executive.txt
git commit -m "feat(blackbox): add prompt templates for blackbox agents"
```

---

### Task 16: Integration test

**Files:**
- Test: `packages/blackbox/tests/test_integration.py`

- [x] **Step 1: Write the integration test**

Create `packages/blackbox/tests/test_integration.py`:

```python
import json
import subprocess
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from shannon_core.models.agents import AgentName, AGENTS, ALL_VULN_CLASSES
from shannon_core.models.metrics import AgentMetrics
from shannon_whitebox.agents.executor import AgentExecutor
from shannon_whitebox.agents.runner import ClaudeRunResult
from shannon_whitebox.prompts.manager import PromptManager

from shannon_blackbox.agents.exploit_executor import ExploitExecutor
from shannon_blackbox.agents.recon_executor import ReconExecutor
from shannon_blackbox.services.exploitation_checker import ExploitationChecker
from shannon_blackbox.services.report_assembler import ReportAssembler


@pytest.fixture
def mock_repo(tmp_path):
    repo = tmp_path / "target-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)
    (repo / "README.md").write_text("# Test App")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)
    deliverables = repo / ".shannon" / "deliverables"
    deliverables.mkdir(parents=True)
    return repo, deliverables


@pytest.fixture
def prompts_dir(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "recon-blackbox.txt").write_text("Recon {{WEB_URL}}")
    for vt in ["injection", "xss", "auth", "ssrf", "authz"]:
        (prompts / f"{vt}-exploit.txt").write_text(f"Exploit {vt} {{VULNERABILITY_ENTRIES}}")
    (prompts / "report-executive.txt").write_text("Report")
    return prompts


@pytest.mark.asyncio
async def test_full_blackbox_pipeline_independent(mock_repo, prompts_dir):
    repo, deliverables = mock_repo
    web_url = "https://example.com"

    mock_result = ClaudeRunResult(text="Done", success=True, duration=1000, turns=3, cost=0.01, model="test")

    with patch("shannon_whitebox.agents.executor.run_claude_prompt", return_value=mock_result):
        pm = PromptManager(prompts_dir)
        executor = AgentExecutor(pm)

        recon = ReconExecutor(executor)
        (deliverables / "recon_deliverable.md").write_text("# Recon")
        recon_metrics = await recon.execute(
            workspace_path=repo,
            deliverables_path=deliverables,
            web_url=web_url,
        )
        assert isinstance(recon_metrics, AgentMetrics)

        for vt in ALL_VULN_CLASSES:
            queue_data = {"vulnerabilities": [
                {"ID": f"{vt.upper()}-001", "vulnerability_type": vt, "externally_exploitable": True, "confidence": "high"},
            ]}
            (deliverables / f"{vt}_exploitation_queue.json").write_text(json.dumps(queue_data))
            should = await ExploitationChecker.should_exploit(deliverables, vt)
            assert should is True

            agent_name = AgentName(f"{vt}-exploit")
            (deliverables / AGENTS[agent_name].deliverable_filename).write_text(f"# {vt} evidence")

            exploit = ExploitExecutor(executor)
            metrics = await exploit.execute(
                agent_name=agent_name,
                vuln_type=vt,
                workspace_path=repo,
                deliverables_path=deliverables,
                web_url=web_url,
            )
            assert isinstance(metrics, AgentMetrics)

        report_path = deliverables / "comprehensive_security_assessment_report.md"
        await ReportAssembler.assemble(deliverables, list(ALL_VULN_CLASSES), report_path)
        assert report_path.exists()
        content = report_path.read_text()
        for vt in ALL_VULN_CLASSES:
            assert vt in content.lower() or f"{vt} evidence" in content.lower()


@pytest.mark.asyncio
async def test_full_blackbox_pipeline_continuation(mock_repo, prompts_dir):
    repo, deliverables = mock_repo
    web_url = "https://example.com"

    for vt in ["injection"]:
        queue_data = {"vulnerabilities": [
            {"ID": "INJ-001", "vulnerability_type": "SQL Injection",
             "externally_exploitable": True, "confidence": "high",
             "source_endpoint": "/api/search"},
        ]}
        (deliverables / f"{vt}_exploitation_queue.json").write_text(json.dumps(queue_data))

    for vt in ["xss", "auth", "ssrf", "authz"]:
        (deliverables / f"{vt}_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": []}))

    has_whitebox = any(
        (deliverables / f"{vt}_exploitation_queue.json").exists()
        for vt in ALL_VULN_CLASSES
    )
    assert has_whitebox is True

    should_inject = await ExploitationChecker.should_exploit(deliverables, "injection")
    assert should_inject is True
    should_xss = await ExploitationChecker.should_exploit(deliverables, "xss")
    assert should_xss is False
```

- [x] **Step 2: Run integration tests**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/test_integration.py -v`
Expected: All PASS

- [x] **Step 3: Commit**

```bash
git add packages/blackbox/tests/test_integration.py
git commit -m "test(blackbox): add integration test for full pipeline"
```

---

### Task 17: Final verification — run all tests

- [x] **Step 1: Run the complete test suite**

Run: `cd /root/shannon-refactor/shannon-py && python -m pytest packages/*/tests/ -v`
Expected: All PASS across core, whitebox, and blackbox

- [x] **Step 2: Run ruff lint check**

Run: `cd /root/shannon-refactor/shannon-py && python -m ruff check packages/blackbox/`
Expected: No errors

- [x] **Step 3: Commit (if any lint fixes needed)**

```bash
git add -A
git commit -m "chore: lint fixes"
```
