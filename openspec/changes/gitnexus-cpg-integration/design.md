## Context

Shannon Lite's five-phase pipeline runs purely dynamic testing: Pre-Recon → Recon → 5 parallel (Vuln → Exploit) → Report. Vulnerability hypotheses come from LLM agents reading source code. The `FindingsProvider` interface (`apps/worker/src/interfaces/findings-provider.ts`) provides a DI-injectable hook between the vuln and exploit phases (`mergeFindingsIntoQueue()`), but is currently a no-op in Lite.

GitNexus is an open-source code intelligence tool (github.com/abhigyanpatwari/GitNexus) that builds a knowledge graph from source code: AST-level symbols, CALLS/ACCESSES/QUERIES edges, Process (execution flow) nodes, Route/Tool entry points. Stored in LadybugDB, queryable via Cypher. It runs as a CLI (`gitnexus analyze`) and exposes an MCP server.

The Docker runtime mounts the target repo as `:ro` into the worker container, with workspace-backed overlays for `.shannon/deliverables`. The repo's `.shannon-cpg/` directory is readable inside the container without any CLI changes.

## Goals / Non-Goals

**Goals:**
- Run GitNexus analysis as a pre-scan step on the host, producing structural security findings per vuln type
- Merge those findings into Shannon's exploitation queue so exploit agents attempt to validate them
- Zero modifications to Shannon's pipeline orchestration (workflows.ts, activities.ts) beyond the one-line DI wiring in worker.ts
- Python analyzer is fully independent — separate directory, own dependencies, no Shannon imports

**Non-Goals:**
- Replacing or modifying the vuln analysis agents (they continue to produce their own findings independently)
- Taint analysis or value-level data flow (GitNexus tracks structural call chains, not parameter-level taint)
- Building a CPG from scratch (GitNexus is the graph engine)
- Modifying the Shannon CLI to add flags or mount handling
- Supporting all 25+ GitNexus languages from day one

## Decisions

### D1: Python for the security analyzer, TypeScript only for the FindingsProvider

**Choice:** Python script for Cypher querying and pattern matching; ~50-line TS class for the DI integration.

**Rationale:** GitNexus's query API is language-agnostic (Cypher over HTTP/MCP). Python has strong graph querying ergonomics and the user prefers it. The TS part is mandatory because `FindingsProvider` is a TypeScript interface injected into the DI container.

**Alternative considered:** All TypeScript — would allow importing GitNexus SDK types directly, but adds Node.js Cypher client dependency to the Shannon worker and forces the user to write TS for the analysis logic.

### D2: File-based contract at `{repo}/.shannon-cpg/`

**Choice:** Python writes per-vuln-type JSON files to `{target-repo}/.shannon-cpg/`. TS FindingsProvider reads from `{repoPath}/.shannon-cpg/` inside the container.

**Rationale:** The repo is mounted `:ro` into the Docker container, so files written before container start are visible. No CLI changes, no extra volume mounts, no environment variables. The `.shannon-cpg/` directory is a convention — users add it to `.gitignore`.

**Alternative considered:** Environment variable `CPG_FINDINGS_PATH` with extra `-v` mount — requires modifying `docker.ts` (AGPL code change), more moving parts.

### D3: Findings format matches `*_exploitation_queue.json` schema

**Choice:** Python outputs JSON with `{ "vulnerabilities": [...] }` structure where each entry contains the base fields (`ID`, `vulnerability_type`, `externally_exploitable`, `confidence`, `notes`) plus vuln-type-specific fields matching Shannon's Zod schemas (`apps/worker/src/ai/queue-schemas.ts`).

**Rationale:** The FindingsProvider merges entries into the existing queue JSON. Using the same schema avoids format conversion in the TS layer and lets the exploit agents consume findings uniformly.

### D4: Simple Cypher pattern matching (v1), not taint analysis

**Choice:** Each vuln type gets a Cypher query template that matches structural patterns:
- **Injection**: `Route → CALLS* → DB/ORM query function` (short path)
- **XSS**: `Route → CALLS* → HTML render function` (innerHTML, document.write, etc.)
- **SSRF**: `Route → CALLS* → HTTP client function` (fetch, axios, etc.)
- **Auth**: Routes lacking middleware/wrapper nodes in their process chain
- **Authz**: `Route handler → ACCESSES → resource field` without ownership-check function in between

**Rationale:** GitNexus tracks structural relationships (CALLS, ACCESSES, QUERIES edges) but not parameter-level taint. Pattern matching on call chains gives "high-value hypotheses" — the exploit agents confirm or deny them. Starting simple, iterating based on signal quality.

### D5: `confidence: "medium"` for all GitNexus findings

**Choice:** All structural findings start at `confidence: "medium"`. LLM-driven vuln agents produce `"high"` or `"low"`. Exploit agents are the arbiter of actual exploitability.

**Rationale:** Structural analysis can confirm a call chain exists but cannot confirm user input flows through it. Medium confidence signals "worth investigating" without polluting the high-confidence signal from LLM agents.

### D6: ID prefix `CPG-` to distinguish from agent findings

**Choice:** GitNexus findings use IDs like `INJ-CPG-001`, `XSS-CPG-001` to distinguish from agent-generated `INJ-VULN-001` findings.

**Rationale:** In the report and exploitation evidence, it's clear which findings came from structural analysis vs LLM analysis. Aids debugging and iterative improvement of Cypher patterns.

## Risks / Trade-offs

**[Low signal-to-noise]** → GitNexus finds many call chains that aren't exploitable (e.g., parameterized queries, internal-only routes). Mitigation: exploit agents filter — they only report confirmed exploits. False positives cost agent turns (token spend) but don't pollute the final report.

**[Language-specific patterns]** → Cypher query templates need per-language sink function lists (e.g., `mysqli_query` for PHP, `cursor.execute` for Python). Mitigation: start with Node.js/TypeScript sinks (most common in Shannon's target audience), add languages incrementally.

**[GitNexus index staleness]** → If the target repo changes between `gitnexus analyze` and Shannon scan, findings may reference outdated code. Mitigation: document the requirement to re-run `gitnexus analyze` if code changes. Could add a hash check in the future.

**[Duplicate findings]** → Both GitNexus and vuln agents may identify the same vulnerability. Mitigation: the report agent de-duplicates. ID prefix helps identify overlaps in debugging.

**[`.shannon-cpg/` in repo]** → Writing to the target repo could be unexpected. Mitigation: document clearly, provide `.gitignore` snippet, consider adding a `--output` flag to the Python tool for custom locations in the future.
