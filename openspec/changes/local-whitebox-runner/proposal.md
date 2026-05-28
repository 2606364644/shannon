## Why

Whitebox-only mode (`--whitebox-only`) currently requires the full Docker + Temporal infrastructure, despite only needing source code analysis. This creates an unnecessarily heavy barrier: users must install Docker, start Temporal server, and pull/build images just to run static code analysis. The services layer is already Temporal-agnostic by design, making a bare-metal local execution path both feasible and high-value.

Additionally, this fork tracks the official Shannon repo and needs to maintain compatibility with upstream merges. The design therefore minimizes modifications to official files — only `apps/cli/src/index.ts` is changed (3 lines in the `start` case), while `start.ts` and all other official files remain untouched.

## What Changes

- Add a **local whitebox runner** (`apps/worker/src/local/runner.ts`) that orchestrates the whitebox pipeline directly in a Node.js process, without Docker or Temporal
- Add a **console activity logger** (`apps/worker/src/local/console-logger.ts`) to replace `TemporalActivityLogger` for bare-metal execution
- Add a **new CLI command file** (`apps/cli/src/commands/local-start.ts`) that handles whitebox-only local execution via `child_process.fork()`, leaving `start.ts` completely untouched
- Modify only `apps/cli/src/index.ts` — add 1 import line and 3-line `if` branch in the `start` case to route whitebox-only to `localStart()`; `start.ts` is **not modified**
- Runner implements its own **retry logic** (3 attempts, exponential backoff) to compensate for losing Temporal's automatic retry
- Runner uses **bounded parallelism** (default maxConcurrency=3) for vuln agents to avoid resource exhaustion on bare metal

## Capabilities

### New Capabilities
- `local-whitebox-execution`: Bare-metal execution of the whitebox pipeline — preflight, pre-recon, static recon, vulnerability analysis, and report generation via forked Node.js process, with bounded parallelism and built-in retry. Minimal invasion to official files (only `index.ts` touched).

### Modified Capabilities
<!-- No existing specs to modify -->

## Impact

- **CLI package** (`apps/cli/`): One new file (`local-start.ts`) + 4 lines changed in `index.ts`; `start.ts` is **not modified**
- **Worker package** (`apps/worker/`): New `local/` directory with runner and console logger; no changes to `services/`, `ai/`, `audit/`, or `temporal/`
- **Docker**: npx whitebox runs in container without Temporal; local whitebox requires no Docker at all
- **Dependencies**: No new package dependencies; reuses existing `@anthropic-ai/claude-agent-sdk`, `services/*`, `audit/*`
- **Backwards compatibility**: Default pipeline (full mode) and blackbox-only are completely untouched
- **Upstream merge impact**: Only `index.ts` has conflict risk; its `switch/case` dispatch structure is very stable. All other modified code is in new files (zero conflict)
