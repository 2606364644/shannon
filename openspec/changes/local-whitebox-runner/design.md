## Context

Shannon's `--whitebox-only` mode runs static code analysis without a live target URL, yet it still requires the full Docker + Temporal stack. The worker's `services/` layer was designed to be Temporal-agnostic ("No Temporal imports in services" per CLAUDE.md), making bare-metal execution feasible. The `AgentExecutionService` already accepts an `ActivityLogger` interface that can be swapped out, and `AuditSession` is pure file I/O.

This fork tracks the official Shannon repo, so upstream compatibility is a hard constraint. The design minimizes changes to official files — `start.ts` is not touched at all.

Current execution paths:

```
CLI → docker run → Temporal Server (localhost:7233) → Worker → Activities → services/*
```

Target for local whitebox:

```
CLI → fork() → runner → services/*   (no Docker, no Temporal)
```

## Goals / Non-Goals

**Goals:**
- Local mode `--whitebox-only` runs with zero infrastructure: Node.js + API key + code repo
- Reuse 100% of existing `services/`, `ai/`, `audit/`, and `prompts/` code
- Maintain feature parity with Temporal-based whitebox (same agents, same deliverables)
- Provide bounded parallelism and built-in retry without Temporal
- Keep blackbox and default pipeline completely untouched
- Minimize changes to official files for easy upstream merges

**Non-Goals:**
- Removing Docker/Temporal requirement for blackbox or default mode
- Making npx whitebox mode Docker-free (npx users already have Docker)
- Modifying existing Temporal activities, workflows, or `start.ts`
- Changing the `AGENTS` registry, prompt files, or deliverable format
- Adding new agents or changing vulnerability classes

## Decisions

### D1: CLI invokes runner via `child_process.fork()`

**Choice:** `fork()` over direct import.

The CLI package (`@keygraph/shannon`) is published as a single-file bundle via tsdown and intentionally has no runtime dependency on worker code. `fork()` preserves this boundary — the CLI spawns a child process just like it spawns a Docker container today, but runs locally instead.

The child process communicates via stdio and exit codes. No IPC channel needed — progress is visible through the audit session's file-based logging (same as Temporal path).

**Alternatives considered:**
- **Direct import**: Would require CLI to depend on worker's full runtime deps (`@anthropic-ai/claude-agent-sdk`, `zx`, etc.), breaking the "CLI is Docker orchestration only" design and bloating the npm package.
- **Separate binary**: Unnecessary complexity for a single entry point.

### D2: New `local-start.ts` instead of modifying `start.ts`

**Choice:** Create `apps/cli/src/commands/local-start.ts` as a standalone command file. Do NOT modify `start.ts`.

This is the key upstream-compatibility decision. `start.ts` handles Docker image management, Temporal infra, workspace creation, container spawning, and polling — all tightly coupled to the Docker+Temporal path. Modifying it to also handle bare-metal execution would create entangled logic and high merge-conflict risk.

Instead, `local-start.ts` is a clean, self-contained file that only handles the local whitebox path:

```
index.ts (only change to an official file):
  ─────────────────────────────────────────
  + import { localStart } from './commands/local-start.js';
    ...
    case 'start': {
      const parsed = parseStartArgs(args.slice(1));
  +   if (parsed.whiteboxOnly && isLocal()) {
  +     await localStart({ ...parsed, version: getVersion() });
  +   } else {
        await start({ ...parsed, version: getVersion() });
  +   }
      break;
    }
```

This means:
- `start.ts`: **zero changes** — no conflict with upstream
- `index.ts`: 1 import + 3-line `if/else` — the `switch/case` dispatch is very stable, low conflict risk
- `local-start.ts`: entirely new file — zero conflict risk
- `--concurrency` flag: parsed inside `local-start.ts`, not in `index.ts` — no change to shared arg parsing

**Alternatives considered:**
- **Modify `start.ts`**: Would require inserting early-return branches around Docker/Temporal setup. High conflict risk with upstream changes to the most-iterated CLI file.
- **New top-level command (`./shannon scan`)**: Cleaner separation but adds another command to the help text and user mental model. The `--whitebox-only` flag already exists; reusing it is more intuitive.

### D3: `ConsoleActivityLogger` replaces `TemporalActivityLogger`

**Choice:** New `console-logger.ts` implementing the existing `ActivityLogger` interface.

The interface is 3 methods (`info`, `warn`, `error`) with no Temporal dependencies in the type. Implementation just maps to `console.log/warn/error` with structured formatting.

```typescript
export class ConsoleActivityLogger implements ActivityLogger {
  info(message: string, attrs?: Record<string, unknown>): void {
    console.log(`[INFO] ${message}`, attrs ?? '')
  }
  warn(message: string, attrs?: Record<string, unknown>): void {
    console.warn(`[WARN] ${message}`, attrs ?? '')
  }
  error(message: string, attrs?: Record<string, unknown>): void {
    console.error(`[ERROR] ${message}`, attrs ?? '')
  }
}
```

### D4: Bounded parallelism with default maxConcurrency=3

**Choice:** Runner executes up to 3 vuln agents concurrently.

Claude Agent SDK spawns independent Claude Code subprocesses. Running 5 simultaneously risks API rate limits, memory pressure, and disk I/O contention on bare metal. Temporal path has Docker container resource caps as implicit throttling.

Configurable via `--concurrency` flag parsed by `local-start.ts`. The runner uses a simple semaphore pattern:

```typescript
async function runParallel(agents, maxConcurrency) {
  const semaphore = new Semaphore(maxConcurrency)
  return Promise.allSettled(
    agents.map(agent => semaphore.with(() => executeAgent(agent)))
  )
}
```

### D5: Runner implements own retry (3 attempts, exponential backoff)

**Choice:** 3 attempts with `min(30s * 2^(attempt-1), 300s)` delay.

Matches Temporal's retry behavior. Only retries on `retryable: true` errors from `PentestError` (same classification that Temporal activities use).

### D6: npx whitebox runs in Docker container without Temporal

**Choice:** npx whitebox still uses Docker but skips Temporal infrastructure.

The Docker image already contains everything needed. The container runs `node apps/worker/dist/local/runner.js` instead of `node apps/worker/dist/temporal/worker.js`. This means:
- No `docker-compose.yml` / Temporal server needed
- No `shannon-net` network needed
- Container is ephemeral and self-contained

This is handled in `local-start.ts` (for npx path), not in `start.ts`.

### D7: Reuse workspace directory structure for audit

**Choice:** Local runner writes to same `workspaces/{name}/` structure.

This ensures `./shannon logs <workspace>` and `./shannon workspaces` continue to work. The runner creates `session.json` and audit logs using the same `AuditSession` class, which is pure file I/O with no Temporal dependency.

## Upstream Merge Impact Matrix

| Official File | Changed? | Lines Changed | Conflict Risk | Reason |
|---|---|---|---|---|
| `apps/cli/src/index.ts` | Yes | ~4 lines (import + if/else) | **Low** | `switch/case` dispatch very stable |
| `apps/cli/src/commands/start.ts` | **No** | 0 | **None** | Untouched |
| `apps/worker/src/temporal/*` | **No** | 0 | **None** | Untouched |
| `apps/worker/src/services/*` | **No** | 0 | **None** | Untouched |
| `apps/worker/src/ai/*` | **No** | 0 | **None** | Untouched |
| `apps/worker/src/audit/*` | **No** | 0 | **None** | Untouched |
| `apps/worker/prompts/*` | **No** | 0 | **None** | Untouched |
| `docker-compose.yml` | **No** | 0 | **None** | Untouched |
| `Dockerfile` | **No** | 0 | **None** | Untouched |

All new code is in new files: `local-start.ts`, `local/runner.ts`, `local/console-logger.ts`, `local/semaphore.ts`. Zero conflict risk.

## Risks / Trade-offs

**[Claude Agent SDK bare-metal compatibility]** → The SDK's `query()` launches a Claude Code subprocess. It works with just `ANTHROPIC_API_KEY` and a `cwd` — no Docker-specific code paths. Risk is low but should be validated with a spike before full implementation. Mitigation: first task is a minimal spike that runs one agent locally.

**[5 concurrent SDK processes on developer machines]** → Each Claude Code instance consumes memory and makes rapid API calls. Mitigated by maxConcurrency=3 default. Users on powerful machines can increase via config.

**[Two execution paths to maintain]** → Temporal path and local path both call the same services layer. Any service-level bug affects both. This is actually a benefit — services layer is the single source of truth. Runner and Temporal activities are thin orchestration layers.

**[Worker package must be compiled before local whitebox works]** → `fork()` requires `dist/` to exist. In local dev mode this is natural (`pnpm run build`). Mitigation: `local-start.ts` checks for `dist/` existence and prints actionable error if missing.

**[Upstream adds competing local execution]** → If official Shannon adds its own local execution path, it would likely modify `start.ts` or add a new command. Our `local-start.ts` and `index.ts` branch would need adjustment, but the runner itself (`apps/worker/src/local/*`) would remain reusable regardless of how the CLI dispatches to it.
