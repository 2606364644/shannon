# 用户专属工作区与全局 admin 权限 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建用户时自动创建同名工作区，并让只有用户名为 `admin` 的管理员拥有全部工作区权限；其他超管仅访问其成员工作区或自己创建的工作区。

**Architecture:** 在 Web 应用层增加幂等的 workspace provisioner，负责创建用户同名工作区、成员关系和 canonical admin 补偿，不让 `AuthStore` 依赖文件系统。启动 seed/bootstrap 后补齐历史用户和历史工作区；工作区鉴权统一使用 `username == "admin" and role == "admin"` 的全局管理员判定，而 `require_admin` 继续保留所有超管的系统管理权限。

**Tech Stack:** Python 3.12, FastAPI, SQLite (`AuthStore`), pytest/TestClient, workspace `workspace.json` 元数据。

---

## 文件变更总览

- Create: `packages/web/src/supernova_web/components/workspace_provisioner.py` — 用户同名工作区创建、canonical admin 识别与幂等成员补偿。
- Create: `packages/web/tests/test_workspace_provisioning.py` — provisioner、历史补偿和安全边界的单元测试。
- Modify: `packages/web/src/supernova_web/auth/dependencies.py` — 统一全局 admin 判定，收紧工作区成员/manager 鉴权。
- Modify: `packages/web/src/supernova_web/app.py` — 启动 seed/bootstrap 后补齐用户工作区与 canonical admin；替换旧的“所有 admin 分配”迁移逻辑。
- Modify: `packages/web/src/supernova_web/api/users.py` — 创建用户时同步 provision 工作区；置顶鉴权和角色变更触发 canonical admin 补偿。
- Modify: `packages/web/src/supernova_web/api/workspaces.py` — 手动创建工作区时加入 canonical admin；列表只对 canonical admin 展示全部工作区。
- Modify: `packages/web/src/supernova_web/api/scans.py` — 跨工作区扫描列表只对 canonical admin 展示全部。
- Modify: `packages/web/src/supernova_web/api/scan.py` — 启动扫描时只对 canonical admin 绕过成员检查。
- Modify: `packages/web/tests/test_users_routes.py` — 用户创建与工作区成员关系、其他超管隔离测试。
- Modify: `packages/web/tests/test_auth_dependencies.py` — canonical admin 与其他 admin 的成员鉴权测试。
- Modify: `packages/web/tests/test_api_workspaces.py` 或新测试文件 — 手动创建工作区默认成员测试。
- Modify: `packages/web/tests/test_scans_cross_ws.py` — 其他超管跨工作区扫描隔离测试。
- Modify: `packages/web/tests/test_pinned_workspace.py` — 其他超管 pin 非成员工作区被拒绝测试。
- Modify: `packages/web/tests/test_legacy_migration.py` — 历史工作区只补 canonical `admin`，即使已有其他成员也要补齐。

---

### Task 1: 先写 workspace provisioner 的失败测试

**Files:**
- Create: `packages/web/tests/test_workspace_provisioning.py`
- Modify: `packages/web/tests/test_auth_dependencies.py`

- [ ] **Step 1: 写 canonical admin 判定和用户工作区的失败测试**

在新测试文件中加入以下测试（测试导入的模块和函数此时尚不存在，确保先红）：

```python
from pathlib import Path

import pytest

from supernova_web.auth.models import User
from supernova_web.auth.store import AuthStore
from supernova_web.components.scan_store import write_workspace_meta
from supernova_web.components.workspace_provisioner import (
    ensure_global_admin_access,
    ensure_user_workspace,
    is_global_admin,
)


def _store(tmp_path: Path) -> AuthStore:
    store = AuthStore(str(tmp_path / "auth.db"))
    store.init_schema()
    return store


def test_is_global_admin_requires_exact_username_and_admin_role():
    assert is_global_admin(User(id=1, username="admin", role="admin")) is True
    assert is_global_admin(User(id=2, username="root", role="admin")) is False
    assert is_global_admin(User(id=3, username="admin", role="user")) is False


def test_ensure_user_workspace_creates_metadata_and_manager_memberships(tmp_path):
    store = _store(tmp_path)
    admin = store.create_user("admin", "h", role="admin")
    alice = store.create_user("alice", "h", role="user")

    ws_dir = ensure_user_workspace(tmp_path / "workspaces", store, alice)

    assert ws_dir == tmp_path / "workspaces" / "alice"
    assert (ws_dir / "workspace.json").exists()
    assert store.get_workspace_member_role("alice", alice.id) == "manager"
    assert store.get_workspace_member_role("alice", admin.id) == "manager"


def test_ensure_user_workspace_is_idempotent(tmp_path):
    store = _store(tmp_path)
    admin = store.create_user("admin", "h", role="admin")
    alice = store.create_user("alice", "h", role="user")

    first = ensure_user_workspace(tmp_path / "workspaces", store, alice)
    second = ensure_user_workspace(tmp_path / "workspaces", store, alice)

    assert first == second
    assert store.list_workspace_members("alice") == [
        (admin.id, "admin", "manager"),
        (alice.id, "alice", "manager"),
    ] or store.list_workspace_members("alice") == [
        (alice.id, "alice", "manager"),
        (admin.id, "admin", "manager"),
    ]


def test_ensure_user_workspace_rejects_unsafe_username(tmp_path):
    store = _store(tmp_path)
    user = store.create_user("alice/../escape", "h")

    with pytest.raises(ValueError, match="unsafe workspace name"):
        ensure_user_workspace(tmp_path / "workspaces", store, user)


def test_ensure_global_admin_access_adds_only_canonical_admin_to_all_workspaces(tmp_path):
    store = _store(tmp_path)
    admin = store.create_user("admin", "h", role="admin")
    other_admin = store.create_user("ops", "h", role="admin")
    alice = store.create_user("alice", "h", role="user")
    workspaces = tmp_path / "workspaces"
    for name in ("one", "two"):
        ws = workspaces / name
        ws.mkdir(parents=True)
        write_workspace_meta(ws, name=name, owner="seed")
    store.add_workspace_member("one", alice.id, "member")

    ensure_global_admin_access(workspaces, store)

    assert store.get_workspace_member_role("one", admin.id) == "manager"
    assert store.get_workspace_member_role("two", admin.id) == "manager"
    assert store.get_workspace_member_role("one", other_admin.id) is None
    assert store.get_workspace_member_role("two", other_admin.id) is None
    assert store.get_workspace_member_role("one", alice.id) == "member"
```

在 `test_auth_dependencies.py` 增加：

```python

def test_noncanonical_admin_must_be_workspace_member():
    ops = User(id=2, username="ops", role="admin")
    with pytest.raises(HTTPException) as exc:
        workspace_member(_req(ops, _FakeStore({})), "ws1", ops)
    assert exc.value.status_code == 403


def test_canonical_admin_bypasses_workspace_membership():
    admin = User(id=1, username="admin", role="admin")
    assert workspace_member(_req(admin, _FakeStore({})), "ws1", admin).id == 1
    assert workspace_manager(_req(admin, _FakeStore({})), "ws1", admin).id == 1
```

说明：`list_workspace_members()` 的 SQL 没有显式排序，因此幂等测试只验证两种合法数据库顺序；实现阶段若统一加排序，则保留单一确定断言。

- [ ] **Step 2: 运行新增测试，确认按预期失败**

Run:

```bash
uv run pytest packages/web/tests/test_workspace_provisioning.py packages/web/tests/test_auth_dependencies.py -q
```

Expected: 新 provisioner 导入失败，或失败原因是目标函数尚未实现；不得因为测试语法或 fixture 错误失败。

- [ ] **Step 3: Commit the failing tests**

```bash
git add packages/web/tests/test_workspace_provisioning.py packages/web/tests/test_auth_dependencies.py
git commit -m "test: specify user workspace provisioning rules"
```

---

### Task 2: 实现幂等 workspace provisioner

**Files:**
- Create: `packages/web/src/supernova_web/components/workspace_provisioner.py`

- [ ] **Step 1: 写最小实现**

创建模块，接口和实现如下：

```python
from __future__ import annotations

from pathlib import Path

from supernova_web.auth.models import User
from supernova_web.auth.store import AuthStore

from .scan_store import read_workspace_meta, write_workspace_meta

GLOBAL_ADMIN_USERNAME = "admin"


def is_global_admin(user: User) -> bool:
    return user.username == GLOBAL_ADMIN_USERNAME and user.role == "admin"


def _global_admin(store: AuthStore) -> User | None:
    user = store.get_user_by_username(GLOBAL_ADMIN_USERNAME)
    return user if user is not None and is_global_admin(user) else None


def _is_safe_workspace_name(name: str) -> bool:
    return bool(name) and name not in {".", ".."} and not name.startswith(".") \
        and Path(name).name == name and "/" not in name and "\\" not in name


def _workspace_is_real(ws_dir: Path) -> bool:
    return ws_dir.is_dir() and read_workspace_meta(ws_dir) is not None


def ensure_user_workspace(workspaces_dir: Path, store: AuthStore, user: User) -> Path:
    """幂等创建 username 工作区，并确保用户/admin 为 manager。"""
    if not _is_safe_workspace_name(user.username):
        raise ValueError("unsafe workspace name")

    workspaces_dir.mkdir(parents=True, exist_ok=True)
    ws_dir = workspaces_dir / user.username
    if ws_dir.exists() and not _workspace_is_real(ws_dir):
        raise FileExistsError(f"workspace conflict: {user.username}")
    if not ws_dir.exists():
        ws_dir.mkdir()
        write_workspace_meta(ws_dir, name=user.username, owner=user.username)

    store.add_workspace_member(user.username, user.id, "manager")
    admin = _global_admin(store)
    if admin is not None:
        store.add_workspace_member(user.username, admin.id, "manager")
    return ws_dir


def ensure_global_admin_member(workspace_name: str, store: AuthStore) -> None:
    admin = _global_admin(store)
    if admin is not None:
        store.add_workspace_member(workspace_name, admin.id, "manager")


def ensure_global_admin_access(workspaces_dir: Path, store: AuthStore) -> None:
    """将 canonical admin 幂等加入所有可见真实工作区。"""
    admin = _global_admin(store)
    if admin is None or not workspaces_dir.is_dir():
        return
    for ws_dir in workspaces_dir.iterdir():
        if not ws_dir.is_dir() or ws_dir.name.startswith("."):
            continue
        if _workspace_is_real(ws_dir):
            store.add_workspace_member(ws_dir.name, admin.id, "manager")


def ensure_all_user_workspaces(workspaces_dir: Path, store: AuthStore) -> None:
    """启动补偿：为历史用户补齐 username 工作区，并补齐 global admin。"""
    for user in store.list_all_users():
        try:
            ensure_user_workspace(workspaces_dir, store, user)
        except (FileExistsError, ValueError, OSError):
            # 单个坏用户名/冲突目录不阻断启动；后续可由管理员修复后重启重试。
            continue
    ensure_global_admin_access(workspaces_dir, store)
```

实现注意：`ensure_user_workspace()` 不覆盖已有 `workspace.json` 或 legacy `session.json`，只补成员；只有存在普通非工作区目录时抛出冲突。`ensure_global_admin_access()` 跳过点前缀系统目录，与 indexer 保持一致。

- [ ] **Step 2: 运行 provisioner 测试，确认通过**

Run:

```bash
uv run pytest packages/web/tests/test_workspace_provisioning.py -q
```

Expected: PASS。

- [ ] **Step 3: Commit provisioner**

```bash
git add packages/web/src/supernova_web/components/workspace_provisioner.py
git commit -m "feat: add idempotent user workspace provisioner"
```

---

### Task 3: 收紧工作区级鉴权，同时保留系统级超管权限

**Files:**
- Modify: `packages/web/src/supernova_web/auth/dependencies.py`
- Modify: `packages/web/src/supernova_web/api/workspaces.py`
- Modify: `packages/web/src/supernova_web/api/scans.py`
- Modify: `packages/web/src/supernova_web/api/scan.py`
- Modify: `packages/web/src/supernova_web/api/users.py`
- Modify: `packages/web/tests/test_auth_dependencies.py`
- Modify: `packages/web/tests/test_scans_cross_ws.py`
- Modify: `packages/web/tests/test_pinned_workspace.py`

- [ ] **Step 1: 写其他超管隔离的失败测试**

在 `test_auth_dependencies.py` 先把原有直接按 `role == "admin"` 放行的测试补成 canonical/非 canonical 两组；在 `test_scans_cross_ws.py` 增加一个 `ops` 登录后只能看到成员工作区扫描的测试；在 `test_pinned_workspace.py` 增加 `ops` pin 非成员工作区返回 403 的测试。测试应使用已有 TestClient fixture，关键断言为：

```python

def test_noncanonical_admin_cannot_pin_nonmember_workspace(...):
    # ops role=admin，无 ws-a membership
    response = ops_client.put(
        "/api/users/me/pinned-workspace",
        json={"workspace": "ws-a"},
        headers={"X-CSRF-Token": _csrf(ops_client)},
    )
    assert response.status_code == 403
```

- [ ] **Step 2: 运行隔离测试，确认当前实现失败**

Run:

```bash
uv run pytest packages/web/tests/test_auth_dependencies.py packages/web/tests/test_scans_cross_ws.py packages/web/tests/test_pinned_workspace.py -q
```

Expected: 新增的非 canonical admin 测试失败，表明现有 `role == "admin"` 仍然全局放行。

- [ ] **Step 3: 修改统一依赖和所有工作区级 bypass**

`auth/dependencies.py` 使用 provisioner 的 `is_global_admin`：

```python
from supernova_web.components.workspace_provisioner import is_global_admin


def workspace_member(request: Request, ws: str, user: User = Depends(current_user)) -> User:
    if is_global_admin(user):
        return user
    if request.app.state.auth_store.get_workspace_member_role(ws, user.id) is None:
        raise HTTPException(status_code=403, detail="not a workspace member")
    return user


def workspace_manager(request: Request, ws: str, user: User = Depends(current_user)) -> User:
    if is_global_admin(user):
        return user
    if request.app.state.auth_store.get_workspace_member_role(ws, user.id) != "manager":
        raise HTTPException(status_code=403, detail="workspace manager required")
    return user
```

同步修改：

- `workspaces.py:list_workspaces`：`if is_global_admin(user)` 才返回 `all_ws`。
- `scans.py:list_all_scans`：`if is_global_admin(user)` 才聚合全部工作区。
- `scan.py:create_scan`：仅 `is_global_admin(user)` 绕过 `workspace_members` 检查。
- `users.py:set_pinned_workspace`：仅 `is_global_admin(user)` 绕过成员检查。

`require_admin` 不变，仍然用 `user.role != "admin"` 判断系统管理权限。

- [ ] **Step 4: 运行隔离测试，确认通过**

Run:

```bash
uv run pytest packages/web/tests/test_auth_dependencies.py packages/web/tests/test_scans_cross_ws.py packages/web/tests/test_pinned_workspace.py -q
```

Expected: PASS。

- [ ] **Step 5: Commit workspace access tightening**

```bash
git add packages/web/src/supernova_web/auth/dependencies.py \
  packages/web/src/supernova_web/api/workspaces.py \
  packages/web/src/supernova_web/api/scans.py \
  packages/web/src/supernova_web/api/scan.py \
  packages/web/src/supernova_web/api/users.py \
  packages/web/tests/test_auth_dependencies.py \
  packages/web/tests/test_scans_cross_ws.py \
  packages/web/tests/test_pinned_workspace.py
git commit -m "fix: restrict workspace bypass to canonical admin"
```

---

### Task 4: 创建用户和手动工作区时接入 provisioning

**Files:**
- Modify: `packages/web/src/supernova_web/api/users.py`
- Modify: `packages/web/src/supernova_web/api/workspaces.py`
- Modify: `packages/web/tests/test_users_routes.py`
- Modify: `packages/web/tests/test_api_workspaces.py` 或 `packages/web/tests/test_workspace_lifecycle.py`

- [ ] **Step 1: 写创建用户和工作区默认成员的失败测试**

在 `test_users_routes.py::test_create_user_success` 增加：

```python
assert (app.state.config.workspaces_dir / "bob" / "workspace.json").exists()
bob = app.state.auth_store.get_user_by_username("bob")
admin = app.state.auth_store.get_user_by_username("admin")
assert app.state.auth_store.get_workspace_member_role("bob", bob.id) == "manager"
assert app.state.auth_store.get_workspace_member_role("bob", admin.id) == "manager"
```

增加其他超管创建用户的隔离场景：创建 `ops` 为 admin，创建 `bob` 后断言 `ops` 不在 `bob` 工作区成员中。

在工作区 API 测试中增加：

```python
# 已有 admin + ops，使用 ops 创建 ws-ops
response = ops_client.post("/api/workspaces", json={"name": "ws-ops"})
assert response.status_code == 201
assert store.get_workspace_member_role("ws-ops", ops.id) == "manager"
assert store.get_workspace_member_role("ws-ops", admin.id) == "manager"
# 其他超管没有被自动加入
assert store.get_workspace_member_role("ws-ops", another_admin.id) is None
```

- [ ] **Step 2: 运行测试确认当前实现失败**

Run:

```bash
uv run pytest packages/web/tests/test_users_routes.py packages/web/tests/test_api_workspaces.py -q
```

Expected: 用户创建不会生成 `bob` 目录；手动工作区不会自动写 canonical admin 成员。

- [ ] **Step 3: 修改用户创建 API**

在 `users.py` 中：

1. 导入 `ensure_user_workspace`, `ensure_global_admin_access`, `is_global_admin`。
2. 校验工作区名安全性并在用户插入前检查 `workspaces_dir / body.username` 是否已存在，冲突返回 `409 workspace already exists for username`。
3. 创建用户后调用 `ensure_user_workspace()`；异常时删除刚创建的用户，避免留下无法 provision 的半成品，然后转换为 HTTP 409/500。
4. 调用 `ensure_global_admin_access()`，覆盖“新建的是 canonical admin、已有工作区”的顺序。
5. 置顶接口用 `is_global_admin(user)` 替换 `user.role != "admin"` bypass。
6. `update_role` 成功把用户名为 `admin` 的用户提升为 admin 时调用 `ensure_global_admin_access()`；降为普通用户时清除该账号的全局自动成员记录，防止已降权账号仍因历史 membership 拥有全部工作区权限。

用户 API 的核心流程应保持原返回结构：

```python
u = store.create_user(...)
try:
    ensure_user_workspace(request.app.state.config.workspaces_dir, store, u)
    ensure_global_admin_access(request.app.state.config.workspaces_dir, store)
except FileExistsError:
    store.delete_user(u.id)
    raise HTTPException(409, "workspace already exists for username")
except (ValueError, OSError):
    store.delete_user(u.id)
    raise HTTPException(500, "failed to provision user workspace")
return {"user": _user_out(u)}
```

- [ ] **Step 4: 修改手动创建工作区 API**

在 `workspaces.py` 的 `create_workspace` 中，在创建者 manager 成员写入后调用：

```python
from supernova_web.components.workspace_provisioner import ensure_global_admin_member

store = request.app.state.auth_store
store.add_workspace_member(ws, user.id, "manager")
ensure_global_admin_member(ws, store)
```

保留创建者为任意超管都可创建工作区的行为。

- [ ] **Step 5: 运行创建流程测试**

Run:

```bash
uv run pytest packages/web/tests/test_users_routes.py packages/web/tests/test_api_workspaces.py -q
```

Expected: PASS。

- [ ] **Step 6: Commit creation provisioning**

```bash
git add packages/web/src/supernova_web/api/users.py \
  packages/web/src/supernova_web/api/workspaces.py \
  packages/web/tests/test_users_routes.py \
  packages/web/tests/test_api_workspaces.py
# 如果测试实际落在 test_workspace_lifecycle.py，则将其替换到 git add 列表
git commit -m "feat: provision user workspaces on creation"
```

---

### Task 5: 启动补偿与 legacy migration 改为 canonical admin

**Files:**
- Modify: `packages/web/src/supernova_web/app.py`
- Modify: `packages/web/tests/test_legacy_migration.py`
- Modify: `packages/web/tests/test_auth_seed.py`
- Modify: `packages/web/tests/test_auth_bootstrap.py`
- Modify: `packages/web/tests/test_workspace_provisioning.py`

- [ ] **Step 1: 写启动补偿失败测试**

扩展 `test_workspace_provisioning.py`，直接调用启动补偿函数或通过 `TestClient(app)` lifespan 验证：

```python

def test_startup_reconciles_existing_users_and_workspaces(tmp_workspaces, monkeypatch):
    # 建 admin、ops、alice；建已有工作区 old，且已有 alice member
    # create_app/TestClient 触发 lifespan 后：
    # 1. admin 在 old 中是 manager；ops 不在 old；alice 仍是 member
    # 2. alice 同名工作区被补建，alice/admin 都是 manager
```

更新 `test_legacy_migration.py`：已有普通成员时也必须补 `admin`，且不补 `ops`：

```python
assert store.get_workspace_member_role("ws1", admin.id) == "manager"
assert store.get_workspace_member_role("ws1", ops.id) is None
assert store.get_workspace_member_role("ws1", alice.id) == "manager"
```

- [ ] **Step 2: 运行启动迁移测试，确认失败**

Run:

```bash
uv run pytest packages/web/tests/test_legacy_migration.py packages/web/tests/test_auth_seed.py packages/web/tests/test_auth_bootstrap.py packages/web/tests/test_workspace_provisioning.py -q
```

Expected: 现有迁移只处理无成员工作区并添加所有 admin，新增断言失败。

- [ ] **Step 3: 接入 lifespan 补偿并替换旧迁移实现**

在 `app.py` 中：

1. 在 `seed_users()` 和 `bootstrap_default_admin()` 之后调用 `ensure_all_user_workspaces(cfg.workspaces_dir, app.state.auth_store)`。
2. 将 `_migrate_legacy_workspace_members()` 改为调用 `ensure_global_admin_access()`，不再收集所有 `role == "admin"`。
3. 保留 legacy repo/scan 迁移顺序：repo/scan 迁移完成后再次调用 canonical admin 补偿，以覆盖迁移过程中创建的 `__legacy__` 工作区。
4. 单个目录冲突、坏用户名等 best-effort 错误记录 warning 但不阻断启动。

建议 lifespan 顺序：

```python
seed_users(...)
bootstrap_default_admin(...)
ensure_all_user_workspaces(cfg.workspaces_dir, app.state.auth_store)
_migrate_legacy_repos(app)
_migrate_legacy_scans(app)
_migrate_legacy_workspace_members(app)
```

其中 `_migrate_legacy_workspace_members()` 负责迁移之后的所有真实工作区 admin 补齐；最终 `ensure_all_user_workspaces()` 若需要可在 legacy migration 后再次调用，但不得覆盖任何 workspace metadata。

- [ ] **Step 4: 运行迁移和 seed 测试**

Run:

```bash
uv run pytest packages/web/tests/test_legacy_migration.py packages/web/tests/test_auth_seed.py packages/web/tests/test_auth_bootstrap.py packages/web/tests/test_workspace_provisioning.py -q
```

Expected: PASS。

- [ ] **Step 5: Commit startup reconciliation**

```bash
git add packages/web/src/supernova_web/app.py \
  packages/web/tests/test_legacy_migration.py \
  packages/web/tests/test_auth_seed.py \
  packages/web/tests/test_auth_bootstrap.py \
  packages/web/tests/test_workspace_provisioning.py
git commit -m "feat: reconcile canonical admin workspace access on startup"
```

---

### Task 6: 全量回归、静态检查和最终审查

**Files:**
- No new production files; only adjust tests or implementation if a concrete regression is found.

- [ ] **Step 1: 运行受影响的完整后端测试集**

Run:

```bash
uv run pytest packages/web/tests/test_users_routes.py \
  packages/web/tests/test_auth_dependencies.py \
  packages/web/tests/test_workspace_permissions.py \
  packages/web/tests/test_workspace_lifecycle.py \
  packages/web/tests/test_members_routes.py \
  packages/web/tests/test_api_workspaces.py \
  packages/web/tests/test_scans_cross_ws.py \
  packages/web/tests/test_pinned_workspace.py \
  packages/web/tests/test_legacy_migration.py \
  packages/web/tests/test_workspace_provisioning.py -q
```

Expected: PASS，且没有 warning/error 输出。

- [ ] **Step 2: 运行 Web 包全量测试**

Run:

```bash
uv run pytest packages/web/tests -q
```

Expected: PASS。

- [ ] **Step 3: 运行 Ruff 检查修改文件**

Run:

```bash
uv run ruff check packages/web/src/supernova_web/components/workspace_provisioner.py \
  packages/web/src/supernova_web/auth/dependencies.py \
  packages/web/src/supernova_web/api/users.py \
  packages/web/src/supernova_web/api/workspaces.py \
  packages/web/src/supernova_web/api/scans.py \
  packages/web/src/supernova_web/api/scan.py \
  packages/web/src/supernova_web/app.py
```

Expected: no violations。

- [ ] **Step 4: 检查权限边界和 git diff**

人工确认以下不变量：

- `require_admin` 仍允许所有超管进入用户管理和创建工作区 API。
- 只有 `admin` + `role=admin` 能无成员记录访问任意工作区。
- `ops` 等其他超管只有成员工作区可见/可操作。
- 每个用户的同名工作区只创建一次，用户和 canonical admin 都是 manager。
- 已有其他成员不被删除或降权。
- 点目录不被列入工作区，也不被自动加入 admin 成员。
- 工作区冲突不会留下用户数据库记录。
- `git status --short` 中不包含本任务之外被暂存或提交的用户改动。

Run:

```bash
git diff HEAD~5..HEAD --stat
git status --short
git diff --check
```

- [ ] **Step 5: 最终提交（如 Task 6 产生修正）**

```bash
git add packages/web/src packages/web/tests
git commit -m "test: verify user workspace and canonical admin isolation"
```

只暂存本任务涉及的路径；不得使用 `git add -A`，避免提交用户已有的未提交改动。
