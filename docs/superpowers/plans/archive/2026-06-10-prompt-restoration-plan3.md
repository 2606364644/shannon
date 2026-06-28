# Plan 3: Prompt 恢复 — recon 4.1/4.2 + Cross-Route + Framework IDOR + Branch Path Exhaustion + 入口裁定

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 恢复原始项目中因架构取舍被删除的 4 个关键 prompt 机制，使重构版在 authz IDOR、跨路由覆盖、分支穷举上回到与原始项目等价的水平。

**Architecture:** 本次改动全部在 prompt 文本层面，不涉及 Python 代码。核心策略：不是原样复制原始 prompt，而是在重构版的已有增强（Source Completeness Rule、`accessible_routes` 字段、`_static-dataflow-hints.txt`）基础上，补回被删的结构化分析能力。

**Tech Stack:** Prompt 文本编辑，无代码变更

**Spec:** `docs/superpowers/specs/2026-06-10-whitebox-analysis-tri-dimensional-comparison.md` §3.2.3, §3.2.4

---

## File Structure

| 文件 | 职责 | 改动类型 |
|---|---|---|
| `prompts/shared/_endpoint-security-context.txt` | 新建 — Endpoint Security Context 查表指令 | **新建** |
| `prompts/shared/_cross-route-enumeration.txt` | 新建 — Cross-Route Verification 查表指令 | **新建** |
| `prompts/recon.txt` | 恢复 Section 4.1/4.2 + Route Mapper group detection | **修改** |
| `prompts/vuln-authz.txt` | 恢复 Section 0 + Framework Guidance + Cross-Route 门控 | **修改** |
| `prompts/vuln-injection.txt` | 恢复 Branch Path Exhaustion + Cross-Route @include | **修改** |
| `prompts/vuln-xss.txt` | 添加 Cross-Route @include | **修改** |
| `prompts/vuln-ssrf.txt` | 添加 Cross-Route @include | **修改** |

---

### Task 1: 新建 `_endpoint-security-context.txt`

**Files:**
- Create: `prompts/shared/_endpoint-security-context.txt`

这是原始项目 91 行的文件，被删除后 vuln-authz 丧失了"查表"能力。重建此文件，适配重构版的 deliverable 格式。

- [ ] **Step 1: 创建 `_endpoint-security-context.txt`**

创建 `prompts/shared/_endpoint-security-context.txt`，内容如下：

```
<endpoint_security_context>
## Endpoint Security Context Collection

When analyzing endpoints, collect the following security context for EACH endpoint:

### Per-Endpoint Security Context Table

| Field | Description |
|---|---|
| **HTTP Method** | Full method (GET, POST, PUT, PATCH, DELETE, etc.) — never abbreviate as "ALL" |
| **Path** | Full route path with parameter placeholders (e.g., `/api/users/:id`) |
| **Auth Requirement** | `anon`, `user`, `admin`, or specific role (e.g., `customer`, `deluxe`) |
| **Middleware Chain** | Ordered list of middleware executed before handler, with file:line |
| **Framework Origin** | `manual`, `finale-rest auto-generated`, `epilogue auto-generated`, `other` |
| **Ownership Check** | `explicit` (e.g., `resource.UserId === user.id`), `none detected`, or `absent` |
| **Notes** | Any relevant observations |

### Framework-Specific Patterns

**finale-rest / epilogue Detection:**
- Search for `finale.initialize()`, `finale.resource()`, `epilogue.resource()`, `epilogue.initialize()`
- For each framework auto-generated endpoint:
  - Identify the model name and all CRUD endpoints created (list, read, create, update, delete)
  - Check for `create.end`, `update.end`, `destroy.end` hooks that may add ownership validation
  - If no hooks override default behavior → mark ownership as "none detected"
  - Mark Framework Origin as `finale-rest auto-generated` or `epilogue auto-generated`

**Auto-generated Endpoint Default Behavior:**
- REST frameworks (finale-rest, epilogue, json-server, etc.) typically generate CRUD endpoints WITHOUT ownership checks
- The default assumption for auto-generated endpoints targeting a single resource (read, update, delete) is: NO ownership validation unless explicitly overridden
- Always check for framework-specific hooks or middleware that might add authorization

### Parameter Analysis

For each endpoint, enumerate parameters by source:
- **Path Parameters:** `:id`, `{uuid}`, etc.
- **Query Parameters:** `?search=`, `?page=`, etc.
- **Body Fields:** JSON/form fields with types
- **Headers:** Custom headers used by the endpoint
- Mark each as: `user-controlled` or `server-generated`

### Output Format

Include the Endpoint Security Context table in your deliverable Section 4.2, following the table format above. This table will be consumed by downstream vulnerability analysis agents.
</endpoint_security_context>
```

- [ ] **Step 2: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add prompts/shared/_endpoint-security-context.txt
git commit -m "feat(prompts): restore _endpoint-security-context.txt for IDOR detection

Recreates the endpoint security context collection instructions that were
deleted during refactoring. Provides structured auth/middleware/framework-origin/
ownership data that vuln-authz uses for IDOR detection."
```

---

### Task 2: 新建 `_cross-route-enumeration.txt`

**Files:**
- Create: `prompts/shared/_cross-route-enumeration.txt`

这是原始项目 58 行的文件，被删除后所有 vuln agent 丧失了跨路由覆盖验证能力。

- [ ] **Step 3: 创建 `_cross-route-enumeration.txt`**

创建 `prompts/shared/_cross-route-enumeration.txt`，内容如下：

```
<cross_route_enumeration>
## Cross-Route Verification Checklist

**CRITICAL:** Before documenting ANY vulnerability finding, you MUST complete this four-step checklist.

### Step CR-1: Read Shared Controller Route Groups
Open `.shannon/deliverables/recon_deliverable.md` and locate **Section 4.1 "Shared Controller Route Groups"**. This section lists all routes that share the same handler function.

### Step CR-2: Identify Matching Handler Group
For the vulnerability you are about to document:
- Identify the handler function name and its file:line
- Look up this handler in Section 4.1 to find all routes that share it

### Step CR-3: Enumerate Affected Routes
Based on the route group analysis:
- If the handler group contains routes with **different authentication levels** (e.g., one authenticated, one pre-auth):
  - Create a **SEPARATE finding** for the pre-auth route
  - Mark `authentication_required: false`
  - This is a HIGHER SEVERITY finding (unauthenticated access)
- If all routes in the group have the **same authentication level**:
  - Create a **single finding** covering all routes
  - List all routes in `affected_routes`
- **NEVER** assume a vulnerability affects only the route you traced — it may affect every route sharing that handler

### Step CR-4: Required Fields
Every vulnerability finding MUST include:
- `affected_routes`: array of ALL routes sharing the vulnerable handler (not just the one you traced)
- `authentication_required`: boolean — `true` if ALL affected routes require authentication, `false` if ANY affected route is pre-auth

**Self-check:** If `affected_routes` is missing or contains only one route for a shared handler, your analysis is INCOMPLETE.
</cross_route_enumeration>
```

- [ ] **Step 4: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add prompts/shared/_cross-route-enumeration.txt
git commit -m "feat(prompts): restore _cross-route-enumeration.txt for shared handler coverage

Recreates the cross-route verification checklist that ensures vulnerability
findings cover ALL routes sharing a handler, not just the one traced. Enables
pre-auth variant detection for shared handlers."
```

---

### Task 3: 修改 `recon.txt` — 恢复 Section 4.1/4.2 + group detection

**Files:**
- Modify: `prompts/recon.txt`

需要做 3 处修改：
1. 在文件顶部（约第 13 行）添加 `@include(shared/_endpoint-security-context.txt)`
2. 在 Route Mapper Agent 指令（约第 160 行）添加 group detection
3. 在 deliverable_instructions 的 Section 4（约第 228-243 行）之后插入 Section 4.1 和 4.2

- [ ] **Step 5: 添加 @include 到 recon.txt 头部**

在 `recon.txt` 第 13 行（`@include(shared/_static-dataflow-hints.txt)` 之后）添加：

```
@include(shared/_endpoint-security-context.txt)
```

- [ ] **Step 6: 扩展 Route Mapper Agent 指令**

修改 `recon.txt` 约第 160 行的 Route Mapper Agent。当前内容：

```
      - **Route Mapper Agent**: "Find all backend routes and controllers that handle the discovered endpoints: [list endpoints]. Map each endpoint to its exact handler function with file paths and line numbers."
```

替换为：

```
      - **Route Mapper Agent**: "Find all backend routes and controllers that handle the discovered endpoints: [list endpoints]. Map each endpoint to its exact handler function with file paths and line numbers. **Group Detection:** Identify all routes that map to the same handler function — these routes share the same processing logic and a vulnerability in the handler affects every route in the group. For each group, generate a per-route table where each route has its own row with Method, Path, Auth Middleware (**none** if no auth), and Router line. Include the router definition file:line range in the group title. Also identify any auto-generated CRUD endpoints from REST frameworks (finale-rest, epilogue, etc.) and mark their Framework Origin."
```

- [ ] **Step 7: 在 Section 4 后插入 Section 4.1 和 4.2**

在 `recon.txt` 约第 243 行（Section 4 表格结束）之后，Section 5 之前，插入：

```markdown

### 4.1 Shared Controller Route Groups

When multiple routes share the same handler function, list them together in groups.

#### Group: `[handler_function_name]` (`[handler_file]:[line]`) — `[router_file]:[line_range]`

| Method | Path | Auth Middleware | Router Line |
|---|---|---|---|
| GET | /api/resource | `requireAuth()` | router.js:40 |
| GET | /api/resource/public | **none** | router.js:41 |

> ⚠️ `/api/resource/public` has NO auth middleware — pre-auth variant. Vulnerabilities in the handler affect this route without authentication.

**Shared Controller Parameter Propagation:** When multiple routes share a handler, all parameters the handler reads (query, body, path) must be listed for EVERY route using that handler.

### 4.2 Endpoint Security Context

Using the `<endpoint_security_context>` instructions above, provide a structured security context for each endpoint:

| Method | Path | Auth | Middleware | Framework Origin | Ownership Check | Notes |
|---|---|---|---|---|---|---|
| GET | /api/users/:id | user | `requireAuth()` | manual | `resource.UserId === user.id` | |
| DELETE | /api/Feedbacks/:id | user | `isAuthorized()` | finale-rest auto-generated | none detected | ⚠️ No ownership |
| GET | /api/admin/users | admin | `requireAdmin()` | manual | N/A (list endpoint) | |

**Framework Endpoints Detected:** [List any finale-rest/epilogue auto-generated endpoints with their models and CRUD operations]
```

- [ ] **Step 8: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add prompts/recon.txt
git commit -m "feat(prompts): restore recon Section 4.1/4.2 + Route Mapper group detection

Restores three deleted capabilities:
- Section 4.1: Shared Controller Route Groups with per-route auth middleware
- Section 4.2: Endpoint Security Context with framework origin + ownership check
- Route Mapper Agent: group detection for shared handler functions

Also adds @include for _endpoint-security-context.txt framework detection instructions."
```

---

### Task 4: 修改 `vuln-authz.txt` — 恢复 Section 0 + Framework Guidance + Cross-Route

**Files:**
- Modify: `prompts/vuln-authz.txt`

需要做 3 处修改：
1. 添加 `@include(shared/_cross-route-enumeration.txt)` 在头部
2. 在 methodology Section 1 之前插入 Section 0
3. 在 conclusion_trigger 中添加 Cross-Route Verification 门控

- [ ] **Step 9: 添加 @include 到 vuln-authz.txt 头部**

在 `vuln-authz.txt` 第 47 行（`@include(shared/_static-dataflow-hints.txt)` 之后）添加：

```
@include(shared/_cross-route-enumeration.txt)
```

- [ ] **Step 10: 在 methodology Section 1 之前插入 Section 0**

在 `vuln-authz.txt` 约第 130-131 行（`---` 之后、`### 1) Horizontal Authorization Analysis` 之前）插入：

```markdown

### 0) Read Endpoint Security Context (REQUIRED — Do This First)

Before analyzing any authorization vulnerabilities:

1. **Read the Recon Deliverable:**
   - Open `.shannon/deliverables/recon_deliverable.md`
   - Locate the "Endpoint Security Context" section (Section 4.2)
   - Extract all endpoints and their security context

2. **For each endpoint in your To Do list:**
   - Look up its security context in Section 4.2
   - Note: Auth requirement, Middleware chain, Framework Origin (manual vs auto-generated), Ownership validation status

3. **Prioritize endpoints with:**
   - Framework Origin: "finale-rest auto-generated" or "epilogue auto-generated"
   - Ownership Validation: "none detected" or "absent"
   - HTTP Method: DELETE, PUT, PATCH (mutation operations)
   - Auth: only "user" (no role restriction)

**Framework Endpoint Guidance:**
When Recon reports an endpoint with `Framework Origin: finale-rest auto-generated` or `epilogue auto-generated`:
- The endpoint was generated by an ORM-to-REST framework, NOT manually coded
- Default behavior is CRUD with NO ownership checks
- Check whether the framework's `create.end`, `update.end`, `destroy.end` hooks add ownership validation
- If no hooks override the default behavior → the endpoint is vulnerable to IDOR
- **Assume IDOR vulnerable** unless Recon explicitly found an ownership check in the framework hooks
- Set `confidence: high` for these findings since the framework behavior is well-documented

---
```

- [ ] **Step 11: 在 conclusion_trigger 中添加 Cross-Route Verification 门控**

在 `vuln-authz.txt` 约第 352 行的 `<conclusion_trigger>` 中，在 "1. **Todo Completion:**" 之前添加：

```

0. **Cross-Route Verification:** For each vulnerability finding, confirm that `affected_routes` lists ALL routes that share the same handler per recon deliverable Section 4.1. Findings missing `affected_routes` or `authentication_required` are INCOMPLETE. A finding with `authentication_required: false` MUST have a corresponding pre-auth route in `affected_routes`.

```

- [ ] **Step 12: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add prompts/vuln-authz.txt
git commit -m "feat(prompts): restore vuln-authz Section 0 + Framework Guidance + Cross-Route gate

Restores three deleted IDOR detection capabilities:
- Section 0: mandatory first step to read Endpoint Security Context
- Framework Endpoint Guidance: assume IDOR for finale-rest/epilogue auto-generated endpoints
- Cross-Route Verification: conclusion gate ensuring affected_routes covers all shared handlers"
```

---

### Task 5: 修改 `vuln-injection.txt` — 恢复 Branch Path Exhaustion + Cross-Route

**Files:**
- Modify: `prompts/vuln-injection.txt`

需要做 2 处修改：
1. 添加 `@include(shared/_cross-route-enumeration.txt)` 在头部
2. 在 Step 2 之后插入 Branch Path Exhaustion 指令

- [ ] **Step 13: 添加 @include 到 vuln-injection.txt 头部**

在 `vuln-injection.txt` 第 45 行（`@include(shared/_static-dataflow-hints.txt)` 之后）添加：

```
@include(shared/_cross-route-enumeration.txt)
```

- [ ] **Step 14: 在 Step 2 之后插入 Branch Path Exhaustion**

在 `vuln-injection.txt` 约第 145 行（Step 2 的 "**C. All concatenations on that path:**" 之后）添加：

```
	  - **D. Branch Path Exhaustion:** When a controller method contains conditional branches (`if/else`, early returns) that cause different data transformations on the same output variable, you MUST trace EACH branch independently. Do NOT assume safety because one branch validates — another branch may read the same parameter directly from user input without validation.
```

- [ ] **Step 15: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add prompts/vuln-injection.txt
git commit -m "feat(prompts): restore Branch Path Exhaustion + Cross-Route in vuln-injection

Restores two deleted injection analysis capabilities:
- Branch Path Exhaustion: forces independent tracing of each conditional branch
- Cross-Route Enumeration: includes _cross-route-enumeration.txt for shared handler coverage"
```

---

### Task 6: 修改其他 vuln prompt 添加 Cross-Route @include

**Files:**
- Modify: `prompts/vuln-xss.txt`
- Modify: `prompts/vuln-ssrf.txt`

原始项目中所有 5 个 vuln prompt 都包含 `@include(shared/_cross-route-enumeration.txt)`。重构版全删了。需要对 XSS 和 SSRF 也恢复。

- [ ] **Step 16: 添加 @include 到 vuln-xss.txt**

在 `vuln-xss.txt` 的 `@include(shared/_static-dataflow-hints.txt)` 之后添加：

```
@include(shared/_cross-route-enumeration.txt)
```

- [ ] **Step 17: 添加 @include 到 vuln-ssrf.txt**

在 `vuln-ssrf.txt` 的 `@include(shared/_static-dataflow-hints.txt)` 之后添加：

```
@include(shared/_cross-route-enumeration.txt)
```

- [ ] **Step 18: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add prompts/vuln-xss.txt prompts/vuln-ssrf.txt
git commit -m "feat(prompts): restore Cross-Route Enumeration in vuln-xss and vuln-ssrf

All vulnerability analysis prompts now include cross-route enumeration
for shared handler coverage verification."
```

---

### Task 7: 入口裁定修复 — 替代橡皮图章

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/__init__.py` (save_adjudication 函数)

当前 `save_adjudication` 无条件全置 `verdict=CONFIRMED`。需要改为基于置信度的裁定。

- [ ] **Step 19: 定位 save_adjudication 函数**

读取 `packages/whitebox/src/shannon_whitebox/pipeline/__init__.py` 中 `save_adjudication` 的实现。

- [ ] **Step 20: 修改裁定逻辑**

将无条件全 CONFIRMED 改为：
- confidence >= 0.80 或有装饰器匹配 → CONFIRMED
- confidence < 0.80 且 `needs_llm_review=True` → NEEDS_REVIEW
- confidence < 0.50 → REJECTED

具体代码取决于当前实现的行号和字段名。修改后在测试中验证。

- [ ] **Step 21: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/__init__.py
git commit -m "fix(pipeline): replace rubber-stamp adjudication with confidence-based filtering

Entry points with confidence < 0.80 and needs_llm_review are now marked
NEEDS_REVIEW instead of being unconditionally CONFIRMED. This prevents
low-confidence async def catch-alls from polluting call chains with noise."
```

---

## Self-Review

**1. Spec coverage:**
- §3.2.3 recon 4.1/4.2 丧失 → Task 1 + 3 ✅
- §3.2.3 _endpoint-security-context.txt 丧失 → Task 1 ✅
- §3.2.3 _cross-route-enumeration.txt 丧失 → Task 2 ✅
- §3.2.3 vuln-authz Section 0 + Framework Guidance 丧失 → Task 4 ✅
- §3.2.3 Cross-Route Verification 门控丧失 → Task 4 ✅
- §3.2.4 Branch Path Exhaustion 删除 → Task 5 ✅
- §3.2.4 vuln prompt 删 Cross-Route @include → Task 4, 5, 6 ✅
- §2.3.1 入口裁定橡皮图章 → Task 7 ✅

**2. Placeholder scan:** No TBD/TODO found ✅

**3. Type consistency:**
- All `@include` paths match actual file locations ✅
- Section 4.1/4.2 format follows recon.txt deliverable_instructions pattern ✅
- Task 7 的裁定逻辑需要读取实际代码确认字段名——Step 19 标注了"定位"步骤 ✅

**注意：** Plan 3 的 Task 7 涉及 Python 代码修改（入口裁定），但步骤 19-20 需要在执行时先确认 `save_adjudication` 的当前实现。这不影响其他 Task 的执行——Task 1-6 都是独立的 prompt 修改。
