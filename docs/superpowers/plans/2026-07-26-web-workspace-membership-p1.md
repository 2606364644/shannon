# Web workspace 成员制 P1 实现计划（成员制 + 产物隔离）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 workspace 加成员制（多对多 + manager/member 两级），admin 建 ws + 见所有、普通用户只见自己所属 workspace，产物 API 按成员鉴权，scan 在已有 ws 内跑（不建 ws）。

**Architecture:** P0 的 SQLite 加 `workspace_members` 表；`auth/store.py` 加成员 CRUD；`auth/dependencies.py` 加 `workspace_member`/`workspace_manager`；现有 workspace 产物路由（`api/workspaces.py`/`api/events.py`/`api/scan.py`）叠加成员依赖与过滤；新增成员管理路由 + 前端管理 dialog。

**Tech Stack:** Python（FastAPI、sqlite3、pydantic）；React 18 + shadcn(Radix) + vitest。**前置**：P0 必须已完成（`auth/` 模块、`current_user`、`app.state.auth_store`、`authed_client` fixture 可用）。

## Global Constraints

- **依赖 P0**：`auth.store.AuthStore`、`auth.dependencies.current_user`、`auth.models.User`、`app.state.auth_store`、conftest `authed_client` fixture 均由 P0 提供，直接复用。
- **范围严格 P1**：只做 workspace 成员制 + 产物隔离。**不碰** repos 隔离（P2）、配置隔离（P3c）。`/api/repos`、`/api/multi-configs` 在 P1 仍所有登录用户共享。
- **workspace 标识 = 目录名**（`workspace_name`），不引 workspace id；workspace 存在性仍由物理目录决定。
- **角色**：全局 `admin`（users.role）+ workspace 级 `manager`/`member`（workspace_members.role）。**ws 由 admin 创建**（`POST /api/workspaces`，`require_admin`）；scan 在已有 ws 内跑，不建 ws、不加 manager。
- **测试陷阱（同 P0）**：后端只跑改动测试文件；前端命令 `cd packages/web/frontend`；Radix 优先受控；i18n zh 值真中文。
- **TDD**：每 task 红→绿→commit。

## File Structure

**后端 Modify：**
- `packages/web/src/supernova_web/auth/store.py` — 加 `workspace_members` 表 + 成员 CRUD + `list_all_users`
- `packages/web/src/supernova_web/auth/dependencies.py` — 加 `workspace_member` / `workspace_manager`
- `packages/web/src/supernova_web/api/workspaces.py` — `list_workspaces` 按成员过滤；`get`/`deliverables*`/`report`/`logs` 加 `workspace_member`；`delete` 加 `workspace_manager` + 清成员；**加 `POST ""` admin 建 ws**
- `packages/web/src/supernova_web/api/scan.py` — `create_scan` 校验已有 ws + 成员（不建 ws/不加 manager）
- `packages/web/src/supernova_web/components/scan_manager.py` — `start`：ws 必须显式，去 `_gen_ws_name`
- `packages/web/src/supernova_web/api/events.py` — `stream_events` 加 `workspace_member`
- `packages/web/src/supernova_web/app.py` — include members/users router + legacy 迁移 startup

**后端 Create：**
- `packages/web/src/supernova_web/api/members.py` — `GET/POST/DELETE /api/workspaces/{ws}/members`
- `packages/web/src/supernova_web/api/users.py` — `GET /api/users`（成员分配 dialog 用）

**前端 Create：**
- `packages/web/frontend/src/components/MemberManagerDialog.tsx`
- `packages/web/frontend/src/api/members.ts` — 成员 API client

**前端 Modify：**
- `packages/web/frontend/src/locales/{zh,en}.json` — `members.*` 文案
- workspace 详情页头部 — 加「成员管理」入口（manager/admin 可见）

**Test（create）：** `test_workspace_members_store.py`、`test_workspace_filter.py`、`test_workspace_permissions.py`、`test_workspace_lifecycle.py`（admin 建 ws + scan 校验）、`test_members_routes.py`、`test_legacy_migration.py`、前端 `MemberManagerDialog.test.tsx`

---

## Task 1: workspace_members 存储层（store 扩展）

**Files:**
- Modify: `packages/web/src/supernova_web/auth/store.py`
- Test: `packages/web/tests/test_workspace_members_store.py`

**Interfaces:**
- Produces（AuthStore 新方法）：`add_workspace_member(ws, user_id, role="member")`、`remove_workspace_member(ws, user_id)`、`list_workspace_members(ws) -> list[tuple[int,str,str]]`（user_id, username, role，join users）、`list_user_workspaces(user_id) -> list[str]`、`get_workspace_member_role(ws, user_id) -> str|None`、`delete_workspace_members(ws) -> int`、`list_all_users() -> list[User]`

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_workspace_members_store.py
from supernova_web.auth.store import AuthStore


def _store(tmp_path):
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    s.create_user("admin", "h", role="admin")
    s.create_user("alice", "h")
    s.create_user("bob", "h")
    return s


def test_add_and_list_members(tmp_path):
    s = _store(tmp_path)
    s.add_workspace_member("ws1", 2, "manager")
    s.add_workspace_member("ws1", 3, "member")
    members = s.list_workspace_members("ws1")
    assert (2, "alice", "manager") in members
    assert (3, "bob", "member") in members
    assert s.list_workspace_members("ws2") == []


def test_get_role_and_list_user_workspaces(tmp_path):
    s = _store(tmp_path)
    s.add_workspace_member("ws1", 2, "manager")
    s.add_workspace_member("ws2", 2, "member")
    assert s.get_workspace_member_role("ws1", 2) == "manager"
    assert s.get_workspace_member_role("ws1", 3) is None
    assert set(s.list_user_workspaces(2)) == {"ws1", "ws2"}


def test_remove_and_delete(tmp_path):
    s = _store(tmp_path)
    s.add_workspace_member("ws1", 2, "manager")
    s.add_workspace_member("ws1", 3, "member")
    s.remove_workspace_member("ws1", 3)
    assert s.get_workspace_member_role("ws1", 3) is None
    assert s.delete_workspace_members("ws1") == 1
    assert s.list_workspace_members("ws1") == []


def test_list_all_users(tmp_path):
    s = _store(tmp_path)
    names = [u.username for u in s.list_all_users()]
    assert names == ["admin", "alice", "bob"]
```

- [ ] **Step 2: 验证失败** — Run: `uv run pytest packages/web/tests/test_workspace_members_store.py -v` → FAIL（方法不存在）

- [ ] **Step 3: 实现** — 在 `store.py` 的 `_SCHEMA` 字符串里（`schema_meta` 表之后）加：

```sql
CREATE TABLE IF NOT EXISTS workspace_members (
  workspace_name TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id),
  role TEXT NOT NULL DEFAULT 'member',
  created_at TEXT NOT NULL,
  PRIMARY KEY (workspace_name, user_id)
);
CREATE INDEX IF NOT EXISTS idx_wm_user ON workspace_members(user_id);
CREATE INDEX IF NOT EXISTS idx_wm_ws ON workspace_members(workspace_name);
```

在 `AuthStore` 类里加方法：

```python
    def add_workspace_member(self, ws_name: str, user_id: int, role: str = "member") -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO workspace_members(workspace_name, user_id, role, created_at) VALUES(?,?,?,?)",
                (ws_name, user_id, role, now),
            )

    def remove_workspace_member(self, ws_name: str, user_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM workspace_members WHERE workspace_name=? AND user_id=?", (ws_name, user_id))

    def list_workspace_members(self, ws_name: str) -> list[tuple[int, str, str]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT u.id, u.username, m.role FROM workspace_members m JOIN users u ON u.id=m.user_id WHERE m.workspace_name=?",
                (ws_name,),
            ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def list_user_workspaces(self, user_id: int) -> list[str]:
        with self._conn() as c:
            rows = c.execute("SELECT workspace_name FROM workspace_members WHERE user_id=?", (user_id,)).fetchall()
        return [r[0] for r in rows]

    def get_workspace_member_role(self, ws_name: str, user_id: int) -> str | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT role FROM workspace_members WHERE workspace_name=? AND user_id=?", (ws_name, user_id)
            ).fetchone()
        return row[0] if row else None

    def delete_workspace_members(self, ws_name: str) -> int:
        with self._conn() as c:
            cur = c.execute("DELETE FROM workspace_members WHERE workspace_name=?", (ws_name,))
            return cur.rowcount

    def list_all_users(self) -> list["User"]:
        with self._conn() as c:
            rows = c.execute("SELECT id, username, role FROM users ORDER BY id").fetchall()
        return [User(id=r[0], username=r[1], role=r[2]) for r in rows]
```

- [ ] **Step 4: 验证通过** — Run: `uv run pytest packages/web/tests/test_workspace_members_store.py -v` → 4 passed

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/auth/store.py packages/web/tests/test_workspace_members_store.py
git commit -m "feat(web/p1): workspace_members 存储层 (成员 CRUD + list_all_users)"
```

---

## Task 2: workspace_member / workspace_manager 依赖

**Files:**
- Modify: `packages/web/src/supernova_web/auth/dependencies.py`
- Test: `packages/web/tests/test_workspace_permissions.py`（依赖 + 过滤合测，见 Task 3/5 续用）

**Interfaces:**
- Produces：`workspace_member(request, ws, user=current_user) -> User`（admin 或该 ws 成员通过，否则 403）、`workspace_manager(request, ws, user=current_user) -> User`（admin 或该 ws manager 通过，否则 403）

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_workspace_permissions.py
import pytest
from fastapi import HTTPException, Request
from supernova_web.auth.dependencies import workspace_member, workspace_manager
from supernova_web.auth.models import User


class _FakeStore:
    def __init__(self, role_map): self._m = role_map  # {(ws,uid): role}
    def get_workspace_member_role(self, ws, uid): return self._m.get((ws, uid))


def _req(user, store):
    r = Request({"type": "http"}); r.state.user = user
    # 模拟 app.state
    class _App: pass
    r._app = _App(); r._app.state = type("S", (), {"auth_store": store})()
    r.app = r._app
    return r


def test_admin_passes_workspace_member():
    admin = User(id=1, username="admin", role="admin")
    assert workspace_member(_req(admin, _FakeStore({})), "ws1", admin).role == "admin"


def test_member_passes():
    alice = User(id=2, username="alice", role="user")
    store = _FakeStore({("ws1", 2): "member"})
    assert workspace_member(_req(alice, store), "ws1", alice).id == 2


def test_non_member_forbidden():
    alice = User(id=2, username="alice", role="user")
    with pytest.raises(HTTPException) as e:
        workspace_member(_req(alice, _FakeStore({})), "ws1", alice)
    assert e.value.status_code == 403


def test_member_not_manager():
    alice = User(id=2, username="alice", role="user")
    store = _FakeStore({("ws1", 2): "member"})
    with pytest.raises(HTTPException) as e:
        workspace_manager(_req(alice, store), "ws1", alice)
    assert e.value.status_code == 403


def test_manager_passes_workspace_manager():
    alice = User(id=2, username="alice", role="user")
    store = _FakeStore({("ws1", 2): "manager"})
    assert workspace_manager(_req(alice, store), "ws1", alice).id == 2
```

- [ ] **Step 2: 验证失败** — Run: `uv run pytest packages/web/tests/test_workspace_permissions.py -v` → FAIL（依赖未定义）

- [ ] **Step 3: 实现** — 在 `dependencies.py` 加：

```python
from fastapi import Request


def workspace_member(request: Request, ws: str, user: User = Depends(current_user)) -> User:
    if user.role == "admin":
        return user
    if request.app.state.auth_store.get_workspace_member_role(ws, user.id) is None:
        raise HTTPException(status_code=403, detail="not a workspace member")
    return user


def workspace_manager(request: Request, ws: str, user: User = Depends(current_user)) -> User:
    if user.role == "admin":
        return user
    if request.app.state.auth_store.get_workspace_member_role(ws, user.id) != "manager":
        raise HTTPException(status_code=403, detail="workspace manager required")
    return user
```

（`dependencies.py` 顶部若未 import `Depends`，加 `from fastapi import Depends`。）

- [ ] **Step 4: 验证通过** — Run: `uv run pytest packages/web/tests/test_workspace_permissions.py -v` → 5 passed

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/auth/dependencies.py packages/web/tests/test_workspace_permissions.py
git commit -m "feat(web/p1): workspace_member/workspace_manager 依赖"
```

---

## Task 3: GET /api/workspaces 按成员过滤

**Files:**
- Modify: `packages/web/src/supernova_web/api/workspaces.py:19-23`（`list_workspaces`）
- Test: `packages/web/tests/test_workspace_filter.py`

**Interfaces:**
- Consumes: T1 `list_user_workspaces`、P0 `current_user`
- 效果：admin 返回全部；普通用户只返回 `list_user_workspaces(user.id)` ∩ 目录存在

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_workspace_filter.py
import pytest
from starlette.testclient import TestClient
from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


@pytest.fixture
def _setup(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    app = create_app()
    st = app.state.auth_store
    st.create_user("admin", hash_password("p"), role="admin")
    st.create_user("alice", hash_password("p"))
    st.create_user("bob", hash_password("p"))
    # 建两个 workspace 目录（模拟已有产物目录）
    (app.state.config.workspaces_dir / "ws_alice").mkdir()
    (app.state.config.workspaces_dir / "ws_bob").mkdir()
    st.add_workspace_member("ws_alice", st.get_user_by_username("alice").id, "manager")
    st.add_workspace_member("ws_bob", st.get_user_by_username("bob").id, "manager")
    return app


def _login(app, username):
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": username, "password": "p"}, headers={"X-CSRF-Token": tok})
    return c


def test_alice_sees_only_her_workspace(_setup):
    c = _login(_setup, "alice")
    names = [w["name"] for w in c.get("/api/workspaces").json()]
    assert names == ["ws_alice"]


def test_admin_sees_all(_setup):
    c = _login(_setup, "admin")
    names = sorted(w["name"] for w in c.get("/api/workspaces").json())
    assert names == ["ws_alice", "ws_bob"]
```

- [ ] **Step 2: 验证失败** — Run: `uv run pytest packages/web/tests/test_workspace_filter.py -v` → FAIL（当前不过滤，alice 见全部）

- [ ] **Step 3: 实现** — 把 `workspaces.py` 的 `list_workspaces` 改为：

```python
from supernova_web.auth.dependencies import current_user
from supernova_web.auth.models import User
from fastapi import Depends


@router.get("")
async def list_workspaces(request: Request, user: User = Depends(current_user)):
    idx = request.app.state.indexer
    idx.sync_active(request.app.state.scan_manager.active_pids())
    all_ws = idx.list_workspaces()
    if user.role == "admin":
        return all_ws
    allowed = set(request.app.state.auth_store.list_user_workspaces(user.id))
    return [w for w in all_ws if w["name"] in allowed]
```

（顶部 import 加 `Depends`、`current_user`、`User`。）

- [ ] **Step 4: 验证通过** — Run: `uv run pytest packages/web/tests/test_workspace_filter.py -v` → 2 passed

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/api/workspaces.py packages/web/tests/test_workspace_filter.py
git commit -m "feat(web/p1): GET /api/workspaces 按成员过滤 (admin 全见/普通用户只己)"
```

---

## Task 4: admin 创建 workspace + scan 在已有 ws 内

> **模型调整（P2 brainstorm 2026-07-26）**：原"scan 创建者=manager"作废。改为 admin 显式建 ws（ws 先于 scan 存在，为 P2 repo 隔离铺路）；scan 在已有 ws 内跑，不建 ws、不加 manager（成员由 admin 预分配）。

**Files:**
- Modify: `packages/web/src/supernova_web/api/workspaces.py`（加 `POST ""` admin 建 ws）
- Modify: `packages/web/src/supernova_web/api/scan.py`（`create_scan` 校验已有 ws + 成员）
- Modify: `packages/web/src/supernova_web/components/scan_manager.py:88`（`start`：ws 必须显式，去 `_gen_ws_name`）
- Test: `packages/web/tests/test_workspace_lifecycle.py`（替代 `test_scan_creates_manager.py`）

**Interfaces:**
- Consumes: P0 `require_admin`/`current_user`、T1 `add_workspace_member`/`get_workspace_member_role`
- Produces: `POST /api/workspaces`（admin only，body `{name}` → 建空 ws 目录 + admin=manager）；`create_scan` 校验 `req.workspace` 已存在 + 当前用户成员/admin

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_workspace_lifecycle.py
import pytest
from starlette.testclient import TestClient
from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


@pytest.fixture
def _app(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    app = create_app()
    st = app.state.auth_store
    st.create_user("admin", hash_password("p"), role="admin")
    st.create_user("alice", hash_password("p"))
    return app


def _login(app, username):
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": username, "password": "p"}, headers={"X-CSRF-Token": tok})
    return c


def test_admin_creates_workspace(_app):
    c = _login(_app, "admin")
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    r = c.post("/api/workspaces", json={"name": "ws1"}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 201
    assert (_app.state.config.workspaces_dir / "ws1").is_dir()
    admin = _app.state.auth_store.get_user_by_username("admin")
    assert _app.state.auth_store.get_workspace_member_role("ws1", admin.id) == "manager"


def test_non_admin_cannot_create_workspace(_app):
    c = _login(_app, "alice")
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    assert c.post("/api/workspaces", json={"name": "ws2"}, headers={"X-CSRF-Token": tok}).status_code == 403


def test_create_workspace_conflict(_app):
    c = _login(_app, "admin")
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/workspaces", json={"name": "ws1"}, headers={"X-CSRF-Token": tok})
    assert c.post("/api/workspaces", json={"name": "ws1"}, headers={"X-CSRF-Token": tok}).status_code == 409


def test_scan_requires_existing_workspace(_app, monkeypatch):
    async def _fake_start(req):
        return req.workspace
    _app.state.scan_manager.start = _fake_start
    c = _login(_app, "alice")
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    r = c.post("/api/scan", json={"type": "whitebox", "workspace": "nope", "url": "http://x"},
               headers={"X-CSRF-Token": tok})
    assert r.status_code == 422  # ws 不存在


def test_scan_requires_membership(_app, monkeypatch):
    admin_c = _login(_app, "admin")
    atok = admin_c.get("/api/auth/csrf").json()["csrf_token"]
    admin_c.post("/api/workspaces", json={"name": "ws1"}, headers={"X-CSRF-Token": atok})  # admin 建 ws1
    async def _fake_start(req):
        return req.workspace
    _app.state.scan_manager.start = _fake_start
    alice_c = _login(_app, "alice")
    altok = alice_c.get("/api/auth/csrf").json()["csrf_token"]
    r = alice_c.post("/api/scan", json={"type": "whitebox", "workspace": "ws1", "url": "http://x"},
                     headers={"X-CSRF-Token": altok})
    assert r.status_code == 403  # alice 非 ws1 成员
```

- [ ] **Step 2: 验证失败** — Run: `uv run pytest packages/web/tests/test_workspace_lifecycle.py -v` → FAIL（`POST /api/workspaces` 不存在 / scan 未校验 ws）

- [ ] **Step 3a: 实现 workspaces.py — admin 建 ws**

顶部 import 加 `from pydantic import BaseModel`、`from supernova_web.auth.dependencies import require_admin`（与 Task 3 的 `current_user` 并列）。在 `list_workspaces` 之前加：

```python
class CreateWorkspaceIn(BaseModel):
    name: str


@router.post("", status_code=201)
async def create_workspace(body: CreateWorkspaceIn, request: Request, user: User = Depends(require_admin)):
    ws = body.name
    ws_dir = request.app.state.config.workspaces_dir / ws
    if ws_dir.exists():
        raise HTTPException(409, "workspace already exists")
    ws_dir.mkdir(parents=True)
    request.app.state.auth_store.add_workspace_member(ws, user.id, "manager")
    return {"name": ws}
```

- [ ] **Step 3b: 实现 scan.py — create_scan 校验已有 ws**

```python
from supernova_web.auth.dependencies import current_user
from supernova_web.auth.models import User
from fastapi import Depends


@router.post("", response_model=ScanAccepted, status_code=202)
async def create_scan(req: ScanRequest, request: Request, user: User = Depends(current_user)):
    ws = req.workspace
    ws_dir = request.app.state.config.workspaces_dir / ws if ws else None
    if not ws or not ws_dir.is_dir():
        raise HTTPException(422, "workspace 不存在，请先让 admin 创建")
    if user.role != "admin" and request.app.state.auth_store.get_workspace_member_role(ws, user.id) is None:
        raise HTTPException(403, "非该 workspace 成员")
    sm = request.app.state.scan_manager
    try:
        ws_name = await sm.start(req)
    except TemporalUnavailable:
        raise HTTPException(400, "Temporal 服务未运行，请先 docker-compose up -d")
    except TooManyScans as e:
        raise HTTPException(409, f"已有扫描在跑，并发上限 {e.limit}")
    except PermissionError as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except ValidationError as e:
        raise HTTPException(422, detail=e.errors())
    return ScanAccepted(workspace=ws_name)
```

- [ ] **Step 3c: 改 scan_manager.start — ws 必须显式**

把 `scan_manager.py:88` 的 `ws = req.workspace or self._gen_ws_name(req)` 改为 `ws = req.workspace`（ws 已由 create_scan 预校验存在）。`ws_dir.mkdir(parents=True, exist_ok=True)`（line 91）保留（幂等无害）。`_gen_ws_name` 成为死代码，可删可留。

- [ ] **Step 4: 验证通过** — Run: `uv run pytest packages/web/tests/test_workspace_lifecycle.py -v` → 5 passed

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/api/workspaces.py packages/web/src/supernova_web/api/scan.py packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_workspace_lifecycle.py
git commit -m "feat(web/p1): admin 建 workspace + scan 在已有 ws 内 (替代 scan 创建 manager)"
```

---

## Task 5: workspace 产物路由加 workspace_member 依赖

**Files:**
- Modify: `packages/web/src/supernova_web/api/workspaces.py`（`get_workspace`/`deliverables_summary`/`deliverables_file`/`report`/`logs` 加 `workspace_member`）
- Modify: `packages/web/src/supernova_web/api/events.py`（`stream_events` 加 `workspace_member`）
- Modify: `packages/web/src/supernova_web/api/scan.py`（`cancel_scan` 加 `workspace_member`）
- Test: 并入 `packages/web/tests/test_workspace_permissions.py`（追加非成员 403 用例）

**Interfaces:** 非成员（且非 admin）访问任何 workspace 产物 → 403。

- [ ] **Step 1: 追加失败测试**

在 `test_workspace_permissions.py` 末尾追加（端到端，经 TestClient）：

```python
from starlette.testclient import TestClient
from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


@pytest.fixture
def _prod_app(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    app = create_app()
    st = app.state.auth_store
    st.create_user("alice", hash_password("p"))
    st.create_user("bob", hash_password("p"))
    (app.state.config.workspaces_dir / "ws_alice").mkdir()
    st.add_workspace_member("ws_alice", st.get_user_by_username("alice").id, "manager")
    return app


def _login(app, username):
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": username, "password": "p"}, headers={"X-CSRF-Token": tok})
    return c


def test_non_member_cannot_read_workspace(_prod_app):
    bob = _login(_prod_app, "bob")     # bob 非 ws_alice 成员
    assert bob.get("/api/workspaces/ws_alice").status_code == 403
    assert bob.get("/api/workspaces/ws_alice/report").status_code == 403
    assert bob.get("/api/workspaces/ws_alice/logs").status_code == 403
    assert bob.get("/api/workspaces/ws_alice/events").status_code == 403


def test_member_can_read(_prod_app):
    alice = _login(_prod_app, "alice")
    assert alice.get("/api/workspaces/ws_alice").status_code != 403  # 200（可能空 metrics）
```

- [ ] **Step 2: 验证失败** — Run: `uv run pytest packages/web/tests/test_workspace_permissions.py -v` → FAIL（非成员当前能访问）

- [ ] **Step 3: 改 workspaces.py — 5 个路由加依赖**

顶部 import 加 `from supernova_web.auth.dependencies import current_user, workspace_member`。

把 `get_workspace`、`deliverables_summary`、`deliverables_file`、`report`、`logs` 的签名各加一个参数（FastAPI 自动从 path 注入 `ws` 给依赖）：

```python
async def get_workspace(ws: str, request: Request, _: User = Depends(workspace_member)):
async def deliverables_summary(ws: str, request: Request, _: User = Depends(workspace_member), path: str | None = Query(None)):
async def deliverables_file(ws: str, filename: str, request: Request, _: User = Depends(workspace_member), track: str = "whitebox"):
async def report(ws: str, request: Request, _: User = Depends(workspace_member)):
async def logs(ws: str, request: Request, _: User = Depends(workspace_member), file: str | None = Query(None)):
```

（`User`、`Depends` 已在 Task 3 import；未 import 则补。）

- [ ] **Step 4: 改 events.py — stream_events 加依赖**

```python
from supernova_web.auth.dependencies import workspace_member
from supernova_web.auth.models import User
from fastapi import Depends


@router.get("/{ws}/events")
async def stream_events(ws: str, request: Request, _: User = Depends(workspace_member)):
    ...
```

- [ ] **Step 5: 改 scan.py — cancel_scan 加依赖**

```python
from supernova_web.auth.dependencies import current_user, workspace_member


@router.delete("/{ws}")
async def cancel_scan(ws: str, request: Request, _: User = Depends(workspace_member)):
    ...
```

- [ ] **Step 6: 验证通过** — Run: `uv run pytest packages/web/tests/test_workspace_permissions.py -v` → 全 passed

- [ ] **Step 7: Commit**

```bash
git add packages/web/src/supernova_web/api/workspaces.py packages/web/src/supernova_web/api/events.py packages/web/src/supernova_web/api/scan.py packages/web/tests/test_workspace_permissions.py
git commit -m "feat(web/p1): workspace 产物路由 (get/report/logs/events/cancel) 加成员依赖"
```

---

## Task 6: DELETE workspace 加 manager 权限 + 清成员

**Files:**
- Modify: `packages/web/src/supernova_web/api/workspaces.py:51-64`（`delete_workspace`）
- Test: 追加到 `test_workspace_permissions.py`

- [ ] **Step 1: 追加测试**

```python
def test_member_cannot_delete_workspace(_prod_app):
    # 让 bob 也成为 ws_alice 的 member（非 manager）
    st = _prod_app.state.auth_store
    st.add_workspace_member("ws_alice", st.get_user_by_username("bob").id, "member")
    bob = _login(_prod_app, "bob")
    tok = bob.get("/api/auth/csrf").json()["csrf_token"]
    r = bob.delete("/api/workspaces/ws_alice", headers={"X-CSRF-Token": tok})
    assert r.status_code == 403


def test_manager_delete_clears_members(_prod_app):
    st = _prod_app.state.auth_store
    alice = _login(_prod_app, "alice")
    tok = alice.get("/api/auth/csrf").json()["csrf_token"]
    r = alice.delete("/api/workspaces/ws_alice", headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert st.list_workspace_members("ws_alice") == []  # 成员关系已清
```

- [ ] **Step 2: 验证失败** — Run: `uv run pytest packages/web/tests/test_workspace_permissions.py -v` → FAIL（member 能删 / 成员未清）

- [ ] **Step 3: 实现** — 把 `delete_workspace` 改为：

```python
async def delete_workspace(ws: str, request: Request, _: User = Depends(workspace_manager)):
    p = _workspace_path(request, ws)
    idx = request.app.state.indexer
    from supernova_core.session import SessionManager
    mgr = SessionManager(request.app.state.config.workspaces_dir)
    if idx._status_of(p, mgr.get_status(p)) == "running":
        raise HTTPException(status_code=409, detail="workspace running, cancel scan first")
    shutil.rmtree(p)
    idx.set_active_pid(ws, None)
    request.app.state.auth_store.delete_workspace_members(ws)
    return {"deleted": ws}
```

（顶部 import 把 `workspace_member` 扩为 `workspace_member, workspace_manager`。）

- [ ] **Step 4: 验证通过** — Run: `uv run pytest packages/web/tests/test_workspace_permissions.py -v` → 全 passed

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/api/workspaces.py packages/web/tests/test_workspace_permissions.py
git commit -m "feat(web/p1): DELETE workspace 需 manager 权限 + 清理成员关系"
```

---

## Task 7: 成员管理路由 + GET /api/users

**Files:**
- Create: `packages/web/src/supernova_web/api/members.py`
- Create: `packages/web/src/supernova_web/api/users.py`
- Modify: `packages/web/src/supernova_web/app.py`（include 两 router）
- Test: `packages/web/tests/test_members_routes.py`

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_members_routes.py
import pytest
from starlette.testclient import TestClient
from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


@pytest.fixture
def _app(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    app = create_app()
    st = app.state.auth_store
    st.create_user("alice", hash_password("p"))
    st.create_user("bob", hash_password("p"))
    (app.state.config.workspaces_dir / "ws1").mkdir()
    st.add_workspace_member("ws1", st.get_user_by_username("alice").id, "manager")
    return app


def _login(app, username):
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": username, "password": "p"}, headers={"X-CSRF-Token": tok})
    return c


def test_list_users(_app):
    c = _login(_app, "alice")
    names = sorted(u["username"] for u in c.get("/api/users").json()["users"])
    assert names == ["alice", "bob"]


def test_list_members(_app):
    c = _login(_app, "alice")
    members = c.get("/api/workspaces/ws1/members").json()["members"]
    assert any(m["username"] == "alice" and m["role"] == "manager" for m in members)


def test_manager_adds_member(_app):
    c = _login(_app, "alice")
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    r = c.post("/api/workspaces/ws1/members", json={"username": "bob", "role": "member"},
               headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    members = c.get("/api/workspaces/ws1/members").json()["members"]
    assert any(m["username"] == "bob" for m in members)


def test_member_cannot_add(_app):
    # bob 先被加为 member
    _app.state.auth_store.add_workspace_member("ws1", _app.state.auth_store.get_user_by_username("bob").id, "member")
    c = _login(_app, "bob")
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    r = c.post("/api/workspaces/ws1/members", json={"username": "alice"}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 403


def test_cannot_remove_last_manager(_app):
    c = _login(_app, "alice")
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    r = c.delete("/api/workspaces/ws1/members/alice", headers={"X-CSRF-Token": tok})
    assert r.status_code == 409
```

- [ ] **Step 2: 验证失败** — Run: `uv run pytest packages/web/tests/test_members_routes.py -v` → FAIL（路由不存在）

- [ ] **Step 3: 实现 members.py**

```python
# packages/web/src/supernova_web/api/members.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from supernova_web.auth.dependencies import workspace_member, workspace_manager
from supernova_web.auth.models import User

router = APIRouter(prefix="/api/workspaces", tags=["members"])


class AddMemberIn(BaseModel):
    username: str
    role: str | None = "member"


@router.get("/{ws}/members")
async def list_members(ws: str, request: Request, _: User = Depends(workspace_member)):
    members = request.app.state.auth_store.list_workspace_members(ws)
    return {"members": [{"user_id": uid, "username": un, "role": r} for uid, un, r in members]}


@router.post("/{ws}/members")
async def add_member(ws: str, body: AddMemberIn, request: Request, _: User = Depends(workspace_manager)):
    target = request.app.state.auth_store.get_user_by_username(body.username)
    if target is None:
        raise HTTPException(404, "user not found")
    role = body.role if body.role in ("manager", "member") else "member"
    request.app.state.auth_store.add_workspace_member(ws, target.id, role)
    return {"ok": True}


@router.delete("/{ws}/members/{username}")
async def remove_member(ws: str, username: str, request: Request, _: User = Depends(workspace_manager)):
    target = request.app.state.auth_store.get_user_by_username(username)
    if target is None:
        raise HTTPException(404, "user not found")
    members = request.app.state.auth_store.list_workspace_members(ws)
    managers = [m for m in members if m[2] == "manager"]
    if len(managers) <= 1 and any(m[1] == username for m in managers):
        raise HTTPException(409, "不能移除最后一个 manager")
    request.app.state.auth_store.remove_workspace_member(ws, target.id)
    return {"ok": True}
```

- [ ] **Step 4: 实现 users.py**

```python
# packages/web/src/supernova_web/api/users.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from supernova_web.auth.dependencies import current_user
from supernova_web.auth.models import User

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("")
async def list_users(request: Request, _: User = Depends(current_user)):
    return {"users": [{"id": u.id, "username": u.username, "role": u.role}
                      for u in request.app.state.auth_store.list_all_users()]}
```

- [ ] **Step 5: 注册到 app.py**

在 `create_app` 的 `include_router` 段加：

```python
    from .api import members, users
    from .auth.dependencies import current_user
    _require_auth = [Depends(current_user)]
    app.include_router(members.router, dependencies=_require_auth)
    app.include_router(users.router)
```

（users.router 内部已 `Depends(current_user)`，无需 router 级再加；members 内部用 workspace_member/manager 更细。）

- [ ] **Step 6: 验证通过** — Run: `uv run pytest packages/web/tests/test_members_routes.py -v` → 5 passed

- [ ] **Step 7: Commit**

```bash
git add packages/web/src/supernova_web/api/members.py packages/web/src/supernova_web/api/users.py packages/web/src/supernova_web/app.py packages/web/tests/test_members_routes.py
git commit -m "feat(web/p1): 成员管理路由 (list/add/remove + 最后 manager 保护) + GET /api/users"
```

---

## Task 8: legacy workspace 迁移（startup）

**Files:**
- Modify: `packages/web/src/supernova_web/app.py`（lifespan 加 `_migrate_legacy_workspace_members`）
- Test: `packages/web/tests/test_legacy_migration.py`

**语义：** P0 前已有、目录存在但 `workspace_members` 无记录的 workspace → 启动时分配给所有 admin（manager）。保证 admin 能见 legacy ws 并进一步分配；普通用户默认不见。

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_legacy_migration.py
from starlette.testclient import TestClient
from supernova_web.app import create_app


def test_legacy_workspace_assigned_to_admins(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    app = create_app()
    admin = app.state.auth_store.create_user("admin", "h", role="admin")
    legacy = app.state.config.workspaces_dir / "legacy_ws"
    legacy.mkdir()
    assert app.state.auth_store.list_workspace_members("legacy_ws") == []  # 迁移前无记录
    with TestClient(app):  # 触发 lifespan → 迁移
        pass
    assert app.state.auth_store.get_workspace_member_role("legacy_ws", admin.id) == "manager"


def test_workspace_with_members_not_reassigned(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    app = create_app()
    admin = app.state.auth_store.create_user("admin", "h", role="admin")
    alice = app.state.auth_store.create_user("alice", "h")
    (app.state.config.workspaces_dir / "ws1").mkdir()
    app.state.auth_store.add_workspace_member("ws1", alice.id, "manager")  # 已有成员
    with TestClient(app):
        pass
    # admin 不被重复加（已有成员记录的不动）—— 验证仍只有 alice
    members = app.state.auth_store.list_workspace_members("ws1")
    assert len(members) == 1 and members[0][1] == "alice"
```

- [ ] **Step 2: 验证失败** — Run: `uv run pytest packages/web/tests/test_legacy_migration.py -v` → FAIL（无迁移函数）

- [ ] **Step 3: 实现** — 在 `app.py` 加模块级函数（`_reconcile_orphaned_scans` 附近）：

```python
def _migrate_legacy_workspace_members(app: FastAPI) -> None:
    """把无成员记录的 legacy workspace 分配给所有 admin（manager），让 admin 能见/管。
    已有成员记录的 workspace 不动（不重复分配、不覆盖）。"""
    store = app.state.auth_store
    admins = [u for u in store.list_all_users() if u.role == "admin"]
    if not admins:
        return
    ws_dir = app.state.config.workspaces_dir
    if not ws_dir.is_dir():
        return
    for d in ws_dir.iterdir():
        if not d.is_dir():
            continue
        if store.list_workspace_members(d.name):
            continue  # 已有成员，跳过
        for a in admins:
            store.add_workspace_member(d.name, a.id, "manager")
```

在 `lifespan` 里 `seed_users(...)` 之后加：

```python
    _migrate_legacy_workspace_members(app)
```

- [ ] **Step 4: 验证通过** — Run: `uv run pytest packages/web/tests/test_legacy_migration.py -v` → 2 passed

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/app.py packages/web/tests/test_legacy_migration.py
git commit -m "feat(web/p1): legacy workspace 启动迁移 → 分配给所有 admin"
```

---

## Task 9: 前端成员管理 dialog

**Files:**
- Create: `packages/web/frontend/src/api/members.ts`
- Create: `packages/web/frontend/src/components/MemberManagerDialog.tsx`
- Modify: `packages/web/frontend/src/locales/{zh,en}.json`（加 `members.*`）
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/index.tsx`（详情页头部加 `<MemberManagerDialog ws={workspace} />`）
- Test: `packages/web/frontend/src/components/MemberManagerDialog.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
// packages/web/frontend/src/components/MemberManagerDialog.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "@/auth/AuthContext";
import { MemberManagerDialog } from "./MemberManagerDialog";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

function wrap() {
  return render(
    <AuthProvider>
      <MemoryRouter><MemberManagerDialog ws="ws1" /></MemoryRouter>
    </AuthProvider>
  );
}

describe("MemberManagerDialog", () => {
  it("manager 可见管理入口", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockResolvedValueOnce(new Response(JSON.stringify({ user: { id: 2, username: "alice", role: "user" } }), { status: 200 })); // /me
    fm.mockResolvedValue(new Response(JSON.stringify({ members: [{ user_id: 2, username: "alice", role: "manager" }] }), { status: 200 })); // members
    wrap();
    await waitFor(() => expect(screen.getByText("members.manage")).toBeTruthy());
  });

  it("非成员/非 manager 隐藏入口", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockResolvedValueOnce(new Response(JSON.stringify({ user: { id: 3, username: "bob", role: "user" } }), { status: 200 }));
    fm.mockResolvedValue(new Response(JSON.stringify({ members: [{ user_id: 2, username: "alice", role: "manager" }] }), { status: 200 }));
    const { container } = wrap();
    await waitFor(() => expect(container.querySelector("[data-testid=member-manager]") === null).toBe(true));
  });
});
```

- [ ] **Step 2: 验证失败** — Run: `cd packages/web/frontend && npx vitest run src/components/MemberManagerDialog.test.tsx` → FAIL（组件不存在）

- [ ] **Step 3: 实现 api/members.ts**

```ts
// packages/web/frontend/src/api/members.ts
import { apiGet, apiPost, apiDelete } from "./client";

export type Member = { user_id: number; username: string; role: string };
export type UserLite = { id: number; username: string; role: string };

const enc = encodeURIComponent;

export const getMembers = (ws: string) =>
  apiGet<{ members: Member[] }>(`/workspaces/${enc(ws)}/members`);
export const addMember = (ws: string, username: string, role: string = "member") =>
  apiPost(`/workspaces/${enc(ws)}/members`, { username, role });
export const removeMember = (ws: string, username: string) =>
  apiDelete(`/workspaces/${enc(ws)}/members/${enc(username)}`);
export const listUsers = () => apiGet<{ users: UserLite[] }>("/users");
```

- [ ] **Step 4: 实现 MemberManagerDialog.tsx**

```tsx
// packages/web/frontend/src/components/MemberManagerDialog.tsx
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAuth } from "@/auth/AuthContext";
import { getMembers, addMember, removeMember, listUsers } from "@/api/members";
import type { Member, UserLite } from "@/api/members";

export function MemberManagerDialog({ ws }: { ws: string }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [members, setMembers] = useState<Member[]>([]);
  const [users, setUsers] = useState<UserLite[]>([]);
  const [picked, setPicked] = useState("");

  useEffect(() => {
    if (!user) return;
    getMembers(ws).then((r) => setMembers(r.members)).catch(() => {});
  }, [ws, user]);

  const myRole = user?.role === "admin" ? "admin" : members.find((m) => m.user_id === user?.id)?.role;
  const canManage = myRole === "admin" || myRole === "manager";
  if (!canManage) return null;
  if (user?.role !== "admin" && !members.some((m) => m.user_id === user?.id)) return null;

  async function onAdd() {
    if (!picked) return;
    await addMember(ws, picked, "member");
    setPicked("");
    setMembers((await getMembers(ws)).members);
  }
  async function onRemove(username: string) {
    await removeMember(ws, username);
    setMembers((await getMembers(ws)).members);
  }
  async function onOpen() {
    setOpen(true);
    setUsers((await listUsers()).users);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" onClick={onOpen} data-testid="member-manager">{t("members.manage")}</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader><DialogTitle>{t("members.title")}</DialogTitle></DialogHeader>
        <ul className="space-y-1">
          {members.map((m) => (
            <li key={m.user_id} className="flex items-center justify-between rounded border px-3 py-1.5 text-sm">
              <span>{m.username} <span className="text-xs text-muted-foreground">{t(`members.${m.role}`)}</span></span>
              <Button variant="ghost" size="sm" onClick={() => onRemove(m.username)}>{t("members.remove")}</Button>
            </li>
          ))}
        </ul>
        <div className="flex items-center gap-2">
          <Select value={picked} onValueChange={setPicked}>
            <SelectTrigger className="flex-1"><SelectValue placeholder={t("members.username")} /></SelectTrigger>
            <SelectContent>
              {users.filter((u) => !members.some((m) => m.user_id === u.id)).map((u) => (
                <SelectItem key={u.id} value={u.username}>{u.username}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button onClick={onAdd} disabled={!picked}>{t("members.add")}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 5: i18n** — zh.json 加 `"members": { "title": "成员管理", "add": "添加成员", "remove": "移除", "username": "选择用户", "manager": "管理者", "member": "成员", "manage": "管理成员" }`；en.json 加 `{ "title": "Members", "add": "Add member", "remove": "Remove", "username": "Select user", "manager": "Manager", "member": "Member", "manage": "Manage members" }`（同 P0 i18n 模式，注意 zh 真中文）。

- [ ] **Step 6: 集成到 WorkspaceDetail 头部**

在 `src/routes/WorkspaceDetail/index.tsx` 的页面头部（workspace 名附近）加 `<MemberManagerDialog ws={workspace} />`（`workspace` 是该页 `useParams` 的 workspace 名）。

- [ ] **Step 7: 验证通过** — Run: `cd packages/web/frontend && npx vitest run src/components/MemberManagerDialog.test.tsx` → 2 passed

- [ ] **Step 8: Commit**

```bash
cd packages/web/frontend
git add src/api/members.ts src/components/MemberManagerDialog.tsx src/components/MemberManagerDialog.test.tsx src/locales/zh.json src/locales/en.json src/routes/WorkspaceDetail/index.tsx
git commit -m "feat(web/p1): 成员管理 dialog (manager/admin 可见) + i18n + 详情页入口"
```

---

## Task 10: 端到端冒烟（真机）

**前置**：T1–T9 全绿，`uv run pytest packages/web/tests/ -v`（auth + p1 相关）全绿、`cd packages/web/frontend && npm run build` 通过。

- [ ] **Step 1: 配多用户** — `configs/users.yaml` 配 `admin`（admin）+ `alice`（user）+ `bob`（user），重启 web。

- [ ] **Step 2: 冒烟 checklist（两个浏览器/隐身窗口分别登 alice、bob，admin 第三窗口）**

- [ ] alice 发起一次扫描（建 `ws_alice`）→ alice 在 `/workspaces` 见 `ws_alice`
- [ ] bob 登录 → `/workspaces` **不见** `ws_alice`
- [ ] bob 直访 `/api/workspaces/ws_alice` → 403；前端 `/p/ws_alice/...` 也被挡（后端 403）
- [ ] admin 登录 → `/workspaces` 见**所有** ws（含 alice 的 + 任何 legacy）
- [ ] alice 在 `ws_alice` 详情页点「管理成员」→ 加 bob 为 member
- [ ] bob 刷新 → 现在见 `ws_alice`（已成为成员），可读报告/日志，但**不见**「管理成员」按钮
- [ ] bob 尝试 `POST /api/workspaces/ws_alice/members` → 403
- [ ] alice 移除 bob → bob 又不见 `ws_alice`
- [ ] alice 试图移除自己（唯一 manager）→ 409
- [ ] 删除 workspace（alice 或 admin）→ 成员关系一并清除

- [ ] **Step 3: 记录结果**（PR 描述 + 截图）。

---

## Self-Review

**1. Spec coverage（P1 spec § → task）**
- §3 `workspace_members` 表 → T1 ✓
- §4 权限矩阵（admin 全见/manager 管/member 读/非成员 403）→ T2(依赖)/T3(过滤)/T5(产物依赖)/T6(删权限)/T7(成员管理权限) ✓
- §5.1 store 成员方法 → T1 ✓
- §5.2 dependencies → T2 ✓
- §5.3 路由改造（list 过滤/scan 创建者/产物依赖/删除清成员）→ T3/T4/T5/T6 ✓
- §5.4 成员管理路由 + GET /api/users → T7 ✓
- §5.5 legacy 迁移 → T8 ✓
- §5.6 删除清成员 → T6 ✓
- §6 前端成员管理 UI → T9 ✓
- §7 测试策略 → 每 task TDD + T10 冒烟 ✓
- §8 范围边界（repos/configs 不碰）→ Global Constraints 明确 ✓

**2. Placeholder scan**：无 TBD；每步含真实代码。

**3. Type consistency**：
- `User(id, username, role)`：P0 定义，P1 复用 ✓
- `workspace_member/manager(request, ws, user)`：T2 定义 → T3/T5/T6/T7 一致 ✓
- `list_workspace_members -> list[tuple[int,str,str]]`：T1 定义 → T7 用 `(uid,un,role)` ✓
- `Member{user_id,username,role}`：T9 前端 ↔ T7 后端 `{user_id,username,role}` 字段名一致 ✓
- cookie/csrf 沿用 P0（`authed_client` + 写操作带 `X-CSRF-Token`）✓

**结论**：spec 全覆盖、无占位、类型一致。可交付执行。

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-26-web-workspace-membership-p1.md`.**

执行方式同 P0：Subagent-Driven（推荐）或 Inline。**前置**：P0 必须先完成并合入（P1 依赖 P0 的 auth 模块与 `authed_client` fixture）。

**Which approach?**（或先 commit 本 plan + P1 spec，执行稍后定）
