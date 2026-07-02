# authz GitNexus 轨 B 配套候选来源（spec-1b） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 让 authz GitNexus 轨的 IDOR 候选非空——补 entry_points 候选来源：扩框架识别（G4）、接 OpenAPI schema 解析（G5）、解耦 fusion 的 LLM 轨门控让确定性源在关 LLM 轨时仍跑（G6），解锁端到端冒烟 V1/V2。

**Architecture:** 三件事：(1) `entry_points.py` 各语言补 FastAPI/JAX-RS/Laravel/echo+chi 路由模式（纯正则/签名，低风险）；(2) 新建 `schema_entry_parser.py` 解析 OpenAPI（yaml/json）→ `EntryPoint`，在 `run_entry_point_fusion` 里作 schema 源追加（与现有 LLM 源同构，func_block_id 去重）——**不调 `merge_entry_points`**：其 `gitnexus_eps` 是 GitNexus cypher dict 格式、与 code_index 流不匹配，且 gitnexus/convention 源当前未产，调它无收益；merge 框架留作 future 接入点；(3) `workflows.py` 把 `run_entry_point_fusion` 移出 `enable_llm_track` 门控（内部 LLM 源靠 deliverable 存在性自然 skip，schema 源无条件跑）。

**Tech Stack:** Python / pyyaml（已装 6.0.3）/ pytest / temporalio

**Spec:** `docs/superpowers/specs/2026-07-02-gitnexus-authz-deep-agent-design.md`（G4/G5/G6，spec-1b 部分）

## Global Constraints

- **不改候选生成算法**：`find_unguarded_sink_paths` / `find_framework_idor_candidates` 逻辑不变；只补 entry_points 来源（让 authz track 第一层过滤 `if not ep_sources: continue` 不再跳空）。
- **不改 LLM 轨 `vuln-authz.txt`**（保留为可选增强，双轨 OR）。
- **不改双轨 merger**：`authz_gitnexus_queue.json` schema 不变。
- **不改 chain_verdict（inj/xss/ssrf）**、**不改 IDOR 候选字段**（`IDORCandidateChain`）。
- **双轨铁律**：G4/G5/G6 都在确定性层 + 读 LLM deliverable（已产，不喂 LLM 轨 prompt）；不喂确定性产物给 LLM 轨。
- **测试**：`uv run pytest <path> -v`，只跑改动相关（全套有预存 hang，见 [[feat-fork-py-test-gotchas]]）。
- **commit**：conventional commits；`git add` 只 named 文件。
- **OpenAPI 解析健壮性（spec R4）**：解析失败 non-fatal（warning + skip），不阻塞主管道。

---

## File Structure

| 文件 | 责任 | 本 plan 改动 |
|---|---|---|
| `packages/core/src/shannon_core/code_index/entry_points.py` | `detect_entry_points` 各语言规则 | T1：G4 加 FastAPI/JAX-RS/Laravel/echo+chi |
| `packages/core/src/shannon_core/code_index/schema_entry_parser.py` | OpenAPI parser（新） | T2：新建 |
| `packages/core/src/shannon_core/code_index/__init__.py` | `run_entry_point_fusion` | T3：加 `repo_path` 参数 + schema 源追加 |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | `run_entry_point_fusion` activity | T3：调 `_fusion` 传 `repo_path=str(repo)` |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | workflow 门控 | T4：fusion 移出 `enable_llm_track` |
| `packages/core/tests/code_index/test_entry_points.py` | entry_points 单测 | T1 测试 |
| `packages/core/tests/code_index/test_schema_entry_parser.py` | OpenAPI parser 测试（新） | T2 测试（新建） |
| `packages/core/tests/code_index/test_entry_point_fusion.py` | fusion 测试 | T3 测试（扩） |
| `packages/whitebox/tests/pipeline/test_workflows_safety.py` | 门控回归锚点 | T4 测试（扩） |

---

### Task 1: G4 扩框架 entry_points 识别（FastAPI / JAX-RS / Laravel / echo+chi）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/entry_points.py`（`_PYTHON_RULES` + `_detect_python` :32-102；`_JAVA_ANNOTATION_RULES` + `_detect_java` :336-376；`_PHP_DECORATOR_RULES` + `_detect_php` :380-414；`_detect_go` :106-141）
- Test: `packages/core/tests/code_index/test_entry_points.py`

**Interfaces:**
- Consumes：`FuncBlock.decorators` / `FuncBlock.source_code`（现有，parser 产）
- Produces：`detect_entry_points` 对 FastAPI `@app.get` / JAX-RS `@GET` / Laravel `Route::get` / echo `e.GET` / chi `r.Get` 产 `EntryPoint`（func_block_id/entry_type/route/http_method/confidence/evidence/needs_llm_review/source 不变）

- [ ] **Step 1: Write failing tests（4 条新规则）**

追加到 `packages/core/tests/code_index/test_entry_points.py`：

```python
from shannon_core.code_index.entry_points import detect_entry_points
from shannon_core.code_index.models import FuncBlock


def _blk(bid, decorators=None, source="", params=None, name="f"):
    return FuncBlock(
        id=bid, file_path=bid.split(":")[0], function_name=name,
        language="x", start_line=1, end_line=10, parameters=params or [],
        decorators=decorators or [], source_code=source,
    )


def test_python_fastapi_app_get_detected():
    """FastAPI @app.get（receiver=app，现有 @router.* 不覆盖）。"""
    blk = _blk("app.py:h", decorators=["@app.get('/items/{id}')"],
               source="@app.get('/items/{id}')\ndef h(): ...", name="h")
    eps = detect_entry_points([blk], "python")
    routes = [(e.route, e.http_method) for e in eps]
    assert ("/items/{id}", "GET") in routes, f"FastAPI @app.get 应识别，实际 {routes}"


def test_java_jaxrs_method_annotation_detected():
    """JAX-RS @GET（javax.ws.rs，现有 Spring @*Mapping 不覆盖）。"""
    blk = _blk("A.java:h", decorators=["@GET"], source="@GET\npublic Response h(){}", name="h")
    eps = detect_entry_points([blk], "java")
    assert any(e.http_method == "GET" and e.entry_type == "http_route" for e in eps), \
        f"JAX-RS @GET 应识别，实际 {[(e.http_method, e.entry_type) for e in eps]}"


def test_php_laravel_route_registration_detected():
    """Laravel Route::get（顶层调用，在 source_code 里，非 decorator）。"""
    blk = _blk("routes/api.php:reg", source="Route::get('/users/{id}', [UserController::class,'show']);",
               name="reg")
    eps = detect_entry_points([blk], "php")
    routes = [(e.route, e.http_method) for e in eps]
    assert ("/users/{id}", "GET") in routes, f"Laravel Route::get 应识别，实际 {routes}"


def test_go_echo_route_registration_detected():
    """echo/gin e.GET / chi r.Get 路由注册式（参数签名之外的注册调用）。"""
    blk = _blk("main.go:setup", source='e.GET("/orders/{id}", getOrder)',
               params=["e echo.Context"], name="setup")
    eps = detect_entry_points([blk], "go")
    routes = [(e.route, e.http_method) for e in eps]
    assert ("/orders/{id}", "GET") in routes, f"echo e.GET 应识别，实际 {routes}"
```

> `FuncBlock` 必填字段以 `models.py:24` 实际为准（上面是语言占位 "x"；若 language 字段有枚举约束，按实际传 "python"/"java"/"php"/"go"——执行时核对模型，**不要删断言**，只补字段）。

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/code_index/test_entry_points.py::test_python_fastapi_app_get_detected packages/core/tests/code_index/test_entry_points.py::test_java_jaxrs_method_annotation_detected packages/core/tests/code_index/test_entry_points.py::test_php_laravel_route_registration_detected packages/core/tests/code_index/test_entry_points.py::test_go_echo_route_registration_detected -v`
Expected: FAIL — 4 条均无对应 EntryPoint 产出。

- [ ] **Step 3: Implement — Python FastAPI（扩 receiver）**

`entry_points.py` `_PYTHON_RULES`（:34）扩 receiver + api_route：

```python
_PYTHON_RULES: list[tuple[str, re.Pattern, str | None, float]] = [
    ("http_route", re.compile(r"@.*\.route\(\s*['\"](.+?)['\"]"), None, 0.95),
    # G4: receiver 扩 app/api_router（FastAPI），加 api_route
    ("http_route", re.compile(r"@(app|api_router|router)\.(get|post|put|delete|patch|api_route)\(\s*['\"](.+?)['\"]"), None, 0.95),
    ("http_route", re.compile(r"@(api_view|require_http_methods)"), None, 0.90),
    ("message_consumer", re.compile(r"@(celery\.task|app\.task|shared_task)"), None, 0.90),
]
```

`_detect_python`（:72-74）分支改 receiver 匹配 + group 索引（receiver=group(1), method=group(2), route=group(3)）：

```python
                    elif re.match(r"@(app|api_router|router)\.(get|post|put|delete|patch|api_route)", decorator):
                        method_tok = m.group(2)
                        http_method = None if method_tok == "api_route" else method_tok.upper()
                        route = m.group(3) if m.lastindex and m.lastindex >= 3 else None
```

- [ ] **Step 4: Implement — Java JAX-RS（加 method 注解规则）**

`_JAVA_ANNOTATION_RULES`（:336）加 JAX-RS 规则：

```python
_JAVA_ANNOTATION_RULES: list[tuple[str, re.Pattern, str, float]] = [
    ("http_route", re.compile(r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)"), "http_route", 0.95),
    # G4: JAX-RS method 注解（javax.ws.rs），无 @Path 类级组合故 confidence 略低 + needs_review
    ("http_route", re.compile(r"@(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b"), "http_route", 0.85),
    ("message_consumer", re.compile(r"@RabbitListener"), "message_consumer", 0.90),
]
```

`_detect_java`（:350-358）http_method 解析加 JAX-RS 分支（注解名本身即大写方法）：

```python
                    if m.lastindex and m.lastindex >= 1:
                        ann = m.group(1)
                        method_map = {
                            "GetMapping": "GET", "PostMapping": "POST",
                            "PutMapping": "PUT", "DeleteMapping": "DELETE",
                            "PatchMapping": "PATCH", "RequestMapping": None,
                        }
                        if ann in method_map:
                            http_method = method_map[ann]
                        elif ann.upper() in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
                            http_method = ann.upper()  # JAX-RS
                        else:
                            http_method = None
```

- [ ] **Step 5: Implement — PHP Laravel（source_code 扫描）**

`entry_points.py` PHP 段（`_PHP_DECORATOR_RULES` :380 上方）加 Laravel 路由模式：

```python
# G4: Laravel / $router 路由注册（顶层调用，在 source_code 里）
_LARAVEL_ROUTE_PATTERN = re.compile(r"(?:Route::|\$router->)(get|post|put|delete|patch|any|match)\(\s*['\"]([^'\"]+)['\"]")
_LARAVEL_METHOD_MAP: dict[str, str | None] = {
    "get": "GET", "post": "POST", "put": "PUT", "delete": "DELETE",
    "patch": "PATCH", "any": "*", "match": "*",
}
```

`_detect_php`（:385）函数体末尾 `return entry_points` 前加 source_code 扫描：

```python
    # G4: Laravel Route:: / $router-> 注册式（在 source_code，非 decorator）
    for block in blocks:
        for m in _LARAVEL_ROUTE_PATTERN.finditer(block.source_code):
            method_tok = m.group(1)
            route = m.group(2)
            http_method = _LARAVEL_METHOD_MAP.get(method_tok)
            entry_points.append(EntryPoint(
                func_block_id=block.id,
                entry_type="http_route",
                route=route,
                http_method=http_method,
                confidence=0.90,
                evidence=f"Laravel route: {m.group(0).strip()}",
                needs_llm_review=False,
            ))

    return entry_points
```

- [ ] **Step 6: Implement — Go echo/chi（source_code 扫描）**

`entry_points.py` Go 段（`_detect_go` 定义 :106 上方）加路由注册模式：

```python
# G4: echo/gin e.GET / chi r.Get 路由注册式（在 source_code）
_GO_ROUTE_PATTERN = re.compile(r"\b\w+\.(GET|POST|PUT|DELETE|PATCH|Any|Get|Post|Put|Delete|Patch)\(\s*['\"]([^'\"]+)['\"]")
_GO_METHOD_NORM: dict[str, str] = {
    "GET": "GET", "POST": "POST", "PUT": "PUT", "DELETE": "DELETE",
    "PATCH": "PATCH", "Any": "*",
    "Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH",
}
```

`_detect_go`（:106）函数体末尾 `return entry_points` 前加扫描：

```python
    # G4: echo/gin/chi 路由注册式（在 source_code）
    for block in blocks:
        for m in _GO_ROUTE_PATTERN.finditer(block.source_code):
            method_tok = m.group(1)
            route = m.group(2)
            http_method = _GO_METHOD_NORM.get(method_tok)
            if http_method is None:
                continue
            entry_points.append(EntryPoint(
                func_block_id=block.id,
                entry_type="http_route",
                route=route,
                http_method=http_method,
                confidence=0.90,
                evidence=f"Go route registration: {m.group(0).strip()}",
                needs_llm_review=False,
            ))

    return entry_points
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/code_index/test_entry_points.py -v`
Expected: PASS（4 新测试 + 现有 entry_points 测试不破——新规则是叠加，不改旧规则行为；若现有测试断言精确规则数，按需更新对齐）。

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/shannon_core/code_index/entry_points.py packages/core/tests/code_index/test_entry_points.py
git commit -m "feat(code_index): 扩 entry_points 框架识别 FastAPI/JAX-RS/Laravel/echo+chi（G4）"
```

---

### Task 2: G5a 新建 OpenAPI schema parser

**Files:**
- Create: `packages/core/src/shannon_core/code_index/schema_entry_parser.py`
- Test: `packages/core/tests/code_index/test_schema_entry_parser.py`（新建）

**Interfaces:**
- Consumes：repo_path（扫 `openapi.{yaml,yml,json}` / `swagger.{yaml,yml,json}`）；`pyyaml`（已装 6.0.3）；`EntryPoint` 模型（`models.py:49`）
- Produces：`parse_openapi_schema_files(repo_path: str) -> list[EntryPoint]`——每条 `(method, path)` 产一个 `EntryPoint`：`func_block_id="openapi:{spec_rel}:{METHOD}:{path}"`、`entry_type="http_route"`、`route=path`、`http_method=METHOD`、`confidence=0.80`、`needs_llm_review=True`、`authentication="required"` 若该 operation 有 security / 否则 None、`source="schema_file"`、`evidence="OpenAPI schema: {spec_rel} {METHOD} {path}"`

- [ ] **Step 1: Write the failing test**

`packages/core/tests/code_index/test_schema_entry_parser.py`（新建）：

```python
import json
from pathlib import Path
from shannon_core.code_index.schema_entry_parser import parse_openapi_schema_files


def _write(repo: Path, name: str, data):
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data) if name.endswith(".json")
                 else "paths:\n" + "".join(
        f"  {path}:\n" + "".join(f"    {m.lower()}:\n      security: [{sec}]\n"
                                 for m, sec in ops.items())
        for path, ops in data.items()))
    return p


def test_parse_openapi_json_basic(tmp_path):
    """openapi.json 的 paths → EntryPoint（schema_file 源）。"""
    _write(tmp_path, "openapi.json", {"/users/{id}": {"GET": None}})
    eps = parse_openapi_schema_files(str(tmp_path))
    assert any(e.route == "/users/{id}" and e.http_method == "GET"
               and e.source == "schema_file" and e.confidence == 0.80
               and e.entry_type == "http_route" and e.needs_llm_review for e in eps), \
        f"应产 schema 源 EntryPoint，实际 {[(e.route, e.http_method, e.source) for e in eps]}"


def test_parse_openapi_yaml_security_marks_auth_required(tmp_path):
    """operation 有 security → authentication='required'。"""
    _write(tmp_path, "openapi.yaml", {"/admin": {"POST": "bearerAuth"}})
    eps = parse_openapi_schema_files(str(tmp_path))
    admin = next(e for e in eps if e.route == "/admin")
    assert admin.authentication == "required", f"有 security 应标 required，实际 {admin.authentication}"


def test_parse_skips_non_path_files_and_malformed(tmp_path):
    """非 OpenAPI 文件 / 解析失败 → 不崩，返回空或跳过。"""
    (tmp_path / "openapi.json").write_text("{ not valid json ")  # 解析失败
    (tmp_path / "openapi.yaml").write_text("swagger: '2.0'\n")    # 无 paths
    eps = parse_openapi_schema_files(str(tmp_path))
    assert eps == [], "解析失败 / 无 paths 应跳过，返回空列表"


def test_parse_ignores_node_modules(tmp_path):
    """node_modules 下的 OpenAPI 不扫。"""
    _write(tmp_path, "node_modules/lib/openapi.json", {"/x": {"GET": None}})
    assert parse_openapi_schema_files(str(tmp_path)) == []
```

> yaml 写法 helper 上面简化了；执行时若 helper 生成的 yaml 与预期不符，直接手写 yaml 字符串（保留断言：security → required、json basic → schema_file 源）。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/code_index/test_schema_entry_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.code_index.schema_entry_parser`。

- [ ] **Step 3: Write the implementation**

`packages/core/src/shannon_core/code_index/schema_entry_parser.py`（新建）：

```python
"""OpenAPI / Swagger schema file parser → EntryPoint list.

Scans repo for openapi.{yaml,yml,json} / swagger.{yaml,yml,json}, parses
`paths` → one EntryPoint per (method, path). These are high-trust route
declarations (explicit, code-verified=False) that supplement code-level
entry point detection, especially for authz candidate generation where
code-level handlers may be missed.

Parse failures are non-fatal (warning + skip), per spec R4.
"""

import json
import logging
import os
import re
from pathlib import Path

import yaml

from shannon_core.code_index.models import EntryPoint

logger = logging.getLogger(__name__)

_OPENAPI_FILENAMES = {
    "openapi.yaml", "openapi.yml", "openapi.json",
    "swagger.yaml", "swagger.yml", "swagger.json",
}
_SKIP_DIRS = {"node_modules", ".git", "dist", "build", "vendor", ".venv", "__pycache__", ".next"}
_VALID_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}


def _find_openapi_files(repo_path: str) -> list[Path]:
    repo = Path(repo_path)
    found: list[Path] = []
    if not repo.exists():
        return found
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            if f.lower() in _OPENAPI_FILENAMES:
                found.append(Path(root) / f)
    return found


def _load_spec(path: Path) -> dict | None:
    """Load an OpenAPI/Swagger spec. Returns None on parse failure (non-fatal)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() == ".json":
            return json.loads(text)
        return yaml.safe_load(text)
    except Exception as e:  # parse failure → skip this file
        logger.warning("OpenAPI parse failed for %s: %s (skipping)", path, e)
        return None


def _has_security(op: dict, path_item: dict, spec: dict | None) -> bool:
    """operation-level security > path-level > root-level (OpenAPI inheritance)."""
    if "security" in op:
        return bool(op["security"])
    if "security" in path_item:
        return bool(path_item["security"])
    if spec and "security" in spec:
        return bool(spec["security"])
    return False


def parse_openapi_schema_files(repo_path: str) -> list[EntryPoint]:
    """Parse all OpenAPI/Swagger files under repo_path → EntryPoint list.

    Each (method, path) under `paths` yields one EntryPoint (source="schema_file",
    confidence=0.80, needs_llm_review=True). Non-OpenAPI files and parse failures
    are skipped silently (warning logged). Returns [] if repo has no spec.
    """
    entry_points: list[EntryPoint] = []
    repo = Path(repo_path)

    for spec_path in _find_openapi_files(repo_path):
        spec = _load_spec(spec_path)
        if not isinstance(spec, dict):
            continue
        paths = spec.get("paths")
        if not isinstance(paths, dict):
            continue

        spec_rel = spec_path.relative_to(repo).as_posix()

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method, op in path_item.items():
                m = method.upper()
                if m not in _VALID_METHODS:
                    continue  # skip parameters/summary etc.
                if not isinstance(op, dict):
                    op = {}
                authentication = "required" if _has_security(op, path_item, spec) else None
                entry_points.append(EntryPoint(
                    func_block_id=f"openapi:{spec_rel}:{m}:{path}",
                    entry_type="http_route",
                    route=path,
                    http_method=m,
                    confidence=0.80,
                    evidence=f"OpenAPI schema: {spec_rel} {m} {path}",
                    needs_llm_review=True,
                    authentication=authentication,
                    source="schema_file",
                ))

    logger.info("OpenAPI schema parse: %d entry points from %s", len(entry_points), repo)
    return entry_points
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/code_index/test_schema_entry_parser.py -v`
Expected: PASS（4 测试）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/schema_entry_parser.py packages/core/tests/code_index/test_schema_entry_parser.py
git commit -m "feat(code_index): 新建 OpenAPI schema parser（G5a）"
```

---

### Task 3: G5b `run_entry_point_fusion` 接 schema 源 + activity 传 repo_path

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py:363-425`（`run_entry_point_fusion`）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:611-630`（activity）
- Test: `packages/core/tests/code_index/test_entry_point_fusion.py`（扩）

**Interfaces:**
- Consumes：`parse_openapi_schema_files`（T2）；`parse_llm_entry_points`（现有）；`_get_paths`（`activities.py:32`，返回 `(repo, deliverables, workspaces)`）
- Produces：`run_entry_point_fusion(deliverables_dir: str, repo_path: str | None = None) -> CodeIndex`——`repo_path` 非空时追加 schema 源 EntryPoint（与 LLM 源同构，`func_block_id` 去重）；activity 调时传 `repo_path=str(repo)`

- [ ] **Step 1: Write the failing test**

追加到 `packages/core/tests/code_index/test_entry_point_fusion.py`：

```python
import json
from pathlib import Path
from shannon_core.code_index import run_entry_point_fusion


def _seed_code_index(deliverables: Path, entry_points=None):
    """写最小 code_index.json（含现有确定性 entry_points）。"""
    (deliverables / "code_index.json").write_text(json.dumps({
        "func_blocks": [],
        "entry_points": entry_points or [],
        "source_points": [],
        "sink_call_sites": [],
        "total_entry_points": len(entry_points or []),
    }))


def test_fusion_appends_schema_source(tmp_path):
    """repo_path 给定 + repo 有 openapi.json → schema 源 EntryPoint 追加进 index。"""
    _seed_code_index(tmp_path)  # 现有确定性 entry_points=[]
    (tmp_path / "openapi.json").write_text(json.dumps({
        "paths": {"/api/orders/{id}": {"GET": {}}}
    }))
    index = run_entry_point_fusion(str(tmp_path), repo_path=str(tmp_path))
    schema_eps = [e for e in index.entry_points if e.source == "schema_file"]
    assert any(e.route == "/api/orders/{id}" and e.http_method == "GET" for e in schema_eps), \
        f"schema 源应追加，实际 {[e.route for e in schema_eps]}"


def test_fusion_dedups_by_func_block_id(tmp_path):
    """schema 源 func_block_id 与现有确定性重复时不追加。"""
    dup = {"func_block_id": "openapi:openapi.json:GET:/x",
           "entry_type": "http_route", "route": "/x", "http_method": "GET",
           "confidence": 0.95, "evidence": "det", "needs_llm_review": False,
           "source": "code_index"}
    _seed_code_index(tmp_path, entry_points=[dup])
    (tmp_path / "openapi.json").write_text(json.dumps({"paths": {"/x": {"GET": {}}}}))
    index = run_entry_point_fusion(str(tmp_path), repo_path=str(tmp_path))
    # schema 源不会被追加（func_block_id 冲突），仍只有 1 条 /x
    xs = [e for e in index.entry_points if e.route == "/x"]
    assert len(xs) == 1, f"func_block_id 重复应去重，实际 {len(xs)} 条 /x"


def test_fusion_without_repo_path_skips_schema(tmp_path):
    """repo_path=None（旧行为）→ 不扫 OpenAPI，schema 源为空。"""
    _seed_code_index(tmp_path)
    (tmp_path / "openapi.json").write_text(json.dumps({"paths": {"/y": {"GET": {}}}}))
    index = run_entry_point_fusion(str(tmp_path))  # 不传 repo_path
    assert not any(e.source == "schema_file" for e in index.entry_points), \
        "不传 repo_path 不应扫 OpenAPI"
```

> `CodeIndex` 的 JSON schema（func_blocks/source_points/sink_call_sites 字段名）以 `models.py:72 CodeIndex` 实际为准；若最小 seed 缺字段导致 model_validate 失败，按实际补全 fixture（**不删断言**）。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/code_index/test_entry_point_fusion.py::test_fusion_appends_schema_source -v`
Expected: FAIL — 现状 `run_entry_point_fusion` 不收 `repo_path`（TypeError）/ 不追加 schema 源。

- [ ] **Step 3: Write the implementation**

`__init__.py:363-425` `run_entry_point_fusion` 改签名 + schema 源追加（保留现有 LLM 源合并逻辑）：

```python
def run_entry_point_fusion(
    deliverables_dir: str, repo_path: str | None = None,
) -> CodeIndex:
    """Merge deterministic entry points with LLM- and schema-discovered entry points.

    Sources merged (all dedup by func_block_id, deterministic code_index as base):
    - deterministic (code_index.json entry_points) — base
    - LLM (pre_recon_deliverable.md, parsed) — only if deliverable exists
    - OpenAPI/Swagger schema files (repo_path) — only if repo_path given (G5)

    Args:
        deliverables_dir: Path to the deliverables directory containing code_index.json.
        repo_path: Optional repo root to scan for OpenAPI/Swagger schema files.

    Returns:
        Updated CodeIndex with merged entry points.
    """
    from shannon_core.code_index.entry_point_fusion import parse_llm_entry_points
    from shannon_core.code_index.schema_entry_parser import parse_openapi_schema_files

    out = Path(deliverables_dir)
    code_index_path = out / "code_index.json"
    deliverable_path = out / "pre_recon_deliverable.md"

    if not code_index_path.exists():
        logger.warning("code_index.json not found; skipping entry point fusion")
        raise FileNotFoundError(f"code_index.json not found in {deliverables_dir}")

    index = CodeIndex.model_validate_json(code_index_path.read_text())

    # Source: LLM pre-recon (only if deliverable exists)
    llm_eps: list[EntryPoint] = []
    if deliverable_path.exists():
        llm_eps = parse_llm_entry_points(deliverable_path.read_text())
        logger.info("Parsed %d LLM entry points from deliverable", len(llm_eps))
    else:
        logger.info("No pre_recon_deliverable.md found; LLM fusion skipped")

    # Source: OpenAPI/Swagger schema files (G5; only if repo_path given)
    schema_eps: list[EntryPoint] = []
    if repo_path:
        schema_eps = parse_openapi_schema_files(repo_path)
        logger.info("Parsed %d schema entry points from OpenAPI files", len(schema_eps))

    # Merge: deterministic as base, append LLM- and schema-only discoveries
    deterministic_ids = {ep.func_block_id for ep in index.entry_points}
    merged_entries = list(index.entry_points)
    added_llm = 0
    added_schema = 0
    for ep in llm_eps:
        if ep.func_block_id not in deterministic_ids:
            merged_entries.append(ep)
            added_llm += 1
    for ep in schema_eps:
        if ep.func_block_id not in deterministic_ids:
            merged_entries.append(ep)
            added_schema += 1

    logger.info(
        "Entry point fusion: %d deterministic + %d LLM-only + %d schema-only = %d total",
        len(index.entry_points), added_llm, added_schema, len(merged_entries),
    )

    updated = index.model_copy(update={
        "entry_points": merged_entries,
        "total_entry_points": len(merged_entries),
    })
    code_index_path.write_text(updated.model_dump_json(indent=2))
    return updated
```

`activities.py:619` 调用点传 repo_path：

```python
        repo, deliverables, _ = _get_paths(input)
        async with get_audit_session().track_step("pre-recon", "entry-point-fusion", intent=intent_for("entry-point-fusion")):
            index = _fusion(str(deliverables), repo_path=str(repo))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/code_index/test_entry_point_fusion.py -v`
Expected: PASS（3 新测试 + 现有 fusion 测试不破——LLM 源逻辑保留，repo_path=None 时行为同前）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/__init__.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/core/tests/code_index/test_entry_point_fusion.py
git commit -m "feat(code_index): run_entry_point_fusion 接 OpenAPI schema 源（G5b）"
```

---

### Task 4: G6 fusion 门控解耦（schema/convention 无条件跑）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:173-186`（fusion 门控块）
- Test: `packages/whitebox/tests/pipeline/test_workflows_safety.py`（扩）

**Interfaces:**
- Consumes：T3 后 `run_entry_point_fusion` 内部对 LLM 源靠 `pre_recon_deliverable.md` 存在性 skip（不依赖外部门控）
- Produces：`run_entry_point_fusion` 移出 `if input.enable_llm_track:` 块（关 LLM 轨时 schema 源仍跑）；`run_merge_sink_reports` 保留门控（纯 LLM）；V6 回归锚点锁"fusion 无条件调用"

- [ ] **Step 1: Write the failing test**

追加到 `packages/whitebox/tests/pipeline/test_workflows_safety.py`：

```python
def test_entry_point_fusion_not_gated_by_llm_track():
    """G6: run_entry_point_fusion 不被 enable_llm_track 门控（schema 源关 LLM 轨时仍跑）。

    断言 workflows.py 中 run_entry_point_fusion 调用在 if enable_llm_track 块外。
    """
    import inspect
    from shannon_whitebox.pipeline import workflows

    src = inspect.getsource(workflows)

    # 找 run_entry_point_fusion 调用所在行
    fusion_line = None
    enable_line = None
    for i, line in enumerate(src.splitlines()):
        if "run_entry_point_fusion" in line and "activities." in line:
            fusion_line = i
        if "if input.enable_llm_track:" in line:
            enable_line = i

    assert fusion_line is not None, "找不到 run_entry_point_fusion 调用"

    # fusion 调用必须在最近的 if enable_llm_track 块之外：
    # 找 fusion 之前最近的 if enable_llm_track，检查它对应的块是否包含 fusion。
    # 简化断言：fusion 之后到下一个 run_merge_sink_reports 之间不应有 "if input.enable_llm_track"
    # 更稳：fusion 的缩进 <= if enable_llm_track 的缩进（同级或更外层）
    lines = src.splitlines()
    fusion_indent = len(lines[fusion_line]) - len(lines[fusion_line].lstrip())
    # 找 fusion 之前最近的 enable_llm_track
    prev_enable_indent = None
    for j in range(fusion_line - 1, -1, -1):
        if "if input.enable_llm_track:" in lines[j]:
            prev_enable_indent = len(lines[j]) - len(lines[j].lstrip())
            break
    if prev_enable_indent is not None:
        assert fusion_indent <= prev_enable_indent, \
            f"run_entry_point_fusion 应在 enable_llm_track 块外（缩进 {fusion_indent} <= {prev_enable_indent}），" \
            "G6 要求 schema 源关 LLM 轨时仍跑"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_workflows_safety.py::test_entry_point_fusion_not_gated_by_llm_track -v`
Expected: FAIL — 现状 `run_entry_point_fusion` 在 `if input.enable_llm_track:` 块内（缩进更深）。

- [ ] **Step 3: Write the implementation**

`workflows.py:173-186` 拆分：`run_merge_sink_reports` 留门控内，`run_entry_point_fusion` 移出门控：

```python
                if input.enable_llm_track:
                    # Merge deterministic sinks with LLM-discovered sinks (needs LLM deliverable)
                    await workflow.execute_activity(
                        activities.run_merge_sink_reports, act_input,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=retry_for("standard"),
                    )

                # Entry point fusion: schema/convention 无条件跑（纯确定性 + LLM 源靠
                # deliverable 存在性内部 skip）；G6 解耦——不再被 enable_llm_track 门控，
                # 让关 LLM 轨时 GitNexus 轨仍融合 OpenAPI schema 源（兜底不丢入口）。
                await workflow.execute_activity(
                    activities.run_entry_point_fusion, act_input,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=retry_for("standard"),
                )
```

> 缩进：`run_entry_point_fusion` 块与 `if input.enable_llm_track:` 同级（回到外层 `try`/phase 体的缩进），不在 if 体内。执行时核对 `workflows.py:173` 周围的实际缩进层级（外层是 pre-recon phase 的 `async with`/顺序体），保持与上下 activity 调用同级。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_workflows_safety.py -v`
Expected: PASS（新 G6 锚点 + spec-1a T5 的 authz_judge_uses_gitnexus_verdict_retry / chain_verdict_keeps_standard_retry 仍过——本 task 只动 fusion 门控，不碰 retry）。

- [ ] **Step 5: 回归——fusion 在开 LLM 轨时仍正常**

Run: `uv run pytest packages/core/tests/code_index/test_entry_point_fusion.py packages/whitebox/tests/pipeline/test_workflows_safety.py -v`
Expected: PASS（fusion 行为对 LLM 源不变；门控只移除 fusion 的外层 if，LLM 源靠 deliverable 存在性）。

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/pipeline/test_workflows_safety.py
git commit -m "feat(whitebox): entry_point_fusion 解耦 enable_llm_track 门控（G6，schema 源关 LLM 轨仍跑）"
```

---

## 验证（真机，task 全过后）

- **G4 框架召回**：真仓库（crAPI Python/FastAPI 或 Java/JAX-RS、PHP/Laravel、Go/echo）白盒跑，`code_index.json` 的 entry_points 含新框架条目（V4）。
- **G5 OpenAPI**：含 `openapi.yaml` 的 repo，`run_entry_point_fusion` 后 entry_points 含 schema 源条目（V5）。
- **G6 解耦**：`SHANNON_LLM_TRACK_ENABLED=0` 跑白盒，`run_entry_point_fusion` 仍执行，schema 源写入；LLM 源因 deliverable 不存在 skip（V6）。
- **端到端 V1/V2（epic 关键，解锁本 spec-1b 的目的）**：`entry_points` 非空 → authz track `find_unguarded_sink_paths` 第一层过滤不再全跳 → dominance_candidates 非空 → spec-1a 的 `run_authz_gitnexus_judge` 多轮深度判定吃候选产出 `authz_gitnexus_queue.json`（V1）；候选仍空时 G2 自主探索（V2）。
- **R3 token 实测（epic）**：GitNexus 深度 agent（吃候选）vs LLM 轨 vuln-authz（从零）token/召回对比——确认杠杆成立；证伪则回 epic 评估。

---

## Self-Review

**Spec coverage**（spec-1 G4/G5/G6）：
- G4（扩框架）→ T1 ✓。spec §3.4 列 Python/PHP/Java/TS/Go：本轮覆盖 Python(FastAPI)/PHP(Laravel)/Java(JAX-RS)/Go(echo+chi)——均为 decorator 或 source_code block 级可检测项；**TS GraphQL `type Query/Mutation` resolver 与 Koa 留 follow-up**：GraphQL resolver 是 schema/SDL 模式（非路由注册函数），需独立 schema 解析，价值低于路由类且复杂度高；Koa `router.get(...)` 已被现有 Express 正则 `(app|router)\.(get|post|...)` 覆盖（receiver 含 router）。在 plan 标注，非占位。
- G5（OpenAPI parser + 接入 fusion）→ T2（parser）+ T3（fusion 接入）✓。V5 达标（schema 源写回 index）。
- G6（fusion 门控解耦）→ T4 ✓。V6 达标（关 LLM 轨 schema 源仍跑）。
- 非目标（不改候选算法 / vuln-authz / merger / chain_verdict / IDORCandidateChain）→ Global Constraints 锁 ✓。
- **架构偏差声明（YAGNI）**：spec §3.5 提"调完整 `merge_entry_points`"，本 plan **不调**——`merge_entry_points` 的 `gitnexus_eps` 是 GitNexus cypher dict 格式、与 code_index 流不匹配，且 gitnexus/convention 源当前未产（GitNexus EP Scoring 未接），调它需额外转换层且无收益。schema 源直接产 `EntryPoint` 追加（与现有 LLM 源同构），最小侵入不破现状。`merge_entry_points` 框架保留，作 future GitNexus EP Scoring / convention 接入点。spec 精神（schema_eps 不再空、写回 index）满足（V5）。

**Placeholder 扫描**：fixture 细节（`FuncBlock`/`CodeIndex` 必填字段、yaml helper）标注"以 models.py 实际为准，按实际补全 fixture，不删断言"——是 TDD fixture 适配指引，非占位空话。workflows.py 缩进标注"核对周围实际缩进，与上下 activity 同级"——是执行指引，代码块已给出目标形态。无 TBD/TODO/"implement later"。

**类型一致**：
- `parse_openapi_schema_files(repo_path: str) -> list[EntryPoint]`（T2 定义）= T3 `run_entry_point_fusion` 调用签名一致 ✓
- `run_entry_point_fusion(deliverables_dir, repo_path=None)`（T3 定义）= activity `_fusion(str(deliverables), repo_path=str(repo))` 调用一致 ✓
- `EntryPoint` 字段（func_block_id/entry_type/route/http_method/confidence/evidence/needs_llm_review/authentication/source）跨 T1/T2/T3 一致（均用 `models.py:49` 现有字段，不新增）✓
- T4 只改 workflows 门控缩进，不碰签名 ✓

**Scope check**（writing-plans）：G4/G5/G6 是三个独立子能力，但同属"补 authz 候选来源"单一目标（让 entry_points 非空），互相衔接（G4 扩代码识别 / G5 补 schema 识别 / G6 让两者关 LLM 轨都跑），合在一个 plan 合理；4 个 task 各自独立 test cycle + 可分别 review。无需拆多 plan。
