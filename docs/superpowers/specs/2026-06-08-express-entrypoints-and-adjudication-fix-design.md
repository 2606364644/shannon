# Fix: Express.js Entry Point Detection & Adjudication Pipeline

**Date:** 2026-06-08
**Status:** Approved

## Problem

White-box scan on NodeGoat (Express.js) produces 0 vulnerabilities. Two compounding bugs:

### Bug A: Express.js Entry Point Detection Missing

`_detect_typescript()` in `entry_points.py` only matches NestJS decorator patterns (`@Get`, `@Post`). Express.js uses function calls (`app.get()`, `router.post()`), which are not decorators. Result: 0 entry points detected for any Express.js project.

### Bug B: `entry_points.json` Never Generated

`rebuild_call_chains()` requires `entry_points.json` (AdjudicationResult format). The PRE_RECON agent prompt instructs it to write this file using `save-deliverable --type ENTRY_POINTS`, but `save-deliverable` does not exist anywhere in the project — not as a CLI binary, Python function, or SDK tool. The agent cannot fulfill this instruction.

## Design

### Fix A: Express.js Route Pattern Detection

**File:** `packages/core/src/shannon_core/code_index/entry_points.py`

Add Express.js route patterns to `_detect_typescript()`. These are function calls, not decorators, so scan `FuncBlock.source_code` with regex:

| Pattern | Entry Type | HTTP Method | Confidence |
|---------|-----------|-------------|------------|
| `app.get('/path', ...)` | http_route | GET | 0.90 |
| `app.post('/path', ...)` | http_route | POST | 0.90 |
| `app.put('/path', ...)` | http_route | PUT | 0.90 |
| `app.delete('/path', ...)` | http_route | DELETE | 0.90 |
| `app.patch('/path', ...)` | http_route | PATCH | 0.90 |
| `app.all('/path', ...)` | http_route | * | 0.85 |
| `app.use('/path', ...)` | http_route | MIDDLEWARE | 0.80 |
| `router.get('/path', ...)` | http_route | GET | 0.90 |
| `router.post('/path', ...)` | http_route | POST | 0.90 |
| `router.put('/path', ...)` | http_route | PUT | 0.90 |
| `router.delete('/path', ...)` | http_route | DELETE | 0.90 |
| `router.patch('/path', ...)` | http_route | PATCH | 0.90 |
| `router.all('/path', ...)` | http_route | * | 0.85 |
| `router.use('/path', ...)` | http_route | MIDDLEWARE | 0.80 |

Also support chained patterns: `app.route('/path').get(...).post(...)`.

All Express-detected entry points get `needs_llm_review=True` (confidence < 0.95 LLM_REVIEW_THRESHOLD) to ensure PRE_RECON adjudicates them.

Implementation: Add a `_detect_express_routes()` function called from `_detect_typescript()`. Scans each block's `source_code` for `(app|router)\.(get|post|put|delete|patch|all|use)\(` patterns. Extracts route path from first string argument.

### Fix B: `save_adjudication()` Pipeline Step

Add a deterministic Python function that runs after PRE_RECON agent completes and before `rebuild_call_chains`. This replaces the non-existent `save-deliverable` CLI for the ENTRY_POINTS deliverable.

**New function:** `save_adjudication(deliverables_dir: str) -> None`

**File:** `packages/core/src/shannon_core/code_index/__init__.py`

Logic:
1. Read `code_index.json` → get candidate entry points
2. High-confidence (confidence >= 0.8, `needs_llm_review=false`) → auto-confirm with `verdict=confirmed`, `source=code_index`
3. Low-confidence (`needs_llm_review=true`) → attempt to extract verdicts from `pre_recon_deliverable.md` using regex/pattern matching
4. For any remaining unadjudicated entry points → default to `confirmed` (conservative: include rather than exclude)
5. Build `AdjudicationResult` and write to `entry_points.json`

**New activity:** `run_save_adjudication`

**File:** `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`

Thin wrapper that calls `save_adjudication()`.

**Workflow change:**

**File:** `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`

Insert `run_save_adjudication` activity between PRE_RECON agent and `run_rebuild_call_chains`:

```
run_agent(PRE_RECON)
       ↓
run_save_adjudication     ← NEW
       ↓
run_rebuild_call_chains   ← unchanged
       ↓
run_agent(RECON)
```

### Unchanged

- `rebuild_call_chains()` — keeps reading `entry_points.json` as before
- PRE_RECON prompt — `save-deliverable` instructions retained (agent may attempt, but pipeline no longer depends on it)
- Other agent prompts — they use Write/Edit tools for deliverables, unaffected

## Verification

1. Run `shannon-whitebox start -r /path/to/NodeGoat`
2. Assert `code_index.json` has `total_entry_points > 0`
3. Assert `entry_points.json` is generated in deliverables
4. Assert `code_index.json` is updated with `total_chains > 0`
5. Assert vulnerability agents produce findings > 0

## Files Changed

| File | Change |
|------|--------|
| `packages/core/src/shannon_core/code_index/entry_points.py` | Add Express.js route detection |
| `packages/core/src/shannon_core/code_index/__init__.py` | Add `save_adjudication()` function |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | Add `run_save_adjudication` activity |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | Wire new activity into pipeline |
