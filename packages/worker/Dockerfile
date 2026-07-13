# packages/worker/Dockerfile
# C1 Phase A：黑白盒共用 worker 镜像（web 容器保持瘦，依赖全装这里）。
FROM python:3.12-slim

# ── apt：git + chrome 运行时依赖 + curl(gitnexus)/ca ──
# HTTPS aliyun：HTTP (port 80) 阿里云 CDN 部分边缘节点对 apt UA 返 403（实测 docker 容器内
# 182.89.193.20 / 36.250.74.132 等 edge），HTTPS 稳定。brief 原为 http sed，冒烟实测改 HTTPS。
RUN sed -i 's|http://deb.debian.org|https://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
       git ca-certificates curl gnupg \
       # headless Chrome 运行时依赖（agent-browser install 下载的 Chrome 需要）
       libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
       libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
       libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# ── safe.directory：全信任（修正 78881cfa 的 '/app/repos/*' 带路径通配失效）──
# git 的 * 不跨 /，带路径 pattern 不 glob；只有整值 '*' 或精确路径有效。
# worker 容器扫 repos 下任意属主仓库（root clone / shannon-user clone 挂载），靠全信任避免 dubious ownership。
RUN git config --global --add safe.directory '*'

# ── node 22（NodeSource，供 gitnexus + agent-browser）──
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── gitnexus@1.6.8 + ladybugdb native binding（白盒确定性轨）──
# --ignore-scripts 跳 onnxruntime post-install（HTTP 302 坑）；补 @ladybugdb/core/install.js 拷 lbugjs.node。
# 走 npmjs 官方源：npmmirror 最新仅 1.6.6（缺 1.6.8/1.6.9），brief 原 npmmirror 装不到 1.6.8。
RUN npm install -g --prefix=/usr --ignore-scripts --registry=https://registry.npmjs.org gitnexus@1.6.8 \
    && node "$(npm root -g --prefix=/usr)/gitnexus/node_modules/@ladybugdb/core/install.js" \
    && gitnexus --version

# ── agent-browser + Chrome（黑盒默认 browser 引擎）──
# agent-browser install 自带 Chrome 下载（参考 scripts/bootstrap.sh:142-153）。
# npmmirror 有 agent-browser（latest 0.31.1，109 版本），用 CN 镜像加速。
RUN npm config set registry https://registry.npmmirror.com \
    && npm install -g agent-browser@latest \
    && agent-browser install \
    && agent-browser --version

# ── python：uv sync 装全部 workspace 包（含 shannon-worker）──
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/

RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages

# --all-packages：强制装全部 workspace 成员（含 shannon-worker，root deps 不含它）。
# 不用 --frozen：让 uv 走阿里源下载（与 web Dockerfile 同策略）。lockfile 仍锁版本。
RUN uv sync --all-packages

# .venv/bin 让 shannon-worker 可解析；/usr/bin 让 gitnexus/agent-browser/node 可解析。
ENV PATH="/app/.venv/bin:/usr/bin:${PATH}"

CMD ["shannon-worker"]
