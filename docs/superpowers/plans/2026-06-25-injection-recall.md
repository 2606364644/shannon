# Injection Recall Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise SQLi / Command Injection recall in whitebox scans by expanding the vuln-injection sink checklist, fixing the recon→vuln contract mismatch, and re-routing cross-service / second-order injection findings into the queue (with an exploit-phase triage) instead of dropping them.

**Architecture:** Prompt-only changes to two files. `vuln-injection.txt` gains (a) a per-language sink checklist, (b) a corrected source-discovery instruction that requires an active repo-wide sink grep, and (c) a redefinition of cross-service sinks as `vulnerable` + `externally_exploitable=false` that enter the queue. `exploit-injection.txt` gains a reachability triage so those entries get a short evidence entry instead of a wasted attack. No code, no schema, no queue-renderer change — findings-renderer already renders every queue entry and report already keeps ID'd sections.

**Tech Stack:** Plain-text prompt templates (`apps/worker/prompts/*.txt`) consumed by the Claude Agent SDK; `@include(shared/...)` partials and `{{VAR}}` substitution via `prompt-manager.ts`.

**Spec:** `docs/superpowers/specs/2026-06-25-injection-recall-design.md`

## Global Constraints

- **Prompt-only:** modify only `apps/worker/prompts/vuln-injection.txt` and `apps/worker/prompts/exploit-injection.txt`. Do NOT touch any `.ts` file. Biome does not lint `.txt` prompts — verify each edit by re-reading the changed region, not by running the linter.
- **Commit policy (from `CLAUDE.md`):** commit/push only when the user asks. The commit steps below run only on explicit user approval; otherwise leave changes in the working tree for review.
- **Timeless copy:** no conversation/PR/history references in prompt text.
- **Preserve substitution:** keep every `@include(shared/...)` and `{{VAR}}` token intact.
- **Indentation:** these prompts mix tab and space indentation. Before each Edit, Read the target region and copy the exact leading whitespace; an Edit whose `old_string` has the wrong indentation will fail to match.

---

## File Structure

- `apps/worker/prompts/vuln-injection.txt` — three edits: step 1 (source discovery), step 3 (sink checklist), step 5 + step 6 queue criteria (cross-service semantics).
- `apps/worker/prompts/exploit-injection.txt` — one edit: reachability triage inserted between "1. Initialization" and "2. The Execution Loop".

No new files. No tests — prompts have no automated test harness; validation is content review + a regression re-scan (Task 5).

---

### Task 1: Expand the SQLi / Command sink checklist (Root cause 1)

**Files:**
- Modify: `apps/worker/prompts/vuln-injection.txt` — methodology step 3 "Detect sinks and label slot types" (around `:153-155`).

**Interfaces:**
- Consumes: the per-language sink list from spec §1.
- Produces: an expanded step 3 that later tasks and the agent both reference as "the sink checklist".

- [ ] **Step 1: Read the exact current text**

Read `apps/worker/prompts/vuln-injection.txt` lines 150-160. Confirm the current step 3 reads (note the tab indentation):

```
	- **3) Detect sinks and label slot types**
		- **SQLi:** DB calls, raw SQL, string-built queries | **Command:** `exec`, `system`, `subprocess`, shell invocations | **File:** `include`, `require`, `fopen`, `readFile` | **SSTI:** template `render`/`compile` with user content | **Deserialize:** `pickle.loads`, `unserialize`, `readObject`, `yaml.load`
		- **Slot labels:** SQL-val/like/num/enum/ident | CMD-argument/part-of-string | FILE-path/include | TEMPLATE-expression | DESERIALIZE-object | PATH-component
```

- [ ] **Step 2: Replace step 3 with the expanded checklist**

Edit `apps/worker/prompts/vuln-injection.txt`. `old_string` = the single `**SQLi:** … yaml.load)` bullet line above (match it exactly, including leading tabs). `new_string`:

```
		- **SQLi — check ALL of these per language (most-missed class):**
			- Go: `db.Raw(`, `gorm.Expr(`, `Where(fmt.Sprintf(`, `db.Exec(` + any concatenation, sharded `tableName` / identifier interpolation
			- Node.js: `sequelize.query` / `.literal`, `knex.raw`, typeorm `.query`, mongoose string-form `where()`
			- Python: `cursor.execute(f"...")` / `%` formatting / `.raw(`, SQLAlchemy `text()`
			- Java: MyBatis `${}` (vs safe `#{}`), JPA `createNativeQuery`, JDBC `Statement` + concatenation
			- PHP: `query()` + concatenation, Laravel `whereRaw` / `DB::raw`
			- **Dynamic identifiers** (slot=ident — parameter binds do NOT protect these): table name, column name, `ORDER BY`, `GROUP BY` interpolation. Trace each identifier to its origin; if it is not whitelist-backed, it is vulnerable.
		- **Command — check ALL of these, not only the obvious ones:** `exec` / `system` / `subprocess` / shell invocations, AND `sh -c`, `shell=True`, library-wrapped shell calls, SSH/remote-shell helpers, job-scheduler or template-driven command construction with user strings.
		- **File:** `include`, `require`, `fopen`, `readFile` | **SSTI:** template `render`/`compile` with user content | **Deserialize:** `pickle.loads`, `unserialize`, `readObject`, `yaml.load`
		- **Slot labels:** SQL-val/like/num/enum/ident | CMD-argument/part-of-string | FILE-path/include | TEMPLATE-expression | DESERIALIZE-object | PATH-component
```

Keep the existing `**File:**` / `**SSTI:**` / `**Deserialize:**` tail and the `**Slot labels:**` line — only the SQLi and Command portions expand.

- [ ] **Step 3: Verify the edit landed**

Read `apps/worker/prompts/vuln-injection.txt` lines 150-170. Confirm: the per-language Go/Node/Python/Java/PHP lines are present; the `**File:**`/`**SSTI:**`/`**Deserialize:**` line and `**Slot labels:**` line are still intact; no `@include` or `{{VAR}}` in this region was disturbed (there are none here).

- [ ] **Step 4: Commit (on user approval)**

```bash
git add apps/worker/prompts/vuln-injection.txt
git commit -m "feat(prompts): expand vuln-injection SQLi/CMD sink checklist per language"
```

---

### Task 2: Fix the recon→vuln contract mismatch (Root cause 3)

**Files:**
- Modify: `apps/worker/prompts/vuln-injection.txt` — methodology step 1 "Create a To Do for each Injection Source…" (around `:140-142`).

**Interfaces:**
- Consumes: spec §2.
- Produces: a step 1 that no longer references the non-existent "Section 7. Injection Sources" and that mandates a repo-wide sink grep.

- [ ] **Step 1: Read the exact current text**

Read `apps/worker/prompts/vuln-injection.txt` lines 138-143. Confirm current step 1 reads:

```
  - **1) Create a To Do for each Injection Source found in the Pre-Recon Deliverable
		  - inside of .shannon/deliverables/pre_recon_deliverable.md under the section "7. Injection Sources (Command Injection and SQL Injection)" use the TodoWrite tool to create a task for each discovered Injection Source. 
		  - Note: All sources are marked as Tainted until they Hit a Santiization that matches the sink context. normalizers (lowercasing, trimming, JSON parse, schema decode) — still **tainted**.
```

- [ ] **Step 2: Replace step 1 with the corrected source-discovery instruction**

Edit `apps/worker/prompts/vuln-injection.txt`. `old_string` = the three-line step 1 block above (match exactly, including tabs and the two trailing spaces on the second line). `new_string`:

```
  - **1) Build your injection source list from recon + your own sink grep**
		  - Derive candidate input vectors from the Attack Surface section (Section 5) of `.shannon/deliverables/recon_deliverable.md` (URL params, POST body, headers, JSON fields, file-upload names).
		  - You MUST also actively grep the whole repository for the sink checklist in step 3. Do NOT rely solely on any upstream target list — the recon and pre-recon deliverables are a starting point, not an exhaustive sink inventory, and sinks they never enumerated will be missed if you only analyze what they list.
		  - Create a TodoWrite task for every input source AND every sink the grep surfaces.
		  - Note: All sources are marked as Tainted until they hit a Sanitization that matches the sink context. Normalizers (lowercasing, trimming, JSON parse, schema decode) — still **tainted**.
```

(Also fixes the original "Santiization" typo within the rewritten line.)

- [ ] **Step 3: Verify the edit landed**

Read `apps/worker/prompts/vuln-injection.txt` lines 138-148. Confirm: no remaining reference to "Section 7" or "Injection Sources (Command Injection and SQL Injection)"; the mandatory grep instruction is present; the tainted-note line is intact.

- [ ] **Step 4: Commit (on user approval)**

```bash
git add apps/worker/prompts/vuln-injection.txt
git commit -m "fix(prompts): vuln-injection no longer references non-existent recon section; require sink grep"
```

---

### Task 3: Route cross-service / second-order injection into the queue (Root cause 2, vuln side)

**Files:**
- Modify: `apps/worker/prompts/vuln-injection.txt` — two adjacent edits in methodology step 5 ("Make the call", `:162-165`) and step 6 QUEUE INCLUSION CRITERIA (`:166-170`).

**Interfaces:**
- Consumes: spec §3.
- Produces: a step 5 that classifies forwarded SQL/command fragments as `vulnerable` (`externally_exploitable=false`), and a queue-inclusion rule that admits every `vulnerable` finding. Task 4 depends on these `externally_exploitable=false` entries existing in the queue.

- [ ] **Step 1: Read the exact current text**

Read `apps/worker/prompts/vuln-injection.txt` lines 160-172. Confirm step 5 and the start of step 6 read:

```
  - **5) Make the call (vulnerability or safe)**
    - **Vulnerable** if any tainted input reaches a slot with no defense or the wrong one.
    - Include a short rationale (e.g., "context mismatch: regex escape on ORDER BY keyword slot").
    - If concat occurred **after** sanitization, treat that sanitization as **non-effective** for this path.
  - **6) Append to findings list (consistent fields)**
	  -  **If the verdict is `vulnerable`:** Include the finding in your exploitation queue. Set `externally_exploitable` to `true` ONLY if exploitable via public internet without internal access. Ensure all fields in the `exploitation_queue_format`, including a minimal `witness_payload`, are populated.
```

- [ ] **Step 2: Append the cross-service rule to step 5**

Edit `apps/worker/prompts/vuln-injection.txt`. `old_string`:

```
    - If concat occurred **after** sanitization, treat that sanitization as **non-effective** for this path.
  - **6) Append to findings list (consistent fields)**
```

`new_string`:

```
    - If concat occurred **after** sanitization, treat that sanitization as **non-effective** for this path.
    - **Cross-service / second-order sinks:** A user-controlled SQL or command fragment that THIS service forwards to a downstream service for execution (e.g. an RPC field literally named `sql_condition` or `olap_sql`, a forwarded query string, a remote-shell command argument) is a **vulnerable** sink, NOT safe. Verdict `vulnerable`, set `externally_exploitable=false`, and record the downstream service in `notes`. The injection executes outside this binary, but it is still a real injection — do not mark it safe merely because there is no local driver/shell call.
  - **6) Append to findings list (consistent fields)**
```

- [ ] **Step 3: Rewrite the queue-inclusion criteria in step 6**

Edit `apps/worker/prompts/vuln-injection.txt`. `old_string` = the `**If the verdict is vulnerable:** …` line and the final `**QUEUE INCLUSION CRITERIA:**` line together (match exactly):

```
	  -  **If the verdict is `vulnerable`:** Include the finding in your exploitation queue. Set `externally_exploitable` to `true` ONLY if exploitable via public internet without internal access. Ensure all fields in the `exploitation_queue_format`, including a minimal `witness_payload`, are populated.
```

`new_string`:

```
	  -  **If the verdict is `vulnerable`:** Include the finding in your exploitation queue. Set `externally_exploitable` to `true` if exploitable via public internet without internal access; set it to `false` for cross-service / second-order / internal-only findings. BOTH enter the queue — `externally_exploitable` is a reachability tag, not an admission gate. Ensure all fields in the `exploitation_queue_format`, including a minimal `witness_payload`, are populated.
```

Then a second Edit in the same task for the trailing criterion line. `old_string`:

```
    - **QUEUE INCLUSION CRITERIA:** ONLY include vulnerabilities where `externally_exploitable = true`. Exclude any vulnerability requiring internal network access, VPN, or direct server access.
```

`new_string`:

```
    - **QUEUE INCLUSION CRITERIA:** Include EVERY finding with verdict `vulnerable`, regardless of `externally_exploitable`. Findings with `externally_exploitable=false` (cross-service, second-order, internal-only) are surfaced in the final report via the findings renderer and are triaged separately by the exploit agent — they must NOT be dropped.
```

- [ ] **Step 4: Verify both edits landed**

Read `apps/worker/prompts/vuln-injection.txt` lines 160-175. Confirm: step 5 has the "Cross-service / second-order sinks" bullet; step 6's vulnerable-branch line says "BOTH enter the queue"; the QUEUE INCLUSION CRITERIA line now says "regardless of externally_exploitable" and "must NOT be dropped".

- [ ] **Step 5: Commit (on user approval)**

```bash
git add apps/worker/prompts/vuln-injection.txt
git commit -m "feat(prompts): route cross-service injection findings into queue as externally_exploitable=false"
```

---

### Task 4: Add reachability triage to the injection exploit agent (Root cause 2, exploit side)

**Files:**
- Modify: `apps/worker/prompts/exploit-injection.txt` — insert a "Reachability Triage" block between "1. Initialization" and "2. The Execution Loop" (around `:151-153`).

**Interfaces:**
- Consumes: the `externally_exploitable=false` queue entries produced by Task 3.
- Produces: a triage rule so those entries get one short evidence entry and advance, instead of the exploit agent attacking them under the "no skipping" rule.

- [ ] **Step 1: Read the exact current text**

Read `apps/worker/prompts/exploit-injection.txt` lines 148-156. Confirm the boundary reads:

```
    - "SQLI-VULN-01: Exploit endpoint /api/search?q= (Hypothesis: Basic UNION injection)"
    - "SQLI-VULN-02: Exploit endpoint /api/products?id= (Hypothesis: Error-based)"

**2. The Execution Loop:**
```

- [ ] **Step 2: Insert the Reachability Triage block**

Edit `apps/worker/prompts/exploit-injection.txt`. `old_string`:

```
    - "SQLI-VULN-02: Exploit endpoint /api/products?id= (Hypothesis: Error-based)"

**2. The Execution Loop:**
```

`new_string`:

```
    - "SQLI-VULN-02: Exploit endpoint /api/products?id= (Hypothesis: Error-based)"

**Reachability Triage (before attacking each entry):**
Queue entries carry `externally_exploitable` as a reachability tag. Before running any payload against a vulnerability:
- If `externally_exploitable=true` (public-internet reachable): follow the full OWASP Exploitation Workflow below.
- If `externally_exploitable=false` (cross-service / second-order / internal-only — the sink executes in a downstream service or internal context, not in this binary): do NOT attempt exploitation. Produce a single short evidence entry for that ID stating "cross-service exposure: the sink executes outside this binary (downstream: <service>); not exploitable via this target, referred to the report for downstream follow-up", mark the task completed, and advance. This is correct reachability classification, NOT skipping — there is no local sink to probe, so attack attempts cannot produce evidence. Exploitation effort belongs only on `externally_exploitable=true` entries.

**2. The Execution Loop:**
```

- [ ] **Step 3: Verify the edit landed**

Read `apps/worker/prompts/exploit-injection.txt` lines 148-165. Confirm: the "Reachability Triage" block sits between the Initialization examples and "2. The Execution Loop"; the existing `**2. The Execution Loop:**` heading is intact and unchanged.

- [ ] **Step 4: Commit (on user approval)**

```bash
git add apps/worker/prompts/exploit-injection.txt
git commit -m "feat(prompts): exploit-injection triages externally_exploitable=false entries to short evidence"
```

---

### Task 5: Validation & regression check

**Files:** none modified (verification only).

- [ ] **Step 1: Confirm no code files were touched**

Run: `git status --short`
Expected: only `apps/worker/prompts/vuln-injection.txt` and `apps/worker/prompts/exploit-injection.txt` (+ this plan + the spec) appear; no `.ts`/`.json`/`.yml` changes.

- [ ] **Step 2: Biome sanity (no collateral damage)**

Run: `pnpm biome`
Expected: passes (prompts are not linted; this confirms no source file was accidentally changed).

- [ ] **Step 3: Static regression check on the existing `task_center_oss` deliverable**

Re-read `workspaces/task_center_oss_whitebox-1781776544378-deliverables/deliverables/injection_analysis_deliverable.md`. Manually confirm the new prompt logic would have re-classified `CreateCrowdReq.sql_condition` / `TagAddReq.olap_sql` as `vulnerable` with `externally_exploitable=false` (instead of the current "locally safe" verdict), per the Task 3 step-5 rule. This is a paper check against the prior deliverable — it does not require a re-scan.

- [ ] **Step 4: Live regression re-scan (optional, on user request)**

On a Go/Node backend with a known `Where(fmt.Sprintf(...))` site or RPC-forwarded SQL field, run a whitebox scan (e.g. `./shannon start -u <url> -r <repo> --pipeline-testing` for fast iteration). Confirm:
- (a) SQLi/CMDi recall rises vs. the prior run's empty queue;
- (b) cross-service findings appear in `injection_exploitation_queue.json` with `externally_exploitable=false` and in the final report;
- (c) if exploitation ran, the exploit agent produced short "cross-service exposure" evidence for those entries instead of attacking them.

- [ ] **Step 5: Commit the plan/spec if not already committed (on user approval)**

```bash
git add docs/superpowers/specs/2026-06-25-injection-recall-design.md docs/superpowers/plans/2026-06-25-injection-recall.md
git commit -m "docs: injection recall improvement spec and plan"
```

---

## Self-Review

**1. Spec coverage:**
- Spec §1 (expand sink checklist) → Task 1. ✓
- Spec §2 (fix contract mismatch + require grep) → Task 2. ✓
- Spec §3 (vuln side: queue criteria + step 5 cross-service) → Task 3. ✓
- Spec §3 (exploit side: triage) → Task 4. ✓
- Spec "No change: findings-renderer, report-executive" → honored (no task touches them). ✓
- Spec "Out of Scope: D dropped" → no task adds a pre-recon sink hunter. ✓
- Spec Validation → Task 5. ✓

**2. Placeholder scan:** every edit step contains the full `old_string` and full `new_string` copy — no TBD/TODO/"add appropriate …". The only conditional language is the optional live re-scan (Task 5 Step 4), which is explicitly optional and on-user-request, not a placeholder.

**3. Consistency check:** `externally_exploitable=false` is introduced in Task 3 (vuln) and consumed in Task 4 (exploit) with identical wording ("reachability tag", "cross-service / second-order / internal-only"). Task 4 must run after Task 3 — the dependency is stated in Task 4's Interfaces block. Task 1 and Task 2 are independent of each other and of Task 3/4, but all four edit `vuln-injection.txt` (1/2/3) or `exploit-injection.txt` (4); execute them in order so line-number references in later tasks stay roughly accurate, and always Read the current region before Edit (indentation-coupling caveat in Global Constraints).
