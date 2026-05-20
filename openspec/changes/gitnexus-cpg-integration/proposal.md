## Why

Shannon Lite only performs dynamic penetration testing — its vulnerability analysis agents discover findings through LLM-driven code reading and live browser exploitation. Shannon Pro adds agentic static analysis (CPG, SAST, SCA, secrets) that feeds findings into the dynamic pipeline, but Pro is closed-source. GitNexus is an open-source code intelligence tool that builds a structural knowledge graph (AST + call graph + execution flows) from source code using tree-sitter across 25+ languages. Integrating GitNexus as a pre-scan static analysis step would give Lite's exploit agents structural vulnerability hypotheses to validate, improving coverage and reducing the chance of missed injection/XSS/SSRF/auth/authz paths.

## What Changes

- Add a Python-based security analyzer that queries GitNexus's LadybugDB via Cypher to detect structural security patterns (e.g., HTTP route → database query call chains without sanitization)
- Write findings to `{repo}/.shannon-cpg/` as per-vuln-type JSON files matching Shannon's exploitation queue schema
- Implement a TypeScript `FindingsProvider` that reads GitNexus findings from `.shannon-cpg/` inside the worker container and merges them into the dynamic pipeline's exploitation queues
- Wire the provider into Shannon's DI container via `setContainerFactory()` in the worker entry point
- The GitNexus analysis runs as a pre-scan step on the host before the Shannon worker container starts; findings are visible inside the container via the read-only repo mount

## Capabilities

### New Capabilities
- `gitnexus-security-analyzer`: Python CLI that runs GitNexus `analyze`, then executes Cypher queries against the resulting knowledge graph to produce structural security findings per vuln type (injection, xss, auth, ssrf, authz). Outputs JSON files to `.shannon-cpg/` directory.
- `findings-provider-gitnexus`: TypeScript `FindingsProvider` implementation that reads `.shannon-cpg/*.json` from the target repo path and merges findings into Shannon's exploitation queue files, enabling the dynamic pipeline's exploit agents to attempt validation of structural hypotheses.

### Modified Capabilities

(none — no existing Shannon Lite specs are modified)

## Impact

- **New Python project**: Independent Python package in a separate directory (not inside the Shannon monorepo). Depends on GitNexus CLI for indexing and its LadybugDB Cypher API for querying.
- **Shannon worker** (`apps/worker/src/temporal/worker.ts`): One-line addition of `setContainerFactory()` to inject the GitNexus findings provider.
- **New file**: `apps/worker/src/services/findings-provider-gitnexus.ts` implementing the `FindingsProvider` interface.
- **Docker volume convention**: Findings at `{repo}/.shannon-cpg/` are readable inside the container via the existing `:ro` repo mount. No CLI changes needed.
- **Target repo**: `.shannon-cpg/` directory should be added to `.gitignore`.
- **No AGPL impact**: The Python analyzer is a separate program. The TS FindingsProvider is new code (AGPL-3.0 as part of the Shannon codebase). Only `worker.ts` is modified (one line).
