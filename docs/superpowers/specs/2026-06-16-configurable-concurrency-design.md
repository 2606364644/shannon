# Configurable Concurrency — Design Spec

## Problem

Phase 3 (Vulnerability Analysis) of the local whitebox runner launches all vuln
agents in parallel — the default concurrency equals the number of vuln classes
(currently 5). When the model endpoint is rate-limited (e.g. a custom LLM proxy
returning HTTP 429), five concurrent agents overwhelm the limit and scans fail or
stall. There is no way to cap this concurrency from `.env`; the only existing knob
is the `--concurrency` CLI flag, which must be remembered on every invocation.

Example failure (`ground_push_prize_web` scan, `glm-5.1` via proxy):

```
=== Phase 3: Vulnerability Analysis (concurrency=5) ===
[xss-vuln] Attempt 1/3 (global: 1)
[ssrf-vuln] Attempt 1/3 (global: 1)
... 5 agents launch simultaneously → 429s
```

## Solution

Add a `SHANNON_CONCURRENCY` environment variable that the local whitebox runner
reads as its default concurrency, so it can be set once in `.env` instead of passed
as a flag every time. CLI `--concurrency` still overrides; the built-in default
(all vuln classes) remains the last-resort fallback.

## Files

- `apps/worker/src/local/runner.ts` — add env-backed default in `parseArgs`.
  **Only file changed.**
- (Optional, see Env Flow) `apps/cli/src/env.ts` — forward `SHANNON_CONCURRENCY`
  into docker containers via `FORWARD_VARS`. Currently inert — not done by default.

After editing, rebuild the worker: `pnpm run build`. The runner is executed from
compiled `apps/worker/dist/local/runner.js`.

## Configuration

| Variable | Purpose | Required |
|---|---|---|
| `SHANNON_CONCURRENCY` | Max parallel vuln agents in Phase 3 (local whitebox runner) | No — falls back to `--concurrency` flag, then to all vuln classes (5) |

Usage — add to repo-root `.env`:

```
SHANNON_CONCURRENCY=2
```

Then run as usual: `./shannon start -r repo`. Phase 3 launches at most 2 agents
concurrently; 429 pressure drops accordingly.

## Resolution & Precedence

Concurrency is resolved in `parseArgs` (`runner.ts`) with this precedence
(highest first):

1. `--concurrency <n>` CLI flag (unchanged — `local-start.ts:63` forwards it;
   runner parses it at `:73`)
2. `SHANNON_CONCURRENCY` env var (new — consulted only when no CLI flag is given)
3. `ALL_VULN_CLASSES.length` (built-in default = 5)

This mirrors the existing env-as-fallback convention (`CLAUDE_ADAPTIVE_THINKING`,
`ANTHROPIC_SMALL_MODEL`, etc.).

New helper:

```ts
function resolveConcurrencyFromEnv(): number {
  const raw = process.env.SHANNON_CONCURRENCY;
  if (!raw) return ALL_VULN_CLASSES.length;          // built-in default (5)
  const n = parseInt(raw, 10);
  if (!Number.isInteger(n) || n < 1) {
    console.warn(
      `[WARN] SHANNON_CONCURRENCY="${raw}" invalid (need integer >= 1); falling back to ${ALL_VULN_CLASSES.length}`,
    );
    return ALL_VULN_CLASSES.length;
  }
  return n;
}
```

`parseArgs` changes:

```ts
// before:
let concurrency = ALL_VULN_CLASSES.length;

// after:
let concurrency: number | undefined;   // set only when --concurrency is passed
// ... `--concurrency` case unchanged: concurrency = parseInt(next, 10)
// in the returned object:
concurrency: concurrency ?? resolveConcurrencyFromEnv(),
```

The env var (and its invalid-value warning) is consulted only when `--concurrency`
is absent, so a CLI override never triggers a misleading env warning.

## Validation

- Non-integer or `< 1` → log `[WARN]` and fall back to the built-in default (5).
  A typo must not crash a long scan.
- No upper clamp: a value larger than the vuln-class count is harmless (the
  `Semaphore` at `runner.ts:368` simply never limits).

## Env Flow (how `.env` reaches the runner)

The reachable local path is a bare fork, so no forwarding list is needed:

```
.env  ──loadEnv() / dotenv──▶  process.env  ──fork {…process.env}──▶  runner process.env.SHANNON_CONCURRENCY
```

- `./shannon start` sets `SHANNON_LOCAL=1`; `start` dispatches to `localStart`
  only when `whiteboxOnly && isLocal()` (`index.ts:241`).
- `localStart` calls `loadEnv()` → `dotenv.config('.env')` (`env.ts:38`),
  populating `process.env`.
- `localStartBare` forks the runner with `env: { ...process.env, SHANNON_LOCAL: '1', ... }`
  (`local-start.ts:120`), so every `.env` var — including `SHANNON_CONCURRENCY` — is
  inherited directly by the runner process.

**Why `FORWARD_VARS` is left untouched:** `FORWARD_VARS` (`env.ts:13`) only governs
the docker-run path (`buildEnvFlags`, `env.ts:49`), used by `localStartNpx`
(`local-start.ts:140`). That branch is currently unreachable from `start`:
`localStart` is only called when `isLocal()` is true (`index.ts:241`), and its own
`isLocal()` check then always takes the bare-fork branch (`local-start.ts:65-68`).
Adding `SHANNON_CONCURRENCY` to `FORWARD_VARS` would therefore be inert today. It
remains a one-line future-proofing step if the docker local-runner path is ever
wired up.

## Testing

The worker package has no unit-test harness; verification follows existing
conventions (type check + manual smoke, no docker needed — bare mode reads env
directly):

- **Type check:** `pnpm run check`.
- **Smoke:**
  - `SHANNON_CONCURRENCY=2 ./shannon start -r repo --pipeline-testing` → startup
    banner prints `Concurrency: 2`; Phase 3 runs ≤2 concurrent agents.
  - `SHANNON_CONCURRENCY=0 ./shannon start -r repo --pipeline-testing` →
    `[WARN] … falling back to 5`; banner prints `Concurrency: 5`.
  - With `SHANNON_CONCURRENCY=2` in `.env`, add `--concurrency 3` → CLI wins,
    banner prints `Concurrency: 3`, no env warning emitted.

## Documentation

- `CLAUDE.md` — Options / SDK Integration sections: document `SHANNON_CONCURRENCY`
  alongside the existing `--concurrency` flag and `CLAUDE_ADAPTIVE_THINKING`
  env-var mentions.
- `apps/cli/src/index.ts:97` — help text for `--concurrency`: append
  "or `SHANNON_CONCURRENCY` env var".

## Out of Scope

- Temporal workflow path (`max_concurrent_pipelines` in `workflows.ts:537`) — stays
  YAML-only; not used by the local whitebox runner. (Per scoping decision: local
  path only.)
- Per-phase or per-agent concurrency knobs — Phase 3 is the only parallel phase in
  the local runner; one knob covers it.
- Upper-bound clamping.
- Wiring up the currently-unreachable docker local-runner path (`localStartNpx`).
