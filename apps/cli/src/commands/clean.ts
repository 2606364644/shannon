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
const BLACKBOX_LOG_PATTERNS = ['*-exploit_*.log', '*validate-authentication_*.log'];

/**
 * Match a filename against a list of glob patterns.
 * Supports only `*` (any chars) and `?` (single char).
 */
function matchGlob(filename: string, patterns: string[]): boolean {
  return patterns.some((pattern) => {
    const re = new RegExp(
      `^${pattern
        .replace(/[.+^${}()|[\]\\]/g, '\\$&')
        .replace(/\*/g, '.*')
        .replace(/\?/g, '.')}$`,
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
    .filter((fp) => fs.statSync(fp).isFile());
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
