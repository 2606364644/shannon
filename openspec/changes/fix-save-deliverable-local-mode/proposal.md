## Why

When running Shannon in local mode (`SHANNON_LOCAL=1`, `./shannon start --whitebox-only`), every agent fails validation and retries up to 3 times — wasting ~$7 and 15-20 minutes per retry. The root cause: `save-deliverable` CLI tool only exists inside the Docker image (symlinked to `/usr/local/bin/`), but in local mode the compiled script sits in `apps/worker/dist/scripts/` with no PATH entry pointing to it. Every agent prompt (96 references across all agents) instructs the Claude agent to run `save-deliverable` as the final delivery step. When the command fails, agents panic — overwriting correctly-placed files, writing to wrong directories, and ultimately triggering `git clean -fd` on the deliverables repo which deletes everything. Recovery also fails because the files were cleaned before recovery runs.

## What Changes

- **Inject `save-deliverable` into PATH for local mode**: `localStartBare()` in `local-start.ts` will ensure `apps/worker/dist/scripts/` is on PATH before forking the runner process, so Claude Agent SDK's Bash subprocesses can find `save-deliverable` and `generate-totp`.
- **Create shell wrappers at runtime**: Since `tsc` output lacks execute permissions, create POSIX shell wrappers in a user-writable temp directory that delegate to the compiled `.js` files.
- **Strengthen deliverable recovery**: Move recovery attempt before `git clean -fd` rollback, increase `MAX_FILE_AGE_MS` from 30 to 60 minutes, and add the deliverables directory itself as a recovery search path (pre-recovery snapshot).
- **Pre-flight check for scripts**: Add a guard in `localStartBare()` that validates `save-deliverable.js` exists before proceeding, similar to the existing `runner.js` check.

## Capabilities

### New Capabilities

- `cli-tool-path-injection`: Ensures CLI tools (`save-deliverable`, `generate-totp`) compiled under `apps/worker/dist/scripts/` are discoverable on PATH when running in local bare-metal mode, via runtime shell wrapper creation and PATH injection into the forked runner process.

### Modified Capabilities

## Impact

- **`apps/cli/src/commands/local-start.ts`**: PATH injection and wrapper creation in `localStartBare()`.
- **`apps/worker/src/services/deliverable-recovery.ts`**: Increased file age threshold, additional search paths.
- **`apps/worker/src/services/agent-execution.ts`**: Recovery-before-rollback ordering in `failAgent()`.
- **`apps/worker/src/ai/claude-executor.ts`**: No changes needed (PATH passthrough already works via `passthroughVars`).
- **Docker mode**: No impact — Dockerfile already handles this via symlink to `/usr/local/bin/`.
- **Prompts**: No changes — all 96 `save-deliverable` references continue to work as-is.
