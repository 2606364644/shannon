# Parallel Sink Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make code_index and PRE_RECON run in parallel, restore the original project's template analysis methodology in the LLM prompt, and merge results from both paths with simple deduplication.

**Architecture:** Replace the sequential code_index → PRE_RECON pipeline with `asyncio.gather()` parallel execution. Restore the original's forced two-step template analysis (glob → per-file escaping mode distinction) + Cross-Variant Verification + Coverage Audit table into the refactored prompt. Add a post-gather merge step that deduplicates deterministic `SinkCallSite[]` with LLM-discovered sinks by `(file_path, line)`.

**Tech Stack:** Python, Temporal workflows, Pydantic models, pytest

---

## Task 1: Restore Template Analysis Methodology in pre-recon-code.txt

**Files:**
- Modify: `prompts/pre-recon-code.txt:141-142` (Sink Hunter Agent description)

This task is independent of the pipeline changes and can be done first.

- [ ] **Step 1: Replace the Sink Hunter Agent description with original's two-step methodology**

In `prompts/pre-recon-code.txt`, replace lines 141-142 (the current single-sentence Sink Hunter description):

```
4. **XSS/Injection Sink Hunter Agent**:
   "Find all dangerous sinks where untrusted input could execute in browser contexts, system commands, file operations, template engines, or deserialization. Include XSS sinks (innerHTML, document.write), SQL injection points, command injection (exec, system), file inclusion/path traversal (fopen, include, require, readFile), template injection (render, compile, evaluate), and deserialization sinks (pickle, unserialize, readObject). Provide exact file locations with line numbers. If no sinks are found, report that explicitly."
```

With the original's three-part structure:

```
4. **XSS/Injection Sink Hunter Agent** (MANDATORY two-step process):
   **Step 1 — Template File Inventory (glob enumeration):**
   "Enumerate ALL template and view files in the project using glob patterns. Cover common template extensions: html, ejs, hbs, pug, jsx, tsx, vue, svelte, php, erb, jinja2, tmpl, and any additional template extensions discovered during analysis. Organize the inventory as a directory tree showing every template file path. If no template files exist, explicitly report 'no template files found' and skip Step 2 for templates."

   **Step 2 — Per-File Sink Analysis with Escaping Mode Distinction:**
   "For EACH template/view file discovered in Step 1, independently analyze it for dangerous sinks. For server-side template engines, distinguish between escaping modes: escaped directives (e.g., EJS `<%= %>`, Jinja2 `{{ }}`) vs unescaped directives (e.g., EJS `<%- %>`, Jinja2 `{{|safe}}`). Flag bare unescaped output without JSON.stringify wrappers as highest-risk. Also analyze non-template sinks: XSS sinks (innerHTML, document.write), SQL injection points, command injection (exec, system), file inclusion/path traversal (fopen, include, require, readFile), and deserialization sinks (pickle, unserialize, readObject). Provide exact file locations with line numbers. If no sinks are found, report that explicitly."

   **Cross-Variant Verification (MANDATORY):**
   "When template files exist in variant directories (brands, locales, themes, sub-applications), you MUST check for equivalent template files across ALL variant directories. For example, if `views/brandA/header/variables.html` is found, you MUST verify whether `views/brandB/header/variables.html` exists and analyze it independently. NEVER assume variant templates are identical — each file MUST be analyzed separately with its own sink report."
```

- [ ] **Step 2: Add Coverage Audit table to Section 9**

In `prompts/pre-recon-code.txt`, in Section 9 (line 281, `## 9. XSS Sinks and Render Contexts`), insert the Coverage Audit table **after** the "Network Surface Focus" paragraph (after line 284) and **before** the "Your output MUST include" paragraph (before line 286).

Insert this text:

```
	 **Template Coverage Audit (MANDATORY):** Before listing individual sinks, include a "Template Coverage Audit" table listing every template/view file discovered during Step 1 of the Sink Hunter Agent, with its sink count and analysis status. Format:

	 | Template File | Sink Count | Escaping Modes | Analysis Status |
	 |---|---|---|---|
	 | `views/brandA/header/variables.html` | 3 | 1 unescaped, 2 escaped | Analyzed |
	 | `views/brandB/header/variables.html` | 2 | 2 unescaped | Analyzed |
	 | `views/brandC/header/variables.html` | - | - | NOT ANALYZED |

	 Any file with "NOT ANALYZED" status indicates a coverage gap that MUST be resolved before finalizing the report. The table MUST include ALL template files from the Step 1 inventory, not just those with sinks.
```

- [ ] **Step 3: Remove `{{STATIC_DATAFLOW_HINTS}}` dependency**

Search the entire `prompts/pre-recon-code.txt` for any references to `STATIC_DATAFLOW_HINTS` or `static_dataflow_hints`. If found, remove or comment out the reference. This file should not depend on deterministic hint injection since PRE_RECON will run in parallel with code_index and the hints won't be available yet.

Note: Based on review, the current `pre-recon-code.txt` does NOT contain `{{STATIC_DATAFLOW_HINTS}}` — this variable is only in vuln prompt templates. Verify with `grep` to confirm, then skip if absent.

- [ ] **Step 4: Verify prompt with grep**

Run: `grep -n "STATIC_DATAFLOW_HINTS\|static_dataflow_hints" prompts/pre-recon-code.txt`

Expected: No output (no references found).

Run: `grep -n "Step 1.*Template\|Step 2.*Per-File\|Cross-Variant\|Coverage Audit" prompts/pre-recon-code.txt`

Expected: Hits for all four patterns, confirming the restored content.

- [ ] **Step 5: Commit**

```bash
git add prompts/pre-recon-code.txt
git commit -m "feat: restore original Sink Hunter template analysis methodology

Restore forced two-step template analysis (glob → per-file escaping mode
distinction), Cross-Variant Verification, and Coverage Audit table from
original Shannon. Replaces the single-sentence generic sink instruction
with the original's structured methodology."
```

---

## Task 2: Create Sink Merger Module

**Files:**
- Create: `packages/core/src/shannon_core/code_index/sink_merger.py`
- Create: `packages/core/tests/code_index/test_sink_merger.py`

This task creates the merge logic independently of the pipeline changes.

- [ ] **Step 1: Write failing tests for `merge_sink_reports()`**

Create `packages/core/tests/code_index/test_sink_merger.py`:

```python
"""Tests for sink report merger (deterministic + LLM)."""
import pytest
from shannon_core.code_index.sink_merger import merge_sink_reports, parse_llm_sinks
from shannon_core.code_index.parameter_models import (
    SinkCallSite, SinkCategory, DangerousSlot, SlotContext,
)


def _make_sink(file_path: str, line: int, rule_id: str = "py-os-system",
               category: SinkCategory = SinkCategory.COMMAND) -> SinkCallSite:
    return SinkCallSite(
        id=f"{file_path}:func:system:{line}:0",
        caller_id=f"{file_path}:func",
        callee_name="system",
        callee_receiver="os",
        category=category,
        sink_subtype="command_shell",
        file_path=file_path,
        line=line,
        column=0,
        dangerous_slots=[],
        rule_id=rule_id,
    )


LLM_REPORT_WITH_SINKS = """## Sink Findings

### SQL Injection
- `src/db.py:42`: cursor.execute(user_input)
- `src/db.py:87`: cursor.execute(filter_query)

### XSS
- `templates/index.ejs:15`: <%- userData %> (unescaped EJS output)
- `src/render.ts:33`: element.innerHTML = response.data

### Template Coverage Audit
| Template File | Sink Count | Escaping Modes | Analysis Status |
|---|---|---|---|
| `templates/index.ejs` | 1 | 1 unescaped | Analyzed |
| `templates/layout.ejs` | 0 | - | Analyzed |
"""

LLM_REPORT_NO_SINKS = """## Sink Findings

No dangerous sinks found in the analyzed codebase.
"""


class TestParseLlmSinks:
    def test_extracts_file_line_pairs(self):
        sinks = parse_llm_sinks(LLM_REPORT_WITH_SINKS)
        # Should find at least 4 file:line pairs
        assert len(sinks) >= 4
        file_lines = {(s.file_path, s.line) for s in sinks}
        assert ("src/db.py", 42) in file_lines
        assert ("src/db.py", 87) in file_lines
        assert ("templates/index.ejs", 15) in file_lines
        assert ("src/render.ts", 33) in file_lines

    def test_empty_report_returns_empty(self):
        sinks = parse_llm_sinks(LLM_REPORT_NO_SINKS)
        assert sinks == []

    def test_category_inference_sql(self):
        sinks = parse_llm_sinks(LLM_REPORT_WITH_SINKS)
        sql_sinks = [s for s in sinks if s.file_path == "src/db.py" and s.line == 42]
        assert len(sql_sinks) == 1
        assert sql_sinks[0].category == SinkCategory.SQL

    def test_category_inference_xss(self):
        sinks = parse_llm_sinks(LLM_REPORT_WITH_SINKS)
        xss_sinks = [s for s in sinks if s.file_path == "src/render.ts"]
        assert len(xss_sinks) == 1
        assert xss_sinks[0].category == SinkCategory.XSS

    def test_category_inference_template(self):
        sinks = parse_llm_sinks(LLM_REPORT_WITH_SINKS)
        tmpl_sinks = [s for s in sinks if s.file_path == "templates/index.ejs"]
        assert len(tmpl_sinks) == 1
        # Template file sink should be TEMPLATE or XSS
        assert tmpl_sinks[0].category in (SinkCategory.TEMPLATE, SinkCategory.XSS)


class TestMergeSinkReports:
    def test_deduplicates_overlapping_sinks(self):
        deterministic = [
            _make_sink("src/db.py", 42, category=SinkCategory.SQL),
        ]
        llm_report = "- `src/db.py:42`: cursor.execute(user_input)\n"
        merged = merge_sink_reports(deterministic, llm_report)
        # Should have exactly 1 sink at that location (deterministic wins)
        db_42 = [s for s in merged if s.file_path == "src/db.py" and s.line == 42]
        assert len(db_42) == 1
        assert db_42[0].rule_id == "py-os-system"  # deterministic record kept

    def test_adds_llm_only_sinks(self):
        deterministic = []
        llm_report = "- `templates/index.ejs:15`: <%- userData %> (unescaped)\n"
        merged = merge_sink_reports(deterministic, llm_report)
        assert len(merged) == 1
        assert merged[0].file_path == "templates/index.ejs"
        assert merged[0].line == 15
        assert merged[0].rule_id == "llm-sink-hunter"
        assert merged[0].needs_review is True

    def test_preserves_all_deterministic(self):
        deterministic = [
            _make_sink("src/a.py", 10),
            _make_sink("src/b.py", 20),
        ]
        merged = merge_sink_reports(deterministic, "")
        assert len(merged) == 2

    def test_merges_mixed(self):
        deterministic = [
            _make_sink("src/a.py", 10),
            _make_sink("src/b.py", 20),
        ]
        llm_report = "- `src/b.py:20`: system(cmd)\n- `src/c.py:30`: eval(x)\n"
        merged = merge_sink_reports(deterministic, llm_report)
        # a:10 deterministic, b:20 deduped (deterministic wins), c:30 LLM-only
        assert len(merged) == 3
        file_lines = {(s.file_path, s.line) for s in merged}
        assert ("src/a.py", 10) in file_lines
        assert ("src/b.py", 20) in file_lines
        assert ("src/c.py", 30) in file_lines
        # c:30 should be LLM-sourced
        c_30 = [s for s in merged if s.file_path == "src/c.py"][0]
        assert c_30.rule_id == "llm-sink-hunter"

    def test_llm_sink_id_format(self):
        llm_report = "- `src/x.py:99`: pickle.loads(data)\n"
        merged = merge_sink_reports([], llm_report)
        assert len(merged) == 1
        assert merged[0].id == "llm:src/x.py:99"

    def test_llm_sink_needs_review(self):
        llm_report = "- `src/x.py:99`: pickle.loads(data)\n"
        merged = merge_sink_reports([], llm_report)
        assert merged[0].needs_review is True

    def test_empty_both(self):
        merged = merge_sink_reports([], "")
        assert merged == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_sink_merger.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.code_index.sink_merger'`

- [ ] **Step 3: Implement `sink_merger.py`**

Create `packages/core/src/shannon_core/code_index/sink_merger.py`:

```python
"""Merge deterministic sink detection results with LLM-discovered sinks.

Reads the LLM pre-recon deliverable text, extracts sink locations via
regex, deduplicates against deterministic SinkCallSite[] by (file_path, line),
and appends LLM-only sinks as new SinkCallSite instances with rule_id
"llm-sink-hunter" and needs_review=True.
"""

import re
import logging
from pydantic import BaseModel

from shannon_core.code_index.parameter_models import (
    SinkCallSite,
    SinkCategory,
    DangerousSlot,
    SlotContext,
)

logger = logging.getLogger(__name__)

# Regex to match file:line patterns in LLM reports.
# Matches backtick-wrapped paths like `src/db.py:42` or `templates/index.ejs:15`.
_FILE_LINE_RE = re.compile(r"`([^`]+):(\d+)`")

# Category inference keywords — first match in the LLM report section wins.
# Each entry: (keyword_pattern, SinkCategory).
# Ordered from most specific to least.
_CATEGORY_HINTS: list[tuple[re.Pattern, SinkCategory]] = [
    (re.compile(r"SQL\s*Injection|sql_raw|cursor\.execute|\.query\(", re.IGNORECASE), SinkCategory.SQL),
    (re.compile(r"Command\s*Injection|command_exec|system\(|exec\(|popen\(", re.IGNORECASE), SinkCategory.COMMAND),
    (re.compile(r"SSRF|server.side.request|fetch\(|requests\.", re.IGNORECASE), SinkCategory.SSRF),
    (re.compile(r"XSS|cross.site.scripting|innerHTML|document\.write", re.IGNORECASE), SinkCategory.XSS),
    (re.compile(r"Template|SSTI|render_template|<%-.*%>|{{\|safe}}", re.IGNORECASE), SinkCategory.TEMPLATE),
    (re.compile(r"Path\s*Traversal|LFI|RFI|file\s*include|fopen|readFile", re.IGNORECASE), SinkCategory.FILE),
    (re.compile(r"Deserializ|pickle\.loads?|unserialize|readObject", re.IGNORECASE), SinkCategory.DESERIALIZATION),
    (re.compile(r"Redirect|open.redirect|location\.href", re.IGNORECASE), SinkCategory.REDIRECT),
]

# Maximum characters of LLM report context to scan before a file:line match
# when inferring category.
_CONTEXT_WINDOW = 500


class LlmSinkCandidate(BaseModel):
    """A sink location extracted from an LLM report."""
    file_path: str
    line: int
    category: SinkCategory = SinkCategory.XSS  # default, overridden by inference


def _infer_category(report_text: str, match_start: int) -> SinkCategory:
    """Infer SinkCategory from the LLM report text preceding a file:line match."""
    context_start = max(0, match_start - _CONTEXT_WINDOW)
    context = report_text[context_start:match_start]
    for pattern, category in _CATEGORY_HINTS:
        if pattern.search(context):
            return category
    # Fallback: also check the line after the match
    line_end = report_text.find("\n", match_start)
    if line_end == -1:
        line_end = len(report_text)
    after_line = report_text[match_start:line_end]
    for pattern, category in _CATEGORY_HINTS:
        if pattern.search(after_line):
            return category
    return SinkCategory.XSS  # conservative default


def parse_llm_sinks(report_text: str) -> list[LlmSinkCandidate]:
    """Extract sink locations from an LLM free-text report.

    Looks for backtick-wrapped file:line patterns (e.g., `src/db.py:42`)
    and infers categories from surrounding context keywords.
    """
    if not report_text or not report_text.strip():
        return []

    candidates: list[LlmSinkCandidate] = []
    seen: set[tuple[str, int]] = set()

    for m in _FILE_LINE_RE.finditer(report_text):
        file_path = m.group(1)
        try:
            line = int(m.group(2))
        except ValueError:
            continue
        key = (file_path, line)
        if key in seen:
            continue
        seen.add(key)
        category = _infer_category(report_text, m.start())
        candidates.append(LlmSinkCandidate(file_path=file_path, line=line, category=category))

    return candidates


def merge_sink_reports(
    deterministic_sinks: list[SinkCallSite],
    llm_report_text: str,
) -> list[SinkCallSite]:
    """Merge deterministic sink detection results with LLM-discovered sinks.

    Deduplication: deterministic sinks win on (file_path, line) collision.
    LLM-only sinks are appended with rule_id="llm-sink-hunter" and
    needs_review=True.
    """
    # Build set of deterministic (file_path, line) pairs
    det_keys: set[tuple[str, int]] = {
        (s.file_path, s.line) for s in deterministic_sinks
    }

    # Parse LLM report
    llm_candidates = parse_llm_sinks(llm_report_text)

    # Build merged list: start with all deterministic
    merged = list(deterministic_sinks)

    # Append LLM-only sinks
    for cand in llm_candidates:
        if (cand.file_path, cand.line) in det_keys:
            logger.debug(
                "merge: LLM sink %s:%d already in deterministic results, skipping",
                cand.file_path, cand.line,
            )
            continue
        merged.append(SinkCallSite(
            id=f"llm:{cand.file_path}:{cand.line}",
            caller_id="",
            callee_name="",
            callee_receiver=None,
            category=cand.category,
            sink_subtype="llm_discovered",
            file_path=cand.file_path,
            line=cand.line,
            column=0,
            dangerous_slots=[],
            rule_id="llm-sink-hunter",
            needs_review=True,
        ))

    logger.info(
        "merge: %d deterministic + %d LLM-only = %d total sinks",
        len(deterministic_sinks),
        len(merged) - len(deterministic_sinks),
        len(merged),
    )
    return merged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_sink_merger.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/sink_merger.py \
        packages/core/tests/code_index/test_sink_merger.py
git commit -m "feat: add sink merger module for deterministic + LLM sink deduplication

Parses LLM pre-recon deliverable text for file:line sink patterns,
infers SinkCategory from surrounding context keywords, and merges
with deterministic SinkCallSite[] using simple (file_path, line)
deduplication. LLM-only sinks are marked with rule_id='llm-sink-hunter'
and needs_review=True."
```

---

## Task 3: Add Merge Activity and Wire into Pipeline

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` (add `run_merge_sink_reports` activity)
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` (parallel execution + merge step)

- [ ] **Step 1: Add `run_merge_sink_reports` activity to activities.py**

In `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`, add the following activity function after the `run_save_adjudication` function (after line 261):

```python
@activity.defn
async def run_merge_sink_reports(input: ActivityInput) -> dict:
    """Merge deterministic sinks with LLM-discovered sinks from pre-recon."""
    try:
        import json
        from shannon_core.code_index.sink_merger import merge_sink_reports
        from shannon_core.code_index.parameter_models import SinkCallSite
        from shannon_core.utils.atomic_write import atomic_write_json

        repo, deliverables, _ = _get_paths(input)

        # Load deterministic sinks from code_index.json
        code_index_path = deliverables / "code_index.json"
        det_sinks: list[SinkCallSite] = []
        if code_index_path.exists():
            from shannon_core.code_index.models import CodeIndex
            index = CodeIndex.model_validate_json(code_index_path.read_text())
            det_sinks = index.sink_call_sites

        # Read LLM pre-recon deliverable
        llm_report = ""
        pre_recon_path = deliverables / "pre_recon_deliverable.md"
        if pre_recon_path.exists():
            llm_report = pre_recon_path.read_text()

        # Merge
        merged = merge_sink_reports(det_sinks, llm_report)

        # Write merged sinks back to code_index.json (update the field)
        if code_index_path.exists():
            index = CodeIndex.model_validate_json(code_index_path.read_text())
            index.sink_call_sites = merged
            atomic_write_json(code_index_path, json.loads(index.model_dump_json()))

        return {
            "deterministic_count": len(det_sinks),
            "llm_only_count": len(merged) - len(det_sinks),
            "total_count": len(merged),
        }
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

- [ ] **Step 2: Modify workflows.py for parallel execution**

In `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`, make **two edits**:

**Edit A: Remove the standalone code_index call (lines 73-78).**

Delete these lines:
```python
        # Code Index — deterministic AST analysis before PRE_RECON
        code_index_result = await workflow.execute_activity(
            activities.run_code_index, act_input,
            start_to_close_timeout=timedelta(minutes=10),
        )
        self._state.code_index_stats = code_index_result
```

**Edit B: Inside the `try` block (starts at line 111), replace lines 112-136** (the PRE_RECON + fusion + adjudication section, from `if AgentName.PRE_RECON.value not in self._state.completed_agents:` through the closing `self._state.current_agent = None`) **with the parallel gather + merge:**

```python
            # === Parallel: Code Index (deterministic) ∥ PRE_RECON (LLM) ===
            # These two have no data dependency. The original Shannon had no
            # deterministic layer, so PRE_RECON's Sink Hunter runs fine
            # without static-dataflow-hints.

            if AgentName.PRE_RECON.value not in self._state.completed_agents:
                self._state.current_phase = "pre-recon"
                self._state.current_agent = AgentName.PRE_RECON.value

                pre_recon_input = ActivityInput(
                    **{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value}
                )

                code_index_result, pre_recon_metrics = await asyncio.gather(
                    workflow.execute_activity(
                        activities.run_code_index, act_input,
                        start_to_close_timeout=timedelta(minutes=10),
                    ),
                    workflow.execute_activity(
                        activities.run_agent, pre_recon_input,
                        start_to_close_timeout=timedelta(hours=2),
                        retry_policy=PRODUCTION_RETRY,
                    ),
                )

                self._state.code_index_stats = code_index_result
                self._state.completed_agents.append(AgentName.PRE_RECON.value)
                self._state.agent_metrics[AgentName.PRE_RECON.value] = pre_recon_metrics

                # Merge deterministic sinks with LLM-discovered sinks
                await workflow.execute_activity(
                    activities.run_merge_sink_reports, act_input,
                    start_to_close_timeout=timedelta(minutes=2),
                )

                # Entry point fusion: merge deterministic + LLM discoveries
                fusion_input = ActivityInput(
                    **{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value}
                )
                await workflow.execute_activity(
                    activities.run_entry_point_fusion, fusion_input,
                    start_to_close_timeout=timedelta(minutes=2),
                )

                # Adjudicate merged entry points by confidence
                adjudication_input = ActivityInput(
                    **{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value}
                )
                await workflow.execute_activity(
                    activities.run_save_adjudication, adjudication_input,
                    start_to_close_timeout=timedelta(minutes=2),
                )
                self._state.current_agent = None
```

Note: The RECON block (lines 139-149) and everything after stays unchanged. Only the code_index + PRE_RECON section is affected.

- [ ] **Step 3: Verify the modified file parses**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -c "import ast; ast.parse(open('packages/whitebox/src/shannon_whitebox/pipeline/workflows.py').read()); print('OK')"`

Expected: `OK`

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -c "import ast; ast.parse(open('packages/whitebox/src/shannon_whitebox/pipeline/activities.py').read()); print('OK')"`

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py \
        packages/whitebox/src/shannon_whitebox/pipeline/activities.py
git commit -m "feat: run code_index and PRE_RECON in parallel with asyncio.gather

- code_index (deterministic AST) and PRE_RECON (LLM agent) now execute
  concurrently via asyncio.gather() instead of sequentially
- Added run_merge_sink_reports activity that parses LLM pre-recon
  deliverable for sink locations, deduplicates against deterministic
  SinkCallSite[], and writes merged results back to code_index.json
- Merge runs after gather completes, before entry point fusion"
```

---

## Task 4: End-to-End Validation

**Files:**
- No new files; verify existing tests still pass

- [ ] **Step 1: Run sink merger tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_sink_merger.py -v`

Expected: All PASS.

- [ ] **Step 2: Run existing code_index tests to verify no regression**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/ -v --timeout=60`

Expected: All PASS (no regressions from the pipeline changes; sink_detector, chain_propagator, etc. are unaffected).

- [ ] **Step 3: Verify prompt changes are correct**

Run: `grep -c "Step 1.*Template\|Step 2.*Per-File\|Cross-Variant\|Coverage Audit" prompts/pre-recon-code.txt`

Expected: At least 4 lines (one per pattern).

Run: `grep -c "STATIC_DATAFLOW_HINTS" prompts/pre-recon-code.txt`

Expected: 0 (no dependency on deterministic hints).

- [ ] **Step 4: Verify import chain**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -c "from shannon_core.code_index.sink_merger import merge_sink_reports, parse_llm_sinks; print('Import OK')"`

Expected: `Import OK`

- [ ] **Step 5: Final commit (if any test fixes needed)**

If any test adjustments were needed, commit them:

```bash
git add -u
git commit -m "fix: address test regressions from parallel sink analysis"
```
