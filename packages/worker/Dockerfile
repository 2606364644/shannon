# packages/worker/Dockerfile
# C1 Phase A：黑白盒共用 worker 镜像（web 容器保持瘦，依赖全装这里）。
FROM python:3.12-slim-bookworm

# ── apt：git + chromium + curl(gitnexus)/ca ──
# HTTPS 腾讯云：mirrors.cloud.tencent.com 仅 HTTPS 稳定（brief 原为 http sed，冒烟实测改 HTTPS）。
# 阿里云 CDN 部分边缘节点曾对 apt UA 返 403，故弃 http；切腾讯后仍统一走 HTTPS 稳妥。
# chromium 单包：替代原先手写的 headless Chrome 运行时依赖长列表（libnss3/libatk1.0-0/...），
# apt 自动拉全部依赖；arm64/amd64 都有此包，且规避 trixie 的 libasound2→libasound2t64 重命名雷。
RUN sed -i 's|http://deb.debian.org|https://mirrors.cloud.tencent.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
       git ca-certificates curl gnupg chromium \
    && rm -rf /var/lib/apt/lists/*

# ── safe.directory：全信任（修正 78881cfa 的 '/app/repos/*' 带路径通配失效）──
# git 的 * 不跨 /，带路径 pattern 不 glob；只有整值 '*' 或精确路径有效。
# worker 容器扫 repos 下任意属主仓库（root clone / supernova-user clone 挂载），靠全信任避免 dubious ownership。
RUN git config --global --add safe.directory '*'

# ── node 24（NodeSource，供 gitnexus + agent-browser；agent-browser@latest 要 node>=24）──
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── gitnexus@1.6.8 + ladybugdb native binding（白盒确定性轨）──
# --ignore-scripts 跳 onnxruntime post-install（HTTP 302 坑）；补 @ladybugdb/core/install.js 拷 lbugjs.node。
# 走腾讯云 npm 镜像：npmmirror 最新仅 1.6.6（缺 1.6.8/1.6.9），腾讯镜像有 1.6.8/1.6.9，可装。
# 不用 --mount=cache：那是 BuildKit 专有语法，legacy builder（如本机 compose 退回 legacy 时）
# 报 "the --mount option requires BuildKit" 直接构建失败。去 --mount 后 legacy/buildkit 通吃，
# 跨机器零 BuildKit 依赖；npm 下载缓存退化为 docker 层缓存（本层不变即命中）。
RUN npm install -g --prefix=/usr --ignore-scripts --registry=https://mirrors.cloud.tencent.com/npm/ gitnexus@1.6.8 \
    && node "$(npm root -g --prefix=/usr)/gitnexus/node_modules/@ladybugdb/core/install.js" \
    && gitnexus --version

# ── LadybugDB fts 扩展 bake（GitNexus 全文检索）──
# GitNexus 1.6.8 的 FTS 是 LadybugDB(DuckDB 内核)可选扩展；MCP 只读查询路径硬编码
# load-only 策略（lbug-adapter.js: readOnly -> policy 'load-only'，env auto 对它无效），
# 扩展未预装即打 "FTS extension unavailable ... continuing without FTS features" 降级。
# build 时 INSTALL 落盘 ~/.lbdb/extension/<lbug版本>/<arch>/fts/，运行时 LOAD-only 直接命中。
# --verify-only 是上游官方设计的 Docker build-time gate：以新进程走运行时 LOAD 路径验证，
# HOME/extension 目录不匹配或 @ladybugdb/core 升级换版本目录(0.17.0)时 build 期爆、
# 而非运行时静默降级。size 参数脚本无默认（对齐 lbug-config.js 默认 16GB=17179869184）。
RUN LBDB_EXT="$(npm root -g --prefix=/usr)/gitnexus/scripts/install-duckdb-extension.mjs" \
    && node "$LBDB_EXT" fts 17179869184 \
    && node "$LBDB_EXT" fts 17179869184 --verify-only

# ── agent-browser（黑盒默认 browser 引擎）──
# 不跑 agent-browser install：它下载 Chrome for Testing，无 linux/arm64 build → ARM64 必失败。
# 改用上方 apt 装的系统 chromium，运行时经 AGENT_BROWSER_EXECUTABLE_PATH env 指定（见下 ENV）。
# 腾讯云 npm 镜像有 agent-browser（latest 0.34.0），用 CN 镜像加速。（同样去 --mount，通吃 legacy/buildkit，见上。）
RUN npm config set registry https://mirrors.cloud.tencent.com/npm/ \
    && npm install -g agent-browser@latest \
    && agent-browser --version

# agent-browser 运行时：用系统 /usr/bin/chromium（arm64 无 Chrome for Testing；两架构统一走系统 chromium）。
# 容器以 root 跑 chromium 通常需 --no-sandbox；agent-browser doctor 实测后再定是否补 AGENT_BROWSER_ARGS。
ENV AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium

# ── python：uv sync 装全部 workspace 包（含 supernova-worker）──
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_DEFAULT_INDEX=https://mirrors.cloud.tencent.com/pypi/simple/

RUN pip install --no-cache-dir -i https://mirrors.cloud.tencent.com/pypi/simple/ uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
# prompts(agent prompt 模板, 如 pre-recon-code.txt / recon.txt / vuln-*.txt): 扫描 activity
# 从 /app/prompts 读; 缺则 pre-recon 等 agent "Prompt file not found" → 扫描 FAIL
# (2026-07-14 端到端验证暴露: worker 容器跑 WEB 扫描, 非像 CLI 在 host 读 repo 根 prompts/).
COPY prompts ./prompts

# --all-packages：强制装全部 workspace 成员（含 supernova-worker，root deps 不含它）。
# 不用 --frozen：让 uv 走腾讯源下载（与 web Dockerfile 同策略）。lockfile 仍锁版本。
RUN uv sync --all-packages

# .venv/bin 让 supernova-worker 可解析；/usr/bin 让 gitnexus/agent-browser/node 可解析。
ENV PATH="/app/.venv/bin:/usr/bin:${PATH}"

CMD ["supernova-worker"]
