#!/usr/bin/env bash
# scripts/up.sh —— 一键启动 web,自动判断 temporal 复用还是自建。
#
# 用法：
#   ./scripts/up.sh              # 启动（自动判断复用/自建）；up 分支已硬编码 --build，无需显式传
#   ./scripts/up.sh down         # 停掉
#   ./scripts/up.sh logs web     # 看日志（任意 docker compose 子命令透传）
#
# 逻辑：
#   先预清理 compose 项目内失败/空壳容器（Created/Exited/Dead 态）——
#   专治"曾直接 docker compose up 失败，留下 shannon-py-{temporal,web} 空壳"在模式切换/端口检测时捣乱。
#   安全（双重，绝不误删外部 shannon-temporal）：
#     - docker compose ps 只列本项目(shannon-py)容器，物理上排除外部 shannon-temporal；
#     - 且只删非运行态，running 的容器（含外部 temporal）一律不动。
#   再检测宿主 7233 端口是否被占用（通常意味着已有外部 temporal 在跑）：
#     - 已占用 → 复用模式：-f 加载 external-temporal override，不起 compose 的 temporal
#     - 未占用 → 自建模式：裸 docker compose up（主 compose 默认就自建 temporal）
#
# 设计要点：
#   - 主 docker-compose.yml 默认就是"自建 temporal"，裸跑 docker compose up 不报错。
#   - 复用模式用 -f 显式加载 docker-compose.override.external-temporal.yml（入库模板）。
#   - 不依赖本地 docker-compose.override.yml（那个被 .gitignore 排除、且会干扰自动判断）。
set -euo pipefail

# 切到仓库根（脚本可能在子目录被调用）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# 默认等价 docker compose up -d --build web
ACTION="${1:-up}"
shift || true  # 剩余参数透传给 docker compose

# 检测 7233 端口是否被监听（docker-proxy / temporal 进程都会占）
port_in_use() {
  if command -v ss >/dev/null 2>&1; then
    ss -tln 2>/dev/null | grep -q ':7233'
  elif command -v netstat >/dev/null 2>&1; then
    netstat -tln 2>/dev/null | grep -q ':7233'
  else
    # 兜底：尝试连一下
    timeout 1 bash -c 'echo > /dev/tcp/127.0.0.1/7233' 2>/dev/null
  fi
}

# 清理 compose 项目内失败/空壳容器（Created/Exited/Dead 等非运行态）。
# 专治：曾直接 `docker compose up` 失败，留下 shannon-py-{temporal,web} 的 Created 态空壳，
#       这些空壳在后续模式切换 / 端口检测 / up 时捣乱。
# 安全保证（双重，绝不误删外部 shannon-temporal）：
#   1. docker compose ps 只列本项目(shannon-py)管辖的容器，物理上排除外部 shannon-temporal；
#   2. 只删 state ∈ {created, exited, dead}，running / restarting / paused 一律保留。
cleanup_stale_containers() {
  local stale
  stale=$(docker compose ps -a --format '{{.Name}}\t{{.State}}' 2>/dev/null \
          | awk -F'\t' '$2 == "created" || $2 == "exited" || $2 == "dead" {print $1}' \
          || true)
  if [ -n "$stale" ]; then
    echo ">> 清理 compose 项目内失败/空壳容器（非运行态；外部 shannon-temporal 不受影响）："
    printf '%s\n' "$stale" | sed 's/^/   - /'
    printf '%s\n' "$stale" | xargs -r docker rm -f >/dev/null
  fi
}

# 如果本地存在 docker-compose.override.yml，警告（会干扰自动判断）
if [ -f docker-compose.override.yml ] && [ "$ACTION" != "down" ]; then
  echo "⚠️  检测到本地 docker-compose.override.yml（已被 .gitignore 排除）。" >&2
  echo "    本脚本用 -f 显式加载模板，本地 override 会干扰自动判断。" >&2
  echo "    建议：rm docker-compose.override.yml 后重试。" >&2
  echo "    （本次仍继续，但自动模式判断可能不准）" >&2
fi

OVERRIDE_FILE="docker-compose.override.external-temporal.yml"

if [ "$ACTION" = "up" ]; then
  cleanup_stale_containers
  if port_in_use; then
    echo ">> 检测到 7233 已被占用 → 复用外部 temporal 模式"
    if [ ! -f "$OVERRIDE_FILE" ]; then
      echo "❌ 缺少 $OVERRIDE_FILE（复用模式依赖它）。请检查仓库。" >&2
      exit 1
    fi
    docker compose -f docker-compose.yml -f "$OVERRIDE_FILE" up -d --build "$@" web
  else
    echo ">> 7233 空闲 → 自建 temporal 模式（主 compose 默认）"
    docker compose up -d --build "$@" web
  fi
else
  # down / logs / ps / restart 等子命令透传。
  # 注意：复用模式下若 web 接入了 shannon-net，down 只停 compose 管辖的服务，
  # 不影响外部 temporal 容器。
  echo ">> 透传子命令: $ACTION $*"
  docker compose "$ACTION" "$@"
fi
