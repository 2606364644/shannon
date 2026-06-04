# Cross-Route Enumeration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure that when multiple routes map to the same controller handler, the recon phase produces structured cross-reference data and every vuln agent enumerates all affected routes — flagging pre-auth variants.

**Architecture:** Two-sided fix — recon produces a Section 4.1 "Shared Controller Route Groups" table; a new shared partial (`_cross-route-enumeration.txt`) injects a pre-documentation checklist into all six vuln prompts via `@include`. The checklist forces agents to read Section 4.1, locate their handler, enumerate every route by auth tier, and attach `affected_routes`/`authentication_required` fields. A conclusion-trigger verification item is the final safety net.

**Tech Stack:** Prompt engineering only — no TypeScript changes. The existing `@include()` mechanism in `prompt-manager.ts` (lines 234-260) resolves includes at prompt-load time.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `apps/worker/prompts/shared/_cross-route-enumeration.txt` | **Create** | Shared partial with 4-step pre-documentation checklist |
| `apps/worker/prompts/recon.txt` | **Modify** (2 edits) | Enhance Route Mapper task description + insert Section 4.1 template |
| `apps/worker/prompts/recon-static.txt` | **Modify** (2 edits) | Same two edits for whitebox/static recon consistency |
| `apps/worker/prompts/vuln-xss.txt` | **Modify** (2 edits) | `@include` + conclusion-trigger item |
| `apps/worker/prompts/vuln-injection.txt` | **Modify** (2 edits) | `@include` + conclusion-trigger item |
| `apps/worker/prompts/vuln-ssrf.txt` | **Modify** (2 edits) | `@include` + conclusion-trigger item |
| `apps/worker/prompts/vuln-auth.txt` | **Modify** (2 edits) | `@include` + conclusion-trigger item |
| `apps/worker/prompts/vuln-authz.txt` | **Modify** (2 edits) | `@include` + conclusion-trigger item |
| `apps/worker/prompts/vuln-misconfig.txt` | **Modify** (2 edits) | `@include` + conclusion-trigger item |

---

### Task 1: Create shared partial `_cross-route-enumeration.txt`

**Files:**
- Create: `apps/worker/prompts/shared/_cross-route-enumeration.txt`

- [x] **Step 1: Create the new shared partial file**

```
apps/worker/prompts/shared/_cross-route-enumeration.txt
```

Content:

```xml
<cross_route_enumeration>
**Pre-Documentation Checklist: Cross-Route Enumeration (Shared Handlers)**

This checklist MUST be completed IMMEDIATELY BEFORE you document any vulnerability
finding in your exploitation queue. Do NOT proceed to write a finding without completing
all four steps below.

### Step CR-1: Read Shared Controller Groups

Read Section 4.1 (Shared Controller Route Groups) in
`.shannon/deliverables/recon_deliverable.md`. If this section does not exist or is empty,
skip to Step CR-4 and use single-route defaults.

### Step CR-2: Locate Your Handler

Find the row in Section 4.1 whose handler matches the vulnerable function you just analyzed.
Match by any of these (in order of reliability):
1. Handler function name + file:line (e.g., `preview` at `index.js:32`).
2. Router definition file:line range — read the router file and confirm your handler is
   referenced at those lines.

If no matching row exists, the handler is unique to one route. Skip to Step CR-4.

### Step CR-3: Enumerate Affected Routes

For every route listed in the matching group:

- **Pre-auth route** (auth middleware is "none" or absent): Create a **separate** finding.
  Set `externally_exploitable: true`.
- **Same auth across all routes**: Document all routes in a single finding, listing every
  route in `affected_routes`.
- **Different auth tiers**: Create **separate findings per auth tier** to preserve
  exploitation context for downstream agents.

### Step CR-4: Attach Required Fields

Add the following fields to EVERY finding in your exploitation queue. A finding without
these fields is **INCOMPLETE** and must not be submitted.

| Field | Type | Required | Description |
|---|---|---|---|
| `affected_routes` | `string[]` | **MANDATORY** | Every route (METHOD /path) affected by this finding, with auth middleware noted in parentheses. Example: `["GET /preview (thirtyLogin)", "GET /preview/v2 (thirtyLogin)", "GET /preview/iframe-demo (none)"]`. If handler maps to one route only, use a single-element array. |
| `authentication_required` | `boolean` | **MANDATORY** | `false` if ANY route in the group lacks auth middleware (pre-auth). `true` if all routes require authentication. For single-route handlers, set based on that route's middleware. |

**Self-check before proceeding:** Does `affected_routes` list every route from the
Section 4.1 group row? If any route is missing, the finding is incomplete.

Do NOT collapse multiple routes into a single finding unless they have identical
authentication requirements AND identical exploitation characteristics.
</cross_route_enumeration>
```

- [x] **Step 2: Verify the file was created correctly**

Run: `cat apps/worker/prompts/shared/_cross-route-enumeration.txt | head -5`
Expected: First 5 lines show the `<cross_route_enumeration>` opening tag and checklist header.

- [x] **Step 3: Commit**

```bash
git add apps/worker/prompts/shared/_cross-route-enumeration.txt
git commit -m "feat(prompts): add cross-route enumeration shared partial"
```

---

### Task 2: Enhance `recon.txt` — Route Mapper + Section 4.1

**Files:**
- Modify: `apps/worker/prompts/recon.txt` (2 edits)

- [x] **Step 1: Enhance Route Mapper Agent task description (line 135)**

Replace the existing Route Mapper Agent line. The old string is:

```
      - **Route Mapper Agent**: "Find all backend routes and controllers that handle the discovered endpoints: [list endpoints]. Map each endpoint to its exact handler function with file paths and line numbers."
```

The new string is:

```
      - **Route Mapper Agent**: "Find all backend routes and controllers that handle the discovered endpoints: [list endpoints]. Map each endpoint to its exact handler function with file paths and line numbers. **Group detection:** Identify all routes that map to the SAME handler function — these share identical processing logic, and a vulnerability in the handler affects every route in the group. For each group, note whether routes differ in authentication middleware. Include the router definition file:line (e.g., router.js:40-42) for each group so downstream agents can cross-reference."
```

- [x] **Step 2: Add Section 4.1 template (after line 227)**

Insert the Section 4.1 block between the "Shared Controller Parameter Propagation" example (ending at line 227 with `even if one route has no authentication middleware.`) and Section 5 (`## 5. Potential Input Vectors`).

The old string is:

```
even if one route has no authentication middleware.

## 5. Potential Input Vectors for Vulnerability Analysis
```

The new string is:

```
even if one route has no authentication middleware.

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

## 5. Potential Input Vectors for Vulnerability Analysis
```

- [x] **Step 3: Commit**

```bash
git add apps/worker/prompts/recon.txt
git commit -m "feat(prompts): enhance recon with route group detection and Section 4.1 template"
```

---

### Task 3: Enhance `recon-static.txt` — Route Mapper + Section 4.1 (consistency)

**Files:**
- Modify: `apps/worker/prompts/recon-static.txt` (2 edits)

This task applies the same two changes from Task 2 to the static/whitebox recon prompt for consistency. The only differences are the exact text of the Route Mapper line and the anchor point for Section 4.1 insertion.

- [x] **Step 1: Enhance Route Mapper Agent task description (line 123)**

The old string is:

```
      - **Route Mapper Agent**: "Find all backend routes and controllers in the codebase. Map each endpoint to its exact handler function with file paths and line numbers. Include HTTP method, path pattern, and any middleware applied."
```

The new string is:

```
      - **Route Mapper Agent**: "Find all backend routes and controllers in the codebase. Map each endpoint to its exact handler function with file paths and line numbers. Include HTTP method, path pattern, and any middleware applied. **Group detection:** Identify all routes that map to the SAME handler function — these share identical processing logic, and a vulnerability in the handler affects every route in the group. For each group, note whether routes differ in authentication middleware. Include the router definition file:line (e.g., router.js:40-42) for each group so downstream agents can cross-reference."
```

- [x] **Step 2: Add Section 4.1 template (between Section 4 table and Section 5)**

The `recon-static.txt` file does NOT have a "Shared Controller Parameter Propagation" paragraph. Insert Section 4.1 between the end of the Section 4 example rows and Section 5.

The old string is:

```
| ... | ... | ... | ... | ... | ... |

## 5. Potential Input Vectors for Vulnerability Analysis
**Static Analysis Note:** These input vectors are derived from source code analysis.
```

The new string is:

```
| ... | ... | ... | ... | ... | ... |

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

**Shared Controller Parameter Propagation:** When multiple routes map to the same controller
handler function, ALL query/body parameters that the handler reads (e.g., via `ctx.query.*`,
`req.query.*`, `request.getParameter()`) must be listed for EVERY route that uses that handler,
regardless of which route you discovered the parameter on. Do NOT assume a parameter is only
available on one route just because you found it there first.

Example: If `GET /preview/v2` and `GET /preview/iframe-demo` both route to `controller.index.preview`,
and the handler reads `ctx.query.bizEntity`, then BOTH route rows must list `bizEntity` as a parameter —
even if one route has no authentication middleware.

## 5. Potential Input Vectors for Vulnerability Analysis
**Static Analysis Note:** These input vectors are derived from source code analysis.
```

Note: The "Shared Controller Parameter Propagation" paragraph is included here because `recon-static.txt` lacks it, unlike `recon.txt` which already has it.

- [x] **Step 3: Commit**

```bash
git add apps/worker/prompts/recon-static.txt
git commit -m "feat(prompts): enhance recon-static with route group detection and Section 4.1"
```

---

### Task 4: Add `@include` + conclusion trigger to `vuln-xss.txt`

**Files:**
- Modify: `apps/worker/prompts/vuln-xss.txt` (2 insertions, no existing lines modified)

- [x] **Step 1: Insert `@include` after Path Forking paragraph (after line 148)**

The old string is (lines 146-149):

```
	- **Path Forking:** If a variable at a sink can be populated from multiple code paths (e.g., from different branches of an `if/else` statement), you must trace **every path** backward independently. Each unique route is a separate "Data Flow Path" to be analyzed.

	- **Track Mutations:** As you trace backward, note any string concatenations or other mutations. A mutation that occurs **before** an encoder is applied (i.e., closer to the sink) can invalidate that encoding, preventing early termination.
```

The new string is:

```
	- **Path Forking:** If a variable at a sink can be populated from multiple code paths (e.g., from different branches of an `if/else` statement), you must trace **every path** backward independently. Each unique route is a separate "Data Flow Path" to be analyzed.

@include(shared/_cross-route-enumeration.txt)

	- **Track Mutations:** As you trace backward, note any string concatenations or other mutations. A mutation that occurs **before** an encoder is applied (i.e., closer to the sink) can invalidate that encoding, preventing early termination.
```

- [x] **Step 2: Insert conclusion-trigger verification item (before line 291)**

The old string is:

```
ONLY AFTER both systematic analysis AND successful deliverable generation, announce "XSS ANALYSIS COMPLETE" and stop.
```

The new string is:

```
3. Cross-Route Verification: For every vulnerability finding, confirm that `affected_routes` lists all routes sharing the same handler per Section 4.1 of the recon deliverable. Findings missing `affected_routes` or `authentication_required` are INCOMPLETE. Findings with `authentication_required: false` MUST have a corresponding pre-auth route in `affected_routes`.

ONLY AFTER both systematic analysis AND successful deliverable generation, announce "XSS ANALYSIS COMPLETE" and stop.
```

- [x] **Step 3: Commit**

```bash
git add apps/worker/prompts/vuln-xss.txt
git commit -m "feat(prompts): add cross-route enumeration to XSS analysis agent"
```

---

### Task 5: Add `@include` + conclusion trigger to `vuln-injection.txt`

**Files:**
- Modify: `apps/worker/prompts/vuln-injection.txt` (2 insertions, no existing lines modified)

- [x] **Step 1: Insert `@include` after Path Forking paragraph (after line 137)**

The old string is (lines 137-138):

```
			    - **Path Forking:** If a single source variable is used in a way that leads to multiple, different database queries (sinks), you must treat each route as a **separate and independent path for analysis**. For example, if `userInput` is passed to both `updateProfile()` and `auditLog()`, you will analyze the "userInput → updateProfile → DB_UPDATE" path and the "userInput → auditLog → DB_INSERT" path as two distinct units.
			    - **Branch Path Exhaustion:** When a controller method contains conditional branches (if/else, early returns) that lead to different data transformations for the same output variable, you MUST trace every branch independently. Do NOT assume a parameter is safe because one branch validates it — another branch may read the same parameter directly from user input without validation.
```

The new string is:

```
			    - **Path Forking:** If a single source variable is used in a way that leads to multiple, different database queries (sinks), you must treat each route as a **separate and independent path for analysis**. For example, if `userInput` is passed to both `updateProfile()` and `auditLog()`, you will analyze the "userInput → updateProfile → DB_UPDATE" path and the "userInput → auditLog → DB_INSERT" path as two distinct units.

@include(shared/_cross-route-enumeration.txt)

			    - **Branch Path Exhaustion:** When a controller method contains conditional branches (if/else, early returns) that lead to different data transformations for the same output variable, you MUST trace every branch independently. Do NOT assume a parameter is safe because one branch validates it — another branch may read the same parameter directly from user input without validation.
```

- [x] **Step 2: Insert conclusion-trigger verification item (before line 370)**

The old string is:

```
**ONLY AFTER** both todo completion AND successful deliverable generation, announce "**INJECTION ANALYSIS COMPLETE**" and stop.
```

The new string is:

```
3. Cross-Route Verification: For every vulnerability finding, confirm that `affected_routes` lists all routes sharing the same handler per Section 4.1 of the recon deliverable. Findings missing `affected_routes` or `authentication_required` are INCOMPLETE. Findings with `authentication_required: false` MUST have a corresponding pre-auth route in `affected_routes`.

**ONLY AFTER** both todo completion AND successful deliverable generation, announce "**INJECTION ANALYSIS COMPLETE**" and stop.
```

- [x] **Step 3: Commit**

```bash
git add apps/worker/prompts/vuln-injection.txt
git commit -m "feat(prompts): add cross-route enumeration to injection analysis agent"
```

---

### Task 6: Add `@include` + conclusion trigger to `vuln-ssrf.txt`

**Files:**
- Modify: `apps/worker/prompts/vuln-ssrf.txt` (2 insertions, no existing lines modified)

- [x] **Step 1: Insert `@include` after Path Forking paragraph (after line 199)**

The old string is (lines 199-200):

```
	- **Path Forking:** If a sink variable can be populated from multiple branches, trace each branch independently.
	- **Track Mutations:** Record concatenations, redirect logic, or transformations. Any mutation **after sanitization** invalidates protections.
```

The new string is:

```
	- **Path Forking:** If a sink variable can be populated from multiple branches, trace each branch independently.

@include(shared/_cross-route-enumeration.txt)

	- **Track Mutations:** Record concatenations, redirect logic, or transformations. Any mutation **after sanitization** invalidates protections.
```

- [x] **Step 2: Insert conclusion-trigger verification item (before line 308)**

The old string is:

```
**ONLY AFTER** both systematic analysis AND successful deliverable generation, announce "**SSRF ANALYSIS COMPLETE**" and stop.
```

The new string is:

```
3. Cross-Route Verification: For every vulnerability finding, confirm that `affected_routes` lists all routes sharing the same handler per Section 4.1 of the recon deliverable. Findings missing `affected_routes` or `authentication_required` are INCOMPLETE. Findings with `authentication_required: false` MUST have a corresponding pre-auth route in `affected_routes`.

**ONLY AFTER** both systematic analysis AND successful deliverable generation, announce "**SSRF ANALYSIS COMPLETE**" and stop.
```

- [x] **Step 3: Commit**

```bash
git add apps/worker/prompts/vuln-ssrf.txt
git commit -m "feat(prompts): add cross-route enumeration to SSRF analysis agent"
```

---

### Task 7: Add `@include` + conclusion trigger to `vuln-auth.txt`

**Files:**
- Modify: `apps/worker/prompts/vuln-auth.txt` (2 insertions, no existing lines modified)

**Note:** `vuln-auth.txt` does not have a "Path Forking" or "Trace backwards" step like the other vuln prompts. Its methodology is a numbered checklist (steps 1-9). The `@include` is inserted after step 9 (SSO/OAuth) and before the "Confidence scoring" section, which is the natural pre-documentation boundary.

- [x] **Step 1: Insert `@include` after step 9 (SSO/OAuth), before Confidence scoring**

The old string is (lines 176-178):

```
**If failed → classify:** `login_flow_logic` or `token_management_issue` → **suggested attack:** oauth_code_interception / token_replay / noauth_attribute_hijack.

# Confidence scoring (analysis phase; applies to all checks above)
```

The new string is:

```
**If failed → classify:** `login_flow_logic` or `token_management_issue` → **suggested attack:** oauth_code_interception / token_replay / noauth_attribute_hijack.

@include(shared/_cross-route-enumeration.txt)

# Confidence scoring (analysis phase; applies to all checks above)
```

- [x] **Step 2: Insert conclusion-trigger verification item (before line 259)**

The old string is:

```
**ONLY AFTER** both systematic analysis AND successful deliverable generation, announce "**AUTH ANALYSIS COMPLETE**" and stop.
```

The new string is:

```
3. Cross-Route Verification: For every vulnerability finding, confirm that `affected_routes` lists all routes sharing the same handler per Section 4.1 of the recon deliverable. Findings missing `affected_routes` or `authentication_required` are INCOMPLETE. Findings with `authentication_required: false` MUST have a corresponding pre-auth route in `affected_routes`.

**ONLY AFTER** both systematic analysis AND successful deliverable generation, announce "**AUTH ANALYSIS COMPLETE**" and stop.
```

- [x] **Step 3: Commit**

```bash
git add apps/worker/prompts/vuln-auth.txt
git commit -m "feat(prompts): add cross-route enumeration to auth analysis agent"
```

---

### Task 8: Add `@include` + conclusion trigger to `vuln-authz.txt`

**Files:**
- Modify: `apps/worker/prompts/vuln-authz.txt` (2 insertions, no existing lines modified)

**Note:** `vuln-authz.txt` methodology step 1 (Horizontal Authorization Analysis) contains "Trace backwards" language. The `@include` is inserted after step 1's termination criteria and before step 2 (Vertical Authorization Analysis).

- [x] **Step 1: Insert `@include` after step 1 termination, before step 2**

The old string is (lines 159-162):

```
- **Termination:**
    - **Guarded:** if sufficient guard found before any side effect.
    - **Vulnerable:** if any side effect is reached before a sufficient guard.

---

### 2) Vertical Authorization Analysis
```

The new string is:

```
- **Termination:**
    - **Guarded:** if sufficient guard found before any side effect.
    - **Vulnerable:** if any side effect is reached before a sufficient guard.

@include(shared/_cross-route-enumeration.txt)

---

### 2) Vertical Authorization Analysis
```

- [x] **Step 2: Insert conclusion-trigger verification item (before line 364)**

The old string is:

```
**ONLY AFTER** both todo completion AND successful deliverable generation, announce "**AUTHORIZATION ANALYSIS COMPLETE**" and stop.
```

The new string is:

```
3. Cross-Route Verification: For every vulnerability finding, confirm that `affected_routes` lists all routes sharing the same handler per Section 4.1 of the recon deliverable. Findings missing `affected_routes` or `authentication_required` are INCOMPLETE. Findings with `authentication_required: false` MUST have a corresponding pre-auth route in `affected_routes`.

**ONLY AFTER** both todo completion AND successful deliverable generation, announce "**AUTHORIZATION ANALYSIS COMPLETE**" and stop.
```

- [x] **Step 3: Commit**

```bash
git add apps/worker/prompts/vuln-authz.txt
git commit -m "feat(prompts): add cross-route enumeration to authz analysis agent"
```

---

### Task 9: Add `@include` + conclusion trigger to `vuln-misconfig.txt`

**Files:**
- Modify: `apps/worker/prompts/vuln-misconfig.txt` (2 insertions, no existing lines modified)

**Note:** `vuln-misconfig.txt` does not have a "Path Forking" step. Its methodology is a numbered checklist (steps 1-9 for Open Redirect phases, then steps 5-9 for security headers/CORS/cookies/clickjacking/info disclosure). The `@include` is inserted after step 3 "Validation Analysis" (the final Open Redirect phase) and before step 4 "Impact Assessment".

- [x] **Step 1: Insert `@include` after step 3 (Validation Analysis), before step 4**

The old string is (lines 142-145):

```
**If validation absent or bypassable → classify:** `Open_Redirect` → **suggested attack:** `open_redirect_protocol_bypass` / `open_redirect_encoding_bypass` / `open_redirect_domain_bypass`.
**If validation sufficient → mark safe in "Secure by Design" section.**

## 4) Open Redirect — Phase D: Impact Assessment
```

The new string is:

```
**If validation absent or bypassable → classify:** `Open_Redirect` → **suggested attack:** `open_redirect_protocol_bypass` / `open_redirect_encoding_bypass` / `open_redirect_domain_bypass`.
**If validation sufficient → mark safe in "Secure by Design" section.**

@include(shared/_cross-route-enumeration.txt)

## 4) Open Redirect — Phase D: Impact Assessment
```

- [x] **Step 2: Insert conclusion-trigger verification item (before line 284)**

The old string is:

```
**ONLY AFTER** both systematic analysis AND successful deliverable generation, announce "**MISCONFIG ANALYSIS COMPLETE**" and stop.
```

The new string is:

```
3. Cross-Route Verification: For every vulnerability finding, confirm that `affected_routes` lists all routes sharing the same handler per Section 4.1 of the recon deliverable. Findings missing `affected_routes` or `authentication_required` are INCOMPLETE. Findings with `authentication_required: false` MUST have a corresponding pre-auth route in `affected_routes`.

**ONLY AFTER** both systematic analysis AND successful deliverable generation, announce "**MISCONFIG ANALYSIS COMPLETE**" and stop.
```

- [x] **Step 3: Commit**

```bash
git add apps/worker/prompts/vuln-misconfig.txt
git commit -m "feat(prompts): add cross-route enumeration to misconfig analysis agent"
```

---

### Task 10: Verify build and lint

**Files:**
- No file changes — verification only

- [x] **Step 1: Run TypeScript build to confirm no regressions**

Run: `pnpm run build`
Expected: Build completes successfully. (Prompt files are plain text and don't affect compilation, but this confirms nothing is broken.)

- [x] **Step 2: Run Biome lint/format check**

Run: `pnpm biome`
Expected: No new errors. (Prompt `.txt` files are excluded from Biome, so this confirms the JS/TS codebase is clean.)

- [x] **Step 3: Verify `@include` resolution would work**

Run: `ls apps/worker/prompts/shared/_cross-route-enumeration.txt`
Expected: File exists.

Run: `grep -r '@include(shared/_cross-route-enumeration.txt)' apps/worker/prompts/`
Expected: 6 matches — one per vuln prompt (`vuln-xss.txt`, `vuln-injection.txt`, `vuln-ssrf.txt`, `vuln-auth.txt`, `vuln-authz.txt`, `vuln-misconfig.txt`).

Run: `grep -c 'Shared Controller Route Groups' apps/worker/prompts/recon.txt apps/worker/prompts/recon-static.txt`
Expected: 1 match in each file (2 total).

- [x] **Step 4: Commit any fixups if needed (optional)**

Only if previous steps revealed issues. Otherwise skip.

---

## Self-Review Checklist

### 1. Spec Coverage

| Spec Requirement | Task |
|---|---|
| New shared partial `_cross-route-enumeration.txt` | Task 1 |
| Recon Route Mapper group detection | Task 2 (recon.txt) + Task 3 (recon-static.txt) |
| Recon Section 4.1 template | Task 2 (recon.txt) + Task 3 (recon-static.txt) |
| Vuln `@include` insertion (6 files) | Tasks 4-9 |
| Conclusion-trigger verification item (6 files) | Tasks 4-9 |
| `recon-static.txt` consistency | Task 3 (not in spec but required for whitebox mode) |

### 2. Placeholder Scan

No TBD, TODO, "implement later", "add appropriate", "similar to", or "write tests" patterns found. Every step contains exact file content and commands.

### 3. Type Consistency

- The `@include` directive `@include(shared/_cross-route-enumeration.txt)` is identical across all 6 vuln prompts.
- The conclusion-trigger verification item text is identical across all 6 vuln prompts (only the agent name in the "ONLY AFTER" line differs — but the verification item itself is the same).
- The Section 4.1 template is identical in `recon.txt` and `recon-static.txt` (with the addition of the "Shared Controller Parameter Propagation" paragraph in `recon-static.txt` which lacks it).
