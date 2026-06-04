// Copyright (C) 2025 Keygraph, Inc.
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License version 3
// as published by the Free Software Foundation.

/**
 * Knowledge store for inter-agent communication
 *
 * Persists shared knowledge between agents in the pipeline as a JSON file
 * in the workspace audit directory. Agents write their findings after
 * completing and read the accumulated knowledge before starting.
 */

import { fs } from 'zx';

import type { SessionMetadata } from '../types/audit.js';
import type { ActivityLogger } from '../types/activity-logger.js';
import type { SharedKnowledge } from '../types/shared-knowledge.js';
import { EMPTY_KNOWLEDGE } from '../types/shared-knowledge.js';
import { generateAuditPath } from './utils.js';

const KNOWLEDGE_FILE = 'shared-knowledge.json';

/**
 * Get the path to the shared knowledge file for a session.
 */
function knowledgePath(sessionMetadata: SessionMetadata): string {
  const auditPath = generateAuditPath(sessionMetadata);
  return `${auditPath}/${KNOWLEDGE_FILE}`;
}

/**
 * Load shared knowledge from disk.
 * Returns empty knowledge if file does not exist.
 */
export async function loadSharedKnowledge(
  sessionMetadata: SessionMetadata,
  logger: ActivityLogger,
): Promise<SharedKnowledge> {
  const filePath = knowledgePath(sessionMetadata);

  if (!(await fs.pathExists(filePath))) {
    logger.info('No shared knowledge file found, returning empty knowledge');
    return EMPTY_KNOWLEDGE;
  }

  try {
    const content = await fs.readFile(filePath, 'utf8');
    const knowledge = JSON.parse(content) as SharedKnowledge;
    logger.info('Loaded shared knowledge from disk');
    return knowledge;
  } catch (error) {
    const errMsg = error instanceof Error ? error.message : String(error);
    logger.warn(`Failed to parse shared knowledge file: ${errMsg}`);
    return EMPTY_KNOWLEDGE;
  }
}

/**
 * Save complete shared knowledge to disk.
 * Overwrites any existing file.
 */
export async function saveSharedKnowledge(
  sessionMetadata: SessionMetadata,
  knowledge: SharedKnowledge,
  logger: ActivityLogger,
): Promise<void> {
  const filePath = knowledgePath(sessionMetadata);

  try {
    await fs.writeFile(filePath, JSON.stringify(knowledge, null, 2), 'utf8');
    logger.info(`Saved shared knowledge to ${filePath}`);
  } catch (error) {
    const errMsg = error instanceof Error ? error.message : String(error);
    logger.warn(`Failed to save shared knowledge: ${errMsg}`);
  }
}

/**
 * Update shared knowledge by merging a partial update into the existing data.
 * Loads existing knowledge, merges the update, and saves back to disk.
 */
export async function updateSharedKnowledge(
  sessionMetadata: SessionMetadata,
  update: Partial<SharedKnowledge>,
  logger: ActivityLogger,
): Promise<void> {
  const existing = await loadSharedKnowledge(sessionMetadata, logger);
  const merged: SharedKnowledge = { ...existing, ...update };
  await saveSharedKnowledge(sessionMetadata, merged, logger);
}
