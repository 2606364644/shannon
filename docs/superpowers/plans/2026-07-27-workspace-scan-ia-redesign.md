# 工作区 / 扫描任务信息架构重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重排 web 层工作区/扫描的信息架构：顶栏「工作区」直跳置顶 ws + 侧边抽屉切换，首页改扫描任务表标归属 ws，工作区管理页收归 admin 且去状态栏，扫描列表加四维筛选，全部按现有多对多权限分层。

**Architecture:** 后端增量 3 处（`GET /api/scans` 跨 ws 聚合 + `PUT /api/users/me/pinned-workspace` 置顶 + `/auth/me` 返 pinned）+ `users.pinned_workspace` 列迁移；前端 2 新组件（`WorkspaceSwitcher` 抽屉 / `ScanFilters` 筛选条）+ 2 页重写（`DashboardPage` 扫描视角 / `WorkspaceListPage` admin 专属精简）+ 顶栏 redirect 组件 `WorkspacesEntry`。复用现有 `workspace_member`/`require_admin` 依赖项，零新增鉴权框架。

**Tech Stack:** FastAPI + pydantic（后端）；React 18 + TypeScript + react-router v6 + @tanstack/react-table + shadcn/ui + react-i18next（前端）；pytest（后端测试）；vitest（前端测试）。

## Global Constraints

- 不触碰核心不变量：双轨 / 引擎 / cost 计费（CLAUDE.md §1/§2/§4）。本次纯 web 层（`packages/web/frontend/src/` + `packages/web/src/supernova_web/`）。
- 权限复用现有依赖项 `current_user` / `workspace_member` / `workspace_manager` / `require_admin`（`auth/dependencies.py`），不新增鉴权框架。
- 多对多关系：一个用户可归属多个 ws，一个 ws 可分配给多个用户；`workspace_members` 表不动。
- `pinned_workspace` 是用户属性，经现有 `/auth/me` 返回，`GET /workspaces` 响应不加 `pinned_by_me`（决策 A）。
- 后端测试复用 `tests/conftest.py` 的 `tmp_workspaces` / `app_with_ws` fixture + TestClient + CSRF 风格。
- 前端测试/构建用 `./node_modules/.bin/vitest` / `tsc` / `vite` 直接跑，**别用 `pnpm test`**（pnpm11 verifyDepsBeforeRun 阻断）。
- 前端测试陷阱：全套 pytest 有预存挂起/失败，只跑改动相关测试文件（见 memory `feat-fork-py-test-gotchas`）。
- i18n：纯加键不删旧，中英双语（`frontend/src/locales/zh.json`、`en.json`）。
- TDD：每个任务先写失败测试再实现。每个任务结束 commit。
- 分支：`feat/fork-py`（本地多项未 push）。

## File Structure

**后端（`packages/web/src/supernova_web/`）：**
- `auth/models.py` — `User` 加 `pinned_workspace: str | None = None` 字段。
- `auth/store.py` — `_SCHEMA` users 表加 `pinned_workspace TEXT` 列；幂等 ALTER；`create_user`/`get_user`/`get_user_by_username`/`list_all_users` SQL 补列读写；新增 `update_pinned_workspace(user_id, ws_name)`。
- `auth/routes.py` — `_user_out` 加 `pinned_workspace` 字段。
- `api/users.py` — 新增 `PUT /api/users/me/pinned-workspace` 路由。
- `api/scans.py` — 新增 `GET /api/scans` 跨 ws 聚合路由。

**后端测试（`packages/web/tests/`）：**
- `test_auth_store.py` — pinned 列迁移 + `update_pinned_workspace` 测试。
- `test_pinned_workspace.py`（新）— 置顶端点 e2e + `/auth/me` 返 pinned。
- `test_scans_cross_ws.py`（新）— `GET /api/scans` 跨 ws 聚合 + 权限过滤测试。

**前端（`packages/web/frontend/src/`）：**
- `api/types.ts` — `ScanSummary` 加 `workspace: string`；`AuthUser`（在 `auth/AuthContext.tsx`）加 `pinned_workspace?: string | null`。
- `api/client.ts` — 新增 `listAllScans()` / `setPinnedWorkspace(ws)` 导出。
- `components/ScanFilters.tsx`（新）— 受控筛选条 + `useScanFilters(scans)` hook。
- `components/WorkspaceSwitcher.tsx`（新）— 侧边抽屉切换器。
- `components/WorkspacesEntry.tsx`（新）— 顶栏「工作区」三段跳转 redirect 组件。
- `components/layout/TopBar.tsx` — 顶栏「工作区」改用 `WorkspacesEntry`；admin 可见「工作区管理」。
- `pages/DashboardPage.tsx` — 重写为扫描任务表。
- `pages/WorkspaceListPage.tsx` — 精简（去 StatRow），admin 专属由路由层 `RequireAdmin` 保证。
- `routes/WorkspaceDetail/index.tsx` — header 加置顶按钮 + 切换器入口。
- `routes/WorkspaceDetail/ScanDetail.tsx` — header 加切换器入口。
- `routes/WorkspaceDetail/ScanList.tsx` — 加 `<ScanFilters />` + 过滤层。
- `router.tsx` — `/workspaces` 包 `RequireAdmin`；`/` 重定向逻辑。
- `auth/AuthContext.tsx` — `AuthUser` 加 `pinned_workspace`。

**i18n（`packages/web/frontend/src/locales/`）：**
- `zh.json` / `en.json` — 新增 `workspaceSwitcher.*` / `workspaceDetail.pin*` / `scanFilters.*` / `dashboard.scanTable.*` / `dashboard.noWorkspace.*` / `nav.workspaceManage`。

---

## Task 1: 后端 — `users.pinned_workspace` 列迁移 + store 方法

**Files:**
- Modify: `packages/web/src/supernova_web/auth/models.py`
- Modify: `packages/web/src/supernova_web/auth/store.py`
- Test: `packages/web/tests/test_auth_store.py`

**Interfaces:**
- Produces: `User.pinned_workspace: str | None`; `AuthStore.update_pinned_workspace(user_id: int, ws_name: str | None) -> None`; `AuthStore.get_user(user_id)` / `get_user_by_username(username)` 返回的 `User` 含 `pinned_workspace`。后续 Task 2/3 依赖这些。

- [ ] **Step 1: 写失败测试 — 列迁移 + update_pinned_workspace**

追加到 `tests/test_auth_store.py` 末尾：

```python
def test_pinned_workspace_column_migration_and_update(tmp_path):
    """旧库（无 pinned_workspace 列）启动补列不崩；update/get 读写 pinned。"""
    import sqlite3
    from supernova_web.auth.store import AuthStore
    from supernova_web.auth.passwords import hash_password

    db = tmp_path / "auth.db"
    # 模拟旧库：手动建无 pinned_workspace 列的 users 表 + 一条用户
    with sqlite3.connect(db) as c:
        c.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
            "password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', "
            "created_at TEXT NOT NULL, must_change_password INTEGER NOT NULL DEFAULT 0)"
        )
        c.execute(
            "INSERT INTO users(username, password_hash, role, created_at, must_change_password) "
            "VALUES(?,?,?,?,?)",
            ("alice", hash_password("pw"), "user", "2026-01-01T00:00:00Z", 0),
        )

    store = AuthStore(str(db))
    store.init_schema()  # 旧库补列，不崩

    u = store.get_user_by_username("alice")
    assert u is not None
    assert u.pinned_workspace is None  # 旧库补列后默认 None

    store.update_pinned_workspace(u.id, "ws-alpha")
    assert store.get_user(u.id).pinned_workspace == "ws-alpha"

    # 新建用户 pinned_workspace 默认 None
    new = store.create_user("bob", hash_password("pw"), role="user")
    assert store.get_user(new.id).pinned_workspace is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /root/shannon-py/packages/web && python -m pytest tests/test_auth_store.py::test_pinned_workspace_column_migration_and_update -v`
Expected: FAIL — `AttributeError: 'User' object has no attribute 'pinned_workspace'`（model 无字段）或 init_schema 未补列。

- [ ] **Step 3: 改 `auth/models.py` — User 加字段**

`packages/web/src/supernova_web/auth/models.py`，`User` class 内（`created_at` 字段后）加：

```python
    # per-user 置顶工作区（IA 重设计 §2.3）：用户从归属 ws 里 pin 一个，
    # 顶栏「工作区」默认跳它。None = 未置顶（跳最近归属 ws）。多对多关系不动。
    pinned_workspace: str | None = None
```

- [ ] **Step 4: 改 `auth/store.py` — _SCHEMA 加列 + 幂等 ALTER + SQL 读写 + update 方法**

4a. `_SCHEMA` 的 `users` 表 CREATE 语句，在 `must_change_password INTEGER NOT NULL DEFAULT 0` 后加列：

```sql
, pinned_workspace TEXT
```

（即 users 表 CREATE 的最后一列变为 `...must_change_password INTEGER NOT NULL DEFAULT 0, pinned_workspace TEXT)`）

4b. 在 `_ADD_MUST_CHANGE_COL` 常量后，加幂等 ALTER 常量：

```python
_ADD_PINNED_WS_COL = "ALTER TABLE users ADD COLUMN pinned_workspace TEXT"
```

4c. `init_schema` 方法，在 `c.execute(_ADD_MUST_CHANGE_COL)` 的 try/except 后，追加同款 try/except：

```python
            try:
                c.execute(_ADD_PINNED_WS_COL)  # 旧库补列；新库已含 -> OperationalError 吞掉
            except sqlite3.OperationalError:
                pass
```

4d. `create_user` 方法的 INSERT，列名加 `pinned_workspace`、值加 `None`：

```python
            cur = c.execute(
                "INSERT INTO users(username, password_hash, role, created_at, must_change_password, pinned_workspace) "
                "VALUES(?,?,?,?,?,?)",
                (username, password_hash, role, now, 1 if must_change else 0, None),
            )
```

（`User(...)` 构造无需改，`pinned_workspace` 有默认 None）

4e. `get_user_by_username`、`get_user` 的 SELECT 列名加 `pinned_workspace`，`User(...)` 构造加 `pinned_workspace=row[N]`。例如 `get_user`：

```python
    def get_user(self, user_id: int) -> User | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT id, username, role, must_change_password, pinned_workspace FROM users WHERE id=?", (user_id,)
            ).fetchone()
        return User(id=row[0], username=row[1], role=row[2],
                    must_change_password=bool(row[3]),
                    pinned_workspace=row[4]) if row else None
```

`get_user_by_username` 同理（SELECT 列加 `pinned_workspace`，构造加 `pinned_workspace=row[4]`）。

4f. `list_all_users` 的 SELECT 与 `User(...)` 构造同理加 `pinned_workspace`（注意此查询多一列 `created_at`，索引顺移）：

```python
    def list_all_users(self) -> list["User"]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, username, role, must_change_password, created_at, pinned_workspace FROM users ORDER BY id"
            ).fetchall()
        return [User(id=r[0], username=r[1], role=r[2],
                     must_change_password=bool(r[3]), created_at=r[4],
                     pinned_workspace=r[5]) for r in rows]
```

4g. 新增方法（放 `update_workspace_member_role` 后）：

```python
    def update_pinned_workspace(self, user_id: int, ws_name: str | None) -> None:
        """per-user 置顶工作区。ws_name=None 清除置顶。多对多关系不动——pin 不改成员关系。"""
        with self._conn() as c:
            c.execute("UPDATE users SET pinned_workspace=? WHERE id=?", (ws_name, user_id))
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd /root/shannon-py/packages/web && python -m pytest tests/test_auth_store.py::test_pinned_workspace_column_migration_and_update tests/test_auth_store.py -v`
Expected: PASS（新测试 + 既有 auth_store 测试全绿）。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/web/src/supernova_web/auth/models.py packages/web/src/supernova_web/auth/store.py packages/web/tests/test_auth_store.py
git commit -m "feat(web): users.pinned_workspace 列迁移 + update_pinned_workspace

per-user 置顶工作区存储；旧库幂等 ALTER 补列；create/get/list SQL 补列读写。
IA 重设计 §2.3，多对多关系不动。"
```

---

## Task 2: 后端 — `/auth/me` 返 pinned + `PUT /api/users/me/pinned-workspace`

**Files:**
- Modify: `packages/web/src/supernova_web/auth/routes.py`
- Modify: `packages/web/src/supernova_web/api/users.py`
- Test: `packages/web/tests/test_pinned_workspace.py`

**Interfaces:**
- Consumes: `AuthStore.update_pinned_workspace`（Task 1）；`User.pinned_workspace`（Task 1）；`workspace_member` 依赖项。
- Produces: `GET /api/auth/me` 响应含 `pinned_workspace` 字段；`PUT /api/users/me/pinned-workspace`（body `{workspace: str}`，返 `{pinned: str}`，403 若非成员）。

- [ ] **Step 1: 写失败测试 — e2e 置顶 + /auth/me 返 pinned**

新建 `packages/web/tests/test_pinned_workspace.py`：

```python
import pytest
from starlette.testclient import TestClient

from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


@pytest.fixture
def admin_client(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_core.utils.paths import resolve_workspaces_dir
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    assert resolve_workspaces_dir() == tmp_workspaces
    app = create_app()
    app.state.auth_store.create_user("admin", hash_password("admin-pw"), role="admin")
    # 建一个真实 ws 目录 + workspace.json，使 workspace_member 依赖项能命中
    from supernova_web.components.scan_store import write_workspace_meta
    (tmp_workspaces / "ws-a").mkdir()
    write_workspace_meta(tmp_workspaces / "ws-a", name="ws-a", owner="admin")
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": "admin", "password": "admin-pw"},
           headers={"X-CSRF-Token": tok})
    return c, app


def _csrf(c):
    return c.cookies.get("sn-csrf") or c.get("/api/auth/csrf").json()["csrf_token"]


def test_me_returns_pinned_field_default_none(admin_client):
    c, _ = admin_client
    r = c.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["user"]["pinned_workspace"] is None


def test_pin_workspace_success(admin_client):
    c, app = admin_client
    r = c.put("/api/users/me/pinned-workspace", json={"workspace": "ws-a"},
              headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 200
    assert r.json()["pinned"] == "ws-a"
    # /auth/me 现在返 pinned
    assert c.get("/api/auth/me").json()["user"]["pinned_workspace"] == "ws-a"


def test_pin_nonexistent_workspace_404(admin_client):
    c, _ = admin_client
    r = c.put("/api/users/me/pinned-workspace", json={"workspace": "no-such-ws"},
              headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 404


def test_pin_non_member_forbidden(tmp_workspaces, monkeypatch):
    """普通用户 pin 非归属 ws -> 403。"""
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_core.utils.paths import resolve_workspaces_dir
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    assert resolve_workspaces_dir() == tmp_workspaces
    app = create_app()
    st = app.state.auth_store
    st.create_user("alice", hash_password("alice-pw"), role="user")
    from supernova_web.components.scan_store import write_workspace_meta
    (tmp_workspaces / "ws-a").mkdir()
    write_workspace_meta(tmp_workspaces / "ws-a", name="ws-a", owner="admin")
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": "alice", "password": "alice-pw"},
           headers={"X-CSRF-Token": tok})
    # alice 非 ws-a 成员 -> 403
    r = c.put("/api/users/me/pinned-workspace", json={"workspace": "ws-a"},
              headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 403
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /root/shannon-py/packages/web && python -m pytest tests/test_pinned_workspace.py -v`
Expected: FAIL — `/auth/me` 响应无 `pinned_workspace` 字段；`PUT /api/users/me/pinned-workspace` 404（路由不存在）。

- [ ] **Step 3: 改 `auth/routes.py` — `_user_out` 加字段**

`_user_out` 函数改为：

```python
def _user_out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role,
            "must_change_password": u.must_change_password,
            "pinned_workspace": u.pinned_workspace}
```

- [ ] **Step 4: 改 `api/users.py` — 新增置顶路由**

在 `packages/web/src/supernova_web/api/users.py` 顶部 import 区，确保有：

```python
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from supernova_web.auth.dependencies import current_user, workspace_member
from supernova_web.auth.models import User
```

在 router 定义后、现有路由前，加：

```python
class PinnedWorkspaceIn(BaseModel):
    workspace: str


@router.put("/me/pinned-workspace")
async def set_pinned_workspace(body: PinnedWorkspaceIn, request: Request,
                               user: User = Depends(current_user)):
    """per-user 置顶工作区（IA 重设计 §2.3）。只能 pin 有权限的 ws
    （workspace_member 依赖项鉴权：admin 全部、普通用户需为成员）。"""
    ws_dir = request.app.state.config.workspaces_dir / body.workspace
    if not ws_dir.exists():
        raise HTTPException(404, "workspace not found")
    # workspace_member 依赖项无法在 body 参数里用（ws 来自 body），手动鉴权
    if user.role != "admin":
        role = request.app.state.auth_store.get_workspace_member_role(body.workspace, user.id)
        if role is None:
            raise HTTPException(403, "not a workspace member")
    request.app.state.auth_store.update_pinned_workspace(user.id, body.workspace)
    return {"pinned": body.workspace}
```

注：`workspace_member` 依赖项的 ws 来自路径参数，此处 ws 来自 body，故手动复用同一鉴权逻辑（admin 放行 / 否则查 `get_workspace_member_role`），与 `workspace_member` 语义一致。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd /root/shannon-py/packages/web && python -m pytest tests/test_pinned_workspace.py tests/test_auth_session.py -v`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/web/src/supernova_web/auth/routes.py packages/web/src/supernova_web/api/users.py packages/web/tests/test_pinned_workspace.py
git commit -m "feat(web): /auth/me 返 pinned + PUT /api/users/me/pinned-workspace

置顶只能 pin 有权限的 ws（admin 全部/普通需成员，手动复用 workspace_member 语义）。
IA 重设计 §2.3/§7.2/§7.3。"
```

---

## Task 3: 后端 — `GET /api/scans` 跨 ws 扫描聚合

**Files:**
- Modify: `packages/web/src/supernova_web/api/scans.py`
- Test: `packages/web/tests/test_scans_cross_ws.py`

**Interfaces:**
- Consumes: `ScanStore.list_scans(ws)`（现有，返 `list[ScanSummary]`）；`indexer.list_workspaces()`（现有）；`auth_store.list_user_workspaces(user_id)`（现有）。
- Produces: `GET /api/scans` 返 `ScanSummary[]`，每条注入 `workspace: str` 字段，按 `created_at` 倒序。前端 Task 7 的 `listAllScans()` 依赖此端点。

- [ ] **Step 1: 写失败测试 — 跨 ws 聚合 + 权限过滤 + workspace 字段**

新建 `packages/web/tests/test_scans_cross_ws.py`：

```python
import json
import pytest
from starlette.testclient import TestClient

from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


def _seed_scan(ws_dir, scan_id, created_at_iso, status="completed"):
    """在 ws/scans/<scan_id>/ 建一个最小 session.json（ScanStore.list_scans 可读）。"""
    from supernova_web.components.scan_store import write_workspace_meta
    scan_dir = ws_dir / "scans" / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    write_workspace_meta(ws_dir, name=ws_dir.name, owner="admin")
    (scan_dir / "session.json").write_text(json.dumps({
        "scan_id": scan_id, "scan_type": "whitebox", "status": status,
        "created_at": created_at_iso, "vuln_count": 0,
    }), encoding="utf-8")


@pytest.fixture
def setup(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_core.utils.paths import resolve_workspaces_dir
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    assert resolve_workspaces_dir() == tmp_workspaces
    app = create_app()
    st = app.state.auth_store
    st.create_user("admin", hash_password("admin-pw"), role="admin")
    alice = st.create_user("alice", hash_password("alice-pw"), role="user")
    # ws-a：admin + alice 都是成员；ws-b：仅 admin
    for ws in ("ws-a", "ws-b"):
        (tmp_workspaces / ws).mkdir()
    st.add_workspace_member("ws-a", alice.id, "member")
    _seed_scan(tmp_workspaces / "ws-a", "20260727-100000", "2026-07-27T10:00:00Z", "completed")
    _seed_scan(tmp_workspaces / "ws-b", "20260727-110000", "2026-07-27T11:00:00Z", "running")
    c = TestClient(app)
    return c, app, alice


def _login(c, username, password):
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": username, "password": password},
           headers={"X-CSRF-Token": tok})


def test_admin_sees_all_ws_scans(setup):
    c, _, _ = setup
    _login(c, "admin", "admin-pw")
    r = c.get("/api/scans")
    assert r.status_code == 200
    scans = r.json()
    assert len(scans) == 2
    ws_names = {s["workspace"] for s in scans}
    assert ws_names == {"ws-a", "ws-b"}
    # 每条都有 workspace 字段
    assert all("workspace" in s for s in scans)
    # 按 created_at 倒序（11:00 在前）
    assert scans[0]["scan_id"] == "20260727-110000"


def test_normal_user_sees_only_member_ws_scans(setup):
    c, _, _ = setup
    _login(c, "alice", "alice-pw")
    r = c.get("/api/scans")
    assert r.status_code == 200
    scans = r.json()
    assert len(scans) == 1
    assert scans[0]["workspace"] == "ws-a"


def test_unauth_401(setup):
    c, _, _ = setup
    assert c.get("/api/scans").status_code == 401
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /root/shannon-py/packages/web && python -m pytest tests/test_scans_cross_ws.py -v`
Expected: FAIL — `GET /api/scans` 404（路由不存在）。

- [ ] **Step 3: 改 `api/scans.py` — 新增 GET /api/scans 路由**

在 `packages/web/src/supernova_web/api/scans.py`，确保顶部 import 有 `current_user` / `ScanStore` / `User`（若已有则不重复）。在现有 per-ws 路由外（router 顶层），加：

```python
from supernova_web.auth.dependencies import current_user
from supernova_web.auth.models import User
from supernova_web.components.scan_store import ScanStore


@router.get("")
async def list_all_scans(request: Request, user: User = Depends(current_user)):
    """跨 ws 扫描聚合（IA 重设计 §3.1/§7.1）。admin 见全部 ws 扫描，
    普通用户只见归属 ws（list_user_workspaces）的扫描。每条注入 workspace 字段，
    按 created_at 倒序。ws 量通常个位数到几十，每 ws list_scans 是目录扫描，可接受。"""
    cfg = request.app.state.config
    indexer = request.app.state.indexer
    store = ScanStore(cfg.workspaces_dir)
    if user.role == "admin":
        ws_names = [w["name"] for w in indexer.list_workspaces()]
    else:
        ws_names = request.app.state.auth_store.list_user_workspaces(user.id)
    out = []
    for ws in ws_names:
        for s in store.list_scans(ws):
            d = s.__dict__ if not hasattr(s, "model_dump") else s.model_dump()
            d["workspace"] = ws
            out.append(d)
    out.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return out
```

注：`ScanSummary` 是 pydantic model（`scan_store.py:82`），优先用 `model_dump()`；`__dict__` 兜底防非 pydantic。`created_at` 在 `ScanSummary` 是 unix int（见 `api/types.ts` 注释），排序键用 `x.get("created_at") or 0` 防 None。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /root/shannon-py/packages/web && python -m pytest tests/test_scans_cross_ws.py tests/test_scan_decoupling_invariants.py -v`
Expected: PASS（新测试 + 既有 scan 解耦不变量测试不破）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/web/src/supernova_web/api/scans.py packages/web/tests/test_scans_cross_ws.py
git commit -m "feat(web): GET /api/scans 跨 ws 扫描聚合

admin 全部/普通用户归属 ws 的扫描并集，每条注入 workspace 字段，created_at 倒序。
首页扫描任务表数据源。IA 重设计 §3.1/§7.1。"
```

---

## Task 4: 前端 — types + client + AuthUser 字段

**Files:**
- Modify: `packages/web/frontend/src/api/types.ts`
- Modify: `packages/web/frontend/src/api/client.ts`
- Modify: `packages/web/frontend/src/auth/AuthContext.tsx`

**Interfaces:**
- Consumes: 后端 Task 2/3 的端点。
- Produces: `ScanSummary.workspace: string`；`AuthUser.pinned_workspace?: string | null`；`listAllScans(): Promise<ScanSummary[]>`；`setPinnedWorkspace(ws: string): Promise<{pinned: string}>`。后续 Task 5-9 依赖这些。

- [ ] **Step 1: 改 `api/types.ts` — ScanSummary 加 workspace**

`ScanSummary` interface 内（末尾 `is_correlation?: boolean;` 后）加：

```typescript
  // IA 重设计 §3：跨 ws 聚合（GET /api/scans）注入的归属工作区名。per-ws listScans 不返此字段。
  workspace?: string;
```

- [ ] **Step 2: 改 `auth/AuthContext.tsx` — AuthUser 加 pinned_workspace**

`AuthUser` type 内（`must_change_password: boolean;` 后）加：

```typescript
  // per-user 置顶工作区（IA 重设计 §2.3）。null=未置顶。经 /auth/me 返回。
  pinned_workspace?: string | null;
```

- [ ] **Step 3: 改 `api/client.ts` — 新增 listAllScans + setPinnedWorkspace**

在 `listScans` 导出（约 136 行）附近，加：

```typescript
export const listAllScans = () => apiGet<ScanSummary[]>("/scans");

export const setPinnedWorkspace = (ws: string) =>
  apiPut<{ pinned: string }>("/users/me/pinned-workspace", { workspace: ws });
```

确保文件顶部已 import `ScanSummary` 类型与 `apiPut`（`apiPut` 已在 70 行导出；`ScanSummary` 若未 import 则加 `import type { ScanSummary } from "./types";`）。

- [ ] **Step 4: tsc 类型检查**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 零错误。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/api/types.ts packages/web/frontend/src/api/client.ts packages/web/frontend/src/auth/AuthContext.tsx
git commit -m "feat(web-fe): ScanSummary.workspace + AuthUser.pinned_workspace + client API

listAllScans() 跨 ws 聚合 + setPinnedWorkspace(ws) 置顶。IA 重设计 §3/§2.3。"
```

---

## Task 5: 前端 — `ScanFilters` 组件 + `useScanFilters` hook

**Files:**
- Create: `packages/web/frontend/src/components/ScanFilters.tsx`
- Test: `packages/web/frontend/src/components/ScanFilters.test.tsx`

**Interfaces:**
- Produces: `<ScanFilters value={filters} onChange={setFilters} />` 受控组件；`useScanFilters(scans)` hook 返 `{ filters, setFilters, filtered }`。Task 6（ScanList）与 Task 7（Dashboard）复用。

- [ ] **Step 1: 写失败测试 — 四维筛选 + hook 过滤逻辑**

新建 `packages/web/frontend/src/components/ScanFilters.test.tsx`：

```typescript
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ScanFilters, useScanFilters, type ScanFiltersValue } from "./ScanFilters";
import type { ScanSummary } from "@/api/types";

const scans: ScanSummary[] = [
  { scan_id: "s1", scan_type: "whitebox", status: "running", created_at: 100, vuln_count: 1, is_running: true, workspace: "ws-a" },
  { scan_id: "s2", scan_type: "blackbox", status: "completed", created_at: 200, vuln_count: 2, is_running: false, workspace: "ws-b" },
  { scan_id: "s3", scan_type: "whitebox", status: "failed", created_at: 300, vuln_count: 3, is_running: false, workspace: "ws-a" },
];

describe("ScanFilters", () => {
  it("renders four filter controls", () => {
    const v: ScanFiltersValue = { status: "all", type: "all", keyword: "", time: "all" };
    render(<ScanFilters value={v} onChange={() => {}} />);
    expect(screen.getByPlaceholderText(/搜索/)).toBeInTheDocument();
    expect(screen.getAllByRole("combobox").length).toBeGreaterThanOrEqual(3); // status/type/time
  });

  it("keyword input calls onChange", () => {
    let v: ScanFiltersValue = { status: "all", type: "all", keyword: "", time: "all" };
    render(<ScanFilters value={v} onChange={(nv) => (v = nv)} />);
    fireEvent.change(screen.getByPlaceholderText(/搜索/), { target: { value: "ws-a" } });
    expect(v.keyword).toBe("ws-a");
  });
});

describe("useScanFilters", () => {
  function run(scans: ScanSummary[], v: ScanFiltersValue) {
    // 直接调 hook via a tiny harness
    let result: ScanSummary[] = [];
    function Harness() {
      const { filtered } = useScanFilters(scans, v);
      result = filtered;
      return null;
    }
    render(<Harness />);
    return result;
  }

  it("status filter", () => {
    expect(run(scans, { status: "running", type: "all", keyword: "", time: "all" }).length).toBe(1);
  });

  it("type filter", () => {
    expect(run(scans, { status: "all", type: "whitebox", keyword: "", time: "all" }).length).toBe(2);
  });

  it("keyword filter matches scan_id or workspace", () => {
    expect(run(scans, { status: "all", type: "all", keyword: "ws-a", time: "all" }).length).toBe(2);
    expect(run(scans, { status: "all", type: "all", keyword: "s2", time: "all" }).length).toBe(1);
  });

  it("returns all when all=all + empty keyword", () => {
    expect(run(scans, { status: "all", type: "all", keyword: "", time: "all" }).length).toBe(3);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/components/ScanFilters.test.tsx`
Expected: FAIL — 模块不存在。

- [ ] **Step 3: 写 `ScanFilters.tsx` 实现**

新建 `packages/web/frontend/src/components/ScanFilters.tsx`：

```typescript
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { ScanSummary } from "@/api/types";

export interface ScanFiltersValue {
  status: string;   // all | running | completed | failed | killed | crashed | interrupted
  type: string;     // all | whitebox | blackbox | correlation
  keyword: string;
  time: string;     // all | today | 7d | 30d
}

export const DEFAULT_SCAN_FILTERS: ScanFiltersValue = { status: "all", type: "all", keyword: "", time: "all" };

export function ScanFilters({ value, onChange }: { value: ScanFiltersValue; onChange: (v: ScanFiltersValue) => void }) {
  const { t } = useTranslation();
  const set = (patch: Partial<ScanFiltersValue>) => onChange({ ...value, ...patch });
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Input
        placeholder={t("scanFilters.keyword")}
        value={value.keyword}
        onChange={(e) => set({ keyword: e.target.value })}
        className="max-w-xs"
      />
      <Select value={value.status} onValueChange={(v) => set({ status: v })}>
        <SelectTrigger aria-label={t("scanFilters.status")} className="w-32"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">{t("workspaces.filter.allStatus")}</SelectItem>
          <SelectItem value="running">{t("workspaces.status.running")}</SelectItem>
          <SelectItem value="completed">{t("workspaces.status.completed")}</SelectItem>
          <SelectItem value="failed">{t("workspaces.status.failed")}</SelectItem>
          <SelectItem value="killed">{t("workspaces.status.killed")}</SelectItem>
          <SelectItem value="crashed">{t("workspaces.status.crashed")}</SelectItem>
          <SelectItem value="interrupted">{t("workspaces.status.interrupted")}</SelectItem>
        </SelectContent>
      </Select>
      <Select value={value.type} onValueChange={(v) => set({ type: v })}>
        <SelectTrigger aria-label={t("scanFilters.type")} className="w-32"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">{t("workspaces.filter.allType")}</SelectItem>
          <SelectItem value="whitebox">{t("workspaces.filter.whitebox")}</SelectItem>
          <SelectItem value="blackbox">{t("workspaces.filter.blackbox")}</SelectItem>
          <SelectItem value="correlation">{t("workspaces.filter.correlation")}</SelectItem>
        </SelectContent>
      </Select>
      <Select value={value.time} onValueChange={(v) => set({ time: v })}>
        <SelectTrigger aria-label={t("scanFilters.time")} className="w-32"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">{t("scanFilters.time.all")}</SelectItem>
          <SelectItem value="today">{t("scanFilters.time.today")}</SelectItem>
          <SelectItem value="7d">{t("scanFilters.time.7d")}</SelectItem>
          <SelectItem value="30d">{t("scanFilters.time.30d")}</SelectItem>
        </SelectContent>
      </Select>
    </div>
  );
}

function inTimeWindow(unix: number, window: string): boolean {
  if (window === "all") return true;
  const now = Date.now() / 1000;
  const day = 86400;
  if (window === "today") {
    const d = new Date(unix * 1000), n = new Date();
    return d.toDateString() === n.toDateString();
  }
  if (window === "7d") return unix >= now - 7 * day;
  if (window === "30d") return unix >= now - 30 * day;
  return true;
}

export function useScanFilters(scans: ScanSummary[], value: ScanFiltersValue) {
  const filtered = scans.filter((s) => {
    if (value.status !== "all" && s.status !== value.status) return false;
    if (value.type !== "all" && s.scan_type !== value.type) return false;
    if (value.time !== "all" && !inTimeWindow(s.created_at, value.time)) return false;
    if (value.keyword.trim()) {
      const q = value.keyword.toLowerCase();
      const hay = `${s.scan_id} ${s.workspace ?? ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  return { filters: value, setFilters: () => {}, filtered };
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/components/ScanFilters.test.tsx`
Expected: PASS。

- [ ] **Step 5: i18n 加键 — scanFilters**

`packages/web/frontend/src/locales/zh.json` 顶层加：

```json
  "scanFilters": {
    "status": "状态筛选",
    "type": "类型筛选",
    "keyword": "搜索 scan_id 或工作区名…",
    "time": "时间筛选",
    "time": { "all": "全部时间", "today": "今天", "7d": "近7天", "30d": "近30天" }
  },
```

`packages/web/frontend/src/locales/en.json` 同结构英文：

```json
  "scanFilters": {
    "status": "Status",
    "type": "Type",
    "keyword": "Search scan_id or workspace…",
    "time": "Time",
    "time": { "all": "All time", "today": "Today", "7d": "Last 7d", "30d": "Last 30d" }
  },
```

注：JSON 不允许同层重名 key，`time` 既是 string 又是 object 会冲突。修正：把 string 项改名为 `timeLabel`，组件里 `t("scanFilters.timeLabel")`；object 项保 `time`。同步修 Step 3 实现（`aria-label={t("scanFilters.timeLabel")}`）。在 Step 3 实现里把 `t("scanFilters.time")` 改为 `t("scanFilters.timeLabel")`，JSON 用 `timeLabel` + `time` object。

- [ ] **Step 6: 修正实现 timeLabel + 重跑测试**

把 `ScanFilters.tsx` 里时间 Select 的 `aria-label={t("scanFilters.time")}` 改为 `aria-label={t("scanFilters.timeLabel")}`。zh/en JSON 的 `scanFilters` 段用 `"timeLabel": "..."` + `"time": {...}`。

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/components/ScanFilters.test.tsx`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/components/ScanFilters.tsx packages/web/frontend/src/components/ScanFilters.test.tsx packages/web/frontend/src/locales/zh.json packages/web/frontend/src/locales/en.json
git commit -m "feat(web-fe): ScanFilters 组件 + useScanFilters hook

四维筛选(状态/类型/关键字/时间)受控组件,ScanList 与 Dashboard 复用。
IA 重设计 §4。"
```

---

## Task 6: 前端 — ScanList 接入 ScanFilters

**Files:**
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/ScanList.tsx`

**Interfaces:**
- Consumes: `ScanFilters` + `useScanFilters`（Task 5）。

- [ ] **Step 1: 改 ScanList — 加筛选条 + 过滤层**

`packages/web/frontend/src/routes/WorkspaceDetail/ScanList.tsx`：

1a. 顶部 import 加：

```typescript
import { ScanFilters, DEFAULT_SCAN_FILTERS, useScanFilters } from "@/components/ScanFilters";
```

1b. `ScanList` 组件内，`const [err, setErr]` 后加筛选 state：

```typescript
  const [filters, setFilters] = useState(DEFAULT_SCAN_FILTERS);
```

（确保 `useState` 已 import）

1c. 在 `if (err) return ...` 之后、`return (` 之前，加过滤层：

```typescript
  const { filtered } = useScanFilters(scans, filters);
```

1d. 在 JSX 的 `<div className="space-y-3">` 内、标题行 `<div className="flex items-center justify-between">...</div>` 之后，加：

```tsx
        <ScanFilters value={filters} onChange={setFilters} />
```

1e. 把渲染卡片处的 `scans.map((s) =>` 改为 `filtered.map((s) =>`，并把 `scans.length === 0` 空态判断改为 `filtered.length === 0 && scans.length > 0 ? (<div>无匹配扫描</div>) : scans.length === 0 ? (<Empty.../>) :`。最小改动：把

```tsx
      ) : scans.length === 0 ? (
        <Empty ...>...</Empty>
      ) : (
        scans.map((s) => (
          <ScanCard ... />
        ))
      )
```

改为：

```tsx
      ) : scans.length === 0 ? (
        <Empty title={t("workspaceDetail.scans.empty")} hint={t("workspaceDetail.scans.emptyHint")}>
          {workspace && (
            <Button asChild>
              <Link to={`/scan/new?workspace=${encodeURIComponent(workspace)}`}>
                {t("workspaceDetail.scans.newScan")}
              </Link>
            </Button>
          )}
        </Empty>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("workspaceDetail.scans.noMatch")}</p>
      ) : (
        filtered.map((s) => (
          <ScanCard key={s.scan_id} ws={workspace!} scan={s} onChanged={load} />
        ))
      )
```

- [ ] **Step 2: i18n 加 noMatch 键**

`zh.json` 的 `workspaceDetail.scans` 段加 `"noMatch": "无匹配的扫描任务"`；`en.json` 加 `"noMatch": "No matching scans"`。

- [ ] **Step 3: 运行现有 ScanList 测试确认不破**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/routes/WorkspaceDetail/ScanList.test.tsx`
Expected: PASS（现有测试不应被筛选层破坏——默认 filters 全 all，filtered=scans）。

- [ ] **Step 4: tsc 检查**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 零错误。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/routes/WorkspaceDetail/ScanList.tsx packages/web/frontend/src/locales/zh.json packages/web/frontend/src/locales/en.json
git commit -m "feat(web-fe): ScanList 接入 ScanFilters 四维筛选

工作区详情页扫描列表加状态/类型/关键字/时间筛选 + 无匹配空态。
IA 重设计 §4.3。"
```

---

## Task 7: 前端 — DashboardPage 重写为扫描任务表

**Files:**
- Modify: `packages/web/frontend/src/pages/DashboardPage.tsx`
- Test: `packages/web/frontend/src/pages/DashboardPage.test.tsx`

**Interfaces:**
- Consumes: `listAllScans()`（Task 4）；`ScanFilters` + `useScanFilters`（Task 5）。

- [ ] **Step 1: 写失败测试 — 扫描表渲染 + 归属工作区列 + 筛选**

改写 `packages/web/frontend/src/pages/DashboardPage.test.tsx`（覆盖现有工作区墙测试）：

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { DashboardPage } from "./DashboardPage";

vi.mock("@/api/client", () => ({
  listAllScans: vi.fn(),
}));

const mockScans = [
  { scan_id: "s1", scan_type: "whitebox", status: "running", created_at: 100, vuln_count: 1, is_running: true, workspace: "ws-a", total_cost_usd: 0.1 },
  { scan_id: "s2", scan_type: "blackbox", status: "completed", created_at: 200, vuln_count: 2, is_running: false, workspace: "ws-b", total_cost_usd: 0.2 },
];

function renderPage() {
  return render(<MemoryRouter><DashboardPage /></MemoryRouter>);
}

describe("DashboardPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders scan table with workspace column", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue(mockScans);
    renderPage();
    await waitFor(() => expect(screen.getByText("s1")).toBeInTheDocument());
    expect(screen.getByText("ws-a")).toBeInTheDocument();
    expect(screen.getByText("ws-b")).toBeInTheDocument();
  });

  it("status filter narrows results", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue(mockScans);
    renderPage();
    await waitFor(() => expect(screen.getByText("s1")).toBeInTheDocument());
    // 选 completed（s2）
    const statusSelects = screen.getAllByRole("combobox");
    fireEvent.mouseDown(statusSelects[1]); // 第二个 = status
    // 选 completed 选项（用文本）
    const opt = await screen.findByText("已完成", undefined, { timeout: 1000 });
    fireEvent.click(opt);
    await waitFor(() => {
      expect(screen.queryByText("s1")).not.toBeInTheDocument();
      expect(screen.getByText("s2")).toBeInTheDocument();
    });
  });

  it("empty state when no scans", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(screen.getByText(/还没有扫描|新建扫描/)).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/pages/DashboardPage.test.tsx`
Expected: FAIL — 现有 DashboardPage 用 `useWorkspaces`，无 `listAllScans` mock，渲染不匹配。

- [ ] **Step 3: 重写 `DashboardPage.tsx`**

```typescript
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/StatusBadge";
import { PageHeader } from "@/components/PageHeader";
import { StatRow } from "@/components/StatRow";
import { Badge } from "@/components/ui/badge";
import { Empty } from "@/components/Empty";
import { ScanFilters, DEFAULT_SCAN_FILTERS, useScanFilters } from "@/components/ScanFilters";
import { listAllScans } from "@/api/client";
import type { ScanSummary } from "@/api/types";
import { fmtCost } from "@/utils/currency";
import { useAsync } from "@/lib/useAsync";

function isToday(unix: number | null | undefined): boolean {
  if (!unix) return false;
  const d = new Date(unix * 1000), now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
}
function fmtTime(unix?: number | null): string {
  if (!unix) return "-";
  return new Date(unix * 1000).toLocaleString();
}

export function DashboardPage() {
  const { t } = useTranslation();
  const { data, loading, error, refresh } = useAsync(listAllScans, []);
  const [filters, setFilters] = useState(DEFAULT_SCAN_FILTERS);
  const { filtered } = useScanFilters(data, filters);

  const running = filtered.filter((s) => s.is_running || s.status === "running");
  const completedToday = filtered.filter((s) => s.status === "completed" && isToday(s.completed_at));
  const totalVulns = filtered.reduce((a, s) => a + (s.vuln_count ?? 0), 0);
  const totalCost = filtered.reduce((a, s) => a + (s.total_cost_usd ?? 0), 0);
  const currency = filtered.find((s) => s.cost_currency)?.cost_currency;

  if (loading && data.length === 0) {
    return <div className="space-y-2">{[0,1,2,3,4].map((i) => <Skeleton key={i} className="h-8 w-full" />)}</div>;
  }
  if (data.length === 0) {
    return (
      <Empty title={t("dashboard.empty.title")} hint={t("dashboard.empty.hint")}>
        <Link to="/scan/new"><Button>{t("dashboard.newScan")}</Button></Link>
      </Empty>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader title={t("dashboard.title")} subtitle={t("dashboard.subtitle")}
        action={<Link to="/scan/new"><Button>{t("dashboard.newScan")}</Button></Link>} />
      <StatRow stats={[
        { label: t("dashboard.stats.running"), value: running.length, tone: "cyan" },
        { label: t("dashboard.stats.completedToday"), value: completedToday.length, tone: "green" },
        { label: t("dashboard.stats.totalVulns"), value: totalVulns },
        { label: t("dashboard.stats.totalCost"), value: fmtCost(totalCost, currency) },
      ]} />

      {running.length > 0 && (
        <section className="space-y-2">
          <h2 className="font-semibold tracking-tight text-lg text-muted-foreground">{t("dashboard.runningTitle")}</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {running.map((s) => (
              <Link key={s.scan_id} to={`/p/${s.workspace}/scans/${s.scan_id}/live`} className="block">
                <Card className="transition-colors hover:border-primary">
                  <CardContent className="space-y-1 p-4 font-mono text-sm">
                    <div className="flex items-center justify-between">
                      <StatusBadge status={s.status} />
                      <Badge variant="outline">{s.scan_type}</Badge>
                    </div>
                    <div className="text-base text-foreground">{s.scan_id}</div>
                    <div className="text-xs text-muted-foreground">{t("dashboard.scanTable.workspace")}: {s.workspace}</div>
                    <div className="text-xs text-primary">{t("dashboard.viewLive")}</div>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        </section>
      )}

      <ScanFilters value={filters} onChange={setFilters} />

      <Card className="overflow-hidden p-0">
        <table className="w-full text-sm">
          <thead className="border-b bg-muted/30">
            <tr className="text-left">
              <th className="p-2">{t("dashboard.scanTable.status")}</th>
              <th className="p-2">{t("dashboard.scanTable.scanId")}</th>
              <th className="p-2">{t("dashboard.scanTable.workspace")}</th>
              <th className="p-2">{t("dashboard.scanTable.type")}</th>
              <th className="p-2">{t("dashboard.scanTable.vulns")}</th>
              <th className="p-2">{t("dashboard.scanTable.cost")}</th>
              <th className="p-2">{t("dashboard.scanTable.time")}</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((s) => (
              <tr key={s.scan_id} className="border-b last:border-0 hover:bg-accent">
                <td className="p-2"><StatusBadge status={s.status} /></td>
                <td className="p-2 font-mono"><Link to={`/p/${s.workspace}/scans/${s.scan_id}`} className="hover:text-primary">{s.scan_id}</Link></td>
                <td className="p-2 font-mono"><Link to={`/p/${s.workspace}`} className="hover:text-primary">{s.workspace}</Link></td>
                <td className="p-2"><Badge variant="outline">{s.scan_type}</Badge></td>
                <td className="p-2">{s.vuln_count ?? 0}</td>
                <td className="p-2">{s.total_cost_usd != null ? fmtCost(s.total_cost_usd, s.cost_currency) : "-"}</td>
                <td className="p-2 text-xs text-muted-foreground">{fmtTime(s.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {filtered.length === 0 && <p className="text-sm text-muted-foreground">{t("workspaceDetail.scans.noMatch")}</p>}
    </div>
  );
}
```

注：`useAsync` hook 若不存在则需新建 `lib/useAsync.ts`（见 Step 4）；若项目已有等价（如 `useWorkspaces` 的模式），优先复用。实现期先 `grep "useAsync\|useEffect.*setState.*fetch"` 确认；若无则 Step 4 建。

- [ ] **Step 4: 若需新建 `lib/useAsync.ts`**

新建 `packages/web/frontend/src/lib/useAsync.ts`：

```typescript
import { useCallback, useEffect, useState } from "react";

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T>([] as unknown as T);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try { const r = await fn(); setData(r); setError(null); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  }, deps);
  useEffect(() => { refresh(); }, [refresh]);
  return { data, loading, error, refresh };
}
```

- [ ] **Step 5: i18n 加 dashboard.scanTable 键**

`zh.json` 的 `dashboard` 段加：

```json
  "scanTable": {
    "status": "状态", "scanId": "扫描", "workspace": "工作区",
    "type": "类型", "vulns": "漏洞", "cost": "花费", "time": "时间"
  },
  "noWorkspace": { "title": "尚未分配工作区", "hint": "请联系管理员为你分配工作区" }
```

`en.json` 同结构英文。

- [ ] **Step 6: 运行测试确认通过**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/pages/DashboardPage.test.tsx`
Expected: PASS。

- [ ] **Step 7: tsc 检查**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 零错误。

- [ ] **Step 8: Commit**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/pages/DashboardPage.tsx packages/web/frontend/src/pages/DashboardPage.test.tsx packages/web/frontend/src/lib/useAsync.ts packages/web/frontend/src/locales/zh.json packages/web/frontend/src/locales/en.json
git commit -m "feat(web-fe): DashboardPage 重写为扫描任务表

跨 ws 扫描(标归属工作区列)+四维筛选+running 卡片置顶;数据来自 GET /api/scans。
IA 重设计 §3。"
```

---

## Task 8: 前端 — `WorkspaceSwitcher` 抽屉组件

**Files:**
- Create: `packages/web/frontend/src/components/WorkspaceSwitcher.tsx`
- Test: `packages/web/frontend/src/components/WorkspaceSwitcher.test.tsx`

**Interfaces:**
- Consumes: `useWorkspaces()`（现有）；`useAuth()`（现有，取 pinned + role）；`CreateWorkspaceDialog`（现有）。
- Produces: `<WorkspaceSwitcher trigger={<Button>切换</Button>} />` 自带触发 + 抽屉。Task 9/10 放入 header。

- [ ] **Step 1: 写失败测试 — 列表/高亮/搜索/切换/新建入口**

新建 `packages/web/frontend/src/components/WorkspaceSwitcher.test.tsx`：

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";

vi.mock("@/api/useWorkspaces", () => ({
  useWorkspaces: () => ({
    data: [
      { name: "ws-a", status: "running", scan_type: "whitebox", created_at: 1, scan_count: 2 },
      { name: "ws-b", status: "completed", scan_type: "blackbox", created_at: 2, scan_count: 1 },
    ],
    loading: false, lastUpdated: new Date(), error: null, refresh: vi.fn(),
  }),
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 1, username: "admin", role: "admin", must_change_password: false, pinned_workspace: "ws-a" } }),
}));

vi.mock("@/components/CreateWorkspaceDialog", () => ({
  CreateWorkspaceDialog: () => <div data-testid="create-ws-dialog" />,
}));

function renderIt(currentWs = "ws-a") {
  return render(<MemoryRouter><WorkspaceSwitcher currentWorkspace={currentWs} /></MemoryRouter>);
}

describe("WorkspaceSwitcher", () => {
  it("opens drawer on trigger click and lists workspaces", async () => {
    renderIt();
    fireEvent.click(screen.getByRole("button", { name: /切换/i }));
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    expect(screen.getByText("ws-b")).toBeInTheDocument();
  });

  it("highlights current workspace", async () => {
    renderIt("ws-a");
    fireEvent.click(screen.getByRole("button", { name: /切换/i }));
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    expect(screen.getByText("ws-a").closest("[data-current]")).toHaveAttribute("data-current", "true");
  });

  it("search filters list", async () => {
    renderIt();
    fireEvent.click(screen.getByRole("button", { name: /切换/i }));
    fireEvent.change(screen.getByPlaceholderText(/搜索/), { target: { value: "ws-b" } });
    expect(screen.queryByText("ws-a")).not.toBeInTheDocument();
    expect(screen.getByText("ws-b")).toBeInTheDocument();
  });

  it("shows create-workspace entry for admin", async () => {
    renderIt();
    fireEvent.click(screen.getByRole("button", { name: /切换/i }));
    await waitFor(() => expect(screen.getByTestId("create-ws-dialog")).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/components/WorkspaceSwitcher.test.tsx`
Expected: FAIL — 模块不存在。

- [ ] **Step 3: 写 `WorkspaceSwitcher.tsx` 实现**

新建 `packages/web/frontend/src/components/WorkspaceSwitcher.tsx`：

```typescript
import { useState, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { ArrowLeftRight, X, Pin } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useWorkspaces } from "@/api/useWorkspaces";
import { useAuth } from "@/auth/AuthContext";
import { CreateWorkspaceDialog } from "@/components/CreateWorkspaceDialog";

const STATUS_COLOR: Record<string, string> = {
  running: "bg-cyan", completed: "bg-green", done: "bg-green",
  failed: "bg-red", killed: "bg-red", crashed: "bg-yellow",
};
const statusColor = (s: string) => STATUS_COLOR[s] ?? "bg-yellow";

export function WorkspaceSwitcher({ currentWorkspace }: { currentWorkspace?: string }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const { data } = useWorkspaces();
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const isAdmin = user?.role === "admin";
  const pinned = user?.pinned_workspace ?? null;

  const list = useMemo(() => {
    if (!q.trim()) return data;
    const s = q.toLowerCase();
    return data.filter((w) => w.name.toLowerCase().includes(s));
  }, [data, q]);

  function pick(name: string) {
    setOpen(false);
    setQ("");
    nav(`/p/${name}`);
  }

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)} aria-label={t("workspaceSwitcher.title")}>
        <ArrowLeftRight className="size-4" /> {t("workspaceSwitcher.title")}
      </Button>
      <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) setQ(""); }}>
        <DialogContent className="left-0 top-0 h-screen max-w-sm translate-x-0 translate-y-0 rounded-l-none rounded-r-2xl sm:left-0">
          <DialogHeader>
            <DialogTitle className="flex items-center justify-between">
              {t("workspaceSwitcher.title")}
              <button onClick={() => setOpen(false)} aria-label="close"><X className="size-4" /></button>
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Input placeholder={t("workspaceSwitcher.search")} value={q} onChange={(e) => setQ(e.target.value)} />
            <div className="max-h-[60vh] space-y-1 overflow-y-auto">
              {list.length === 0 && <p className="text-sm text-muted-foreground">{t("workspaceSwitcher.empty")}</p>}
              {list.map((w) => (
                <button
                  key={w.name}
                  data-current={w.name === currentWorkspace}
                  onClick={() => pick(w.name)}
                  className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-accent ${
                    w.name === currentWorkspace ? "bg-accent" : ""
                  }`}
                >
                  <span className={`inline-block size-2 rounded-full ${statusColor(w.status)}`} />
                  <span className="flex-1 font-mono">{w.name}</span>
                  {w.scan_count != null && <span className="text-xs text-muted-foreground">{w.scan_count}</span>}
                  {pinned === w.name && <Pin className="size-3 text-primary" />}
                </button>
              ))}
            </div>
            {isAdmin && (
              <div className="border-t pt-2">
                <CreateWorkspaceDialog onCreated={() => {}} />
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
```

注：用 shadcn `Dialog` + 居左定位类实现抽屉（库无 sheet 组件，spec §5.2 已预留）。`DialogContent` 的定位类覆盖默认居中。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/components/WorkspaceSwitcher.test.tsx`
Expected: PASS。

- [ ] **Step 5: i18n 加 workspaceSwitcher 键**

`zh.json` 顶层加：

```json
  "workspaceSwitcher": {
    "title": "切换工作区",
    "search": "搜索工作区…",
    "empty": "无工作区",
    "newWorkspace": "+ 新建工作区"
  },
```

`en.json`：

```json
  "workspaceSwitcher": {
    "title": "Switch workspace",
    "search": "Search workspace…",
    "empty": "No workspaces",
    "newWorkspace": "+ New workspace"
  },
```

- [ ] **Step 6: tsc 检查**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 零错误。

- [ ] **Step 7: Commit**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/components/WorkspaceSwitcher.tsx packages/web/frontend/src/components/WorkspaceSwitcher.test.tsx packages/web/frontend/src/locales/zh.json packages/web/frontend/src/locales/en.json
git commit -m "feat(web-fe): WorkspaceSwitcher 侧边抽屉切换器

左侧滑出 dialog,列可见 ws(状态点+扫描数+置顶标),当前 ws 高亮,搜索,admin 见新建入口。
IA 重设计 §5.2。"
```

---

## Task 9: 前端 — WorkspaceDetail header 加置顶按钮 + 切换器

**Files:**
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/index.tsx`

**Interfaces:**
- Consumes: `WorkspaceSwitcher`（Task 8）；`setPinnedWorkspace`（Task 4）；`useAuth`。

- [ ] **Step 1: 改 `routes/WorkspaceDetail/index.tsx`**

1a. import 加：

```typescript
import { Pin } from "lucide-react";
import { WorkspaceSwitcher } from "@/components/WorkspaceSwitcher";
import { setPinnedWorkspace } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { toast } from "sonner";
```

1b. `WorkspaceDetail` 组件内（`const status = ...` 前）加：

```typescript
  const { user, refreshUser } = useAuth();
  const isPinned = user?.pinned_workspace === workspace;

  async function onPin() {
    if (!workspace) return;
    try {
      await setPinnedWorkspace(workspace);
      await refreshUser();
      toast.success(t("workspaceDetail.pinPinned"));
    } catch (e) {
      toast.error(t("workspaceDetail.pinFailed", { error: e instanceof Error ? e.message : String(e) }));
    }
  }
```

1c. 在 header 的 `<div className="flex flex-wrap items-center gap-3">` 内，`<h2>{workspace}</h2>` 之后，加置顶按钮 + 切换器：

```tsx
          <Button variant={isPinned ? "secondary" : "outline"} size="icon" onClick={onPin} title={t(isPinned ? "workspaceDetail.unpin" : "workspaceDetail.pin")}>
            <Pin className="size-4" />
          </Button>
          <WorkspaceSwitcher currentWorkspace={workspace} />
```

- [ ] **Step 2: i18n 加 pin 键**

`zh.json` 的 `workspaceDetail` 段加：

```json
    "pin": "置顶", "unpin": "取消置顶", "pinPinned": "已置顶", "pinFailed": "置顶失败：{{error}}",
```

`en.json`：

```json
    "pin": "Pin", "unpin": "Unpin", "pinPinned": "Pinned", "pinFailed": "Pin failed: {{error}}",
```

- [ ] **Step 3: 运行 WorkspaceDetail 测试确认不破**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/routes/WorkspaceDetail/index.test.tsx`
Expected: PASS（新增按钮不破坏既有断言；若 mock 未提供 `useAuth` user，组件应 null-safe——`user?.pinned_workspace` 已守卫）。

- [ ] **Step 4: tsc 检查**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 零错误。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/routes/WorkspaceDetail/index.tsx packages/web/frontend/src/locales/zh.json packages/web/frontend/src/locales/en.json
git commit -m "feat(web-fe): WorkspaceDetail header 加置顶按钮 + 切换器入口

📌 置顶(per-user)+ 切换工作区抽屉入口。IA 重设计 §5.1。"
```

---

## Task 10: 前端 — ScanDetail header 加切换器入口

**Files:**
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/ScanDetail.tsx`

- [ ] **Step 1: 改 `routes/WorkspaceDetail/ScanDetail.tsx`**

import 加：

```typescript
import { WorkspaceSwitcher } from "@/components/WorkspaceSwitcher";
```

在 header 的 `<div className="flex flex-wrap items-center gap-3">` 内、`<h2>{scanId}</h2>` 之后，加：

```tsx
          <WorkspaceSwitcher currentWorkspace={workspace} />
```

- [ ] **Step 2: 运行 ScanDetail 测试确认不破**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/routes/WorkspaceDetail/ScanDetail.test.tsx`
Expected: PASS（若测试因 `useWorkspaces`/`useAuth` mock 缺失报错，在测试文件顶部补 `vi.mock("@/api/useWorkspaces", ...)` + `vi.mock("@/auth/AuthContext", ...)` 同 Task 8 mock）。

- [ ] **Step 3: tsc 检查**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 零错误。

- [ ] **Step 4: Commit**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/routes/WorkspaceDetail/ScanDetail.tsx
git commit -m "feat(web-fe): ScanDetail header 加切换器入口

扫描详情页可直接切 ws。IA 重设计 §5。"
```

---

## Task 11: 前端 — `WorkspacesEntry` redirect 组件 + TopBar + 路由

**Files:**
- Create: `packages/web/frontend/src/components/WorkspacesEntry.tsx`
- Modify: `packages/web/frontend/src/components/layout/TopBar.tsx`
- Modify: `packages/web/frontend/src/router.tsx`
- Test: `packages/web/frontend/src/components/WorkspacesEntry.test.tsx`

**Interfaces:**
- Consumes: `useAuth`（pinned）；`useWorkspaces`（最近归属 ws）。
- Produces: `<WorkspacesEntry />` 三段跳转；顶栏「工作区」改用它；`/workspaces` 包 `RequireAdmin`；admin 可见「工作区管理」入口。

- [ ] **Step 1: 写失败测试 — 三段跳转**

新建 `packages/web/frontend/src/components/WorkspacesEntry.test.tsx`：

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { WorkspacesEntry } from "./WorkspacesEntry";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/" element={<div>home</div>} />
        <Route path="/p/:workspace" element={<div data-testid="ws-detail" />} />
        <Route path="/workspaces" element={<div data-testid="ws-list" />} />
        <Route path="*" element={<WorkspacesEntry />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("WorkspacesEntry", () => {
  it("redirects to pinned workspace when set", async () => {
    vi.doMock("@/auth/AuthContext", () => ({ useAuth: () => ({ user: { pinned_workspace: "ws-pinned" } }) }));
    vi.doMock("@/api/useWorkspaces", () => ({ useWorkspaces: () => ({ data: [], loading: false }) }));
    const { container } = renderAt("/entry");
    await waitFor(() => expect(container.querySelector("[data-testid='ws-detail']")).toBeInTheDocument());
  });

  it("redirects to most recent workspace when no pinned but has membership", async () => {
    vi.doMock("@/auth/AuthContext", () => ({ useAuth: () => ({ user: { pinned_workspace: null } }) }));
    vi.doMock("@/api/useWorkspaces", () => ({ useWorkspaces: () => ({
      data: [
        { name: "ws-old", status: "completed", created_at: 1, latest_created_at: 1, scan_type: "whitebox" },
        { name: "ws-new", status: "completed", created_at: 2, latest_created_at: 2, scan_type: "whitebox" },
      ], loading: false,
    }) }));
    const { container } = renderAt("/entry");
    await waitFor(() => expect(container.querySelector("[data-testid='ws-detail']")).toBeInTheDocument());
  });

  it("redirects to /workspaces when no membership", async () => {
    vi.doMock("@/auth/AuthContext", () => ({ useAuth: () => ({ user: { pinned_workspace: null } }) }));
    vi.doMock("@/api/useWorkspaces", () => ({ useWorkspaces: () => ({ data: [], loading: false }) }));
    const { container } = renderAt("/entry");
    await waitFor(() => expect(container.querySelector("[data-testid='ws-list']")).toBeInTheDocument());
  });
});
```

注：`vi.doMock` 需动态 import，实现期若 vitest 行为不符可改 `vi.mock` + 工厂按测试隔离（用 `beforeEach` 切换 mock 返回值）。测试骨架以表达意图为主。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/components/WorkspacesEntry.test.tsx`
Expected: FAIL — 模块不存在。

- [ ] **Step 3: 写 `WorkspacesEntry.tsx` 实现**

新建 `packages/web/frontend/src/components/WorkspacesEntry.tsx`：

```typescript
import { useEffect } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { useWorkspaces } from "@/api/useWorkspaces";

/**
 * 顶栏「工作区」入口的三段跳转（IA 重设计 §2.3）：
 * 1) pinned 存在 -> /p/:pinned
 * 2) 无 pinned 但有归属 ws -> /p/:最近活跃 ws（latest_created_at 倒序首项）
 * 3) 无归属 ws -> /workspaces 空态引导页
 */
export function WorkspacesEntry() {
  const { user } = useAuth();
  const { data, loading } = useWorkspaces();
  const nav = useNavigate();

  useEffect(() => {
    if (loading) return;
    const pinned = user?.pinned_workspace;
    if (pinned) { nav(`/p/${pinned}`, { replace: true }); return; }
    if (data.length > 0) {
      const recent = [...data].sort(
        (a, b) => (b.latest_created_at ?? b.created_at) - (a.latest_created_at ?? a.created_at),
      )[0];
      nav(`/p/${recent.name}`, { replace: true });
      return;
    }
    nav("/workspaces", { replace: true });
  }, [user?.pinned_workspace, data, loading, nav]);

  return null;
}
```

- [ ] **Step 4: 改 `router.tsx` — 顶栏「工作区」改跳 WorkspacesEntry + /workspaces 包 RequireAdmin**

4a. import 加：

```typescript
import { WorkspacesEntry } from "./components/WorkspacesEntry";
```

4b. 在 AppShell children 内，把 `{ path: "/workspaces", element: <WorkspaceListPage /> }` 改为：

```typescript
      { path: "/workspaces", element: <RequireAdmin><WorkspaceListPage /></RequireAdmin> },
```

4c. 新增 entry 路由（放 `/workspaces` 前）：

```typescript
      { path: "/workspaces-entry", element: <WorkspacesEntry /> },
```

- [ ] **Step 5: 改 `TopBar.tsx` — 「工作区」nav 改跳 entry；admin 加「工作区管理」**

5a. NAV 数组改：

```typescript
const NAV: NavItem[] = [
  { labelKey: "nav.dashboard", to: "/", end: true },
  { labelKey: "nav.workspaces", to: "/workspaces-entry", end: true },
  { labelKey: "nav.scan", to: "/scan/new" },
  { labelKey: "nav.settings", to: "/settings" },
];
```

5b. 在 NAV `.map(...)` 之后、`<div className="ml-auto ...">` 之前，admin 专属「工作区管理」入口：

```tsx
        {user?.role === "admin" && (
          <NavLink to="/workspaces" className="inline-flex">
            {({ isActive }) => (
              <span data-active={isActive} className={cn(
                "border-b-2 px-3 py-1.5 text-sm transition-colors",
                isActive ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"
              )}>{t("nav.workspaceManage")}</span>
            )}
          </NavLink>
        )}
```

- [ ] **Step 6: i18n 加 nav.workspaceManage 键**

`zh.json` 的 `nav` 段加 `"workspaceManage": "工作区管理"`；`en.json` 加 `"workspaceManage": "Workspaces"`。

- [ ] **Step 7: 运行测试 + WorkspacesEntry 测试**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/components/WorkspacesEntry.test.tsx src/components/layout/TopBar.test.tsx`
Expected: PASS（TopBar 现有测试可能断言 nav 数量/文本——若失败，更新 TopBar.test.tsx 适配新 entry 路径与 admin 入口）。

- [ ] **Step 8: tsc + router 测试**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/vitest run src/router.test.ts`
Expected: 零错误 + router 测试绿（若 router.test 断言 `/workspaces` 无 RequireAdmin，更新测试）。

- [ ] **Step 9: Commit**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/components/WorkspacesEntry.tsx packages/web/frontend/src/components/WorkspacesEntry.test.tsx packages/web/frontend/src/components/layout/TopBar.tsx packages/web/frontend/src/router.tsx packages/web/frontend/src/locales/zh.json packages/web/frontend/src/locales/en.json
git commit -m "feat(web-fe): 顶栏工作区改跳置顶 ws + 工作区管理页 admin 专属

WorkspacesEntry 三段跳转(pinned->最近->空态);/workspaces 包 RequireAdmin;
admin 顶栏见「工作区管理」。IA 重设计 §2.1/§2.2/§6。"
```

---

## Task 12: 前端 — WorkspaceListPage 去 StatRow 精简 + 最终联调

**Files:**
- Modify: `packages/web/frontend/src/pages/WorkspaceListPage.tsx`

- [ ] **Step 1: 去掉 StatRow 状态条**

`pages/WorkspaceListPage.tsx`：

1a. 删除 import `StatRow` / `StatItem`（若仅此处用）：

```typescript
// 删: import { StatRow, type StatItem } from "@/components/StatRow";
```

1b. 删除 `stats` useMemo 整块（约 125-137 行）。

1c. 删除 JSX 里的 `<StatRow stats={stats} />` 行。

- [ ] **Step 2: 运行 WorkspaceListPage 测试确认不破**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/pages/WorkspaceListPage.test.tsx`
Expected: PASS（若测试断言 StatRow 文本，更新测试去掉相关断言）。

- [ ] **Step 3: 全量前端测试 + tsc + build**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/vitest run`
Expected: 零类型错误 + 测试全绿（预存失败除外，见 memory `feat-fork-py-test-gotchas`）。

- [ ] **Step 4: 后端回归测试**

Run: `cd /root/shannon-py/packages/web && python -m pytest tests/test_auth_store.py tests/test_pinned_workspace.py tests/test_scans_cross_ws.py tests/test_workspace_permissions.py tests/test_members_routes.py tests/test_scan_decoupling_invariants.py -v`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/pages/WorkspaceListPage.tsx packages/web/frontend/src/pages/WorkspaceListPage.test.tsx
git commit -m "feat(web-fe): WorkspaceListPage 去 StatRow 精简为工作区管理表

admin 专属工作区管理页,去顶部状态条。IA 重设计 §6。"
```

- [ ] **Step 6: 更新 memory**

在 `/root/.claude/projects/-root-shannon-py/memory/` 新建 `web-workspace-scan-ia-redesign.md` 记录本次 IA 重设计落地状态（per-user 置顶 + 跨 ws GET /api/scans + 侧边抽屉切换 + 首页扫描表 + admin 专属管理页 + 四维筛选），并在 MEMORY.md 加一行指针。

---

## Self-Review 结果

**1. Spec 覆盖：**
- §2.1 顶栏导航 -> Task 11
- §2.2 页面职责 -> Task 7（首页）/ Task 11（管理页 admin）/ Task 9（详情页）/ Task 6（详情页 ScanList）
- §2.3 置顶 -> Task 1（列）/ Task 2（端点）/ Task 9（按钮）/ Task 11（跳转）
- §2.4 权限 -> Task 2/3（后端）/ Task 11（前端 RequireAdmin）
- §3 首页扫描视图 -> Task 3（数据源）/ Task 7（页面）
- §4 扫描筛选 -> Task 5（组件）/ Task 6（ScanList 接入）/ Task 7（首页接入）
- §5 详情页 + 抽屉 -> Task 8（抽屉）/ Task 9（详情入口）/ Task 10（ScanDetail 入口）
- §6 工作区管理页 -> Task 11（RequireAdmin）/ Task 12（去 StatRow）
- §7 后端端点 -> Task 1/2/3
- §8 i18n -> Task 5/7/8/9/11（各任务内联）
- §9 测试 -> 各任务 TDD
- §10 风险 -> Task 11（三段跳转集中）/ Task 12（回归）
无遗漏。

**2. 占位扫描：** Task 5 Step 5 的 JSON `time` 重名问题已在 Step 5/6 显式给出修正路径（timeLabel），非占位。Task 7 Step 3/4 的 `useAsync` 已给出完整实现。Task 11 测试用 `vi.doMock` 已注明实现期适配。无 TBD。

**3. 类型一致性：** `ScanFiltersValue` / `useScanFilters` 在 Task 5 定义、Task 6/7 消费一致；`WorkspaceSwitcher` props `currentWorkspace` 在 Task 8 定义、Task 9/10 消费一致；`setPinnedWorkspace` / `listAllScans` 在 Task 4 定义、Task 7/9 消费一致；`User.pinned_workspace` / `AuthUser.pinned_workspace` 在 Task 1/4 定义、Task 8/9/11 消费一致。`WorkspacesEntry` 路由路径 `/workspaces-entry` 在 Task 11 定义、TopBar 消费一致。
