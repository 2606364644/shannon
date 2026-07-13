# packages/web/Dockerfile

# ---- stage 1: build frontend ----
FROM node:20-slim AS frontend
WORKDIR /fe
COPY packages/web/frontend/package.json packages/web/frontend/package-lock.json ./
RUN npm config set registry https://registry.npmmirror.com && npm ci
COPY packages/web/frontend/ ./
RUN npm run build            # → /fe/dist

# ---- stage 2: python backend + 拷前端 dist ----
FROM python:3.12-slim

# GitFetcher 需要 git；ca-certificates for https clone
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# repos 是 WEB(容器 root) 与宿主 CLI(shannon-user) 共享的扫描目标，.git 属主可能非 root
# （CLI clone 或宿主 chown）。信任 /app/repos/* 避免 git dubious ownership(exit 128) 卡 WEB 扫描。
RUN git config --global --add safe.directory '/app/repos/*'

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/

RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages

# 装全部 workspace 包（含 shannon-whitebox/-blackbox/-multi CLI + shannon-web）
# 不用 --frozen：frozen 模式下 uv 严格按 lockfile 里的 files.pythonhosted.org URL 下载，
# --default-index 会被忽略。去掉 --frozen 让 uv 走清华源下载，lockfile 仍锁版本。
# UV_DEFAULT_INDEX（上方 ENV）对所有 uv 调用生效，含本地 workspace 包 build-system.requires
# （如 hatchling）的解析——之前 `uv run` 启动时重建本地包走 PyPI 拉 hatchling 失败即缺此兜底。
RUN uv sync

# 拷前端 build 产物 + 触发后端 serve 静态
COPY --from=frontend /fe/dist /app/frontend_dist
ENV SHANNON_WEB_FRONTEND_DIR=/app/frontend_dist

# 直接用 .venv 的 uvicorn 启动，绕开 `uv run` 的运行时 sync——否则 uv run 启动时会重新
# 解析本地 workspace 包的 build deps（hatchling），曾因走 PyPI 拉取失败 (tls eof) 致容器
# Exited(1)。UV_DEFAULT_INDEX 已兜底任何残留 uv 调用；PATH 注入 .venv/bin 让 web app 运行
# 时 exec 的 shannon-whitebox 等 CLI 可解析（原 `uv run` 注入 PATH 的等价替代）。
ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 7878
CMD ["uvicorn", "shannon_web.app:app", "--host", "0.0.0.0", "--port", "7878"]
