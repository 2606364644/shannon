# recon 双轨实现计划（Plan 6）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 recon §4.1（共享路由组）和 §4.2（endpoint security context）补上 **GitNexus 确定性轨**——调用图反推"哪些路由映射同一 handler"产路由组 + 源码扫描产 auth/middleware + ORM 谓词扫描产 ownership 候选——并把这个确定性轨作为**下限**注入 recon prompt，让 recon LLM 在其 §4.1/§4.2 表中据此填充 + 独立补全其他端点（合并发生在 LLM 侧，不是双 queue 文件合并）。

**Architecture:** 新增确定性检测器 `recon_gitnexus_track`（读 `code_index.json` 的 `FuncBlock` + `EntryPoint` + `edges`）→ 产三份产物：(1) §4.1 handler→多路由组（按 `func_block_id` 分组 `EntryPoint`，同组 ≥2 路由成组）+ auth 源码扫描（`FuncBlock.source_code` 内 decorator/middleware 关键词）；(2) §4.2 endpoint security context（每端点 auth/middleware from 源码扫描 + ownership 候选 from ORM 谓词 `where { userId }` / `findByOwner` 扫描 + framework origin from `framework_analysis.json`，后者 Plan 2 已接通）→ 渲染成 `{{RECON_GITNEXUS_TRACK}}` markdown → `run_agent` 对 RECON agent 注入 `prompt_variables={"recon_gitnexus_track": md}` → `recon.txt` §4.1/§4.2 用占位符填充。LLM 轨（recon 自主产 §4.1/§4.2，现状不变）+ GitNexus 轨（确定性下限注入）= 双轨；**合并在 LLM 侧**：recon LLM 读下限 + 独立探索其他端点，字段冲突取危险侧（auth 任一无→无；framework origin 任一 auto-generated→auto-generated；ownership 任一 none→none）由 prompt 指令约束。

**Tech Stack:** Python 3.12, pydantic v2, pytest, pytest-asyncio, tree-sitter（仅读已构建的 `FuncBlock.source_code`，不重新解析）

## Global Constraints

- **recon intel 合并 ≠ vuln verdict 合并**：spec §4.3 的 recon 情报合并是**字段级危险侧**，但 recon intel 产物是**结构化 markdown**（§4.1 路由组表 / §4.2 endpoint context 表），不是 `VulnerabilityQueue` JSON。因此 **Plan 3 的 `merge_dual_track_queues`（操作 `Vulnerability` 对象）不适用于 recon §4.1/§4.2**——它产 verdict queue 文件。recon 双轨合并**在 LLM 侧**：GitNexus 轨 markdown 作下限注入，recon LLM 读后产合并后的 §4.1/§4.2 表（字段冲突由 prompt 指令取危险侧）。这是 spec §5.2/§5.3「GitNexus 轨 → LLM 确认」的自然落地（recon LLM 即那个"确认"角色，对每个路由组/端点做语义确认 + 独立探索）。
- **仅 RECON agent 注入**（`run_agent` 通用入口，recon/vuln 都走；Plan 2 已为 framework_endpoints_summary 在 RECON 注入，本 plan 复用同一注入点追加 `recon_gitnexus_track`）。非 RECON 不注入。
- **`code_index.json` 缺失 / 为空时跳过**（不崩；GitNexus 索引降级时 recon 仍能跑，spec §6）。`entry_points` 为空 → §4.1 路由组为空、§4.2 端点表为空，注入"（无确定性路由组）"占位。
- **handler→路由分组依赖 `EntryPoint.func_block_id`**：同 `func_block_id` 的多个 `EntryPoint`（不同 `route`/`http_method`）= 同一 handler 映射多路由。这是 spec §4.1「调用图 handler→多路由映射」的反推（GitNexus 产 entry_points 时已带 `func_block_id`，见 `entry_point_fusion.py:77` + `gitnexus_call_graph.py:207-229`）。**不**依赖 `edges`（edges 是函数间调用，非路由映射）；路由映射信息在 `EntryPoint.route` + `func_block_id`。
- **auth/middleware 用源码扫描（regex on `FuncBlock.source_code`），不依赖 `decorators` 字段**：调研发现 TS/Go/PHP parser **不填充 `decorators`**（仅 java/python parser 填，见 `typescript_parser.py` 无 `_extract_decorators`）。因此 auth/middleware 检测统一用 `FuncBlock.source_code` 内的 decorator/middleware 关键词扫描（跨语言一致，复用 `frontend_mapper._has_auth_guard` 的关键词思路）。`FuncBlock.decorators`（java/python 有值）作为**补充信号**。
- **ownership 候选用 ORM 谓词 regex**：调研发现 `sink_detector` 无现成 ownership 检测（它检测 sink call site，非 ownership guard）。本 plan 新增确定性 ORM 谓词扫描器：在 handler 的 `FuncBlock.source_code` 内扫描 `where { userId`/`whereUserId`/`findByOwner`/`req.user.id`/`currentUser.id` 等模式 → 有命中 = ownership guard 存在（候选）；无命中 = ownership **none 候选**（危险侧）。这是**候选**非定论（LLM 仍须语义确认，regex 可能漏 framework-specific 写法）。
- **framework origin 复用 Plan 2 的 `framework_analysis.json`**（已接通到 §4.2 via `{{FRAMEWORK_ENDPOINTS_SUMMARY}}`）。本 plan 的 GitNexus 轨 §4.2 表**不重复** framework origin 列的 auto-generated 标注（那是 Plan 2 的职责），而是产 auth/middleware/ownership 三列（Plan 2 产 framework origin 一列）。两份 markdown 在 §4.2 各占一区，LLM 合并到一张表。
- **不锚定（下限非上限）**：GitNexus 轨 markdown 是**下限**——recon LLM 仍须独立探索其他路由组/端点。prompt 明确声明（复用 Plan 2 的「下限非上限」措辞）。
- **不删/不改 Plan 2 的 framework 注入**：两者并存（framework origin 区 + auth/middleware/ownership 区）。
- TDD + frequent commits（`feat(code_index):` / `feat(whitebox):` / `feat(prompt):`）；真实 GitNexus 索引产出非空路由组需 MCP 环境，单元测试用合成 `code_index.json`。

---

### Task 1: 写 §4.1 路由组检测器 `detect_shared_route_groups`

**Files:**
- Create: `packages/core/src/shannon_core/code_index/recon_gitnexus_track.py`
- Test: `packages/core/tests/code_index/test_recon_route_groups.py`（Create）

**Interfaces:**
- Consumes: `list[EntryPoint]`（从 `CodeIndex.entry_points` 读，`EntryPoint.func_block_id`/`route`/`http_method`/`authentication`，`models.py:49-60`）
- Produces: `detect_shared_route_groups(entry_points: list[EntryPoint]) -> list[SharedRouteGroup]`；`SharedRouteGroup`（新 dataclass：`handler_id: str`、`routes: list[RouteRow]`、`auth_conflict: bool`）；`RouteRow`（`method: str | None`、`path: str`、`auth: str`，auth ∈ `{"present"|"none"|"unknown"}`）。仅返回组内 ≥2 路由的组。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_recon_route_groups.py
from shannon_core.code_index.models import EntryPoint
from shannon_core.code_index.recon_gitnexus_track import (
    SharedRouteGroup,
    detect_shared_route_groups,
)


def _ep(func_block_id, route, method="GET", auth=None):
    return EntryPoint(
        func_block_id=func_block_id,
        entry_type="http_route",
        route=route,
        http_method=method,
        confidence=0.9,
        evidence="",
        needs_llm_review=False,
        authentication=auth,
    )


def test_no_groups_when_no_shared_handlers():
    eps = [
        _ep("app.py:handlerA:10", "/a"),
        _ep("app.py:handlerB:20", "/b"),
    ]
    assert detect_shared_route_groups(eps) == []


def test_groups_routes_by_handler():
    eps = [
        _ep("controller.js:index:32", "/preview", auth="required"),
        _ep("controller.js:index:32", "/preview/v2", auth="required"),
        _ep("controller.js:index:32", "/preview/iframe-demo", auth=None),
    ]
    groups = detect_shared_route_groups(eps)
    assert len(groups) == 1
    g = groups[0]
    assert g.handler_id == "controller.js:index:32"
    assert len(g.routes) == 3
    paths = {r.path for r in g.routes}
    assert paths == {"/preview", "/preview/v2", "/preview/iframe-demo"}
    # one route has no auth → conflict (dangerous side)
    assert g.auth_conflict is True


def test_auth_conflict_false_when_all_routes_have_auth():
    eps = [
        _ep("u.js:getProfile:45", "/api/users/me", auth="required"),
        _ep("u.js:getProfile:45", "/api/admin/users/profile", auth="required"),
    ]
    groups = detect_shared_route_groups(eps)
    assert len(groups) == 1
    assert groups[0].auth_conflict is False


def test_skips_routes_without_route():
    """EntryPoint with route=None (e.g. rpc/entry_type != http_route) excluded."""
    eps = [
        EntryPoint(func_block_id="x.py:f:1", entry_type="rpc", route=None,
                   confidence=0.9, evidence="", needs_llm_review=False),
        EntryPoint(func_block_id="x.py:f:1", entry_type="rpc", route=None,
                   confidence=0.9, evidence="", needs_llm_review=False),
    ]
    assert detect_shared_route_groups(eps) == []


def test_dedups_identical_route_within_handler():
    """Same handler + same route+method twice → one row (dedup)."""
    eps = [
        _ep("c.js:h:1", "/x", method="GET"),
        _ep("c.js:h:1", "/x", method="GET"),
        _ep("c.js:h:1", "/y", method="GET"),
    ]
    groups = detect_shared_route_groups(eps)
    assert len(groups) == 1
    assert len(groups[0].routes) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_recon_route_groups.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.code_index.recon_gitnexus_track`

- [ ] **Step 3: Implement the detector + dataclasses**

```python
# packages/core/src/shannon_core/code_index/recon_gitnexus_track.py
"""recon §4.1/§4.2 GitNexus deterministic track (spec §5.2/§5.3).

Produces deterministic intelligence for recon's shared-route-groups (§4.1)
and endpoint security context (§4.2) from the code index — surfaced to the
recon LLM as a LOWER BOUND. The recon LLM still independently explores
other routes/endpoints (dual-track: lower bound, not ceiling).

The merge happens on the LLM side: recon reads this track and produces its
§4.1/§4.2 tables applying field-level "dangerous side" conflict rules
(auth any-missing→missing; framework origin any-auto→auto; ownership
any-none→none) per spec §4.3. This is NOT a VulnerabilityQueue file merge
(Plan 3's merge_dual_track_queues operates on Vulnerability objects for the
vuln verdict phase — a different product type).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from shannon_core.code_index.models import EntryPoint, FuncBlock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouteRow:
    """One route in a shared-route group."""
    method: str | None
    path: str
    auth: str  # "present" | "none" | "unknown"


@dataclass(frozen=True)
class SharedRouteGroup:
    """§4.1 group: ≥2 routes mapped to the same handler (spec §4.1)."""
    handler_id: str
    routes: tuple[RouteRow, ...]
    auth_conflict: bool  # True if any route lacks auth (dangerous side)


def _auth_token(auth: str | None) -> str:
    """Normalize EntryPoint.authentication → present/none/unknown."""
    if auth is None:
        return "unknown"
    a = auth.strip().lower()
    if a in ("public", "none", "no", "missing", "absent"):
        return "none"
    if a in ("required", "user", "admin"):
        return "present"
    return "unknown"


def detect_shared_route_groups(entry_points: list[EntryPoint]) -> list[SharedRouteGroup]:
    """Group EntryPoints by handler (func_block_id); keep groups with ≥2 routes.

    Only EntryPoints with a non-None route and entry_type indicating an HTTP
    route are grouped (rpc/cli/webhook-only without a path excluded). Routes
    are deduped by (method, path) within a handler.
    """
    by_handler: dict[str, list[EntryPoint]] = {}
    for ep in entry_points:
        if ep.route is None:
            continue
        # Only HTTP-like routes form §4.1 groups (webhook with a path is fine).
        by_handler.setdefault(ep.func_block_id, []).append(ep)

    groups: list[SharedRouteGroup] = []
    for handler_id, eps in by_handler.items():
        seen: set[tuple[str | None, str]] = set()
        rows: list[RouteRow] = []
        for ep in eps:
            key = (ep.http_method, ep.route)
            if key in seen:
                continue
            seen.add(key)
            rows.append(RouteRow(method=ep.http_method, path=ep.route,
                                 auth=_auth_token(ep.authentication)))
        if len(rows) < 2:
            continue
        auth_conflict = any(r.auth == "none" for r in rows)
        groups.append(SharedRouteGroup(
            handler_id=handler_id,
            routes=tuple(rows),
            auth_conflict=auth_conflict,
        ))

    logger.info(
        "recon §4.1 track: %d handlers → %d shared-route groups (≥2 routes)",
        len(by_handler), len(groups),
    )
    return groups
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_recon_route_groups.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/recon_gitnexus_track.py packages/core/tests/code_index/test_recon_route_groups.py
git commit -m "feat(code_index): detect shared route groups from entry points (recon §4.1 track)"
```

---

### Task 2: 写 auth/middleware + ownership 源码扫描器 `scan_endpoint_security`

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/recon_gitnexus_track.py`（追加扫描函数 + dataclass）
- Test: `packages/core/tests/code_index/test_recon_endpoint_security.py`（Create）

**Interfaces:**
- Consumes: `list[EntryPoint]` + `dict[str, FuncBlock]`（按 `func_block_id` 索引，从 `CodeIndex.blocks` 建）；handler 的 `FuncBlock.source_code` + `FuncBlock.decorators`
- Produces:
  - `scan_endpoint_security(entry_points, blocks_by_id) -> list[EndpointSecurityContext]`
  - `EndpointSecurityContext`（新 dataclass：`method`、`path`、`handler_id`、`auth: str`、`middleware: tuple[str, ...]`、`ownership: str`、`ownership_evidence: str | None`）
  - `auth` ∈ `{"present"|"none"|"unknown"}`（源码扫描 decorator/middleware 关键词 + EntryPoint.authentication）
  - `ownership` ∈ `{"guarded"|"none"|"unknown"}`（ORM 谓词 regex）

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_recon_endpoint_security.py
from shannon_core.code_index.models import EntryPoint, FuncBlock
from shannon_core.code_index.recon_gitnexus_track import (
    EndpointSecurityContext,
    scan_endpoint_security,
)


def _block(handler_id, source, decorators=None):
    file_path, func_name, line = handler_id.rsplit(":", 2)
    return FuncBlock(
        id=handler_id, file_path=file_path, function_name=func_name,
        start_line=int(line), end_line=int(line) + 5, source_code=source,
        parameters=[], decorators=decorators or [], language="typescript",
    )


def _ep(handler_id, path, method="GET", auth=None):
    return EntryPoint(
        func_block_id=handler_id, entry_type="http_route", route=path,
        http_method=method, confidence=0.9, evidence="",
        needs_llm_review=False, authentication=auth,
    )


def test_auth_present_when_decorator_matches():
    ep = _ep("u.js:getProfile:45", "/api/users/me")
    block = _block("u.js:getProfile:45",
                   "@UseGuards(AuthGuard)\nexport function getProfile() { ... }")
    out = scan_endpoint_security([ep], {"u.js:getProfile:45": block})
    assert len(out) == 1
    assert out[0].auth == "present"


def test_auth_none_when_no_guard_keyword():
    ep = _ep("u.js:public:1", "/api/open")
    block = _block("u.js:public:1", "export function public() { return data; }")
    out = scan_endpoint_security([ep], {"u.js:public:1": block})
    assert len(out) == 1
    assert out[0].auth == "none"


def test_middleware_extracted_from_source():
    ep = _ep("a.js:admin:5", "/api/admin")
    block = _block("a.js:admin:5",
                   "router.use(requireAdmin);\nexport function admin() {}")
    out = scan_endpoint_security([ep], {"a.js:admin:5": block})
    assert "requireAdmin" in out[0].middleware


def test_ownership_guarded_when_orm_predicate_present():
    ep = _ep("u.js:update:10", "/api/users/:id", method="PUT")
    block = _block("u.js:update:10",
                   "async function update(req) {\n"
                   "  const row = await db.user.findFirst({ where: { userId: req.user.id } });\n"
                   "}")
    out = scan_endpoint_security([ep], {"u.js:update:10": block})
    assert out[0].ownership == "guarded"
    assert out[0].ownership_evidence is not None
    assert "userId" in out[0].ownership_evidence


def test_ownership_none_when_no_predicate():
    ep = _ep("u.js:list:1", "/api/users")
    block = _block("u.js:list:1", "async function list(req) { return db.user.findMany(); }")
    out = scan_endpoint_security([ep], {"u.js:list:1": block})
    assert out[0].ownership == "none"


def test_missing_handler_block_yields_unknown_auth():
    """EntryPoint whose handler isn't in blocks (unresolved) → auth unknown."""
    ep = _ep("x.js:ghost:1", "/api/ghost")
    out = scan_endpoint_security([ep], {})  # no blocks
    assert len(out) == 1
    assert out[0].auth == "unknown"
    assert out[0].ownership == "unknown"


def test_decorators_field_used_as_supplementary_signal():
    """Java/Python parsers populate decorators; use them as auth signal too."""
    ep = _ep("Ctrl.java:show:20", "/api/show")
    block = _block("Ctrl.java:show:20", "public void show() {}",
                   decorators=["@PreAuthorize(\"hasRole('USER')\")"])
    out = scan_endpoint_security([ep], {"Ctrl.java:show:20": block})
    assert out[0].auth == "present"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_recon_endpoint_security.py -v`
Expected: FAIL — `ImportError: cannot import name 'EndpointSecurityContext'`（或 `scan_endpoint_security`）

- [ ] **Step 3: Implement the scanner**

Append to `packages/core/src/shannon_core/code_index/recon_gitnexus_track.py`:

```python
# ===== §4.2 endpoint security context =====

# Auth/middleware keyword patterns. Cross-language (TS/Go/Java/PHP/Python).
# Decorator-style (Java @PreAuthorize, Python @login_required, TS @UseGuards),
# middleware-style (Express requireAuth, Koa ctx.state.user check), and
# framework guards (Nest Guard, Laravel middleware).
_AUTH_GUARD_RE = re.compile(
    r"(?i)\b("
    r"@?RequireAuth|@?requireAuth|@?IsAuthenticated|@?isAuthenticated|"
    r"@?UseGuards|@?Guard|@?PreAuthorize|@?Secured|@?RolesAllowed|"
    r"@?login_required|@?loginRequired|@?auth_required|@?AuthRequired|"
    r"AuthGuard|canActivate|@Authorize|@AuthorizeRequest"
    r")\b"
)
# A route line that explicitly attaches NO auth (e.g. `.use([])` empty, or
# documented public). Rare; mainly we infer "none" by absence of a guard.
_EXPLICIT_PUBLIC_RE = re.compile(r"(?i)\b(public|@?Anonymous|allowAnonymous|noAuth)\b")
# Middleware names: identifiers near "use("/"guard"/"middleware" that look like
# middleware (capitalized or camelCase ending in Auth/Role/Admin/Guard).
_MIDDLEWARE_NAME_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(|;|\s*$|\s*[,)])"
)
_MIDDLEWARE_HINT_RE = re.compile(
    r"(?i)(\.use\s*\(|\.guard\s*\(|middleware\s*[:=]|Guard\b|requireAdmin|requireRole)"
)
# ORM ownership predicates. Match the predicate that ties a query to the
# authenticated user. Covers: Prisma `where: { userId: ... }`, Sequelize
# `where: { userId }`, TypeORM `where: { userId }`, GORM `Where("user_id = ?")`,
# Knex `.where('user_id', ...)`, raw SQL `WHERE user_id`, Mongoose
# `{ owner: req.user.id }`, and Java/PHP equivalents.
_OWNERSHIP_PREDICATE_RE = re.compile(
    r"(?i)("
    r"where\s*[:({]?\s*['\"]?\s*(user_?id|owner_?id|owner|creator_?id|author_?id)\b"
    r"|where\s*\(\s*['\"]?(user_?id|owner_?id|owner|creator_?id|author_?id)['\"]?\s*[,=]"
    r"|\bfind(First|One|All)?\s*\(\s*\{[^}]*?(user_?id|owner|creator)"
    r"|\b(owner|currentUser|req\.user|ctx\.state\.user)\s*\.\s*id\b"
    r"|\b(user_?id|owner_?id)\s*=\s*(req|ctx|currentUser)"
    r")"
)


@dataclass(frozen=True)
class EndpointSecurityContext:
    """§4.2 endpoint security context (deterministic track)."""
    method: str | None
    path: str
    handler_id: str
    auth: str                       # "present" | "none" | "unknown"
    middleware: tuple[str, ...]     # detected middleware/guard names
    ownership: str                  # "guarded" | "none" | "unknown"
    ownership_evidence: str | None  # matched snippet (for LLM confirmation)


def _detect_auth(source: str, decorators: list[str], ep_auth: str | None) -> str:
    """Determine auth presence from source + decorators + EntryPoint.authentication."""
    blob = source + "\n" + " ".join(decorators)
    if _AUTH_GUARD_RE.search(blob):
        return "present"
    if _EXPLICIT_PUBLIC_RE.search(blob):
        return "none"
    # Fall back to the entry-point's LLM/code-index auth hint (auth != None).
    if ep_auth is not None:
        token = ep_auth.strip().lower()
        if token in ("required", "user", "admin"):
            return "present"
        if token in ("public", "none"):
            return "none"
    # No signal → "none" (absence of guard = dangerous-side default). Caller
    # may override to "unknown" when the handler block is missing entirely.
    return "none"


def _extract_middleware(source: str, decorators: list[str]) -> tuple[str, ...]:
    """Extract middleware/guard names from source near middleware hints."""
    blob = source + "\n" + " ".join(decorators)
    found: list[str] = []
    # Decorators themselves (Java/Python) are guards.
    for dec in decorators:
        m = re.match(r"@?([A-Za-z_][A-Za-z0-9_]*)", dec)
        if m and m.group(1) not in found:
            found.append(m.group(1))
    # Identifiers near .use(/guard/middleware hints.
    if _MIDDLEWARE_HINT_RE.search(blob):
        for m in _MIDDLEWARE_NAME_RE.finditer(blob):
            name = m.group(1)
            # Heuristic: names containing Auth/Role/Admin/Guard/Middleware look
            # like middleware; skip language keywords / common words.
            if re.search(r"(?i)(auth|role|admin|guard|middleware)", name):
                if name not in found:
                    found.append(name)
    return tuple(found)


def _detect_ownership(source: str) -> tuple[str, str | None]:
    """Return (ownership, evidence) from ORM predicate regex."""
    m = _OWNERSHIP_PREDICATE_RE.search(source)
    if m:
        # Snippet a small window around the match for the LLM to confirm.
        start = max(0, m.start() - 20)
        end = min(len(source), m.end() + 20)
        return ("guarded", source[start:end].replace("\n", " ").strip())
    return ("none", None)


def scan_endpoint_security(
    entry_points: list[EntryPoint],
    blocks_by_id: dict[str, FuncBlock],
) -> list[EndpointSecurityContext]:
    """§4.2 deterministic track: per-endpoint auth/middleware/ownership.

    Auth: source-code scan for guard/middleware keywords + FuncBlock.decorators
    (java/python) + EntryPoint.authentication fallback. Absent guard → "none"
    (dangerous side). Missing handler block → "unknown".

    Middleware: identifiers near .use(/guard/middleware hints + decorator names.

    Ownership: ORM-predicate regex in the handler body. Hit → "guarded"
    (candidate); no hit → "none" (candidate, dangerous side). The recon LLM
    must confirm (regex may miss framework-specific ownership idioms).
    """
    out: list[EndpointSecurityContext] = []
    for ep in entry_points:
        if ep.route is None:
            continue
        block = blocks_by_id.get(ep.func_block_id)
        if block is None:
            out.append(EndpointSecurityContext(
                method=ep.http_method, path=ep.route, handler_id=ep.func_block_id,
                auth="unknown", middleware=(), ownership="unknown",
                ownership_evidence=None,
            ))
            continue
        auth = _detect_auth(block.source_code, list(block.decorators), ep.authentication)
        middleware = _extract_middleware(block.source_code, list(block.decorators))
        ownership, evidence = _detect_ownership(block.source_code)
        out.append(EndpointSecurityContext(
            method=ep.http_method, path=ep.route, handler_id=ep.func_block_id,
            auth=auth, middleware=middleware, ownership=ownership,
            ownership_evidence=evidence,
        ))
    logger.info(
        "recon §4.2 track: %d endpoints (auth present=%d none=%d unknown=%d; "
        "ownership guarded=%d none=%d unknown=%d)",
        len(out),
        sum(1 for e in out if e.auth == "present"),
        sum(1 for e in out if e.auth == "none"),
        sum(1 for e in out if e.auth == "unknown"),
        sum(1 for e in out if e.ownership == "guarded"),
        sum(1 for e in out if e.ownership == "none"),
        sum(1 for e in out if e.ownership == "unknown"),
    )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_recon_endpoint_security.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/recon_gitnexus_track.py packages/core/tests/code_index/test_recon_endpoint_security.py
git commit -m "feat(code_index): scan endpoint auth/middleware/ownership from source (recon §4.2 track)"
```

---

### Task 3: 写渲染器 `render_recon_gitnexus_track`（§4.1 + §4.2 markdown）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/recon_gitnexus_track.py`（追加渲染函数）
- Test: `packages/core/tests/code_index/test_recon_render_track.py`（Create）

**Interfaces:**
- Consumes: `list[SharedRouteGroup]`（Task 1）+ `list[EndpointSecurityContext]`（Task 2）
- Produces: `render_recon_gitnexus_track(groups, contexts) -> str`（markdown，含 §4.1 路由组表 + §4.2 端点表 + 「下限非上限」声明 + 字段危险侧合并指令）

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_recon_render_track.py
from shannon_core.code_index.recon_gitnexus_track import (
    EndpointSecurityContext,
    RouteRow,
    SharedRouteGroup,
    render_recon_gitnexus_track,
)


def _group(handler_id, rows, conflict=False):
    return SharedRouteGroup(handler_id=handler_id, routes=tuple(rows),
                            auth_conflict=conflict)


def test_render_empty_yields_no_data_notice():
    out = render_recon_gitnexus_track([], [])
    assert "无" in out or "no" in out.lower()


def test_render_lists_route_group_table_with_preauth_warning():
    g = _group(
        "controller.js:index:32",
        [RouteRow("GET", "/preview", "present"),
         RouteRow("GET", "/preview/iframe-demo", "none")],
        conflict=True,
    )
    out = render_recon_gitnexus_track([g], [])
    assert "controller.js:index:32" in out
    assert "/preview/iframe-demo" in out
    # pre-auth warning surfaced when conflict
    assert "pre-auth" in out.lower() or "none" in out.lower()


def test_render_lists_endpoint_security_table():
    ctx = EndpointSecurityContext(
        method="PUT", path="/api/users/:id", handler_id="u.js:update:10",
        auth="present", middleware=("requireAuth",), ownership="none",
        ownership_evidence=None,
    )
    out = render_recon_gitnexus_track([], [ctx])
    assert "PUT /api/users/:id" in out
    assert "requireAuth" in out
    assert "none" in out.lower()  # ownership none shown


def test_render_includes_lower_bound_disclaimer_and_merge_rules():
    out = render_recon_gitnexus_track([], [
        EndpointSecurityContext("GET", "/x", "h.js:f:1", "present", (), "none", None)
    ])
    # 下限非上限
    assert "下限" in out or "独立" in out
    # 字段危险侧合并指令（auth/framework/ownership）
    assert "危险侧" in out or "none" in out.lower()


def test_render_shows_ownership_evidence_when_present():
    ctx = EndpointSecurityContext(
        method="PUT", path="/api/u/:id", handler_id="u.js:up:1",
        auth="present", middleware=(), ownership="guarded",
        ownership_evidence="where: { userId: req.user.id }",
    )
    out = render_recon_gitnexus_track([], [ctx])
    assert "userId" in out
    assert "guarded" in out.lower() or "guarded" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_recon_render_track.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_recon_gitnexus_track'`

- [ ] **Step 3: Implement the renderer**

Append to `packages/core/src/shannon_core/code_index/recon_gitnexus_track.py`:

```python
def _auth_cell(auth: str) -> str:
    return {"present": "present", "none": "**none**", "unknown": "unknown"}.get(auth, auth)


def render_recon_gitnexus_track(
    groups: list[SharedRouteGroup],
    contexts: list[EndpointSecurityContext],
) -> str:
    """Render the recon GitNexus track as markdown for {{RECON_GITNEXUS_TRACK}}.

    Contains §4.1 (shared route groups) + §4.2 (endpoint security context) as
    deterministic LOWER BOUND tables, plus conflict/merge directives for the
    recon LLM (field-level dangerous side, spec §4.3).
    """
    if not groups and not contexts:
        return "（无确定性检测到的共享路由组或端点安全上下文。GitNexus 索引可能未就绪或为空。）"

    lines: list[str] = ["## Recon GitNexus Track（确定性下限：§4.1 路由组 + §4.2 端点安全上下文）", ""]

    # ----- §4.1 shared route groups -----
    if groups:
        lines.append("### §4.1 Shared Route Groups（调用图反推：同 handler 多路由）")
        lines.append("")
        lines.append("| Handler | Method | Path | Auth |")
        lines.append("|---|---|---|---|")
        for g in groups:
            for r in g.routes:
                lines.append(
                    f"| `{g.handler_id}` | {r.method or '—'} | `{r.path}` | {_auth_cell(r.auth)} |"
                )
            if g.auth_conflict:
                lines.append("")
                lines.append(
                    f"> ⚠️ `{g.handler_id}`: 至少一条路由 **none** auth — pre-auth variant 候选（危险侧）。"
                )
        lines.append("")

    # ----- §4.2 endpoint security context -----
    if contexts:
        lines.append("### §4.2 Endpoint Security Context（确定性：auth/middleware/ownership）")
        lines.append("")
        lines.append("| Method | Path | Auth | Middleware | Ownership | Evidence |")
        lines.append("|---|---|---|---|---|---|")
        for c in contexts:
            mw = ", ".join(c.middleware) if c.middleware else "—"
            own = c.ownership
            if own == "none":
                own = "**none**"
            ev = c.ownership_evidence or "—"
            lines.append(
                f"| {c.method or '—'} | `{c.path}` | {_auth_cell(c.auth)} | {mw} | {own} | `{ev}` |"
            )
        lines.append("")

    # ----- directives -----
    lines.extend([
        "⚠️ **填充与合并规则（字段危险侧，spec §4.3）：**",
        "- 以上为**确定性检测**下限。`recon_deliverable.md` 的 §4.1/§4.2 表据此填充 + **独立探索其他路由组/端点**（下限非上限：确定性未列出 ≠ 无）。",
        "- **Auth 冲突取「无」**：任一轨（确定性 vs 你独立判断）标 none → 合并取 none。",
        "- **Framework Origin 冲突取 auto-generated**：见上方 `{{FRAMEWORK_ENDPOINTS_SUMMARY}}`（Plan 2），确定性标 auto-generated 即取 auto-generated。",
        "- **Ownership 冲突取 none**：任一轨标 none → 合并取 none。Ownership = `guarded` 仅为**候选**（regex 可能漏 framework-specific 写法），须你语义确认；`none` 候选表示 handler body 未检出 ownership 谓词。",
        "- **Missing handler（auth/ownership = unknown）**：handler 未在 code_index 解析到源码——优先独立核实，勿直接当 present。",
    ])
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_recon_render_track.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/recon_gitnexus_track.py packages/core/tests/code_index/test_recon_render_track.py
git commit -m "feat(code_index): render recon GitNexus track markdown (§4.1 + §4.2)"
```

---

### Task 4: 写编排函数 `build_recon_gitnexus_track`（读 code_index.json → 三产物 → markdown）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/recon_gitnexus_track.py`（追加编排函数）
- Test: `packages/core/tests/code_index/test_recon_build_track.py`（Create）

**Interfaces:**
- Consumes: `code_index.json`（`CodeIndex.model_validate_json`）→ `entry_points` + `blocks`（按 `func_block_id` 索引）
- Produces: `build_recon_gitnexus_track(deliverables_dir: str) -> str`（读 `code_index.json`，调 Task 1-3，返回 markdown；文件缺失/空 → 返回空轨占位 markdown，不 raise）

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_recon_build_track.py
import json

from shannon_core.code_index.recon_gitnexus_track import build_recon_gitnexus_track


def _index_json(entry_points, blocks):
    return json.dumps({
        "repository": "r", "language": "typescript",
        "total_blocks": len(blocks), "total_entry_points": len(entry_points),
        "total_chains": 0, "blocks": blocks, "edges": [],
        "entry_points": entry_points, "chains": [],
    })


def _block(handler_id, source):
    file_path, func_name, line = handler_id.rsplit(":", 2)
    return {
        "id": handler_id, "file_path": file_path, "function_name": func_name,
        "start_line": int(line), "end_line": int(line) + 3,
        "source_code": source, "parameters": [], "decorators": [],
        "language": "typescript",
    }


def _ep(handler_id, route, method="GET", auth=None):
    return {
        "func_block_id": handler_id, "entry_type": "http_route", "route": route,
        "http_method": method, "confidence": 0.9, "evidence": "",
        "needs_llm_review": False, "authentication": auth, "source": "code_index",
    }


def test_build_from_code_index(tmp_path):
    handler = "c.js:index:32"
    eps = [
        _ep(handler, "/preview", auth="required"),
        _ep(handler, "/preview/v2", auth="required"),
        _ep(handler, "/preview/iframe-demo", auth=None),
        _ep("u.js:update:10", "/api/users/:id", method="PUT"),
    ]
    blocks = [
        _block(handler, "router.use(requireAuth);\nexport function index() {}"),
        _block("u.js:update:10",
               "async function update(req){ db.user.findFirst({where:{userId:req.user.id}}) }"),
    ]
    (tmp_path / "code_index.json").write_text(_index_json(eps, blocks))

    md = build_recon_gitnexus_track(str(tmp_path))
    # §4.1 group present
    assert "c.js:index:32" in md
    assert "/preview/iframe-demo" in md
    # §4.2 endpoint present with ownership evidence
    assert "PUT /api/users/:id" in md
    assert "userId" in md
    # disclaimer
    assert "下限" in md


def test_build_missing_code_index_returns_empty_notice(tmp_path):
    md = build_recon_gitnexus_track(str(tmp_path))  # no code_index.json
    assert "无" in md or "no" in md.lower()
    # does not raise
    assert isinstance(md, str)


def test_build_empty_entry_points_returns_notice(tmp_path):
    (tmp_path / "code_index.json").write_text(_index_json([], []))
    md = build_recon_gitnexus_track(str(tmp_path))
    assert "无" in md or "no" in md.lower()


def test_build_invalid_json_returns_empty_notice(tmp_path):
    (tmp_path / "code_index.json").write_text("not json")
    md = build_recon_gitnexus_track(str(tmp_path))
    # lenient: no crash, returns notice
    assert isinstance(md, str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_recon_build_track.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_recon_gitnexus_track'`

- [ ] **Step 3: Implement the orchestrator**

Append to `packages/core/src/shannon_core/code_index/recon_gitnexus_track.py`:

```python
from pathlib import Path

from shannon_core.code_index.models import CodeIndex


def build_recon_gitnexus_track(deliverables_dir: str) -> str:
    """Read code_index.json, build §4.1 groups + §4.2 contexts, render markdown.

    Lenient: missing/invalid code_index.json or empty entry_points → returns an
    empty-track notice string (never raises). This is the graceful-degradation
    contract: when the GitNexus index is absent (spec §6 degradation), the
    recon GitNexus track is empty and only the LLM track runs.
    """
    out = Path(deliverables_dir)
    ci_path = out / "code_index.json"
    if not ci_path.exists():
        logger.info("recon GitNexus track: code_index.json missing → empty track")
        return render_recon_gitnexus_track([], [])

    try:
        index = CodeIndex.model_validate_json(ci_path.read_text())
    except Exception as exc:  # invalid JSON / schema drift
        logger.warning("recon GitNexus track: code_index.json parse failed (%s) → empty", exc)
        return render_recon_gitnexus_track([], [])

    entry_points = list(index.entry_points)
    if not entry_points:
        logger.info("recon GitNexus track: no entry_points → empty track")
        return render_recon_gitnexus_track([], [])

    blocks_by_id: dict[str, FuncBlock] = {b.id: b for b in index.blocks}

    groups = detect_shared_route_groups(entry_points)
    contexts = scan_endpoint_security(entry_points, blocks_by_id)
    return render_recon_gitnexus_track(groups, contexts)
```

> 注：`CodeIndex` import 放函数内会有循环 import 风险（`models.py` 与本文件互引）——实测 `recon_gitnexus_track.py` 只 import `EntryPoint`/`FuncBlock` from models，`CodeIndex` 仅用于 `model_validate_json`，放模块顶部安全（models.py 不反向 import 本文件）。上方块顶 import 即可。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_recon_build_track.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full recon_gitnexus_track test suite**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_recon_route_groups.py packages/core/tests/code_index/test_recon_endpoint_security.py packages/core/tests/code_index/test_recon_render_track.py packages/core/tests/code_index/test_recon_build_track.py -v`
Expected: PASS (5+7+5+4 = 21 tests)

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/recon_gitnexus_track.py packages/core/tests/code_index/test_recon_build_track.py
git commit -m "feat(code_index): build recon GitNexus track from code_index.json (orchestrator)"
```

---

### Task 5: `run_agent` 对 RECON 注入 `recon_gitnexus_track`

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（`run_agent`，约 L74-102，在 `executor.execute` 调用前注入；**与 Plan 2 的 framework_endpoints_summary 注入合并到同一 `prompt_variables` dict**）
- Test: `packages/whitebox/tests/test_run_agent_recon_track_injection.py`（Create）

**Interfaces:**
- Consumes: `build_recon_gitnexus_track`（Task 4）、`executor.execute` 的 `prompt_variables` 通道（`executor.py:56-57,85-86`，已支持）
- Produces: RECON agent 跑时收到填充好的 `{{RECON_GITNEXUS_TRACK}}`；非 RECON 不受影响；`code_index.json` 缺失时 `build_recon_gitnexus_track` 返回空轨占位（注入仍发生，占位为「无」notice）

> **注意 Plan 2 协同**：Plan 2（framework-analyzer-wiring）的 Task 2 也改 `run_agent` 注入 `framework_endpoints_summary`。两个 plan 都改同一函数同一注入点。若 Plan 2 先落地，本 task 在其 `prompt_variables` dict 上**追加** `recon_gitnexus_track` 键（不覆盖）；若并行，合并成一个 dict。下方 Step 3 的实现假设两者合并到同一 dict，用 `prompt_variables = {}` 初始化后逐键加。

- [ ] **Step 1: Write the failing test**

```python
# packages/whitebox/tests/test_run_agent_recon_track_injection.py
import json
from unittest.mock import patch

import pytest

from shannon_whitebox.pipeline import activities


class _FakeInput:
    def __init__(self, agent_name, tmp_path):
        self.agent_name = agent_name
        self.web_url = None
        self.repo_path = str(tmp_path)
        self.deliverables_subdir = None
        self.workspace_name = None
        self.config_path = None
        self.api_key = None
        self.pipeline_testing_mode = False
        self.prompt_override = None


def _index_with_group():
    handler = "c.js:index:32"
    return json.dumps({
        "repository": "r", "language": "typescript", "total_blocks": 0,
        "total_entry_points": 3, "total_chains": 0, "blocks": [],
        "edges": [],
        "entry_points": [
            {"func_block_id": handler, "entry_type": "http_route", "route": "/preview",
             "http_method": "GET", "confidence": 0.9, "evidence": "",
             "needs_llm_review": False, "authentication": "required", "source": "code_index"},
            {"func_block_id": handler, "entry_type": "http_route", "route": "/preview/v2",
             "http_method": "GET", "confidence": 0.9, "evidence": "",
             "needs_llm_review": False, "authentication": "required", "source": "code_index"},
            {"func_block_id": handler, "entry_type": "http_route", "route": "/preview/iframe-demo",
             "http_method": "GET", "confidence": 0.9, "evidence": "",
             "needs_llm_review": False, "authentication": None, "source": "code_index"},
        ],
        "chains": [],
    })


@pytest.mark.asyncio
async def test_recon_agent_gets_gitnexus_track(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "code_index.json").write_text(_index_with_group())

    captured = {}

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return type("M", (), {"to_dict": lambda self: {}, "duration_ms": 0,
                              "cost_usd": 0.0, "model": "m"})()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)):
        with patch.object(activities, "executor") as mock_exec:
            mock_exec.execute = fake_execute
            # stub the audit-session side effects
            with patch("shannon_whitebox.audit.session_registry.get_audit_session") as gs:
                inst = gs.return_value
                inst.start_agent = inst.end_agent = inst.log_error = _noop_async
                inst.track_step = _noop_cm
            await activities.run_agent(_FakeInput("recon", tmp_path))

    pv = captured.get("prompt_variables") or {}
    track = pv.get("recon_gitnexus_track", "")
    assert "c.js:index:32" in track
    assert "/preview/iframe-demo" in track


@pytest.mark.asyncio
async def test_non_recon_agent_not_injected(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "code_index.json").write_text(_index_with_group())

    captured = {}

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return type("M", (), {"to_dict": lambda self: {}, "duration_ms": 0,
                              "cost_usd": 0.0, "model": "m"})()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)):
        with patch.object(activities, "executor") as mock_exec:
            mock_exec.execute = fake_execute
            with patch("shannon_whitebox.audit.session_registry.get_audit_session") as gs:
                inst = gs.return_value
                inst.start_agent = inst.end_agent = inst.log_error = _noop_async
                inst.track_step = _noop_cm
            await activities.run_agent(_FakeInput("injection", tmp_path))

    pv = captured.get("prompt_variables")
    assert pv is None or "recon_gitnexus_track" not in (pv or {})


@pytest.mark.asyncio
async def test_recon_agent_without_code_index_still_runs(tmp_path):
    """code_index.json missing → empty-track notice injected, no crash."""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    captured = {}

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return type("M", (), {"to_dict": lambda self: {}, "duration_ms": 0,
                              "cost_usd": 0.0, "model": "m"})()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)):
        with patch.object(activities, "executor") as mock_exec:
            mock_exec.execute = fake_execute
            with patch("shannon_whitebox.audit.session_registry.get_audit_session") as gs:
                inst = gs.return_value
                inst.start_agent = inst.end_agent = inst.log_error = _noop_async
                inst.track_step = _noop_cm
            await activities.run_agent(_FakeInput("recon", tmp_path))

    pv = captured.get("prompt_variables") or {}
    # notice injected (graceful), not absent
    assert "recon_gitnexus_track" in pv
    assert "无" in pv["recon_gitnexus_track"]


# ---- helpers ----
async def _noop_async(*a, **k):
    return None


class _NoopCM:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _noop_cm(*a, **k):
    return _NoopCM()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_agent_recon_track_injection.py -v`
Expected: FAIL — `prompt_variables` 未传（或不含 `recon_gitnexus_track`）

- [ ] **Step 3: Inject prompt_variables for RECON in `run_agent`**

Edit `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` 的 `run_agent`（约 L74-102，`repo, deliverables, _ = _get_paths(input)` 之后、`executor.execute(...)` 之前）。**若 Plan 2 已落地**（已有 `prompt_variables` + `framework_endpoints_summary` 块），在其 dict 上追加 `recon_gitnexus_track` 键；**若 Plan 2 未落地**，用下方完整块（同时含两键，向前兼容 Plan 2）。

```python
        repo, deliverables, _ = _get_paths(input)
        prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)

        # Dual-track lower bound: inject deterministic tracks into recon.
        # - framework_endpoints_summary (Plan 2): §4.2 Framework Origin.
        # - recon_gitnexus_track (Plan 6): §4.1 route groups + §4.2 auth/
        #   middleware/ownership. Recon LLM merges on its side (field-level
        #   dangerous side, spec §4.3) + independently explores others.
        prompt_variables = None
        if input.agent_name == "recon":
            prompt_variables = {}
            # Plan 2: framework endpoints summary
            fa_path = deliverables / "framework_analysis.json"
            if fa_path.exists():
                import json as _json
                from shannon_core.services.framework_analyzer import InferredEndpoint  # noqa: F401
                from shannon_core.services.framework_endpoint_renderer import render_framework_endpoints
                fa_data = _json.loads(fa_path.read_text())
                fa_endpoints = []
                for ep in fa_data.get("inferred_endpoints", []):
                    if isinstance(ep, dict):
                        fa_endpoints.append(InferredEndpoint(
                            method=ep["method"], path=ep["path"], source=ep["source"],
                            model=ep.get("model"),
                            middleware=tuple(ep.get("middleware", [])),
                            vulnerability_indicators=tuple(ep.get("vulnerability_indicators", [])),
                        ))
                prompt_variables["framework_endpoints_summary"] = render_framework_endpoints(fa_endpoints)
            # Plan 6: recon GitNexus track (§4.1 + §4.2)
            from shannon_core.code_index.recon_gitnexus_track import build_recon_gitnexus_track
            prompt_variables["recon_gitnexus_track"] = build_recon_gitnexus_track(str(deliverables))

        await session.start_agent(agent_name.value, f"agent={agent_name.value}", attempt=attempt)
        await tool_audit_logger.initialize()
        metrics = await executor.execute(
            agent_name=agent_name,
            repo_path=str(repo),
            web_url=input.web_url,
            deliverables_path=str(deliverables),
            config_path=input.config_path,
            api_key=input.api_key,
            pipeline_testing=input.pipeline_testing_mode,
            prompt_override=input.prompt_override,
            prompt_variables=prompt_variables,
            tool_audit_logger=tool_audit_logger,
        )
```

> 注：`_to_endpoint` 在 activities.py 现有两处（L651, L715）但都是 `run_route_chain_building`/`run_attack_chain_assembly` 内的局部函数。本注入点为避免依赖其作用域，内联 `InferredEndpoint(...)` 构造（与 L651-656 一致）。若 Plan 2 已提取 `_to_endpoint` 为模块级函数，改用 `_to_endpoint(ep)`。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_agent_recon_track_injection.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run Plan 2's injection test to confirm no regression (if Plan 2 landed)**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_agent_framework_injection.py -v 2>/dev/null || echo "Plan 2 test not present yet — skip"`
Expected: PASS（若 Plan 2 已落地）或 skip（Plan 2 未落地）

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_run_agent_recon_track_injection.py
git commit -m "feat(whitebox): inject recon GitNexus track into recon agent (§4.1 + §4.2 dual-track)"
```

---

### Task 6: `recon.txt` §4.1/§4.2 接入 `{{RECON_GITNEXUS_TRACK}}` 占位符

**Files:**
- Modify: `/root/shannon-py/prompts/recon.txt`（§4.1 标题后 + §4.2 区内插入占位符 + 合并指令）
- Test: 集成验证（`_interpolate` 替换 + 手动跑一次 recon 确认 prompt 含渲染内容）

**Interfaces:**
- Consumes: `{{RECON_GITNEXUS_TRACK}}`（Task 5 注入的 `recon_gitnexus_track`，manager L154-157 upper-case 匹配）
- Produces: recon §4.1/§4.2 从确定性 track 填充（下限）+ LLM 独立补全；字段冲突取危险侧（prompt 指令）

- [ ] **Step 1: Insert the placeholder + directive in recon.txt §4.1**

Edit `/root/shannon-py/prompts/recon.txt`。在 `### 4.1 Shared Controller Route Groups` 标题行之后（:252 附近）、第一个 `#### Group:` 示例之前，插入：

```markdown
<recon_gitnexus_track>
{{RECON_GITNEXUS_TRACK}}

**填充规则**：上方「§4.1 Shared Route Groups」表为 GitNexus 调用图反推的确定性路由组（同 handler 多路由）。你的 §4.1 表须据此填充这些组 + **独立探索其他共享 handler 的路由组**（下限非上限）。**Auth 冲突取「无」**：若确定性轨标某路由 **none** auth，合并取 none（pre-auth variant）。
</recon_gitnexus_track>
```

- [ ] **Step 2: Insert the placeholder reference in §4.2**

在 `## 4.2 Endpoint Security Context` 标题之后（:291 附近），`<framework_endpoints_deterministic>` 区（Plan 2 已加，若已落地）之后、示例表格之前，插入：

```markdown
**§4.2 确定性 Auth/Middleware/Ownership**：见上方 `<recon_gitnexus_track>` 区的「§4.2 Endpoint Security Context」表（GitNexus 源码扫描）。Auth/Middleware/Ownership 据此填充 + 独立核实其他端点。**字段冲突取危险侧**：Auth 任一 none→none；Ownership 任一 none→none；Framework Origin 任一 auto-generated→auto-generated（见 `{{FRAMEWORK_ENDPOINTS_SUMMARY}}`）。Ownership = `guarded` 为候选，须语义确认。
```

> 注：若 Plan 2 的 `<framework_endpoints_deterministic>` 区尚未加（Plan 2 未落地），上方引用 `{{FRAMEWORK_ENDPOINTS_SUMMARY}}` 会成残留占位符 warning——此时删去该句中的 framework origin 引用，仅保留 auth/ownership 指令。先跑 Step 3 确认无残留。

- [ ] **Step 3: Verify placeholder resolves (no residual warning)**

Run: `cd /root/shannon-py && python -c "
from shannon_core.prompts.manager import PromptManager
pm = PromptManager()
tpl = open('prompts/recon.txt').read()
out = pm._interpolate(tpl, {'recon_gitnexus_track': 'TEST_TRACK', 'deliverables_path': '/tmp'}, None, 'recon')
assert 'TEST_TRACK' in out
assert '{{RECON_GITNEXUS_TRACK}}' not in out
print('OK: placeholder resolves')
"`
Expected: `OK: placeholder resolves`

- [ ] **Step 4: Verify recon-static is unaffected**

Run: `cd /root/shannon-py && grep -c "RECON_GITNEXUS_TRACK" prompts/recon-static.txt`
Expected: `0`（recon-static 无此占位符；Plan 2 同款，recon-static 缺 §4.2 是已知缺口）

- [ ] **Step 5: Manual smoke (本 plan 外)**

跑一次白盒扫描，确认 recon prompt 实际收到渲染的 GitNexus track（`recon_deliverable.md` §4.1 含确定性路由组、§4.2 含 auth/middleware/ownership 三列）。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add prompts/recon.txt
git commit -m "feat(prompt): recon §4.1/§4.2 consume RECON_GITNEXUS_TRACK (deterministic dual-track lower bound)"
```

---

### Task 7: 集成验证 — end-to-end track build → markdown → round-trip

**Files:**
- Test: `packages/core/tests/code_index/test_recon_track_integration.py`（Create）

**Interfaces:**
- Consumes: Task 1-4（检测器 + 渲染器 + 编排）
- Produces: 验证从合成 `code_index.json` 经 `build_recon_gitnexus_track` 产出的 markdown 含 §4.1 组 + §4.2 端点 + 危险侧标注；空/缺失场景降级不崩

- [ ] **Step 1: Write the integration test**

```python
# packages/core/tests/code_index/test_recon_track_integration.py
"""End-to-end: code_index.json → build_recon_gitnexus_track → markdown.

Covers §4.1 (shared route groups, auth conflict dangerous side) and §4.2
(auth/middleware/ownership) rendering, plus graceful degradation.
"""
import json

from shannon_core.code_index.recon_gitnexus_track import build_recon_gitnexus_track


def _block(handler_id, source, decorators=None, language="typescript"):
    file_path, func_name, line = handler_id.rsplit(":", 2)
    return {
        "id": handler_id, "file_path": file_path, "function_name": func_name,
        "start_line": int(line), "end_line": int(line) + 5,
        "source_code": source, "parameters": [], "decorators": decorators or [],
        "language": language,
    }


def _ep(handler_id, route, method="GET", auth=None):
    return {
        "func_block_id": handler_id, "entry_type": "http_route", "route": route,
        "http_method": method, "confidence": 0.9, "evidence": "",
        "needs_llm_review": False, "authentication": auth, "source": "code_index",
    }


def _write_index(tmp_path, eps, blocks):
    (tmp_path / "code_index.json").write_text(json.dumps({
        "repository": "r", "language": "typescript",
        "total_blocks": len(blocks), "total_entry_points": len(eps),
        "total_chains": 0, "blocks": blocks, "edges": [],
        "entry_points": eps, "chains": [],
    }))


def test_e2e_shared_group_with_preauth_plus_unowned_endpoint(tmp_path):
    """§4.1 group with one no-auth route (pre-auth) + §4.2 endpoint with no ownership."""
    shared = "c.js:index:32"
    unowned = "u.js:list:1"
    _write_index(tmp_path, [
        _ep(shared, "/preview", auth="required"),
        _ep(shared, "/preview/v2", auth="required"),
        _ep(shared, "/preview/iframe-demo", auth=None),  # pre-auth
        _ep(unowned, "/api/users", method="GET"),
    ], [
        _block(shared, "@UseGuards(AuthGuard)\nexport function index() {}"),
        _block(unowned, "async function list(req) { return db.user.findMany(); }"),
    ])

    md = build_recon_gitnexus_track(str(tmp_path))

    # §4.1
    assert "c.js:index:32" in md
    assert "/preview/iframe-demo" in md
    assert "pre-auth" in md.lower()  # conflict warning
    # §4.2
    assert "GET /api/users" in md
    assert "none" in md.lower()  # ownership none (dangerous side)
    # disclaimer + merge rules
    assert "下限" in md
    assert "危险侧" in md


def test_e2e_java_decorator_auth_and_orm_ownership(tmp_path):
    """Java handler with @PreAuthorize (auth present) + ownership predicate."""
    handler = "Ctrl.java:update:20"
    _write_index(tmp_path, [
        _ep(handler, "/api/items/:id", method="PUT"),
    ], [
        _block(handler,
               "@PreAuthorize(\"hasRole('USER')\")\n"
               "public void update() { repo.findByOwnerId(req.user.id); }",
               decorators=['@PreAuthorize("hasRole(\'USER\')")'], language="java"),
    ])

    md = build_recon_gitnexus_track(str(tmp_path))
    assert "PUT /api/items/:id" in md
    assert "present" in md.lower()  # auth via decorator
    assert "guarded" in md.lower() or "owner" in md.lower()


def test_e2e_graceful_degradation_when_index_missing(tmp_path):
    md = build_recon_gitnexus_track(str(tmp_path))  # no code_index.json
    assert isinstance(md, str)
    assert "无" in md or "no" in md.lower()


def test_e2e_unresolved_handler_marked_unknown(tmp_path):
    """EntryPoint whose handler isn't in blocks → auth/ownership unknown."""
    _write_index(tmp_path, [
        _ep("ghost.js:f:1", "/api/ghost"),
    ], [])  # no blocks
    md = build_recon_gitnexus_track(str(tmp_path))
    assert "/api/ghost" in md
    assert "unknown" in md.lower()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_recon_track_integration.py -v`
Expected: PASS (4 tests)

- [ ] **Step 3: Run the full recon_gitnexus_track test suite**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_recon_route_groups.py packages/core/tests/code_index/test_recon_endpoint_security.py packages/core/tests/code_index/test_recon_render_track.py packages/core/tests/code_index/test_recon_build_track.py packages/core/tests/code_index/test_recon_track_integration.py -v`
Expected: PASS (5+7+5+4+4 = 25 tests)

- [ ] **Step 4: Run broader code_index suite for no regression**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/ -v 2>&1 | tail -30`
Expected: PASS（含新 25 测试 + 现有 code_index 测试）

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/tests/code_index/test_recon_track_integration.py
git commit -m "test(code_index): integration test for recon GitNexus track end-to-end"
```

---

## Self-Review

**1. Spec coverage**（对照 spec §5.2 recon §4.1 + §5.3 recon §4.2 + §4.3 recon 情报合并）：
- §5.2 GitNexus 轨（调用图 handler→多路由 + 中间件注解检测 auth）→ Task 1（handler→路由分组）+ Task 2（auth/middleware 源码扫描）✓
- §5.2 合并（按 handler 去重，auth 冲突取「无」）→ Task 1 `auth_conflict` + Task 3 prompt 指令（LLM 侧合并）✓
- §5.3 GitNexus 轨（framework origin 下限 + 中间件注解 + ORM 谓词 ownership）→ Task 2（auth/middleware/ownership）+ framework origin 复用 Plan 2 ✓
- §5.3 合并（framework origin 取 auto-generated；ownership 取 none）→ Task 3 prompt 指令（LLM 侧合并，字段危险侧）✓
- §4.3 recon 情报合并（字段危险侧）→ Task 3 prompt 指令约束 LLM 合并 ✓（**非** Plan 3 的 `_merge_intel`——见 Global Constraint，产物类型不同）
- §6 framework-analyzer 接通 → 复用 Plan 2 ✓（本 plan 不重做）
- LLM 轨不被锚定（下限非上限）→ Task 3 disclaimer + Task 6 prompt 声明 ✓

**2. Placeholder scan**：无 TBD/TODO。Task 5 Step 3 标注「Plan 2 协同」（两 plan 改同一注入点，合并 dict）——诚实标注并行 plan 依赖，非占位符。Task 6 Step 2 标注 framework 占位符引用的 Plan 2 落地依赖——给出 fallback（删引用句）。

**3. Type consistency**：
- `EntryPoint` 字段（`func_block_id`/`route`/`http_method`/`authentication`，`models.py:49-60`）在 Task 1/2/4 一致。
- `FuncBlock` 字段（`id`/`source_code`/`decorators`/`language`，`models.py:24-36`）在 Task 2 一致；`decorators` 默认 `[]`（TS/Go/PHP parser 不填，java/python 填）→ `_detect_auth`/`_extract_middleware` 容忍空 list ✓。
- `SharedRouteGroup`/`RouteRow`/`EndpointSecurityContext` dataclass 在 Task 1-3 一致（frozen dataclass，tuple 字段）。
- `build_recon_gitnexus_track(str) -> str` 在 Task 4/5/7 一致；`prompt_variables={"recon_gitnexus_track": md}` 键名与 `{{RECON_GITNEXUS_TRACK}}` 占位符（manager L154-157 upper-case）一致。

**4. 危险侧正确性**（spec §4.3）：
- auth：`_detect_auth` 无 guard 关键词 → "none"（Task 2）；§4.1 组 `auth_conflict` 任一 none → True（Task 1）；prompt 指令「Auth 任一 none→none」（Task 3/6）✓
- ownership：`_detect_ownership` 无 ORM 谓词 → "none"（Task 2）；prompt 指令「Ownership 任一 none→none」✓
- framework origin：复用 Plan 2 `{{FRAMEWORK_ENDPOINTS_SUMMARY}}`，prompt 指令「任一 auto-generated→auto-generated」✓

**需人决策点**：
- **A. recon 情报合并定位（LLM 侧 vs 文件侧）**：本 plan 把 recon §4.1/§4.2 双轨合并放在 **LLM 侧**（GitNexus 轨 markdown 作下限注入，recon LLM 读后产合并表）。这是 spec §5.2/§5.3「GitNexus 轨 → LLM 确认」的自然落地——recon LLM 即确认角色。**替代方案**：把 GitNexus 轨也产成结构化 JSON + Plan 3 `_merge_intel` 做文件级合并（产 `recon_exploitation_queue.json` 类产物）。**不选替代方案的理由**：recon intel 当前产物是 `recon_deliverable.md`（markdown 表格），无结构化 schema；若改结构化需先定义 recon intel schema（auth/framework/ownership 为独立字段），是更大的 schema 演进，超本 plan 范围。LLM 侧合并是**务实落地**，依赖 prompt 指令约束危险侧（Task 6 已写明）。**风险**：LLM 可能不严格遵守危险侧规则——mitigated by prompt 显式指令 + 下游 vuln agent（authz）会二次核实 ownership（spec §5.7 authz 也读 §4.2）。
- **B. ORM ownership regex 覆盖度**：`_OWNERSHIP_PREDICATE_RE` 覆盖 Prisma/Sequelize/TypeORM/GORM/Knex/Mongoose/raw SQL 常见写法，但**可能漏 framework-specific 模式**（如 Laravel Eloquent policy、Spring Data `@CreatedBy`、自定义 authz decorator）。故 ownership = `guarded` 标为**候选**（prompt 指令「须语义确认」），`none` 标为**候选**（危险侧，下游 authz 二次核实）。regex 漏报 → `none` 候选 → 危险侧（保守，宁过报不漏报）；regex 误报（`guarded` 但实际无 ownership）→ LLM 语义确认修正。净效果偏向保守，符合 spec §2 原则 4。
- **C. handler→路由分组精度**：依赖 `EntryPoint.func_block_id` 准确（GitNexus EP Scoring 产）。若 GitNexus 未给同一 handler 的多路由正确 `func_block_id`（如 Express 动态路由），分组会漏。mitigated by：recon LLM 独立探索补全（下限非上限）。真实精度需手动冒烟。

**Caveat（诚实）**：
- **TS/Go/PHP parser 不填 `decorators`**（仅 java/python 填，调研确认 `typescript_parser.py` 无 `_extract_decorators`）。故 auth/middleware 检测**主要靠源码 regex**（`_AUTH_GUARD_RE`/`_MIDDLEWARE_HINT_RE`），`FuncBlock.decorators` 仅作 java/python 补充信号。源码 regex 跨语言一致，但精度依赖关键词覆盖——见决策点 B。**未来改进**：给 TS/Go/PHP parser 补 decorator 提取（trivial，仿 `java_parser._extract_annotations`），但超本 plan 范围。
- **真实 GitNexus 索引产出非空路由组需 MCP 环境**：单元测试用合成 `code_index.json`（含 `entry_points` + `blocks`）。真实 `EntryPoint.func_block_id` 精度 + 多路由分组效果由手动冒烟（Task 6 Step 5）验证。
- **Plan 2 协同**：本 plan Task 5/6 与 Plan 2 改同一注入点（`run_agent`）+ 同一 §4.2 区。若两 plan 并行开发，需合并 `prompt_variables` dict + §4.2 区占位符。Task 5 Step 3 的实现已向前兼容（同时含 `framework_endpoints_summary` + `recon_gitnexus_track` 两键）。
- **recon-static 不接**（缺 §4.2，Plan 2 已知缺口）：本 plan 占位符只加 `recon.txt`，recon-static 是 no-op（注入多余键不报错，manager L154-157 `if token in result`）。
