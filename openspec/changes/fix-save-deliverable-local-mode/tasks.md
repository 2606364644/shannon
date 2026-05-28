## 1. CLI Tool PATH Injection

- [x] 1.1 Add pre-flight check in `localStartBare()` (`apps/cli/src/commands/local-start.ts`) that validates `apps/worker/dist/scripts/save-deliverable.js` exists; exit with clear error if missing
- [x] 1.2 Add helper function `ensureScriptWrappers()` that creates `/tmp/shannon-scripts/save-deliverable` and `/tmp/shannon-scripts/generate-totp` POSIX shell wrappers delegating to the compiled `.js` files; use `os.tmpdir()` + `shannon-scripts/` as the base directory; make idempotent (overwrite without error)
- [x] 1.3 Call `ensureScriptWrappers()` in `localStartBare()` before `fork()`, and prepend the wrappers directory to PATH in the forked env: `PATH: \`${wrappersDir}${path.delimiter}\${process.env.PATH}\``
- [ ] 1.4 Verify the wrapper approach works end-to-end: run `./shannon start --whitebox-only -r <repo>` in local mode and confirm `save-deliverable` is found by the Claude agent's Bash subprocess (check logs for `save-deliverable` success or absence of "command not found")

## 2. Recovery Hardening

- [x] 2.1 Increase `MAX_FILE_AGE_MS` from `30 * 60 * 1000` to `60 * 60 * 1000` in `apps/worker/src/services/deliverable-recovery.ts`
- [x] 2.2 Refactor `failAgent()` in `apps/worker/src/services/agent-execution.ts` to attempt deliverable snapshot/recovery BEFORE `rollbackGitWorkspace()`: copy deliverable files from `deliverablesPath` to a temp snapshot dir, then rollback, then restore snapshots to `deliverablesPath`, then re-validate
- [x] 2.3 Add the deliverables directory itself as a recovery search path in `buildSearchPaths()` so that pre-rollback snapshots are considered

## 3. Verification

- [x] 3.1 Run `pnpm biome` to ensure linting/formatting passes on all changed files
- [x] 3.2 Run `pnpm run check` to ensure TypeScript compilation passes
- [x] 3.3 Run `pnpm run build` and verify `dist/scripts/save-deliverable.js` is produced
- [ ] 3.4 Test local mode scan end-to-end with a small repo and confirm agents complete without validation retry loops
