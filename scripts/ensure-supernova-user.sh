#!/usr/bin/env bash
# scripts/ensure-supernova-user.sh
#
# 幂等确保项目专属用户 supernova-user 就绪，使其能在宿主上直接跑白盒扫描：
#   1. 用户存在（不存在才创建，已存在则跳过）+ 在 docker 组
#   2. 系统级 uv（/usr/local/bin/uv）—— 让非 root 登录环境 PATH 自带 uv，
#      否则 `su - supernova-user` 后 `uv run supernova-whitebox` 会 command not found
#      （gitnexus/node 已系统级化到 /usr，uv 之前漏了）。
#
# 设计取舍：只动宿主层，不改容器内身份——容器（supernova-web）内维持 root，
# 由 Dockerfile 决定；本脚本只让「宿主 CLI 直跑扫描」可用 supernova-user。
#
# 需以 root 执行。幂等：重复跑无副作用。
# 用法：sudo bash scripts/ensure-supernova-user.sh
set -euo pipefail

SUPERNOVA_USER="${SUPERNOVA_USER:-supernova-user}"
SUPERNOVA_HOME="${SUPERNOVA_HOME:-/root/supernova}"

if [ "$(id -u)" -ne 0 ]; then
    echo "ensure-supernova-user: 需要 root（建用户 / 写 /usr/local/bin），请用 sudo 或 root 执行。" >&2
    exit 1
fi

echo "=== ensure-supernova-user ==="

# ── 1. 用户：不存在才建；已存在确保在 docker 组 ──
if ! id "$SUPERNOVA_USER" >/dev/null 2>&1; then
    echo ">> 创建项目用户 $SUPERNOVA_USER（加入 docker 组）"
    # -m 建 home；-s /bin/bash（POSIX 必有，新机器不依赖 zsh 是否安装）；
    # -G docker 让它能操作 docker（复用/自建 temporal、compose）。
    useradd -m -G docker -s /bin/bash "$SUPERNOVA_USER"
else
    echo ">> 用户 $SUPERNOVA_USER 已存在（不重建）"
    if ! id -nG "$SUPERNOVA_USER" | tr ' ' '\n' | grep -qx docker; then
        echo ">> 补加 docker 组"
        usermod -aG docker "$SUPERNOVA_USER"
    fi
fi

# ── 2. 系统级 uv：让 supernova-user 登录 PATH(/usr/local/bin) 能找到 uv ──
# .venv 归 supernova-user（可执行），但用户习惯 `uv run supernova-whitebox`；
# uv 之前只在 /root/.local/bin（root 私有），非 root 登录找不到。
if [ -x /usr/local/bin/uv ]; then
    echo ">> 系统级 uv 已就绪 (/usr/local/bin/uv)"
else
    SRC=""
    for c in /root/.local/bin/uv "$(command -v uv 2>/dev/null || true)"; do
        if [ -x "$c" ]; then SRC="$c"; break; fi
    done
    if [ -n "$SRC" ]; then
        echo ">> 系统级化 uv：$SRC → /usr/local/bin/uv"
        install -m 0755 "$SRC" /usr/local/bin/uv
    else
        # 新机器 root 多半没 uv：官方安装器自举装到 /root/.local/bin，再系统级化。
        echo ">> 未找到现有 uv，用官方安装器装后系统级化"
        curl -LsSf https://astral.sh/uv/install.sh | sh
        install -m 0755 /root/.local/bin/uv /usr/local/bin/uv
        [ -x /usr/local/bin/uv ] || { echo "!! uv 安装失败" >&2; exit 1; }
    fi
fi

# ── 3. safe.directory：让 supernova-user 能访问 root 属主的扫描仓库 ──
# git 2.35.2+ 对非属主 .git 触发 dubious ownership → gitnexus index exit 1。
# 新建用户 gitconfig 为空，必须设；幂等：已含该通配则不重复加。
if id "$SUPERNOVA_USER" >/dev/null 2>&1; then
    if ! runuser -u "$SUPERNOVA_USER" -- git config --global --get-all safe.directory 2>/dev/null \
        | grep -Fxq "$SUPERNOVA_HOME/repos/*"; then
        echo ">> safe.directory += $SUPERNOVA_HOME/repos/*（$SUPERNOVA_USER）"
        runuser -u "$SUPERNOVA_USER" -- git config --global --add safe.directory "$SUPERNOVA_HOME/repos/*"
    else
        echo ">> safe.directory 已含 $SUPERNOVA_HOME/repos/*"
    fi
fi

echo ">> done：supernova-user 现可  su - $SUPERNOVA_USER → cd $SUPERNOVA_HOME → uv run supernova-whitebox ..."
