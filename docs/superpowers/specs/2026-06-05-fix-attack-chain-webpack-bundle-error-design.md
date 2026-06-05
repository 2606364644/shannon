# Fix: buildAttackChainsActivity Webpack Bundle Error

## Problem

`workflows.ts` line 39 directly imports `buildAttackChainsActivity` as a value from `activities.js`:

```typescript
import { buildAttackChainsActivity } from './activities.js';
```

This pulls the entire `activities.js` module into the Temporal webpack bundle. The import chain `activities.js → session-manager.js → zx → deno.js → node:process` triggers webpack's `UnhandledSchemeError` because webpack cannot handle `node:` protocol URIs. The worker crashes on startup with "Webpack finished with 10 errors", causing all scans (not just blackbox) to time out.

## Root Cause

Commit `69038a4` introduced a value import of an activity function directly in `workflows.ts`. Temporal workflows must only reference activities through `proxyActivities` — all other activities in the codebase already follow this pattern.

## Fix

Two-line change in `apps/worker/src/temporal/workflows.ts`:

1. **Delete line 39:** `import { buildAttackChainsActivity } from './activities.js';`
2. **Change line 568:** `await buildAttackChainsActivity(activityInput)` → `await a.buildAttackChainsActivity(activityInput)`

The `acts` proxy (created by `proxyActivities<typeof activities>`) already exposes `buildAttackChainsActivity` with heartbeat, retry, and timeout protection. No changes to official code.

## Verification

1. `pnpm run build` — TypeScript compilation passes
2. `pnpm run check` — Type check passes
3. `./shannon build` — Docker image rebuilds without error
4. Re-run the blackbox scan — workflow starts without timeout
