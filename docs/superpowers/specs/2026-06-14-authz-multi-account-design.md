# Multi-Account Identity for Authorization Testing — Design Spec

## Problem

Shannon's authorization (`authz`) and authentication (`auth`) testing runs against a
**single** logged-in identity. The whole pipeline logs in once during preflight
(`validate-authentication.ts`), saves one browser session (`auth-state.json`), and every
agent shares it via `_shared-session.txt`.

For authorization testing this is a fundamental defect, not a convenience gap:

- **Horizontal (IDOR) cannot produce hard evidence.** To prove "user A accessed user B's
  data" you need B's data as a **baseline** — which requires logging in as B. With one
  account, the agent can only enumerate IDs and observe a `200`. It **cannot distinguish**
  "another user's private data" from "data A is legitimately allowed to see" or "public
  data". Findings default to weak `POTENTIAL` or wrong `EXPLOITED`.
- **Vertical escalation lacks a capability baseline.** To confirm "only admin should reach
  this", you need an admin session to establish what admin-only looks like. One account
  cannot.
- **The exploit prompts already assume multiple identities exist.** `exploit-authz.txt`'s
  Task Agent template requires `Identity set: [list of user IDs/tokens/roles]` and
  "DO NOT exceed 5 identities per run" — but the config layer supplies exactly **one**.
  The identity set is effectively empty.
- **One Playwright session cannot hold two identities at once.** `authz-vuln`/`authz-exploit`
  both map to `agent5`. Even with a second account, the agent must log out/in repeatedly,
  risking cookie/storage bleed-through, and `_shared-session.txt` forbids overwriting
  `AUTH_STATE_FILE`.

The analysis phase (`vuln-authz`, pure static) is unaffected — it traces code, not
sessions. The gap is in **dynamic verification** and in **role awareness** during static
analysis.

## Goal

Let operators supply multiple pre-configured accounts. Each identity lives in its own
persistent Playwright session during the exploitation phase, so agents can run the
"victim/baseline captures baseline ↔ attacker attacks → compare" protocol and produce
hard authorization evidence. The vuln phase consumes identities as **role context** (no
browser), improving `role_context` accuracy.

**Fully backward-compatible:** with no `accounts` configured, behavior is identical to
today.

### Non-Goals

- Automatic victim account registration (open-registration abuse, ROE risk, empty-account
  problem). Identities are operator-supplied.
- Multi-tenant isolation testing (tenant field). Single-tenant only for v1.
- A global identity layer for every agent. Scope is `authz` + `auth` only.
- Live verification in the vuln phase. Vuln stays static; it only reads role context.

## Key Decisions (confirmed)

| Decision | Choice | Rationale |
|---|---|---|
| Identity source | Manual config (`accounts[]`) | Controllable, non-invasive, compliance-clear |
| Coverage | Horizontal + Vertical, single-tenant | Covers the two core authz classes |
| Scope | `authz` + `auth` agents only | Focused ROI, avoids YAGNI global layer |
| Session strategy | **Plan A** — multi-slot, identities resident simultaneously | Eliminates the error-prone state-clear/switch choreography LLM agents do worst |
| Multi-session gating | vuln = role context only; exploit = multi-session comparison | Saves browsers where live access isn't needed; honors "No live exploitation in vuln" |
| Login failure | attacker required (fail-fast); victim/baseline degrade | Robust — one dead victim must not block the whole scan |
| Per-account login config | Inherit global `authentication` login config | Covers "same login page, different credentials" — the common case |

## Architecture

```
config accounts[] ──► preflight: login each identity into its own session
                       auth-state.json (attacker) + auth-state-{id}.json (victim/baseline)
                       attacker fail = stop; victim/baseline fail = mark unavailable
                              │
       ┌──────────────────────┴───────────────────────┐
       ▼                                              ▼
  vuln-authz / vuln-auth                         exploit-authz / exploit-auth
  (static: identities as role context,           (live: resident sessions,
   no victim/baseline sessions)                            comparison protocol)
       │                                              │
       ▼                                              ▼
  more accurate role_context                   hard IDOR / vertical evidence
```

## Design

### 1. Data Model & Config

**New `Account` type** (`apps/worker/src/types/config.ts`):

```ts
export type AccountUsage = 'victim' | 'baseline'; // attacker always = primary `authentication`

export interface Account {
  readonly id: string;            // slug-safe, unique; names auth-state-{id}.json
  readonly role: string;          // free-form (user/admin/viewer…); prompt context
  readonly usage: AccountUsage;
  readonly credentials: Credentials; // reuse existing credentials shape
}
```

`Config` gains `accounts?: Account[]`. `authentication` remains the primary account and is
implicitly `usage: 'attacker'`, `id: 'primary'` — this is what keeps legacy configs working.

**Config example** (`configs/example-config.yaml`):

```yaml
authentication:                 # primary = attacker (backward compatible)
  login_type: form
  login_url: "https://app.example.com/login"
  credentials: { username: userA, password: "***" }
  login_flow: [...]
  success_condition: { type: url_contains, value: /dashboard }

accounts:                       # optional additional identities
  - id: victim_b
    role: user
    usage: victim              # horizontal baseline; must own private resources
    credentials: { username: userB, password: "***" }
  - id: admin
    role: admin
    usage: baseline            # vertical baseline; high-privilege capability reference
    credentials: { username: admin, password: "***" }
  # all accounts inherit authentication.login_type/login_url/login_flow/success_condition
  # each account's credentials fill the $username/$password/$totp placeholders in login_flow
```

**Validation** (`config-parser.ts` `performSecurityValidation`):
- `id` must match `^[a-z0-9-]+$` and be unique across `accounts` (filename safety).
- `usage` must be `victim` or `baseline` (attacker is implicit, not allowed in `accounts`).
- If `accounts` is non-empty, `authentication` must be present (attacker is mandatory).
- Each `credentials.username` runs the existing dangerous-pattern check.

### 2. Identity Lifecycle (Preflight)

**`authStateFile(meta, accountId?)`** (`apps/worker/src/audit/utils.ts`) becomes
parameterized: no `accountId` → `auth-state.json` (primary, legacy path untouched);
with `accountId` → `auth-state-{accountId}.json`.

**`validateAuthentication`** (`apps/worker/src/services/validate-authentication.ts`) loops
over `[primary, ...accounts]`. For each identity it logs in using a **distinct session**
(so login states never collide), saves the matching auth-state file, and runs
`verifySavedAuthState` (cookies/origins non-empty).

**Failure handling:**
- **attacker (primary) login fails → fail-fast.** No authenticated session = no auth testing.
- **victim/baseline login fails → log warning, mark that identity unavailable, continue.**
  The unavailable identity is omitted from the rendered identity manifest (Section 4), and
  the relevant comparison protocol degrades (see below).

**Cleanup:** `logWorkflowComplete` in `temporal/activities.ts` currently deletes
`auth-state.json`; it now globs `auth-state*.json` so every identity snapshot is purged
between scans.

### 3. Session Mapping & Multi-Slot

**Why 4 new slots:** during exploitation, `authz-exploit` and `auth-exploit` run **in
parallel**. If their victim/baseline shared one session name they would navigate over each
other. The same account's auth-state file is one file, but each agent loads it into its
**own** session. `authz` and `auth` share the same configured `accounts`; the snapshot is
one file per account, loaded into a separate session per agent.

**Extend `PlaywrightSession`** (`apps/worker/src/types/agents.ts:39`) with
`agent7 | agent8 | agent9 | agent10`:

| agent | attacker | victim | baseline |
|---|---|---|---|
| `exploit-authz` | agent5 (existing) | agent7 | agent8 |
| `exploit-auth` | agent3 (existing) | agent9 | agent10 |

`PLAYWRIGHT_SESSION_MAPPING` (`session-manager.ts`) value type widens from
`PlaywrightSession` to `PlaywrightSession | { attacker: PlaywrightSession; victim?: PlaywrightSession; baseline?: PlaywrightSession }`. Plain single values keep working (backward compat).

**`prompt-manager.ts`**: when an agent has multiple identities, inject
`{{SESSION_ATTACKER}}` / `{{SESSION_VICTIM}}` / `{{SESSION_BASELINE}}` instead of the single
`{{PLAYWRIGHT_SESSION}}`. With no `accounts`, it falls back to the single variable — old
prompts render unchanged.

**Resource cost:** peak ~10 concurrent chromium during exploitation (authz 3 + auth 3 +
four other exploit agents × 1). Mitigation: the comparison protocol instructs agents to
close a session's browser when not actively comparing, keeping the auth-state file for
on-demand reopen.

### 4. Prompts & Comparison Protocol

**New shared partial `apps/worker/prompts/shared/_identities.txt`** (renders the identity
manifest; supersedes `_shared-session.txt` for multi-account agents):

```
<available_identities>
Preflight has logged in and saved a session for each identity below.
Unavailable identities (login failed) are omitted.

| id (account) | role | usage | session | auth-state |
| primary (userA) | user | attacker | {{SESSION_ATTACKER}} | auth-state.json |
| victim_b (userB) | user | victim | {{SESSION_VICTIM}} | auth-state-victim_b.json |
| admin | admin | baseline | {{SESSION_BASELINE}} | auth-state-admin.json |

Before use, restore the session: playwright-cli -s=<session> state-load <auth-state>
</available_identities>
```

**Exploit phase (real comparison)** — `exploit-authz.txt` / `exploit-auth.txt` get a
comparison-protocol block:

- **Horizontal (IDOR):** ① victim session reads its own resource `/orders/42` → baseline
  data; ② attacker session reads the same `/orders/42`; ③ data matches ⇒ confirmed
  `EXPLOITED`. If **victim unavailable** (degraded) ⇒ fall back to "enumerate ID, observe
  200" weak verification, forced `POTENTIAL`.
- **Vertical:** ① attacker session hits the admin endpoint; ② baseline (admin) session
  confirms the endpoint is genuinely admin-only capability ⇒ confirmed. Baseline
  unavailable ⇒ "low-priv can reach it" only, `POTENTIAL`.

**Vuln phase (context only)** — `vuln-authz.txt` / `vuln-auth.txt` receive the identity
manifest's **role columns** (no victim/baseline session variables, no comparison protocol). This turns `role_context`
from "guessed" into "known user/admin roles exist", directly improving which-role-can-
trigger accuracy. No live access is added — vuln stays static.

**`validate-authentication.txt`** is loop-aware: the prompt is rendered per-identity with
that identity's session and target auth-state file, then the loop driver in
`validateAuthentication` reuses the existing login/verify logic per iteration.

### 5. Error Handling, Degradation, Backward Compatibility

- **No `accounts`** → entire pipeline equivalent to today (single attacker, single session,
  single auth-state).
- **`exploit: false`** → multi-session never opens; only vuln consumes role context.
- **victim owns no private data** → operator prerequisite; the protocol prompts the agent:
  "no comparable resource ⇒ horizontal cannot be substantiated, mark POTENTIAL".
- **Resume** → auth-state files live in the workspace; preflight `rm`s and rebuilds them,
  so resume is naturally correct.
- **`exactOptionalPropertyTypes`** → optional account fields use spread, never direct
  `undefined` assignment (codebase convention).

## Merge Compatibility Strategy

This repo is a fork (`feat/fork`) that rebases on upstream Shannon. Several touched files
are upstream-active, so changes are structured to minimize conflict surface:

- **New files (zero conflict):** `prompts/shared/_identities.txt`.
- **Prompt edits (insert-only, like the cross-route-enumeration pattern):** each of
  `exploit-authz/exploit-auth/vuln-authz/vuln-auth/validate-authentication.txt` gets
  `@include(shared/_identities.txt)` + protocol text as **insertions at stable anchors**,
  modifying no existing lines. `pipeline-testing/` mirrors get the same insertions.
- **Schema/config (additive):** `accounts` is a new optional property in
  `config-schema.json` / `example-config.yaml` — additive, low conflict.
- **Core logic (higher conflict risk, documented):** `types/agents.ts`,
  `session-manager.ts`, `prompt-manager.ts`, `validate-authentication.ts`,
  `audit/utils.ts`, `config-parser.ts`, `temporal/activities.ts`. Each change is localized
  (type widening, parameterized function, single new loop) so a rebase conflict is a
  small, semantically clear diff rather than a rewrite. `authStateFile` keeps its existing
  single-arg call sites working via an optional second argument.

## Files Changed

| File | Change | Type |
|---|---|---|
| `prompts/shared/_identities.txt` | Identity manifest partial | **New** |
| `types/config.ts` | `Account`, `AccountUsage`; `Config.accounts` | Modify |
| `configs/config-schema.json` | `accounts` array schema (additive) | Modify |
| `configs/example-config.yaml` | `accounts` example (additive) | Modify |
| `config-parser.ts` | `sanitizeAccount`, distribution, account validation | Modify |
| `types/agents.ts` | `PlaywrightSession` += `agent7..agent10` | Modify |
| `session-manager.ts` | `PLAYWRIGHT_SESSION_MAPPING` multi-session values | Modify |
| `audit/utils.ts` | `authStateFile(meta, accountId?)` | Modify |
| `services/validate-authentication.ts` | per-identity login loop + degradation | Modify |
| `services/prompt-manager.ts` | multi-session var injection + manifest render | Modify |
| `temporal/activities.ts` | `logWorkflowComplete` globs `auth-state*.json`; preflight passes accounts | Modify |
| `prompts/exploit-authz.txt` | `@include` + comparison protocol (insert-only) | Insert |
| `prompts/exploit-auth.txt` | `@include` + comparison protocol (insert-only) | Insert |
| `prompts/vuln-authz.txt` | role-context manifest (insert-only) | Insert |
| `prompts/vuln-auth.txt` | role-context manifest (insert-only) | Insert |
| `prompts/validate-authentication.txt` | per-identity session/state targets | Modify |
| `prompts/pipeline-testing/*` | mirror insertions | Insert |

**Note:** the implementation plan must verify `playwright-cli` (MCP server) accepts
`agent7..agent10` as session names. All evidence indicates `agent1..6` are a Shannon
naming convention (`-s=<任意字符串>`; `output-formatters.ts` matches `-s=\S+`), not an
MCP-side limit — but this is the one assumption to confirm with a smoke check before
relying on it.

## Testing

- **`config-parser` unit:** valid `accounts`; invalid (duplicate `id`, bad slug, wrong
  `usage`, `accounts` without `authentication`).
- **`validate-authentication`:** all identities succeed; victim fails → degrades;
  attacker fails → fail-fast.
- **`authStateFile`:** parameterized naming correct; legacy call sites unchanged.
- **`prompt-manager`:** multi-session variables injected when `accounts` present; single
  variable regression when absent; manifest rendering.
- **End-to-end:** `--pipeline-testing` run with a 2-account config (attacker + victim)
  produces a comparison-protocol exploit attempt; mirror changes in
  `prompts/pipeline-testing/`.

## Risks

- **Concurrent browser cost** (~10 chromium at exploit peak). Mitigated by on-demand
  session close in the protocol.
- **Operator must seed victim with private data.** Documented as a prerequisite; the
  protocol degrades gracefully otherwise.
- **`playwright-cli` session-name assumption** — the single item to smoke-check first.

## Out of Scope / Future Extensions

- Automatic victim registration (ROE-sensitive, empty-data problem).
- Multi-tenant `tenant` field and cross-tenant isolation testing.
- Per-account `login_url`/`login_flow`/`success_condition` override (current: inherit
  global — add when a real target needs a separate admin login entry).
- Extending the identity layer beyond `authz` + `auth`.
