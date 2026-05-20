## 1. Python Analyzer: Project Setup

- [ ] 1.1 Create Python project directory structure (outside Shannon monorepo) with `pyproject.toml`, `src/gitnexus_security_analyzer/` package, and `__main__.py` entry point
- [ ] 1.2 Add dependencies: `neo4j` (or HTTP client for LadybugDB Cypher API), `click` or `argparse` for CLI
- [ ] 1.3 Implement CLI argument parsing: `--repo` (required), `--output` (default `{repo}/.shannon-cpg/`)

## 2. Python Analyzer: GitNexus Integration

- [ ] 2.1 Implement GitNexus index detection (check `.gitnexus/` existence in target repo)
- [ ] 2.2 Implement auto-indexing: run `gitnexus analyze` subprocess when index is missing; fail with descriptive error on non-zero exit
- [ ] 2.3 Implement Cypher query client: connect to LadybugDB (default `localhost:7687` or GitNexus MCP HTTP endpoint) and execute parameterized queries

## 3. Python Analyzer: Sink Function Catalog

- [ ] 3.1 Create `sinks.py` with Node.js/TypeScript sink function catalog per vuln type: injection (mysql.query, pg.query, sequelize.query, knex.raw, cursor.execute), XSS (innerHTML, document.write, dangerouslySetInnerHTML), SSRF (fetch, axios, request, http.get), auth (middleware names), authz (authorization check patterns)
- [ ] 3.2 Design catalog as a structured dict keyed by `(language, vuln_type)` → list of `(function_name_pattern, sink_type)` tuples, extensible for future languages

## 4. Python Analyzer: Cypher Query Templates

- [ ] 4.1 Implement injection Cypher query: `MATCH (r:Route)-[:CALLS*1..4]->(handler)-[:CALLS*1..3]->(sink) WHERE sink.name IN $injection_sinks RETURN r, handler, sink, path`
- [ ] 4.2 Implement XSS Cypher query: `MATCH (r:Route)-[:CALLS*1..4]->(handler)-[:CALLS*1..3]->(sink) WHERE sink.name IN $xss_sinks RETURN r, handler, sink, path`
- [ ] 4.3 Implement SSRF Cypher query: `MATCH (r:Route)-[:CALLS*1..4]->(handler)-[:CALLS*1..3]->(sink) WHERE sink.name IN $ssrf_sinks RETURN r, handler, sink, path`
- [ ] 4.4 Implement auth Cypher query: `MATCH (r:Route)-[:HANDLES_ROUTE]->(handler) WHERE NOT (handler)-[:CALLS]->(middleware) RETURN r, handler` (routes lacking auth middleware in process chain)
- [ ] 4.5 Implement authz Cypher query: `MATCH (r:Route)-[:CALLS*1..3]->(handler)-[:QUERIES]->(model)` returning routes that access data models without an intervening ownership-check function

## 5. Python Analyzer: Output Formatting

- [ ] 5.1 Implement `Finding` dataclass matching Shannon's queue schema: `ID`, `vulnerability_type`, `externally_exploitable`, `confidence` ("medium"), `notes`, plus vuln-type-specific optional fields
- [ ] 5.2 Implement ID generator: `{VULN_PREFIX}-CPG-{sequence:03d}` (e.g., `INJ-CPG-001`)
- [ ] 5.3 Implement query result → `Finding` converter per vuln type (maps Cypher result fields to schema fields)
- [ ] 5.4 Implement JSON writer: writes `{vuln_type}_findings.json` with `{ "vulnerabilities": [...] }` to output directory; creates directory if missing; overwrites existing files

## 6. Python Analyzer: Main Orchestrator

- [ ] 6.1 Implement main flow: parse args → verify/create GitNexus index → run 5 Cypher queries → convert results → write output files → print summary → exit 0
- [ ] 6.2 Add error handling: Cypher connection failures, malformed results, write errors — all produce non-zero exit with stderr message
- [ ] 6.3 Add summary output to stdout: findings count per vuln type, total findings, output path

## 7. TypeScript FindingsProvider

- [ ] 7.1 Create `apps/worker/src/services/findings-provider-gitnexus.ts` implementing the `FindingsProvider` interface from `apps/worker/src/interfaces/findings-provider.ts`
- [ ] 7.2 Implement `mergeFindingsIntoQueue(repoPath, vulnType, input)`: read `{repoPath}/.shannon-cpg/{vulnType}_findings.json`, read existing `{deliverablesDir}/{vulnType}_exploitation_queue.json` (if exists), append non-duplicate findings, write merged result, return `{ mergedCount }`
- [ ] 7.3 Handle edge cases: missing `.shannon-cpg/` directory (return 0), missing findings file (return 0), empty vulnerabilities array (return 0), ID dedup against existing queue entries

## 8. DI Container Wiring

- [ ] 8.1 Modify `apps/worker/src/temporal/worker.ts`: import `setContainerFactory` and `GitNexusFindingsProvider`, call `setContainerFactory()` before worker creation to inject the provider
- [ ] 8.2 Verify that the modification is minimal (one-line factory override) and does not affect existing container behavior when `.shannon-cpg/` is absent

## 9. Testing and Validation

- [ ] 9.1 Test Python analyzer against a sample Node.js/Express app with known vulnerability patterns (e.g., crAPI or juice-shop source)
- [ ] 9.2 Verify JSON output format matches Shannon's `queue-schemas.ts` by piping through a JSON schema validator
- [ ] 9.3 Test FindingsProvider unit: mock repo with `.shannon-cpg/` findings, verify merge into existing queue, verify dedup, verify no-op when directory missing
- [ ] 9.4 Run Shannon Lite end-to-end with GitNexus findings present: verify exploit agents attempt to validate CPG findings, verify report includes both agent and CPG-originated findings
