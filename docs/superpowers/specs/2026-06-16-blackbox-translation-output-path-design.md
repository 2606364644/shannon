# Blackbox Translation Output Path Fix

## Background

`ReportTranslationProvider` translates every `.md` deliverable to Chinese after the report agent finalizes `comprehensive_security_assessment_report.md`. Its output directory is hardcoded:

```typescript
// apps/worker/src/providers/report-translation-provider.ts:91
const cnDir = path.join(input.repoPath, '.shannon', 'deliverables-cn');
```

This path ignores `deliverablesSubdir` and assumes `repoPath/.shannon` is writable. That assumption holds for the **whitebox local runner** (deliverables live under the real repo's `.shannon/`), but **breaks for the `--blackbox-only` Temporal run**.

### Why blackbox produces no translation

The Temporal worker container mounts the user repo **read-only** and shadows only specific subdirs with writable workspace-backed overlays (`apps/cli/src/docker.ts:273-280`):

```
repo                                                  → /repos/<name>         :ro
workspaces/<ws>/deliverables                          → /repos/<name>/.shannon/deliverables     (writable overlay)
workspaces/<ws>/scratchpad                            → /repos/<name>/.shannon/scratchpad       (writable overlay)
workspaces/<ws>/.playwright-cli                       → /repos/<name>/.shannon/.playwright-cli  (writable overlay)
workspaces/<ws>/.playwright                           → /repos/<name>/.playwright               (writable overlay)
```

There is **no overlay for `deliverables-cn`**. So inside the container, `repo/.shannon/deliverables-cn` falls under the `:ro` repo mount. Every `writeFile` in the translation provider fails with `EROFS` (read-only filesystem).

That failure is **silently swallowed**: `runWithConcurrencyLimit` runs the per-file thunks through `Promise.allSettled` (`report-translation-provider.ts:33-63, 147`), so each rejection is captured and only logged via the activity logger (`Context.current().log`), which does **not** write to `workflow.log`. The provider then returns `{}` cleanly. Net effect: the blackbox workflow completes normally, `workflow.log` shows nothing, and zero Chinese files are produced. The user only sees a Chinese report if they translate it by hand.

The blackbox deliverables themselves live in the workspace (`workspaces/<ws>/deliverables/`), not the repo, so even the *location* the provider targets is wrong for blackbox.

### Why whitebox is unaffected

Whitebox runs via the local runner, where the deliverables sit under the real repo's `.shannon/` and `deliverables-cn` is writable. Whitebox output stays at `repo/.shannon/deliverables-cn/` — unchanged by this fix.

## Design

Goal: blackbox translations land at `workspaces/<ws>/deliverables-cn/` (sibling of `workspaces/<ws>/deliverables/`); whitebox unchanged; translation failures become visible instead of silent.

### 1. Add a `deliverables-cn` overlay mount (blackbox only)

In `apps/cli/src/docker.ts` `spawnWorker` — which is used **only** by the blackbox/Temporal `start` command; `local-start.ts` builds its own `docker run` args and is not affected — add one overlay next to the existing `deliverables` mount:

```typescript
args.push('-v', `${path.join(workspacePath, 'deliverables')}:${opts.repo.containerPath}/.shannon/deliverables`);
args.push('-v', `${path.join(workspacePath, 'deliverables-cn')}:${opts.repo.containerPath}/.shannon/deliverables-cn`); // NEW
```

The provider's hardcoded `repo/.shannon/deliverables-cn` now resolves to a writable, workspace-backed directory. On the host it appears at `workspaces/<ws>/deliverables-cn/`.

### 2. Pre-create the mount source and target

Docker cannot auto-create overlay targets reliably under `:ro` mounts. Two pre-creation loops in `apps/cli/src/commands/start.ts` already handle this for the other overlays; add `deliverables-cn` to both:

- **Line 73** — workspace-side overlay sources: add `'deliverables-cn'` to `['deliverables', 'scratchpad', '.playwright-cli', '.playwright']`.
- **Line 96** — repo-side mount points under `repo/.shannon`: add `'deliverables-cn'` to `['deliverables', 'scratchpad', '.playwright-cli']`.

`local-start.ts` is intentionally **not** changed, so whitebox keeps writing to `repo/.shannon/deliverables-cn/`.

### 3. Make translation failures visible in `workflow.log`

Today the provider's per-file/summary logs go only to the Temporal activity logger and never reach the workflow log the user watches. Surface the outcome at the activity boundary instead.

- Extend the `ReportOutputProvider.generate()` return type (`apps/worker/src/interfaces/report-output-provider.ts`) to carry counts:
  ```typescript
  generate(input, logger): Promise<{ outputPath?: string; successCount?: number; failCount?: number }>;
  ```
  `ReportTranslationProvider` already computes `successCount`/`failCount` (lines 156-177) — return them. `NoOpReportOutputProvider` returns `{}` (the new fields are optional).

- In `generateReportOutputActivity` (`apps/worker/src/temporal/activities.ts:1089`), after `provider.generate(...)`, log a one-line summary through the `AuditSession` — the same handle `logWorkflowComplete` (line 957) uses to reach `WorkflowLogger`/`workflow.log`. If `failCount > 0` (or no `outputPath`), emit a visible warning naming the failure so the next regression is diagnosable from `./shannon logs` rather than invisible.

The provider's existing detailed logging (per-file `✓`/`✗`, aggregate totals) is retained for worker-stdout debugging; only the activity-level summary is newly routed to `workflow.log`.

## Files Changed

| File | Change |
|------|--------|
| `apps/cli/src/docker.ts` | Add `deliverables-cn` overlay mount in `spawnWorker` (blackbox path only) |
| `apps/cli/src/commands/start.ts` | Pre-create `workspaces/<ws>/deliverables-cn` and `repo/.shannon/deliverables-cn` mount points |
| `apps/worker/src/interfaces/report-output-provider.ts` | Add optional `successCount`/`failCount` to `generate()` return type |
| `apps/worker/src/providers/report-translation-provider.ts` | Return the counts it already computes |
| `apps/worker/src/temporal/activities.ts` | Log translation summary via `AuditSession`/`WorkflowLogger` in `generateReportOutputActivity` |

## Verification

- Re-run `--blackbox-only` on the welfare workspace; confirm `workspaces/<ws>/deliverables-cn/comprehensive_security_assessment_report-cn.md` is produced automatically and `workflow.log` shows the translation summary line.
- Re-run a whitebox scan; confirm translations still appear at `repo/.shannon/deliverables-cn/` (and confirm why that path is writable for whitebox, ruling out a latent identical bug).
- Rebuild before testing: `pnpm run build` (recompiles `apps/cli/dist` for the `docker.ts`/`start.ts` CLI changes) **and** `./shannon build` (rebuilds the worker Docker image for the provider/activity changes).

## Out of Scope

- Making `cnDir` derive from `deliverablesSubdir` generally (the overlay approach fixes the immediate path without touching the provider's hardcoded path — minimal blast radius).
- Translation quality, language selection, or retry logic.
- Changing whitebox's output location or mount scheme.
