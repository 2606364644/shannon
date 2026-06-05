# Recon Route Auth Classification Accuracy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Recon agent's incorrect authentication classification by changing the Section 4.1 table format from one-row-per-handler-group to one-row-per-route with group subsection headers.

**Architecture:** Three prompt template files are modified — `recon.txt` (dynamic recon), `recon-static.txt` (static recon), and `_cross-route-enumeration.txt` (shared cross-route verification partial). No TypeScript code changes. The structural change forces the Recon agent to read each router line individually, eliminating the pattern-matching shortcut that caused misclassification.

**Tech Stack:** Prompt template editing (Markdown), Shannon pipeline (Temporal + Claude Agent SDK)

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `apps/worker/prompts/recon.txt` | Modify | Dynamic recon prompt — Route Mapper Agent instruction + Section 4.1 template |
| `apps/worker/prompts/recon-static.txt` | Modify | Static recon prompt — Section 4.1 template (identical change) |
| `apps/worker/prompts/shared/_cross-route-enumeration.txt` | Modify | Shared partial consumed by all 6 vuln agents — update format description, handler lookup, and self-check |

No new files created. No TypeScript files modified.

---

### Task 1: Update `recon.txt` — Route Mapper Agent Instruction

**Files:**
- Modify: `apps/worker/prompts/recon.txt:137`

- [x] **Step 1: Edit the Route Mapper Agent instruction**

Change the Route Mapper Agent instruction at line 137 to require per-route output format.

Old text (line 137):
```
      - **Route Mapper Agent**: "Find all backend routes and controllers that handle the discovered endpoints: [list endpoints]. Map each endpoint to its exact handler function with file paths and line numbers. **Group detection:** Identify all routes that map to the SAME handler function — these share identical processing logic, and a vulnerability in the handler affects every route in the group. For each group, note whether routes differ in authentication middleware. Include the router definition file:line (e.g., router.js:40-42) for each group so downstream agents can cross-reference."
```

New text:
```
      - **Route Mapper Agent**: "Find all backend routes and controllers that handle the discovered endpoints: [list endpoints]. Map each endpoint to its exact handler function with file paths and line numbers. **Group detection:** Identify all routes that map to the SAME handler function — these share identical processing logic, and a vulnerability in the handler affects every route in the group. For each group, produce a per-route table where EACH route gets its own row with Method, Path, Auth Middleware (or **none** if absent), and Router Line (exact line number). Do NOT group multiple routes into a single cell — each route must be verified independently by reading its exact router line. Include the router definition file:line range (e.g., router.js:40-42) for each group so downstream agents can cross-reference."
```

- [x] **Step 2: Verify the edit**

Run: `grep -n "Route Mapper Agent" apps/worker/prompts/recon.txt`
Expected: Line 137 contains the updated instruction with "per-route table" and "Do NOT group multiple routes into a single cell".

- [x] **Step 3: Commit**

```bash
git add apps/worker/prompts/recon.txt
git commit -m "feat(recon): update Route Mapper Agent instruction to require per-route format"
```

---

### Task 2: Update `recon.txt` — Section 4.1 Template

**Files:**
- Modify: `apps/worker/prompts/recon.txt:231-248`

- [x] **Step 1: Replace the Section 4.1 template**

Replace lines 231–248 (from `### 4.1 Shared Controller Route Groups` through the end of the rules list, up to but not including `## 4.2 Endpoint Security Context`).

Old text (lines 231–248):
```markdown
### 4.1 Shared Controller Route Groups

When multiple routes map to the same handler function, a vulnerability in that
handler affects ALL routes in the group. You MUST produce this summary table so
downstream vulnerability agents can enumerate every affected route and flag
pre-auth (unauthenticated) variants.

| Handler (file:line) | Router Definition | Routes (method + path) | Auth Middleware per Route |
|---|---|---|---|
| controller.index.preview (index.js:32) | router.js:40-42 | GET /preview (thirtyLogin), GET /preview/v2 (thirtyLogin), GET /preview/iframe-demo (none) | /preview/iframe-demo has NO auth middleware → pre-auth risk |
| controller.users.getProfile (users.js:45) | router.js:18-19 | GET /api/users/me (requireAuth), GET /api/admin/users/profile (requireAdmin) | Different auth levels |

**Rules for this table:**
- Only include groups where ≥2 routes share the same handler function.
- For each route, note the exact auth middleware (or "none" if absent).
- Highlight routes with NO auth middleware — these are pre-auth variants of any vulnerability found in the handler.
- Include the handler's file:line location for downstream agents to trace.
- Include the router definition file:line range where the routes are registered. Downstream agents can use this as a cross-reference anchor when their handler name doesn't exactly match the one in this table — they can read the router file to confirm the mapping.
```

New text:
```markdown
### 4.1 Shared Controller Route Groups

When multiple routes map to the same handler function, a vulnerability in that
handler affects ALL routes in the group. You MUST produce per-group subsections
so downstream vulnerability agents can enumerate every affected route and flag
pre-auth (unauthenticated) variants.

#### Group: controller.index.preview (index.js:32) — router.js:40-42

| Method | Path | Auth Middleware | Router Line |
|---|---|---|---|
| GET | /preview | thirtyLogin() | router.js:40 |
| GET | /preview/v2 | thirtyLogin() | router.js:41 |
| GET | /preview/iframe-demo | **none** | router.js:42 |

> ⚠️ `/preview/iframe-demo` has NO auth middleware — pre-auth variant.

---

#### Group: controller.users.getProfile (users.js:45) — router.js:18-19

| Method | Path | Auth Middleware | Router Line |
|---|---|---|---|
| GET | /api/users/me | requireAuth | router.js:18 |
| GET | /api/admin/users/profile | requireAdmin | router.js:19 |

> ⚠️ Different auth levels across routes — admin route has elevated privileges.

---

**Rules for these groups:**
- Only include groups where ≥2 routes share the same handler function.
- Each group starts with a `#### Group:` header containing handler name, file:line, and router definition range.
- Each row MUST correspond to exactly one route — do NOT pack multiple routes into a single table cell.
- The Auth Middleware column must reflect the presence or absence of middleware in that specific router line — never infer from sibling routes. Use `**none**` for routes with no middleware.
- The Router Line column must cite the exact line number so each route can be independently verified.
- When any route in a group has `**none**` auth, add a `> ⚠️` warning block below the table identifying the pre-auth route(s).
- Include the handler's file:line location in the group header for downstream agents to trace.
- Include the router definition file:line range in the group header. Downstream agents can use this as a cross-reference anchor when their handler name doesn't exactly match — they can read the router file to confirm the mapping.
```

- [x] **Step 2: Verify the edit**

Run: `grep -n "### 4.1\|#### Group:\|Each row MUST" apps/worker/prompts/recon.txt`
Expected: Shows the new `#### Group:` subsection headers and the new rule "Each row MUST correspond to exactly one route".

- [x] **Step 3: Verify no breakage with Section 4.2**

Run: `grep -n "## 4.2" apps/worker/prompts/recon.txt`
Expected: `## 4.2 Endpoint Security Context` still appears immediately after the new Section 4.1 content, with a blank line separator.

- [x] **Step 4: Commit**

```bash
git add apps/worker/prompts/recon.txt
git commit -m "feat(recon): replace Section 4.1 one-row-per-group table with one-row-per-route format"
```

---

### Task 3: Update `recon-static.txt` — Section 4.1 Template

**Files:**
- Modify: `apps/worker/prompts/recon-static.txt:207-224`

- [x] **Step 1: Replace the Section 4.1 template**

The static variant has the identical Section 4.1 structure. Replace lines 207–224 (from `### 4.1 Shared Controller Route Groups` through the end of the rules list).

Old text (lines 207–224):
```markdown
### 4.1 Shared Controller Route Groups

When multiple routes map to the same handler function, a vulnerability in that
handler affects ALL routes in the group. You MUST produce this summary table so
downstream vulnerability agents can enumerate every affected route and flag
pre-auth (unauthenticated) variants.

| Handler (file:line) | Router Definition | Routes (method + path) | Auth Middleware per Route |
|---|---|---|---|
| controller.index.preview (index.js:32) | router.js:40-42 | GET /preview (thirtyLogin), GET /preview/v2 (thirtyLogin), GET /preview/iframe-demo (none) | /preview/iframe-demo has NO auth middleware → pre-auth risk |
| controller.users.getProfile (users.js:45) | router.js:18-19 | GET /api/users/me (requireAuth), GET /api/admin/users/profile (requireAdmin) | Different auth levels |

**Rules for this table:**
- Only include groups where ≥2 routes share the same handler function.
- For each route, note the exact auth middleware (or "none" if absent).
- Highlight routes with NO auth middleware — these are pre-auth variants of any vulnerability found in the handler.
- Include the handler's file:line location for downstream agents to trace.
- Include the router definition file:line range where the routes are registered. Downstream agents can use this as a cross-reference anchor when their handler name doesn't exactly match the one in this table — they can read the router file to confirm the mapping.
```

New text (identical to `recon.txt`):
```markdown
### 4.1 Shared Controller Route Groups

When multiple routes map to the same handler function, a vulnerability in that
handler affects ALL routes in the group. You MUST produce per-group subsections
so downstream vulnerability agents can enumerate every affected route and flag
pre-auth (unauthenticated) variants.

#### Group: controller.index.preview (index.js:32) — router.js:40-42

| Method | Path | Auth Middleware | Router Line |
|---|---|---|---|
| GET | /preview | thirtyLogin() | router.js:40 |
| GET | /preview/v2 | thirtyLogin() | router.js:41 |
| GET | /preview/iframe-demo | **none** | router.js:42 |

> ⚠️ `/preview/iframe-demo` has NO auth middleware — pre-auth variant.

---

#### Group: controller.users.getProfile (users.js:45) — router.js:18-19

| Method | Path | Auth Middleware | Router Line |
|---|---|---|---|
| GET | /api/users/me | requireAuth | router.js:18 |
| GET | /api/admin/users/profile | requireAdmin | router.js:19 |

> ⚠️ Different auth levels across routes — admin route has elevated privileges.

---

**Rules for these groups:**
- Only include groups where ≥2 routes share the same handler function.
- Each group starts with a `#### Group:` header containing handler name, file:line, and router definition range.
- Each row MUST correspond to exactly one route — do NOT pack multiple routes into a single table cell.
- The Auth Middleware column must reflect the presence or absence of middleware in that specific router line — never infer from sibling routes. Use `**none**` for routes with no middleware.
- The Router Line column must cite the exact line number so each route can be independently verified.
- When any route in a group has `**none**` auth, add a `> ⚠️` warning block below the table identifying the pre-auth route(s).
- Include the handler's file:line location in the group header for downstream agents to trace.
- Include the router definition file:line range in the group header. Downstream agents can use this as a cross-reference anchor when their handler name doesn't exactly match — they can read the router file to confirm the mapping.
```

- [x] **Step 2: Verify the edit**

Run: `grep -n "### 4.1\|#### Group:\|Each row MUST" apps/worker/prompts/recon-static.txt`
Expected: Shows the new `#### Group:` subsection headers and the new rule.

- [x] **Step 3: Verify consistency with `recon.txt`**

Run: `diff <(sed -n '/### 4.1/,/^## 4.2/p' apps/worker/prompts/recon.txt) <(sed -n '/### 4.1/,/^$/p' apps/worker/prompts/recon-static.txt | head -n -1)`
Expected: No diff (the Section 4.1 content is identical in both files).

- [x] **Step 4: Commit**

```bash
git add apps/worker/prompts/recon-static.txt
git commit -m "feat(recon-static): replace Section 4.1 one-row-per-group table with one-row-per-route format"
```

---

### Task 4: Update `_cross-route-enumeration.txt` — Step CR-1 Format Description

**Files:**
- Modify: `apps/worker/prompts/shared/_cross-route-enumeration.txt:8-12`

- [x] **Step 1: Add format description to Step CR-1**

Replace lines 8–12 (the Step CR-1 section).

Old text:
```markdown
### Step CR-1: Read Shared Controller Groups

Read Section 4.1 (Shared Controller Route Groups) in
`.shannon/deliverables/recon_deliverable.md`. If this section does not exist or is empty,
skip to Step CR-4 and use single-route defaults.
```

New text:
```markdown
### Step CR-1: Read Shared Controller Groups

Read Section 4.1 (Shared Controller Route Groups) in
`.shannon/deliverables/recon_deliverable.md`. If this section does not exist or is empty,
skip to Step CR-4 and use single-route defaults.

The section is organized as group subsections:
- Each group starts with `#### Group: HandlerName (file:line) — router.js:XX-YY`
- Below each group header is a table with one row per route
- Each row contains: Method, Path, Auth Middleware (or `**none**`), Router Line
```

- [x] **Step 2: Commit**

```bash
git add apps/worker/prompts/shared/_cross-route-enumeration.txt
git commit -m "feat(cross-route): add Section 4.1 group subsection format description to CR-1"
```

---

### Task 5: Update `_cross-route-enumeration.txt` — Step CR-2 Handler Lookup

**Files:**
- Modify: `apps/worker/prompts/shared/_cross-route-enumeration.txt:14-22`

- [x] **Step 1: Update Step CR-2 to reference group subsections**

Replace lines 14–22 (the Step CR-2 section).

Old text:
```markdown
### Step CR-2: Locate Your Handler

Find the row in Section 4.1 whose handler matches the vulnerable function you just analyzed.
Match by any of these (in order of reliability):
1. Handler function name + file:line (e.g., `preview` at `index.js:32`).
2. Router definition file:line range — read the router file and confirm your handler is
   referenced at those lines.

If no matching row exists, the handler is unique to one route. Skip to Step CR-4.
```

New text:
```markdown
### Step CR-2: Locate Your Handler

Find the `#### Group:` subsection in Section 4.1 whose handler matches the vulnerable
function you just analyzed. Match by any of these (in order of reliability):
1. Handler function name + file:line in the group header (e.g., `preview` at `index.js:32`).
2. Router definition file:line range in the group header — read the router file and confirm
   your handler is referenced at those lines.

If no matching group exists, the handler is unique to one route. Skip to Step CR-4.

Read ALL rows in the matching group's table — each row is a separate route with its own
auth middleware. Do NOT assume all routes in the group share the same authentication.
```

- [x] **Step 2: Commit**

```bash
git add apps/worker/prompts/shared/_cross-route-enumeration.txt
git commit -m "feat(cross-route): update CR-2 to locate group subsections instead of table rows"
```

---

### Task 6: Update `_cross-route-enumeration.txt` — Self-Check Wording

**Files:**
- Modify: `apps/worker/prompts/shared/_cross-route-enumeration.txt:45-46`

- [x] **Step 1: Update the self-check reference**

Replace lines 45–46.

Old text:
```markdown
**Self-check before proceeding:** Does `affected_routes` list every route from the
Section 4.1 group row? If any route is missing, the finding is incomplete.
```

New text:
```markdown
**Self-check before proceeding:** Does `affected_routes` list every route from the
Section 4.1 group table? If any route is missing, the finding is incomplete.
```

(The change is `group row` → `group table`, matching the new per-row format.)

- [x] **Step 2: Verify the complete file reads correctly**

Run: `cat apps/worker/prompts/shared/_cross-route-enumeration.txt`
Expected: The file contains all three updates — CR-1 with format description, CR-2 with group subsection lookup, and the self-check with "group table".

- [x] **Step 3: Commit**

```bash
git add apps/worker/prompts/shared/_cross-route-enumeration.txt
git commit -m "fix(cross-route): update self-check wording from 'group row' to 'group table'"
```

---

### Task 7: Final Verification — Consistency Check Across All Modified Files

**Files:**
- Verify: `apps/worker/prompts/recon.txt`
- Verify: `apps/worker/prompts/recon-static.txt`
- Verify: `apps/worker/prompts/shared/_cross-route-enumeration.txt`

- [x] **Step 1: Verify Section 4.1 format is identical in both recon prompts**

Run: `diff <(sed -n '/^### 4.1/,/^## 4.2/p' apps/worker/prompts/recon.txt) <(sed -n '/^### 4.1/,/^$/p' apps/worker/prompts/recon-static.txt | head -n -1)`
Expected: No diff output (identical content).

- [x] **Step 2: Verify cross-route partial references group subsections consistently**

Run: `grep -n "group subsection\|#### Group:\|group table\|group row" apps/worker/prompts/shared/_cross-route-enumeration.txt`
Expected:
- Line(s) containing "group subsections" (CR-1)
- Line(s) containing `#### Group:` format reference (CR-1)
- Line(s) containing "group table" in the self-check
- No occurrence of "group row" (the old wording)

- [x] **Step 3: Verify no stale "one-row-per-group" references remain**

Run: `grep -rn "Routes (method + path)" apps/worker/prompts/`
Expected: No output (the old column header is gone from all files).

Run: `grep -rn "Auth Middleware per Route" apps/worker/prompts/`
Expected: No output (the old column header is gone from all files).

- [x] **Step 4: Verify downstream vuln prompts still reference Section 4.1 correctly**

Run: `grep -rn "Section 4.1" apps/worker/prompts/vuln-*.txt | head -20`
Expected: All 6 vuln prompt files still reference "Section 4.1" generically — no changes needed since they don't depend on table structure.

- [x] **Step 5: Run Biome format check (no-op for prompts but good practice)**

Run: `pnpm biome check apps/worker/prompts/ 2>&1 || true`
Expected: Biome may not scan `.txt` files — this is fine. The key check is that no TypeScript files are broken.

Run: `pnpm biome check apps/worker/src/`
Expected: All TypeScript files pass (no changes were made to them).

- [x] **Step 6: Verify git diff is clean and complete**

Run: `git diff main --stat`
Expected: Exactly 3 files modified — `recon.txt`, `recon-static.txt`, `_cross-route-enumeration.txt`.

Run: `git log --oneline main..HEAD`
Expected: 6 commits (one per logical change from Tasks 1–6).

---

## Self-Review Checklist

### 1. Spec Coverage

| Spec Requirement | Task |
|---|---|
| recon.txt — Route Mapper Agent instruction (line 137) | Task 1 |
| recon.txt — Section 4.1 template (lines 231–248) | Task 2 |
| recon-static.txt — Section 4.1 template (lines 207–224) | Task 3 |
| _cross-route-enumeration.txt — CR-1 format description | Task 4 |
| _cross-route-enumeration.txt — CR-2 handler lookup | Task 5 |
| _cross-route-enumeration.txt — Self-check wording | Task 6 |
| Final consistency verification | Task 7 |

### 2. Placeholder Scan

No TBD, TODO, "implement later", "add appropriate error handling", "similar to Task N", or undescribed steps found.

### 3. Type Consistency

Not applicable — this is a prompt-only change with no TypeScript types or function signatures.
