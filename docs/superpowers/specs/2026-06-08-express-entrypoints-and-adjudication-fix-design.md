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

Confidence and `needs_llm_review`:
- `LLM_REVIEW_THRESHOLD = 0.8` in `entry_points.py`. Routes with confidence >= 0.8 get `needs_llm_review=False` and are auto-confirmed.
- `app.use(...)` with a path string argument (route-level middleware) gets confidence=0.80, `needs_llm_review=False`.
- `app.use(...)` WITHOUT a path string argument (framework middleware like `session()`, `bodyParser()`) is **excluded** — not an attack surface entry point.

Implementation: Add a `_detect_express_routes()` function called from `_detect_typescript()`. It operates in two passes:

**Pass 1 — FuncBlock scan:** Scans each block's `source_code` for `(app|router)\.(get|post|put|delete|patch|all|use)\(` patterns. Extracts route path from first string argument. Creates one `EntryPoint` per route, all sharing the same `func_block_id`.

**Pass 2 — Top-level route scan:** For files in common route directories (`routes/`, `router/`, `server.js`, `app.js`), scans the full file source for route patterns that are NOT inside any FuncBlock. These top-level route registrations are created as synthetic entry points with `func_block_id` pointing to the file itself (e.g., `server.js::0`).

**`app.use()` filtering:** Only match `app.use('/path', ...)` or `router.use('/path', ...)` where the first argument is a string literal starting with `/`. Skip `app.use(fn)` calls where the first argument is not a route path (framework middleware).

### Fix B: `save_adjudication()` Pipeline Step

Add a deterministic Python function that runs after PRE_RECON agent completes and before `rebuild_call_chains`. This replaces the non-existent `save-deliverable` CLI for the ENTRY_POINTS deliverable.

**New function:** `save_adjudication(deliverables_dir: str) -> None`

**File:** `packages/core/src/shannon_core/code_index/__init__.py`

Logic:
1. Read `code_index.json` → get candidate entry points
2. High-confidence (`confidence >= 0.8`, `needs_llm_review=false`) → auto-confirm with `verdict=confirmed`, `source=code_index`
3. Low-confidence (`needs_llm_review=true`) → default to `confirmed` with `source=code_index` (conservative: include rather than exclude)
4. Build `AdjudicationResult` and write to `entry_points.json`

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

## Known Limitations

- **Shared func_block_id:** When multiple Express routes are registered inside one function (e.g., NodeGoat's `index(app, db)`), all routes share the same `func_block_id`. `rebuild_call_chains` deduplicates by ID, producing a single chain from that function. This means all routes in the function share one call chain rather than being analyzed independently. This is acceptable because the chain includes calls to all handlers, and vulnerability agents analyze the full chain.
- **Dynamic route patterns:** Routes computed at runtime (e.g., `app.get(config.route, ...)`) are not detected. Only static string literal routes are matched.
- **No LLM adjudication yet:** All entry points are auto-confirmed. The `save_adjudication()` pipeline step is designed to be extended later — when PRE_RECON can reliably output structured adjudication data, step 3 can be enhanced to use it instead of defaulting to confirmed.

## Files Changed

| File | Change |
|------|--------|
| `packages/core/src/shannon_core/code_index/entry_points.py` | Add `_detect_express_routes()` with Pass 1 (FuncBlock scan) and Pass 2 (top-level route scan) |
| `packages/core/src/shannon_core/code_index/__init__.py` | Add `save_adjudication()` function; wire `_detect_express_routes()` into `build_code_index()` |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | Add `run_save_adjudication` activity |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | Wire new activity into pipeline |
