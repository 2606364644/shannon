# Report Generation Gap Fix Design

**Date:** 2026-06-03
**Status:** Approved
**Scope:** Align shannon-py report generation with original /root/shannon capabilities

## Problem Statement

The refactored shannon-py project has five gaps in report generation compared to the original /root/shannon project. Two are critical (P0), two are moderate (P1), and one is minor (P2).

### Gap Summary

| # | Severity | Gap | Impact |
|---|----------|-----|--------|
| 1 | P0 Critical | File naming mismatch: whitebox writes `_analysis_deliverable.md`, blackbox assembler reads `_findings.md` | Whitebox-only scans produce empty final reports |
| 2 | P0 Critical | Missing deterministic findings renderer (JSON queue → Markdown without LLM) | No fallback when LLM fails; inconsistent output |
| 3 | P1 Moderate | Missing ReportOutputProvider extensibility interface | Cannot add PDF/HTML/JSON output formats |
| 4 | P1 Moderate | ReportAssembler ignores report_config filtering (min_severity, min_confidence) | Low-severity noise in reports |
| 5 | P2 Minor | Missing model information injection into Executive Summary | Missing audit trail of which AI model was used |

## Design

### 1. Deterministic Findings Renderer

**File:** `packages/core/src/shannon_core/services/findings_renderer.py`

A pure-Python renderer that converts JSON exploitation queue files into structured Markdown without calling any LLM. Functionally equivalent to the original `findings-renderer.ts`.

#### Class Structure

```
FindingsRenderer
├── CLASS_CONFIG: dict[str, VulnClassConfig]  # Per-class rendering config
├── render_findings_from_queues(deliverables_path, report_config?) -> None
├── filter_vulnerabilities(queue, config) -> list[Vulnerability]
└── (helper functions per vuln class)
    ├── render_injection_entry(vuln) -> str
    ├── render_xss_entry(vuln) -> str
    ├── render_auth_entry(vuln) -> str
    ├── render_authz_entry(vuln) -> str
    ├── render_ssrf_entry(vuln) -> str
    └── render_misconfig_entry(vuln) -> str
```

#### VulnClassConfig

```python
class VulnClassConfig:
    heading: str              # Markdown section heading
    none_found_label: str     # Message when no vulnerabilities found
    queue_file: str           # Input JSON filename
    findings_file: str        # Output Markdown filename
    render_entry: Callable    # Per-entry renderer function
```

#### Rendering Logic

1. Iterate all 6 vulnerability classes
2. Skip if findings file already exists (respect LLM-generated results)
3. Read and parse JSON queue file using existing `VulnerabilityQueue` Pydantic model
4. Apply ReportConfig filtering (min_severity, min_confidence)
5. For empty queues: write "No vulnerabilities found" message
6. For non-empty queues: render each entry using class-specific renderer
7. Append disclaimer (matches original behavior)
8. Write findings file to deliverables directory

#### Entry Rendering

Each entry follows the standard format matching the original:

```markdown
### [VULN-ID]

**Summary:**
- **Field 1:** value
- **Field 2:** value
...

**Notes:** optional notes
```

Field mappings per class (matching original findings-renderer.ts exactly):

- **Injection**: vulnerable location (source + path), sink_call, concat_occurrences, sanitization_observed, verdict, witness_payload
- **XSS**: vulnerable location (source + path), sink_function, render_context, encoding_observed, verdict, witness_payload
- **Auth**: source_endpoint, vulnerable_code_location, missing_defense, exploitation_hypothesis, suggested_exploit_technique
- **Authz**: endpoint, vulnerable_code_location, role_context, guard_evidence, side_effect, reason, minimal_witness
- **SSRF**: source_endpoint, vulnerable_parameter, vulnerable_code_location, missing_defense, exploitation_hypothesis, suggested_exploit_technique
- **Misconfig**: source_endpoint, vulnerable_parameter, vulnerable_code_location, missing_defense, exploitation_hypothesis, suggested_exploit_technique, redirect_sink, existing_validation

#### Placement Rationale

Placed in `packages/core` because:
- Used by both whitebox and blackbox workflows
- Depends only on `queue_schemas.py` (also in core) and file I/O utilities
- No external dependencies beyond Pydantic

### 2. Report Assembler Fixes

**File:** `packages/blackbox/src/shannon_blackbox/services/report_assembler.py`

#### 2a. File Naming Fix

Add `_analysis_deliverable.md` as third-priority fallback:

Priority order:
1. `{type}_exploitation_evidence.md` — blackbox exploitation evidence
2. `{type}_findings.md` — deterministic renderer output
3. `{type}_analysis_deliverable.md` — LLM-generated whitebox analysis (new fallback)

This ensures the assembler always finds deliverables regardless of scan mode.

#### 2b. ReportConfig Filtering

Move filtering logic into `FindingsRenderer` (where it belongs — at the data transformation layer). The assembler remains a simple concatenation service.

The `assemble()` method signature gains an optional `report_config` parameter that it passes through to the renderer if invoked as part of the same step.

#### 2c. Model Information Injection

New method `inject_model_info(report_path, session_path)`:

1. Read `session.json` from workspace root (`workspaces/{name}/session.json`)
2. Extract model names from `metrics.agents` — collect all unique model values from each agent entry
3. Find the "- Assessment Date: " line in the report
4. Insert `- Model: <comma-separated model list>` after it
5. Fallback: insert after `## Executive Summary` header if no date line found
6. Skip entirely if session.json missing, no model info, or no injection point found
7. Write updated report

### 3. ReportOutputProvider Interface

**File:** `packages/core/src/shannon_core/interfaces/report_output_provider.py`

Abstract base class with a single `generate()` method. Default `NoOpReportOutputProvider` does nothing.

```python
class ReportOutputProvider(ABC):
    @abstractmethod
    async def generate(self, report_path: Path, deliverables_path: Path) -> dict[str, str | None]:
        ...

class NoOpReportOutputProvider(ReportOutputProvider):
    async def generate(self, report_path: Path, deliverables_path: Path) -> dict[str, str | None]:
        return {"output_path": None}
```

Integration point: called at the end of the report assembly phase in blackbox workflow. Provider instance resolved from configuration (future: dependency injection or config-based selection).

### 4. Workflow Integration

#### Whitebox Workflow Changes

**File:** `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`

After all VULN agents complete, add a new activity call:

```
FindingsRenderer.render_findings_from_queues(deliverables_path, report_config)
```

This runs deterministically without LLM invocation.

#### Blackbox Workflow Changes

**File:** `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`

In `assemble_report` activity:
1. Call updated `ReportAssembler.assemble()` with three-priority fallback
2. Call `ReportAssembler.inject_model_info()` after assembly

After report agent completes:
3. Call `ReportOutputProvider.generate()` (NoOp by default)

#### Blackbox Activities Changes

**File:** `packages/blackbox/src/shannon_blackbox/pipeline/activities.py`

Update `assemble_report` to pass report_config and call model injection.

#### Updated Workflow Sequence

**Whitebox-only (exploitation=false):**
```
PRE_RECON → RECON → VULN_AGENTS →
FindingsRenderer.render_findings_from_queues() →    [NEW]
ReportAssembler.assemble() →
ReportAgent (executive summary) →
ReportAssembler.inject_model_info() →                [NEW]
ReportOutputProvider.generate()                      [NEW]
```

**Full scan (exploitation=true):**
```
PRE_RECON → RECON → VULN_AGENTS →
FindingsRenderer.render_findings_from_queues() →    [NEW, as fallback]
EXPLOIT_AGENTS →
ReportAssembler.assemble() (evidence > findings > analysis) →
ReportAgent (executive summary) →
ReportAssembler.inject_model_info() →                [NEW]
ReportOutputProvider.generate()                      [NEW]
```

## Files to Create

| File | Description |
|------|-------------|
| `packages/core/src/shannon_core/services/findings_renderer.py` | Deterministic JSON → Markdown renderer |
| `packages/core/src/shannon_core/interfaces/report_output_provider.py` | Extensible output provider interface |

## Files to Modify

| File | Changes |
|------|---------|
| `packages/blackbox/src/shannon_blackbox/services/report_assembler.py` | Three-priority fallback, model injection |
| `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` | Pass report_config, call model injection, call output provider |
| `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | Wire new steps into workflow |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | Add findings rendering step after vuln agents |
| `packages/core/src/shannon_core/services/__init__.py` | Export FindingsRenderer |

## Testing Strategy

1. **Unit tests for FindingsRenderer**: Test each render function with sample JSON data, verify Markdown output matches expected format
2. **Unit tests for ReportAssembler**: Test three-priority fallback with mock files, test model injection
3. **Unit tests for ReportOutputProvider**: Test NoOp implementation, verify interface contract
4. **Integration test**: End-to-end whitebox-only scan with exploitation=false, verify non-empty final report
5. **Regression test**: Full scan with exploitation=true, verify evidence files still take priority

## Out of Scope

- PDF/HTML/JSON output implementations (only the interface)
- Changes to prompt templates
- Changes to the Temporal workflow orchestration beyond adding new activity calls
- Performance optimization of the rendering pipeline
