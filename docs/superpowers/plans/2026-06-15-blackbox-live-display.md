# Blackbox 实时日志展示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote the (package-agnostic) whitebox `audit/` layer to `shannon_core/audit/`, then wire the existing display pipeline into blackbox Temporal activities + add a parallel-exploit live dashboard + `--plain` + delete the `print()` poller — so `shannon-blackbox start` shows a scrolling event log + bottom dashboard with parallel exploit-agent rows.

**Architecture:** Move `packages/whitebox/.../audit/` (11 files, verified package-agnostic) to `packages/core/src/shannon_core/audit/`; leave `shannon_whitebox/audit/` as one-line re-export shims so whitebox is untouched (L0 regression gate stays green structurally). Blackbox imports `shannon_core.audit` directly and mirrors the whitebox 2026-06-15 event-driven wiring: `run_with_display` driver → `set_audit_session` singleton → activities call `get_audit_session()` + `SessionToolAuditLogger` → `WorkflowLogger → DisplayDispatcher → renderers`. Progress is event-driven (zero polling).

**Tech Stack:** Python 3.13+, Rich (`Console`/`Live`/`Panel`/`Table`/`Spinner`), Temporal (python SDK, async activities), pytest + pytest-asyncio (asyncio_mode=auto), uv workspace.

**Spec:** `docs/superpowers/specs/2026-06-15-blackbox-live-display-design.md`

**Key facts established (do not re-derive):**
- `audit/` = 11 files: `__init__.py`, `agent_logger.py`, `audit_logger.py`, `display_lifecycle.py`, `log_stream.py`, `metrics_tracker.py`, `session.py`, `session_registry.py`, `session_tool_audit_logger.py`, `utils.py`, `workflow_logger.py`.
- Only **7 absolute imports** inside audit/ point at whitebox (the rest are relative): `workflow_logger.py:14,15`; `session_tool_audit_logger.py:15` (TYPE_CHECKING); `agent_logger.py:8,9`; `metrics_tracker.py:7`; `log_stream.py:6`.
- `whitebox/worker.py:89` does a **function-body lazy import** of `clear_audit_session`; `tests/test_worker.py:124` patches the **source module path**. Because the lazy import runs after the patch is applied, the patch intercepts it. **Shim submodules preserve this with zero test changes.**
- `AgentExecutor.execute(...)` already accepts `tool_audit_logger` (`executor.py:39`) and threads it (`:75`) — whitebox Task 12 done.
- `ReconExecutor.execute` / `ExploitExecutor.execute` / `validate_authentication` accept only `audit_logger`, NOT `tool_audit_logger` — Task 2 adds it.
- `_AGENT_PREFIXES` **already** has exploit keys (`injection-exploit`→`[Injection]`, etc.) — no formatter change needed.
- `AgentName`: `RECON_BLACKBOX="recon-blackbox"`, `INJECTION_EXPLOIT="injection-exploit"` (etc.), `REPORT="report"`, `VALIDATE_AUTH="validate-authentication"`.
- Models (`shannon_core.models.audit`): `AgentEndResult(success, duration_ms, cost_usd, attempt_number=1, model=None, error=None)`; `WorkflowSummary(status: Literal["completed","failed","cancelled"], total_duration_ms, total_cost_usd, completed_agents: list[str], agent_metrics: dict[str, AgentMetricsSummary], error=None)`; `AgentMetricsSummary(duration_ms, cost_usd=None)`.
- `BlackboxPipelineState`: `status, current_phase, current_agent, completed_agents: list[str], agent_metrics: dict[str, dict], has_whitebox_results, found_whitebox_classes, start_time, errors: list[str], error_code, failed_agents`. `agent_metrics` values are `metrics.model_dump()` dicts (`duration_ms`/`cost_usd`/`model` keys).
- Blackbox workflow phases (`workflows.py`): preflight `:68`, auth-validation `:108` (only if `config_path`), recon-blackbox `:149` (only if `!has_whitebox_results`), exploitation `:158` (parallel `run_exploit_agent` ×N via `Semaphore(max_concurrent)` + `asyncio.gather` `:223`), reporting `:268` (assemble/report/finalize).
- Whitebox `run_agent` (`whitebox/pipeline/activities.py:75-127`) is the mirror template: it drops `audit_logger=`, passes only `tool_audit_logger=`, uses `agent_start = time.monotonic()` for failure duration.

---

## File Structure

**Created:**
- `packages/core/src/shannon_core/audit/__init__.py` — core audit package re-exports (moved)
- `packages/blackbox/tests/test_activity_display_wiring.py` — L3 gate
- `packages/blackbox/tests/test_display_integration.py` — L2 gate

**Moved (whitebox/audit → core/audit, impl):**
- `session.py`, `workflow_logger.py`, `session_registry.py`, `session_tool_audit_logger.py`, `display_lifecycle.py`, `agent_logger.py`, `audit_logger.py`, `metrics_tracker.py`, `utils.py`, `log_stream.py`

**Shim (whitebox/audit kept as 1-line re-exports → core):**
- `packages/whitebox/src/shannon_whitebox/audit/__init__.py` + 10 submodule shims

**Modified:**
- `packages/blackbox/src/shannon_blackbox/agents/recon_executor.py` — thread `tool_audit_logger`
- `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py` — thread `tool_audit_logger`
- `packages/core/src/shannon_core/services/validate_authentication.py` — thread `tool_audit_logger`
- `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` — wire 4 activities + add phase markers
- `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` — schedule phase markers
- `packages/blackbox/src/shannon_blackbox/worker.py` — `run_with_display` + singleton + delete poller + summary
- `packages/blackbox/src/shannon_blackbox/cli/main.py` — `--plain` flag

---

## Phase 0 — Promote audit/ to core (前置依赖 + L0 回归闸)

### Task 1: Move `audit/` → `shannon_core/audit/` + whitebox compat shims (L0 gate)

**Files:**
- Move: 10 impl `.py` from `packages/whitebox/src/shannon_whitebox/audit/` → `packages/core/src/shannon_core/audit/`
- Create: `packages/core/src/shannon_core/audit/__init__.py`
- Replace: `packages/whitebox/src/shannon_whitebox/audit/*.py` (11 files) with 1-line re-export shims

This task leaves **every whitebox import resolving identically** (via shims) so the full whitebox suite stays green — that green run is the L0 regression gate proving the promote didn't break whitebox.

- [ ] **Step 1: Create the core audit dir and move the 10 implementation files**

```bash
mkdir -p packages/core/src/shannon_core/audit
for f in agent_logger audit_logger display_lifecycle log_stream metrics_tracker session session_registry session_tool_audit_logger utils workflow_logger; do
  git mv packages/whitebox/src/shannon_whitebox/audit/$f.py packages/core/src/shannon_core/audit/$f.py
done
```

- [ ] **Step 2: Fix the 7 internal absolute imports (now in core) to relative**

`packages/core/src/shannon_core/audit/workflow_logger.py:14`:
```python
from .log_stream import LogStream
```
`packages/core/src/shannon_core/audit/workflow_logger.py:15`:
```python
from .utils import generate_workflow_log_path
```
`packages/core/src/shannon_core/audit/session_tool_audit_logger.py:15` (inside `if TYPE_CHECKING:`):
```python
    from .session import AuditSession
```
`packages/core/src/shannon_core/audit/agent_logger.py:8`:
```python
from .log_stream import LogStream
```
`packages/core/src/shannon_core/audit/agent_logger.py:9` — change the `from shannon_whitebox.audit.utils import (...)` to:
```python
from .utils import (
```
(keep the same parenthesized name list, only change the module path)
`packages/core/src/shannon_core/audit/metrics_tracker.py:7` — change `from shannon_whitebox.audit.utils import (` to:
```python
from .utils import (
```
`packages/core/src/shannon_core/audit/log_stream.py:6`:
```python
from .utils import format_timestamp
```

- [ ] **Step 3: Create the core audit package `__init__.py`**

Create `packages/core/src/shannon_core/audit/__init__.py`:
```python
"""Audit layer: AuditSession / WorkflowLogger / registry / display lifecycle.

Promoted from packages/whitebox/audit (package-agnostic; depends only on
shannon_core). Whitebox keeps a re-export shim at shannon_whitebox.audit.
"""
from .session import AuditSession
from .audit_logger import AuditLogger, create_audit_logger

__all__ = ["AuditSession", "AuditLogger", "create_audit_logger"]
```

- [ ] **Step 4: Replace whitebox `audit/` with re-export shims**

Rewrite `packages/whitebox/src/shannon_whitebox/audit/__init__.py`:
```python
"""Compat shim — implementation moved to shannon_core.audit."""
from shannon_core.audit import AuditSession, AuditLogger, create_audit_logger  # noqa: F401

__all__ = ["AuditSession", "AuditLogger", "create_audit_logger"]
```

Generate the 10 submodule shims (each re-exports the moved module so deep imports like `from shannon_whitebox.audit.session_registry import get_audit_session` and the `test_worker.py` patch target keep resolving):
```bash
for mod in agent_logger audit_logger display_lifecycle log_stream metrics_tracker session session_registry session_tool_audit_logger utils workflow_logger; do
  printf '"""Compat shim — implementation moved to shannon_core.audit.%s."""\nfrom shannon_core.audit.%s import *  # noqa: F401,F403\n' "$mod" "$mod" \
    > packages/whitebox/src/shannon_whitebox/audit/$mod.py
done
```

- [ ] **Step 5: Smoke-check both packages import**

Run: `uv run python -c "import shannon_core.audit.session_registry; from shannon_whitebox.audit.session_registry import get_audit_session; from shannon_core.audit.display_lifecycle import run_with_display; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 6: L0 regression gate — full whitebox suite stays green**

Run: `uv run pytest packages/whitebox/tests packages/core/tests/display -q`
Expected: all passed (the promote touched zero whitebox logic; shims preserve every import including the `test_worker.py:124` patch target).

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/shannon_core/audit packages/whitebox/src/shannon_whitebox/audit
git commit -m "refactor(audit): promote audit/ layer to shannon_core; whitebox keeps re-export shim"
```

---

## Phase 1 — Blackbox display plumbing

### Task 2: Thread `tool_audit_logger` through ReconExecutor / ExploitExecutor / validate_authentication

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/agents/recon_executor.py`
- Modify: `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`
- Modify: `packages/core/src/shannon_core/services/validate_authentication.py`

These three are the only callers of `AgentExecutor.execute` in the blackbox path that don't yet forward `tool_audit_logger`. `AgentExecutor.execute` already accepts it.

- [ ] **Step 1: `ReconExecutor.execute` — add + forward `tool_audit_logger`**

In `packages/blackbox/src/shannon_blackbox/agents/recon_executor.py`, change the `TYPE_CHECKING` import block and the signature. Replace:
```python
if TYPE_CHECKING:
    from shannon_core.logging.activity_logger import ActivityLogger
```
with:
```python
if TYPE_CHECKING:
    from shannon_core.logging.activity_logger import ActivityLogger
    from shannon_core.agents.tool_audit_logger import ToolAuditLogger
```
Add the param to `execute` (after `audit_logger`):
```python
        audit_logger: "ActivityLogger | None" = None,
        tool_audit_logger: "ToolAuditLogger | None" = None,
```
And forward it — replace the `return await self._executor.execute(` block with:
```python
        return await self._executor.execute(
            agent_name=AgentName.RECON_BLACKBOX,
            repo_path=str(deliverables_path),
            web_url=web_url,
            deliverables_path=str(deliverables_path),
            config_path=config_path,
            api_key=api_key,
            pipeline_testing=pipeline_testing,
            audit_logger=audit_logger,
            tool_audit_logger=tool_audit_logger,
        )
```

- [ ] **Step 2: `ExploitExecutor.execute` — add + forward `tool_audit_logger`**

In `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`, same `TYPE_CHECKING` change as Step 1, add the param after `audit_logger`:
```python
        audit_logger: "ActivityLogger | None" = None,
        tool_audit_logger: "ToolAuditLogger | None" = None,
```
And forward it — replace the `return await self._executor.execute(` block with:
```python
        return await self._executor.execute(
            agent_name=agent_name,
            repo_path=str(deliverables_path),
            web_url=web_url,
            deliverables_path=str(deliverables_path),
            config_path=config_path,
            api_key=api_key,
            pipeline_testing=pipeline_testing,
            prompt_variables=prompt_variables,
            audit_logger=audit_logger,
            tool_audit_logger=tool_audit_logger,
        )
```

- [ ] **Step 3: `validate_authentication` — add + forward `tool_audit_logger`**

In `packages/core/src/shannon_core/services/validate_authentication.py`, add the param after `audit_logger` (line 99):
```python
    audit_logger: "ActivityLogger | None" = None,
    tool_audit_logger: "ToolAuditLogger | None" = None,
```
And in the `executor.execute(...)` call (line ~125-135), add the kwarg alongside `audit_logger=audit_logger`:
```python
    metrics = await executor.execute(
        agent_name=AgentName.VALIDATE_AUTH,
        repo_path=repo_path or "/tmp/shannon-auth-check",
        web_url=web_url,
        config_path=config_path,
        api_key=api_key,
        prompt_override="validate-authentication",
        prompt_variables={"AUTH_STATE_FILE": str(state_file)},
        structured_output_schema=AUTH_VALIDATION_SCHEMA,
        audit_logger=audit_logger,
        tool_audit_logger=tool_audit_logger,
    )
```

- [ ] **Step 4: Verify nothing regressed**

Run: `uv run pytest packages/blackbox/tests packages/core/tests -q`
Expected: all passed (new params default `None`; behavior unchanged).

- [ ] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/agents/recon_executor.py packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py packages/core/src/shannon_core/services/validate_authentication.py
git commit -m "feat(blackbox): thread tool_audit_logger through recon/exploit executors + auth validation"
```

---

## Phase 2 — Blackbox activity wiring

### Task 3: Wire `run_recon` / `run_exploit_agent` / `run_report_agent` to AuditSession (L3)

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/activities.py`
- Create: `packages/blackbox/tests/test_activity_display_wiring.py`

Mirror whitebox `run_agent` (`whitebox/pipeline/activities.py:75-127`): drop `audit_logger=`, pass only `tool_audit_logger=`, wrap with `start_agent`/`end_agent`/`log_error`.

- [ ] **Step 1: Add the imports the wired activities need**

In `packages/blackbox/src/shannon_blackbox/pipeline/activities.py`, add `import time` at the top (after `from pathlib import Path`):
```python
import time
from pathlib import Path
from urllib.parse import urlparse
```

- [ ] **Step 2: Rewrite `run_recon` (currently `activities.py:104-130`)**

Replace the body of `run_recon` with:
```python
@activity.defn
async def run_recon(input: BlackboxActivityInput) -> dict:
    from shannon_core.audit.session_registry import get_audit_session
    from shannon_core.audit.session_tool_audit_logger import SessionToolAuditLogger
    from shannon_core.models.audit import AgentEndResult

    agent_name = AgentName.RECON_BLACKBOX
    attempt = activity.info().attempt
    session = get_audit_session()
    tool_audit_logger = SessionToolAuditLogger(session)
    agent_start = time.monotonic()
    try:
        from shannon_blackbox.agents.recon_executor import ReconExecutor

        deliverables = _get_deliverables_path(input)
        deliverables.mkdir(parents=True, exist_ok=True)
        prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)
        recon = ReconExecutor(executor)

        await session.start_agent(agent_name.value, f"agent={agent_name.value}", attempt=attempt)
        metrics = await recon.execute(
            workspace_path=deliverables.parent,
            deliverables_path=deliverables,
            web_url=input.web_url,
            config_path=input.config_path,
            api_key=input.api_key,
            pipeline_testing=input.pipeline_testing_mode,
            tool_audit_logger=tool_audit_logger,
        )
        await session.end_agent(agent_name.value, AgentEndResult(
            success=True,
            duration_ms=metrics.duration_ms,
            cost_usd=metrics.cost_usd or 0.0,
            attempt_number=attempt,
            model=metrics.model,
        ))
        return metrics.model_dump()
    except PentestError as e:
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value)
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value)
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

- [ ] **Step 3: Rewrite `run_exploit_agent` (currently `activities.py:133-162`)**

Replace its body with:
```python
@activity.defn
async def run_exploit_agent(input: BlackboxActivityInput) -> dict:
    from shannon_core.audit.session_registry import get_audit_session
    from shannon_core.audit.session_tool_audit_logger import SessionToolAuditLogger
    from shannon_core.models.audit import AgentEndResult

    vuln_type: str = input.vuln_type
    agent_name = AgentName(f"{vuln_type}-exploit")
    attempt = activity.info().attempt
    session = get_audit_session()
    tool_audit_logger = SessionToolAuditLogger(session)
    agent_start = time.monotonic()
    try:
        from shannon_blackbox.agents.exploit_executor import ExploitExecutor

        deliverables = _get_deliverables_path(input)
        prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)
        exploit = ExploitExecutor(executor)

        await session.start_agent(agent_name.value, f"agent={agent_name.value}", attempt=attempt)
        metrics = await exploit.execute(
            agent_name=agent_name,
            vuln_type=vuln_type,
            workspace_path=deliverables.parent,
            deliverables_path=deliverables,
            web_url=input.web_url,
            config_path=input.config_path,
            api_key=input.api_key,
            pipeline_testing=input.pipeline_testing_mode,
            tool_audit_logger=tool_audit_logger,
        )
        await session.end_agent(agent_name.value, AgentEndResult(
            success=True,
            duration_ms=metrics.duration_ms,
            cost_usd=metrics.cost_usd or 0.0,
            attempt_number=attempt,
            model=metrics.model,
        ))
        return metrics.model_dump()
    except PentestError as e:
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value)
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value)
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

- [ ] **Step 4: Rewrite `run_report_agent` (currently `activities.py:192-215`)**

Replace its body with:
```python
@activity.defn
async def run_report_agent(input: BlackboxActivityInput) -> dict:
    from shannon_core.audit.session_registry import get_audit_session
    from shannon_core.audit.session_tool_audit_logger import SessionToolAuditLogger
    from shannon_core.models.audit import AgentEndResult

    agent_name = AgentName.REPORT
    attempt = activity.info().attempt
    session = get_audit_session()
    tool_audit_logger = SessionToolAuditLogger(session)
    agent_start = time.monotonic()
    try:
        deliverables = _get_deliverables_path(input)
        prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)

        await session.start_agent(agent_name.value, f"agent={agent_name.value}", attempt=attempt)
        metrics = await executor.execute(
            agent_name=agent_name,
            repo_path=str(deliverables),
            web_url=input.web_url,
            deliverables_path=str(deliverables),
            config_path=input.config_path,
            api_key=input.api_key,
            pipeline_testing=input.pipeline_testing_mode,
            tool_audit_logger=tool_audit_logger,
        )
        await session.end_agent(agent_name.value, AgentEndResult(
            success=True,
            duration_ms=metrics.duration_ms,
            cost_usd=metrics.cost_usd or 0.0,
            attempt_number=attempt,
            model=metrics.model,
        ))
        return metrics.model_dump()
    except PentestError as e:
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value)
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value)
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

- [ ] **Step 5: Write the L3 wiring test**

Create `packages/blackbox/tests/test_activity_display_wiring.py`:
```python
"""L3: tool/llm events through SessionToolAuditLogger reach workflow.log via the
blackbox AuditSession -> WorkflowLogger -> dispatcher -> FileLogRenderer path."""
from pathlib import Path

from shannon_core.models.audit import AgentEndResult
from shannon_core.models.metrics import SessionMetadata
from shannon_core.audit.session import AuditSession
from shannon_core.audit.session_registry import set_audit_session, clear_audit_session
from shannon_core.audit.session_tool_audit_logger import SessionToolAuditLogger
from shannon_core.audit.utils import generate_audit_path


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="s1", web_url="https://example.com", output_path=str(tmp_path))


async def test_session_tool_audit_logger_feeds_workflow_log(tmp_path: Path):
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    set_audit_session(session)
    try:
        await session.start_agent("injection-exploit", "p", attempt=1)
        lg = SessionToolAuditLogger(session)
        await lg.log_tool_start("Bash", {"command": "curl 'http://x/?q=<script>'"})
        await lg.log_assistant_turn(1, "confirmed reflected XSS")
        await session.end_agent("injection-exploit", AgentEndResult(
            success=True, duration_ms=100, cost_usd=0.01, attempt_number=1))
    finally:
        clear_audit_session()
        await session.close()
    wf = (generate_audit_path(_make_meta(tmp_path)) / "workflow.log").read_text()
    assert "[AGENT] [Injection] injection-exploit: Starting" in wf
    assert "[TOOL]  [Injection] injection-exploit: Bash:" in wf
    assert "[LLM]   [Injection] injection-exploit: Turn 1:" in wf
    assert "[AGENT] [Injection] injection-exploit: Completed" in wf
```

- [ ] **Step 6: Run the L3 test**

Run: `uv run pytest packages/blackbox/tests/test_activity_display_wiring.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/activities.py packages/blackbox/tests/test_activity_display_wiring.py
git commit -m "feat(blackbox): wire run_recon/run_exploit_agent/run_report_agent to AuditSession"
```

---

### Task 4: Phase-marker activities + `run_blackbox_auth_validation` agent row

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/activities.py`
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`

- [ ] **Step 1: Add phase-marker activities (mirror whitebox)**

Append to `packages/blackbox/src/shannon_blackbox/pipeline/activities.py`:
```python
@activity.defn
async def log_phase_start_activity(input: BlackboxActivityInput) -> None:
    from shannon_core.audit.session_registry import get_audit_session
    phase = input.workspace_name or "unknown"
    await get_audit_session().log_phase_start(phase)


@activity.defn
async def log_phase_complete_activity(input: BlackboxActivityInput) -> None:
    from shannon_core.audit.session_registry import get_audit_session
    phase = input.workspace_name or "unknown"
    await get_audit_session().log_phase_complete(phase)
```

- [ ] **Step 2: Wire `run_blackbox_auth_validation` as an agent row (judgment point 1)**

Replace the body of `run_blackbox_auth_validation` (currently `activities.py:68-101`) with:
```python
@activity.defn
async def run_blackbox_auth_validation(input: BlackboxActivityInput) -> None:
    from shannon_core.audit.session_registry import get_audit_session
    from shannon_core.audit.session_tool_audit_logger import SessionToolAuditLogger
    from shannon_core.models.audit import AgentEndResult
    from shannon_core.models.agents import AgentName

    agent_name = AgentName.VALIDATE_AUTH
    attempt = activity.info().attempt
    session = get_audit_session()
    tool_audit_logger = SessionToolAuditLogger(session)
    agent_start = time.monotonic()
    try:
        from shannon_core.services.validate_authentication import validate_authentication

        prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)

        await session.start_agent(agent_name.value, f"agent={agent_name.value}", attempt=attempt)
        result = await validate_authentication(
            web_url=input.web_url,
            config_path=input.config_path,
            workspace_path=input.workspace_path or "",
            prompt_manager=prompt_manager,
            executor=executor,
            repo_path=input.repo_path or "",
            api_key=input.api_key,
            tool_audit_logger=tool_audit_logger,
        )
        await session.end_agent(agent_name.value, AgentEndResult(
            success=True, duration_ms=int((time.monotonic() - agent_start) * 1000),
            cost_usd=0.0, attempt_number=attempt))
        if not result.success:
            raise PentestError(
                f"Authentication validation failed: {result.failure_detail or 'unknown'}",
                category="preflight",
                retryable=False,
                error_code=ErrorCode.AUTH_LOGIN_FAILED,
            )
    except PentestError as e:
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value)
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value)
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

> Note: `validate_authentication` returns `AuthValidationResult(success=True)` early when there is no auth config — in that case the agent row still emits a start/end (short, harmless). `ErrorCode`/`PentestError` are already imported at the top of `activities.py`.

- [ ] **Step 3: Schedule phase markers at workflow boundaries**

In `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`, insert a marker activity before each phase. Use the exact `BlackboxActivityInput(**{**act_input.__dict__, "workspace_name": ...})` form already used in the file.

Insert before the preflight activity (before line 68 `await workflow.execute_activity(activities.run_blackbox_preflight, ...)`):
```python
        await workflow.execute_activity(
            activities.log_phase_start_activity,
            BlackboxActivityInput(**{**act_input.__dict__, "workspace_name": "preflight"}),
            start_to_close_timeout=timedelta(seconds=10),
        )
```

Inside the `if input.config_path:` block, before the auth-validation activity (before line 108):
```python
            await workflow.execute_activity(
                activities.log_phase_start_activity,
                BlackboxActivityInput(**{**act_input.__dict__, "workspace_name": "auth-validation"}),
                start_to_close_timeout=timedelta(seconds=10),
            )
```

Before `self._state.current_phase = "recon-blackbox"` (line 146), insert (still inside the `if not has_whitebox_results ...` guard so it only fires when recon actually runs):
```python
                await workflow.execute_activity(
                    activities.log_phase_start_activity,
                    BlackboxActivityInput(**{**act_input.__dict__, "workspace_name": "recon-blackbox"}),
                    start_to_close_timeout=timedelta(seconds=10),
                )
```

Before `self._state.current_phase = "exploitation"` (line 159), inside `if input.exploit:`:
```python
            await workflow.execute_activity(
                activities.log_phase_start_activity,
                BlackboxActivityInput(**{**act_input.__dict__, "workspace_name": "exploitation"}),
                start_to_close_timeout=timedelta(seconds=10),
            )
```

Before `self._state.current_phase = "reporting"` (line 268):
```python
            await workflow.execute_activity(
                activities.log_phase_start_activity,
                BlackboxActivityInput(**{**act_input.__dict__, "workspace_name": "reporting"}),
                start_to_close_timeout=timedelta(seconds=10),
            )
```

- [ ] **Step 4: Verify workflow imports/syntax**

Run: `uv run python -c "from shannon_blackbox.pipeline.workflows import BlackboxScanWorkflow; from shannon_blackbox.pipeline.activities import log_phase_start_activity, log_phase_complete_activity; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 5: Run blackbox suite (activities/workflows tests)**

Run: `uv run pytest packages/blackbox/tests/test_workflows.py packages/blackbox/tests/test_activity_display_wiring.py -q`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/activities.py packages/blackbox/src/shannon_blackbox/pipeline/workflows.py
git commit -m "feat(blackbox): phase-marker activities + auth-validation agent row"
```

---

### Task 5: Wire `worker.py` — `run_with_display` + singleton + delete poller + summary

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/worker.py`

- [ ] **Step 1: Replace `worker.py` with the wired version**

Replace the entire contents of `packages/blackbox/src/shannon_blackbox/worker.py` with:
```python
import asyncio
import time

from temporalio.client import Client
from temporalio.worker import Worker

from .pipeline.activities import (
    run_blackbox_preflight,
    run_blackbox_auth_validation,
    run_recon,
    run_exploit_agent,
    assemble_report,
    run_report_agent,
    log_phase_start_activity,
    log_phase_complete_activity,
)
from .pipeline.workflows import BlackboxScanWorkflow
from .pipeline.shared import BlackboxPipelineInput, BlackboxPipelineState
from shannon_core.utils.paths import resolve_workspaces_dir
from shannon_core.services.temporal_infra import generate_task_queue
from shannon_core.models.metrics import SessionMetadata
from shannon_core.models.audit import AgentMetricsSummary, WorkflowSummary
from shannon_core.audit.display_lifecycle import run_with_display
from shannon_core.audit.session_registry import set_audit_session, clear_audit_session

TASK_QUEUE_PREFIX = "shannon-py-bb"


def _to_workflow_summary(result: BlackboxPipelineState, total_duration_ms: int) -> WorkflowSummary:
    status = result.status if result.status in ("completed", "failed", "cancelled") else "failed"
    return WorkflowSummary(
        status=status,  # type: ignore[arg-type]
        total_duration_ms=total_duration_ms,
        total_cost_usd=sum((m.get("cost_usd") or 0.0) for m in result.agent_metrics.values()),
        completed_agents=list(result.completed_agents),
        agent_metrics={
            name: AgentMetricsSummary(
                duration_ms=int(m.get("duration_ms") or 0),
                cost_usd=m.get("cost_usd"),
            )
            for name, m in result.agent_metrics.items()
        },
        error=result.errors[-1] if result.errors else None,
    )


async def run_scan(input: BlackboxPipelineInput, temporal_address: str = "localhost:7233",
                   use_rich: bool = False) -> BlackboxPipelineState:
    client = await Client.connect(temporal_address)
    task_queue = generate_task_queue(TASK_QUEUE_PREFIX)

    worker = Worker(
        client=client,
        task_queue=task_queue,
        workflows=[BlackboxScanWorkflow],
        activities=[
            run_blackbox_preflight, run_blackbox_auth_validation, run_recon,
            run_exploit_agent, assemble_report, run_report_agent,
            log_phase_start_activity, log_phase_complete_activity,
        ],
    )

    meta = SessionMetadata(
        id=input.workspace_name or "blackbox-scan",
        web_url=input.web_url,
        repo_path=input.repo_path,
        output_path=str(resolve_workspaces_dir(input.repo_path)),
    )

    async with worker:
        async with run_with_display(meta, use_rich=use_rich) as session:
            set_audit_session(session)
            scan_start = time.monotonic()
            handle = await client.start_workflow(
                BlackboxScanWorkflow.run,
                input,
                id=input.workspace_name or f"blackbox-{int(asyncio.get_event_loop().time())}",
                task_queue=task_queue,
            )
            try:
                result = await handle.result()
            finally:
                clear_audit_session()

            total_duration_ms = int((time.monotonic() - scan_start) * 1000)
            await session.log_workflow_complete(_to_workflow_summary(result, total_duration_ms))
            return result


def main():
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000"
    asyncio.run(run_scan(BlackboxPipelineInput(web_url=url)))
```

> `poll_workflow_progress` + `print()` are gone; progress is event-driven via the dashboard. `PipelineProgress` import dropped (no longer queried by the poller).

- [ ] **Step 2: Verify import sanity**

Run: `uv run python -c "from shannon_blackbox.worker import run_scan; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/worker.py
git commit -m "feat(blackbox): run_scan wires AuditSession+Live, registers singleton, drops print() poller"
```

---

### Task 6: CLI `start` — `--plain` flag + `use_rich` autodetect

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py`

- [ ] **Step 1: Add the flag and thread `use_rich`**

In `packages/blackbox/src/shannon_blackbox/cli/main.py`, add the option to the `start` decorator block (after the `--retry-profile` option, line ~40):
```python
@click.option("--plain", is_flag=True, help="Disable Rich live dashboard; print one line per event (CI/pipes).")
```

Add `plain` to the `start` signature (line 41):
```python
def start(url, repo, output, workspace, latest, config_path, vuln_classes, no_exploit, pipeline_testing, temporal_address, max_concurrent, retry_profile, plain):
```

Replace the `result = asyncio.run(run_scan(input, temporal_address))` line (line ~130) with:
```python
    import sys
    use_rich = sys.stdout.isatty() and not plain
    result = asyncio.run(run_scan(input, temporal_address, use_rich=use_rich))
```

- [ ] **Step 2: Smoke-check the CLI parses**

Run: `uv run shannon-blackbox start --help | grep -- --plain`
Expected: one line showing `--plain`.

- [ ] **Step 3: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py
git commit -m "feat(blackbox): --plain flag + TTY autodetect for Rich display"
```

---

## Phase 3 — Integration gate + acceptance

### Task 7: L2 display integration gate (blackbox)

**Files:**
- Create: `packages/blackbox/tests/test_display_integration.py`

Drive a scripted event sequence **through a blackbox AuditSession** (real WorkflowLogger→dispatcher→renderers) and assert BOTH a scrolling-log line AND a dashboard region appear in the captured terminal. This is the §3.4 anti-regression check.

- [ ] **Step 1: Write the gate test**

Create `packages/blackbox/tests/test_display_integration.py`:
```python
"""L2 gate: blackbox AuditSession -> WorkflowLogger -> dispatcher -> renderers
actually reaches the terminal. Empty capture => pipeline not wired."""
import io
from pathlib import Path

from rich.console import Console

from shannon_core.models.audit import AgentEndResult
from shannon_core.models.metrics import SessionMetadata
from shannon_core.display.live_dashboard import LiveDashboardRenderer
from shannon_core.audit.session import AuditSession


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="gate", web_url="https://example.com", output_path=str(tmp_path))


async def test_audit_session_reaches_console_and_dashboard(tmp_path: Path):
    buf = io.StringIO()
    console = Console(file=buf, width=120, force_terminal=True, color_system=None, force_interactive=True)
    dashboard = LiveDashboardRenderer(console)
    session = AuditSession(_make_meta(tmp_path), use_rich=True, console=console, dashboard=dashboard)
    await session.initialize(workflow_id="wf-gate")

    await session.log_phase_start("exploitation")
    await session.start_agent("injection-exploit", "p", attempt=1)
    await session.log_event("tool_start", {"toolName": "Bash", "parameters": {"command": "curl 'http://x/?q=<script>'"}})
    await session.log_event("llm_response", {"turn": 2, "content": "reflected XSS confirmed"})
    await session.end_agent("injection-exploit", AgentEndResult(
        success=True, duration_ms=5200, cost_usd=0.15, attempt_number=1))

    console.print(dashboard)
    await session.close()

    out = buf.getvalue()
    # Scrolling-log lines (RichConsoleRenderer printed each event)
    assert "[AGENT]" in out and "injection-exploit" in out
    assert "[TOOL]" in out
    assert "[LLM]" in out
    # Dashboard region (phase + completed count)
    assert "exploitation" in out
    assert "1 done" in out
```

- [ ] **Step 2: Run the gate test**

Run: `uv run pytest packages/blackbox/tests/test_display_integration.py -q`
Expected: PASS. (Empty/missing output => wiring broken — re-check Tasks 1, 3.)

- [ ] **Step 3: Commit**

```bash
git add packages/blackbox/tests/test_display_integration.py
git commit -m "test(blackbox): L2 display integration gate — AuditSession reaches console+dashboard"
```

---

### Task 8: DoD verification + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite (whitebox + blackbox + core display)**

Run: `uv run pytest packages/core/tests/display packages/core/tests/audit packages/whitebox/tests packages/blackbox/tests -q`
Expected: all passed (whitebox still green = L0 promote gate held; blackbox L2/L3 green).

> If `packages/core/tests/audit` does not exist (audit-layer tests stayed under whitebox via shim), drop that path — the whitebox suite already exercises the audit layer through the shim.

- [ ] **Step 2: DoD — blackbox `AuditSession` has a non-test caller**

Run: `grep -rn "run_with_display\|AuditSession(" packages/blackbox/src`
Expected: hit in `worker.py` (production).

- [ ] **Step 3: DoD — activities call `get_audit_session()`**

Run: `grep -rn "get_audit_session()" packages/blackbox/src`
Expected: hits in `activities.py` (`run_recon`, `run_exploit_agent`, `run_report_agent`, `run_blackbox_auth_validation`, `log_phase_start_activity`, `log_phase_complete_activity`).

- [ ] **Step 4: DoD — `print()` poller is gone**

Run: `grep -n "poll_workflow_progress\|Completed: {completed}" packages/blackbox/src/shannon_blackbox/worker.py`
Expected: no matches.

- [ ] **Step 5: DoD — `--plain` flag exists**

Run: `uv run shannon-blackbox start --help | grep -- --plain`
Expected: one line showing `--plain`.

- [ ] **Step 6: Manual smoke (human sign-off)**

Run in a real terminal:
```
uv run shannon-blackbox start --url http://localhost:3000 -r <fixture-repo> --pipeline-testing
```
Expected: a scrolling event log (phase headers, `[AGENT]`/`[TOOL]`/`[LLM]` lines) with a live dashboard pinned at the bottom (phase, N done, elapsed, $, per-agent rows incl. **parallel exploit-agent spinner rows** during exploitation). Then:
```
uv run shannon-blackbox start --url http://localhost:3000 -r <fixture-repo> --pipeline-testing --plain
```
Expected: one plain text line per event, no dashboard, no ANSI.
```
uv run shannon-blackbox start --url http://localhost:3000 -r <fixture-repo> --pipeline-testing | cat
```
Expected: auto-degraded plain output (piped, non-TTY), no ANSI garbage.

- [ ] **Step 7: Update gap analysis + spec status**

Update `docs/gap/logging-display-gap-analysis.md` §3.4 (add a note that the接入 gap is now closed for blackbox too) and flip the spec status line of `docs/superpowers/specs/2026-06-15-blackbox-live-display-design.md` from "设计待评审" to "已实现". Commit:
```bash
git add docs/gap/logging-display-gap-analysis.md docs/superpowers/specs/2026-06-15-blackbox-live-display-design.md
git commit -m "docs(gap): mark blackbox display pipeline integration complete"
```

---

## Self-Review

**Spec coverage** (each spec section → task):
- §4.1 上移（audit/ → core + shim）→ Task 1.
- §4.2 黑盒接线（worker/activities/workflows/cli）→ Tasks 3-6.
- §5 组件清单（上移/新增 phase-marker/改动/删除 poller/复用 core）→ Task 1 (上移+shim), Task 4 (phase markers), Tasks 3-5 (改动), Task 5 (删 poller), 全程复用 core display（不改）.
- §6 数据流 + 并发（lock/不可变快照/并行 exploit）→ 复用 core（Task 1 不改 dispatcher）；并行 exploit agent 经 Task 3 wiring 自动多行展示.
- §7 `--plain`/降级/错误/重试/resume/summary → Task 6 (`--plain`), Task 3+4 (`log_error`/`end_agent`), Task 5 (summary 适配).
- §7.7 判断点 1（auth-validation agent 行）→ Task 4 Step 2；判断点 2（校验摘要不接 display）→ 非目标，无 task（正确）.
- §8 测试 L1/L2/L3 + L0 回归闸 + DoD → Task 1 Step 6 (L0), Task 3 (L3), Task 7 (L2), Task 8 (DoD).

**Placeholder scan:** 无 TBD/TODO。Task 1 Step 2 的 utils 多名 import 给出了精确改法（"改模块路径，保留同名列表"）；Task 4 Step 3 的 5 个插入点都给了精确锚点行号 + 完整代码块。

**Type consistency:** `SessionToolAuditLogger(session)`（Task 3/4）= Task 1 上移后的 `shannon_core.audit.session_tool_audit_logger.SessionToolAuditLogger`（经白盒 shim 也可达，但黑盒直接 import core）。`get_audit_session()`/`set_audit_session()`/`clear_audit_session()`（Tasks 3/4/5）来自 `shannon_core.audit.session_registry`。`AgentEndResult(success, duration_ms, cost_usd, attempt_number, model, error)`（Tasks 3/4）字段匹配 `models/audit.py:6`。`WorkflowSummary(status, total_duration_ms, total_cost_usd, completed_agents, agent_metrics, error)` + `AgentMetricsSummary(duration_ms, cost_usd)`（Task 5）匹配 `models/audit.py:25,30`。`run_with_display(meta, use_rich)`（Task 5）签名匹配 `display_lifecycle.py:14`。`AuditSession(meta, use_rich, console, dashboard)`（Task 7）匹配 `session.py:22`。`recon.execute`/`exploit.execute`/`validate_authentication` 的 `tool_audit_logger=` 参数（Tasks 3/4）由 Task 2 引入。`_AGENT_PREFIXES` 已含 exploit 键（无需改 formatter，与 §9 开放项 2 一致）。

**执行顺序:** Task 1（上移 + L0 闸）是 Task 3-7 的前置（黑盒 import `shannon_core.audit`）。Task 2（透传 tool_audit_logger）是 Task 3/4 的前置。Task 5（worker）import phase-marker activities → 依赖 Task 4。顺序 1→2→3→4→5→6→7→8，无环。
