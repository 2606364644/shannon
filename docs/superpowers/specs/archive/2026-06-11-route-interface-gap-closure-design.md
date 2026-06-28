# Route & Interface Gap Closure Design

> Date: 2026-06-11  
> Status: Draft  
> Scope: Full parity with original Shannon (TypeScript) route analysis and interface binding capabilities

## 1. Problem Statement

The refactored shannon-py (Python) project lost critical route analysis and interface binding capabilities during the TypeScript → Python migration. A line-by-line diff of the actual source code reveals **11 specific gaps** across prompt and code layers:

### 1.1 Prompt-Layer Gaps (recon.txt diff)

| # | Gap | Original Location | Refactored Status |
|---|-----|-------------------|-------------------|
| P1 | Section 4.1 Shared Controller Route Groups | `recon.txt` L221-269 | **Deleted** — no replacement |
| P2 | Section 4.2 Endpoint Security Context | `recon.txt` L270-288 | **Deleted** — no replacement |
| P3 | `@include(shared/_endpoint-security-context.txt)` | `recon.txt` L45-46 | **File missing** |
| P4 | Route Mapper Agent Group Detection instruction | `recon.txt` L137 | **Weakened** — only "Map each endpoint" |
| P5 | Input Validator Agent type enumeration instruction | `recon.txt` L139 | **Weakened** — no TypeScript/Zod/wildcard requirement |
| P6 | Section 5 Parameter Completeness Verification table | `recon.txt` L299-310 | **Deleted** |
| P7 | Section 9 Injection Source Tracer template cross-ref | `recon.txt` L451 | **Weakened** — no template↔input cross-reference |
| P8 | Section 4 Shared Controller Parameter Propagation | `recon.txt` L221-228 | **Deleted** |

### 1.2 Code-Layer Gaps (no Python equivalent)

| # | Gap | Original File | Lines |
|---|-----|---------------|-------|
| C1 | Framework auto-REST detection + IDOR warnings | `framework-analyzer.ts` + `framework-patterns.ts` | 251 + 82 |
| C2 | Frontend route → API call mapping + XSS chains | `frontend-mapper.ts` | 241 |
| C3 | Route chain building (framework+frontend correlation) | `route-chain-builder.ts` | 133 |
| C4 | Attack chain assembly (multi-step scenarios) | `attack-chain-builder.ts` | 78 |

### 1.3 Cross-Route Enumeration Gap

| # | Gap | Original Location | Refactored Status |
|---|-----|-------------------|-------------------|
| P9 | `@include(shared/_cross-route-enumeration.txt)` | All 5 refactored vuln prompts (injection, xss, auth, authz, ssrf) | **File missing** — no structured CR-1→CR-4 checklist |
| P10 | Cross-Route Verification step in vuln prompts | e.g., `vuln-injection.txt` L380 | **Missing** — no `affected_routes` / `authentication_required` structured enforcement |

## 2. Architecture: Three Pillars

```
Pillar 1: Route Structured Index
├── Prompt: Section 4.1 Shared Controller Route Groups (P1)
├── Prompt: Section 4.2 Endpoint Security Context (P2)
├── Prompt: shared/_endpoint-security-context.txt (P3)
├── Prompt: Route Mapper Agent Group Detection (P4)
├── Prompt: Input Validator Agent type enumeration (P5)
├── Prompt: Section 4 Parameter Propagation (P8)
├── Code:  framework_analyzer.py + framework_patterns.py (C1)
└── Code:  frontend_mapper.py (C2)

Pillar 2: Cross-Route Vulnerability Enumeration
├── Prompt: shared/_cross-route-enumeration.txt (P9)
├── Prompt: Cross-Route Verification step in vuln prompts (P10)
├── Code:  route_chain_builder.py (C3)
└── Code:  attack_chain_builder.py (C4)

Pillar 3: Parameter Completeness
├── Prompt: Section 5 Parameter Completeness Verification table (P6)
└── Prompt: Section 9 Injection Source Tracer template cross-ref (P7)
```

## 3. Pipeline Integration

### 3.1 Current Pipeline

```
CodeIndex ∥ PRE_RECON
  → Entry Point Fusion → Adjudication
  → RECON
  → Risk Scoring
  → Render Dataflow Hints
  → Vuln Agents (parallel)
  → Report
```

### 3.2 New Pipeline

```
CodeIndex ∥ PRE_RECON
  → Entry Point Fusion → Adjudication
  → [NEW] Framework Analysis  ─┐
  → [NEW] Frontend Mapping     ─┘  (parallel)
  → [NEW] Route Chain Building     (sequential, depends on above)
  → RECON (reads framework + frontend deliverables)
  → Risk Scoring
  → Render Dataflow Hints
  → Vuln Agents (parallel, with cross-route-enumeration)
  → [NEW] Attack Chain Assembly
  → Report
```

## 4. Detailed Design — Pillar 1: Route Structured Index

### 4.1 Prompt Changes to `prompts/recon.txt`

#### 4.1.1 Add `@include(shared/_endpoint-security-context.txt)` (P3)

After `@include(shared/_rules-of-engagement.txt)` (currently line 44), add:

```
@include(shared/_endpoint-security-context.txt)
```

#### 4.1.2 Restore Section 4 Parameter Propagation (P8)

After the API Endpoint Inventory table, before Section 5, add:

```markdown
**Shared Controller Parameter Propagation:** When multiple routes map to the same controller
handler function, ALL query/body parameters that the handler reads (e.g., via `ctx.query.*`,
`req.query.*`, `request.getParameter()`) must be listed for EVERY route that uses that handler,
regardless of which route you discovered the parameter on. Do NOT assume a parameter is only
available on one route just because you found it there first.

Example: If `GET /preview/v2` and `GET /preview/iframe-demo` both route to `controller.index.preview`,
and the handler reads `ctx.query.bizEntity`, then BOTH route rows must list `bizEntity` as a parameter —
even if one route has no authentication middleware.
```

#### 4.1.3 Restore Section 4.1 Shared Controller Route Groups (P1)

After the Parameter Propagation text, add the full Section 4.1 block from original `recon.txt` lines 232-269, including:
- Group header format: `#### Group: HandlerName (file:line) — router.js:XX-YY`
- Per-route table: Method, Path, Auth Middleware, Router Line
- Pre-auth warning blocks
- Rules for grouping (≥2 routes sharing handler, independent rows, no inference)

#### 4.1.4 Restore Section 4.2 Endpoint Security Context (P2)

After Section 4.1, add:

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

#### 4.1.5 Enhance Route Mapper Agent Instruction (P4)

Replace the current Route Mapper Agent instruction:

```
"Find all backend routes and controllers that handle the discovered endpoints: [list endpoints]. Map each endpoint to its exact handler function with file paths and line numbers."
```

With the original version:

```
"Find all backend routes and controllers that handle the discovered endpoints: [list endpoints]. Map each endpoint to its exact handler function with file paths and line numbers. **Group detection:** Identify all routes that map to the SAME handler function — these share identical processing logic, and a vulnerability in the handler affects every route in the group. For each group, produce a per-route table where EACH route gets its own row with Method, Path, Auth Middleware (or **none** if absent), and Router Line (exact line number). Do NOT group multiple routes into a single cell — each route must be verified independently by reading its exact router line. Include the router definition file:line range (e.g., router.js:40-42) for each group so downstream agents can cross-reference."
```

#### 4.1.6 Enhance Input Validator Agent Instruction (P5)

Replace the current Input Validator Agent instruction:

```
"Analyze the input validation logic for all discovered form fields and API parameters. Find validation rules, sanitization, and data processing for each input with exact file paths."
```

With the original version:

```
"Analyze the input validation logic for all discovered form fields and API parameters. Find validation rules, sanitization, and data processing for each input with exact file paths. Additionally, enumerate ALL fields from the application's input type definitions (TypeScript interfaces, Zod schemas, Joi schemas, Pydantic models, JSON Schema). Report wildcard or catch-all fields (e.g., `[key: string]: unknown`) alongside explicit fields, noting that additional undeclared parameters may pass through. Provide a complete parameter inventory for each endpoint."
```

### 4.2 New Shared Prompt File: `prompts/shared/_endpoint-security-context.txt`

Port verbatim from `/root/shannon/apps/worker/prompts/shared/_endpoint-security-context.txt`. This file contains:
- Endpoint Security Context Analysis instructions
- Information to collect: HTTP Methods, Authentication Requirements, Middleware Chain, Framework Origin, Parameter Analysis, Ownership Validation
- Framework-Specific Patterns for finale-rest/epilogue
- Output format specification

### 4.3 New Service: `packages/core/src/shannon_core/services/framework_analyzer.py`

Port from `framework-analyzer.ts` + `framework-patterns.ts`.

#### Data Models

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class EndpointTemplate:
    methods: tuple[str, ...]
    path_template: str
    default_middleware: tuple[str, ...]
    notes: str

@dataclass(frozen=True)
class FrameworkPattern:
    name: str
    detection_patterns: dict[str, tuple[str, ...]]  # import, initialize, config
    endpoint_templates: tuple[EndpointTemplate, ...]
    vulnerability_patterns: tuple[str, ...]

@dataclass(frozen=True)
class InferredEndpoint:
    method: str
    path: str
    source: str  # 'framework-auto-generated' | 'manual'
    model: str | None = None
    middleware: tuple[str, ...] = ()
    vulnerability_indicators: tuple[str, ...] = ()

@dataclass
class FrameworkAnalysisResult:
    detected_framework: FrameworkPattern | None
    inferred_endpoints: list[InferredEndpoint]
    recommendations: list[str]
```

#### Framework Patterns

```python
FRAMEWORK_PATTERNS: tuple[FrameworkPattern, ...] = (
    FrameworkPattern(
        name='finale-rest',
        detection_patterns={
            'import': ('require("express-finale")', 'require("finale-rest")', 'import.*finale.*from'),
            'initialize': ('finale.initialize(', 'finale.resource('),
            'config': ('finale.resource(',),
        },
        endpoint_templates=(
            EndpointTemplate(
                methods=('GET', 'POST', 'PUT', 'DELETE'),
                path_template='/api/{Model}s',
                default_middleware=('isAuthenticated',),
                notes='Auto-generated CRUD operations, no ownership validation by default',
            ),
            EndpointTemplate(
                methods=('GET', 'POST', 'PUT', 'DELETE'),
                path_template='/api/{Model}s/:id',
                default_middleware=('isAuthenticated',),
                notes='Individual resource operations, commonly vulnerable to IDOR',
            ),
        ),
        vulnerability_patterns=(
            'No ownership check on finale resource operations',
            'DELETE endpoint often unblocked by default',
            'PUT endpoint may lack role checks',
        ),
    ),
    FrameworkPattern(
        name='epilogue',
        detection_patterns={
            'import': ('require("epilogue")', 'import.*epilogue.*from'),
            'initialize': ('epilogue.initialize(', 'epilogue.resource('),
            'config': ('epilogue.resource(',),
        },
        endpoint_templates=(
            EndpointTemplate(
                methods=('GET', 'POST', 'PUT', 'DELETE'),
                path_template='/api/{resource}',
                default_middleware=(),
                notes='Similar to finale, auto-generated CRUD',
            ),
        ),
        vulnerability_patterns=(
            'Epilogue resources lack ownership validation by default',
            'Mass operations enabled without explicit disable',
        ),
    ),
)
```

#### Core Function

```python
async def analyze_frameworks(
    codebase_path: str,
    logger,  # ActivityLogger equivalent
) -> FrameworkAnalysisResult:
    """Detect auto-REST frameworks, discover models, infer endpoints, build recommendations."""
    # 1. Detect framework
    # 2. Discover models (regex: .resource\([^)]*?model\s*:\s*(\w+))
    # 3. Generate inferred endpoints from templates
    # 4. Build security recommendations
```

### 4.4 New Service: `packages/core/src/shannon_core/services/frontend_mapper.py`

Port from `frontend-mapper.ts`.

#### Data Models

```python
@dataclass(frozen=True)
class UserInputPoint:
    type: str  # 'url-param' | 'query-param' | 'body' | 'header'
    field: str
    sanitization: str | None = None

@dataclass(frozen=True)
class ApiCall:
    endpoint: str
    method: str
    purpose: str
    data_flow: tuple[str, ...] = ()

@dataclass(frozen=True)
class FrontendRoute:
    path: str
    component: str
    authenticated: bool
    api_calls: tuple[ApiCall, ...] = ()
    user_inputs: tuple[UserInputPoint, ...] = ()

@dataclass(frozen=True)
class XssAttackChain:
    entry_point: str
    storage_endpoint: str
    render_endpoint: str
    sink: str
    confidence: str  # 'high' | 'medium' | 'low'

@dataclass
class FrontendAnalysisResult:
    routes: list[FrontendRoute]
    xss_chains: list[XssAttackChain]
```

#### Core Function

```python
async def map_frontend_routes(
    codebase_path: str,
    logger,
) -> FrontendAnalysisResult:
    """Detect frontend framework, find route files, parse routes, identify XSS chains."""
    # 1. Detect frontend framework (package.json: angular/react/vue)
    # 2. Find route definition files (framework-specific filenames + dirs)
    # 3. Parse routes (framework-specific regex)
    # 4. Identify XSS attack chains (POST→GET same base path)
```

## 5. Detailed Design — Pillar 2: Cross-Route Vulnerability Enumeration

### 5.1 New Shared Prompt File: `prompts/shared/_cross-route-enumeration.txt`

Port verbatim from `/root/shannon/apps/worker/prompts/shared/_cross-route-enumeration.txt`. This file contains the Pre-Documentation Checklist with steps CR-1 through CR-4:

- **CR-1**: Read Shared Controller Groups from recon deliverable Section 4.1
- **CR-2**: Locate the matching handler group
- **CR-3**: Enumerate affected routes (pre-auth / same-auth / different-auth tiers)
- **CR-4**: Attach `affected_routes` and `authentication_required` to every finding

### 5.2 Vuln Prompt Modifications

For each of the 5 vuln agent prompts (injection, xss, auth, authz, ssrf), add:

1. `@include(shared/_cross-route-enumeration.txt)` — in the instructions section
2. Cross-Route Verification step — requiring `affected_routes` and `authentication_required` validation

The refactored vuln prompts already have `authentication_required` in their JSON schema, but lack the structured CR-1→CR-4 checklist that forces systematic cross-route analysis.

### 5.3 New Service: `packages/core/src/shannon_core/services/route_chain_builder.py`

Port from `route-chain-builder.ts`.

#### Data Models

```python
@dataclass(frozen=True)
class AttackChainStep:
    order: int
    phase: str  # 'input' | 'storage' | 'retrieval' | 'render'
    endpoint: str
    method: str
    description: str

@dataclass
class AttackChain:
    id: str
    name: str
    description: str
    steps: list[AttackChainStep]
    vuln_type: str  # 'xss' | 'authz' | 'injection'
    severity: str  # 'critical' | 'high' | 'medium' | 'low'
    confidence: str  # 'confirmed' | 'probable' | 'theoretical'
```

#### Core Function

```python
def build_attack_chains_from_analysis(
    inferred_endpoints: list[InferredEndpoint],
    frontend_routes: list[FrontendRoute],
    xss_chains: list[XssAttackChain],
    logger,
) -> list[AttackChain]:
    """
    Build attack chains from framework endpoints and frontend analysis.
    
    1. XSS chains from frontend analysis (input→storage→retrieval→render)
    2. IDOR chains from framework endpoints without ownership checks
       (correlated with frontend routes that trigger them)
    """
```

### 5.4 New Service: `packages/core/src/shannon_core/services/attack_chain_builder.py`

Port from `attack-chain-builder.ts`.

```python
async def build_attack_chains(
    framework_result: FrameworkAnalysisResult,
    frontend_result: FrontendAnalysisResult,
    logger,
) -> list[AttackChain]:
    """
    Build attack chains from analysis results.
    
    1. Call build_attack_chains_from_analysis() for base chains
    2. Enhance chains with vulnerability context if available
    3. Return complete attack chain list
    """
```

## 6. Detailed Design — Pillar 3: Parameter Completeness

### 6.1 Restore Section 5 Parameter Completeness Verification (P6)

In `prompts/recon.txt`, after the existing Section 5 content, add:

```markdown
**Parameter Completeness Verification (MANDATORY for endpoints with template rendering):**
For each endpoint that renders templates or views, include a cross-reference table verifying parameter coverage:

| Endpoint | Input Type Fields | Template Variables | Hidden Parameters | Extraction Code Location |
|---|---|---|---|---|
| `/api/render` | `site`, `lang`, `channel` | `seoPath`, `global_content`, `oneTapConfig`, `site`, `lang` | `seoPath`, `global_content`, `oneTapConfig` (via wildcard) | `BuildCommonParamsService.build()` |
| **Input Type Fields:** Explicitly declared fields in the endpoint's input type definition
| **Template Variables:** All variable names used in the rendered template(s)
| **Hidden Parameters:** Variables present in templates but NOT in explicit type definitions (arriving through wildcards, middleware, or server-side construction)
| **Extraction Code Location:** Where hidden parameters are extracted from input or constructed

If all template variables map to explicitly typed input fields or server-computed values, confirm "all template variables accounted for" for that endpoint.
```

### 6.2 Enhance Injection Source Tracer (P7)

In `prompts/recon.txt` Section 9, replace the current Injection Source Tracer Agent instruction with the original version that includes template variable extraction and cross-referencing:

```
"Find all injection sources in the codebase: SQL injection, command injection, file inclusion/path traversal (LFI/RFI), server-side template injection (SSTI), and insecure deserialization. Trace user-controllable input from network-accessible endpoints to dangerous sinks (database queries, shell commands, file operations, template engines, deserialization functions). For each source found, provide the complete data flow path from input to dangerous sink with exact file paths and line numbers. Additionally, extract all variable names used in template rendering (from template files) and cross-reference them against the input type definitions and parameter construction code. Any variable in a template that could originate from user input but is NOT in the explicit input type definition MUST be reported as a potentially hidden parameter."
```

## 7. Workflow Changes

### 7.1 New Activities in `activities.py`

```python
async def run_framework_analysis(input: ActivityInput) -> dict:
    """Detect auto-REST frameworks, infer endpoints, write deliverable."""
    
async def run_frontend_mapping(input: ActivityInput) -> dict:
    """Map frontend routes to API calls, identify XSS chains, write deliverable."""

async def run_route_chain_building(input: ActivityInput) -> dict:
    """Build route chain map from framework + frontend analysis results."""

async def run_attack_chain_assembly(input: ActivityInput) -> dict:
    """Assemble multi-step attack chains from all analysis results."""
```

### 7.2 Workflow Orchestration in `workflows.py`

Insert after the Entry Point Adjudication step and before RECON:

```python
# === Route Analysis Phase (parallel) ===
framework_result, frontend_result = await asyncio.gather(
    workflow.execute_activity(
        activities.run_framework_analysis, act_input,
        start_to_close_timeout=timedelta(minutes=5),
    ),
    workflow.execute_activity(
        activities.run_frontend_mapping, act_input,
        start_to_close_timeout=timedelta(minutes=5),
    ),
)

# Route chain building (depends on framework + frontend)
chain_result = await workflow.execute_activity(
    activities.run_route_chain_building, act_input,
    start_to_close_timeout=timedelta(minutes=2),
)
```

After Vuln Agents, before Report:

```python
# === Attack Chain Assembly ===
await workflow.execute_activity(
    activities.run_attack_chain_assembly, act_input,
    start_to_close_timeout=timedelta(minutes=5),
)
```

## 8. File Manifest

### New Files

| File | Source | Purpose |
|------|--------|---------|
| `prompts/shared/_endpoint-security-context.txt` | Port from `/root/shannon/apps/worker/prompts/shared/_endpoint-security-context.txt` | Shared prompt for endpoint security analysis |
| `prompts/shared/_cross-route-enumeration.txt` | Port from `/root/shannon/apps/worker/prompts/shared/_cross-route-enumeration.txt` | Shared prompt for cross-route vulnerability checklist |
| `packages/core/src/shannon_core/services/framework_analyzer.py` | Port from `framework-analyzer.ts` + `framework-patterns.ts` | Auto-REST framework detection |
| `packages/core/src/shannon_core/services/frontend_mapper.py` | Port from `frontend-mapper.ts` | Frontend route → API mapping |
| `packages/core/src/shannon_core/services/route_chain_builder.py` | Port from `route-chain-builder.ts` | Route chain construction |
| `packages/core/src/shannon_core/services/attack_chain_builder.py` | Port from `attack-chain-builder.ts` | Attack chain assembly |
| `tests/test_framework_analyzer.py` | New | Tests for framework detection |
| `tests/test_frontend_mapper.py` | New | Tests for frontend mapping |
| `tests/test_route_chain_builder.py` | New | Tests for route chain building |
| `tests/test_attack_chain_builder.py` | New | Tests for attack chain assembly |

### Modified Files

| File | Changes |
|------|---------|
| `prompts/recon.txt` | P1: Section 4.1, P2: Section 4.2, P3: include endpoint-security-context, P4: Route Mapper, P5: Input Validator, P6: Parameter Completeness, P7: Injection Tracer, P8: Parameter Propagation |
| `prompts/vuln-injection.txt` | P9: include cross-route-enumeration, P10: Cross-Route Verification step |
| `prompts/vuln-xss.txt` | Same as vuln-injection.txt |
| `prompts/vuln-auth.txt` | Same as vuln-injection.txt |
| `prompts/vuln-authz.txt` | Same as vuln-injection.txt |
| `prompts/vuln-ssrf.txt` | Same as vuln-injection.txt |
| `packages/core/src/shannon_core/services/__init__.py` | Export new services |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | Add 4 new activity functions |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | Add parallel route analysis phase + attack chain phase |

## 9. Testing Strategy

### Unit Tests

- **framework_analyzer**: Test framework detection with sample `package.json` + route files; test model discovery regex; test endpoint generation from templates
- **frontend_mapper**: Test frontend framework detection; test route parsing for Angular/React/Vue patterns; test XSS chain identification
- **route_chain_builder**: Test chain building from sample inferred endpoints + frontend routes; test IDOR chain generation
- **attack_chain_builder**: Test chain assembly; test confidence upgrade with vulnerability context

### Integration Tests

- Verify full pipeline flow with new steps
- Verify deliverable files are written correctly
- Verify RECON agent can read framework + frontend analysis results

### Prompt Verification

- Diff check: `recon.txt` must include all 8 restored sections
- Diff check: All 5 vuln prompts must include `_cross-route-enumeration.txt`
- Diff check: Both new shared prompt files must match original content

## 10. Implementation Order

1. **Phase 1 — Prompt restoration** (lowest risk, highest immediate impact)
   - Create `_endpoint-security-context.txt` and `_cross-route-enumeration.txt`
   - Restore all 8 prompt sections in `recon.txt`
   - Add cross-route includes to all 5 vuln prompts

2. **Phase 2 — Core services** (new code, no existing changes)
   - Implement `framework_analyzer.py` + `framework_patterns.py`
   - Implement `frontend_mapper.py`
   - Implement `route_chain_builder.py`
   - Implement `attack_chain_builder.py`
   - Write unit tests for each

3. **Phase 3 — Pipeline integration** (wiring)
   - Add 4 new activities to `activities.py`
   - Modify workflow in `workflows.py`
   - Write integration tests
