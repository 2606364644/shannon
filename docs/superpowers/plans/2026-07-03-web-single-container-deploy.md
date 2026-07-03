# Web 单容器部署 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让后端 FastAPI 在 `:7878` 同时 serve 前端 SPA 静态产物与 API，Dockerfile 改多阶段，实现 `docker compose up` 一键起完整应用（前端 + API 同源）。

**Architecture:** 后端按配置 `SHANNON_WEB_FRONTEND_DIR`（默认空=dev 不 serve）挂载 `/assets` 静态 + 根 `/` 与 `/{full_path:path}` catch-all 返回 `index.html`（SPA fallback）；catch-all 在所有 `/api/*` 与 `/health` 路由**之后**注册，靠 FastAPI 注册顺序保证 API 优先命中。Dockerfile 多阶段：stage-1 `node` build 前端 → `dist`，stage-2 `python` 拷 dist 入镜像并设 env 触发后端 serve。

**Tech Stack:** Python 3.12 + FastAPI + Starlette StaticFiles + uvicorn（后端）；Node 20 + Vite + React（前端）；Docker 多阶段 + docker-compose（部署）。

## Global Constraints

- Python 3.12+、uv workspace；后端测试经 `uv run pytest` 跑（`packages/web/tests/`，conftest 自动清 `get_config` lru_cache）。
- 前端**零代码改动**：vite `base="/"`、`build.outDir=dist`、相对 `/api/*` 调用均不变（同源，**不引入 CORS**）。
- dist 路径**只用 env `SHANNON_WEB_FRONTEND_DIR` 注入绝对路径**，不靠 `__file__` 相对路径（避免 wheel 安装位置坑）。
- catch-all 路由必须在 `/api/*` 路由与 `/health` **之后**注册（FastAPI 按注册顺序匹配）。
- 前端 vite build 产物结构（测试 fixture 据此造）：`dist/index.html` + `dist/assets/<hash>.js` + `dist/assets/<hash>.css` + `dist/vite.svg`（vite 默认 public 资源）。
- **不做（YAGNI）**：OpenAPI→TS 契约同步、静态资源 Cache-Control/gzip、前端独立 nginx 容器、CDN、SSR、CI 流水线。
- 分支 `feat/fork-py`；每个 task 末尾 commit；commit 风格 `<type>(<scope>): <中文描述>`。
- 前端留在 `packages/web/frontend/`（**不拆**——代码组织与单容器部署形态对齐）。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `packages/web/src/shannon_web/config.py` | `WebConfig.frontend_dir` 读 env `SHANNON_WEB_FRONTEND_DIR` | Modify（加 1 行） |
| `packages/web/src/shannon_web/app.py` | `_mount_frontend(app, cfg)` 挂静态 + SPA fallback；`create_app` 末尾调用 | Modify（加 import + 函数 + 1 行调用） |
| `packages/web/tests/test_config.py` | `frontend_dir` 默认/读 env | Create |
| `packages/web/tests/test_frontend_serving.py` | 静态托管 + SPA fallback + dev 容错全场景 | Create |
| `packages/web/Dockerfile` | 多阶段（node build 前端 → python 拷 dist） | Modify（重写） |
| `README.md` | 目录树补全 + 新增 Web 平台小节 | Modify |
| `docs/superpowers/plans/2026-07-02-shannon-web-frontend.md` | 顶部加部署形态注记 | Modify |

`docker-compose.yml` **不改**（web 服务已 expose 7878，多阶段 Dockerfile 由其 `build.dockerfile` 驱动）。

---

## Task 1: config 新增 `frontend_dir` 配置

**Files:**
- Modify: `packages/web/src/shannon_web/config.py`（`WebConfig.__init__` 内，紧接 `self.configs_dir = ...` 之后）
- Test: `packages/web/tests/test_config.py`

**Interfaces:**
- Consumes: env `SHANNON_WEB_FRONTEND_DIR`
- Produces: `WebConfig.frontend_dir: str | None`（默认 `None`）

- [ ] **Step 1: 写失败测试**

Create `packages/web/tests/test_config.py`:

```python
from shannon_web.config import get_config


def test_frontend_dir_defaults_none(monkeypatch):
    monkeypatch.delenv("SHANNON_WEB_FRONTEND_DIR", raising=False)
    get_config.cache_clear()
    cfg = get_config()
    assert cfg.frontend_dir is None


def test_frontend_dir_reads_env(monkeypatch):
    monkeypatch.setenv("SHANNON_WEB_FRONTEND_DIR", "/tmp/fe-dist")
    get_config.cache_clear()
    cfg = get_config()
    assert cfg.frontend_dir == "/tmp/fe-dist"
```

> conftest.py 的 `_reset_config` autouse fixture 已在每个测试前后 `get_config.cache_clear()`；此处显式再 clear 一次保险。

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web && uv run pytest tests/test_config.py -v`
Expected: 两个测试 FAIL，`AttributeError: 'WebConfig' object has no attribute 'frontend_dir'`

- [ ] **Step 3: 实现 config**

在 `packages/web/src/shannon_web/config.py` 的 `WebConfig.__init__` 内，紧接 `self.configs_dir = Path(...)` 行之后追加：

```python
        self.frontend_dir = os.environ.get("SHANNON_WEB_FRONTEND_DIR")
```

（`os` 已在 config.py 顶部 import，无需新增 import。）

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web && uv run pytest tests/test_config.py -v`
Expected: 2 passed

- [ ] **Step 5: 回归现有测试**

Run: `cd packages/web && uv run pytest tests/test_app_health.py -v`
Expected: passed（确认 config 改动未破坏既有）

- [ ] **Step 6: Commit**

```bash
git add packages/web/src/shannon_web/config.py packages/web/tests/test_config.py
git commit -m "feat(web): WebConfig 新增 frontend_dir 配置项"
```

---

## Task 2: 后端静态托管 + SPA fallback

**Files:**
- Modify: `packages/web/src/shannon_web/app.py`（顶部 import + 新增 `_mount_frontend` + `create_app` 末尾调用）
- Test: `packages/web/tests/test_frontend_serving.py`

**Interfaces:**
- Consumes: `WebConfig.frontend_dir`（Task 1 产出）
- Produces: `_mount_frontend(app, cfg) -> None`（模块级函数，`create_app` 内在所有路由注册后调用）；运行时路由 `/`、`/{full_path:path}`、`/assets/*`（后者仅当 dist/assets 存在）

- [ ] **Step 1: （可选）确认前端 build 产物结构**

> 若本机有 node，先 build 看真实产物，据此校准下面的 fixture。无 node 可跳过——fixture 自造，不依赖真实 build。

Run: `cd packages/web/frontend && npm install && npm run build && ls dist && ls dist/assets`
Expected: `dist/` 含 `index.html`、`vite.svg`、`assets/`；`assets/` 含 `<hash>.js`、`<hash>.css`。

- [ ] **Step 2: 写失败测试**

Create `packages/web/tests/test_frontend_serving.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient

from shannon_web.app import create_app


def _make_dist(tmp_path: Path) -> Path:
    """造一个最小前端 dist（结构对齐 vite build 产物）。"""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / "index.js").write_text("// js bundle")
    (dist / "assets" / "index.css").write_text("body{}")
    (dist / "vite.svg").write_text("<svg></svg>")
    (dist / "index.html").write_text(
        '<html><body><div id="root"></div></body></html>'
    )
    return dist


def _client_with_dist(monkeypatch, tmp_path):
    dist = _make_dist(tmp_path)
    monkeypatch.setenv("SHANNON_WEB_FRONTEND_DIR", str(dist))
    return TestClient(create_app())


def test_serves_index_at_root(monkeypatch, tmp_path):
    client = _client_with_dist(monkeypatch, tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="root"' in r.text


def test_health_not_swallowed(monkeypatch, tmp_path):
    client = _client_with_dist(monkeypatch, tmp_path)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_api_routes_not_swallowed(monkeypatch, tmp_path):
    client = _client_with_dist(monkeypatch, tmp_path)
    r = client.get("/api/workspaces")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")


def test_spa_fallback_deep_path(monkeypatch, tmp_path):
    client = _client_with_dist(monkeypatch, tmp_path)
    r = client.get("/workspaces/some-id")
    assert r.status_code == 200
    assert 'id="root"' in r.text


def test_assets_served(monkeypatch, tmp_path):
    client = _client_with_dist(monkeypatch, tmp_path)
    r = client.get("/assets/index.js")
    assert r.status_code == 200
    assert "js bundle" in r.text


def test_root_static_file_returned(monkeypatch, tmp_path):
    # dist 根目录的真实静态文件（如 vite.svg）应直返，而非当 SPA 路由
    client = _client_with_dist(monkeypatch, tmp_path)
    r = client.get("/vite.svg")
    assert r.status_code == 200


def test_no_frontend_dir_means_no_serving(monkeypatch):
    # dev 模式：不设 env → 后端不挂静态 → GET / 应 404（不崩）
    monkeypatch.delenv("SHANNON_WEB_FRONTEND_DIR", raising=False)
    client = TestClient(create_app())
    r = client.get("/")
    assert r.status_code == 404


def test_missing_dist_dir_does_not_crash(monkeypatch, tmp_path):
    # env 指向不存在目录 → create_app 不抛、GET / 返 404
    monkeypatch.setenv("SHANNON_WEB_FRONTEND_DIR", str(tmp_path / "nonexistent"))
    client = TestClient(create_app())
    r = client.get("/")
    assert r.status_code == 404
```

- [ ] **Step 3: 跑测试验证失败**

Run: `cd packages/web && uv run pytest tests/test_frontend_serving.py -v`
Expected: 多数测试 FAIL（`/`、深路径等返回 404，因为尚未挂载静态）；`test_no_frontend_dir_*` 与 `test_missing_dist_dir_*` 可能恰好通过（属正常，未实现时 dev 行为天然如此）。

- [ ] **Step 4: 实现 app.py**

4a. 顶部 import 区追加（在现有 `from fastapi import FastAPI` 之后）：

```python
from pathlib import Path

from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles
```

4b. 在 `create_app` 函数**之前**新增模块级函数：

```python
def _mount_frontend(app: FastAPI, cfg) -> None:
    """挂载前端 SPA 静态托管（生产/集成模式）。

    cfg.frontend_dir 为空或目录不存在时直接返回（dev 模式前端走 vite 5173）。
    必须在所有 /api/* 路由与 /health 注册**之后**调用——catch-all 靠 FastAPI
    注册顺序保证 API 优先命中。
    """
    if not cfg.frontend_dir:
        return
    dist = Path(cfg.frontend_dir)
    if not dist.is_dir():
        return
    index_html = dist / "index.html"
    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/")
    async def _spa_root():
        return FileResponse(index_html)

    @app.get("/{full_path:path}")
    async def _spa_fallback(full_path: str):
        candidate = dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_html)
```

4c. 在 `create_app` 的 `return app` **之前**追加一行：

```python
    _mount_frontend(app, cfg)

    return app
```

- [ ] **Step 5: 跑测试验证通过**

Run: `cd packages/web && uv run pytest tests/test_frontend_serving.py -v`
Expected: 8 passed

- [ ] **Step 6: 全量回归 web 包测试**

Run: `cd packages/web && uv run pytest -v`
Expected: 全绿（含原有 test_api_*、test_app_health 等）

- [ ] **Step 7: Commit**

```bash
git add packages/web/src/shannon_web/app.py packages/web/tests/test_frontend_serving.py
git commit -m "feat(web): 后端 serve 前端 SPA（静态托管 + catch-all fallback）"
```

---

## Task 3: Dockerfile 多阶段构建

**Files:**
- Modify: `packages/web/Dockerfile`（整体重写为多阶段）

**Interfaces:**
- Consumes: 前端 `packages/web/frontend/`（npm 源）、后端 `packages/`（uv 源）
- Produces: 最终镜像含 `/app/frontend_dist`（前端 build 产物）+ env `SHANNON_WEB_FRONTEND_DIR=/app/frontend_dist`（触发 Task 2 的静态托管）

- [ ] **Step 1: 重写 Dockerfile**

整体替换 `packages/web/Dockerfile` 为：

```dockerfile
# packages/web/Dockerfile

# ---- stage 1: build frontend ----
FROM node:20-slim AS frontend
WORKDIR /fe
COPY packages/web/frontend/package.json packages/web/frontend/package-lock.json ./
RUN npm ci
COPY packages/web/frontend/ ./
RUN npm run build            # → /fe/dist

# ---- stage 2: python backend + 拷前端 dist ----
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

# 拷前端 build 产物 + 触发后端 serve 静态
COPY --from=frontend /fe/dist /app/frontend_dist
ENV SHANNON_WEB_FRONTEND_DIR=/app/frontend_dist

EXPOSE 7878
# uv run 把 .venv/bin 注入 PATH，子进程 exec shannon-whitebox 可解析
CMD ["uv", "run", "uvicorn", "shannon_web.app:app", "--host", "0.0.0.0", "--port", "7878"]
```

- [ ] **Step 2: 本地 build 镜像验证**

Run: `docker build -f packages/web/Dockerfile -t shannon-py-web:plancheck .`
Expected: build 成功（stage-1 `npm ci` + `npm run build` 通过；stage-2 `uv sync` 通过）

> 首次 build 会下载 node_modules 与 uv 包，耗时数分钟。若 `npm ci` 因 lock 不同步失败，先在宿主 `cd packages/web/frontend && npm install` 更新 lock 再重提（注意 lock 是否纳入本 task）。

- [ ] **Step 3: 验证镜像内前端产物 + env**

Run:
```bash
docker run --rm shannon-py-web:plancheck ls /app/frontend_dist
docker run --rm --entrypoint env shannon-py-web:plancheck | grep SHANNON_WEB_FRONTEND_DIR
```
Expected: 第一条列出 `index.html`、`assets`、`vite.svg`；第二条输出 `SHANNON_WEB_FRONTEND_DIR=/app/frontend_dist`。

- [ ] **Step 4: （可选）镜像内冒烟后端 serve**

Run:
```bash
docker run --rm -d -p 7879:7878 --name web-plancheck shannon-py-web:plancheck
sleep 3
curl -s http://localhost:7879/health
curl -s http://localhost:7879/ | grep -o 'id="root"'
docker rm -f web-plancheck
```
Expected: `/health` 返 `{"status":"ok",...}`；`/` 含 `id="root"`。

- [ ] **Step 5: Commit**

```bash
git add packages/web/Dockerfile
git commit -m "build(web): Dockerfile 多阶段（node build 前端 → python 拷 dist 单容器 serve）"
```

---

## Task 4: docker compose 端到端冒烟

**Files:** 无（验证性 task，不改代码）

**Interfaces:**
- 验证 Task 1-3 闭环：compose 起 temporal + web → `:7878` 同时给前端 + API + SPA fallback。

- [ ] **Step 1: 一键起栈**

Run: `docker compose up --build -d`
Expected: `temporal`、`web` 两个服务 `Up`（`docker compose ps` 确认 web 健康）。

- [ ] **Step 2: 冒烟前端 + API + SPA fallback**

Run:
```bash
curl -s http://localhost:7878/health | grep -o '"status":"ok"'
curl -s http://localhost:7878/ | grep -o 'id="root"'
curl -s http://localhost:7878/api/workspaces
curl -s -o /dev/null -w "deep=%{http_code}\n" http://localhost:7878/workspaces/some-id
curl -s -o /dev/null -w "asset=%{http_code}\n" http://localhost:7878/assets/index.js
```
Expected:
- `/health` → `"status":"ok"`
- `/` → 含 `id="root"`
- `/api/workspaces` → JSON（可能空列表 `[]`）
- `deep=200`（SPA fallback 返 index.html）
- `asset=200`（注：真实产物文件名是 `<hash>.js`；若 `index.js` 404，改用 `ls` 得到的真实 hash 文件名重试——此处仅验证 `/assets/*` 路由通）

> 若要严格验证 asset，先 `docker compose exec web ls /app/frontend_dist/assets` 拿真实文件名。

- [ ] **Step 3: 浏览器人工确认**

打开 `http://localhost:7878`：
- 首页加载、JS/CSS 注入、React 挂载
- 点进某个 workspace 详情（深路径），**刷新页面**不 404（验证 SPA fallback）
- API 列表正常加载

- [ ] **Step 4: 收尾**

Run: `docker compose down`
Expected: 栈停止（保留 volume 无妨）。

- [ ] **Step 5: Commit（仅当本 task 改了文件才提交；纯验证无 commit）**

本 task 无文件改动，跳过 commit。若 Step 2/3 发现 bug 需回 Task 2/3 修复，则修复后在该 task commit。

---

## Task 5: 文档更新

**Files:**
- Modify: `README.md`（目录树 188-200 行补全 + 新增 Web 平台小节）
- Modify: `docs/superpowers/plans/2026-07-02-shannon-web-frontend.md`（顶部加部署形态注记）

**Interfaces:** 无（纯文档）

- [ ] **Step 1: 更新 README 目录树**

将 `README.md` 的项目结构代码块（约 188-200 行）：

```
shannon-py/
├── packages/
│   ├── core/                    # 共享模型、配置解析、agent 集成层与工具函数
│   ├── whitebox/                # 白盒源码漏洞分析扫描器
│   └── blackbox/                # 黑盒运行时漏洞验证和报告生成
├── prompts/                     # Prompt 模板文件
├── scripts/                     # 验证 / 调试脚本（如 validate_*_task_probe.py）
├── docs/                        # 项目文档
├── .env.example                 # 共享配置模板
├── .env.profiles.example/       # 各 profile 的引擎/账号模板
└── pyproject.toml               # uv workspace 配置
```

替换为：

```
shannon-py/
├── packages/
│   ├── core/                    # 共享模型、配置解析、agent 集成层与工具函数
│   ├── whitebox/                # 白盒源码漏洞分析扫描器
│   ├── blackbox/                # 黑盒运行时漏洞验证和报告生成
│   ├── combined/                # 白盒+黑盒组合编排
│   ├── multi/                   # 多仓 / 跨仓扫描
│   └── web/                     # Web 平台：后端 FastAPI + 前端 SPA
│       └── frontend/            # Vite + React 前端（构建产物由后端单容器 serve）
├── apps/                        # 原始 TS 参考（cli / worker）
├── prompts/                     # Prompt 模板文件
├── scripts/                     # 验证 / 调试脚本（如 validate_*_task_probe.py）
├── docs/                        # 项目文档
├── docker-compose.yml           # temporal + web 单容器部署
├── .env.example                 # 共享配置模板
├── .env.profiles.example/       # 各 profile 的引擎/账号模板
└── pyproject.toml               # uv workspace 配置
```

- [ ] **Step 2: README 新增 Web 平台小节**

在 `README.md` 的「### 黑盒扫描」小节结束（"查看工作区和日志" 的黑盒代码块之后，约 164 行）、「## 架构概览」（约 166 行）**之前**，插入：

```markdown
## Web 平台（可选）

除 CLI 外，shannon-py 提供一个 Web 平台（`packages/web`）用于扫描调度与结果查看——前端 SPA（Vite + React）+ 后端 API（FastAPI），**单容器部署**：后端在 `:7878` 同时 serve 前端静态产物与 API，同源无 CORS。

### 一键部署（Docker）

```bash
docker compose up --build
# 浏览器访问 http://localhost:7878（前端 + API 同源）
```

compose 起两个服务：`temporal`（workflow 引擎，:7233 gRPC / :8233 Web UI）与 `web`（前端 + API，:7878）。

### 本地开发（热更新）

前后端分离跑，前端走 Vite 热更新：

```bash
# 终端 1：后端
uv run uvicorn shannon_web.app:app --port 7878

# 终端 2：前端（:5173，proxy /api → 7878）
cd packages/web/frontend && npm install && npm run dev
```

浏览器访问 `http://localhost:5173`。

> 生产（单容器）与开发（分离）共用同一份后端代码：后端 serve 静态由 `SHANNON_WEB_FRONTEND_DIR` 控制，开发时不设此变量即跳过。详见 [设计 spec](docs/superpowers/specs/2026-07-03-web-single-container-deploy-design.md)。
```

- [ ] **Step 3: 历史 plan 加部署形态注记**

在 `docs/superpowers/plans/2026-07-02-shannon-web-frontend.md` 的 H1 标题（第 1 行）**正下方**插入一行：

```markdown
> **更新（2026-07-03）**：前端部署形态已定为**单容器**（后端 FastAPI serve 前端 SPA），见 `docs/superpowers/specs/2026-07-03-web-single-container-deploy-design.md`。本文档当初"生产由后端静态托管或 nginx"的悬而未决项已兑现为前者。
```

- [ ] **Step 4: Commit**

```bash
git add README.md docs/superpowers/plans/2026-07-02-shannon-web-frontend.md
git commit -m "docs(web): README 目录树补全 + Web 平台单容器部署说明"
```

---

## 完成判据

- [ ] Task 1-2：`cd packages/web && uv run pytest -v` 全绿（含新增 test_config / test_frontend_serving）。
- [ ] Task 3：`docker build -f packages/web/Dockerfile .` 成功，镜像内 `/app/frontend_dist` 含前端产物 + env 已设。
- [ ] Task 4：`docker compose up` 后 `:7878` 同时给前端（`/` 含 root）、API（`/api/*`、`/health` JSON）、SPA fallback（深路径 200）。
- [ ] Task 5：README 目录树含 `web/frontend`，Web 平台小节在；历史 plan 顶部有注记。
- [ ] 全部 task 已 commit 到 `feat/fork-py`。
