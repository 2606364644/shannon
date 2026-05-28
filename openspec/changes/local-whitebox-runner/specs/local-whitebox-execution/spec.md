## ADDED Requirements

### Requirement: Local whitebox execution without Docker or Temporal
The system SHALL execute the whitebox pipeline (pre-recon, static recon, vulnerability analysis, findings rendering, report) directly in a Node.js process when `--whitebox-only` is specified in local mode, without requiring Docker, Temporal server, or any container infrastructure.

#### Scenario: Bare-metal whitebox scan
- **WHEN** user runs `./shannon start -r ./my-repo --whitebox-only` in local mode (`SHANNON_LOCAL=1`)
- **THEN** the CLI SHALL fork a Node.js child process running the local runner
- **AND** no Docker containers SHALL be created
- **AND** no Temporal server connection SHALL be attempted

#### Scenario: Missing dist directory
- **WHEN** user runs whitebox-only in local mode and `apps/worker/dist/` does not exist
- **THEN** `local-start.ts` SHALL print an actionable error instructing the user to run `pnpm run build`
- **AND** SHALL exit with code 1

### Requirement: Console-based activity logging
The system SHALL provide a `ConsoleActivityLogger` that implements the `ActivityLogger` interface, routing all log output to stdout/stderr instead of Temporal's Context.current().log.

#### Scenario: Agent produces info log
- **WHEN** a service calls `logger.info("Agent completed", { duration: 120 })`
- **THEN** the console logger SHALL output `[INFO] Agent completed { duration: 120 }` to stdout

#### Scenario: Agent produces error log
- **WHEN** a service calls `logger.error("Validation failed")`
- **THEN** the console logger SHALL output `[ERROR] Validation failed` to stderr

### Requirement: Bounded parallelism for vulnerability agents
The system SHALL execute vulnerability analysis agents with configurable concurrency, defaulting to 3 simultaneous agents.

#### Scenario: Five vuln agents with default concurrency
- **WHEN** the runner executes 5 vulnerability agents (injection, auth, authz, ssrf, misconfig) with default settings
- **THEN** no more than 3 agents SHALL run simultaneously
- **AND** remaining agents SHALL start as earlier ones complete

#### Scenario: Custom concurrency via CLI flag
- **WHEN** user runs `./shannon start -r ./repo --whitebox-only --concurrency 5`
- **THEN** all 5 vulnerability agents SHALL run simultaneously

#### Scenario: Sequential execution
- **WHEN** user runs `./shannon start -r ./repo --whitebox-only --concurrency 1`
- **THEN** vulnerability agents SHALL execute one at a time in order

### Requirement: Built-in retry with exponential backoff
The system SHALL retry failed agents up to 3 times with exponential backoff (30s, 60s, 120s, capped at 300s), matching Temporal's retry semantics.

#### Scenario: Retryable agent failure
- **WHEN** an agent fails with a `PentestError` where `retryable === true`
- **THEN** the runner SHALL retry up to 3 times with exponential backoff
- **AND** SHALL log each retry attempt with the reason and delay

#### Scenario: Non-retryable agent failure
- **WHEN** an agent fails with a `PentestError` where `retryable === false`
- **THEN** the runner SHALL NOT retry
- **AND** SHALL mark the agent as failed and proceed with remaining agents

#### Scenario: All retries exhausted
- **WHEN** an agent fails 3 consecutive times
- **THEN** the runner SHALL mark the agent as failed
- **AND** SHALL continue executing remaining agents
- **AND** SHALL report the failure in the final summary

### Requirement: Feature parity with Temporal whitebox pipeline
The local runner SHALL produce identical deliverables to the Temporal `whiteboxPipelineWorkflow` — same agent sequence, same prompt templates, same output files.

#### Scenario: Deliverable structure matches Temporal run
- **WHEN** a local whitebox scan completes successfully
- **THEN** the `.shannon/deliverables/` directory SHALL contain the same file set as a Temporal whitebox run: `pre_recon_deliverable.md`, `recon_deliverable.md`, `*_analysis_deliverable.md` (x5), `*_exploitation_queue.json` (x5), `comprehensive_security_assessment_report.md`

#### Scenario: Static recon prompt is used
- **WHEN** the runner executes the recon agent
- **THEN** it SHALL use the `recon-static` prompt template (via `promptOverride`)

#### Scenario: No XSS or exploit agents run
- **WHEN** the runner executes the whitebox pipeline
- **THEN** no XSS vulnerability agent SHALL run
- **AND** no exploit agents SHALL run
- **AND** findings SHALL be rendered via `findings-renderer.ts`

### Requirement: Workspace and audit compatibility
The local runner SHALL use the same workspace directory structure and audit session format as Temporal-based runs.

#### Scenario: Workspace created
- **WHEN** a local whitebox scan starts
- **THEN** a workspace directory SHALL be created under `./workspaces/<name>/`
- **AND** `session.json` SHALL be written with session metadata

#### Scenario: Logs command works
- **WHEN** user runs `./shannon logs <workspace>` after a local whitebox scan
- **THEN** the command SHALL display the workflow log from the workspace directory

### Requirement: npx whitebox runs in Docker without Temporal
The system SHALL support whitebox-only mode in npx mode by running the local runner inside the Docker container, without starting Temporal infrastructure.

#### Scenario: npx whitebox execution
- **WHEN** user runs `npx @keygraph/shannon start -r ./repo --whitebox-only` (npx mode, `SHANNON_LOCAL` not set)
- **THEN** the CLI SHALL spawn a Docker container
- **AND** the container SHALL run `node apps/worker/dist/local/runner.js` instead of the Temporal worker
- **AND** no Temporal server SHALL be started
- **AND** no `docker-compose.yml` services SHALL be launched

### Requirement: Minimal upstream merge impact
The implementation SHALL modify only `apps/cli/src/index.ts` among official files, keeping `start.ts` and all other official files untouched. New functionality SHALL live entirely in new files.

#### Scenario: start.ts is not modified
- **WHEN** the implementation is complete
- **THEN** `apps/cli/src/commands/start.ts` SHALL be byte-identical to the upstream version
- **AND** all files under `apps/worker/src/temporal/`, `apps/worker/src/services/`, `apps/worker/src/ai/`, `apps/worker/src/audit/`, `apps/worker/prompts/` SHALL be unchanged

#### Scenario: index.ts change is minimal and stable
- **WHEN** the implementation is complete
- **THEN** `apps/cli/src/index.ts` SHALL differ from upstream only by: 1 new import line + 3-line `if/else` in the `start` case
- **AND** the change SHALL NOT alter argument parsing, help text, or any other command dispatch

#### Scenario: Local mode whitebox dispatches to local-start.ts
- **WHEN** `SHANNON_LOCAL=1` and `--whitebox-only` is specified without `--url`
- **THEN** `index.ts` SHALL call `localStart()` from `./commands/local-start.js`
- **AND** SHALL NOT call `start()` from `./commands/start.js`

#### Scenario: Non-whitebox modes use original path
- **WHEN** `--whitebox-only` is NOT specified, or npx mode is active
- **THEN** `index.ts` SHALL call `start()` exactly as before with no behavioral change
