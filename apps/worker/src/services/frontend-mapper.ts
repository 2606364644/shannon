// Copyright (C) 2025 Keygraph, Inc.
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License version 3
// as published by the Free Software Foundation.

/**
 * Frontend route mapper
 *
 * Maps frontend routes to their data sources and API calls to identify
 * potential multi-step attack chains (e.g., stored XSS via user input →
 * API storage → admin panel rendering).
 */

import { fs } from 'zx';

import type { ActivityLogger } from '../types/activity-logger.js';

export interface FrontendRoute {
  path: string;
  component: string;
  authenticated: boolean;
  apiCalls: readonly ApiCall[];
  userInputs: readonly UserInputPoint[];
}

export interface ApiCall {
  endpoint: string;
  method: string;
  purpose: string;
  dataFlow: readonly string[];
}

export interface UserInputPoint {
  type: 'url-param' | 'query-param' | 'body' | 'header';
  field: string;
  sanitization?: string;
}

export interface XssAttackChain {
  entryPoint: string;
  storageEndpoint: string;
  renderEndpoint: string;
  sink: string;
  confidence: 'high' | 'medium' | 'low';
}

export interface FrontendAnalysisResult {
  routes: readonly FrontendRoute[];
  xssChains: readonly XssAttackChain[];
}

/**
 * Map frontend routes to their API dependencies.
 * Scans frontend source files for route definitions and API call patterns.
 */
export async function mapFrontendRoutes(codebasePath: string, logger: ActivityLogger): Promise<FrontendAnalysisResult> {
  const routes: FrontendRoute[] = [];

  // 1. Detect frontend framework
  const framework = await detectFrontendFramework(codebasePath, logger);
  logger.info(`Detected frontend framework: ${framework}`);

  // 2. Find route definition files
  const routeFiles = await findRouteFiles(codebasePath, framework, logger);
  if (routeFiles.length === 0) {
    logger.info('No frontend route files found');
    return { routes: [], xssChains: [] };
  }

  logger.info(`Found ${routeFiles.length} route file(s): ${routeFiles.join(', ')}`);

  // 3. Parse routes from files
  for (const file of routeFiles) {
    const fileRoutes = await parseRoutes(file, framework, logger);
    routes.push(...fileRoutes);
  }

  // 4. Identify XSS attack chains from collected routes
  const xssChains = identifyXssChains(routes);

  logger.info(`Mapped ${routes.length} route(s), identified ${xssChains.length} potential XSS chain(s)`);

  return { routes, xssChains };
}

/**
 * Detect which frontend framework is in use.
 */
async function detectFrontendFramework(
  codebasePath: string,
  logger: ActivityLogger,
): Promise<'angular' | 'react' | 'vue' | 'unknown'> {
  const packageJsonPath = `${codebasePath}/package.json`;
  if (!(await fs.pathExists(packageJsonPath))) {
    return 'unknown';
  }

  try {
    const content = await fs.readFile(packageJsonPath, 'utf8');
    if (content.includes('@angular/core')) return 'angular';
    if (content.includes('react') || content.includes('next')) return 'react';
    if (content.includes('vue') || content.includes('nuxt')) return 'vue';
  } catch (error) {
    const errMsg = error instanceof Error ? error.message : String(error);
    logger.warn(`Error reading package.json: ${errMsg}`);
  }

  return 'unknown';
}

/**
 * Find frontend route definition files based on framework.
 */
async function findRouteFiles(codebasePath: string, framework: string, _logger: ActivityLogger): Promise<string[]> {
  const files: string[] = [];

  const searchDirs = [
    `${codebasePath}/frontend/src`,
    `${codebasePath}/frontend`,
    `${codebasePath}/src/app`,
    `${codebasePath}/src`,
    codebasePath,
  ];

  const filenamePatterns: Record<string, string[]> = {
    angular: ['app-routing.module.ts', 'app.routes.ts', 'routes.ts'],
    react: ['routes.tsx', 'routes.ts', 'router.tsx', 'router.ts', 'App.tsx'],
    vue: ['router.ts', 'router.js', 'index.ts', 'index.js'],
    unknown: ['routes.ts', 'routes.tsx', 'router.ts', 'router.tsx', 'app.routes.ts'],
  };

  const patterns = filenamePatterns[framework] ?? filenamePatterns.unknown ?? [];

  for (const dir of searchDirs) {
    if (!(await fs.pathExists(dir))) continue;
    for (const pattern of patterns) {
      const filePath = `${dir}/${pattern}`;
      if (await fs.pathExists(filePath)) {
        files.push(filePath);
      }
    }
  }

  return files;
}

/**
 * Parse route definitions from a file.
 * Extracts route paths, components, and API call patterns.
 */
async function parseRoutes(filePath: string, framework: string, logger: ActivityLogger): Promise<FrontendRoute[]> {
  const routes: FrontendRoute[] = [];

  try {
    const content = await fs.readFile(filePath, 'utf8');

    // Extract route patterns — framework-specific regex
    const routeRegexMap: Record<string, RegExp> = {
      angular: /path\s*:\s*['"`]([^'"`]+)['"`][^}]*?component\s*:\s*([A-Za-z_][A-Za-z0-9_]*)/g,
      react: /path\s*:\s*['"`]([^'"`]+)['"`][^}]*?(?:element|component)\s*:\s*(?:<|([A-Za-z_][A-Za-z0-9_]*))/g,
      vue: /path\s*:\s*['"`]([^'"`]+)['"`][^}]*?(?:component|name)\s*:\s*['"`]?([A-Za-z_][A-Za-z0-9_]*)/g,
    };

    const regex = routeRegexMap[framework] ?? routeRegexMap.angular ?? /path\s*:\s*['"`]([^'"`]+)['"`]/g;
    let match: RegExpExecArray | null;
    while (true) {
      match = regex.exec(content);
      if (match === null) break;
      const path = match[1];
      const component = match[2] ?? 'Unknown';
      if (path) {
        routes.push({
          path,
          component,
          authenticated:
            content.includes('AuthGuard') || content.includes('canActivate') || content.includes('requireAuth'),
          apiCalls: [],
          userInputs: [],
        });
      }
    }
  } catch (error) {
    const errMsg = error instanceof Error ? error.message : String(error);
    logger.warn(`Error parsing routes from ${filePath}: ${errMsg}`);
  }

  return routes;
}

/**
 * Identify potential XSS attack chains from frontend routes.
 * Looks for routes where user input flows through an API to a rendering endpoint.
 */
export function identifyXssChains(routes: readonly FrontendRoute[]): XssAttackChain[] {
  const chains: XssAttackChain[] = [];

  // Find routes with user inputs that POST to an API
  const inputRoutes = routes.filter((r) => r.userInputs.length > 0 || r.apiCalls.some((a) => a.method === 'POST'));

  // Find routes that render data from GET APIs
  const renderRoutes = routes.filter((r) => r.apiCalls.some((a) => a.method === 'GET'));

  for (const inputRoute of inputRoutes) {
    for (const apiCall of inputRoute.apiCalls) {
      if (apiCall.method !== 'POST') continue;

      // Check if any render route fetches from the same API endpoint family
      for (const renderRoute of renderRoutes) {
        for (const renderApi of renderRoute.apiCalls) {
          if (renderApi.method !== 'GET') continue;

          // Check if storage and retrieval endpoints share a base path
          const storageBase = extractBasePath(apiCall.endpoint);
          const renderBase = extractBasePath(renderApi.endpoint);

          if (storageBase && renderBase && storageBase === renderBase) {
            chains.push({
              entryPoint: inputRoute.path,
              storageEndpoint: apiCall.endpoint,
              renderEndpoint: renderRoute.path,
              sink: renderRoute.component,
              confidence: 'medium',
            });
          }
        }
      }
    }
  }

  return chains;
}

/**
 * Extract the base path from an API endpoint (e.g., /api/Videos from /api/Videos/:id).
 */
function extractBasePath(endpoint: string): string {
  const parts = endpoint.split('/');
  return parts.filter((p) => !p.startsWith(':')).join('/');
}
