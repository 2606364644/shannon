# Multi-Account Identity for Authorization Testing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let operators supply multiple pre-configured accounts (victim/baseline) so `authz`/`auth` exploit agents can run victim/baseline-vs-attacker comparison for hard IDOR/vertical-escalation evidence, instead of single-account enumeration.

**Architecture:** New optional `accounts[]` in config. Preflight logs in every identity into its own session and saves per-identity `auth-state-{id}.json` plus an `auth-identities.json` availability manifest. `PlaywrightSession` extends to `agent7..agent10`; `exploit-authz`/`exploit-auth` each get resident victim/baseline slots. A new `_identities.txt` partial renders the live identity table; exploit prompts run the comparison protocol, vuln prompts consume role context only. Fully backward-compatible (no `accounts` ⇒ today's behavior).

**Tech Stack:** TypeScript (strict, `exactOptionalPropertyTypes`), AJV + js-yaml config, Biome lint/format, `@anthropic-ai/claude-agent-sdk`, Temporal activities, file-based data flow between preflight and agents.

**Spec:** `docs/superpowers/specs/2026-06-14-authz-multi-account-design.md`

---

## Verification Strategy (read first — this project has no unit-test framework)

`apps/worker/package.json` has **no test runner** (only `build`/`check`/`clean`; no vitest/jest in devDependencies). This codebase verifies via type-checking, lint, and end-to-end pipeline runs. **Do not introduce a test framework in this plan** — follow the existing pattern. Each task verifies with:

1. `pnpm run check` — `tsc --noEmit` across all packages (MUST be clean before every commit).
2. `pnpm biome` — lint + format + import-order check (run `pnpm biome:fix` to auto-fix).
3. End-to-end `--pipeline-testing` run at the final task (and after any prompt change that could break rendering).

Commit after each task with conventional-commit messages. Run all commands from the repo root unless noted.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `apps/worker/src/types/config.ts` | `Account`, `AccountUsage` types; `Config.accounts`, `DistributedConfig.accounts` | Modify |
| `apps/worker/src/types/agents.ts` | `PlaywrightSession` union widened to `agent1..agent10` | Modify |
| `apps/worker/configs/config-schema.json` | `accounts[]` JSON schema (additive, credentials inlined) | Modify |
| `apps/worker/src/config-parser.ts` | `sanitizeAccount`, distribute accounts, account validation | Modify |
| `apps/worker/src/audit/utils.ts` | `authStateFile(meta, accountId?)` + `authIdentitiesFile(meta)` | Modify |
| `apps/worker/src/session-manager.ts` | `SessionAssignment` type; multi-session mapping for exploit-authz/auth | Modify |
| `apps/worker/src/services/prompt-manager.ts` | Multi-session vars, identity manifest rendering, `{{IDENTITIES}}`/`{{IDENTITY_ROLES}}` | Modify |
| `apps/worker/prompts/shared/_identities.txt` | Identity manifest partial (exploit agents) | **Create** |
| `apps/worker/src/services/validate-authentication.ts` | Per-identity login loop, degradation, write `auth-identities.json` | Modify |
| `apps/worker/src/temporal/activities.ts` | `logWorkflowComplete` globs `auth-state*.json` + manifest | Modify |
| `apps/worker/prompts/exploit-authz.txt` | `@include` `_identities.txt` (replaces `_shared-session.txt`) | Modify (insert) |
| `apps/worker/prompts/exploit-auth.txt` | `@include` `_identities.txt` (replaces `_shared-session.txt`) | Modify (insert) |
| `apps/worker/prompts/vuln-authz.txt` | `{{IDENTITY_ROLES}}` role-context injection point | Modify (insert) |
| `apps/worker/prompts/vuln-auth.txt` | `{{IDENTITY_ROLES}}` role-context injection point | Modify (insert) |
| `apps/worker/prompts/pipeline-testing/{exploit-authz,exploit-auth,vuln-authz,vuln-auth}.txt` | Mirror insertions | Modify (insert) |
| `apps/worker/configs/example-config.yaml` | `accounts` example | Modify |

---

## Task 1: Add `Account` types and widen `PlaywrightSession`

**Files:**
- Modify: `apps/worker/src/types/config.ts`
- Modify: `apps/worker/src/types/agents.ts:39`

- [ ] **Step 1: Add account types to `types/config.ts`**

Insert after the `Authentication` interface (after line 64) and before `Config`:

```ts
export type AccountUsage = 'victim' | 'baseline';

/**
 * An additional authenticated identity for authorization testing.
 * Inherits login_type/login_url/login_flow/success_condition from `authentication`;
 * only `credentials` differ. `attacker` is always the primary `authentication`.
 */
export interface Account {
  readonly id: string;
  readonly role: string;
  readonly usage: AccountUsage;
  readonly credentials: Credentials;
}
```

Add `accounts?: Account[];` to the `Config` interface (after `authentication?: Authentication;`).

Add `accounts: Account[];` to the `DistributedConfig` interface (after `authentication: Authentication | null;`).

- [ ] **Step 2: Widen `PlaywrightSession` in `types/agents.ts`**

Replace line 39:

```ts
export type PlaywrightSession =
  | 'agent1'
  | 'agent2'
  | 'agent3'
  | 'agent4'
  | 'agent5'
  | 'agent6'
  | 'agent7'
  | 'agent8'
  | 'agent9'
  | 'agent10';
```

- [ ] **Step 3: Verify type-check passes**

Run: `pnpm run check`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add apps/worker/src/types/config.ts apps/worker/src/types/agents.ts
git commit -m "feat(authz): add Account types and widen PlaywrightSession to agent10"
```

---

## Task 2: Add `accounts[]` to the config JSON schema

**Files:**
- Modify: `apps/worker/configs/config-schema.json`

The `credentials` definition is inlined (duplicated from `authentication.credentials`) to avoid touching the existing `authentication` block — keeps this purely additive for merge safety.

- [ ] **Step 1: Add the `accounts` property**

Inside `properties` (alongside `authentication`), add:

```json
"accounts": {
  "type": "array",
  "description": "Additional authenticated identities (victim/baseline) for authorization testing. Each inherits login_type/login_url/login_flow/success_condition from 'authentication'.",
  "items": {
    "type": "object",
    "properties": {
      "id": {
        "type": "string",
        "pattern": "^[a-z0-9-]+$",
        "minLength": 1,
        "maxLength": 64,
        "description": "Slug-safe unique identifier; names auth-state-{id}.json"
      },
      "role": {
        "type": "string",
        "minLength": 1,
        "maxLength": 64,
        "description": "Free-form role label (user/admin/viewer...) for prompt context"
      },
      "usage": {
        "type": "string",
        "enum": ["victim", "baseline"],
        "description": "victim = horizontal IDOR baseline (owns private resources); baseline = vertical high-privilege reference"
      },
      "credentials": {
        "type": "object",
        "description": "Login credentials (same shape as authentication.credentials)",
        "properties": {
          "username": { "type": "string", "minLength": 1, "maxLength": 255 },
          "password": { "type": "string", "minLength": 1, "maxLength": 255 },
          "totp_secret": { "type": "string", "pattern": "^[A-Za-z2-7]+=*$" },
          "email_login": {
            "type": "object",
            "properties": {
              "address": { "type": "string", "format": "email" },
              "password": { "type": "string", "minLength": 1, "maxLength": 255 },
              "totp_secret": { "type": "string", "pattern": "^[A-Za-z2-7]+=*$" }
            },
            "required": ["address", "password"],
            "additionalProperties": false
          }
        },
        "required": ["username"],
        "additionalProperties": false
      }
    },
    "required": ["id", "role", "usage", "credentials"],
    "additionalProperties": false
  },
  "maxItems": 10,
  "uniqueItems": true
}
```

- [ ] **Step 2: Verify no regressions**

Run: `pnpm run check`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add apps/worker/configs/config-schema.json
git commit -m "feat(config): add accounts[] schema for multi-account identities"
```

---

## Task 3: Parse, sanitize, and validate `accounts`

**Files:**
- Modify: `apps/worker/src/config-parser.ts` (`sanitizeAuthentication` ~line 697, `distributeConfig` ~line 666, `performSecurityValidation` ~line 401)

- [ ] **Step 1: Add `sanitizeAccount`**

Add `Account` to the import from `./types/config.js` (lines 14-19). After `sanitizeAuthentication` (after line 721), add:

```ts
const sanitizeAccount = (account: Account): Account => ({
  id: account.id.trim(),
  role: account.role.trim(),
  usage: account.usage,
  credentials: {
    username: account.credentials.username.trim(),
    ...(account.credentials.password && { password: account.credentials.password }),
    ...(account.credentials.totp_secret && { totp_secret: account.credentials.totp_secret.trim() }),
    ...(account.credentials.email_login && {
      email_login: {
        address: account.credentials.email_login.address.trim(),
        password: account.credentials.email_login.password,
        ...(account.credentials.email_login.totp_secret && {
          totp_secret: account.credentials.email_login.totp_secret.trim(),
        }),
      },
    }),
  },
});
```

- [ ] **Step 2: Distribute accounts in `distributeConfig`**

In `distributeConfig` (line 666), add after `const authentication = config?.authentication || null;`:

```ts
const accounts = config?.accounts ?? [];
```

In the returned object, add:

```ts
accounts: accounts.map(sanitizeAccount),
```

- [ ] **Step 3: Validate accounts in `performSecurityValidation`**

After the `if (config.authentication) { ... }` block (after line 449), add:

```ts
if (config.accounts && config.accounts.length > 0) {
  if (!config.authentication) {
    throw new PentestError(
      `'accounts' requires 'authentication' — the primary account is the attacker and must be present.`,
      'config',
      false,
      {},
      ErrorCode.CONFIG_VALIDATION_FAILED,
    );
  }

  const seenIds = new Set<string>();
  config.accounts.forEach((account, index) => {
    if (!/^[a-z0-9-]+$/.test(account.id)) {
      throw new PentestError(
        `accounts[${index}].id '${account.id}' must be slug-safe (lowercase letters, digits, hyphens only).`,
        'config',
        false,
        { field: `accounts[${index}].id`, value: account.id },
        ErrorCode.CONFIG_VALIDATION_FAILED,
      );
    }
    if (seenIds.has(account.id)) {
      throw new PentestError(
        `Duplicate account id '${account.id}' in accounts[${index}].`,
        'config',
        false,
        { field: `accounts[${index}].id`, value: account.id },
        ErrorCode.CONFIG_VALIDATION_FAILED,
      );
    }
    seenIds.add(account.id);

    for (const pattern of DANGEROUS_PATTERNS) {
      if (pattern.test(account.credentials.username)) {
        throw new PentestError(
          `accounts[${index}].credentials.username contains potentially dangerous pattern: ${pattern.source}`,
          'config',
          false,
          { field: `accounts[${index}].credentials.username`, pattern: pattern.source },
          ErrorCode.CONFIG_VALIDATION_FAILED,
        );
      }
    }
  });
}
```

- [ ] **Step 4: Verify type-check and lint**

Run: `pnpm run check && pnpm biome:fix`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/src/config-parser.ts
git commit -m "feat(config): parse, sanitize, and validate accounts[]"
```

---

## Task 4: Parameterize `authStateFile` and add `authIdentitiesFile`

**Files:**
- Modify: `apps/worker/src/audit/utils.ts:81`

- [ ] **Step 1: Update `authStateFile` and add `authIdentitiesFile`**

Replace the `authStateFile` function (lines 78-83) with:

```ts
/**
 * Path to an authenticated browser session snapshot.
 * Without accountId: the primary attacker session (legacy `auth-state.json`).
 * With accountId: a per-identity snapshot `auth-state-{accountId}.json`.
 */
export function authStateFile(sessionMetadata: SessionMetadata, accountId?: string): string {
  const filename = accountId ? `auth-state-${accountId}.json` : 'auth-state.json';
  return path.join(generateAuditPath(sessionMetadata), filename);
}

/**
 * Path to the runtime identity availability manifest written by preflight and
 * consumed by prompt-manager when rendering the identity table. Records which
 * identities logged in successfully so failed ones can be omitted/degraded.
 */
export function authIdentitiesFile(sessionMetadata: SessionMetadata): string {
  return path.join(generateAuditPath(sessionMetadata), 'auth-identities.json');
}
```

- [ ] **Step 2: Verify existing call sites still compile**

Run: `pnpm run check`
Expected: no errors (the optional 2nd arg keeps all existing single-arg calls valid).

- [ ] **Step 3: Commit**

```bash
git add apps/worker/src/audit/utils.ts
git commit -m "feat(audit): parameterize authStateFile by accountId, add authIdentitiesFile"
```

---

## Task 5: Multi-session mapping + prompt-manager session support

**Files:**
- Modify: `apps/worker/src/session-manager.ts:176`
- Modify: `apps/worker/src/services/prompt-manager.ts` (`PromptVariables` ~line 130, `loadPrompt` ~line 532-542, `interpolateVariables` ~line 381/409)

The mapping type change and its consumer (`prompt-manager.ts`) ship in one commit so `pnpm run check` stays green. This task also fixes a **correctness requirement**: `loadPrompt` must honor a caller-supplied `PLAYWRIGHT_SESSION` (preflight logs each identity into its own session); without this, every identity's login collapses onto `agent1` and sessions bleed together.

- [ ] **Step 1: Add `SessionAssignment` and update the mapping** (`session-manager.ts`)

Above `PLAYWRIGHT_SESSION_MAPPING` (line 174), add:

```ts
/**
 * Single session (most agents) or an attacker/victim/baseline triple for
 * multi-account exploit agents. victim/baseline are optional — absent when
 * those identities aren't configured or failed to log in.
 */
export type SessionAssignment =
  | PlaywrightSession
  | {
      readonly attacker: PlaywrightSession;
      readonly victim?: PlaywrightSession;
      readonly baseline?: PlaywrightSession;
    };
```

Change the mapping's type and the two exploit entries:

```ts
export const PLAYWRIGHT_SESSION_MAPPING: Record<string, SessionAssignment> = Object.freeze({
  // Runs before any agent — non-concurrent, so agent1 is safe to share
  'validate-authentication': 'agent1',

  // Phase 1: Pre-reconnaissance
  'pre-recon-code': 'agent1',

  // Phase 2: Reconnaissance
  recon: 'agent2',
  'recon-static': 'agent2',

  // Phase 3: Vulnerability Analysis (6 parallel agents) — single session each;
  // vuln agents consume identities as role context only, no victim/baseline slots.
  'vuln-injection': 'agent1',
  'vuln-xss': 'agent2',
  'vuln-auth': 'agent3',
  'vuln-ssrf': 'agent4',
  'vuln-authz': 'agent5',
  'vuln-misconfig': 'agent6',

  // Phase 4: Exploitation — authz and auth get resident victim/baseline slots so
  // identities stay online simultaneously without state-clear/switch choreography.
  'exploit-injection': 'agent1',
  'exploit-xss': 'agent2',
  'exploit-auth': { attacker: 'agent3', victim: 'agent9', baseline: 'agent10' },
  'exploit-ssrf': 'agent4',
  'exploit-authz': { attacker: 'agent5', victim: 'agent7', baseline: 'agent8' },
  'exploit-misconfig': 'agent6',

  // Phase 5: Reporting
  'report-executive': 'agent3',
});
```

- [ ] **Step 2: Extend `PromptVariables`** (`prompt-manager.ts`)

Add `import type { SessionAssignment } from '../session-manager.js';` at the top. Replace the `PromptVariables` interface (lines 130-135):

```ts
interface PromptVariables {
  webUrl?: string;
  repoPath: string;
  AUTH_STATE_FILE: string;
  PLAYWRIGHT_SESSION?: string;
  SESSION_ATTACKER?: string;
  SESSION_VICTIM?: string;
  SESSION_BASELINE?: string;
}
```

- [ ] **Step 3: Resolve multi-session assignments in `loadPrompt` (honor caller-supplied session)**

Replace the session-assignment block (lines 532-542):

```ts
// 2. Assign Playwright session(s). A caller-supplied PLAYWRIGHT_SESSION wins
//    (per-identity preflight login pins its own session); otherwise derive from mapping.
const enhancedVariables: PromptVariables = { ...variables };
const assignment = PLAYWRIGHT_SESSION_MAPPING[promptName as keyof typeof PLAYWRIGHT_SESSION_MAPPING];

if (enhancedVariables.PLAYWRIGHT_SESSION) {
  // Caller pinned the session — keep it as-is (used by per-identity preflight login).
} else if (typeof assignment === 'string') {
  enhancedVariables.PLAYWRIGHT_SESSION = assignment;
} else if (assignment) {
  enhancedVariables.PLAYWRIGHT_SESSION = assignment.attacker;
  enhancedVariables.SESSION_ATTACKER = assignment.attacker;
  if (assignment.victim) enhancedVariables.SESSION_VICTIM = assignment.victim;
  if (assignment.baseline) enhancedVariables.SESSION_BASELINE = assignment.baseline;
  logger.info(
    `Assigned ${promptName} -> attacker=${assignment.attacker}` +
      `${assignment.victim ? `, victim=${assignment.victim}` : ''}` +
      `${assignment.baseline ? `, baseline=${assignment.baseline}` : ''}`,
  );
} else {
  enhancedVariables.PLAYWRIGHT_SESSION = 'agent1';
  logger.warn(`Unknown agent ${promptName}, using fallback -> ${enhancedVariables.PLAYWRIGHT_SESSION}`);
}
```

- [ ] **Step 4: Thread `currentAssignment` and replace session vars in `interpolateVariables`**

Add `currentAssignment?: SessionAssignment` as the last parameter of `interpolateVariables` (line 381). Pass it from `loadPrompt` (line 551):

```ts
return await interpolateVariables(template, enhancedVariables, config, logger, basePromptsDir, assignment);
```

Add multi-session variable replacement near line 409 (after the `{{PLAYWRIGHT_SESSION}}` replace):

```ts
result = result
  .replace(/{{SESSION_ATTACKER}}/g, variables.SESSION_ATTACKER || variables.PLAYWRIGHT_SESSION || 'agent1')
  .replace(/{{SESSION_VICTIM}}/g, variables.SESSION_VICTIM || '')
  .replace(/{{SESSION_BASELINE}}/g, variables.SESSION_BASELINE || '');
```

- [ ] **Step 5: Verify type-check and lint**

Run: `pnpm run check && pnpm biome:fix`
Expected: no errors (mapping change and its consumer ship together).

- [ ] **Step 6: Commit**

```bash
git add apps/worker/src/session-manager.ts apps/worker/src/services/prompt-manager.ts
git commit -m "feat(authz): multi-session mapping + prompt-manager session support"
```

---

## Task 6: Identity manifest rendering

**Files:**
- Modify: `apps/worker/src/services/prompt-manager.ts` (manifest helpers + `{{IDENTITIES}}`/`{{IDENTITY_ROLES}}` wiring)

Session plumbing is in place from Task 5. This task adds the runtime identity table exploit agents display (`{{IDENTITIES}}`) and the role-only summary vuln agents consume (`{{IDENTITY_ROLES}}`).

- [ ] **Step 1: Add manifest rendering helpers**

Add this import (the manifest filename is a literal matching `authIdentitiesFile` in audit/utils.ts and the cleanup glob in activities.ts — kept as a string here to avoid threading `SessionMetadata` through the renderer):

```ts
import type { Account, AccountUsage } from '../types/config.js';
```

Add these helpers above `interpolateVariables`:

```ts
interface AvailableIdentity {
  readonly id: string;
  readonly role: string;
  readonly usage: AccountUsage;
  readonly authState: string;
}

async function readAvailableIdentities(authStateFilePath: string): Promise<AvailableIdentity[] | null> {
  if (!authStateFilePath) return null;
  const manifestPath = path.join(path.dirname(authStateFilePath), 'auth-identities.json');
  try {
    const parsed = JSON.parse(await fs.readFile(manifestPath, 'utf8')) as unknown;
    return Array.isArray(parsed) ? (parsed as AvailableIdentity[]) : null;
  } catch {
    // Manifest absent (e.g. no authentication configured) — caller falls back to config-only.
    return null;
  }
}

function sessionForUsage(
  assignment: SessionAssignment | undefined,
  usage: 'attacker' | 'victim' | 'baseline',
): string | undefined {
  if (!assignment || typeof assignment === 'string') {
    return usage === 'attacker' ? (assignment ?? 'agent1') : undefined;
  }
  if (usage === 'attacker') return assignment.attacker;
  if (usage === 'victim') return assignment.victim;
  return assignment.baseline;
}

function inferPrimaryRole(config: DistributedConfig): string {
  const victim = (config.accounts ?? []).find((a) => a.usage === 'victim');
  return victim ? victim.role : 'user';
}

async function renderIdentityManifest(
  authStateFilePath: string,
  config: DistributedConfig | null,
  assignment: SessionAssignment | undefined,
): Promise<string> {
  if (!config?.authentication) return 'Single unauthenticated context (no identities).';
  const available = await readAvailableIdentities(authStateFilePath);
  const availableIds = new Set((available ?? []).map((i) => i.id));

  const rows: Array<{ id: string; role: string; usage: string; session: string; authState: string }> = [];
  if (available === null || availableIds.has('primary')) {
    rows.push({
      id: 'primary',
      role: inferPrimaryRole(config),
      usage: 'attacker',
      session: sessionForUsage(assignment, 'attacker') ?? 'agent1',
      authState: authStateFilePath,
    });
  }
  for (const account of config.accounts) {
    if (available !== null && !availableIds.has(account.id)) continue; // degraded — omit
    rows.push({
      id: account.id,
      role: account.role,
      usage: account.usage,
      session: sessionForUsage(assignment, account.usage) ?? '(unassigned)',
      authState: available?.find((i) => i.id === account.id)?.authState ?? '',
    });
  }
  if (rows.length === 0) return 'No identities available (all logins failed).';
  const body = rows
    .map((r) => `| ${r.id} | ${r.role} | ${r.usage} | ${r.session} | ${path.basename(r.authState)} |`)
    .join('\n');
  return `| id (account) | role | usage | session | auth-state |\n|---|---|---|---|---|\n${body}`;
}

async function renderIdentityRoles(authStateFilePath: string, config: DistributedConfig | null): Promise<string> {
  if (!config?.authentication) return 'No roles configured (unauthenticated target).';
  const available = await readAvailableIdentities(authStateFilePath);
  const availableIds = new Set((available ?? []).map((i) => i.id));
  const roles: Array<{ id: string; role: string; usage: string }> = [];
  if (available === null || availableIds.has('primary')) {
    roles.push({ id: 'primary', role: inferPrimaryRole(config), usage: 'attacker' });
  }
  for (const account of config.accounts) {
    if (available !== null && !availableIds.has(account.id)) continue;
    roles.push({ id: account.id, role: account.role, usage: account.usage });
  }
  if (roles.length === 0) return 'No roles available (all logins failed).';
  return roles.map((r) => `- ${r.id}: role=${r.role}, usage=${r.usage}`).join('\n');
}
```

- [ ] **Step 2: Wire the manifest into `interpolateVariables`**

After the `{{AUTH_STATE_FILE}}` block (around line 444), add (uses `currentAssignment` threaded in Task 5):

```ts
if (result.includes('{{IDENTITIES}}')) {
  const manifest = await renderIdentityManifest(variables.AUTH_STATE_FILE, config, currentAssignment);
  result = result.replace(/{{IDENTITIES}}/g, manifest);
}
if (result.includes('{{IDENTITY_ROLES}}')) {
  const roles = await renderIdentityRoles(variables.AUTH_STATE_FILE, config);
  result = result.replace(/{{IDENTITY_ROLES}}/g, roles);
}
```

- [ ] **Step 3: Verify type-check and lint**

Run: `pnpm run check && pnpm biome:fix`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add apps/worker/src/services/prompt-manager.ts
git commit -m "feat(prompt): render identity manifest for authz/auth agents"
```

---

## Task 7: Create the `_identities.txt` partial

**Files:**
- Create: `apps/worker/prompts/shared/_identities.txt`

- [ ] **Step 1: Write the partial**

```
<available_identities>
Preflight has logged in and saved a session for each identity below.
Unavailable identities (login failed) are omitted. Before using an identity,
restore its session:

  playwright-cli -s=<session> state-load <auth-state>

Identities:
{{IDENTITIES}}

Comparison Protocol (how to produce hard authorization evidence):

- Horizontal (IDOR): with the VICTIM session, read one of the victim's own
  resources (e.g. GET /orders/42) and record the response as the BASELINE.
  Then with the ATTACKER session, request the SAME resource. If the attacker
  sees the victim's data, authorization is broken — classify EXPLOITED.
  If NO victim identity is available, you CANNOT establish a baseline: fall
  back to identifier enumeration and classify the finding POTENTIAL only.

- Vertical: with the ATTACKER session, hit a privileged (admin) endpoint.
  Then with the BASELINE (admin) session, confirm the endpoint is genuinely
  admin-only capability. If the attacker succeeds, classify EXPLOITED.
  If NO baseline identity is available, you can only show a low-privilege
  account reached the endpoint — classify POTENTIAL only.

Do NOT mix sessions. Each identity's cookies/storage live only in its own
session; loading a different auth-state into the wrong session corrupts it.
</available_identities>
```

- [ ] **Step 2: Commit**

```bash
git add apps/worker/prompts/shared/_identities.txt
git commit -m "feat(prompt): add _identities.txt partial with comparison protocol"
```

---

## Task 8: Per-identity preflight login loop with degradation

**Files:**
- Modify: `apps/worker/src/services/validate-authentication.ts`

This is the largest change. The existing single-login logic is extracted into `loginOneIdentity`, and `validateAuthentication` loops over `[primary, ...accounts]`.

- [ ] **Step 1: Add imports and types**

At the top, add to imports:

```ts
import { writeFile } from 'node:fs/promises';
import { authIdentitiesFile } from '../audit/utils.js';
import type { Account } from '../types/config.js';
```

Add the identity and availability types below the existing type declarations:

```ts
interface IdentityToLogin {
  readonly id: string;
  readonly role: string;
  readonly usage: 'attacker' | 'victim' | 'baseline';
  readonly credentials: Account['credentials'];
  readonly session: string;
  readonly authState: string;
}

interface AvailableIdentityRecord {
  readonly id: string;
  readonly role: string;
  readonly usage: 'attacker' | 'victim' | 'baseline';
  readonly authState: string;
}

/** Pre-allocated login sessions for additional identities (primary uses agent1). */
function loginSessionForAccount(index: number): string {
  return `agent${7 + index}`;
}
```

- [ ] **Step 2: Extract `loginOneIdentity`**

Extract the core of the current `validateAuthentication` body (lines 93-148: load prompt, run agent, classify, verify state) into a helper taking an `IdentityToLogin` plus shared inputs, returning `Result<AvailableIdentityRecord, PentestError>`:

```ts
async function loginOneIdentity(
  identity: IdentityToLogin,
  authentication: NonNullable<DistributedConfig['authentication']>,
  shared: {
    readonly repoPath: string;
    readonly webUrl: string;
    readonly logger: ActivityLogger;
    readonly auditSession: AuditSession;
    readonly attemptNumber: number;
    readonly apiKey?: string;
    readonly providerConfig?: ProviderConfig;
    readonly deliverablesSubdir?: string;
    readonly promptDir?: string;
    readonly pipelineTestingMode?: boolean;
    readonly distributedConfig: DistributedConfig;
  },
): Promise<Result<AvailableIdentityRecord, PentestError>> {
  const { logger, auditSession, attemptNumber } = shared;

  // Per-identity auth state; clear any stale file from a prior run.
  await rm(identity.authState, { force: true });

  // Synthesize an Authentication whose credentials are this identity's,
  // inheriting login_type/login_url/login_flow/success_condition from the primary.
  const identityAuthentication = { ...authentication, credentials: identity.credentials };

  // PLAYWRIGHT_SESSION is caller-supplied, so loadPrompt (Task 5) honors it instead
  // of overwriting from the mapping — each identity logs into its own isolated session.
  const prompt = await loadPrompt(
    AGENT_NAME,
    {
      webUrl: shared.webUrl,
      repoPath: shared.repoPath,
      AUTH_STATE_FILE: identity.authState,
      PLAYWRIGHT_SESSION: identity.session,
    },
    { ...shared.distributedConfig, authentication: identityAuthentication },
    shared.pipelineTestingMode ?? false,
    logger,
    shared.promptDir,
  );

  // NOTE: Audit records are keyed per identity so multiple logins don't overwrite
  // each other. If AuditSession.startAgent keys by name alone, suffix the name:
  //   const auditName = `${AGENT_NAME}:${identity.id}`;
  // and pass auditName to startAgent/endAgent below. Verify against
  // apps/worker/src/audit/ (AuditSession.startAgent signature) before finalizing.
  await auditSession.startAgent(AGENT_NAME, prompt, attemptNumber);
  const startTime = Date.now();

  const result = await runClaudePrompt(
    prompt,
    shared.repoPath,
    '',
    `Authentication validation (${identity.id})`,
    AGENT_NAME,
    auditSession,
    logger,
    'medium',
    VALIDATION_SCHEMA,
    shared.apiKey,
    shared.deliverablesSubdir,
    shared.providerConfig,
  );

  let classification = classifyResult(result, identityAuthentication);
  if (classification.ok) {
    const sessionCheck = await verifySavedAuthState(identity.authState, logger);
    if (!sessionCheck.ok) {
      classification = sessionCheck;
    }
  }

  const endResult: AgentEndResult = {
    attemptNumber,
    duration_ms: Date.now() - startTime,
    cost_usd: result.cost || 0,
    success: classification.ok,
    ...(result.model !== undefined && { model: result.model }),
    ...(!classification.ok && { error: classification.error.message }),
  };
  await auditSession.endAgent(AGENT_NAME, endResult);

  if (!classification.ok) {
    return classification;
  }
  return ok({ id: identity.id, role: identity.role, usage: identity.usage, authState: identity.authState });
}
```

- [ ] **Step 3: Rewrite `validateAuthentication` to loop and degrade**

Replace the body of `validateAuthentication` (lines 73-148) with:

```ts
export async function validateAuthentication(input: ValidateAuthInput): Promise<Result<void, PentestError>> {
  const {
    distributedConfig,
    repoPath,
    webUrl,
    logger,
    auditSession,
    attemptNumber,
    apiKey,
    providerConfig,
    deliverablesSubdir,
    promptDir,
    pipelineTestingMode,
  } = input;

  const authentication = distributedConfig.authentication;
  if (!authentication) {
    return ok(undefined);
  }

  logger.info('Validating authentication credentials with live browser...', {
    loginUrl: authentication.login_url,
    loginType: authentication.login_type,
  });

  // Build the identity list: primary attacker (agent1) + configured accounts.
  const primary: IdentityToLogin = {
    id: 'primary',
    role: '',
    usage: 'attacker',
    credentials: authentication.credentials,
    session: 'agent1',
    authState: authStateFile(auditSession.sessionMetadata),
  };

  const accounts: readonly Account[] = distributedConfig.accounts ?? [];
  const accountIdentities: IdentityToLogin[] = accounts.map((account, index) => ({
    id: account.id,
    role: account.role,
    usage: account.usage,
    credentials: account.credentials,
    session: loginSessionForAccount(index),
    authState: authStateFile(auditSession.sessionMetadata, account.id),
  }));

  const identities = [primary, ...accountIdentities];
  const shared = {
    repoPath,
    webUrl,
    logger,
    auditSession,
    attemptNumber,
    apiKey,
    providerConfig,
    deliverablesSubdir,
    promptDir,
    pipelineTestingMode,
    distributedConfig,
  };

  const available: AvailableIdentityRecord[] = [];
  for (const identity of identities) {
    const result = await loginOneIdentity(identity, authentication, shared);
    if (result.ok) {
      available.push(result.value);
      continue;
    }
    // attacker (primary) failure is fatal — no authenticated session, no auth testing.
    if (identity.usage === 'attacker') {
      return result;
    }
    // victim/baseline failure degrades: warn and skip that identity.
    logger.warn(`Identity '${identity.id}' (${identity.usage}) login failed — degrading. ${result.error.message}`);
  }

  // Persist the availability manifest for prompt-manager to render the identity table.
  try {
    await writeFile(authIdentitiesFile(auditSession.sessionMetadata), JSON.stringify(available), 'utf8');
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    logger.warn(`Failed to write auth-identities.json: ${detail}`);
  }

  return ok(undefined);
}
```

Keep `verifySavedAuthState`, `countStorageEntries`, `classifyResult`, `AuthValidationSchema`, `VALIDATION_SCHEMA`, `AGENT_NAME`, and the type imports — only the orchestration changes.

- [ ] **Step 4: Verify type-check and lint**

Run: `pnpm run check && pnpm biome:fix`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/src/services/validate-authentication.ts
git commit -m "feat(authz): per-identity preflight login with attacker-failfast/victim-degrade"
```

---

## Task 9: Clean up all auth-state files on workflow completion

**Files:**
- Modify: `apps/worker/src/temporal/activities.ts:1000-1006`

- [ ] **Step 1: Replace single-file cleanup with directory glob**

Ensure `path` is available (add `import { fs, path } from 'zx';` if `path` is not already imported — `fs` is already imported from `zx`). Replace the cleanup block (lines 1000-1006):

```ts
// 6. Drop all authenticated browser sessions (primary + per-identity) and the manifest
try {
  const auditDir = path.dirname(authStateFile(sessionMetadata));
  const entries = await fs.readdir(auditDir);
  const toRemove = entries.filter(
    (name) => (name.startsWith('auth-state') && name.endsWith('.json')) || name === 'auth-identities.json',
  );
  await Promise.all(toRemove.map((name) => fs.rm(path.join(auditDir, name), { force: true })));
} catch (error) {
  const detail = error instanceof Error ? error.message : String(error);
  console.warn(`Failed to clean up auth session files: ${detail}`);
}
```

- [ ] **Step 2: Verify type-check and lint**

Run: `pnpm run check && pnpm biome:fix`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add apps/worker/src/temporal/activities.ts
git commit -m "feat(authz): clean up all auth-state files and manifest on completion"
```

---

## Task 10: Wire identity partials into authz/auth prompts

**Files:**
- Modify: `apps/worker/prompts/exploit-authz.txt`
- Modify: `apps/worker/prompts/exploit-auth.txt`
- Modify: `apps/worker/prompts/vuln-authz.txt`
- Modify: `apps/worker/prompts/vuln-auth.txt`
- Modify: `apps/worker/prompts/pipeline-testing/exploit-authz.txt`
- Modify: `apps/worker/prompts/pipeline-testing/exploit-auth.txt`
- Modify: `apps/worker/prompts/pipeline-testing/vuln-authz.txt`
- Modify: `apps/worker/prompts/pipeline-testing/vuln-auth.txt`

Each change is a pure insertion (or single-line swap) — no other existing lines are modified — to keep merge conflicts minimal (same pattern as the cross-route-enumeration spec).

- [ ] **Step 1: exploit-authz.txt — swap `_shared-session.txt` for `_identities.txt`**

In `exploit-authz.txt`, replace the single line `@include(shared/_shared-session.txt)` (near line 91) with:

```
@include(shared/_identities.txt)
```

The comparison protocol now lives in the partial; the existing Task Agent scripting section remains unchanged and works with the attacker/victim/baseline sessions.

- [ ] **Step 2: exploit-auth.txt — same swap**

In `exploit-auth.txt`, replace `@include(shared/_shared-session.txt)` with:

```
@include(shared/_identities.txt)
```

- [ ] **Step 3: vuln-authz.txt — inject role context**

In `vuln-authz.txt`, inside `<starting_context>` (after line 46, before `</starting_context>`), insert:

```
- **Known roles in this target** (from configured identities — use these for `role_context`):
{{IDENTITY_ROLES}}
```

- [ ] **Step 4: vuln-auth.txt — inject role context**

In `vuln-auth.txt`, inside its `<starting_context>` block, insert the same:

```
- **Known roles in this target** (from configured identities — use these for `role_context`):
{{IDENTITY_ROLES}}
```

- [ ] **Step 5: Mirror the four changes in `prompts/pipeline-testing/`**

Apply the identical swap/insertion to `pipeline-testing/exploit-authz.txt`, `pipeline-testing/exploit-auth.txt`, `pipeline-testing/vuln-authz.txt`, and `pipeline-testing/vuln-auth.txt`. If a pipeline-testing prompt omits the `@include(shared/_shared-session.txt)` line or the `<starting_context>` block, add the `_identities.txt` include (exploit) / `{{IDENTITY_ROLES}}` block (vuln) at the top of the file body instead.

- [ ] **Step 6: Verify no unresolved placeholders after rendering**

This is fully exercised in the end-to-end run (Task 12). For now:

Run: `pnpm run check`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add apps/worker/prompts/exploit-authz.txt apps/worker/prompts/exploit-auth.txt apps/worker/prompts/vuln-authz.txt apps/worker/prompts/vuln-auth.txt apps/worker/prompts/pipeline-testing/
git commit -m "feat(prompt): wire identity partials into authz/auth prompts"
```

---

## Task 11: Document `accounts` in the example config

**Files:**
- Modify: `apps/worker/configs/example-config.yaml`

- [ ] **Step 1: Add an `accounts` example block**

After the `authentication:` block (after line 52), insert:

```yaml
# Additional authenticated identities for authorization (authz) testing.
# Each inherits login_type/login_url/login_flow/success_condition from
# `authentication` above; only credentials differ.
#
# - usage: victim   -> horizontal (IDOR) baseline; MUST own private resources
#                     (orders, documents) so the attacker's access can be compared.
# - usage: baseline -> vertical baseline; a higher-privilege role (e.g. admin)
#                     used to confirm a function is genuinely admin-only.
#
# Omit this section entirely to keep today's single-account behavior.
accounts:
  - id: victim_b
    role: user
    usage: victim
    credentials:
      username: "userB"
      password: "victim-password"
  - id: admin
    role: admin
    usage: baseline
    credentials:
      username: "admin"
      password: "admin-password"
```

- [ ] **Step 2: Commit**

```bash
git add apps/worker/configs/example-config.yaml
git commit -m "docs(config): add accounts[] example for multi-account authz testing"
```

---

## Task 12: End-to-end verification with `--pipeline-testing`

**Files:** none (verification only)

- [ ] **Step 1: Build**

Run: `pnpm run build`
Expected: all packages build cleanly.

- [ ] **Step 2: Lint the whole change**

Run: `pnpm biome`
Expected: no errors.

- [ ] **Step 3: Confirm playwright-cli accepts the new session names**

This is the one assumption flagged in the spec. From a worker container (or a `--debug` run), issue a no-op against `agent7` and `agent8`:

```bash
# Inside a running worker container, or via the playwright-cli skill directly:
playwright-cli -s=agent7 navigate "about:blank"
playwright-cli -s=agent8 navigate "about:blank"
```

Expected: both succeed without "unknown session" errors. If playwright-cli rejects `agent7`/`agent8`, stop and report — the multi-slot design depends on arbitrary session names.

- [ ] **Step 4: End-to-end pipeline run with two accounts**

Create a test config `configs/multi-account-smoke.yaml` with `authentication` (attacker) + one `victim` account against a local target (e.g. juice-shop or NodeGoat), then:

```bash
./shannon start -u http://host.docker.internal:3000 -r <repo> -c configs/multi-account-smoke.yaml --pipeline-testing
```

Expected:
- Preflight logs in twice (primary on agent1, victim on agent7); `auth-identities.json` appears in the workspace with both identities.
- `exploit-authz` runs with attacker=agent5 and victim=agent7 resident.
- No unresolved `{{IDENTITIES}}` / `{{IDENTITY_ROLES}}` placeholders in the rendered prompts (check the workspace `prompts/` snapshot).
- On completion, all `auth-state*.json` and `auth-identities.json` are removed from the workspace.

- [ ] **Step 5: Backward-compat regression — single-account run**

Run the same target with a config that has `authentication` only (no `accounts`):

```bash
./shannon start -u http://host.docker.internal:3000 -r <repo> --pipeline-testing
```

Expected: behavior identical to before this change — single `auth-state.json`; prompts that now include `_identities.txt` still render a single-row primary table; no breakage.

- [ ] **Step 6: Final commit (smoke config, if kept)**

```bash
git add configs/multi-account-smoke.yaml  # only if you choose to keep it
git commit -m "test(authz): multi-account pipeline-testing smoke config"
```

---

## Self-Review (completed during planning)

**Spec coverage:** every spec section maps to a task — data model (T1), schema (T2), parse/validate (T3), auth-state files (T4), session mapping + prompt-manager session support (T5), identity manifest rendering (T6), `_identities.txt` partial (T7), preflight loop + degradation + manifest write (T8), cleanup glob (T9), prompt wiring (T10), example config (T11), e2e + playwright-cli assumption check (T12). The spec's "Merge Compatibility Strategy" is honored: one new file, prompt edits are insert-only, core-logic changes are localized.

**Placeholder scan:** no TBD/TODO; each code step contains actual code. Task 8 Step 2 carries an explicit NOTE (not a placeholder) flagging one runtime unknown — whether `AuditSession.startAgent` keys records by name alone — with the exact fix (suffix the agent name per identity) and where to verify it.

**Type consistency:** `AccountUsage = 'victim' | 'baseline'` (T1) is used in `Account` (T1), the schema `usage` enum (T2), and `sanitizeAccount` (T3). `IdentityToLogin.usage` widens to `'attacker' | 'victim' | 'baseline'` deliberately (the login list includes the attacker) and matches `sessionForUsage`/`renderIdentityManifest`. `SessionAssignment` (T5) is defined once and consumed identically by `loadPrompt`, `interpolateVariables` (`currentAssignment`), and `sessionForUsage` (T6). `authStateFile(meta, accountId?)` (T4) is used unchanged in T8 and T9.

**Per-commit green:** Tasks 5 ships the mapping type change and its `prompt-manager.ts` consumer together so `pnpm run check` is clean at every commit (an earlier draft split these and left a broken intermediate commit — corrected).

**Correctness fix embedded:** Task 5 Step 3 makes `loadPrompt` honor a caller-supplied `PLAYWRIGHT_SESSION` instead of overwriting it from the mapping. Without this, Task 8's per-identity login would route every identity through `agent1` and sessions would bleed together — a silent correctness bug caught during self-review.

**Deviation noted:** this plan does not use TDD unit tests because the project has no test runner. Verification is `pnpm run check` + `pnpm biome` + end-to-end `--pipeline-testing`, matching the established project pattern. If you want vitest-based TDD, say so before execution and a Task 0 (add vitest) will be inserted.
