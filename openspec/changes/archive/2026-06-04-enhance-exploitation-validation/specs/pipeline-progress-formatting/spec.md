## ADDED Requirements

### Requirement: AgentOutcome dataclass captures per-agent execution result

A `AgentOutcome` dataclass SHALL be defined in `shannon_core.utils.progress` with fields: `agent_name` (str), `vuln_type` (str), `status` (one of "completed", "failed", "skipped"), `duration_s` (float, default 0.0), `cost_usd` (float, default 0.0), `turns` (int, default 0), and `error` (str, default "").

#### Scenario: Completed outcome has metrics
- **WHEN** an exploit agent completes with duration=272s, cost=0.1234, turns=12
- **THEN** an `AgentOutcome` with status="completed", duration_s=272.0, cost_usd=0.1234, turns=12 SHALL be constructed

#### Scenario: Failed outcome has error message
- **WHEN** an exploit agent raises an exception "API rate limit exceeded"
- **THEN** an `AgentOutcome` with status="failed", error="API rate limit exceeded" SHALL be constructed

#### Scenario: Skipped outcome has no metrics
- **WHEN** a vulnerability type was not scheduled for exploitation
- **THEN** an `AgentOutcome` with status="skipped", duration_s=0.0, cost_usd=0.0, turns=0 SHALL be constructed

### Requirement: format_exploit_summary produces human-readable table

The `format_exploit_summary(outcomes)` function SHALL accept a list of `AgentOutcome` objects and return a multi-line string containing a formatted table. The table SHALL include:
- A header line with completion/failure counts
- A columnar layout showing agent name, status icon, turns, duration, and cost
- Status icons: ✅ for completed, ❌ for failed, ⏭️ for skipped
- A totals line with aggregate duration, cost, and turns
- Failure detail lines appended for any failed outcomes

#### Scenario: All agents completed
- **WHEN** `format_exploit_summary` receives 3 outcomes all with status="completed"
- **THEN** the output SHALL contain a header "3/3 completed", 3 data rows with ✅ icons, and a totals line

#### Scenario: Mixed results with failures
- **WHEN** `format_exploit_summary` receives outcomes with 2 completed, 1 failed, 1 skipped
- **THEN** the output SHALL contain header "(2/4 completed, 1 failed)", ✅ for completed, ❌ for failed, ⏭️ for skipped, and a failure detail line

#### Scenario: Duration formatting handles seconds, minutes, and hours
- **WHEN** durations are 45s, 272s, and 3725s
- **THEN** they SHALL be formatted as "45s", "4m 32s", and "1h 02m" respectively

### Requirement: Blackbox workflow uses progress formatter for exploit results

After parallel exploit execution completes, the blackbox workflow SHALL construct `AgentOutcome` objects from the `asyncio.gather` results and call `format_exploit_summary` to produce a single INFO-level log entry. Skipped vulnerability types (validated but not scheduled) SHALL be included in the outcomes list.

#### Scenario: Workflow logs formatted summary after gather
- **WHEN** 6 exploit agents complete (4 succeeded, 1 failed, 1 skipped)
- **THEN** an INFO-level log entry containing the formatted table SHALL be emitted

#### Scenario: Skipped types included in summary
- **WHEN** "ssrf" was not scheduled because `queue_file_missing`
- **THEN** an `AgentOutcome` with agent_name="ssrf-exploit", status="skipped" SHALL appear in the summary
