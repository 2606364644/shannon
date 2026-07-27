# WEB 用户管理页面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/users` 独立页(仅 admin)承载账号 CRUD(创建/删除/改全局角色/重置密码)与集中式 ws 成员分配(以用户为中心展开归属编辑)。

**Architecture:** 后端 users router 整体 `require_admin` + 5 个新端点,members router 加 1 个 PATCH 改角色端点;store 层补 5 个方法(删用户单事务清 `workspace_members`+`sessions`,SQLite FK 不强制)。前端新增 `UsersPage` + 形态 A 展开面板 `UserWorkspacesPanel` + 账号 Dialogs,复用 members 写 API 不造批量端点。`GET /api/users` 收紧到 admin,`MemberManagerDialog` 配套改手输 username。

**Tech Stack:** FastAPI + SQLite + pydantic(后端);React + TypeScript + react-router + vitest(前端);bcrypt 密码;CSRF 双重(token cookie + X-CSRF-Token header)。

## Global Constraints

- **铁律边界**: 全部改动在 `packages/web/` 的 auth/users/members 层,**不碰**双轨/确定性层/LLM 轨/GitNexus/sink 规则/chain_verdict/cost 计费/双引擎。
- **密码最小长度 8**: `NEW_PASSWORD_MIN_LEN = 8`(create/reset 校验;`==旧`仅 change-password 适用,本 plan 新端点不校验)。
- **create/reset 置 `must_change=1`**: 接 `2026-07-27-web-default-password-change-reminder` 提醒闭环。
- **删用户单事务清三表**: SQLite 无 `PRAGMA foreign_keys`,`REFERENCES users(id)` 不强制,`delete_user` 必须显式 DELETE `workspace_members` + `sessions` + `users`。
- **后端测试**: `cd /root/shannon-py/packages/web && uv run pytest tests/<file>.py -v`(只跑改动相关文件,勿广跑全套——见 CLAUDE.md 测试陷阱)。
- **前端测试**: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/<path>.test.tsx`(直接跑 vitest,**不用 pnpm**——见 memory pnpm 陷阱)。
- **前端 HTTP**: 用 `apiGet/apiPost/apiDelete/apiPatch`(`@/api/client`),**不用裸 fetch**;CSRF header 由 client 自动注入,401 由 client 自动跳 `/login`。组件 catch `ApiError` 按 `e.status` 映射文案(对齐 WsSettingsTab 模式)。
- **i18n**: 所有用户可见文案进 `zh.json`/`en.json`,key 前缀 `users.*` / `members.input.*`。
- **commit 风格**: `feat(web/auth): ...` / `test(web): ...`,尾部 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。
- **与 ws-scan 解耦正交**: 共享 `app.py`/`router.tsx` 但改动点不重叠(users router include 行 vs scans;`/users` 顶级路由 vs `/p/:workspace/scans`)。若 ws-scan 解耦已先合并,行号可能再变,以"找 `app.include_router(users.router)` 行""找 `{ path: "/settings" ...}` 同级位置"为准。

---

## Task 1: AuthStore 扩展 + User 加 created_at(store 层 TDD)

**Files:**
- Modify: `packages/web/src/supernova_web/auth/models.py`
- Modify: `packages/web/src/supernova_web/auth/store.py`
- Test: `packages/web/tests/test_auth_store.py`(扩)

**Interfaces:**
- Consumes: 现有 `AuthStore._conn()` / `_SCHEMA` / `create_user` / `update_password`
- Produces(后续 task 依赖):
  - `User.created_at: str`(新字段,默认 `""`)
  - `AuthStore.delete_user(user_id: int) -> None`(单事务清 3 表)
  - `AuthStore.update_role(user_id: int, role: str) -> None`
  - `AuthStore.reset_password(user_id: int, new_hash: str) -> None`(UPDATE hash + `must_change_password=1`)
  - `AuthStore.list_user_workspaces_with_role(user_id: int) -> list[tuple[str, str]]`(`[(ws_name, role), ...]`)
  - `AuthStore.update_workspace_member_role(ws_name: str, user_id: int, role: str) -> None`
  - `AuthStore.list_all_users()` 返回的 `User` 带 `created_at` + `must_change_password`

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_auth_store.py` 末尾

```python
def test_list_all_users_includes_created_at_and_must_change(tmp_path):
    """list_all_users 返回的 User 必须带 created_at 与 must_change_password。"""
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    s.create_user("alice", "h", role="user")
    s.create_user("admin", "h", role="admin", must_change=True)
    users = s.list_all_users()
    by_name = {u.username: u for u in users}
    assert by_name["alice"].created_at != ""          # create_user 写了 created_at
    assert by_name["admin"].must_change_password is True


def test_delete_user_clears_members_and_sessions(tmp_path):
    """删用户必须单事务清 workspace_members + sessions + users（FK 不强制，手动清）。"""
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    u = s.create_user("alice", "h")
    s.add_workspace_member("ws-a", u.id, "member")
    s.insert_session(SessionRow(id="sid-1", user_id=u.id,
                                expires_at="2099-01-01T00:00:00",
                                last_seen_at="2026-01-01T00:00:00"))
    assert s.list_workspace_members("ws-a") != []      # 前置：有成员
    s.delete_user(u.id)
    assert s.get_user(u.id) is None                     # 本体已删
    assert s.list_workspace_members("ws-a") == []       # 成员记录已清
    assert s.get_session("sid-1") is None               # session 已清


def test_update_role(tmp_path):
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    u = s.create_user("alice", "h", role="user")
    s.update_role(u.id, "admin")
    assert s.get_user(u.id).role == "admin"


def test_reset_password_sets_must_change(tmp_path):
    """reset_password 写新 hash 并置 must_change=1（与 update_password 置 0 相对）。"""
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    u = s.create_user("alice", "$2b$12$old")
    s.reset_password(u.id, "$2b$12$new")
    assert s.get_password_hash("alice") == "$2b$12$new"
    assert s.get_user(u.id).must_change_password is True


def test_list_user_workspaces_with_role(tmp_path):
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    u = s.create_user("alice", "h")
    s.add_workspace_member("ws-a", u.id, "manager")
    s.add_workspace_member("ws-b", u.id, "member")
    got = dict(s.list_user_workspaces_with_role(u.id))
    assert got == {"ws-a": "manager", "ws-b": "member"}


def test_update_workspace_member_role(tmp_path):
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    u = s.create_user("alice", "h")
    s.add_workspace_member("ws-a", u.id, "member")
    s.update_workspace_member_role("ws-a", u.id, "manager")
    assert s.get_workspace_member_role("ws-a", u.id) == "manager"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py/packages/web && uv run pytest tests/test_auth_store.py -v`
Expected: 6 个新测试 FAIL(`AttributeError: 'AuthStore' object has no attribute 'delete_user'` 等 + `created_at` 缺失)

- [ ] **Step 3: 实现 — models.py User 加 created_at**

`packages/web/src/supernova_web/auth/models.py` 的 `User` 类加字段(默认 `""` 兼容现有构造点):

```python
class User(BaseModel):
    id: int
    username: str
    role: str = "user"  # 'admin' | 'user'
    must_change_password: bool = False
    created_at: str = ""  # ISO8601；list_all_users 填充，其余构造点默认空
```

- [ ] **Step 4: 实现 — store.py 扩展**

`list_all_users` 补字段(改 SELECT + 构造):

```python
def list_all_users(self) -> list["User"]:
    with self._conn() as c:
        rows = c.execute(
            "SELECT id, username, role, must_change_password, created_at FROM users ORDER BY id"
        ).fetchall()
    return [User(id=r[0], username=r[1], role=r[2],
                 must_change_password=bool(r[3]), created_at=r[4]) for r in rows]
```

在 `AuthStore` 末尾(purge_expired 之后)追加 5 个方法:

```python
def delete_user(self, user_id: int) -> None:
    """删用户：单事务清 workspace_members + sessions + users。
    SQLite 未开 PRAGMA foreign_keys，REFERENCES 不强制，必须手动清，否则孤儿成员 +
    被删用户 session 残留有效。护栏（自删/最后 admin）在 route 层先做。"""
    with self._conn() as c:
        c.execute("DELETE FROM workspace_members WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM users WHERE id=?", (user_id,))

def update_role(self, user_id: int, role: str) -> None:
    with self._conn() as c:
        c.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))

def reset_password(self, user_id: int, new_hash: str) -> None:
    """admin 重置他人密码：写新 hash 并置 must_change=1（强制对方首登改密）。
    区别于 update_password（自己改自己，置 must_change=0）。"""
    with self._conn() as c:
        c.execute(
            "UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?",
            (new_hash, user_id),
        )

def list_user_workspaces_with_role(self, user_id: int) -> list[tuple[str, str]]:
    """该用户的全部 ws 归属 [(ws_name, role)]（形态 A 展开面板用）。"""
    with self._conn() as c:
        rows = c.execute(
            "SELECT workspace_name, role FROM workspace_members WHERE user_id=?",
            (user_id,),
        ).fetchall()
    return [(r[0], r[1]) for r in rows]

def update_workspace_member_role(self, ws_name: str, user_id: int, role: str) -> None:
    with self._conn() as c:
        c.execute(
            "UPDATE workspace_members SET role=? WHERE workspace_name=? AND user_id=?",
            (role, ws_name, user_id),
        )
```

- [ ] **Step 5: 跑测试验证通过**

Run: `cd /root/shannon-py/packages/web && uv run pytest tests/test_auth_store.py -v`
Expected: 全 PASS(原有 + 6 新)

- [ ] **Step 6: Commit**

```bash
git add packages/web/src/supernova_web/auth/models.py packages/web/src/supernova_web/auth/store.py packages/web/tests/test_auth_store.py
git commit -m "feat(web/auth): AuthStore 扩展用户管理(delete/update_role/reset/归属)"
```

---

## Task 2: users router CRUD 端点 + 收紧 require_admin

**Files:**
- Modify: `packages/web/src/supernova_web/auth/passwords.py`(加 `NEW_PASSWORD_MIN_LEN`)
- Modify: `packages/web/src/supernova_web/api/users.py`(整体 require_admin + 5 端点)
- Modify: `packages/web/src/supernova_web/app.py`(users router 挂载行)
- Test: `packages/web/tests/test_users_routes.py`(新建)

**Interfaces:**
- Consumes: Task 1 的 `delete_user/update_role/reset_password/list_user_workspaces_with_role`;现有 `create_user/get_user_by_username/list_all_users`;`require_admin`(`auth/dependencies`);`hash_password`(`auth/passwords`);`verify_csrf`(`auth/csrf`)
- Produces(前端 Task 4+ 依赖):
  - `GET /api/users` → `{users: [{id, username, role, must_change_password, created_at}]}`
  - `POST /api/users` body `{username, password, role: "admin"|"user"}` → `{user: {...}}`
  - `DELETE /api/users/{id}` → `{ok: true}`
  - `PATCH /api/users/{id}` body `{role}` → `{ok: true}`
  - `POST /api/users/{id}/reset-password` body `{new_password}` → `{ok: true}`
  - `GET /api/users/{id}/workspaces` → `{workspaces: [{workspace, role}]}`

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_users_routes.py`

```python
import pytest
from starlette.testclient import TestClient

from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


@pytest.fixture
def admin_client(tmp_workspaces, monkeypatch):
    """建 admin + 普通 user 两个账号，以 admin 身份登录返回 client。"""
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_core.utils.paths import resolve_workspaces_dir
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    assert resolve_workspaces_dir() == tmp_workspaces
    app = create_app()
    st = app.state.auth_store
    st.create_user("admin", hash_password("admin-pw"), role="admin")
    st.create_user("alice", hash_password("alice-pw"), role="user")
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": "admin", "password": "admin-pw"},
           headers={"X-CSRF-Token": tok})
    return c, app


@pytest.fixture
def user_client(admin_client):
    """以普通 user 身份登录（测 403 收紧）。"""
    c, app = admin_client
    c2 = TestClient(app)
    tok = c2.get("/api/auth/csrf").json()["csrf_token"]
    c2.post("/api/auth/login", json={"username": "alice", "password": "alice-pw"},
            headers={"X-CSRF-Token": tok})
    return c2


def _csrf(c):
    return c.cookies.get("sn-csrf") or c.get("/api/auth/csrf").json()["csrf_token"]


def _alice_id(app):
    return app.state.auth_store.get_user_by_username("alice").id


# --- GET 收紧 ---

def test_list_users_requires_admin(user_client):
    """非 admin 访问 GET /api/users -> 403（收紧）。"""
    assert user_client.get("/api/users").status_code == 403


def test_list_users_admin_ok(admin_client):
    c, app = admin_client
    r = c.get("/api/users")
    assert r.status_code == 200
    names = [u["username"] for u in r.json()["users"]]
    assert set(names) == {"admin", "alice"}
    # 带新字段
    alice = next(u for u in r.json()["users"] if u["username"] == "alice")
    assert "must_change_password" in alice and "created_at" in alice


# --- 创建 ---

def test_create_user_success(admin_client):
    c, app = admin_client
    r = c.post("/api/users", json={"username": "bob", "password": "bob-pw-1", "role": "user"},
               headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 200
    assert app.state.auth_store.get_user_by_username("bob").must_change_password is True


def test_create_user_dup_409(admin_client):
    c, app = admin_client
    r = c.post("/api/users", json={"username": "alice", "password": "x1234567", "role": "user"},
               headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 409


def test_create_user_short_pw_400(admin_client):
    c, app = admin_client
    r = c.post("/api/users", json={"username": "bob", "password": "123", "role": "user"},
               headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 400


def test_create_user_bad_role_422(admin_client):
    c, app = admin_client
    r = c.post("/api/users", json={"username": "bob", "password": "x1234567", "role": "superuser"},
               headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 422


# --- 删除 ---

def test_delete_user_clears_members(admin_client):
    c, app = admin_client
    aid = _alice_id(app)
    app.state.auth_store.add_workspace_member("ws-a", aid, "member")
    r = c.delete(f"/api/users/{aid}", headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 200
    assert app.state.auth_store.get_user(aid) is None
    assert app.state.auth_store.list_workspace_members("ws-a") == []


def test_delete_self_409(admin_client):
    c, app = admin_client
    me = app.state.auth_store.get_user_by_username("admin").id
    r = c.delete(f"/api/users/{me}", headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 409


def test_delete_last_admin_409(admin_client):
    c, app = admin_client
    me = app.state.auth_store.get_user_by_username("admin").id
    # 只有一个 admin，删自己即删最后 admin -> 409（同自删，覆盖最后 admin 护栏分支）
    r = c.delete(f"/api/users/{me}", headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 409


# --- 改全局角色 ---

def test_patch_role_success(admin_client):
    c, app = admin_client
    aid = _alice_id(app)
    r = c.patch(f"/api/users/{aid}", json={"role": "admin"},
                headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 200
    assert app.state.auth_store.get_user(aid).role == "admin"


def test_patch_role_self_demote_409(admin_client):
    c, app = admin_client
    me = app.state.auth_store.get_user_by_username("admin").id
    r = c.patch(f"/api/users/{me}", json={"role": "user"},
                headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 409


# --- 重置密码 ---

def test_reset_password_sets_must_change(admin_client):
    c, app = admin_client
    aid = _alice_id(app)
    r = c.post(f"/api/users/{aid}/reset-password", json={"new_password": "new-pw-12"},
               headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 200
    assert app.state.auth_store.get_user(aid).must_change_password is True


def test_reset_password_short_400(admin_client):
    c, app = admin_client
    aid = _alice_id(app)
    r = c.post(f"/api/users/{aid}/reset-password", json={"new_password": "123"},
               headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 400


# --- 归属 ---

def test_get_user_workspaces(admin_client):
    c, app = admin_client
    aid = _alice_id(app)
    app.state.auth_store.add_workspace_member("ws-a", aid, "member")
    r = c.get(f"/api/users/{aid}/workspaces")
    assert r.status_code == 200
    assert {"workspace": "ws-a", "role": "member"} in r.json()["workspaces"]


# --- CSRF ---

def test_create_user_requires_csrf(admin_client):
    c, app = admin_client
    r = c.post("/api/users", json={"username": "bob", "password": "x1234567", "role": "user"})
    assert r.status_code == 403
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py/packages/web && uv run pytest tests/test_users_routes.py -v`
Expected: FAIL(端点不存在/404 或 403 收紧未生效)

- [ ] **Step 3: 实现 — passwords.py 加常量**

`packages/web/src/supernova_web/auth/passwords.py` 末尾追加(公开常量,供 users router 复用;auth/routes 的私有 `_NEW_PASSWORD_MIN_LEN` 留作 follow-up 统一):

```python
# 新密码最小长度（create/reset 与 change-password 共用）。auth/routes._NEW_PASSWORD_MIN_LEN
# 同值；此处提为公开常量供 api/users 复用，避免跨模块依赖私有名。
NEW_PASSWORD_MIN_LEN = 8
```

- [ ] **Step 4: 实现 — users.py 重写为 require_admin + 5 端点**

替换 `packages/web/src/supernova_web/api/users.py` 全文:

```python
# packages/web/src/supernova_web/api/users.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Literal

from supernova_web.auth.csrf import verify_csrf
from supernova_web.auth.dependencies import current_user, require_admin
from supernova_web.auth.models import User
from supernova_web.auth.passwords import NEW_PASSWORD_MIN_LEN, hash_password

router = APIRouter(prefix="/api/users", tags=["users"])


class CreateUserIn(BaseModel):
    username: str
    password: str
    role: Literal["admin", "user"] = "user"


class UpdateRoleIn(BaseModel):
    role: Literal["admin", "user"]


class ResetPasswordIn(BaseModel):
    new_password: str


def _user_out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role,
            "must_change_password": u.must_change_password, "created_at": u.created_at}


def _check_csrf(request: Request) -> None:
    if not verify_csrf(request.headers.get("x-csrf-token"), request.cookies.get("sn-csrf")):
        raise HTTPException(status_code=403, detail="invalid csrf token")


def _admin_count(store) -> int:
    return sum(1 for u in store.list_all_users() if u.role == "admin")


@router.get("")
async def list_users(request: Request, _: User = Depends(require_admin)):
    store = request.app.state.auth_store
    return {"users": [_user_out(u) for u in store.list_all_users()]}


@router.post("")
async def create_user(body: CreateUserIn, request: Request, _: User = Depends(require_admin)):
    _check_csrf(request)
    if len(body.password) < NEW_PASSWORD_MIN_LEN:
        raise HTTPException(400, f"password must be at least {NEW_PASSWORD_MIN_LEN} characters")
    store = request.app.state.auth_store
    if store.get_user_by_username(body.username) is not None:
        raise HTTPException(409, "username exists")
    u = store.create_user(body.username, hash_password(body.password),
                          role=body.role, must_change=True)
    return {"user": _user_out(u)}


@router.delete("/{user_id}")
async def delete_user(user_id: int, request: Request, admin: User = Depends(require_admin)):
    _check_csrf(request)
    store = request.app.state.auth_store
    target = store.get_user(user_id)
    if target is None:
        raise HTTPException(404, "user not found")
    if target.id == admin.id:
        raise HTTPException(409, "cannot delete self")
    if target.role == "admin" and _admin_count(store) <= 1:
        raise HTTPException(409, "cannot delete last admin")
    store.delete_user(user_id)
    return {"ok": True}


@router.patch("/{user_id}")
async def update_role(user_id: int, body: UpdateRoleIn, request: Request,
                      admin: User = Depends(require_admin)):
    _check_csrf(request)
    store = request.app.state.auth_store
    target = store.get_user(user_id)
    if target is None:
        raise HTTPException(404, "user not found")
    if target.id == admin.id and body.role != "admin":
        raise HTTPException(409, "cannot demote self")
    if target.role == "admin" and body.role != "admin" and _admin_count(store) <= 1:
        raise HTTPException(409, "cannot demote last admin")
    store.update_role(user_id, body.role)
    return {"ok": True}


@router.post("/{user_id}/reset-password")
async def reset_password(user_id: int, body: ResetPasswordIn, request: Request,
                         _: User = Depends(require_admin)):
    _check_csrf(request)
    if len(body.new_password) < NEW_PASSWORD_MIN_LEN:
        raise HTTPException(400, f"password must be at least {NEW_PASSWORD_MIN_LEN} characters")
    store = request.app.state.auth_store
    if store.get_user(user_id) is None:
        raise HTTPException(404, "user not found")
    store.reset_password(user_id, hash_password(body.new_password))
    return {"ok": True}


@router.get("/{user_id}/workspaces")
async def user_workspaces(user_id: int, request: Request, _: User = Depends(require_admin)):
    store = request.app.state.auth_store
    if store.get_user(user_id) is None:
        raise HTTPException(404, "user not found")
    pairs = store.list_user_workspaces_with_role(user_id)
    return {"workspaces": [{"workspace": w, "role": r} for w, r in pairs]}
```

- [ ] **Step 5: 实现 — app.py 收紧 users router 挂载**

找到 `app.include_router(users.router)` 行(当前约 332 行,ws-scan 解耦合并后可能变,以文本匹配为准),改为加登录依赖(require_admin 在每个端点内做):

```python
    app.include_router(users.router, dependencies=_require_auth)
```

- [ ] **Step 6: 跑测试验证通过**

Run: `cd /root/shannon-py/packages/web && uv run pytest tests/test_users_routes.py tests/test_auth_routes.py -v`
Expected: 全 PASS(新 13 测试 + 既有 auth routes 回归不受影响)

- [ ] **Step 7: Commit**

```bash
git add packages/web/src/supernova_web/auth/passwords.py packages/web/src/supernova_web/api/users.py packages/web/src/supernova_web/app.py packages/web/tests/test_users_routes.py
git commit -m "feat(web/auth): users router CRUD + 收紧 require_admin"
```

---

## Task 3: members router PATCH 改角色端点

**Files:**
- Modify: `packages/web/src/supernova_web/api/members.py`(+PATCH)
- Test: `packages/web/tests/test_members_routes.py`(新建)

**Interfaces:**
- Consumes: Task 1 的 `update_workspace_member_role`;现有 `list_workspace_members`/`workspace_manager` 依赖;`verify_csrf`
- Produces: `PATCH /api/workspaces/{ws}/members/{username}` body `{role: "manager"|"member"}` → `{ok: true}`(前端 Task 8 形态 A 面板用)

> **CSRF note**: 现有 `add_member`/`remove_member` 未显式 `verify_csrf`(预存问题)。本 plan **仅新 PATCH 加 CSRF**(新代码安全),不扩展到修 add/remove(超出 spec 范围,留独立安全 follow-up)。

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_members_routes.py`

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
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": "admin", "password": "admin-pw"},
           headers={"X-CSRF-Token": tok})
    return c, app


def _csrf(c):
    return c.cookies.get("sn-csrf") or c.get("/api/auth/csrf").json()["csrf_token"]


def test_patch_member_role_success(admin_client):
    c, app = admin_client
    st = app.state.auth_store
    u = st.create_user("alice", "h")
    st.add_workspace_member("ws-a", u.id, "member")
    r = c.patch("/api/workspaces/ws-a/members/alice", json={"role": "manager"},
                headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 200
    assert st.get_workspace_member_role("ws-a", u.id) == "manager"


def test_patch_member_role_last_manager_409(admin_client):
    """降最后 manager -> 409（复用 remove_member 的护栏逻辑）。"""
    c, app = admin_client
    st = app.state.auth_store
    u = st.create_user("alice", "h")
    st.add_workspace_member("ws-a", u.id, "manager")  # 唯一 manager
    r = c.patch("/api/workspaces/ws-a/members/alice", json={"role": "member"},
                headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 409


def test_patch_member_role_bad_role_422(admin_client):
    c, app = admin_client
    st = app.state.auth_store
    u = st.create_user("alice", "h")
    st.add_workspace_member("ws-a", u.id, "member")
    r = c.patch("/api/workspaces/ws-a/members/alice", json={"role": "owner"},
                headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 422


def test_patch_member_role_user_not_found_404(admin_client):
    c, app = admin_client
    r = c.patch("/api/workspaces/ws-a/members/nobody", json={"role": "manager"},
                headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 404
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py/packages/web && uv run pytest tests/test_members_routes.py -v`
Expected: FAIL(405 Method Not Allowed 或 404,PATCH 端点不存在)

- [ ] **Step 3: 实现 — members.py 加 PATCH**

在 `packages/web/src/supernova_web/api/members.py` 的 `remove_member` 之后追加:

```python
class UpdateMemberRoleIn(BaseModel):
    role: Literal["manager", "member"]


@router.patch("/{ws}/members/{username}")
async def update_member_role(ws: str, username: str, body: UpdateMemberRoleIn,
                             request: Request, _: User = Depends(workspace_manager)):
    _check_csrf_members(request)
    store = request.app.state.auth_store
    target = store.get_user_by_username(username)
    if target is None:
        raise HTTPException(404, "user not found")
    members = store.list_workspace_members(ws)
    if not any(m[1] == username for m in members):
        raise HTTPException(404, "not a member")
    # 护栏：降最后 manager -> 拒（与 remove_member 同逻辑）
    managers = [m for m in members if m[2] == "manager"]
    if body.role != "manager" and len(managers) <= 1 and any(m[1] == username for m in managers):
        raise HTTPException(409, "不能降最后一个 manager")
    store.update_workspace_member_role(ws, target.id, body.role)
    return {"ok": True}
```

在文件顶部 import 区加:
```python
from typing import Literal
from supernova_web.auth.csrf import verify_csrf
```

并在 router 内加 CSRF helper(新 PATCH 用;add/remove 维持现状不加——见 task note):
```python
def _check_csrf_members(request: Request) -> None:
    if not verify_csrf(request.headers.get("x-csrf-token"), request.cookies.get("sn-csrf")):
        raise HTTPException(status_code=403, detail="invalid csrf token")
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /root/shannon-py/packages/web && uv run pytest tests/test_members_routes.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/api/members.py packages/web/tests/test_members_routes.py
git commit -m "feat(web/auth): members PATCH 改 ws 角色(+最后 manager 护栏)"
```

---

## Task 4: 前端基建(apiPatch + users API client + i18n + RequireAdmin + router + UserMenu)

**Files:**
- Modify: `packages/web/frontend/src/api/client.ts`(+`apiPatch`)
- Create: `packages/web/frontend/src/api/users.ts`(CRUD + 归属 client)
- Modify: `packages/web/frontend/src/api/members.ts`(删 `listUsers`/挪 `UserLite`)
- Create: `packages/web/frontend/src/auth/RequireAdmin.tsx`
- Modify: `packages/web/frontend/src/router.tsx`(+`/users` 路由)
- Modify: `packages/web/frontend/src/components/layout/UserMenu.tsx`(+入口)
- Modify: `packages/web/frontend/src/locales/zh.json` + `en.json`
- Test: `packages/web/frontend/src/auth/RequireAdmin.test.tsx`(新)
- Test: `packages/web/frontend/src/components/layout/UserMenu.test.tsx`(已有则改;否则参照现有)

**Interfaces:**
- Consumes: Task 2/3 的后端端点;`apiGet/apiPost/apiDelete`(`client.ts`);`useAuth`(`AuthContext`)
- Produces(后续 Task 5+ 依赖):
  - `apiPatch<T>(path, body, opts?)`(`client.ts`)
  - `api/users.ts`: `listUsers()`, `createUser(body)`, `deleteUser(id)`, `updateRole(id, role)`, `resetPassword(id, new_password)`, `getUserWorkspaces(id)`, 类型 `UserRow`/`UserWorkspace`
  - `RequireAdmin` 组件(非 admin `Navigate` 回 `/`)
  - `/users` 路由 + `UserMenu` admin 入口

- [ ] **Step 1: 实现 — client.ts 加 apiPatch**

`packages/web/frontend/src/api/client.ts` 在 `apiDelete` 之后(apiPut 之后)加:

```typescript
export const apiPatch = <T>(path: string, body: unknown, opts?: ReqOptions) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) }, opts);
```

- [ ] **Step 2: 实现 — api/users.ts**

新建 `packages/web/frontend/src/api/users.ts`:

```typescript
import { apiGet, apiPost, apiPatch, apiDelete } from "./client";

export type UserRow = {
  id: number;
  username: string;
  role: "admin" | "user";
  must_change_password: boolean;
  created_at: string;
};

export type UserWorkspace = { workspace: string; role: "manager" | "member" };

export const listUsers = () => apiGet<{ users: UserRow[] }>("/users");
export const createUser = (body: { username: string; password: string; role: "admin" | "user" }) =>
  apiPost<{ user: UserRow }>("/users", body);
export const deleteUser = (id: number) => apiDelete(`/users/${id}`);
export const updateRole = (id: number, role: "admin" | "user") =>
  apiPatch(`/users/${id}`, { role });
export const resetPassword = (id: number, new_password: string) =>
  apiPost<{ ok: true }>(`/users/${id}/reset-password`, { new_password });
export const getUserWorkspaces = (id: number) =>
  apiGet<{ workspaces: UserWorkspace[] }>(`/users/${id}/workspaces`);
```

- [ ] **Step 3: 实现 — members.ts 删 listUsers(UserLite 挪 users.ts)**

`packages/web/frontend/src/api/members.ts`: 删 `listUsers` 导出(改手输后无调用方)与 `UserLite` 类型(挪到 `users.ts` 复用——但 `UserRow` 更全,`MemberManagerDialog` 改手输后不再需用户列表类型)。改为:

```typescript
import { apiGet, apiPost, apiDelete } from "./client";

export type Member = { user_id: number; username: string; role: string };

const enc = encodeURIComponent;

export const getMembers = (ws: string) =>
  apiGet<{ members: Member[] }>(`/workspaces/${enc(ws)}/members`);
export const addMember = (ws: string, username: string, role: string = "member") =>
  apiPost(`/workspaces/${enc(ws)}/members`, { username, role });
export const removeMember = (ws: string, username: string) =>
  apiDelete(`/workspaces/${enc(ws)}/members/${enc(username)}`);
```

(删了 `listUsers` 与 `UserLite`;`MemberManagerDialog` Task 9 改手输不再 import 它们)

- [ ] **Step 4: 实现 — RequireAdmin.tsx**

新建 `packages/web/frontend/src/auth/RequireAdmin.tsx`(类比 `RequireAuth`):

```typescript
import { Navigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "./AuthContext";

export function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    return <div className="flex min-h-screen items-center justify-center text-muted-foreground">Loading…</div>;
  }
  if (user?.role !== "admin") {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}
```

- [ ] **Step 5: 写 RequireAdmin 测试** — 新建 `packages/web/frontend/src/auth/RequireAdmin.test.tsx`

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { RequireAdmin } from "./RequireAdmin";
import { AuthContext, type AuthState } from "./AuthContext";

function wrap(user: AuthState["user"], loading = false) {
  const value: AuthState = {
    user, loading,
    login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn(),
  };
  return render(
    <MemoryRouter>
      <AuthContext.Provider value={value}>
        <RequireAdmin><div>protected</div></RequireAdmin>
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

describe("RequireAdmin", () => {
  it("admin 渲染 children", () => {
    wrap({ id: 1, username: "admin", role: "admin", must_change_password: false });
    expect(screen.getByText("protected")).toBeInTheDocument();
  });

  it("非 admin 跳转(Navigate to /)", () => {
    wrap({ id: 2, username: "alice", role: "user", must_change_password: false });
    expect(screen.queryByText("protected")).toBeNull();
  });
});
```

- [ ] **Step 6: 跑 RequireAdmin 测试**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/auth/RequireAdmin.test.tsx`
Expected: 2 PASS

- [ ] **Step 7: 实现 — router.tsx 加 /users 路由**

在 `router.tsx` import 区加:
```typescript
import { UsersPage } from "./pages/UsersPage";
import { RequireAdmin } from "./auth/RequireAdmin";
```

在 `{ path: "/settings", element: <SettingsPage /> }` 之后(同级 children 数组内)加:
```typescript
      { path: "/users", element: <RequireAdmin><UsersPage /></RequireAdmin> },
```

> 注: `UsersPage` 在 Task 5 创建。本步先加路由 import,若按 task 顺序执行 Task 5 在后,此步会 tsc 报缺 `UsersPage`——**调整: 先做 Task 5 再接 router,或本步建 UsersPage 占位**。为保持每步可编译,本 task 末尾建 `UsersPage` 最小占位(下一 task 填充),或在 Task 5 完成后再加此 router 行。**推荐: 把 router.tsx 的 /users 行放到 Task 5 末尾**(UsersPage 已存在时)。本 task **跳过 router 改动**,仅做 apiPatch/users.ts/RequireAdmin/UserMenu/i18n。

修正: 本 task 不动 router.tsx(移到 Task 5 末尾)。UserMenu 入口 `Link to="/users"` 在 router 有 /users 前点击会 404,但 Task 5 紧接着补,可接受。

- [ ] **Step 8: 实现 — UserMenu.tsx 加入口**

`packages/web/frontend/src/components/layout/UserMenu.tsx`: import `Link`,在 `PopoverContent` 的用户信息块与 logout 按钮之间加(admin 才显):

```typescript
import { Link } from "react-router-dom";
// ...
// 在 PopoverContent 内, <div>用户信息</div> 之后, logout Button 之前:
        {isAdmin && (
          <Button variant="ghost" className="w-full justify-start" asChild>
            <Link to="/users" data-testid="user-mgmt-link" onClick={() => setOpen(false)}>
              {t("users.manageLink")}
            </Link>
          </Button>
        )}
```

- [ ] **Step 9: 实现 — i18n 键**

`packages/web/frontend/src/locales/zh.json` 加(`users` 命名空间):
```json
{
  "users": {
    "manageLink": "用户管理",
    "title": "用户管理",
    "subtitle": "管理账号与工作区归属分配",
    "create": "新建用户",
    "username": "用户名",
    "role": "全局角色",
    "roleAdmin": "管理员",
    "roleUser": "普通用户",
    "mustChange": "待改密",
    "createdAt": "创建时间",
    "actions": "操作",
    "expand": "工作区归属",
    "delete": "删除",
    "resetPassword": "重置密码",
    "loadFailed": "加载用户失败",
    "created": "用户已创建",
    "createFailed": "创建失败",
    "deleted": "用户已删除",
    "deleteFailed": "删除失败",
    "deleteConfirm": "确认删除用户 {{name}}?该操作不可撤销,会同时移除其所有工作区成员关系与登录态。",
    "deleteConfirmBtn": "确认删除",
    "cancel": "取消",
    "password": "初始密码",
    "passwordHint": "用户首次登录需改密",
    "roleChanged": "角色已更新",
    "roleChangeFailed": "角色更新失败",
    "passwordReset": "密码已重置",
    "passwordResetFailed": "重置失败",
    "newPassword": "新临时密码",
    "members": {
      "title": "工作区归属",
      "workspace": "工作区",
      "wsRole": "角色",
      "add": "加入",
      "remove": "移除",
      "save": "保存",
      "saved": "归属已更新",
      "saveFailed": "部分更新失败,已刷新",
      "notMember": "未加入",
      "empty": "该用户未加入任何工作区"
    }
  },
  "members": {
    "input": {
      "placeholder": "输入用户名",
      "addFailed": "加入失败",
      "notFound": "用户不存在"
    }
  }
}
```

`en.json` 同结构英文翻译:`manageLink`:"User Management", `title`:"User Management", `subtitle`:"Manage accounts and workspace membership", `create`:"New User", `username`:"Username", `role`:"Global Role", `roleAdmin`:"Admin", `roleUser`:"User", `mustChange`:"Password Change Due", `createdAt`:"Created", `actions`:"Actions", `expand`:"Workspace Membership", `delete`:"Delete", `resetPassword`:"Reset Password", `loadFailed`:"Failed to load users", `created`:"User created", `createFailed`:"Create failed", `deleted`:"User deleted", `deleteFailed`:"Delete failed", `deleteConfirm`:"Delete user {{name}}? This cannot be undone and removes all workspace memberships and sessions.", `deleteConfirmBtn`:"Confirm Delete", `cancel`:"Cancel", `password`:"Initial Password", `passwordHint`:"User must change on first login", `roleChanged`:"Role updated", `roleChangeFailed`:"Role update failed", `passwordReset`:"Password reset", `passwordResetFailed`:"Reset failed", `newPassword`:"New temporary password", `members.title`:"Workspace Membership", `members.workspace`:"Workspace", `members.wsRole`:"Role", `members.add`:"Add", `members.remove`:"Remove", `members.save`:"Save", `members.saved`:"Membership updated", `members.saveFailed`:"Some updates failed; refreshed", `members.notMember`:"Not a member", `members.empty`:"Not a member of any workspace", `members.input.placeholder`:"Enter username", `members.input.addFailed`:"Add failed", `members.input.notFound`:"User not found"。

> **注**: `en.json`/`zh.json` 现有 `members.*`(MemberManagerDialog 用,如 `members.title`/`members.add`)。新增的 `members.input.*` 是子命名空间,不冲突。**勿覆盖现有 `members.*` 顶层键**——只新增 `members.input`。

- [ ] **Step 10: tsc + Commit**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 0 error(UserMenu 的 Link/users.ts 等)

```bash
git add packages/web/frontend/src/api/client.ts packages/web/frontend/src/api/users.ts packages/web/frontend/src/api/members.ts packages/web/frontend/src/auth/RequireAdmin.tsx packages/web/frontend/src/auth/RequireAdmin.test.tsx packages/web/frontend/src/components/layout/UserMenu.tsx packages/web/frontend/src/locales/zh.json packages/web/frontend/src/locales/en.json
git commit -m "feat(web): 用户管理基建(apiPatch/users client/RequireAdmin/UserMenu入口)"
```

---

## Task 5: UsersPage 表格(只读列表)

**Files:**
- Create: `packages/web/frontend/src/pages/UsersPage.tsx`
- Create: `packages/web/frontend/src/pages/UsersPage.test.tsx`
- Modify: `packages/web/frontend/src/router.tsx`(/users 路由,Task 4 推迟到此)

**Interfaces:**
- Consumes: Task 4 的 `listUsers()`/`UserRow`;`useAuth`
- Produces: `<UsersPage />` 表格(后续 Task 6/7/8 在此加 Dialog/面板)

- [ ] **Step 1: 写失败测试** — 新建 `pages/UsersPage.test.tsx`

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { UsersPage } from "./UsersPage";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 1, username: "admin", role: "admin", must_change_password: false } }),
}));

function renderPage() {
  return render(<MemoryRouter><UsersPage /></MemoryRouter>);
}

describe("UsersPage", () => {
  beforeEach(() => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response(JSON.stringify({
      users: [
        { id: 1, username: "admin", role: "admin", must_change_password: false, created_at: "2026-07-27T00:00:00Z" },
        { id: 2, username: "alice", role: "user", must_change_password: true, created_at: "2026-07-27T00:00:00Z" },
      ],
    }), { status: 200 }));
  });

  it("加载并渲染用户表格", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("admin")).toBeInTheDocument());
    expect(screen.getByText("alice")).toBeInTheDocument();
  });

  it("must_change 用户显示标记", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("alice")).toBeInTheDocument());
    expect(screen.getByText("users.mustChange")).toBeInTheDocument();
  });

  it("加载失败显错误态", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("err", { status: 500 }));
    renderPage();
    await waitFor(() => expect(screen.getByText("users.loadFailed")).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/pages/UsersPage.test.tsx`
Expected: FAIL(`UsersPage` 未导出)

- [ ] **Step 3: 实现 — UsersPage.tsx(只读表格,留 Task 6/7/8 的操作 slot)**

新建 `packages/web/frontend/src/pages/UsersPage.tsx`:

```typescript
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { PageHeader } from "@/components/PageHeader";
import { ErrorState } from "@/components/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { listUsers, type UserRow } from "@/api/users";

export function UsersPage() {
  const { t } = useTranslation();
  const [users, setUsers] = useState<UserRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setLoading(true); setError(null);
    try {
      const r = await listUsers();
      setUsers(r.users);
    } catch {
      setError(t("users.loadFailed"));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void refresh(); }, []);

  return (
    <div className="space-y-4">
      <PageHeader title={t("users.title")} />
      <p className="text-sm text-muted-foreground">{t("users.subtitle")}</p>
      {loading && <Skeleton className="h-40 w-full" />}
      {error && <ErrorState message={error} onRetry={refresh} />}
      {!loading && !error && (
        <table className="w-full text-sm">
          <thead className="text-left text-muted-foreground">
            <tr>
              <th className="py-2">{t("users.username")}</th>
              <th className="py-2">{t("users.role")}</th>
              <th className="py-2">{t("users.mustChange")}</th>
              <th className="py-2">{t("users.createdAt")}</th>
              <th className="py-2">{t("users.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-t" data-testid={`user-row-${u.username}`}>
                <td className="py-2 font-mono">{u.username}</td>
                <td className="py-2">{t(`users.role${u.role === "admin" ? "Admin" : "User"}`)}</td>
                <td className="py-2">{u.must_change_password && <Badge variant="outline" className="border-amber/50 text-amber">{t("users.mustChange")}</Badge>}</td>
                <td className="py-2 text-muted-foreground">{u.created_at.slice(0, 10)}</td>
                <td className="py-2">{/* Task 6/7/8 注入 Dialog 触发与归属展开 */}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
```

- [ ] **Step 4: 实现 — router.tsx 接 /users 路由**

`router.tsx` import 区加:
```typescript
import { UsersPage } from "./pages/UsersPage";
import { RequireAdmin } from "./auth/RequireAdmin";
```
`{ path: "/settings", element: <SettingsPage /> }` 之后加:
```typescript
      { path: "/users", element: <RequireAdmin><UsersPage /></RequireAdmin> },
```

- [ ] **Step 5: 跑测试验证通过 + tsc**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/pages/UsersPage.test.tsx && ./node_modules/.bin/tsc --noEmit`
Expected: 3 PASS + 0 tsc error

- [ ] **Step 6: Commit**

```bash
git add packages/web/frontend/src/pages/UsersPage.tsx packages/web/frontend/src/pages/UsersPage.test.tsx packages/web/frontend/src/router.tsx
git commit -m "feat(web): UsersPage 只读表格 + /users 路由"
```

---

## Task 6: CreateUserDialog

**Files:**
- Create: `packages/web/frontend/src/components/CreateUserDialog.tsx`
- Create: `packages/web/frontend/src/components/CreateUserDialog.test.tsx`
- Modify: `packages/web/frontend/src/pages/UsersPage.tsx`(接入创建按钮)

**Interfaces:**
- Consumes: Task 4 的 `createUser`;`apiPost` 401/409 由 ApiError 抛
- Produces: `<CreateUserDialog open onOpenChange onCreated />`(UsersPage 接入)

- [ ] **Step 1: 写失败测试** — 新建 `components/CreateUserDialog.test.tsx`(参照 `ChangePasswordDialog.test.tsx` 模式)

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { CreateUserDialog } from "./CreateUserDialog";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

function fill(username: string, password: string) {
  fireEvent.change(screen.getByLabelText("users.username"), { target: { value: username } });
  fireEvent.change(screen.getByLabelText("users.password"), { target: { value: password } });
}

describe("CreateUserDialog", () => {
  beforeEach(() => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
  });

  it("提交成功调 onCreated 并关闭", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockResolvedValue(new Response(JSON.stringify({ user: { id: 3 } }), { status: 200 }));
    const onCreated = vi.fn(), onOpenChange = vi.fn();
    render(<CreateUserDialog open onOpenChange={onOpenChange} onCreated={onCreated} />);
    fill("bob", "bob-pw-12");
    fireEvent.click(screen.getByRole("button", { name: "users.create" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    const body = JSON.parse(fm.mock.calls[0][1]?.body as string);
    expect(body).toEqual({ username: "bob", password: "bob-pw-12", role: "user" });
  });

  it("username 重复(409)提示错误且不关闭", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 409 }));
    const onOpenChange = vi.fn();
    render(<CreateUserDialog open onOpenChange={onOpenChange} onCreated={vi.fn()} />);
    fill("alice", "alice-pw-12");
    fireEvent.click(screen.getByRole("button", { name: "users.create" }));
    await waitFor(() => expect(screen.getByText("users.createFailed")).toBeInTheDocument());
    expect(onOpenChange).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/components/CreateUserDialog.test.tsx`
Expected: FAIL(组件未导出)

- [ ] **Step 3: 实现 — CreateUserDialog.tsx**

```typescript
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { createUser } from "@/api/users";
import { ApiError } from "@/api/client";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}

export function CreateUserDialog({ open, onOpenChange, onCreated }: Props) {
  const { t } = useTranslation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"admin" | "user">("user");
  const [busy, setBusy] = useState(false);

  function reset() { setUsername(""); setPassword(""); setRole("user"); }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await createUser({ username, password, role });
      toast.success(t("users.created"));
      reset();
      onCreated();
      onOpenChange(false);
    } catch (err) {
      toast.error(t("users.createFailed"));
      // 409/400 等均显 createFailed,Dialog 不关闭让用户重试
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) reset(); onOpenChange(o); }}>
      <DialogContent>
        <DialogHeader><DialogTitle>{t("users.create")}</DialogTitle></DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="cu-user">{t("users.username")}</Label>
            <Input id="cu-user" value={username} onChange={(e) => setUsername(e.target.value)} required />
          </div>
          <div className="space-y-2">
            <Label htmlFor="cu-pw">{t("users.password")}</Label>
            <Input id="cu-pw" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            <p className="text-xs text-muted-foreground">{t("users.passwordHint")}</p>
          </div>
          <div className="space-y-2">
            <Label>{t("users.role")}</Label>
            <Select value={role} onValueChange={(v) => setRole(v as "admin" | "user")}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="user">{t("users.roleUser")}</SelectItem>
                <SelectItem value="admin">{t("users.roleAdmin")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t("users.cancel")}</Button>
            <Button type="submit" disabled={busy}>{busy ? "…" : t("users.create")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 4: 接入 UsersPage** — 顶部加"新建用户"按钮 + Dialog

`UsersPage.tsx`: import `CreateUserDialog` + `Button`;在 `<PageHeader>` 之后加:
```typescript
      <div className="flex justify-end">
        <Button onClick={() => setCreateOpen(true)}>{t("users.create")}</Button>
      </div>
      <CreateUserDialog open={createOpen} onOpenChange={setCreateOpen} onCreated={refresh} />
```
组件内加 `const [createOpen, setCreateOpen] = useState(false);`(顶部 state 区)。`Button` 已 import 自 `@/components/ui/button`(若未 import 则补)。

- [ ] **Step 5: 跑测试验证通过 + tsc**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/components/CreateUserDialog.test.tsx src/pages/UsersPage.test.tsx && ./node_modules/.bin/tsc --noEmit`
Expected: PASS + 0 error

- [ ] **Step 6: Commit**

```bash
git add packages/web/frontend/src/components/CreateUserDialog.tsx packages/web/frontend/src/components/CreateUserDialog.test.tsx packages/web/frontend/src/pages/UsersPage.tsx
git commit -m "feat(web): CreateUserDialog + 接入 UsersPage"
```

---

## Task 7: 账号操作(全局角色改 + 重置密码 + 删除确认)

**Files:**
- Create: `packages/web/frontend/src/components/ResetPasswordDialog.tsx` + `.test.tsx`
- Create: `packages/web/frontend/src/components/ConfirmDeleteUserDialog.tsx` + `.test.tsx`
- Modify: `packages/web/frontend/src/pages/UsersPage.tsx`(行内角色 Select + 重置/删除按钮)

**Interfaces:**
- Consumes: Task 4 的 `updateRole`/`resetPassword`/`deleteUser`;`useAuth`(自删/自降前端禁用)
- Produces: 行内角色改 + ResetPasswordDialog + ConfirmDeleteUserDialog,接入 UsersPage 表格 actions 列

- [ ] **Step 1: 写 ResetPasswordDialog 测试** — `components/ResetPasswordDialog.test.tsx`

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ResetPasswordDialog } from "./ResetPasswordDialog";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe("ResetPasswordDialog", () => {
  beforeEach(() => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
  });

  it("提交成功 POST reset-password", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    const onOpenChange = vi.fn();
    render(<ResetPasswordDialog userId={2} open onOpenChange={onOpenChange} />);
    fireEvent.change(screen.getByLabelText("users.newPassword"), { target: { value: "new-pw-12" } });
    fireEvent.click(screen.getByRole("button", { name: "users.resetPassword" }));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    const body = JSON.parse(fm.mock.calls[0][1]?.body as string);
    expect(body).toEqual({ new_password: "new-pw-12" });
  });
});
```

- [ ] **Step 2: 实现 ResetPasswordDialog.tsx**(单字段新临时密码;提交 `resetPassword(id, new_password)`)

```typescript
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { resetPassword } from "@/api/users";

export function ResetPasswordDialog({ userId, open, onOpenChange }: {
  userId: number; open: boolean; onOpenChange: (o: boolean) => void;
}) {
  const { t } = useTranslation();
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await resetPassword(userId, pw);
      toast.success(t("users.passwordReset"));
      setPw("");
      onOpenChange(false);
    } catch {
      toast.error(t("users.passwordResetFailed"));
    } finally { setBusy(false); }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader><DialogTitle>{t("users.resetPassword")}</DialogTitle></DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="rp-pw">{t("users.newPassword")}</Label>
            <Input id="rp-pw" type="password" value={pw} onChange={(e) => setPw(e.target.value)} required />
            <p className="text-xs text-muted-foreground">{t("users.passwordHint")}</p>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t("users.cancel")}</Button>
            <Button type="submit" disabled={busy}>{busy ? "…" : t("users.resetPassword")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 3: 写 ConfirmDeleteUserDialog 测试** — `components/ConfirmDeleteUserDialog.test.tsx`

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ConfirmDeleteUserDialog } from "./ConfirmDeleteUserDialog";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string, o?: any) => k.replace("{{name}}", o?.name ?? "") }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe("ConfirmDeleteUserDialog", () => {
  beforeEach(() => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
  });

  it("需点确认按钮才删(防误删)", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    const onDeleted = vi.fn(), onOpenChange = vi.fn();
    render(<ConfirmDeleteUserDialog user={{ id: 2, username: "alice", role: "user", must_change_password: false, created_at: "" }} open onOpenChange={onOpenChange} onDeleted={onDeleted} />);
    fireEvent.click(screen.getByRole("button", { name: "users.deleteConfirmBtn" }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
    expect(fm.mock.calls[0][1]?.method).toBe("DELETE");
  });
});
```

- [ ] **Step 4: 实现 ConfirmDeleteUserDialog.tsx**(二次确认按钮,显式文案含用户名)

```typescript
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { deleteUser, type UserRow } from "@/api/users";

export function ConfirmDeleteUserDialog({ user, open, onOpenChange, onDeleted }: {
  user: UserRow; open: boolean; onOpenChange: (o: boolean) => void; onDeleted: () => void;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  async function onConfirm() {
    setBusy(true);
    try {
      await deleteUser(user.id);
      toast.success(t("users.deleted"));
      onDeleted();
      onOpenChange(false);
    } catch {
      toast.error(t("users.deleteFailed"));
    } finally { setBusy(false); }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader><DialogTitle>{t("users.delete")}</DialogTitle></DialogHeader>
        <p className="text-sm text-destructive">{t("users.deleteConfirm", { name: user.username })}</p>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t("users.cancel")}</Button>
          <Button variant="destructive" onClick={onConfirm} disabled={busy}>{busy ? "…" : t("users.deleteConfirmBtn")}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 5: 接入 UsersPage — 行内角色 Select + 重置/删除按钮**

`UsersPage.tsx` import `ResetPasswordDialog`/`ConfirmDeleteUserDialog`/`updateRole`/`Select...`/`useAuth`;actions 列(`<td>`)替换为:
```typescript
                <td className="py-2 space-x-2">
                  <Select defaultValue={u.role} disabled={u.id === me?.id} onValueChange={async (v) => {
                    try { await updateRole(u.id, v as "admin" | "user"); toast.success(t("users.roleChanged")); void refresh(); }
                    catch { toast.error(t("users.roleChangeFailed")); }
                  }}>
                    <SelectTrigger className="w-28"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="user">{t("users.roleUser")}</SelectItem>
                      <SelectItem value="admin">{t("users.roleAdmin")}</SelectItem>
                    </SelectContent>
                  </Select>
                  <Button variant="outline" size="sm" disabled={u.id === me?.id}
                          onClick={() => { setResetTarget(u); setResetOpen(true); }}>{t("users.resetPassword")}</Button>
                  <Button variant="outline" size="sm" disabled={u.id === me?.id}
                          onClick={() => { setDelTarget(u); setDelOpen(true); }}>{t("users.delete")}</Button>
                </td>
```
顶部加:`const { user: me } = useAuth();` + state `resetTarget/setResetOpen/delTarget/delOpen`;底部加:
```typescript
      {resetTarget && <ResetPasswordDialog userId={resetTarget.id} open={resetOpen} onOpenChange={setResetOpen} />}
      {delTarget && <ConfirmDeleteUserDialog user={delTarget} open={delOpen} onOpenChange={setDelOpen} onDeleted={refresh} />}
```
import `toast` from `sonner`。`me?.id` 用于禁用对自己的角色改/重置/删除(前端层;后端护栏是权威)。

- [ ] **Step 6: 跑测试 + tsc**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/components/ResetPasswordDialog.test.tsx src/components/ConfirmDeleteUserDialog.test.tsx src/pages/UsersPage.test.tsx && ./node_modules/.bin/tsc --noEmit`
Expected: PASS + 0 error

- [ ] **Step 7: Commit**

```bash
git add packages/web/frontend/src/components/ResetPasswordDialog.tsx packages/web/frontend/src/components/ResetPasswordDialog.test.tsx packages/web/frontend/src/components/ConfirmDeleteUserDialog.tsx packages/web/frontend/src/components/ConfirmDeleteUserDialog.test.tsx packages/web/frontend/src/pages/UsersPage.tsx
git commit -m "feat(web): 账号操作(角色改/重置密码/删除确认)"
```

---

## Task 8: UserWorkspacesPanel(形态 A 集中归属编辑)

**Files:**
- Create: `packages/web/frontend/src/components/UserWorkspacesPanel.tsx` + `.test.tsx`
- Modify: `packages/web/frontend/src/pages/UsersPage.tsx`(每行可展开挂面板)

**Interfaces:**
- Consumes: Task 4 的 `getUserWorkspaces`;`listWorkspaces`/`getMembers`/`addMember`/`removeMember`(`api/members` + ws 列表);Task 3 的 PATCH 经 `apiPatch` 调用
- Produces: `<UserWorkspacesPanel user />` 展开:勾选加入 ws + 改 ws 角色 + 移除

> **全部 ws 清单**: `GET /api/workspaces` 返 `{name, ...}[]`(admin 全见)。用现有 `apiGet<...[]>("/workspaces")`(types 有 `Workspace`?若无则内联 type)。

- [ ] **Step 1: 写失败测试** — `components/UserWorkspacesPanel.test.tsx`

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { UserWorkspacesPanel } from "./UserWorkspacesPanel";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const USER = { id: 2, username: "alice", role: "user" as const, must_change_password: false, created_at: "" };

function mockFetchSeq(responses: { status: number; body: any }[]) {
  let i = 0;
  vi.spyOn(window, "fetch").mockImplementation(async () => {
    const r = responses[Math.min(i++, responses.length - 1)];
    return new Response(JSON.stringify(r.body), { status: r.status });
  });
}

describe("UserWorkspacesPanel", () => {
  beforeEach(() => {
    // GET /users/2/workspaces -> 已加入 ws-a(member); GET /workspaces -> [ws-a, ws-b]
    mockFetchSeq([
      { status: 200, body: { workspaces: [{ workspace: "ws-a", role: "member" }] } },
      { status: 200, body: [{ name: "ws-a" }, { name: "ws-b" }] },
    ]);
  });

  it("加载已加入归属 + 全部 ws 清单", async () => {
    render(<UserWorkspacesPanel user={USER} />);
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    expect(screen.getByText("ws-b")).toBeInTheDocument();  // 未加入的也在勾选清单
  });

  it("点加入 ws-b -> POST members", async () => {
    const fm = vi.spyOn(window, "fetch");
    render(<UserWorkspacesPanel user={USER} />);
    await waitFor(() => expect(screen.getByText("ws-b")).toBeInTheDocument());
    // ws-b 行的"加入"按钮
    fireEvent.click(screen.getByTestId("add-ws-b"));
    await waitFor(() => expect(fm.mock.calls.some(c => (c[1]?.method) === "POST")).toBe(true));
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/components/UserWorkspacesPanel.test.tsx`
Expected: FAIL(组件未导出)

- [ ] **Step 3: 实现 — UserWorkspacesPanel.tsx**

```typescript
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { getUserWorkspaces, type UserRow, type UserWorkspace } from "@/api/users";
import { getMembers, addMember, removeMember } from "@/api/members";
import { apiPatch, apiGet } from "@/api/client";

type WsInfo = { name: string };

export function UserWorkspacesPanel({ user }: { user: UserRow }) {
  const { t } = useTranslation();
  const [memberOf: MemberOf] = useState<Record<string, "manager" | "member">>({});
  const [allWs, setAllWs] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      const [own, wsList] = await Promise.all([
        getUserWorkspaces(user.id),
        apiGet<WsInfo[]>("/workspaces"),
      ]);
      const map: Record<string, "manager" | "member"> = {};
      own.workspaces.forEach((w: UserWorkspace) => { map[w.workspace] = w.role; });
      setMemberOf(map);
      setAllWs(wsList.map((w) => w.name));
    } catch { /* 面内错误态 */ } finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, [user.id]);

  type MemberOf = Record<string, "manager" | "member">;

  async function onAdd(ws: string) {
    try {
      await addMember(ws, user.username, "member");
      toast.success(t("users.members.saved"));
      void load();
    } catch { toast.error(t("users.members.saveFailed")); void load(); }
  }
  async function onRoleChange(ws: string, role: "manager" | "member") {
    try {
      await apiPatch(`/workspaces/${encodeURIComponent(ws)}/members/${encodeURIComponent(user.username)}`, { role });
      toast.success(t("users.members.saved"));
      void load();
    } catch { toast.error(t("users.members.saveFailed")); void load(); }
  }
  async function onRemove(ws: string) {
    try {
      await removeMember(ws, user.username);
      toast.success(t("users.members.saved"));
      void load();
    } catch { toast.error(t("users.members.saveFailed")); void load(); }
  }

  if (loading) return <Skeleton className="h-20 w-full" />;
  return (
    <div className="rounded border p-3 space-y-2" data-testid={`wsp-${user.username}`}>
      <p className="text-sm font-medium">{t("users.members.title")}</p>
      {allWs.length === 0 && <p className="text-sm text-muted-foreground">{t("users.members.empty")}</p>}
      {allWs.map((ws) => {
        const role = memberOf[ws];
        return (
          <div key={ws} className="flex items-center justify-between text-sm">
            <span className="font-mono">{ws}</span>
            {role ? (
              <>
                <Select value={role} onValueChange={(v) => onRoleChange(ws, v as "manager" | "member")}>
                  <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="member">{t("users.members.wsRole")} member</SelectItem>
                    <SelectItem value="manager">manager</SelectItem>
                  </SelectContent>
                </Select>
                <Button variant="ghost" size="sm" onClick={() => onRemove(ws)}>{t("users.members.remove")}</Button>
              </>
            ) : (
              <Button variant="outline" size="sm" data-testid={`add-${ws}`} onClick={() => onAdd(ws)}>{t("users.members.add")}</Button>
            )}
          </div>
        );
      })}
    </div>
  );
}
```

> 注: `getMembers` import 实际本组件未直接用(归属来自 `getUserWorkspaces`),可去掉该 import 避免未用告警——以 tsc 为准,若 lint 报 unused 则删 `getMembers` from import。

- [ ] **Step 4: 接入 UsersPage — 行可展开**

`UsersPage.tsx`: 给 `<tr>` 加展开态 `expanded` set;username 列前加展开按钮(▶/▼);展开时在行下渲染 `<UserWorkspacesPanel user={u} />`(占满列:`<tr><td colSpan={5}>...`)。简化实现:

```typescript
// state: const [expanded, setExpanded] = useState<Set<number>>(new Set());
// username <td> 改为:
                <td className="py-2 font-mono">
                  <button onClick={() => setExpanded((s) => { const n = new Set(s); n.has(u.id) ? n.delete(u.id) : n.add(u.id); return n; })}>
                    {expanded.has(u.id) ? "▼" : "▶"}
                  </button>{" "}{u.username}
                </td>
// 表格 tbody 内,每个 user 行之后条件渲染展开行:
            {users.flatMap((u) => [
              (<tr key={u.id} ...>...</tr>),
              expanded.has(u.id) ? (
                <tr key={`${u.id}-wsp`} className="border-t"><td colSpan={5}><UserWorkspacesPanel user={u} /></td></tr>
              ) : false,
            ] as const)}
```

> `flatMap` + colSpan 展开行是表格内展开的标准模式。若 TS 推断 `false` 不合法,改 `null`。

- [ ] **Step 5: 跑测试 + tsc**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/components/UserWorkspacesPanel.test.tsx src/pages/UsersPage.test.tsx && ./node_modules/.bin/tsc --noEmit`
Expected: PASS + 0 error

- [ ] **Step 6: Commit**

```bash
git add packages/web/frontend/src/components/UserWorkspacesPanel.tsx packages/web/frontend/src/components/UserWorkspacesPanel.test.tsx packages/web/frontend/src/pages/UsersPage.tsx
git commit -m "feat(web): UserWorkspacesPanel 形态A集中归属编辑"
```

---

## Task 9: MemberManagerDialog 改手输 username + 回归

**Files:**
- Modify: `packages/web/frontend/src/components/MemberManagerDialog.tsx`(下拉选人 → 手输)
- Modify: `packages/web/frontend/src/components/MemberManagerDialog.test.tsx`(回归)

**Interfaces:**
- Consumes: Task 4 后 `listUsers` 已从 members.ts 删除;`addMember` 按 username 加(后端按 username 查 user,404=不存在)
- Produces: MemberManagerDialog 不再调 `listUsers`(GET /api/users 收紧到 admin 后 non-admin manager 拉不到),改为手输 username

- [ ] **Step 1: 改测试** — `components/MemberManagerDialog.test.tsx`

现有测试断言"下拉选用户"(`listUsers` mock + Select)。改为"手输 username + 加入"。关键用例:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MemberManagerDialog } from "./MemberManagerDialog";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 2, username: "alice", role: "user", must_change_password: false } }),
}));

function mockSeq(responses: { status: number; body: any }[]) {
  let i = 0;
  vi.spyOn(window, "fetch").mockImplementation(async () => {
    const r = responses[Math.min(i++, responses.length - 1)];
    return new Response(JSON.stringify(r.body), { status: r.status });
  });
}

describe("MemberManagerDialog", () => {
  beforeEach(() => {
    // GET members -> alice 是 manager(能见按钮);后续 addMember POST -> 200
    mockSeq([
      { status: 200, body: { members: [{ user_id: 2, username: "alice", role: "manager" }] } },
    ]);
  });

  it("手输 username 加入(不调 listUsers)", async () => {
    const fm = vi.spyOn(window, "fetch");
    render(<MemoryRouter><MemberManagerDialog ws="ws-a" /></MemoryRouter>);
    fireEvent.click(screen.getByTestId("member-manager"));
    await waitFor(() => expect(screen.getByPlaceholderText("members.input.placeholder")).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText("members.input.placeholder"), { target: { value: "bob" } });
    fm.mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    fireEvent.click(screen.getByRole("button", { name: "members.add" }));
    await waitFor(() => {
      const addCall = fm.mock.calls.find(c => (c[0] as string)?.includes("/members") && (c[1]?.method) === "POST");
      expect(addCall).toBeTruthy();
    });
    // 确认未调 GET /users(listUsers 已删)
    expect(fm.mock.calls.some(c => (c[0] as string)?.includes("/api/users") && (c[1] === undefined))).toBe(false);
  });

  it("加入不存在用户(404)提示错误", async () => {
    render(<MemoryRouter><MemberManagerDialog ws="ws-a" /></MemoryRouter>);
    fireEvent.click(screen.getByTestId("member-manager"));
    await waitFor(() => expect(screen.getByPlaceholderText("members.input.placeholder")).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText("members.input.placeholder"), { target: { value: "nobody" } });
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 404 }));
    fireEvent.click(screen.getByRole("button", { name: "members.add" }));
    await waitFor(() => expect(screen.getByText("members.input.notFound")).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: 实现 — MemberManagerDialog.tsx 改手输**

替换原"下拉选人"块(原 `users` state + `listUsers` + `<Select>` picked)为手输 Input。关键改动:

```typescript
// 删: const [users, setUsers] = useState<UserLite[]>([]);  (含 import listUsers)
// 删: onOpen 里 setUsers((await listUsers()).users);
// 改 picked state 语义为手输值(保留 const [picked, setPicked] = useState("");)
// onAdd 成功后 setPicked("") 清空
// 渲染: 下拉 Select -> Input:
        <div className="flex items-center gap-2">
          <Input value={picked} onChange={(e) => setPicked(e.target.value)}
                 placeholder={t("members.input.placeholder")} className="flex-1" />
          <Button onClick={onAdd} disabled={!picked}>{t("members.add")}</Button>
        </div>
```
`onAdd` 里 `addMember(ws, picked, "member")` 不变;catch 内 404 显 `members.input.notFound`、其余 `members.addFailed`:
```typescript
  async function onAdd() {
    if (!picked) return;
    try {
      await addMember(ws, picked, "member");
      setPicked("");
      setMembers((await getMembers(ws)).members);
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      toast.error(status === 404 ? t("members.input.notFound") : t("members.addFailed"));
    }
  }
```
import 补:`Input` from `@/components/ui/input`、`ApiError` from `@/api/client`;删 `listUsers`/`UserLite` import(已从 members.ts 移除)。`onOpen` 简化为仅 `setOpen(true)`(不再拉用户列表)。

- [ ] **Step 3: 跑测试 + tsc**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/components/MemberManagerDialog.test.tsx && ./node_modules/.bin/tsc --noEmit`
Expected: PASS + 0 error

- [ ] **Step 4: Commit**

```bash
git add packages/web/frontend/src/components/MemberManagerDialog.tsx packages/web/frontend/src/components/MemberManagerDialog.test.tsx
git commit -m "feat(web): MemberManagerDialog 改手输 username(GET /users 收紧配套)"
```

---

## Self-Review(plan 写完后自查,已修正)

**1. Spec coverage**: 账号 CRUD(Task 1-2,6-7)、集中分配形态 A(Task 8)、GET 收紧 + MemberManagerDialog 手输(Task 2,9)、members PATCH(Task 3)、删用户清三表(Task 1)、create/reset 置 must_change(Task 1-2)、护栏(Task 1-3)、i18n/入口/守卫(Task 4-5)——spec 各节均有 task 覆盖。✓

**2. Placeholder scan**: 无 TBD/TODO。代码块完整。`UserWorkspacesPanel` 注释里提示 `getMembers` 可能 unused(以 tsc 为准)——已注明处理方式,非占位。

**3. Type consistency**: `UserRow`(Task 4 定义)在 Task 5/7/8/9 一致;`UserWorkspace`(Task 4)在 Task 8 一致;`delete_user`/`update_role`/`reset_password`/`list_user_workspaces_with_role`/`update_workspace_member_role`(Task 1)在 Task 2/3 调用名一致;`apiPatch`(Task 4)在 Task 8 用。✓

**4. 已知 follow-up(plan 外,留独立 issue)**:
- members `add_member`/`remove_member` 缺 CSRF 校验(预存);本 plan 仅新 PATCH 加 CSRF。
- `auth/routes._NEW_PASSWORD_MIN_LEN` 与 `passwords.NEW_PASSWORD_MIN_LEN` 两常量待统一。
- 真机冒烟(`docker compose build web && up -d web`):admin 登录 → UserMenu 进 /users → 创建用户/改角色/重置/删除/展开分配 ws → 被 (must_change=1) 用户登录验证改密提醒。

## Execution Handoff

见下(offer 执行方式)。
