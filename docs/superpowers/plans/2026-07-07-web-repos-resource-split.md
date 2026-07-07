# Web 仓库资源化拆分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 clone 好的代码仓库提升为一等资源——新增 `/repos` 管理页 + `/repos/:name` 详情页，`/scan/new` 改为选已下载仓库，clone 异步化并以 SSE 推送进度，彻底消除 scan 内同步 clone 阻塞。

**Architecture:** 纯磁盘无 DB（对齐 `workspaces/`）：`repos/<name>/` + `.shannon-repo.json`（元数据/state）+ `clone.ndjson`（进度事件流）。`RepoManager` 跑异步 `git clone --progress` 子进程，解析 stderr 百分比写 ndjson；`EventTailer`（参数化 stop_type）tail `clone.ndjson` 经 SSE 推前端。`ScanRequest.source` 简化为 `PathSource | RepoSource`（移除 `GitSource`），scan 不再 clone，repo kind 解析为 `repos/<name>` 路径并校验 `state==ready`。联动不动、path 模式保留。

**Tech Stack:** Python 3.12 / FastAPI / Pydantic v2 / aiofiles / asyncio；React 18 / Vite / shadcn(Radix)+Tailwind / sonner / vitest + MSW。

## Global Constraints

- **分支**：`feat/fork-py`（本地多项未 push）。每个 task 末尾 commit。
- **测试纪律**（CLAUDE.md §3）：只跑改动相关测试文件，**勿广跑全套**（预存挂起/失败）。后端 `cd packages/web && uv run pytest tests/<file>.py -v`；前端 `cd packages/web/frontend && npx vitest run src/<file>`。
- **双轨铁律**（CLAUDE.md §1）：本计划不触 whitebox/blackbox LLM/确定性轨，只动 web 层（包内）+ `ScanRequest` 契约。
- **shadcn 四不变量**（上位 spec §4）：语义色 token（`text-destructive`/`text-primary`/`text-muted-foreground`，禁裸 `text-red`）、Plex 字体、radius ≤ 4px、`.ev-*` 事件 class 不并入 token。
- **path 模式保留**、**联动保持现状**（`multi_repo_config` 不动，yaml repos 仍写 path/git url）。
- **git clone 一律入库**（`repos/`），scan source 不含 git kind。
- **预存不一致不修**：前端 `ScanRequest` 用 `workspace_name`/`config_yaml`，后端用 `workspace`/`config_content`——本次仅加 `repo` kind，不顺带修命名。
- env 新增：`SHANNON_REPOS_MAX_CONCURRENT_CLONES`（默认 `3`）；复用 `SHANNON_REPOS_DIR`（默认 `repos`）、`GITLAB_USER`/`GITLAB_TOKEN`。

## File Structure

**后端（`packages/web/src/shannon_web/`）**
- MODIFY `models.py` — 移除 `GitSource`，加 `RepoSource`；`Source = Union[PathSource, RepoSource]`
- MODIFY `config.py` — 加 `repos_max_concurrent_clones`
- MODIFY `components/event_tailer.py` — `tail()` 加 `stop_type` 参数（默认 `"scan_end"`，向后兼容）
- CREATE `components/repo_manager.py` — `RepoManager`（clone/pull 异步 + ndjson + 元数据 + 限流 + stale/迁移 + checkout）
- MODIFY `components/scan_manager.py` — `_resolve_inputs` 删 git 分支 + 加 repo 分支（解析路径+校验 state）；加 `active_repo_sources()`；移除 `git_fetcher` 参数
- CREATE `api/repos.py` — `/api/repos` CRUD + SSE
- MODIFY `app.py` — 注册 repos router + `app.state.repo_manager` + lifespan 迁移扫描
- MODIFY `components/git_fetcher.py` — 保留 `repo_name`/`redact`/`_inject_auth`/`available`/`_TOKEN_RE` 作 RepoManager helper（`fetch` 不再被调用，保留无害）

**后端测试（`packages/web/tests/`）**
- MODIFY `test_event_tailer.py` — 加 `stop_type` 场景
- CREATE `test_repo_manager.py`
- CREATE `test_api_repos.py`
- CREATE `test_api_scan_repo_source.py`

**前端（`packages/web/frontend/src/`）**
- MODIFY `api/types.ts` — `ScanRequest.source.kind` 改 `"repo"|"path"`；加 `Repo`/`RepoMeta` 类型
- MODIFY `api/client.ts` — 加 `listRepos`/`createRepo`/`deleteRepo`/`pullRepo`/`checkoutRepo`
- MODIFY `api/useEventSource.ts` — 加 `stopType` 参数（默认 `"scan_end"`）
- CREATE `components/CloneProgress.tsx`
- CREATE `components/AddRepoDialog.tsx`
- MODIFY `components/ScanFormFields.tsx` — sourceKind `repo|path`，移除 git fieldset，加 repo Select + 内联 AddRepoDialog
- MODIFY `pages/ScanNewPage.tsx` — `FormState`/`buildBody`/`validate`/`deriveName` 改
- CREATE `pages/ReposPage.tsx`
- CREATE `pages/RepoDetailPage.tsx`
- MODIFY `router.tsx` — 加 `/repos`、`/repos/:name`
- MODIFY `components/layout/TopBar.tsx` — 加「仓库」导航
- CREATE `pages/ReposPage.test.tsx`
- MODIFY `pages/ScanNewPage.test.tsx` — 现有 8 断言调整 + 新增

**对 spec 的两处 DRY 优化（plan 内固化）**
1. 前端不新建 `useRepoEvents`——参数化现有 `api/useEventSource.ts` 加 `stopType`，各处直接 `useEventSource(url, "clone_end")`。
2. `GitFetcher` 保留为 `RepoManager` 的 helper（token 注入/redact/repo_name），不合并；`RepoManager` 新写异步 clone 主体（参考 `scan_manager._watch` 的 stderr drain）。

---

## Task 1: EventTailer 参数化 stop_type

**Files:**
- Modify: `packages/web/src/shannon_web/components/event_tailer.py:42-86`
- Test: `packages/web/tests/test_event_tailer.py`

**Interfaces:**
- Produces: `EventTailer.tail(on_event, last_event_id=None, idle_timeout=300.0, stop_type="scan_end")` —— Task 5 的 SSE 端点用 `stop_type="clone_end"` tail `clone.ndjson`。

- [ ] **Step 1: 加失败测试（clone_end 收束）**

在 `packages/web/tests/test_event_tailer.py` 末尾追加：
```python
@pytest.mark.asyncio
async def test_stops_on_custom_stop_type(tmp_path):
    f = tmp_path / "c.ndjson"
    f.write_text(
        _line({"type": "progress", "ts": "t1", "category": "INFO", "progress": 40}) + "\n"
        + _line({"type": "clone_end", "ts": "t2", "category": "CONTROL", "status": "ready"}) + "\n"
    )
    t = EventTailer(f)
    seen: list[dict] = []
    async def cb(d, eid): seen.append(d)
    await t.tail(cb, stop_type="clone_end")
    assert seen[-1]["type"] == "clone_end"
    assert seen[0]["progress"] == 40
```
> `_line` 是该文件已有的 `lambda d: json.dumps(d, ensure_ascii=False)`（确认存在；若无则用 `json.dumps(...)`）。

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web && uv run pytest tests/test_event_tailer.py::test_stops_on_custom_stop_type -v`
Expected: FAIL —— tail 因没遇 `scan_end` 而阻塞到 `idle_timeout`（或超时）。

- [ ] **Step 3: 参数化 tail()**

`event_tailer.py` 的 `tail` 签名与停止判定改为：
```python
    async def tail(
        self,
        on_event: OnEvent,
        last_event_id: int | None = None,
        idle_timeout: float = 300.0,
        stop_type: str = "scan_end",
    ) -> None:
        """Tail the file, dispatching each parsed line to ``on_event``.

        Stops once a line whose ``type`` equals ``stop_type`` is observed
        (default ``scan_end``), or once the file fails to appear within
        ``idle_timeout`` seconds.
        """
        if last_event_id is not None:
            self._offset = last_event_id
        waited = 0.0
        while not self._path.exists():
            await asyncio.sleep(0.2)
            waited += 0.2
            if waited > idle_timeout:
                return
        closed = False
        while not closed:
            async with aiofiles.open(self._path, "rb") as fh:
                await fh.seek(self._offset)
                chunk = await fh.read()
            if chunk:
                self._offset += len(chunk)
                self._carry += chunk.decode("utf-8", "replace")
                lines = self._carry.split("\n")
                self._carry = lines.pop()
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        self.corrupt_count += 1
                        continue
                    await on_event(data, self._offset)
                    if data.get("type") == stop_type:
                        closed = True
                        break
            else:
                await asyncio.sleep(0.1)
        self._carry = ""
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web && uv run pytest tests/test_event_tailer.py -v`
Expected: PASS（含新增 + 现有 scan_end 用例不回归）。

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/shannon_web/components/event_tailer.py packages/web/tests/test_event_tailer.py
git commit -m "feat(web): EventTailer.tail 参数化 stop_type（为 clone.ndjson 复用铺路）"
```

---

## Task 2: models + config（契约层）

**Files:**
- Modify: `packages/web/src/shannon_web/models.py:13-21`
- Modify: `packages/web/src/shannon_web/config.py:9-21`
- Test: `packages/web/tests/test_models_repo_source.py`（新建）

**Interfaces:**
- Produces: `RepoSource{kind:"repo", value:str}`、`Source = Union[PathSource, RepoSource]`、`WebConfig.repos_max_concurrent_clones`。Task 3/4/5 依赖。

- [ ] **Step 1: 加失败测试**

新建 `packages/web/tests/test_models_repo_source.py`：
```python
import pytest
from pydantic import TypeAdapter, ValidationError
from shannon_web.models import ScanRequest, Source


def test_repo_source_accepted():
    req = ScanRequest(type="whitebox", source={"kind": "repo", "value": "foo"})
    assert req.source is not None and req.source.kind == "repo"
    assert req.source.value == "foo"


def test_git_source_rejected():
    with pytest.raises(ValidationError):
        ScanRequest(type="whitebox", source={"kind": "git", "value": "https://x.git"})


def test_source_union_discriminates():
    ta = TypeAdapter(Source)
    assert ta.validate_python({"kind": "path", "value": "/x"}).kind == "path"
    assert ta.validate_python({"kind": "repo", "value": "foo"}).kind == "repo"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web && uv run pytest tests/test_models_repo_source.py -v`
Expected: FAIL（`RepoSource` 未定义 / git 仍被接受）。

- [ ] **Step 3: 改 models.py**

把 `models.py:13-21` 的 `GitSource` + `Source` 定义替换为：
```python
class RepoSource(BaseModel):
    kind: Literal["repo"]
    value: str  # 仓库名（repos_dir 下的目录名）


Source = Union[PathSource, RepoSource]
```
> 完全删除 `GitSource` 类。`PathSource`（:8-10）不动。

- [ ] **Step 4: 改 config.py**

在 `WebConfig.__init__`（`config.py:9-21`）的 `self.repos_dir = ...` 行下方加：
```python
        self.repos_max_concurrent_clones = max(
            1, int(os.environ.get("SHANNON_REPOS_MAX_CONCURRENT_CLONES", "3"))
        )
```

- [ ] **Step 5: 跑测试验证通过**

Run: `cd packages/web && uv run pytest tests/test_models_repo_source.py -v`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add packages/web/src/shannon_web/models.py packages/web/src/shannon_web/config.py packages/web/tests/test_models_repo_source.py
git commit -m "feat(web): ScanRequest.source 改 repo|path（移除 git）+ repos 并发限流 env"
```

> ⚠️ 此 task 后 `scan_manager._resolve_inputs` 仍引用 `req.source.kind == "git"`（死分支，git kind 已被 Pydantic 拒绝，不会执行）—— Task 3 清理。在此之前 `test_api_scan.py` 等现有测试若发 git kind body 会变 422，先不跑那些（Task 3 修 scan_manager 后再回归）。

---

## Task 3: ScanManager 改造（repo 解析 + 移除 git clone）

**Files:**
- Modify: `packages/web/src/shannon_web/components/scan_manager.py:31-140`
- Modify: `packages/web/src/shannon_web/app.py:67-71`（ScanManager 构造去 git_fetcher）
- Test: `packages/web/tests/test_api_scan_repo_source.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `RepoSource`。
- Produces: `ScanManager._resolve_inputs` repo→`repos/<name>` 路径 + `state==ready` 校验；`ScanManager.active_repo_sources() -> set[str]`（Task 5 DELETE 判引用）。
- scan 不再依赖 `git_fetcher`（构造参数移除）。

- [ ] **Step 1: 加失败测试**

新建 `packages/web/tests/test_api_scan_repo_source.py`（直接调 `_resolve_inputs` 验 repo 解析与 state 校验，避开 scan 子进程；另加一个 API 层用例验 git kind→422）：
```python
import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from shannon_web.app import create_app
from shannon_web.models import ScanRequest


def _app_with_repos(tmp_path, monkeypatch, repos_state):
    monkeypatch.setenv("SHANNON_REPOS_DIR", str(tmp_path / "repos"))
    (tmp_path / "repos").mkdir(parents=True, exist_ok=True)
    for name, state in repos_state.items():
        d = tmp_path / "repos" / name; d.mkdir()
        (d / ".shannon-repo.json").write_text(json.dumps({"name": name, "state": state}))
    monkeypatch.setenv("SHANNON_WORKER_ROOT", str(tmp_path))
    from shannon_web.config import get_config; get_config.cache_clear()
    return create_app()


def test_resolve_repo_ready(tmp_path, monkeypatch):
    app = _app_with_repos(tmp_path, monkeypatch, {"foo": "ready"})
    target, _ = asyncio.get_event_loop().run_until_complete(
        app.state.scan_manager._resolve_inputs(
            ScanRequest(type="whitebox", source={"kind": "repo", "value": "foo"})))
    assert target.endswith("repos/foo")


def test_resolve_repo_cloning_raises(tmp_path, monkeypatch):
    app = _app_with_repos(tmp_path, monkeypatch, {"foo": "cloning"})
    with pytest.raises(ValueError, match="未就绪"):
        asyncio.get_event_loop().run_until_complete(
            app.state.scan_manager._resolve_inputs(
                ScanRequest(type="whitebox", source={"kind": "repo", "value": "foo"})))


def test_resolve_repo_missing_raises(tmp_path, monkeypatch):
    app = _app_with_repos(tmp_path, monkeypatch, {"foo": "ready"})
    with pytest.raises(ValueError):
        asyncio.get_event_loop().run_until_complete(
            app.state.scan_manager._resolve_inputs(
                ScanRequest(type="whitebox", source={"kind": "repo", "value": "nope"})))


def test_scan_git_kind_422(tmp_path, monkeypatch):
    app = _app_with_repos(tmp_path, monkeypatch, {})
    r = TestClient(app).post("/api/scan", json={
        "type": "whitebox", "source": {"kind": "git", "value": "https://x.git"}, "url": "http://x"})
    assert r.status_code == 422
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web && uv run pytest tests/test_api_scan_repo_source.py -v`
Expected: FAIL（`_resolve_inputs` 仍走 git 分支 / repo 未识别）。

- [ ] **Step 3: 改 scan_manager.py**

3a. `__init__`（:31-42）移除 `git_fetcher` 参数（保留 `repos_dir`）：
```python
    def __init__(self, workspaces_dir: Path, repos_dir: Path, config_store: Any,
                 max_concurrent: int = 1, scan_timeout: float = 0.0) -> None:
        self._workspaces_dir = Path(workspaces_dir)
        self._repos_dir = Path(repos_dir)
        self._config_store = config_store
        self._max_concurrent = max(1, max_concurrent)
        self._scan_timeout = scan_timeout
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        # 进行中的 scan 请求快照（ws -> ScanRequest），供 active_repo_sources() 判引用
        self._active_reqs: dict[str, ScanRequest] = {}
```

3b. `start()`（:53-82）在 `self._procs[ws] = proc` 前后登记/清理 `_active_reqs`：在 `argv = self._build_argv(...)` 之后、`proc = await ...` 之前加 `self._active_reqs[ws] = req`；在 `_watch` 末尾（:211 `self._tasks.pop(ws, None)` 后）加 `self._active_reqs.pop(ws, None)`。

3c. 加公共方法（放在 `active_pids` 旁，:45-46 后）：
```python
    def active_repo_sources(self) -> set[str]:
        """当前正在跑的 scan 引用的 repo 名集合（DELETE /repos 判引用用）。"""
        out = set()
        for req in self._active_reqs.values():
            if req.source is not None and req.source.kind == "repo":
                out.add(req.source.value)
        return out
```

3d. `_resolve_inputs`（:126-140）重写——删 git 分支，加 repo 分支：
```python
    async def _resolve_inputs(self, req: ScanRequest) -> tuple[str | None, Path | None]:
        target: str | None = None
        yaml_path: Path | None = None
        if req.source is not None:
            if req.source.kind == "repo":
                target = self._resolve_repo_path(req.source.value)
            else:  # path
                target = req.source.value
        if req.type == "correlation":
            yaml_path = await self._resolve_correlation_yaml(req)
        return target, yaml_path

    def _resolve_repo_path(self, name: str) -> str:
        import json as _json
        repo_dir = self._repos_dir / name
        if not repo_dir.is_dir():
            raise ValueError(f"仓库不存在：{name}")
        meta_file = repo_dir / ".shannon-repo.json"
        state = "ready"
        if meta_file.exists():
            try:
                state = _json.loads(meta_file.read_text("utf-8", errors="replace")).get("state", "ready")
            except _json.JSONDecodeError:
                state = "ready"  # 元数据损坏不阻塞扫描
        if state != "ready":
            raise ValueError(f"仓库未就绪（state={state}），请先在 /repos 完成 clone")
        return str(repo_dir)
```

- [ ] **Step 4: 改 app.py ScanManager 构造**

`app.py:67-71` 改为（移除 git_fetcher 传参，git_fetcher 改传 RepoManager——Task 5 接）：
```python
    git_fetcher = GitFetcher(cfg.repos_dir, cfg.gitlab_user, cfg.gitlab_token)
    overrides = overrides or {}
    app.state.scan_manager = overrides.get("scan_manager") or ScanManager(
        cfg.workspaces_dir, cfg.repos_dir, app.state.config_store,
        max_concurrent=cfg.max_concurrent, scan_timeout=cfg.scan_timeout)
```
> 此 task 暂不动 RepoManager 注入（Task 5）。git_fetcher 变量保留（Task 5 用），若 linter 报 unused 暂忽略（Task 5 接走）。

- [ ] **Step 5: 跑测试验证通过**

Run: `cd packages/web && uv run pytest tests/test_api_scan_repo_source.py tests/test_scan_manager.py -v`
Expected: PASS。`test_scan_manager.py` 的构造 `ScanManager(ws, repos, None, max_concurrent=2)` 用关键字传 `max_concurrent`，移除 `git_fetcher` 位置参数后仍兼容（`None` 是第 3 位 config_store，`max_concurrent=` 关键字不受位置参数移除影响）。

- [ ] **Step 6: Commit**

```bash
git add packages/web/src/shannon_web/components/scan_manager.py packages/web/src/shannon_web/app.py packages/web/tests/test_api_scan_repo_source.py
git commit -m "feat(web): ScanManager repo source 解析+state 校验，移除 git clone 路径"
```

---

## Task 4: RepoManager（异步 clone + 进度 ndjson + 元数据）

**Files:**
- Create: `packages/web/src/shannon_web/components/repo_manager.py`
- Test: `packages/web/tests/test_repo_manager.py`

**Interfaces:**
- Consumes: `GitFetcher.repo_name`/`redact`/`_inject_auth`/`available`（`git_fetcher.py`）；`WebConfig.repos_dir`/`repos_max_concurrent_clones`（Task 2）。
- Produces: `RepoManager` 实例方法 `list_repos()`/`get_repo(name)`/`clone(url,branch,commit,name)`/`pull(name)`/`checkout(name,branch)`/`delete(name)`/`is_busy(name)`/`migrate_legacy()`。Task 5 的 API 层调用这些。

- [ ] **Step 1: 加失败测试**

新建 `packages/web/tests/test_repo_manager.py`：
```python
import asyncio
import json
import sys
import textwrap
import pytest
from shannon_web.components.repo_manager import RepoManager, TooManyClones


def _rm(tmp_path, monkeypatch) -> RepoManager:
    monkeypatch.setenv("SHANNON_REPOS_DIR", str(tmp_path / "repos"))
    from shannon_web.config import get_config; get_config.cache_clear()
    from shannon_web.components.git_fetcher import GitFetcher
    cfg = get_config()
    return RepoManager(cfg.repos_dir, GitFetcher(cfg.repos_dir, "u", "t"), max_concurrent=3)


@pytest.fixture
def fake_clone_ok(tmp_path):
    """假 git 子进程：写一行 progress 到 stderr 后退出 0。"""
    s = tmp_path / "ok.py"
    s.write_text(textwrap.dedent('''
        import sys
        sys.stderr.write("Receiving objects: 40%\\n")
        sys.exit(0)
    '''))
    return s


@pytest.mark.asyncio
async def test_clone_writes_ndjson_and_meta(tmp_path, monkeypatch, fake_clone_ok):
    rm = _rm(tmp_path, monkeypatch)
    monkeypatch.setattr(rm, "_build_clone_argv",
                        lambda url, target, branch: [sys.executable, str(fake_clone_ok)])
    name = await rm.clone("https://gitlab.example/foo.git", None, None, None)
    assert name == "foo"
    # 等 task 结束
    await asyncio.sleep(0.3)
    meta = json.loads((tmp_path / "repos" / "foo" / ".shannon-repo.json").read_text())
    assert meta["state"] == "ready"
    lines = (tmp_path / "repos" / "foo" / "clone.ndjson").read_text().splitlines()
    assert any("40" in l and '"progress"' in l for l in lines)
    assert any('"clone_end"' in l and '"ready"' in l for l in lines)


@pytest.mark.asyncio
async def test_clone_failed_writes_failed_state(tmp_path, monkeypatch):
    crash = tmp_path / "crash.py"
    crash.write_text('import sys; sys.stderr.write("boom https://u:t@host\\n"); sys.exit(1)')
    rm = _rm(tmp_path, monkeypatch)
    monkeypatch.setattr(rm, "_build_clone_argv",
                        lambda url, target, branch: [sys.executable, str(crash)])
    await rm.clone("https://gitlab.example/foo.git", None, None, None)
    await asyncio.sleep(0.3)
    meta = json.loads((tmp_path / "repos" / "foo" / ".shannon-repo.json").read_text())
    assert meta["state"] == "failed"
    assert "t@" not in meta["last_error"]  # token 脱敏


@pytest.mark.asyncio
async def test_name_conflict_raises(tmp_path, monkeypatch):
    rm = _rm(tmp_path, monkeypatch)
    (tmp_path / "repos").mkdir()
    (tmp_path / "repos" / "foo").mkdir()
    with pytest.raises(ValueError, match="已存在"):
        await rm.clone("https://gitlab.example/foo.git", None, None, None)


@pytest.mark.asyncio
async def test_concurrent_limit(tmp_path, monkeypatch):
    rm = _rm(tmp_path, monkeypatch)
    rm._sem = asyncio.Semaphore(1)  # 限到 1
    rm._jobs["busy"] = asyncio.create_task(asyncio.sleep(10))
    with pytest.raises(TooManyClones):
        await rm.clone("https://gitlab.example/bar.git", None, None, None)
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web && uv run pytest tests/test_repo_manager.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现 RepoManager**

新建 `packages/web/src/shannon_web/components/repo_manager.py`：
```python
from __future__ import annotations

import asyncio
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles

from .git_fetcher import GitFetcher

_PROGRESS_RE = re.compile(r"(?:Receiving objects|Resolving deltas|Compressing objects|Counting objects):\s+(\d+)%")


class TooManyClones(Exception):
    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"并发 clone 上限 {limit}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


class RepoManager:
    def __init__(self, repos_dir: Path, git_fetcher: GitFetcher,
                 max_concurrent: int = 3) -> None:
        self._dir = Path(repos_dir)
        self._git = git_fetcher
        self._max_concurrent = max(1, max_concurrent)
        self._sem = asyncio.Semaphore(self._max_concurrent)
        self._jobs: dict[str, asyncio.Task] = {}

    # ---- 查询 ----
    def is_busy(self, name: str) -> bool:
        return name in self._jobs

    def list_repos(self) -> list[dict]:
        self._dir.mkdir(parents=True, exist_ok=True)
        out: list[dict] = []
        for sub in sorted(self._dir.iterdir()):
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            out.append(self._repo_view(sub.name))
        return out

    def get_repo(self, name: str) -> dict | None:
        d = self._dir / name
        if not d.is_dir():
            return None
        view = self._repo_view(name)
        view["recent_events"] = self._recent_events(name, 20)
        return view

    def _repo_view(self, name: str) -> dict:
        d = self._dir / name
        meta = self._read_meta(name)
        state = meta.get("state", "ready")
        # stale：磁盘 cloning/pulling 但内存无 job → 重启后未完成
        if state in ("cloning", "pulling") and not self.is_busy(name):
            state = "stale"
        view = {"name": name, **meta, "state": state}
        if self.is_busy(name):
            view["progress"] = self._last_progress(name)
        return view

    def _read_meta(self, name: str) -> dict:
        f = self._dir / name / ".shannon-repo.json"
        if not f.exists():
            return {"name": name, "source": {"kind": "unknown"}, "state": "ready"}
        try:
            return json.loads(f.read_text("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return {"name": name, "source": {"kind": "unknown"}, "state": "ready"}

    def _write_meta(self, name: str, **patch) -> None:
        f = self._dir / name / ".shannon-repo.json"
        meta = self._read_meta(name) if f.exists() else {"name": name}
        meta.update(patch)
        f.write_text(json.dumps(meta, ensure_ascii=False))

    def _last_progress(self, name: str) -> int | None:
        f = self._dir / name / "clone.ndjson"
        if not f.exists():
            return None
        last_pct = None
        for line in f.read_text("utf-8", errors="replace").splitlines()[-20:]:
            try:
                d = json.loads(line)
                if "progress" in d:
                    last_pct = d["progress"]
            except json.JSONDecodeError:
                continue
        return last_pct

    def _recent_events(self, name: str, n: int) -> list[dict]:
        f = self._dir / name / "clone.ndjson"
        if not f.exists():
            return []
        out: list[dict] = []
        for line in f.read_text("utf-8", errors="replace").splitlines()[-n:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    # ---- clone / pull ----
    async def clone(self, url: str, branch: str | None, commit: str | None,
                    name: str | None) -> str:
        if not self._git.available():
            raise PermissionError("未配置 git 凭证（GITLAB_USER/TOKEN）")
        name = name or self._git.repo_name(url)
        target = self._dir / name
        if target.exists():
            raise ValueError(f"仓库已存在：{name}（可改用更新 pull）")
        if len(self._jobs) >= self._max_concurrent:
            raise TooManyClones(self._max_concurrent)
        target.mkdir(parents=True, exist_ok=False)
        self._write_meta(name, source={"kind": "git", "url": url, "branch": branch, "commit": commit},
                         cloned_at=_now_iso(), state="cloning", last_error=None)
        task = asyncio.create_task(self._clone_task(name, url, branch, commit, target))
        self._jobs[name] = task
        return name

    async def _clone_task(self, name: str, url: str, branch: str | None,
                          commit: str | None, target: Path) -> None:
        async with self._sem:
            await self._run_git_with_progress(
                name, phase="cloning",
                argv=self._build_clone_argv(self._git._inject_auth(url), target, branch))
            # commit checkout（可选）
            if commit:
                await self._run_git_with_progress(
                    name, phase="cloning",
                    argv=["git", "-C", str(target), "fetch", "--all"])
                proc = await asyncio.create_subprocess_exec(
                    "git", "-C", str(target), "checkout", commit,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await proc.wait()
            head = await self._head_commit(target)
            self._write_meta(name, state="ready", last_pull_at=_now_iso(),
                             size_bytes=_dir_size(target),
                             source={**self._read_meta(name).get("source", {}), "commit": head})
            await self._append_event(name, {"ts": _now_iso(), "type": "clone_end", "status": "ready"})
        self._jobs.pop(name, None)

    async def pull(self, name: str) -> None:
        target = self._dir / name
        if not target.is_dir():
            raise ValueError(f"仓库不存在：{name}")
        if name in self._jobs:
            raise ValueError(f"仓库正忙：{name}")
        self._write_meta(name, state="pulling")
        task = asyncio.create_task(self._pull_task(name, target))
        self._jobs[name] = task

    async def _pull_task(self, name: str, target: Path) -> None:
        async with self._sem:
            ok = await self._run_git_with_progress(
                name, phase="pulling", argv=["git", "-C", str(target), "pull", "--ff-only"])
            if ok:
                head = await self._head_commit(target)
                self._write_meta(name, state="ready", last_pull_at=_now_iso(),
                                 size_bytes=_dir_size(target),
                                 source={**self._read_meta(name).get("source", {}), "commit": head})
                await self._append_event(name, {"ts": _now_iso(), "type": "clone_end", "status": "ready"})
            else:
                self._mark_failed(name, "pull 失败")
        self._jobs.pop(name, None)

    # ---- checkout / delete ----
    async def checkout(self, name: str, branch: str) -> None:
        target = self._dir / name
        if not target.is_dir():
            raise ValueError(f"仓库不存在：{name}")
        if name in self._jobs:
            raise ValueError(f"仓库正忙：{name}")
        # checkout 同步（通常快，不写 ndjson）
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(target), "fetch", "origin", branch,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise ValueError(f"分支不存在：{branch}")
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(target), "checkout", branch,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.wait()
        head = await self._head_commit(target)
        src = self._read_meta(name).get("source", {})
        src["branch"] = branch; src["commit"] = head
        self._write_meta(name, source=src)

    async def delete(self, name: str) -> None:
        if name in self._jobs:
            raise ValueError(f"仓库正忙：{name}")
        target = self._dir / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=False)

    # ---- git 子进程 + stderr 进度解析 ----
    def _build_clone_argv(self, authed_url: str, target: Path, branch: str | None) -> list[str]:
        cmd = ["git", "clone", "--progress"]
        if branch:
            cmd += ["--branch", branch]
        cmd += [authed_url, str(target)]
        return cmd

    async def _run_git_with_progress(self, name: str, phase: str, argv: list[str]) -> bool:
        """跑 git，异步读 stderr 解析进度写 ndjson。返回 returncode==0。"""
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        assert proc.stderr is not None

        async def drain_stderr():
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace")
                m = _PROGRESS_RE.search(text)
                if m:
                    await self._append_event(name, {
                        "ts": _now_iso(), "phase": phase, "progress": int(m.group(1)),
                        "status": "progress"})
                else:
                    await self._append_event(name, {
                        "ts": _now_iso(), "phase": phase,
                        "message": self._git.redact(text.rstrip()), "status": "progress"})

        async def drain_stdout():
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break

        await asyncio.gather(drain_stderr(), drain_stdout())
        rc = await proc.wait()
        if rc != 0:
            self._mark_failed(name, f"{phase} 失败（rc={rc}）")
        return rc == 0

    def _mark_failed(self, name: str, msg: str) -> None:
        self._write_meta(name, state="failed", last_error=msg, last_pull_at=_now_iso())
        # clone_end 失败事件（同步写，task 内调用）
        f = self._dir / name / "clone.ndjson"
        with open(f, "a") as fh:
            fh.write(json.dumps({"ts": _now_iso(), "type": "clone_end",
                                 "status": "failed", "error": msg}, ensure_ascii=False) + "\n")

    async def _append_event(self, name: str, payload: dict) -> None:
        f = self._dir / name / "clone.ndjson"
        async with aiofiles.open(f, "a") as fh:
            await fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    async def _head_commit(self, target: Path) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(target), "rev-parse", "HEAD",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _ = await proc.communicate()
            return out.decode("utf-8", "replace").strip() or None
        except Exception:
            return None

    # ---- 旧目录迁移 ----
    def migrate_legacy(self) -> int:
        """把无 .shannon-repo.json 的旧 repos/<name> 纳入管理。返回迁移数。"""
        if not self._dir.is_dir():
            return 0
        n = 0
        for sub in self._dir.iterdir():
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            meta = sub / ".shannon-repo.json"
            if meta.exists():
                continue
            url, branch = self._infer_from_git(sub)
            import os
            self._write_meta(sub.name,
                source={"kind": "git" if url else "unknown", "url": url, "branch": branch},
                cloned_at=datetime.fromtimestamp(sub.stat().st_mtime, timezone.utc).isoformat(),
                state="ready", last_error=None)
            n += 1
        return n

    @staticmethod
    def _infer_from_git(repo: Path) -> tuple[str | None, str | None]:
        url, branch = None, None
        cfg = repo / ".git" / "config"
        if cfg.exists():
            for line in cfg.read_text("utf-8", errors="replace").splitlines():
                s = line.strip()
                if s.startswith("url = "):
                    url = s[6:]
        head = repo / ".git" / "HEAD"
        if head.exists():
            t = head.read_text("utf-8", errors="replace").strip()
            if t.startswith("ref: refs/heads/"):
                branch = t[len("ref: refs/heads/"):]
        return url, branch
```

> `clone()` 里 `TooManyClones` 用 `len(self._jobs) >= max` 判（简单；并发精确性由 Semaphore 在 task 内再保底，但外层先挡避免堆积 task 对象）。

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web && uv run pytest tests/test_repo_manager.py -v`
Expected: PASS（4 用例）。

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/shannon_web/components/repo_manager.py packages/web/tests/test_repo_manager.py
git commit -m "feat(web): RepoManager 异步 clone+进度 ndjson+元数据+迁移"
```

---

## Task 5: /api/repos CRUD + SSE + app 接入

**Files:**
- Create: `packages/web/src/shannon_web/api/repos.py`
- Modify: `packages/web/src/shannon_web/app.py:54-92`
- Test: `packages/web/tests/test_api_repos.py`

**Interfaces:**
- Consumes: `RepoManager`（Task 4）、`EventTailer.tail(stop_type="clone_end")`（Task 1）、`ScanManager.active_repo_sources()`（Task 3）。
- Produces: REST 端点 `GET/POST/DELETE /api/repos`、`GET /api/repos/{name}`、`GET /api/repos/{name}/events`(SSE)、`POST /api/repos/{name}/pull`、`POST /api/repos/{name}/checkout`。

- [ ] **Step 1: 加失败测试**

新建 `packages/web/tests/test_api_repos.py`：
```python
import json
import pytest
from fastapi.testclient import TestClient
from shannon_web.app import create_app


def _app(tmp_path, monkeypatch, repos):
    monkeypatch.setenv("SHANNON_REPOS_DIR", str(tmp_path / "repos"))
    (tmp_path / "repos").mkdir(parents=True, exist_ok=True)
    for name, state in repos.items():
        d = tmp_path / "repos" / name; d.mkdir()
        (d / ".shannon-repo.json").write_text(json.dumps({"name": name, "state": state}))
    monkeypatch.setenv("SHANNON_WORKER_ROOT", str(tmp_path))
    from shannon_web.config import get_config; get_config.cache_clear()
    return create_app()


def test_list_repos(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {"foo": "ready", "bar": "failed"})
    r = TestClient(app).get("/api/repos")
    assert r.status_code == 200
    names = [x["name"] for x in r.json()]
    assert names == ["bar", "foo"]
    states = {x["name"]: x["state"] for x in r.json()}
    assert states["foo"] == "ready" and states["bar"] == "failed"


def test_get_repo_detail(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {"foo": "ready"})
    r = TestClient(app).get("/api/repos/foo")
    assert r.status_code == 200 and r.json()["name"] == "foo"
    assert TestClient(app).get("/api/repos/missing").status_code == 404


def test_post_repo_503_no_creds(tmp_path, monkeypatch):
    monkeypatch.delenv("GITLAB_USER", raising=False)
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    app = _app(tmp_path, monkeypatch, {})
    r = TestClient(app).post("/api/repos", json={"git_url": "https://x/foo.git"})
    assert r.status_code == 503


def test_post_repo_409_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv("GITLAB_USER", "u"); monkeypatch.setenv("GITLAB_TOKEN", "t")
    app = _app(tmp_path, monkeypatch, {"foo": "ready"})
    r = TestClient(app).post("/api/repos", json={"git_url": "https://x/foo.git"})
    assert r.status_code == 409


def test_delete_busy_409(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {"foo": "cloning"})
    # 模拟正在 clone：注入内存 job
    import asyncio
    app.state.repo_manager._jobs["foo"] = asyncio.create_task(asyncio.sleep(10))
    r = TestClient(app).delete("/api/repos/foo")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_sse_events(tmp_path, monkeypatch):
    import httpx
    app = _app(tmp_path, monkeypatch, {"foo": "ready"})
    (tmp_path / "repos" / "foo" / "clone.ndjson").write_text(
        json.dumps({"ts": "t1", "phase": "cloning", "progress": 40, "status": "progress"}) + "\n"
        + json.dumps({"ts": "t2", "type": "clone_end", "status": "ready"}) + "\n")
    transport = httpx.ASGITransport(app=app)
    lines = []
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=5) as c:
        async with c.stream("GET", "/api/repos/foo/events") as r:
            assert r.status_code == 200
            async for line in r.aiter_lines():
                lines.append(line)
                if line.startswith("data:") and "clone_end" in line:
                    break
    assert any("40" in l for l in lines if l.startswith("data:"))
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web && uv run pytest tests/test_api_repos.py -v`
Expected: FAIL（路由不存在 / repo_manager 未挂 state）。

- [ ] **Step 3: 实现 api/repos.py**

新建 `packages/web/src/shannon_web/api/repos.py`：
```python
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from shannon_web.components.event_tailer import EventTailer
from shannon_web.components.repo_manager import TooManyClones

router = APIRouter(prefix="/api/repos", tags=["repos"])


class CreateRepoBody(BaseModel):
    git_url: str
    branch: str | None = None
    commit: str | None = None
    name: str | None = None


class CheckoutBody(BaseModel):
    branch: str


@router.get("")
async def list_repos(request: Request):
    return request.app.state.repo_manager.list_repos()


@router.post("", status_code=202)
async def create_repo(body: CreateRepoBody, request: Request):
    rm = request.app.state.repo_manager
    try:
        name = await rm.clone(body.git_url, body.branch, body.commit, body.name)
    except PermissionError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:        # 已存在
        raise HTTPException(409, str(e))
    except TooManyClones as e:
        raise HTTPException(409, f"并发 clone 上限 {e.limit}")
    return {"name": name}


@router.get("/{name}")
async def get_repo(name: str, request: Request):
    repo = request.app.state.repo_manager.get_repo(name)
    if repo is None:
        raise HTTPException(404, "repo not found")
    return repo


@router.get("/{name}/events")
async def repo_events(name: str, request: Request):
    rm = request.app.state.repo_manager
    if rm.get_repo(name) is None:
        raise HTTPException(404, "repo not found")
    ndjson = rm._dir / name / "clone.ndjson"
    last = request.headers.get("Last-Event-ID")
    last_offset = int(last) if last else None

    async def gen():
        tailer = EventTailer(ndjson)
        # EventTailer.tail 用 callback 而非 async generator；用 queue 桥接成 SSE
        import asyncio
        queue: asyncio.Queue = asyncio.Queue()
        SENTINEL = object()

        async def cb(data, offset):
            await queue.put(EventTailer.encode_sse(data, event_id=offset))

        async def run_tail():
            await tailer.tail(cb, last_event_id=last_offset, stop_type="clone_end")
            await queue.put(SENTINEL)

        task = asyncio.create_task(run_tail())
        try:
            while True:
                item = await queue.get()
                if item is SENTINEL:
                    break
                yield item
        finally:
            task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.delete("/{name}")
async def delete_repo(name: str, request: Request):
    rm = request.app.state.repo_manager
    sm = request.app.state.scan_manager
    if name in sm.active_repo_sources():
        raise HTTPException(409, "仓库正被扫描引用")
    try:
        await rm.delete(name)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"deleted": name}


@router.post("/{name}/pull", status_code=202)
async def pull_repo(name: str, request: Request):
    rm = request.app.state.repo_manager
    try:
        await rm.pull(name)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"pulling": name}


@router.post("/{name}/checkout")
async def checkout_repo(name: str, body: CheckoutBody, request: Request):
    rm = request.app.state.repo_manager
    try:
        await rm.checkout(name, body.branch)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"checked_out": body.branch}
```

- [ ] **Step 4: 改 app.py 注册**

`app.py` 顶部 `from .api import ...`（:63）加 `repos`：
```python
    from .api import events, fs, multi_configs, repos, scan, system_status, workspaces
```
构造段（:65-71）加 RepoManager 注入：
```python
    from .components.repo_manager import RepoManager
    ...
    git_fetcher = GitFetcher(cfg.repos_dir, cfg.gitlab_user, cfg.gitlab_token)
    overrides = overrides or {}
    app.state.scan_manager = overrides.get("scan_manager") or ScanManager(
        cfg.workspaces_dir, cfg.repos_dir, app.state.config_store,
        max_concurrent=cfg.max_concurrent, scan_timeout=cfg.scan_timeout)
    app.state.repo_manager = overrides.get("repo_manager") or RepoManager(
        cfg.repos_dir, git_fetcher, max_concurrent=cfg.repos_max_concurrent_clones)
```
路由注册段（:73-78）加：
```python
    app.include_router(repos.router)
```
lifespan（:13-17）加迁移扫描：
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.repo_manager.migrate_legacy()  # 旧 repos 目录纳入管理
    yield
```

- [ ] **Step 5: 跑测试验证通过**

Run: `cd packages/web && uv run pytest tests/test_api_repos.py tests/test_api_scan_repo_source.py -v`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add packages/web/src/shannon_web/api/repos.py packages/web/src/shannon_web/app.py packages/web/tests/test_api_repos.py
git commit -m "feat(web): /api/repos CRUD + SSE 进度 + lifespan 迁移"
```

---

## Task 6: 前端 types + client + useEventSource 参数化

**Files:**
- Modify: `packages/web/frontend/src/api/types.ts:175-183`
- Modify: `packages/web/frontend/src/api/client.ts:22-32`
- Modify: `packages/web/frontend/src/api/useEventSource.ts`

**Interfaces:**
- Produces: `Repo`/`RepoMeta` 类型；`listRepos/createRepo/deleteRepo/pullRepo/checkoutRepo`；`useEventSource(url, stopType?)`。

- [ ] **Step 1: 改 types.ts**

`types.ts:175-183` 的 `ScanRequest` + 末尾追加 Repo 类型：
```typescript
export interface ScanRequest {
  type: "whitebox" | "blackbox" | "correlation";
  source?: { kind: "repo" | "path"; value: string };
  url?: string;
  workspace_name?: string;
  reuse_latest_whitebox?: boolean;
  config_yaml?: string;
  config_name?: string;
}

export type RepoState = "ready" | "cloning" | "pulling" | "failed" | "stale";

export interface Repo {
  name: string;
  source?: { kind: string; url?: string; branch?: string; commit?: string };
  state: RepoState;
  size_bytes?: number;
  cloned_at?: string;
  last_pull_at?: string;
  last_error?: string | null;
  progress?: number | null;
}

export interface RepoDetail extends Repo {
  recent_events?: Array<Record<string, unknown>>;
}
```

- [ ] **Step 2: 改 client.ts**

在 `client.ts`（`cancelScan` 定义后，:32 前）加：
```typescript
export const listRepos = () => apiGet<Repo[]>("/repos");
export const getRepo = (name: string) => apiGet<RepoDetail>(`/repos/${encodeURIComponent(name)}`);
export const createRepo = (body: { git_url: string; branch?: string; commit?: string; name?: string }) =>
  apiPost<{ name: string }>("/repos", body);
export const deleteRepo = (name: string) =>
  apiDelete<{ deleted: string }>(`/repos/${encodeURIComponent(name)}`);
export const pullRepo = (name: string) =>
  apiPost<{ pulling: string }>(`/repos/${encodeURIComponent(name)}/pull`, {});
export const checkoutRepo = (name: string, branch: string) =>
  apiPost<{ checked_out: string }>(`/repos/${encodeURIComponent(name)}/checkout`, { branch });
```
并在顶部 `import type { FsBrowseResult } from "./types";` 改为：
```typescript
import type { FsBrowseResult, Repo, RepoDetail } from "./types";
```

- [ ] **Step 3: 参数化 useEventSource.ts**

读 `useEventSource.ts` 现状（接口 `{events, status, lastEventId}`，硬编码 `scan_end` 关闭）。加 `stopType` 参数（默认 `"scan_end"`），把内部 `data.type === "scan_end"` 判定改为 `data.type === stopType`。目标签名：
```typescript
export function useEventSource(url: string, stopType: string = "scan_end") {
  // ... 内部 onmessage: if (data.type === stopType) { es.close(); setStatus("closed"); }
  return { events, status, lastEventId };
}
```
> 执行者：Read 该文件，把 `"scan_end"` 字面量替换为 `stopType` 参数，函数签名加 `stopType = "scan_end"`。现有 LiveTab 调用 `useEventSource(url)` 不传第二参 → 默认 scan_end，行为不变。

- [ ] **Step 4: 验证前端类型 + 现有测试不回归**

Run: `cd packages/web/frontend && npx tsc --noEmit && npx vitest run src/api/useEventSource.test.ts`
Expected: 类型 0 error；useEventSource 测试 PASS（默认 scan_end 行为不变）。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/api/types.ts packages/web/frontend/src/api/client.ts packages/web/frontend/src/api/useEventSource.ts
git commit -m "feat(web): 前端 Repo 类型+API+useEventSource 参数化 stopType"
```

---

## Task 7: CloneProgress + AddRepoDialog 组件

**Files:**
- Create: `packages/web/frontend/src/components/CloneProgress.tsx`
- Create: `packages/web/frontend/src/components/AddRepoDialog.tsx`

**Interfaces:**
- Consumes: `useEventSource`（Task 6）、`createRepo`（Task 6）、shadcn Dialog/Input/Button/Label。
- Produces: `<CloneProgress name={...} />`（订阅 SSE 显进度）、`<AddRepoDialog open onCreated onOpenChange />`。

- [ ] **Step 1: 实现 CloneProgress.tsx**

```tsx
import { useEventSource } from "@/api/useEventSource";

export function CloneProgress({ name }: { name: string }) {
  const { events, status } = useEventSource(`/api/repos/${name}/events`, "clone_end");
  const last = events[events.length - 1];
  const progress = (last as { progress?: number } | undefined)?.progress ?? null;
  const failed = (last as { type?: string; status?: string } | undefined)?.type === "clone_end"
    && (last as { status?: string }).status === "failed";

  if (failed) {
    return <div className="text-xs text-destructive">clone 失败：{(last as { error?: string }).error ?? "未知错误"}</div>;
  }
  if (status === "closed") {
    return <div className="text-xs text-green">✓ 就绪</div>;
  }
  return (
    <div className="text-xs text-muted-foreground">
      clone 中{progress !== null ? `… ${progress}%` : "…"}
    </div>
  );
}
```

- [ ] **Step 2: 实现 AddRepoDialog.tsx**

```tsx
import { useState } from "react";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { createRepo, ApiError } from "@/api/client";

interface Props {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onCreated: (name: string) => void;
}

export function AddRepoDialog({ open, onOpenChange, onCreated }: Props) {
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("");
  const [commit, setCommit] = useState("");
  const [busy, setBusy] = useState(false);

  const urlOk = /^(https?:|git@|ssh:)/.test(url.trim());

  async function submit() {
    try {
      setBusy(true);
      const r = await createRepo({
        git_url: url.trim(),
        branch: branch.trim() || undefined,
        commit: commit.trim() || undefined,
      });
      onCreated(r.name);
      onOpenChange(false);
      setUrl(""); setBranch(""); setCommit("");
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 503) toast.error("未配置 git 凭证（GITLAB_USER/TOKEN）");
        else if (e.status === 409) toast.error("仓库已存在，可改用更新");
        else toast.error(`添加失败（${e.status}）`);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent onInteractOutside={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle>添加仓库</DialogTitle>
          <DialogDescription>clone git 仓库到本地，之后可反复扫描。</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="repo-url">git URL</Label>
            <Input id="repo-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://gitlab.example/foo.git" />
            {!urlOk && url && <div className="text-xs text-destructive">需为 git URL（https: / git@ / ssh:）</div>}
          </div>
          <div className="flex gap-2">
            <Input value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="分支(可选)" />
            <Input value={commit} onChange={(e) => setCommit(e.target.value)} placeholder="commit(可选)" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>取消</Button>
          <Button disabled={!urlOk || busy} onClick={submit}>clone</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 3: 类型检查**

Run: `cd packages/web/frontend && npx tsc --noEmit`
Expected: 0 error。

- [ ] **Step 4: Commit**

```bash
git add packages/web/frontend/src/components/CloneProgress.tsx packages/web/frontend/src/components/AddRepoDialog.tsx
git commit -m "feat(web): CloneProgress + AddRepoDialog 组件"
```

---

## Task 8: ReposPage（列表 + 行内进度 + 删除）

**Files:**
- Create: `packages/web/frontend/src/pages/ReposPage.tsx`
- Create: `packages/web/frontend/src/pages/ReposPage.test.tsx`

**Interfaces:**
- Consumes: `listRepos`/`deleteRepo`/`pullRepo`（Task 6）、`CloneProgress`/`AddRepoDialog`（Task 7）、shadcn Table/Button/Dialog。

- [ ] **Step 1: 实现 ReposPage.tsx**

```tsx
import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { listRepos, deleteRepo, pullRepo, ApiError } from "@/api/client";
import type { Repo } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { AddRepoDialog } from "@/components/AddRepoDialog";
import { CloneProgress } from "@/components/CloneProgress";

function fmtSize(b?: number) {
  if (!b) return "-";
  if (b > 1_000_000) return `${(b / 1_000_000).toFixed(1)} MB`;
  if (b > 1000) return `${(b / 1000).toFixed(0)} KB`;
  return `${b} B`;
}

export function ReposPage() {
  const [repos, setRepos] = useState<Repo[]>([]);
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setRepos(await listRepos());
    } catch (e) {
      if (e instanceof ApiError) toast.error("加载仓库列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  async function doDelete() {
    if (!pendingDelete) return;
    try {
      setBusy(true);
      await deleteRepo(pendingDelete);
      await refresh();
    } catch (e) {
      if (e instanceof ApiError) toast.error(e.status === 409 ? "仓库正被使用" : `删除失败（${e.status}）`);
    } finally {
      setBusy(false);
      setPendingDelete(null);
    }
  }

  async function doPull(name: string) {
    try {
      await pullRepo(name);
      toast.success(`正在更新 ${name}`);
      setTimeout(() => void refresh(), 1500);
    } catch (e) {
      if (e instanceof ApiError) toast.error(`更新失败（${e.status}）`);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="font-serif text-lg">仓库</h1>
        <Button onClick={() => setAddOpen(true)}>+ 添加仓库</Button>
      </div>
      {loading ? (
        <div className="text-sm text-muted-foreground">加载中…</div>
      ) : repos.length === 0 ? (
        <div className="text-sm text-muted-foreground">暂无仓库。点「+ 添加仓库」clone 一个。</div>
      ) : (
        <table className="w-full text-sm">
          <thead className="border-b border-border text-left text-muted-foreground">
            <tr>
              <th className="py-2 pr-4">名称</th>
              <th className="py-2 pr-4">来源</th>
              <th className="py-2 pr-4">分支</th>
              <th className="py-2 pr-4">大小</th>
              <th className="py-2 pr-4">状态</th>
              <th className="py-2 pr-4">操作</th>
            </tr>
          </thead>
          <tbody>
            {repos.map((r) => (
              <tr key={r.name} className="border-b border-border">
                <td className="py-2 pr-4"><Link to={`/repos/${r.name}`} className="text-primary hover:underline">{r.name}</Link></td>
                <td className="py-2 pr-4 text-muted-foreground">{r.source?.url ?? r.source?.kind ?? "-"}</td>
                <td className="py-2 pr-4 text-muted-foreground">{r.source?.branch ?? "-"}</td>
                <td className="py-2 pr-4 text-muted-foreground">{fmtSize(r.size_bytes)}</td>
                <td className="py-2 pr-4">
                  {(r.state === "cloning" || r.state === "pulling") ? (
                    <CloneProgress name={r.name} />
                  ) : r.state === "failed" ? (
                    <span className="text-destructive">✗ 失败</span>
                  ) : r.state === "stale" ? (
                    <span className="text-yellow">⚠ 未完成</span>
                  ) : (
                    <span className="text-green">✓ 就绪</span>
                  )}
                </td>
                <td className="py-2 pr-4 space-x-2">
                  <Button size="sm" variant="ghost" onClick={() => doPull(r.name)}>更新</Button>
                  <Button size="sm" variant="ghost" className="text-red" onClick={() => setPendingDelete(r.name)}>删除</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <AddRepoDialog open={addOpen} onOpenChange={setAddOpen} onCreated={() => void refresh()} />

      <Dialog open={!!pendingDelete} onOpenChange={(o) => !o && setPendingDelete(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除仓库</DialogTitle>
            <DialogDescription>删除仓库 {pendingDelete}？代码目录永久删除。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPendingDelete(null)}>取消</Button>
            <Button variant="destructive" disabled={busy} onClick={doDelete}>确认</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
```

- [ ] **Step 2: 加测试 ReposPage.test.tsx**

```tsx
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router-dom";
import { ReposPage } from "./ReposPage";
import { Toaster } from "@/components/ui/sonner";

const server = setupServer(
  http.get("/api/repos", () => HttpResponse.json([
    { name: "foo", state: "ready", source: { kind: "git", url: "https://x/foo.git", branch: "main" } },
    { name: "bar", state: "failed", source: { kind: "git" } },
  ])),
  http.delete("/api/repos/:name", ({ params }) => HttpResponse.json({ deleted: params.name })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderPage() {
  render(<MemoryRouter><ReposPage /><Toaster /></MemoryRouter>);
}

describe("ReposPage", () => {
  it("列出仓库 + 状态", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("foo")).toBeInTheDocument());
    expect(screen.getByText("✗ 失败")).toBeInTheDocument();
  });

  it("删除确认 Dialog", async () => {
    const spy = vi.spyOn(console, "log").mockImplementation(() => {});
    renderPage();
    await waitFor(() => expect(screen.getByText("foo")).toBeInTheDocument());
    fireEvent.click(screen.getAllByText("删除")[0]);
    expect(await screen.findByText(/删除仓库/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    await waitFor(() => expect(screen.queryByText(/删除仓库/)).toBeNull());
    spy.mockRestore();
  });
});
```

- [ ] **Step 3: 跑测试**

Run: `cd packages/web/frontend && npx vitest run src/pages/ReposPage.test.tsx`
Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add packages/web/frontend/src/pages/ReposPage.tsx packages/web/frontend/src/pages/ReposPage.test.tsx
git commit -m "feat(web): ReposPage 列表+行内进度+删除 Dialog"
```

---

## Task 9: RepoDetailPage（详情 + 分支切换 + 发起扫描）

**Files:**
- Create: `packages/web/frontend/src/pages/RepoDetailPage.tsx`

**Interfaces:**
- Consumes: `getRepo`/`pullRepo`/`checkoutRepo`（Task 6）、`CloneProgress`（Task 7）。
- 依赖 Task 11 注册路由 `/repos/:name`。

- [ ] **Step 1: 实现 RepoDetailPage.tsx**

```tsx
import { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { toast } from "sonner";
import { getRepo, pullRepo, checkoutRepo, ApiError } from "@/api/client";
import type { RepoDetail } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CloneProgress } from "@/components/CloneProgress";

export function RepoDetailPage() {
  const { name = "" } = useParams<{ name: string }>();
  const nav = useNavigate();
  const [repo, setRepo] = useState<RepoDetail | null>(null);
  const [branch, setBranch] = useState("");

  useEffect(() => {
    getRepo(name).then(setRepo).catch(() => toast.error("加载失败"));
  }, [name]);

  async function doCheckout() {
    if (!branch.trim()) return;
    try {
      await checkoutRepo(name, branch.trim());
      toast.success(`已切换到 ${branch.trim()}`);
      setRepo(await getRepo(name));
    } catch (e) {
      if (e instanceof ApiError) toast.error(e.status === 422 ? "分支不存在" : `切换失败（${e.status}）`);
    }
  }

  if (!repo) return <div className="text-sm text-muted-foreground">加载中…</div>;

  const busy = repo.state === "cloning" || repo.state === "pulling";

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Link to="/repos" className="text-sm text-muted-foreground hover:underline">← 仓库</Link>
        <h1 className="font-serif text-lg">{repo.name}</h1>
        <span className={repo.state === "ready" ? "text-green text-sm" : repo.state === "failed" ? "text-destructive text-sm" : "text-muted-foreground text-sm"}>
          {repo.state}
        </span>
      </div>

      <div className="flex gap-2">
        <Button onClick={() => nav(`/scan/new?repo=${encodeURIComponent(name)}`)} disabled={repo.state !== "ready"}>
          发起扫描
        </Button>
        <Button variant="outline" onClick={async () => { await pullRepo(name); toast.success("更新中"); }}>
          更新 pull
        </Button>
      </div>

      {busy && <CloneProgress name={name} />}
      {repo.state === "stale" && (
        <div className="border border-border bg-card p-3 text-sm text-yellow">⚠ 上次 clone 未完成，建议重新添加或更新。</div>
      )}

      <div className="border border-border bg-card p-4 space-y-1 text-sm">
        <div>来源：{repo.source?.url ?? repo.source?.kind ?? "-"}</div>
        <div>分支：{repo.source?.branch ?? "-"} {repo.source?.commit ? `@ ${repo.source.commit.slice(0, 8)}` : ""}</div>
        <div>clone 于：{repo.cloned_at ?? "-"} · 最后更新：{repo.last_pull_at ?? "-"}</div>
        {repo.last_error && <div className="text-destructive">错误：{repo.last_error}</div>}
      </div>

      <div className="flex gap-2">
        <Input value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="切换分支" />
        <Button variant="outline" onClick={doCheckout}>checkout</Button>
      </div>

      {repo.recent_events && repo.recent_events.length > 0 && (
        <div className="border border-border bg-card p-4">
          <div className="mb-2 text-sm font-medium">clone 历史（最近）</div>
          <ul className="space-y-1 text-xs text-muted-foreground">
            {repo.recent_events.slice(-10).map((e, i) => (
              <li key={i}>{JSON.stringify(e)}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 类型检查**

Run: `cd packages/web/frontend && npx tsc --noEmit`
Expected: 0 error（路由在 Task 11 注册后才能跑通，但 tsc 不依赖路由）。

- [ ] **Step 3: Commit**

```bash
git add packages/web/frontend/src/pages/RepoDetailPage.tsx
git commit -m "feat(web): RepoDetailPage 详情+分支切换+发起扫描"
```

---

## Task 10: ScanNewPage / ScanFormFields 改造（选 repo / path）

**Files:**
- Modify: `packages/web/frontend/src/pages/ScanNewPage.tsx`
- Modify: `packages/web/frontend/src/components/ScanFormFields.tsx`
- Modify: `packages/web/frontend/src/pages/ScanNewPage.test.tsx`

**Interfaces:**
- Consumes: `listRepos`（Task 6）、`AddRepoDialog`/`CloneProgress`（Task 7）、`useSearchParams`（预选 `?repo=`）。

- [ ] **Step 1: 改 ScanNewPage.tsx**

1a. `FormState`（:17-27）改为：
```typescript
export interface FormState {
  sourceKind: "repo" | "path";
  selectedRepo: string;       // repo kind 用
  sourceValue: string;        // path kind 用
  url: string;
  wsName: string;
  reuseLatest: boolean;
  yaml: string;
}
```
> 移除 `branch`/`commit`/`forceReclone`（归 AddRepoDialog）。

1b. 初始 state（:94-104）改为 `{ sourceKind: "repo", selectedRepo: "", sourceValue: "", url: "", wsName: "", reuseLatest: false, yaml: "..." }`。

1c. `buildBody`（:29-45）改为：
```typescript
function buildBody(type: ScanType, f: FormState): ScanRequest {
  if (type === "correlation") return { type, config_yaml: f.yaml };
  const source = f.sourceKind === "repo"
    ? { kind: "repo" as const, value: f.selectedRepo }
    : { kind: "path" as const, value: f.sourceValue };
  const body: ScanRequest = { type, source, url: f.url, workspace_name: f.wsName || undefined };
  if (type === "blackbox") body.reuse_latest_whitebox = f.reuseLatest;
  return body;
}
```

1d. `validateSourceValue`（:60-66）改为按 kind 校验（repo→选中非空；path→绝对路径）：
```typescript
function validateSource(kind: "repo" | "path", selectedRepo: string, pathValue: string): string | null {
  if (kind === "repo") return selectedRepo ? null : "请选择仓库";
  if (!pathValue.trim()) return "代码来源不能为空";
  return /^(\/|[A-Za-z]:[\\/])/.test(pathValue) ? null : "本地路径需为绝对路径";
}
```

1e. `deriveName`（:75-89）改为：repo → `selectedRepo`；path → basename（去掉 kind 分支里的 git 逻辑）：
```typescript
function deriveName(kind: "repo" | "path", selectedRepo: string, pathValue: string): string {
  const base = kind === "repo" ? selectedRepo
    : (pathValue.trim().replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? "");
  if (!base) return "";
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const ts = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  return `${base}_${ts}`;
}
```

1f. 用 `useSearchParams` 预选 repo。组件顶部加：
```typescript
import { useSearchParams } from "react-router-dom";
// ...
const [params] = useSearchParams();
const presetRepo = params.get("repo");
useEffect(() => {
  if (presetRepo) set({ sourceKind: "repo", selectedRepo: presetRepo });
}, [presetRepo]);
```

1g. `isValid` / `sourceValueErr` 调用改为 `validateSource(f.sourceKind, f.selectedRepo, f.sourceValue)`；传给 `ScanFormFields` 的 props 调整（见 Step 2）。

- [ ] **Step 2: 改 ScanFormFields.tsx**

整体替换为（移除 git fieldset，加 repo Select + 内联 AddRepoDialog；保留 path + 扫描配置 fieldset）：
```tsx
import { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { FileSystemPicker } from "./FileSystemPicker";
import { AddRepoDialog } from "./AddRepoDialog";
import { CloneProgress } from "./CloneProgress";
import { listRepos } from "@/api/client";
import type { Repo } from "@/api/types";
import type { FormState } from "../pages/ScanNewPage";

interface Props {
  type: "whitebox" | "blackbox";
  f: FormState;
  set: (patch: Partial<FormState>) => void;
  sourceErr: string | null;
  urlErr: string | null;
  loadingConflict: boolean;
  derivedName: string;
}

export function ScanFormFields({ type, f, set, sourceErr, urlErr, loadingConflict, derivedName }: Props) {
  const [repos, setRepos] = useState<Repo[]>([]);
  const [addOpen, setAddOpen] = useState(false);

  useEffect(() => { listRepos().then(setRepos).catch(() => {}); }, [addOpen]);

  const selectedRepoState = repos.find((r) => r.name === f.selectedRepo)?.state;

  return (
    <Card>
      <CardHeader><CardTitle>{type === "blackbox" ? "黑盒扫描" : "白盒扫描"}</CardTitle></CardHeader>
      <CardContent className="space-y-6">
        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">选择仓库</legend>
          <Select value={f.sourceKind} onValueChange={(v) => set({ sourceKind: v as "repo" | "path" })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="repo">已下载仓库</SelectItem>
              <SelectItem value="path">本地路径</SelectItem>
            </SelectContent>
          </Select>

          {f.sourceKind === "repo" ? (
            <div className="space-y-2">
              <Select value={f.selectedRepo} onValueChange={(v) => set({ selectedRepo: v })}>
                <SelectTrigger><SelectValue placeholder="选择仓库" /></SelectTrigger>
                <SelectContent>
                  {repos.map((r) => (
                    <SelectItem key={r.name} value={r.name}>{r.name} — {r.source?.url ?? r.state}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button variant="outline" size="sm" onClick={() => setAddOpen(true)}>+ 添加新仓库</Button>
              {f.selectedRepo && selectedRepoState && selectedRepoState !== "ready" && (
                selectedRepoState === "cloning" || selectedRepoState === "pulling"
                  ? <CloneProgress name={f.selectedRepo} />
                  : <div className="text-xs text-destructive">仓库未就绪（{selectedRepoState}）</div>
              )}
              <AddRepoDialog open={addOpen} onOpenChange={setAddOpen}
                onCreated={(name) => set({ selectedRepo: name })} />
            </div>
          ) : (
            <div className="space-y-2">
              <div className="flex gap-2">
                <Input value={f.sourceValue} onChange={(e) => set({ sourceValue: e.target.value })}
                  placeholder="/root/code/foo" />
                <FileSystemPicker value={f.sourceValue} onChange={(v) => set({ sourceValue: v })} triggerLabel="📁 浏览" />
              </div>
            </div>
          )}
          {sourceErr && <div className="text-destructive text-xs">{sourceErr}</div>}
        </fieldset>

        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">扫描目标 + 命名</legend>
          <div className="space-y-2">
            <Label htmlFor="url">目标 URL</Label>
            <Input id="url" value={f.url} onChange={(e) => set({ url: e.target.value })} placeholder="http://example.com" />
            {urlErr && <div className="text-destructive text-xs">{urlErr}</div>}
          </div>
          <div className="space-y-2">
            <Label htmlFor="wsName">workspace 名</Label>
            <Input id="wsName" value={f.wsName} onChange={(e) => set({ wsName: e.target.value })} placeholder="空=自动 {repo}_{timestamp}" />
            {loadingConflict && <div className="text-xs text-yellow">检测重名中…</div>}
            {!f.wsName && derivedName && <div className="text-xs text-muted-foreground">预览名：{derivedName}（预览，实际由后端生成）</div>}
          </div>
        </fieldset>

        {type === "blackbox" && (
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">复用</legend>
            <div className="flex items-center gap-2">
              <Checkbox id="reuseLatest" checked={f.reuseLatest} onCheckedChange={(v) => set({ reuseLatest: !!v })} />
              <Label htmlFor="reuseLatest">复用最新白盒结果</Label>
            </div>
            <div className="text-xs text-muted-foreground">--latest 按 url 匹配；不勾选时后端传 --repo 显式 standalone</div>
          </fieldset>
        )}
      </CardContent>
    </Card>
  );
}
```

1h. ScanNewPage 传给 `ScanFormFields` 的 props 调整：移除 `conflict`/`onConflictDismiss`/`sourceValueErr`/`loadingConflict`，改传 `sourceErr`（= `validateSource(...)` 结果）。`ScanNewPage` 的 `return` 里两处 `<ScanFormFields ...>` 调用同步改 props。

- [ ] **Step 3: 改 ScanNewPage.test.tsx**

调整现有 8 断言到新选择器（git fieldset 移除 → repo/path Select）+ 加 repo 预选 + 未就绪用例。最小新增：
```tsx
it("选 repo 且就绪 → buildBody kind=repo", async () => {
  server.use(http.get("/api/repos", () => HttpResponse.json([
    { name: "foo", state: "ready", source: { kind: "git" } }])));
  renderPage();
  // 选「已下载仓库」(默认) → 选 foo
  fireEvent.mouseDown(screen.getAllByText("已下载仓库")[0]);
  fireEvent.click(await screen.findByRole("option", { name: /foo/ }));
  // 填 url + 提交 → 断言 POST body source.kind === "repo"
  // (用 MSW capture handler 收集 body)
});
```
> 执行者：现有 8 断言里涉及 git URL/branch/forceReclone 的（如"黑盒 --latest 陷阱"保留；git URL 格式校验用例改为 path 绝对路径校验或删除）。保留：Tabs 切换、wsName 冲突 Dialog、400/409/422 toast、path 绝对路径校验。删除：git URL 格式校验、git fieldset 显隐。

- [ ] **Step 4: 跑测试**

Run: `cd packages/web/frontend && npx vitest run src/pages/ScanNewPage.test.tsx`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/pages/ScanNewPage.tsx packages/web/frontend/src/components/ScanFormFields.tsx packages/web/frontend/src/pages/ScanNewPage.test.tsx
git commit -m "feat(web): /scan/new 改选 repo/path，移除 git fieldset"
```

---

## Task 11: 路由 + 顶栏 + 冒烟回归

**Files:**
- Modify: `packages/web/frontend/src/router.tsx:3-16,35-58`
- Modify: `packages/web/frontend/src/components/layout/TopBar.tsx:13-18`

- [ ] **Step 1: 改 router.tsx**

顶部 import 加：
```typescript
import { ReposPage } from "./pages/ReposPage";
import { RepoDetailPage } from "./pages/RepoDetailPage";
```
路由数组（`/workspaces` 后）加：
```typescript
      { path: "/repos", element: <ReposPage /> },
      { path: "/repos/:name", element: <RepoDetailPage /> },
```

- [ ] **Step 2: 改 TopBar.tsx**

`NAV`（:13-18）插入「仓库」：
```typescript
const NAV: NavItem[] = [
  { label: "Dashboard", to: "/", end: true },
  { label: "Workspaces", to: "/workspaces", end: true },
  { label: "仓库", to: "/repos", end: true },
  { label: "Scan", to: "/scan/new" },
  { label: "Settings", to: "/settings" },
];
```

- [ ] **Step 3: 前端全套类型 + 测试回归**

Run: `cd packages/web/frontend && npx tsc --noEmit && npx vitest run`
Expected: 0 type error；所有前端测试 PASS（DSF / 列表页 / 扫描页调整后 / Repos 新增）。

- [ ] **Step 4: 后端改动相关测试回归**

Run: `cd packages/web && uv run pytest tests/test_event_tailer.py tests/test_models_repo_source.py tests/test_api_scan_repo_source.py tests/test_repo_manager.py tests/test_api_repos.py tests/test_scan_manager.py tests/test_api_scan.py -v`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/router.tsx packages/web/frontend/src/components/layout/TopBar.tsx
git commit -m "feat(web): /repos 路由 + 顶栏仓库入口"
```

- [ ] **Step 6: 人工冒烟（不在 plan 自动化范围，提示执行者）**

启动 web（`uv run uvicorn shannon_web.app:app` 或 docker compose），浏览器验：`/repos` 列表 → 添加仓库 Dialog → 行内 clone 进度 → 详情页 → 发起扫描跳 `/scan/new?repo=` → `/scan/new` 选 repo 扫描。git 凭证需配 `GITLAB_USER`/`GITLAB_TOKEN`。

---

## Self-Review 记录

- **Spec 覆盖**：spec §1-9 全覆盖——架构/数据模型/RepoManager/API/ScanManager 改/前端三页/错误处理/测试/迁移/风险均落 task。`stale` 态（spec §2.3）= Task 4 `_repo_view`；DELETE 引用判定（spec §5）= Task 3 `active_repo_sources` + Task 5 delete handler；迁移（spec §7）= Task 4 `migrate_legacy` + Task 5 lifespan。
- **占位符**：无 TBD/TODO；每个 code step 给完整代码。Task 3 Step 1 测试直调 `_resolve_inputs` 避开 scan 子进程；Task 5 SSE 用 queue 桥接 EventTailer callback→async generator。
- **类型一致**：`RepoSource.value`（models）/ `_resolve_repo_path(name)` / `active_repo_sources` / 前端 `Repo.state` 跨 task 一致；`useEventSource(url, stopType)` 前后端 `clone_end` 对齐。
- **已知简化**：Task 4 `clone()` 并发判定用 `len(self._jobs)`（简单），Semaphore 在 task 内二次保底；Task 9 RepoDetailPage 无独立测试（靠 tsc + Task 11 回归覆盖，发起扫描跳转逻辑轻）。
