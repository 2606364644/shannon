## Context

Shannon's five-phase pipeline (pre-recon → recon → vuln analysis → exploitation → report) passes intelligence downstream through markdown deliverables. The pre-recon agent is the **only agent with full source code access**; its output determines what every downstream agent can see.

In the `official_common_header_footer` scan, the pre-recon XSS Sink Hunter Agent analyzed 3 of 4 variant template files and silently skipped `futu/cmps/header/variables.html`. This file contained the most dangerous sink in the entire codebase — `seoPath: JSON.parse('<%- seoPath%>')` — which uses unescaped EJS output with no `JSON.stringify` wrapper.

The root cause is two-fold:
1. The XSS Sink Hunter prompt says "find all dangerous sinks" but provides no enumeration mechanism, so the LLM stops after finding "enough" sinks
2. The recon Injection Source Tracer prompt doesn't cross-reference input type definitions against template variables, so parameters arriving through wildcard types (`[key: string]: unknown`) are invisible

## Goals / Non-Goals

**Goals:**
- Guarantee every template/view file in the codebase is enumerated and analyzed during pre-recon
- Ensure cross-variant verification: when brands/locales/themes exist, each variant's templates are independently analyzed
- Ensure recon phase enumerates all parameters that flow from HTTP input to template rendering
- Make coverage gaps visible in deliverables rather than silent

**Non-Goals:**
- Changes to injection, auth, authz, ssrf, or report agent prompts — they correctly consume upstream deliverables
- Changes to pipeline architecture, agent count, or orchestration logic
- Runtime validation of prompt outputs (this is prompt engineering, not code changes)
- Handling template engines not already supported by the scanner

## Decisions

### Decision 1: Two-step forced enumeration in Sink Hunter prompt

**Choice:** Replace the single "find all sinks" instruction with a mandatory Step 1 (glob enumerate) → Step 2 (per-file analysis) workflow.

**Rationale:** The current prompt gives the LLM no structure for ensuring completeness. LLMs satisfice — they stop after finding a representative set. A forced enumeration step creates an explicit checklist the agent must work through.

**Alternatives considered:**
- Add a post-hoc validation step in Phase 3 synthesis — rejected because synthesis is already complex and the pre-recon agent may not have read the skipped files
- Add a separate "Template Enumerator Agent" — rejected because it adds an extra agent round-trip for information that should be gathered in one pass

### Decision 2: Coverage audit table in Section 9

**Choice:** Require a "Template Coverage Audit" table at the top of Section 9 listing every template file and its analysis status.

**Rationale:** Even with forced enumeration, an LLM might skip a file. The table makes gaps explicit and visible to both the synthesis phase and downstream recon agent. It transforms a silent omission into a visible gap.

**Alternatives considered:**
- Rely solely on the prompt change — rejected as single-point-of-failure
- Add a separate validation pass — rejected as over-engineering

### Decision 3: Input-to-template cross-reference in recon

**Choice:** Add parameter completeness verification to recon Section 5 and Section 9 by requiring agents to cross-reference (a) input type definitions, (b) parameter construction code, and (c) template variables.

**Rationale:** The `seoPath` parameter was invisible to recon because it arrived through a wildcard `[key: string]: unknown` type and was extracted in `BuildCommonParamsService.build()`, not in the controller. The recon agent only looked at the controller layer. Cross-referencing the template files themselves catches parameters that bypass explicit type definitions.

**Alternatives considered:**
- Only fix pre-recon — rejected because recon is the "single source of truth" for downstream agents and should independently verify parameter coverage
- Add this check to injection agent — rejected because injection agent deliberately has no source code access; it only reads `recon_deliverable.md`

### Decision 4: Apply changes to both recon-static.txt and recon.txt

**Choice:** Identical changes to both static and live recon prompts.

**Rationale:** The two prompts share Section 5 and Section 9 structure. The static version does pure code analysis; the live version adds browser exploration. The template coverage gap exists in both modes.

## Risks / Trade-offs

| Risk | Impact | Mitigation |
|------|--------|------------|
| Increased prompt token usage | ~350 additional tokens across 3 files | Acceptable — these are one-time prompt costs, not per-turn costs |
| LLM ignores forced enumeration despite instructions | Agent skips a template file | Coverage audit table in Section 9 makes gap visible; recon cross-reference provides second safety net |
| False positives from exhaustive template analysis | More "potential" sinks reported | Downstream injection agent already filters by source-to-sink traceability |
| Overly rigid prompt may not adapt to unusual project structures | Agent struggles with non-standard layouts | Prompt uses examples ("brands, locales, themes") not exhaustive enumeration of possible variant types |
| Changes may conflict with future prompt refactoring | Merge conflicts | Changes are localized to specific agent prompts and report sections |
