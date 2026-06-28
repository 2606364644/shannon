# Shannon-Py Whitebox Scanner Design

## Overview

Refactor Shannon's white-box scanning pipeline from TypeScript to Python 3.12. The white-box scanner analyzes source code repositories to identify potential vulnerabilities, producing structured deliverables that a future black-box scanner can consume.

## Goals

1. **Separate white-box from black-box**: White-box (code analysis) runs independently; black-box (live exploitation) consumes white-box outputs later
2. **Output compatibility**: Deliverable filenames, JSON structures, and directory layout match the TypeScript version exactly
3. **Pythonic implementation**: Use Pydantic, modern Python idioms, clean abstractions — while maintaining readability
4. **Same pipeline semantics**: Pre-Recon → Recon → Vulnerability Analysis (5 parallel agents)

## Technology Stack

| Component | TS Version | Python Version |
|-----------|-----------|----------------|
| AI Execution | `@anthropic-ai/claude-agent-sdk` | `claude-agent-sdk` (Python) — fallback: `anthropic` SDK + custom agent loop if agent SDK is unavailable |
| Orchestration | `@temporalio/*` | `temporalio` (Python SDK) |
| CLI | Commander.js + tsdown | Click |
| Config Validation | AJV + JSON Schema | Pydantic |
| Schema Validation | Zod | Pydantic (for queue JSON) |
| YAML Parsing | `js-yaml` | `pyyaml` |
| Runtime | Node.js (Docker) | Python 3.12 |

## Project Structure

```
shannon-py/
├── packages/
│   ├── core/                         # Shared models and utilities
│   │   ├── pyproject.toml
│   │   └── src/shannon_core/
│   │       ├── __init__.py
│   │       ├── models/
│   │       │   ├── __init__.py
│   │       │   ├── config.py         # Config, Rules, Authentication, etc.
│   │       │   ├── agents.py         # AgentName, AgentDefinition, VulnType
│   │       │   ├── deliverables.py   # DeliverableType, filename mappings
│   │       │   ├── errors.py         # ErrorCode, PentestError
│   │       │   ├── metrics.py        # AgentMetrics, SessionMetadata
│   │       │   └── result.py         # Scan result models
│   │       ├── config/
│   │       │   ├── __init__.py
│   │       │   └── parser.py         # YAML parsing + Pydantic validation
│   │       └── utils/
│   │           ├── __init__.py
│   │           ├── formatting.py
│   │           ├── file_io.py
│   │           ├── billing.py        # Spending cap detection
│   │           └── concurrency.py
│   │
│   ├── whitebox/                     # White-box scanning pipeline
│   │   ├── pyproject.toml
│   │   └── src/shannon_whitebox/
│   │       ├── __init__.py
│   │       ├── cli/
│   │       │   ├── __init__.py
│   │       │   └── main.py           # Click CLI: start, status, logs, workspaces
│   │       ├── pipeline/
│   │       │   ├── __init__.py
│   │       │   ├── workflows.py      # Temporal workflow definition
│   │       │   ├── activities.py     # Thin Temporal activity wrappers
│   │       │   └── shared.py         # PipelineInput, PipelineState, queries
│   │       ├── agents/
│   │       │   ├── __init__.py
│   │       │   ├── executor.py       # Agent lifecycle management
│   │       │   ├── runner.py         # Claude Agent SDK integration
│   │       │   └── validators.py     # Output validation
│   │       ├── prompts/
│   │       │   ├── __init__.py
│   │       │   └── manager.py        # Template loading, includes, variable substitution
│   │       ├── audit/
│   │       │   ├── __init__.py
│   │       │   ├── session.py        # AuditSession per-agent logging
│   │       │   └── log_stream.py     # Append-only log stream
│   │       ├── session.py            # Agent registry, workspace management
│   │       ├── git_manager.py        # Git checkpoint/rollback/commit
│   │       └── worker.py             # Temporal worker entry point
│   │
│   └── blackbox/                     # Black-box scanning (future implementation)
│       └── pyproject.toml
│
├── prompts/                          # Prompt templates (shared)
│   ├── pre-recon-code.txt
│   ├── recon.txt
│   ├── vuln-injection.txt
│   ├── vuln-xss.txt
│   ├── vuln-auth.txt
│   ├── vuln-ssrf.txt
│   ├── vuln-authz.txt
│   ├── shared/
│   │   ├── _code-path-rules.txt
│   │   ├── _rules-of-engagement.txt
│   │   ├── _rules.txt
│   │   ├── _target.txt
│   │   ├── _vuln-scope.txt
│   │   ├── _exploit-scope.txt
│   │   └── login-instructions.txt
│   └── pipeline-testing/
│       └── ...
│
├── configs/
│   └── example-config.yaml
│
├── pyproject.toml                    # Root workspace config
└── README.md
```

## Core Domain Models

### Configuration (config.py)

Pydantic models replace TS interfaces + AJV validation. Same validation rules as TS version.

```python
from pydantic import BaseModel, model_validator
from typing import Literal

class Rule(BaseModel):
    description: str
    type: Literal["url_path", "subdomain", "domain", "method", "header", "parameter", "code_path"]
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

class Config(BaseModel):
    rules: Rules | None = None
    description: str | None = None
    vuln_classes: list[VulnClass] | None = None
    exploit: bool = True
    report: ReportConfig | None = None
    rules_of_engagement: str | None = None
    authentication: Authentication | None = None

    @model_validator(mode="after")
    def validate_security(self) -> "Config":
        # Dangerous pattern detection (path traversal, JS URLs, etc.)
        # Same rules as TS version's performSecurityValidation()
        ...
```

### Agent Definitions (agents.py)

```python
from enum import Enum
from pydantic import BaseModel, ConfigDict

class AgentName(str, Enum):
    PRE_RECON = "pre-recon"
    RECON = "recon"
    INJECTION_VULN = "injection-vuln"
    XSS_VULN = "xss-vuln"
    AUTH_VULN = "auth-vuln"
    SSRF_VULN = "ssrf-vuln"
    AUTHZ_VULN = "authz-vuln"
    # REPORT and EXPLOIT agents belong to the black-box scanner

VulnType = Literal["injection", "xss", "auth", "ssrf", "authz"]

class AgentDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: AgentName
    display_name: str
    prerequisites: list[AgentName]
    prompt_template: str
    deliverable_filename: str
    model_tier: Literal["small", "medium", "large"] = "medium"

# Agent registry — mirrors TS version's AGENTS record
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
    # ... 5 vuln agents with same definitions as TS version
}
```

### Error Handling (errors.py)

Python exceptions replace `Result<T,E>`. Services throw `PentestError`; Temporal activities catch and classify.

```python
from enum import Enum

class ErrorCode(str, Enum):
    CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"
    CONFIG_VALIDATION_FAILED = "CONFIG_VALIDATION_FAILED"
    CONFIG_PARSE_ERROR = "CONFIG_PARSE_ERROR"
    AGENT_EXECUTION_FAILED = "AGENT_EXECUTION_FAILED"
    OUTPUT_VALIDATION_FAILED = "OUTPUT_VALIDATION_FAILED"
    API_RATE_LIMITED = "API_RATE_LIMITED"
    SPENDING_CAP_REACHED = "SPENDING_CAP_REACHED"
    GIT_CHECKPOINT_FAILED = "GIT_CHECKPOINT_FAILED"
    PROMPT_LOAD_FAILED = "PROMPT_LOAD_FAILED"
    DELIVERABLE_NOT_FOUND = "DELIVERABLE_NOT_FOUND"
    REPO_NOT_FOUND = "REPO_NOT_FOUND"
    TARGET_UNREACHABLE = "TARGET_UNREACHABLE"

class PentestError(Exception):
    def __init__(
        self,
        message: str,
        category: str,  # "config" | "network" | "prompt" | "filesystem" | "validation" | "billing"
        retryable: bool = False,
        error_code: ErrorCode | None = None,
        context: dict | None = None,
    ):
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.error_code = error_code
        self.context = context or {}
```

### Deliverables (deliverables.py)

Same filename mappings as TS version for output compatibility.

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

### Queue Schemas (queue_schemas.py)

Pydantic models for structured vulnerability output. Each vuln agent returns JSON matching its schema. These models replace TS version's Zod schemas and ensure output compatibility with the black-box scanner.

```python
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

# Queue document wrapper
class VulnerabilityQueue(BaseModel):
    vulnerabilities: list[BaseVulnerability] = []
```

## Pipeline Architecture

### White-Box Phases

White-box executes only 3 of the 5 phases. Exploitation and Reporting belong to the black-box scanner.

```
Phase 1: Pre-Reconnaissance (sequential)
    → Source code analysis
    → Output: pre_recon_deliverable.md

Phase 2: Reconnaissance (sequential)
    → Attack surface mapping
    → Output: recon_deliverable.md

Phase 3: Vulnerability Analysis (5 parallel agents)
    → injection, xss, auth, authz, ssrf
    → Output per agent:
      - {type}_exploitation_queue.json  (structured vulnerability data)
      - {type}_analysis_deliverable.md  (analysis narrative)
```

### Temporal Workflow

```python
from datetime import timedelta
from temporalio import workflow, activity
from dataclasses import dataclass, field

@dataclass
class PipelineInput:
    repo_path: str
    web_url: str = ""
    config_path: str | None = None
    output_path: str | None = None
    workspace_name: str | None = None
    resume_from_workspace: str | None = None
    vuln_classes: list[VulnClass] | None = None
    pipeline_testing_mode: bool = False

@dataclass
class PipelineState:
    status: str = "running"
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    start_time: float = 0.0
    error: str | None = None

@workflow.defn
class WhiteboxScanWorkflow:
    def __init__(self):
        self._state = PipelineState()

    @workflow.run
    async def run(self, input: PipelineInput) -> PipelineState:
        self._state.start_time = workflow.time_ns() / 1e9

        # 1. Preflight validation
        await workflow.execute_activity(
            run_preflight, input,
            start_to_close_timeout=timedelta(minutes=2),
        )

        # 2. Pre-Recon (sequential)
        if "pre-recon" not in self._state.completed_agents:
            metrics = await workflow.execute_activity(
                run_agent, (AgentName.PRE_RECON, input),
                start_to_close_timeout=timedelta(hours=2),
            )
            self._state.completed_agents.append("pre-recon")
            self._state.agent_metrics["pre-recon"] = metrics

        # 3. Recon (sequential)
        if "recon" not in self._state.completed_agents:
            metrics = await workflow.execute_activity(
                run_agent, (AgentName.RECON, input),
                start_to_close_timeout=timedelta(hours=2),
            )
            self._state.completed_agents.append("recon")
            self._state.agent_metrics["recon"] = metrics

        # 4. Vulnerability Analysis (5 parallel pipelines)
        selected = input.vuln_classes or ["injection", "xss", "auth", "ssrf", "authz"]
        tasks = []
        for vt in selected:
            agent_name = f"{vt}-vuln"
            if agent_name not in self._state.completed_agents:
                tasks.append(
                    workflow.execute_activity(
                        run_vuln_agent, (vt, input),
                        start_to_close_timeout=timedelta(hours=2),
                    )
                )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Process results, update state

        # 5. Persist session data
        await workflow.execute_activity(
            save_session, (input, self._state),
            start_to_close_timeout=timedelta(minutes=1),
        )

        self._state.status = "completed"
        return self._state
```

### Temporal Activities

Thin wrappers following the Services Boundary pattern. Business logic lives in `AgentExecutor`.

```python
@activity.defn
async def run_agent(args: tuple[AgentName, PipelineInput]) -> dict:
    agent_name, input = args
    executor = get_executor(input)

    try:
        return await executor.execute(agent_name, input)
    except PentestError as e:
        if not e.retryable:
            raise ApplicationFailure.non_retryable(str(e), e.__class__.__name__)
        raise ApplicationFailure(str(e), e.__class__.__name__)

@activity.defn
async def run_vuln_agent(args: tuple[str, PipelineInput]) -> dict:
    vuln_type, input = args
    agent_name = AgentName(f"{vuln_type}-vuln")
    return await run_agent((agent_name, input))
```

### Resume Support

Same mechanism as TS version:
- `session.json` tracks completed agents
- On resume, skip already-completed agents
- Git checkpoints for rollback safety
- Workspace validation (URL match check)

## Agent Execution

### AgentExecutor

Full lifecycle management, matching TS version's `AgentExecutionService`.

```python
class AgentExecutor:
    def __init__(self, config_loader: ConfigLoader, prompt_manager: PromptManager):
        self.config_loader = config_loader
        self.prompt_manager = prompt_manager

    async def execute(self, agent_name: AgentName, input: AgentInput) -> AgentMetrics:
        # 1. Load config (YAML → Pydantic model)
        config = await self.config_loader.load(input.config_path)

        # 2. Load prompt template with variable substitution
        prompt = await self.prompt_manager.load(
            AGENTS[agent_name].prompt_template,
            variables={"web_url": input.web_url, "repo_path": input.repo_path},
            config=config,
        )

        # 3. Git checkpoint
        await GitManager.create_checkpoint(input.deliverables_path, agent_name)

        # 4. Execute via Claude Agent SDK
        result = await ClaudeRunner.run(
            prompt=prompt,
            repo_path=input.repo_path,
            model_tier=AGENTS[agent_name].model_tier,
            output_format=self._get_output_format(agent_name, config.exploit),
        )

        # 5. Spending cap detection
        if is_spending_cap_behavior(result.turns, result.cost, result.text):
            raise PentestError("Spending cap reached", "billing", retryable=True)

        # 6. Write structured output (vuln agents only)
        if result.structured_output and agent_name.value.endswith("-vuln"):
            queue_path = Path(input.deliverables_path) / f"{vuln_type}_exploitation_queue.json"
            queue_path.write_text(json.dumps(result.structured_output, indent=2))

        # 7. Validate deliverable exists
        deliverable_path = Path(input.deliverables_path) / AGENTS[agent_name].deliverable_filename
        if not deliverable_path.exists():
            raise PentestError(
                f"Missing deliverable: {AGENTS[agent_name].deliverable_filename}",
                "validation", error_code=ErrorCode.OUTPUT_VALIDATION_FAILED,
            )

        # 8. Commit success
        await GitManager.commit(input.deliverables_path, agent_name)

        return AgentMetrics(
            duration_ms=result.duration,
            cost_usd=result.cost,
            num_turns=result.turns,
            model=result.model,
        )
```

### ClaudeRunner

```python
class ClaudeRunner:
    @staticmethod
    async def run(
        prompt: str,
        repo_path: str,
        model_tier: str = "medium",
        output_format: dict | None = None,
        api_key: str | None = None,
        provider_config: ProviderConfig | None = None,
    ) -> ClaudeRunResult:
        sdk_env = ClaudeRunner._build_sdk_env(api_key, provider_config)

        options = {
            "model": resolve_model(model_tier),
            "max_turns": 10_000,
            "cwd": repo_path,
            "permission_mode": "bypassPermissions",
            "env": sdk_env,
        }
        if output_format:
            options["output_format"] = output_format

        # Execute via claude-agent-sdk Python
        turn_count = 0
        cost = 0.0
        result_text = ""
        structured_output = None
        model = None

        async for message in claude_sdk.query(prompt=prompt, options=options):
            if message.type == "assistant":
                turn_count += 1
            elif message.type == "result":
                result_text = message.text
                cost = message.cost_usd
                model = message.model
                if hasattr(message, "structured_output"):
                    structured_output = message.structured_output

        return ClaudeRunResult(
            text=result_text,
            success=True,
            duration=...,
            turns=turn_count,
            cost=cost,
            model=model,
            structured_output=structured_output,
        )
```

## Prompt Management

Same template system as TS version:

```python
class PromptManager:
    def __init__(self, prompts_dir: Path):
        self.prompts_dir = prompts_dir

    async def load(
        self,
        template_name: str,
        variables: PromptVariables,
        config: DistributedConfig | None = None,
        pipeline_testing: bool = False,
    ) -> str:
        # 1. Read template file
        template = await self._read_template(template_name, pipeline_testing)

        # 2. Process @include() directives
        template = await self._process_includes(template)

        # 3. Variable substitution
        return self._interpolate(template, variables, config)
```

Variable substitution preserves all TS version placeholders:
- `{{WEB_URL}}`, `{{REPO_PATH}}`, `{{PLAYWRIGHT_SESSION}}`
- `{{AUTH_CONTEXT}}`, `{{DESCRIPTION}}`
- `{{RULES_AVOID}}`, `{{RULES_FOCUS}}`
- `{{CODE_RULES_AVOID}}`, `{{CODE_RULES_FOCUS}}`
- `{{RULES_OF_ENGAGEMENT}}`
- `{{LOGIN_INSTRUCTIONS}}`
- `{{VULN_CLASSES_TESTED}}`, `{{VULN_SUMMARY_SUBSECTIONS}}`
- `{{EXPLOITATION}}`, `{{REPORT_VULN_HEADING}}`
- `{{REPORT_FILTERS_BLOCK}}`, `{{REPORT_FILTER_RULES}}`

## CLI Interface

```python
import click

@click.group()
def cli():
    """Shannon White-Box Scanner - Source code vulnerability analysis."""

@cli.command()
@click.option("-r", "--repo", required=True, help="Target repository path")
@click.option("-o", "--output", help="Output directory for deliverables")
@click.option("-w", "--workspace", help="Workspace name (supports resume)")
@click.option("-c", "--config", help="YAML configuration file path")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
def start(repo, output, workspace, config, pipeline_testing):
    """Start a white-box security scan."""
    ...

@cli.command()
def status():
    """Show running scan status."""

@cli.command()
@click.argument("workspace_name")
def logs(workspace_name):
    """View workspace execution logs."""

@cli.command()
def workspaces():
    """List all workspaces."""
```

## Audit System

Crash-safe append-only logging in workspace directory:

```
workspaces/{hostname}_{sessionId}/
├── session.json                    # Session metadata and per-agent metrics
├── workflow.log                    # Human-readable workflow log
├── agents/                         # Per-agent execution logs
│   ├── pre-recon.log
│   ├── recon.log
│   └── {vuln_type}-vuln.log
├── prompts/                        # Prompt snapshots for reproducibility
│   ├── pre-recon-code.txt
│   ├── recon.txt
│   └── vuln-*.txt
└── deliverables/
    ├── pre_recon_deliverable.md
    ├── recon_deliverable.md
    ├── injection_exploitation_queue.json
    ├── injection_analysis_deliverable.md
    ├── xss_exploitation_queue.json
    ├── xss_analysis_deliverable.md
    ├── auth_exploitation_queue.json
    ├── auth_analysis_deliverable.md
    ├── ssrf_exploitation_queue.json
    ├── ssrf_analysis_deliverable.md
    ├── authz_exploitation_queue.json
    └── authz_analysis_deliverable.md
```

## Output Compatibility

All deliverable files maintain exact format compatibility with the TypeScript version:

| File | Format | Consumer |
|------|--------|----------|
| `*_exploitation_queue.json` | Same JSON schema as TS version (ID, vulnerability_type, confidence, etc.) | Black-box scanner |
| `*_analysis_deliverable.md` | Same Markdown format | Black-box scanner, reporting |
| `session.json` | Same JSON structure (metrics.agents.{name}.model, etc.) | Resume, reporting |
| `workflow.log` | Same human-readable log format | Monitoring |
| `pre_recon_deliverable.md` | Same Markdown format | Downstream agents |
| `recon_deliverable.md` | Same Markdown format | Downstream agents |

The black-box scanner reads the white-box workspace directory (via `--workspace` parameter) and picks up from `*_exploitation_queue.json` to execute exploitation and reporting phases.

## Key Design Decisions

### Why exceptions over Result<T,E>
Python's exception handling is idiomatic and well-understood. The TS version used `Result<T,E>` because TypeScript's union types make it ergonomic. In Python, try/except is the standard pattern. The Temporal activity layer catches `PentestError` and classifies it into `ApplicationFailure` for retry control — the same classification logic, just expressed differently.

### Why Pydantic over JSON Schema
Pydantic provides:
- Type-safe model definitions with validation
- Automatic JSON schema generation if needed
- Runtime validation on deserialization
- Better IDE support and documentation

The YAML config format remains compatible — users can use the same config files across TS and Python versions. Only the internal validation mechanism changes.

### Why no Docker CLI layer
The TS version includes a CLI package (`apps/cli/`) that manages Docker containers and Temporal infrastructure. The Python version assumes users manage their own infrastructure (Temporal server running separately). This keeps the initial implementation focused on the scanning logic. A deployment layer can be added later.

### White-box / black-box interface
The interface is filesystem-based. The white-box scanner writes deliverables to a workspace directory. The black-box scanner reads from the same directory. No API, no message queue, no shared database. This is the simplest approach and matches how the TS version already works internally (deliverable files in repo).

## Dependencies

```toml
# packages/core/pyproject.toml
[project]
name = "shannon-core"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.0",
    "pyyaml>=6.0",
]

# packages/whitebox/pyproject.toml
[project]
name = "shannon-whitebox"
requires-python = ">=3.12"
dependencies = [
    "shannon-core",
    "temporalio>=1.0",
    "claude-agent-sdk",  # Python version when available
    "click>=8.0",
    "aiofiles>=23.0",
]
```

## Scope Exclusions (for this iteration)

- Black-box scanner (separate implementation)
- Docker/deployment layer
- Authentication validation (requires browser automation — belongs to black-box)
- Playwright integration
- Exploitation phase
- Report generation (report assembly belongs to black-box)
- npx/pnpm-style distribution
