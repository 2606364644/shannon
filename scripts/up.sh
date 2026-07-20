#!/usr/bin/env bash
# scripts/up.sh —— 一键启动 web,自动判断 temporal 复用还是自建。
#
# 用法：
#   ./scripts/up.sh              # 启动（自动判断复用/自建），复用现有镜像不重建（秒起，生产式）
#   ./scripts/up.sh --dev        # 叠加 docker-compose.dev.yml：bind mount 源码，改 Python 免 rebuild（开发式）
#   ./scripts/up.sh --build      # 启动并强制重建镜像（改了依赖/前端/Dockerfile 后用）
#   ./scripts/up.sh restart web  # 重启进程（dev 下改了 Python 后让它加载新代码，免 rebuild）
#   ./scripts/up.sh down         # 停掉
#   ./scripts/up.sh logs web     # 看日志（任意 docker compose 子命令透传）
#
# --dev 与 --build 正交、可组合（./scripts/up.sh --dev --build）。
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

# C1: worker 常驻消费 WEB 固定 task queue，故 up 一并拉起 web worker。
#
# 参数解析：第一个非 flag 参数 = compose 子命令（默认 up）；--build / --dev 各自提取为
# up 专属标志（强制重建镜像 / 叠加开发 override），不透传给 docker compose；其余原样透传。
# 这样 `up.sh --build`、`up.sh --dev`、`up.sh --dev --build` 都能工作（flag 可放任意位置），
# 而裸 `up.sh` 复用现有镜像——适合只改了 workspaces/repos/.env 等挂载内容、没动 packages/ 源码的场景。
# 首次启动（镜像不存在）时 docker compose up 会自动构建（compose 有 build: 配置），无需显式 --build。
ACTION="up"
PASSTHROUGH=()
WANT_BUILD=0
WANT_DEV=0
_ACTION_SET=0
for _arg in "$@"; do
  if [ "$_arg" = "--build" ]; then
    WANT_BUILD=1
  elif [ "$_arg" = "--dev" ]; then
    WANT_DEV=1
  elif [ "$_ACTION_SET" = "0" ] && [[ ! "$_arg" =~ ^- ]]; then
    ACTION="$_arg"; _ACTION_SET=1
  else
    PASSTHROUGH+=("$_arg")
  fi
done
unset _arg _ACTION_SET
BUILD_FLAG=""
if [ "$WANT_BUILD" = "1" ]; then
  BUILD_FLAG="--build"
fi

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
DEV_FILE="docker-compose.dev.yml"

if [ "$ACTION" = "up" ]; then
  # 确保 docker 环境：buildx 就位 compose 才走 BuildKit（跨平台 Linux/WSL2/Mac）。
  # 失败（runtime 缺等）→ set -e 中止，不带着坏环境硬 build。down/logs 不 build 不调。
  bash "$SCRIPT_DIR/ensure-docker.sh"
  cleanup_stale_containers
  if [ "$WANT_BUILD" = "1" ]; then
    echo ">> --build：重建 web/worker 镜像（COPY 进镜像的 packages/ 源码会重新打入）"
  else
    echo ">> 复用现有镜像（未传 --build）。dev 下改 Python 用 restart；改依赖/前端再加 --build。"
  fi

  # 组装 compose 文件列表：base + temporal 模式（互斥）+ dev override（正交，可叠加）。
  # 用数组而非两条分支，便于 dev override 独立于 temporal 模式叠加在任一之上。
  COMPOSE_FILES=(-f docker-compose.yml)
  if port_in_use; then
    echo ">> 检测到 7233 已被占用 → 复用外部 temporal 模式"
    if [ ! -f "$OVERRIDE_FILE" ]; then
      echo "❌ 缺少 $OVERRIDE_FILE（复用模式依赖它）。请检查仓库。" >&2
      exit 1
    fi
    COMPOSE_FILES+=(-f "$OVERRIDE_FILE")
  else
    echo ">> 7233 空闲 → 自建 temporal 模式（主 compose 默认）"
  fi
  if [ "$WANT_DEV" = "1" ]; then
    if [ ! -f "$DEV_FILE" ]; then
      echo "❌ 缺少 $DEV_FILE（--dev 依赖它）。请检查仓库。" >&2
      exit 1
    fi
    echo ">> --dev：叠加 $DEV_FILE，bind mount packages/prompts 源码（改 Python restart 即生效，免 rebuild）"
    COMPOSE_FILES+=(-f "$DEV_FILE")
  fi

  # BUILD_FLAG 为空时需展开为无（不加引号是有意为之），故关闭 SC2086：
  # shellcheck disable=SC2086
  # PASSTHROUGH 可能为空数组：macOS 默认 /bin/bash 3.2 在 set -u 下展开空数组
  # "${ARR[@]}" 会报 unbound variable（4.4+ 才修）。用 ${ARR[@]+"${ARR[@]}"} 惯用法：
  # 空时展开为零参数，非空时正确分词（含空格元素），全 bash 版本兼容。
  docker compose "${COMPOSE_FILES[@]}" up -d $BUILD_FLAG ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"} web worker
else
  # down / logs / ps / restart 等子命令透传。
  # 注意：复用模式下若 web 接入了 shannon-net，down 只停 compose 管辖的服务，
  # 不影响外部 temporal 容器。
  # 用 PASSTHROUGH 而非 $@：参数解析已把 ACTION/--build/--dev 提取掉，$@ 仍是原始全部
  # 参数，直接用会把 ACTION 重复传一次（如 `down` → `docker compose down down`）。
  echo ">> 透传子命令: $ACTION ${PASSTHROUGH[*]}"
  docker compose "$ACTION" ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}
fi
