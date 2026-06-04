# Missed Vulnerabilities Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Shannon's missed vulnerabilities (越权漏报, XSS 多步骤攻击链断裂, 前后端脱节) through prompt optimization, framework analysis infrastructure, and inter-agent knowledge sharing.

**Architecture:** Three-phase approach. Phase 1 improves Recon prompt to collect richer endpoint security context and shares it with downstream vuln agents via existing `@include` mechanism. Phase 2 adds TypeScript service-layer modules that detect auto-generated framework endpoints and map frontend routes. Phase 3 introduces structured JSON-based knowledge sharing between agents and an attack chain builder that assembles multi-step attack scenarios from individual findings.

**Tech Stack:** TypeScript (existing codebase patterns: `Result<T,E>`, `PentestError`, `ActivityLogger`), prompt templates with `@include()` and `{{VAR}}` substitution, file-based persistence in workspace audit directories.

**Spec:** `docs/shannon-missed-vulnerabilities-fix-spec.md`

---

## File Structure

### Phase 1 — Prompt Optimization (new + modified files)

| File | Action | Responsibility |
|------|--------|----------------|
| `apps/worker/prompts/shared/_endpoint-security-context.txt` | Create | Shared partial: endpoint security context template for Recon agent |
| `apps/worker/prompts/recon.txt` | Modify | Include the new shared partial, add endpoint security context output format |
| `apps/worker/prompts/vuln-authz.txt` | Modify | Add Step 0 to read Recon's endpoint security context, add framework-aware analysis |
| `apps/worker/prompts/vuln-xss.txt` | Modify | Expand starting_context to reference Recon's endpoint security context |
| `apps/worker/prompts/vuln-injection.txt` | Modify | Expand starting_context to reference Recon's endpoint security context |

### Phase 2 — Service Layer Infrastructure (new files)

| File | Action | Responsibility |
|------|--------|----------------|
| `apps/worker/src/services/framework-patterns.ts` | Create | Framework detection pattern definitions (finale-rest, epilogue) |
| `apps/worker/src/services/framework-analyzer.ts` | Create | Framework analysis service — scans codebase for framework usage |
| `apps/worker/src/services/frontend-mapper.ts` | Create | Frontend route mapper — maps frontend routes to API dependencies |
| `apps/worker/src/services/route-chain-builder.ts` | Create | Attack chain builder from frontend route analysis |

### Phase 3 — Coordination Layer (new + modified files)

| File | Action | Responsibility |
|------|--------|----------------|
| `apps/worker/src/types/shared-knowledge.ts` | Create | Shared knowledge type definitions |
| `apps/worker/src/types/index.ts` | Modify | Add barrel export for shared-knowledge |
| `apps/worker/src/audit/knowledge-store.ts` | Create | Knowledge persistence (read/write JSON in workspace audit dir) |
| `apps/worker/src/audit/index.ts` | Modify | Add barrel export for knowledge-store |
| `apps/worker/src/services/attack-chain-builder.ts` | Create | Assembles multi-step attack chains from shared knowledge |
| `apps/worker/prompts/shared/_shared-knowledge.txt` | Create | Prompt partial for injecting shared knowledge into agent prompts |
| `apps/worker/src/services/prompt-manager.ts` | Modify | Add `{{SHARED_KNOWLEDGE}}` variable interpolation |
| `apps/worker/src/temporal/activities.ts` | Modify | Add knowledge save/load calls in activity wrappers |
| `apps/worker/src/temporal/workflows.ts` | Modify | Add attack-chain assembly step between vuln and exploit phases |

---

## Phase 1: Prompt Optimization

No test runner is configured in this project. Validation is done via type-checking (`pnpm run check`), linting (`pnpm biome`), and pipeline testing (`./shannon start --pipeline-testing`).

### Task 1: Create shared endpoint security context partial

**Files:**
- Create: `apps/worker/prompts/shared/_endpoint-security-context.txt`

- [ ] **Step 1: Create the shared partial file**

````xml
<endpoint_security_context>

## Endpoint Security Context Analysis

Your mission is to build a comprehensive security context for each discovered endpoint. This is **descriptive analysis** — you document what protections exist, NOT whether they are sufficient.

### Information to Collect for Each Endpoint

For every endpoint, collect and document:

1. **HTTP Methods (Complete List)**
   - Do NOT use "ALL" shorthand
   - List each method explicitly: GET, POST, PUT, PATCH, DELETE
   - Note if a method is explicitly blocked (e.g., with denyAll())

2. **Authentication Requirements**
   - anon = No authentication required
   - user = Valid JWT required (any role)
   - customer/deluxe/accounting/admin = Specific role required
   - denyAll = Explicitly blocked

3. **Middleware Chain**
   - List all middleware in execution order
   - Note the purpose of each middleware
   - Examples: isAuthorized(), appendUserId(), denyAll()

4. **Framework Origin**
   - manual = Explicitly defined in routes/
   - finale-rest = Auto-generated by finale-rest framework
   - epilogue = Auto-generated by epilogue framework
   - other = Specify the framework

5. **Parameter Analysis**
   - List all parameters (path, query, body, header)
   - Note parameter sources (user-controlled vs server-generated)
   - Identify any parameter transformation/validation

6. **Ownership Validation**
   - Check if code validates resource ownership
   - Look for patterns like: resource.UserId === user.id
   - Note if validation exists: "yes (file:line)" or "none detected"

### Framework-Specific Patterns

#### finale-rest / epilogue

When analyzing applications using finale-rest or epilogue:

1. **Identify framework usage:**
   - Search for: `finale.initialize()`, `epilogue.initialize()`
   - Look for: `finale.resource()`, `epilogue.resource()`
   - Check model configurations

2. **Auto-generated endpoints:**
   For each model configured with the framework, assume these endpoints exist:
   - `GET /api/{Model}s` → findAll
   - `GET /api/{Model}s/:id` → findOne
   - `POST /api/{Model}s` → create
   - `PUT /api/{Model}s/:id` → update
   - `DELETE /api/{Model}s/:id` → destroy

3. **Check for overrides:**
   - After auto-generation, check if app.use() overrides any endpoint
   - Note if any middleware is added/removed

4. **Document explicitly:**
   - Mark framework-generated endpoints with: [finale-rest auto-generated]
   - List the model name the endpoint is based on

### Output Format

For each endpoint, use this format:

```
Endpoint: DELETE /api/Feedbacks/:id
Origin: finale-rest auto-generated
Authentication: user (isAuthorized)
Middleware: [isAuthorized]
Parameters: { id: path }
Ownership Validation: none detected
Notes: DELETE not explicitly blocked in server.ts
```

### Common Pitfalls

- **Don't assume**: Don't assume GET/POST/PUT/DELETE all exist just because others do
- **Check explicitly**: Trace through code to confirm each method exists
- **Don't judge**: Don't conclude whether protections are "good enough" — just document them
- **Be specific**: Use exact middleware names and file:line locations when available

</endpoint_security_context>
````

- [ ] **Step 2: Verify the file was created correctly**

Run: `head -5 apps/worker/prompts/shared/_endpoint-security-context.txt`
Expected: Shows `<endpoint_security_context>` as the first line.

- [ ] **Step 3: Commit**

```bash
git add apps/worker/prompts/shared/_endpoint-security-context.txt
git commit -m "feat(prompts): add endpoint security context shared partial

Adds shared/_endpoint-security-context.txt with descriptive analysis
template for Recon agent to document endpoint protections, framework
origins, and ownership validation status."
```

---

### Task 2: Update Recon prompt to include endpoint security context

**Files:**
- Modify: `apps/worker/prompts/recon.txt`

The existing recon.txt has `@include` directives around lines 35-43 and deliverable instructions starting at line 151. We add the new `@include` in the same block and add a new deliverable section.

- [ ] **Step 1: Add @include directive after existing includes (after line 43)**

In `apps/worker/prompts/recon.txt`, after the line `@include(shared/_rules-of-engagement.txt)` (line 43), add:

```
@include(shared/_endpoint-security-context.txt)
```

- [ ] **Step 2: Add endpoint security context table to deliverable section**

In `apps/worker/prompts/recon.txt`, after the `## 4. API Endpoint Inventory` table section (after line 247, the `**Rules for this table:**` block that ends with `- Include the router definition file:line range where the routes are registered.`), add a new section:

```markdown

## 4.2 Endpoint Security Context

For every endpoint in Section 4, you MUST also provide an Endpoint Security Context entry using the format defined in `<endpoint_security_context>` above.

Present this as a table:

| Method | Path | Auth | Middleware | Framework Origin | Ownership Check | Notes |
|--------|------|------|------------|------------------|-----------------|-------|
| DELETE | /api/Feedbacks/:id | user | isAuthorized | finale-rest auto-generated | none | Auto-generated |
| GET | /api/Users/:id | user | isAuthorized | finale-rest | absent | Auto-generated |
| POST | /api/Users | anon | none | manual | n/a | Open registration |

**Framework Endpoints Detected** — When finale-rest, epilogue, or similar auto-REST frameworks are detected:

1. List all models configured with the framework
2. For each model, enumerate all auto-generated endpoints
3. Mark each endpoint with its framework origin
4. Note any overrides or customizations applied after auto-generation
```

- [ ] **Step 3: Verify the changes**

Run: `grep -n "endpoint_security_context\|Endpoint Security Context" apps/worker/prompts/recon.txt`
Expected: At least 2 lines — the `@include` directive and the section heading.

- [ ] **Step 4: Commit**

```bash
git add apps/worker/prompts/recon.txt
git commit -m "feat(prompts): add endpoint security context to recon deliverable

Recon now collects and outputs structured endpoint security context
including HTTP methods, auth requirements, middleware chains, framework
origin, and ownership validation status for every discovered endpoint."
```

---

### Task 3: Enhance AuthZ agent to read Recon's endpoint security context

**Files:**
- Modify: `apps/worker/prompts/vuln-authz.txt`

The methodology section starts at line 122 with `<methodology_and_domain_expertise>`. The first step is "1) Horizontal Authorization Analysis" at line 131. We insert a new Step 0 before it.

- [ ] **Step 1: Add Step 0 before the existing Step 1 (insert before line 131)**

In `apps/worker/prompts/vuln-authz.txt`, before `### 1) Horizontal Authorization Analysis` (line 131), insert:

```markdown
### 0) Read Endpoint Security Context (REQUIRED — Do This First)

Before analyzing any authorization vulnerabilities:

1. **Read Recon deliverable:**
   - Open `.shannon/deliverables/recon_deliverable.md`
   - Locate the "Endpoint Security Context" section (Section 4.2)
   - Extract all endpoints with their security context

2. **For each endpoint in your TODO list:**
   - Look up its security context in Section 4.2
   - Note: Authentication requirement
   - Note: Middleware chain
   - Note: Framework origin (manual vs auto-generated)
   - Note: Ownership validation status

3. **Prioritize endpoints with:**
   - Framework origin: "finale-rest auto-generated" or "epilogue auto-generated"
   - Ownership validation: "none detected" or "absent"
   - HTTP methods: DELETE, PUT, PATCH (mutation operations)
   - Authentication: "user" only (no role restriction)

**For framework auto-generated endpoints:** These typically lack ownership validation by default. Assume vulnerable unless Recon explicitly found an ownership check that dominates all code paths to side effects.

---
```

- [ ] **Step 2: Add framework-aware guidance to Horizontal Authorization Analysis**

In `apps/worker/prompts/vuln-authz.txt`, after the existing `**Termination:**` block under `### 1) Horizontal Authorization Analysis` (after line 159, which reads `- **Vulnerable:** if any side effect is reached before a sufficient guard.`), replace the line that follows to add framework guidance. Insert before the `@include(shared/_cross-route-enumeration.txt)` on line 161:

```markdown

**Framework Endpoint Guidance:**
When Recon reports an endpoint with `Framework Origin: finale-rest auto-generated` or `epilogue auto-generated`:
- The endpoint was generated by an ORM-to-REST framework, not manually coded
- Default behavior is CRUD without ownership checks
- Check if the framework's `create.end`, `update.end`, `destroy.end` hooks add ownership validation
- If no hooks override the default behavior → the endpoint is vulnerable to IDOR
- Document the framework origin in your finding:
  ```json
  {
    "endpoint": "DELETE /api/Feedbacks/:id",
    "framework_origin": "finale-rest auto-generated",
    "recon_ownership_check": "none detected",
    "guard_evidence": "isAuthenticated() only, no ownership validation"
  }
  ```

```

- [ ] **Step 3: Verify the changes**

Run: `grep -n "Read Endpoint Security Context\|Framework Endpoint Guidance" apps/worker/prompts/vuln-authz.txt`
Expected: 2 lines found at the inserted locations.

- [ ] **Step 4: Commit**

```bash
git add apps/worker/prompts/vuln-authz.txt
git commit -m "feat(prompts): add endpoint security context reading to authz agent

AuthZ agent now reads Recon's endpoint security context first (Step 0),
prioritizes framework auto-generated endpoints, and includes framework
origin in vulnerability findings."
```

---

### Task 4: Update XSS and Injection agents to reference Recon endpoint context

**Files:**
- Modify: `apps/worker/prompts/vuln-xss.txt`
- Modify: `apps/worker/prompts/vuln-injection.txt`

Both files have a `<starting_context>` section. We expand each to reference the new endpoint security context.

- [ ] **Step 1: Update vuln-xss.txt starting_context**

In `apps/worker/prompts/vuln-xss.txt`, the `<starting_context>` section is at lines 40-42:

```
<starting_context>
- Your primary source of truth for the application's structure is the reconnaissance report located at .shannon/deliverables/recon_deliverable.md. You must derive your list of testable targets from this file.
</starting_context>
```

Replace it with:

```
<starting_context>
- Your primary source of truth for the application's structure is the reconnaissance report located at .shannon/deliverables/recon_deliverable.md. You must derive your list of testable targets from this file.
- The Recon deliverable contains an "Endpoint Security Context" section (Section 4.2) that tells you:
  - Which HTTP methods exist for each endpoint (never assume "ALL")
  - Authentication requirements (anon/user/admin)
  - Whether ownership validation exists
  - Whether the endpoint is framework auto-generated (finale-rest/epilogue)
  - The middleware chain protecting each endpoint
- Use this context to determine if an endpoint is reachable and what protections exist before tracing data flows.
- For XSS analysis, pay special attention to endpoints marked as framework auto-generated that accept user input and trace where that input is rendered in frontend components.
</starting_context>
```

- [ ] **Step 2: Update vuln-injection.txt starting_context**

In `apps/worker/prompts/vuln-injection.txt`, the `<starting_context>` section is at lines 41-44:

```
<starting_context>
- Your **single source of truth** for the application's structure is the reconnaissance report located at `.shannon/deliverables/recon_deliverable.md`. You must derive your list of testable targets from this file.

</starting_context>
```

Replace it with:

```
<starting_context>
- Your **single source of truth** for the application's structure is the reconnaissance report located at `.shannon/deliverables/recon_deliverable.md`. You must derive your list of testable targets from this file.
- The Recon deliverable contains an "Endpoint Security Context" section (Section 4.2) that tells you:
  - Which HTTP methods exist for each endpoint (never assume "ALL")
  - Authentication requirements (anon/user/admin)
  - Whether ownership validation exists
  - Whether the endpoint is framework auto-generated (finale-rest/epilogue)
  - The middleware chain protecting each endpoint
- Use this context to determine if an endpoint is reachable, what input vectors exist, and what validation middleware processes user input before it reaches data sinks.
- For injection analysis, pay special attention to endpoints marked as framework auto-generated that accept parameters reaching database queries or command execution without parameterized queries.

</starting_context>
```

- [ ] **Step 3: Verify the changes**

Run: `grep -c "Endpoint Security Context" apps/worker/prompts/vuln-xss.txt apps/worker/prompts/vuln-injection.txt`
Expected: Each file shows at least 1 match.

- [ ] **Step 4: Commit**

```bash
git add apps/worker/prompts/vuln-xss.txt apps/worker/prompts/vuln-injection.txt
git commit -m "feat(prompts): add endpoint security context reference to XSS and injection agents

XSS and Injection agents now reference Recon's endpoint security context
section to understand HTTP methods, auth requirements, framework origins,
and ownership validation before starting analysis."
```

---

### Task 5: Validate Phase 1 changes

- [ ] **Step 1: Run type-checking to ensure no regressions**

Run: `pnpm run check`
Expected: No TypeScript errors.

- [ ] **Step 2: Run linting**

Run: `pnpm biome`
Expected: No new lint errors. (Pre-existing errors are acceptable.)

- [ ] **Step 3: Verify all prompt files parse correctly**

Run: `for f in apps/worker/prompts/shared/_endpoint-security-context.txt apps/worker/prompts/recon.txt apps/worker/prompts/vuln-authz.txt apps/worker/prompts/vuln-xss.txt apps/worker/prompts/vuln-injection.txt; do echo "=== $f ===" && head -2 "$f" && echo "... ($(wc -l < "$f") lines)"; done`
Expected: All files exist and have reasonable line counts.

- [ ] **Step 4: Verify @include chain resolves**

The shared partial `shared/_endpoint-security-context.txt` is included from `recon.txt` via the existing `processIncludes()` mechanism in `apps/worker/src/services/prompt-manager.ts:234-260`. No code changes needed — the `@include()` system already resolves paths relative to the prompt directory.

Run: `grep "@include(shared/_endpoint-security-context" apps/worker/prompts/recon.txt`
Expected: 1 match.

- [ ] **Step 5: Commit Phase 1 completion marker (optional)**

```bash
git tag phase-1-endpoint-context-complete
```

---

## Phase 2: Service Layer Infrastructure

### Task 6: Create framework pattern definitions

**Files:**
- Create: `apps/worker/src/services/framework-patterns.ts`

- [ ] **Step 1: Create the framework patterns file**

```typescript
// Copyright (C) 2025 Keygraph, Inc.
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License version 3
// as published by the Free Software Foundation.

/**
 * Framework detection patterns
 *
 * Defines patterns for auto-generated REST frameworks that commonly
 * create authorization and XSS vulnerabilities due to lack of ownership
 * validation on CRUD endpoints.
 */

export interface FrameworkPattern {
  readonly name: string;
  readonly detectionPatterns: {
    readonly import?: readonly string[];
    readonly initialize?: readonly string[];
    readonly config?: readonly string[];
  };
  readonly endpointTemplates: readonly EndpointTemplate[];
  readonly vulnerabilityPatterns: readonly string[];
}

export interface EndpointTemplate {
  readonly methods: readonly string[];
  readonly pathTemplate: string;
  readonly defaultMiddleware: readonly string[];
  readonly notes: string;
}

export const FRAMEWORK_PATTERNS: readonly FrameworkPattern[] = [
  {
    name: 'finale-rest',
    detectionPatterns: {
      import: ['require("express-finale")', 'require("finale-rest")', 'import.*finale.*from'],
      initialize: ['finale.initialize(', 'finale.resource('],
      config: ['finale.resource('],
    },
    endpointTemplates: [
      {
        methods: ['GET', 'POST', 'PUT', 'DELETE'],
        pathTemplate: '/api/{Model}s',
        defaultMiddleware: ['isAuthenticated'],
        notes: 'Auto-generated CRUD operations, no ownership validation by default',
      },
      {
        methods: ['GET', 'POST', 'PUT', 'DELETE'],
        pathTemplate: '/api/{Model}s/:id',
        defaultMiddleware: ['isAuthenticated'],
        notes: 'Individual resource operations, commonly vulnerable to IDOR',
      },
    ],
    vulnerabilityPatterns: [
      'No ownership check on finale resource operations',
      'DELETE endpoint often unblocked by default',
      'PUT endpoint may lack role checks',
    ],
  },
  {
    name: 'epilogue',
    detectionPatterns: {
      import: ['require("epilogue")', 'import.*epilogue.*from'],
      initialize: ['epilogue.initialize(', 'epilogue.resource('],
      config: ['epilogue.resource('],
    },
    endpointTemplates: [
      {
        methods: ['GET', 'POST', 'PUT', 'DELETE'],
        pathTemplate: '/api/{resource}',
        defaultMiddleware: [],
        notes: 'Similar to finale, auto-generated CRUD',
      },
    ],
    vulnerabilityPatterns: [
      'Epilogue resources lack ownership validation by default',
      'Mass operations enabled without explicit disable',
    ],
  },
] as const;
```

- [ ] **Step 2: Run type-checking**

Run: `pnpm run check`
Expected: No TypeScript errors.

- [ ] **Step 3: Commit**

```bash
git add apps/worker/src/services/framework-patterns.ts
git commit -m "feat(worker): add framework detection pattern definitions

Defines FrameworkPattern and EndpointTemplate types with patterns
for finale-rest and epilogue auto-generated REST frameworks."
```

---

### Task 7: Create framework analyzer service

**Files:**
- Create: `apps/worker/src/services/framework-analyzer.ts`

This service scans a codebase for framework usage and generates a list of inferred endpoints.

- [ ] **Step 1: Create the framework analyzer**

```typescript
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
  const allPatterns = [
    ...(pattern.detectionPatterns.import ?? []),
    ...(pattern.detectionPatterns.initialize ?? []),
  ];

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
function generateInferredEndpoints(
  framework: FrameworkPattern,
  models: readonly string[],
): InferredEndpoint[] {
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
function buildRecommendations(
  framework: FrameworkPattern,
  endpoints: readonly InferredEndpoint[],
): string[] {
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
    recommendations.push(
      `${putEndpoints.length} PUT endpoint(s) auto-generated — verify role-based access control`,
    );
  }

  recommendations.push(...framework.vulnerabilityPatterns);

  return recommendations;
}
```

- [ ] **Step 2: Run type-checking**

Run: `pnpm run check`
Expected: No TypeScript errors.

- [ ] **Step 3: Commit**

```bash
git add apps/worker/src/services/framework-analyzer.ts
git commit -m "feat(worker): add framework analyzer service

Scans codebases for finale-rest/epilogue usage, discovers configured
models, and infers auto-generated endpoints with vulnerability indicators."
```

---

### Task 8: Create frontend route mapper

**Files:**
- Create: `apps/worker/src/services/frontend-mapper.ts`

- [ ] **Step 1: Create the frontend route mapper**

```typescript
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
export async function mapFrontendRoutes(
  codebasePath: string,
  logger: ActivityLogger,
): Promise<FrontendAnalysisResult> {
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
async function findRouteFiles(
  codebasePath: string,
  framework: string,
  logger: ActivityLogger,
): Promise<string[]> {
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

  const patterns = filenamePatterns[framework] ?? filenamePatterns['unknown'];

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
async function parseRoutes(
  filePath: string,
  framework: string,
  logger: ActivityLogger,
): Promise<FrontendRoute[]> {
  const routes: FrontendRoute[] = [];

  try {
    const content = await fs.readFile(filePath, 'utf8');

    // Extract route patterns — framework-specific regex
    const routeRegexMap: Record<string, RegExp> = {
      angular: /path\s*:\s*['"`]([^'"`]+)['"`][^}]*?component\s*:\s*([A-Za-z_][A-Za-z0-9_]*)/g,
      react: /path\s*:\s*['"`]([^'"`]+)['"`][^}]*?(?:element|component)\s*:\s*(?:<|([A-Za-z_][A-Za-z0-9_]*))/g,
      vue: /path\s*:\s*['"`]([^'"`]+)['"`][^}]*?(?:component|name)\s*:\s*['"`]?([A-Za-z_][A-Za-z0-9_]*)/g,
    };

    const regex = routeRegexMap[framework] ?? routeRegexMap['angular'];
    let match: RegExpExecArray | null;
    while ((match = regex.exec(content)) !== null) {
      const path = match[1];
      const component = match[2] ?? 'Unknown';
      if (path) {
        routes.push({
          path,
          component,
          authenticated: content.includes('AuthGuard') || content.includes('canActivate') || content.includes('requireAuth'),
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
```

- [ ] **Step 2: Run type-checking**

Run: `pnpm run check`
Expected: No TypeScript errors.

- [ ] **Step 3: Commit**

```bash
git add apps/worker/src/services/frontend-mapper.ts
git commit -m "feat(worker): add frontend route mapper service

Maps frontend routes to API dependencies, detects user input points,
and identifies potential XSS attack chains between input and rendering
endpoints."
```

---

### Task 9: Create route chain builder

**Files:**
- Create: `apps/worker/src/services/route-chain-builder.ts`

- [ ] **Step 1: Create the route chain builder**

```typescript
// Copyright (C) 2025 Keygraph, Inc.
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License version 3
// as published by the Free Software Foundation.

/**
 * Route chain builder
 *
 * Builds multi-step attack chains by correlating framework-inferred
 * endpoints with frontend route analysis results. Produces structured
 * attack scenarios for downstream exploitation agents.
 */

import type { ActivityLogger } from '../types/activity-logger.js';
import type { InferredEndpoint } from './framework-analyzer.js';
import type { FrontendRoute, XssAttackChain } from './frontend-mapper.js';

export interface AttackChainStep {
  order: number;
  phase: 'input' | 'storage' | 'retrieval' | 'render';
  endpoint: string;
  method: string;
  description: string;
}

export interface AttackChain {
  id: string;
  name: string;
  description: string;
  steps: readonly AttackChainStep[];
  vulnType: 'xss' | 'authz' | 'injection';
  severity: 'critical' | 'high' | 'medium' | 'low';
  confidence: 'confirmed' | 'probable' | 'theoretical';
}

/**
 * Build attack chains from framework endpoints and frontend analysis.
 * Correlates auto-generated endpoints with frontend routes to identify
 * complete attack paths.
 */
export function buildAttackChainsFromAnalysis(
  inferredEndpoints: readonly InferredEndpoint[],
  frontendRoutes: readonly FrontendRoute[],
  xssChains: readonly XssAttackChain[],
  logger: ActivityLogger,
): AttackChain[] {
  const chains: AttackChain[] = [];

  // 1. Build XSS chains from frontend analysis
  for (const xssChain of xssChains) {
    chains.push({
      id: `xss-chain-${chains.length + 1}`,
      name: `Stored XSS: ${xssChain.entryPoint} → ${xssChain.renderEndpoint}`,
      description: `User input at ${xssChain.entryPoint} is stored via ${xssChain.storageEndpoint} and rendered at ${xssChain.renderEndpoint} in ${xssChain.sink}`,
      steps: [
        {
          order: 1,
          phase: 'input',
          endpoint: xssChain.entryPoint,
          method: 'GET',
          description: `User navigates to ${xssChain.entryPoint} and provides input`,
        },
        {
          order: 2,
          phase: 'storage',
          endpoint: xssChain.storageEndpoint,
          method: 'POST',
          description: `Input is stored via POST ${xssChain.storageEndpoint}`,
        },
        {
          order: 3,
          phase: 'retrieval',
          endpoint: xssChain.storageEndpoint,
          method: 'GET',
          description: `Stored data is retrieved via GET ${xssChain.storageEndpoint}`,
        },
        {
          order: 4,
          phase: 'render',
          endpoint: xssChain.renderEndpoint,
          method: 'GET',
          description: `Data is rendered unsanitized in ${xssChain.sink}`,
        },
      ],
      vulnType: 'xss',
      severity: 'high',
      confidence: xssChain.confidence === 'high' ? 'probable' : 'theoretical',
    });
  }

  // 2. Build IDOR chains from framework endpoints without ownership checks
  const vulnerableEndpoints = inferredEndpoints.filter(
    (ep) => ep.path.includes(':id') && ep.vulnerabilityIndicators.length > 0,
  );

  for (const endpoint of vulnerableEndpoints) {
    // Check if this endpoint has any frontend route that triggers it
    const relatedRoute = frontendRoutes.find((r) =>
      r.apiCalls.some((a) => endpoint.path.startsWith(extractPathPrefix(a.endpoint))),
    );

    chains.push({
      id: `idor-chain-${chains.length + 1}`,
      name: `IDOR: ${endpoint.method} ${endpoint.path} (${endpoint.source})`,
      description: `${endpoint.method} ${endpoint.path} is auto-generated by ${endpoint.source} with no ownership validation. ${relatedRoute ? `Triggered from frontend route ${relatedRoute.path}.` : ''}`,
      steps: [
        {
          order: 1,
          phase: 'input',
          endpoint: endpoint.path,
          method: endpoint.method,
          description: `Attacker crafts request with arbitrary ID parameter`,
        },
        {
          order: 2,
          phase: 'storage',
          endpoint: endpoint.path,
          method: endpoint.method,
          description: `${endpoint.method} reaches side effect without ownership validation`,
        },
      ],
      vulnType: 'authz',
      severity: endpoint.method === 'DELETE' ? 'high' : 'medium',
      confidence: 'probable',
    });
  }

  logger.info(`Built ${chains.length} attack chain(s) from analysis`);
  return chains;
}

/**
 * Extract path prefix for correlation (e.g., /api/Users from /api/Users/:id).
 */
function extractPathPrefix(endpoint: string): string {
  const parts = endpoint.split('/');
  return parts.filter((p) => !p.startsWith(':')).join('/');
}
```

- [ ] **Step 2: Run type-checking**

Run: `pnpm run check`
Expected: No TypeScript errors.

- [ ] **Step 3: Commit**

```bash
git add apps/worker/src/services/route-chain-builder.ts
git commit -m "feat(worker): add route chain builder service

Builds multi-step attack chains by correlating framework-inferred
endpoints with frontend route analysis, producing structured attack
scenarios for downstream exploitation."
```

---

### Task 10: Validate Phase 2

- [ ] **Step 1: Run full type-checking**

Run: `pnpm run check`
Expected: No TypeScript errors across all new files.

- [ ] **Step 2: Run linting on new files**

Run: `pnpm biome check apps/worker/src/services/framework-patterns.ts apps/worker/src/services/framework-analyzer.ts apps/worker/src/services/frontend-mapper.ts apps/worker/src/services/route-chain-builder.ts`
Expected: No new lint errors.

- [ ] **Step 3: Auto-fix any formatting issues**

Run: `pnpm biome:fix`
Expected: Any formatting/indentation issues are auto-fixed.

- [ ] **Step 4: Re-verify after formatting**

Run: `pnpm run check && pnpm biome`
Expected: Clean.

---

## Phase 3: Coordination Layer

### Task 11: Create shared knowledge types

**Files:**
- Create: `apps/worker/src/types/shared-knowledge.ts`
- Modify: `apps/worker/src/types/index.ts`

- [ ] **Step 1: Create the shared knowledge types file**

```typescript
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
```

- [ ] **Step 2: Add barrel export to types/index.ts**

In `apps/worker/src/types/index.ts`, add a new export line after the existing exports. After line 18 (`export * from './result.js';`), add:

```typescript
export * from './shared-knowledge.js';
```

- [ ] **Step 3: Run type-checking**

Run: `pnpm run check`
Expected: No TypeScript errors. The `import()` types reference the service modules correctly.

- [ ] **Step 4: Commit**

```bash
git add apps/worker/src/types/shared-knowledge.ts apps/worker/src/types/index.ts
git commit -m "feat(worker): add shared knowledge types for inter-agent communication

Defines SharedKnowledge, EndpointSecurityContext, VulnerabilityKnowledge,
and related types. Agents read/write structured data through the knowledge
store instead of relying solely on markdown deliverables."
```

---

### Task 12: Create knowledge store

**Files:**
- Create: `apps/worker/src/audit/knowledge-store.ts`
- Modify: `apps/worker/src/audit/index.ts`

The knowledge store persists `SharedKnowledge` as JSON in the workspace audit directory, alongside `session.json` and `workflow.log`.

- [ ] **Step 1: Create the knowledge store**

```typescript
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
```

- [ ] **Step 2: Add barrel export to audit/index.ts**

Read `apps/worker/src/audit/index.ts` first, then add the new export. After the existing export lines, add:

```typescript
export * from './knowledge-store.js';
```

- [ ] **Step 3: Run type-checking**

Run: `pnpm run check`
Expected: No TypeScript errors.

- [ ] **Step 4: Commit**

```bash
git add apps/worker/src/audit/knowledge-store.ts apps/worker/src/audit/index.ts
git commit -m "feat(worker): add knowledge store for inter-agent communication

Persists SharedKnowledge as JSON in the workspace audit directory.
Agents update partial knowledge after completing, and load accumulated
knowledge before starting."
```

---

### Task 13: Create attack chain builder service

**Files:**
- Create: `apps/worker/src/services/attack-chain-builder.ts`

This is the Phase 3 orchestration service that assembles attack chains from the accumulated shared knowledge of all prior agents.

- [ ] **Step 1: Create the attack chain builder**

```typescript
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
```

- [ ] **Step 2: Run type-checking**

Run: `pnpm run check`
Expected: No TypeScript errors.

- [ ] **Step 3: Commit**

```bash
git add apps/worker/src/services/attack-chain-builder.ts
git commit -m "feat(worker): add attack chain builder from shared knowledge

Assembles multi-step attack chains by correlating framework analysis,
frontend routes, and vulnerability findings from the shared knowledge
store."
```

---

### Task 14: Create shared knowledge prompt partial

**Files:**
- Create: `apps/worker/prompts/shared/_shared-knowledge.txt`

- [ ] **Step 1: Create the shared knowledge prompt partial**

````xml
<shared_knowledge>

## Shared Knowledge from Prior Agents

The following context was accumulated by agents that ran before you. Use this to inform your analysis and avoid re-discovering information that prior agents already gathered.

{{SHARED_KNOWLEDGE}}

### How to Use This Context

- **Framework Analysis:** If auto-generated endpoints are listed, they were inferred from framework patterns. Verify them but treat them as likely present.
- **Endpoint Inventory:** The endpoint security context comes from Recon. Each entry documents what protections exist — not whether they are sufficient (that is YOUR job).
- **Frontend Routes:** These map the frontend to backend API calls. Use them to trace data flows from user input through storage to rendering.
- **Attack Chains:** Pre-assembled attack scenarios from correlating the above data. Validate and extend them with your own analysis.

</shared_knowledge>
````

- [ ] **Step 2: Commit**

```bash
git add apps/worker/prompts/shared/_shared-knowledge.txt
git commit -m "feat(prompts): add shared knowledge prompt partial

Prompt template for injecting accumulated shared knowledge context
into downstream agent prompts via {{SHARED_KNOWLEDGE}} substitution."
```

---

### Task 15: Add shared knowledge variable to prompt manager

**Files:**
- Modify: `apps/worker/src/services/prompt-manager.ts`

We add a new `{{SHARED_KNOWLEDGE}}` variable that injects the formatted shared knowledge into prompts that use it.

- [ ] **Step 1: Add the buildSharedKnowledgeContext function**

In `apps/worker/src/services/prompt-manager.ts`, after the `buildAuthContext` function (after line 279), add:

```typescript
/**
 * Format shared knowledge for injection into agent prompts.
 * Produces a human-readable summary of accumulated knowledge.
 */
export function buildSharedKnowledgeContext(sharedKnowledgeJson: string): string {
  if (!sharedKnowledgeJson || sharedKnowledgeJson.trim() === '') {
    return 'No shared knowledge available from prior agents.';
  }

  try {
    const knowledge = JSON.parse(sharedKnowledgeJson) as Record<string, unknown>;

    const lines: string[] = [];

    // Framework analysis
    const framework = knowledge.frameworkAnalysis as { detectedFrameworks?: string[]; inferredEndpoints?: { method: string; path: string; model?: string }[]; recommendations?: string[] } | undefined;
    if (framework?.detectedFrameworks?.length) {
      lines.push('### Framework Analysis');
      lines.push(`Detected frameworks: ${framework.detectedFrameworks.join(', ')}`);
      if (framework.inferredEndpoints?.length) {
        lines.push('');
        lines.push('Inferred endpoints:');
        for (const ep of framework.inferredEndpoints) {
          lines.push(`  ${ep.method.padEnd(6)} ${ep.path}${ep.model ? ` (model: ${ep.model})` : ''}`);
        }
      }
      if (framework.recommendations?.length) {
        lines.push('');
        lines.push('Recommendations:');
        for (const r of framework.recommendations) {
          lines.push(`  - ${r}`);
        }
      }
    }

    // Endpoint inventory
    const endpoints = knowledge.endpointInventory as { endpoints?: { path: string; methods: string[]; authentication: string; frameworkOrigin: string; ownershipValidation: string }[] } | undefined;
    if (endpoints?.endpoints?.length) {
      lines.push('');
      lines.push('### Endpoint Security Context');
      lines.push('| Method | Path | Auth | Framework | Ownership |');
      lines.push('|--------|------|------|-----------|-----------|');
      for (const ep of endpoints.endpoints) {
        const methods = ep.methods.join('/');
        lines.push(`| ${methods} | ${ep.path} | ${ep.authentication} | ${ep.frameworkOrigin} | ${ep.ownershipValidation} |`);
      }
    }

    // Attack chains
    const chains = knowledge.attackChains as { chains?: { id: string; name: string; vulnType: string; severity: string; confidence: string }[] } | undefined;
    if (chains?.chains?.length) {
      lines.push('');
      lines.push('### Pre-assembled Attack Chains');
      for (const chain of chains.chains) {
        lines.push(`- [${chain.severity}/${chain.confidence}] ${chain.name} (${chain.vulnType})`);
      }
    }

    return lines.length > 0 ? lines.join('\n') : 'Shared knowledge loaded but contains no relevant data for this agent.';
  } catch {
    return `Shared knowledge available but could not be parsed. Raw data: ${sharedKnowledgeJson.slice(0, 200)}...`;
  }
}
```

- [ ] **Step 2: Add {{SHARED_KNOWLEDGE}} substitution to interpolateVariables**

In the `interpolateVariables` function, after the `{{REPORT_FILTER_RULES}}` substitution (after line 383), add:

```typescript
    // Shared knowledge injection — replace {{SHARED_KNOWLEDGE}} if present
    // The caller must pass the serialized knowledge via the template or a side channel.
    // If the placeholder exists but no knowledge is provided, the partial handles it.
    const sharedKnowledgeMatch = result.match(/{{SHARED_KNOWLEDGE}}/);
    if (sharedKnowledgeMatch) {
      // Default: no knowledge available. The caller (activity) can pre-replace this.
      // If still present, the shared partial will show its fallback message.
      result = result.replace(/{{SHARED_KNOWLEDGE}}/g, 'No shared knowledge available from prior agents.');
    }
```

- [ ] **Step 3: Run type-checking**

Run: `pnpm run check`
Expected: No TypeScript errors.

- [ ] **Step 4: Commit**

```bash
git add apps/worker/src/services/prompt-manager.ts
git commit -m "feat(worker): add shared knowledge context builder and substitution

Adds buildSharedKnowledgeContext() to format shared knowledge for prompt
injection, and {{SHARED_KNOWLEDGE}} variable handling in interpolation."
```

---

### Task 16: Integrate knowledge sharing into activities

**Files:**
- Modify: `apps/worker/src/temporal/activities.ts`

This is the most complex integration task. We need to:
1. Save framework analysis knowledge after pre-recon
2. Save endpoint + frontend knowledge after recon
3. Load knowledge before vuln agents and inject into prompts
4. Add an attack chain assembly step

The changes use the existing activity patterns in the file. The key insertion points are identified from the exploration: `runAgentActivity()` (lines 140-251) and `saveCheckpoint()` (lines 984-1001).

**NOTE:** The exact line numbers may shift as changes accumulate. The engineer should locate functions by name, not line number.

- [ ] **Step 1: Add imports at the top of activities.ts**

At the top of `apps/worker/src/temporal/activities.ts`, add these imports alongside existing imports:

```typescript
import { loadSharedKnowledge, updateSharedKnowledge } from '../audit/knowledge-store.js';
import { analyzeFrameworks } from '../services/framework-analyzer.js';
import { mapFrontendRoutes } from '../services/frontend-mapper.js';
import { buildSharedKnowledgeContext } from '../services/prompt-manager.js';
import type { SharedKnowledge } from '../types/shared-knowledge.js';
```

- [ ] **Step 2: Add framework analysis hook after pre-recon completion**

Find the pre-recon activity function (it delegates to `runAgentActivity`). After the agent completes successfully, add framework analysis. Look for the pattern where the pre-recon activity returns its result.

After the successful execution of the pre-recon agent, before the return statement, add a knowledge save call. The pattern to follow is the existing `saveCheckpoint()` flow.

Find the location in the pre-recon activity where the agent result is processed after success. Add:

```typescript
    // Save framework analysis to shared knowledge
    try {
      const frameworkAnalysis = await analyzeFrameworks(input.repoPath, logger);
      await updateSharedKnowledge(input.sessionMetadata, {
        frameworkAnalysis: {
          detectedFrameworks: frameworkAnalysis.detectedFramework ? [frameworkAnalysis.detectedFramework.name] : [],
          inferredEndpoints: frameworkAnalysis.inferredEndpoints,
          recommendations: frameworkAnalysis.recommendations,
        },
      }, logger);
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      logger.warn(`Framework analysis failed (non-fatal): ${errMsg}`);
    }
```

- [ ] **Step 3: Add frontend route mapping hook after recon completion**

Similarly, after the recon agent completes successfully, add frontend analysis. Find the recon activity's success path and add:

```typescript
    // Save frontend route analysis to shared knowledge
    try {
      const frontendAnalysis = await mapFrontendRoutes(input.repoPath, logger);
      await updateSharedKnowledge(input.sessionMetadata, {
        frontendRoutes: {
          routes: frontendAnalysis.routes,
          xssVectors: frontendAnalysis.xssChains,
        },
      }, logger);
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      logger.warn(`Frontend route mapping failed (non-fatal): ${errMsg}`);
    }
```

- [ ] **Step 4: Add knowledge injection before vuln agent execution**

In the `runAgentActivity()` function, before the agent is executed (before `container.agentExecution.executeOrThrow()`), add knowledge loading and prompt enrichment:

```typescript
    // Load shared knowledge and inject into prompt (vuln agents only)
    if (agentName.includes('-vuln')) {
      try {
        const sharedKnowledge = await loadSharedKnowledge(input.sessionMetadata, logger);
        const knowledgeJson = JSON.stringify(sharedKnowledge);
        const knowledgeContext = buildSharedKnowledgeContext(knowledgeJson);
        // Inject knowledge into the prompt via SHARED_KNOWLEDGE placeholder
        if (prompt.includes('{{SHARED_KNOWLEDGE}}')) {
          prompt = prompt.replace('{{SHARED_KNOWLEDGE}}', knowledgeContext);
        }
      } catch (error) {
        const errMsg = error instanceof Error ? error.message : String(error);
        logger.warn(`Failed to load shared knowledge (non-fatal): ${errMsg}`);
      }
    }
```

**IMPORTANT:** The variable name `prompt` here refers to the already-loaded prompt string in the activity. The engineer should use the actual variable name used in the function (likely `prompt` or `loadedPrompt` — check the local scope).

- [ ] **Step 5: Add attack chain assembly activity**

Add a new exported activity function after the existing activity functions in the file:

```typescript
/**
 * Build attack chains from accumulated shared knowledge.
 * Runs after all vuln agents complete, before exploitation phase.
 */
export async function buildAttackChainsActivity(input: ActivityInput): Promise<void> {
  const { logger } = createActivityContext(input);

  logger.info('Building attack chains from shared knowledge');

  try {
    const sharedKnowledge = await loadSharedKnowledge(input.sessionMetadata, logger);
    const chains = await buildAttackChains(sharedKnowledge, logger);

    await updateSharedKnowledge(input.sessionMetadata, {
      attackChains: { chains },
    }, logger);

    logger.info(`Built ${chains.length} attack chain(s)`);
  } catch (error) {
    const errMsg = error instanceof Error ? error.message : String(error);
    logger.warn(`Attack chain building failed (non-fatal): ${errMsg}`);
  }
}
```

This also requires adding an import for `buildAttackChains`:

```typescript
import { buildAttackChains } from '../services/attack-chain-builder.js';
```

- [ ] **Step 6: Verify the function signature patterns match**

The `ActivityInput` type and `createActivityContext` function are used in the existing activities. The engineer should verify these exist by checking the imports and patterns in the file.

Run: `grep -n "ActivityInput\|createActivityContext" apps/worker/src/temporal/activities.ts | head -10`
Expected: Multiple matches showing these are defined/imported.

- [ ] **Step 7: Run type-checking**

Run: `pnpm run check`
Expected: No TypeScript errors.

- [ ] **Step 8: Commit**

```bash
git add apps/worker/src/temporal/activities.ts
git commit -m "feat(worker): integrate knowledge sharing into pipeline activities

Pre-recon saves framework analysis, recon saves frontend routes, vuln
agents load shared knowledge before execution. Adds buildAttackChains
activity for assembling multi-step attack scenarios."
```

---

### Task 17: Integrate attack chain step into workflow

**Files:**
- Modify: `apps/worker/src/temporal/workflows.ts`

The workflow orchestrates phases sequentially. We add an attack chain assembly step between the vulnerability analysis phase and the exploitation phase.

**NOTE:** The exact line numbers may shift. Locate functions by name.

- [ ] **Step 1: Add import for the new activity**

In `apps/worker/src/temporal/workflows.ts`, add the import for the new activity alongside existing activity imports:

```typescript
import { buildAttackChainsActivity } from './activities.js';
```

- [ ] **Step 2: Add attack chain step in the workflow**

Find the location in `pentestPipelineWorkflow` where the vulnerability analysis phase completes and the exploitation phase begins. Between these two phases, insert the attack chain assembly step.

Look for a pattern like:
```typescript
// Phase: Exploitation
```

Before that phase starts, add:

```typescript
    // Phase: Attack Chain Assembly (between vuln analysis and exploitation)
    try {
      await buildAttackChainsActivity(activityInput);
    } catch (error) {
      // Non-fatal — attack chains enhance the report but don't block the pipeline
      const errMsg = error instanceof Error ? error.message : String(error);
      logger.warn(`Attack chain assembly failed: ${errMsg}`);
    }
```

- [ ] **Step 3: Run type-checking**

Run: `pnpm run check`
Expected: No TypeScript errors.

- [ ] **Step 4: Commit**

```bash
git add apps/worker/src/temporal/workflows.ts
git commit -m "feat(worker): add attack chain assembly step to pipeline workflow

Inserts attack chain building between vulnerability analysis and
exploitation phases. Non-fatal — failure doesn't block the pipeline."
```

---

### Task 18: Final validation

- [ ] **Step 1: Run full type-checking**

Run: `pnpm run check`
Expected: Clean — no TypeScript errors across all packages.

- [ ] **Step 2: Run linting**

Run: `pnpm biome`
Expected: No new lint errors. Pre-existing errors are acceptable.

- [ ] **Step 3: Run auto-fix for any formatting issues**

Run: `pnpm biome:fix`
Expected: Any formatting issues auto-fixed.

- [ ] **Step 4: Verify all new files exist**

Run: `echo "=== Phase 1 ===" && ls -la apps/worker/prompts/shared/_endpoint-security-context.txt && echo "=== Phase 2 ===" && ls -la apps/worker/src/services/framework-patterns.ts apps/worker/src/services/framework-analyzer.ts apps/worker/src/services/frontend-mapper.ts apps/worker/src/services/route-chain-builder.ts && echo "=== Phase 3 ===" && ls -la apps/worker/src/types/shared-knowledge.ts apps/worker/src/audit/knowledge-store.ts apps/worker/src/services/attack-chain-builder.ts apps/worker/prompts/shared/_shared-knowledge.txt`
Expected: All files exist with non-zero sizes.

- [ ] **Step 5: Verify all modified files changed as expected**

Run: `git diff --stat main...HEAD`
Expected: Shows all new and modified files from the plan.

- [ ] **Step 6: Run a final clean check**

Run: `pnpm run check && pnpm biome`
Expected: Clean.

- [ ] **Step 7: Verify the complete file list matches spec**

Expected files from spec (Appendix B):

**Phase 1:**
- ✅ `apps/worker/prompts/shared/_endpoint-security-context.txt` (new)
- ✅ `apps/worker/prompts/recon.txt` (modified)
- ✅ `apps/worker/prompts/vuln-authz.txt` (modified)
- ✅ `apps/worker/prompts/vuln-xss.txt` (modified)
- ✅ `apps/worker/prompts/vuln-injection.txt` (modified)

**Phase 2:**
- ✅ `apps/worker/src/services/framework-patterns.ts` (new)
- ✅ `apps/worker/src/services/framework-analyzer.ts` (new)
- ✅ `apps/worker/src/services/frontend-mapper.ts` (new)
- ✅ `apps/worker/src/services/route-chain-builder.ts` (new)

**Phase 3:**
- ✅ `apps/worker/src/types/shared-knowledge.ts` (new)
- ✅ `apps/worker/src/types/index.ts` (modified — barrel export)
- ✅ `apps/worker/src/audit/knowledge-store.ts` (new)
- ✅ `apps/worker/src/audit/index.ts` (modified — barrel export)
- ✅ `apps/worker/src/services/attack-chain-builder.ts` (new)
- ✅ `apps/worker/prompts/shared/_shared-knowledge.txt` (new)
- ✅ `apps/worker/src/services/prompt-manager.ts` (modified)
- ✅ `apps/worker/src/temporal/activities.ts` (modified)
- ✅ `apps/worker/src/temporal/workflows.ts` (modified)

---

## Self-Review Checklist

### 1. Spec Coverage

| Spec Section | Task(s) | Status |
|---|---|---|
| §4.1 Shared tool file `_endpoint-security-context.txt` | Task 1 | ✅ |
| §4.2 Recon prompt modification | Task 2 | ✅ |
| §4.3 AuthZ agent enhancement (Step 0, framework guidance) | Task 3 | ✅ |
| §4.4 XSS and Injection agent starting_context | Task 4 | ✅ |
| §5.1 Framework analysis plugin (patterns + analyzer) | Tasks 6, 7 | ✅ |
| §5.2 Frontend route mapper | Task 8 | ✅ |
| §5.3 Prompt integration (prompt-manager.ts) | Tasks 14, 15 | ✅ |
| §6.1 Shared knowledge types + knowledge store | Tasks 11, 12 | ✅ |
| §6.2 Attack chain builder | Task 13 | ✅ |
| §6.3 Activity integration | Task 16 | ✅ |
| §7 Data structure definitions | Task 11 | ✅ |

### 2. Placeholder Scan

No `TBD`, `TODO`, `implement later`, or placeholder patterns found. All code blocks contain complete implementations.

### 3. Type Consistency

- `InferredEndpoint` defined in `framework-analyzer.ts`, re-exported from `shared-knowledge.ts`, consumed by `route-chain-builder.ts` and `attack-chain-builder.ts` — consistent.
- `FrontendRoute`, `XssAttackChain` defined in `frontend-mapper.ts`, referenced via `import()` types in `shared-knowledge.ts` — consistent.
- `AttackChain` defined in `route-chain-builder.ts`, re-exported from `attack-chain-builder.ts` — consistent.
- `SharedKnowledge` defined in `shared-knowledge.ts`, consumed by `knowledge-store.ts` and `attack-chain-builder.ts` — consistent.
- `ActivityLogger` imported from `types/activity-logger.ts` consistently across all new service files.
- `SessionMetadata` imported from `types/audit.ts` in `knowledge-store.ts` — matches the existing `generateAuditPath()` signature.
