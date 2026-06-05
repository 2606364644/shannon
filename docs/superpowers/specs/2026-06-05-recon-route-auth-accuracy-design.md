# Fix: Recon Route Authentication Classification Accuracy

**Date:** 2026-06-05
**Status:** Approved
**Scope:** Recon phase — Section 4.1 Shared Controller Route Groups format

## Problem

When multiple routes share the same handler function, the Recon agent incorrectly
"classifies" all routes as having the same authentication middleware, even when individual
routes lack middleware. This cascades to all downstream vulnerability agents (XSS, injection,
auth, authz, SSRF), causing them to mark pre-auth vulnerabilities as requiring authentication.

**Observed failure:** In the `ads_oa_fe` whitebox scan, `/preview/iframe-demo` has no
authentication middleware (confirmed in `router.js:42`), but the Recon deliverable's
Shared Controller Route Groups section (prompt template Section 4.1; agent renumbered to
Section 4.5 in output) classified it as `thirtyLogin` — identical to sibling routes
`/preview` and `/preview/v2`. The XSS agent then reported the reflected XSS as requiring
authentication, missing that it is a pre-auth (login-free) vulnerability.

**Root cause:** The current Section 4.1 format packs all routes of a shared handler group
into a single table cell. The Recon agent performs pattern-matching ("most routes in this
group use thirtyLogin, so they all do") instead of reading each router line individually.

## Solution: One-Row-Per-Route Format with Group Subsection Headers

Change the Section 4.1 table format from "one row per handler group" to "one row per route",
organized under group subsection headers.

### Before (current format)

```markdown
| Handler (file:line) | Router Definition | Routes (method + path) | Auth Middleware per Route |
|---|---|---|---|
| Index.preview (index.js:32) | router.js:40-43, 46 | GET /preview (thirtyLogin), GET /preview/v2 (thirtyLogin), GET /preview/iframe-demo (thirtyLogin) | All routes use thirtyLogin |
```

### After (new format)

```markdown
#### Group: Index.preview (index.js:32) — router.js:40-43, 46

| Method | Path | Auth Middleware | Router Line |
|---|---|---|---|
| GET | /preview | thirtyLogin() | router.js:40 |
| GET | /preview/v2 | thirtyLogin() | router.js:41 |
| GET | /preview/iframe-demo | **none** | router.js:42 |
| GET | /preview/preview-demo | thirtyLogin() | router.js:43 |
| GET | /mobile/my-campaign-list | thirtyLogin() | router.js:46 |

> ⚠️ `/preview/iframe-demo` has NO auth middleware — pre-auth variant.

---

#### Group: queryCampaignPage (ads.js:27) — router.js:60, 47

| Method | Path | Auth Middleware | Router Line |
|---|---|---|---|
| POST | /api/campaign/queryPage | oaLogin | router.js:60 |
| GET | /mobile-api/campaign/queryPage | thirtyLogin | router.js:47 |

> ⚠️ Different auth levels across routes — mobile route uses weaker authentication.
```

### Design principles

1. **Structural constraint over instruction:** The one-row-per-route format makes it
   structurally impossible to "uniformly classify" routes — each row forces individual
   verification.

2. **Preserve grouping semantics:** `#### Group:` subsection headers keep the handler
   grouping visible. Downstream agents locate groups by handler name + file:line, same as before.

3. **Router line citation:** The `Router Line` column forces the Recon agent to read each
   router line individually rather than inferring middleware from sibling routes.

4. **Visual alert for pre-auth routes:** `**none**` bold marking and `> ⚠️` warning blocks
   make pre-auth variants immediately visible to downstream agents.

## Files to Modify

### 1. `apps/worker/prompts/recon.txt`

**Change A — Route Mapper Agent instruction (line 137):**

Add explicit per-route output format requirement to the Route Mapper Agent task description:

```
For each group, produce a per-route table where EACH route gets its own row with:
- Method, Path, Auth Middleware (or "**none**" if absent), Router Line (exact line number).
Do NOT group multiple routes into a single cell — each route must be verified independently
by reading its exact router.js line.
```

**Change B — Section 4.1 template (lines 231-248):**

Replace the current one-row-per-group table format with the new group-subsection +
one-row-per-route format. Update the rules list to include:

- "Each row MUST correspond to exactly one route."
- "The Auth Middleware column must reflect the presence or absence of middleware in that
  specific router.js line — never infer from sibling routes."
- "When any route in a group has `**none**` auth, add a `> ⚠️` warning block below the table."

### 2. `apps/worker/prompts/recon-static.txt`

**Change — Section 4.1 template (lines 207-224):**

Apply the identical format change as `recon.txt`. The static variant shares the same
Section 4.1 structure.

### 3. `apps/worker/prompts/shared/_cross-route-enumeration.txt`

**Change A — Step CR-1 (lines 8-11):**

Add format description after "Read Section 4.1":

```
The section is organized as group subsections:
- Each group starts with `#### Group: HandlerName (file:line) — router.js:XX-YY`
- Below each group header is a table with one row per route
- Each row contains: Method, Path, Auth Middleware (or **none**), Router Line
```

**Change B — Step CR-2 (lines 14-22):**

Change "Find the row in Section 4.1" to "Find the `#### Group:` subsection in Section 4.1".
Add: "Read ALL rows in the matching group's table — each row is a separate route with its
own auth middleware."

**Change C — Self-check (line 46):**

Change "Section 4.1 group row" to "Section 4.1 group table".

## Files NOT Modified

- **6 vuln prompt files** (`vuln-xss.txt`, `vuln-injection.txt`, `vuln-auth.txt`,
  `vuln-authz.txt`, `vuln-ssrf.txt`, `vuln-misconfig.txt`) — their Cross-Route
  Verification instructions reference "Section 4.1" generically without depending on
  table structure.
- **`queue-schemas.ts`** — `authentication_required` and `affected_routes` are not in the
  formal schema but are enforced by prompt instructions. Schema enforcement is a separate
  improvement.
- **`findings-renderer.ts`** — Auth information renders from the `notes` field; format
  change has no rendering impact.

## Impact

- **All scans** using the recon → vuln pipeline benefit from more accurate auth classification.
- **No impact** on pre-recon, exploit, or report phases.
- **Token cost** may increase slightly in recon (more table rows), but the improvement in
  accuracy eliminates expensive false-negative re-scans.
- **Backward compatibility** is not a concern — recon deliverables are regenerated for each
  scan, so no old-format deliverables exist in the pipeline.

## Success Criteria

After implementing this change, a re-scan of `ads_oa_fe` should:
1. Section 4.1 shows `/preview/iframe-demo` as a separate row with `**none**` auth.
2. XSS agent creates a separate finding for the pre-auth variant with
   `authentication_required: false`.
3. The comprehensive report correctly identifies the iframe-demo XSS as pre-auth.
