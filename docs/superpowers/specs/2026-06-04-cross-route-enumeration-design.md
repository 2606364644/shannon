# Cross-Route Enumeration for Shared Controller Handlers

## Problem

When multiple routes map to the same controller handler function, the vulnerability analysis
pipeline currently attributes findings to only one route, missing affected endpoints —
especially pre-auth variants.

**Concrete case:** `/preview`, `/preview/v2`, and `/preview/iframe-demo` all route to
`controller.index.preview`. The XSS agent identified `bizEntity` reflected XSS but only
attributed it to `/preview`. The `/preview/iframe-demo` route lacks `thirtyLogin()` middleware,
making it a pre-auth reflected XSS — the most easily exploitable variant — yet it was never
flagged.

**Root causes:**

1. Recon produces route-to-handler data but in an unstructured format (scattered table rows).
   Downstream agents must manually correlate rows by handler name.
2. No vuln prompt instructs agents to enumerate all routes sharing a vulnerable handler or
   to compare authentication middleware across those routes.
3. Exploitation queue JSON schema doesn't require per-route granularity, so omitting
   co-affected routes isn't structurally visible as an incomplete finding.

## Merge Compatibility Strategy

To minimize merge conflicts when rebasing on upstream Shannon changes:

- **New file** (`_cross-route-enumeration.txt`) — zero conflict risk.
- **Recon.txt** — one line modified (Route Mapper task description) + one block insertion
  (Section 4.1 template). The template insertion is at a stable anchor point between
  Section 4 and Section 5.
- **Vuln prompts** — two single-line insertions per file (no existing lines modified):
  1. `@include` in methodology section (inserted after existing step).
  2. Verification item appended to `<conclusion_trigger>` numbered list.
- **No changes** to `<exploitation_queue_format>` sections — the shared partial instructs
  agents to add fields alongside the existing schema.

## Solution: Two-Sided Fix (Recon Output + Vuln Consumption)

### Part 1: Recon — Enhance Route Mapper Agent + Add Section 4.1

**File:** `apps/worker/prompts/recon.txt`

#### 1a. Enhance Route Mapper Agent Task (line 135)

Append group detection instruction to the existing Route Mapper Agent task. The original
text stays intact; new text is added at the end of the quoted task string.

Current text (line 135):
```
- **Route Mapper Agent**: "Find all backend routes and controllers that handle the discovered endpoints: [list endpoints]. Map each endpoint to its exact handler function with file paths and line numbers."
```

Replace with:
```
- **Route Mapper Agent**: "Find all backend routes and controllers that handle the discovered endpoints: [list endpoints]. Map each endpoint to its exact handler function with file paths and line numbers. **Group detection:** Identify all routes that map to the SAME handler function — these share identical processing logic, and a vulnerability in the handler affects every route in the group. For each group, note whether routes differ in authentication middleware. Include the router definition file:line (e.g., router.js:40-42) for each group so downstream agents can cross-reference."
```

#### 1b. Add Section 4.1 Deliverable Template (after line 227)

Insert after the existing "Shared Controller Parameter Propagation" paragraph and before
Section 5:

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

### Part 2: Shared Partial — Cross-Route Enumeration Rule

**New file:** `apps/worker/prompts/shared/_cross-route-enumeration.txt`

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

### Part 3: Include in All Vuln Prompts

Each vuln prompt gets exactly **two single-line insertions** (no existing lines modified):

#### 3a. Methodology `@include`

Add one line inside the `<methodology>` section, after the "trace backward" or "path forking"
step:

```
@include(shared/_cross-route-enumeration.txt)
```

| File | Insert After |
|---|---|
| `vuln-xss.txt` | Step 2 `**Path Forking:**` paragraph (line ~146) |
| `vuln-injection.txt` | Step 3 `**Path Forking:**` paragraph (line ~145) |
| `vuln-ssrf.txt` | `**Path Forking:**` paragraph (line ~199) |
| `vuln-auth.txt` | First "Trace backwards" related step |
| `vuln-authz.txt` | Step 3 "Trace backwards" (line ~135) |
| `vuln-misconfig.txt` | Step 3 "Validation Analysis" (line ~134) |

#### 3b. Conclusion Trigger Verification

Add one numbered item at the end of the `<conclusion_trigger>` numbered list, before the
`ONLY AFTER` paragraph:

```
3. Cross-Route Verification: For every vulnerability finding, confirm that `affected_routes` lists all routes sharing the same handler per Section 4.1 of the recon deliverable. Findings missing `affected_routes` or `authentication_required` are INCOMPLETE. Findings with `authentication_required: false` MUST have a corresponding pre-auth route in `affected_routes`.
```

| File | Insert Before |
|---|---|
| `vuln-xss.txt` | `ONLY AFTER both systematic analysis` paragraph (line ~291) |
| `vuln-injection.txt` | `ONLY AFTER both todo completion` paragraph (line ~370) |
| `vuln-ssrf.txt` | `ONLY AFTER both systematic analysis` paragraph (line ~308) |
| `vuln-auth.txt` | `ONLY AFTER both systematic analysis` paragraph (line ~259) |
| `vuln-authz.txt` | `ONLY AFTER both todo completion` paragraph (line ~364) |
| `vuln-misconfig.txt` | `ONLY AFTER both systematic analysis` paragraph (line ~284) |

**Exploit prompts are NOT modified.** The exploit phase weaponizes findings already broken
out per-route by the vuln phase.

## Files Changed

| File | Change | Lines Modified |
|---|---|---|
| `prompts/shared/_cross-route-enumeration.txt` | **New** — shared partial | N/A |
| `prompts/recon.txt` | Enhance Route Mapper task (line 135) | Modify 1 line |
| `prompts/recon.txt` | Add Section 4.1 template (after line 227) | Insert ~18 lines |
| `prompts/vuln-xss.txt` | `@include` in methodology + conclusion trigger item | Insert 2 lines |
| `prompts/vuln-injection.txt` | `@include` in methodology + conclusion trigger item | Insert 2 lines |
| `prompts/vuln-ssrf.txt` | `@include` in methodology + conclusion trigger item | Insert 2 lines |
| `prompts/vuln-auth.txt` | `@include` in methodology + conclusion trigger item | Insert 2 lines |
| `prompts/vuln-authz.txt` | `@include` in methodology + conclusion trigger item | Insert 2 lines |
| `prompts/vuln-misconfig.txt` | `@include` in methodology + conclusion trigger item | Insert 2 lines |

**Total:** 1 new file, 8 modified files. Only 1 existing line is modified (recon Route Mapper
task description at line 135). All other changes are pure insertions.

## Expected Outcome

For the concrete test case (`/preview/v2` and `/preview/iframe-demo`):

1. **Recon**: Route Mapper Agent detects that `/preview`, `/preview/v2`, `/preview/iframe-demo`
   share `controller.index.preview`. Section 4.1 output includes a group row flagging
   `/preview/iframe-demo` as pre-auth, with router definition `router.js:40-42` as
   cross-reference anchor.

2. **XSS agent**: Upon confirming `bizEntity` reflected XSS in the `preview()` handler:
   - Reaches documentation phase → triggers pre-documentation checklist (Step CR-1).
   - Reads Section 4.1 → finds the group row via handler match or router definition
     cross-reference (Step CR-2).
   - Enumerates routes by auth tier (Step CR-3).
   - Creates finding for `/preview` + `/preview/v2` (both `thirtyLogin` auth):
     `affected_routes: ["GET /preview (thirtyLogin)", "GET /preview/v2 (thirtyLogin)"]`,
     `authentication_required: true`
   - Creates **separate** finding for `/preview/iframe-demo` (no auth):
     `affected_routes: ["GET /preview/iframe-demo (none)"]`,
     `authentication_required: false`, `externally_exploitable: true`
   - Attaches MANDATORY fields to both findings (Step CR-4).
   - Conclusion trigger catches any finding missing `affected_routes` before completion.

3. **Exploit agent**: Sees two distinct findings, prioritizes the pre-auth variant.

## Determinism Chain: Four-Layer Enforcement

| Layer | Mechanism | What It Catches |
|---|---|---|
| **Recon data** | Route Mapper group detection + Section 4.1 structured table with router cross-reference | Ensures the data exists for downstream agents to consume |
| **Pre-doc checklist** | Step CR-1 through CR-4 in shared partial, explicitly gated before documentation | Agent cannot write a finding without completing the checklist |
| **Schema** | `affected_routes` + `authentication_required` MANDATORY fields with INCOMPLETE warning | Finding feels structurally incomplete without these fields |
| **Verification** | Conclusion trigger item 3 with specific field checks | Final safety net catches any omission before completion |

## Out of Scope

- Exploit prompt modifications.
- Structural changes to how prompt-manager.ts processes includes (already supports `@include`).
- Changes to `session-manager.ts` or `agent-execution.ts` (data flow is file-based, no code changes needed).
- Modifications to `<exploitation_queue_format>` sections (shared partial instructs agents to add fields alongside existing schema).
