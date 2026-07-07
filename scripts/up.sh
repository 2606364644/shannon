#!/usr/bin/env bash
# scripts/up.sh —— 一键启动 web,自动判断 temporal 复用还是自建。
#
# 用法：
#   ./scripts/up.sh              # 等价 docker compose up -d --build web
#   ./scripts/up.sh --build      # 显式带 build
#   ./scripts/up.sh down         # 停掉
#   ./scripts/up.sh logs web     # 看日志（任意 docker compose 子命令透传）
#
# 逻辑：
#   检测宿主 7233 端口是否被占用（通常意味着已有外部 temporal 在跑）：
#     - 已占用 → 复用模式：加载 external-temporal override，不再起 compose 的 temporal
#     - 未占用 → 自建模式：--profile infra，compose 自建 temporal + web
#
# 设计要点：
#   - 不依赖 docker-compose.override.yml（那个是本地副本，会被 .gitignore 排除，
#     且在自建模式下会误合并导致冲突）。本脚本纯用 -f 显式加载模板文件。
#   - 模板 docker-compose.override.external-temporal.yml 已入库可分享。
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

# 如果本地存在 docker-compose.override.yml，警告并跳过它（避免自建模式误合并）
if [ -f docker-compose.override.yml ] && [ "$ACTION" != "down" ]; then
  echo "⚠️  检测到本地 docker-compose.override.yml（已被 .gitignore 排除）。" >&2
  echo "    本脚本用 -f 显式加载模板，本地 override 会干扰自动判断。" >&2
  echo "    建议：rm docker-compose.override.yml 后重试。" >&2
  echo "    （本次仍继续，但自动模式判断可能不准）" >&2
fi

OVERRIDE_FILE="docker-compose.override.external-temporal.yml"

if [ "$ACTION" = "up" ]; then
  if port_in_use; then
    echo ">> 检测到 7233 已被占用 → 复用外部 temporal 模式"
    if [ ! -f "$OVERRIDE_FILE" ]; then
      echo "❌ 缺少 $OVERRIDE_FILE（复用模式依赖它）。请检查仓库。" >&2
      exit 1
    fi
    docker compose -f docker-compose.yml -f "$OVERRIDE_FILE" up -d --build "$@" web
  else
    echo ">> 7233 空闲 → 自建 temporal 模式（--profile infra）"
    docker compose --profile infra up -d --build "$@" web
  fi
else
  # down / logs / ps / restart 等子命令：两种模式服务集不同，统一用 --profile infra
  # 覆盖最全（自建模式包含 temporal + web；复用模式只有 web，多带个 infra profile 无副作用）
  echo ">> 透传子命令: $ACTION $*"
  docker compose --profile infra "$ACTION" "$@"
fi
