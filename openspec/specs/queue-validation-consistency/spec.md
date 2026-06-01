## ADDED Requirements

### Requirement: Blackbox-only validation uses shared queue validation logic

The `validateDeliverablesExist` activity SHALL use `validateQueueSafe` from `queue-validation.ts` to parse and validate `*_exploitation_queue.json` files, instead of custom inline JSON parsing.

#### Scenario: Queue file with object format
- **WHEN** a `*_exploitation_queue.json` contains `{"vulnerabilities": [{...}]}`
- **THEN** `validateDeliverablesExist` SHALL include the corresponding `VulnType` in its result

#### Scenario: Queue file with empty vulnerabilities array
- **WHEN** a `*_exploitation_queue.json` contains `{"vulnerabilities": []}`
- **THEN** `validateDeliverablesExist` SHALL NOT include the corresponding `VulnType` in its result

#### Scenario: Missing deliverable file for a vuln type
- **WHEN** a vuln type has a queue file but no matching deliverable file
- **THEN** `validateQueueSafe` SHALL return an error and `validateDeliverablesExist` SHALL skip that vuln type
