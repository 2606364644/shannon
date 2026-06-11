# SDK Connectivity Test Script — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a CLI script that verifies connectivity to the configured model provider for all three model tiers using the same `query()` code path as the production pipeline.

**Architecture:** Single TypeScript file (`scripts/test-sdk-connectivity.ts`) that reads `.env`, iterates over the three model tiers, calls `query()` from `@anthropic-ai/claude-agent-sdk` with `maxTurns: 1` for each, and reports pass/fail with timing. Uses `AbortController` for per-model 30s timeout. No external dependencies beyond what's already installed.

**Tech Stack:** TypeScript, `@anthropic-ai/claude-agent-sdk` (`query()`), `npx tsx` for execution.

---

### Task 1: Add `@anthropic-ai/claude-agent-sdk` as root devDependency

The SDK is only installed in `apps/worker/node_modules/`. For `scripts/test-sdk-connectivity.ts` to resolve it via `pnpm tsx`, it must be accessible from the root. Adding it as a root devDependency (using the catalog version) makes pnpm link it to `node_modules/` at the root level. No download needed — the package is already in the store.

**Files:**
- Modify: `package.json` (add devDependency)
- Modify: `pnpm-workspace.yaml` (no change needed — catalog already has the entry)

- [ ] **Step 1: Add the SDK to root devDependencies**

In `package.json`, add to `devDependencies`:

```json
"@anthropic-ai/claude-agent-sdk": "catalog:"
```

The full `devDependencies` block becomes:

```json
"devDependencies": {
  "@anthropic-ai/claude-agent-sdk": "catalog:",
  "@biomejs/biome": "^2.0.0",
  "@types/node": "^25.0.3",
  "turbo": "^2.5.0",
  "typescript": "^5.9.3"
}
```

- [ ] **Step 2: Run pnpm install to link the package**

Run: `pnpm install`

Expected: The SDK is linked to `node_modules/@anthropic-ai/claude-agent-sdk/` at the root (symlink or hard link from the store). No actual download since it's already in `node_modules/.pnpm/`.

- [ ] **Step 3: Verify the SDK resolves from root**

Run: `node -e "require('@anthropic-ai/claude-agent-sdk'); console.log('OK')"`

Expected: Prints `OK` with no error.

- [ ] **Step 4: Commit**

```bash
git add package.json pnpm-lock.yaml
git commit -m "chore: add claude-agent-sdk as root devDependency for scripts"
```

---

### Task 2: Create the connectivity test script

Single file with all logic: `.env` loading, config validation, model iteration, `query()` calls with timeout, formatted output.

**Files:**
- Create: `scripts/test-sdk-connectivity.ts`

- [ ] **Step 1: Create `scripts/test-sdk-connectivity.ts`**

```typescript
#!/usr/bin/env node

// SDK connectivity test — verifies model provider reachability for all three tiers.
// Reads .env from project root, calls query() per model, reports pass/fail with timing.
//
// Usage: npx tsx scripts/test-sdk-connectivity.ts
// Exit codes: 0 = all passed, 1 = at least one failed, 2 = config error

import { execSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { query } from '@anthropic-ai/claude-agent-sdk';

// === Configuration ===

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(SCRIPT_DIR, '..');
const ENV_FILE = resolve(PROJECT_ROOT, '.env');
const MODEL_TIERS = ['small', 'medium', 'large'] as const;
const TIMEOUT_MS = 30_000;

const DEFAULT_MODELS: Record<string, string> = {
  small: 'claude-haiku-4-5-20251001',
  medium: 'claude-sonnet-4-6',
  large: 'claude-opus-4-7',
};

const ENV_VAR_MAP: Record<string, string> = {
  small: 'ANTHROPIC_SMALL_MODEL',
  medium: 'ANTHROPIC_MEDIUM_MODEL',
  large: 'ANTHROPIC_LARGE_MODEL',
};

// === .env Parser ===

/** Minimal .env parser — handles KEY=VALUE lines, ignores comments and blanks. */
function parseEnvFile(filePath: string): Record<string, string> {
  const content = readFileSync(filePath, 'utf-8');
  const env: Record<string, string> = {};
  for (const line of content.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eqIndex = trimmed.indexOf('=');
    if (eqIndex === -1) continue;
    const key = trimmed.slice(0, eqIndex).trim();
    let value = trimmed.slice(eqIndex + 1).trim();
    // Strip surrounding quotes
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    env[key] = value;
  }
  return env;
}

// === Output Helpers ===

function maskToken(token: string): string {
  if (token.length <= 8) return '****';
  return `${token.slice(0, 4)}...${token.slice(-4)}`;
}

function printHeader(endpoint: string, authSource: string, authToken: string): void {
  console.log('🔗 Shannon SDK Connectivity Test');
  console.log('─────────────────────────────────');
  console.log(`Endpoint: ${endpoint}`);
  console.log(`Auth: ${authSource} (${maskToken(authToken)})`);
  console.log();
}

function printFooter(passed: number, total: number): void {
  console.log('─────────────────────────────────');
  console.log(`Results: ${passed}/${total} passed`);
}

// === Model Test ===

interface TestResult {
  tier: string;
  model: string;
  passed: boolean;
  durationMs: number;
  error?: string;
}

async function testModel(tier: string, model: string, sdkEnv: Record<string, string>): Promise<TestResult> {
  const start = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

  console.log(`📡 Testing ${tier} model (${model})...`);

  try {
    let resultText = '';

    const stream = query({
      prompt: 'Respond with only the word: OK',
      options: {
        model,
        maxTurns: 1,
        cwd: resolve(SCRIPT_DIR),
        permissionMode: 'bypassPermissions',
        allowDangerouslySkipPermissions: true,
        tools: [],
        env: sdkEnv,
        persistSession: false,
        abortController: controller,
      },
    });

    for await (const message of stream) {
      if (message.type === 'result') {
        resultText = (message as { type: 'result'; result: string }).result || '';
        break;
      }
    }

    const durationMs = Date.now() - start;
    clearTimeout(timer);

    if (resultText) {
      console.log(`  ✅ OK (${(durationMs / 1000).toFixed(1)}s)`);
      console.log();
      return { tier, model, passed: true, durationMs };
    }

    console.log(`  ❌ FAILED: Empty response`);
    console.log();
    return { tier, model, passed: false, durationMs, error: 'Empty response' };
  } catch (error) {
    clearTimeout(timer);
    const durationMs = Date.now() - start;
    const err = error as Error;
    let errorMessage = err.message;

    if (err.name === 'AbortError') {
      errorMessage = `Timeout after ${TIMEOUT_MS / 1000}s`;
    } else if (err.message.includes('401') || err.message.includes('403')) {
      errorMessage = 'Authentication failed (check credentials)';
    } else if (err.message.includes('ECONNREFUSED') || err.message.includes('ENOTFOUND')) {
      errorMessage = `Network error: ${err.message}`;
    }

    console.log(`  ❌ FAILED: ${errorMessage}`);
    console.log();
    return { tier, model, passed: false, durationMs, error: errorMessage };
  }
}

// === Main ===

async function main(): Promise<number> {
  // 1. Load .env
  let env: Record<string, string>;
  try {
    env = parseEnvFile(ENV_FILE);
  } catch {
    console.error(`❌ Cannot read ${ENV_FILE}`);
    console.error('   Create one with ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN (or ANTHROPIC_API_KEY).');
    return 2;
  }

  // 2. Validate required config
  const baseUrl = env.ANTHROPIC_BASE_URL;
  const authToken = env.ANTHROPIC_AUTH_TOKEN;
  const apiKey = env.ANTHROPIC_API_KEY;

  if (!baseUrl) {
    console.error('❌ ANTHROPIC_BASE_URL is not set in .env');
    return 2;
  }

  const effectiveToken = authToken || apiKey;
  if (!effectiveToken) {
    console.error('❌ Neither ANTHROPIC_AUTH_TOKEN nor ANTHROPIC_API_KEY is set in .env');
    return 2;
  }

  const authSource = authToken ? 'ANTHROPIC_AUTH_TOKEN' : 'ANTHROPIC_API_KEY';

  // 3. Resolve model names
  const models: Record<string, string> = {};
  for (const tier of MODEL_TIERS) {
    models[tier] = env[ENV_VAR_MAP[tier]] || DEFAULT_MODELS[tier];
  }

  // 4. Build SDK env (only pass what the SDK needs)
  const sdkEnv: Record<string, string> = {
    ANTHROPIC_BASE_URL: baseUrl,
    ...(authToken && { ANTHROPIC_AUTH_TOKEN: authToken }),
    ...(apiKey && !authToken && { ANTHROPIC_API_KEY: apiKey }),
    HOME: process.env.HOME || '',
    PATH: process.env.PATH || '',
  };

  // 5. Print header
  printHeader(baseUrl, authSource, effectiveToken);

  // 6. Test each model tier sequentially
  const results: TestResult[] = [];
  for (const tier of MODEL_TIERS) {
    const result = await testModel(tier, models[tier], sdkEnv);
    results.push(result);
  }

  // 7. Summary
  const passed = results.filter((r) => r.passed).length;
  printFooter(passed, results.length);

  return passed === results.length ? 0 : 1;
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error('Fatal error:', err);
    process.exit(2);
  });
```

- [ ] **Step 2: Verify TypeScript compiles without errors**

Run: `npx tsx --eval "import './scripts/test-sdk-connectivity.ts'"`

This will fail at runtime (no .env), but should show no TypeScript compilation errors. If there are type errors, fix them.

Expected: No TypeScript errors. May show a runtime error about missing `.env`, which is expected.

- [ ] **Step 3: Run Biome lint/format check**

Run: `pnpm biome check scripts/test-sdk-connectivity.ts`

Expected: May report formatting issues. Auto-fix if needed:

Run: `pnpm biome check --write scripts/test-sdk-connectivity.ts`

- [ ] **Step 4: Commit**

```bash
git add scripts/test-sdk-connectivity.ts
git commit -m "feat: add SDK connectivity test script"
```

---

### Task 3: Manual smoke test with real credentials

This task validates the script works end-to-end against the configured provider. It requires a valid `.env` with real credentials (already present in the project).

**Files:**
- No file changes expected

- [ ] **Step 1: Run the script**

Run: `npx tsx scripts/test-sdk-connectivity.ts`

Expected: Output similar to:

```
🔗 Shannon SDK Connectivity Test
─────────────────────────────────
Endpoint: https://open.bigmodel.cn/api/anthropic
Auth: ANTHROPIC_AUTH_TOKEN (cb85...4edb)

📡 Testing small model (glm-4.5-air)...
  ✅ OK (X.Xs)

📡 Testing medium model (glm-5-turbo)...
  ✅ OK (X.Xs)

📡 Testing large model (glm-5.1)...
  ✅ OK (X.Xs)

─────────────────────────────────
Results: 3/3 passed
```

- [ ] **Step 2: Verify exit code is 0**

Run: `npx tsx scripts/test-sdk-connectivity.ts; echo "Exit: $?"`

Expected: Last line shows `Exit: 0`

- [ ] **Step 3: Test config error handling — remove BASE_URL temporarily**

Run: `ANTHROPIC_BASE_URL= npx tsx scripts/test-sdk-connectivity.ts; echo "Exit: $?"`

Expected: Prints config error about missing ANTHROPIC_BASE_URL, exits with code 2.

- [ ] **Step 4: Commit (only if any fixes were needed)**

```bash
git add scripts/test-sdk-connectivity.ts
git commit -m "fix: address smoke test issues in connectivity script"
```

Only commit if changes were made. If the script passes all checks, no commit needed.
