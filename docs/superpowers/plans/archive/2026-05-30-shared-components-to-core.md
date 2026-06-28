# Extract Shared Components to Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Move all shared infrastructure components from `shannon-whitebox` into `shannon-core` so that `shannon-blackbox` can run independently without depending on the whitebox package.

**Architecture:** Move 9 source files (agents/executor, agents/runner, agents/validators, prompts/manager, session, git_manager, services/validate_authentication, services/playwright_config_writer, services/settings_writer) from whitebox into core, update all internal imports to use absolute `shannon_core.*` paths, then update whitebox and blackbox to import from core instead. No business logic changes — purely mechanical import path migration.

**Tech Stack:** Python 3.12+, hatchling build system, uv workspace, pytest + pytest-asyncio

---

## File Structure

### New files in core

| File | Responsibility |
|---|---|
| `packages/core/src/shannon_core/agents/__init__.py` | Package marker (empty) |
| `packages/core/src/shannon_core/agents/runner.py` | `ClaudeRunResult` dataclass + `run_claude_prompt` stub |
| `packages/core/src/shannon_core/agents/validators.py` | `validate_deliverable`, `get_vuln_type`, `get_queue_filename` |
| `packages/core/src/shannon_core/agents/executor.py` | `AgentExecutor` — orchestrates agent execution |
| `packages/core/src/shannon_core/prompts/__init__.py` | Package marker (empty) |
| `packages/core/src/shannon_core/prompts/manager.py` | `PromptManager` — template loading and interpolation |
| `packages/core/src/shannon_core/services/__init__.py` | Package marker (empty) |
| `packages/core/src/shannon_core/services/validate_authentication.py` | `validate_authentication` + `AuthValidationResult` |
| `packages/core/src/shannon_core/services/playwright_config_writer.py` | `write_stealth_config` + `cleanup_stealth_config` |
| `packages/core/src/shannon_core/services/settings_writer.py` | `sync_code_path_deny_rules` + `cleanup_settings` |
| `packages/core/src/shannon_core/session.py` | `SessionManager` — workspace lifecycle |
| `packages/core/src/shannon_core/git_manager.py` | `GitManager` — checkpoint/commit/rollback |

### New test files in core

| File | Moved from |
|---|---|
| `packages/core/tests/test_validators.py` | `packages/whitebox/tests/test_validators.py` |
| `packages/core/tests/test_prompt_manager.py` | `packages/whitebox/tests/test_prompt_manager.py` |
| `packages/core/tests/test_git_manager.py` | `packages/whitebox/tests/test_git_manager.py` |
| `packages/core/tests/test_session.py` | `packages/whitebox/tests/test_session.py` |
| `packages/core/tests/test_playwright_config_writer.py` | `packages/whitebox/tests/test_playwright_config_writer.py` |
| `packages/core/tests/test_settings_writer.py` | `packages/whitebox/tests/test_settings_writer.py` |
| `packages/core/tests/test_validate_authentication.py` | `packages/whitebox/tests/test_validate_authentication.py` |

### Modified files in whitebox (import path updates only)

| File | Change |
|---|---|
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | 3 imports: `shannon_whitebox.*` → `shannon_core.*` |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | 2 imports: `shannon_whitebox.*` → `shannon_core.*` |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | 1 import: `shannon_whitebox.session` → `shannon_core.session` |
| `packages/whitebox/tests/test_integration.py` | 4 imports: `shannon_whitebox.*` → `shannon_core.*` |

### Modified files in blackbox (import path updates only)

| File | Change |
|---|---|
| `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py` | 1 import: `shannon_whitebox.*` → `shannon_core.*` |
| `packages/blackbox/src/shannon_blackbox/agents/recon_executor.py` | 1 import: `shannon_whitebox.*` → `shannon_core.*` |
| `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` | 3 imports: `shannon_whitebox.*` → `shannon_core.*` |
| `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | 2 imports: `shannon_whitebox.*` → `shannon_core.*` |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | 1 import: `shannon_whitebox.*` → `shannon_core.*` |
| `packages/blackbox/tests/test_executors.py` | No change needed (imports from `shannon_blackbox.agents.*` which re-export from core) |
| `packages/blackbox/tests/test_integration.py` | 3 imports: `shannon_whitebox.*` → `shannon_core.*` |

### Modified config files

| File | Change |
|---|---|
| `packages/core/pyproject.toml` | Add `aiofiles>=23.0` dependency |
| `packages/blackbox/pyproject.toml` | Remove `shannon-whitebox` dependency |

### Deleted files from whitebox

All 9 source files + 3 `__init__.py` package markers + 7 test files (moved to core).

---

## Task 1: Create new package directories in core

**Files:**
- Create: `packages/core/src/shannon_core/agents/__init__.py`
- Create: `packages/core/src/shannon_core/prompts/__init__.py`
- Create: `packages/core/src/shannon_core/services/__init__.py`

- [x] **Step 1: Create agents package directory**

```bash
mkdir -p packages/core/src/shannon_core/agents
touch packages/core/src/shannon_core/agents/__init__.py
```

- [x] **Step 2: Create prompts package directory**

```bash
mkdir -p packages/core/src/shannon_core/prompts
touch packages/core/src/shannon_core/prompts/__init__.py
```

- [x] **Step 3: Create services package directory**

```bash
mkdir -p packages/core/src/shannon_core/services
touch packages/core/src/shannon_core/services/__init__.py
```

- [x] **Step 4: Commit**

```bash
git add packages/core/src/shannon_core/agents/__init__.py packages/core/src/shannon_core/prompts/__init__.py packages/core/src/shannon_core/services/__init__.py
git commit -m "feat(core): create agents, prompts, services package directories"
```

---

## Task 2: Move agents/runner.py to core

**Files:**
- Create: `packages/core/src/shannon_core/agents/runner.py`
- Test: `packages/core/tests/test_runner.py` (new — trivial smoke test since function raises NotImplementedError)

- [x] **Step 1: Write the failing test**

Create `packages/core/tests/test_runner.py`:

```python
from shannon_core.agents.runner import ClaudeRunResult, run_claude_prompt
import pytest


def test_claude_run_result_defaults():
    result = ClaudeRunResult()
    assert result.text == ""
    assert result.success is False
    assert result.duration == 0
    assert result.turns == 0
    assert result.cost == 0.0
    assert result.model is None
    assert result.structured_output is None
    assert result.error is None
    assert result.retryable is True


def test_claude_run_result_with_values():
    result = ClaudeRunResult(
        text="hello",
        success=True,
        duration=5000,
        turns=3,
        cost=0.05,
        model="claude-sonnet-4-6",
        structured_output={"key": "value"},
        error=None,
        retryable=False,
    )
    assert result.text == "hello"
    assert result.success is True
    assert result.cost == 0.05
    assert result.structured_output == {"key": "value"}


@pytest.mark.asyncio
async def test_run_claude_prompt_not_implemented():
    with pytest.raises(NotImplementedError, match="Claude Agent SDK"):
        await run_claude_prompt(prompt="test", repo_path="/tmp")
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd shannon-py && uv run pytest packages/core/tests/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.agents'`

- [x] **Step 3: Copy runner.py into core**

Create `packages/core/src/shannon_core/agents/runner.py` — exact copy of `packages/whitebox/src/shannon_whitebox/agents/runner.py` (no import changes needed — it has no dependencies):

```python
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
    raise NotImplementedError(
        "Claude Agent SDK Python integration pending. "
        "Install claude-agent-sdk and implement this function."
    )
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd shannon-py && uv run pytest packages/core/tests/test_runner.py -v`
Expected: 3 passed

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/runner.py packages/core/tests/test_runner.py
git commit -m "feat(core): add agents/runner — ClaudeRunResult and run_claude_prompt"
```

---

## Task 3: Move agents/validators.py to core

**Files:**
- Create: `packages/core/src/shannon_core/agents/validators.py`
- Create: `packages/core/tests/test_validators.py`
- Reference: `packages/whitebox/tests/test_validators.py` (original — will be deleted in Task 10)

- [x] **Step 1: Copy validators.py into core**

Create `packages/core/src/shannon_core/agents/validators.py` — exact copy of whitebox version (only imports from `shannon_core.models`, no changes needed):

```python
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
    if value.endswith("-exploit"):
        return value.replace("-exploit", "")
    return None

def get_queue_filename(agent_name: AgentName) -> str | None:
    vuln_type = get_vuln_type(agent_name)
    if vuln_type:
        return f"{vuln_type}_exploitation_queue.json"
    return None
```

- [x] **Step 2: Copy test with updated import**

Create `packages/core/tests/test_validators.py` — copied from whitebox, import changed from `shannon_whitebox.agents.validators` to `shannon_core.agents.validators`:

```python
import pytest
from pathlib import Path
from shannon_core.models.agents import AgentName
from shannon_core.agents.validators import validate_deliverable, get_vuln_type, get_queue_filename

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

- [x] **Step 3: Run tests to verify they pass**

Run: `cd shannon-py && uv run pytest packages/core/tests/test_validators.py -v`
Expected: 7 passed

- [x] **Step 4: Commit**

```bash
git add packages/core/src/shannon_core/agents/validators.py packages/core/tests/test_validators.py
git commit -m "feat(core): add agents/validators — validate_deliverable, get_vuln_type, get_queue_filename"
```

---

## Task 4: Move prompts/manager.py to core

**Files:**
- Create: `packages/core/src/shannon_core/prompts/manager.py`
- Create: `packages/core/tests/test_prompt_manager.py`

- [x] **Step 1: Copy prompts/manager.py into core**

Create `packages/core/src/shannon_core/prompts/manager.py` — exact copy (only imports from `shannon_core.models`, no changes needed):

```python
import re
from pathlib import Path

from shannon_core.models.agents import PLAYWRIGHT_SESSION_MAPPING
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
        template = self._interpolate(template, variables, config, template_name)
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
        template_name: str = "",
    ) -> str:
        result = template
        result = result.replace("{{WEB_URL}}", variables.get("web_url", ""))
        result = result.replace("{{REPO_PATH}}", variables.get("repo_path", ""))
        playwright_session = variables.get("playwright_session") or PLAYWRIGHT_SESSION_MAPPING.get(template_name, "agent1")
        result = result.replace("{{PLAYWRIGHT_SESSION}}", playwright_session)

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

            report_filters_block = self._build_report_filters_block(config)
            result = result.replace("{{REPORT_FILTERS_BLOCK}}", report_filters_block)

            if config.report:
                report_rules = self._build_report_filter_rules(config.report)
                result = result.replace("{{REPORT_FILTER_RULES}}", report_rules)

            vuln_subsections = self._build_vuln_summary_subsections(config.vuln_classes)
            result = result.replace("{{VULN_SUMMARY_SUBSECTIONS}}", vuln_subsections)
        else:
            result = result.replace("{{DESCRIPTION}}", "")
            result = result.replace("{{AUTH_CONTEXT}}", "No authentication configured")
            result = result.replace("{{RULES_AVOID}}", "None")
            result = result.replace("{{RULES_FOCUS}}", "None")
            result = result.replace("{{VULN_CLASSES_TESTED}}", "injection, xss, auth, authz, ssrf")
            result = result.replace("{{EXPLOITATION}}", "enabled")
            result = result.replace("{{RULES_OF_ENGAGEMENT}}", "")
            result = result.replace("{{REPORT_FILTERS_BLOCK}}", "")
            result = result.replace("{{REPORT_FILTER_RULES}}", "")
            result = result.replace("{{VULN_SUMMARY_SUBSECTIONS}}", "")

        result = result.replace("{{LOGIN_INSTRUCTIONS}}", "")

        for key, value in variables.items():
            token = "{{" + key.upper() + "}}"
            if token in result:
                result = result.replace(token, value)

        result = re.sub(r"\n{3,}", "\n\n", result)
        return result

    def _build_report_filters_block(self, config) -> str:
        """Render the REPORT_FILTERS_BLOCK conditional section."""
        report = config.report
        if not report or not any([
            report.min_severity, report.min_confidence, report.guidance,
        ]):
            return ""
        rules_text = self._build_report_filter_rules(report)
        return (
            "<report_filters>\n"
            "Apply the following filters to the report:\n"
            f"{rules_text}\n"
            "</report_filters>"
        )

    def _build_report_filter_rules(self, report) -> str:
        """Generate human-readable filter rules from ReportConfig."""
        lines = []
        if report.min_severity:
            lines.append(f"- Exclude vulnerabilities below **{report.min_severity.upper()}** severity")
        if report.min_confidence:
            lines.append(f"- Exclude vulnerabilities below **{report.min_confidence.upper()}** confidence")
        if report.guidance:
            lines.append(f"- Additional guidance: {report.guidance}")
        return "\n".join(lines)

    def _build_vuln_summary_subsections(self, vuln_classes: list[str]) -> str:
        """Generate per-class summary subsection templates."""
        lines = []
        for vc in vuln_classes:
            label = vc.replace("-", " ").title()
            lines.append(
                f"### {label}\n"
                f"Count: {{number of confirmed {vc} vulnerabilities}}\n"
                f"Severity range: {{range}}\n"
                f"Key findings: {{1-2 sentence summary}}"
            )
        return "\n\n".join(lines)
```

- [x] **Step 2: Copy test with updated import**

Create `packages/core/tests/test_prompt_manager.py` — import changed from `shannon_whitebox.prompts.manager` to `shannon_core.prompts.manager`:

```python
import pytest
from pathlib import Path
from shannon_core.prompts.manager import PromptManager

@pytest.fixture
def prompts_dir(tmp_path):
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

- [x] **Step 3: Run tests to verify they pass**

Run: `cd shannon-py && uv run pytest packages/core/tests/test_prompt_manager.py -v`
Expected: 4 passed

- [x] **Step 4: Commit**

```bash
git add packages/core/src/shannon_core/prompts/manager.py packages/core/tests/test_prompt_manager.py
git commit -m "feat(core): add prompts/manager — PromptManager with template loading"
```

---

## Task 5: Move session.py to core

**Files:**
- Create: `packages/core/src/shannon_core/session.py`
- Create: `packages/core/tests/test_session.py`

- [x] **Step 1: Copy session.py into core**

Create `packages/core/src/shannon_core/session.py` — exact copy (only imports from `shannon_core.models`):

```python
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

- [x] **Step 2: Copy test with updated import**

Create `packages/core/tests/test_session.py` — import changed from `shannon_whitebox.session` to `shannon_core.session`:

```python
import json
import pytest
from pathlib import Path
from shannon_core.session import SessionManager

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

- [x] **Step 3: Run tests to verify they pass**

Run: `cd shannon-py && uv run pytest packages/core/tests/test_session.py -v`
Expected: 5 passed

- [x] **Step 4: Commit**

```bash
git add packages/core/src/shannon_core/session.py packages/core/tests/test_session.py
git commit -m "feat(core): add session — SessionManager for workspace lifecycle"
```

---

## Task 6: Move git_manager.py to core

**Files:**
- Create: `packages/core/src/shannon_core/git_manager.py`
- Create: `packages/core/tests/test_git_manager.py`

- [x] **Step 1: Copy git_manager.py into core**

Create `packages/core/src/shannon_core/git_manager.py` — exact copy (only imports from `shannon_core.models`):

```python
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

- [x] **Step 2: Copy test with updated import**

Create `packages/core/tests/test_git_manager.py` — import changed from `shannon_whitebox.git_manager` to `shannon_core.git_manager`:

```python
import pytest
import subprocess
from pathlib import Path
from shannon_core.git_manager import GitManager

@pytest.fixture
def git_repo(tmp_path):
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

def test_commit_success(git_repo):
    (git_repo / "deliverable.md").write_text("# Report")
    GitManager.commit(git_repo, "pre-recon")
    result = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=git_repo, capture_output=True, text=True, check=True,
    )
    assert "pre-recon" in result.stdout

def test_rollback(git_repo):
    (git_repo / "bad_file.txt").write_text("bad content")
    subprocess.run(["git", "add", "."], cwd=git_repo, capture_output=True, check=True)
    GitManager.rollback(git_repo, "test failure")
    assert not (git_repo / "bad_file.txt").exists()

def test_get_commit_hash(git_repo):
    h = GitManager.get_commit_hash(git_repo)
    assert h is not None
    assert len(h) == 40
```

- [x] **Step 3: Run tests to verify they pass**

Run: `cd shannon-py && uv run pytest packages/core/tests/test_git_manager.py -v`
Expected: 4 passed

- [x] **Step 4: Commit**

```bash
git add packages/core/src/shannon_core/git_manager.py packages/core/tests/test_git_manager.py
git commit -m "feat(core): add git_manager — GitManager for checkpoint/commit/rollback"
```

---

## Task 7: Move services/playwright_config_writer.py to core

**Files:**
- Create: `packages/core/src/shannon_core/services/playwright_config_writer.py`
- Create: `packages/core/tests/test_playwright_config_writer.py`

- [x] **Step 1: Copy playwright_config_writer.py into core**

Create `packages/core/src/shannon_core/services/playwright_config_writer.py` — exact copy (no dependencies on shannon_core at all):

```python
"""Write stealth Playwright config + anti-detection init script.

Direct port of shannon/apps/worker/src/ai/playwright-config-writer.ts.
"""

from __future__ import annotations

import json
from pathlib import Path

_STEALTH_INIT_SCRIPT = """\
// Remove navigator.webdriver flag set by Playwright/Chrome automation
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
delete Object.getPrototypeOf(navigator).webdriver;

// Override navigator.plugins to appear as a real browser
Object.defineProperty(navigator, 'plugins', {
  get: () => {
    const arr = [
      { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
      { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
    ];
    arr.__proto__ = PluginArray.prototype;
    return arr;
  },
});

window.chrome = window.chrome || {};
window.chrome.runtime = window.chrome.runtime || {
  PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
  PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
  PlatformNaclArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
  RequestUpdateCheckStatus: { THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' },
  OnInstalledReason: { INSTALL: 'install', UPDATE: 'update', CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update' },
  OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
};
"""


def _build_stealth_config(init_script_path: str) -> dict:
    return {
        "browser": {
            "browserName": "chromium",
            "launchOptions": {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled"],
                "ignoreDefaultArgs": ["--enable-automation"],
            },
            "contextOptions": {
                "viewport": {"width": 1920, "height": 1080},
                "locale": "en-US",
                "extraHTTPHeaders": {"Accept-Language": "en-US,en;q=0.9"},
                "userAgent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            },
            "initScript": [init_script_path],
        },
    }


def write_stealth_config(source_dir: str) -> dict:
    """Write .playwright/cli.config.json + scripts/stealth.js under *source_dir*.

    Returns ``{"result": "wrote"|"skipped-existing", "configPath": str}``.
    """
    playwright_dir = Path(source_dir) / ".playwright"
    config_path = playwright_dir / "cli.config.json"

    if config_path.exists():
        return {"result": "skipped-existing", "configPath": str(config_path)}

    init_script_path = playwright_dir / "scripts" / "stealth.js"
    init_script_path.parent.mkdir(parents=True, exist_ok=True)
    init_script_path.write_text(_STEALTH_INIT_SCRIPT)

    config = _build_stealth_config(str(init_script_path))
    config_path.write_text(json.dumps(config, indent=2))

    return {"result": "wrote", "configPath": str(config_path)}


def cleanup_stealth_config(source_dir: str) -> None:
    """Remove the .playwright/ directory created by write_stealth_config."""
    import shutil

    pw_dir = Path(source_dir) / ".playwright"
    if pw_dir.exists():
        shutil.rmtree(pw_dir)
```

- [x] **Step 2: Copy test with updated import**

Create `packages/core/tests/test_playwright_config_writer.py` — imports changed from `shannon_whitebox.services.playwright_config_writer` to `shannon_core.services.playwright_config_writer`:

```python
# shannon-py/packages/core/tests/test_playwright_config_writer.py
import json
from pathlib import Path

import pytest

from shannon_core.services.playwright_config_writer import (
    write_stealth_config,
    cleanup_stealth_config,
)


class TestWriteStealthConfig:
    def test_creates_config_and_script(self, tmp_path):
        result = write_stealth_config(str(tmp_path))
        assert result["result"] == "wrote"

        config_path = Path(result["configPath"])
        assert config_path.exists()

        # Config references init script by absolute path
        config = json.loads(config_path.read_text())
        assert config["browser"]["browserName"] == "chromium"
        assert config["browser"]["launchOptions"]["headless"] is True
        init_scripts = config["browser"]["initScript"]
        assert len(init_scripts) == 1
        assert Path(init_scripts[0]).exists()

    def test_stealth_script_content(self, tmp_path):
        write_stealth_config(str(tmp_path))
        script = tmp_path / ".playwright" / "scripts" / "stealth.js"
        content = script.read_text()
        assert "navigator.webdriver" in content
        assert "chrome.runtime" in content
        assert "navigator.plugins" in content

    def test_skips_existing_config(self, tmp_path):
        playwright_dir = tmp_path / ".playwright"
        playwright_dir.mkdir()
        (playwright_dir / "cli.config.json").write_text('{"existing": true}')

        result = write_stealth_config(str(tmp_path))
        assert result["result"] == "skipped-existing"
        # Verify it didn't overwrite
        config = json.loads((playwright_dir / "cli.config.json").read_text())
        assert config == {"existing": True}


class TestCleanupStealthConfig:
    def test_removes_playwright_dir(self, tmp_path):
        write_stealth_config(str(tmp_path))
        assert (tmp_path / ".playwright").exists()

        cleanup_stealth_config(str(tmp_path))
        assert not (tmp_path / ".playwright").exists()

    def test_noop_when_no_dir(self, tmp_path):
        cleanup_stealth_config(str(tmp_path))  # Should not raise
```

- [x] **Step 3: Run tests to verify they pass**

Run: `cd shannon-py && uv run pytest packages/core/tests/test_playwright_config_writer.py -v`
Expected: 5 passed

- [x] **Step 4: Commit**

```bash
git add packages/core/src/shannon_core/services/playwright_config_writer.py packages/core/tests/test_playwright_config_writer.py
git commit -m "feat(core): add services/playwright_config_writer — stealth config writer"
```

---

## Task 8: Move services/settings_writer.py to core

**Files:**
- Create: `packages/core/src/shannon_core/services/settings_writer.py`
- Create: `packages/core/tests/test_settings_writer.py`

- [x] **Step 1: Copy settings_writer.py into core**

Create `packages/core/src/shannon_core/services/settings_writer.py` — exact copy (only imports `Rule` from `shannon_core.models`):

```python
"""Write ~/.claude/settings.json with permissions.deny rules from code_path avoid patterns.

Direct port of shannon/apps/worker/src/ai/settings-writer.ts.
"""

from __future__ import annotations

import json
from pathlib import Path

from shannon_core.models.config import Rule

_FILE_TOOLS = ("Read", "Edit")


def _settings_path() -> Path:
    """Compute the settings path at call time so monkeypatched Path.home works in tests."""
    return Path.home() / ".claude" / "settings.json"


def _strip_leading_dotslash(pattern: str) -> str:
    """Remove a leading './' prefix from the pattern, preserving dots that are part of the name (e.g. '.env')."""
    if pattern.startswith("./"):
        return pattern[2:]
    return pattern


def _deny_entries_for(pattern: str) -> list[str]:
    arg = f"./{_strip_leading_dotslash(pattern)}"
    return [f"{tool}({arg})" for tool in _FILE_TOOLS]


def sync_code_path_deny_rules(avoid_rules: list[Rule]) -> None:
    """Write deny rules for all code_path avoid patterns; remove file when none."""
    code_path_patterns = [
        r.value for r in avoid_rules
        if r.type == "code_path" and r.value and r.value.strip()
    ]

    settings_path = _settings_path()

    if not code_path_patterns:
        if settings_path.exists():
            settings_path.unlink()
        return

    # Read existing settings or start fresh
    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            settings = {}

    # Merge deny rules
    permissions = settings.setdefault("permissions", {})
    permissions["deny"] = [
        entry
        for pattern in code_path_patterns
        for entry in _deny_entries_for(pattern)
    ]

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2))


def cleanup_settings() -> None:
    """Remove the settings file created by sync_code_path_deny_rules."""
    settings_path = _settings_path()
    if settings_path.exists():
        settings_path.unlink()
```

- [x] **Step 2: Copy test with updated import**

Create `packages/core/tests/test_settings_writer.py` — imports changed from `shannon_whitebox.services.settings_writer` to `shannon_core.services.settings_writer`:

```python
import json
from pathlib import Path

import pytest

from shannon_core.models.config import Rule, Rules
from shannon_core.services.settings_writer import (
    sync_code_path_deny_rules,
    cleanup_settings,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect ~/.claude to a temp directory."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    return claude_dir


class TestSyncCodePathDenyRules:
    def test_writes_deny_rules(self, fake_home):
        rules = Rules(
            avoid=[
                Rule(description="secrets", type="code_path", value="secrets/**"),
                Rule(description="env files", type="code_path", value=".env*"),
                Rule(description="skip this URL", type="url_path", value="/admin"),
            ],
            focus=[],
        )
        sync_code_path_deny_rules(rules.avoid)

        settings_path = fake_home / "settings.json"
        assert settings_path.exists()
        data = json.loads(settings_path.read_text())
        assert "permissions" in data
        deny_list = data["permissions"]["deny"]
        # 2 code_path rules × 2 tools (Read, Edit) = 4 entries
        assert len(deny_list) == 4
        assert "Read(./secrets/**)" in deny_list
        assert "Edit(./secrets/**)" in deny_list
        assert "Read(./.env*)" in deny_list
        assert "Edit(./.env*)" in deny_list

    def test_removes_settings_when_no_code_path_rules(self, fake_home):
        settings_path = fake_home / "settings.json"
        settings_path.write_text('{"permissions": {"deny": []}}')

        rules = Rules(
            avoid=[Rule(description="url", type="url_path", value="/admin")],
            focus=[],
        )
        sync_code_path_deny_rules(rules.avoid)
        assert not settings_path.exists()

    def test_strips_leading_dots_slashes(self, fake_home):
        rules = Rules(
            avoid=[Rule(description="test", type="code_path", value="./secrets/**")],
            focus=[],
        )
        sync_code_path_deny_rules(rules.avoid)
        settings_path = fake_home / "settings.json"
        data = json.loads(settings_path.read_text())
        deny_list = data["permissions"]["deny"]
        assert "Read(./secrets/**)" in deny_list

    def test_empty_pattern_produces_no_deny_entries(self, fake_home):
        rules = Rules(
            avoid=[
                Rule(description="empty", type="code_path", value=""),
                Rule(description="whitespace", type="code_path", value="   "),
                Rule(description="valid", type="code_path", value="secrets/**"),
            ],
            focus=[],
        )
        sync_code_path_deny_rules(rules.avoid)
        settings_path = fake_home / "settings.json"
        data = json.loads(settings_path.read_text())
        deny_list = data["permissions"]["deny"]
        # Only the valid pattern should produce entries (2 tools)
        assert len(deny_list) == 2
        assert "Read(./secrets/**)" in deny_list
        assert "Edit(./secrets/**)" in deny_list

    def test_creates_parent_directory(self, tmp_path, monkeypatch):
        """Verify ~/.claude/ is created when it doesn't exist yet."""
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        assert not (tmp_path / ".claude").exists()

        rules = Rules(
            avoid=[Rule(description="secrets", type="code_path", value="secrets/**")],
            focus=[],
        )
        sync_code_path_deny_rules(rules.avoid)

        settings_path = tmp_path / ".claude" / "settings.json"
        assert settings_path.exists()
        data = json.loads(settings_path.read_text())
        assert "Read(./secrets/**)" in data["permissions"]["deny"]

    def test_merges_into_existing_settings(self, fake_home):
        settings_path = fake_home / "settings.json"
        settings_path.write_text(json.dumps({
            "someOtherKey": "preserved",
            "permissions": {"allow": ["Bash(git log)"]},
        }))

        rules = Rules(
            avoid=[Rule(description="secrets", type="code_path", value="secrets/**")],
            focus=[],
        )
        sync_code_path_deny_rules(rules.avoid)

        data = json.loads(settings_path.read_text())
        assert data["someOtherKey"] == "preserved"
        assert data["permissions"]["allow"] == ["Bash(git log)"]
        assert "Read(./secrets/**)" in data["permissions"]["deny"]


class TestCleanupSettings:
    def test_removes_settings_file(self, fake_home):
        settings_path = fake_home / "settings.json"
        settings_path.write_text('{"permissions": {"deny": ["Read(./x)"]}}')
        cleanup_settings()
        assert not settings_path.exists()

    def test_noop_when_no_file(self, fake_home):
        cleanup_settings()  # Should not raise
```

- [x] **Step 3: Run tests to verify they pass**

Run: `cd shannon-py && uv run pytest packages/core/tests/test_settings_writer.py -v`
Expected: 8 passed

- [x] **Step 4: Commit**

```bash
git add packages/core/src/shannon_core/services/settings_writer.py packages/core/tests/test_settings_writer.py
git commit -m "feat(core): add services/settings_writer — deny rules for code paths"
```

---

## Task 9: Move agents/executor.py to core (depends on Tasks 2, 3, 6, 4)

**Files:**
- Create: `packages/core/src/shannon_core/agents/executor.py`

- [x] **Step 1: Copy executor.py into core with updated internal imports**

Create `packages/core/src/shannon_core/agents/executor.py` — relative imports changed to absolute `shannon_core.*`:

```python
import json
import time
from pathlib import Path

from shannon_core.config.parser import distribute_config, parse_config
from shannon_core.models.agents import AgentName, AGENTS
from shannon_core.models.config import Config
from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.models.metrics import AgentMetrics
from shannon_core.utils.billing import is_spending_cap_behavior

from shannon_core.agents.runner import ClaudeRunResult, run_claude_prompt
from shannon_core.agents.validators import get_queue_filename, get_vuln_type, validate_deliverable
from shannon_core.git_manager import GitManager
from shannon_core.prompts.manager import PromptManager

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
        prompt_variables: dict[str, str] | None = None,
        prompt_override: str | None = None,
    ) -> AgentMetrics:
        defn = AGENTS[agent_name]
        repo = Path(repo_path)
        deliverables = Path(deliverables_path) if deliverables_path else repo / ".shannon" / "deliverables"
        deliverables.mkdir(parents=True, exist_ok=True)

        config: Config | None = None
        if config_path:
            config = parse_config(config_path)
        distributed = distribute_config(config)

        variables = {"web_url": web_url, "repo_path": str(repo)}
        if prompt_variables:
            variables.update(prompt_variables)
        template_name = prompt_override or defn.prompt_template
        prompt = self.prompt_manager.load_sync(
            template_name,
            variables=variables,
            config=distributed,
            pipeline_testing=pipeline_testing,
        )

        GitManager.create_checkpoint(deliverables, agent_name)

        start_time = time.monotonic()
        result = await run_claude_prompt(
            prompt=prompt,
            repo_path=str(repo),
            model_tier=defn.model_tier,
            api_key=api_key,
            deliverables_subdir=str(deliverables.relative_to(repo)) if deliverables.is_relative_to(repo) else None,
        )
        duration_ms = int((time.monotonic() - start_time) * 1000)

        if result.success and is_spending_cap_behavior(result.turns, result.cost, result.text):
            GitManager.rollback(deliverables, "spending cap detected")
            raise PentestError(
                f"Spending cap likely reached (turns={result.turns}, cost=${result.cost})",
                "billing",
                retryable=True,
                error_code=ErrorCode.SPENDING_CAP_REACHED,
            )

        if not result.success:
            GitManager.rollback(deliverables, "execution failure")
            raise PentestError(
                result.error or f"Agent {agent_name.value} execution failed",
                "validation",
                retryable=result.retryable,
                error_code=ErrorCode.AGENT_EXECUTION_FAILED,
            )

        queue_filename = get_queue_filename(agent_name)
        if result.structured_output is not None and queue_filename:
            queue_path = deliverables / queue_filename
            queue_path.write_text(json.dumps(result.structured_output, indent=2), encoding="utf-8")

        await validate_deliverable(deliverables, agent_name)

        GitManager.commit(deliverables, agent_name)

        return AgentMetrics(
            duration_ms=duration_ms,
            cost_usd=result.cost,
            num_turns=result.turns,
            model=result.model,
        )
```

- [x] **Step 2: Verify the import resolves**

Run: `cd shannon-py && uv run python -c "from shannon_core.agents.executor import AgentExecutor; print('OK')"`
Expected: `OK`

- [x] **Step 3: Commit**

```bash
git add packages/core/src/shannon_core/agents/executor.py
git commit -m "feat(core): add agents/executor — AgentExecutor with absolute imports"
```

---

## Task 10: Move services/validate_authentication.py to core (depends on Task 9)

**Files:**
- Create: `packages/core/src/shannon_core/services/validate_authentication.py`
- Create: `packages/core/tests/test_validate_authentication.py`

- [x] **Step 1: Copy validate_authentication.py into core with updated TYPE_CHECKING imports**

Create `packages/core/src/shannon_core/services/validate_authentication.py` — TYPE_CHECKING imports changed from `shannon_whitebox.*` to `shannon_core.*`:

```python
"""Preflight authentication validation — reuses AgentExecutor to drive a browser login check."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shannon_core.models.agents import AgentName

if TYPE_CHECKING:
    from shannon_core.agents.executor import AgentExecutor
    from shannon_core.prompts.manager import PromptManager


@dataclass
class AuthValidationResult:
    success: bool
    failure_point: str | None = None  # "username_or_password" | "totp_secret" | "out_of_band"
    failure_detail: str | None = None


async def validate_authentication(
    *,
    web_url: str,
    config_path: str | None,
    prompt_manager: PromptManager,
    executor: AgentExecutor,
    repo_path: str = "",
    api_key: str | None = None,
) -> AuthValidationResult:
    """Validate user-supplied credentials by running the validate-authentication agent.

    Returns ``AuthValidationResult(success=True)`` when no auth config is present
    (nothing to validate) or when the agent confirms successful login.
    """
    if not config_path:
        return AuthValidationResult(success=True)

    # Try to parse config and check for authentication section
    try:
        from shannon_core.config.parser import parse_config, distribute_config
        config = parse_config(config_path)
        dist_config = distribute_config(config)
    except Exception:
        return AuthValidationResult(success=True)

    if not dist_config.authentication:
        return AuthValidationResult(success=True)

    # Execute as a one-shot agent using the existing executor infrastructure
    metrics = await executor.execute(
        agent_name=AgentName.PRE_RECON,  # Borrow pre-recon name — actual prompt is overridden
        repo_path=repo_path or "/tmp/shannon-auth-check",
        web_url=web_url,
        config_path=config_path,
        api_key=api_key,
        prompt_override="validate-authentication",
    )

    return AuthValidationResult(success=True)
```

- [x] **Step 2: Copy test with updated import**

Create `packages/core/tests/test_validate_authentication.py` — imports changed from `shannon_whitebox.services.validate_authentication` to `shannon_core.services.validate_authentication`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_auth_validation_no_config():
    """When config_path is None, skip validation and return success."""
    from shannon_core.services.validate_authentication import validate_authentication

    mock_pm = MagicMock()
    mock_executor = MagicMock()

    result = await validate_authentication(
        web_url="https://example.com",
        config_path=None,
        prompt_manager=mock_pm,
        executor=mock_executor,
    )
    assert result.success is True
    mock_executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_auth_validation_config_no_auth_section():
    """When config exists but has no authentication section, return success without calling executor."""
    from shannon_core.services.validate_authentication import validate_authentication

    mock_pm = MagicMock()
    mock_executor = MagicMock()

    mock_dist_config = MagicMock()
    mock_dist_config.authentication = None

    with patch("shannon_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("shannon_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    assert result.success is True
    mock_executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_auth_validation_with_config_calls_executor():
    """When authentication config exists, executor.execute is called with prompt_override."""
    from shannon_core.services.validate_authentication import validate_authentication

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=MagicMock(
        duration_ms=5000, cost_usd=0.01, num_turns=3, model="claude-sonnet-4-6",
    ))
    mock_pm = MagicMock()

    mock_dist_config = MagicMock()
    mock_dist_config.authentication = {"username": "admin", "password": "pass123"}

    with patch("shannon_core.config.parser.parse_config", return_value=MagicMock()), \
         patch("shannon_core.config.parser.distribute_config", return_value=mock_dist_config):
        result = await validate_authentication(
            web_url="https://example.com",
            config_path="/path/to/config.yaml",
            prompt_manager=mock_pm,
            executor=mock_executor,
        )

    assert result.success is True
    mock_executor.execute.assert_called_once()
    call_kwargs = mock_executor.execute.call_args.kwargs
    assert call_kwargs.get("prompt_override") == "validate-authentication"
```

- [x] **Step 3: Run tests to verify they pass**

Run: `cd shannon-py && uv run pytest packages/core/tests/test_validate_authentication.py -v`
Expected: 3 passed

- [x] **Step 4: Commit**

```bash
git add packages/core/src/shannon_core/services/validate_authentication.py packages/core/tests/test_validate_authentication.py
git commit -m "feat(core): add services/validate_authentication — auth preflight check"
```

---

## Task 11: Update core pyproject.toml — add aiofiles dependency

**Files:**
- Modify: `packages/core/pyproject.toml`

- [x] **Step 1: Add aiofiles to core dependencies**

In `packages/core/pyproject.toml`, change the dependencies section from:

```toml
dependencies = [
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "httpx>=0.27",
]
```

to:

```toml
dependencies = [
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "httpx>=0.27",
    "aiofiles>=23.0",
]
```

- [x] **Step 2: Sync workspace**

Run: `cd shannon-py && uv sync`
Expected: `Resolved X packages, installed Y packages`

- [x] **Step 3: Run all core tests**

Run: `cd shannon-py && uv run pytest packages/core/tests/ -v`
Expected: All tests pass (existing 10 + new 7 test files = 17 test files)

- [x] **Step 4: Commit**

```bash
git add packages/core/pyproject.toml uv.lock
git commit -m "feat(core): add aiofiles dependency for AgentExecutor"
```

---

## Task 12: Update whitebox imports to point at core

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` (lines 11-13)
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` (lines 14-15)
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py` (line 5)
- Modify: `packages/whitebox/tests/test_integration.py` (lines 9-12)

- [x] **Step 1: Update pipeline/activities.py imports**

In `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`, change lines 11-13 from:

```python
from shannon_whitebox.agents.executor import AgentExecutor
from shannon_whitebox.prompts.manager import PromptManager
from shannon_whitebox.session import SessionManager
```

to:

```python
from shannon_core.agents.executor import AgentExecutor
from shannon_core.prompts.manager import PromptManager
from shannon_core.session import SessionManager
```

The inline import on line 92 also changes from:

```python
from shannon_whitebox.services.validate_authentication import validate_authentication
from shannon_whitebox.prompts.manager import PromptManager
from shannon_whitebox.agents.executor import AgentExecutor
```

to:

```python
from shannon_core.services.validate_authentication import validate_authentication
from shannon_core.prompts.manager import PromptManager
from shannon_core.agents.executor import AgentExecutor
```

- [x] **Step 2: Update pipeline/workflows.py imports**

In `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`, change lines 14-15 from:

```python
from shannon_whitebox.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
from shannon_whitebox.services.playwright_config_writer import write_stealth_config, cleanup_stealth_config
```

to:

```python
from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
from shannon_core.services.playwright_config_writer import write_stealth_config, cleanup_stealth_config
```

- [x] **Step 3: Update cli/main.py import**

In `packages/whitebox/src/shannon_whitebox/cli/main.py`, change line 5 from:

```python
from shannon_whitebox.session import SessionManager
```

to:

```python
from shannon_core.session import SessionManager
```

- [x] **Step 4: Update test_integration.py imports**

In `packages/whitebox/tests/test_integration.py`, change lines 9-12 from:

```python
from shannon_whitebox.agents.executor import AgentExecutor
from shannon_whitebox.prompts.manager import PromptManager
from shannon_whitebox.session import SessionManager
from shannon_whitebox.agents.runner import ClaudeRunResult
```

to:

```python
from shannon_core.agents.executor import AgentExecutor
from shannon_core.prompts.manager import PromptManager
from shannon_core.session import SessionManager
from shannon_core.agents.runner import ClaudeRunResult
```

Also update the `run_claude_prompt` patch target on line 55 from:

```python
with patch("shannon_whitebox.agents.executor.run_claude_prompt", side_effect=mock_run_claude):
```

to:

```python
with patch("shannon_core.agents.executor.run_claude_prompt", side_effect=mock_run_claude):
```

- [x] **Step 5: Run whitebox tests**

Run: `cd shannon-py && uv run pytest packages/whitebox/tests/ -v --ignore=packages/whitebox/tests/test_git_manager.py --ignore=packages/whitebox/tests/test_session.py --ignore=packages/whitebox/tests/test_prompt_manager.py --ignore=packages/whitebox/tests/test_validators.py --ignore=packages/whitebox/tests/test_playwright_config_writer.py --ignore=packages/whitebox/tests/test_settings_writer.py --ignore=packages/whitebox/tests/test_validate_authentication.py`
Expected: test_cli.py and test_integration.py pass

Note: The 7 test files for moved components still import from `shannon_whitebox` — they will be deleted in Task 14.

- [x] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/src/shannon_whitebox/cli/main.py packages/whitebox/tests/test_integration.py
git commit -m "refactor(whitebox): update imports to use shannon_core for moved components"
```

---

## Task 13: Update blackbox imports and dependency

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py` (line 7)
- Modify: `packages/blackbox/src/shannon_blackbox/agents/recon_executor.py` (line 6)
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` (lines 9-10, 54-56)
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` (lines 14-15)
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py` (line 7)
- Modify: `packages/blackbox/tests/test_integration.py` (lines 9-11)
- Modify: `packages/blackbox/pyproject.toml` (remove shannon-whitebox)

- [x] **Step 1: Update agents/exploit_executor.py**

In `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`, change line 7 from:

```python
from shannon_whitebox.agents.executor import AgentExecutor
```

to:

```python
from shannon_core.agents.executor import AgentExecutor
```

- [x] **Step 2: Update agents/recon_executor.py**

In `packages/blackbox/src/shannon_blackbox/agents/recon_executor.py`, change line 6 from:

```python
from shannon_whitebox.agents.executor import AgentExecutor
```

to:

```python
from shannon_core.agents.executor import AgentExecutor
```

- [x] **Step 3: Update pipeline/activities.py**

In `packages/blackbox/src/shannon_blackbox/pipeline/activities.py`, change lines 9-10 from:

```python
from shannon_whitebox.agents.executor import AgentExecutor
from shannon_whitebox.prompts.manager import PromptManager
```

to:

```python
from shannon_core.agents.executor import AgentExecutor
from shannon_core.prompts.manager import PromptManager
```

Change lines 54-56 from:

```python
from shannon_whitebox.services.validate_authentication import validate_authentication
from shannon_whitebox.prompts.manager import PromptManager
from shannon_whitebox.agents.executor import AgentExecutor
```

to:

```python
from shannon_core.services.validate_authentication import validate_authentication
from shannon_core.prompts.manager import PromptManager
from shannon_core.agents.executor import AgentExecutor
```

- [x] **Step 4: Update pipeline/workflows.py**

In `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`, change lines 14-15 from:

```python
from shannon_whitebox.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
from shannon_whitebox.services.playwright_config_writer import write_stealth_config, cleanup_stealth_config
```

to:

```python
from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
from shannon_core.services.playwright_config_writer import write_stealth_config, cleanup_stealth_config
```

- [x] **Step 5: Update cli/main.py**

In `packages/blackbox/src/shannon_blackbox/cli/main.py`, change line 7 from:

```python
from shannon_whitebox.session import SessionManager
```

to:

```python
from shannon_core.session import SessionManager
```

- [x] **Step 6: Update test_integration.py**

In `packages/blackbox/tests/test_integration.py`, change lines 9-11 from:

```python
from shannon_whitebox.agents.executor import AgentExecutor
from shannon_whitebox.agents.runner import ClaudeRunResult
from shannon_whitebox.prompts.manager import PromptManager
```

to:

```python
from shannon_core.agents.executor import AgentExecutor
from shannon_core.agents.runner import ClaudeRunResult
from shannon_core.prompts.manager import PromptManager
```

Also update the `run_claude_prompt` patch target on line 42 from:

```python
with patch("shannon_whitebox.agents.executor.run_claude_prompt", return_value=mock_result):
```

to:

```python
with patch("shannon_core.agents.executor.run_claude_prompt", return_value=mock_result):
```

- [x] **Step 7: Remove shannon-whitebox from blackbox pyproject.toml**

In `packages/blackbox/pyproject.toml`, change dependencies from:

```toml
dependencies = [
    "shannon-core",
    "shannon-whitebox",
    "temporalio>=1.0",
    "click>=8.0",
    "aiofiles>=23.0",
]
```

to:

```toml
dependencies = [
    "shannon-core",
    "temporalio>=1.0",
    "click>=8.0",
    "aiofiles>=23.0",
]
```

- [x] **Step 8: Sync workspace**

Run: `cd shannon-py && uv sync`
Expected: Resolves without errors

- [x] **Step 9: Run blackbox tests**

Run: `cd shannon-py && uv run pytest packages/blackbox/tests/ -v`
Expected: All tests pass

- [x] **Step 10: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py packages/blackbox/src/shannon_blackbox/agents/recon_executor.py packages/blackbox/src/shannon_blackbox/pipeline/activities.py packages/blackbox/src/shannon_blackbox/pipeline/workflows.py packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/test_integration.py packages/blackbox/pyproject.toml uv.lock
git commit -m "refactor(blackbox): import moved components from shannon_core, remove whitebox dependency"
```

---

## Task 14: Delete moved files from whitebox

**Files:**
- Delete: `packages/whitebox/src/shannon_whitebox/agents/executor.py`
- Delete: `packages/whitebox/src/shannon_whitebox/agents/runner.py`
- Delete: `packages/whitebox/src/shannon_whitebox/agents/validators.py`
- Delete: `packages/whitebox/src/shannon_whitebox/agents/__init__.py`
- Delete: `packages/whitebox/src/shannon_whitebox/prompts/manager.py`
- Delete: `packages/whitebox/src/shannon_whitebox/prompts/__init__.py`
- Delete: `packages/whitebox/src/shannon_whitebox/session.py`
- Delete: `packages/whitebox/src/shannon_whitebox/git_manager.py`
- Delete: `packages/whitebox/src/shannon_whitebox/services/validate_authentication.py`
- Delete: `packages/whitebox/src/shannon_whitebox/services/playwright_config_writer.py`
- Delete: `packages/whitebox/src/shannon_whitebox/services/settings_writer.py`
- Delete: `packages/whitebox/src/shannon_whitebox/services/__init__.py`
- Delete: `packages/whitebox/tests/test_validators.py`
- Delete: `packages/whitebox/tests/test_prompt_manager.py`
- Delete: `packages/whitebox/tests/test_git_manager.py`
- Delete: `packages/whitebox/tests/test_session.py`
- Delete: `packages/whitebox/tests/test_playwright_config_writer.py`
- Delete: `packages/whitebox/tests/test_settings_writer.py`
- Delete: `packages/whitebox/tests/test_validate_authentication.py`

- [x] **Step 1: Delete moved source files**

```bash
rm packages/whitebox/src/shannon_whitebox/agents/executor.py
rm packages/whitebox/src/shannon_whitebox/agents/runner.py
rm packages/whitebox/src/shannon_whitebox/agents/validators.py
rm packages/whitebox/src/shannon_whitebox/agents/__init__.py
rmdir packages/whitebox/src/shannon_whitebox/agents

rm packages/whitebox/src/shannon_whitebox/prompts/manager.py
rm packages/whitebox/src/shannon_whitebox/prompts/__init__.py
rmdir packages/whitebox/src/shannon_whitebox/prompts

rm packages/whitebox/src/shannon_whitebox/session.py
rm packages/whitebox/src/shannon_whitebox/git_manager.py

rm packages/whitebox/src/shannon_whitebox/services/validate_authentication.py
rm packages/whitebox/src/shannon_whitebox/services/playwright_config_writer.py
rm packages/whitebox/src/shannon_whitebox/services/settings_writer.py
rm packages/whitebox/src/shannon_whitebox/services/__init__.py
rmdir packages/whitebox/src/shannon_whitebox/services
```

- [x] **Step 2: Delete moved test files**

```bash
rm packages/whitebox/tests/test_validators.py
rm packages/whitebox/tests/test_prompt_manager.py
rm packages/whitebox/tests/test_git_manager.py
rm packages/whitebox/tests/test_session.py
rm packages/whitebox/tests/test_playwright_config_writer.py
rm packages/whitebox/tests/test_settings_writer.py
rm packages/whitebox/tests/test_validate_authentication.py
```

- [x] **Step 3: Run whitebox tests (only test_cli.py and test_integration.py remain)**

Run: `cd shannon-py && uv run pytest packages/whitebox/tests/ -v`
Expected: test_cli.py and test_integration.py pass

- [x] **Step 4: Commit**

```bash
git add -A packages/whitebox/
git commit -m "refactor(whitebox): remove moved components (now in shannon_core)"
```

---

## Task 15: Full verification suite

**Files:** None (verification only)

- [x] **Step 1: Run all core tests**

Run: `cd shannon-py && uv run pytest packages/core/tests/ -v`
Expected: All 17 test files pass (10 original + 7 moved)

- [x] **Step 2: Run all whitebox tests**

Run: `cd shannon-py && uv run pytest packages/whitebox/tests/ -v`
Expected: test_cli.py and test_integration.py pass (2 files remaining)

- [x] **Step 3: Run all blackbox tests**

Run: `cd shannon-py && uv run pytest packages/blackbox/tests/ -v`
Expected: All test files pass

- [x] **Step 4: Verify core import works**

Run: `cd shannon-py && uv run python -c "from shannon_core.agents.executor import AgentExecutor; print('AgentExecutor OK')"`
Expected: `AgentExecutor OK`

Run: `cd shannon-py && uv run python -c "from shannon_core.session import SessionManager; print('SessionManager OK')"`
Expected: `SessionManager OK`

- [x] **Step 5: Verify blackbox does not depend on whitebox**

Run: `cd shannon-py && grep -c "shannon-whitebox" packages/blackbox/pyproject.toml || echo "OK: no whitebox dependency"`
Expected: `OK: no whitebox dependency`

- [x] **Step 6: Verify whitebox-specific code stays in whitebox**

Run: `cd shannon-py && uv run python -c "from shannon_whitebox.audit.session import AuditSession; print('AuditSession OK')"`
Expected: `AuditSession OK`

Run: `cd shannon-py && uv run python -c "from shannon_whitebox.audit.log_stream import LogStream; print('LogStream OK')"`
Expected: `LogStream OK`

- [x] **Step 7: Final commit if any cleanup needed**

```bash
git add -A
git diff --cached --quiet || git commit -m "chore: final cleanup after shared component extraction"
```
