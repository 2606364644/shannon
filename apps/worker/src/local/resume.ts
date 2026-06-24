// Copyright (C) 2025 Keygraph, Inc.
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License version 3
// as published by the Free Software Foundation.

/**
 * Whitebox resume — load completed-agent state from an existing workspace so the
 * local runner can skip finished phases on `-w <workspace>` re-runs.
 *
 * Mirrors the temporal resume loader but is Temporal-free: throws PentestError
 * instead of ApplicationFailure, skips URL validation because whitebox is offline,
 * and returns null when nothing completed.
 */

import path from 'node:path';
import { deliverablesDir, WORKSPACES_DIR } from '../paths.js';
import { PentestError } from '../services/error-handling.js';
import { executeGitCommandWithRetry } from '../services/git-manager.js';
import { AGENTS } from '../session-manager.js';
import { findLatestCommit, restoreGitCheckpoint } from '../temporal/activities.js';
import { type AgentName, ALL_AGENTS } from '../types/agents.js';
import { ALL_VULN_CLASSES } from '../types/config.js';
import { ErrorCode } from '../types/errors.js';
import { fileExists, readJson } from '../utils/file-io.js';
import type { ConsoleActivityLogger } from './console-logger.js';

/** Agents a whitebox run executes, in expected completion order. */
export const WHITEBOX_EXPECTED_AGENTS: readonly AgentName[] = [
  'pre-recon',
  'recon',
  ...ALL_VULN_CLASSES.map((cls) => `${cls}-vuln` as AgentName),
  'report',
];

/** Resume state loaded from a prior whitebox run's workspace. */
export interface WhiteboxResumeState {
  /** Agent names with status=success and deliverable present on disk. */
  readonly completedAgents: string[];
  /** Non-agent phases previously marked complete (findings-rendering, report-assembly, translation). */
  readonly completedNonAgentPhases: string[];
  /** Git checkpoint hash to reset deliverables back to. */
  readonly checkpointHash: string;
  /** Workflow id of the original run that created the workspace. */
  readonly originalWorkflowId: string;
}

/**
 * Load whitebox resume state from a workspace's session.json.
 *
 * Returns null for a fresh workspace (no agent succeeded yet) so the caller runs
 * the full pipeline. Throws if the workspace is corrupted (success recorded but
 * no recoverable checkpoint) — that needs human intervention, not a silent re-run.
 *
 * @param workspace - Workspace name, also used as the session id.
 * @param repoPath - Target repo path.
 * @param logger - Whitebox console logger.
 */
export async function loadResumeState(
  workspace: string,
  repoPath: string,
  logger: ConsoleActivityLogger,
): Promise<WhiteboxResumeState | null> {
  // 1. Read session.json
  const sessionPath = path.join(WORKSPACES_DIR, workspace, 'session.json');
  if (!(await fileExists(sessionPath))) {
    return null;
  }

  let session: {
    session: { originalWorkflowId?: string; id: string };
    metrics: {
      agents: Record<string, { status: string; checkpoint?: string }>;
      completedNonAgentPhases?: string[];
    };
  };

  try {
    session = await readJson(sessionPath);
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    throw new PentestError(
      `Corrupted session.json in workspace ${workspace}: ${msg}`,
      'validation',
      false,
      { workspace, phase: 'resume' },
      ErrorCode.CONFIG_PARSE_ERROR,
    );
  }

  // 2. Cross-check success agents against deliverables on disk
  const completedAgents: string[] = [];
  const agents = session.metrics.agents;

  for (const agentName of ALL_AGENTS) {
    const agentData = agents[agentName];
    if (!agentData || agentData.status !== 'success') {
      continue;
    }

    const deliverablePath = path.join(deliverablesDir(repoPath), AGENTS[agentName].deliverableFilename);
    if (!(await fileExists(deliverablePath))) {
      logger.warn(`Agent ${agentName} shows success but deliverable missing, will re-run`);
      continue;
    }

    completedAgents.push(agentName);
  }

  // 3. Fresh run — nothing succeeded
  if (completedAgents.length === 0) {
    return null;
  }

  // 4. Collect checkpoints
  const checkpoints = completedAgents
    .map((name) => agents[name]?.checkpoint)
    .filter((hash): hash is string => hash != null);

  if (checkpoints.length === 0) {
    throw new PentestError(
      `Cannot resume workspace ${workspace}: ${completedAgents.length} agent(s) show success ` +
        `(${completedAgents.join(', ')}) but their deliverable checkpoints are missing. ` +
        'Start a fresh run instead.',
      'validation',
      false,
      { workspace, phase: 'resume' },
      ErrorCode.GIT_CHECKPOINT_FAILED,
    );
  }

  // 5. Resolve latest checkpoint commit (fall back to first hash if no git history)
  const deliverablesPath = deliverablesDir(repoPath);
  let checkpointHash: string;
  try {
    checkpointHash = await findLatestCommit(deliverablesPath, checkpoints);
  } catch {
    checkpointHash = checkpoints[0] ?? '';
  }

  logger.info('Resume state loaded', {
    workspace,
    completedAgents: completedAgents.length,
    checkpoint: checkpointHash,
  });

  return {
    completedAgents,
    completedNonAgentPhases: session.metrics.completedNonAgentPhases ?? [],
    checkpointHash,
    originalWorkflowId: session.session.originalWorkflowId || session.session.id,
  };
}

/** Re-export so runner imports resume helpers from one place. */
export { executeGitCommandWithRetry, restoreGitCheckpoint };
