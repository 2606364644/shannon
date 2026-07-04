# Shannon Web 重设计 · 子项目 2（列表页 + 文件浏览器）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重做 WorkspaceListPage（TanStack Table + 搜索/筛选/排序/取消/删除/expandable）+ 新建 `<FileSystemPicker>`（Dialog 模态选目录）+ 配套后端（`GET /api/fs/browse` / `DELETE /api/workspaces/{ws}` / `list_workspaces` 补字段）。

**Architecture:** 后端三处独立改动（fs/browse 列目录 + 删 workspace + list 补字段对齐前端 type）；前端复用 DSF 组件库（shadcn Dialog/Table/Button/Input/Select/Skeleton），新建 FileSystemPicker（子项目 3 ScanNewPage 消费）+ useWorkspaces 轮询 hook + DataTable 重写列表页。增量迁移：仅本页迁 Tailwind，旧 events.css 保留（其他页仍消费 `.ledger`）。

**Tech Stack:** 后端 FastAPI + TestClient；前端 React 18 + @tanstack/react-table + shadcn/ui（DSF 已 copy）+ vitest + MSW

## Global Constraints

- **复用 DSF**：`@/components/ui/*`（button/dialog/table/select/input/skeleton/badge/tooltip 已 copy）+ `cn()` from `@/lib/utils` + Tailwind utility class + `hsl(var(--token))` 消费双主题变量。
- **增量迁移**：仅 WorkspaceListPage 迁 Tailwind；**不动其他业务页内部**（详情 5 tab / 扫描页 / Dashboard / Settings）；旧 `events.css` 保留（OverviewTab agent-table 仍用 `.ledger`）；不向 events.css 加新规则。
- **不动 DSF 产物**：tokens.css / tailwind.config / TopBar / AppShell / ThemeToggle 不改。
- **后端测试栈**：`fastapi.testclient.TestClient` + `app_with_ws`/`tmp_workspaces` fixtures（conftest.py）+ `_reset_config` autouse（清 get_config lru_cache）；造 ws = 建 `<ws>/session.json`。
- **前端测试栈**：vitest（globals + jsdom）+ MSW（`setupServer` + `http`/`HttpResponse`）+ @testing-library/react；lifecycle：`beforeAll listen / afterEach resetHandlers+cleanup / afterAll close`。
- **路径别名 `@/ → src/`**（DSF Task 1 已建）。
- **shadcn 组件 export**：Dialog={DialogContent,DialogHeader,DialogTitle,DialogFooter,DialogTrigger,DialogClose}；Table={Table,TableHeader,TableBody,TableRow,TableHead,TableCell}；Select={Select,SelectTrigger,SelectValue,SelectContent,SelectItem}。
- **后端工作目录**：后端命令在 `packages/web/`；前端命令在 `packages/web/frontend/`。
- **spec 文档**：`docs/superpowers/specs/2026-07-04-shannon-web-redesign-list-fs-design.md`（契约来源）。

---

## File Structure

**后端 Create:**
- `packages/web/src/shannon_web/api/fs.py` — `GET /api/fs/browse` router
- `packages/web/tests/test_fs_browse.py`
- `packages/web/tests/test_workspaces_delete.py`

**后端 Modify:**
- `packages/web/src/shannon_web/config.py` — 加 `fs_roots`
- `packages/web/src/shannon_web/app.py` — include fs.router
- `packages/web/src/shannon_web/api/workspaces.py` — 加 `DELETE /{ws}`
- `packages/web/src/shannon_web/components/workspaces_indexer.py` — `list_workspaces` 补字段
- `packages/web/tests/test_workspaces_indexer.py` — 扩补字段断言

**前端 Create:**
- `packages/web/frontend/src/components/FileSystemPicker.tsx` + `.test.tsx`
- `packages/web/frontend/src/api/useWorkspaces.ts` + `.test.ts`

**前端 Modify:**
- `packages/web/frontend/src/api/types.ts` — FsEntry / FsBrowseResult + Workspace.is_correlation
- `packages/web/frontend/src/api/client.ts` — browseFs / deleteWorkspace / cancelScan
- `packages/web/frontend/src/pages/WorkspaceListPage.tsx` — DataTable 重写
- `packages/web/frontend/src/pages/WorkspaceListPage.test.tsx` — 重写（旧 .ledger 断言全废）
- `packages/web/frontend/src/pages/DevComponentsPage.tsx` — 加 FileSystemPicker demo
- `packages/web/frontend/package.json` — +`@tanstack/react-table`

---

## Task 1: 后端 `GET /api/fs/browse` + `WebConfig.fs_roots`

**Files:**
- Modify: `packages/web/src/shannon_web/config.py`
- Create: `packages/web/src/shannon_web/api/fs.py`
- Modify: `packages/web/src/shannon_web/app.py`
- Test: `packages/web/tests/test_fs_browse.py`

**Interfaces:**
- Consumes: `WebConfig.fs_roots: list[Path]`（本 task 加）；`os.scandir` / `Path.is_absolute` / `Path.resolve`
- Produces: `GET /api/fs/browse?path=<abs>` → `{path, parent, entries: [{name, type, size?, mtime?}], truncated?}`；400/404/403/409 错误码。fs.router 注册到 app。

- [ ] **Step 1: 加 `WebConfig.fs_roots`**

Modify `packages/web/src/shannon_web/config.py`，在 `__init__` 末尾（`self.frontend_dir = ...` 之后）加：
```python
        self.fs_roots: list[Path] = [
            Path(p).resolve() for p in os.environ.get("SHANNON_FS_ROOTS", "").split(",") if p.strip()
        ]
```

- [ ] **Step 2: 写失败测试 `tests/test_fs_browse.py`**

Create `packages/web/tests/test_fs_browse.py`:
```python
import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_no_roots(app_with_ws):
    """默认 fs_roots=[]（整机可见）。"""
    return app_with_ws


@pytest.fixture
def app_with_roots(tmp_workspaces, monkeypatch):
    """配 SHANNON_FS_ROOTS=tmp_path 限制可见根。"""
    monkeypatch.setenv("SHANNON_FS_ROOTS", str(tmp_workspaces))
    from shannon_web import config as cfg_mod
    cfg_mod.get_config.cache_clear()
    from shannon_web.app import create_app
    app = create_app()
    cfg_mod.get_config.cache_clear()
    return app


def test_list_dir_entries(app_no_roots, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / ".hidden").write_text("y")
    client = TestClient(app_no_roots)
    r = client.get("/api/fs/browse", params={"path": str(tmp_path)})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == str(tmp_path.resolve())
    names = {e["name"]: e for e in body["entries"]}
    assert names["sub"]["type"] == "dir"
    assert names["a.txt"]["type"] == "file"
    assert names[".hidden"]["type"] == "file"  # dotfiles 显示
    assert "size" in names["a.txt"]


def test_parent_root_is_null(app_no_roots, tmp_path):
    client = TestClient(app_no_roots)
    # tmp_path 的 parent 有值；根目录（/）的 parent 为 null
    r = client.get("/api/fs/browse", params={"path": str(tmp_path)})
    assert r.json()["parent"] == str(tmp_path.resolve().parent)
    root = client.get("/api/fs/browse", params={"path": "/"})
    assert root.json()["parent"] is None


def test_sort_dirs_first(app_no_roots, tmp_path):
    (tmp_path / "z_dir").mkdir()
    (tmp_path / "a_file").write_text("x")
    (tmp_path / "m_dir").mkdir()
    client = TestClient(app_no_roots)
    entries = client.get("/api/fs/browse", params={"path": str(tmp_path)}).json()["entries"]
    types = [e["type"] for e in entries]
    # 目录全在文件前
    assert types == ["dir", "dir", "file"]


def test_reject_relative_path(app_no_roots):
    client = TestClient(app_no_roots)
    r = client.get("/api/fs/browse", params={"path": "relative/path"})
    assert r.status_code == 400


def test_traversal_rejected(app_no_roots, tmp_path):
    client = TestClient(app_no_roots)
    # / 等安全路径；用相对 .. 测 400（is_absolute false）
    r = client.get("/api/fs/browse", params={"path": "../../etc"})
    assert r.status_code == 400


def test_not_exist_404(app_no_roots, tmp_path):
    client = TestClient(app_no_roots)
    r = client.get("/api/fs/browse", params={"path": str(tmp_path / "nope")})
    assert r.status_code == 404


def test_file_not_dir_400(app_no_roots, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    client = TestClient(app_no_roots)
    r = client.get("/api/fs/browse", params={"path": str(f)})
    assert r.status_code == 400


def test_allowlist_violation_409(app_with_roots, tmp_path):
    # roots=[tmp_path]；访问 tmp_path 之外 → 409
    client = TestClient(app_with_roots)
    r = client.get("/api/fs/browse", params={"path": str(tmp_path.parent)})
    assert r.status_code == 409


def test_allowlist_inside_ok(app_with_roots, tmp_path):
    (tmp_path / "sub").mkdir()
    client = TestClient(app_with_roots)
    r = client.get("/api/fs/browse", params={"path": str(tmp_path / "sub")})
    assert r.status_code == 200


def test_tilde_expands_home(app_no_roots):
    client = TestClient(app_no_roots)
    r = client.get("/api/fs/browse", params={"path": "~"})
    assert r.status_code == 200
    assert r.json()["path"] == os.path.expanduser("~")


def test_truncated(monkeypatch, app_no_roots, tmp_path):
    # 造 10 个 entry，MAX_ENTRIES 改 5 → truncated=True + 5 条
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x")
    from shannon_web.api import fs as fs_mod
    monkeypatch.setattr(fs_mod, "MAX_ENTRIES", 5)
    client = TestClient(app_no_roots)
    body = client.get("/api/fs/browse", params={"path": str(tmp_path)}).json()
    assert body["truncated"] is True
    assert len(body["entries"]) == 5
```

- [ ] **Step 3: 跑测试验证失败**

Run（在 `packages/web/` 下）: `python -m pytest tests/test_fs_browse.py -v`
Expected: FAIL（`/api/fs/browse` 路由不存在 → 404）。

- [ ] **Step 4: 写 `api/fs.py`**

Create `packages/web/src/shannon_web/api/fs.py`:
```python
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/fs", tags=["fs"])

MAX_ENTRIES = 5000


@router.get("/browse")
async def browse(request: Request, path: str):
    # ~ 展开 home
    if path == "~":
        path = os.path.expanduser("~")
    if not Path(path).is_absolute():
        raise HTTPException(status_code=400, detail="path must be absolute")

    resolved = Path(path).resolve()

    # allowlist（配了 SHANNON_FS_ROOTS 才约束）
    roots = request.app.state.config.fs_roots
    if roots:
        inside = any(resolved == root or root in resolved.parents for root in roots)
        if not inside:
            raise HTTPException(status_code=409, detail="path outside allowed roots")

    if not resolved.exists():
        raise HTTPException(status_code=404, detail="path not found")
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="path is not a directory")

    try:
        scandir = list(os.scandir(resolved))
    except PermissionError:
        raise HTTPException(status_code=403, detail="permission denied")

    entries: list[dict] = []
    for entry in scandir:
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        is_dir = entry.is_dir(follow_symlinks=False)
        entries.append({
            "name": entry.name,
            "type": "dir" if is_dir else "file",
            **({} if is_dir else {"size": stat.st_size}),
            "mtime": int(stat.st_mtime),
        })

    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))

    truncated = False
    if len(entries) > MAX_ENTRIES:
        entries = entries[:MAX_ENTRIES]
        truncated = True

    parent = str(resolved.parent) if resolved != resolved.parent else None

    return {
        "path": str(resolved),
        "parent": parent,
        "entries": entries,
        **({"truncated": True} if truncated else {}),
    }
```

> `shutil` import 多余，移除（本 task 未用）。最终 fs.py 不 import shutil。

- [ ] **Step 5: 确认 fs.py 无 unused import**

`api/fs.py` 顶部 import 应仅：`import os`（scandir/expanduser）、`from pathlib import Path`（is_absolute/resolve）、`from fastapi import APIRouter, HTTPException, Request`（`request: Request` 参数消费）。无 shutil/其他未用 import。

- [ ] **Step 6: app.py 注册 fs.router**

Modify `packages/web/src/shannon_web/app.py`，在 `from .api import events, multi_configs, scan, workspaces` 行改为：
```python
    from .api import events, fs, multi_configs, scan, workspaces
```
在 `app.include_router(events.router)` 之后加：
```python
    app.include_router(fs.router)
```

- [ ] **Step 7: 跑测试验证通过**

Run: `python -m pytest tests/test_fs_browse.py -v`
Expected: PASS（11 用例全绿）。

- [ ] **Step 8: 跑现有后端测试不破**

Run: `python -m pytest tests/ -v`
Expected: 现有测试全绿（fs router 新增不影响 workspaces/scan/events/multi_configs）。

- [ ] **Step 9: Commit**

```bash
git add packages/web/src/shannon_web/api/fs.py packages/web/src/shannon_web/config.py packages/web/src/shannon_web/app.py packages/web/tests/test_fs_browse.py
git commit -m "feat(web): GET /api/fs/browse 列目录（穿越防护+allowlist+dotfiles+truncated+~ 展开）"
```

---

## Task 2: 后端 `DELETE /api/workspaces/{ws}`

**Files:**
- Modify: `packages/web/src/shannon_web/api/workspaces.py`
- Test: `packages/web/tests/test_workspaces_delete.py`

**Interfaces:**
- Consumes: `_workspace_path(request, ws)`（已存在）；`scan_manager.active_pids() -> dict[str,int]`；`WorkspacesIndexer._pid_alive(pid) -> bool`（staticmethod）；`indexer.set_active_pid(ws, None)`
- Produces: `DELETE /api/workspaces/{ws}` → `{deleted: ws}`；404（不存在）/ 409（运行中）。

- [ ] **Step 1: 写失败测试 `tests/test_workspaces_delete.py`**

Create `packages/web/tests/test_workspaces_delete.py`:
```python
import json
import shutil

from fastapi.testclient import TestClient


def _make_ws(root, name, status="completed"):
    ws = root / name
    ws.mkdir(parents=True)
    (ws / "session.json").write_text(json.dumps({
        "status": status, "scan_type": "whitebox",
        "created_at": "2026-07-02T10:00:00Z",
    }))


def test_delete_completed_ws(app_with_ws, tmp_workspaces):
    _make_ws(tmp_workspaces, "done-ws")
    client = TestClient(app_with_ws)
    r = client.delete("/api/workspaces/done-ws")
    assert r.status_code == 200
    assert r.json() == {"deleted": "done-ws"}
    assert not (tmp_workspaces / "done-ws").exists()
    # list 不再返
    assert all(w["name"] != "done-ws" for w in client.get("/api/workspaces").json())


def test_delete_not_exist_404(app_with_ws):
    client = TestClient(app_with_ws)
    assert client.delete("/api/workspaces/nope").status_code == 404


def test_delete_running_rejected_409(app_with_ws, tmp_workspaces):
    _make_ws(tmp_workspaces, "running-ws", status="running")
    # 注入一个 alive pid（当前进程）使 indexer 判运行中
    import os
    app_with_ws.state.indexer.set_active_pid("running-ws", os.getpid())
    client = TestClient(app_with_ws)
    r = client.delete("/api/workspaces/running-ws")
    assert r.status_code == 409
    assert (tmp_workspaces / "running-ws").exists()  # 未删
```

> 当前进程 pid 一定 alive（`os.kill(os.getpid(), 0)` 成功），故 `_pid_alive` 返 True → 409。

- [ ] **Step 2: 跑测试验证失败**

Run: `python -m pytest tests/test_workspaces_delete.py -v`
Expected: FAIL（`DELETE /api/workspaces/{ws}` 路由不存在 → 405/404）。

- [ ] **Step 3: 加 DELETE 端点到 `api/workspaces.py`**

Modify `packages/web/src/shannon_web/api/workspaces.py`，文件顶部 import 区加 `shutil`（删目录）+ `WorkspacesIndexer`（pid alive 检查）。最终顶部 import 为：
```python
from __future__ import annotations

import shutil

from fastapi import APIRouter, HTTPException, Request

from shannon_web.components.workspaces_indexer import WorkspacesIndexer
```
> 原 `workspaces.py` 仅 import `APIRouter / HTTPException / Request`；本 task 加 `shutil` + `WorkspacesIndexer`，不误增其他。

在 `get_workspace` 端点之后（`@router.get("/{ws}")` 函数体后）加：
```python
@router.delete("/{ws}")
async def delete_workspace(ws: str, request: Request):
    p = _workspace_path(request, ws)  # 404 if 不存在
    active = request.app.state.scan_manager.active_pids()
    if ws in active and WorkspacesIndexer._pid_alive(active[ws]):
        raise HTTPException(status_code=409, detail="workspace running, cancel scan first")
    shutil.rmtree(p)
    request.app.state.indexer.set_active_pid(ws, None)
    return {"deleted": ws}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `python -m pytest tests/test_workspaces_delete.py -v`
Expected: PASS（3 用例全绿）。

- [ ] **Step 5: 跑现有后端测试不破**

Run: `python -m pytest tests/ -v`
Expected: 全绿（含 fs_browse + workspaces_delete + 现有）。

- [ ] **Step 6: Commit**

```bash
git add packages/web/src/shannon_web/api/workspaces.py packages/web/tests/test_workspaces_delete.py
git commit -m "feat(web): DELETE /api/workspaces/{ws}（删目录+运行中拒绝+indexer pid 清）"
```

---

## Task 3: 后端 `list_workspaces` 补字段

**Files:**
- Modify: `packages/web/src/shannon_web/components/workspaces_indexer.py`
- Modify: `packages/web/tests/test_workspaces_indexer.py`

**Interfaces:**
- Consumes: `mgr.get_session_data(ws_path) -> dict`（已调用，现取返回值）；`vuln_counts` dict（已取）
- Produces: `list_workspaces` 每行追加 `total_cost_usd` / `total_duration_ms` / `vuln_count`（number）/ `links`，对齐前端 Workspace type。

- [ ] **Step 1: 看现有 test_workspaces_indexer.py 结构**

Run: `cat packages/web/tests/test_workspaces_indexer.py | head -40`
确认现有 fixture + 断言风格（造 ws + session.json + 断 list 字段）。

- [ ] **Step 2: 写失败测试（扩 test_workspaces_indexer.py）**

在 `packages/web/tests/test_workspaces_indexer.py` **末尾**追加：
```python
def test_list_supplements_cost_duration_links_vuln_count(tmp_workspaces):
    """list_workspaces 补返 total_cost_usd/total_duration_ms/vuln_count(number)/links。"""
    import json
    ws = tmp_workspaces / "full-ws"
    ws.mkdir()
    (ws / "session.json").write_text(json.dumps({
        "status": "completed", "scan_type": "whitebox",
        "created_at": 1780000000.0,
        "metrics": {"total_cost_usd": 1.23, "total_duration_ms": 45000},
        "links": {"child_workspaces": ["child-a", "child-b"]},
    }))
    from shannon_web.components.workspaces_indexer import WorkspacesIndexer
    rows = WorkspacesIndexer(tmp_workspaces).list_workspaces()
    row = next(r for r in rows if r["name"] == "full-ws")
    assert row["total_cost_usd"] == 1.23
    assert row["total_duration_ms"] == 45000
    assert row["links"] == {"child_workspaces": ["child-a", "child-b"]}
    # vuln_count 是聚合后的 number（无漏洞数据 → 0）
    assert row["vuln_count"] == 0
    assert isinstance(row["vuln_count"], int)


def test_list_vuln_count_aggregates_dict(tmp_workspaces):
    """vuln_counts dict → vuln_count number（sum values）。"""
    import json
    ws = tmp_workspaces / "agg-ws"
    ws.mkdir()
    (ws / "session.json").write_text(json.dumps({
        "status": "completed", "scan_type": "whitebox", "created_at": 1,
    }))
    from shannon_web.components.workspaces_indexer import WorkspacesIndexer
    idx = WorkspacesIndexer(tmp_workspaces)
    # mock get_workspace_vuln_counts 返多类型 dict
    import shannon_web.components.workspaces_indexer as mod
    orig = mod.get_workspace_vuln_counts
    mod.get_workspace_vuln_counts = lambda _p: {"injection": 3, "xss": 2}
    try:
        rows = idx.list_workspaces()
    finally:
        mod.get_workspace_vuln_counts = orig
    row = next(r for r in rows if r["name"] == "agg-ws")
    assert row["vuln_count"] == 5
    assert row["vuln_counts"] == {"injection": 3, "xss": 2}


def test_list_missing_metrics_returns_none(tmp_workspaces):
    """session.json 无 metrics → total_cost_usd/duration 为 None，不崩。"""
    import json
    ws = tmp_workspaces / "bare-ws"
    ws.mkdir()
    (ws / "session.json").write_text(json.dumps({
        "status": "completed", "scan_type": "whitebox", "created_at": 1,
    }))
    from shannon_web.components.workspaces_indexer import WorkspacesIndexer
    row = next(r for r in WorkspacesIndexer(tmp_workspaces).list_workspaces() if r["name"] == "bare-ws")
    assert row["total_cost_usd"] is None
    assert row["total_duration_ms"] is None
    assert row["links"] == {}
```

- [ ] **Step 3: 跑测试验证失败**

Run: `python -m pytest tests/test_workspaces_indexer.py::test_list_supplements_cost_duration_links_vuln_count -v`
Expected: FAIL（`KeyError: 'total_cost_usd'`，list_workspaces 未返该字段）。

- [ ] **Step 4: 改 `list_workspaces` 取 session data 补字段**

Modify `packages/web/src/shannon_web/components/workspaces_indexer.py` 的 `list_workspaces` 方法，把：
```python
            try:
                mgr.get_session_data(ws_path)  # 触发读，失败则跳过
            except Exception:
                continue
            scan_type = mgr.get_scan_type(ws_path)
            status = self._status_of(name, mgr.get_status(ws_path))
            try:
                vuln = get_workspace_vuln_counts(ws_path)
            except Exception:
                vuln = {}
            out.append({
                "name": name,
                "scan_type": scan_type,
                "status": status,
                "vuln_counts": vuln,
                "created_at": mgr.get_created_at(ws_path),
                "completed_at": mgr.get_completed_at(ws_path),
                "is_correlation": scan_type == "correlation",
            })
```
替换为：
```python
            try:
                data = mgr.get_session_data(ws_path)
            except Exception:
                continue
            scan_type = mgr.get_scan_type(ws_path)
            status = self._status_of(name, mgr.get_status(ws_path))
            try:
                vuln = get_workspace_vuln_counts(ws_path)
            except Exception:
                vuln = {}
            metrics = data.get("metrics", {}) if isinstance(data, dict) else {}
            out.append({
                "name": name,
                "scan_type": scan_type,
                "status": status,
                "vuln_counts": vuln,
                "vuln_count": sum(vuln.values()) if vuln else 0,
                "total_cost_usd": metrics.get("total_cost_usd"),
                "total_duration_ms": metrics.get("total_duration_ms"),
                "links": data.get("links", {}) if isinstance(data, dict) else {},
                "created_at": mgr.get_created_at(ws_path),
                "completed_at": mgr.get_completed_at(ws_path),
                "is_correlation": scan_type == "correlation",
            })
```

- [ ] **Step 5: 跑测试验证通过**

Run: `python -m pytest tests/test_workspaces_indexer.py -v`
Expected: PASS（新 3 用例 + 现有全绿）。

- [ ] **Step 6: 跑现有前端测试不破（前端 type 已含这些可选字段，DSF 定）**

Run（在 `packages/web/frontend/` 下）: `npx vitest run`
Expected: 前端测试全绿（WorkspaceListPage.test 用的 fixture 已含 total_cost_usd 等，后端对齐后一致）。

- [ ] **Step 7: Commit**

```bash
git add packages/web/src/shannon_web/components/workspaces_indexer.py packages/web/tests/test_workspaces_indexer.py
git commit -m "feat(web): list_workspaces 补字段（cost/duration/links/vuln_count 对齐前端 type）"
```

---

## Task 4: 前端 types + api client

**Files:**
- Modify: `packages/web/frontend/src/api/types.ts`
- Modify: `packages/web/frontend/src/api/client.ts`
- Test: `packages/web/frontend/src/api/types.test.ts`（若存在则扩；否则跳过 type 测试，client 用 MSW 集成测在组件层）

**Interfaces:**
- Consumes: 无（基础类型 + fetch 封装）
- Produces:
  - `FsEntry` / `FsBrowseResult` types（types.ts）
  - `Workspace.is_correlation?: boolean`（types.ts 加）
  - `browseFs(path): Promise<FsBrowseResult>` / `deleteWorkspace(ws): Promise<{deleted:string}>` / `cancelScan(ws): Promise<{cancelled:string}>`（client.ts）

- [ ] **Step 1: types.ts 加 FsEntry / FsBrowseResult + Workspace.is_correlation**

Modify `packages/web/frontend/src/api/types.ts`，在 `Workspace` interface 内 `"links"?: ...` 行之后加 `is_correlation?: boolean;`：
```ts
  links?: { parent_workspace?: string | null; child_workspaces?: string[] };
  is_correlation?: boolean;
}
```
在文件**末尾**追加：
```ts
export interface FsEntry {
  name: string;
  type: "dir" | "file";
  size?: number;
  mtime?: number;
}
export interface FsBrowseResult {
  path: string;
  parent: string | null;
  entries: FsEntry[];
  truncated?: boolean;
}
```

- [ ] **Step 2: client.ts 加 browseFs / deleteWorkspace / cancelScan**

Modify `packages/web/frontend/src/api/client.ts`，在 `apiGetText` 函数之前加：
```ts
export const browseFs = (path: string) =>
  apiGet<FsBrowseResult>(`/fs/browse?path=${encodeURIComponent(path)}`);
export const deleteWorkspace = (ws: string) =>
  apiDelete<{ deleted: string }>(`workspaces/${encodeURIComponent(ws)}`);
export const cancelScan = (ws: string) =>
  apiDelete<{ cancelled: string }>(`scan/${encodeURIComponent(ws)}`);
```
并在文件顶部 `import` 区加类型 import（与现有 `ApiError` 同 module 内，类型可从 `./types` 引）：
```ts
import type { FsBrowseResult } from "./types";
```

- [ ] **Step 3: 跑 TS 编译验证**

Run（在 `packages/web/frontend/` 下）: `npx tsc -b`
Expected: 0 错（无未用 import / 类型对齐）。

- [ ] **Step 4: 跑现有测试不破**

Run: `npx vitest run`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/api/types.ts packages/web/frontend/src/api/client.ts
git commit -m "feat(web-frontend): FsEntry/FsBrowseResult 类型 + browseFs/deleteWorkspace/cancelScan client"
```

---

## Task 5: 装 `@tanstack/react-table`

**Files:**
- Modify: `packages/web/frontend/package.json`

**Interfaces:**
- Consumes: 无
- Produces: `@tanstack/react-table` 可 import（Task 8 DataTable 用）

- [ ] **Step 1: 装依赖**

Run（在 `packages/web/frontend/` 下）:
```bash
npm install @tanstack/react-table@^8
```
Expected: `package.json` dependencies 新增 `@tanstack/react-table`。

- [ ] **Step 2: 验证 import 可用**

Run: `npx tsc -b`
Expected: 0 错。

- [ ] **Step 3: Commit**

```bash
git add packages/web/frontend/package.json packages/web/frontend/package-lock.json
git commit -m "chore(web-frontend): 装 @tanstack/react-table（列表页 DataTable 用）"
```

---

## Task 6: `<FileSystemPicker>` 组件

**Files:**
- Create: `packages/web/frontend/src/components/FileSystemPicker.tsx`
- Test: `packages/web/frontend/src/components/FileSystemPicker.test.tsx`

**Interfaces:**
- Consumes: `browseFs` from `@/api/client`；`Dialog/Button/Input` from `@/components/ui/*`；`cn` from `@/lib/utils`；`FsEntry`/`FsBrowseResult` from `@/api/types`
- Produces: `<FileSystemPicker value onChange title? triggerLabel? />`（受控；Dialog 模态选目录；localStorage 书签 `shannon-fs-recent`）。

- [ ] **Step 1: 写失败测试 `src/components/FileSystemPicker.test.tsx`**

Create `packages/web/frontend/src/components/FileSystemPicker.test.tsx`:
```tsx
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { FileSystemPicker } from "./FileSystemPicker";

const ROOT = "/tmp/test-root";
const SUB = `${ROOT}/sub`;

const server = setupServer(
  http.get("/api/fs/browse", (req) => {
    const path = new URL(req.request.url).searchParams.get("path");
    if (path === ROOT) {
      return HttpResponse.json({
        path: ROOT, parent: "/tmp",
        entries: [
          { name: "sub", type: "dir" },
          { name: "a.txt", type: "file", size: 10 },
        ],
      });
    }
    if (path === SUB) {
      return HttpResponse.json({ path: SUB, parent: ROOT, entries: [] });
    }
    if (path === "/nope") {
      return HttpResponse.json({ detail: "path not found" }, { status: 404 });
    }
    return HttpResponse.json({ path: path ?? "/", parent: null, entries: [] });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  localStorage.clear();
  cleanup();
});
afterAll(() => server.close());

function renderPicker(props: { value?: string; onChange?: (v: string) => void } = {}) {
  let value = props.value ?? "";
  const onChange = props.onChange ?? ((v: string) => { value = v; });
  const r = render(
    <FileSystemPicker value={value} onChange={onChange} {...props} />,
  );
  return { ...r, getValue: () => value };
}

describe("FileSystemPicker", () => {
  it("打开 Dialog → 列目录 entries", async () => {
    renderPicker({ value: ROOT });
    fireEvent.click(screen.getByRole("button", { name: /浏览/ }));
    await waitFor(() => expect(screen.getByText("sub")).toBeInTheDocument());
    expect(screen.getByText("a.txt")).toBeInTheDocument();
  });

  it("双击目录 → 进入子目录", async () => {
    renderPicker({ value: ROOT });
    fireEvent.click(screen.getByRole("button", { name: /浏览/ }));
    await waitFor(() => expect(screen.getByText("sub")).toBeInTheDocument());
    fireEvent.doubleClick(screen.getByText("sub"));
    await waitFor(() => expect(screen.getByText(/空目录|empty/i)).toBeInTheDocument());
  });

  it("单击目录选中 + '选择此目录'启用；单击文件不启用", async () => {
    renderPicker({ value: ROOT });
    fireEvent.click(screen.getByRole("button", { name: /浏览/ }));
    await waitFor(() => expect(screen.getByText("sub")).toBeInTheDocument());
    const confirmBtn = screen.getByRole("button", { name: /选择此目录/ });
    expect(confirmBtn).toBeDisabled();
    fireEvent.click(screen.getByText("sub"));
    expect(confirmBtn).not.toBeDisabled();
    // 点文件不启用确认
    fireEvent.click(screen.getByText("a.txt"));
    expect(confirmBtn).toBeDisabled();
  });

  it("确认 → onChange 回填 + 书签写入 localStorage", async () => {
    const onChange = vi.fn();
    renderPicker({ value: ROOT, onChange });
    fireEvent.click(screen.getByRole("button", { name: /浏览/ }));
    await waitFor(() => expect(screen.getByText("sub")).toBeInTheDocument());
    fireEvent.click(screen.getByText("sub"));
    fireEvent.click(screen.getByRole("button", { name: /选择此目录/ }));
    expect(onChange).toHaveBeenCalledWith(SUB);
    const recent = JSON.parse(localStorage.getItem("shannon-fs-recent") ?? "[]");
    expect(recent).toContain(SUB);
  });

  it("404 → inline 错误，不关 Dialog", async () => {
    renderPicker({ value: "/nope" });
    fireEvent.click(screen.getByRole("button", { name: /浏览/ }));
    await waitFor(() => expect(screen.getByText(/not found|不存在/)).toBeInTheDocument());
    // Dialog 仍在
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `npx vitest run src/components/FileSystemPicker.test.tsx`
Expected: FAIL（组件未定义）。

- [ ] **Step 3: 写 `src/components/FileSystemPicker.tsx`**

Create `packages/web/frontend/src/components/FileSystemPicker.tsx:
```tsx
import { useEffect, useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { browseFs } from "@/api/client";
import type { FsEntry } from "@/api/types";
import { ApiError } from "@/api/client";
import { cn } from "@/lib/utils";

const RECENT_KEY = "shannon-fs-recent";

export interface FileSystemPickerProps {
  value: string;
  onChange: (abs: string) => void;
  title?: string;
  triggerLabel?: string;
}

function loadRecent(): string[] {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function pushRecent(path: string): string[] {
  const cur = loadRecent().filter((p) => p !== path);
  const next = [path, ...cur].slice(0, 5);
  localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  return next;
}

export function FileSystemPicker({ value, onChange, title = "选择代码目录", triggerLabel = "📁 浏览" }: FileSystemPickerProps) {
  const [open, setOpen] = useState(false);
  const [currentPath, setCurrentPath] = useState(value || "/");
  const [entries, setEntries] = useState<FsEntry[]>([]);
  const [parent, setParent] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [manualPath, setManualPath] = useState(value || "/");
  const [error, setError] = useState<string | null>(null);
  const [recent, setRecent] = useState<string[]>([]);

  async function load(path: string) {
    setError(null);
    try {
      const r = await browseFs(path);
      setCurrentPath(r.path);
      setEntries(r.entries);
      setParent(r.parent);
      setManualPath(r.path);
      setSelected(null);
    } catch (e) {
      if (e instanceof ApiError) {
        const detail = (e.body as { detail?: string })?.detail;
        setError(detail ?? `错误（${e.status}）`);
      } else {
        setError("请求失败");
      }
    }
  }

  useEffect(() => {
    if (open) {
      setRecent(loadRecent());
      load(value || "/");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function confirm() {
    if (!selected) return;
    onChange(selected);
    setRecent(pushRecent(selected));
    setOpen(false);
  }

  const selectedIsDir = entries.find((e) => `${currentPath}/${e.name}` === selected)?.type === "dir";

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>{triggerLabel}</Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
          </DialogHeader>

          {/* 面包屑 + home + 刷新 */}
          <div className="flex items-center gap-2 text-sm">
            <Button variant="ghost" size="sm" onClick={() => load("~")}>🏠</Button>
            <span className="font-mono text-muted-foreground truncate">{currentPath}</span>
            <Button variant="ghost" size="icon" aria-label="刷新" onClick={() => load(currentPath)}>↻</Button>
          </div>

          {/* 最近书签 */}
          {recent.length > 0 && (
            <div className="flex flex-wrap items-center gap-1 text-xs">
              <span className="text-muted-foreground">最近：</span>
              {recent.map((p) => (
                <button key={p} className="rounded border border-border px-2 py-0.5 hover:bg-accent" onClick={() => load(p)}>
                  {p.split("/").pop() || p}
                </button>
              ))}
            </div>
          )}

          {/* 列表区 */}
          <div className="min-h-[200px] max-h-[320px] overflow-auto rounded-md border border-border bg-background">
            {error ? (
              <div className="p-3 text-sm text-red">⚠ {error}</div>
            ) : entries.length === 0 ? (
              <div className="p-3 text-sm text-muted-foreground">空目录</div>
            ) : (
              <ul>
                {entries.map((e) => {
                  const full = `${currentPath}/${e.name}`;
                  const isDir = e.type === "dir";
                  return (
                    <li
                      key={e.name}
                      data-selected={selected === full}
                      onDoubleClick={() => isDir && load(full)}
                      onClick={() => setSelected(full)}
                      className={cn(
                        "flex cursor-pointer items-center gap-2 px-3 py-1 text-sm",
                        isDir ? "text-foreground" : "text-muted-foreground",
                        selected === full && "bg-accent",
                      )}
                    >
                      <span>{isDir ? "📁" : "📄"}</span>
                      <span className="font-mono">{e.name}</span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {/* 路径输入 */}
          <Input
            value={manualPath}
            onChange={(e) => setManualPath(e.target.value)}
            onBlur={() => manualPath !== currentPath && load(manualPath)}
            onKeyDown={(e) => { if (e.key === "Enter") load(manualPath); }}
            className="font-mono text-sm"
          />

          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>取消</Button>
            <Button onClick={confirm} disabled={!selectedIsDir}>选择此目录</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `npx vitest run src/components/FileSystemPicker.test.tsx`
Expected: PASS（5 用例全绿）。若红，回查：双击/单击/确认逻辑、localStorage key、ApiError detail 提取。

- [ ] **Step 5: 跑 TS 编译**

Run: `npx tsc -b`
Expected: 0 错。

- [ ] **Step 6: Commit**

```bash
git add packages/web/frontend/src/components/FileSystemPicker.tsx packages/web/frontend/src/components/FileSystemPicker.test.tsx
git commit -m "feat(web-frontend): FileSystemPicker 组件（Dialog 模态+面包屑+书签+路径输入+错误 inline）"
```

---

## Task 7: `useWorkspaces` 轮询 hook

**Files:**
- Create: `packages/web/frontend/src/api/useWorkspaces.ts`
- Test: `packages/web/frontend/src/api/useWorkspaces.test.ts`

**Interfaces:**
- Consumes: `apiGet<Workspace[]>("/workspaces")` from `@/api/client`
- Produces: `useWorkspaces()` → `{ data: Workspace[]; loading: boolean; lastUpdated: Date | null; refresh: () => void; error: string | null }`；5s 轮询；unmount 清理 timer。

- [ ] **Step 1: 写失败测试 `src/api/useWorkspaces.test.ts`**

Create `packages/web/frontend/src/api/useWorkspaces.test.tsx`（hook 测试用 renderHook）:
```tsx
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from "vitest";
import { renderHook, act, cleanup, waitFor } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { useWorkspaces } from "./useWorkspaces";

const server = setupServer(
  http.get("/api/workspaces", () => HttpResponse.json([
    { name: "ws-a", scan_type: "whitebox", status: "completed", created_at: 0 },
  ])),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { server.resetHandlers(); cleanup(); if (vi.isFakeTimers()) vi.useRealTimers(); });
afterAll(() => server.close());

describe("useWorkspaces", () => {
  it("初始 fetch + loading 转 false + data", async () => {
    const { result } = renderHook(() => useWorkspaces());
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.lastUpdated).not.toBeNull();
  });

  it("5s 轮询触发新 fetch", async () => {
    vi.useFakeTimers();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { result } = renderHook(() => useWorkspaces());
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    const afterMount = fetchSpy.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetchSpy.mock.calls.length).toBeGreaterThan(afterMount);
    fetchSpy.mockRestore();
  });

  it("refresh() 手动触发", async () => {
    const { result } = renderHook(() => useWorkspaces());
    await waitFor(() => expect(result.current.loading).toBe(false));
    const calls = vi.spyOn(globalThis, "fetch").mock.calls.length;
    await act(async () => { await result.current.refresh(); });
    expect(vi.spyOn(globalThis, "fetch").mock.calls.length).toBeGreaterThanOrEqual(calls);
  });

  it("fetch 错误 → error 非 null，loading false", async () => {
    server.use(http.get("/api/workspaces", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    const { result } = renderHook(() => useWorkspaces());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).not.toBeNull();
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `npx vitest run src/api/useWorkspaces.test.tsx`
Expected: FAIL（hook 未定义）。

- [ ] **Step 3: 写 `src/api/useWorkspaces.ts`**

Create `packages/web/frontend/src/api/useWorkspaces.ts`:
```ts
import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, ApiError } from "./client";
import type { Workspace } from "./types";

export interface UseWorkspacesResult {
  data: Workspace[];
  loading: boolean;
  lastUpdated: Date | null;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useWorkspaces(intervalMs = 5000): UseWorkspacesResult {
  const [data, setData] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const rows = await apiGet<Workspace[]>("/workspaces");
      setData(rows);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? `加载失败（${e.status}）` : "加载失败");
    } finally {
      setLastUpdated(new Date());
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    timerRef.current = setInterval(refresh, intervalMs);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [refresh, intervalMs]);

  return { data, loading, lastUpdated, error, refresh };
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `npx vitest run src/api/useWorkspaces.test.tsx`
Expected: PASS（4 用例全绿）。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/api/useWorkspaces.ts packages/web/frontend/src/api/useWorkspaces.test.tsx
git commit -m "feat(web-frontend): useWorkspaces hook（5s 轮询+lastUpdated+refresh+error）"
```

---

## Task 8: WorkspaceListPage DataTable 重写

**Files:**
- Modify: `packages/web/frontend/src/pages/WorkspaceListPage.tsx`（重写）
- Modify: `packages/web/frontend/src/pages/WorkspaceListPage.test.tsx`（重写，旧 .ledger 断言全废）

**Interfaces:**
- Consumes: `useWorkspaces`（Task 7）；`@tanstack/react-table`（Task 5）；shadcn Table/Dialog/Button/Input/Select/Skeleton（DSF）；`StatusBadge`（旧组件，迁移期复用）；`Empty`（DSF）；`deleteWorkspace`/`cancelScan`（Task 4）
- Produces: WorkspaceListPage（DataTable 全功能：搜索/筛选/排序/expandable/取消/删除/空态/loading/上次刷新）。

- [ ] **Step 1: 重写失败测试 `src/pages/WorkspaceListPage.test.tsx`**

Replace `packages/web/frontend/src/pages/WorkspaceListPage.test.tsx` 全文为:
```tsx
import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { WorkspaceListPage } from "./WorkspaceListPage";
import type { Workspace } from "../api/types";

const baseWorkspaces: Workspace[] = [
  { name: "ws-a", scan_type: "whitebox", status: "running", created_at: 1780000000, total_cost_usd: 2.34, total_duration_ms: 2530000, vuln_count: 14, is_correlation: false },
  { name: "ws-failed", scan_type: "blackbox", status: "failed", created_at: 1780000100, total_cost_usd: 0.5, total_duration_ms: 60000, vuln_count: 0, is_correlation: false },
  { name: "ws-corr", scan_type: "correlation", status: "completed", created_at: 1780000200, is_correlation: true, links: { child_workspaces: ["ws-child1"] } },
];

const server = setupServer(
  http.get("/api/workspaces", () => HttpResponse.json(baseWorkspaces)),
  http.delete("/api/workspaces/:ws", ({ params }) => HttpResponse.json({ deleted: params.ws })),
  http.delete("/api/scan/:ws", ({ params }) => HttpResponse.json({ cancelled: params.ws })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderPage() {
  return render(<MemoryRouter><WorkspaceListPage /></MemoryRouter>);
}

describe("WorkspaceListPage (DataTable)", () => {
  it("渲染所有 workspace 行 + 列（name/status/type/vulns/cost/time/操作）", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    expect(screen.getByText("ws-failed")).toBeInTheDocument();
    expect(screen.getByText("ws-corr")).toBeInTheDocument();
    expect(screen.getByText(/\$2\.34/)).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument();
  });

  it("搜索框过滤 name", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    const search = screen.getByPlaceholderText(/搜索/i);
    fireEvent.change(search, { target: { value: "failed" } });
    expect(screen.queryByText("ws-a")).not.toBeInTheDocument();
    expect(screen.getByText("ws-failed")).toBeInTheDocument();
  });

  it("status 筛选", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    // 选 failed → 仅 ws-failed
    fireEvent.change(screen.getByLabelText(/status/i), { target: { value: "failed" } });
    expect(screen.queryByText("ws-a")).not.toBeInTheDocument();
    expect(screen.getByText("ws-failed")).toBeInTheDocument();
  });

  it("correlation 行 expandable → 展开显子 ws", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-corr")).toBeInTheDocument());
    // 展开按钮（correlation 行）
    const expandBtn = screen.getAllByRole("button", { name: /展开/i })[0];
    fireEvent.click(expandBtn);
    expect(await screen.findByText("ws-child1")).toBeInTheDocument();
  });

  it("running 行有'取消'按钮；点击 → Dialog 确认 → cancelScan", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /取消/ }));
    expect(await screen.findByText(/取消扫描/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /确认/ }));
    // cancelScan DELETE /api/scan/ws-a 已 mock → 触发 refresh
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("非 running 行有'删除'按钮；点击 → Dialog 确认 → deleteWorkspace", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-failed")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /删除/ }));
    expect(await screen.findByText(/删除 workspace/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /确认/ }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("空列表 → Empty 空态", async () => {
    server.use(http.get("/api/workspaces", () => HttpResponse.json([])));
    renderPage();
    expect(await screen.findByText(/no workspaces/i)).toBeInTheDocument();
  });

  it("loading → Skeleton 行；上次刷新时间显示", async () => {
    renderPage();
    // lastUpdated 显示（waitFor data 后）
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    expect(screen.getByText(/上次刷新|last updated/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `npx vitest run src/pages/WorkspaceListPage.test.tsx`
Expected: FAIL（旧组件结构与测试断言不符；DataTable 未实现）。

- [ ] **Step 3: 重写 `src/pages/WorkspaceListPage.tsx`（DataTable）**

Replace `packages/web/frontend/src/pages/WorkspaceListPage.tsx` 全文为:
```tsx
import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  createColumnHelper, flexRender, getCoreRowModel,
  getExpandedRowModel, getFilteredRowModel, getSortedRowModel,
  SortingState, useReactTable,
} from "@tanstack/react-table";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Empty } from "@/components/Empty";
import { StatusBadge } from "@/components/StatusBadge";
import { useWorkspaces } from "@/api/useWorkspaces";
import { cancelScan, deleteWorkspace } from "@/api/client";
import type { Workspace } from "@/api/types";

const helper = createColumnHelper<Workspace>();

function fmtTime(unix?: number): string {
  if (!unix) return "—";
  return new Date(unix * 1000).toLocaleString();
}

export function WorkspaceListPage() {
  const { data, loading, lastUpdated, error, refresh } = useWorkspaces();
  const [globalFilter, setGlobalFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [sorting, setSorting] = useState<SortingState>([{ id: "created_at", desc: true }]);

  // 待操作的 ws（取消/删除 Dialog）
  const [pendingAction, setPendingAction] = useState<{ ws: string; kind: "cancel" | "delete" } | null>(null);
  const [busy, setBusy] = useState(false);

  const filtered = useMemo(() => {
    let rows = data;
    if (statusFilter !== "all") rows = rows.filter((w) => w.status === statusFilter);
    if (typeFilter !== "all") rows = rows.filter((w) => w.scan_type === typeFilter);
    if (globalFilter.trim()) {
      const q = globalFilter.toLowerCase();
      rows = rows.filter((w) => w.name.toLowerCase().includes(q));
    }
    return rows;
  }, [data, statusFilter, typeFilter, globalFilter]);

  const columns = useMemo(() => [
    helper.display({
      id: "expand",
      header: () => "",
      cell: ({ row }) =>
        row.original.is_correlation ? (
          <button aria-label="展开" onClick={row.getToggleExpandedHandler()} className="text-muted-foreground">
            {row.getIsExpanded() ? "▼" : "▶"}
          </button>
        ) : null,
    }),
    helper.accessor("name", {
      header: "workspace", cell: (info) => (
        <span className="flex items-center gap-2">
          <span className={`status-bar status-${info.row.original.status}`} />
          <Link to={`/p/${info.getValue()}`} className="font-mono hover:text-primary">{info.getValue()}</Link>
          {info.row.original.is_correlation ? " 🔗" : ""}
        </span>
      ),
    }),
    helper.accessor("status", {
      header: "status", cell: (info) => (
        <StatusBadge status={info.getValue()} correlation={!!info.row.original.is_correlation} />
      ),
    }),
    helper.accessor("scan_type", { header: "type" }),
    helper.accessor("vuln_count", { header: "vulns", cell: (info) => info.getValue() ?? "—" }),
    helper.accessor("total_cost_usd", {
      header: "cost", cell: (info) => {
        const v = info.getValue();
        return v != null ? `$${v.toFixed(2)}` : "—";
      },
    }),
    helper.accessor("created_at", { header: "time", cell: (info) => fmtTime(info.getValue()) }),
    helper.display({
      id: "actions", header: "操作", cell: (info) => {
        const w = info.row.original;
        return w.status === "running" ? (
          <Button size="sm" variant="ghost" onClick={() => setPendingAction({ ws: w.name, kind: "cancel" })}>取消</Button>
        ) : (
          <Button size="sm" variant="ghost" className="text-red" onClick={() => setPendingAction({ ws: w.name, kind: "delete" })}>删除</Button>
        );
      },
    }),
  ], []);

  const table = useReactTable({
    data: filtered, columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    getRowCanExpand: (row) => !!row.original.is_correlation,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
  });

  async function doAction() {
    if (!pendingAction) return;
    setBusy(true);
    try {
      if (pendingAction.kind === "cancel") await cancelScan(pendingAction.ws);
      else await deleteWorkspace(pendingAction.ws);
      await refresh();
      setPendingAction(null);
    } finally {
      setBusy(false);
    }
  }

  const lastUpdatedStr = lastUpdated ? lastUpdated.toLocaleTimeString() : "—";

  return (
    <div className="space-y-4">
      {/* 工具栏 */}
      <div className="flex flex-wrap items-center gap-3">
        <Input
          placeholder="搜索 workspace..."
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.target.value)}
          className="max-w-xs"
        />
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger aria-label="status 筛选" className="w-32"><SelectValue placeholder="status" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">all status</SelectItem>
            <SelectItem value="running">running</SelectItem>
            <SelectItem value="completed">completed</SelectItem>
            <SelectItem value="failed">failed</SelectItem>
            <SelectItem value="killed">killed</SelectItem>
            <SelectItem value="crashed">crashed</SelectItem>
            <SelectItem value="interrupted">interrupted</SelectItem>
          </SelectContent>
        </Select>
        <Select value={typeFilter} onValueChange={setTypeFilter}>
          <SelectTrigger aria-label="type 筛选" className="w-32"><SelectValue placeholder="type" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">all type</SelectItem>
            <SelectItem value="whitebox">whitebox</SelectItem>
            <SelectItem value="blackbox">blackbox</SelectItem>
            <SelectItem value="correlation">correlation</SelectItem>
          </SelectContent>
        </Select>
        <Button variant="outline" size="sm" onClick={() => refresh()}>+ 新建扫描</Button>
        <div className="ml-auto flex items-center gap-3">
          <span className="text-xs text-muted-foreground">上次刷新 {lastUpdatedStr}</span>
          <Button variant="ghost" size="icon" aria-label="手动刷新" onClick={() => refresh()}>↻</Button>
        </div>
      </div>

      {error && <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm text-red">{error}</div>}

      {/* 表格 / 空态 / loading */}
      {loading && data.length === 0 ? (
        <div className="space-y-2">
          {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-8 w-full" />)}
        </div>
      ) : data.length === 0 ? (
        <Empty title="no workspaces" hint="新建一个扫描开始">
          <Button onClick={() => refresh()}>+ 新建扫描</Button>
        </Empty>
      ) : (
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((hg) => (
              <TableRow key={hg.id}>
                {hg.headers.map((h) => (
                  <TableHead key={h.id} onClick={h.column.getToggleSortingHandler()} className="cursor-pointer">
                    {flexRender(h.column.columnDef.header, h.getContext())}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.map((row) => (
              <Fragment key={row.id}>
                <TableRow>
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>
                  ))}
                </TableRow>
                {row.getIsExpanded() && row.original.is_correlation && (
                  <TableRow key={`${row.id}-expanded`}>
                    <TableCell colSpan={row.getVisibleCells().length} className="bg-muted/30">
                      <div className="flex flex-col gap-1 pl-6 text-sm">
                        {(row.original.links?.child_workspaces ?? []).length === 0 ? (
                          <span className="text-muted-foreground">无子白盒</span>
                        ) : (
                          (row.original.links?.child_workspaces ?? []).map((c) => (
                            <Link key={c} to={`/p/${c}`} className="font-mono hover:text-primary">└─ {c}</Link>
                          ))
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            ))}
          </TableBody>
        </Table>
      )}

      {/* 取消/删除确认 Dialog */}
      <Dialog open={!!pendingAction} onOpenChange={(o) => !o && setPendingAction(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{pendingAction?.kind === "cancel" ? "取消扫描" : "删除 workspace"}</DialogTitle>
            <DialogDescription>
              {pendingAction?.kind === "cancel"
                ? `取消扫描 ${pendingAction.ws}？进度会丢失。`
                : `删除 workspace ${pendingAction.ws}？目录和产物永久删除。`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPendingAction(null)}>取消</Button>
            <Button variant="destructive" disabled={busy} onClick={doAction}>确认</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `npx vitest run src/pages/WorkspaceListPage.test.tsx`
Expected: PASS（8 用例全绿）。若红，回查：搜索/筛选/expandable/Dialog 逻辑、`screen.getByLabelText("status 筛选")` 是否匹配 SelectTrigger aria-label。

- [ ] **Step 5: 跑 TS 编译**

Run: `npx tsc -b`
Expected: 0 错。

- [ ] **Step 6: 跑全套前端测试不破**

Run: `npx vitest run`
Expected: 全绿（含 FileSystemPicker + useWorkspaces + DSF 测试）。

- [ ] **Step 7: Commit**

```bash
git add packages/web/frontend/src/pages/WorkspaceListPage.tsx packages/web/frontend/src/pages/WorkspaceListPage.test.tsx
git commit -m "feat(web-frontend): WorkspaceListPage 用 TanStack Table 重做（搜索/筛选/排序/expandable/取消+删除/空态/loading/上次刷新）"
```

---

## Task 9: dev 预览页加 FileSystemPicker demo

**Files:**
- Modify: `packages/web/frontend/src/pages/DevComponentsPage.tsx`

**Interfaces:**
- Consumes: `FileSystemPicker`（Task 6）
- Produces: `/dev/components` 含 FileSystemPicker demo（手选目录冒烟）。

- [ ] **Step 1: 加 demo 到 DevComponentsPage**

Modify `packages/web/frontend/src/pages/DevComponentsPage.tsx`，在 `<Section title="Card">...</Section>` 之后加一个新 Section：
```tsx
      <Section title="FileSystemPicker">
        <FileSystemPickerDemo />
      </Section>
```
在文件 import 区加：
```tsx
import { useState } from "react";
import { FileSystemPicker } from "@/components/FileSystemPicker";
```
在 `DevComponentsPage` 函数之前加 helper 组件：
```tsx
function FileSystemPickerDemo() {
  const [path, setPath] = useState("/");
  return (
    <div className="flex items-center gap-2">
      <FileSystemPicker value={path} onChange={setPath} />
      <span className="font-mono text-sm text-muted-foreground">已选：{path}</span>
    </div>
  );
}
```

- [ ] **Step 2: 跑 TS 编译 + 全套测试**

Run:
```bash
npx tsc -b
npx vitest run
```
Expected: 0 错；全绿。

- [ ] **Step 3: Commit**

```bash
git add packages/web/frontend/src/pages/DevComponentsPage.tsx
git commit -m "feat(web-frontend): dev 预览页加 FileSystemPicker demo"
```

---

## Task 10: 冒烟回归

**Files:** 无（验证性 task）

- [ ] **Step 1: 后端全套测试**

Run（在 `packages/web/` 下）: `python -m pytest tests/ -v`
Expected: 全绿（fs_browse + workspaces_delete + workspaces_indexer + 现有）。

- [ ] **Step 2: 前端全套测试 + 构建**

Run（在 `packages/web/frontend/` 下）:
```bash
npx vitest run
npx tsc -b && npx vite build
```
Expected: 全绿；0 错；build 成功。

- [ ] **Step 3: 手动冒烟（dev server + 后端）**

后端启动：`uv run uvicorn shannon_web.app:app --reload --port 7878`（在 `packages/web/` 下）。
前端：`npm run dev`（在 `packages/web/frontend/` 下）。

浏览器验证：
1. 访问 `/` → 新 WorkspaceListPage：工具栏（搜索/筛选/+ 新建扫描/上次刷新/↻）+ 表格列（workspace/status/type/vulns/cost/time/操作）。`/` 路由现在由 WorkspaceListPage 占（DSF 阶段 Workspaces NavLink 指 `/`，本 task 后列表页是新样）。
2. 搜索 / status 筛选 / type 筛选 / 列排序均生效；5s 轮询刷新（上次刷新时间更新）；排序/筛选不被轮询冲掉。
3. correlation 行 ▶ 展开显子 ws；running 行"取消"Dialog；completed/failed 行"删除"Dialog → 确认后行消失。
4. 访问 `/dev/components` → FileSystemPicker demo：点"📁 浏览"打开 Dialog → 浏览整机目录 → 双击进入 / 单击选中 / "选择此目录"回填；最近书签 chip 跨会话保留；敲不存在路径 → 404 inline。
5. 深浅主题切换（TopBar 🌓）列表页 + Dialog 均正确。

Expected: 上述全通过；preflight 不破其他页（/scan/new / /p/:ws/* 内部旧样式保留）。

- [ ] **Step 4: （无新代码改动，无需 commit；若冒烟发现 bug，各自 task 回炉修 + 新 commit）**

---

## Definition of Done

- 10 task commit 落地，每 task 测试绿、commit 独立。
- 后端：`python -m pytest tests/` 全绿（含 fs_browse 11 + workspaces_delete 3 + workspaces_indexer 新 3 + 现有）。
- 前端：`npx vitest run` 全绿；`npx tsc -b && npx vite build` 0 错。
- `GET /api/workspaces` 返字段含 `total_cost_usd` / `total_duration_ms` / `vuln_count` / `links`，对齐前端 Workspace type。
- WorkspaceListPage：DataTable 全功能（搜索/筛选/排序/expandable/取消/删除/空态/loading/上次刷新/轮询保留 state）。
- `<FileSystemPicker>` 在 `/dev/components` 可手选目录（冒烟），子项目 3 ScanNewPage 可直接消费。
- DSF 测试持续绿；其他业务页（扫描页/详情 5 tab）内部未动（增量迁移）。

## 后续（非本 plan 范围）

- 子项目 3：ScanNewPage 重做（集成 FileSystemPicker 替换手敲路径 input + 表单重设计）。
- 子项目 4：详情 5 tab 重做。
- 子项目 5：Dashboard 首页 + 设置页。
- 旧 `events.css` 中 `.ledger-row` / `.ledger-child` / `.status-bar`（若仅 WorkspaceListPage 消费）随本 task 已不被新列表页消费——但 OverviewTab agent-table 仍用 `.ledger`、StatusBadge 用 `.status-bar`、`.status-badge`，故 events.css 保留；待所有子项目迁完再清。
