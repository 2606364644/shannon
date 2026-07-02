# Shannon Web 后端（子项目 1）实现 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 shannon web 平台后端——core 的 `StructuredEventRenderer`（唯一侵入点）+ `packages/web` 全后端（6 组件 + FastAPI + REST/SSE API）+ 联动 ndjson 小增强 + 部署 wiring，可独立于前端用 curl + SSE 客户端冒烟。

**Architecture:** 三种扫描（白盒/黑盒/联动）统一 `asyncio` subprocess 调 CLI；扫描子进程经 `StructuredEventRenderer` 把原子 `DisplayEvent` 写成 `events.ndjson`（env `SHANNON_WEB_EVENT_FILE` 启用，未设=零影响）；`EventTailer` tail ndjson → SSE 推前端；`ScanManager` 管并发/超时/取消/崩溃兜底（子进程无 `scan_end` 时补写）；`WorkspacesIndexer`/`DeliverablesReader` 复用 core 的 `SessionManager`/`resolve_track_deliverable`/`compute_deliverables_summary`/`get_workspace_vuln_counts` 读产物。

**Tech Stack:** Python ≥3.11、FastAPI、uvicorn[standard]、aiofiles、pydantic（core 已带）、pytest + pytest-asyncio + httpx（async SSE 测试）、uv workspace（`packages/*` 自动成员）。

**Spec 来源：** 上位设计 `docs/superpowers/specs/2026-07-02-shannon-web-platform-design.md`（架构 / ndjson schema 硬契约 / 扫描类型 / 错误处理 / 测试 / 部署定稿），实现细节见 `docs/superpowers/specs/2026-07-02-shannon-web-backend-design.md`。

## Global Constraints

逐条来自 spec，每个 task 隐式遵守：

- **Python ≥3.11**；新包 `packages/web` 加入即自动成为 uv workspace 成员（根 `pyproject.toml` 已有 `[tool.uv.workspace] members = ["packages/*"]`）。
- **包间依赖**用包名声明（`dependencies = ["shannon-core"]`），经根 `[tool.uv.sources]` 的 `workspace = true` 解析为本地源——**不写 path 依赖**。
- **ndjson 事件 schema 是前后端 + core 三方硬契约**：每行 = 通用字段 `{ts, category, type}` + 各 event 类型附加字段；收尾行 `{ts, category:"CONTROL", type:"scan_end", status, returncode?, stderr_tail?}`；联动行 `{ts, category:"CONTROL", type:"correlation_progress", node, name, status, detail?}`。**不得擅自改**；如需改回主 spec 并同步前端。
- **`ts` 取自 `event.timestamp`**（基类 `DisplayEvent.timestamp: str`，已是 ISO8601）；`category` 取自 `event.category`；`type` = `type(event).__name__`。
- **`StructuredEventRenderer` 是 core 唯一改动**：env `SHANNON_WEB_EVENT_FILE` 未设 = 行为完全不变（dispatcher 不挂它）。
- **测试陷阱（CLAUDE.md 铁律）**：只跑改动相关子集——`packages/web/tests/` + `packages/core/tests/display/test_structured_event_renderer.py` + Task 2 的挂载测试。**不广跑预存挂起的全套 pytest**。ScanManager 测试用 mock CLI 子进程（短脚本），**不真跑 Temporal**。
- **yaml 校验**用 `parse_multi_repo_config`（Pydantic `ValidationError`），API 返 **422 + `e.errors()`**——Pydantic 错误不带 yaml 行号，故 spec「422+行号」修正为「422+结构化错误列表（loc/msg）」。
- **语言**：代码标识符英文，注释/文档可中文；commit 用 conventional commits（`feat(web): ...` / `feat(core): ...` / `feat(multi): ...`）。
- **未设 env = 不挂** 范式对齐 `config/concurrency.py` 散读 `os.environ.get` 风格，不强行集中。

## File Structure

**core（改 1 处 + 新建 1 文件 + 1 测试）：**
- Create: `packages/core/src/shannon_core/display/structured_event_renderer.py` — `StructuredEventRenderer`，原子 DisplayEvent → ndjson
- Modify: `packages/core/src/shannon_core/audit/workflow_logger.py` — `initialize()`（L72-L83 renderers 组装段后加 env 分支）+ `close()`（L243-L247 加遍历 renderers 调 close）
- Test: `packages/core/tests/display/test_structured_event_renderer.py`
- Test: `packages/core/tests/audit/test_web_renderer_mount.py`

**multi（联动小增强）：**
- Modify: `packages/multi/src/shannon_multi/orchestrator.py` — 关键节点写 correlation ndjson（asyncio.Lock）

**web（全新包）：**
- Create: `packages/web/pyproject.toml`
- Create: `packages/web/src/shannon_web/__init__.py`, `app.py`, `config.py`, `models.py`
- Create: `packages/web/src/shannon_web/components/{workspaces_indexer,scan_manager,event_tailer,deliverables_reader,multi_repo_config_store,git_fetcher}.py`
- Create: `packages/web/src/shannon_web/api/{workspaces,scan,deliverables,events,multi_configs}.py`
- Create: `packages/web/tests/{conftest,test_app_health,test_workspaces_indexer,test_deliverables_reader,test_multi_repo_config_store,test_git_fetcher,test_event_tailer,test_scan_manager,test_api_workspaces,test_api_scan,test_api_events,test_api_multi_configs}.py`
- Create: `packages/web/Dockerfile`
- Modify: `docker-compose.yml` — 加 `web` 服务

---

## 任务依赖图

```
Task 1 (renderer) ──► Task 2 (挂载)
Task 3 (脚手架) ──► {Task 4,5,6,7,8} ──► Task 9 (ScanManager) ──┐
                                                                  ├──► Task 10 (REST) ──► Task 11 (SSE)
Task 3 ──────────────────────────────────────────────────────────┘
Task 12 (orchestrator) 独立于 web（改 multi 包），可与 4-11 并行
Task 13 (部署) 依赖 1-12 全部
```


## Task 1: StructuredEventRenderer（core 唯一新文件）

把每个 `DisplayEvent` 序列化成一行 JSON 追加写到 `events.ndjson`，收到 `SummaryEvent` 时额外写一行 `scan_end` 收尾。这是 Web 对 core 的唯一侵入点，单测必须独立绿。

**Files:**
- Create: `packages/core/src/shannon_core/display/structured_event_renderer.py`
- Test: `packages/core/tests/display/test_structured_event_renderer.py`

**Interfaces:**
- Consumes: `shannon_core.display.events.DisplayEvent`（基类，含 `.timestamp: str` / `.category: str`）、`SummaryEvent`（含 `.status: str`，值 `completed|failed|cancelled`）
- Produces: class `StructuredEventRenderer(path: str)`，方法 `async render(event: DisplayEvent) -> None`、`async close() -> None`。ndjson 行格式见 Global Constraints（通用字段 + 附加字段）。

- [ ] **Step 1: 写失败测试（序列化 + 通用字段 + 附加字段）**

```python
# packages/core/tests/display/test_structured_event_renderer.py
import asyncio
import json
from pathlib import Path

import pytest

from shannon_core.display.events import (
    AgentEvent,
    InfoEvent,
    PhaseEvent,
    StepEvent,
    SummaryEvent,
    ToolCallEvent,
)
from shannon_core.display.structured_event_renderer import StructuredEventRenderer


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_writes_phase_event_with_common_and_extra_fields(tmp_path: Path):
    f = tmp_path / "events.ndjson"
    r = StructuredEventRenderer(str(f))
    ev = PhaseEvent(timestamp="2026-07-02T09:44:01.123Z", category="PHASE",
                    phase="recon", event="start", steps=("s1", "s2"))
    await r.render(ev)
    await r.close()

    rows = _lines(f)
    assert len(rows) == 1
    row = rows[0]
    assert row["ts"] == "2026-07-02T09:44:01.123Z"
    assert row["category"] == "PHASE"
    assert row["type"] == "PhaseEvent"
    assert row["phase"] == "recon"
    assert row["event"] == "start"
    assert row["steps"] == ["s1", "s2"]  # tuple -> list


@pytest.mark.asyncio
async def test_tool_call_parameters_any_serializable(tmp_path: Path):
    f = tmp_path / "events.ndjson"
    r = StructuredEventRenderer(str(f))
    await r.render(ToolCallEvent(timestamp="t1", category="TOOL",
                                 agent_name="recon", tool_name="Bash",
                                 parameters={"cmd": "ls", "n": 3}))
    await r.close()
    row = _lines(f)[0]
    assert row["parameters"] == {"cmd": "ls", "n": 3}


@pytest.mark.asyncio
async def test_summary_event_appends_scan_end(tmp_path: Path):
    f = tmp_path / "events.ndjson"
    r = StructuredEventRenderer(str(f))
    await r.render(SummaryEvent(timestamp="t2", category="SUMMARY",
                                status="completed", total_duration_ms=1000,
                                total_cost_usd=0.5))
    await r.close()
    rows = _lines(f)
    assert len(rows) == 2
    assert rows[0]["type"] == "SummaryEvent"
    assert rows[1] == {"ts": "t2", "category": "CONTROL", "type": "scan_end", "status": "completed"}


@pytest.mark.asyncio
async def test_non_summary_event_does_not_write_scan_end(tmp_path: Path):
    f = tmp_path / "events.ndjson"
    r = StructuredEventRenderer(str(f))
    await r.render(StepEvent(timestamp="t3", category="STEP", name="x", phase="p", event="start"))
    await r.close()
    assert [r["type"] for r in _lines(f)] == ["StepEvent"]


@pytest.mark.asyncio
async def test_lazy_open_no_event_no_file(tmp_path: Path):
    f = tmp_path / "events.ndjson"
    r = StructuredEventRenderer(str(f))
    await r.close()
    assert not f.exists()


@pytest.mark.asyncio
async def test_concurrent_renders_no_interleaving(tmp_path: Path):
    f = tmp_path / "events.ndjson"
    r = StructuredEventRenderer(str(f))
    events = [InfoEvent(timestamp=f"t{i}", category="INFO", message=f"m{i}") for i in range(50)]
    await asyncio.gather(*(r.render(e) for e in events))
    await r.close()
    rows = _lines(f)
    assert len(rows) == 50
    for row in rows:
        assert row["type"] == "InfoEvent"  # 每行完整可 parse = 无交错断行
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/core && uv run pytest tests/display/test_structured_event_renderer.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.display.structured_event_renderer`

- [ ] **Step 3: 实现 renderer**

```python
# packages/core/src/shannon_core/display/structured_event_renderer.py
"""Web 事件落盘 renderer：把原子 DisplayEvent 序列化成 ndjson 行。

env SHANNON_WEB_EVENT_FILE 启用（由 workflow_logger.initialize 挂载）。
收到 SummaryEvent 时额外写一行 scan_end 收尾（双路兜底之一，另一路在 web 的 ScanManager）。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from typing import Any

import aiofiles

from shannon_core.display.events import DisplayEvent, SummaryEvent


class StructuredEventRenderer:
    def __init__(self, path: str) -> None:
        self._path = path
        self._fh: Any = None  # lazy open
        self._lock = asyncio.Lock()

    async def _ensure_open(self) -> Any:
        if self._fh is None:
            self._fh = await aiofiles.open(self._path, "a")
        return self._fh

    @staticmethod
    def _serialize(event: DisplayEvent) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ts": event.timestamp,
            "category": event.category,
            "type": type(event).__name__,
        }
        if is_dataclass(event):
            extra = asdict(event)
            extra.pop("timestamp", None)  # 已并入 ts
            extra.pop("category", None)
            payload.update(extra)
        return payload

    async def render(self, event: DisplayEvent) -> None:
        async with self._lock:
            fh = await self._ensure_open()
            await fh.write(json.dumps(self._serialize(event), default=str, ensure_ascii=False) + "\n")
            await fh.flush()
            if isinstance(event, SummaryEvent):
                await fh.write(json.dumps({
                    "ts": event.timestamp,
                    "category": "CONTROL",
                    "type": "scan_end",
                    "status": event.status,
                }, ensure_ascii=False) + "\n")
                await fh.flush()

    async def close(self) -> None:
        async with self._lock:
            if self._fh is not None:
                await self._fh.flush()
                await self._fh.close()
                self._fh = None
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/core && uv run pytest tests/display/test_structured_event_renderer.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/display/structured_event_renderer.py \
        packages/core/tests/display/test_structured_event_renderer.py
git commit -m "feat(core): add StructuredEventRenderer (ndjson event sink, web-only)"
```


## Task 2: workflow_logger 挂载 + close 遍历（core 改 1 处）

env `SHANNON_WEB_EVENT_FILE` 存在时把 `StructuredEventRenderer` 挂进 dispatcher；`close()` 加遍历 renderers 调 `close()`（当前只关 stream，Web renderer 需要 flush+关句柄）。

**Files:**
- Modify: `packages/core/src/shannon_core/audit/workflow_logger.py`（`initialize()` 的 L72-L83 组装段 + `close()` 的 L243-L247）
- Test: `packages/core/tests/audit/test_web_renderer_mount.py`

**Interfaces:**
- Consumes: `StructuredEventRenderer`（任务 1）
- Produces: env `SHANNON_WEB_EVENT_FILE` 控制挂载；`close()` 遍历 dispatcher renderers 调 `close()`

**核实结论（已读源码）：** `import os` 已在 `workflow_logger.py:4`（无需补）。renderers 组装在 L72-L83，`self._dispatcher = DisplayDispatcher(renderers)` 在 L83。`close()` 在 L243-L247，**当前不遍历 renderers**。

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/audit/test_web_renderer_mount.py
import os
import pytest

from shannon_core.audit.workflow_logger import WorkflowLogger


def _make_logger() -> WorkflowLogger:
    # 最小构造：跳过 rich/console，只走 FileLogRenderer 路径
    return WorkflowLogger(workflow_id="wf-test", console=None, use_rich=False, dashboard=None)


@pytest.mark.asyncio
async def test_env_unset_does_not_mount_web_renderer(tmp_path, monkeypatch):
    monkeypatch.delenv("SHANNON_WEB_EVENT_FILE", raising=False)
    logger = _make_logger()
    await logger.initialize(str(tmp_path / "workflow.log"))
    types = [type(r).__name__ for r in logger._dispatcher._renderers]
    assert "StructuredEventRenderer" not in types
    await logger.close()


@pytest.mark.asyncio
async def test_env_set_mounts_web_renderer(tmp_path, monkeypatch):
    target = tmp_path / "events.ndjson"
    monkeypatch.setenv("SHANNON_WEB_EVENT_FILE", str(target))
    logger = _make_logger()
    await logger.initialize(str(tmp_path / "workflow.log"))
    types = [type(r).__name__ for r in logger._dispatcher._renderers]
    assert "StructuredEventRenderer" in types
    await logger.close()


@pytest.mark.asyncio
async def test_close_calls_renderer_close(tmp_path, monkeypatch):
    target = tmp_path / "events.ndjson"
    monkeypatch.setenv("SHANNON_WEB_EVENT_FILE", str(target))
    logger = _make_logger()
    await logger.initialize(str(tmp_path / "workflow.log"))
    web_r = next(r for r in logger._dispatcher._renderers
                 if type(r).__name__ == "StructuredEventRenderer")
    await logger.close()
    assert web_r._fh is None  # close 后句柄已关
```

> **若 `WorkflowLogger.__init__` 签名与上面不符**：实现者先 `rg -n "class WorkflowLogger" packages/core/src/shannon_core/audit/workflow_logger.py` 看真实构造参数，调整 `_make_logger`。测试意图不变：env 未设→不挂、设了→挂、close→关。

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/core && uv run pytest tests/audit/test_web_renderer_mount.py -v`
Expected: FAIL（无 env 分支 → 第二个测试断言失败；close 不遍历 → 第三个测试 `_fh` 仍非 None）

- [ ] **Step 3: 改 initialize（L72-L83 后加分支）**

定位 `workflow_logger.py` 里 `self._dispatcher = DisplayDispatcher(renderers)` 这一行，**在它之前**插入：

```python
        # 【新增】Web 事件落盘 renderer（env 启用，未设=零影响）
        web_event_file = os.environ.get("SHANNON_WEB_EVENT_FILE")
        if web_event_file:
            from shannon_core.display.structured_event_renderer import StructuredEventRenderer
            renderers.append(StructuredEventRenderer(web_event_file))
```

- [ ] **Step 4: 改 close（L243-L247 加遍历）**

把现有 `close()` 改为（在关 stream 前遍历 renderers）：

```python
    async def close(self) -> None:
        # 【新增】遍历 renderers 调 close（Web renderer 需 flush+关句柄）
        if self._dispatcher is not None:
            for r in getattr(self._dispatcher, "_renderers", []):
                close_fn = getattr(r, "close", None)
                if close_fn is not None:
                    try:
                        await close_fn()
                    except Exception:
                        pass
        if self._stream is not None:
            await self._stream.close()
            self._stream = None
        self._dispatcher = None  # all log_* methods check dispatcher and no-op after close
```

- [ ] **Step 5: 跑测试验证通过**

Run: `cd packages/core && uv run pytest tests/audit/test_web_renderer_mount.py tests/display/test_structured_event_renderer.py -v`
Expected: PASS（两个文件全绿）

- [ ] **Step 6: 回归现有 display 测试**

Run: `cd packages/core && uv run pytest tests/display/ tests/audit/ -v`
Expected: PASS（env 未设路径行为不变，现有 renderer 测试不受影响）

- [ ] **Step 7: 提交**

```bash
git add packages/core/src/shannon_core/audit/workflow_logger.py \
        packages/core/tests/audit/test_web_renderer_mount.py
git commit -m "feat(core): mount StructuredEventRenderer via SHANNON_WEB_EVENT_FILE + close renderers"
```


## Task 3: packages/web 脚手架（pyproject + config + app + models + /health）

建 `packages/web` 包：pyproject（workspace 成员）、config（env 读取）、app（FastAPI + lifespan + `/health`）、models（Pydantic 请求/响应）。`/health` 可测即可。

**Files:**
- Create: `packages/web/pyproject.toml`
- Create: `packages/web/src/shannon_web/__init__.py`, `config.py`, `models.py`, `app.py`
- Create: `packages/web/tests/__init__.py`, `conftest.py`, `test_app_health.py`

**Interfaces:**
- Consumes: `shannon_core.utils.paths.resolve_workspaces_dir`（工作区根）
- Produces:
  - `shannon_web.config.get_config() -> WebConfig`（`.port/.max_concurrent/.scan_timeout/.gitlab_user/.gitlab_token/.workspaces_dir/.repos_dir/.configs_dir/.git_available`）
  - `shannon_web.app.create_app() -> FastAPI` + 模块级 `app`
  - `shannon_web.models.ScanRequest`（`.type`/`.source`/`.url`/`.workspace`/`.reuse_latest`/`.config_name`/`.config_content`/`.save_as`）、`PathSource`、`GitSource`（`.kind/.value/.branch/.commit/.force_reclone`）

- [ ] **Step 1: 建 pyproject.toml**

```toml
# packages/web/pyproject.toml
[project]
name = "shannon-web"
version = "0.1.0"
description = "Shannon Web Platform backend (FastAPI)"
requires-python = ">=3.11"
dependencies = [
    "shannon-core",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "aiofiles>=23.0",
    "pyyaml>=6.0",
]

[project.scripts]
shannon-web = "shannon_web.app:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/shannon_web"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: 建 config.py**

```python
# packages/web/src/shannon_web/config.py
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


class WebConfig:
    def __init__(self) -> None:
        self.port = int(os.environ.get("SHANNON_WEB_PORT", "7878"))
        self.max_concurrent = max(1, int(os.environ.get("SHANNON_WEB_MAX_CONCURRENT", "1")))
        self.scan_timeout = float(os.environ.get("SHANNON_WEB_SCAN_TIMEOUT", "0"))
        self.gitlab_user = os.environ.get("GITLAB_USER")
        self.gitlab_token = os.environ.get("GITLAB_TOKEN")
        self.repos_dir = Path(os.environ.get("SHANNON_REPOS_DIR", "repos"))
        self.configs_dir = Path(os.environ.get("SHANNON_CONFIGS_DIR", "configs"))

    @property
    def workspaces_dir(self) -> Path:
        from shannon_core.utils.paths import resolve_workspaces_dir
        return Path(resolve_workspaces_dir())

    @property
    def git_available(self) -> bool:
        return bool(self.gitlab_user and self.gitlab_token)


@lru_cache
def get_config() -> WebConfig:
    return WebConfig()
```

> **若 `resolve_workspaces_dir` 不在 `utils/paths.py`**：实现者 `rg -n "def resolve_workspaces_dir" packages/core/src` 找真实路径并修正 import。`orchestrator.py:145` 已用它，必然存在。

- [ ] **Step 3: 建 models.py**

```python
# packages/web/src/shannon_web/models.py
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel


class PathSource(BaseModel):
    kind: Literal["path"]
    value: str


class GitSource(BaseModel):
    kind: Literal["git"]
    value: str
    branch: str | None = None
    commit: str | None = None
    force_reclone: bool = False


Source = Union[PathSource, GitSource]


class ScanRequest(BaseModel):
    type: Literal["whitebox", "blackbox", "correlation"]
    source: Source | None = None
    url: str | None = None
    workspace: str | None = None
    reuse_latest: bool = False
    # correlation 专用
    config_name: str | None = None
    config_content: str | None = None
    save_as: str | None = None


class ScanAccepted(BaseModel):
    workspace: str


class ErrorOut(BaseModel):
    detail: str
```

- [ ] **Step 4: 建 app.py（/health + lifespan 占位）**

```python
# packages/web/src/shannon_web/app.py
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup（僵尸清理在任务 9 接入 ScanManager 后填充）
    yield
    # shutdown（任务 9 接入 ScanManager 取消在途扫描后填充）


def create_app() -> FastAPI:
    app = FastAPI(title="Shannon Web", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "git_available": get_config().git_available}

    # 路由由任务 10/11 注册：app.include_router(...)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    cfg = get_config()
    uvicorn.run("shannon_web.app:app", host="0.0.0.0", port=cfg.port, reload=False)
```

```python
# packages/web/src/shannon_web/__init__.py
```

- [ ] **Step 5: 建测试（/health + config）**

```python
# packages/web/tests/__init__.py
```

```python
# packages/web/tests/conftest.py
import os
import sys
from pathlib import Path

import pytest

# 确保 src 在 path（开发期非 wheel 安装）
_ROOT = Path(__file__).resolve().parents[3]
for member in ("src",):
    p = _ROOT / "packages" / "web" / member
    if p.is_dir():
        sys.path.insert(0, str(p))


@pytest.fixture
def tmp_workspaces(tmp_path, monkeypatch):
    ws = tmp_path / "workspaces"
    ws.mkdir()
    monkeypatch.setenv("SHANNON_WORKER_ROOT", str(ws))
    return ws
```

```python
# packages/web/tests/test_app_health.py
from fastapi.testclient import TestClient

from shannon_web.app import create_app


def test_health_ok():
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
```

- [ ] **Step 6: 安装新包 + 跑测试**

Run: `uv sync` 然后 `cd packages/web && uv run pytest tests/test_app_health.py -v`
Expected: PASS（`/health` 返 200）。若 `shannon-core` 未被 workspace 解析，确认根 `pyproject.toml [tool.uv.sources]` 含 `shannon-core = { workspace = true }`。

- [ ] **Step 7: 提交**

```bash
git add packages/web
git commit -m "feat(web): scaffold packages/web (pyproject + config + app + /health)"
```

## Task 4: WorkspacesIndexer（列表 + 状态四态 + 漏洞数）

扫 `workspaces/*/` 读 `session.json` 建列表，状态判定（✓completed / ✗failed / ●running / ⚠interrupted），漏洞数聚合。**用 `SessionManager` 读 session.json（兼容新旧格式），不自己 parse。**

**Files:**
- Create: `packages/web/src/shannon_web/components/__init__.py`, `workspaces_indexer.py`
- Test: `packages/web/tests/test_workspaces_indexer.py`

**Interfaces:**
- Consumes: `shannon_core.session.SessionManager`（`.list_workspaces()/.get_session_data()/.get_status()/.get_scan_type()/.get_created_at()/.get_completed_at()`）、`shannon_core.workspace.get_workspace_vuln_counts(workspace_path) -> dict[str,int]`
- Produces: `WorkspacesIndexer(workspaces_dir)`，`.list_workspaces() -> list[dict]`、`.set_active_pid(ws, pid|None)`（ScanManager 注入）

**v1 简化（注明）：** 联动子白盒 ws 的"隐藏不平铺"由前端列表层处理（识别 `is_correlation` + 前端过滤）。后端忠实返回所有 ws + `is_correlation` 标记；要后端精确隐藏子 ws 需 orchestrator 给子 ws 打 `correlation_parent` 标记，超 v1 边界，标 follow-up。

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_workspaces_indexer.py
import json
import os

import pytest

from shannon_web.components.workspaces_indexer import WorkspacesIndexer


def _make_ws(root, name, status="completed", scan_type="whitebox", queues=None, nested=False):
    ws = root / name
    ws.mkdir(parents=True)
    data = {"status": status, "scan_type": scan_type,
            "created_at": "2026-07-02T10:00:00Z", "completed_at": "2026-07-02T10:05:00Z"}
    payload = {"session": data} if nested else data
    (ws / "session.json").write_text(json.dumps(payload))
    if queues:
        dl = ws / "deliverables" / "whitebox"
        dl.mkdir(parents=True)
        for cls, n in queues.items():
            (dl / f"{cls}_exploitation_queue.json").write_text(
                json.dumps({"vulnerabilities": [{}] * n}))


def test_completed_with_vuln_counts(tmp_workspaces):
    _make_ws(tmp_workspaces, "NodeGoat_x", status="completed", queues={"xss": 3, "ssrf": 1})
    rows = WorkspacesIndexer(tmp_workspaces).list_workspaces()
    assert len(rows) == 1
    assert rows[0]["name"] == "NodeGoat_x"
    assert rows[0]["status"] == "completed"
    assert rows[0]["vuln_counts"] == {"xss": 3, "ssrf": 1}


def test_nested_legacy_session_format(tmp_workspaces):
    _make_ws(tmp_workspaces, "Old_y", status="failed", scan_type="whitebox", nested=True)
    rows = WorkspacesIndexer(tmp_workspaces).list_workspaces()
    assert rows[0]["status"] == "failed"
    assert rows[0]["scan_type"] == "whitebox"


def test_running_when_pid_alive(tmp_workspaces):
    _make_ws(tmp_workspaces, "Run_z", status=None)
    idx = WorkspacesIndexer(tmp_workspaces)
    idx.set_active_pid("Run_z", os.getpid())
    assert idx.list_workspaces()[0]["status"] == "running"


def test_interrupted_when_no_pid_no_status(tmp_workspaces):
    _make_ws(tmp_workspaces, "Dead_w", status=None)
    idx = WorkspacesIndexer(tmp_workspaces)
    assert idx.list_workspaces()[0]["status"] == "interrupted"


def test_correlation_marked(tmp_workspaces):
    _make_ws(tmp_workspaces, "Cor_c", status="completed", scan_type="correlation")
    rows = WorkspacesIndexer(tmp_workspaces).list_workspaces()
    assert rows[0]["is_correlation"] is True


def test_sorts_by_created_at_desc(tmp_workspaces):
    _make_ws(tmp_workspaces, "A", )
    _make_ws(tmp_workspaces, "B")
    # B 的新 session 已写；用覆盖法给 A 更早
    (tmp_workspaces / "A" / "session.json").write_text(json.dumps(
        {"status": "completed", "scan_type": "whitebox",
         "created_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-01T00:05:00Z"}))
    names = [r["name"] for r in WorkspacesIndexer(tmp_workspaces).list_workspaces()]
    assert names[0] == "B"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web && uv run pytest tests/test_workspaces_indexer.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 indexer**

```python
# packages/web/src/shannon_web/components/__init__.py
```

```python
# packages/web/src/shannon_web/components/workspaces_indexer.py
from __future__ import annotations

import os
from pathlib import Path

from shannon_core.session import SessionManager
from shannon_core.workspace import get_workspace_vuln_counts


class WorkspacesIndexer:
    def __init__(self, workspaces_dir: Path) -> None:
        self._dir = Path(workspaces_dir)
        self._active_pids: dict[str, int] = {}

    def set_active_pid(self, ws: str, pid: int | None) -> None:
        if pid is None:
            self._active_pids.pop(ws, None)
        else:
            self._active_pids[ws] = pid

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:
            return False

    def _status_of(self, ws_name: str, session_status: str | None) -> str:
        if session_status == "completed":
            return "completed"
        if session_status == "failed":
            return "failed"
        pid = self._active_pids.get(ws_name)
        alive = pid is not None and self._pid_alive(pid)
        if alive:
            return "running"
        if session_status == "running":
            return "running" if alive else "interrupted"
        return "interrupted"  # 无 scan_end 且 pid 不在 = 未正常结束

    def list_workspaces(self) -> list[dict]:
        mgr = SessionManager(self._dir)
        out: list[dict] = []
        for ws_path in mgr.list_workspaces():
            name = ws_path.name
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
        out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return out
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web && uv run pytest tests/test_workspaces_indexer.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add packages/web/src/shannon_web/components/__init__.py \
        packages/web/src/shannon_web/components/workspaces_indexer.py \
        packages/web/tests/test_workspaces_indexer.py
git commit -m "feat(web): add WorkspacesIndexer (status 4-state + vuln counts, legacy session.json)"
```

---

## Task 5: DeliverablesReader（md/json/log 读取，新旧布局）

读 `deliverables/`——新分轨 `deliverables/{whitebox|blackbox}/*`、旧平铺 `deliverables/*`，用 `resolve_track_deliverable()` 回退。

**Files:**
- Create: `packages/web/src/shannon_web/components/deliverables_reader.py`
- Test: `packages/web/tests/test_deliverables_reader.py`

**Interfaces:**
- Consumes: `shannon_core.utils.paths.resolve_track_deliverable(deliverables_dir, track, filename)`（+ 常量 `WHITEBOX_SUBDIR`/`BLACKBOX_SUBDIR`）、`shannon_core.workspace.compute_deliverables_summary(workspace_path) -> {"vuln_queues":[...], "reports":[...]}`
- Produces: `DeliverablesReader(workspace_path)`，`.summary() -> dict`、`.read(filename, track="whitebox") -> dict|str`、`.read_log(name="workflow.log") -> str`

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_deliverables_reader.py
import json

import pytest

from shannon_web.components.deliverables_reader import DeliverablesReader


def test_read_json_new_layout(tmp_path):
    ws = tmp_path / "ws"
    dl = ws / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    (dl / "xss_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": [1]}))
    assert DeliverablesReader(ws).read("xss_exploitation_queue.json") == {"vulnerabilities": [1]}


def test_read_md_legacy_flat_layout(tmp_path):
    ws = tmp_path / "ws"
    dl = ws / "deliverables"
    dl.mkdir()
    (dl / "report.md").write_text("# hi")
    assert DeliverablesReader(ws).read("report.md") == "# hi"


def test_empty_json_returns_empty_list(tmp_path):
    ws = tmp_path / "ws"
    dl = ws / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    (dl / "attack_chains.json").write_text("[]")
    assert DeliverablesReader(ws).read("attack_chains.json") == []


def test_summary(tmp_path):
    ws = tmp_path / "ws"
    dl = ws / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    (dl / "xss_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": [{}]}))
    (dl / "report.md").write_text("# r")
    s = DeliverablesReader(ws).summary()
    assert "xss" in s["vuln_queues"]
    assert "report.md" in s["reports"]


def test_missing_raises(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(FileNotFoundError):
        DeliverablesReader(ws).read("nope.md")
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web && uv run pytest tests/test_deliverables_reader.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 reader**

```python
# packages/web/src/shannon_web/components/deliverables_reader.py
from __future__ import annotations

import json
from pathlib import Path

from shannon_core.utils.paths import WHITEBOX_SUBDIR, resolve_track_deliverable
from shannon_core.workspace import compute_deliverables_summary


class DeliverablesReader:
    def __init__(self, workspace_path: Path) -> None:
        self._ws = Path(workspace_path)
        self._deliverables = self._ws / "deliverables"

    def summary(self) -> dict:
        return compute_deliverables_summary(self._ws)

    def read(self, filename: str, track: str = WHITEBOX_SUBDIR) -> dict | list | str:
        p = resolve_track_deliverable(self._deliverables, track, filename)
        if not p.exists():
            raise FileNotFoundError(filename)
        text = p.read_text("utf-8")
        if p.suffix == ".json":
            return json.loads(text) if text.strip() else []
        return text

    def read_log(self, name: str = "workflow.log") -> str:
        p = self._ws / name
        if not p.exists():
            p = self._ws / "agents" / name  # 兼容 agents/*.log
        if not p.exists():
            raise FileNotFoundError(name)
        return p.read_text("utf-8")
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web && uv run pytest tests/test_deliverables_reader.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add packages/web/src/shannon_web/components/deliverables_reader.py \
        packages/web/tests/test_deliverables_reader.py
git commit -m "feat(web): add DeliverablesReader (new/legacy layout, md/json/log)"
```

---

## Task 6: MultiRepoConfigStore（联动 yaml CRUD + 强校验）

管 `configs/web-multi-*.yaml`：list/read/write/write_temp，写前用 `parse_multi_repo_config` 强校验，失败抛 Pydantic `ValidationError`（API 层 catch 返 422）。

**Files:**
- Create: `packages/web/src/shannon_web/components/multi_repo_config_store.py`
- Test: `packages/web/tests/test_multi_repo_config_store.py`

**Interfaces:**
- Consumes: `shannon_core.config.parser.parse_multi_repo_config(path)`
- Produces: `MultiRepoConfigStore(configs_dir)`，`.list_configs() -> list[str]`、`.read(name) -> str`、`.validate(content) -> None`（抛 ValidationError）、`.write(name, content) -> Path`、`.write_temp(content) -> Path`

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_multi_repo_config_store.py
import pytest
from pydantic import ValidationError

from shannon_web.components.multi_repo_config_store import MultiRepoConfigStore

_VALID = """\
repos:
  svc-a:
    path: /code/a
    role: backend
relations:
  - from: svc-a
    to: svc-b
correlation:
  out_workspace: cor-out
"""


def test_write_read_list(tmp_path):
    store = MultiRepoConfigStore(tmp_path)
    store.write("demo", _VALID)
    assert "demo" in store.list_configs()
    assert "repos" in store.read("demo")


def test_invalid_yaml_raises_validation_error(tmp_path):
    store = MultiRepoConfigStore(tmp_path)
    with pytest.raises(ValidationError):
        store.write("bad", "repos: not-a-mapping\n")


def test_temp_write_validates_and_persists(tmp_path):
    store = MultiRepoConfigStore(tmp_path)
    p = store.write_temp(_VALID)
    assert p.exists()
    assert "tmp-" in p.stem


def test_path_traversal_rejected(tmp_path):
    store = MultiRepoConfigStore(tmp_path)
    with pytest.raises(ValueError):
        store.read("../etc")
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web && uv run pytest tests/test_multi_repo_config_store.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 store**

```python
# packages/web/src/shannon_web/components/multi_repo_config_store.py
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from shannon_core.config.parser import parse_multi_repo_config


class MultiRepoConfigStore:
    PREFIX = "web-multi-"

    def __init__(self, configs_dir: Path) -> None:
        self._dir = Path(configs_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def list_configs(self) -> list[str]:
        return sorted(
            p.stem[len(self.PREFIX):]
            for p in self._dir.glob(f"{self.PREFIX}*.yaml")
        )

    def read(self, name: str) -> str:
        p = self._path(name)
        if not p.exists():
            raise FileNotFoundError(name)
        return p.read_text("utf-8")

    def validate(self, content: str) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(content)
            tmp = f.name
        try:
            parse_multi_repo_config(tmp)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def write(self, name: str, content: str) -> Path:
        self.validate(content)  # ValidationError 向上抛
        p = self._path(name)
        p.write_text(content, "utf-8")
        return p

    def write_temp(self, content: str) -> Path:
        self.validate(content)
        p = self._dir / f"{self.PREFIX}tmp-{int(time.time())}.yaml"
        p.write_text(content, "utf-8")
        return p

    def _path(self, name: str) -> Path:
        if "/" in name or ".." in name or name == "":
            raise ValueError("invalid config name")
        return self._dir / f"{self.PREFIX}{name}.yaml"
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web && uv run pytest tests/test_multi_repo_config_store.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add packages/web/src/shannon_web/components/multi_repo_config_store.py \
        packages/web/tests/test_multi_repo_config_store.py
git commit -m "feat(web): add MultiRepoConfigStore (yaml CRUD + pydantic validation)"
```

---

## Task 7: GitFetcher（git URL clone + 凭证注入 + branch/commit + 脱敏）

裸 URL 注入 `https://${user}:${token}@` clone 到 `repos/<name>/`；支持 branch/commit checkout；stderr 脱敏 token；重复 clone 策略（pull 失败 fallback 重 clone；force_reclone 跳过 pull）。

**Files:**
- Create: `packages/web/src/shannon_web/components/git_fetcher.py`
- Test: `packages/web/tests/test_git_fetcher.py`

**Interfaces:**
- Consumes: env `GITLAB_USER`/`GITLAB_TOKEN`
- Produces: `GitFetcher(repos_dir, gitlab_user, gitlab_token)`，`.available() -> bool`、`.fetch(url, branch=None, commit=None, force_reclone=False) -> Path`（不可用抛 `PermissionError`、clone/checkout 失败抛 `RuntimeError(脱敏消息)`）、静态 `.repo_name(url)`、`.redact(text)`

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_git_fetcher.py
import pytest

from shannon_web.components.git_fetcher import GitFetcher


def test_repo_name_strips_git():
    assert GitFetcher.repo_name("https://gitlab.com/g/foo.git") == "foo"


def test_redact_hides_token():
    assert "secret" not in GitFetcher.redact("https://user:secret@gitlab.com/x")


def test_inject_auth():
    f = GitFetcher("/tmp/x", "u", "t")
    assert f._inject_auth("https://gitlab.com/g.git") == "https://u:t@gitlab.com/g.git"


def test_available_flag():
    assert GitFetcher("/x", None, None).available() is False
    assert GitFetcher("/x", "u", "t").available() is True


@pytest.mark.asyncio
async def test_missing_creds_raises(tmp_path):
    f = GitFetcher(tmp_path, None, None)
    with pytest.raises(PermissionError):
        await f.fetch("https://gitlab.com/g.git")


@pytest.mark.asyncio
async def test_clone_command_with_branch(monkeypatch, tmp_path):
    f = GitFetcher(tmp_path, "u", "t")
    calls: list[list[str]] = []

    async def fake_run(args, cwd=None):
        calls.append(list(args))
        if args[:2] == ["git", "clone"]:
            (tmp_path / "foo").mkdir(exist_ok=True)  # 模拟 clone 建目录
        return 0, "", ""

    monkeypatch.setattr(f, "_run", fake_run)
    await f.fetch("https://gitlab.com/g/foo.git", branch="dev")
    clone = next(c for c in calls if c[:2] == ["git", "clone"])
    assert "--branch" in clone and "dev" in clone


@pytest.mark.asyncio
async def test_force_reclone_triggers_clone(monkeypatch, tmp_path):
    f = GitFetcher(tmp_path, "u", "t")
    target = tmp_path / "foo"
    target.mkdir()
    (target / "dirty").write_text("x")
    cloned: list[bool] = []

    async def fake_run(args, cwd=None):
        if args[:2] == ["git", "clone"]:
            cloned.append(True)
            target.mkdir(exist_ok=True)
        return 0, "", ""

    monkeypatch.setattr(f, "_run", fake_run)
    await f.fetch("https://gitlab.com/g/foo.git", force_reclone=True)
    assert cloned  # force_reclone 删后走了 clone 路径


@pytest.mark.asyncio
async def test_checkout_after_clone_when_commit(monkeypatch, tmp_path):
    f = GitFetcher(tmp_path, "u", "t")
    seq: list[list[str]] = []

    async def fake_run(args, cwd=None):
        seq.append(list(args))
        if args[:2] == ["git", "clone"]:
            (tmp_path / "foo").mkdir(exist_ok=True)
        return 0, "", ""

    monkeypatch.setattr(f, "_run", fake_run)
    await f.fetch("https://gitlab.com/g/foo.git", commit="abc123")
    assert any(s[:2] == ["git", "fetch", "--all"] for s in seq)
    assert any(s[:2] == ["git", "checkout"] and "abc123" in s for s in seq)


@pytest.mark.asyncio
async def test_clone_failure_redacts_token(monkeypatch, tmp_path):
    f = GitFetcher(tmp_path, "u", "t")
    async def fake_run(args, cwd=None):
        return 128, "", "fatal: https://u:secret@gitlab.com/x"
    monkeypatch.setattr(f, "_run", fake_run)
    with pytest.raises(RuntimeError) as ei:
        await f.fetch("https://gitlab.com/x.git")
    assert "secret" not in str(ei.value)
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web && uv run pytest tests/test_git_fetcher.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 fetcher**

```python
# packages/web/src/shannon_web/components/git_fetcher.py
from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

_TOKEN_RE = re.compile(r"https?://[^/]+:[^@]+@")


class GitFetcher:
    def __init__(self, repos_dir: Path, gitlab_user: str | None, gitlab_token: str | None) -> None:
        self._dir = Path(repos_dir)
        self._user = gitlab_user
        self._token = gitlab_token

    def available(self) -> bool:
        return bool(self._user and self._token)

    @staticmethod
    def repo_name(url: str) -> str:
        last = url.rstrip("/").split("/")[-1]
        return last[:-4] if last.endswith(".git") else last

    @staticmethod
    def redact(text: str) -> str:
        return _TOKEN_RE.sub("https://***:***@", text)

    def _inject_auth(self, url: str) -> str:
        return url.replace("https://", f"https://{self._user}:{self._token}@", 1)

    async def _run(self, args: list[str], cwd: str | Path | None = None) -> tuple[int, str, str]:
        self._dir.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    async def fetch(self, url: str, branch: str | None = None,
                    commit: str | None = None, force_reclone: bool = False) -> Path:
        if not self.available():
            raise PermissionError("GitLab credentials missing")
        name = self.repo_name(url)
        target = self._dir / name
        authed = self._inject_auth(url)

        if target.exists() and not force_reclone:
            rc, _, _ = await self._run(["git", "pull", "--ff-only"], cwd=target)
            if rc != 0:
                shutil.rmtree(target, ignore_errors=True)  # fallback 重 clone
        if force_reclone and target.exists():
            shutil.rmtree(target, ignore_errors=True)

        if not target.exists():
            cmd = ["git", "clone"]
            if branch and not commit:
                cmd += ["--branch", branch]
            cmd += [authed, str(target)]
            rc, _, err = await self._run(cmd)
            if rc != 0:
                raise RuntimeError(f"clone failed: {self.redact(err)}")

        if commit:
            await self._run(["git", "fetch", "--all"], cwd=target)
            rc, _, err = await self._run(["git", "checkout", commit], cwd=target)
            if rc != 0:
                raise RuntimeError(f"checkout failed: {self.redact(err)}")
        return target
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web && uv run pytest tests/test_git_fetcher.py -v`
Expected: PASS（9 passed）

- [ ] **Step 5: 提交**

```bash
git add packages/web/src/shannon_web/components/git_fetcher.py \
        packages/web/tests/test_git_fetcher.py
git commit -m "feat(web): add GitFetcher (auth inject + branch/commit + token redaction)"
```

## Task 8: EventTailer（tail ndjson → SSE 编码 + scan_end 关闭 + offset 续传）

`tail -f` 语义 tail `events.ndjson`，记 byte offset；读到 `scan_end` 关流；损坏行跳过计数；`Last-Event-ID`（byte offset）从断点续 tail。

**Files:**
- Create: `packages/web/src/shannon_web/components/event_tailer.py`
- Test: `packages/web/tests/test_event_tailer.py`

**Interfaces:**
- Consumes: ndjson 文件路径
- Produces: `EventTailer(path)`，`.offset -> int`、`.corrupt_count -> int`、`async tail(on_event, last_event_id=None, idle_timeout=300)`（`on_event(data: dict, event_id: int) -> Awaitable`）、静态 `encode_sse(data, event_id=None) -> str`

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_event_tailer.py
import asyncio
import json

import pytest

from shannon_web.components.event_tailer import EventTailer


def _line(d):
    return json.dumps(d, ensure_ascii=False)


@pytest.mark.asyncio
async def test_tails_until_scan_end(tmp_path):
    f = tmp_path / "e.ndjson"
    f.write_text(
        _line({"type": "InfoEvent", "ts": "t1", "category": "INFO", "message": "a"}) + "\n"
        + _line({"type": "scan_end", "ts": "t2", "category": "CONTROL", "status": "completed"}) + "\n"
    )
    t = EventTailer(f)
    seen: list[dict] = []

    async def cb(d, eid):
        seen.append(d)

    await t.tail(cb)
    assert seen[-1]["type"] == "scan_end"
    assert seen[0]["message"] == "a"


@pytest.mark.asyncio
async def test_corrupt_line_skipped(tmp_path):
    f = tmp_path / "e.ndjson"
    f.write_text(
        "not-json\n"
        + _line({"type": "scan_end", "ts": "t", "category": "CONTROL", "status": "completed"}) + "\n"
    )
    t = EventTailer(f)
    await t.tail(lambda d, eid: asyncio.sleep(0))
    assert t.corrupt_count == 1


@pytest.mark.asyncio
async def test_continues_from_last_event_id(tmp_path):
    f = tmp_path / "e.ndjson"
    first = _line({"type": "InfoEvent", "ts": "t1", "category": "INFO", "message": "old"}) + "\n"
    f.write_text(first)
    offset_after_first = len(first.encode())
    f.write_text(_line({"type": "scan_end", "ts": "t2", "category": "CONTROL", "status": "completed"}) + "\n")
    t = EventTailer(f)
    seen: list[dict] = []

    async def cb(d, eid):
        seen.append(d)

    await t.tail(cb, last_event_id=offset_after_first)  # 跳过第一条
    assert all(d.get("message") != "old" for d in seen)


@pytest.mark.asyncio
async def test_append_during_tail(tmp_path):
    f = tmp_path / "e.ndjson"
    f.write_text(_line({"type": "InfoEvent", "ts": "t1", "category": "INFO", "message": "x"}) + "\n")
    t = EventTailer(f)
    seen: list[dict] = []

    async def cb(d, eid):
        seen.append(d)

    async def append_later():
        await asyncio.sleep(0.3)
        with open(f, "a") as fh:
            fh.write(_line({"type": "scan_end", "ts": "t2", "category": "CONTROL", "status": "completed"}) + "\n")

    asyncio.create_task(append_later())
    await asyncio.wait_for(t.tail(cb), timeout=5)
    assert any(d["type"] == "scan_end" for d in seen)


def test_encode_sse_format():
    out = EventTailer.encode_sse({"a": 1}, event_id=42)
    assert out.startswith("id: 42\n")
    assert "data: " in out
    assert out.endswith("\n\n")
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web && uv run pytest tests/test_event_tailer.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 tailer**

```python
# packages/web/src/shannon_web/components/event_tailer.py
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Awaitable, Callable

import aiofiles

OnEvent = Callable[[dict, int], Awaitable[None]]


class EventTailer:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._offset = 0
        self._carry = ""
        self.corrupt_count = 0

    @property
    def offset(self) -> int:
        return self._offset

    @staticmethod
    def encode_sse(data: dict, event_id: int | None = None) -> str:
        body = "data: " + json.dumps(data, ensure_ascii=False) + "\n"
        if event_id is not None:
            body = f"id: {event_id}\n" + body
        return body + "\n"  # 空行 = SSE 事件分隔

    async def tail(self, on_event: OnEvent, last_event_id: int | None = None,
                   idle_timeout: float = 300.0) -> None:
        if last_event_id is not None:
            self._offset = last_event_id
        waited = 0.0
        while not self._path.exists():  # 等文件出现
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
                self._carry = lines.pop()  # 末尾可能不完整，留存
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
                    if data.get("type") == "scan_end":
                        closed = True
                        break
            else:
                await asyncio.sleep(0.1)
        self._carry = ""
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web && uv run pytest tests/test_event_tailer.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add packages/web/src/shannon_web/components/event_tailer.py \
        packages/web/tests/test_event_tailer.py
git commit -m "feat(web): add EventTailer (ndjson tail-f + SSE encode + offset resume)"
```

---

## Task 9: ScanManager（subprocess 三扫描统一 + 并发/超时/取消/崩溃兜底）

核心：`asyncio.create_subprocess_exec` 起 CLI（env 注入 `SHANNON_WEB_EVENT_FILE`）；并发计数限流；SIGINT 取消；wall-clock 超时；子进程退出后若无 `scan_end` 补写 `killed|crashed`（含 stderr_tail）；stdout/stderr 异步 drain 防管道阻塞。

**Files:**
- Create: `packages/web/src/shannon_web/components/scan_manager.py`
- Test: `packages/web/tests/test_scan_manager.py`

**Interfaces:**
- Consumes: `ScanRequest`/`PathSource`/`GitSource`（任务 3）、`GitFetcher`（任务 7，git 模式）、`MultiRepoConfigStore`（任务 6，correlation 模式）
- Produces:
  - `class TemporalUnavailable(Exception)`、`class TooManyScans(Exception)`
  - `ScanManager(workspaces_dir, repos_dir, config_store, git_fetcher=None, max_concurrent=1, scan_timeout=0.0)`
  - `async start(req: ScanRequest) -> str`（返 workspace 名）；`async cancel(ws) -> bool`；`async reap_zombies()`（lifespan 调）；`active_pids() -> dict[str,int]`（供 WorkspacesIndexer）
  - 钩子 `_build_argv(req, target, ws, yaml_path=None) -> list[str]`（测试 monkeypatch 注入 mock CLI）、`async _check_temporal()`（测可 monkeypatch）

- [ ] **Step 1: 写 mock CLI fixture + 失败测试**

```python
# packages/web/tests/test_scan_manager.py
import asyncio
import sys
import textwrap

import pytest

from shannon_web.models import PathSource, ScanRequest
from shannon_web.components.scan_manager import ScanManager, TemporalUnavailable, TooManyScans


@pytest.fixture
def fake_ok(tmp_path):
    s = tmp_path / "ok.py"
    s.write_text(textwrap.dedent('''
        import os, sys
        ef = os.environ.get("SHANNON_WEB_EVENT_FILE")
        if ef:
            with open(ef, "a") as f:
                f.write(\'{"type":"InfoEvent","ts":"t","category":"INFO","message":"x"}\\n\')
                f.write(\'{"type":"scan_end","ts":"t","category":"CONTROL","status":"completed"}\\n\')
    '''))
    return s


@pytest.fixture
def fake_crash(tmp_path):
    s = tmp_path / "crash.py"
    s.write_text('import sys; sys.stderr.write("boom\\n"); sys.exit(1)\n')
    return s


@pytest.fixture
def fake_long(tmp_path):
    s = tmp_path / "long.py"
    s.write_text('import time; time.sleep(30)\n')
    return s


async def _ok():
    return None


def _patch_ok(monkeypatch, mgr):
    monkeypatch.setattr(mgr, "_check_temporal", _ok)


@pytest.mark.asyncio
async def test_start_writes_event_file_and_scan_end(tmp_workspaces, fake_ok, monkeypatch):
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "repos", None, max_concurrent=2)
    _patch_ok(monkeypatch, mgr)
    monkeypatch.setattr(mgr, "_build_argv",
                        lambda req, t, ws, yaml=None: [sys.executable, str(fake_ok)])
    ws = await mgr.start(ScanRequest(type="whitebox",
                                     source=PathSource(kind="path", value="/code/x"),
                                     url="http://e", workspace="WS1"))
    assert ws == "WS1"
    await asyncio.sleep(0.6)
    ef = tmp_workspaces / "WS1" / "events.ndjson"
    lines = [l for l in ef.read_text().splitlines() if l.strip()]
    assert any('"scan_end"' in l and '"completed"' in l for l in lines)
    assert "WS1" not in mgr.active_pids()  # 退出后清出


@pytest.mark.asyncio
async def test_crash_writes_scan_end_crashed_with_stderr(tmp_workspaces, fake_crash, monkeypatch):
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "repos", None, max_concurrent=2)
    _patch_ok(monkeypatch, mgr)
    monkeypatch.setattr(mgr, "_build_argv",
                        lambda req, t, ws, yaml=None: [sys.executable, str(fake_crash)])
    await mgr.start(ScanRequest(type="whitebox",
                                source=PathSource(kind="path", value="/x"),
                                url="u", workspace="WC"))
    await asyncio.sleep(0.6)
    text = (tmp_workspaces / "WC" / "events.ndjson").read_text()
    assert '"scan_end"' in text and '"crashed"' in text
    assert "boom" in text  # stderr_tail 透传


@pytest.mark.asyncio
async def test_concurrency_limit_raises(tmp_workspaces, monkeypatch):
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "r", None, max_concurrent=1)
    _patch_ok(monkeypatch, mgr)
    mgr._procs["existing"] = object()  # 占位 1 个在跑
    with pytest.raises(TooManyScans):
        await mgr.start(ScanRequest(type="whitebox",
                                    source=PathSource(kind="path", value="/x"),
                                    url="u", workspace="W2"))


@pytest.mark.asyncio
async def test_temporal_unavailable_raises(tmp_workspaces, monkeypatch):
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "r", None)

    async def _fail():
        raise TemporalUnavailable()

    monkeypatch.setattr(mgr, "_check_temporal", _fail)
    with pytest.raises(TemporalUnavailable):
        await mgr.start(ScanRequest(type="whitebox",
                                    source=PathSource(kind="path", value="/x"),
                                    url="u", workspace="W"))


@pytest.mark.asyncio
async def test_cancel_sends_sigint_then_killed_scan_end(tmp_workspaces, fake_long, monkeypatch):
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "r", None, max_concurrent=2)
    _patch_ok(monkeypatch, mgr)
    monkeypatch.setattr(mgr, "_build_argv",
                        lambda req, t, ws, yaml=None: [sys.executable, str(fake_long)])
    ws = await mgr.start(ScanRequest(type="whitebox",
                                     source=PathSource(kind="path", value="/x"),
                                     url="u", workspace="WL"))
    ok = await mgr.cancel(ws)
    assert ok is True
    await asyncio.sleep(0.6)
    text = (tmp_workspaces / "WL" / "events.ndjson").read_text()
    assert '"killed"' in text


@pytest.mark.asyncio
async def test_correlation_resolves_yaml_and_runs(tmp_workspaces, fake_ok, monkeypatch):
    store = _MemStore()  # 见下：极简 store stub
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "r", store, max_concurrent=2)
    _patch_ok(monkeypatch, mgr)
    captured = {}
    monkeypatch.setattr(mgr, "_build_argv",
                        lambda req, t, ws, yaml=None: (captured.__setitem__("yaml", yaml),
                                                       [sys.executable, str(fake_ok)])[1])
    await mgr.start(ScanRequest(type="correlation", config_name="demo", workspace="WCO"))
    await asyncio.sleep(0.4)
    assert str(captured["yaml"]).endswith("web-multi-demo.yaml")


class _MemStore:
    """极简 store stub：write_temp/write 返回固定路径。"""
    def write(self, name, content):
        from pathlib import Path
        p = Path(f"/tmp/web-multi-{name}.yaml")
        return p

    def write_temp(self, content):
        from pathlib import Path
        return Path("/tmp/web-multi-tmp-1.yaml")
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web && uv run pytest tests/test_scan_manager.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 ScanManager**

```python
# packages/web/src/shannon_web/components/scan_manager.py
from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles

from shannon_web.models import ScanRequest


class TemporalUnavailable(Exception):
    pass


class TooManyScans(Exception):
    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"已有扫描在跑（并发上限 {limit}）")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScanManager:
    def __init__(self, workspaces_dir: Path, repos_dir: Path, config_store: Any,
                 git_fetcher: Any = None, max_concurrent: int = 1,
                 scan_timeout: float = 0.0) -> None:
        self._workspaces_dir = Path(workspaces_dir)
        self._repos_dir = Path(repos_dir)
        self._config_store = config_store
        self._git = git_fetcher
        self._max_concurrent = max(1, max_concurrent)
        self._scan_timeout = scan_timeout
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ---- 公共 API ----
    def active_pids(self) -> dict[str, int]:
        return {ws: p.pid for ws, p in self._procs.items() if p.returncode is None}

    async def reap_zombies(self) -> None:
        """lifespan 启动时扫无主子进程（本会话 _procs 外的）。v1: 无操作占位，
        真正无主 ws 由 WorkspacesIndexer 判 interrupted 呈现。"""
        return None

    async def start(self, req: ScanRequest) -> str:
        await self._check_temporal()
        if len(self._procs) >= self._max_concurrent:
            raise TooManyScans(self._max_concurrent)
        ws = req.workspace or self._gen_ws_name(req)
        ws_dir = self._workspaces_dir / ws
        ws_dir.mkdir(parents=True, exist_ok=True)
        event_file = ws_dir / "events.ndjson"

        target, yaml_path = await self._resolve_inputs(req)

        argv = self._build_argv(req, target, ws, yaml_path)
        env = {**os.environ, "SHANNON_WEB_EVENT_FILE": str(event_file)}
        proc = await asyncio.create_subprocess_exec(
            *argv, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        self._procs[ws] = proc
        self._tasks[ws] = asyncio.create_task(self._watch(ws, proc, event_file))
        return ws

    async def cancel(self, ws: str) -> bool:
        proc = self._procs.get(ws)
        if proc is None:
            return False
        try:
            proc.send_signal(signal.SIGINT)
        except ProcessLookupError:
            pass
        return True

    # ---- 钩子（可 monkeypatch）----
    def _build_argv(self, req: ScanRequest, target: str | None,
                    ws: str, yaml_path: Path | None = None) -> list[str]:
        if req.type == "whitebox":
            return ["shannon-whitebox", "start", "-r", target or "", "--url", req.url or "", "-w", ws]
        if req.type == "blackbox":
            cmd = ["shannon-blackbox", "start", "--url", req.url or "", "--repo", target or "", "-w", ws]
            if req.reuse_latest:
                cmd.append("--latest")
            return cmd
        if req.type == "correlation":
            return ["shannon-multi", "start", "-c", str(yaml_path)]
        raise ValueError(f"unknown scan type: {req.type}")

    async def _check_temporal(self) -> None:
        import socket

        def _probe() -> bool:
            try:
                with socket.create_connection(("localhost", 7233), timeout=1.0):
                    return True
            except OSError:
                return False

        loop = asyncio.get_running_loop()
        if not await loop.run_in_executor(None, _probe):
            raise TemporalUnavailable()

    # ---- 内部 ----
    async def _resolve_inputs(self, req: ScanRequest) -> tuple[str | None, Path | None]:
        target: str | None = None
        yaml_path: Path | None = None
        if req.source is not None:
            if req.source.kind == "git":
                if self._git is None or not self._git.available():
                    raise PermissionError("git 模式不可用：缺少 GitLab 凭证")
                p = await self._git.fetch(req.source.value, req.source.branch,
                                          req.source.commit, req.source.force_reclone)
                target = str(p)
            else:
                target = req.source.value
        if req.type == "correlation":
            yaml_path = await self._resolve_correlation_yaml(req)
        return target, yaml_path

    async def _resolve_correlation_yaml(self, req: ScanRequest) -> Path:
        assert self._config_store is not None, "correlation 需 config_store"
        if req.config_name:
            return self._config_store_dir() / f"web-multi-{req.config_name}.yaml"
        if req.config_content:
            if req.save_as:
                return self._config_store.write(req.save_as, req.config_content)
            return self._config_store.write_temp(req.config_content)
        raise ValueError("correlation 扫描需 config_name 或 config_content")

    def _config_store_dir(self) -> Path:
        # MultiRepoConfigStore 写入目录（list_configs 同源）
        return Path(getattr(self._config_store, "_dir", "configs"))

    def _gen_ws_name(self, req: ScanRequest) -> str:
        base = "scan"
        if req.source:
            base = Path(req.source.value).stem or "scan"
        elif req.config_name:
            base = req.config_name
        return f"{base}_{int(time.time())}"

    async def _watch(self, ws: str, proc: asyncio.subprocess.Process, event_file: Path) -> None:
        stderr_tail = bytearray()

        def stderr_sink(line: bytes) -> None:
            stderr_tail.extend(line)
            if len(stderr_tail) > 2048:
                del stderr_tail[:len(stderr_tail) - 2048]

        async def drain(stream, sink=None):
            while True:
                line = await stream.readline()
                if not line:
                    break
                if sink is not None:
                    sink(line)

        s_out = asyncio.create_task(drain(proc.stdout))
        s_err = asyncio.create_task(drain(proc.stderr, stderr_sink))
        try:
            rc = await proc.wait()
        finally:
            await asyncio.gather(s_out, s_err, return_exceptions=True)

        if not self._has_scan_end(event_file):
            status = "killed" if (rc is not None and rc < 0) else "crashed"
            tail_text = bytes(stderr_tail[-2048:]).decode("utf-8", "replace")
            await self._write_scan_end(event_file, status, rc if rc is not None else -1, tail_text)
        self._procs.pop(ws, None)
        self._tasks.pop(ws, None)

    @staticmethod
    def _has_scan_end(event_file: Path) -> bool:
        if not event_file.exists():
            return False
        for line in event_file.read_text("utf-8", errors="replace").splitlines()[-5:]:
            try:
                if json.loads(line).get("type") == "scan_end":
                    return True
            except json.JSONDecodeError:
                continue
        return False

    async def _write_scan_end(self, event_file: Path, status: str,
                              returncode: int, stderr_tail: str) -> None:
        payload = {
            "ts": _now_iso(), "category": "CONTROL", "type": "scan_end",
            "status": status, "returncode": returncode, "stderr_tail": stderr_tail,
        }
        async with aiofiles.open(event_file, "a") as fh:
            await fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web && uv run pytest tests/test_scan_manager.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add packages/web/src/shannon_web/components/scan_manager.py \
        packages/web/tests/test_scan_manager.py
git commit -m "feat(web): add ScanManager (unified subprocess + concurrency/cancel/crash scan_end)"
```

## Task 10: REST API（workspaces / scan / deliverables / multi_configs）

建 4 个 router + 改写 `create_app` 持有单例（indexer/scan_manager/config_store），支持 `overrides` 注入 mock 便于测试。错误码：Temporal 不通→400、并发超限→409、yaml 校验失败→422、git 不可用→400、不存在→404。

**Files:**
- Create: `packages/web/src/shannon_web/api/__init__.py`, `workspaces.py`, `scan.py`, `multi_configs.py`
- Modify: `packages/web/src/shannon_web/app.py`（`create_app` 持有单例 + 注册 router + overrides 参数）
- Modify: `packages/web/src/shannon_web/components/workspaces_indexer.py`（加 `sync_active(pids)`）
- Modify: `packages/web/tests/conftest.py`（加 `_reset_config` autouse 清 `get_config` 缓存）
- Test: `packages/web/tests/test_api_workspaces.py`, `test_api_scan.py`, `test_api_multi_configs.py`

**Interfaces:**
- Consumes: 任务 3-9 全部组件
- Produces: REST 端点（见 spec「API」节）+ `create_app(overrides={"scan_manager": ...})`

- [ ] **Step 1: 给 WorkspacesIndexer 加 `sync_active`**

在 `workspaces_indexer.py` 的 `WorkspacesIndexer` 类里加方法（紧挨 `set_active_pid`）：

```python
    def sync_active(self, pids: dict[str, int]) -> None:
        """ScanManager 每次 list 时注入当前在跑 pid 表（替换式，避免 stale）。"""
        self._active_pids = dict(pids)
```

- [ ] **Step 2: 建 api/__init__.py 与 workspaces.py**

```python
# packages/web/src/shannon_web/api/__init__.py
```

```python
# packages/web/src/shannon_web/api/workspaces.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


def _workspace_path(request: Request, ws: str):
    p = request.app.state.config.workspaces_dir / ws
    if not p.exists():
        raise HTTPException(404, "workspace not found")
    return p


@router.get("")
async def list_workspaces(request: Request):
    idx = request.app.state.indexer
    idx.sync_active(request.app.state.scan_manager.active_pids())
    return idx.list_workspaces()


@router.get("/{ws}")
async def get_workspace(ws: str, request: Request):
    idx = request.app.state.indexer
    idx.sync_active(request.app.state.scan_manager.active_pids())
    for row in idx.list_workspaces():
        if row["name"] == ws:
            return row
    raise HTTPException(404, "workspace not found")


@router.get("/{ws}/deliverables")
async def deliverables_summary(ws: str, request: Request):
    from shannon_web.components.deliverables_reader import DeliverablesReader
    return DeliverablesReader(_workspace_path(request, ws)).summary()


@router.get("/{ws}/deliverables/{filename}")
async def deliverables_file(ws: str, filename: str, request: Request, track: str = "whitebox"):
    from shannon_web.components.deliverables_reader import DeliverablesReader
    try:
        return DeliverablesReader(_workspace_path(request, ws)).read(filename, track)
    except FileNotFoundError:
        raise HTTPException(404, "file not found")


@router.get("/{ws}/report")
async def report(ws: str, request: Request):
    from shannon_web.components.deliverables_reader import DeliverablesReader
    reader = DeliverablesReader(_workspace_path(request, ws))
    reports = reader.summary().get("reports", [])
    chosen = next((x for x in reports if "comprehensive" in x.lower()), reports[0] if reports else None)
    if not chosen:
        raise HTTPException(404, "no report")
    return reader.read(chosen)


@router.get("/{ws}/logs")
async def logs(ws: str, request: Request, name: str = "workflow.log"):
    from shannon_web.components.deliverables_reader import DeliverablesReader
    try:
        return DeliverablesReader(_workspace_path(request, ws)).read_log(name)
    except FileNotFoundError:
        raise HTTPException(404, "log not found")
```

- [ ] **Step 3: 建 scan.py + multi_configs.py**

```python
# packages/web/src/shannon_web/api/scan.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from shannon_web.components.scan_manager import TemporalUnavailable, TooManyScans
from shannon_web.models import ScanAccepted, ScanRequest

router = APIRouter(prefix="/api/scan", tags=["scan"])


@router.post("", response_model=ScanAccepted, status_code=202)
async def create_scan(req: ScanRequest, request: Request):
    sm = request.app.state.scan_manager
    try:
        ws = await sm.start(req)
    except TemporalUnavailable:
        raise HTTPException(400, "Temporal 服务未运行，请先 docker-compose up -d")
    except TooManyScans as e:
        raise HTTPException(409, f"已有扫描在跑，并发上限 {e.limit}")
    except PermissionError as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    from pydantic import ValidationError
    except ValidationError as e:  # correlation yaml 校验失败
        raise HTTPException(422, detail=e.errors())
    return ScanAccepted(workspace=ws)


@router.delete("/{ws}")
async def cancel_scan(ws: str, request: Request):
    ok = await request.app.state.scan_manager.cancel(ws)
    if not ok:
        raise HTTPException(404, "scan not found")
    return {"cancelled": ws}
```

```python
# packages/web/src/shannon_web/api/multi_configs.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError

router = APIRouter(prefix="/api/multi-configs", tags=["multi-configs"])


class CreateBody(BaseModel):
    name: str
    content: str


@router.get("")
async def list_configs(request: Request):
    return request.app.state.config_store.list_configs()


@router.post("", status_code=201)
async def create_config(body: CreateBody, request: Request):
    try:
        request.app.state.config_store.write(body.name, body.content)
    except ValidationError as e:
        raise HTTPException(422, detail=e.errors())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"name": body.name}


@router.get("/{name}")
async def get_config(name: str, request: Request):
    try:
        return {"name": name, "content": request.app.state.config_store.read(name)}
    except FileNotFoundError:
        raise HTTPException(404, "config not found")
```

- [ ] **Step 4: 改写 app.py 的 create_app（持单例 + overrides + 注册 router）**

把 Task 3 的 `create_app` 替换为：

```python
def create_app(overrides: dict | None = None) -> FastAPI:
    app = FastAPI(title="Shannon Web", version="0.1.0", lifespan=lifespan)
    cfg = get_config()
    app.state.config = cfg

    from .components.workspaces_indexer import WorkspacesIndexer
    from .components.git_fetcher import GitFetcher
    from .components.multi_repo_config_store import MultiRepoConfigStore
    from .components.scan_manager import ScanManager
    from .api import workspaces, scan, multi_configs

    app.state.indexer = WorkspacesIndexer(cfg.workspaces_dir)
    app.state.config_store = MultiRepoConfigStore(cfg.configs_dir)
    git_fetcher = GitFetcher(cfg.repos_dir, cfg.gitlab_user, cfg.gitlab_token)
    overrides = overrides or {}
    app.state.scan_manager = overrides.get("scan_manager") or ScanManager(
        cfg.workspaces_dir, cfg.repos_dir, app.state.config_store, git_fetcher,
        max_concurrent=cfg.max_concurrent, scan_timeout=cfg.scan_timeout)

    app.include_router(workspaces.router)
    app.include_router(scan.router)
    app.include_router(multi_configs.router)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "git_available": cfg.git_available}

    return app
```

> 模块级 `app = create_app()` 保留（生产用）。`main()` 不变。

- [ ] **Step 5: conftest 加 _reset_config**

在 `packages/web/tests/conftest.py` 追加：

```python
@pytest.fixture(autouse=True)
def _reset_config():
    from shannon_web import config as cfg_mod
    cfg_mod.get_config.cache_clear()
    yield
    cfg_mod.get_config.cache_clear()


@pytest.fixture
def app_with_ws(tmp_workspaces):
    from shannon_web.app import create_app
    return create_app()
```

- [ ] **Step 6: 写 API 测试**

```python
# packages/web/tests/test_api_workspaces.py
import json

from fastapi.testclient import TestClient


def _ws(root, name, **kw):
    ws = root / name
    ws.mkdir(parents=True)
    data = {"status": "completed", "scan_type": "whitebox",
            "created_at": "2026-07-02T10:00:00Z", "completed_at": "2026-07-02T10:05:00Z"}
    data.update(kw)
    (ws / "session.json").write_text(json.dumps(data))


def test_list_and_get(app_with_ws, tmp_workspaces):
    _ws(tmp_workspaces, "A")
    client = TestClient(app_with_ws)
    r = client.get("/api/workspaces")
    assert r.status_code == 200
    assert any(w["name"] == "A" for w in r.json())
    assert client.get("/api/workspaces/A").status_code == 200
    assert client.get("/api/workspaces/nope").status_code == 404


def test_report_deliverables_logs(app_with_ws, tmp_workspaces):
    _ws(tmp_workspaces, "A")
    ws = tmp_workspaces / "A"
    dl = ws / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    (dl / "comprehensive_security_assessment_report.md").write_text("# R")
    (dl / "xss_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": [{}]}))
    client = TestClient(app_with_ws)
    assert client.get("/api/workspaces/A/report").json() == "# R"
    s = client.get("/api/workspaces/A/deliverables").json()
    assert "xss" in s["vuln_queues"]
    f = client.get("/api/workspaces/A/deliverables/xss_exploitation_queue.json")
    assert f.status_code == 200 and "vulnerabilities" in f.json()
    assert client.get("/api/workspaces/A/deliverables/missing.json").status_code == 404
```

```python
# packages/web/tests/test_api_scan.py
from fastapi.testclient import TestClient

from shannon_web.app import create_app
from shannon_web.components.scan_manager import TemporalUnavailable, TooManyScans


class FakeSM:
    def __init__(self):
        self.started = []
        self.exc = None
        self.cancelled = []

    async def start(self, req):
        if self.exc:
            raise self.exc
        self.started.append(req)
        return "WSX"

    async def cancel(self, ws):
        self.cancelled.append(ws)
        return True

    def active_pids(self):
        return {}


_BODY = {"type": "whitebox", "source": {"kind": "path", "value": "/x"}, "url": "http://e"}


def test_post_scan_202():
    fake = FakeSM()
    client = TestClient(create_app(overrides={"scan_manager": fake}))
    r = client.post("/api/scan", json=_BODY)
    assert r.status_code == 202
    assert r.json() == {"workspace": "WSX"}
    assert len(fake.started) == 1


def test_post_scan_400_temporal():
    fake = FakeSM()
    fake.exc = TemporalUnavailable()
    client = TestClient(create_app(overrides={"scan_manager": fake}))
    assert client.post("/api/scan", json=_BODY).status_code == 400


def test_post_scan_409_concurrent():
    fake = FakeSM()
    fake.exc = TooManyScans(1)
    client = TestClient(create_app(overrides={"scan_manager": fake}))
    assert client.post("/api/scan", json=_BODY).status_code == 409


def test_delete_scan():
    fake = FakeSM()
    client = TestClient(create_app(overrides={"scan_manager": fake}))
    assert client.delete("/api/scan/WSX").status_code == 200
```

```python
# packages/web/tests/test_api_multi_configs.py
from fastapi.testclient import TestClient

from shannon_web.app import create_app

_VALID = """\
repos:
  svc-a:
    path: /code/a
correlation:
  out_workspace: cor-out
"""


def test_crud(app_with_ws, tmp_workspaces):
    client = TestClient(app_with_ws)
    assert client.post("/api/multi-configs", json={"name": "demo", "content": _VALID}).status_code == 201
    assert "demo" in client.get("/api/multi-configs").json()
    got = client.get("/api/multi-configs/demo").json()
    assert got["content"] == _VALID


def test_invalid_returns_422(app_with_ws):
    client = TestClient(app_with_ws)
    r = client.post("/api/multi-configs", json={"name": "bad", "content": "repos: not-a-mapping\n"})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list)
```

- [ ] **Step 7: 跑测试验证通过**

Run: `cd packages/web && uv run pytest tests/test_api_workspaces.py tests/test_api_scan.py tests/test_api_multi_configs.py tests/test_app_health.py -v`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add packages/web/src/shannon_web/api packages/web/src/shannon_web/app.py \
        packages/web/src/shannon_web/components/workspaces_indexer.py \
        packages/web/tests/conftest.py packages/web/tests/test_api_*.py
git commit -m "feat(web): REST API (workspaces/scan/deliverables/multi-configs) + app wiring"
```

---

## Task 11: SSE API（`/api/workspaces/{ws}/events`）

`StreamingResponse(media_type="text/event-stream")` + `EventTailer`；`Last-Event-ID` header（byte offset）从断点续 tail；读 `scan_end` 后关闭流。

**Files:**
- Create: `packages/web/src/shannon_web/api/events.py`
- Modify: `packages/web/src/shannon_web/app.py`（注册 events router）
- Test: `packages/web/tests/test_api_events.py`

**Interfaces:**
- Consumes: `EventTailer`（任务 8）
- Produces: `GET /api/workspaces/{ws}/events` → `text/event-stream`

- [ ] **Step 1: 建 events.py**

```python
# packages/web/src/shannon_web/api/events.py
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from shannon_web.components.event_tailer import EventTailer

router = APIRouter(prefix="/api/workspaces", tags=["events"])


@router.get("/{ws}/events")
async def stream_events(ws: str, request: Request):
    cfg = request.app.state.config
    ws_dir = cfg.workspaces_dir / ws
    if not ws_dir.exists():
        raise HTTPException(404, "workspace not found")
    ndjson = ws_dir / "events.ndjson"

    last = request.headers.get("last-event-id")
    last_offset = int(last) if last else None

    async def gen():
        queue: asyncio.Queue = asyncio.Queue()
        tailer = EventTailer(ndjson)

        async def on_event(data: dict, event_id: int):
            await queue.put(EventTailer.encode_sse(data, event_id))
            if data.get("type") == "scan_end":
                await queue.put(None)  # sentinel：关流

        task = asyncio.create_task(tailer.tail(on_event, last_event_id=last_offset))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            task.cancel()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 2: app.py 注册 events router**

在 `app.py` 的 `create_app` 里，把 `from .api import workspaces, scan, multi_configs` 改为：

```python
    from .api import events, multi_configs, scan, workspaces
```

并在 `app.include_router(multi_configs.router)` 之后加：

```python
    app.include_router(events.router)
```

- [ ] **Step 3: 写 SSE 测试（httpx async + ASGITransport）**

```python
# packages/web/tests/test_api_events.py
import json

import httpx
import pytest


@pytest.mark.asyncio
async def test_sse_streams_until_scan_end(app_with_ws, tmp_workspaces):
    ws = tmp_workspaces / "E"
    ws.mkdir()
    ef = ws / "events.ndjson"
    ef.write_text(
        json.dumps({"type": "InfoEvent", "ts": "t1", "category": "INFO", "message": "hi"}) + "\n"
        + json.dumps({"type": "scan_end", "ts": "t2", "category": "CONTROL", "status": "completed"}) + "\n"
    )
    transport = httpx.ASGITransport(app=app_with_ws)
    lines: list[str] = []
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=5) as client:
        async with client.stream("GET", "/api/workspaces/E/events") as r:
            assert r.status_code == 200
            async for line in r.aiter_lines():
                lines.append(line)
                if line.startswith("data:") and "scan_end" in line:
                    break
    assert any("data:" in l and "hi" in l for l in lines)
    assert any("id:" in l for l in lines)  # 带事件 id（Last-Event-ID 用）


@pytest.mark.asyncio
async def test_sse_404_unknown_workspace(app_with_ws):
    transport = httpx.ASGITransport(app=app_with_ws)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/workspaces/nope/events")
        assert r.status_code == 404
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web && uv run pytest tests/test_api_events.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add packages/web/src/shannon_web/api/events.py \
        packages/web/src/shannon_web/app.py \
        packages/web/tests/test_api_events.py
git commit -m "feat(web): SSE endpoint /api/workspaces/{ws}/events (Last-Event-ID resume)"
```

## Task 12: 联动 ndjson 小增强（orchestrator 写 correlation 进度）

`packages/multi/src/shannon_multi/orchestrator.py` 在关键节点写 `correlation_progress` + `scan_end` 到 correlation workspace 的 `events.ndjson`，`asyncio.Lock` 保护 append（多 repo 顺序 + edge 并发）。**不动 orchestrator 业务逻辑，只加事件写入。**

**Files:**
- Create: `packages/multi/src/shannon_multi/correlation_event_writer.py`
- Modify: `packages/multi/src/shannon_multi/orchestrator.py`（`run_cross_repo` 关键节点插 writer 调用）
- Test: `packages/multi/tests/test_correlation_event_writer.py`

**Interfaces:**
- Consumes: correlation workspace 路径（`out_ws / "events.ndjson"`，`out_ws` = `SessionManager.create_workspace(name=config.correlation.out_workspace, scan_type="correlation")` 返回值）
- Produces: `CorrelationEventWriter(ndjson_path)`，`async repo(name, status, detail=None)` / `phase(name, status)` / `edge(name, status, detail=None)` / `scan_end(status)`

**TDD 策略：** writer 是全部新序列化逻辑 → 单测独立绿（覆盖格式 + Lock 串行 + scan_end）。orchestrator 的几行 writer 调用是 wiring，其端到端正确性由冒烟验收第 7 条（联动 yaml → SSE 流含 `correlation_progress`）验证。

- [ ] **Step 1: 写失败测试（writer 单测）**

```python
# packages/multi/tests/test_correlation_event_writer.py
import asyncio
import json

import pytest

from shannon_multi.correlation_event_writer import CorrelationEventWriter


def _rows(p):
    return [json.loads(l) for l in p.read_text("utf-8").splitlines() if l.strip()]


@pytest.mark.asyncio
async def test_repo_event_format(tmp_path):
    w = CorrelationEventWriter(tmp_path / "e.ndjson")
    await w.repo("svc-a", "started")
    r = _rows(tmp_path / "e.ndjson")[-1]
    assert r["category"] == "CONTROL"
    assert r["type"] == "correlation_progress"
    assert r["node"] == "repo" and r["name"] == "svc-a" and r["status"] == "started"
    assert "ts" in r


@pytest.mark.asyncio
async def test_phase_and_edge(tmp_path):
    w = CorrelationEventWriter(tmp_path / "e.ndjson")
    await w.phase("correlation", "started")
    await w.edge("svc-a->svc-b", "completed", detail="grpc")
    rows = _rows(tmp_path / "e.ndjson")
    assert rows[0]["node"] == "phase" and rows[0]["name"] == "correlation"
    assert rows[1]["node"] == "edge" and rows[1]["detail"] == "grpc"


@pytest.mark.asyncio
async def test_scan_end(tmp_path):
    w = CorrelationEventWriter(tmp_path / "e.ndjson")
    await w.scan_end("completed")
    r = _rows(tmp_path / "e.ndjson")[-1]
    assert r["type"] == "scan_end" and r["status"] == "completed"


@pytest.mark.asyncio
async def test_concurrent_edges_no_interleave(tmp_path):
    w = CorrelationEventWriter(tmp_path / "e.ndjson")
    await asyncio.gather(*(w.edge(f"a->b:{i}", "completed") for i in range(30)))
    rows = _rows(tmp_path / "e.ndjson")
    assert len(rows) == 30  # 每行完整可 parse = Lock 串行无交错


@pytest.mark.asyncio
async def test_creates_parent_dir(tmp_path):
    w = CorrelationEventWriter(tmp_path / "nested" / "dir" / "e.ndjson")
    await w.phase("correlation", "started")
    assert (tmp_path / "nested" / "dir" / "e.ndjson").exists()
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/multi && uv run pytest tests/test_correlation_event_writer.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 writer**

```python
# packages/multi/src/shannon_multi/correlation_event_writer.py
"""联动编排器层进度事件 writer：把 repo/phase/edge 级状态写到 correlation workspace 的 ndjson。

asyncio.Lock 保护 append（多 repo 顺序 + edge 并发需串行化）。
诚实局限：edge 内部 agent 细粒度事件不进 ndjson（AgentExecutor 不经 dispatcher）。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import aiofiles


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CorrelationEventWriter:
    def __init__(self, ndjson_path: Path) -> None:
        self._path = Path(ndjson_path)
        self._lock = asyncio.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    async def _append(self, payload: dict) -> None:
        async with self._lock:
            async with aiofiles.open(self._path, "a") as fh:
                await fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
                await fh.flush()

    async def repo(self, name: str, status: str, detail: str | None = None) -> None:
        await self._append({"ts": _now_iso(), "category": "CONTROL",
                            "type": "correlation_progress", "node": "repo",
                            "name": name, "status": status, "detail": detail})

    async def phase(self, name: str, status: str) -> None:
        await self._append({"ts": _now_iso(), "category": "CONTROL",
                            "type": "correlation_progress", "node": "phase",
                            "name": name, "status": status})

    async def edge(self, name: str, status: str, detail: str | None = None) -> None:
        await self._append({"ts": _now_iso(), "category": "CONTROL",
                            "type": "correlation_progress", "node": "edge",
                            "name": name, "status": status, "detail": detail})

    async def scan_end(self, status: str) -> None:
        await self._append({"ts": _now_iso(), "category": "CONTROL",
                            "type": "scan_end", "status": status})
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/multi && uv run pytest tests/test_correlation_event_writer.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 在 orchestrator.py 的 `run_cross_repo` 插入 writer 调用（wiring）**

定位 `packages/multi/src/shannon_multi/orchestrator.py` 的 `run_cross_repo` 函数，按下述节点插入（行号基于当前核实，实现者按实际代码定位，**不改业务逻辑只加 writer 调用**）：

```python
from shannon_multi.correlation_event_writer import CorrelationEventWriter
```

1. **创建 correlation workspace 后**（`out_ws = mgr.create_workspace(...)` 之后，约 L146-148）：
```python
        writer = CorrelationEventWriter(out_ws / "events.ndjson")
        overall_failed = False
```

2. **repo 扫描循环**（约 L98-141，遍历 `plans`）：现扫分支前后各加一行——
```python
            await writer.repo(p.service, "started")
            try:
                # ... 原现扫调用 run_whitebox(...) ...
            except Exception:
                overall_failed = True
                await writer.repo(p.service, "failed", detail="scan error")
                raise
            else:
                await writer.repo(p.service, "completed")
```
（reuse 分支也写 `await writer.repo(p.service, "completed", detail="reused")`。）

3. **关联阶段开始前**（`asyncio.gather(...)` 之前，约 L198 前）：
```python
        await writer.phase("correlation", "started")
```

4. **edge_runner closure 内**（约 L171-195，每个 edge 完成时）：
```python
            edge_status = "completed"  # 或按 edge 结果定 failed
            await writer.edge(f"{rel.from_}->{rel.to}", edge_status)
```

5. **`run_cross_repo` 返回前**（函数末尾）：
```python
        await writer.scan_end("failed" if overall_failed else "completed")
```

> 实现者按真实控制流把 `overall_failed` 与 edge/repo 失败对齐；核心是 5 类节点都有 writer 调用 + 末尾 scan_end。

- [ ] **Step 6: 回归 + 提交**

Run: `cd packages/multi && uv run pytest tests/test_correlation_event_writer.py -v`
Expected: PASS（writer 单测绿；orchestrator wiring 由冒烟第 7 条验证）

```bash
git add packages/multi/src/shannon_multi/correlation_event_writer.py \
        packages/multi/src/shannon_multi/orchestrator.py \
        packages/multi/tests/test_correlation_event_writer.py
git commit -m "feat(multi): write correlation_progress + scan_end ndjson (orchestrator wiring)"
```

---

## Task 13: 部署 wiring（Dockerfile + docker-compose web 服务）

`packages/web/Dockerfile`（装 git + uv + 全部 CLI + shannon-web）+ `docker-compose.yml` 加 `web` 服务（共享 workspaces/repos/configs 卷）。

**Files:**
- Create: `packages/web/Dockerfile`
- Modify: `docker-compose.yml`（加 `web` 服务）

**spec 修正：** spec 原 `build: packages/web` 不足以 COPY 整个 monorepo（web 经 workspace 依赖 core/whitebox/blackbox/multi），改为 `build: { context: ., dockerfile: packages/web/Dockerfile }`。

- [ ] **Step 1: 写 Dockerfile**

```dockerfile
# packages/web/Dockerfile
FROM python:3.11-slim

# GitFetcher 需要 git；ca-certificates for https clone
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages

# 装全部 workspace 包（含 shannon-whitebox/-blackbox/-multi CLI + shannon-web）
RUN uv sync --frozen

EXPOSE 7878
# uv run 把 .venv/bin 注入 PATH，子进程 exec shannon-whitebox 可解析
CMD ["uv", "run", "uvicorn", "shannon_web.app:app", "--host", "0.0.0.0", "--port", "7878"]
```

- [ ] **Step 2: docker-compose.yml 加 web 服务**

在 `docker-compose.yml` 的 `services:` 下（`temporal` 之后）追加：

```yaml
  web:
    build:
      context: .
      dockerfile: packages/web/Dockerfile
    container_name: shannon-py-web
    ports:
      - "${SHANNON_WEB_PORT:-7878}:7878"
    volumes:
      - ./workspaces:/app/workspaces
      - ./repos:/app/repos
      - ./configs:/app/configs
      - ./.env:/app/.env:ro
    environment:
      - SHANNON_WEB_MAX_CONCURRENT=${SHANNON_WEB_MAX_CONCURRENT:-1}
      - SHANNON_WEB_SCAN_TIMEOUT=${SHANNON_WEB_SCAN_TIMEOUT:-0}
    depends_on:
      temporal:
        condition: service_healthy
    networks:
      - shannon-py-net
```

> 若根 compose 无顶层 `networks:` 定义，确认现有 `temporal` 服务的 networks 名；web 与 temporal 必须同网络（web 探 `localhost:7233` 实为容器内 loopback——**注意**：web 容器探 `localhost:7233` 探不到 temporal（不同容器），需改探 `temporal:7233`。见 Step 3 修正）。

- [ ] **Step 3: ScanManager 的 Temporal 探测适配容器网络（修正）**

容器内 web 与 temporal 是不同容器，`localhost:7233` 探不到。改 `_check_temporal` 探测地址可配：

在 `packages/web/src/shannon_web/components/scan_manager.py` 的 `_check_temporal` 里，把硬编码 `("localhost", 7233)` 改为读 env：

```python
    async def _check_temporal(self) -> None:
        import os, socket
        host = os.environ.get("SHANNON_TEMPORAL_HOST", "localhost")
        port = int(os.environ.get("SHANNON_TEMPORAL_PORT", "7233"))

        def _probe() -> bool:
            try:
                with socket.create_connection((host, port), timeout=1.0):
                    return True
            except OSError:
                return False

        loop = asyncio.get_running_loop()
        if not await loop.run_in_executor(None, _probe):
            raise TemporalUnavailable()
```

并在 docker-compose 的 `web.environment` 加：

```yaml
      - SHANNON_TEMPORAL_HOST=temporal
      - SHANNON_TEMPORAL_PORT=7233
```

同步更新 `packages/web/tests/test_scan_manager.py` 的 `_ok`/`_fail` mock 仍 monkeypatch `_check_temporal`，不受影响；新增一个真实探测测试可选（本地起 Temporal 时 `host=localhost`，CI 跳过）。

- [ ] **Step 4: 验收命令**

Run:
```bash
docker compose build web
docker compose up -d temporal web
sleep 5
curl -s localhost:7878/health
```
Expected: `{"status":"ok","git_available":...}`（git_available 取决于 .env 是否配 GITLAB_*）。

- [ ] **Step 5: 提交**

```bash
git add packages/web/Dockerfile docker-compose.yml \
        packages/web/src/shannon_web/components/scan_manager.py
git commit -m "feat(web): Dockerfile + compose web service (configurable Temporal host)"
```

---

## 冒烟验收（独立于前端，curl + SSE 客户端）

全部 task 完成后，按 backend spec §6 逐条手动验收（不属 TDD，是交付门槛）：

1. `curl localhost:7878/api/workspaces` → 返回现有 workspace 列表（含状态/漏洞数）。
2. `curl -X POST localhost:7878/api/scan -d '{"type":"whitebox","source":{"kind":"path","value":"/root/code/foo"},"url":"http://example.com"}'` → 202 + workspace 名。
3. `curl -N localhost:7878/api/workspaces/{ws}/events` → SSE 流，看到 PHASE/STEP/AGENT 事件 + `scan_end` 收尾。
4. 扫描中 `curl localhost:7878/api/workspaces/{ws}` → 状态 `running`。
5. 扫描完 `curl localhost:7878/api/workspaces/{ws}/report` → 返回 md 原文。
6. `curl localhost:7878/api/workspaces/{ws}/deliverables` → 产物清单。
7. 联动：`POST /api/scan` type=correlation + 手写 yaml → correlation workspace SSE 流含 `correlation_progress`（任务 12 wiring）。
8. git URL 模式：clone + branch/commit checkout 正确（`repos/<name>/` 工作区状态符合指定 ref）。
9. 崩溃场景：`kill -INT` 子进程 → ScanManager 补写 `scan_end`，SSE 流收到 `killed` 并关闭流。

**本地开发模式（绕过 docker）：** `uv run uvicorn shannon_web.app:app --reload --port 7878` + 手动起 `docker compose up -d temporal`。

---

## Self-Review（写完后自查）

**1. Spec 覆盖（backend spec 各节 → task）：**
- §1 StructuredEventRenderer → 任务 1；§1.2 挂载 → 任务 2；§1.3 收尾双路（SummaryEvent→scan_end 在任务 1；ScanManager 补写 in 任务 9）；§1.4 单测 → 任务 1 ✓
- §2.1 包结构 → 任务 3；§2.2 六组件（WorkspacesIndexer→4 / ScanManager→9 / EventTailer→8 / DeliverablesReader→5 / MultiRepoConfigStore→6 / GitFetcher→7）✓；§2.3 API → 任务 10/11；§2.4 config → 任务 3 ✓
- §3 联动 → 任务 12 ✓；§4 测试表前 5 层 → 各任务对应测试文件 ✓；§5 部署 → 任务 13 ✓；§6 冒烟 → 冒烟验收清单 ✓
- 主 spec「ndjson schema 硬契约」「错误处理」「扫描类型」全在 Global Constraints + 各 task 实现中体现 ✓

**2. Placeholder 扫描：** 无 TBD/TODO/"implement later"/"add error handling"；每个代码步骤含完整代码 ✓

**3. 类型/函数名一致性（跨 task 引用）：**
- `StructuredEventRenderer.render/close`（任务 1 定义 → 任务 2 挂载调用）✓
- `WorkspacesIndexer.list_workspaces/sync_active/set_active_pid`（任务 4 定义 → 任务 10 list_workspaces 调 sync_active）✓
- `DeliverablesReader.read/summary/read_log`（任务 5 → 任务 10 端点调用）✓
- `MultiRepoConfigStore.list_configs/read/write/write_temp`（任务 6 → 任务 10 multi_configs + 任务 9 `_resolve_correlation_yaml` 调 write/write_temp）✓
- `GitFetcher.fetch/available`（任务 7 → 任务 9 `_resolve_inputs` 调用）✓
- `EventTailer.tail/encode_sse/offset`（任务 8 → 任务 11 gen 调用）✓
- `ScanManager.start/cancel/active_pids/_build_argv/_check_temporal`（任务 9 → 任务 10 scan 端点 + 任务 10 list_workspaces 调 active_pids）✓
- `CorrelationEventWriter.repo/phase/edge/scan_end`（任务 12 writer 定义 → orchestrator 调用）✓

**4. spec 与实际代码偏差（已在 plan 钉死修正）：**
- workflow_logger renderers 组装段 L72-L83（非 spec 的 :62-83）→ 任务 2 已用精确行号 ✓
- MultiRepoConfigStore 校验抛 Pydantic `ValidationError`（非带行号）→ Global Constraints + 任务 6/10 返 422 + `e.errors()` ✓
- docker-compose `build: packages/web` 不足以 COPY monorepo → 任务 13 改 `context: .` ✓
- 容器内 web 探 `localhost:7233` 探不到 temporal → 任务 13 Step 3 引入 `SHANNON_TEMPORAL_HOST` ✓
- `scan_end` status：`killed`（rc<0 信号）/ `crashed`（其他无 scan_end）→ 任务 9 实现 ✓

**结论：** plan 覆盖 backend spec 全部章节，无 placeholder，跨 task 接口一致，spec 模糊点已钉死。ready to execute。

