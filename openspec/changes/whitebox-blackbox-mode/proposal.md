# Proposal: Whitebox-Only & Blackbox-Only Scan Modes

## Problem

Shannon's current pipeline requires a live target URL from the first step. This makes it impossible to run security analysis when:

1. Only source code is available (no deployed environment yet)
2. The target environment is not yet provisioned
3. The user wants to do a quick static security review before deployment

Additionally, there is no way to split the work into two phases: analyze source code first, then exploit later when the environment is ready.

## Proposed Solution

Add two new execution modes to the CLI, alongside the existing full pipeline:

| Mode | CLI Flag | Needs URL | Needs Repo | What Runs |
|---|---|---|---|---|
| **Full (unchanged)** | *(default)* | Yes | Yes | Complete 5-phase pipeline |
| **Whitebox-only** | `--whitebox-only` | No | Yes | Pre-recon → Recon (static) → 4× Vuln → Report |
| **Blackbox-only** | `--blackbox-only` | Yes | Yes | 5× Exploit → Report |

Key design principles:
- **Zero impact on existing functionality** — all changes are additive; the default pipeline is untouched
- **File-level handoff** — whitebox and blackbox communicate through deliverable files in `.shannon/deliverables/`, not through Temporal resume/session.json
- **Reuse existing infrastructure** — findings-renderer, queue.json schema, report assembly pipeline are all reused

## Scope

### In Scope

- `--whitebox-only` flag: runs pre-recon, static recon, 4 vuln agents (injection, auth, authz, ssrf — skips XSS), produces static report + exploitation queues
- `--blackbox-only` flag: reads existing deliverables from a prior whitebox run, runs 5 exploit agents + report
- New `recon-static.txt` prompt (pure source-code recon, same deliverable structure)
- CLI changes to make `-u` optional when `--whitebox-only` is set
- Workflow branching for both modes
- Sentinel value for `{{WEB_URL}}` in whitebox prompts

### Out of Scope

- Modifying existing prompt files (existing pipeline must produce identical results)
- Modifying Temporal resume logic (whitebox→blackbox does not use resume)
- Modifying `session.json` structure
- Adding XSS analysis to whitebox mode (requires live verification)
- Changing the `AGENTS` registry or `ALL_AGENTS` array

## Dependencies

- Existing `exploit: false` path (findings-renderer) — reused for whitebox report generation
- Existing `vulnClasses` filtering in workflow — reused to skip XSS
- Existing deliverable file conventions — unchanged
