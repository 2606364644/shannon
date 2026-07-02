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
