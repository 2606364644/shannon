## 1. Spike: Validate Claude Agent SDK bare-metal execution

- [x] 1.1 Create minimal `apps/worker/src/local/spike.ts` that imports `query` from `@anthropic-ai/claude-agent-sdk` and runs a single prompt against a local repo with `cwd` set to the repo path, confirming it works without Docker
- [x] 1.2 Verify the spike produces output, handles errors, and respects `ANTHROPIC_API_KEY` env var

## 2. Console logger and utilities

- [x] 2.1 Create `apps/worker/src/local/console-logger.ts` implementing `ActivityLogger` interface with `[INFO]`/`[WARN]`/`[ERROR]` prefixed console output
- [x] 2.2 Create `apps/worker/src/local/semaphore.ts` — a simple bounded concurrency primitive (counting semaphore) for parallel agent execution

## 3. Local runner core

- [x] 3.1 Create `apps/worker/src/local/runner.ts` with CLI argument parsing (repoPath, configPath, workspace, concurrency, session metadata)
- [x] 3.2 Implement pipeline orchestration: preflight (lite), initDeliverableGit, syncCodePathDenyRules, pre-recon agent, recon-static agent
- [x] 3.3 Implement bounded-parallel vuln agent execution using the semaphore (injection, auth, authz, ssrf, misconfig)
- [x] 3.4 Implement findings rendering and report assembly (reuse `findings-renderer.ts` and `reporting.ts`)
- [x] 3.5 Implement retry logic: 3 attempts, exponential backoff (30s/60s/120s cap 300s), only retry on `retryable === true`
- [x] 3.6 Implement session/audit initialization using existing `AuditSession` class, writing to `./workspaces/<name>/`
- [x] 3.7 Implement final summary output (duration, cost, agent status, deliverables path)
- [x] 3.8 Implement graceful shutdown on SIGINT/SIGTERM (stop running agents, write partial results)

## 4. CLI local-start command (new file, start.ts untouched)

- [x] 4.1 Create `apps/cli/src/commands/local-start.ts` — self-contained command for local whitebox execution: validate environment (dist/ exists, API key set), resolve repo path, create workspace, fork runner, pipe stdio, handle exit codes
- [x] 4.2 Implement `--concurrency` flag parsing inside `local-start.ts` (not in `index.ts` — keeps arg parsing untouched)
- [x] 4.3 Implement npx whitebox path in `local-start.ts`: call `ensureImage()` but skip `ensureInfra()`, spawn container running `node apps/worker/dist/local/runner.js` instead of Temporal worker

## 5. Minimal index.ts dispatch change

- [x] 5.1 Add `import { localStart } from './commands/local-start.js'` to `apps/cli/src/index.ts`
- [x] 5.2 Add 3-line `if/else` in `case 'start'`: if `whiteboxOnly && isLocal()` → `localStart()`, else → `start()` (unchanged)

## 6. Integration verification

- [x] 6.1 Run `pnpm run build` and verify worker compiles including new `local/` directory
- [x] 6.2 Run `pnpm biome` and fix any lint/format issues in new files
- [x] 6.3 Run `pnpm run check` and verify type-checking passes across all packages
- [x] 6.4 Verify `apps/cli/src/commands/start.ts` is byte-identical to upstream (no accidental edits)
- [ ] 6.5 Execute local whitebox scan against a test repo and verify deliverables match Temporal whitebox output structure
- [ ] 6.6 Verify `./shannon logs <workspace>` works for local whitebox runs
- [ ] 6.7 Verify default mode (`./shannon start -u <url> -r <repo>`) is completely unaffected
