#!/usr/bin/env bash
# scripts/ensure-docker.sh — 跨平台 docker 环境 health（Linux / WSL2 Ubuntu / Mac）。
#
# 确保 docker CLI 能找到 buildx plugin（~/.docker/cli-plugins/docker-buildx），
# 使 docker compose 走 BuildKit。根因（受控实验证明）：docker CLI 找不到 buildx →
# compose 退 legacy builder → legacy 不认 RUN --mount=type=cache，报
# "the --mount option requires BuildKit"。buildx 就位即根治。
# compose 本身不动（v5.x 是 2025+ 官方主线，功能同 v2；问题从不在 compose）。
# 设计：docs/superpowers/specs/2026-07-15-ensure-docker-cross-platform-design.md
#
# 结构：上半部纯函数（平台/架构/URL 判定，可 source 单测）；下半部 main（系统副作用，
# 直接执行时跑，被 source 时经 BASH_SOURCE guard 跳过，使纯函数可单独单测）。
set -euo pipefail

# ── 纯函数区（平台/架构/URL 判定，可 source 单测）──────────────────────

# uname -s (Linux/Darwin/...) → release platform slug (linux/darwin/"")。
_plat_for_uname_s() {
    case "$1" in
        Linux)  echo "linux" ;;
        Darwin) echo "darwin" ;;
        *)      echo "" ;;
    esac
}

# uname -m (x86_64/aarch64/arm64/...) → release arch slug (amd64/arm64/"")。
_arch_for_uname_m() {
    case "$1" in
        x86_64)        echo "amd64" ;;
        aarch64|arm64) echo "arm64" ;;
        *)             echo "" ;;
    esac
}

# buildx release URL：base + plat + arch + ver → 完整下载 URL。
# asset 命名 buildx-v<ver>.<plat>-<arch>（点分隔 + 重复版本号）。base 参数化便于镜像前缀。
_buildx_download_url() {
    local base="$1" plat="$2" arch="$3" ver="$4"
    echo "${base}/docker/buildx/releases/download/v${ver}/buildx-v${ver}.${plat}-${arch}"
}

# ── main：系统副作用（检测 runtime / 确保 buildx / 清理残留 / 验证）──
# 被 source（非直接执行）时只暴露纯函数，跳过 orchestrator，便于单测。
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    return 0 2>/dev/null || true
fi

# 配置（env 可覆盖；版本不设则运行时解析 latest stable，避免硬编码过时版本号）
SUPERNOVA_GH_BASE="${SUPERNOVA_GH_BASE:-https://github.com}"
CLI_PLUGINS_DIR="${HOME}/.docker/cli-plugins"

_log()  { printf '\033[0;32m✓\033[0m %s\n' "$*"; }
_skip() { printf '\033[1;33m○\033[0m %s\n' "$*"; }
_fail() { printf '\033[0;31m✗\033[0m %s\n' "$*" >&2; }
_die()  { _fail "$*"; exit 1; }
_has()  { command -v "$1" >/dev/null 2>&1; }

# 从 github releases/latest redirect 解析最新 tag（去 v 前缀）。失败返回空。
_resolve_latest() {
    local repo="$1" tag
    tag="$(curl -s -o /dev/null -w '%{redirect_url}\n' \
        "${SUPERNOVA_GH_BASE}/${repo}/releases/latest" 2>/dev/null \
        | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+$')" || tag=""
    echo "${tag#v}"
}

# 核心：确保 docker CLI 能找到 buildx plugin。compose 走 BuildKit 的前提。
# 判定：docker buildx version 退出 0 → 已就位 skip；否则下官方 release 到
# ~/.docker/cli-plugins/docker-buildx（docker CLI 搜索路径最高优先级，覆盖系统/dangling）。
_ensure_buildx() {
    local plat="$1" arch="$2" ver url
    if docker buildx version >/dev/null 2>&1; then
        _skip "buildx 已就绪（$(docker buildx version 2>/dev/null | head -1)）"
        return 0
    fi
    [ -n "$arch" ] || _die "不支持架构 $(uname -m)，无法下载 buildx（手动装到 $CLI_PLUGINS_DIR/docker-buildx）"
    if [ -n "${SUPERNOVA_BUILDX_VERSION:-}" ]; then
        ver="$SUPERNOVA_BUILDX_VERSION"
    else
        ver="$(_resolve_latest docker/buildx)"
    fi
    [ -n "$ver" ] || _die "无法解析 buildx latest（github 不可达？设 SUPERNOVA_BUILDX_VERSION=x.y.z 固定）"
    url="$(_buildx_download_url "$SUPERNOVA_GH_BASE" "$plat" "$arch" "$ver")"
    _log "装 buildx v${ver}（compose 走 BuildKit 的前提）"
    _log "  下 $url"
    mkdir -p "$CLI_PLUGINS_DIR"
    curl -fsSL "$url" -o "$CLI_PLUGINS_DIR/docker-buildx" || _die "下载 buildx 失败：$url"
    chmod +x "$CLI_PLUGINS_DIR/docker-buildx"
}

# 可选清理：Docker Desktop WSL 集成残留。不阻塞核心（default builder 才激活，
# ~/.docker buildx 已覆盖），但消掉 `docker buildx ls` 的 error、避免误导。
# 全程容错：失败仅 _fail warn，绝不 _die（非 root 删 /usr/local/lib 会失败，正常忽略）。
_cleanup_stale_buildx() {
    local p="/usr/local/lib/docker/cli-plugins/docker-buildx"
    # 1. dangling symlink：指向不存在的 /mnt/wsl/docker-desktop/...（Docker Desktop 未挂载）。
    if [ -L "$p" ] && [ ! -e "$p" ]; then
        if rm -f "$p" 2>/dev/null; then
            _log "清 dangling symlink：$p（Docker Desktop WSL 集成未挂载）"
        else
            _skip "dangling $p 无法删（非 root；~/.docker buildx 已覆盖，忽略）"
        fi
    fi
    # 2. desktop-linux error context：Docker Desktop WSL 集成的 npipe 端点在 WSL2 不可用
    #    （"protocol not available"）。不影响 build（default context 才激活）。
    #    仅提示，不自动删——context 是 Docker Desktop 管理的 docker 配置，删它有副作用；
    #    如需清：docker context rm desktop-linux。
    if docker context ls 2>/dev/null | grep -q 'desktop-linux'; then
        _skip "desktop-linux context 报 error（Docker Desktop npipe 不可用；不影响 build，如需清：docker context rm desktop-linux）"
    fi
}

main() {
    if ! _has docker; then
        cat >&2 <<'EOF'
✗ 未检测到 docker runtime。请先装：
   Linux / WSL2: curl -fsSL https://get.docker.com | sh
   Mac:          安装 Docker Desktop — https://www.docker.com/products/docker-desktop/
EOF
        exit 1
    fi
    local plat arch
    plat="$(_plat_for_uname_s "$(uname -s)")"
    arch="$(_arch_for_uname_m "$(uname -m)")"
    [ -n "$plat" ] || _die "不支持平台 $(uname -s)"
    [ -n "$arch" ] || _die "不支持架构 $(uname -m)"

    _ensure_buildx "$plat" "$arch"
    _cleanup_stale_buildx

    _log "buildx:  $(docker buildx version 2>&1 | head -1)"
    _log "compose: $(docker compose version 2>&1 | head -1)"
    _log "done：docker 环境 health OK（buildx 就位 → compose 走 BuildKit）"
}

main "$@"
