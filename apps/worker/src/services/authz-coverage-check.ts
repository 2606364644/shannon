// Copyright (C) 2025 Keygraph, Inc.
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License version 3
// as published by the Free Software Foundation.

/**
 * Deterministic authz coverage reconciliation.
 *
 * Mechanically computes which USER-authenticated endpoints (from recon) were
 * NOT judged by the authz agent — neither flagged vulnerable in the queue nor
 * recorded safe. Closes the false-negative gap for endpoints whose only vector
 * is a non-object-id selector (e.g. `brokerage`) that recon may not have
 * surfaced as a Section 8 candidate.
 *
 * Pure data reconciliation — no LLM, no Temporal imports. Reads three
 * deliverables produced earlier in the pipeline (recon_endpoints.json,
 * authz_exploitation_queue.json, authz_safe_vectors.json) and returns a report.
 * Missing inputs yield a non-fatal "skipped" report rather than an error,
 * because older workspaces may predate this instrumentation.
 */

import path from 'node:path';

import { readJson } from '../utils/file-io.js';

interface ReconEndpoint {
  method: string;
  path: string;
  required_role: string;
}

interface QueueFile {
  vulnerabilities?: ReadonlyArray<{ endpoint?: string }>;
}

interface CoverageReport {
  totalUserEndpoints: number;
  coveredCount: number;
  uncovered: string[];
  missingDataFiles: string[];
}

/**
 * Treat any authenticated non-admin role as USER-scoped for coverage purposes.
 * Excludes anon/public and explicit admin/staff tiers, which authz does not
 * horizontally test. The token set is intentionally narrow — when uncertain,
 * the endpoint is excluded from the universe (under-coverage is safe here
 * because the goal is to catch missed USER endpoints, not enumerate all roles).
 */
function isUserRole(role: string): boolean {
  const lower = role.trim().toLowerCase();
  if (lower === 'user' || lower === 'authenticated' || lower === 'auth') return true;
  return lower.includes('user') && !lower.includes('admin');
}

/**
 * Normalize an endpoint string into a comparable path key: strip a leading
 * HTTP method if present, remove `{param}` placeholders, collapse slashes,
 * drop a trailing slash, and lowercase. Method-insensitive by design — the
 * coverage check cares whether a path was analyzed, not which verb was used.
 */
function normPath(raw: string): string {
  const matched = raw.trim().match(/^\S+\s+(\/.*)$/);
  const parsed = matched?.[1] ?? raw.trim();
  return parsed
    .replace(/\{[^}]+\}/g, '')
    .replace(/\/+/g, '/')
    .replace(/\/$/, '')
    .toLowerCase();
}

/**
 * True if `userPath` is plausibly covered by any entry in `covered`.
 * Lenient on purpose: recon may record a path with `{id}` placeholders while
 * the queue records a concrete path (or vice-versa), so a prefix relationship
 * either direction counts as covered. This biases toward "covered", keeping
 * the uncovered list free of method/placeholder false alarms.
 */
function isCovered(userPath: string, covered: ReadonlySet<string>): boolean {
  if (covered.has(userPath)) return true;
  for (const c of covered) {
    if (c && (userPath.startsWith(`${c}/`) || c.startsWith(`${userPath}/`))) return true;
  }
  return false;
}

async function readOptional<T>(filePath: string): Promise<T | null> {
  try {
    return await readJson<T>(filePath);
  } catch {
    return null;
  }
}

export async function checkAuthzCoverage(dir: string): Promise<CoverageReport> {
  const missingDataFiles: string[] = [];

  // F: the USER endpoint universe from recon.
  const endpoints = await readOptional<ReconEndpoint[]>(path.join(dir, 'recon_endpoints.json'));
  if (!endpoints) {
    return {
      totalUserEndpoints: 0,
      coveredCount: 0,
      uncovered: [],
      missingDataFiles: ['recon_endpoints.json'],
    };
  }

  const userEndpoints = endpoints
    .filter((e) => isUserRole(e.required_role))
    .map((e) => `${e.method.toUpperCase()} ${normPath(e.path)}`);

  // C: endpoints already judged — vulnerable (queue) or safe (safe_vectors).
  const covered = new Set<string>();

  const queue = await readOptional<QueueFile>(path.join(dir, 'authz_exploitation_queue.json'));
  if (!queue) {
    missingDataFiles.push('authz_exploitation_queue.json');
  } else {
    for (const v of queue.vulnerabilities ?? []) {
      if (v.endpoint) covered.add(normPath(v.endpoint));
    }
  }

  const safe = await readOptional<string[]>(path.join(dir, 'authz_safe_vectors.json'));
  if (!safe) {
    missingDataFiles.push('authz_safe_vectors.json');
  } else {
    for (const subject of safe) covered.add(normPath(subject));
  }

  // G = F \ C: USER endpoints with no verdict. The path is the second token onward.
  const uncovered = userEndpoints.filter((ep) => {
    const epPath = ep.split(' ').slice(1).join(' ');
    return !isCovered(epPath, covered);
  });

  return {
    totalUserEndpoints: userEndpoints.length,
    coveredCount: userEndpoints.length - uncovered.length,
    uncovered,
    missingDataFiles,
  };
}
