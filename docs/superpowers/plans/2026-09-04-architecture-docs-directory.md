# Architecture Docs Directory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `docs/architecture/` as the authoritative current-architecture documentation directory covering the requested detection, analysis, runtime, and engine topics.

**Architecture:** Preserve relevant existing document history with `git mv`, then supplement each moved document and create missing topic documents from source/test analysis. The directory README is the navigation and ownership entry point; `docs/superpowers/` remains the historical spec/plan workflow.

**Tech Stack:** Markdown documentation, repository source code, YAML configuration, tests, and `rg`/`find` verification commands.

**Spec:** Approved chat design on 2026-09-04: new architecture directory, moved existing docs where applicable, source-derived documents for missing topics, no skill or runtime-code changes.

## Global Constraints

- Documentation only; do not modify runtime code or tests.
- Do not edit the pre-existing unrelated changes in `docs/superpowers/README.md`, `packages/web/src/supernova_web/components/repo_manager.py`, `packages/web/tests/test_repo_manager.py`, or the linked-repos design spec.
- Preserve the dual-track invariant: deterministic GitNexus output is never fed into the independent pure-LLM vulnerability track prompt.
- Describe current behavior first; link historical specs/plans rather than copying them.
- Mark designed-but-unimplemented behavior explicitly as design/future state.
- Do not commit because the workspace contains unrelated user changes.

---

### Task 1: Inventory Sources and Map Existing Documents

**Files:**
- Create: `docs/architecture/README.md`
- Move later: `docs/architecture/overview.md` → `docs/architecture/overview.md`
- Move later: `docs/architecture/dual-track-analysis.md` → `docs/architecture/dual-track-analysis.md`
- Move later: `docs/architecture/cross-repo-microservice-scanning.md` → `docs/architecture/cross-repo-microservice-scanning.md`
- Move later: `docs/architecture/auth-profiles.md` → `docs/architecture/auth-profiles.md`
- Move later: `docs/architecture/blackbox-verification.md` → `docs/architecture/blackbox-verification.md`

**Interfaces:**
- Produces the canonical file map used by every later task.
- Consumes existing repository docs and the approved topic list.

- [x] Enumerate source modules, tests, YAML rules, prompts, and API components relevant to each requested topic.
- [x] Inspect old-path references so every moved document can be relinked.
- [x] Draft `docs/architecture/README.md` with directory purpose, reading paths, and complete topic index.
- [x] Verify the index contains every requested topic and no placeholder entries.

### Task 2: Migrate Existing Architecture Documents

**Files:**
- Create: `docs/architecture/`
- Move/modify: the five files listed in Task 1
- Modify: all Markdown references to their old paths

**Interfaces:**
- Produces stable paths consumed by README and later topic documents.
- Preserves Git history through `git mv`.

- [x] Run `git mv` for the five existing documents.
- [x] Refit each migrated document to current architecture terminology and source behavior.
- [x] Replace old-path links throughout the repository.
- [x] Verify no Markdown file still references the moved old paths.

### Task 3: Document Whitebox Detection Chain

**Files:**
- Create: `docs/architecture/entry-point-identification.md`
- Create: `docs/architecture/sink-identification.md`
- Create: `docs/architecture/call-chain-extraction.md`
- Create: `docs/architecture/call-chain-verdict.md`

**Interfaces:**
- Consumes `source_rules.yml`, `sink_rules.yml`, `sink_candidates.yml`, code-index analyzers, graph builders, and verdict/checkpoint modules.
- Produces four end-to-end architecture documents linked from README.

- [x] Analyze entry discovery and classification from source and tests.
- [x] Analyze rule sinks, candidate sinks, LLM-discovered sinks, and deterministic fallback.
- [x] Analyze intra-procedural/cross-function/cross-repo chain construction and graph persistence.
- [x] Analyze verdict agents, timeout/retry behavior, checkpoint fingerprints, merge semantics, and authz special handling.
- [x] Ensure the GitNexus/LLM track boundary and independence invariant are explicit.

### Task 4: Document Exploitation, MR, and Cross-Repo Features

**Files:**
- Create: `docs/architecture/poc-generation.md`
- Create: `docs/architecture/mr-scanning.md`
- Modify: `docs/architecture/cross-repo-microservice-scanning.md`

**Interfaces:**
- Consumes exploitation queues, PoC generation/verification modules, MR scan pipeline, topology discovery, and multi-repo worker code.
- Produces implementation-oriented documents for vulnerability output and repository workflows.

- [x] Trace PoC generation, transport, execution, evidence, and verification status.
- [x] Trace MR input, diff processing, changed-file-to-entry/chain reasoning, and output.
- [x] Trace multi-repo workspace loading, topology discovery, service call edges, and cross-repo analysis.

### Task 5: Document Profiles, Blackbox Runtime, and Engines

**Files:**
- Create: `docs/architecture/host-profiles.md`
- Modify: `docs/architecture/auth-profiles.md`
- Modify: `docs/architecture/blackbox-verification.md`
- Create: `docs/architecture/browser-engines.md`
- Create: `docs/architecture/agent-engines.md`

**Interfaces:**
- Consumes auth/host profile models and stores, web APIs, blackbox pipeline, Playwright/agent-browse abstractions, and provider implementations.
- Produces current-state documents for operator-facing configuration and execution engines.

- [x] Document auth profile schema, matching, verification, persistence, and scan integration.
- [x] Document host profile schema, host mapping, persistence, API, and consumers.
- [x] Document whitebox-to-blackbox handoff, target selection, probes, evidence, and result correlation.
- [x] Compare Playwright and agent-browse capabilities, switching, and degradation behavior.
- [x] Compare Codex/Claude-side and openai-agents execution paths without misrepresenting SDK names.

### Task 6: Final Navigation and Verification

**Files:**
- Modify: `docs/architecture/README.md`
- Modify: any architecture document needing final cross-links

**Interfaces:**
- Produces the completed documentation set and verified navigation graph.

- [x] Run a stale-path scan for moved documents.
- [x] Run a Markdown link-target existence check.
- [x] Scan architecture docs for accidental placeholders.
- [x] Verify the changed-file list contains only intended documentation files and the implementation plan.
- [x] Review every requested user topic against README entries.

## Self-Review

- Spec coverage: all thirteen requested topics map to README plus a dedicated document; project overview is additional context.
- Placeholder prohibition: tasks require concrete source inspection and final placeholder checks.
- Consistency: paths and document responsibilities match the approved chat design; no runtime interfaces are introduced.
