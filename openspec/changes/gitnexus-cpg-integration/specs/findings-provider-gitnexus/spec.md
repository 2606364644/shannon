## ADDED Requirements

### Requirement: Read GitNexus findings from .shannon-cpg directory
The provider SHALL read JSON files from `{repoPath}/.shannon-cpg/` when the `mergeFindingsIntoQueue()` method is called. For a given vuln type, it SHALL look for `{vuln_type}_findings.json`.

#### Scenario: Findings file exists
- **WHEN** `mergeFindingsIntoQueue()` is called with vulnType "injection" and `.shannon-cpg/injection_findings.json` exists
- **THEN** the provider reads and parses the JSON file

#### Scenario: Findings file does not exist
- **WHEN** `mergeFindingsIntoQueue()` is called with vulnType "injection" and `.shannon-cpg/injection_findings.json` does not exist
- **THEN** the provider returns `{ mergedCount: 0 }` without error

#### Scenario: Findings directory does not exist
- **WHEN** `.shannon-cpg/` directory does not exist in the repo
- **THEN** the provider returns `{ mergedCount: 0 }` without error

### Requirement: Merge findings into existing exploitation queue
The provider SHALL merge GitNexus findings into the corresponding `{vuln_type}_exploitation_queue.json` in the deliverables directory. If the queue file already exists (from a vuln agent), GitNexus entries SHALL be appended to the existing `vulnerabilities` array. If the queue file does not exist, it SHALL be created with the GitNexus findings.

#### Scenario: Queue file exists with agent findings
- **WHEN** `injection_exploitation_queue.json` already contains 3 agent-generated entries
- **THEN** GitNexus findings are appended, resulting in agent entries followed by CPG entries, and `mergedCount` reflects the number of appended entries

#### Scenario: Queue file does not exist
- **WHEN** `injection_exploitation_queue.json` does not exist in the deliverables directory
- **THEN** a new queue file is created with the GitNexus findings as the sole entries, and `mergedCount` reflects the total

#### Scenario: No GitNexus findings for this vuln type
- **WHEN** the findings file exists but contains `{ "vulnerabilities": [] }`
- **THEN** no changes are made to the exploitation queue and `mergedCount` is 0

### Requirement: ID deduplication
The provider SHALL skip merging any GitNexus finding whose `ID` already exists in the exploitation queue to prevent duplicates on resume.

#### Scenario: Duplicate ID found
- **WHEN** the exploitation queue already contains an entry with ID "INJ-CPG-001"
- **THEN** that entry is skipped during merge and `mergedCount` does not include it

### Requirement: DI container wiring
The provider SHALL be registered via `setContainerFactory()` in the worker entry point (`apps/worker/src/temporal/worker.ts`), passing a `GitNexusFindingsProvider` instance as the `findingsProvider` dependency.

#### Scenario: Worker starts with GitNexus provider
- **WHEN** the Temporal worker starts
- **THEN** `getOrCreateContainer()` returns containers with `findingsProvider` being a `GitNexusFindingsProvider` instance (not the default `NoOpFindingsProvider`)

#### Scenario: GitNexus provider is used during pipeline
- **WHEN** the pipeline calls `mergeFindingsIntoQueue()` activity
- **THEN** the call delegates to `GitNexusFindingsProvider.mergeFindingsIntoQueue()` which reads from `.shannon-cpg/`
