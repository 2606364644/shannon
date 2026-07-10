#!/usr/bin/env bash
# cleanup-shannon-py.sh — 清理「重构项目 shannon-py」的运行残留,可重复执行。
#
# 铁律:本脚本绝不触碰 /root/shannon(原始 TS 项目)的任何进程,
#       也绝不触碰 gitnexus 等共享组件。所有进程匹配一律用绝对路径锁死
#       /root/shannon-py / shannon_web,不会误伤 TS 的 node ./shannon / runner.js /
#       claude-agent-sdk 子进程。容器按 compose project=shannon-py 精确过滤。
#
# 用法见 usage()。
set -uo pipefail

REPO=/root/shannon-py
FRONTEND="$REPO/packages/web/frontend"
DRY_RUN=0
REMOVE=0

usage() {
  cat <<'EOF'
cleanup-shannon-py.sh — 清理重构项目 shannon-py 的运行残留(绝不触碰 /root/shannon)

用法:
  bash scripts/cleanup-shannon-py.sh [选项]

选项:
  -n, --dry-run   只打印将要清理的内容,不实际执行
      --rm        删除容器实例(默认仅 docker stop,保留实例方便重启)
  -h, --help      显示本帮助

例:
  bash scripts/cleanup-shannon-py.sh --dry-run   # 先预览清单
  bash scripts/cleanup-shannon-py.sh             # 清理(stop 容器 + 杀前端进程)
  bash scripts/cleanup-shannon-py.sh --rm        # 连容器实例一起删

注意:
  - 请用 `bash <file>` 或 `./<file>` 执行,不要 `bash -c "$(cat <file>)"`
    (后者会让脚本字面量出现在 shell cmdline 里,可能被 pgrep 自匹配)。
  - 杀进程建议以 root 执行(容器 + 跨用户进程才都清得掉)。
EOF
}

for arg in "$@"; do
  case "$arg" in
    -n|--dry-run) DRY_RUN=1 ;;
    --rm)         REMOVE=1 ;;
    -h|--help)    usage; exit 0 ;;
    *) echo "未知参数: $arg(用 -h 查看用法)" >&2; exit 2 ;;
  esac
done

# dry-run 时只打印命令,否则真正执行
run() { if (( DRY_RUN )); then echo "    [dry-run] $*"; else "$@"; fi; }

# 取匹配 pattern 的 PID,排除本脚本 shell 及其父进程(独立 .sh 调用下一般
# 不会命中调用者,这里是双保险,防止 bash -c 内联调用时自匹配)
safe_pids() {
  pgrep -f "$1" 2>/dev/null \
    | grep -v -x "$$" | grep -v -x "$PPID" || true
}

echo "=================================================="
echo " shannon-py 残留清理   (dry_run=$DRY_RUN, rm=$REMOVE)"
echo " 铁律:绝不触碰 /root/shannon(原始 TS),不碰 gitnexus"
echo "=================================================="

# ---- 1) 前端 vite + esbuild(路径锁死 /root/shannon-py/packages/web/frontend)----
echo "[1/4] 前端进程 vite / esbuild"
for pat in \
  "$FRONTEND/node_modules/.bin/vite" \
  "$FRONTEND/node_modules/@esbuild"; do
  pids=$(safe_pids "$pat")
  if [[ -n "$pids" ]]; then
    echo "  -> kill  (PID: $(echo $pids | tr '\n' ' '))"
    for p in $pids; do run kill "$p" 2>/dev/null || true; done
  else
    echo "  -- 无匹配: $(basename "$pat")"
  fi
done

# ---- 2) 宿主直跑的 shannon_web 后端(docker 容器内的不受影响,这里只杀宿主直跑)----
echo "[2/4] 宿主直跑的 shannon_web 后端(非容器)"
pids=$(safe_pids 'shannon_web\.app:app')
if [[ -n "$pids" ]]; then
  echo "  -> kill  (PID: $(echo $pids | tr '\n' ' '))"
  for p in $pids; do run kill "$p" 2>/dev/null || true; done
else
  echo "  -- 无宿主直跑后端(若用 docker 跑 web 则正常)"
fi

# ---- 3) Docker 容器(按 compose project=shannon-py 精确过滤,只动本项目的)----
echo "[3/4] Docker 容器 (label: com.docker.compose.project=shannon-py)"
containers=$(docker ps -a --filter "label=com.docker.compose.project=shannon-py" \
               --format '{{.Names}}' 2>/dev/null || true)
if [[ -n "$containers" ]]; then
  for c in $containers; do
    if (( REMOVE )); then
      echo "  -> docker rm -f $c"
      run docker rm -f "$c" >/dev/null
    else
      echo "  -> docker stop $c"
      run docker stop "$c" >/dev/null
    fi
  done
else
  echo "  -- 无 shannon-py 容器"
fi

# ---- 4) 验证 ----
echo "[4/4] 验证"
if ps aux | grep -E 'shannon_web|shannon-py/packages/web' | grep -qv grep; then
  echo "  ⚠ 仍有 shannon-py 进程残留:"
  ps aux | grep -E 'shannon_web|shannon-py/packages/web' | grep -v grep \
    | awk '{print "     ", $2, $11, $12, $13}'
else
  echo "  ✓ 无 shannon-py 进程残留"
fi
if ss -tlnp 2>/dev/null | grep -qE ':7878|:5173'; then
  echo "  ⚠ 端口仍在监听:"
  ss -tlnp 2>/dev/null | grep -E ':7878|:5173' | sed 's/^/     /'
else
  echo "  ✓ 端口 7878 / 5173 已释放"
fi
echo ""
ts_count=$(ps aux | grep -E '/root/shannon/(apps|node_modules)|node \./shannon' | grep -v grep | wc -l | tr -d ' ')
echo "  原始 TS /root/shannon 运行中进程数: $ts_count (本脚本未触碰,保持原样)"
echo "=================================================="
echo " 完成。"
