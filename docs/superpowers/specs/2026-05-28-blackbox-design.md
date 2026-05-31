# Shannon-Py Blackbox Scanner — Design Spec

## Overview

Refactor the black-box scanning logic from the TypeScript Shannon project into `shannon-blackbox`, a new Python 3.12 package within the `shannon-py` monorepo. The blackbox package handles runtime/DAST scanning: reconnaissance of live targets and exploitation of confirmed vulnerabilities. It reuses whitebox infrastructure (AgentExecutor, PromptManager, SessionManager, GitManager, AuditSession) and shares data models via `shannon-core`.

### Key Requirements

1. Blackbox can run independently (no whitebox results needed) or continue from whitebox results
2. When continuing from whitebox, reads `*_exploitation_queue.json` from the shared workspace
3. Output files (names, formats, directory structure) match the TypeScript project exactly
4. Uses Pydantic v2, modern Python 3.12, Temporal for workflow orchestration
5. Claude Agent SDK drives all LLM-powered agents with Playwright for browser automation

## Architecture Decision: Whitebox Subset (Option A)

Blackbox depends on `shannon-whitebox` and reuses its infrastructure components:

```
shannon-core (models, config, utils)
    ↑
shannon-whitebox (AgentExecutor, PromptManager, SessionManager, GitManager, AuditSession)
    ↑
shannon-blackbox (ExploitExecutor, ReconExecutor, BlackboxScanWorkflow, ExploitationChecker, ReportAssembler)
```

This avoids code duplication while keeping whitebox unchanged. Blackbox imports from whitebox directly.

## Agent Registry

### New AgentName Values (added to `shannon-core` models/agents.py)

```python
class AgentName(str, Enum):
    # Whitebox (existing)
    PRE_RECON = "pre-recon"
    RECON = "recon"
    INJECTION_VULN = "injection-vuln"
    XSS_VULN = "xss-vuln"
    AUTH_VULN = "auth-vuln"
    SSRF_VULN = "ssrf-vuln"
    AUTHZ_VULN = "authz-vuln"
    # Blackbox (new)
    RECON_BLACKBOX = "recon-blackbox"
    INJECTION_EXPLOIT = "injection-exploit"
    XSS_EXPLOIT = "xss-exploit"
    AUTH_EXPLOIT = "auth-exploit"
    SSRF_EXPLOIT = "ssrf-exploit"
    AUTHZ_EXPLOIT = "authz-exploit"
    # Shared
    REPORT = "report"
```

### Agent Definitions (added to AGENTS registry in core)

| Agent | Display Name | Prerequisites | Prompt Template | Deliverable Filename | Model Tier |
|-------|-------------|---------------|-----------------|---------------------|------------|
| `RECON_BLACKBOX` | Reconnaissance (Black-Box) | none | `recon-blackbox.txt` | `recon_deliverable.md` | medium |
| `INJECTION_EXPLOIT` | Injection Exploitation | RECON_BLACKBOX or RECON | `injection-exploit.txt` | `injection_exploitation_evidence.md` | medium |
| `XSS_EXPLOIT` | XSS Exploitation | RECON_BLACKBOX or RECON | `xss-exploit.txt` | `xss_exploitation_evidence.md` | medium |
| `AUTH_EXPLOIT` | Auth Exploitation | RECON_BLACKBOX or RECON | `auth-exploit.txt` | `auth_exploitation_evidence.md` | medium |
| `SSRF_EXPLOIT` | SSRF Exploitation | RECON_BLACKBOX or RECON | `ssrf-exploit.txt` | `ssrf_exploitation_evidence.md` | medium |
| `AUTHZ_EXPLOIT` | Authz Exploitation | RECON_BLACKBOX or RECON | `authz-exploit.txt` | `authz_exploitation_evidence.md` | medium |
| `REPORT` | Report | all exploit agents | `report-executive.txt` | `comprehensive_security_assessment_report.md` | medium |

### DeliverableType Enum Extension (in core models/deliverables.py)

Add new enum values for exploit evidence and report:

```python
class DeliverableType(str, Enum):
    # Whitebox (existing)
    CODE_ANALYSIS = "CODE_ANALYSIS"
    RECON = "RECON"
    INJECTION_ANALYSIS = "INJECTION_ANALYSIS"
    XSS_ANALYSIS = "XSS_ANALYSIS"
    AUTH_ANALYSIS = "AUTH_ANALYSIS"
    AUTHZ_ANALYSIS = "AUTHZ_ANALYSIS"
    SSRF_ANALYSIS = "SSRF_ANALYSIS"
    # Blackbox (new)
    INJECTION_EVIDENCE = "INJECTION_EVIDENCE"
    XSS_EVIDENCE = "XSS_EVIDENCE"
    AUTH_EVIDENCE = "AUTH_EVIDENCE"
    AUTHZ_EVIDENCE = "AUTHZ_EVIDENCE"
    SSRF_EVIDENCE = "SSRF_EVIDENCE"
    # Shared
    REPORT = "REPORT"
```

## Package Structure

```
packages/blackbox/
├── pyproject.toml
├── src/shannon_blackbox/
│   ├── __init__.py
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── shared.py           # BlackboxPipelineInput, BlackboxPipelineState
│   │   ├── workflows.py        # BlackboxScanWorkflow (Temporal workflow)
│   │   └── activities.py       # Temporal activities (thin wrappers)
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── exploit_executor.py # Reads queue JSON, constructs exploit prompt
│   │   └── recon_executor.py   # Standalone recon (no source code)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── exploitation_checker.py  # Queue → should_exploit decision
│   │   └── report_assembler.py      # Concatenate evidence → final report
│   ├── cli/
│   │   ├── __init__.py
│   │   └── main.py             # Click CLI: start, logs, workspaces
│   └── worker.py               # Temporal worker entry point
└── tests/
    ├── test_exploitation_checker.py
    ├── test_report_assembler.py
    ├── test_exploit_executor.py
    ├── test_recon_executor.py
    └── test_integration.py
```

### pyproject.toml

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

## Pipeline Workflow

### BlackboxPipelineInput

```python
class BlackboxPipelineInput(BaseModel):
    web_url: str
    workspace_name: str | None = None
    config_path: str | None = None
    output_path: str | None = None
    resume_from_workspace: str | None = None
    vuln_classes: list[VulnClass] = ALL_VULN_CLASSES
    exploit: bool = True
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    deliverables_subdir: str = ".shannon/deliverables"
```

### BlackboxPipelineState

```python
class BlackboxPipelineState(BaseModel):
    status: str = "running"
    current_phase: str | None = None
    completed_agents: list[str] = []
    agent_metrics: dict[str, AgentMetrics] = {}
    has_whitebox_results: bool = False
    error: str | None = None
```

### Workflow Steps

```
1. Preflight
   - Validate target URL reachable (DNS + HTTP HEAD)
   - Parse config if provided
   - Block link-local metadata addresses

2. Detect Whitebox Results
   - Check workspace for *_exploitation_queue.json files
   - Set has_whitebox_results flag
   - If whitebox results exist, load queue data per vuln class

3. Recon (conditional)
   - Independent mode (no whitebox results): run RECON_BLACKBOX agent
     - Agent uses Playwright + HTTP tools to explore target URL
     - No source code context in prompt
   - Continuation mode (has whitebox results): skip recon
     - Use existing recon_deliverable.md from whitebox

4. Exploitation (5 parallel agents)
   - For each vuln class in vuln_classes:
     a. ExploitationChecker reads *_exploitation_queue.json
     b. If vulnerabilities array is empty or file missing → skip
     c. If should_exploit → run corresponding EXPLOIT agent
     d. Exploit agent prompt includes vulnerability details from queue
     e. Each agent uses Playwright (isolated session) + HTTP tools
     f. Writes *_exploitation_evidence.md

5. Report
   a. ReportAssembler concatenates:
      - Per-class: prefers *_exploitation_evidence.md, falls back to *_findings.md
      - Renders *_findings.md from queue JSON if no exploit was run
   b. Writes comprehensive_security_assessment_report.md
   c. Report agent (REPORT) adds executive summary via LLM
```

### Resume Support

When `resume_from_workspace` is provided:
- Check session.json for completed agents
- Skip already-completed agents
- Restore git state to latest checkpoint
- Clean up incomplete deliverables

## Key Components

### AgentExecutor Extension (in shannon-whitebox)

The existing `AgentExecutor.execute()` method needs an optional `prompt_variables: dict[str, str] | None = None` parameter. When provided, these variables are merged into the prompt interpolation alongside the standard variables (WEB_URL, CONFIG_CONTEXT, etc.). This is the mechanism by which `ExploitExecutor` injects vulnerability queue data into exploit agent prompts without modifying the executor's core logic.

### ExploitExecutor (agents/exploit_executor.py)

Responsible for running a single exploit agent. Wraps the whitebox `AgentExecutor` with exploit-specific logic:

1. Read the corresponding `*_exploitation_queue.json` from the workspace
2. Parse the vulnerability entries using `shannon-core`'s `VulnerabilityQueue` model
3. Inject vulnerability details into the prompt context (endpoint, parameters, payload hints)
4. Delegate to whitebox's `AgentExecutor.execute()` for the standard agent lifecycle
5. Validate the deliverable (`*_exploitation_evidence.md`) exists after execution

```python
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
        config_path: str | None,
        api_key: str | None,
        pipeline_testing: bool = False,
    ) -> AgentMetrics:
        queue_path = deliverables_path / f"{vuln_type}_exploitation_queue.json"
        vulnerabilities = await self._load_queue(queue_path)
        # Vulnerability details are passed as extra prompt variables via
        # AgentExecutor.execute()'s prompt_variables parameter. The exploit
        # prompt templates use {{VULNERABILITY_ENTRIES}} to render the queue.
        return await self._executor.execute(
            agent_name=agent_name,
            repo_path=deliverables_path,
            web_url=web_url,
            deliverables_path=deliverables_path,
            config_path=config_path,
            api_key=api_key,
            pipeline_testing=pipeline_testing,
        )
```

### ReconExecutor (agents/recon_executor.py)

Runs standalone reconnaissance without source code. Simpler than ExploitExecutor — no queue reading, just launches the RECON_BLACKBOX agent:

```python
class ReconExecutor:
    def __init__(self, agent_executor: AgentExecutor):
        self._executor = agent_executor

    async def execute(
        self,
        workspace_path: Path,
        deliverables_path: Path,
        web_url: str,
        config_path: str | None,
        api_key: str | None,
        pipeline_testing: bool = False,
    ) -> AgentMetrics:
        return await self._executor.execute(
            agent_name=AgentName.RECON_BLACKBOX,
            repo_path=deliverables_path,
            web_url=web_url,
            deliverables_path=deliverables_path,
            config_path=config_path,
            api_key=api_key,
            pipeline_testing=pipeline_testing,
        )
```

### ExploitationChecker (services/exploitation_checker.py)

Reads a queue JSON file and returns whether exploitation should proceed:

```python
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
        data = await async_read_json(queue_path)
        return len(data.get("vulnerabilities", [])) > 0
```

### ReportAssembler (services/report_assembler.py)

Concatenates per-class deliverables into the final report:

```python
class ReportAssembler:
    @staticmethod
    async def assemble(
        deliverables_path: Path,
        vuln_classes: list[VulnClass],
        report_path: Path,
    ) -> None:
        sections = []
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

When `exploit=false`, the assembler first renders each queue JSON into a `*_findings.md` file using the deterministic renderer pattern from the TS project (no LLM in the loop), then concatenates.

## CLI Interface

```bash
# Independent scan
shannon-blackbox start --url https://target.com

# Continue from whitebox (shared workspace)
shannon-blackbox start --url https://target.com --workspace my-audit

# With config
shannon-blackbox start --url https://target.com -c ./config.yaml

# Full options
shannon-blackbox start \
    --url https://target.com \
    --output ./results \
    --workspace my-audit \
    --config ./config.yaml \
    --vuln-classes injection xss \
    --no-exploit \
    --pipeline-testing

# Management
shannon-blackbox logs <workspace>
shannon-blackbox workspaces
```

Click CLI implementation follows the same pattern as whitebox's `cli/main.py`:

```python
@click.group()
def cli():
    """Shannon Black-Box Scanner"""

@cli.command()
@click.option("--url", required=True, help="Target URL")
@click.option("--output", help="Output directory")
@click.option("--workspace", help="Workspace name (resume if exists)")
@click.option("--config", help="YAML config file path")
@click.option("--vuln-classes", multiple=True, help="Vuln classes to test")
@click.option("--no-exploit", is_flag=True, help="Skip exploitation phase")
@click.option("--pipeline-testing", is_flag=True, help="Fast iteration mode")
@click.option("--temporal-address", default="localhost:7233")
def start(url, output, workspace, config, vuln_classes, no_exploit, pipeline_testing, temporal_address):
    ...
```

## Configuration

Fully reuses `shannon-core`'s `Config` model. Blackbox-relevant config fields:

| Field | Usage in Blackbox |
|-------|-------------------|
| `authentication` | Browser-driven login before exploitation |
| `rules.avoid` (url_path, domain, etc.) | URL-level scope restrictions |
| `rules.focus` (url_path, domain, etc.) | URL-level scope targeting |
| `vuln_classes` | Which exploit agents to run |
| `exploit` | Whether to run exploitation phase |
| `report` | Report filtering (min_severity, min_confidence, guidance) |
| `rules_of_engagement` | Free-text constraints for agents |

## Error Handling

Reuses `shannon-core`'s error infrastructure:

- `PentestError` with `ErrorCode` enum for classification
- 3 retry attempts per agent with exponential backoff (matching TS project)
- Parallel exploit agents fail independently — one failure does not block others
- Final report includes all successful and failed agent results
- Temporal handles crash recovery via durable workflow state

## Testing Strategy

| Test Type | Scope |
|-----------|-------|
| Unit tests | ExploitationChecker, ReportAssembler, ExploitExecutor, ReconExecutor |
| Integration test | Full pipeline with mocked `run_claude_prompt`, all exploit agents, deliverable validation |
| CLI tests | Click command help output and option parsing |
| Compatibility tests | Verify output file names and JSON schemas match TS project |

## Output Compatibility

All output files match the TypeScript project exactly:

| File | Format | Source |
|------|--------|--------|
| `recon_deliverable.md` | Markdown | RECON_BLACKBOX agent |
| `injection_exploitation_evidence.md` | Markdown | INJECTION_EXPLOIT agent |
| `xss_exploitation_evidence.md` | Markdown | XSS_EXPLOIT agent |
| `auth_exploitation_evidence.md` | Markdown | AUTH_EXPLOIT agent |
| `authz_exploitation_evidence.md` | Markdown | AUTHZ_EXPLOIT agent |
| `ssrf_exploitation_evidence.md` | Markdown | SSRF_EXPLOIT agent |
| `comprehensive_security_assessment_report.md` | Markdown | Report assembler + REPORT agent |
| `session.json` | JSON | Session manager (compatible with TS format) |
| `workflow.log` | Text | Audit session (compatible with TS format) |
