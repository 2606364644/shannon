## 1. Rewrite validateDeliverablesExist

**File:** `apps/worker/src/temporal/activities.ts`

- [x] 1.1 Import `isOk` from `../types/result.js` and `validateQueueSafe` from `../services/queue-validation.js`
- [x] 1.2 Replace the `for (const vt of ALL_VULN_CLASSES)` loop body: call `validateQueueSafe(vt, delivPath)`, if `isOk(result)` and `result.value.shouldExploit`, push `vt` to `typesWithQueues`
- [x] 1.3 Remove the old inline `JSON.parse` / `Array.isArray` block (lines 1024-1038)
- [x] 1.4 Keep the `recon_deliverable.md` existence check unchanged (lines 1013-1020)
- [x] 1.5 Keep the "at least one non-empty" guard unchanged (lines 1040-1044) — it still checks `typesWithQueues.length === 0`

## 2. Build and Verify

- [x] 2.1 Run `pnpm run build` to compile worker TypeScript
- [x] 2.2 Run `pnpm biome` and `pnpm run check` — zero new issues
- [x] 2.3 Run `./shannon build` to rebuild Docker image with fix
- [x] 2.4 Run `./shannon start -u https://testheader.futunn.com -r /root/code/official_common_header_footer --blackbox-only --debug`
- [x] 2.5 Confirm `validateDeliverablesExist` passes and exploit agents begin execution
