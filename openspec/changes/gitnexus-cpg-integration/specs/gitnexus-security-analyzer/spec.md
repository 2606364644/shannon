## ADDED Requirements

### Requirement: CLI entry point with repo and output configuration
The analyzer SHALL accept a target repository path via `--repo` argument and output directory via `--output` argument (defaulting to `{repo}/.shannon-cpg/`). The analyzer SHALL exit with code 0 on success and non-zero on failure.

#### Scenario: Basic invocation
- **WHEN** `python -m gitnexus_security_analyzer --repo /path/to/repo` is executed
- **THEN** findings JSON files are written to `/path/to/repo/.shannon-cpg/` and process exits with code 0

#### Scenario: Custom output directory
- **WHEN** `python -m gitnexus_security_analyzer --repo /path/to/repo --output /tmp/findings` is executed
- **THEN** findings JSON files are written to `/tmp/findings/` and process exits with code 0

#### Scenario: Missing repo path
- **WHEN** `python -m gitnexus_security_analyzer` is executed without `--repo`
- **THEN** process exits with non-zero code and prints usage to stderr

### Requirement: GitNexus index prerequisite
The analyzer SHALL verify that a GitNexus index exists for the target repository (`.gitnexus/` directory present). If no index exists, the analyzer SHALL run `gitnexus analyze` automatically before querying. If indexing fails, the analyzer SHALL exit with a non-zero code and descriptive error.

#### Scenario: Index already exists
- **WHEN** the target repo contains `.gitnexus/` directory
- **THEN** the analyzer proceeds directly to Cypher queries without re-indexing

#### Scenario: Index missing, auto-index succeeds
- **WHEN** the target repo does not contain `.gitnexus/` directory
- **THEN** the analyzer runs `gitnexus analyze` and proceeds to Cypher queries

#### Scenario: Indexing fails
- **WHEN** `gitnexus analyze` fails (non-zero exit)
- **THEN** the analyzer exits with non-zero code and prints the indexing error to stderr

### Requirement: Per-vuln-type Cypher query execution
The analyzer SHALL execute one Cypher query per Shannon vuln type (injection, xss, auth, ssrf, authz). Each query SHALL match structural security patterns in the GitNexus knowledge graph and return a list of finding entries.

#### Scenario: All five vuln types queried
- **WHEN** the analyzer runs against a repo with a valid GitNexus index
- **THEN** Cypher queries are executed for injection, xss, auth, ssrf, and authz patterns

#### Scenario: A vuln type finds no matches
- **WHEN** the Cypher query for a vuln type returns zero results
- **THEN** the corresponding output file is written with `{ "vulnerabilities": [] }`

### Requirement: Output format matches Shannon exploitation queue schema
Each output file SHALL be named `{vuln_type}_findings.json` (e.g., `injection_findings.json`) and contain a JSON object with a `vulnerabilities` array. Each entry SHALL include the base fields: `ID` (prefixed with `CPG-`), `vulnerability_type`, `externally_exploitable` (true), `confidence` ("medium"), and `notes`.

#### Scenario: Injection findings output
- **WHEN** the injection Cypher query returns call chains from routes to DB/ORM functions
- **THEN** `injection_findings.json` is written with entries containing `ID` (e.g., "INJ-CPG-001"), `vulnerability_type`, `externally_exploitable`, `confidence`, `source`, `path`, `sink_call`, `mismatch_reason`, `notes`

#### Scenario: XSS findings output
- **WHEN** the XSS Cypher query returns call chains from routes to HTML render functions
- **THEN** `xss_findings.json` is written with entries containing `ID` (e.g., "XSS-CPG-001"), `vulnerability_type`, `externally_exploitable`, `confidence`, `source`, `path`, `sink_function`, `mismatch_reason`, `notes`

#### Scenario: Auth findings output
- **WHEN** the auth Cypher query returns routes missing security middleware
- **THEN** `auth_findings.json` is written with entries containing `ID`, `vulnerability_type`, `externally_exploitable`, `confidence`, `source_endpoint`, `missing_defense`, `exploitation_hypothesis`, `notes`

#### Scenario: SSRF findings output
- **WHEN** the SSRF Cypher query returns call chains from routes to HTTP client functions
- **THEN** `ssrf_findings.json` is written with entries containing `ID`, `vulnerability_type`, `externally_exploitable`, `confidence`, `source_endpoint`, `vulnerable_code_location`, `missing_defense`, `exploitation_hypothesis`, `notes`

#### Scenario: Authz findings output
- **WHEN** the authz Cypher query returns routes accessing resources without ownership checks
- **THEN** `authz_findings.json` is written with entries containing `ID`, `vulnerability_type`, `externally_exploitable`, `confidence`, `endpoint`, `guard_evidence`, `side_effect`, `notes`

### Requirement: Language-aware sink function catalog
The analyzer SHALL maintain a catalog of dangerous sink functions per language per vuln type. The initial implementation SHALL support Node.js/TypeScript sinks.

#### Scenario: Node.js injection sinks
- **WHEN** analyzing a Node.js/TypeScript codebase
- **THEN** injection patterns match `mysql.query`, `pg.query`, `sequelize.query`, `knex.raw`, `cursor.execute`, and similar DB query functions

#### Scenario: Node.js XSS sinks
- **WHEN** analyzing a Node.js/TypeScript codebase
- **THEN** XSS patterns match `innerHTML`, `document.write`, `dangerouslySetInnerHTML`, and similar DOM render functions

### Requirement: Idempotent output
Running the analyzer multiple times on the same repo SHALL produce the same output, overwriting previous files.

#### Scenario: Re-run overwrites
- **WHEN** the analyzer is run twice on the same repo without code changes
- **THEN** the output files are identical both times
