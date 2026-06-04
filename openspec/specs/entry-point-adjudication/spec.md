## ADDED Requirements

### Requirement: CallChain construction deferred past adjudication

`build_code_index()` SHALL NOT build CallChain objects. The returned `CodeIndex.chains` SHALL be an empty list. `CodeIndex.total_chains` SHALL be 0.

#### Scenario: code_index produces no chains
- **WHEN** `build_code_index(repo_path)` is called
- **THEN** the returned `CodeIndex` has `chains=[]`, `total_chains=0`, and `blocks`, `edges`, `entry_points` populated normally

#### Scenario: code_index.json written to disk has empty chains
- **WHEN** `write_index_files(index, output_dir)` is called after `build_code_index`
- **THEN** the written `code_index.json` contains `"chains": []` and `"total_chains": 0`

### Requirement: async def catch-all noise reduction

The `_detect_python()` entry point detector SHALL apply heuristic filters before marking an `async def` as a candidate entry point. Functions matching any of the following patterns SHALL be excluded from the catch-all:
- Function name starts with `_` (private)
- File name contains `test_` or `_test` or is `conftest.py`
- Any parent directory is `tests`, `test`, or `spec`

Functions passing the filter SHALL be marked with `entry_type="unknown"`, `confidence=0.40`, `needs_llm_review=True`.

#### Scenario: async def in test file excluded
- **WHEN** an `async def` function has no known decorator AND its file path contains `test_`
- **THEN** it SHALL NOT appear in the returned entry points list

#### Scenario: private async def excluded
- **WHEN** an `async def` function has no known decorator AND its function name starts with `_`
- **THEN** it SHALL NOT appear in the returned entry points list

#### Scenario: valid async def candidate detected with higher confidence
- **WHEN** an `async def` function has no known decorator AND passes all heuristic filters
- **THEN** an `EntryPoint` with `entry_type="unknown"`, `confidence=0.40`, `needs_llm_review=True` SHALL be returned

### Requirement: PRE_RECON Phase 0 entry point adjudication

PRE_RECON SHALL execute a Phase 0 step that reads `code_index.json`, identifies all entry points with `needs_llm_review=True`, and produces a structured `entry_points.json` file containing adjudication results.

Each adjudicated entry point in `entry_points.json` SHALL have:
- `func_block_id`: the original FuncBlock ID
- `verdict`: one of `confirmed`, `rejected`, `reclassified`
- `entry_type`: the entry point type (for `reclassified`, the new type)
- `evidence`: brief justification for the verdict
- `source`: `code_index` (deterministic) or `llm_discovery` (LLM supplement)

High-confidence entry points (confidence >= 0.8) SHALL be automatically included with `verdict=confirmed` and `source=code_index`.

#### Scenario: high-confidence entry points auto-confirmed
- **WHEN** an entry point has `confidence >= 0.8` and `needs_llm_review=False`
- **THEN** it SHALL appear in `entry_points.json` with `verdict=confirmed`, `source=code_index`

#### Scenario: low-confidence entry point adjudicated
- **WHEN** an entry point has `needs_llm_review=True`
- **THEN** PRE_RECON SHALL read the surrounding source code context and produce a verdict of `confirmed`, `rejected`, or `reclassified`

#### Scenario: LLM discovers entry point not in code_index
- **WHEN** PRE_RECON discovers an entry point through Phase 1 supplementary discovery that is not in `code_index.json`
- **THEN** the discovered entry point SHALL be appended to `entry_points.json` with `source=llm_discovery`

### Requirement: PRE_RECON Entry Point Mapper supplementary role

The Entry Point Mapper Task Agent prompt SHALL focus on discovering entry points that the deterministic code_index may have missed, rather than rediscovering all entry points. This includes configuration-file routes, dynamic registration patterns, and unknown framework conventions.

#### Scenario: Entry Point Mapper finds config-file route
- **WHEN** the target project defines routes in a configuration file (e.g., `urls.py`, `routes.yaml`)
- **THEN** the Entry Point Mapper SHALL report these routes as supplementary discoveries

#### Scenario: Entry Point Mapper does not duplicate code_index findings
- **WHEN** the Entry Point Mapper encounters an entry point already detected by code_index
- **THEN** it SHALL NOT report it as a new finding

### Requirement: PRE_RECON starting context unified

The `<starting_context>` section of `pre-recon-code.txt` SHALL contain a single coherent description of the relationship between code_index and PRE_RECON, replacing the current contradictory instructions. It SHALL state:
- code_index has extracted candidate entry points with confidence levels
- PRE_RECON MUST adjudicate low-confidence candidates in Phase 0
- PRE_RECON MUST supplement with discoveries code_index could not make
- The call graph (CallEdge[]) is available for analysis but CallChains will be built after adjudication

#### Scenario: no contradictory instructions in prompt
- **WHEN** `pre-recon-code.txt` is read
- **THEN** there SHALL NOT be any instruction simultaneously telling the agent to both "not discover entry points" and "find all entry points"

### Requirement: Rebuild CallChains deterministic activity

A deterministic activity `rebuild_call_chains` SHALL run after PRE_RECON completes within the same `pre-recon` phase. It SHALL read `entry_points.json` to get the confirmed entry point IDs, read `code_index.json` for FuncBlocks and CallEdges, call `build_call_chains()` with only confirmed entry point IDs, and write the updated `code_index.json` with populated chains.

#### Scenario: rebuild from confirmed entry points only
- **WHEN** `entry_points.json` contains 5 confirmed and 3 rejected entry points
- **THEN** `rebuild_call_chains` SHALL call `build_call_chains()` with exactly the 5 confirmed entry point IDs

#### Scenario: LLM-discovered entry points included in chains
- **WHEN** `entry_points.json` contains an entry with `source=llm_discovery` and a `func_block_id` matching a known FuncBlock
- **THEN** that entry point SHALL be included in the chain-building input

#### Scenario: LLM-discovered entry point with no matching FuncBlock
- **WHEN** `entry_points.json` contains an entry with `source=llm_discovery` whose `func_block_id` does not match any known FuncBlock
- **THEN** that entry point SHALL be logged as unresolved and excluded from chain building

### Requirement: Pipeline workflow updated for rebuild step

The whitebox scan workflow SHALL call `rebuild_call_chains` activity after the PRE_RECON agent activity completes, within the same `pre-recon` phase. The activity SHALL be classified under the `pre-recon` agent for logging and state tracking.

#### Scenario: rebuild runs after PRE_RECON in same phase
- **WHEN** the whitebox scan workflow executes the pre-recon phase
- **THEN** the sequence SHALL be: `run_agent(pre-recon)` → `rebuild_call_chains` → mark pre-recon complete

#### Scenario: rebuild skipped on resume when pre-recon already complete
- **WHEN** the workflow resumes and `pre-recon` is in `completedAgents`
- **THEN** `rebuild_call_chains` SHALL NOT execute

### Requirement: entry_points.json file format

`entry_points.json` SHALL be a JSON file written to `.shannon/deliverables/` with the following schema:

```json
{
  "repository": "string",
  "language": "string",
  "adjudicated_entry_points": [
    {
      "func_block_id": "string",
      "verdict": "confirmed | rejected | reclassified",
      "entry_type": "string",
      "route": "string | null",
      "http_method": "string | null",
      "evidence": "string",
      "source": "code_index | llm_discovery"
    }
  ]
}
```

#### Scenario: file written to deliverables directory
- **WHEN** PRE_RECON Phase 0 completes
- **THEN** `entry_points.json` SHALL exist in `.shannon/deliverables/`

#### Scenario: rejected entry points included for audit trail
- **WHEN** an entry point is adjudicated as `rejected`
- **THEN** it SHALL still appear in `entry_points.json` with `verdict=rejected` for auditability

### Requirement: Exploitation validation result includes human-readable message

`QueueValidationResult` SHALL include a `message` field of type `str` providing a human-readable description of the validation outcome. For failure cases, the message SHALL include the specific file name and a description of what went wrong. For success cases, the message SHALL include the vulnerability count.

#### Scenario: JSON parse failure message includes file name and error location
- **WHEN** `validate_queue("xss", deliverables_path)` encounters a JSON decode error at line 42
- **THEN** the returned `QueueValidationResult.message` SHALL contain both the queue file name (`xss_exploitation_queue.json`) and the parse error detail (line number)

#### Scenario: Success message includes vulnerability count
- **WHEN** `validate_queue("injection", deliverables_path)` finds 3 vulnerabilities
- **THEN** the returned `QueueValidationResult.message` SHALL contain the string "3 injection vulnerability(ies) queued for exploitation"

### Requirement: Exploitation validation result includes structured context

`QueueValidationResult` SHALL include a `context` field of type `dict` containing structured data for log aggregation and debugging. The context dict SHALL always include `vuln_type` and `queue_path` keys. Additional keys SHALL be included based on the failure type.

#### Scenario: JSON parse error context includes parse detail
- **WHEN** `validate_queue("xss", deliverables_path)` encounters a JSON decode error
- **THEN** the returned `QueueValidationResult.context` SHALL contain keys `vuln_type`, `queue_path`, and `parse_error` with the specific error message

#### Scenario: Invalid structure context includes actual type and top keys
- **WHEN** `validate_queue("auth", deliverables_path)` finds `vulnerabilities` is a dict instead of a list
- **THEN** the returned `QueueValidationResult.context` SHALL contain keys `actual_type` (value: "dict") and `top_keys` (list of the JSON object's top-level keys)

#### Scenario: Deliverable missing context includes both paths
- **WHEN** `validate_queue("ssrf", deliverables_path)` finds the queue exists but the deliverable does not
- **THEN** the returned `QueueValidationResult.context` SHALL contain both `queue_path` and `deliverable_path`

### Requirement: Exploitation validation distinguishes expected vs anomalous failures

`QueueValidationResult` SHALL expose an `is_expected` property that returns `True` when the failure represents a normal pipeline outcome (not an error). The reasons `queue_file_missing` and `empty_vulnerabilities` SHALL be classified as expected. All other failure reasons SHALL be classified as anomalous.

#### Scenario: Queue file missing is expected
- **WHEN** `validate_queue("xss", deliverables_path)` returns `reason="queue_file_missing"`
- **THEN** `result.is_expected` SHALL be `True`

#### Scenario: Empty vulnerabilities is expected
- **WHEN** `validate_queue("auth", deliverables_path)` returns `reason="empty_vulnerabilities"`
- **THEN** `result.is_expected` SHALL be `True`

#### Scenario: JSON parse error is anomalous
- **WHEN** `validate_queue("xss", deliverables_path)` returns `reason="json_parse_error"`
- **THEN** `result.is_expected` SHALL be `False`

#### Scenario: Deliverable missing is anomalous
- **WHEN** `validate_queue("ssrf", deliverables_path)` returns `reason="deliverable_missing"`
- **THEN** `result.is_expected` SHALL be `False`

### Requirement: Exploitation retryable classification reflects failure semantics

`validate_queue()` SHALL set `retryable=True` for failure reasons that represent transient issues resolvable by retrying: `json_parse_error`, `invalid_vulnerabilities_array`, and `deliverable_missing`. It SHALL set `retryable=False` for failure reasons that represent permanent outcomes: `queue_file_missing` and `empty_vulnerabilities`.

#### Scenario: JSON parse error is retryable
- **WHEN** `validate_queue("xss", deliverables_path)` encounters malformed JSON
- **THEN** the returned `QueueValidationResult.retryable` SHALL be `True`

#### Scenario: Queue file missing is not retryable
- **WHEN** `validate_queue("auth", deliverables_path)` finds no queue file
- **THEN** the returned `QueueValidationResult.retryable` SHALL be `False`

#### Scenario: Invalid vulnerabilities array is retryable
- **WHEN** `validate_queue("injection", deliverables_path)` finds `vulnerabilities` is not a list
- **THEN** the returned `QueueValidationResult.retryable` SHALL be `True`

### Requirement: should_exploit logs decision reason

`ExploitationChecker.should_exploit()` SHALL emit a log entry for every `False` return value. Expected failures SHALL be logged at `DEBUG` level. Anomalous failures SHALL be logged at `WARNING` level with the `context` from the validation result.

#### Scenario: Expected skip logged at DEBUG
- **WHEN** `should_exploit(deliverables_path, "xss")` returns `False` because `reason="empty_vulnerabilities"`
- **THEN** a DEBUG-level log entry SHALL be emitted containing the human-readable message

#### Scenario: Anomalous failure logged at WARNING with context
- **WHEN** `should_exploit(deliverables_path, "xss")` returns `False` because `reason="json_parse_error"`
- **THEN** a WARNING-level log entry SHALL be emitted containing both the message and the `queue_path` from context

### Requirement: Workflow logs validation summary for all vulnerability types

The blackbox workflow SHALL collect validation results for all selected vulnerability types and produce a single summary log entry before scheduling exploit agents. The summary SHALL use emoji icons (✅ scheduled, ⚠️ anomalous skip, ⏭️ expected skip) and include the human-readable message for each type.

#### Scenario: Mixed validation outcomes produce formatted summary
- **WHEN** validation results are: injection=valid(3 vulns), xss=json_parse_error, auth=empty_vulnerabilities, ssrf=queue_file_missing, authz=valid(1 vuln)
- **THEN** a single INFO-level log entry SHALL contain lines for all 5 types with appropriate icons and messages
