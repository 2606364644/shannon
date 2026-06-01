# Fix Pipeline Defects D4, D5, D7

## Summary

Fix three engineering-layer defects in Shannon's whitebox pipeline: error state overwrite (D4), missing vuln agent retry (D5), and misconfig prompt inconsistency (D7).

## D4: Error State Overwrite

**Problem**: `PipelineState.error: str | None` — when multiple parallel vuln agents fail, only the last error is retained.

**Fix**:

1. `packages/whitebox/src/shannon_whitebox/pipeline/shared.py` line 26:
   - Change `error: str | None = None` to `errors: list[str] = field(default_factory=list)`

2. `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` line 132:
   - Change `self._state.error = f"..."` to `self._state.errors.append(f"...")`

3. `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`:
   - Apply the same change to `BlackboxPipelineState` for consistency.

4. `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` line 117:
   - Apply the same append change.

5. Any downstream code checking `state.error is not None` must be updated to `len(state.errors) > 0`.

**Scope**: `shared.py` + `workflows.py` in both whitebox and blackbox packages.

## D5: Vuln Agent Missing Retry Policy

**Problem**: Whitebox vuln agents have no `retry_policy`, so a transient failure permanently skips that vulnerability class. Blackbox already uses `RetryPolicy(maximum_attempts=3)` for its exploit agents.

**Fix**:

1. `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` lines 119-124:
   Add `retry_policy` to vuln agent task creation, matching blackbox's parameters:

   ```python
   retry_policy=RetryPolicy(
       maximum_attempts=3,
       initial_interval=timedelta(seconds=30),
       maximum_interval=timedelta(minutes=5),
       backoff_coefficient=2.0,
   )
   ```

**Not changing**: PRE_RECON (already has 50 retries, appropriate for its role as pipeline entry point) and RECON (single serial agent, failure should halt the pipeline).

**Scope**: 1 file, 1 location.

## D7: Misconfig Prompt Inconsistency

**Problem**: `vuln-misconfig.txt` is 89 lines while other 5 vuln prompts average 320 lines. It is missing: TodoWrite tracking, conclusion_trigger, _shared-session.txt, false_positives_to_avoid, system_architecture, definitions, detailed cli_tools, and data_format_specifications.

**Fix**: Complete rewrite of `prompts/vuln-misconfig.txt` using `vuln-injection.txt` as the structural skeleton, target 280-350 lines.

### New modules to add

| Module | Description |
|--------|-------------|
| `_shared-session.txt` include | Enables Playwright for live HTTP request verification of security headers |
| `system_architecture` | Phase position (RECON → Misconfig → Exploitation), upstream/downstream dependencies |
| `definitions` | "exploitable misconfiguration" standard: externally exploitable + concrete evidence |
| `cli_tools` | Tool usage rules aligned with other agents (Task Agent for source code, save-deliverable, TodoWrite) |
| `data_format_specifications` | Full exploitation_queue_format with misconfig-specific field semantics (missing_defense, redirect_sink, etc.) |
| `methodology_and_domain_expertise` | Systematic flow: TodoWrite task creation → HTTP response audit → source code correlation → config extraction → evidence collection → verdict. Domain expertise per misconfig category (CORS, CSP, cookie flags, open redirect, info disclosure, clickjacking) |
| `false_positives_to_avoid` | Common false positives: dev-only HSTS absence, localhost CORS *, test route verbose errors, debug mode in non-production |
| `conclusion_trigger` | Completion condition: all TodoWrite tasks completed + deliverable saved + queue JSON written |
| TodoWrite guidance | One task per endpoint in recon_deliverable.md, plus per-misconfig-category tasks |
| Enhanced `<critical>` | Thoroughness is Non-Negotiable, code is ground truth, no purely internal findings |

### Preserved from current prompt

- `<role>` — misconfig domain expert positioning
- `<objective>` — 6 check categories (security headers, CORS, cookie security, open redirect, info disclosure, clickjacking)
- Existing `@include` directives (`_target.txt`, `_code-path-rules.txt`, `_rules-of-engagement.txt`)
- `<context>` with `{{AUTH_CONTEXT}}`
- `<output_format>` JSON structure (expanded to match other agents' field set)

### Not changing

- Other 5 vuln prompts
- `AgentDefinition` (already points to `vuln-misconfig` template)
- Blackbox code

## Files Changed

| File | Change |
|------|--------|
| `packages/whitebox/src/shannon_whitebox/pipeline/shared.py` | D4: `error` → `errors: list[str]` |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | D4: append + D5: retry_policy |
| `packages/blackbox/src/shannon_blackbox/pipeline/shared.py` | D4: `error` → `errors: list[str]` |
| `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | D4: append |
| `prompts/vuln-misconfig.txt` | D7: full rewrite |

## Testing

- D4/D5: Verify pipeline still runs end-to-end. Inject a failure in one vuln agent and confirm error is collected in list, not overwriting.
- D7: Run misconfig agent against a test repo and verify it produces TodoWrite tasks, uses shared session, outputs complete queue JSON, and respects conclusion trigger.
