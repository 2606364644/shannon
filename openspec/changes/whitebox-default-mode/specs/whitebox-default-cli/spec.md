## ADDED Requirements

### Requirement: Auto-infer whitebox mode from URL absence
The CLI SHALL automatically set whitebox-only mode when no `-u/--url` argument is provided. The `--whitebox-only` flag SHALL remain supported as an explicit opt-in but SHALL NOT be required when no URL is given.

#### Scenario: Start command without URL
- **WHEN** user runs `shannon start -r my-repo` without `-u` flag
- **THEN** the CLI SHALL set `whiteboxOnly = true` and proceed without error, logging an info message indicating whitebox-only mode was auto-selected

#### Scenario: Start command with URL
- **WHEN** user runs `shannon start -u https://example.com -r my-repo`
- **THEN** the CLI SHALL set `whiteboxOnly = false` and run the full pipeline, unchanged from current behavior

#### Scenario: Explicit whitebox-only with URL
- **WHEN** user runs `shannon start --whitebox-only -u https://example.com -r my-repo`
- **THEN** the CLI SHALL accept the command (URL is allowed but unused for vuln analysis), consistent with current behavior

### Requirement: Worker auto-inference consistency
The Temporal worker entry point (`apps/worker/src/temporal/worker.ts`) SHALL automatically infer `whiteboxOnly = true` when `webUrl` is absent and `blackboxOnly` is not set.

#### Scenario: Worker started without webUrl and without SHANNON_WHITEBOX_ONLY
- **WHEN** the worker receives no `webUrl` positional argument and `SHANNON_WHITEBOX_ONLY` is not set
- **THEN** the worker SHALL infer `whiteboxOnly = true` and select `whiteboxPipelineWorkflow`

#### Scenario: Worker started without webUrl with SHANNON_BLACKBOX_ONLY=1
- **WHEN** the worker has `SHANNON_BLACKBOX_ONLY=1` set but no `webUrl`
- **THEN** the worker SHALL error with "webUrl is required for blackbox-only mode" (unchanged)

### Requirement: Info log on auto-inference
When whitebox mode is auto-inferred (not explicitly set via flag or env var), the system SHALL log an informational message indicating the mode was automatically selected because no target URL was provided.

#### Scenario: Auto-inference produces visible log
- **WHEN** whitebox mode is auto-inferred at the CLI layer
- **THEN** an info message SHALL be printed: "No target URL provided — running in whitebox-only (static analysis) mode"

### Requirement: Help text reflects optional URL
The CLI help text SHALL describe `--url` as optional, not required. The help SHALL indicate that omitting the URL results in whitebox-only mode.

#### Scenario: Help text displays updated usage
- **WHEN** user runs `shannon help`
- **THEN** the `-u, --url <url>` option SHALL be described as optional with a note like "omit for whitebox-only static analysis"
