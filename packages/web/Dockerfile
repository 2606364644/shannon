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

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages

# 装全部 workspace 包（含 shannon-whitebox/-blackbox/-multi CLI + shannon-web）
# 不用 --frozen：frozen 模式下 uv 严格按 lockfile 里的 files.pythonhosted.org URL 下载，
# --default-index 会被忽略。去掉 --frozen 让 uv 走清华源下载，lockfile 仍锁版本。
RUN uv sync --default-index https://pypi.tuna.tsinghua.edu.cn/simple

# 拷前端 build 产物 + 触发后端 serve 静态
COPY --from=frontend /fe/dist /app/frontend_dist
ENV SHANNON_WEB_FRONTEND_DIR=/app/frontend_dist

EXPOSE 7878
# uv run 把 .venv/bin 注入 PATH，子进程 exec shannon-whitebox 可解析
CMD ["uv", "run", "uvicorn", "shannon_web.app:app", "--host", "0.0.0.0", "--port", "7878"]
