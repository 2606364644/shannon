# Whitebox Injection Recall Improvement Design

**Date:** 2026-06-25
**Status:** Pending Review

## Problem

Whitebox scans systematically under-report SQL injection and command injection on backend services. Evidence gathered from ~35 existing workspaces:

- **Type distribution across all injection queues:** SQLi 46, SSTI 39, PathTraversal 33, CommandInjection **7** (almost entirely from `hr_whitebox` + test targets). Real backend services produce near-zero CommandInjection findings.
- **Empty queues on backend services:** `task_center_oss`, `task_center_service_go`, `futu_auth_svr`, `rewards_club_service`, `passkey_info_svr`, `rewards_club_activity` all produce `{"vulnerabilities":[]}`.
- **True positives judged SAFE:** the `task_center_oss` deliverable (otherwise high-quality) marks the following as safe, yet each is a real injection risk:
  - `CreateCrowdReq.sql_condition` / `TagAddReq.olap_sql` — request fields whose names are literally SQL fragments, forwarded over RPC to `task_center_service_go` for execution. Judged "RPC pass-through, locally safe" → a **cross-service second-order SQLi** is silently dropped.
  - `tableName` derived from `uid % N` interpolated into DDL `db.Exec(CREATE TABLE …)` — judged SAFE without tracing whether `uid` is user-controlled (a **DDL identifier injection**).
  - `Where(fmt.Sprintf(...))` in `worker/cron` — excluded by scope without verifying the formatted token is static.

### Root causes

| # | Root cause | Confidence | Location |
|---|---|---|---|
| 1 | Sink checklist too coarse — lists only `DB calls, raw SQL / exec, system`. Omits ORM Raw APIs, dynamic identifiers, indirect command execution. | High | `vuln-injection.txt:153-155` |
| 2 | No guidance for cross-service / second-order injection, and `externally_exploitable=true` queue gate drops them. | High (`sql_condition` case) | `vuln-injection.txt:167-170` |
| 3 | Contract mismatch — agent is told to read "Section 7. Injection Sources" from `pre_recon_deliverable.md`, but pre-recon never emits that section (deliverables end at Section 6). | High | `vuln-injection.txt:140-141` |

## Design

### 1. Expand sink checklist (Root cause 1)

**File:** `apps/worker/prompts/vuln-injection.txt`

Expand the SQLi and Command portions of the one-line sink list in methodology step 3 (`:153-155`, currently `**SQLi:** DB calls, raw SQL, string-built queries | **Command:** exec, system, subprocess …`) into a **per-language sink checklist** — keep the existing File / SSTI / Deserialize portions intact. Keep it terse (a checklist, not prose — the file is already 387 lines):

- **ORM Raw / string-built SQL** (highest-miss class):
  - Go: `db.Raw(`, `gorm.Expr(`, `Where(fmt.Sprintf(`, `db.Exec(` + concatenation, sharded `tableName` interpolation
  - Node: `sequelize.query`/`.literal`, `knex.raw`, typeorm `.query`, mongoose string `where()`
  - Python: `cursor.execute(f"...")`, `%` formatting, `.raw(`, SQLAlchemy `text()`
  - Java: MyBatis `${}` vs `#{}`, JPA `createNativeQuery`, JDBC `Statement` + concatenation
  - PHP: `query()` + concatenation, Laravel `whereRaw` / `DB::raw`
- **Dynamic identifiers** (slot=ident; binds do NOT protect — covers the `tableName`/`uid%N` case):
  - Table name / column name / `ORDER BY` / `GROUP BY` interpolation → MUST trace to origin; non-whitelisted = vulnerable.
- **Indirect command execution** (not only `exec`/`system`):
  - `sh -c`, `shell=True`, library-wrapped shell calls, SSH, job-scheduler concatenation of user strings.

### 2. Fix contract mismatch (Root cause 3)

**File:** `apps/worker/prompts/vuln-injection.txt`

Rewrite the starting instruction at `:140-141`. Today it tells the agent to build its todo list from the non-existent "Section 7. Injection Sources". Change to:

- Derive candidate sources from the Attack Surface section (Section 5) of `recon_deliverable.md` (input vectors), AND
- **Actively grep the whole repo for the sink checklist above** — do not rely solely on the upstream target list. (The `task_center_oss` deliverable shows the agent already greps sinks when it chooses to, but the prompt never requires it, so coverage is inconsistent.)

pre-recon is **not** modified (see Out of Scope).

### 3. Cross-service / second-order injection — enter queue, don't drop (Root cause 2)

A finding reaches the final report **only** via `injection_exploitation_queue.json`: findings-renderer renders every queue entry without filtering on `externally_exploitable` (`findings-renderer.ts:254-259`), and report keeps any section carrying a `### [TYPE]-VULN-NN` ID (`report-executive.txt:87-104`). A watchlist section inside the analysis deliverable would be invisible to report (report never reads that file and would delete the un-ID'd section). So cross-service findings must enter the queue, and `externally_exploitable` is repurposed from an admission gate into a reachability tag.

**File:** `apps/worker/prompts/vuln-injection.txt`
- Rewrite QUEUE INCLUSION CRITERIA (`:167-170`): every `vulnerable` finding enters the queue regardless of reachability. `externally_exploitable` becomes a per-entry reachability tag (`true` = public-internet reachable, `false` = internal/cross-service only) — no longer gates admission.
- Strengthen methodology step 5 ("Make the call", `:162-165`): a user-controlled SQL/command fragment this service **forwards to a downstream service for execution** is a `vulnerable` cross-service sink (`externally_exploitable=false`), NOT `safe`. (Directly fixes `task_center_oss` marking `sql_condition` / `olap_sql` SAFE.)

**File:** `apps/worker/prompts/exploit-injection.txt`
- Add a triage rule: for queue entries with `externally_exploitable=false`, do NOT attempt exploitation — emit a short evidence entry ("cross-service exposure, not exploitable in this binary; referred to report") and advance. This is correct classification (no local sink exists), not skipping, and prevents the phase grinding through un-exploitable items under the existing "no skipping" rule (`:138`, `:448`).

**No change:** `findings-renderer.ts` (already renders all entries), `report-executive.txt` (keeps ID'd sections). This is what makes both `exploit=false` (renderer path) and `exploit=true` (short-evidence path) surface cross-service findings in the final report.

## Out of Scope

**D — dedicated SQL/CMD Sink Hunter in pre-recon: dropped.** Reasons:
1. **Redundant with change #1** — the `task_center_oss` deliverable proves `vuln-injection` already greps sinks itself; giving it a complete checklist (#1) covers the gap.
2. **Highest cost** — pre-recon already spawns 6 sub-agents (3 discovery + 3 vuln); another hunter lengthens Phase 1 wall-clock and token spend on every scan.
3. **Role boundary** — pre-recon is the intelligence gatherer, not the taint analyzer. Unverified sink lists produced in Phase 1 would be noise; source→sink verdicts are the vuln agent's job.

pre-recon's Section 9 ("XSS Sinks and Render Contexts") being XSS-centric is acknowledged but not addressed here — change #2's mandatory repo-wide grep compensates for SQL/CMD.

## Impact

- **Code:** none. No queue schema change, no `session.json` change, no workflow change.
- **Prompts:** `vuln-injection.txt` (3 edits — sink checklist, contract mismatch, queue criteria + step 5), `exploit-injection.txt` (1 edit — `externally_exploitable` triage).
- **Scan cost:** none — no new agent, no extra phase.
- **Blackbox scans:** unaffected (same prompts apply, but the sink checklist and watchlist are net-positive there too).
- **Exploit phase:** `exploit-injection.txt` gains a triage rule for `externally_exploitable=false` entries (short evidence, no attack attempt) — prevents the phase grinding through un-exploitable cross-service items under the existing "no skipping" rule.

## Validation

- `pnpm biome` — lint/format passes on no source changes (prompts are not linted, but confirm nothing else regressed).
- Regression check on existing `task_center_oss` deliverable: confirm the new prompt logic would have placed `sql_condition` / `olap_sql` in the queue as `externally_exploitable=false` cross-service findings instead of marking them SAFE.
- Re-run one backend whitebox scan (e.g. a Go service with known `Where(fmt.Sprintf(...))` or RPC-forwarded SQL fields) and confirm: (a) SQLi/CMDi recall rises, (b) cross-service findings appear in the queue (`externally_exploitable=false`) and in the final report, (c) the exploit agent (if run) triages those entries to short evidence without wasting the phase.
