# Shannon-Py Whitebox Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a Python 3.12 white-box security scanner that analyzes source code repositories and produces structured vulnerability deliverables compatible with the TypeScript Shannon version.

**Architecture:** Monorepo with `packages/core` (shared Pydantic models, config parsing, utilities) and `packages/whitebox` (Temporal-orchestrated pipeline, agent execution, CLI). The pipeline runs Pre-Recon → Recon → 5 parallel Vuln agents via Temporal activities. Each agent uses Claude Agent SDK to analyze code and produce deliverables.

**Tech Stack:** Python 3.12, Pydantic v2, Temporal Python SDK, Click, claude-agent-sdk (or anthropic SDK), PyYAML, aiofiles

**Design spec:** `docs/superpowers/specs/2026-05-27-shannon-py-whitebox-design.md`

---

## File Structure

### packages/core/src/shannon_core/

| File | Responsibility |
|------|---------------|
| `__init__.py` | Package init, re-export public API |
| `models/__init__.py` | Models package init |
| `models/config.py` | Config, Rules, Rule, ReportConfig, Authentication, Credentials, etc. |
| `models/agents.py` | AgentName enum, AgentDefinition, VulnType, AGENTS registry |
| `models/deliverables.py` | DeliverableType enum, DELIVERABLE_FILENAMES mapping |
| `models/errors.py` | ErrorCode enum, PentestError exception |
| `models/metrics.py` | AgentMetrics, SessionMetadata |
| `models/queue_schemas.py` | Pydantic models for vulnerability queue JSON |
| `models/result.py` | Scan result model |
| `config/__init__.py` | Config package init |
| `config/parser.py` | YAML parsing, Pydantic validation, security checks, distribute |
| `utils/__init__.py` | Utils package init |
| `utils/formatting.py` | Timestamp formatting, text utilities |
| `utils/file_io.py` | Async file read/write/exists helpers |
| `utils/billing.py` | Spending cap detection |
| `utils/concurrency.py` | Async concurrency limiter |

### packages/core/tests/

| File | Tests for |
|------|-----------|
| `test_config.py` | Config parsing, validation, security checks |
| `test_agents.py` | AgentName, AgentDefinition, AGENTS registry |
| `test_deliverables.py` | DeliverableType, filename mappings |
| `test_errors.py` | ErrorCode, PentestError |
| `test_metrics.py` | AgentMetrics, SessionMetadata |
| `test_queue_schemas.py` | Queue JSON model validation |
| `test_parser.py` | YAML parsing edge cases, dangerous patterns |
| `test_billing.py` | Spending cap detection |

### packages/whitebox/src/shannon_whitebox/

| File | Responsibility |
|------|---------------|
| `__init__.py` | Package init |
| `cli/__init__.py` | CLI package init |
| `cli/main.py` | Click CLI: start, status, logs, workspaces |
| `pipeline/__init__.py` | Pipeline package init |
| `pipeline/workflows.py` | Temporal workflow definition |
| `pipeline/activities.py` | Thin Temporal activity wrappers |
| `pipeline/shared.py` | PipelineInput, PipelineState, query definitions |
| `agents/__init__.py` | Agents package init |
| `agents/executor.py` | Agent lifecycle: config → prompt → execute → validate → commit |
| `agents/runner.py` | Claude Agent SDK / anthropic SDK integration |
| `agents/validators.py` | Deliverable existence validation |
| `prompts/__init__.py` | Prompts package init |
| `prompts/manager.py` | Template loading, @include, variable substitution |
| `audit/__init__.py` | Audit package init |
| `audit/session.py` | AuditSession per-agent logging |
| `audit/log_stream.py` | Append-only log stream |
| `session.py` | Workspace management, session.json |
| `git_manager.py` | Git checkpoint/rollback/commit |
| `worker.py` | Temporal worker entry point |

### packages/whitebox/tests/

| File | Tests for |
|------|-----------|
| `test_executor.py` | Agent executor lifecycle |
| `test_runner.py` | Claude runner (mocked) |
| `test_validators.py` | Deliverable validation |
| `test_prompt_manager.py` | Template loading, variable substitution |
| `test_git_manager.py` | Git operations |
| `test_session.py` | Workspace management |
| `test_workflows.py` | Temporal workflow (mocked activities) |
| `test_cli.py` | Click CLI commands |

---

## Task 1: Project Scaffolding

**Files:**
- Create: `shannon-py/pyproject.toml`
- Create: `shannon-py/packages/core/pyproject.toml`
- Create: `shannon-py/packages/whitebox/pyproject.toml`
- Create: `shannon-py/packages/blackbox/pyproject.toml`
- Create: `shannon-py/packages/core/src/shannon_core/__init__.py`
- Create: `shannon-py/packages/whitebox/src/shannon_whitebox/__init__.py`
- Create: all `__init__.py` files for sub-packages

- [ ] **Step 1: Create root pyproject.toml**

```toml
# shannon-py/pyproject.toml
[project]
name = "shannon-py"
version = "0.1.0"
requires-python = ">=3.12"

[tool.uv.sources]
shannon-core = { path = "packages/core" }
shannon-whitebox = { path = "packages/whitebox" }

[tool.uv.workspace]
members = ["packages/*"]

[tool.pytest.ini_options]
testpaths = ["packages/*/tests"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 120
target-version = "py312"
```

- [ ] **Step 2: Create packages/core/pyproject.toml**

```toml
# shannon-py/packages/core/pyproject.toml
[project]
name = "shannon-core"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.0",
    "pyyaml>=6.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/shannon_core"]
```

- [ ] **Step 3: Create packages/whitebox/pyproject.toml**

```toml
# shannon-py/packages/whitebox/pyproject.toml
[project]
name = "shannon-whitebox"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "shannon-core",
    "temporalio>=1.0",
    "click>=8.0",
    "aiofiles>=23.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/shannon_whitebox"]
```

- [ ] **Step 4: Create packages/blackbox/pyproject.toml (placeholder)**

```toml
# shannon-py/packages/blackbox/pyproject.toml
[project]
name = "shannon-blackbox"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "shannon-core",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] **Step 5: Create all __init__.py files**

Create empty `__init__.py` files for all packages and sub-packages:
- `packages/core/src/shannon_core/__init__.py`
- `packages/core/src/shannon_core/models/__init__.py`
- `packages/core/src/shannon_core/config/__init__.py`
- `packages/core/src/shannon_core/utils/__init__.py`
- `packages/whitebox/src/shannon_whitebox/__init__.py`
- `packages/whitebox/src/shannon_whitebox/cli/__init__.py`
- `packages/whitebox/src/shannon_whitebox/pipeline/__init__.py`
- `packages/whitebox/src/shannon_whitebox/agents/__init__.py`
- `packages/whitebox/src/shannon_whitebox/prompts/__init__.py`
- `packages/whitebox/src/shannon_whitebox/audit/__init__.py`

- [ ] **Step 6: Create tests directories and configs**

Create `packages/core/tests/__init__.py` and `packages/whitebox/tests/__init__.py` (empty).

- [ ] **Step 7: Verify project structure**

Run: `find shannon-py -type f -name '*.toml' -o -name '*.py' | sort`
Expected: All files from steps 1-6 listed.

- [ ] **Step 8: Commit**

```bash
git add shannon-py/
git commit -m "feat: scaffold shannon-py monorepo structure"
```

---

## Task 2: Core Error and Metrics Models

**Files:**
- Create: `packages/core/src/shannon_core/models/errors.py`
- Create: `packages/core/src/shannon_core/models/metrics.py`
- Create: `packages/core/tests/test_errors.py`
- Create: `packages/core/tests/test_metrics.py`

- [ ] **Step 1: Write failing test for ErrorCode and PentestError**

```python
# packages/core/tests/test_errors.py
from shannon_core.models.errors import ErrorCode, PentestError

def test_error_code_values():
    assert ErrorCode.CONFIG_NOT_FOUND == "CONFIG_NOT_FOUND"
    assert ErrorCode.AGENT_EXECUTION_FAILED == "AGENT_EXECUTION_FAILED"
    assert ErrorCode.API_RATE_LIMITED == "API_RATE_LIMITED"
    assert ErrorCode.SPENDING_CAP_REACHED == "SPENDING_CAP_REACHED"

def test_pentest_error_basic():
    err = PentestError("test error", "config")
    assert str(err) == "test error"
    assert err.category == "config"
    assert err.retryable is False
    assert err.error_code is None
    assert err.context == {}

def test_pentest_error_full():
    err = PentestError(
        "rate limited",
        "billing",
        retryable=True,
        error_code=ErrorCode.API_RATE_LIMITED,
        context={"agent": "injection-vuln"},
    )
    assert err.retryable is True
    assert err.error_code == ErrorCode.API_RATE_LIMITED
    assert err.context["agent"] == "injection-vuln"

def test_pentest_error_is_exception():
    err = PentestError("fail", "validation")
    assert isinstance(err, Exception)

def test_all_error_codes_exist():
    expected = [
        "CONFIG_NOT_FOUND", "CONFIG_VALIDATION_FAILED", "CONFIG_PARSE_ERROR",
        "AGENT_EXECUTION_FAILED", "OUTPUT_VALIDATION_FAILED",
        "API_RATE_LIMITED", "SPENDING_CAP_REACHED", "INSUFFICIENT_CREDITS",
        "GIT_CHECKPOINT_FAILED", "GIT_ROLLBACK_FAILED",
        "PROMPT_LOAD_FAILED", "DELIVERABLE_NOT_FOUND",
        "REPO_NOT_FOUND", "TARGET_UNREACHABLE",
        "AUTH_FAILED", "AUTH_LOGIN_FAILED", "BILLING_ERROR",
    ]
    for name in expected:
        assert hasattr(ErrorCode, name), f"Missing ErrorCode.{name}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd shannon-py && python -m pytest packages/core/tests/test_errors.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement ErrorCode and PentestError**

```python
# packages/core/src/shannon_core/models/errors.py
from enum import Enum

class ErrorCode(str, Enum):
    CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"
    CONFIG_VALIDATION_FAILED = "CONFIG_VALIDATION_FAILED"
    CONFIG_PARSE_ERROR = "CONFIG_PARSE_ERROR"
    AGENT_EXECUTION_FAILED = "AGENT_EXECUTION_FAILED"
    OUTPUT_VALIDATION_FAILED = "OUTPUT_VALIDATION_FAILED"
    API_RATE_LIMITED = "API_RATE_LIMITED"
    SPENDING_CAP_REACHED = "SPENDING_CAP_REACHED"
    INSUFFICIENT_CREDITS = "INSUFFICIENT_CREDITS"
    GIT_CHECKPOINT_FAILED = "GIT_CHECKPOINT_FAILED"
    GIT_ROLLBACK_FAILED = "GIT_ROLLBACK_FAILED"
    PROMPT_LOAD_FAILED = "PROMPT_LOAD_FAILED"
    DELIVERABLE_NOT_FOUND = "DELIVERABLE_NOT_FOUND"
    REPO_NOT_FOUND = "REPO_NOT_FOUND"
    TARGET_UNREACHABLE = "TARGET_UNREACHABLE"
    AUTH_FAILED = "AUTH_FAILED"
    AUTH_LOGIN_FAILED = "AUTH_LOGIN_FAILED"
    BILLING_ERROR = "BILLING_ERROR"

PentestErrorType = str  # "config" | "network" | "prompt" | "filesystem" | "validation" | "billing" | "unknown"

class PentestError(Exception):
    def __init__(
        self,
        message: str,
        category: PentestErrorType,
        retryable: bool = False,
        error_code: ErrorCode | None = None,
        context: dict | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.category = category
        self.retryable = retryable
        self.error_code = error_code
        self.context = context or {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd shannon-py && python -m pytest packages/core/tests/test_errors.py -v`
Expected: All PASS

- [ ] **Step 5: Write failing test for AgentMetrics and SessionMetadata**

```python
# packages/core/tests/test_metrics.py
from shannon_core.models.metrics import AgentMetrics, SessionMetadata

def test_agent_metrics_defaults():
    m = AgentMetrics(duration_ms=1000)
    assert m.duration_ms == 1000
    assert m.input_tokens is None
    assert m.output_tokens is None
    assert m.cost_usd is None
    assert m.num_turns is None
    assert m.model is None

def test_agent_metrics_full():
    m = AgentMetrics(
        duration_ms=5000,
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.05,
        num_turns=10,
        model="claude-sonnet-4-6",
    )
    assert m.cost_usd == 0.05
    assert m.model == "claude-sonnet-4-6"

def test_session_metadata():
    s = SessionMetadata(id="test-123", web_url="https://example.com")
    assert s.id == "test-123"
    assert s.web_url == "https://example.com"
    assert s.repo_path is None
    assert s.output_path is None

def test_session_metadata_extra_fields():
    s = SessionMetadata(id="test", web_url="https://x.com", custom_field="value")
    assert s.custom_field == "value"
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd shannon-py && python -m pytest packages/core/tests/test_metrics.py -v`
Expected: FAIL

- [ ] **Step 7: Implement AgentMetrics and SessionMetadata**

```python
# packages/core/src/shannon_core/models/metrics.py
from pydantic import BaseModel

class AgentMetrics(BaseModel):
    duration_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    model: str | None = None

class SessionMetadata(BaseModel):
    model_config = {"extra": "allow"}
    id: str
    web_url: str
    repo_path: str | None = None
    output_path: str | None = None
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd shannon-py && python -m pytest packages/core/tests/test_metrics.py -v`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add packages/core/src/shannon_core/models/errors.py packages/core/src/shannon_core/models/metrics.py packages/core/tests/test_errors.py packages/core/tests/test_metrics.py
git commit -m "feat(core): add error and metrics models"
```

---

## Task 3: Agent and Deliverable Models

**Files:**
- Create: `packages/core/src/shannon_core/models/agents.py`
- Create: `packages/core/src/shannon_core/models/deliverables.py`
- Create: `packages/core/src/shannon_core/models/result.py`
- Create: `packages/core/tests/test_agents.py`
- Create: `packages/core/tests/test_deliverables.py`

- [ ] **Step 1: Write failing test for agents**

```python
# packages/core/tests/test_agents.py
from shannon_core.models.agents import AgentName, AgentDefinition, AGENTS, VulnType

def test_agent_name_values():
    assert AgentName.PRE_RECON == "pre-recon"
    assert AgentName.RECON == "recon"
    assert AgentName.INJECTION_VULN == "injection-vuln"
    assert AgentName.XSS_VULN == "xss-vuln"
    assert AgentName.AUTH_VULN == "auth-vuln"
    assert AgentName.SSRF_VULN == "ssrf-vuln"
    assert AgentName.AUTHZ_VULN == "authz-vuln"

def test_agent_definition_frozen():
    defn = AgentDefinition(
        name=AgentName.PRE_RECON,
        display_name="Pre-recon",
        prerequisites=[],
        prompt_template="pre-recon-code",
        deliverable_filename="pre_recon_deliverable.md",
        model_tier="large",
    )
    assert defn.name == AgentName.PRE_RECON
    assert defn.model_tier == "large"

def test_agents_registry_has_all_whitebox_agents():
    expected = [AgentName.PRE_RECON, AgentName.RECON, AgentName.INJECTION_VULN,
                AgentName.XSS_VULN, AgentName.AUTH_VULN, AgentName.SSRF_VULN,
                AgentName.AUTHZ_VULN]
    for name in expected:
        assert name in AGENTS, f"Missing agent: {name}"

def test_agents_prerequisites_valid():
    for name, defn in AGENTS.items():
        for prereq in defn.prerequisites:
            assert prereq in AGENTS, f"Agent {name} has invalid prerequisite: {prereq}"

def test_pre_recon_has_no_prerequisites():
    assert AGENTS[AgentName.PRE_RECON].prerequisites == []

def test_recon_depends_on_pre_recon():
    assert AgentName.PRE_RECON in AGENTS[AgentName.RECON].prerequisites

def test_vuln_agents_depend_on_recon():
    for agent_name in [AgentName.INJECTION_VULN, AgentName.XSS_VULN,
                        AgentName.AUTH_VULN, AgentName.SSRF_VULN, AgentName.AUTHZ_VULN]:
        assert AgentName.RECON in AGENTS[agent_name].prerequisites, f"{agent_name} missing recon prereq"

def test_vuln_type():
    vt: VulnType = "injection"
    assert vt == "injection"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd shannon-py && python -m pytest packages/core/tests/test_agents.py -v`
Expected: FAIL

- [ ] **Step 3: Implement agent models**

```python
# packages/core/src/shannon_core/models/agents.py
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict

VulnType = Literal["injection", "xss", "auth", "ssrf", "authz"]

class AgentName(str, Enum):
    PRE_RECON = "pre-recon"
    RECON = "recon"
    INJECTION_VULN = "injection-vuln"
    XSS_VULN = "xss-vuln"
    AUTH_VULN = "auth-vuln"
    SSRF_VULN = "ssrf-vuln"
    AUTHZ_VULN = "authz-vuln"

class AgentDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: AgentName
    display_name: str
    prerequisites: list[AgentName]
    prompt_template: str
    deliverable_filename: str
    model_tier: Literal["small", "medium", "large"] = "medium"

AGENTS: dict[AgentName, AgentDefinition] = {
    AgentName.PRE_RECON: AgentDefinition(
        name=AgentName.PRE_RECON,
        display_name="Pre-recon agent",
        prerequisites=[],
        prompt_template="pre-recon-code",
        deliverable_filename="pre_recon_deliverable.md",
        model_tier="large",
    ),
    AgentName.RECON: AgentDefinition(
        name=AgentName.RECON,
        display_name="Recon agent",
        prerequisites=[AgentName.PRE_RECON],
        prompt_template="recon",
        deliverable_filename="recon_deliverable.md",
    ),
    AgentName.INJECTION_VULN: AgentDefinition(
        name=AgentName.INJECTION_VULN,
        display_name="Injection vuln agent",
        prerequisites=[AgentName.RECON],
        prompt_template="vuln-injection",
        deliverable_filename="injection_analysis_deliverable.md",
    ),
    AgentName.XSS_VULN: AgentDefinition(
        name=AgentName.XSS_VULN,
        display_name="XSS vuln agent",
        prerequisites=[AgentName.RECON],
        prompt_template="vuln-xss",
        deliverable_filename="xss_analysis_deliverable.md",
    ),
    AgentName.AUTH_VULN: AgentDefinition(
        name=AgentName.AUTH_VULN,
        display_name="Auth vuln agent",
        prerequisites=[AgentName.RECON],
        prompt_template="vuln-auth",
        deliverable_filename="auth_analysis_deliverable.md",
    ),
    AgentName.SSRF_VULN: AgentDefinition(
        name=AgentName.SSRF_VULN,
        display_name="SSRF vuln agent",
        prerequisites=[AgentName.RECON],
        prompt_template="vuln-ssrf",
        deliverable_filename="ssrf_analysis_deliverable.md",
    ),
    AgentName.AUTHZ_VULN: AgentDefinition(
        name=AgentName.AUTHZ_VULN,
        display_name="Authz vuln agent",
        prerequisites=[AgentName.RECON],
        prompt_template="vuln-authz",
        deliverable_filename="authz_analysis_deliverable.md",
    ),
}

ALL_VULN_CLASSES: list[VulnType] = ["injection", "xss", "auth", "ssrf", "authz"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd shannon-py && python -m pytest packages/core/tests/test_agents.py -v`
Expected: All PASS

- [ ] **Step 5: Write failing test for deliverables**

```python
# packages/core/tests/test_deliverables.py
from shannon_core.models.deliverables import DeliverableType, DELIVERABLE_FILENAMES

def test_deliverable_type_values():
    assert DeliverableType.CODE_ANALYSIS == "CODE_ANALYSIS"
    assert DeliverableType.RECON == "RECON"
    assert DeliverableType.INJECTION_ANALYSIS == "INJECTION_ANALYSIS"

def test_deliverable_filenames_complete():
    for dt in DeliverableType:
        assert dt in DELIVERABLE_FILENAMES, f"Missing filename for {dt}"

def test_deliverable_filenames_match_ts():
    assert DELIVERABLE_FILENAMES[DeliverableType.CODE_ANALYSIS] == "pre_recon_deliverable.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.RECON] == "recon_deliverable.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.INJECTION_ANALYSIS] == "injection_analysis_deliverable.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.XSS_ANALYSIS] == "xss_analysis_deliverable.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.AUTH_ANALYSIS] == "auth_analysis_deliverable.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.AUTHZ_ANALYSIS] == "authz_analysis_deliverable.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.SSRF_ANALYSIS] == "ssrf_analysis_deliverable.md"
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd shannon-py && python -m pytest packages/core/tests/test_deliverables.py -v`
Expected: FAIL

- [ ] **Step 7: Implement deliverable models**

```python
# packages/core/src/shannon_core/models/deliverables.py
from enum import Enum

class DeliverableType(str, Enum):
    CODE_ANALYSIS = "CODE_ANALYSIS"
    RECON = "RECON"
    INJECTION_ANALYSIS = "INJECTION_ANALYSIS"
    XSS_ANALYSIS = "XSS_ANALYSIS"
    AUTH_ANALYSIS = "AUTH_ANALYSIS"
    AUTHZ_ANALYSIS = "AUTHZ_ANALYSIS"
    SSRF_ANALYSIS = "SSRF_ANALYSIS"

DELIVERABLE_FILENAMES: dict[DeliverableType, str] = {
    DeliverableType.CODE_ANALYSIS: "pre_recon_deliverable.md",
    DeliverableType.RECON: "recon_deliverable.md",
    DeliverableType.INJECTION_ANALYSIS: "injection_analysis_deliverable.md",
    DeliverableType.XSS_ANALYSIS: "xss_analysis_deliverable.md",
    DeliverableType.AUTH_ANALYSIS: "auth_analysis_deliverable.md",
    DeliverableType.AUTHZ_ANALYSIS: "authz_analysis_deliverable.md",
    DeliverableType.SSRF_ANALYSIS: "ssrf_analysis_deliverable.md",
}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd shannon-py && python -m pytest packages/core/tests/test_deliverables.py -v`
Expected: All PASS

- [ ] **Step 9: Create result model**

```python
# packages/core/src/shannon_core/models/result.py
from pydantic import BaseModel

from .metrics import AgentMetrics

class WhiteboxScanResult(BaseModel):
    status: str
    completed_agents: list[str]
    agent_metrics: dict[str, AgentMetrics]
    error: str | None = None
    workspace_path: str | None = None
```

- [ ] **Step 10: Update models __init__.py with re-exports**

```python
# packages/core/src/shannon_core/models/__init__.py
from .agents import AGENTS, ALL_VULN_CLASSES, AgentDefinition, AgentName, VulnType
from .deliverables import DELIVERABLE_FILENAMES, DeliverableType
from .errors import ErrorCode, PentestError, PentestErrorType
from .metrics import AgentMetrics, SessionMetadata
from .queue_schemas import (
    AuthVulnerability,
    AuthzVulnerability,
    BaseVulnerability,
    InjectionVulnerability,
    SsrfVulnerability,
    VulnerabilityQueue,
    XssVulnerability,
)
from .result import WhiteboxScanResult

__all__ = [
    "AGENTS",
    "ALL_VULN_CLASSES",
    "AgentDefinition",
    "AgentMetrics",
    "AgentName",
    "AuthVulnerability",
    "AuthzVulnerability",
    "BaseVulnerability",
    "DELIVERABLE_FILENAMES",
    "DeliverableType",
    "ErrorCode",
    "InjectionVulnerability",
    "PentestError",
    "PentestErrorType",
    "SessionMetadata",
    "SsrfVulnerability",
    "VulnType",
    "VulnerabilityQueue",
    "WhiteboxScanResult",
    "XssVulnerability",
]
```

- [ ] **Step 11: Commit**

```bash
git add packages/core/
git commit -m "feat(core): add agent, deliverable, and result models"
```

---

## Task 4: Queue Schema Models

**Files:**
- Create: `packages/core/src/shannon_core/models/queue_schemas.py`
- Create: `packages/core/tests/test_queue_schemas.py`

- [ ] **Step 1: Write failing test for queue schemas**

```python
# packages/core/tests/test_queue_schemas.py
import json
from shannon_core.models.queue_schemas import (
    BaseVulnerability, InjectionVulnerability, XssVulnerability,
    AuthVulnerability, SsrfVulnerability, AuthzVulnerability,
    VulnerabilityQueue,
)

def test_base_vulnerability_required_fields():
    v = BaseVulnerability(
        ID="INJECTION-VULN-001",
        vulnerability_type="SQL Injection",
        externally_exploitable=True,
        confidence="high",
    )
    assert v.ID == "INJECTION-VULN-001"
    assert v.notes is None

def test_injection_vulnerability():
    v = InjectionVulnerability(
        ID="INJECTION-VULN-001",
        vulnerability_type="SQL Injection",
        externally_exploitable=True,
        confidence="high",
        source="user input",
        path="/api/users",
        sink_call="sqlite3.execute",
        mismatch_reason="No parameterized query",
    )
    assert v.sink_call == "sqlite3.execute"

def test_xss_vulnerability():
    v = XssVulnerability(
        ID="XSS-VULN-001",
        vulnerability_type="Reflected XSS",
        externally_exploitable=True,
        confidence="medium",
        sink_function="innerHTML",
        path="/search",
    )
    assert v.sink_function == "innerHTML"

def test_auth_vulnerability():
    v = AuthVulnerability(
        ID="AUTH-VULN-001",
        vulnerability_type="Broken Authentication",
        externally_exploitable=True,
        confidence="high",
        source_endpoint="/api/login",
        missing_defense="No rate limiting",
        exploitation_hypothesis="Brute force possible",
    )
    assert v.missing_defense == "No rate limiting"

def test_ssrf_vulnerability():
    v = SsrfVulnerability(
        ID="SSRF-VULN-001",
        vulnerability_type="SSRF",
        externally_exploitable=True,
        confidence="high",
        vulnerable_parameter="url",
    )
    assert v.vulnerable_parameter == "url"

def test_authz_vulnerability():
    v = AuthzVulnerability(
        ID="AUTHZ-VULN-001",
        vulnerability_type="IDOR",
        externally_exploitable=True,
        confidence="high",
        endpoint="/api/users/{id}",
        guard_evidence="No ownership check",
        side_effect="Access other users' data",
    )
    assert v.guard_evidence == "No ownership check"

def test_vulnerability_queue():
    queue = VulnerabilityQueue(vulnerabilities=[])
    assert len(queue.vulnerabilities) == 0

def test_vulnerability_queue_json_roundtrip():
    v = InjectionVulnerability(
        ID="INJECTION-VULN-001",
        vulnerability_type="SQL Injection",
        externally_exploitable=True,
        confidence="high",
        sink_call="execute",
    )
    queue = VulnerabilityQueue(vulnerabilities=[v])
    json_str = queue.model_dump_json(indent=2)
    parsed = json.loads(json_str)
    assert parsed["vulnerabilities"][0]["ID"] == "INJECTION-VULN-001"
    assert parsed["vulnerabilities"][0]["sink_call"] == "execute"

def test_queue_json_matches_ts_format():
    """Verify the JSON output matches the TS version's queue format."""
    v = InjectionVulnerability(
        ID="INJECTION-VULN-001",
        vulnerability_type="SQL Injection",
        externally_exploitable=True,
        confidence="high",
        source="query param",
        path="/api/search",
        sink_call="db.execute",
        mismatch_reason="String concatenation in query",
    )
    queue = VulnerabilityQueue(vulnerabilities=[v])
    data = json.loads(queue.model_dump_json())

    # These are the exact keys the TS version expects
    entry = data["vulnerabilities"][0]
    assert "ID" in entry
    assert "vulnerability_type" in entry
    assert "externally_exploitable" in entry
    assert "confidence" in entry
    assert "source" in entry
    assert "path" in entry
    assert "sink_call" in entry
    assert "mismatch_reason" in entry
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd shannon-py && python -m pytest packages/core/tests/test_queue_schemas.py -v`
Expected: FAIL

- [ ] **Step 3: Implement queue schemas**

```python
# packages/core/src/shannon_core/models/queue_schemas.py
from pydantic import BaseModel

class BaseVulnerability(BaseModel):
    ID: str
    vulnerability_type: str
    externally_exploitable: bool
    confidence: str
    notes: str | None = None

class InjectionVulnerability(BaseVulnerability):
    source: str | None = None
    combined_sources: str | None = None
    path: str | None = None
    sink_call: str | None = None
    slot_type: str | None = None
    sanitization_observed: str | None = None
    concat_occurrences: str | None = None
    verdict: str | None = None
    mismatch_reason: str | None = None
    witness_payload: str | None = None

class XssVulnerability(BaseVulnerability):
    source: str | None = None
    source_detail: str | None = None
    path: str | None = None
    sink_function: str | None = None
    render_context: str | None = None
    encoding_observed: str | None = None
    verdict: str | None = None
    mismatch_reason: str | None = None
    witness_payload: str | None = None

class AuthVulnerability(BaseVulnerability):
    source_endpoint: str | None = None
    vulnerable_code_location: str | None = None
    missing_defense: str | None = None
    exploitation_hypothesis: str | None = None
    suggested_exploit_technique: str | None = None

class SsrfVulnerability(BaseVulnerability):
    source_endpoint: str | None = None
    vulnerable_parameter: str | None = None
    vulnerable_code_location: str | None = None
    missing_defense: str | None = None
    exploitation_hypothesis: str | None = None
    suggested_exploit_technique: str | None = None

class AuthzVulnerability(BaseVulnerability):
    endpoint: str | None = None
    vulnerable_code_location: str | None = None
    role_context: str | None = None
    guard_evidence: str | None = None
    side_effect: str | None = None
    reason: str | None = None
    minimal_witness: str | None = None

class VulnerabilityQueue(BaseModel):
    vulnerabilities: list[BaseVulnerability] = []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd shannon-py && python -m pytest packages/core/tests/test_queue_schemas.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/models/queue_schemas.py packages/core/tests/test_queue_schemas.py
git commit -m "feat(core): add vulnerability queue schema models"
```

---

## Task 5: Configuration Models and Parser

**Files:**
- Create: `packages/core/src/shannon_core/models/config.py`
- Create: `packages/core/src/shannon_core/config/parser.py`
- Create: `packages/core/tests/test_config.py`
- Create: `packages/core/tests/test_parser.py`

- [ ] **Step 1: Write failing test for config models**

```python
# packages/core/tests/test_config.py
import pytest
from shannon_core.models.config import Config, Rule, Rules, ReportConfig

def test_empty_config():
    c = Config()
    assert c.rules is None
    assert c.description is None
    assert c.vuln_classes is None
    assert c.exploit is True

def test_config_with_rules():
    c = Config(rules=Rules(
        avoid=[Rule(description="skip logout", type="url_path", value="/logout")],
        focus=[Rule(description="test API", type="url_path", value="/api")],
    ))
    assert len(c.rules.avoid) == 1
    assert c.rules.avoid[0].value == "/logout"

def test_config_with_vuln_classes():
    c = Config(vuln_classes=["injection", "xss"])
    assert len(c.vuln_classes) == 2

def test_report_config():
    r = ReportConfig(min_severity="medium", min_confidence="high")
    assert r.min_severity == "medium"

def test_invalid_rule_type():
    with pytest.raises(Exception):
        Rule(description="bad", type="invalid_type", value="test")

def test_distributed_config():
    from shannon_core.config.parser import distribute_config
    c = Config(
        rules=Rules(
            avoid=[Rule(description="skip", type="url_path", value="/admin")],
        ),
        description="Test app",
        vuln_classes=["injection"],
    )
    d = distribute_config(c)
    assert d.description == "Test app"
    assert len(d.avoid) == 1
    assert d.vuln_classes == ["injection"]
    assert d.exploit is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd shannon-py && python -m pytest packages/core/tests/test_config.py -v`
Expected: FAIL

- [ ] **Step 3: Implement config models**

```python
# packages/core/src/shannon_core/models/config.py
from typing import Literal

from pydantic import BaseModel

RuleType = Literal["url_path", "subdomain", "domain", "method", "header", "parameter", "code_path"]

class Rule(BaseModel):
    description: str
    type: RuleType
    value: str

class Rules(BaseModel):
    avoid: list[Rule] = []
    focus: list[Rule] = []

VulnClass = Literal["injection", "xss", "auth", "authz", "ssrf"]
Severity = Literal["low", "medium", "high", "critical"]
Confidence = Literal["low", "medium", "high"]

class ReportConfig(BaseModel):
    min_severity: Severity | None = None
    min_confidence: Confidence | None = None
    guidance: str | None = None

class Authentication(BaseModel):
    login_type: Literal["form", "sso", "api", "basic"]
    login_url: str
    credentials: "Credentials"
    login_flow: list[str] | None = None
    success_condition: "SuccessCondition"

class Credentials(BaseModel):
    username: str
    password: str | None = None
    totp_secret: str | None = None

class SuccessCondition(BaseModel):
    type: Literal["url_contains", "element_present", "url_equals_exactly", "text_contains"]
    value: str

class PipelineConfig(BaseModel):
    retry_preset: Literal["default", "subscription"] | None = None
    max_concurrent_pipelines: int | None = None

class DistributedConfig(BaseModel):
    avoid: list[Rule]
    focus: list[Rule]
    description: str
    vuln_classes: list[VulnClass]
    exploit: bool
    report: ReportConfig
    rules_of_engagement: str
    authentication: Authentication | None = None

class Config(BaseModel):
    rules: Rules | None = None
    authentication: Authentication | None = None
    pipeline: PipelineConfig | None = None
    description: str | None = None
    vuln_classes: list[VulnClass] | None = None
    exploit: bool = True
    report: ReportConfig | None = None
    rules_of_engagement: str | None = None

ALL_VULN_CLASSES: list[VulnClass] = ["injection", "xss", "auth", "authz", "ssrf"]
```

- [ ] **Step 4: Implement config parser**

```python
# packages/core/src/shannon_core/config/parser.py
import re
from pathlib import Path

import yaml
from shannon_core.models.config import (
    ALL_VULN_CLASSES,
    Config,
    DistributedConfig,
    ReportConfig,
    Rule,
    VulnClass,
)
from shannon_core.models.errors import ErrorCode, PentestError

DANGEROUS_PATTERNS: list[re.Pattern] = [
    re.compile(r"\.\./"),
    re.compile(r"[<>]"),
    re.compile(r"javascript:", re.IGNORECASE),
    re.compile(r"data:", re.IGNORECASE),
    re.compile(r"file:", re.IGNORECASE),
]

def _check_dangerous_patterns(value: str, field: str) -> None:
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(value):
            raise PentestError(
                f"{field} contains potentially dangerous pattern: {pattern.pattern}",
                "config",
                error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
                context={"field": field, "pattern": pattern.pattern},
            )

def _validate_config_security(config: Config) -> None:
    if config.description:
        _check_dangerous_patterns(config.description, "description")
    if config.rules_of_engagement:
        _check_dangerous_patterns(config.rules_of_engagement, "rules_of_engagement")
    if config.authentication:
        _check_dangerous_patterns(config.authentication.login_url, "authentication.login_url")
        _check_dangerous_patterns(config.authentication.credentials.username, "credentials.username")

def _validate_url_path_rules(rules: list[Rule], rule_type: str) -> None:
    for i, rule in enumerate(rules):
        if rule.type == "url_path" and not rule.value.startswith("/"):
            raise PentestError(
                f"rules.{rule_type}[{i}].value for type 'url_path' must start with '/'",
                "config",
                error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
            )

def parse_config(config_path: str) -> Config:
    path = Path(config_path)
    if not path.exists():
        raise PentestError(
            f"Configuration file not found: {config_path}",
            "config",
            error_code=ErrorCode.CONFIG_NOT_FOUND,
            context={"config_path": config_path},
        )

    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise PentestError(
            "Configuration file is empty",
            "config",
            error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
        )

    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise PentestError(
            f"YAML parsing failed: {e}",
            "config",
            error_code=ErrorCode.CONFIG_PARSE_ERROR,
            context={"original_error": str(e)},
        ) from e

    if raw is None:
        raise PentestError(
            "Configuration file resulted in null after parsing",
            "config",
            error_code=ErrorCode.CONFIG_PARSE_ERROR,
        )

    try:
        config = Config.model_validate(raw)
    except Exception as e:
        raise PentestError(
            f"Configuration validation failed: {e}",
            "config",
            error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
            context={"original_error": str(e)},
        ) from e

    _validate_config_security(config)
    if config.rules:
        _validate_url_path_rules(config.rules.avoid, "avoid")
        _validate_url_path_rules(config.rules.focus, "focus")

    return config

def distribute_config(config: Config | None) -> DistributedConfig:
    if config is None:
        return DistributedConfig(
            avoid=[], focus=[], description="",
            vuln_classes=list(ALL_VULN_CLASSES), exploit=True,
            report=ReportConfig(), rules_of_engagement="",
        )

    return DistributedConfig(
        avoid=config.rules.avoid if config.rules else [],
        focus=config.rules.focus if config.rules else [],
        description=config.description or "",
        vuln_classes=config.vuln_classes if config.vuln_classes else list(ALL_VULN_CLASSES),
        exploit=config.exploit,
        report=config.report or ReportConfig(),
        rules_of_engagement=config.rules_of_engagement or "",
        authentication=config.authentication,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd shannon-py && python -m pytest packages/core/tests/test_config.py -v`
Expected: All PASS

- [ ] **Step 6: Write parser tests**

```python
# packages/core/tests/test_parser.py
import pytest
import tempfile
from pathlib import Path
from shannon_core.config.parser import parse_config, distribute_config
from shannon_core.models.config import Config
from shannon_core.models.errors import PentestError, ErrorCode

def test_parse_valid_config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
description: "Test app"
vuln_classes:
  - injection
  - xss
rules:
  avoid:
    - description: "skip logout"
      type: url_path
      value: "/logout"
""")
    config = parse_config(str(config_file))
    assert config.description == "Test app"
    assert len(config.vuln_classes) == 2

def test_parse_empty_config_file(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("")
    with pytest.raises(PentestError) as exc_info:
        parse_config(str(config_file))
    assert exc_info.value.error_code == ErrorCode.CONFIG_VALIDATION_FAILED

def test_parse_missing_file():
    with pytest.raises(PentestError) as exc_info:
        parse_config("/nonexistent/config.yaml")
    assert exc_info.value.error_code == ErrorCode.CONFIG_NOT_FOUND

def test_parse_dangerous_description(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text('description: "<script>alert(1)</script>"')
    with pytest.raises(PentestError) as exc_info:
        parse_config(str(config_file))
    assert "dangerous pattern" in str(exc_info.value.message)

def test_parse_url_path_without_slash(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
rules:
  avoid:
    - description: "bad path"
      type: url_path
      value: "no-slash"
""")
    with pytest.raises(PentestError) as exc_info:
        parse_config(str(config_file))
    assert "must start with '/'" in str(exc_info.value.message)

def test_distribute_config_none():
    d = distribute_config(None)
    assert d.description == ""
    assert len(d.vuln_classes) == 5
    assert d.exploit is True

def test_distribute_config_full():
    c = Config(description="My app", vuln_classes=["injection"])
    d = distribute_config(c)
    assert d.description == "My app"
    assert d.vuln_classes == ["injection"]
```

- [ ] **Step 7: Run parser tests**

Run: `cd shannon-py && python -m pytest packages/core/tests/test_parser.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add packages/core/
git commit -m "feat(core): add config models and YAML parser with security validation"
```

---

## Task 6: Core Utilities

**Files:**
- Create: `packages/core/src/shannon_core/utils/formatting.py`
- Create: `packages/core/src/shannon_core/utils/file_io.py`
- Create: `packages/core/src/shannon_core/utils/billing.py`
- Create: `packages/core/src/shannon_core/utils/concurrency.py`
- Create: `packages/core/tests/test_billing.py`

- [ ] **Step 1: Implement formatting utilities**

```python
# packages/core/src/shannon_core/utils/formatting.py
from datetime import datetime, timezone

def format_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()

def truncate_text(text: str, max_length: int = 200) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."
```

- [ ] **Step 2: Implement file I/O utilities**

```python
# packages/core/src/shannon_core/utils/file_io.py
import aiofiles
import aiofiles.os
from pathlib import Path

async def async_read_file(path: str | Path) -> str:
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        return await f.read()

async def async_write_file(path: str | Path, content: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(p, "w", encoding="utf-8") as f:
        await f.write(content)

async def async_path_exists(path: str | Path) -> bool:
    return await aiofiles.os.path.exists(str(path))

async def async_read_json(path: str | Path) -> dict | list:
    import json
    content = await async_read_file(path)
    return json.loads(content)

async def async_write_json(path: str | Path, data: dict | list, indent: int = 2) -> None:
    import json
    await async_write_file(path, json.dumps(data, indent=indent, ensure_ascii=False))
```

- [ ] **Step 3: Write failing test for billing detection**

```python
# packages/core/tests/test_billing.py
from shannon_core.utils.billing import is_spending_cap_behavior

def test_spending_cap_detected():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="I've reached my spending cap")

def test_spending_cap_zero_turns_zero_cost():
    assert is_spending_cap_behavior(turns=0, cost=0.0, text="spending limit reached")

def test_normal_execution_not_cap():
    assert not is_spending_cap_behavior(turns=50, cost=2.50, text="Found vulnerability in login")

def test_normal_execution_low_turns():
    assert not is_spending_cap_behavior(turns=1, cost=0.01, text="Analysis complete")

def test_spending_cap_keywords():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="budget exceeded")
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="credit limit")
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd shannon-py && python -m pytest packages/core/tests/test_billing.py -v`
Expected: FAIL

- [ ] **Step 5: Implement billing detection**

Reference the TS version at `shannon/apps/worker/src/utils/billing-detection.ts` for the exact keywords and logic.

```python
# packages/core/src/shannon_core/utils/billing.py
import re

_SPENDING_CAP_PATTERNS: list[re.Pattern] = [
    re.compile(r"spending\s+cap", re.IGNORECASE),
    re.compile(r"spending\s+limit", re.IGNORECASE),
    re.compile(r"budget\s+exceeded", re.IGNORECASE),
    re.compile(r"credit\s+limit", re.IGNORECASE),
    re.compile(r"usage\s+limit", re.IGNORECASE),
    re.compile(r"rate\s+limit", re.IGNORECASE),
]

def is_spending_cap_behavior(turns: int, cost: float, text: str) -> bool:
    if turns > 2:
        return False
    if cost > 0:
        return False
    for pattern in _SPENDING_CAP_PATTERNS:
        if pattern.search(text):
            return True
    return False
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd shannon-py && python -m pytest packages/core/tests/test_billing.py -v`
Expected: All PASS

- [ ] **Step 7: Implement concurrency utility**

```python
# packages/core/src/shannon_core/utils/concurrency.py
import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

async def run_with_concurrency_limit(
    coroutines: list[Callable[[], Awaitable[T]]],
    limit: int,
) -> list[T]:
    semaphore = asyncio.Semaphore(limit)
    results: list[T] = []

    async def run_one(fn: Callable[[], Awaitable[T]]) -> T:
        async with semaphore:
            return await fn()

    tasks = [asyncio.create_task(run_one(fn)) for fn in coroutines]
    completed = await asyncio.gather(*tasks, return_exceptions=True)
    for item in completed:
        if isinstance(item, Exception):
            raise item
        results.append(item)
    return results
```

- [ ] **Step 8: Commit**

```bash
git add packages/core/
git commit -m "feat(core): add formatting, file_io, billing, and concurrency utilities"
```

---

## Task 7: Prompt Manager

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/prompts/manager.py`
- Create: `packages/whitebox/tests/test_prompt_manager.py`

- [ ] **Step 1: Write failing test for prompt manager**

```python
# packages/whitebox/tests/test_prompt_manager.py
import pytest
from pathlib import Path
from shannon_whitebox.prompts.manager import PromptManager

@pytest.fixture
def prompts_dir(tmp_path):
    """Create a minimal prompts directory structure."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "pre-recon-code.txt").write_text("Analyze {{REPO_PATH}} for {{WEB_URL}}")
    (prompts / "recon.txt").write_text("Recon for {{WEB_URL}}")
    shared = prompts / "shared"
    shared.mkdir()
    (shared / "_target.txt").write_text("Target: {{WEB_URL}}")
    include_prompt = prompts / "with-include.txt"
    include_prompt.write_text("Header\n@include(shared/_target.txt)\nFooter")
    return prompts

def test_load_simple_template(prompts_dir):
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("pre-recon-code", {"web_url": "https://example.com", "repo_path": "/repo"})
    assert "https://example.com" in result
    assert "/repo" in result

def test_variable_substitution(prompts_dir):
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("recon", {"web_url": "https://test.com", "repo_path": "/app"})
    assert "https://test.com" in result

def test_include_directive(prompts_dir):
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("with-include", {"web_url": "https://inc.com", "repo_path": "/r"})
    assert "Target: https://inc.com" in result
    assert "Header" in result
    assert "Footer" in result

def test_missing_template_raises(prompts_dir):
    manager = PromptManager(prompts_dir)
    with pytest.raises(Exception):
        manager.load_sync("nonexistent", {"web_url": "https://x.com", "repo_path": "/r"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd shannon-py && python -m pytest packages/whitebox/tests/test_prompt_manager.py -v`
Expected: FAIL

- [ ] **Step 3: Implement prompt manager**

```python
# packages/whitebox/src/shannon_whitebox/prompts/manager.py
import re
from pathlib import Path

from shannon_core.models.config import DistributedConfig
from shannon_core.models.errors import ErrorCode, PentestError

class PromptManager:
    def __init__(self, prompts_dir: Path):
        self.prompts_dir = prompts_dir

    def load_sync(
        self,
        template_name: str,
        variables: dict[str, str],
        config: DistributedConfig | None = None,
        pipeline_testing: bool = False,
    ) -> str:
        base_dir = self.prompts_dir
        if pipeline_testing:
            base_dir = base_dir / "pipeline-testing"

        template_path = base_dir / f"{template_name}.txt"
        if not template_path.exists():
            raise PentestError(
                f"Prompt file not found: {template_path}",
                "prompt",
                error_code=ErrorCode.PROMPT_LOAD_FAILED,
                context={"template_name": template_name},
            )

        template = template_path.read_text(encoding="utf-8")
        template = self._process_includes(template, base_dir)
        template = self._interpolate(template, variables, config)
        return template

    def _process_includes(self, content: str, base_dir: Path) -> str:
        include_re = re.compile(r"@include\(([^)]+)\)")

        def replace_include(match: re.Match) -> str:
            raw_path = match.group(1)
            if not raw_path:
                return ""
            include_path = (base_dir / raw_path).resolve()
            base_resolved = base_dir.resolve()
            if not str(include_path).startswith(str(base_resolved)):
                raise PentestError(
                    f"Path traversal in @include: {raw_path}",
                    "prompt",
                    error_code=ErrorCode.PROMPT_LOAD_FAILED,
                )
            if include_path.exists():
                return include_path.read_text(encoding="utf-8")
            return ""

        return include_re.sub(replace_include, content)

    def _interpolate(
        self,
        template: str,
        variables: dict[str, str],
        config: DistributedConfig | None,
    ) -> str:
        result = template
        result = result.replace("{{WEB_URL}}", variables.get("web_url", ""))
        result = result.replace("{{REPO_PATH}}", variables.get("repo_path", ""))
        result = result.replace("{{PLAYWRIGHT_SESSION}}", variables.get("playwright_session", "agent1"))

        if config:
            result = result.replace("{{DESCRIPTION}}", f"Description: {config.description}" if config.description else "")
            result = result.replace("{{AUTH_CONTEXT}}", "No authentication configured" if not config.authentication else f"Login type: {config.authentication.login_type}")
            avoid_str = "\n".join(f"- {r.description}" for r in config.avoid) if config.avoid else "None"
            focus_str = "\n".join(f"- {r.description}" for r in config.focus) if config.focus else "None"
            result = result.replace("{{RULES_AVOID}}", avoid_str)
            result = result.replace("{{RULES_FOCUS}}", focus_str)
            result = result.replace("{{VULN_CLASSES_TESTED}}", ", ".join(config.vuln_classes) if config.vuln_classes else "injection, xss, auth, authz, ssrf")
            result = result.replace("{{EXPLOITATION}}", "enabled" if config.exploit else "disabled")
            roe = config.rules_of_engagement.strip() if config.rules_of_engagement else ""
            result = result.replace("{{RULES_OF_ENGAGEMENT}}", roe)
        else:
            result = result.replace("{{DESCRIPTION}}", "")
            result = result.replace("{{AUTH_CONTEXT}}", "No authentication configured")
            result = result.replace("{{RULES_AVOID}}", "None")
            result = result.replace("{{RULES_FOCUS}}", "None")
            result = result.replace("{{VULN_CLASSES_TESTED}}", "injection, xss, auth, authz, ssrf")
            result = result.replace("{{EXPLOITATION}}", "enabled")
            result = result.replace("{{RULES_OF_ENGAGEMENT}}", "")

        result = result.replace("{{LOGIN_INSTRUCTIONS}}", "")

        # Collapse 3+ consecutive newlines
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd shannon-py && python -m pytest packages/whitebox/tests/test_prompt_manager.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/prompts/ packages/whitebox/tests/test_prompt_manager.py
git commit -m "feat(whitebox): add prompt manager with template loading and variable substitution"
```

---

## Task 8: Git Manager

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/git_manager.py`
- Create: `packages/whitebox/tests/test_git_manager.py`

- [ ] **Step 1: Write failing test for git manager**

```python
# packages/whitebox/tests/test_git_manager.py
import pytest
from pathlib import Path
from shannon_whitebox.git_manager import GitManager

@pytest.fixture
def git_repo(tmp_path):
    """Create a git repo with initial commit."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)
    (repo / "initial.txt").write_text("initial")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, capture_output=True, check=True)
    return repo

def test_create_checkpoint(git_repo):
    GitManager.create_checkpoint(git_repo, "pre-recon", 1)
    # Should not raise

def test_commit_success(git_repo):
    (git_repo / "deliverable.md").write_text("# Report")
    GitManager.commit(git_repo, "pre-recon")
    # File should be committed
    import subprocess
    result = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=git_repo, capture_output=True, text=True, check=True,
    )
    assert "pre-recon" in result.stdout

def test_rollback(git_repo):
    (git_repo / "bad_file.txt").write_text("bad content")
    import subprocess
    subprocess.run(["git", "add", "."], cwd=git_repo, capture_output=True, check=True)
    GitManager.rollback(git_repo, "test failure")
    assert not (git_repo / "bad_file.txt").exists() or (git_repo / "bad_file.txt").read_text() == ""

def test_get_commit_hash(git_repo):
    h = GitManager.get_commit_hash(git_repo)
    assert h is not None
    assert len(h) == 40
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd shannon-py && python -m pytest packages/whitebox/tests/test_git_manager.py -v`
Expected: FAIL

- [ ] **Step 3: Implement git manager**

```python
# packages/whitebox/src/shannon_whitebox/git_manager.py
import subprocess
from pathlib import Path

from shannon_core.models.agents import AgentName
from shannon_core.models.errors import ErrorCode, PentestError

class GitManager:
    @staticmethod
    def _run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        return result

    @staticmethod
    def create_checkpoint(repo_path: Path, agent_name: str | AgentName, attempt: int = 1) -> None:
        name = agent_name.value if isinstance(agent_name, AgentName) else agent_name
        result = GitManager._run_git(repo_path, "add", "-A")
        result = GitManager._run_git(repo_path, "commit", "-m", f"checkpoint: before {name} (attempt {attempt})", "--allow-empty")
        if result.returncode != 0:
            raise PentestError(
                f"Git checkpoint failed for {name}: {result.stderr}",
                "filesystem",
                error_code=ErrorCode.GIT_CHECKPOINT_FAILED,
            )

    @staticmethod
    def commit(repo_path: Path, agent_name: str | AgentName) -> None:
        name = agent_name.value if isinstance(agent_name, AgentName) else agent_name
        GitManager._run_git(repo_path, "add", "-A")
        result = GitManager._run_git(repo_path, "commit", "-m", f"deliverable: {name}", "--allow-empty")
        if result.returncode != 0:
            raise PentestError(
                f"Git commit failed for {name}: {result.stderr}",
                "filesystem",
                error_code=ErrorCode.GIT_CHECKPOINT_FAILED,
            )

    @staticmethod
    def rollback(repo_path: Path, reason: str) -> None:
        GitManager._run_git(repo_path, "reset", "--hard", "HEAD")
        result = GitManager._run_git(repo_path, "clean", "-fd")
        if result.returncode != 0:
            raise PentestError(
                f"Git rollback failed: {result.stderr}",
                "filesystem",
                error_code=ErrorCode.GIT_ROLLBACK_FAILED,
                context={"reason": reason},
            )

    @staticmethod
    def get_commit_hash(repo_path: Path) -> str | None:
        result = GitManager._run_git(repo_path, "rev-parse", "HEAD")
        if result.returncode == 0:
            return result.stdout.strip()
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd shannon-py && python -m pytest packages/whitebox/tests/test_git_manager.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/git_manager.py packages/whitebox/tests/test_git_manager.py
git commit -m "feat(whitebox): add git manager for checkpoint/rollback/commit"
```

---

## Task 9: Agent Executor

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/agents/runner.py`
- Create: `packages/whitebox/src/shannon_whitebox/agents/executor.py`
- Create: `packages/whitebox/src/shannon_whitebox/agents/validators.py`
- Create: `packages/whitebox/tests/test_validators.py`
- Create: `packages/whitebox/tests/test_executor.py`

- [ ] **Step 1: Implement validators**

```python
# packages/whitebox/src/shannon_whitebox/agents/validators.py
from pathlib import Path

from shannon_core.models.agents import AgentName, AGENTS
from shannon_core.models.errors import ErrorCode, PentestError

async def validate_deliverable(deliverables_path: Path, agent_name: AgentName) -> bool:
    defn = AGENTS[agent_name]
    deliverable_file = deliverables_path / defn.deliverable_filename
    if not deliverable_file.exists():
        raise PentestError(
            f"Missing deliverable: {defn.deliverable_filename}",
            "validation",
            error_code=ErrorCode.OUTPUT_VALIDATION_FAILED,
            context={"agent_name": agent_name.value, "expected_file": defn.deliverable_filename},
        )
    return True

def get_vuln_type(agent_name: AgentName) -> str | None:
    value = agent_name.value
    if value.endswith("-vuln"):
        return value.replace("-vuln", "")
    return None

def get_queue_filename(agent_name: AgentName) -> str | None:
    vuln_type = get_vuln_type(agent_name)
    if vuln_type:
        return f"{vuln_type}_exploitation_queue.json"
    return None
```

- [ ] **Step 2: Write failing test for validators**

```python
# packages/whitebox/tests/test_validators.py
import pytest
from pathlib import Path
from shannon_core.models.agents import AgentName
from shannon_whitebox.agents.validators import validate_deliverable, get_vuln_type, get_queue_filename

def test_get_vuln_type():
    assert get_vuln_type(AgentName.INJECTION_VULN) == "injection"
    assert get_vuln_type(AgentName.XSS_VULN) == "xss"
    assert get_vuln_type(AgentName.PRE_RECON) is None

def test_get_queue_filename():
    assert get_queue_filename(AgentName.INJECTION_VULN) == "injection_exploitation_queue.json"
    assert get_queue_filename(AgentName.AUTH_VULN) == "auth_exploitation_queue.json"
    assert get_queue_filename(AgentName.PRE_RECON) is None

@pytest.mark.asyncio
async def test_validate_deliverable_exists(tmp_path):
    (tmp_path / "pre_recon_deliverable.md").write_text("# Analysis")
    assert await validate_deliverable(tmp_path, AgentName.PRE_RECON)

@pytest.mark.asyncio
async def test_validate_deliverable_missing(tmp_path):
    with pytest.raises(Exception, match="Missing deliverable"):
        await validate_deliverable(tmp_path, AgentName.PRE_RECON)
```

- [ ] **Step 3: Run test to verify it passes**

Run: `cd shannon-py && python -m pytest packages/whitebox/tests/test_validators.py -v`
Expected: All PASS

- [ ] **Step 4: Implement Claude runner (stub)**

The actual Claude Agent SDK Python integration will depend on the SDK's availability. This is a stub that defines the interface:

```python
# packages/whitebox/src/shannon_whitebox/agents/runner.py
from dataclasses import dataclass, field
from typing import Any

@dataclass
class ClaudeRunResult:
    text: str = ""
    success: bool = False
    duration: int = 0
    turns: int = 0
    cost: float = 0.0
    model: str | None = None
    structured_output: Any | None = None
    error: str | None = None
    retryable: bool = True

async def run_claude_prompt(
    prompt: str,
    repo_path: str,
    model_tier: str = "medium",
    output_format: dict | None = None,
    api_key: str | None = None,
    deliverables_subdir: str | None = None,
    provider_config: dict | None = None,
) -> ClaudeRunResult:
    """Execute a Claude Agent SDK query.

    This is the integration point with the Claude Agent SDK Python version.
    When the SDK is available, replace this implementation with the actual
    SDK call. The interface contract:

    1. Takes a prompt and repo path
    2. Streams messages from the SDK
    3. Returns structured result with text, cost, turns, model, and optional structured_output

    The SDK call pattern mirrors the TS version's runClaudePrompt:
    - maxTurns: 10_000
    - permissionMode: bypassPermissions
    - cwd: repo_path
    - env: SDK env vars (API key, provider config, etc.)
    """
    raise NotImplementedError(
        "Claude Agent SDK Python integration pending. "
        "Install claude-agent-sdk and implement this function."
    )
```

- [ ] **Step 5: Implement agent executor**

```python
# packages/whitebox/src/shannon_whitebox/agents/executor.py
import json
import time
from pathlib import Path

from shannon_core.config.parser import distribute_config, parse_config
from shannon_core.models.agents import AgentName, AGENTS
from shannon_core.models.config import Config
from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.models.metrics import AgentMetrics
from shannon_core.utils.billing import is_spending_cap_behavior

from .runner import ClaudeRunResult, run_claude_prompt
from .validators import get_queue_filename, get_vuln_type, validate_deliverable
from ..git_manager import GitManager
from ..prompts.manager import PromptManager

class AgentExecutor:
    def __init__(self, prompt_manager: PromptManager):
        self.prompt_manager = prompt_manager

    async def execute(
        self,
        agent_name: AgentName,
        repo_path: str,
        web_url: str = "",
        deliverables_path: str | None = None,
        config_path: str | None = None,
        api_key: str | None = None,
        pipeline_testing: bool = False,
    ) -> AgentMetrics:
        defn = AGENTS[agent_name]
        repo = Path(repo_path)
        deliverables = Path(deliverables_path) if deliverables_path else repo / ".shannon" / "deliverables"
        deliverables.mkdir(parents=True, exist_ok=True)

        # 1. Load config
        config: Config | None = None
        if config_path:
            config = parse_config(config_path)
        distributed = distribute_config(config)

        # 2. Load prompt
        prompt = self.prompt_manager.load_sync(
            defn.prompt_template,
            variables={"web_url": web_url, "repo_path": str(repo)},
            config=distributed,
            pipeline_testing=pipeline_testing,
        )

        # 3. Git checkpoint
        GitManager.create_checkpoint(deliverables, agent_name)

        # 4. Execute
        start_time = time.monotonic()
        result = await run_claude_prompt(
            prompt=prompt,
            repo_path=str(repo),
            model_tier=defn.model_tier,
            api_key=api_key,
            deliverables_subdir=str(deliverables.relative_to(repo)) if deliverables.is_relative_to(repo) else None,
        )
        duration_ms = int((time.monotonic() - start_time) * 1000)

        # 5. Spending cap check
        if result.success and is_spending_cap_behavior(result.turns, result.cost, result.text):
            GitManager.rollback(deliverables, "spending cap detected")
            raise PentestError(
                f"Spending cap likely reached (turns={result.turns}, cost=${result.cost})",
                "billing",
                retryable=True,
                error_code=ErrorCode.SPENDING_CAP_REACHED,
            )

        # 6. Handle failure
        if not result.success:
            GitManager.rollback(deliverables, "execution failure")
            raise PentestError(
                result.error or f"Agent {agent_name.value} execution failed",
                "validation",
                retryable=result.retryable,
                error_code=ErrorCode.AGENT_EXECUTION_FAILED,
            )

        # 7. Write structured output
        queue_filename = get_queue_filename(agent_name)
        if result.structured_output is not None and queue_filename:
            queue_path = deliverables / queue_filename
            queue_path.write_text(json.dumps(result.structured_output, indent=2), encoding="utf-8")

        # 8. Validate deliverable
        await validate_deliverable(deliverables, agent_name)

        # 9. Commit
        GitManager.commit(deliverables, agent_name)

        return AgentMetrics(
            duration_ms=duration_ms,
            cost_usd=result.cost,
            num_turns=result.turns,
            model=result.model,
        )
```

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/agents/ packages/whitebox/tests/test_validators.py
git commit -m "feat(whitebox): add agent executor, runner stub, and validators"
```

---

## Task 10: Audit System

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/audit/log_stream.py`
- Create: `packages/whitebox/src/shannon_whitebox/audit/session.py`

- [ ] **Step 1: Implement log stream**

```python
# packages/whitebox/src/shannon_whitebox/audit/log_stream.py
import aiofiles
from pathlib import Path
from datetime import datetime, timezone

class LogStream:
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    async def append(self, line: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        async with aiofiles.open(self.file_path, "a", encoding="utf-8") as f:
            await f.write(f"[{timestamp}] {line}\n")

    async def append_lines(self, lines: list[str]) -> None:
        for line in lines:
            await self.append(line)
```

- [ ] **Step 2: Implement audit session**

```python
# packages/whitebox/src/shannon_whitebox/audit/session.py
import json
from pathlib import Path

from shannon_core.models.agents import AgentName
from shannon_core.models.metrics import AgentMetrics

from .log_stream import LogStream

class AuditSession:
    def __init__(self, workspace_path: Path):
        self.workspace_path = workspace_path
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        self.workflow_log = LogStream(workspace_path / "workflow.log")
        self.agents_dir = workspace_path / "agents"
        self.agents_dir.mkdir(exist_ok=True)
        self.prompts_dir = workspace_path / "prompts"
        self.prompts_dir.mkdir(exist_ok=True)
        self._current_agent: str | None = None

    async def log(self, message: str) -> None:
        await self.workflow_log.append(message)

    async def log_phase(self, phase: str, status: str) -> None:
        await self.workflow_log.append(f"Phase {phase}: {status}")

    async def start_agent(self, agent_name: AgentName, prompt: str, attempt: int = 1) -> None:
        self._current_agent = agent_name.value
        agent_log = LogStream(self.agents_dir / f"{agent_name.value}.log")
        await agent_log.append(f"Started (attempt {attempt})")

        prompt_path = self.prompts_dir / f"{agent_name.value}.txt"
        prompt_path.write_text(prompt, encoding="utf-8")

    async def end_agent(self, agent_name: AgentName, success: bool, metrics: AgentMetrics | None = None) -> None:
        status = "completed" if success else "failed"
        agent_log = LogStream(self.agents_dir / f"{agent_name.value}.log")
        await agent_log.append(f"Status: {status}")
        if metrics:
            await agent_log.append(f"Duration: {metrics.duration_ms}ms, Cost: ${metrics.cost_usd or 0:.2f}")
        self._current_agent = None

    async def save_session(self, session_data: dict) -> None:
        session_path = self.workspace_path / "session.json"
        session_path.write_text(json.dumps(session_data, indent=2, default=str), encoding="utf-8")
```

- [ ] **Step 3: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/audit/
git commit -m "feat(whitebox): add audit log stream and session tracking"
```

---

## Task 11: Workspace Session Management

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/session.py`
- Create: `packages/whitebox/tests/test_session.py`

- [ ] **Step 1: Write failing test for session management**

```python
# packages/whitebox/tests/test_session.py
import json
import pytest
from pathlib import Path
from shannon_whitebox.session import SessionManager

def test_create_workspace(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo")
    assert ws.exists()
    assert (ws / "session.json").exists()

def test_list_workspaces(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    mgr.create_workspace("https://a.com", "/repo1")
    mgr.create_workspace("https://b.com", "/repo2")
    workspaces = mgr.list_workspaces()
    assert len(workspaces) == 2

def test_get_workspace(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo")
    found = mgr.get_workspace(ws.name)
    assert found is not None
    assert found.name == ws.name

def test_get_workspace_not_found(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    assert mgr.get_workspace("nonexistent") is None

def test_session_json_contains_url(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://test.com", "/repo")
    data = json.loads((ws / "session.json").read_text())
    assert data["web_url"] == "https://test.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd shannon-py && python -m pytest packages/whitebox/tests/test_session.py -v`
Expected: FAIL

- [ ] **Step 3: Implement session manager**

```python
# packages/whitebox/src/shannon_whitebox/session.py
import json
import time
from pathlib import Path

from shannon_core.models.agents import AgentName

class SessionManager:
    def __init__(self, workspaces_dir: Path):
        self.workspaces_dir = workspaces_dir
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)

    def create_workspace(self, web_url: str, repo_path: str, name: str | None = None) -> Path:
        if not name:
            hostname = web_url.replace("https://", "").replace("http://", "").split("/")[0].replace(".", "-")
            session_id = f"shannon-{int(time.time() * 1000)}"
            name = f"{hostname}_{session_id}"

        ws = self.workspaces_dir / name
        ws.mkdir(parents=True, exist_ok=True)

        session_data = {
            "web_url": web_url,
            "repo_path": repo_path,
            "created_at": time.time(),
            "completed_agents": [],
            "metrics": {"agents": {}},
        }
        (ws / "session.json").write_text(json.dumps(session_data, indent=2), encoding="utf-8")
        return ws

    def list_workspaces(self) -> list[Path]:
        if not self.workspaces_dir.exists():
            return []
        return sorted(
            [p for p in self.workspaces_dir.iterdir() if p.is_dir() and (p / "session.json").exists()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

    def get_workspace(self, name: str) -> Path | None:
        ws = self.workspaces_dir / name
        if ws.exists() and (ws / "session.json").exists():
            return ws
        return None

    def get_session_data(self, workspace_path: Path) -> dict:
        session_file = workspace_path / "session.json"
        if not session_file.exists():
            return {}
        return json.loads(session_file.read_text(encoding="utf-8"))

    def update_session(self, workspace_path: Path, data: dict) -> None:
        existing = self.get_session_data(workspace_path)
        existing.update(data)
        (workspace_path / "session.json").write_text(
            json.dumps(existing, indent=2, default=str), encoding="utf-8",
        )

    def mark_agent_completed(self, workspace_path: Path, agent_name: AgentName) -> None:
        data = self.get_session_data(workspace_path)
        completed = data.get("completed_agents", [])
        if agent_name.value not in completed:
            completed.append(agent_name.value)
        data["completed_agents"] = completed
        self.update_session(workspace_path, data)

    def is_agent_completed(self, workspace_path: Path, agent_name: AgentName) -> bool:
        data = self.get_session_data(workspace_path)
        return agent_name.value in data.get("completed_agents", [])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd shannon-py && python -m pytest packages/whitebox/tests/test_session.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/session.py packages/whitebox/tests/test_session.py
git commit -m "feat(whitebox): add workspace session management"
```

---

## Task 12: Temporal Pipeline

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`
- Create: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`
- Create: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`

- [ ] **Step 1: Implement shared types**

```python
# packages/whitebox/src/shannon_whitebox/pipeline/shared.py
from dataclasses import dataclass, field

from shannon_core.models.agents import VulnType
from shannon_core.models.metrics import AgentMetrics

@dataclass
class PipelineInput:
    repo_path: str
    web_url: str = ""
    config_path: str | None = None
    output_path: str | None = None
    workspace_name: str | None = None
    resume_from_workspace: str | None = None
    vuln_classes: list[VulnType] | None = None
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    deliverables_subdir: str = ".shannon/deliverables"

@dataclass
class PipelineState:
    status: str = "running"
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    start_time: float = 0.0
    error: str | None = None

@dataclass
class ActivityInput:
    repo_path: str
    web_url: str = ""
    config_path: str | None = None
    workspace_name: str | None = None
    deliverables_subdir: str = ".shannon/deliverables"
    pipeline_testing_mode: bool = False
    api_key: str | None = None
```

- [ ] **Step 2: Implement activities**

```python
# packages/whitebox/src/shannon_whitebox/pipeline/activities.py
from datetime import timedelta
from pathlib import Path

from temporalio import activity

from shannon_core.models.agents import AgentName, AGENTS, ALL_VULN_CLASSES, VulnType
from shannon_core.models.config import ALL_VULN_CLASSES as CONFIG_VULN_CLASSES
from shannon_core.models.errors import PentestError
from shannon_core.models.metrics import AgentMetrics
from shannon_whitebox.agents.executor import AgentExecutor
from shannon_whitebox.prompts.manager import PromptManager
from shannon_whitebox.session import SessionManager
from shannon_whitebox.audit.session import AuditSession

from .shared import ActivityInput

def _get_paths(input: ActivityInput) -> tuple[Path, Path, Path]:
    repo = Path(input.repo_path)
    deliverables = repo / input.deliverables_subdir
    workspaces = repo.parent / "workspaces"
    return repo, deliverables, workspaces

@activity.defn
async def run_preflight(input: ActivityInput) -> None:
    repo, _, _ = _get_paths(input)
    if not repo.exists():
        raise PentestError(
            f"Repository not found: {input.repo_path}",
            "config",
            error_code=shannon_core.models.errors.ErrorCode.REPO_NOT_FOUND,
        )
    if not (repo / ".git").exists():
        raise PentestError(
            f"Not a git repository: {input.repo_path}",
            "config",
            error_code=shannon_core.models.errors.ErrorCode.REPO_NOT_FOUND,
        )

@activity.defn
async def run_agent(input: ActivityInput) -> dict:
    agent_name = AgentName(input.workspace_name)  # passed via workspace_name field as workaround
    repo, deliverables, _ = _get_paths(input)
    prompt_manager = PromptManager(repo.parent.parent / "prompts")
    executor = AgentExecutor(prompt_manager)
    metrics = await executor.execute(
        agent_name=agent_name,
        repo_path=str(repo),
        web_url=input.web_url,
        deliverables_path=str(deliverables),
        config_path=input.config_path,
        api_key=input.api_key,
        pipeline_testing=input.pipeline_testing_mode,
    )
    return metrics.model_dump()

@activity.defn
async def run_vuln_agent(input: ActivityInput) -> dict:
    return await run_agent(input)
```

- [ ] **Step 3: Implement workflow**

```python
# packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
import asyncio
from datetime import timedelta

from temporalio import workflow

from shannon_core.models.agents import AgentName, ALL_VULN_CLASSES, VulnType
from shannon_core.models.errors import PentestError

from .shared import ActivityInput, PipelineInput, PipelineState

with workflow.unsafe.imports_passed_through():
    from . import activities

@workflow.defn
class WhiteboxScanWorkflow:
    def __init__(self):
        self._state = PipelineState()

    @workflow.run
    async def run(self, input: PipelineInput) -> PipelineState:
        self._state.start_time = workflow.time_ns() / 1e9

        selected_classes: list[VulnType] = input.vuln_classes or list(ALL_VULN_CLASSES)

        # 1. Preflight
        act_input = ActivityInput(
            repo_path=input.repo_path,
            web_url=input.web_url,
            config_path=input.config_path,
            workspace_name=input.workspace_name,
            deliverables_subdir=input.deliverables_subdir,
            pipeline_testing_mode=input.pipeline_testing_mode,
            api_key=input.api_key,
        )
        await workflow.execute_activity(
            activities.run_preflight, act_input,
            start_to_close_timeout=timedelta(minutes=2),
        )

        # 2. Pre-Recon
        if AgentName.PRE_RECON.value not in self._state.completed_agents:
            pre_recon_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value})
            metrics = await workflow.execute_activity(
                activities.run_agent, pre_recon_input,
                start_to_close_timeout=timedelta(hours=2),
                retry_policy=temporalio.common.RetryPolicy(
                    maximum_attempts=50,
                    initial_interval=timedelta(minutes=5),
                    maximum_interval=timedelta(minutes=30),
                    backoff_coefficient=2.0,
                ),
            )
            self._state.completed_agents.append(AgentName.PRE_RECON.value)
            self._state.agent_metrics[AgentName.PRE_RECON.value] = metrics

        # 3. Recon
        if AgentName.RECON.value not in self._state.completed_agents:
            recon_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.RECON.value})
            metrics = await workflow.execute_activity(
                activities.run_agent, recon_input,
                start_to_close_timeout=timedelta(hours=2),
            )
            self._state.completed_agents.append(AgentName.RECON.value)
            self._state.agent_metrics[AgentName.RECON.value] = metrics

        # 4. Vulnerability Analysis (parallel)
        vuln_tasks = []
        for vt in selected_classes:
            agent_name = AgentName(f"{vt}-vuln")
            if agent_name.value not in self._state.completed_agents:
                vuln_input = ActivityInput(**{**act_input.__dict__, "workspace_name": agent_name.value})
                vuln_tasks.append(
                    workflow.execute_activity(
                        activities.run_vuln_agent, vuln_input,
                        start_to_close_timeout=timedelta(hours=2),
                    )
                )

        if vuln_tasks:
            import temporalio.common
            results = await asyncio.gather(*vuln_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                vt = selected_classes[i]
                agent_name = AgentName(f"{vt}-vuln")
                if isinstance(result, Exception):
                    self._state.error = f"{agent_name.value}: {result}"
                else:
                    self._state.completed_agents.append(agent_name.value)
                    self._state.agent_metrics[agent_name.value] = result

        self._state.status = "completed"
        return self._state
```

- [ ] **Step 4: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/
git commit -m "feat(whitebox): add Temporal workflow, activities, and shared types"
```

---

## Task 13: Temporal Worker Entry Point

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/worker.py`

- [ ] **Step 1: Implement worker**

```python
# packages/whitebox/src/shannon_whitebox/worker.py
import asyncio
from datetime import timedelta

from temporalio.client import Client
from temporalio.worker import Worker

from .pipeline.activities import run_agent, run_preflight, run_vuln_agent
from .pipeline.workflows import WhiteboxScanWorkflow
from .pipeline.shared import PipelineInput

TASK_QUEUE = "shannon-whitebox"

async def run_scan(input: PipelineInput, temporal_address: str = "localhost:7233") -> dict:
    """Connect to Temporal, start a worker, submit the workflow, and wait for result."""
    client = await Client.connect(temporal_address)

    worker = Worker(
        client=client,
        task_queue=TASK_QUEUE,
        workflows=[WhiteboxScanWorkflow],
        activities=[run_preflight, run_agent, run_vuln_agent],
    )

    async with worker:
        result = await client.execute_workflow(
            WhiteboxScanWorkflow.run,
            input,
            id=input.workspace_name or f"whitebox-{int(asyncio.get_event_loop().time())}",
            task_queue=TASK_QUEUE,
        )
        return result

def main():
    """Entry point for running the worker standalone."""
    import sys
    asyncio.run(run_scan(PipelineInput(repo_path=sys.argv[1] if len(sys.argv) > 1 else ".")))
```

- [ ] **Step 2: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/worker.py
git commit -m "feat(whitebox): add Temporal worker entry point"
```

---

## Task 14: CLI

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/cli/main.py`
- Create: `packages/whitebox/tests/test_cli.py`

- [ ] **Step 1: Write failing test for CLI**

```python
# packages/whitebox/tests/test_cli.py
from click.testing import CliRunner
from shannon_whitebox.cli.main import cli

def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Shannon White-Box Scanner" in result.output

def test_start_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "--help"])
    assert result.exit_code == 0
    assert "--repo" in result.output

def test_workspaces_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["workspaces", "--help"])
    assert result.exit_code == 0

def test_logs_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["logs", "--help"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd shannon-py && python -m pytest packages/whitebox/tests/test_cli.py -v`
Expected: FAIL

- [ ] **Step 3: Implement CLI**

```python
# packages/whitebox/src/shannon_whitebox/cli/main.py
import asyncio
import click
from pathlib import Path

from shannon_whitebox.session import SessionManager
from shannon_whitebox.pipeline.shared import PipelineInput

@click.group()
def cli():
    """Shannon White-Box Scanner - Source code vulnerability analysis."""

@cli.command()
@click.option("-r", "--repo", required=True, help="Target repository path")
@click.option("-o", "--output", default=None, help="Output directory for deliverables")
@click.option("-w", "--workspace", default=None, help="Workspace name (supports resume)")
@click.option("-c", "--config", "config_path", default=None, help="YAML configuration file")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
@click.option("--temporal-address", default="localhost:7233", help="Temporal server address")
def start(repo, output, workspace, config_path, pipeline_testing, temporal_address):
    """Start a white-box security scan."""
    from shannon_whitebox.worker import run_scan

    input = PipelineInput(
        repo_path=str(Path(repo).resolve()),
        output_path=str(Path(output).resolve()) if output else None,
        workspace_name=workspace,
        config_path=config_path,
        pipeline_testing_mode=pipeline_testing,
    )
    click.echo(f"Starting white-box scan on {repo}")
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

- [ ] **Step 4: Run test to verify it passes**

Run: `cd shannon-py && python -m pytest packages/whitebox/tests/test_cli.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/ packages/whitebox/tests/test_cli.py
git commit -m "feat(whitebox): add Click CLI with start, logs, workspaces commands"
```

---

## Task 15: Prompt Templates

**Files:**
- Create: `shannon-py/prompts/pre-recon-code.txt`
- Create: `shannon-py/prompts/recon.txt`
- Create: `shannon-py/prompts/vuln-injection.txt`
- Create: `shannon-py/prompts/vuln-xss.txt`
- Create: `shannon-py/prompts/vuln-auth.txt`
- Create: `shannon-py/prompts/vuln-ssrf.txt`
- Create: `shannon-py/prompts/vuln-authz.txt`

- [ ] **Step 1: Copy prompt templates from TS version**

Copy prompt template files from `shannon/apps/worker/prompts/` to `shannon-py/prompts/`. The variable placeholders (`{{WEB_URL}}`, `{{REPO_PATH}}`, etc.) and `@include()` directives must be preserved exactly.

Files to copy:
- `pre-recon-code.txt`
- `recon.txt`
- `vuln-injection.txt`
- `vuln-xss.txt`
- `vuln-auth.txt`
- `vuln-ssrf.txt`
- `vuln-authz.txt`
- `shared/` directory (all shared partials)

Run: `cp -r shannon/apps/worker/prompts/*.txt shannon-py/prompts/ && cp -r shannon/apps/worker/prompts/shared shannon-py/prompts/shared`

- [ ] **Step 2: Verify prompts are identical**

Run: `diff <(ls shannon/apps/worker/prompts/*.txt) <(ls shannon-py/prompts/*.txt)`
Expected: No differences (except exploit-*.txt which belong to black-box)

- [ ] **Step 3: Commit**

```bash
git add shannon-py/prompts/
git commit -m "feat: copy prompt templates from TS version"
```

---

## Task 16: Integration Test

**Files:**
- Create: `packages/whitebox/tests/test_integration.py`

- [ ] **Step 1: Write integration test for full pipeline (mocked)**

```python
# packages/whitebox/tests/test_integration.py
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from shannon_core.models.agents import AgentName
from shannon_whitebox.agents.executor import AgentExecutor
from shannon_whitebox.prompts.manager import PromptManager
from shannon_whitebox.session import SessionManager
from shannon_whitebox.agents.runner import ClaudeRunResult

@pytest.fixture
def mock_repo(tmp_path):
    """Create a mock repo with git init."""
    import subprocess
    repo = tmp_path / "target-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)
    (repo / "README.md").write_text("# Test App")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)
    return repo

@pytest.fixture
def prompts_dir(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "pre-recon-code.txt").write_text("Analyze {{REPO_PATH}} for {{WEB_URL}}")
    (prompts / "recon.txt").write_text("Recon {{WEB_URL}}")
    for vt in ["injection", "xss", "auth", "ssrf", "authz"]:
        (prompts / f"vuln-{vt}.txt").write_text(f"Find {vt} vulns in {{REPO_PATH}}")
    return prompts

@pytest.mark.asyncio
async def test_full_pipeline_mocked(mock_repo, prompts_dir):
    """Test the full pipeline with mocked Claude runner."""
    from shannon_core.models.metrics import AgentMetrics

    mock_results: dict[str, ClaudeRunResult] = {}
    for agent in [AgentName.PRE_RECON, AgentName.RECON,
                   AgentName.INJECTION_VULN, AgentName.XSS_VULN,
                   AgentName.AUTH_VULN, AgentName.SSRF_VULN,
                   AgentName.AUTHZ_VULN]:
        from shannon_core.models.agents import AGENTS
        filename = AGENTS[agent].deliverable_filename
        mock_results[agent.value] = ClaudeRunResult(
            text="Analysis complete",
            success=True,
            duration=1000,
            turns=5,
            cost=0.01,
            model="test-model",
        )

    call_count = 0
    async def mock_run_claude(**kwargs):
        nonlocal call_count
        call_count += 1
        return mock_results.get("pre-recon", ClaudeRunResult(success=True, text="ok"))

    with patch("shannon_whitebox.agents.executor.run_claude_prompt", side_effect=mock_run_claude):
        deliverables = mock_repo / ".shannon" / "deliverables"
        deliverables.mkdir(parents=True, exist_ok=True)

        for agent_name in [AgentName.PRE_RECON, AgentName.RECON,
                            AgentName.INJECTION_VULN, AgentName.XSS_VULN,
                            AgentName.AUTH_VULN, AgentName.SSRF_VULN,
                            AgentName.AUTHZ_VULN]:
            filename = AGENTS[agent_name].deliverable_filename
            (deliverables / filename).write_text(f"# {agent_name.value} result")

            pm = PromptManager(prompts_dir)
            executor = AgentExecutor(pm)
            metrics = await executor.execute(
                agent_name=agent_name,
                repo_path=str(mock_repo),
                web_url="https://example.com",
                deliverables_path=str(deliverables),
            )
            assert isinstance(metrics, AgentMetrics)
            assert metrics.duration_ms > 0

    assert call_count == 7

    files = list(deliverables.glob("*.md"))
    assert len(files) == 7
```

- [ ] **Step 2: Run integration test**

Run: `cd shannon-py && python -m pytest packages/whitebox/tests/test_integration.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add packages/whitebox/tests/test_integration.py
git commit -m "test(whitebox): add integration test for full pipeline with mocked Claude runner"
```

---

## Task 17: Final Verification

- [ ] **Step 1: Run all tests**

Run: `cd shannon-py && python -m pytest packages/ -v`
Expected: All tests pass

- [ ] **Step 2: Verify project structure**

Run: `find shannon-py/packages -name '*.py' -not -name '__pycache__' | sort`
Expected: All files from tasks 1-16 listed

- [ ] **Step 3: Verify core package imports**

Run: `cd shannon-py && python -c "from shannon_core.models import AGENTS, AgentName, Config, ErrorCode, PentestError; print('Core imports OK')"`
Expected: "Core imports OK"

- [ ] **Step 4: Verify whitebox package imports**

Run: `cd shannon-py && python -c "from shannon_whitebox.cli.main import cli; from shannon_whitebox.session import SessionManager; print('Whitebox imports OK')"`
Expected: "Whitebox imports OK"
