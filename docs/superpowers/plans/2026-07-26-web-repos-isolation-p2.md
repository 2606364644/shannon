# Web repos 仓库隔离 P2 实现计划（repo 按 workspace 隔离）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 repo 从全局 `repos/` 迁到每个 workspace 内（`workspaces/<ws>/repos/<name>`），clone/list/pull/checkout/delete 都在 ws 上下文 + ws 成员鉴权，scan 解析 repo 也按当前 ws。

**Architecture:** `RepoManager` 全方法加 `ws` 维度（base 目录由全局 `repos_dir` 改为 `workspaces_dir/<ws>/repos`）；`repos.py` 路由从 `/api/repos` 迁到 `/api/workspaces/{ws}/repos` + `workspace_member` 依赖；`scan_manager._resolve_repo_path` 加 ws；前端 clone 管理移到 ws 详情页"仓库"tab + ScanNewPage 选 ws。

**Tech Stack:** Python（FastAPI、sqlite3、pydantic）；React 18 + shadcn(Radix) + vitest。**前置**：P0 + P1 必须已完成（auth、`workspace_member`、admin 建 ws、scan 在 ws 内均已就位）。

## Global Constraints

- **依赖 P0+P1**：`workspace_member`/`workspace_manager`/`current_user`/`require_admin`、`app.state.auth_store`、admin 建 ws（`POST /api/workspaces`）、scan 在已有 ws 内——均由 P0/P1 提供。
- **repo 物理位置**：`workspaces/<ws>/repos/<name>`（原全局 `repos/<name>` 废弃，legacy 迁移见 T7）。worker 容器已 mount `workspaces/`，路径透明。
- **clone 凭据全局**（GITLAB_USER/TOKEN，`GitFetcher` 不动）——隔离属 P3c。
- **不碰** configs/multi-configs（P3c）。`/api/multi-configs` 在 P2 仍共享。
- **workspace 标识 = 目录名**；repo 鉴权复用 P1 `workspace_member`（能访问 ws 即能访问其 repo，无 repo_members 表）。
- **测试陷阱**：后端只跑改动测试；前端 `cd packages/web/frontend`；Radix 优先受控；i18n zh 真中文。
- **TDD**：每 task 红→绿→commit。

## File Structure

**后端 Modify：**
- `packages/web/src/supernova_web/components/repo_manager.py` — `RepoManager` 全方法加 `ws` 维度（`_dir` → `_workspaces_dir`，`_repo_dir(name)` → `_repo_dir(ws, name)`，`_jobs[name]` → `_jobs[(ws,name)]`）
- `packages/web/src/supernova_web/components/scan_manager.py` — `_resolve_repo_path(ws, name)`（base = `workspaces/<ws>/repos`）；`active_repo_sources` 带 ws
- `packages/web/src/supernova_web/api/repos.py` — 路由从 `/api/repos` 迁到 `/api/workspaces/{ws}/repos`，全加 `workspace_member`
- `packages/web/src/supernova_web/api/scan.py` — `create_scan` 把 `ws` 传给 `sm.start`/`_resolve_repo_path`
- `packages/web/src/supernova_web/app.py` — `RepoManager` 构造改 `workspaces_dir`；注册新 repos router；legacy repo 迁移 startup
- `packages/web/src/supernova_web/components/git_fetcher.py` — **不动**（凭据全局）

**前端 Modify：**
- `packages/web/frontend/src/api/client.ts` — repos API 函数加 `ws` 参数，路径改 `/workspaces/{ws}/repos`
- `packages/web/frontend/src/components/AddRepoDialog.tsx` / `RepoCombobox` — 收 `ws` prop
- `packages/web/frontend/src/pages/ReposPage.tsx` / `RepoDetailPage.tsx` — 改为 ws 内视图（或 admin 跨 ws 总览）
- `packages/web/frontend/src/pages/ScanNewPage.tsx` — 选 ws + ws 内 repo
- `packages/web/frontend/src/pages/WorkspaceListPage.tsx` — admin "新建 workspace" 按钮（补 P1 §6 前端缺口）
- workspace 详情页 — 加"仓库"tab
- `packages/web/frontend/src/locales/{zh,en}.json` — `repos.*` 文案调整 + `workspace.create.*`

**Test（create）：** `test_repo_ws_isolation.py`、`test_repos_routes_ws.py`、`test_scan_resolves_repo_in_ws.py`、`test_legacy_repo_migration.py`、前端

---

## Task 1: RepoManager 加 ws 维度

**Files:**
- Modify: `packages/web/src/supernova_web/components/repo_manager.py`（全文件）
- Test: `packages/web/tests/test_repo_ws_isolation.py`

**改造模式（统一应用到所有方法）：**
- `__init__(repos_dir, ...)` → `__init__(workspaces_dir, ...)`；`self._dir` → `self._workspaces_dir`；`_jobs: dict[str,...]` → `dict[tuple[str,str],...]`
- 新增 `_repos_root(ws) -> self._workspaces_dir / ws / "repos"`
- 每个方法签名加 `ws: str` 作第一参数（`clone` 在 `url` 前、`list_repos/get_repo/...` 在 `name` 前）
- `self._repo_dir(name)` → `self._repo_dir(ws, name)`；`self._dir` → `self._repos_root(ws)`；`self._jobs[name]` → `self._jobs[(ws, name)]`；`name in self._jobs` → `(ws, name) in self._jobs`

**Interfaces:**
- Produces：`RepoManager(workspaces_dir, git_fetcher, max_concurrent)`，方法 `list_repos(ws)`、`get_repo(ws, name)`、`clone(ws, url, branch, commit, name, group)`、`pull(ws, name)`、`checkout(ws, name, branch)`、`delete(ws, name)`、`is_busy(ws, name)`、`migrate_legacy(ws)`

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_repo_ws_isolation.py
import pytest
from pathlib import Path
from supernova_web.components.repo_manager import RepoManager
from supernova_web.components.git_fetcher import GitFetcher


@pytest.fixture
def rm(tmp_path):
    ws_dir = tmp_path / "workspaces"
    ws_dir.mkdir()
    (ws_dir / "ws1").mkdir()
    (ws_dir / "ws2").mkdir()
    # git_fetcher.available() 返 False 即可（不真 clone）；用 mock
    gf = GitFetcher(ws_dir, "u", "t")
    return RepoManager(ws_dir, gf)


def test_repos_root_per_workspace(rm, tmp_path):
    assert rm._repos_root("ws1") == tmp_path / "workspaces" / "ws1" / "repos"
    assert rm._repos_root("ws1") != rm._repos_root("ws2")


def test_list_repos_empty_per_ws(rm):
    assert rm.list_repos("ws1") == []
    assert rm.list_repos("ws2") == []


def test_clone_into_ws_dir(rm, monkeypatch):
    # mock git 子进程 + available
    monkeypatch.setattr(rm._git, "available", lambda: True)
    async def _fake_clone_task(self, *a, **kw):  # 不真跑 git
        self._jobs.pop(a[0] if isinstance(a[0], tuple) else (a[0]), None)
    monkeypatch.setattr(RepoManager, "_clone_task", _fake_clone_task)
    # _repo_dir(ws, name) 校验 + 建目录 + 写 meta（不真 clone）
    name = rm.clone("ws1", "https://x/y.git", None, None, "y", None)
    assert (rm._workspaces_dir / "ws1" / "repos" / "y").exists()
    # ws2 不受影响
    assert not (rm._workspaces_dir / "ws2" / "repos" / "y").exists()


def test_repo_dir_isolation(rm):
    # ws1/repos/y 与 ws2/repos/y 是不同路径
    assert rm._repo_dir("ws1", "y") == rm._workspaces_dir / "ws1" / "repos" / "y"
    assert rm._repo_dir("ws2", "y") == rm._workspaces_dir / "ws2" / "repos" / "y"
```

- [ ] **Step 2: 验证失败** — Run: `uv run pytest packages/web/tests/test_repo_ws_isolation.py -v` → FAIL（`_repos_root`/ws 参数不存在）

- [ ] **Step 3: 改造 RepoManager（按上面模式）**

关键方法的新代码（其余方法按"改造模式"机械应用）：

```python
class RepoManager:
    def __init__(self, workspaces_dir: Path, git_fetcher: GitFetcher,
                 max_concurrent: int = 3) -> None:
        self._workspaces_dir = Path(workspaces_dir)
        self._git = git_fetcher
        self._max_concurrent = max(1, max_concurrent)
        self._sem = asyncio.Semaphore(self._max_concurrent)
        self._jobs: dict[tuple[str, str], asyncio.Task] = {}

    def _repos_root(self, ws: str) -> Path:
        return self._workspaces_dir / ws / "repos"

    def _repo_dir(self, ws: str, name: str) -> Path:
        return _resolve_repo_dir(self._repos_root(ws), name)

    def is_busy(self, ws: str, name: str) -> bool:
        return (ws, name) in self._jobs

    def list_repos(self, ws: str) -> list[dict]:
        root = self._repos_root(ws)
        root.mkdir(parents=True, exist_ok=True)
        out: list[dict] = []
        for sub in sorted(root.iterdir()):
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            if (sub / ".git").exists():
                try:
                    out.append(self._repo_view(ws, sub.name))
                except ValueError:
                    continue
                continue
            for sub2 in sorted(sub.iterdir()):
                if not sub2.is_dir() or sub2.name.startswith("."):
                    continue
                if _is_repo(sub2):
                    try:
                        out.append(self._repo_view(ws, f"{sub.name}/{sub2.name}"))
                    except ValueError:
                        continue
        return out

    async def clone(self, ws: str, url: str, branch: str | None, commit: str | None,
                    name: str | None, group: str | None = None) -> str:
        if not self._git.available():
            raise PermissionError("未配置 git 凭证（GITLAB_USER/TOKEN）")
        name = name or self._git.repo_name(url)
        _validate_repo_segment(name, "仓库名")
        if group:
            _validate_repo_segment(group, "分组名")
            final_name = f"{group}/{name}"
        else:
            final_name = name
        target = self._repo_dir(ws, final_name)
        if target.exists():
            raise ValueError(f"仓库已存在：{final_name}（可改用更新 pull）")
        if len(self._jobs) >= self._max_concurrent:
            raise TooManyClones(self._max_concurrent)
        target.mkdir(parents=True, exist_ok=False)
        self._write_meta(ws, final_name, source={"kind": "git", "url": strip_credentials(url), "branch": branch, "commit": commit},
                         cloned_at=_now_iso(), state="cloning", last_error=None)
        task = asyncio.create_task(self._clone_task(ws, final_name, url, branch, commit, target))
        self._jobs[(ws, final_name)] = task
        return final_name
```

其余方法（`get_repo`/`_repo_view`/`_read_meta`/`_write_meta`/`_ensure_meta`/`_last_progress`/`_recent_events`/`_clone_task`/`pull`/`_pull_task`/`checkout`/`delete`/`_run_git_with_progress`/`_mark_failed`/`_append_event`/`migrate_legacy`/`_migrate_one`）一律按改造模式：签名加 `ws: str` 第一参数，`self._repo_dir(name)` → `self._repo_dir(ws, name)`，`self._dir` → `self._repos_root(ws)`，`self._jobs[name]`/`name in self._jobs` → `(ws, name)` 元组。`_clone_task`/`_pull_task` 的 `self._jobs.pop(name)` → `self._jobs.pop((ws, name))`。

- [ ] **Step 4: 验证通过** — Run: `uv run pytest packages/web/tests/test_repo_ws_isolation.py -v` → 4 passed

- [ ] **Step 5: 改 app.py 构造 + scan_manager（最小对齐，使现有调用不崩）**

`app.py` 的 `RepoManager(cfg.repos_dir, ...)` 改为 `RepoManager(cfg.workspaces_dir, ...)`。`scan_manager` 的 `self._repos_dir` 暂保留（T3 改 `_resolve_repo_path`）。

- [ ] **Step 6: Commit**

```bash
git add packages/web/src/supernova_web/components/repo_manager.py packages/web/src/supernova_web/app.py packages/web/tests/test_repo_ws_isolation.py
git commit -m "feat(web/p2): RepoManager 全方法加 ws 维度 (repo→workspaces/<ws>/repos)"
```

---

## Task 2: repos 路由迁到 ws 上下文 + 成员鉴权

**Files:**
- Modify: `packages/web/src/supernova_web/api/repos.py`（router prefix `/api/repos` → `/api/workspaces`；每个路由加 `{ws}` + `workspace_member`；调 `repo_manager.<m>(ws, ...)`）
- Test: `packages/web/tests/test_repos_routes_ws.py`

**Interfaces:**
- Consumes: T1 `RepoManager.<ws 方法>`、P1 `workspace_member`
- Produces：`GET/POST /api/workspaces/{ws}/repos`、`GET/DELETE /api/workspaces/{ws}/repos/{name:path}`、`POST .../pull`、`POST .../checkout`、`GET .../events`（SSE），全加 `workspace_member`

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_repos_routes_ws.py
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
    st.create_user("bob", hash_password("p"))
    (app.state.config.workspaces_dir / "ws1").mkdir()
    st.add_workspace_member("ws1", st.get_user_by_username("alice").id, "manager")
    return app


def _login(app, username):
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": username, "password": "p"}, headers={"X-CSRF-Token": tok})
    return c


def test_member_lists_repos(_app, monkeypatch):
    monkeypatch.setattr(_app.state.repo_manager, "list_repos", lambda ws: [{"name": "r1"}])
    c = _login(_app, "alice")
    r = c.get("/api/workspaces/ws1/repos")
    assert r.status_code == 200 and r.json() == [{"name": "r1"}]


def test_non_member_forbidden(_app):
    bob = _login(_app, "bob")  # bob 非 ws1 成员
    assert bob.get("/api/workspaces/ws1/repos").status_code == 403


def test_admin_accesses_any_ws(_app, monkeypatch):
    monkeypatch.setattr(_app.state.repo_manager, "list_repos", lambda ws: [])
    admin = _login(_app, "admin")
    assert admin.get("/api/workspaces/ws1/repos").status_code == 200


def test_clone_into_ws(_app, monkeypatch):
    async def _fake_clone(ws, url, branch, commit, name, group=None):
        return name or "y"
    monkeypatch.setattr(_app.state.repo_manager, "clone", _fake_clone)
    monkeypatch.setattr(_app.state.repo_manager._git, "available", lambda: True)
    alice = _login(_app, "alice")
    tok = alice.get("/api/auth/csrf").json()["csrf_token"]
    r = alice.post("/api/workspaces/ws1/repos", json={"git_url": "https://x/y.git"}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 202
```

- [ ] **Step 2: 验证失败** — Run: `uv run pytest packages/web/tests/test_repos_routes_ws.py -v` → FAIL（`/api/workspaces/{ws}/repos` 不存在）

- [ ] **Step 3: 改 repos.py**

router prefix 与依赖：

```python
from fastapi import APIRouter, HTTPException, Depends, Request
from starlette.responses import StreamingResponse
from supernova_web.auth.dependencies import workspace_member
from supernova_web.auth.models import User
from .components.event_tailer import EventTailer

router = APIRouter(prefix="/api/workspaces", tags=["repos"])
```

代表路由（其余按模式：加 `ws: str` + `_: User = Depends(workspace_member)`，调 `rm.<m>(ws, ...)`）：

```python
@router.get("/{ws}/repos")
async def list_repos(ws: str, request: Request, _: User = Depends(workspace_member)):
    return request.app.state.repo_manager.list_repos(ws)


@router.post("/{ws}/repos", status_code=202)
async def create_repo(ws: str, body: CreateRepoBody, request: Request, _: User = Depends(workspace_member)):
    rm = request.app.state.repo_manager
    try:
        name = await rm.clone(ws, body.git_url, body.branch, body.commit, body.name, body.group)
    except PermissionError:
        raise HTTPException(503, "未配置 git 凭据（GITLAB_USER/TOKEN）")
    except ValueError as e:
        raise HTTPException(409, str(e))
    except TooManyClones as e:
        raise HTTPException(409, f"并发 clone 上限 {e.limit}")
    return {"name": name}


# 路由顺序：events 必须在 {name:path} 之前声明（贪婪匹配）
@router.get("/{ws}/repos/{name:path}/events")
async def repo_events(ws: str, name: str, request: Request, _: User = Depends(workspace_member)):
    ndjson = request.app.state.repo_manager._repo_dir(ws, name) / "clone.ndjson"
    # ... 复用原 EventTailer SSE 逻辑（stop_type="clone_end"）...


@router.get("/{ws}/repos/{name:path}")
async def get_repo(ws: str, name: str, request: Request, _: User = Depends(workspace_member)):
    repo = request.app.state.repo_manager.get_repo(ws, name)
    if repo is None:
        raise HTTPException(404, "repo not found")
    return repo


@router.delete("/{ws}/repos/{name:path}")
async def delete_repo(ws: str, name: str, request: Request, _: User = Depends(workspace_member)):
    sm = request.app.state.scan_manager
    if (ws, name) in sm.active_repo_sources():  # T3 改 active_repo_sources 返 (ws,name) 集合
        raise HTTPException(409, "仓库正被扫描引用")
    await request.app.state.repo_manager.delete(ws, name)
    return {"deleted": name}


@router.post("/{ws}/repos/{name:path}/pull", status_code=202)
async def pull_repo(ws: str, name: str, request: Request, _: User = Depends(workspace_member)):
    await request.app.state.repo_manager.pull(ws, name)
    return {"pulling": name}


@router.post("/{ws}/repos/{name:path}/checkout")
async def checkout_repo(ws: str, name: str, body: CheckoutBody, request: Request, _: User = Depends(workspace_member)):
    await request.app.state.repo_manager.checkout(ws, name, body.branch)
    return {"checked_out": name}
```

- [ ] **Step 4: 验证通过** — Run: `uv run pytest packages/web/tests/test_repos_routes_ws.py -v` → 4 passed

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/api/repos.py packages/web/tests/test_repos_routes_ws.py
git commit -m "feat(web/p2): repos 路由迁 /api/workspaces/{ws}/repos + workspace_member 鉴权"
```

---

## Task 3: scan 按 ws 解析 repo

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（`_resolve_repo_path(ws, name)`、`active_repo_sources` 返 `(ws,name)` 集、`_resolve_inputs` 用 `req.workspace`）
- Test: `packages/web/tests/test_scan_resolves_repo_in_ws.py`

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_scan_resolves_repo_in_ws.py
import pytest
from supernova_web.components.scan_manager import ScanManager


def test_resolve_repo_path_uses_ws(tmp_path):
    ws_dir = tmp_path / "workspaces"; ws_dir.mkdir()
    (ws_dir / "ws1" / "repos" / "myrepo").mkdir(parents=True)
    (ws_dir / "ws1" / "repos" / "myrepo" / ".git").mkdir()
    sm = ScanManager(ws_dir, tmp_path / "repos", None)
    p = sm._resolve_repo_path("ws1", "myrepo")
    assert p.endswith("ws1/repos/myrepo")


def test_resolve_repo_path_ws_isolation(tmp_path):
    ws_dir = tmp_path / "workspaces"; ws_dir.mkdir()
    (ws_dir / "ws1" / "repos" / "r").mkdir(parents=True)
    sm = ScanManager(ws_dir, tmp_path / "repos", None)
    import pytest
    with pytest.raises(ValueError):
        sm._resolve_repo_path("ws2", "r")  # ws2 没这个 repo
```

- [ ] **Step 2: 验证失败** — Run: `uv run pytest packages/web/tests/test_scan_resolves_repo_in_ws.py -v` → FAIL（`_resolve_repo_path` 不收 ws）

- [ ] **Step 3: 改 scan_manager**

把 `_resolve_repo_path`（line 269-283）改为：

```python
def _resolve_repo_path(self, ws: str, name: str) -> str:
    repo_dir = _resolve_repo_dir(self._workspaces_dir / ws / "repos", name)
    if not repo_dir.is_dir():
        raise ValueError(f"仓库不存在：{name}")
    meta_file = repo_dir / ".supernova-repo.json"
    state = "ready"
    if meta_file.exists():
        try:
            state = json.loads(meta_file.read_text("utf-8", errors="replace")).get("state", "ready")
        except json.JSONDecodeError:
            state = "ready"
    if state != "ready":
        raise ValueError(f"仓库未就绪（state={state}），请先在 ws 内完成 clone")
    return str(repo_dir)
```

`_resolve_inputs`（line 257-267）把 `self._resolve_repo_path(req.source.value)` 改为 `self._resolve_repo_path(req.workspace, req.source.value)`（ws 来自 P1 的 `req.workspace`，已校验存在）。

`active_repo_sources`（line 65-71）改为返 `(ws, name)` 元组集合：

```python
def active_repo_sources(self) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for ws, req in self._active_reqs.items():
        if req.source is not None and req.source.kind == "repo":
            out.add((ws, req.source.value))
    return out
```

（`_active_reqs` 已是 `dict[str, ScanRequest]`，key=ws；P1 改动。）

- [ ] **Step 4: 验证通过** — Run: `uv run pytest packages/web/tests/test_scan_resolves_repo_in_ws.py -v` → 2 passed

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_scan_resolves_repo_in_ws.py
git commit -m "feat(web/p2): scan 按 ws 解析 repo (workspaces/<ws>/repos) + active_repo_sources 带 ws"
```

---

## Task 4: 前端 admin 新建 workspace 按钮（补 P1 §6 缺口）

**Files:**
- Modify: `packages/web/frontend/src/api/client.ts`（加 `createWorkspace`）
- Create: `packages/web/frontend/src/components/CreateWorkspaceDialog.tsx`
- Modify: `packages/web/frontend/src/pages/WorkspaceListPage.tsx`（admin 可见"新建 workspace"按钮）
- Modify: `packages/web/frontend/src/locales/{zh,en}.json`（`workspace.create.*`）
- Test: `packages/web/frontend/src/components/CreateWorkspaceDialog.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
// packages/web/frontend/src/components/CreateWorkspaceDialog.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "@/auth/AuthContext";
import { CreateWorkspaceDialog } from "./CreateWorkspaceDialog";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

describe("CreateWorkspaceDialog", () => {
  it("admin 可见新建按钮", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ user: { id: 1, username: "admin", role: "admin" } }), { status: 200 })
    );
    render(<AuthProvider><MemoryRouter><CreateWorkspaceDialog onCreated={() => {}} /></MemoryRouter></AuthProvider>);
    await waitFor(() => expect(screen.getByText("workspace.create.button")).toBeTruthy());
  });
});
```

- [ ] **Step 2: 验证失败** — Run: `cd packages/web/frontend && npx vitest run src/components/CreateWorkspaceDialog.test.tsx` → FAIL（组件不存在）

- [ ] **Step 3a: client.ts 加 createWorkspace**

```ts
export const createWorkspace = (name: string) =>
  apiPost<{ name: string }>("/workspaces", { name });
```

- [ ] **Step 3b: CreateWorkspaceDialog.tsx**

```tsx
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/auth/AuthContext";
import { createWorkspace } from "@/api/client";

export function CreateWorkspaceDialog({ onCreated }: { onCreated: (name: string) => void }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  if (user?.role !== "admin") return null;

  async function onCreate() {
    const r = await createWorkspace(name);
    setOpen(false); setName("");
    onCreated(r.name);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild><Button size="sm">{t("workspace.create.button")}</Button></DialogTrigger>
      <DialogContent>
        <DialogHeader><DialogTitle>{t("workspace.create.title")}</DialogTitle></DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="ws-name">{t("workspace.create.name")}</Label>
          <Input id="ws-name" value={name} onChange={(e) => setName(e.target.value)} />
          <Button onClick={onCreate} disabled={!name.trim()}>{t("workspace.create.submit")}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 3c: WorkspaceListPage.tsx 加按钮** — 在页面头部加 `<CreateWorkspaceDialog onCreated={() => 刷新列表} />`。

- [ ] **Step 3d: i18n** — zh.json 加 `"workspace": { "create": { "button": "新建 workspace", "title": "新建 workspace", "name": "名称", "submit": "创建" } }`；en.json 对应英文（`{ "button": "New workspace", ... }`）。

- [ ] **Step 4: 验证通过** — Run: `cd packages/web/frontend && npx vitest run src/components/CreateWorkspaceDialog.test.tsx` → 1 passed

- [ ] **Step 5: Commit**

```bash
cd packages/web/frontend
git add src/api/client.ts src/components/CreateWorkspaceDialog.tsx src/components/CreateWorkspaceDialog.test.tsx src/pages/WorkspaceListPage.tsx src/locales/zh.json src/locales/en.json
git commit -m "feat(web/p2): admin 新建 workspace dialog (补 P1 §6 前端缺口) + i18n"
```

---

## Task 5: 前端 ws 内仓库 tab + AddRepoDialog/RepoCombobox 带 ws

**Files:**
- Modify: `packages/web/frontend/src/api/client.ts`（repos 函数全加 `ws` 首参，路径 `/workspaces/${ws}/repos`）
- Modify: `packages/web/frontend/src/components/AddRepoDialog.tsx`（收 `ws` prop）
- Modify: `packages/web/frontend/src/components/RepoCombobox.tsx`（收 `ws` prop）
- Create: `packages/web/frontend/src/routes/WorkspaceDetail/ReposTab.tsx`（ws 内仓库列表 + clone/pull/checkout/delete）
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/index.tsx`（加"仓库"tab）+ `router.tsx`（`/p/:workspace/repos`）
- Test: `packages/web/frontend/src/routes/WorkspaceDetail/ReposTab.test.tsx`

- [ ] **Step 1: 写失败测试** — 渲染 `<ReposTab workspace="ws1" />`，mock `listRepos("ws1")` 返回 repo 列表，断言列表渲染 + "添加仓库"按钮。

```tsx
// packages/web/frontend/src/routes/WorkspaceDetail/ReposTab.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "@/auth/AuthContext";
import { ReposTab } from "./ReposTab";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

describe("ReposTab", () => {
  it("列出 ws 内仓库", async () => {
    const fm = vi.spyOn(window, "fetch");
    fm.mockResolvedValueOnce(new Response(JSON.stringify({ user: { id: 1, username: "alice", role: "user" } }), { status: 200 })); // /me
    fm.mockResolvedValue(new Response(JSON.stringify([{ name: "r1", state: "ready" }]), { status: 200 })); // listRepos(ws1)
    render(<AuthProvider><MemoryRouter><ReposTab workspace="ws1" /></MemoryRouter></AuthProvider>);
    await waitFor(() => expect(screen.getByText("r1")).toBeTruthy());
  });
});
```

- [ ] **Step 2: 验证失败** — Run: `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/ReposTab.test.tsx` → FAIL（组件不存在）

- [ ] **Step 3a: client.ts — repos 函数加 ws**

```ts
const encWs = (ws: string) => encodeURIComponent(ws);
export const listRepos = (ws: string) => apiGet<Repo[]>(`/workspaces/${encWs(ws)}/repos`);
export const getRepo = (ws: string, name: string) => apiGet<RepoDetail>(`/workspaces/${encWs(ws)}/repos/${encRepo(name)}`);
export const createRepo = (ws: string, body: { git_url: string; branch?: string; commit?: string; name?: string; group?: string }) =>
  apiPost<{ name: string }>(`/workspaces/${encWs(ws)}/repos`, body);
export const deleteRepo = (ws: string, name: string) =>
  apiDelete<{ deleted: string }>(`/workspaces/${encWs(ws)}/repos/${encRepo(name)}`);
export const pullRepo = (ws: string, name: string) =>
  apiPost<{ pulling: string }>(`/workspaces/${encWs(ws)}/repos/${encRepo(name)}/pull`, {});
export const checkoutRepo = (ws: string, name: string, branch: string) =>
  apiPost<{ checked_out: string }>(`/workspaces/${encWs(ws)}/repos/${encRepo(name)}/checkout`, { branch });
```

- [ ] **Step 3b: AddRepoDialog / RepoCombobox 收 ws prop** — `AddRepoDialog({ ws, onCreated })` 调 `createRepo(ws, body)`；`RepoCombobox({ ws, value, onChange })` 调 `listRepos(ws)`。其余逻辑不变。

- [ ] **Step 3c: ReposTab.tsx** — 复用现有 `ReposPage` 的列表/StateCell/CloneProgress 逻辑，但全部带 `ws`：`listRepos(ws)` + `<AddRepoDialog ws={ws}>` + pull/checkout/delete 调 `pullRepo(ws,name)` 等。

- [ ] **Step 3d: router + WorkspaceDetail 加 tab** — `router.tsx` 在 WorkspaceDetail children 加 `{ path: "repos", element: <ReposTab /> }`；`WorkspaceDetail/index.tsx` 的 tab 列表加"仓库"。

- [ ] **Step 4: 验证通过** — Run: `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/ReposTab.test.tsx` → 1 passed

- [ ] **Step 5: Commit**

```bash
cd packages/web/frontend
git add src/api/client.ts src/components/AddRepoDialog.tsx src/components/RepoCombobox.tsx src/routes/WorkspaceDetail/ReposTab.tsx src/routes/WorkspaceDetail/ReposTab.test.tsx src/routes/WorkspaceDetail/index.tsx src/router.tsx
git commit -m "feat(web/p2): 前端 ws 内仓库 tab + AddRepoDialog/RepoCombobox 带 ws"
```

---

## Task 6: ScanNewPage 选 workspace

**Files:**
- Modify: `packages/web/frontend/src/pages/ScanNewPage.tsx`（加 ws 下拉，repo source 从选定 ws）
- Modify: `packages/web/frontend/src/components/ScanFormFields.tsx`（`RepoCombobox` 传选定 ws；提交 body 带 `workspace_name`）
- Test: 扩展 `ScanNewPage` 现有测试或新增

- [ ] **Step 1: 写失败测试** — 渲染 ScanNewPage，mock `/api/workspaces` 返回用户可访问的 ws 列表，断言 ws 下拉存在 + 选 repo 时调 `listRepos(<选中 ws>)`。

- [ ] **Step 2: 验证失败** — FAIL（ws 下拉未实现）

- [ ] **Step 3: 实现**
- `ScanNewPage` 加 `workspace` state，下拉选项来自 `apiGet("/workspaces")`（后端已按 P1 过滤为当前用户可见的 ws）；未选 ws 时禁用提交。
- `ScanFormFields` 把选定的 `workspace` 传给 `RepoCombobox`（`<RepoCombobox ws={workspace} ...>`）。
- 提交 body 的 `workspace_name` = 选定 ws（替代原自动生成/可选字段）。

- [ ] **Step 4: 验证通过** — 测试 PASS

- [ ] **Step 5: Commit**

```bash
cd packages/web/frontend
git add src/pages/ScanNewPage.tsx src/components/ScanFormFields.tsx
git commit -m "feat(web/p2): ScanNewPage 选 workspace + repo source 走当前 ws"
```

---

## Task 7: legacy repo 迁移（startup）

**Files:**
- Modify: `packages/web/src/supernova_web/app.py`（lifespan 加 `_migrate_legacy_repos`）
- Test: `packages/web/tests/test_legacy_repo_migration.py`

**语义**：旧全局 `repos/<name>` 迁到 `workspaces/__legacy__/repos/<name>`，`__legacy__` ws 分配给所有 admin（manager）。

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_legacy_repo_migration.py
from pathlib import Path
from starlette.testclient import TestClient
from supernova_web.app import create_app


def test_legacy_repos_moved_to_legacy_ws(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPERNOVA_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    app = create_app()
    admin = app.state.auth_store.create_user("admin", "h", role="admin")
    # legacy repo
    legacy = tmp_path / "repos" / "oldrepo"
    (legacy / ".git").mkdir(parents=True)
    with TestClient(app):  # 触发 lifespan
        pass
    legacy_target = tmp_path / "workspaces" / "workspaces" / "__legacy__" / "repos" / "oldrepo"
    assert legacy_target.exists()
    assert app.state.auth_store.get_workspace_member_role("__legacy__", admin.id) == "manager"
```

（注：`workspaces_dir` 解析在测试 fixture 下可能嵌套一层，按实际 `resolve_workspaces_dir()` 调整断言路径。）

- [ ] **Step 2: 验证失败** — FAIL（无迁移函数）

- [ ] **Step 3: 实现 app.py**

```python
def _migrate_legacy_repos(app: FastAPI) -> None:
    """旧全局 repos/<name> → workspaces/__legacy__/repos/<name>，__legacy__ ws 分配给所有 admin。"""
    import shutil
    cfg = app.state.config
    old_root = cfg.repos_dir
    if not old_root.is_dir():
        return
    legacy_ws = cfg.workspaces_dir / "__legacy__"
    legacy_repos = legacy_ws / "repos"
    moved = False
    for sub in list(old_root.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        if (sub / ".git").exists():
            legacy_repos.mkdir(parents=True, exist_ok=True)
            shutil.move(str(sub), str(legacy_repos / sub.name))
            moved = True
    if moved:
        store = app.state.auth_store
        if store.list_workspace_members("__legacy__") == []:
            for u in store.list_all_users():
                if u.role == "admin":
                    store.add_workspace_member("__legacy__", u.id, "manager")
```

在 `lifespan` 的 `_migrate_legacy_workspace_members(app)` 之后调 `_migrate_legacy_repos(app)`。

- [ ] **Step 4: 验证通过** — Run: `uv run pytest packages/web/tests/test_legacy_repo_migration.py -v` → 1 passed

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/app.py packages/web/tests/test_legacy_repo_migration.py
git commit -m "feat(web/p2): legacy repo 启动迁移 → __legacy__ ws (admin 可见)"
```

---

## Task 8: 端到端冒烟（真机）

**前置**：T1–T7 全绿，`uv run pytest packages/web/tests/ -v`（auth+p1+p2 相关）全绿、`cd packages/web/frontend && npm run build` 通过。

- [ ] **Step 1: 配多用户 + 多 ws** — `configs/users.yaml` 配 admin + alice + bob。admin 建 `ws_alice`（分配 alice manager）、`ws_bob`（分配 bob manager）。

- [ ] **Step 2: 冒烟 checklist**

- [ ] alice 登录 → 进 `ws_alice` 详情页 → "仓库"tab → clone 一个 repo → 出现在 `ws_alice` 的仓库列表
- [ ] alice 发起扫描（选 `ws_alice` + 刚 clone 的 repo）→ 扫描跑起来
- [ ] bob 登录 → 进 `ws_bob` → 看不到 alice 的 repo（`GET /api/workspaces/ws_alice/repos` → 403）
- [ ] bob 在 `ws_bob` clone 同名 repo → 独立（不影响 alice 的）
- [ ] admin 登录 → 能访问任意 ws 的 repo（含 `__legacy__`）
- [ ] alice 删除 `ws_alice`（DELETE /api/workspaces/ws_alice）→ 该 ws 的 `repos/` 一并清除
- [ ] 旧全局 `repos/` 的 legacy repo 启动后出现在 `__legacy__` ws（admin 可见）
- [ ] clone 进度 SSE（`/api/workspaces/{ws}/repos/{name}/events`）在 ws 内正常推送

- [ ] **Step 3: 记录结果**（PR 描述 + 截图）。

---

## Self-Review

**1. Spec coverage（P2 spec § → task）**
- §4.1 repo 物理并入 ws（`workspaces/<ws>/repos/`）→ T1（RepoManager `_repos_root(ws)`）✓
- §5.2 repo 操作改到 ws 上下文 + ws 成员鉴权 → T1（RepoManager ws）+ T2（路由）✓
- scan 按 ws 解析 repo → T3 ✓
- §6.1 admin 新建 ws UI → T4 ✓
- §6.2 clone 管理移 ws 内（仓库 tab）+ AddRepoDialog/RepoCombobox 带 ws → T5 ✓
- §6.3 ScanNewPage 选 ws → T6 ✓
- §6.4 RepoCombobox/AddRepoDialog ws 上下文 → T5 ✓
- §4.3 legacy repo 迁移 → T7 ✓
- §5.4 clone 凭据全局（不动 GitFetcher）→ Global Constraints 明确 ✓
- §8 范围边界（configs/multi-configs 不碰）→ Global Constraints ✓

**2. Placeholder scan**：T5 Step 3c "复用 ReposPage 逻辑"给的方向 + 关键函数调用（带 ws），非空泛"适当处理"；T6 Step 3 给具体三步。无 TBD。

**3. Type consistency**：
- `RepoManager.<m>(ws, ...)`：T1 定义 → T2 调用一致 ✓
- `_resolve_repo_path(ws, name)`：T3 定义 → scan 调用一致 ✓
- `active_repo_sources() -> set[tuple[str,str]]`：T3 定义 → T2 delete 引用检查 `(ws,name) in ...` 一致 ✓
- 前端 `listRepos(ws)` 等：T5 定义 → T6 RepoCombobox 调用一致 ✓
- cookie/csrf 沿用 P0/P1（写操作带 `X-CSRF-Token`）✓

**结论**：spec 全覆盖、无占位、类型一致。可交付执行。

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-26-web-repos-isolation-p2.md`.**

执行方式同 P0/P1：Subagent-Driven（推荐）或 Inline。**前置**：P0 + P1 必须先完成并合入（P2 依赖 auth、workspace_member、admin 建 ws、scan 在 ws 内）。

**Which approach?**（或先 commit 本 plan，执行稍后定）
