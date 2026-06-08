# `./shannon clean` Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `clean` CLI subcommand that removes blackbox scan results (deliverables + logs) from a workspace so users can re-run blackbox scans without pollution.

**Architecture:** Pure filesystem operation in the CLI package. One new command file (`clean.ts`) imports `getWorkspacesDir` from `home.ts` and `resolveRepo` from `paths.ts`. Registered in `index.ts` alongside existing commands. No Docker, no worker changes.

**Tech Stack:** Node.js, TypeScript, `@clack/prompts` for confirmation, `fs` for file ops, `path` for resolution.

---

### Task 1: Create `apps/cli/src/commands/clean.ts`

**Files:**
- Create: `apps/cli/src/commands/clean.ts`

This is the entire command implementation — scan, preview, confirm, delete.

- [x] **Step 1: Write the clean command module**

Create `apps/cli/src/commands/clean.ts` with the following content:

```typescript
/**
 * `shannon clean` command — remove scan results from a workspace.
 *
 * Defaults to cleaning blackbox results (--blackbox is implicit).
 * Removes blackbox deliverables from the repo and blackbox agent logs
 * from the workspace directory, then truncates workflow.log.
 */

import fs from 'node:fs';
import path from 'node:path';
import * as p from '@clack/prompts';
import { getWorkspacesDir } from '../home.js';
import { resolveRepo } from '../paths.js';

/** File glob patterns for blackbox deliverables (relative to deliverables dir). */
const BLACKBOX_DELIVERABLE_PATTERNS = [
  '*_exploitation_evidence.md',
  '*_findings.md',
  'comprehensive_security_assessment_report.md',
];

/** File glob patterns for blackbox agent logs (relative to workspace agents/ dir). */
const BLACKBOX_LOG_PATTERNS = [
  '*-exploit_*.log',
  '*validate-authentication_*.log',
];

/**
 * Match a filename against a list of glob patterns.
 * Supports only `*` (any chars) and `?` (single char).
 */
function matchGlob(filename: string, patterns: string[]): boolean {
  return patterns.some((pattern) => {
    const re = new RegExp(
      `^${pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.')}$`,
    );
    return re.test(filename);
  });
}

/** Expand glob patterns in a directory, returning full paths of existing files. */
function expandGlobs(dir: string, patterns: string[]): string[] {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((name) => matchGlob(name, patterns))
    .map((name) => path.join(dir, name))
    .filter((p) => fs.statSync(p).isFile());
}

export interface CleanOptions {
  workspace: string;
  repo: string;
}

export async function clean(opts: CleanOptions): Promise<void> {
  p.intro('Shannon Clean');

  // 1. Resolve paths
  const workspacesDir = getWorkspacesDir();
  const workspaceDir = path.join(workspacesDir, opts.workspace);
  const repoMount = resolveRepo(opts.repo);
  const deliverablesPath = path.join(repoMount.hostPath, '.shannon', 'deliverables');

  // 2. Validate directories exist
  if (!fs.existsSync(workspaceDir)) {
    console.error(`ERROR: Workspace not found: ${workspaceDir}`);
    process.exit(1);
  }
  if (!fs.existsSync(deliverablesPath)) {
    console.error(`ERROR: Deliverables directory not found: ${deliverablesPath}`);
    console.error('Run a whitebox scan first.');
    process.exit(1);
  }

  // 3. Scan for blackbox files
  const agentsDir = path.join(workspaceDir, 'agents');
  const filesToDelete = [
    ...expandGlobs(deliverablesPath, BLACKBOX_DELIVERABLE_PATTERNS),
    ...expandGlobs(agentsDir, BLACKBOX_LOG_PATTERNS),
  ];

  const dirsToDelete: string[] = [];
  for (const subdir of ['.playwright', '.playwright-cli']) {
    const full = path.join(workspaceDir, subdir);
    if (fs.existsSync(full)) {
      dirsToDelete.push(full);
    }
  }

  const workflowLog = path.join(workspaceDir, 'workflow.log');
  const hasWorkflowLog = fs.existsSync(workflowLog) && fs.statSync(workflowLog).size > 0;

  // 4. Check if there's anything to clean
  if (filesToDelete.length === 0 && dirsToDelete.length === 0 && !hasWorkflowLog) {
    p.log.info('No blackbox results found. Nothing to clean.');
    p.outro('Done.');
    return;
  }

  // 5. Preview
  p.log.info('Files to be removed:');
  for (const f of filesToDelete) {
    p.log.warn(`  ${path.relative(workspacesDir, f)}`);
  }
  for (const d of dirsToDelete) {
    p.log.warn(`  ${path.relative(workspacesDir, d)}/`);
  }
  if (hasWorkflowLog) {
    p.log.warn(`  ${path.relative(workspacesDir, workflowLog)} (truncate)`);
  }

  // 6. Confirm
  const confirmed = await p.confirm({
    message: `Delete ${filesToDelete.length} file(s)${dirsToDelete.length > 0 ? ` and ${dirsToDelete.length} director${dirsToDelete.length === 1 ? 'y' : 'ies'}` : ''}?`,
  });
  if (p.isCancel(confirmed) || !confirmed) {
    p.cancel('Aborted.');
    process.exit(0);
  }

  // 7. Delete files
  let deleted = 0;
  let failed = 0;
  for (const f of filesToDelete) {
    try {
      fs.unlinkSync(f);
      deleted++;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      p.log.warn(`Failed to delete ${path.basename(f)}: ${msg}`);
      failed++;
    }
  }

  // 8. Delete directories
  for (const d of dirsToDelete) {
    try {
      fs.rmSync(d, { recursive: true, force: true });
      deleted++;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      p.log.warn(`Failed to delete ${path.basename(d)}: ${msg}`);
      failed++;
    }
  }

  // 9. Truncate workflow.log
  if (hasWorkflowLog) {
    try {
      fs.writeFileSync(workflowLog, '');
      deleted++;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      p.log.warn(`Failed to truncate workflow.log: ${msg}`);
      failed++;
    }
  }

  // 10. Summary
  p.log.success(`Cleaned ${deleted} item(s)${failed > 0 ? ` (${failed} failed)` : ''}.`);
  p.outro('Done. Ready to re-run blackbox scan.');
}
```

- [x] **Step 2: Verify TypeScript compiles**

Run: `pnpm --filter @keygraph/shannon run check`
Expected: No type errors

- [x] **Step 3: Commit**

```bash
git add apps/cli/src/commands/clean.ts
git commit -m "feat(cli): add clean command module"
```

---

### Task 2: Register `clean` command in `apps/cli/src/index.ts`

**Files:**
- Modify: `apps/cli/src/index.ts`

Add the `clean` command to the dispatcher and help text.

- [x] **Step 1: Add import**

At the top of `apps/cli/src/index.ts`, add to the existing imports (after the `stop` import on line 21):

```typescript
import { clean } from './commands/clean.js';
```

- [x] **Step 2: Add `clean` case to command switch**

In the `switch (command)` block (after the `stop` case around line 248), add. Note: `args` is `process.argv.slice(2)`, so `args[0]` is `clean` and sub-args start at `args[1]`:

```typescript
  case 'clean': {
    const cleanArgs = args.slice(1);
    let cleanWorkspace: string | undefined;
    let cleanRepo: string | undefined;
    for (let i = 0; i < cleanArgs.length; i++) {
      if ((cleanArgs[i] === '-w' || cleanArgs[i] === '--workspace') && cleanArgs[i + 1]) {
        cleanWorkspace = cleanArgs[i + 1];
        i++;
      } else if ((cleanArgs[i] === '-r' || cleanArgs[i] === '--repo') && cleanArgs[i + 1]) {
        cleanRepo = cleanArgs[i + 1];
        i++;
      } else if (cleanArgs[i] === '--blackbox') {
        // Accepted but no-op (default behavior)
      }
    }
    if (!cleanWorkspace) {
      console.error('ERROR: Workspace name is required');
      console.error(`Usage: ${getMode() === 'local' ? './shannon' : 'npx @keygraph/shannon'} clean -w <workspace> -r <repo>`);
      process.exit(1);
    }
    if (!cleanRepo) {
      console.error('ERROR: Repository path is required');
      console.error(`Usage: ${getMode() === 'local' ? './shannon' : 'npx @keygraph/shannon'} clean -w <workspace> -r <repo>`);
      process.exit(1);
    }
    await clean({ workspace: cleanWorkspace, repo: cleanRepo });
    break;
  }
```

- [x] **Step 3: Add `clean` to help text**

In `showHelp()`, add after the `stop` line (around line 72):

```
  ${prefix} clean -w <workspace> -r <repo>             Clean blackbox scan results [--blackbox]
```

- [x] **Step 4: Verify TypeScript compiles**

Run: `pnpm --filter @keygraph/shannon run check`
Expected: No type errors

- [x] **Step 5: Build and verify help output**

Run: `pnpm run build && node apps/cli/dist/index.mjs help`
Expected: Help text shows `clean` command with description

- [x] **Step 6: Commit**

```bash
git add apps/cli/src/index.ts
git commit -m "feat(cli): register clean command in dispatcher and help"
```

---

### Task 3: Manual integration test

**Files:** None (manual verification)

- [x] **Step 1: Verify clean detects no blackbox files on a whitebox-only workspace**

```bash
pnpm run build
node apps/cli/dist/index.mjs clean -w ads_oa_fe_whitebox-1780646286202 -r /root/code/ads_oa_fe/
```

Expected (if blackbox deliverables still exist): Shows preview of files to delete, prompts for confirmation. Press `n` to abort.

Expected (if blackbox deliverables were already manually cleaned): Shows "No blackbox results found. Nothing to clean."

- [x] **Step 2: Verify unknown workspace errors cleanly**

```bash
node apps/cli/dist/index.mjs clean -w nonexistent-workspace -r /root/code/ads_oa_fe/
```

Expected: `ERROR: Workspace not found: .../workspaces/nonexistent-workspace`

- [x] **Step 3: Verify missing repo errors cleanly**

```bash
node apps/cli/dist/index.mjs clean -w ads_oa_fe_whitebox-1780646286202 -r /nonexistent/path
```

Expected: `ERROR: Repository not found: /nonexistent/path`
