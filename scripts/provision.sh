#!/usr/bin/env bash
# scripts/provision.sh — idempotent, cross-distro one-shot provisioning so
# shannon-user can run whitebox scans on a fresh machine.
# Spec: docs/superpowers/specs/2026-07-13-shannon-user-provision-design.md
#
# 边界（spec §3）：装系统依赖 + 用户/权限/归属 + .venv + safe.directory + verify。
# 不起服务（up.sh 管）、不碰 .env 密钥、不改容器内身份。
set -euo pipefail

# ── Config (env-overridable) ──────────────────────────────────────────
SHANNON_USER="${SHANNON_USER:-shannon-user}"
SHANNON_HOME="${SHANNON_HOME:-/root/shannon-py}"
SHANNON_PYPI_INDEX="${SHANNON_PYPI_INDEX:-https://mirrors.aliyun.com/pypi/simple/}"
SHANNON_NPM_REGISTRY="${SHANNON_NPM_REGISTRY:-https://registry.npmmirror.com}"
SHANNON_GITNEXUS_VERSION="${SHANNON_GITNEXUS_VERSION:-1.6.8}"
SHANNON_SKIP_DOCKER="${SHANNON_SKIP_DOCKER:-0}"

# ── Logging helpers ──────────────────────────────────────────────────
_log()  { printf '\033[0;32m✓\033[0m %s\n' "$*"; }
_skip() { printf '\033[1;33m○\033[0m %s\n' "$*"; }
_fail() { printf '\033[0;31m✗\033[0m %s\n' "$*" >&2; }
_die()  { _fail "$*"; exit 1; }
_has()  { command -v "$1" >/dev/null 2>&1; }

# ── Pure: distro → package manager (unit-tested) ─────────────────────
# Pure: map distro ID / ID_LIKE → package manager. Echoes apt|dnf|yum|"".
# Empty = unsupported distro (main orchestrator errors out).
_pkg_mgr_for_id() {
    local id="${1:-}" id_like="${2:-}"
    if printf '%s\n%s\n' "$id" "$id_like" | grep -qwE 'debian|ubuntu'; then
        echo "apt"
    elif printf '%s\n%s\n' "$id" "$id_like" | grep -qwE 'rhel|centos|rocky|almalinux|fedora'; then
        echo "dnf"
    else
        echo ""
    fi
}

# Pure: parse an os-release file → package manager (apt|dnf|yum|"").
# Defaults to /etc/os-release. Empty = unsupported / file missing.
detect_pkg_mgr() {
    local osr="${1:-/etc/os-release}" id="" id_like=""
    if [ -f "$osr" ]; then
        id=$(grep -E '^ID=' "$osr" | head -1 | cut -d= -f2- | tr -d '"')
        id_like=$(grep -E '^ID_LIKE=' "$osr" | head -1 | cut -d= -f2- | tr -d '"')
    fi
    _pkg_mgr_for_id "$id" "$id_like"
}

# ── System-level steps (idempotent) ───────────────────────────────────

ensure_root() {
    [ "$(id -u)" -eq 0 ] || _die "provision 需要 root（建用户/装系统包/改权限）。请 sudo 跑。"
}

install_docker() {
    if [ "$SHANNON_SKIP_DOCKER" = "1" ]; then _skip "docker (SHANNON_SKIP_DOCKER=1)"; return; fi
    if _has docker; then _skip "docker 已就绪"; return; fi
    _log "安装 docker（get.docker.com 跨发行版）"
    curl -fsSL https://get.docker.com | sh
    # 启动 daemon：systemd 优先，WSL2 无 systemd 时 service
    if command -v systemctl >/dev/null 2>&1; then
        systemctl enable --now docker || _fail "systemctl enable docker 失败（可手动启动）"
    elif command -v service >/dev/null 2>&1; then
        service docker start || _fail "service docker start 失败（WSL2？手动 service docker start）"
    fi
}

# 用户 + 系统级 uv + safe.directory（委托 ensure-shannon-user.sh）
ensure_user_and_uv() {
    local script
    script="$(dirname "${BASH_SOURCE[0]}")/ensure-shannon-user.sh"
    [ -f "$script" ] || _die "未找到 $script"
    SHANNON_USER="$SHANNON_USER" SHANNON_HOME="$SHANNON_HOME" bash "$script"
}

install_node_system() {
    if [ -x /usr/bin/node ]; then _skip "node $(node --version 2>/dev/null)"; return; fi
    local pkg
    pkg="$(detect_pkg_mgr)"
    [ -n "$pkg" ] || _die "detect_pkg_mgr 返回空（不支持的发行版？），手动装 node 到 /usr/bin"
    _log "安装系统级 node 22 via $pkg（NodeSource）"
    if [ "$pkg" = apt ]; then
        curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
        apt-get install -y nodejs
    else
        curl -fsSL https://rpm.nodesource.com/setup_22.x | bash -
        $pkg install -y nodejs
    fi
    [ -x /usr/bin/node ] || _die "node 安装后 /usr/bin/node 仍不存在"
}

install_gitnexus_system() {
    if _has gitnexus; then _skip "gitnexus 已就绪"; return; fi
    _has npm || _die "npm 未就绪（install_node_system 应先跑）"
    _log "安装系统级 gitnexus@${SHANNON_GITNEXUS_VERSION}（npm --prefix=/usr）"
    npm config set registry "$SHANNON_NPM_REGISTRY" 2>/dev/null || true
    npm install -g --prefix=/usr --ignore-scripts "gitnexus@${SHANNON_GITNEXUS_VERSION}"
    # 补 ladybugdb binding（动态解析系统级 npm 全局根，不写死路径）
    local root install_js
    root="$(npm root -g --prefix=/usr 2>/dev/null || echo /usr/lib/node_modules)"
    install_js="$root/gitnexus/node_modules/@ladybugdb/core/install.js"
    if [ -f "$install_js" ]; then
        node "$install_js" || _fail "@ladybugdb/core/install.js 失败（gitnexus doctor 验）"
    else
        _fail "未找到 $install_js（gitnexus doctor 验 native binary）"
    fi
    _has gitnexus || _die "gitnexus 安装后仍不可用"
}

fix_root_acl() {
    # 让 shannon-user 穿越 /root 进 /root/shannon-py（不动 others；比 777 安全）。
    if runuser -u "$SHANNON_USER" -- test -x /root 2>/dev/null; then
        _skip "/root ACL（shannon-user 已可穿越）"; return; fi
    if command -v setfacl >/dev/null 2>&1; then
        _log "setfacl -m u:$SHANNON_USER:x /root"
        setfacl -m "u:$SHANNON_USER:x" /root || _die "setfacl 失败（装 acl 包或手动 chmod o+x /root）"
    else
        _log "无 setfacl，fallback chmod o+x /root"
        chmod o+x /root || _die "chmod o+x /root 失败"
    fi
}

fix_ownership() {
    # shannon-user 产物 + uv 元数据归 shannon-user；.env/.env.profiles 保持 root（密钥）。
    # ⚠️ 不 chown repos：repos 是 WEB(容器 root) 与 CLI(shannon-user) 共享的扫描目标，
    # chown 给任一方都让另一方 git dubious ownership（实测容器 root 访问 shannon-user
    # 属主 .git → exit 128，WEB 扫描挂）。repos 属主保持 clone 时的，dubious 靠
    # safe.directory 信任解决（shannon-user: ensure 设；容器 root: Dockerfile 设）。
    local d f
    for d in workspaces configs .venv; do
        if [ -e "$SHANNON_HOME/$d" ]; then
            chown -R "$SHANNON_USER:$SHANNON_USER" "$SHANNON_HOME/$d"
        fi
    done
    for f in uv.lock pyproject.toml; do
        [ -e "$SHANNON_HOME/$f" ] && chown "$SHANNON_USER:$SHANNON_USER" "$SHANNON_HOME/$f"
    done
    chown "$SHANNON_USER:$SHANNON_USER" "$SHANNON_HOME"
    _log "归属 → $SHANNON_USER（workspaces/configs/.venv + uv.lock/pyproject.toml；repos 不动）"
}

uv_sync_venv() {
    [ -d "$SHANNON_HOME" ] || _die "$SHANNON_HOME 不存在（先 git clone shannon-py 到此）"
    _log "uv sync（以 $SHANNON_USER 身份，走 $SHANNON_PYPI_INDEX）"
    runuser -u "$SHANNON_USER" -- sh -c \
        "cd '$SHANNON_HOME' && UV_DEFAULT_INDEX='$SHANNON_PYPI_INDEX' uv sync"
}

verify() {
    local fails=0
    _chk() {  # _chk <label> <cmd...>
        local label="$1"; shift
        if "$@" >/dev/null 2>&1; then _log "verify: $label"; else _fail "verify FAIL: $label"; fails=$((fails+1)); fi
    }
    _chk docker          _has docker
    _chk shannon-user    id "$SHANNON_USER"
    _chk uv              test -x /usr/local/bin/uv
    _chk node            test -x /usr/bin/node
    _chk gitnexus        _has gitnexus
    _chk "root reachable" runuser -u "$SHANNON_USER" -- test -x "$SHANNON_HOME"
    _chk .venv           runuser -u "$SHANNON_USER" -- test -d "$SHANNON_HOME/.venv"
    # safe.directory：接受 repos/* 通配 或 全局 *
    local sd
    sd="$(runuser -u "$SHANNON_USER" -- git config --global --get-all safe.directory 2>/dev/null || true)"
    if printf '%s\n' "$sd" | grep -Fxq "$SHANNON_HOME/repos/*" \
        || printf '%s\n' "$sd" | grep -Fxq '*'; then
        _log "verify: safe.directory"
    else
        _fail "verify FAIL: safe.directory"; fails=$((fails+1))
    fi
    # 端到端：shannon-user 真能跑 uv run shannon-whitebox
    if runuser -u "$SHANNON_USER" -- sh -c \
        "cd '$SHANNON_HOME' && uv run --no-sync shannon-whitebox --help" >/dev/null 2>&1; then
        _log "verify: uv run shannon-whitebox --help"
    else
        _fail "verify FAIL: uv run shannon-whitebox --help"; fails=$((fails+1))
    fi
    [ "$fails" -eq 0 ] || _die "verify: $fails 项未就绪（见上方 ✗）"
    echo "all green"
}

main() {
    ensure_root
    echo "=== shannon-py provision（$SHANNON_HOME，用户 $SHANNON_USER）==="
    install_docker
    ensure_user_and_uv          # 用户 + 系统级 uv + safe.directory（ensure）
    install_node_system
    install_gitnexus_system
    fix_root_acl
    fix_ownership
    uv_sync_venv
    verify
    echo ">> done：shannon-user 可跑 uv run shannon-whitebox；你可 bash scripts/up.sh 起 web"
}

# When sourced (not executed), expose functions only; skip the orchestrator
# so pure functions can be unit-tested in isolation.
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    return 0 2>/dev/null || true
fi

main "$@"
