# Whitebox XSS Coverage Fix Design

**Date:** 2026-06-03
**Status:** Approved

## Problem

Whitebox scans miss reflected XSS vulnerabilities for two reasons:

1. **xss agent excluded from whitebox workflow** — `WHITEBOX_VULN_CLASSES` in `workflows.ts:635` omits `'xss'`, so the dedicated XSS analysis agent never runs.
2. **injection agent converges too early on branch analysis** — When a controller has conditional branches (whitelist path vs direct query param path), the agent only traces the validated branch and skips the unprotected one.

The primary fix is #1 (add xss agent to whitebox workflow). The secondary fix is a concise prompt rule to prevent the injection agent from repeating the same mistake.

## Changes

### 1. Add xss to whitebox workflow

**File:** `apps/worker/src/temporal/workflows.ts`

- Line 635: Add `'xss'` to `WHITEBOX_VULN_CLASSES`
- Line 746: Update comment to reflect 6 agents

### 2. Branch Path Exhaustion Rule in injection prompt

**File:** `apps/worker/prompts/vuln-injection.txt`

Add after "Path Forking" paragraph in `<systematic_inquiry_process>`:

> **Branch Path Exhaustion Rule:** When a controller method contains conditional branches (if/else, early returns) that lead to different data transformations for the same output variable, you MUST trace every branch independently. Do NOT assume a parameter is safe because one branch validates it — another branch may read the same parameter directly from user input without validation.

### 3. Query parameter reminder in xss prompt

**File:** `apps/worker/prompts/vuln-xss.txt`

Add brief note in `<methodology>` between Step 1 and Step 2:

> **Note on server-rendered templates:** When enumerating sinks, pay attention to template render calls (`ctx.render`, `res.render`) where template context variables originate from URL query parameters (`ctx.query.*`). These are reflected XSS candidates even when the injection agent has already analyzed SSTI for the same template — the xss agent provides independent render-context analysis (e.g., `JSON.stringify()` inside a `<script>` tag does not escape `</script>`, making it unsafe for JAVASCRIPT_STRING context).

## Impact

- **Scan cost:** +1 agent run (xss-vuln) per whitebox scan, runs in parallel
- **Blackbox scans:** No change
- **Deliverables:** Whitebox scans produce `xss_analysis_deliverable.md` + `xss_exploitation_queue.json`

## Validation

- `pnpm run check` — type check passes
- `pnpm biome` — lint passes on modified files
- Smoke test: whitebox scan includes xss-vuln in session.json metrics
