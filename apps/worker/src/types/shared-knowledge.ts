// Copyright (C) 2025 Keygraph, Inc.
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License version 3
// as published by the Free Software Foundation.

/**
 * Shared knowledge types for inter-agent communication
 *
 * Design principle: Recon provides descriptive data only (what protections exist).
 * Vuln agents make their own judgments (whether protections are sufficient).
 * All data flows through file-based persistence — no in-process messaging.
 */

// Re-export inferred endpoint type from framework analyzer
export type { InferredEndpoint } from '../services/framework-analyzer.js';

export interface SharedKnowledge {
  readonly frameworkAnalysis?: FrameworkKnowledge;
  readonly endpointInventory?: EndpointKnowledge;
  readonly frontendRoutes?: FrontendKnowledge;
  readonly vulnerabilityContext?: VulnerabilityKnowledge;
  readonly attackChains?: AttackChainKnowledge;
}

export interface FrameworkKnowledge {
  readonly detectedFrameworks: readonly string[];
  readonly inferredEndpoints: readonly import('../services/framework-analyzer.js').InferredEndpoint[];
  readonly recommendations: readonly string[];
}

export interface EndpointKnowledge {
  readonly endpoints: readonly EndpointSecurityContext[];
}

export interface EndpointSecurityContext {
  readonly path: string;
  readonly methods: readonly string[];
  readonly authentication: string;
  readonly middleware: readonly string[];
  readonly frameworkOrigin: string;
  readonly ownershipValidation: 'present' | 'absent' | 'unknown';
  readonly parameterSources: readonly ParameterSource[];
}

export interface ParameterSource {
  readonly name: string;
  readonly location: 'path' | 'query' | 'body' | 'header';
  readonly controlledBy: 'user' | 'server';
}

export interface FrontendKnowledge {
  readonly routes: readonly import('../services/frontend-mapper.js').FrontendRoute[];
  readonly xssVectors: readonly import('../services/frontend-mapper.js').XssAttackChain[];
}

export interface VulnerabilityKnowledge {
  readonly endpointVulnerabilities: Readonly<Record<string, readonly VulnerabilityEntry[]>>;
  readonly patterns: readonly VulnerabilityPattern[];
}

export interface VulnerabilityEntry {
  readonly type: 'xss' | 'injection' | 'authz' | 'auth' | 'ssrf';
  readonly severity: 'critical' | 'high' | 'medium' | 'low';
  readonly confirmed: boolean;
  readonly relatedEndpoints?: readonly string[];
}

export interface VulnerabilityPattern {
  readonly description: string;
  readonly affectedEndpoints: readonly string[];
  readonly recommendation: string;
}

export interface AttackChainKnowledge {
  readonly chains: readonly import('../services/route-chain-builder.js').AttackChain[];
}

/**
 * Empty shared knowledge object for initialization.
 */
export const EMPTY_KNOWLEDGE: SharedKnowledge = {};
