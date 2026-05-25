# Design: Whitebox-Only & Blackbox-Only Scan Modes

## Architecture Overview

```
┌─ Execution Modes ────────────────────────────────────────────────┐
│                                                                   │
│  DEFAULT (unchanged):                                             │
│  shannon start -u <URL> -r <repo>                                │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │ Preflight → Auth → PreRecon → Recon → 5×Vuln → 5×Exploit   ││
│  │ → Report                                                     ││
│  └──────────────────────────────────────────────────────────────┘│
│                                                                   │
│  WHITEBOX-ONLY:                                                   │
│  shannon start -r <repo> --whitebox-only                         │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │ Preflight(lite) → PreRecon → Recon(static) → 4×Vuln        ││
│  │ → FindingsRenderer → Report(static)                         ││
│  │                                                              ││
│  │ Skips: URL check, auth validation, XSS vuln, all exploits   ││
│  └──────────────────────────────────────────────────────────────┘│
│                         │                                         │
│                         │ .shannon/deliverables/                  │
│                         │ • pre_recon_deliverable.md              │
│                         │ • recon_deliverable.md                  │
│                         │ • *_analysis_deliverable.md (×4)       │
│                         │ • *_exploitation_queue.json (×4)       │
│                         │ • static report                        │
│                         ▼                                         │
│  BLACKBOX-ONLY:                                                   │
│  shannon start -u <URL> -r <repo> --blackbox-only                │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │ Preflight → Auth → Read existing deliverables               ││
│  │ → 5×Exploit → Report(full)                                  ││
│  │                                                              ││
│  │ Skips: pre-recon, recon, all vuln agents                    ││
│  └──────────────────────────────────────────────────────────────┘│
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
```

## Layer-by-Layer Changes

### 1. CLI Layer (`apps/cli/`)

**`StartArgs` changes:**
```typescript
export interface StartArgs {
  url?: string;           // optional when whitebox-only
  repo: string;
  config?: string;
  workspace?: string;
  output?: string;
  pipelineTesting: boolean;
  debug: boolean;
  version: string;
  whiteboxOnly: boolean;  // NEW
  blackboxOnly: boolean;  // NEW
}
```

**Argument parsing:**
- `-u`/`--url` becomes optional
- Add `--whitebox-only` flag (boolean, no value)
- Add `--blackbox-only` flag (boolean, no value)
- Validation: `--whitebox-only` and `--blackbox-only` are mutually exclusive
- Validation: `--blackbox-only` requires `-u`
- Validation: default mode (neither flag) requires `-u` (unchanged behavior)

**Workspace naming for whitebox-only:**
- When no URL provided, derive workspace name from repo directory name + timestamp
- Pattern: `<repo-dirname>_static-<timestamp>`

**Environment flags passed to worker:**
- `SHANNON_WHITEBOX_ONLY=1` or `SHANNON_BLACKBOX_ONLY=1`
- These flow through to `PipelineInput`

### 2. Temporal Shared Types (`apps/worker/src/temporal/shared.ts`)

**`PipelineInput` changes:**
```typescript
export interface PipelineInput {
  webUrl?: string;           // was: string (now optional for whitebox)
  repoPath: string;
  // ... existing fields unchanged ...
  whiteboxOnly?: boolean;    // NEW
  blackboxOnly?: boolean;    // NEW
}
```

**`ActivityInput` changes (`activities.ts`):**
```typescript
export interface ActivityInput {
  webUrl?: string;           // was: string (now optional for whitebox)
  // ... existing fields unchanged ...
  whiteboxOnly?: boolean;    // NEW
  blackboxOnly?: boolean;    // NEW
}
```

### 3. Workflow Layer (`apps/worker/src/temporal/workflows.ts`)

**Whitebox-only workflow branch:**

```
pentestPipeline(input):
  if (input.whiteboxOnly):
    1. Validate repoPath (unchanged)
    2. Preflight (lite): skip URL check, skip auth validation
    3. syncPlaywrightStealthConfig (skip — no browser needed)
    4. runAuthenticationValidation (skip — no live target)
    5. initDeliverableGit (unchanged)
    6. syncCodePathDenyRules (unchanged)
    7. persistOrValidateRunScope: vulnClasses=[inj,auth,authz,ssrf], exploit=false
    8. runSequentialPhase: pre-recon
    9. runSequentialPhase: recon (with promptTemplate override → 'recon-static')
    10. Run 4 vuln pipelines (no XSS):
        for [injection, auth, authz, ssrf]:
          run vuln agent
          merge findings into queue
          (skip exploit agent — exploit=false)
    11. assembleReportActivity(exploit=false) — uses findings-renderer
    12. runReportAgent
    13. injectReportMetadataActivity
    14. generateReportOutputActivity
    return state

  if (input.blackboxOnly):
    1. Validate repoPath + webUrl
    2. Preflight (full): URL check, auth validation
    3. syncPlaywrightStealthConfig
    4. runAuthenticationValidation
    5. initDeliverableGit
    6. syncCodePathDenyRules
    7. Validate deliverables exist:
       - Check for at least one *_exploitation_queue.json
       - If missing → throw ApplicationFailure.nonRetryable
    8. For each VulnClass in ALL_VULN_CLASSES:
        checkExploitationQueue
        if shouldExploit → run exploit agent
    9. assembleReportActivity(exploit=true)
    10. runReportAgent
    11. injectReportMetadataActivity
    12. generateReportOutputActivity
    return state

  else:
    // EXISTING PIPELINE — UNCHANGED
```

**Key design decisions:**

- Whitebox and blackbox each run as **fresh Temporal workflows** (no resume interaction)
- The handoff is purely file-based: blackbox reads `*_exploitation_queue.json` + `*_analysis_deliverable.md` + `pre_recon_deliverable.md` + `recon_deliverable.md` from the repo's `.shannon/deliverables/` directory
- Blackbox validates that these files exist before starting

### 4. Preflight Changes (`apps/worker/src/services/preflight.ts`)

**`runPreflightChecks` signature change:**
```typescript
export async function runPreflightChecks(
  targetUrl: string | undefined,    // was: string (now optional)
  repoPath: string,
  configPath: string | undefined,
  logger: ActivityLogger,
  skipGitCheck?: boolean,
  apiKey?: string,
  providerConfig?: ProviderConfig,
): Promise<Result<void, PentestError>>
```

**Behavior change:**
- If `targetUrl` is undefined → skip step 5 (URL reachability check)
- Steps 1-4 (repo, config, code_path, credentials) run unchanged
- In normal mode (`targetUrl` provided), behavior is identical to current

### 5. Prompt Layer

#### New file: `apps/worker/prompts/recon-static.txt`

- Based on `recon.txt` with the following modifications:
  - Remove "Step 2: Interactive Application Exploration" (the Playwright crawl step)
  - Remove Playwright from `<cli_tools>` section
  - Add preamble: "You are operating in offline mode. No live target is available. Analyze the source code only."
  - Keep the same deliverable template structure (Sections 0-12) unchanged
  - Adjust Step 3 wording: correlate source code findings without browser context
  - The agent derives all endpoint/route/parameter information from static analysis of source code

#### `{{WEB_URL}}` handling in `prompt-manager.ts`

**No changes to existing prompt files.** Instead, modify `interpolateVariables`:

```typescript
// In interpolateVariables, before the .replace() calls:
const effectiveWebUrl = variables.webUrl || '(offline — source code analysis only)';
```

Then use `effectiveWebUrl` instead of `variables.webUrl` in the replacement chain.

This means:
- `_target.txt`: becomes `URL: (offline — source code analysis only)` in whitebox mode
- `_vuln-scope.txt`: becomes "exploitable via (offline...) from the internet" — the agent context makes this clear
- Normal mode: `variables.webUrl` is always set, so behavior is identical

**Validation relaxation in `interpolateVariables`:**

```typescript
// Current: throws if webUrl is missing
// Change: allow empty/undefined webUrl
if (!variables || !variables.repoPath) {  // removed: !variables.webUrl
```

#### Prompt template override for recon

In the workflow, when `whiteboxOnly` is true, the recon agent uses a different prompt template. The mechanism:

- `AGENTS['recon'].promptTemplate` remains `'recon'` (unchanged)
- In `AgentExecutionService.execute` or the activity layer, detect `whiteboxOnly` flag and override `promptTemplate` from `'recon'` to `'recon-static'`
- This can be done by passing the override through `AgentExecutionInput`:

```typescript
export interface AgentExecutionInput {
  // ... existing fields ...
  promptOverride?: string;  // NEW: override AGENTS[name].promptTemplate
}
```

In `execute()`:
```typescript
const promptTemplate = input.promptOverride ?? AGENTS[agentName].promptTemplate;
```

### 6. Vuln Agent Behavior in Whitebox Mode

The 4 whitebox vuln agents (injection, auth, authz, ssrf) work without modification:

- Their primary tool is `Task Agent (Code Analysis)` — pure source code analysis
- `Playwright` is available but optional; in whitebox mode without a URL, the agent simply won't use it
- `{{WEB_URL}}` is replaced with the sentinel text; agents understand from context that live testing is unavailable
- The `externally_exploitable` field in queue.json can still be judged from code analysis
- `witness_payload` is still generated (theoretical, not verified live)

### 7. Blackbox-Only Workflow Details

The blackbox-only workflow is a **new, simplified workflow function**:

```
pentestBlackboxWorkflow(input):
  1. Preflight (full — with URL check and auth validation)
  2. Validate deliverables:
     - Must find at least one *_exploitation_queue.json
     - Must find recon_deliverable.md
     - Throw if missing
  3. For each VulnClass in ALL_VULN_CLASSES:
     a. checkExploitationQueue
     b. If queue has entries → run exploit agent
     c. If queue empty/missing → skip (log warning)
  4. assembleReportActivity(exploit=true)
  5. runReportAgent
  6. injectReportMetadataActivity
  7. generateReportOutputActivity
```

This is a separate exported function from `pentestPipelineWorkflow`. The worker dispatches to the correct workflow based on the mode flag.

### 8. Worker Entry Point (`apps/worker/src/temporal/worker.ts`)

The worker reads mode flags from environment variables and starts the appropriate workflow:

- `SHANNON_WHITEBOX_ONLY=1` → starts `whiteboxPipelineWorkflow`
- `SHANNON_BLACKBOX_ONLY=1` → starts `blackboxPipelineWorkflow`
- Neither → starts `pentestPipelineWorkflow` (unchanged)

### 9. Report Differences

**Whitebox report:**
- Uses `findings-renderer.ts` (same as `exploit=false`) to render queue.json into findings.md
- Report agent adds Executive Summary noting "offline analysis — no live exploitation"
- Contains: vulnerability analysis, source-to-sink traces, witness payloads, confidence ratings
- Does NOT contain: exploitation evidence, exfiltrated data, command execution proof

**Blackbox report (from blackbox-only run):**
- Full exploitation evidence for each vulnerability found in the queues
- Report agent produces standard Executive Summary
- Overwrites the previous whitebox report

## What Does NOT Change

To ensure zero impact on existing functionality:

- `pentestPipelineWorkflow` function body — untouched
- All existing prompt files (`recon.txt`, `vuln-*.txt`, `exploit-*.txt`, `report-executive.txt`) — untouched
- `AGENTS` registry — untouched
- `ALL_AGENTS` array — untouched
- `session.json` structure — untouched
- Resume logic (`loadResumeState`, `persistOrValidateRunScope`) — untouched
- Temporal activity functions — existing signatures unchanged, new activities added
- CLI default behavior (`start -u URL -r repo`) — untouched
- Docker compose, Dockerfile — untouched

## File Change Summary

| File | Change Type | Description |
|---|---|---|
| `apps/cli/src/commands/start.ts` | Modify | Add flags, make URL optional, pass mode to worker |
| `apps/cli/src/docker.ts` | Modify | Pass mode env vars to worker container |
| `apps/cli/src/index.ts` | Modify | Parse new CLI flags |
| `apps/worker/src/temporal/shared.ts` | Modify | Add `whiteboxOnly`/`blackboxOnly` to `PipelineInput` |
| `apps/worker/src/temporal/workflows.ts` | Modify | Add two new workflow functions |
| `apps/worker/src/temporal/activities.ts` | Modify | Make `webUrl` optional in `ActivityInput`, add `promptOverride` to execution input |
| `apps/worker/src/temporal/worker.ts` | Modify | Dispatch to correct workflow based on mode |
| `apps/worker/src/services/preflight.ts` | Modify | Make `targetUrl` optional, skip URL check when undefined |
| `apps/worker/src/services/prompt-manager.ts` | Modify | Allow empty `webUrl`, use sentinel value |
| `apps/worker/src/services/agent-execution.ts` | Modify | Support `promptOverride` |
| `apps/worker/prompts/recon-static.txt` | **New** | Pure source-code recon prompt |
