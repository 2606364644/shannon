# Configurable Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the local whitebox runner's Phase 3 concurrency be configured via a `SHANNON_CONCURRENCY` env var (settable in `.env`), so rate-limited model endpoints stop returning 429s.

**Architecture:** Add a pure resolver `resolveConcurrencyFromEnv()` in `apps/worker/src/local/runner.ts` and consult it as the default in `parseArgs` only when `--concurrency` is absent. Precedence: `--concurrency` CLI > `SHANNON_CONCURRENCY` env > `ALL_VULN_CLASSES.length` (5). Invalid env values warn and fall back. No other file's logic changes.

**Tech Stack:** TypeScript, pnpm workspaces + Turborepo, Biome (lint/format). Worker package has no unit-test runner, so verification is `pnpm run check` (type-check) + a credential-free smoke against the compiled runner.

**Spec:** `docs/superpowers/specs/2026-06-16-configurable-concurrency-design.md`

---

## File Structure

- **Modify** `apps/worker/src/local/runner.ts` — add `resolveConcurrencyFromEnv()`; rewire `parseArgs` so the `--concurrency` CLI flag overrides an env-backed default. This is the only behavioral change.
- **Modify** `apps/cli/src/index.ts:97` — append env-var mention to the `--concurrency` help line.
- **Modify** `CLAUDE.md:85` and `CLAUDE.md:150` — document `SHANNON_CONCURRENCY` (Options line + the env-var cluster in SDK Integration).
- **Not modified** `apps/cli/src/env.ts` — the docker local-runner path (`localStartNpx`) is unreachable from `start`, so `FORWARD_VARS` is inert; the reachable bare-fork path inherits `.env` via `process.env` directly. (See spec "Env Flow".)

**Why no unit tests:** the worker package (`apps/worker/package.json`) has only `build` / `check` / `clean` scripts — no test runner is installed. Introducing one is out of scope. `resolveConcurrencyFromEnv()` is pure and is instead verified by running the compiled runner with the env var set and reading the startup banner, which prints before any credential check.

---

## Task 1: Add env-backed concurrency default in the runner

**Files:**
- Modify: `apps/worker/src/local/runner.ts` (add helper after the `RunnerArgs` interface ~line 35; change default at line 41; change return at lines 108-117)

- [ ] **Step 1: Add the `resolveConcurrencyFromEnv()` helper**

In `apps/worker/src/local/runner.ts`, insert this function between the `RunnerArgs` interface (ends at the `}` around line 35) and `function parseArgs` (line 37). `ALL_VULN_CLASSES` is already imported at line 15.

```ts
/**
 * Resolve the default Phase 3 concurrency from the SHANNON_CONCURRENCY env var.
 * Returns the built-in default (all vuln classes) when unset or invalid; an
 * invalid value logs a warning and falls back rather than crashing a long scan.
 */
function resolveConcurrencyFromEnv(): number {
  const raw = process.env.SHANNON_CONCURRENCY;
  if (!raw) {
    return ALL_VULN_CLASSES.length;
  }
  const parsed = parseInt(raw, 10);
  if (!Number.isInteger(parsed) || parsed < 1) {
    console.warn(
      `[WARN] SHANNON_CONCURRENCY="${raw}" invalid (need integer >= 1); using default ${ALL_VULN_CLASSES.length}`,
    );
    return ALL_VULN_CLASSES.length;
  }
  return parsed;
}
```

- [ ] **Step 2: Change the `concurrency` default in `parseArgs` to "unset"**

At `apps/worker/src/local/runner.ts:41`, replace:

```ts
  let concurrency = ALL_VULN_CLASSES.length;
```

with:

```ts
  let concurrency: number | undefined;
```

The existing `--concurrency` case (lines 73-78) already assigns `concurrency = parseInt(next, 10)`, which stays a `number` — no change needed there.

- [ ] **Step 3: Resolve the default in the return object**

At `apps/worker/src/local/runner.ts:108-117`, in the returned object literal, replace the shorthand:

```ts
    concurrency,
```

with:

```ts
    concurrency: concurrency ?? resolveConcurrencyFromEnv(),
```

`concurrency ?? resolveConcurrencyFromEnv()` always evaluates to a `number`, matching the `concurrency: number` field on `RunnerArgs`. The `??` short-circuits, so the env var (and any warning) is consulted only when `--concurrency` was not passed.

- [ ] **Step 4: Type-check**

Run: `pnpm run check`
Expected: passes with no errors. (If it fails on `exactOptionalPropertyTypes`, confirm the return uses `concurrency ?? resolveConcurrencyFromEnv()`, not a bare `concurrency`.)

- [ ] **Step 5: Build (runner executes from `dist/`)**

Run: `pnpm run build`
Expected: completes; `apps/worker/dist/local/runner.js` is regenerated.

- [ ] **Step 6: Smoke — env value is used**

Run:
```bash
SHANNON_CONCURRENCY=2 node apps/worker/dist/local/runner.js --repo /tmp/shannon-conc-smoke 2>&1 | grep -E "Concurrency:"
```
Expected: a line containing `Concurrency: 2`. (The runner prints its banner, then will fail later at preflight — that's fine; the banner is what we're checking.)

- [ ] **Step 7: Smoke — invalid value warns and falls back**

Run:
```bash
SHANNON_CONCURRENCY=0 node apps/worker/dist/local/runner.js --repo /tmp/shannon-conc-smoke 2>&1 | grep -E "Concurrency:|WARN"
```
Expected: a `[WARN] SHANNON_CONCURRENCY="0" invalid ...` line followed by `Concurrency: 5`.

- [ ] **Step 8: Smoke — CLI flag overrides env, no warning**

Run:
```bash
SHANNON_CONCURRENCY=2 node apps/worker/dist/local/runner.js --repo /tmp/shannon-conc-smoke --concurrency 3 2>&1 | grep -E "Concurrency:|WARN"
```
Expected: `Concurrency: 3` and **no** `WARN` line (env is never consulted when the flag is present).

- [ ] **Step 9: Commit**

```bash
git add apps/worker/src/local/runner.ts
git commit -m "feat(worker): add SHANNON_CONCURRENCY env for local runner concurrency

Phase 3 vuln-agent parallelism in the local whitebox runner can now be
capped via the SHANNON_CONCURRENCY env var, mitigating 429s from
rate-limited model endpoints. Precedence: --concurrency CLI >
SHANNON_CONCURRENCY env > all vuln classes (5). Invalid values warn
and fall back."
```

---

## Task 2: Document the env var in the CLI help

**Files:**
- Modify: `apps/cli/src/index.ts:97`

- [ ] **Step 1: Extend the `--concurrency` help line**

At `apps/cli/src/index.ts:97`, replace:

```ts
      --concurrency <n>     Max parallel vuln agents in whitebox mode
```

with:

```ts
      --concurrency <n>     Max parallel vuln agents in whitebox mode (or SHANNON_CONCURRENCY env var)
```

- [ ] **Step 2: Type-check**

Run: `pnpm run check`
Expected: passes (string change inside a template literal; no type impact).

- [ ] **Step 3: Commit**

```bash
git add apps/cli/src/index.ts
git commit -m "docs(cli): mention SHANNON_CONCURRENCY in start --concurrency help"
```

---

## Task 3: Document the env var in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md:85` (Options line) and `CLAUDE.md:150` (SDK Integration env-var cluster)

- [ ] **Step 1: Add to the Options line**

At `CLAUDE.md:85`, replace:

```
**Options:** `-c <file>` (YAML config), `-o <path>` (output directory), `-w <name>` (named workspace; auto-resumes if exists), `--pipeline-testing` (minimal prompts, 10s retries), `--debug` (preserve worker container after exit for log inspection)
```

with:

```
**Options:** `-c <file>` (YAML config), `-o <path>` (output directory), `-w <name>` (named workspace; auto-resumes if exists), `--pipeline-testing` (minimal prompts, 10s retries), `--debug` (preserve worker container after exit for log inspection), `--concurrency <n>` (max parallel vuln agents in whitebox mode; or `SHANNON_CONCURRENCY` env var, local whitebox runner only)
```

- [ ] **Step 2: Add to the SDK Integration env-var cluster**

At `CLAUDE.md:150`, find the clause ending:

```
disable per-scan via `CLAUDE_ADAPTIVE_THINKING=false` (env) or `core.adaptive_thinking = false` (npx TOML).
```

Append exactly one sentence to the end of that clause (after its closing `.`):

```
. Phase 3 vuln-agent parallelism in the local whitebox runner is capped by `SHANNON_CONCURRENCY=<n>` (env) or `--concurrency <n>` (CLI), defaulting to all vuln classes when unset.
```

So the clause reads `... or \`core.adaptive_thinking = false\` (npx TOML). Phase 3 vuln-agent parallelism ...` — one added sentence inside the existing paragraph.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document SHANNON_CONCURRENCY env var"
```

---

## Task 4: Final lint + build gate

**Files:** none (verification only; commits only if Biome reformats)

- [ ] **Step 1: Lint/format check**

Run: `pnpm biome`
Expected: clean for the touched `.ts` files (`apps/worker/src/local/runner.ts`, `apps/cli/src/index.ts`). If Biome reports formatting/import-sort issues, run `pnpm biome:fix` and commit the normalization:

```bash
git add -A
git commit -m "style: biome formatting for concurrency changes"
```

- [ ] **Step 2: Final build (regenerates CLI bundle with the help-text change)**

Run: `pnpm run build`
Expected: completes. This rebuilds `apps/cli/dist/index.mjs` (so `./shannon help` shows the new help text) and `apps/worker/dist/`.

- [ ] **Step 3: Confirm the help text is live**

Run: `./shannon help 2>&1 | grep -i concurrency`
Expected: `--concurrency <n>     Max parallel vuln agents in whitebox mode (or SHANNON_CONCURRENCY env var)`

- [ ] **Step 4: Confirm end-to-end via the wrapper**

In a repo root with `.env` containing `SHANNON_CONCURRENCY=2`, run:
```bash
./shannon start -r <repo> --pipeline-testing 2>&1 | grep -E "Concurrency:"
```
Expected: `Concurrency: 2` (proves `.env` → `dotenv` → fork inheritance → runner resolution works through the real `./shannon` entry point, not just a direct `node` call).
