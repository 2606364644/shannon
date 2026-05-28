## ADDED Requirements

### Requirement: CLI tools available on PATH in local bare-metal mode
When running in local bare-metal mode (`SHANNON_LOCAL=1`), the system SHALL ensure `save-deliverable` and `generate-totp` are discoverable on PATH before forking the runner process. This SHALL be achieved by creating POSIX shell wrapper scripts in the system temp directory and prepending that directory to the PATH environment variable passed to the forked runner.

#### Scenario: Normal startup with compiled scripts available
- **WHEN** `localStartBare()` is called AND `apps/worker/dist/scripts/save-deliverable.js` exists
- **THEN** the system SHALL create wrapper scripts in a temp directory (`os.tmpdir()` + `shannon-scripts/`) for both `save-deliverable` and `generate-totp`, each delegating to the corresponding `.js` file via `exec node "<absolute-path>" "$@"`, and prepend this directory to PATH in the forked runner's environment

#### Scenario: Compiled scripts missing
- **WHEN** `localStartBare()` is called AND `apps/worker/dist/scripts/save-deliverable.js` does NOT exist
- **THEN** the system SHALL print an error message directing the user to run `pnpm run build` and exit with code 1, BEFORE forking the runner

### Requirement: Wrapper scripts are idempotent and portable
Wrapper scripts SHALL be safe to create multiple times (idempotent overwrite). The wrapper SHALL use an absolute path to the compiled `.js` file resolved at creation time. The PATH delimiter SHALL use `path.delimiter` for cross-platform correctness.

#### Scenario: Repeated localStartBare invocations
- **WHEN** `localStartBare()` is called multiple times (e.g., sequential scans)
- **THEN** wrapper scripts SHALL be overwritten with identical content each time without error, and PATH SHALL be set consistently

### Requirement: Recovery attempts before rollback deletes files
When an agent fails output validation, the system SHALL attempt to snapshot and recover deliverable files from the deliverables directory BEFORE running `git clean -fd` rollback. The recovery mechanism SHALL preserve files written by the agent's Write/Edit tools that may not have been registered with `save-deliverable`.

#### Scenario: Agent writes file via Write tool but save-deliverable fails
- **WHEN** an agent writes `pre_recon_deliverable.md` to `.shannon/deliverables/` via the Write tool, then `save-deliverable` fails, and validation fails
- **THEN** the system SHALL copy the file from `.shannon/deliverables/` to a temporary location before `git clean -fd` runs, attempt to restore it after rollback, and re-validate

#### Scenario: No file exists to recover
- **WHEN** validation fails AND no deliverable file exists in the deliverables directory or any recovery search path
- **THEN** the system SHALL proceed with rollback and retry as before, without error

### Requirement: Extended recovery file age threshold
The maximum file age for deliverable recovery (`MAX_FILE_AGE_MS`) SHALL be 60 minutes instead of 30 minutes. This accommodates long-running agents (pre-recon can exceed 15 minutes) plus retry delays.

#### Scenario: Agent runs for 35 minutes and fails validation
- **WHEN** a deliverable file was created 35 minutes ago during the current attempt
- **THEN** the recovery mechanism SHALL still consider the file fresh and attempt recovery
