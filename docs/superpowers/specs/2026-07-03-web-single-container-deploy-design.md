# Web 单容器部署（后端 serve 前端 SPA）设计

**Date:** 2026-07-03
**Status:** Pending Review
**分支:** `feat/fork-py`

**相关 spec / plan：**
- `docs/superpowers/plans/2026-07-02-shannon-web-frontend.md`（前端初始交付 plan，把前端放在 `packages/web/frontend/`，当时定为"生产由后端静态托管或 nginx，dev 走 Vite proxy"）——本设计**兑现其中的"后端静态托管"分支**，并据此调整部署形态。
- 本 spec 取代会话中一度讨论的"前端拆到仓库根 `frontend/`"方案——经业内实践对比后撤销拆分，改为单容器部署（详见 §1.3）。

---

## 0. 一句话结论

让后端 FastAPI 在 `:7878` **同时 serve 前端 SPA 静态产物与 API**，Dockerfile 改多阶段（node build 前端 → python 镜像拷 dist），实现**一个 `docker compose up` 起全套、浏览器开 `:7878` 即得完整应用**。前端目录留在 `packages/web/frontend/`（不拆）。本地开发仍保留 vite 热更新（5173 + proxy）。

---

## 1. 背景

### 1.1 现状（前后端工程上完全解耦）

`packages/web` 内含两个独立项目：

| 维度 | 后端 | 前端 |
|---|---|---|
| 位置 | `packages/web/src/shannon_web/`（Python，FastAPI） | `packages/web/frontend/`（Node，Vite + React） |
| 端口 | `:7878` | dev `:5173` |
| 契约 | Pydantic 模型 + `/api/*` 路由 | 手写 `frontend/src/api/types.ts` + 相对 `/api/*` 调用 |
| 构建 | `uv sync` | `npm run build` → `frontend/dist/`（`.gitignore` 忽略） |
| 部署 | `Dockerfile` → compose `web` 服务 | **无生产部署形态**（dev-only：vite 5173 + proxy `/api → 7878`） |

后端 `app.py` 当前**不 serve 前端**（无 `StaticFiles` mount），`Dockerfile` 不 build 前端。结论：**前端缺一个"如何部署到生产"的答案**。

### 1.2 业界三种部署模式（决策依据）

| 模式 | 拓扑 | 适用 | 代价 |
|---|---|---|---|
| ① **单容器**（后端 serve SPA） | FastAPI 同时给 `/api` + 静态 + SPA fallback | 小/内部/MVP、纯 SPA、无 SSR | 前后端构建耦合（改前端重建后端镜像） |
| ② **双容器**（前端 nginx + 后端 API） | 前端 nginx serve + 反代 `/api` | 中型 SaaS、前后端独立发版 | 多一个容器/镜像 + 编排 |
| ③ **CDN + API** | 前端上 S3/CloudFront | 大流量/公网产品 | 需 CDN 基础设施 |

shannon-py web 平台画像：**内部安全审计工具的扫描调度 UI、纯 SPA（无 SSR）、无 CI、无生产部署痕迹、已有 docker-compose、小团队** → 精确落在模式①的适用域。

### 1.3 决策转向：撤销"拆代码"，改"单容器部署"

会话中一度讨论把前端从 `packages/web/` 拆到仓库根（"拆开更干净"）。但**代码组织应与部署形态对齐**：

- 单容器①（后端 serve 前端）↔ **代码同包**最顺（构建耦合：后端 Dockerfile 要 build 前端 dist）
- 双容器②/CDN③ ↔ 代码拆开最顺（构建/部署/发版独立）

既然 shannon-py 选①，对应最佳代码组织是**不拆、留在 `packages/web/frontend/`**。拆代码 + 单容器部署能跑，但属"代码拆开了、构建又耦合回去"的拧巴态，无红利。故**撤销拆分**，本设计聚焦单容器部署。

### 1.4 隐藏耦合排查（拆分阶段已确认全绿，沿用为低风险依据）

- 无 `turbo.json`、无根 `package.json` 管 frontend、无 CI、无 Makefile
- `packages/web/tests` 不引用 frontend；`scripts/` 不引用 frontend
- 前端内部全相对 import，`vite.config.ts` proxy 指 `localhost:7878`（与前端位置无关）
- 根 `.gitignore` 已全局忽略 `node_modules/`、`dist/`、`.turbo/`

---

## 2. 目标 / 非目标

### 2.1 目标
1. **单容器一键起全套**：`docker compose up` 后，浏览器访问 `:7878` 即得完整应用（前端 SPA + API，同源）。
2. **dev 体验不退化**：本地开发仍走 vite 5173 热更新 + proxy `/api`，改前端无需重建后端。
3. **零前端代码改动**：vite base `/`、outDir `dist`、相对 `/api` 调用均不变。
4. **dev/prod 同一份后端代码**：后端 serve 静态的能力受配置开关控制，dev 时（无 dist）graceful 跳过。

### 2.2 非目标（YAGNI，明确不做）
- ❌ OpenAPI → TS 契约自动同步（保持手写 `types.ts`，属另一个独立决策）
- ❌ 静态资源缓存头 / CDN / gzip（内部工具）
- ❌ 前端独立 compose 服务（双容器方案，已排除）
- ❌ 引入 CI（仓库当前无 CI，超出本 spec 范围）
- ❌ SSR（前端是纯 SPA）

---

## 3. 架构

### 3.1 部署拓扑（单容器）

```
浏览器 http://localhost:7878
        │
        ▼
┌──────────────────────────────────────────┐
│ 容器 web   FastAPI :7878                  │
│   /api/*        → 业务路由（workspaces/   │
│                   scan/multi-configs/     │
│                   events），不变          │
│   /health       → 健康检查，不变          │
│   /assets/*     → vite 构建的 JS/CSS chunk │
│   /*  (catch-all) → index.html (SPA)      │
│                    交给 React Router      │
└──────────────────────────────────────────┘
        │
        ▼ (扫描调度)
┌────────────────┐
│ temporal :7233 │  (compose 另一服务)
└────────────────┘
```

### 3.2 dev vs 生产两种模式

| 模式 | 前端 | 后端 | 访问 | 用途 |
|---|---|---|---|---|
| **开发**（热更新） | `cd packages/web/frontend && npm run dev`（5173） | `uv run uvicorn shannon_web.app:app`（7878，不 build 前端） | `:5173`，vite proxy `/api → 7878` | 日常前端/后端开发 |
| **生产/集成** | Dockerfile stage-1 build → dist | uvicorn serve dist + API | `:7878` 一个地址 | 部署、集成测试 |

两种模式共用同一份后端代码——serve 静态的能力由"dist 是否存在 / env 是否配置"决定，dev 时自动跳过。

---

## 4. 改动清单

### 4.1 前端（零代码改动）

- `vite.config.ts`：proxy `/api → localhost:7878` 不变；`build.outDir` 用默认 `dist`；`base` 默认 `/`。
- `frontend/src/api/*`：相对 `/api/*` 调用不变（同源，无需 CORS）。
- 验证点：`npm run build` 产 `frontend/dist/index.html` + `frontend/dist/assets/*`。

### 4.2 后端 `packages/web/src/shannon_web/`

#### 4.2.1 `config.py`：新增前端 dist 路径配置

```python
# WebConfig 新增
self.frontend_dir = os.environ.get("SHANNON_WEB_FRONTEND_DIR")  # None=不 serve
```

默认 `None`（dev 不 serve）；Dockerfile 设绝对路径（如 `/app/frontend_dist`）。

#### 4.2.2 `app.py`：新增静态托管 + SPA fallback

在现有路由注册**之后**追加（保证 `/api/*`、`/health` 先注册、catch-all 最后匹配）：

```python
def _mount_frontend(app: FastAPI, cfg: WebConfig) -> None:
    if not cfg.frontend_dir:
        return  # dev 模式：不 serve 前端，前端走 vite 5173
    dist = Path(cfg.frontend_dir)
    if not dist.is_dir():
        return  # dist 不存在：graceful 跳过（避免 dev 启动崩）

    index_html = dist / "index.html"

    # 静态资源 chunk
    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    # SPA fallback：非 /api、非 /health、非静态的 GET → index.html
    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # 直接命中的静态文件（favicon.ico、vite.svg 等根目录资源）
        candidate = dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_html)
```

注意：catch-all `/{full_path:path}` 必须在所有 `/api/*` 路由和 `@app.get("/health")` **之后**注册——FastAPI 按注册顺序匹配，先注册的优先。`/assets` mount 也在 catch-all 之前。

#### 4.2.3 测试新增（`packages/web/tests/`）

新增 `test_frontend_serving.py`：
- `GET /` → 200，body 含 `<div id="root">`（index.html 标记）
- `GET /health` → 仍 200 `{"status":"ok",...}`（不被 catch-all 吞）
- `GET /api/workspaces` → 仍 200（API 路由优先）
- `GET /workspaces/some-id`（未知深路径）→ 200 返 index.html（SPA fallback）
- `GET /assets/index-xxx.js` → 200（静态资源）
- `GET /favicon.ico`（dist 根存在的文件）→ 200
- `frontend_dir=None`（默认）→ 上述静态路由不存在，`GET /` → 404（dev 行为，确认不崩）
- `frontend_dir` 指向不存在目录 → `create_app` 不抛异常（graceful）

测试用临时 dist 目录 fixture（造 `index.html` + `assets/foo.js` + `favicon.ico`），不依赖真实前端 build。

### 4.3 Dockerfile（多阶段）

```dockerfile
# packages/web/Dockerfile

# ---- stage 1: build frontend ----
FROM node:20-slim AS frontend
WORKDIR /fe
COPY packages/web/frontend/package.json packages/web/frontend/package-lock.json ./
RUN npm ci
COPY packages/web/frontend/ ./
RUN npm run build            # → /fe/dist

# ---- stage 2: python backend ----
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
RUN uv sync --frozen

# 拷前端 build 产物
COPY --from=frontend /fe/dist /app/frontend_dist
ENV SHANNON_WEB_FRONTEND_DIR=/app/frontend_dist

EXPOSE 7878
CMD ["uv", "run", "uvicorn", "shannon_web.app:app", "--host", "0.0.0.0", "--port", "7878"]
```

说明：
- stage-1 `COPY .../package*.json` 先于源码，利用 Docker 层缓存（依赖不变时跳过 `npm ci`）。
- stage-2 `ENV SHANNON_WEB_FRONTEND_DIR=/app/frontend_dist` 触发后端 serve 静态。
- `packages/web/frontend/` 整体被 COPY 进 stage-1，不污染 stage-2（stage-2 只拿 `/fe/dist`）。

### 4.4 docker-compose.yml（不变）

`web` 服务已 expose 7878；现在同端口同时给前端 + API，无需改 compose。`build.context = .`、`dockerfile = packages/web/Dockerfile` 已能驱动多阶段构建。

### 4.5 文档

- `README.md`：
  - 目录树补 `packages/web/frontend/`（顺带修 README 目录树滞后——之前连 combined/multi/web 都没列）。
  - 新增"Web 平台部署"小节：`docker compose up` → 访问 `:7878`；dev 模式说明（vite 5173 + uvicorn 7878）。
- `docs/superpowers/plans/2026-07-02-shannon-web-frontend.md`：顶部加一行注记"前端部署形态见 `2026-07-03-web-single-container-deploy-design.md`（单容器，后端 serve）"。

---

## 5. 关键技术点

### 5.1 dist 路径解耦（避免 wheel 位置坑）

不依赖 `__file__` 相对路径（包从 wheel 装到 site-packages 时，相对路径指不对 frontend/dist）。用 **env `SHANNON_WEB_FRONTEND_DIR`** 显式注入绝对路径；默认 `None` = dev 不 serve。dev（本地 `uv run`）不设此 env，后端跳过静态托管；Docker 设 `/app/frontend_dist`。

### 5.2 路由优先级（catch-all 不吞 API）

FastAPI/Starlette 按路由注册顺序匹配。`create_app` 中**先** `include_router(workspaces/scan/multi_configs/events)` + 注册 `/health`，**最后**调 `_mount_frontend`（含 `/assets` mount + `/{full_path:path}` catch-all）。这样 `/api/workspaces`、`/health` 优先命中，深路径才落到 SPA fallback。

**实现注意**：`/{full_path:path}` 对根路径 `/`（`full_path=""`）的命中行为依 Starlette 版本而定，稳妥起见在 `_mount_frontend` 里额外显式注册 `@app.get("/")` 返 `index.html`，与 catch-all 并存。此细节交给实现 plan 固化。

### 5.3 dev 容错（dist 缺失不崩）

`_mount_frontend` 在 `frontend_dir` 为空或目录不存在时直接 `return`，不挂任何静态路由。开发时（未 build 前端、未设 env）后端是纯 API，前端走 vite 5173。

### 5.4 SPA fallback 的静态文件直返

catch-all 先检查 `dist/<full_path>` 是否为真实文件（如 vite 默认的 `vite.svg`、`robots.txt` 等根目录资源），是则直返；否则返 `index.html`。避免这些根目录静态资源被误当 SPA 路由。

---

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| 前端 build 失败 → Docker build 失败 | 可接受（fail fast）；`npm run build` 含 `tsc -b` 类型检查，类型错会拦在 build 阶段 |
| catch-all 误吞 `/api`/`/health` | §5.2 路由顺序保证；测试 `test_frontend_serving.py` 显式覆盖 |
| dist 路径在 wheel 安装场景指不对 | §5.1 用 env 注入绝对路径，不靠 `__file__` |
| 开发者本地 `uv run uvicorn` 时意外 serve 旧 dist | 默认 `frontend_dir=None`；除非显式设 env，否则永不 serve |
| 前端产物体积膨胀镜像 | node 仅在 stage-1（不进最终镜像）；最终镜像只多一个 `/app/frontend_dist`（典型 < 5MB gzip） |
| React Router 的 client-side 路由刷新 404 | catch-all 返 index.html 解决（正是 SPA fallback 的目的） |

---

## 7. 验证

### 7.1 单元/集成测试（本 spec 交付物）
- `cd packages/web && uv run pytest tests/test_frontend_serving.py`（新增）
- 现有 `packages/web/tests/*` 全绿（确认静态托管改动不破坏 API）

### 7.2 前端不退化
- `cd packages/web/frontend && npm install && npm test && npm run build`（确认 build 产 dist 且测试绿）

### 7.3 端到端冒烟（手动，本 spec 之后的实现 plan 会细化）
- `docker compose up --build` → 浏览器开 `http://localhost:7878`：
  - 首页加载（index.html + assets）
  - 点进某个 workspace 详情（深路径）刷新不 404（SPA fallback）
  - API 正常（`/api/workspaces` 返列表、`/health` ok）
- dev 模式：`uv run uvicorn ...` + `cd frontend && npm run dev` → `:5173` 热更新正常

---

## 8. 不做（YAGNI，明确排除）

- OpenAPI → TS 契约同步（手写 `types.ts` 保留）
- 静态资源 Cache-Control / ETag / gzip / brotli
- 前端独立 nginx 容器 / CDN
- SSR / 边缘渲染
- CI 流水线
- 多前端入口 / 微前端

---

## 9. 后续（本 spec 范围外，记录待定项）

- 若未来 shannon-py web 走向公网/多租户/高流量，重新评估升级到模式②（双容器）或③（CDN），届时再谈代码拆分。
- 若前后端契约漂移成为痛点，独立立项做 OpenAPI → TS 生成（与本 spec 正交）。
