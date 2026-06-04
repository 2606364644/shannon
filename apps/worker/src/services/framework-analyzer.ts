// Copyright (C) 2025 Keygraph, Inc.
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License version 3
// as published by the Free Software Foundation.

/**
 * Framework analyzer service
 *
 * Analyzes codebase for auto-generated REST framework usage (finale-rest,
 * epilogue) and infers endpoints that may not be visible in route definitions.
 */

import { fs } from 'zx';

import type { ActivityLogger } from '../types/activity-logger.js';
import { FRAMEWORK_PATTERNS, type FrameworkPattern } from './framework-patterns.js';

export interface InferredEndpoint {
  method: string;
  path: string;
  source: 'framework-auto-generated' | 'manual';
  model?: string;
  middleware: readonly string[];
  vulnerabilityIndicators: readonly string[];
}

export interface FrameworkAnalysisResult {
  detectedFramework: FrameworkPattern | null;
  inferredEndpoints: readonly InferredEndpoint[];
  recommendations: readonly string[];
}

/**
 * Analyze codebase for auto-generated REST framework usage.
 * Scans source files for framework initialization patterns and infers
 * endpoints based on model configurations.
 */
export async function analyzeFrameworks(
  codebasePath: string,
  logger: ActivityLogger,
): Promise<FrameworkAnalysisResult> {
  // 1. Detect which frameworks are in use
  let detectedFramework: FrameworkPattern | null = null;

  for (const pattern of FRAMEWORK_PATTERNS) {
    const isDetected = await detectFramework(codebasePath, pattern, logger);
    if (isDetected) {
      detectedFramework = pattern;
      logger.info(`Detected framework: ${pattern.name}`);
      break;
    }
  }

  if (!detectedFramework) {
    logger.info('No auto-generated REST framework detected');
    return { detectedFramework: null, inferredEndpoints: [], recommendations: [] };
  }

  // 2. Discover models configured with the framework
  const models = await discoverModels(codebasePath, detectedFramework, logger);
  logger.info(`Found ${models.length} model(s) configured with ${detectedFramework.name}: ${models.join(', ')}`);

  // 3. Generate inferred endpoints from templates
  const inferredEndpoints = generateInferredEndpoints(detectedFramework, models);

  // 4. Build recommendations
  const recommendations = buildRecommendations(detectedFramework, inferredEndpoints);

  return { detectedFramework, inferredEndpoints, recommendations };
}

/**
 * Scan source files for framework initialization patterns.
 */
async function detectFramework(
  codebasePath: string,
  pattern: FrameworkPattern,
  logger: ActivityLogger,
): Promise<boolean> {
  const allPatterns = [...(pattern.detectionPatterns.import ?? []), ...(pattern.detectionPatterns.initialize ?? [])];

  if (allPatterns.length === 0) return false;

  try {
    // Scan server entry point and routes directory
    const candidatePaths = await findSourceFiles(codebasePath);
    for (const filePath of candidatePaths) {
      const content = await fs.readFile(filePath, 'utf8');
      for (const detectionPattern of allPatterns) {
        if (content.includes(detectionPattern)) {
          logger.info(`Framework pattern "${detectionPattern}" found in ${filePath}`);
          return true;
        }
      }
    }
  } catch (error) {
    const errMsg = error instanceof Error ? error.message : String(error);
    logger.warn(`Error scanning for framework ${pattern.name}: ${errMsg}`);
  }

  return false;
}

/**
 * Find relevant source files to scan for framework patterns.
 * Looks in the top-level server file and routes/models directories.
 */
async function findSourceFiles(codebasePath: string): Promise<string[]> {
  const files: string[] = [];
  const candidates = ['server.js', 'server.ts', 'app.js', 'app.ts', 'index.js', 'index.ts'];

  for (const candidate of candidates) {
    const fullPath = `${codebasePath}/${candidate}`;
    if (await fs.pathExists(fullPath)) {
      files.push(fullPath);
    }
  }

  // Scan routes and models directories
  const subdirs = ['routes', 'models', 'api', 'src/routes', 'src/models'];
  for (const subdir of subdirs) {
    const dirPath = `${codebasePath}/${subdir}`;
    if (await fs.pathExists(dirPath)) {
      const dirFiles = await fs.readdir(dirPath);
      for (const file of dirFiles) {
        if (file.endsWith('.js') || file.endsWith('.ts')) {
          files.push(`${dirPath}/${file}`);
        }
      }
    }
  }

  return files;
}

/**
 * Discover model names configured with the framework.
 * Extracts model names from `finale.resource({ model: Model })` or similar patterns.
 */
async function discoverModels(
  codebasePath: string,
  pattern: FrameworkPattern,
  logger: ActivityLogger,
): Promise<string[]> {
  const models: string[] = [];
  const configPatterns = pattern.detectionPatterns.config ?? [];

  if (configPatterns.length === 0) return models;

  try {
    const sourceFiles = await findSourceFiles(codebasePath);
    for (const filePath of sourceFiles) {
      const content = await fs.readFile(filePath, 'utf8');

      // Match patterns like: finale.resource({ model: ModelName })
      // or: finale.resource(sequelize, { model: ModelName })
      const modelRegex = /\.resource\([^)]*?model\s*:\s*([A-Za-z_][A-Za-z0-9_]*)/g;
      let match: RegExpExecArray | null;
      while ((match = modelRegex.exec(content)) !== null) {
        const modelName = match[1];
        if (modelName && !models.includes(modelName)) {
          models.push(modelName);
          logger.info(`Discovered model: ${modelName} in ${filePath}`);
        }
      }

      // Also match: new Resource endpoint path patterns
      const endpointRegex = /\.resource\([^)]*?endpoints\s*:\s*\[([^\]]+)\]/g;
      while ((match = endpointRegex.exec(content)) !== null) {
        const endpointsStr = match[1];
        if (endpointsStr) {
          const pathMatches = endpointsStr.match(/['"`][/][^'"`]+['"`]/g);
          if (pathMatches) {
            for (const p of pathMatches) {
              const cleanPath = p.replace(/['"`]/g, '');
              logger.info(`Discovered resource endpoint path: ${cleanPath} in ${filePath}`);
            }
          }
        }
      }
    }
  } catch (error) {
    const errMsg = error instanceof Error ? error.message : String(error);
    logger.warn(`Error discovering models for ${pattern.name}: ${errMsg}`);
  }

  return models;
}

/**
 * Generate inferred endpoints from framework templates and discovered models.
 */
function generateInferredEndpoints(framework: FrameworkPattern, models: readonly string[]): InferredEndpoint[] {
  const endpoints: InferredEndpoint[] = [];

  for (const model of models) {
    for (const template of framework.endpointTemplates) {
      const basePath = template.pathTemplate.replace('{Model}', model).replace('{resource}', model.toLowerCase());
      for (const method of template.methods) {
        // For collection endpoints (no :id), only GET and POST typically apply
        // For individual endpoints (with :id), all methods apply
        const isCollectionEndpoint = !template.pathTemplate.includes(':id');
        if (isCollectionEndpoint && (method === 'PUT' || method === 'DELETE')) {
          continue;
        }

        endpoints.push({
          method,
          path: basePath,
          source: 'framework-auto-generated',
          model,
          middleware: template.defaultMiddleware,
          vulnerabilityIndicators: framework.vulnerabilityPatterns,
        });
      }
    }
  }

  return endpoints;
}

/**
 * Build security recommendations based on detected framework and endpoints.
 */
function buildRecommendations(framework: FrameworkPattern, endpoints: readonly InferredEndpoint[]): string[] {
  const recommendations: string[] = [
    `Framework ${framework.name} detected — auto-generated endpoints may lack ownership validation`,
  ];

  const deleteEndpoints = endpoints.filter((ep) => ep.method === 'DELETE');
  if (deleteEndpoints.length > 0) {
    recommendations.push(
      `${deleteEndpoints.length} DELETE endpoint(s) auto-generated — verify each has authorization guards`,
    );
  }

  const putEndpoints = endpoints.filter((ep) => ep.method === 'PUT');
  if (putEndpoints.length > 0) {
    recommendations.push(`${putEndpoints.length} PUT endpoint(s) auto-generated — verify role-based access control`);
  }

  recommendations.push(...framework.vulnerabilityPatterns);

  return recommendations;
}
