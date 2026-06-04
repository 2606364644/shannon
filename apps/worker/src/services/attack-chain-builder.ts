// Copyright (C) 2025 Keygraph, Inc.
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License version 3
// as published by the Free Software Foundation.

/**
 * Attack chain builder
 *
 * Assembles multi-step attack chains from shared knowledge accumulated
 * by prior agents (framework analysis, endpoint inventory, frontend routes,
 * vulnerability findings).
 */

import type { ActivityLogger } from '../types/activity-logger.js';
import type { SharedKnowledge } from '../types/shared-knowledge.js';
import type { AttackChain } from './route-chain-builder.js';
import { buildAttackChainsFromAnalysis } from './route-chain-builder.js';

export type { AttackChain, AttackChainStep } from './route-chain-builder.js';

/**
 * Build attack chains from shared knowledge.
 * Combines framework analysis, endpoint inventory, frontend routes,
 * and vulnerability findings into complete attack scenarios.
 */
export async function buildAttackChains(
  knowledge: SharedKnowledge,
  logger: ActivityLogger,
): Promise<AttackChain[]> {
  const frameworkEndpoints = knowledge.frameworkAnalysis?.inferredEndpoints ?? [];
  const frontendRoutes = knowledge.frontendRoutes?.routes ?? [];
  const xssChains = knowledge.frontendRoutes?.xssVectors ?? [];

  // 1. Build chains from framework + frontend correlation
  const analysisChains = buildAttackChainsFromAnalysis(frameworkEndpoints, frontendRoutes, xssChains, logger);

  // 2. Enhance chains with vulnerability context if available
  const vulnKnowledge = knowledge.vulnerabilityContext;
  if (vulnKnowledge) {
    for (const chain of analysisChains) {
      // Check if any endpoint in this chain has confirmed vulnerabilities
      for (const step of chain.steps) {
        const vulnEntries = vulnKnowledge.endpointVulnerabilities[step.endpoint];
        if (vulnEntries && vulnEntries.length > 0) {
          const confirmed = vulnEntries.some((v) => v.confirmed);
          if (confirmed && chain.confidence !== 'confirmed') {
            // Upgrade confidence — mutation of the chain object is safe here
            // because these are freshly created in this function
            (chain as { confidence: string }).confidence = 'confirmed';
          }
        }
      }
    }
  }

  logger.info(`Built ${analysisChains.length} attack chain(s) from shared knowledge`);
  return analysisChains;
}
