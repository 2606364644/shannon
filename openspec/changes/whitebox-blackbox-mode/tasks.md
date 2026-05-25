# Tasks: Whitebox-Only & Blackbox-Only Scan Modes

## Task 1: CLI Flag Support
**Files:** `apps/cli/src/index.ts`, `apps/cli/src/commands/start.ts`

- [x] Add `--whitebox-only` and `--blackbox-only` boolean flags to argument parser in `index.ts`
- [x] Make `-u`/`--url` optional in `StartArgs` (change `url: string` → `url?: string`)
- [x] Add validation: `--whitebox-only` and `--blackbox-only` are mutually exclusive
- [x] Add validation: `--blackbox-only` requires `-u`
- [x] Add validation: default mode (neither flag) requires `-u` (preserve existing behavior)
- [x] Update workspace naming: when no URL provided, use `<repo-dirname>_static-<timestamp>`
- [x] Update `start()` to pass mode flags through to worker env

## Task 2: Docker Env Passthrough
**Files:** `apps/cli/src/docker.ts`, `apps/cli/src/env.ts`

- [x] Add `SHANNON_WHITEBOX_ONLY=1` and `SHANNON_BLACKBOX_ONLY=1` to worker container env when respective flag is set
- [x] Read these env vars in worker to determine mode

## Task 3: Temporal Type Changes
**Files:** `apps/worker/src/temporal/shared.ts`, `apps/worker/src/temporal/activities.ts`

- [x] Make `PipelineInput.webUrl` optional (`webUrl?: string`)
- [x] Add `whiteboxOnly?: boolean` and `blackboxOnly?: boolean` to `PipelineInput`
- [x] Make `ActivityInput.webUrl` optional
- [x] Add `whiteboxOnly?: boolean` and `blackboxOnly?: boolean` to `ActivityInput`
- [x] Add `promptOverride?: string` to `AgentExecutionInput`

## Task 4: Preflight Lite Mode
**Files:** `apps/worker/src/services/preflight.ts`

- [x] Make `targetUrl` parameter optional in `runPreflightChecks` (`targetUrl: string | undefined`)
- [x] Skip step 5 (URL reachability check) when `targetUrl` is undefined
- [x] Steps 1-4 (repo, config, code_path, credentials) remain unchanged
- [x] Verify: when `targetUrl` is provided, behavior is identical to current

## Task 5: Prompt Manager — Sentinel URL
**Files:** `apps/worker/src/services/prompt-manager.ts`

- [x] In `interpolateVariables`: compute `effectiveWebUrl` as `variables.webUrl || '(offline — source code analysis only)'`
- [x] Use `effectiveWebUrl` in `{{WEB_URL}}` replacement
- [x] Relax validation: remove `!variables.webUrl` from the guard clause, keep `!variables.repoPath` check
- [x] Verify: when `variables.webUrl` is set, output is identical to current

## Task 6: Agent Execution — Prompt Override
**Files:** `apps/worker/src/services/agent-execution.ts`

- [x] Add `promptOverride?: string` to `AgentExecutionInput` interface
- [x] In `execute()`: use `input.promptOverride ?? AGENTS[agentName].promptTemplate` to resolve template name
- [x] Verify: when `promptOverride` is undefined, behavior is identical

## Task 7: Static Recon Prompt
**Files:** `apps/worker/prompts/recon-static.txt` (NEW)

- [x] Create `recon-static.txt` based on `recon.txt` with these modifications:
  - Add preamble: "You are operating in offline/static analysis mode. No live target is available. Perform analysis using source code only."
  - Remove "Step 2: Interactive Application Exploration" (Playwright crawl)
  - Remove `playwright-cli` from `<cli_tools>` section
  - Adjust Step 3: change "correlate with browser observations" to "correlate across source code modules"
  - Keep the complete deliverable template structure (Sections 0-12) identical to `recon.txt`
  - Adjust Section 4 (API Endpoint Inventory): note that entries are derived from static route analysis
  - Adjust Section 6 (Network Map): note that the map is inferred from code, not observed

## Task 8: Whitebox Workflow
**Files:** `apps/worker/src/temporal/workflows.ts`

- [x] Export new `whiteboxPipelineWorkflow(input: PipelineInput)` function
- [x] Implement whitebox pipeline:
  1. Validate repoPath (reuse existing logic)
  2. Preflight (lite): pass `undefined` as URL → skips URL check
  3. Skip `syncPlaywrightStealthConfig`
  4. Skip `runAuthenticationValidation`
  5. `initDeliverableGit` (unchanged)
  6. `syncCodePathDenyRules` (unchanged)
  7. `persistOrValidateRunScope`: `vulnClasses=['injection','auth','authz','ssrf']`, `exploit=false`
  8. `runSequentialPhase('pre-recon', 'pre-recon', ...)` (unchanged)
  9. `runSequentialPhase('recon', 'recon', ...)` with `promptOverride='recon-static'` passed to agent execution
  10. Run 4 vuln pipelines: injection, auth, authz, ssrf (skip xss)
      - For each: run vuln agent → merge findings → skip exploit (exploit=false)
  11. `assembleReportActivity(exploit=false)` — uses findings-renderer
  12. `runReportAgent`
  13. `injectReportMetadataActivity`
  14. `generateReportOutputActivity`
- [x] Wire progress query handler (same pattern as existing workflow)
- [x] Error handling: same try/catch pattern with `computeSummary`

## Task 9: Blackbox Workflow
**Files:** `apps/worker/src/temporal/workflows.ts`, `apps/worker/src/temporal/activities.ts`

- [x] Export new `blackboxPipelineWorkflow(input: PipelineInput)` function
- [x] Add new activity `validateDeliverablesExist(input: ActivityInput)` that checks:
  - At least one `*_exploitation_queue.json` exists in deliverables
  - `recon_deliverable.md` exists
  - Returns list of VulnTypes that have non-empty queues
- [x] Implement blackbox pipeline:
  1. Validate repoPath + webUrl
  2. Preflight (full): URL check + auth validation
  3. `syncPlaywrightStealthConfig`
  4. `runAuthenticationValidation`
  5. `initDeliverableGit` (idempotent — skips if exists)
  6. `syncCodePathDenyRules`
  7. `validateDeliverablesExist`
  8. For each VulnType with a non-empty queue:
     - `checkExploitationQueue`
     - If shouldExploit → run exploit agent
  9. `assembleReportActivity(exploit=true)`
  10. `runReportAgent`
  11. `injectReportMetadataActivity`
  12. `generateReportOutputActivity`
- [x] Wire progress query handler
- [x] Error handling: same pattern

## Task 10: Worker Dispatch
**Files:** `apps/worker/src/temporal/worker.ts`

- [x] Read `SHANNON_WHITEBOX_ONLY` and `SHANNON_BLACKBOX_ONLY` from env
- [x] Select workflow function based on mode:
  - `SHANNON_WHITEBOX_ONLY=1` → `whiteboxPipelineWorkflow`
  - `SHANNON_BLACKBOX_ONLY=1` → `blackboxPipelineWorkflow`
  - Neither → `pentestPipelineWorkflow` (unchanged)
- [x] Register all three workflows with the worker
- [x] Pass mode flags into the workflow input

## Task 11: End-to-End Verification

- [ ] Run existing full pipeline: `shannon start -u <URL> -r <repo>` — verify identical behavior
- [ ] Run whitebox-only: `shannon start -r <repo> --whitebox-only` — verify:
  - No URL required
  - Produces: pre_recon_deliverable.md, recon_deliverable.md, 4× analysis deliverables, 4× queue.json, static report
  - XSS agent does not run
  - No Playwright/browser activity
- [ ] Run blackbox-only on same repo: `shannon start -u <URL> -r <repo> --blackbox-only` — verify:
  - Reads existing deliverables
  - Runs exploit agents for all queued vulnerabilities
  - Produces full report with exploitation evidence
- [ ] Verify `--whitebox-only` and `--blackbox-only` are mutually exclusive
- [ ] Verify `--blackbox-only` without `-u` shows error
- [x] Run `pnpm biome` and `pnpm run check` — zero new issues (requires dev environment with pnpm installed)
