## Context

Shannon's agent pipeline uses a CLI tool called `save-deliverable` as the final step in every agent's workflow. All 13 agent prompts (96+ references total) instruct the Claude agent to run `save-deliverable --type <TYPE> --file-path <path>` after writing their deliverable file. The tool normalizes the file path via `SHANNON_DELIVERABLES_SUBDIR` and writes to the canonical location under `.shannon/deliverables/`.

Today this tool only works inside Docker containers. The `Dockerfile` (line 101-104) compiles the TypeScript source and symlinks the `.js` output to `/usr/local/bin/save-deliverable`. In local bare-metal mode (`SHANNON_LOCAL=1`), `localStartBare()` in `local-start.ts` forks the runner directly — the compiled `apps/worker/dist/scripts/save-deliverable.js` exists but is not on PATH, so every agent's `save-deliverable` call fails with "command not found".

The failure cascade is severe: agents panic, overwrite correctly-placed files, and the validation failure triggers `failAgent()` which runs `git clean -fd` on the deliverables repo, deleting all untracked deliverables before the recovery mechanism can find them. Each retry wastes ~$7 and 15-20 minutes with identical results.

## Goals / Non-Goals

**Goals:**
- Make `save-deliverable` (and `generate-totp`) discoverable on PATH when running in local bare-metal mode
- Zero changes to agent prompts — all 96 references continue to work unmodified
- Zero impact on Docker mode — no behavioral change when running in a container
- Strengthen recovery as a defense-in-depth layer against file placement failures
- Pre-flight validation that CLI tools are available before starting the pipeline

**Non-Goals:**
- Removing the `save-deliverable` CLI tool or rewriting it as an in-process function
- Changing agent prompt templates or the chunked writing workflow
- Addressing WSL2 NTFS filesystem latency issues (separate concern)
- Modifying the Docker build or Dockerfile symlink strategy

## Decisions

### Decision 1: Runtime shell wrapper creation in temp directory

**Choice**: Create POSIX shell wrapper scripts in a temp directory (`/tmp/shannon-scripts/`) at local startup time, then prepend this directory to PATH before forking the runner.

**Alternatives considered**:
- *Symlink into an existing PATH directory* (e.g. `/usr/local/bin/`): Requires elevated permissions. The runner may execute as a non-root user (`shannon-user` in the user's setup).
- *chmod +x on .js files and add dist/scripts/ to PATH*: `tsc` doesn't set execute permissions; would need a post-build step. Fragile across `pnpm build` runs.
- *Modify `claude-executor.ts` to pass script path as env var*: Would require changing all 96 prompt references to use `$SHANNON_SAVE_DELIVERABLE` instead of bare `save-deliverable`.
- *Use `node --import` or `tsx`*: Heavier dependency, changes invocation semantics.

**Rationale**: Wrapper scripts are the lightest touch — one-time creation, no permissions issues, idempotent across runs. The wrapper is a 2-line shell script: `#!/bin/sh\nexec node "/absolute/path/to/save-deliverable.js" "$@"`. The absolute path is resolved once at startup.

### Decision 2: Pre-flight script existence check in localStartBare

**Choice**: Add a guard in `localStartBare()` that checks `dist/scripts/save-deliverable.js` exists, analogous to the existing `runner.js` check at line 60-63.

**Rationale**: Fail fast with a clear error message ("Run `pnpm run build` first") rather than silently proceeding into a pipeline where every agent will fail.

### Decision 3: Recovery-before-rollback ordering

**Choice**: In `agent-execution.ts` `failAgent()`, attempt deliverable recovery *before* running `git clean -fd` rollback. Capture recovered file paths, then rollback, then re-validate.

**Current flow** (broken):
```
validate → fail → rollback (git clean -fd, deletes files) → recovery (finds nothing) → retry
```

**New flow**:
```
validate → fail → snapshot deliverables dir → rollback → restore snapshots → re-validate → retry
```

**Alternative considered**: *Skip `git clean -fd` entirely on validation failures*: Too risky — rollback exists to prevent state pollution from failed agent attempts. Keeping rollback but reordering preserves its value.

**Rationale**: The current ordering assumes recovery searches locations *outside* the deliverables directory (repo root, /tmp, parent). But the most likely location for the file is *inside* deliverables (from the Write tool), and rollback deletes it. Snapshotting before rollback fixes this.

### Decision 4: Increase MAX_FILE_AGE_MS to 60 minutes

**Choice**: Increase from 30 to 60 minutes.

**Rationale**: Pre-recon agents can run for 15-20 minutes (936s in the user's case). With retries and delays, the total elapsed time before recovery can exceed 30 minutes. 60 minutes provides headroom without risking recovery of stale files from previous runs (workspace sessions are typically hours apart).

## Risks / Trade-offs

- **[Wrapper script temp dir cleanup]** → Wrappers in `/tmp/shannon-scripts/` persist across runs. Mitigation: wrappers are idempotent and tiny (<100 bytes). No cleanup needed — `/tmp` is cleared on reboot. Use `os.tmpdir()` for cross-platform correctness.

- **[Race condition on wrapper creation]** → Multiple concurrent `localStartBare()` invocations could race on wrapper creation. Mitigation: wrapper creation is idempotent (overwrite with same content). Use `writeFileSync` which is atomic for small files.

- **[Recovery snapshot overhead]** → Snapshotting files before rollback adds I/O. Mitigation: only runs on validation failure (rare path), and files are typically <1MB markdown.

- **[Cross-platform PATH separator]** → Windows uses `;`, POSIX uses `:`. Mitigation: use `path.delimiter` from Node.js `path` module. Local mode only runs on Linux/macOS (WSL2 is Linux), but defensive coding is cheap.

- **[PATH injection scope]** → Adding scripts dir to PATH affects all subprocesses of the runner, not just `save-deliverable`. Mitigation: the directory only contains the two intended CLI tools. No other files should be placed there.
