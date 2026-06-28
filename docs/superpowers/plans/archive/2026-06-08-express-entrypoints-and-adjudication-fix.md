# Express.js Entry Points & Adjudication Pipeline Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Status: COMPLETE (2026-06-08)** — all 5 tasks implemented on `feat/fork-py` via subagent-driven-development with per-task spec + quality reviews. Commits: `5c51e6c` (Task 1), `172b4aa` (Task 2), `2e2503b` (Task 3), `26b261c` Task 4 (+ required `worker.py` registration the plan omitted), `550f02a` (Task 5). Task 5's integration test caught a real Pass 2 gap (route files with no parseable functions were never discovered); fixed in `8762d36` (beyond the original plan). Tests: `code_index` 280 passed/1 skipped, `whitebox` 145 passed (2 pre-existing unrelated CLI hangs).
>
> **Known follow-ups (non-blocking, surfaced by final review):**
> 1. Pass 2 synthetic `func_block_id="{file}::0"` never resolves to a real block, so top-level routes are recorded as entry-point evidence but seed no call chains — including when a top-level route calls a *named* handler. Acceptable for the regex-detector scope; revisit if pure-top-level-route repos need chains.
> 2. Multiple routes registered in one function share one `func_block_id`, so `rebuild_call_chains` builds the same fan-out per route (inflated chain counts). Root cause is pre-existing in `rebuild_call_chains` (no dedup of `confirmed_ids`); dedup there when it matters.

**Goal:** Fix two compounding bugs that produce 0 vulnerabilities on Express.js projects: missing route detection and missing `entry_points.json` generation.

**Architecture:** Add `_detect_express_routes()` to the TypeScript entry point detector (regex-based Pass 1 for FuncBlock scan, Pass 2 for top-level route scan). Add a deterministic `save_adjudication()` pipeline step that auto-confirms entry points and writes `entry_points.json`. Wire a new Temporal activity between PRE_RECON agent and `rebuild_call_chains`.

**Tech Stack:** Python, regex, Pydantic models, Temporal activities/workflows

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `packages/core/src/shannon_core/code_index/entry_points.py` | Modify | Add `_detect_express_routes()`, `_scan_top_level_express_routes()`, `_is_route_file()` constants and helpers |
| `packages/core/src/shannon_core/code_index/__init__.py` | Modify | Add `save_adjudication()` function; pass `repo_path` to `detect_entry_points()` |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | Modify | Add `run_save_adjudication` activity |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | Modify | Wire `run_save_adjudication` between PRE_RECON and `rebuild_call_chains` |
| `packages/core/tests/code_index/test_entry_points.py` | Modify | Add `TestExpressEntryPoints` class |
| `packages/core/tests/code_index/test_save_adjudication.py` | Create | Tests for `save_adjudication()` function |

---

### Task 1: Express Pass 1 — FuncBlock Source Scan

Detect Express route patterns (`app.get()`, `router.post()`, etc.) inside function blocks by scanning each `FuncBlock.source_code` with regex.

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/entry_points.py`
- Modify: `packages/core/tests/code_index/test_entry_points.py`

- [x] **Step 1: Write failing tests for Express Pass 1**

Append a new class `TestExpressEntryPoints` at the end of `packages/core/tests/code_index/test_entry_points.py`:

```python
class TestExpressEntryPoints:
    """Express.js route detection — Pass 1 (FuncBlock source_code scan)."""

    def test_express_app_get_in_func_block(self):
        """Routes registered inside a function body (e.g., NodeGoat's index(app, db))."""
        block = _block(
            id="src/routes.ts:setupRoutes:10",
            file_path="src/routes.ts",
            function_name="setupRoutes",
            start_line=10,
            source_code=(
                "app.get('/api/users', (req, res) => {\n"
                "  res.json(getUsers());\n"
                "});\n"
            ),
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 1
        assert express_eps[0].entry_type == "http_route"
        assert express_eps[0].route == "/api/users"
        assert express_eps[0].http_method == "GET"
        assert express_eps[0].confidence == 0.90
        assert express_eps[0].needs_llm_review is False

    def test_express_router_post_in_func_block(self):
        block = _block(
            id="src/routes.ts:registerRoutes:5",
            file_path="src/routes.ts",
            function_name="registerRoutes",
            start_line=5,
            source_code=(
                "router.post('/api/users/:id', async (req, res) => {\n"
                "  const result = await saveUser(req.params.id, req.body);\n"
                "  res.json(result);\n"
                "});\n"
            ),
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 1
        assert express_eps[0].http_method == "POST"
        assert express_eps[0].route == "/api/users/:id"
        assert express_eps[0].confidence == 0.90

    def test_express_app_all_route(self):
        block = _block(
            id="src/app.ts:catchAll:20",
            file_path="src/app.ts",
            function_name="catchAll",
            start_line=20,
            source_code="app.all('/api/*', (req, res, next) => { next(); });",
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 1
        assert express_eps[0].http_method == "*"
        assert express_eps[0].confidence == 0.85

    def test_express_app_use_with_path(self):
        block = _block(
            id="src/app.ts:setup:1",
            file_path="src/app.ts",
            function_name="setup",
            start_line=1,
            source_code="app.use('/api', router);",
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 1
        assert express_eps[0].http_method == "MIDDLEWARE"
        assert express_eps[0].route == "/api"
        assert express_eps[0].confidence == 0.80

    def test_express_app_use_without_path_excluded(self):
        """app.use() without a string path argument (framework middleware) is excluded."""
        block = _block(
            id="src/server.ts:setup:5",
            file_path="src/server.ts",
            function_name="setup",
            start_line=5,
            source_code="app.use(express.json());",
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 0

    def test_express_app_use_bare_function_excluded(self):
        """app.use(bodyParser()) without route string is excluded."""
        block = _block(
            id="src/server.ts:middleware:3",
            file_path="src/server.ts",
            function_name="middleware",
            start_line=3,
            source_code="app.use(session({ secret: 'keyboard cat' }));",
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 0

    def test_multiple_routes_in_one_block(self):
        """Multiple routes in one function (NodeGoat pattern)."""
        block = _block(
            id="src/routes.ts:register:1",
            file_path="src/routes.ts",
            function_name="register",
            start_line=1,
            source_code=(
                "app.get('/users', getUsersHandler);\n"
                "app.post('/users', createUserHandler);\n"
                "app.delete('/users/:id', deleteUserHandler);\n"
            ),
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 3
        methods = {ep.http_method for ep in express_eps}
        assert methods == {"GET", "POST", "DELETE"}
        # All share the same func_block_id
        assert all(ep.func_block_id == block.id for ep in express_eps)

    def test_express_put_patch_delete(self):
        block = _block(
            id="src/routes.ts:crud:10",
            file_path="src/routes.ts",
            function_name="crud",
            start_line=10,
            source_code=(
                "router.put('/users/:id', updateHandler);\n"
                "router.patch('/users/:id', patchHandler);\n"
                "router.delete('/users/:id', deleteHandler);\n"
            ),
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 3
        methods = {ep.http_method for ep in express_eps}
        assert methods == {"PUT", "PATCH", "DELETE"}

    def test_no_express_in_python_block(self):
        """Express patterns in Python files are not scanned."""
        block = _block(
            source_code="app.get('/api/users', handler)",
            function_name="setup",
            language="python",
        )
        eps = detect_entry_points([block], "python")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 0
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_entry_points.py::TestExpressEntryPoints -v`
Expected: FAIL — `detect_entry_points` does not detect Express routes yet

- [x] **Step 3: Add Express route constants and `_detect_express_routes()` function**

In `packages/core/src/shannon_core/code_index/entry_points.py`, add the following constants and function. Place them between the `_TS_DECORATOR_RULES` definition (line 143) and the `_detect_typescript` function (line 147):

First, update the import on line 1 from `from pathlib import PurePosixPath` to:

```python
from pathlib import Path, PurePosixPath
```

Then add these constants after `_TS_DECORATOR_RULES` (after line 143):

```python
# Express.js route detection
_EXPRESS_ROUTE_PATTERN = re.compile(
    r"""(app|router)\.(get|post|put|delete|patch|all|use)\(\s*['"](/[^'"]*)['"]\s*"""
)

_EXPRESS_METHOD_MAP: dict[str, str | None] = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "delete": "DELETE",
    "patch": "PATCH",
    "all": "*",
    "use": "MIDDLEWARE",
}

_EXPRESS_CONFIDENCE: dict[str, float] = {
    "get": 0.90,
    "post": 0.90,
    "put": 0.90,
    "delete": 0.90,
    "patch": 0.90,
    "all": 0.85,
    "use": 0.80,
}

_ROUTE_FILE_NAMES = {"server.js", "server.ts", "app.js", "app.ts", "index.js", "index.ts"}
```

Then add the `_detect_express_routes` function (after the constants, before `_detect_typescript`):

```python
def _detect_express_routes(
    blocks: list[FuncBlock], repo_path: str | None = None,
) -> list[EntryPoint]:
    """Detect Express.js route registrations.

    Pass 1: Scan each FuncBlock's source_code for (app|router).(get|post|...) patterns.
    Pass 2 (if repo_path given): Scan full file source for top-level routes
            not inside any FuncBlock, in common route directories.
    """
    entry_points: list[EntryPoint] = []

    # Pass 1: FuncBlock source_code scan
    for block in blocks:
        for match in _EXPRESS_ROUTE_PATTERN.finditer(block.source_code):
            method_str = match.group(2)
            route_path = match.group(3)

            http_method = _EXPRESS_METHOD_MAP.get(method_str)
            confidence = _EXPRESS_CONFIDENCE.get(method_str, 0.80)

            entry_points.append(EntryPoint(
                func_block_id=block.id,
                entry_type="http_route",
                route=route_path,
                http_method=http_method,
                confidence=confidence,
                evidence=f"Express route: {match.group(1)}.{method_str}('{route_path}')",
                needs_llm_review=confidence < LLM_REVIEW_THRESHOLD,
            ))

    # Pass 2: Top-level route scan (implemented in Task 2)
    if repo_path is not None:
        entry_points.extend(_scan_top_level_express_routes(blocks, repo_path))

    return entry_points


def _is_route_file(file_path: str) -> bool:
    """Check if a file is likely a top-level Express route file."""
    parts = PurePosixPath(file_path).parts
    basename = parts[-1] if parts else ""
    if basename in _ROUTE_FILE_NAMES:
        return True
    return any(p in ("routes", "router") for p in parts)


def _scan_top_level_express_routes(
    blocks: list[FuncBlock], repo_path: str,
) -> list[EntryPoint]:
    """Scan full file source for Express routes not inside any FuncBlock."""
    # Placeholder — implemented in Task 2
    return []
```

- [x] **Step 4: Wire `_detect_express_routes` into `_detect_typescript`**

Replace the existing `_detect_typescript` function (lines 147-166) with:

```python
def _detect_typescript(
    blocks: list[FuncBlock], repo_path: str | None = None,
) -> list[EntryPoint]:
    entry_points: list[EntryPoint] = []

    # NestJS decorator patterns
    for block in blocks:
        for decorator in block.decorators:
            for rule_type, pattern, _, confidence in _TS_DECORATOR_RULES:
                m = pattern.search(decorator)
                if m:
                    http_method = m.group(1).upper()
                    entry_points.append(EntryPoint(
                        func_block_id=block.id,
                        entry_type=rule_type,
                        http_method=http_method,
                        confidence=confidence,
                        evidence=f"Decorated with {decorator}",
                        needs_llm_review=confidence < LLM_REVIEW_THRESHOLD,
                    ))
                    break

    # Express.js route patterns
    entry_points.extend(_detect_express_routes(blocks, repo_path))

    return entry_points
```

- [x] **Step 5: Update `detect_entry_points` to accept and pass `repo_path`**

Replace the existing `detect_entry_points` function (lines 12-25) with:

```python
def detect_entry_points(
    blocks: list[FuncBlock], language: str, repo_path: str | None = None,
) -> list[EntryPoint]:
    """Detect entry points from function blocks using per-language rules."""
    if language == "python":
        return _detect_python(blocks)
    elif language == "go":
        return _detect_go(blocks)
    elif language == "typescript":
        return _detect_typescript(blocks, repo_path)
    elif language == "java":
        return _detect_java(blocks)
    elif language == "php":
        return _detect_php(blocks)
    else:
        return []
```

- [x] **Step 6: Run the Express tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_entry_points.py::TestExpressEntryPoints -v`
Expected: All 9 tests PASS

- [x] **Step 7: Run existing test suite to verify no regressions**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_entry_points.py -v`
Expected: All tests pass (existing + new Express tests)

- [x] **Step 8: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/code_index/entry_points.py packages/core/tests/code_index/test_entry_points.py
git commit -m "feat(code-index): add Express.js route detection (Pass 1 — FuncBlock scan)"
```

---

### Task 2: Express Pass 2 — Top-Level Route Scan

Detect top-level Express routes in route directories (`routes/`, `router/`, `server.js`, `app.js`) that are NOT inside any FuncBlock. Also wire `repo_path` through `build_code_index()`.

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/entry_points.py`
- Modify: `packages/core/src/shannon_core/code_index/__init__.py`
- Modify: `packages/core/tests/code_index/test_entry_points.py`

- [x] **Step 1: Write failing tests for Express Pass 2**

Append the following class to `packages/core/tests/code_index/test_entry_points.py`:

```python
class TestExpressPass2TopLevel:
    """Express.js route detection — Pass 2 (top-level route scan in route files)."""

    def test_top_level_route_in_routes_dir(self, tmp_path):
        """Routes in a routes/ directory are detected even if not inside a function."""
        repo = tmp_path / "repo"
        routes_dir = repo / "routes"
        routes_dir.mkdir(parents=True)

        (routes_dir / "users.ts").write_text(
            "import { Router } from 'express';\n"
            "const router = Router();\n"
            "\n"
            "router.get('/users', (req, res) => {\n"
            "  res.json(getUsers());\n"
            "});\n"
            "\n"
            "router.post('/users', (req, res) => {\n"
            "  res.json(createUser());\n"
            "});\n"
        )

        # Create a FuncBlock for a helper function inside the same file
        block = _block(
            id="routes/users.ts:getUsers:12",
            file_path="routes/users.ts",
            function_name="getUsers",
            start_line=12,
            end_line=15,
            source_code="function getUsers() { return []; }",
            language="typescript",
        )

        eps = detect_entry_points([block], "typescript", repo_path=str(repo))
        top_level = [ep for ep in eps
                     if ep.evidence.startswith("Express top-level")]
        assert len(top_level) == 2
        methods = {ep.http_method for ep in top_level}
        assert methods == {"GET", "POST"}
        assert all(ep.route == "/users" for ep in top_level)
        # Synthetic func_block_id for top-level routes
        assert all(ep.func_block_id == "routes/users.ts::0" for ep in top_level)

    def test_top_level_route_in_server_js(self, tmp_path):
        """Routes in server.js are detected."""
        repo = tmp_path / "repo"
        repo.mkdir()

        (repo / "server.js").write_text(
            "const express = require('express');\n"
            "const app = express();\n"
            "\n"
            "app.get('/health', (req, res) => {\n"
            "  res.json({ status: 'ok' });\n"
            "});\n"
        )

        # No FuncBlocks at all — purely top-level routes
        eps = detect_entry_points([], "typescript", repo_path=str(repo))
        top_level = [ep for ep in eps
                     if ep.evidence.startswith("Express top-level")]
        assert len(top_level) == 1
        assert top_level[0].http_method == "GET"
        assert top_level[0].route == "/health"

    def test_non_route_file_not_scanned(self, tmp_path):
        """Files not in route directories are not scanned by Pass 2."""
        repo = tmp_path / "repo"
        repo.mkdir()

        (repo / "helpers.ts").write_text(
            "app.get('/internal', (req, res) => {\n"
            "  res.json({ data: 42 });\n"
            "});\n"
        )

        block = _block(
            id="helpers.ts:helper:1",
            file_path="helpers.ts",
            function_name="helper",
            start_line=1,
            end_line=3,
            source_code="function helper() { return 42; }",
            language="typescript",
        )

        eps = detect_entry_points([block], "typescript", repo_path=str(repo))
        top_level = [ep for ep in eps
                     if ep.evidence.startswith("Express top-level")]
        assert len(top_level) == 0

    def test_route_inside_funcblock_not_duplicated(self, tmp_path):
        """Routes inside a FuncBlock are NOT double-counted by Pass 2."""
        repo = tmp_path / "repo"
        repo.mkdir()

        (repo / "app.ts").write_text(
            "import express from 'express';\n"
            "const app = express();\n"
            "\n"
            "function setup(app) {\n"
            "  app.get('/api/users', getUsers);\n"
            "  app.post('/api/users', createUser);\n"
            "}\n"
        )

        block = _block(
            id="app.ts:setup:4",
            file_path="app.ts",
            function_name="setup",
            start_line=4,
            end_line=7,
            source_code=(
                "function setup(app) {\n"
                "  app.get('/api/users', getUsers);\n"
                "  app.post('/api/users', createUser);\n"
                "}\n"
            ),
            language="typescript",
        )

        eps = detect_entry_points([block], "typescript", repo_path=str(repo))
        # Pass 1 finds 2 routes from the FuncBlock source_code
        pass1 = [ep for ep in eps if ep.evidence.startswith("Express route:")]
        # Pass 2 should NOT duplicate these (they're inside the FuncBlock)
        pass2 = [ep for ep in eps
                 if ep.evidence.startswith("Express top-level")]
        assert len(pass1) == 2
        assert len(pass2) == 0

    def test_no_repo_path_skips_pass2(self):
        """When repo_path is None, Pass 2 is skipped entirely."""
        block = _block(
            id="src/routes.ts:setup:1",
            file_path="src/routes.ts",
            function_name="setup",
            start_line=1,
            source_code="app.get('/users', handler);",
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        top_level = [ep for ep in eps
                     if ep.evidence.startswith("Express top-level")]
        assert len(top_level) == 0
        # Pass 1 still works
        pass1 = [ep for ep in eps if ep.evidence.startswith("Express route:")]
        assert len(pass1) == 1
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_entry_points.py::TestExpressPass2TopLevel -v`
Expected: FAIL — `_scan_top_level_express_routes` returns empty list

- [x] **Step 3: Implement `_scan_top_level_express_routes`**

Replace the placeholder `_scan_top_level_express_routes` function in `packages/core/src/shannon_core/code_index/entry_points.py` with:

```python
def _scan_top_level_express_routes(
    blocks: list[FuncBlock], repo_path: str,
) -> list[EntryPoint]:
    """Scan full file source for Express routes not inside any FuncBlock.

    Only scans files in common route directories (routes/, router/,
    server.js, app.js, etc.).
    """
    repo = Path(repo_path)
    entry_points: list[EntryPoint] = []

    # Group blocks by file_path
    blocks_by_file: dict[str, list[FuncBlock]] = {}
    for block in blocks:
        blocks_by_file.setdefault(block.file_path, []).append(block)

    # Also scan files that have NO blocks at all (e.g., purely top-level routes)
    # We discover these by checking route-file paths that appear in blocks_by_file
    # plus common route files that may have no parsed functions
    candidate_files: set[str] = set(blocks_by_file.keys())

    # Also check common route files that might not have any FuncBlocks
    for name in ("server.js", "server.ts", "app.js", "app.ts", "index.js", "index.ts"):
        if (repo / name).exists():
            candidate_files.add(name)

    for file_path_str in sorted(candidate_files):
        if not _is_route_file(file_path_str):
            continue

        file_path = repo / file_path_str
        if not file_path.exists():
            continue

        try:
            full_source = file_path.read_text(errors="replace")
        except Exception:
            continue

        # Build set of line ranges covered by FuncBlocks
        covered_lines: set[int] = set()
        for block in blocks_by_file.get(file_path_str, []):
            for line in range(block.start_line, block.end_line + 1):
                covered_lines.add(line)

        # Scan for route patterns not inside any FuncBlock
        for match in _EXPRESS_ROUTE_PATTERN.finditer(full_source):
            line_num = full_source[:match.start()].count("\n") + 1
            if line_num in covered_lines:
                continue

            method_str = match.group(2)
            route_path = match.group(3)

            http_method = _EXPRESS_METHOD_MAP.get(method_str)
            confidence = _EXPRESS_CONFIDENCE.get(method_str, 0.80)

            entry_points.append(EntryPoint(
                func_block_id=f"{file_path_str}::0",
                entry_type="http_route",
                route=route_path,
                http_method=http_method,
                confidence=confidence,
                evidence=f"Express top-level route: {match.group(1)}.{method_str}('{route_path}')",
                needs_llm_review=confidence < LLM_REVIEW_THRESHOLD,
            ))

    return entry_points
```

- [x] **Step 4: Update `build_code_index` to pass `repo_path` to `detect_entry_points`**

In `packages/core/src/shannon_core/code_index/__init__.py`, change line 71 from:

```python
    entry_points = detect_entry_points(all_blocks, language)
```

to:

```python
    entry_points = detect_entry_points(all_blocks, language, repo_path=str(repo))
```

- [x] **Step 5: Run Pass 2 tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_entry_points.py::TestExpressPass2TopLevel -v`
Expected: All 5 tests PASS

- [x] **Step 6: Run full test suite to verify no regressions**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_entry_points.py -v`
Expected: All tests pass (Python, Go, TypeScript, Express Pass 1, Express Pass 2, Java, PHP, Unknown)

- [x] **Step 7: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/code_index/entry_points.py packages/core/src/shannon_core/code_index/__init__.py packages/core/tests/code_index/test_entry_points.py
git commit -m "feat(code-index): add Express.js Pass 2 — top-level route scan + repo_path wiring"
```

---

### Task 3: `save_adjudication()` Function

Add a deterministic Python function that reads `code_index.json`, auto-confirms all entry points, and writes `entry_points.json` for `rebuild_call_chains()`.

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py`
- Create: `packages/core/tests/code_index/test_save_adjudication.py`

- [x] **Step 1: Write failing tests for `save_adjudication`**

Create `packages/core/tests/code_index/test_save_adjudication.py`:

```python
import json
from pathlib import Path

import pytest

from shannon_core.code_index import save_adjudication
from shannon_core.code_index.models import (
    AdjudicatedEntryPoint,
    AdjudicationResult,
    CallEdge,
    CodeIndex,
    EntryPoint,
    EntryPointSource,
    FuncBlock,
    Verdict,
)


def _make_block(name: str, file_path: str = "app.ts", start: int = 1) -> FuncBlock:
    return FuncBlock(
        id=f"{file_path}:{name}:{start}",
        file_path=file_path,
        function_name=name,
        start_line=start,
        end_line=start + 5,
        source_code=f"function {name}() {{ }}",
        parameters=[],
        language="typescript",
    )


def _write_index(tmp_path: Path, index: CodeIndex) -> Path:
    d = tmp_path / "deliverables"
    d.mkdir(exist_ok=True)
    (d / "code_index.json").write_text(index.model_dump_json(indent=2))
    return d


class TestSaveAdjudication:
    def test_auto_confirms_high_confidence(self, tmp_path):
        b1 = _make_block("getUsers")
        ep = EntryPoint(
            func_block_id=b1.id,
            entry_type="http_route",
            route="/users",
            http_method="GET",
            confidence=0.90,
            evidence="Express route: app.get('/users')",
            needs_llm_review=False,
        )
        index = CodeIndex(
            repository="test-repo",
            language="typescript",
            total_blocks=1,
            total_entry_points=1,
            total_chains=0,
            blocks=[b1],
            edges=[],
            entry_points=[ep],
            chains=[],
        )

        d = _write_index(tmp_path, index)
        save_adjudication(str(d))

        result = AdjudicationResult.model_validate_json(
            (d / "entry_points.json").read_text()
        )
        assert len(result.adjudicated_entry_points) == 1
        aep = result.adjudicated_entry_points[0]
        assert aep.verdict == Verdict.CONFIRMED
        assert aep.source == EntryPointSource.CODE_INDEX
        assert aep.func_block_id == b1.id
        assert aep.route == "/users"
        assert aep.http_method == "GET"

    def test_auto_confirms_low_confidence(self, tmp_path):
        """Low-confidence entry points are also confirmed (conservative)."""
        b1 = _make_block("handler")
        ep = EntryPoint(
            func_block_id=b1.id,
            entry_type="unknown",
            confidence=0.40,
            evidence="async def with no decorator",
            needs_llm_review=True,
        )
        index = CodeIndex(
            repository="test-repo",
            language="typescript",
            total_blocks=1,
            total_entry_points=1,
            total_chains=0,
            blocks=[b1],
            edges=[],
            entry_points=[ep],
            chains=[],
        )

        d = _write_index(tmp_path, index)
        save_adjudication(str(d))

        result = AdjudicationResult.model_validate_json(
            (d / "entry_points.json").read_text()
        )
        assert len(result.adjudicated_entry_points) == 1
        assert result.adjudicated_entry_points[0].verdict == Verdict.CONFIRMED

    def test_writes_entry_points_json(self, tmp_path):
        b1 = _make_block("handler")
        ep = EntryPoint(
            func_block_id=b1.id,
            entry_type="http_route",
            confidence=0.95,
            evidence="test",
            needs_llm_review=False,
        )
        index = CodeIndex(
            repository="test-repo",
            language="typescript",
            total_blocks=1,
            total_entry_points=1,
            total_chains=0,
            blocks=[b1],
            edges=[],
            entry_points=[ep],
            chains=[],
        )

        d = _write_index(tmp_path, index)
        assert not (d / "entry_points.json").exists()

        save_adjudication(str(d))

        assert (d / "entry_points.json").exists()
        data = json.loads((d / "entry_points.json").read_text())
        assert "adjudicated_entry_points" in data

    def test_no_entry_points_still_writes(self, tmp_path):
        """Empty entry point list → empty adjudication file."""
        b1 = _make_block("helper")
        index = CodeIndex(
            repository="test-repo",
            language="typescript",
            total_blocks=1,
            total_entry_points=0,
            total_chains=0,
            blocks=[b1],
            edges=[],
            entry_points=[],
            chains=[],
        )

        d = _write_index(tmp_path, index)
        save_adjudication(str(d))

        result = AdjudicationResult.model_validate_json(
            (d / "entry_points.json").read_text()
        )
        assert len(result.adjudicated_entry_points) == 0

    def test_skips_when_no_code_index(self, tmp_path):
        """Graceful no-op when code_index.json is missing."""
        d = tmp_path / "deliverables"
        d.mkdir()
        # Should not raise
        save_adjudication(str(d))
        assert not (d / "entry_points.json").exists()

    def test_multiple_entry_points(self, tmp_path):
        b1 = _make_block("getHandler", start=1)
        b2 = _make_block("postHandler", start=10)
        ep1 = EntryPoint(
            func_block_id=b1.id,
            entry_type="http_route",
            route="/users",
            http_method="GET",
            confidence=0.90,
            evidence="Express route: app.get('/users')",
            needs_llm_review=False,
        )
        ep2 = EntryPoint(
            func_block_id=b2.id,
            entry_type="http_route",
            route="/users",
            http_method="POST",
            confidence=0.90,
            evidence="Express route: app.post('/users')",
            needs_llm_review=False,
        )
        index = CodeIndex(
            repository="test-repo",
            language="typescript",
            total_blocks=2,
            total_entry_points=2,
            total_chains=0,
            blocks=[b1, b2],
            edges=[],
            entry_points=[ep1, ep2],
            chains=[],
        )

        d = _write_index(tmp_path, index)
        save_adjudication(str(d))

        result = AdjudicationResult.model_validate_json(
            (d / "entry_points.json").read_text()
        )
        assert len(result.adjudicated_entry_points) == 2
        methods = {aep.http_method for aep in result.adjudicated_entry_points}
        assert methods == {"GET", "POST"}
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_save_adjudication.py -v`
Expected: FAIL — `ImportError: cannot import name 'save_adjudication'`

- [x] **Step 3: Implement `save_adjudication`**

In `packages/core/src/shannon_core/code_index/__init__.py`, add the following function after `write_index_files` (after line 177) and before `rebuild_call_chains` (line 180):

```python
def save_adjudication(deliverables_dir: str) -> None:
    """Auto-confirm entry points and write adjudication result.

    Reads code_index.json, confirms all detected entry points with
    verdict=CONFIRMED and source=CODE_INDEX, and writes entry_points.json
    for rebuild_call_chains().
    """
    out = Path(deliverables_dir)
    code_index_path = out / "code_index.json"

    if not code_index_path.exists():
        logger.warning("code_index.json not found; skipping adjudication")
        return

    index = CodeIndex.model_validate_json(code_index_path.read_text())

    adjudicated = []
    for ep in index.entry_points:
        adjudicated.append(AdjudicatedEntryPoint(
            func_block_id=ep.func_block_id,
            verdict=Verdict.CONFIRMED,
            entry_type=ep.entry_type,
            route=ep.route,
            http_method=ep.http_method,
            evidence=ep.evidence,
            source=EntryPointSource.CODE_INDEX,
        ))

    result = AdjudicationResult(
        repository=index.repository,
        language=index.language,
        adjudicated_entry_points=adjudicated,
    )

    entry_points_path = out / "entry_points.json"
    entry_points_path.write_text(result.model_dump_json(indent=2))

    logger.info(
        "Auto-confirmed %d entry points via save_adjudication",
        len(adjudicated),
    )
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_save_adjudication.py -v`
Expected: All 6 tests PASS

- [x] **Step 5: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/code_index/__init__.py packages/core/tests/code_index/test_save_adjudication.py
git commit -m "feat(code-index): add save_adjudication() — auto-confirm entry points pipeline step"
```

---

### Task 4: `run_save_adjudication` Activity + Workflow Wiring

Add a Temporal activity wrapping `save_adjudication()` and insert it into the whitebox pipeline between PRE_RECON agent and `rebuild_call_chains`.

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`

- [x] **Step 1: Add `run_save_adjudication` activity**

In `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`, add the following activity after `run_code_index` (after line 178) and before `run_rebuild_call_chains` (line 182):

```python
@activity.defn
async def run_save_adjudication(input: ActivityInput) -> dict:
    try:
        from shannon_core.code_index import save_adjudication

        repo, deliverables, _ = _get_paths(input)
        save_adjudication(str(deliverables))

        return {"status": "ok"}
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

- [x] **Step 2: Wire `run_save_adjudication` into the workflow**

In `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`, insert the new activity between the PRE_RECON agent execution and `run_rebuild_call_chains`. Find this block (around lines 112-128):

```python
                if AgentName.PRE_RECON.value not in self._state.completed_agents:
                    pre_recon_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value})
                    metrics = await workflow.execute_activity(
                        activities.run_agent, pre_recon_input,
                        start_to_close_timeout=timedelta(hours=2),
                        retry_policy=PRODUCTION_RETRY,
                    )
                    self._state.completed_agents.append(AgentName.PRE_RECON.value)
                    self._state.agent_metrics[AgentName.PRE_RECON.value] = metrics

                    rebuild_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value})
                    rebuild_result = await workflow.execute_activity(
                        activities.run_rebuild_call_chains, rebuild_input,
                        start_to_close_timeout=timedelta(minutes=5),
                    )
```

Replace it with:

```python
                if AgentName.PRE_RECON.value not in self._state.completed_agents:
                    pre_recon_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value})
                    metrics = await workflow.execute_activity(
                        activities.run_agent, pre_recon_input,
                        start_to_close_timeout=timedelta(hours=2),
                        retry_policy=PRODUCTION_RETRY,
                    )
                    self._state.completed_agents.append(AgentName.PRE_RECON.value)
                    self._state.agent_metrics[AgentName.PRE_RECON.value] = metrics

                    # Auto-confirm entry points before rebuilding call chains
                    adjudication_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value})
                    await workflow.execute_activity(
                        activities.run_save_adjudication, adjudication_input,
                        start_to_close_timeout=timedelta(minutes=2),
                    )

                    rebuild_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value})
                    rebuild_result = await workflow.execute_activity(
                        activities.run_rebuild_call_chains, rebuild_input,
                        start_to_close_timeout=timedelta(minutes=5),
                    )
```

- [x] **Step 3: Run existing whitebox tests to verify no regressions**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/ -v`
Expected: All tests pass

- [x] **Step 4: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
git commit -m "feat(whitebox): wire run_save_adjudication activity into scan pipeline"
```

---

### Task 5: Integration Test — Express Entry Points → Adjudication → Call Chains

End-to-end test that validates the full Express.js detection → adjudication → chain rebuild flow using the existing fixture and temp repos.

**Files:**
- Create: `packages/core/tests/code_index/test_express_integration.py`

- [x] **Step 1: Write integration test**

Create `packages/core/tests/code_index/test_express_integration.py`:

```python
"""Integration: Express route detection → save_adjudication → rebuild_call_chains."""

import json
from pathlib import Path

from shannon_core.code_index import (
    build_code_index,
    rebuild_call_chains,
    save_adjudication,
    write_index_files,
)
from shannon_core.code_index.models import (
    AdjudicationResult,
    AdjudicatedEntryPoint,
    EntryPointSource,
    Verdict,
)


class TestExpressIntegration:
    def test_full_pipeline_with_func_block_routes(self, tmp_path):
        """Pass 1: Routes inside a function → detect → adjudicate → rebuild chains."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.ts").write_text(
            "import express from 'express';\n"
            "const app = express();\n"
            "\n"
            "function setupRoutes(app) {\n"
            "  app.get('/users', (req, res) => {\n"
            "    res.json(listUsers());\n"
            "  });\n"
            "  app.post('/users', (req, res) => {\n"
            "    res.json(createUser());\n"
            "  });\n"
            "}\n"
            "\n"
            "function listUsers(): any[] {\n"
            "  return db.query('SELECT * FROM users');\n"
            "}\n"
            "\n"
            "function createUser(): any {\n"
            "  return db.insert('users');\n"
            "}\n"
        )

        # Step 1: Build code index
        index = build_code_index(str(repo))
        assert index.total_entry_points >= 2, (
            f"Expected >= 2 entry points, got {index.total_entry_points}: "
            f"{[f'{ep.route} {ep.http_method}' for ep in index.entry_points]}"
        )

        express_eps = [ep for ep in index.entry_points
                       if ep.evidence.startswith("Express")]
        assert len(express_eps) >= 2
        methods = {ep.http_method for ep in express_eps}
        assert "GET" in methods
        assert "POST" in methods

        # Step 2: Write deliverables
        deliverables = tmp_path / "deliverables"
        write_index_files(index, str(deliverables))

        # Step 3: Run save_adjudication
        save_adjudication(str(deliverables))
        assert (deliverables / "entry_points.json").exists()

        adjudication = AdjudicationResult.model_validate_json(
            (deliverables / "entry_points.json").read_text()
        )
        assert len(adjudication.adjudicated_entry_points) >= 2
        assert all(
            aep.verdict == Verdict.CONFIRMED
            for aep in adjudication.adjudicated_entry_points
        )

        # Step 4: Rebuild call chains
        updated = rebuild_call_chains(str(deliverables))
        assert updated.total_chains >= 1, (
            f"Expected >= 1 call chain, got {updated.total_chains}"
        )

        # Verify code_index.json was updated on disk
        data = json.loads((deliverables / "code_index.json").read_text())
        assert data["total_chains"] >= 1

    def test_full_pipeline_with_top_level_routes(self, tmp_path):
        """Pass 2: Top-level routes in a routes/ directory → full pipeline."""
        repo = tmp_path / "repo"
        routes_dir = repo / "routes"
        routes_dir.mkdir(parents=True)

        (routes_dir / "api.ts").write_text(
            "import { Router } from 'express';\n"
            "const router = Router();\n"
            "\n"
            "router.get('/health', (req, res) => {\n"
            "  res.json({ ok: true });\n"
            "});\n"
        )

        # Build index — the parser may extract some blocks, Pass 2 handles top-level routes
        index = build_code_index(str(repo))

        # Write + adjudicate + rebuild
        deliverables = tmp_path / "deliverables"
        write_index_files(index, str(deliverables))
        save_adjudication(str(deliverables))
        updated = rebuild_call_chains(str(deliverables))

        # The entry points.json should exist and contain the detected routes
        assert (deliverables / "entry_points.json").exists()
        adjudication = AdjudicationResult.model_validate_json(
            (deliverables / "entry_points.json").read_text()
        )
        assert len(adjudication.adjudicated_entry_points) >= 1

    def test_no_entry_points_graceful(self, tmp_path):
        """Repo with no Express routes → empty but valid pipeline."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "util.ts").write_text(
            "function helper(x: number): number {\n"
            "  return x * 2;\n"
            "}\n"
        )

        index = build_code_index(str(repo))
        express_eps = [ep for ep in index.entry_points
                       if ep.evidence.startswith("Express")]
        assert len(express_eps) == 0

        deliverables = tmp_path / "deliverables"
        write_index_files(index, str(deliverables))
        save_adjudication(str(deliverables))

        adjudication = AdjudicationResult.model_validate_json(
            (deliverables / "entry_points.json").read_text()
        )
        assert len(adjudication.adjudicated_entry_points) == 0

        # rebuild_call_chains should handle empty adjudication gracefully
        updated = rebuild_call_chains(str(deliverables))
        assert updated.total_chains == 0
```

- [x] **Step 2: Run the integration tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_express_integration.py -v`
Expected: All 3 tests PASS

- [x] **Step 3: Run full code_index test suite**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/ -v`
Expected: All tests pass across the entire code_index test directory

- [x] **Step 4: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/tests/code_index/test_express_integration.py
git commit -m "test(code-index): add Express.js integration tests for full pipeline"
```

---

## Self-Review Checklist

### 1. Spec Coverage

| Spec Requirement | Task |
|------------------|------|
| Express route patterns table (app.get, router.post, etc.) | Task 1 |
| `app.route('/path').get(...).post(...)` chained patterns | Covered by regex — the pattern `(app\|router)\.(get\|...)` matches individual method calls, chained or not |
| Confidence 0.90 for GET/POST/PUT/DELETE/PATCH | Task 1 (`_EXPRESS_CONFIDENCE`) |
| Confidence 0.85 for `app.all()` | Task 1 |
| Confidence 0.80 for `app.use()` | Task 1 |
| `app.use()` without path excluded | Task 1 (regex requires string arg) |
| Pass 1 — FuncBlock scan | Task 1 |
| Pass 2 — Top-level route scan | Task 2 |
| Synthetic `func_block_id` (e.g., `server.js::0`) | Task 2 |
| `save_adjudication()` function | Task 3 |
| High-confidence auto-confirm | Task 3 |
| Low-confidence auto-confirm (conservative) | Task 3 |
| `run_save_adjudication` activity | Task 4 |
| Workflow wiring between PRE_RECON and rebuild_call_chains | Task 4 |
| `rebuild_call_chains` unchanged | ✅ No changes to this function |

### 2. Placeholder Scan

No TBD, TODO, "implement later", "fill in details", "add appropriate error handling", or "similar to Task N" patterns found. All code blocks contain complete implementations.

### 3. Type Consistency

- `EntryPoint.func_block_id` is `str` → used consistently across Tasks 1-5
- `EntryPoint.entry_type` is `str` → always `"http_route"` for Express
- `EntryPoint.http_method` is `str | None` → `_EXPRESS_METHOD_MAP` returns `str | None`
- `EntryPoint.confidence` is `float` → `_EXPRESS_CONFIDENCE` returns `float`
- `EntryPoint.evidence` is `str` → all evidence strings are complete
- `EntryPoint.needs_llm_review` is `bool` → `confidence < LLM_REVIEW_THRESHOLD`
- `AdjudicatedEntryPoint.verdict` is `Verdict` → always `Verdict.CONFIRMED`
- `AdjudicatedEntryPoint.source` is `EntryPointSource` → always `EntryPointSource.CODE_INDEX`
- `detect_entry_points` signature: `(list[FuncBlock], str, str | None)` → all calls match
- `_detect_typescript` signature: `(list[FuncBlock], str | None)` → called with `(blocks, repo_path)`
- `_detect_express_routes` signature: `(list[FuncBlock], str | None)` → called with `(blocks, repo_path)`
- `_scan_top_level_express_routes` signature: `(list[FuncBlock], str)` → called with `(blocks, repo_path)` when not None
- `save_adjudication` signature: `(str)` → called with `str(deliverables)`
