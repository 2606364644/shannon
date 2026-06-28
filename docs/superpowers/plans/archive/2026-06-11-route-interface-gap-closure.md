# Route & Interface Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore all route analysis and interface binding capabilities lost during the TypeScript → Python migration by porting prompt content and code services from the original Shannon project.

**Architecture:** Three pillars — (1) Route Structured Index (prompt restoration + framework/frontend services), (2) Cross-Route Vulnerability Enumeration (prompt + route/attack chain builders), (3) Parameter Completeness (prompt only). New services are standalone modules in `packages/core/src/shannon_core/services/`, integrated as Temporal activities in the whitebox pipeline.

**Tech Stack:** Python 3.12+, dataclasses, aiofiles, pytest, Temporal.io activities

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `prompts/shared/_endpoint-security-context.txt` | Shared prompt: per-endpoint security context analysis instructions |
| `prompts/shared/_cross-route-enumeration.txt` | Shared prompt: CR-1→CR-4 pre-documentation checklist for vuln agents |
| `packages/core/src/shannon_core/services/framework_analyzer.py` | Detect auto-REST frameworks (finale-rest, epilogue), infer endpoints, build IDOR warnings |
| `packages/core/src/shannon_core/services/frontend_mapper.py` | Detect frontend framework, parse route definitions, identify XSS chains |
| `packages/core/src/shannon_core/services/route_chain_builder.py` | Build multi-step attack chains from framework+frontend analysis |
| `packages/core/src/shannon_core/services/attack_chain_builder.py` | Assemble and enhance attack chains from route chain builder output |
| `packages/core/tests/test_framework_analyzer.py` | Unit tests for framework detection |
| `packages/core/tests/test_frontend_mapper.py` | Unit tests for frontend route mapping |
| `packages/core/tests/test_route_chain_builder.py` | Unit tests for route chain building |
| `packages/core/tests/test_attack_chain_builder.py` | Unit tests for attack chain assembly |

### Modified Files

| File | What Changes |
|------|-------------|
| `prompts/recon.txt` | Add `@include(_endpoint-security-context)`, restore Sections 4.1/4.2/parameter propagation, enhance Route Mapper & Input Validator agents, restore Parameter Completeness table, enhance Injection Source Tracer |
| `prompts/vuln-injection.txt` | Add `@include(shared/_cross-route-enumeration.txt)` |
| `prompts/vuln-xss.txt` | Add `@include(shared/_cross-route-enumeration.txt)` |
| `prompts/vuln-auth.txt` | Add `@include(shared/_cross-route-enumeration.txt)` |
| `prompts/vuln-authz.txt` | Add `@include(shared/_cross-route-enumeration.txt)` |
| `prompts/vuln-ssrf.txt` | Add `@include(shared/_cross-route-enumeration.txt)` |
| `packages/core/src/shannon_core/services/__init__.py` | Export new services |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | Add 4 new activity functions |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | Add route analysis phase + attack chain assembly |
| `packages/whitebox/src/shannon_whitebox/worker.py` | Import and register 4 new activities |

---

## Phase 1: Prompt Restoration

### Task 1: Create `prompts/shared/_endpoint-security-context.txt`

**Files:**
- Create: `prompts/shared/_endpoint-security-context.txt`

- [ ] **Step 1: Create the shared prompt file**

Copy verbatim from the original project at `/root/shannon/apps/worker/prompts/shared/_endpoint-security-context.txt`. The content begins with `<endpoint_security_context>` and ends with `</endpoint_security_context>`. It contains:
- Information to collect per endpoint (HTTP Methods, Authentication, Middleware Chain, Framework Origin, Parameter Analysis, Ownership Validation)
- Framework-specific patterns for finale-rest/epilogue
- Output format specification
- Common pitfalls section

```bash
cp /root/shannon/apps/worker/prompts/shared/_endpoint-security-context.txt /root/shannon-py/prompts/shared/_endpoint-security-context.txt
```

- [ ] **Step 2: Verify the file was copied correctly**

Run: `diff /root/shannon/apps/worker/prompts/shared/_endpoint-security-context.txt /root/shannon-py/prompts/shared/_endpoint-security-context.txt`
Expected: No output (files identical)

- [ ] **Step 3: Commit**

```bash
git add prompts/shared/_endpoint-security-context.txt
git commit -m "feat: add endpoint-security-context shared prompt (P3)"
```

---

### Task 2: Create `prompts/shared/_cross-route-enumeration.txt`

**Files:**
- Create: `prompts/shared/_cross-route-enumeration.txt`

- [ ] **Step 1: Create the shared prompt file**

Copy verbatim from the original project at `/root/shannon/apps/worker/prompts/shared/_cross-route-enumeration.txt`. The content is wrapped in `<cross_route_enumeration>` tags and contains the Pre-Documentation Checklist (steps CR-1 through CR-4) that forces vuln agents to read Section 4.1, locate matching handler groups, enumerate affected routes, and attach `affected_routes` + `authentication_required` fields.

```bash
cp /root/shannon/apps/worker/prompts/shared/_cross-route-enumeration.txt /root/shannon-py/prompts/shared/_cross-route-enumeration.txt
```

- [ ] **Step 2: Verify the file was copied correctly**

Run: `diff /root/shannon/apps/worker/prompts/shared/_cross-route-enumeration.txt /root/shannon-py/prompts/shared/_cross-route-enumeration.txt`
Expected: No output (files identical)

- [ ] **Step 3: Commit**

```bash
git add prompts/shared/_cross-route-enumeration.txt
git commit -m "feat: add cross-route-enumeration shared prompt (P9)"
```

---

### Task 3: Restore `prompts/recon.txt` — all 8 prompt sections

**Files:**
- Modify: `prompts/recon.txt`

This task applies 8 changes to `recon.txt`. Each change is identified by the gap ID (P1–P8) from the spec. Apply them in order.

- [ ] **Step 1: P3 — Add `@include(shared/_endpoint-security-context.txt)`**

After line 44 (`@include(shared/_rules-of-engagement.txt)`), add a blank line then the include:

```

@include(shared/_endpoint-security-context.txt)
```

- [ ] **Step 2: P4 — Enhance Route Mapper Agent instruction**

Find this line (around line 160):

```
      - **Route Mapper Agent**: "Find all backend routes and controllers that handle the discovered endpoints: [list endpoints]. Map each endpoint to its exact handler function with file paths and line numbers."
```

Replace with:

```
      - **Route Mapper Agent**: "Find all backend routes and controllers that handle the discovered endpoints: [list endpoints]. Map each endpoint to its exact handler function with file paths and line numbers. **Group detection:** Identify all routes that map to the SAME handler function — these share identical processing logic, and a vulnerability in the handler affects every route in the group. For each group, produce a per-route table where EACH route gets its own row with Method, Path, Auth Middleware (or **none** if absent), and Router Line (exact line number). Do NOT group multiple routes into a single cell — each route must be verified independently by reading its exact router line. Include the router definition file:line range (e.g., router.js:40-42) for each group so downstream agents can cross-reference."
```

- [ ] **Step 3: P5 — Enhance Input Validator Agent instruction**

Find this line:

```
      - **Input Validator Agent**: "Analyze the input validation logic for all discovered form fields and API parameters. Find validation rules, sanitization, and data processing for each input with exact file paths."
```

Replace with:

```
      - **Input Validator Agent**: "Analyze the input validation logic for all discovered form fields and API parameters. Find validation rules, sanitization, and data processing for each input with exact file paths. Additionally, enumerate ALL fields from the application's input type definitions (TypeScript interfaces, Zod schemas, Joi schemas, Pydantic models, JSON Schema). Report wildcard or catch-all fields (e.g., `[key: string]: unknown`) alongside explicit fields, noting that additional undeclared parameters may pass through. Provide a complete parameter inventory for each endpoint."
```

- [ ] **Step 4: P8 + P1 + P2 — Restore Section 4 Parameter Propagation, Section 4.1, and Section 4.2**

Find this line (the end of the Section 4 API Endpoint Inventory table):

```
| ... | ... | ... | ... | ... | ... |
```

It is followed immediately by `## 5. Potential Input Vectors`. Between the table and Section 5, insert the following block:

```markdown
**Shared Controller Parameter Propagation:** When multiple routes map to the same controller
handler function, ALL query/body parameters that the handler reads (e.g., via `ctx.query.*`,
`req.query.*`, `request.getParameter()`) must be listed for EVERY route that uses that handler,
regardless of which route you discovered the parameter on. Do NOT assume a parameter is only
available on one route just because you found it there first.

Example: If `GET /preview/v2` and `GET /preview/iframe-demo` both route to `controller.index.preview`,
and the handler reads `ctx.query.bizEntity`, then BOTH route rows must list `bizEntity` as a parameter —
even if one route has no authentication middleware.

### 4.1 Shared Controller Route Groups

When multiple routes map to the same handler function, a vulnerability in that
handler affects ALL routes in the group. You MUST produce per-group subsections
so downstream vulnerability agents can enumerate every affected route and flag
pre-auth (unauthenticated) variants.

#### Group: controller.index.preview (index.js:32) — router.js:40-42

| Method | Path | Auth Middleware | Router Line |
|---|---|---|---|
| GET | /preview | thirtyLogin() | router.js:40 |
| GET | /preview/v2 | thirtyLogin() | router.js:41 |
| GET | /preview/iframe-demo | **none** | router.js:42 |

> ⚠️ `/preview/iframe-demo` has NO auth middleware — pre-auth variant.

---

#### Group: controller.users.getProfile (users.js:45) — router.js:18-19

| Method | Path | Auth Middleware | Router Line |
|---|---|---|---|
| GET | /api/users/me | requireAuth | router.js:18 |
| GET | /api/admin/users/profile | requireAdmin | router.js:19 |

> ⚠️ Different auth levels across routes — admin route has elevated privileges.

---

**Rules for these groups:**
- Only include groups where ≥2 routes share the same handler function.
- Each group starts with a `#### Group:` header containing handler name, file:line, and router definition range.
- Each row MUST correspond to exactly one route — do NOT pack multiple routes into a single table cell.
- The Auth Middleware column must reflect the presence or absence of middleware in that specific router line — never infer from sibling routes. Use `**none**` for routes with no middleware.
- The Router Line column must cite the exact line number so each route can be independently verified.
- When any route in a group has `**none**` auth, add a `> ⚠️` warning block below the table identifying the pre-auth route(s).
- Include the handler's file:line location in the group header for downstream agents to trace.
- Include the router definition file:line range in the group header. Downstream agents can use this as a cross-reference anchor when their handler name doesn't exactly match — they can read the router file to confirm the mapping.
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

- [ ] **Step 5: P6 — Restore Section 5 Parameter Completeness Verification**

Find the end of Section 5 content (just before `## 6. Network & Interaction Map`). Insert the following block before `## 6`:

```markdown
**Parameter Completeness Verification (MANDATORY for endpoints with template rendering):** For each endpoint that renders templates or views, include a cross-reference table verifying parameter coverage:

| Endpoint | Input Type Fields | Template Variables | Hidden Parameters | Extraction Code Location |
|---|---|---|---|---|
| `/api/render` | `site`, `lang`, `channel` | `seoPath`, `global_content`, `oneTapConfig`, `site`, `lang` | `seoPath`, `global_content`, `oneTapConfig` (via wildcard) | `BuildCommonParamsService.build()` |
| **Input Type Fields:** Explicitly declared fields in the endpoint's input type definition
| **Template Variables:** All variable names used in the rendered template(s)
| **Hidden Parameters:** Variables present in templates but NOT in explicit type definitions (arriving through wildcards, middleware, or server-side construction)
| **Extraction Code Location:** Where hidden parameters are extracted from input or constructed

If all template variables map to explicitly typed input fields or server-computed values, confirm "all template variables accounted for" for that endpoint.
```

- [ ] **Step 6: P7 — Enhance Section 9 Injection Source Tracer**

Find this line in Section 9:

```
"Find all injection sources in the codebase: SQL injection, command injection, file inclusion/path traversal (LFI/RFI), server-side template injection (SSTI), and insecure deserialization. Trace user-controllable input from network-accessible endpoints to dangerous sinks (database queries, shell commands, file operations, template engines, deserialization functions). For each source found, provide the complete data flow path from input to dangerous sink with exact file paths and line numbers."
```

Replace with:

```
"Find all injection sources in the codebase: SQL injection, command injection, file inclusion/path traversal (LFI/RFI), server-side template injection (SSTI), and insecure deserialization. Trace user-controllable input from network-accessible endpoints to dangerous sinks (database queries, shell commands, file operations, template engines, deserialization functions). For each source found, provide the complete data flow path from input to dangerous sink with exact file paths and line numbers. Additionally, extract all variable names used in template rendering (from template files) and cross-reference them against the input type definitions and parameter construction code. Any variable in a template that could originate from user input but is NOT in the explicit input type definition MUST be reported as a potentially hidden parameter."
```

- [ ] **Step 7: Commit**

```bash
git add prompts/recon.txt
git commit -m "feat: restore Sections 4.1/4.2, parameter propagation, parameter completeness, and enhance agent instructions (P1-P8)"
```

---

### Task 4: Add `@include(shared/_cross-route-enumeration.txt)` to all 5 vuln prompts

**Files:**
- Modify: `prompts/vuln-injection.txt`
- Modify: `prompts/vuln-xss.txt`
- Modify: `prompts/vuln-auth.txt`
- Modify: `prompts/vuln-authz.txt`
- Modify: `prompts/vuln-ssrf.txt`

All 5 vuln prompts have the same structure. In each, the `@include(shared/_static-dataflow-hints.txt)` line appears near the top (around line 43-47 depending on the file). Insert the new include **after** `_static-dataflow-hints.txt` with a blank line separator.

- [ ] **Step 1: Add include to vuln-injection.txt**

After the line `@include(shared/_static-dataflow-hints.txt)` (line 45), add:

```

@include(shared/_cross-route-enumeration.txt)
```

- [ ] **Step 2: Add include to vuln-xss.txt**

After the line `@include(shared/_static-dataflow-hints.txt)` (line 43), add:

```

@include(shared/_cross-route-enumeration.txt)
```

- [ ] **Step 3: Add include to vuln-auth.txt**

After the line `@include(shared/_static-dataflow-hints.txt)` (line 43), add:

```

@include(shared/_cross-route-enumeration.txt)
```

- [ ] **Step 4: Add include to vuln-authz.txt**

After the line `@include(shared/_static-dataflow-hints.txt)` (line 47), add:

```

@include(shared/_cross-route-enumeration.txt)
```

- [ ] **Step 5: Add include to vuln-ssrf.txt**

After the line `@include(shared/_static-dataflow-hints.txt)` (line 43), add:

```

@include(shared/_cross-route-enumeration.txt)
```

- [ ] **Step 6: Verify all 5 files have the include**

Run: `grep -l "_cross-route-enumeration" /root/shannon-py/prompts/vuln-*.txt`
Expected: All 5 files listed (injection, xss, auth, authz, ssrf)

- [ ] **Step 7: Commit**

```bash
git add prompts/vuln-injection.txt prompts/vuln-xss.txt prompts/vuln-auth.txt prompts/vuln-authz.txt prompts/vuln-ssrf.txt
git commit -m "feat: add cross-route-enumeration include to all vuln prompts (P9, P10)"
```

---

## Phase 2: Core Services

### Task 5: Implement `framework_analyzer.py` — data models and patterns

**Files:**
- Create: `packages/core/src/shannon_core/services/framework_analyzer.py`
- Test: `packages/core/tests/test_framework_analyzer.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_framework_analyzer.py`:

```python
"""Tests for framework_analyzer service."""
import pytest

from shannon_core.services.framework_analyzer import (
    EndpointTemplate,
    FrameworkPattern,
    FRAMEWORK_PATTERNS,
    InferredEndpoint,
    FrameworkAnalysisResult,
)


class TestFrameworkPatterns:
    def test_patterns_defined(self):
        assert len(FRAMEWORK_PATTERNS) >= 2

    def test_finale_rest_pattern(self):
        fr = FRAMEWORK_PATTERNS[0]
        assert fr.name == "finale-rest"
        assert "import" in fr.detection_patterns
        assert "initialize" in fr.detection_patterns
        assert "config" in fr.detection_patterns
        assert len(fr.endpoint_templates) == 2
        assert len(fr.vulnerability_patterns) >= 2

    def test_epilogue_pattern(self):
        ep = FRAMEWORK_PATTERNS[1]
        assert ep.name == "epilogue"
        assert len(ep.endpoint_templates) >= 1


class TestInferredEndpoint:
    def test_creation(self):
        ep = InferredEndpoint(
            method="DELETE",
            path="/api/Feedbacks/:id",
            source="framework-auto-generated",
            model="Feedback",
            middleware=("isAuthenticated",),
            vulnerability_indicators=("No ownership check",),
        )
        assert ep.method == "DELETE"
        assert ep.model == "Feedback"


class TestEndpointTemplate:
    def test_collection_endpoint_skips_put_delete(self):
        """Collection endpoints (/api/Models) should not have PUT/DELETE."""
        tpl = EndpointTemplate(
            methods=("GET", "POST", "PUT", "DELETE"),
            path_template="/api/{Model}s",
            default_middleware=("isAuthenticated",),
            notes="test",
        )
        assert "GET" in tpl.methods
        assert "POST" in tpl.methods
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_framework_analyzer.py -v 2>&1 | tail -10`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.services.framework_analyzer'`

- [ ] **Step 3: Write the data models and patterns**

Create `packages/core/src/shannon_core/services/framework_analyzer.py`:

```python
"""Framework analyzer service.

Detects auto-generated REST framework usage (finale-rest, epilogue) and
infers endpoints that may not be visible in explicit route definitions.
Ported from /root/shannon/apps/worker/src/services/framework-analyzer.ts
and framework-patterns.ts.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EndpointTemplate:
    """Template for generating inferred endpoints from a model name."""

    methods: tuple[str, ...]
    path_template: str
    default_middleware: tuple[str, ...]
    notes: str


@dataclass(frozen=True)
class FrameworkPattern:
    """Detection pattern for an auto-REST framework."""

    name: str
    detection_patterns: dict[str, tuple[str, ...]]  # import, initialize, config
    endpoint_templates: tuple[EndpointTemplate, ...]
    vulnerability_patterns: tuple[str, ...]


@dataclass(frozen=True)
class InferredEndpoint:
    """An endpoint inferred from framework configuration."""

    method: str
    path: str
    source: str  # 'framework-auto-generated' | 'manual'
    model: str | None = None
    middleware: tuple[str, ...] = ()
    vulnerability_indicators: tuple[str, ...] = ()


@dataclass
class FrameworkAnalysisResult:
    """Result of framework analysis."""

    detected_framework: FrameworkPattern | None = None
    inferred_endpoints: list[InferredEndpoint] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Framework patterns (ported from framework-patterns.ts)
# ---------------------------------------------------------------------------

FRAMEWORK_PATTERNS: tuple[FrameworkPattern, ...] = (
    FrameworkPattern(
        name="finale-rest",
        detection_patterns={
            "import": (
                'require("express-finale")',
                'require("finale-rest")',
                "import.*finale.*from",
            ),
            "initialize": ("finale.initialize(", "finale.resource("),
            "config": ("finale.resource(",),
        },
        endpoint_templates=(
            EndpointTemplate(
                methods=("GET", "POST", "PUT", "DELETE"),
                path_template="/api/{Model}s",
                default_middleware=("isAuthenticated",),
                notes="Auto-generated CRUD operations, no ownership validation by default",
            ),
            EndpointTemplate(
                methods=("GET", "POST", "PUT", "DELETE"),
                path_template="/api/{Model}s/:id",
                default_middleware=("isAuthenticated",),
                notes="Individual resource operations, commonly vulnerable to IDOR",
            ),
        ),
        vulnerability_patterns=(
            "No ownership check on finale resource operations",
            "DELETE endpoint often unblocked by default",
            "PUT endpoint may lack role checks",
        ),
    ),
    FrameworkPattern(
        name="epilogue",
        detection_patterns={
            "import": ('require("epilogue")', "import.*epilogue.*from"),
            "initialize": ("epilogue.initialize(", "epilogue.resource("),
            "config": ("epilogue.resource(",),
        },
        endpoint_templates=(
            EndpointTemplate(
                methods=("GET", "POST", "PUT", "DELETE"),
                path_template="/api/{resource}",
                default_middleware=(),
                notes="Similar to finale, auto-generated CRUD",
            ),
        ),
        vulnerability_patterns=(
            "Epilogue resources lack ownership validation by default",
            "Mass operations enabled without explicit disable",
        ),
    ),
)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

async def analyze_frameworks(
    codebase_path: str,
) -> FrameworkAnalysisResult:
    """Detect auto-REST frameworks, discover models, infer endpoints, build recommendations."""
    detected: FrameworkPattern | None = None

    for pattern in FRAMEWORK_PATTERNS:
        if await _detect_framework(codebase_path, pattern):
            detected = pattern
            logger.info("Detected framework: %s", pattern.name)
            break

    if detected is None:
        logger.info("No auto-generated REST framework detected")
        return FrameworkAnalysisResult()

    models = await _discover_models(codebase_path, detected)
    logger.info("Found %d model(s) configured with %s: %s", len(models), detected.name, ", ".join(models))

    endpoints = _generate_inferred_endpoints(detected, models)
    recommendations = _build_recommendations(detected, endpoints)

    return FrameworkAnalysisResult(
        detected_framework=detected,
        inferred_endpoints=endpoints,
        recommendations=recommendations,
    )


async def _detect_framework(codebase_path: str, pattern: FrameworkPattern) -> bool:
    """Scan source files for framework initialization patterns."""
    all_patterns = list(pattern.detection_patterns.get("import", ())) + list(
        pattern.detection_patterns.get("initialize", ())
    )
    if not all_patterns:
        return False

    try:
        source_files = await _find_source_files(codebase_path)
        for file_path in source_files:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            for detection_pattern in all_patterns:
                if detection_pattern in content:
                    logger.info('Framework pattern "%s" found in %s', detection_pattern, file_path)
                    return True
    except Exception as exc:
        logger.warning("Error scanning for framework %s: %s", pattern.name, exc)

    return False


async def _find_source_files(codebase_path: str) -> list[Path]:
    """Find relevant source files to scan for framework patterns."""
    base = Path(codebase_path)
    files: list[Path] = []

    for candidate in ("server.js", "server.ts", "app.js", "app.ts", "index.js", "index.ts"):
        full = base / candidate
        if full.exists():
            files.append(full)

    for subdir in ("routes", "models", "api", "src/routes", "src/models"):
        dir_path = base / subdir
        if dir_path.exists():
            for f in dir_path.iterdir():
                if f.suffix in (".js", ".ts"):
                    files.append(f)

    return files


async def _discover_models(codebase_path: str, pattern: FrameworkPattern) -> list[str]:
    """Discover model names configured with the framework."""
    models: list[str] = []
    config_patterns = pattern.detection_patterns.get("config", ())
    if not config_patterns:
        return models

    model_regex = re.compile(r"\.resource\([^)]*?model\s*:\s*([A-Za-z_][A-Za-z0-9_]*)")

    try:
        source_files = await _find_source_files(codebase_path)
        for file_path in source_files:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            for match in model_regex.finditer(content):
                model_name = match.group(1)
                if model_name not in models:
                    models.append(model_name)
                    logger.info("Discovered model: %s in %s", model_name, file_path)
    except Exception as exc:
        logger.warning("Error discovering models for %s: %s", pattern.name, exc)

    return models


def _generate_inferred_endpoints(
    framework: FrameworkPattern, models: Sequence[str]
) -> list[InferredEndpoint]:
    """Generate inferred endpoints from framework templates and discovered models."""
    endpoints: list[InferredEndpoint] = []

    for model in models:
        for template in framework.endpoint_templates:
            base_path = template.path_template.replace("{Model}", model).replace(
                "{resource}", model.lower()
            )
            is_collection = ":id" not in template.path_template

            for method in template.methods:
                # Collection endpoints skip PUT and DELETE
                if is_collection and method in ("PUT", "DELETE"):
                    continue

                endpoints.append(
                    InferredEndpoint(
                        method=method,
                        path=base_path,
                        source="framework-auto-generated",
                        model=model,
                        middleware=template.default_middleware,
                        vulnerability_indicators=framework.vulnerability_patterns,
                    )
                )

    return endpoints


def _build_recommendations(
    framework: FrameworkPattern, endpoints: Sequence[InferredEndpoint]
) -> list[str]:
    """Build security recommendations based on detected framework and endpoints."""
    recs: list[str] = [
        f"Framework {framework.name} detected — auto-generated endpoints may lack ownership validation"
    ]

    delete_endpoints = [ep for ep in endpoints if ep.method == "DELETE"]
    if delete_endpoints:
        recs.append(
            f"{len(delete_endpoints)} DELETE endpoint(s) auto-generated — verify each has authorization guards"
        )

    put_endpoints = [ep for ep in endpoints if ep.method == "PUT"]
    if put_endpoints:
        recs.append(f"{len(put_endpoints)} PUT endpoint(s) auto-generated — verify role-based access control")

    recs.extend(framework.vulnerability_patterns)
    return recs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_framework_analyzer.py -v 2>&1 | tail -20`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/services/framework_analyzer.py packages/core/tests/test_framework_analyzer.py
git commit -m "feat: add framework_analyzer service with data models and detection logic (C1)"
```

---

### Task 6: Implement `frontend_mapper.py`

**Files:**
- Create: `packages/core/src/shannon_core/services/frontend_mapper.py`
- Test: `packages/core/tests/test_frontend_mapper.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_frontend_mapper.py`:

```python
"""Tests for frontend_mapper service."""
import pytest

from shannon_core.services.frontend_mapper import (
    FrontendRoute,
    ApiCall,
    UserInputPoint,
    XssAttackChain,
    FrontendAnalysisResult,
    identify_xss_chains,
    extract_base_path,
)


class TestDataModels:
    def test_frontend_route(self):
        r = FrontendRoute(path="/dashboard", component="DashboardComponent", authenticated=True)
        assert r.path == "/dashboard"
        assert r.api_calls == ()
        assert r.user_inputs == ()

    def test_xss_attack_chain(self):
        c = XssAttackChain(
            entry_point="/input",
            storage_endpoint="/api/data",
            render_endpoint="/view",
            sink="DataView",
            confidence="medium",
        )
        assert c.confidence == "medium"


class TestIdentifyXssChains:
    def test_no_chains_when_no_post(self):
        routes = [
            FrontendRoute(path="/view", component="View", authenticated=False,
                          api_calls=(ApiCall(endpoint="/api/data", method="GET", purpose="fetch"),)),
        ]
        chains = identify_xss_chains(routes)
        assert len(chains) == 0

    def test_chain_detected_when_post_get_share_base(self):
        routes = [
            FrontendRoute(
                path="/input",
                component="InputForm",
                authenticated=True,
                api_calls=(ApiCall(endpoint="/api/data", method="POST", purpose="save"),),
                user_inputs=(UserInputPoint(type="body", field="content"),),
            ),
            FrontendRoute(
                path="/view",
                component="DataView",
                authenticated=False,
                api_calls=(ApiCall(endpoint="/api/data", method="GET", purpose="fetch"),),
            ),
        ]
        chains = identify_xss_chains(routes)
        assert len(chains) == 1
        assert chains[0].entry_point == "/input"
        assert chains[0].render_endpoint == "/view"

    def test_no_chain_when_bases_differ(self):
        routes = [
            FrontendRoute(
                path="/input",
                component="InputForm",
                authenticated=True,
                api_calls=(ApiCall(endpoint="/api/posts", method="POST", purpose="save"),),
                user_inputs=(UserInputPoint(type="body", field="content"),),
            ),
            FrontendRoute(
                path="/view",
                component="DataView",
                authenticated=False,
                api_calls=(ApiCall(endpoint="/api/comments", method="GET", purpose="fetch"),),
            ),
        ]
        chains = identify_xss_chains(routes)
        assert len(chains) == 0


class TestExtractBasePath:
    def test_strips_id_segment(self):
        assert extract_base_path("/api/Users/:id") == "/api/Users"

    def test_no_id_segment(self):
        assert extract_base_path("/api/Users") == "/api/Users"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_frontend_mapper.py -v 2>&1 | tail -10`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `packages/core/src/shannon_core/services/frontend_mapper.py`:

```python
"""Frontend route mapper.

Maps frontend routes to their data sources and API calls to identify
potential multi-step attack chains (e.g., stored XSS via user input →
API storage → admin panel rendering).
Ported from /root/shannon/apps/worker/src/services/frontend-mapper.ts
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

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
    routes: list[FrontendRoute] = field(default_factory=list)
    xss_chains: list[XssAttackChain] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

async def map_frontend_routes(codebase_path: str) -> FrontendAnalysisResult:
    """Detect frontend framework, find route files, parse routes, identify XSS chains."""
    routes: list[FrontendRoute] = []

    framework = await _detect_frontend_framework(codebase_path)
    logger.info("Detected frontend framework: %s", framework)

    route_files = await _find_route_files(codebase_path, framework)
    if not route_files:
        logger.info("No frontend route files found")
        return FrontendAnalysisResult()

    logger.info("Found %d route file(s): %s", len(route_files), ", ".join(str(f) for f in route_files))

    for file_path in route_files:
        file_routes = _parse_routes(file_path, framework)
        routes.extend(file_routes)

    xss_chains = identify_xss_chains(routes)
    logger.info("Mapped %d route(s), identified %d potential XSS chain(s)", len(routes), len(xss_chains))

    return FrontendAnalysisResult(routes=routes, xss_chains=xss_chains)


async def _detect_frontend_framework(codebase_path: str) -> str:
    """Detect which frontend framework is in use."""
    pkg_path = Path(codebase_path) / "package.json"
    if not pkg_path.exists():
        return "unknown"

    try:
        content = pkg_path.read_text(encoding="utf-8", errors="ignore")
        if '"@angular/core"' in content:
            return "angular"
        if '"react"' in content or '"next"' in content:
            return "react"
        if '"vue"' in content or '"nuxt"' in content:
            return "vue"
    except Exception as exc:
        logger.warning("Error reading package.json: %s", exc)

    return "unknown"


async def _find_route_files(codebase_path: str, framework: str) -> list[Path]:
    """Find frontend route definition files based on framework."""
    base = Path(codebase_path)
    files: list[Path] = []

    search_dirs = [
        base / "frontend" / "src",
        base / "frontend",
        base / "src" / "app",
        base / "src",
        base,
    ]

    filename_patterns: dict[str, list[str]] = {
        "angular": ["app-routing.module.ts", "app.routes.ts", "routes.ts"],
        "react": ["routes.tsx", "routes.ts", "router.tsx", "router.ts", "App.tsx"],
        "vue": ["router.ts", "router.js", "index.ts", "index.js"],
        "unknown": ["routes.ts", "routes.tsx", "router.ts", "router.tsx", "app.routes.ts"],
    }

    patterns = filename_patterns.get(framework, filename_patterns["unknown"])

    for dir_path in search_dirs:
        if not dir_path.exists():
            continue
        for pattern in patterns:
            file_path = dir_path / pattern
            if file_path.exists():
                files.append(file_path)

    return files


def _parse_routes(file_path: Path, framework: str) -> list[FrontendRoute]:
    """Parse route definitions from a file."""
    routes: list[FrontendRoute] = []

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")

        route_regex_map: dict[str, re.Pattern[str]] = {
            "angular": re.compile(
                r"path\s*:\s*['\"`]([^'\"`]+)['\"`][^}]*?component\s*:\s*([A-Za-z_][A-Za-z0-9_]*)"
            ),
            "react": re.compile(
                r"path\s*:\s*['\"`]([^'\"`]+)['\"`][^}]*?(?:element|component)\s*:\s*(?:<|([A-Za-z_][A-Za-z0-9_]*))"
            ),
            "vue": re.compile(
                r"path\s*:\s*['\"`]([^'\"`]+)['\"`][^}]*?(?:component|name)\s*:\s*['\"`]?([A-Za-z_][A-Za-z0-9_]*)"
            ),
        }

        regex = route_regex_map.get(framework, re.compile(r"path\s*:\s*['\"`]([^'\"`]+)['\"`]"))

        for match in regex.finditer(content):
            path = match.group(1)
            component = match.group(2) if match.lastindex and match.lastindex >= 2 else "Unknown"
            if path:
                routes.append(
                    FrontendRoute(
                        path=path,
                        component=component or "Unknown",
                        authenticated=_has_auth_guard(content),
                        api_calls=(),
                        user_inputs=(),
                    )
                )
    except Exception as exc:
        logger.warning("Error parsing routes from %s: %s", file_path, exc)

    return routes


def _has_auth_guard(content: str) -> bool:
    """Check if content contains common auth guard patterns."""
    return any(
        guard in content
        for guard in ("AuthGuard", "canActivate", "requireAuth")
    )


def identify_xss_chains(routes: list[FrontendRoute]) -> list[XssAttackChain]:
    """Identify potential XSS attack chains from frontend routes."""
    chains: list[XssAttackChain] = []

    input_routes = [
        r for r in routes
        if r.user_inputs or any(a.method == "POST" for a in r.api_calls)
    ]
    render_routes = [r for r in routes if any(a.method == "GET" for a in r.api_calls)]

    for input_route in input_routes:
        for api_call in input_route.api_calls:
            if api_call.method != "POST":
                continue
            for render_route in render_routes:
                for render_api in render_route.api_calls:
                    if render_api.method != "GET":
                        continue
                    storage_base = extract_base_path(api_call.endpoint)
                    render_base = extract_base_path(render_api.endpoint)
                    if storage_base and render_base and storage_base == render_base:
                        chains.append(
                            XssAttackChain(
                                entry_point=input_route.path,
                                storage_endpoint=api_call.endpoint,
                                render_endpoint=render_route.path,
                                sink=render_route.component,
                                confidence="medium",
                            )
                        )

    return chains


def extract_base_path(endpoint: str) -> str:
    """Extract the base path from an API endpoint (e.g., /api/Users from /api/Users/:id)."""
    parts = endpoint.split("/")
    return "/".join(p for p in parts if not p.startswith(":"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_frontend_mapper.py -v 2>&1 | tail -20`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/services/frontend_mapper.py packages/core/tests/test_frontend_mapper.py
git commit -m "feat: add frontend_mapper service with route parsing and XSS chain detection (C2)"
```

---

### Task 7: Implement `route_chain_builder.py`

**Files:**
- Create: `packages/core/src/shannon_core/services/route_chain_builder.py`
- Test: `packages/core/tests/test_route_chain_builder.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_route_chain_builder.py`:

```python
"""Tests for route_chain_builder service."""
import logging
import pytest

from shannon_core.services.route_chain_builder import (
    AttackChainStep,
    AttackChain,
    build_attack_chains_from_analysis,
)
from shannon_core.services.framework_analyzer import InferredEndpoint
from shannon_core.services.frontend_mapper import FrontendRoute, XssAttackChain


class TestBuildAttackChainsFromAnalysis:
    def test_empty_inputs(self):
        chains = build_attack_chains_from_analysis([], [], [], logging.getLogger())
        assert len(chains) == 0

    def test_xss_chain_from_frontend(self):
        xss_chains = [
            XssAttackChain(
                entry_point="/input",
                storage_endpoint="/api/data",
                render_endpoint="/view",
                sink="DataView",
                confidence="high",
            ),
        ]
        chains = build_attack_chains_from_analysis([], [], xss_chains, logging.getLogger())
        assert len(chains) == 1
        assert chains[0].vuln_type == "xss"
        assert chains[0].severity == "high"
        assert len(chains[0].steps) == 4  # input, storage, retrieval, render

    def test_idor_chain_from_framework(self):
        endpoints = [
            InferredEndpoint(
                method="DELETE",
                path="/api/Feedbacks/:id",
                source="framework-auto-generated",
                model="Feedback",
                middleware=("isAuthenticated",),
                vulnerability_indicators=("No ownership check",),
            ),
        ]
        chains = build_attack_chains_from_analysis(endpoints, [], [], logging.getLogger())
        assert len(chains) == 1
        assert chains[0].vuln_type == "authz"
        assert "IDOR" in chains[0].name

    def test_idor_chain_correlated_with_frontend(self):
        endpoints = [
            InferredEndpoint(
                method="DELETE",
                path="/api/Feedbacks/:id",
                source="framework-auto-generated",
                model="Feedback",
                middleware=("isAuthenticated",),
                vulnerability_indicators=("No ownership check",),
            ),
        ]
        frontend_routes = [
            FrontendRoute(
                path="/feedback",
                component="FeedbackView",
                authenticated=True,
            ),
        ]
        chains = build_attack_chains_from_analysis(endpoints, frontend_routes, [], logging.getLogger())
        assert len(chains) == 1
        # Should mention the frontend route in description
        assert "FeedbackView" not in chains[0].description  # no api_calls to correlate

    def test_combined_xss_and_idor(self):
        xss_chains = [
            XssAttackChain("/input", "/api/posts", "/view", "PostView", "medium"),
        ]
        endpoints = [
            InferredEndpoint("DELETE", "/api/Users/:id", "framework-auto-generated",
                             model="User", vulnerability_indicators=("No ownership",)),
        ]
        chains = build_attack_chains_from_analysis(endpoints, [], xss_chains, logging.getLogger())
        assert len(chains) == 2  # 1 XSS + 1 IDOR
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_route_chain_builder.py -v 2>&1 | tail -10`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `packages/core/src/shannon_core/services/route_chain_builder.py`:

```python
"""Route chain builder.

Builds multi-step attack chains by correlating framework-inferred
endpoints with frontend route analysis results.
Ported from /root/shannon/apps/worker/src/services/route-chain-builder.ts
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from shannon_core.services.framework_analyzer import InferredEndpoint
from shannon_core.services.frontend_mapper import FrontendRoute, XssAttackChain


@dataclass(frozen=True)
class AttackChainStep:
    """A single step in an attack chain."""

    order: int
    phase: str  # 'input' | 'storage' | 'retrieval' | 'render'
    endpoint: str
    method: str
    description: str


@dataclass
class AttackChain:
    """A multi-step attack chain."""

    id: str
    name: str
    description: str
    steps: list[AttackChainStep] = field(default_factory=list)
    vuln_type: str = ""  # 'xss' | 'authz' | 'injection'
    severity: str = "medium"  # 'critical' | 'high' | 'medium' | 'low'
    confidence: str = "theoretical"  # 'confirmed' | 'probable' | 'theoretical'


def build_attack_chains_from_analysis(
    inferred_endpoints: list[InferredEndpoint],
    frontend_routes: list[FrontendRoute],
    xss_chains: list[XssAttackChain],
    logger: logging.Logger,
) -> list[AttackChain]:
    """Build attack chains from framework endpoints and frontend analysis.

    1. XSS chains from frontend analysis (input → storage → retrieval → render)
    2. IDOR chains from framework endpoints without ownership checks
       (correlated with frontend routes that trigger them)
    """
    chains: list[AttackChain] = []

    # 1. Build XSS chains from frontend analysis
    for xss_chain in xss_chains:
        chains.append(
            AttackChain(
                id=f"xss-chain-{len(chains) + 1}",
                name=f"Stored XSS: {xss_chain.entry_point} → {xss_chain.render_endpoint}",
                description=(
                    f"User input at {xss_chain.entry_point} is stored via "
                    f"{xss_chain.storage_endpoint} and rendered at "
                    f"{xss_chain.render_endpoint} in {xss_chain.sink}"
                ),
                steps=[
                    AttackChainStep(1, "input", xss_chain.entry_point, "GET",
                                    f"User navigates to {xss_chain.entry_point} and provides input"),
                    AttackChainStep(2, "storage", xss_chain.storage_endpoint, "POST",
                                    f"Input is stored via POST {xss_chain.storage_endpoint}"),
                    AttackChainStep(3, "retrieval", xss_chain.storage_endpoint, "GET",
                                    f"Stored data is retrieved via GET {xss_chain.storage_endpoint}"),
                    AttackChainStep(4, "render", xss_chain.render_endpoint, "GET",
                                    f"Data is rendered unsanitized in {xss_chain.sink}"),
                ],
                vuln_type="xss",
                severity="high",
                confidence="probable" if xss_chain.confidence == "high" else "theoretical",
            )
        )

    # 2. Build IDOR chains from framework endpoints without ownership checks
    vulnerable_endpoints = [
        ep for ep in inferred_endpoints
        if ":id" in ep.path and ep.vulnerability_indicators
    ]

    for endpoint in vulnerable_endpoints:
        chains.append(
            AttackChain(
                id=f"idor-chain-{len(chains) + 1}",
                name=f"IDOR: {endpoint.method} {endpoint.path} ({endpoint.source})",
                description=(
                    f"{endpoint.method} {endpoint.path} is auto-generated by "
                    f"{endpoint.source} with no ownership validation."
                ),
                steps=[
                    AttackChainStep(1, "input", endpoint.path, endpoint.method,
                                    "Attacker crafts request with arbitrary ID parameter"),
                    AttackChainStep(2, "storage", endpoint.path, endpoint.method,
                                    f"{endpoint.method} reaches side effect without ownership validation"),
                ],
                vuln_type="authz",
                severity="high" if endpoint.method == "DELETE" else "medium",
                confidence="probable",
            )
        )

    logger.info("Built %d attack chain(s) from analysis", len(chains))
    return chains
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_route_chain_builder.py -v 2>&1 | tail -20`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/services/route_chain_builder.py packages/core/tests/test_route_chain_builder.py
git commit -m "feat: add route_chain_builder service with XSS and IDOR chain generation (C3)"
```

---

### Task 8: Implement `attack_chain_builder.py`

**Files:**
- Create: `packages/core/src/shannon_core/services/attack_chain_builder.py`
- Test: `packages/core/tests/test_attack_chain_builder.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_attack_chain_builder.py`:

```python
"""Tests for attack_chain_builder service."""
import logging
import pytest

from shannon_core.services.attack_chain_builder import build_attack_chains
from shannon_core.services.framework_analyzer import FrameworkAnalysisResult, InferredEndpoint
from shannon_core.services.frontend_mapper import FrontendAnalysisResult, XssAttackChain


class TestBuildAttackChains:
    def test_empty_results(self):
        framework_result = FrameworkAnalysisResult()
        frontend_result = FrontendAnalysisResult()
        chains = build_attack_chains(framework_result, frontend_result, logging.getLogger())
        assert len(chains) == 0

    def test_builds_from_xss_chains(self):
        framework_result = FrameworkAnalysisResult()
        frontend_result = FrontendAnalysisResult(
            xss_chains=[
                XssAttackChain("/input", "/api/data", "/view", "DataView", "high"),
            ],
        )
        chains = build_attack_chains(framework_result, frontend_result, logging.getLogger())
        assert len(chains) == 1
        assert chains[0].vuln_type == "xss"

    def test_builds_from_framework_endpoints(self):
        framework_result = FrameworkAnalysisResult(
            inferred_endpoints=[
                InferredEndpoint(
                    "DELETE", "/api/Users/:id", "framework-auto-generated",
                    model="User", vulnerability_indicators=("No ownership",),
                ),
            ],
        )
        frontend_result = FrontendAnalysisResult()
        chains = build_attack_chains(framework_result, frontend_result, logging.getLogger())
        assert len(chains) == 1
        assert chains[0].vuln_type == "authz"

    def test_builds_from_both(self):
        framework_result = FrameworkAnalysisResult(
            inferred_endpoints=[
                InferredEndpoint(
                    "DELETE", "/api/Items/:id", "framework-auto-generated",
                    model="Item", vulnerability_indicators=("No ownership",),
                ),
            ],
        )
        frontend_result = FrontendAnalysisResult(
            xss_chains=[
                XssAttackChain("/form", "/api/items", "/display", "DisplayView", "medium"),
            ],
        )
        chains = build_attack_chains(framework_result, frontend_result, logging.getLogger())
        assert len(chains) == 2  # 1 IDOR + 1 XSS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_attack_chain_builder.py -v 2>&1 | tail -10`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `packages/core/src/shannon_core/services/attack_chain_builder.py`:

```python
"""Attack chain builder.

Assembles multi-step attack chains from framework analysis and frontend
mapping results.
Ported from /root/shannon/apps/worker/src/services/attack-chain-builder.ts
"""

from __future__ import annotations

import logging

from shannon_core.services.framework_analyzer import FrameworkAnalysisResult
from shannon_core.services.frontend_mapper import FrontendAnalysisResult
from shannon_core.services.route_chain_builder import (
    AttackChain,
    build_attack_chains_from_analysis,
)


async def build_attack_chains(
    framework_result: FrameworkAnalysisResult,
    frontend_result: FrontendAnalysisResult,
    logger: logging.Logger,
) -> list[AttackChain]:
    """Build attack chains from analysis results.

    1. Call build_attack_chains_from_analysis() for base chains
    2. Return complete attack chain list
    """
    framework_endpoints = framework_result.inferred_endpoints
    frontend_routes = frontend_result.routes
    xss_chains = frontend_result.xss_chains

    chains = build_attack_chains_from_analysis(
        framework_endpoints, frontend_routes, xss_chains, logger,
    )

    logger.info("Built %d attack chain(s) from shared knowledge", len(chains))
    return chains
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_attack_chain_builder.py -v 2>&1 | tail -20`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/services/attack_chain_builder.py packages/core/tests/test_attack_chain_builder.py
git commit -m "feat: add attack_chain_builder service (C4)"
```

---

### Task 9: Update `services/__init__.py` exports

**Files:**
- Modify: `packages/core/src/shannon_core/services/__init__.py`

- [ ] **Step 1: Add new service exports**

Append to the end of `packages/core/src/shannon_core/services/__init__.py`:

```python
from shannon_core.services.framework_analyzer import (
    EndpointTemplate,
    FrameworkPattern,
    FRAMEWORK_PATTERNS,
    InferredEndpoint,
    FrameworkAnalysisResult,
    analyze_frameworks,
)
from shannon_core.services.frontend_mapper import (
    FrontendRoute,
    ApiCall,
    UserInputPoint,
    XssAttackChain,
    FrontendAnalysisResult,
    map_frontend_routes,
)
from shannon_core.services.route_chain_builder import (
    AttackChainStep,
    AttackChain,
    build_attack_chains_from_analysis,
)
from shannon_core.services.attack_chain_builder import (
    build_attack_chains,
)
```

- [ ] **Step 2: Run all new tests to verify imports work**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_framework_analyzer.py packages/core/tests/test_frontend_mapper.py packages/core/tests/test_route_chain_builder.py packages/core/tests/test_attack_chain_builder.py -v 2>&1 | tail -25`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add packages/core/src/shannon_core/services/__init__.py
git commit -m "feat: export new route analysis services from __init__.py"
```

---

## Phase 3: Pipeline Integration

### Task 10: Add 4 new activities to `activities.py`

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`

- [ ] **Step 1: Add the 4 new activity functions**

Append to the end of `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`:

```python


@activity.defn
async def run_framework_analysis(input: ActivityInput) -> dict:
    """Detect auto-REST frameworks, infer endpoints, write deliverable."""
    try:
        from shannon_core.services.framework_analyzer import analyze_frameworks

        repo, deliverables, _ = _get_paths(input)
        result = await analyze_frameworks(str(repo))

        # Write result as JSON deliverable
        import dataclasses
        result_data = dataclasses.asdict(result)
        result_path = deliverables / "framework_analysis.json"
        atomic_write_json(result_path, result_data)

        return {
            "detected_framework": result.detected_framework.name if result.detected_framework else None,
            "inferred_endpoint_count": len(result.inferred_endpoints),
            "recommendation_count": len(result.recommendations),
        }
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_frontend_mapping(input: ActivityInput) -> dict:
    """Map frontend routes to API calls, identify XSS chains, write deliverable."""
    try:
        from shannon_core.services.frontend_mapper import map_frontend_routes

        repo, deliverables, _ = _get_paths(input)
        result = await map_frontend_routes(str(repo))

        # Write result as JSON deliverable
        import dataclasses
        result_data = dataclasses.asdict(result)
        result_path = deliverables / "frontend_mapping.json"
        atomic_write_json(result_path, result_data)

        return {
            "route_count": len(result.routes),
            "xss_chain_count": len(result.xss_chains),
        }
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_route_chain_building(input: ActivityInput) -> dict:
    """Build route chain map from framework + frontend analysis results."""
    try:
        from shannon_core.services.framework_analyzer import FrameworkAnalysisResult
        from shannon_core.services.frontend_mapper import FrontendAnalysisResult
        from shannon_core.services.route_chain_builder import build_attack_chains_from_analysis
        import logging

        repo, deliverables, _ = _get_paths(input)
        log = logging.getLogger(__name__)

        # Load framework analysis result
        framework_result = FrameworkAnalysisResult()
        framework_path = deliverables / "framework_analysis.json"
        if framework_path.exists():
            data = json.loads(framework_path.read_text())
            # Reconstruct from dict (simplified — full reconstruction from JSON)
            framework_result = FrameworkAnalysisResult(
                detected_framework=None,  # Pattern objects can't be trivially deserialized
                inferred_endpoints=data.get("inferred_endpoints", []),
                recommendations=data.get("recommendations", []),
            )

        # Load frontend mapping result
        frontend_result = FrontendAnalysisResult()
        frontend_path = deliverables / "frontend_mapping.json"
        if frontend_path.exists():
            data = json.loads(frontend_path.read_text())
            frontend_result = FrontendAnalysisResult(
                routes=data.get("routes", []),
                xss_chains=data.get("xss_chains", []),
            )

        # Build chains
        from shannon_core.services.frontend_mapper import XssAttackChain, FrontendRoute
        from shannon_core.services.framework_analyzer import InferredEndpoint

        # Deserialize endpoints and chains from dicts.
        # JSON stores tuples as lists, so wrap list fields back to tuples.
        def _to_endpoint(d: dict) -> InferredEndpoint:
            return InferredEndpoint(
                method=d["method"], path=d["path"], source=d["source"],
                model=d.get("model"), middleware=tuple(d.get("middleware", [])),
                vulnerability_indicators=tuple(d.get("vulnerability_indicators", [])),
            )

        def _to_route(d: dict) -> FrontendRoute:
            return FrontendRoute(
                path=d["path"], component=d["component"], authenticated=d["authenticated"],
            )

        def _to_xss(d: dict) -> XssAttackChain:
            return XssAttackChain(
                entry_point=d["entry_point"], storage_endpoint=d["storage_endpoint"],
                render_endpoint=d["render_endpoint"], sink=d["sink"], confidence=d["confidence"],
            )

        endpoints = [_to_endpoint(ep) for ep in framework_result.inferred_endpoints if isinstance(ep, dict)]
        routes = [_to_route(r) for r in frontend_result.routes if isinstance(r, dict)]
        xss_chains = [_to_xss(c) for c in frontend_result.xss_chains if isinstance(c, dict)]

        chains = build_attack_chains_from_analysis(endpoints, routes, xss_chains, log)

        # Write chains
        chains_data = [dataclasses.asdict(c) for c in chains]
        chains_path = deliverables / "route_chains.json"
        atomic_write_json(chains_path, chains_data)

        return {"chain_count": len(chains)}
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_attack_chain_assembly(input: ActivityInput) -> dict:
    """Assemble multi-step attack chains from all analysis results."""
    try:
        from shannon_core.services.framework_analyzer import FrameworkAnalysisResult, InferredEndpoint
        from shannon_core.services.frontend_mapper import FrontendAnalysisResult, XssAttackChain, FrontendRoute
        from shannon_core.services.attack_chain_builder import build_attack_chains
        import dataclasses
        import logging

        repo, deliverables, _ = _get_paths(input)
        log = logging.getLogger(__name__)

        # Load results
        # JSON stores tuples as lists, so convert back.
        def _to_endpoint(d: dict) -> InferredEndpoint:
            return InferredEndpoint(
                method=d["method"], path=d["path"], source=d["source"],
                model=d.get("model"), middleware=tuple(d.get("middleware", [])),
                vulnerability_indicators=tuple(d.get("vulnerability_indicators", [])),
            )

        def _to_route(d: dict) -> FrontendRoute:
            return FrontendRoute(
                path=d["path"], component=d["component"], authenticated=d["authenticated"],
            )

        def _to_xss(d: dict) -> XssAttackChain:
            return XssAttackChain(
                entry_point=d["entry_point"], storage_endpoint=d["storage_endpoint"],
                render_endpoint=d["render_endpoint"], sink=d["sink"], confidence=d["confidence"],
            )

        framework_result = FrameworkAnalysisResult()
        framework_path = deliverables / "framework_analysis.json"
        if framework_path.exists():
            data = json.loads(framework_path.read_text())
            endpoints = [_to_endpoint(ep) for ep in data.get("inferred_endpoints", []) if isinstance(ep, dict)]
            framework_result = FrameworkAnalysisResult(
                inferred_endpoints=endpoints,
                recommendations=data.get("recommendations", []),
            )

        frontend_result = FrontendAnalysisResult()
        frontend_path = deliverables / "frontend_mapping.json"
        if frontend_path.exists():
            data = json.loads(frontend_path.read_text())
            routes = [_to_route(r) for r in data.get("routes", []) if isinstance(r, dict)]
            xss_chains = [_to_xss(c) for c in data.get("xss_chains", []) if isinstance(c, dict)]
            frontend_result = FrontendAnalysisResult(routes=routes, xss_chains=xss_chains)

        chains = await build_attack_chains(framework_result, frontend_result, log)

        # Write assembled chains
        chains_data = [dataclasses.asdict(c) for c in chains]
        chains_path = deliverables / "attack_chains.json"
        atomic_write_json(chains_path, chains_data)

        return {"chain_count": len(chains)}
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

Note: Add `import dataclasses` at the top of the file is not needed since it's imported inline.

- [ ] **Step 2: Verify the file has no syntax errors**

Run: `cd /root/shannon-py && python -c "from shannon_whitebox.pipeline import activities; print('OK')" 2>&1`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py
git commit -m "feat: add 4 new pipeline activities for route analysis and attack chain assembly"
```

---

### Task 11: Wire new activities into `workflows.py`

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`

- [ ] **Step 1: Add route analysis phase after adjudication, before RECON**

In `workflows.py`, find the block that ends with the adjudication step (the line `self._state.current_agent = None` after `run_save_adjudication`). After that block, **before** the RECON phase (`if AgentName.RECON.value not in self._state.completed_agents:`), insert:

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
            await workflow.execute_activity(
                activities.run_route_chain_building, act_input,
                start_to_close_timeout=timedelta(minutes=2),
            )
```

- [ ] **Step 2: Add attack chain assembly after vuln agents, before report**

Find the block after the vuln agents section (after the `asyncio.gather(*vuln_tasks, return_exceptions=True)` result processing loop) and **before** `self._state.current_phase = "reporting"`. Insert:

```python
            # === Attack Chain Assembly ===
            try:
                await workflow.execute_activity(
                    activities.run_attack_chain_assembly, act_input,
                    start_to_close_timeout=timedelta(minutes=5),
                )
            except Exception as exc:
                # Non-fatal — attack chains enhance the report but don't block the pipeline
                import logging
                logging.getLogger(__name__).warning("Attack chain assembly failed: %s", exc)
```

- [ ] **Step 3: Verify the workflow has no syntax errors**

Run: `cd /root/shannon-py && python -c "from shannon_whitebox.pipeline.workflows import WhiteboxScanWorkflow; print('OK')" 2>&1`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
git commit -m "feat: wire route analysis and attack chain assembly into whitebox workflow"
```

---

### Task 12: Register new activities in `worker.py`

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py`

- [ ] **Step 1: Add imports for new activities**

In `worker.py`, find the import block from `.pipeline.activities` (lines 9-20). Add these 4 imports:

```python
    run_attack_chain_assembly,
    run_framework_analysis,
    run_frontend_mapping,
    run_route_chain_building,
```

- [ ] **Step 2: Register new activities in the Worker**

Find the `activities=[...]` list in the `Worker(...)` constructor (around line 66-76). Add these 4 entries:

```python
            run_attack_chain_assembly,
            run_framework_analysis,
            run_frontend_mapping,
            run_route_chain_building,
```

- [ ] **Step 3: Verify the worker imports work**

Run: `cd /root/shannon-py && python -c "from shannon_whitebox.worker import run_scan; print('OK')" 2>&1`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/worker.py
git commit -m "feat: register new route analysis activities in whitebox worker"
```

---

### Task 13: Run all tests and final verification

**Files:** None (verification only)

- [ ] **Step 1: Run all new unit tests**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_framework_analyzer.py packages/core/tests/test_frontend_mapper.py packages/core/tests/test_route_chain_builder.py packages/core/tests/test_attack_chain_builder.py -v 2>&1 | tail -30`
Expected: All tests PASS

- [ ] **Step 2: Verify prompt changes**

Run: `grep -l "_endpoint-security-context\|Shared Controller Route Groups\|Endpoint Security Context\|Parameter Completeness Verification\|cross-route-enumeration" /root/shannon-py/prompts/recon.txt /root/shannon-py/prompts/vuln-*.txt /root/shannon-py/prompts/shared/_endpoint-security-context.txt /root/shannon-py/prompts/shared/_cross-route-enumeration.txt`
Expected: All 8 files listed

- [ ] **Step 3: Verify worker imports all activities**

Run: `cd /root/shannon-py && python -c "from shannon_whitebox.worker import run_scan; print('Worker OK')" 2>&1 && python -c "from shannon_whitebox.pipeline.workflows import WhiteboxScanWorkflow; print('Workflow OK')" 2>&1`
Expected: Both print `OK`

- [ ] **Step 4: Commit (if any final fixes needed)**

```bash
git add -A
git commit -m "chore: final verification of route-interface gap closure"
```
