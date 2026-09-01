## 1. Core topology models and validation

- [x] 1.1 Add topology discovery Pydantic models for analysis request/result, node capabilities, evidence, candidate edges, uncertain clues, coverage, task usage, and normalized/invalid outputs.
- [x] 1.2 Extend `RepoSpec` with backward-compatible `roles`, add effective-role helpers, and update `MultiRepoConfig` graph validation without breaking legacy single-`role` YAML.
- [x] 1.3 Implement the deterministic navigation manifest collector with bounded file/output limits and tests for selected repositories, ignored directories, language/framework/proto/client/service clues, and truncation.
- [x] 1.4 Implement topology result normalization and evidence validation (selected-node only, no self-loop, legal protocol, duplicate merge by ordered from/to/protocol, path/line checks, confidence downgrade, uncertain retention, M:N edge retention).
- [x] 1.5 Add topology fingerprint/cache metadata and tests for git HEAD/dirty state plus bounded non-git fallback.

## 2. Read-only topology agent

- [x] 2.1 Add the `CROSS_REPO_TOPOLOGY_DISCOVERY` agent definition and prompt, including full-graph methodology, evidence rules, multi-role output, coverage, uncertain output, and JSON schema.
- [x] 2.2 Add an engine-neutral `readonly-code` tool policy to provider execution and wire openai/Codex engines to expose only read/glob/grep-equivalent tools.
- [x] 2.3 Add prompt/normalizer unit tests and pipeline-testing fixtures covering inferred gRPC edge, HTTP edge, dual-role repository, empty graph, malformed JSON, invalid evidence, and unknown protocol.
- [x] 2.4 Add a dual-engine probe script or test hook that asserts the available/called tool set is read-only and records topology structured output.

## 3. Web backend analysis jobs

- [x] 3.1 Implement a workspace-scoped `TopologyAnalysisStore` with atomic state files, TTL/cache lookup, interrupted-state recovery, usage/cost persistence, and cleanup.
- [x] 3.2 Implement the analysis manager: validate repositories, resolve safe paths, build manifest/fingerprint, enforce count/concurrency/timeout/turn limits, execute the agent, normalize output, and expose cancellable task state.
- [x] 3.3 Add API routes for create/get/cancel analysis with workspace authorization, structured 422 errors, and no scan/session side effects.
- [x] 3.4 Add backend tests for lifecycle, cache reuse/refresh, provider failure, timeout, cancellation, restart recovery, unauthorized workspace access, and cost accounting.

## 4. Frontend topology draft and editor

- [x] 4.1 Extend correlation API types/client and draft state for selected repos, analysis job, node positions, capabilities, AI/manual edges, evidence, uncertain clues, and confirmation status.
- [x] 4.2 Implement the repository multi-selector and analysis panel (start/retry/cancel/poll, progress/errors, cache indicator, manual-mode fallback).
- [x] 4.3 Implement `TopologyEditor` SVG interactions for general directed graphs: draggable multi-entry/M:N nodes, connection handles, edge select/delete/disable, protocol edit, capability toggles, undo/redo, reset layout, and evidence/coverage side panel.
- [x] 4.4 Add an accessible relationship/node table that can perform all topology edits without pointer drag.
- [x] 4.5 Implement draft-to-`CorrFormState`/YAML conversion and confirmation gating, preserving per-repo rescan/reuse source choices and legacy `role`/new `roles` serialization.
- [x] 4.6 Add frontend tests for polling, dual-entry, one-to-many fan-out, many-to-many/shared backend, editor interactions, manual override preservation, dual-role serialization, invalid topology blocking, YAML round trip, i18n, and fallback after analysis failure.

## 5. Scan integration and compatibility

- [x] 5.1 Update cross-repo correlation role maps and topology output construction to consume effective role sets while emitting legacy `role` plus new `roles`.
- [x] 5.2 Keep existing manual form/YAML mode available behind the new flow and ensure old saved multi-configs load unchanged.
- [x] 5.3 Add orchestration regression coverage for star, one-to-many fan-out, dual-entry, M:N/shared backend, multi-hop, backend-to-backend, dual-role, cycle, isolated-node warning, and empty/invalid relation cases.
- [x] 5.4 Run an end-to-end fixture smoke test: select four repos covering two entrypoints and shared backends, auto-analyze, adjust graph, confirm, start scan, and verify existing child scans/per-edge correlation deliverables.

## 6. Documentation and rollout

- [x] 6.1 Document configuration limits/env names, cache behavior, evidence/confidence semantics, supported protocols, manual fallback, and troubleshooting.
- [x] 6.2 Add user-facing copy in zh/en and update feature screenshots/help text.
- [x] 6.3 Add rollout/rollback notes and verify no unrelated full-suite pytest is required; run only targeted package test files plus frontend build/tests.
