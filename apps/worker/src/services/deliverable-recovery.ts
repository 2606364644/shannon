// Copyright (C) 2025 Keygraph, Inc.
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License version 3
// as published by the Free Software Foundation.

import { fs, path } from 'zx';

import { DEFAULT_DELIVERABLES_SUBDIR } from '../paths.js';
import { AGENTS } from '../session-manager.js';
import type { ActivityLogger } from '../types/activity-logger.js';
import type { AgentName } from '../types/agents.js';

const MAX_FILE_AGE_MS = 60 * 60 * 1000;

interface RecoveryCandidate {
  filepath: string;
  label: string;
}

function buildSearchPaths(filename: string, deliverablesPath: string): RecoveryCandidate[] {
  const depth = DEFAULT_DELIVERABLES_SUBDIR.split('/').length;
  let repoPath = deliverablesPath;
  for (let i = 0; i < depth; i++) repoPath = path.dirname(repoPath);

  return [
    { filepath: path.join(deliverablesPath, filename), label: 'deliverables dir' },
    { filepath: path.join(repoPath, filename), label: 'repo root' },
    { filepath: path.join('/tmp', filename), label: '/tmp' },
    { filepath: path.join(path.dirname(deliverablesPath), filename), label: 'parent of deliverables dir' },
  ];
}

async function isFreshFile(filepath: string): Promise<boolean> {
  try {
    const stat = await fs.stat(filepath);
    return Date.now() - stat.mtimeMs < MAX_FILE_AGE_MS;
  } catch {
    return false;
  }
}

export async function attemptDeliverableRecovery(
  agentName: AgentName,
  deliverablesPath: string,
  logger: ActivityLogger,
): Promise<boolean> {
  const agent = AGENTS[agentName];
  if (!agent) return false;

  const filename = agent.deliverableFilename;
  const candidates = buildSearchPaths(filename, deliverablesPath);

  for (const candidate of candidates) {
    if (!(await fs.pathExists(candidate.filepath))) continue;
    if (!(await isFreshFile(candidate.filepath))) {
      logger.warn(`Found stale deliverable at ${candidate.label}, skipping`, {
        path: candidate.filepath,
      });
      continue;
    }

    try {
      await fs.ensureDir(deliverablesPath);
      const targetPath = path.join(deliverablesPath, filename);
      await fs.copy(candidate.filepath, targetPath);
      await fs.remove(candidate.filepath);
      logger.info(`Recovered ${filename} from ${candidate.label}`, {
        from: candidate.filepath,
        to: targetPath,
      });
      return true;
    } catch (moveError) {
      const msg = moveError instanceof Error ? moveError.message : String(moveError);
      logger.warn(`Failed to move deliverable from ${candidate.label}: ${msg}`);
    }
  }

  logger.info(`No recoverable deliverable found for ${agentName}`, { filename });
  return false;
}
