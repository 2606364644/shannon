# Shannon Recon Prompt Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two Recon-phase prompt defects that caused Shannon to miss XSS vulnerabilities on shared-controller routes and make premature security judgments.

**Architecture:** Pure prompt text edits to `recon.txt` — adding two rule blocks at precise insertion points. No code changes. The spec also calls for reverting `vuln-injection.txt` and `vuln-xss.txt`, but analysis confirms those files are already in the desired state (no `authentication_required`/`accessible_routes` fields exist; `combined_sources` is present), so Change 3 is a verification-only step.

**Tech Stack:** Prompt engineering, Markdown

---

## Path Mapping

The spec references `shannon-py/prompts/` but the actual codebase uses `apps/worker/prompts/`. All paths below use the real filesystem locations.

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `apps/worker/prompts/recon.txt` | Modify | Add two rule blocks (Change 1 + Change 2) |
| `apps/worker/prompts/vuln-injection.txt` | Verify only | Confirm already in correct state (Change 3) |
| `apps/worker/prompts/vuln-xss.txt` | Verify only | Confirm already in correct state (Change 3) |

---

### Task 1: Add "Shared Controller Parameter Propagation" rule to recon.txt (Change 1)

**Files:**
- Modify: `apps/worker/prompts/recon.txt:217-218`

**Context:** Section 4 (API Endpoint Inventory) contains a table example ending at line 217 with `| ... | ... | ... | ... | ... | ... |`. An empty line follows at 218, then Section 5 starts at line 219. The new rule must be inserted between the table example and Section 5.

- [ ] **Step 1: Insert the Shared Controller Parameter Propagation rule**

Edit `apps/worker/prompts/recon.txt`, replacing the blank line between the table and Section 5:

```
old_string:
| ... | ... | ... | ... | ... | ... |

## 5. Potential Input Vectors for Vulnerability Analysis
```

```
new_string:
| ... | ... | ... | ... | ... | ... |

**Shared Controller Parameter Propagation:** When multiple routes map to the same controller
handler function, ALL query/body parameters that the handler reads (e.g., via `ctx.query.*`,
`req.query.*`, `request.getParameter()`) must be listed for EVERY route that uses that handler,
regardless of which route you discovered the parameter on. Do NOT assume a parameter is only
available on one route just because you found it there first.

Example: If `GET /preview/v2` and `GET /preview/iframe-demo` both route to `controller.index.preview`,
and the handler reads `ctx.query.bizEntity`, then BOTH route rows must list `bizEntity` as a parameter —
even if one route has no authentication middleware.

## 5. Potential Input Vectors for Vulnerability Analysis
```

- [ ] **Step 2: Verify the insertion**

Run: `grep -n "Shared Controller Parameter" apps/worker/prompts/recon.txt`
Expected: single match at the new insertion point, between Section 4 table and Section 5 heading.

- [ ] **Step 3: Verify no accidental duplication**

Run: `grep -c "Shared Controller Parameter" apps/worker/prompts/recon.txt`
Expected: `1`

---

### Task 2: Add "No Security Judgments" rule to recon.txt Section 9 (Change 2)

**Files:**
- Modify: `apps/worker/prompts/recon.txt:370-371`

**Context:** Section 9 starts at line 370 with `## 9. Injection Sources (...)`. The `**TASK AGENT COORDINATION:**` line is at line 371. The new rule must be inserted between the section heading and the task agent coordination line.

- [ ] **Step 1: Insert the No Security Judgments rule**

Edit `apps/worker/prompts/recon.txt`, inserting the new rule block between the Section 9 heading and the TASK AGENT COORDINATION line:

```
old_string:
## 9. Injection Sources (Command Injection, SQL Injection, LFI/RFI, SSTI, Path Traversal, Deserialization)
**TASK AGENT COORDINATION:** Launch a dedicated **Injection Source Tracer Agent** to identify these sources:
```

```
new_string:
## 9. Injection Sources (Command Injection, SQL Injection, LFI/RFI, SSTI, Path Traversal, Deserialization)

**CRITICAL — No Security Judgments:** Your job is to IDENTIFY and REPORT facts about injection
sources — where user-controllable input enters, where it flows, and what sink it reaches. You
MUST NOT make security judgments about whether a source is "properly handled", "safe", "secure",
or "not exploitable". Determining whether defenses are sufficient is the SOLE responsibility of
the downstream Vulnerability Analysis agents. If you find a user-controllable input reaching a
sink, REPORT IT — even if you believe the encoding or validation in place is adequate. Let the
vuln agents decide.

**TASK AGENT COORDINATION:** Launch a dedicated **Injection Source Tracer Agent** to identify these sources:
```

- [ ] **Step 2: Verify the insertion**

Run: `grep -n "No Security Judgments" apps/worker/prompts/recon.txt`
Expected: single match in Section 9, before the TASK AGENT COORDINATION line.

- [ ] **Step 3: Verify no accidental duplication**

Run: `grep -c "No Security Judgments" apps/worker/prompts/recon.txt`
Expected: `1`

---

### Task 3: Verify vuln-injection.txt and vuln-xss.txt are in correct state (Change 3)

**Files:**
- Verify: `apps/worker/prompts/vuln-injection.txt`
- Verify: `apps/worker/prompts/vuln-xss.txt`

**Context:** The spec calls for reverting these files to remove `authentication_required`, `accessible_routes`, and Source Completeness Rule, and to restore `combined_sources`. Analysis shows these files already match the desired state — no reversion needed. This task confirms that.

- [ ] **Step 1: Verify no `authentication_required` or `accessible_routes` fields exist**

Run: `grep -n "authentication_required\|accessible_routes" apps/worker/prompts/vuln-injection.txt apps/worker/prompts/vuln-xss.txt`
Expected: no output (no matches)

- [ ] **Step 2: Verify no Source Completeness Rule exists**

Run: `grep -n "Source Completeness\|source_completeness" apps/worker/prompts/vuln-injection.txt apps/worker/prompts/vuln-xss.txt`
Expected: no output (no matches)

- [ ] **Step 3: Verify `combined_sources` field is present**

Run: `grep -n "combined_sources" apps/worker/prompts/vuln-injection.txt apps/worker/prompts/vuln-xss.txt`
Expected: `apps/worker/prompts/vuln-injection.txt:111:		"combined_sources": "list if multiple sources were merged (with order).",`

---

### Task 4: Final verification and commit

**Files:**
- All modified files from Tasks 1–2

- [ ] **Step 1: Review the full diff**

Run: `git diff apps/worker/prompts/recon.txt`
Expected: exactly two additions — the "Shared Controller Parameter Propagation" block (after Section 4 table) and the "No Security Judgments" block (before TASK AGENT COORDINATION in Section 9). No other changes.

- [ ] **Step 2: Verify file integrity — no broken sections**

Run: `grep -n "^## " apps/worker/prompts/recon.txt`
Expected: all section headings present and in order (0 through 9), with no missing or duplicated sections.

- [ ] **Step 3: Commit**

```bash
git add apps/worker/prompts/recon.txt
git commit -m "fix(recon): add shared controller parameter propagation and no-security-judgments rules

Two prompt fixes to prevent Recon from missing XSS vulnerabilities:

1. Shared Controller Parameter Propagation (Section 4): When multiple
   routes share a controller handler, all parameters that handler reads
   must be listed for every route. Prevents the ads_oa_fe case where
   /preview/iframe-demo was missing the bizEntity parameter.

2. No Security Judgments (Section 9): Recon must report injection source
   facts only — never judge whether defenses are sufficient. That is the
   sole responsibility of downstream Vuln Agents. Prevents premature
   'properly handled' conclusions that suppress findings."
```

---

## Self-Review

### 1. Spec Coverage

| Spec Requirement | Task |
|---|---|
| Change 1: Add Shared Controller Parameter Propagation to Section 4 | Task 1 |
| Change 2: Add No Security Judgments to Section 9 | Task 2 |
| Change 3: Revert vuln-injection.txt (remove authentication_required, accessible_routes, Source Completeness Rule; restore combined_sources) | Task 3 (verified already correct) |
| Change 3: Revert vuln-xss.txt (same) | Task 3 (verified already correct) |
| Verification: Section 4 /preview/iframe-demo must show bizEntity | Post-scan verification (out of scope for this plan — requires running a full scan) |
| Verification: Section 9 must not contain security judgment language | Post-scan verification |

### 2. Placeholder Scan

No TBD, TODO, "implement later", "add appropriate error handling", or "similar to Task N" patterns found.

### 3. Type Consistency

N/A — this plan modifies prompt text only, no code types or method signatures involved.
