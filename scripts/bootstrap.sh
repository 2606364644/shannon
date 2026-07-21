#!/usr/bin/env bash
set -euo pipefail

# scripts/bootstrap.sh — Install external dependencies for supernova.
# Usage: bash scripts/bootstrap.sh [whitebox|blackbox|all] [--yes]

PROFILE="${1:-all}"
AUTO_YES=false
[[ "${2:-}" == "--yes" ]] && AUTO_YES=true

# ── 确保项目用户 supernova-user + 系统级 uv 就绪（幂等）──────────────
# 让「宿主 CLI 直跑扫描」可用 supernova-user（容器内身份不动，由 Dockerfile 决定）。
# 仅 root 生效；非 root 跑 bootstrap 时跳过（不阻断后续依赖安装）。
if [ "$(id -u)" -eq 0 ]; then
    bash "$(dirname "${BASH_SOURCE[0]}")/ensure-supernova-user.sh" \
        || echo "  ⚠ ensure-supernova-user 失败（非致命，继续）" >&2
fi

# ── Colors ──────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✅ $*${NC}"; }
warn() { echo -e "  ${YELLOW}⚠  $*${NC}"; }
fail() { echo -e "  ${RED}❌ $*${NC}"; }

# ── Helpers ─────────────────────────────────────────────────────────
has() { command -v "$1" &>/dev/null; }

confirm() {
    local msg="$1"
    if $AUTO_YES; then return 0; fi
    read -rp "$msg [Y/n] " ans
    [[ "${ans,,}" =~ ^(y|yes|)$ ]]
}

# ── Preflight: node / npm ───────────────────────────────────────────
if ! has npm; then
    fail "Node.js/npm is required but not found."
    echo "  Install from: https://nodejs.org/"
    echo "  On Ubuntu/Debian: curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs"
    exit 1
fi
ok "npm $(npm --version)"

# ── Install functions ───────────────────────────────────────────────

install_gitnexus() {
    if has gitnexus; then
        ok "gitnexus (already installed)"
        return 0
    fi
    if ! confirm "Install gitnexus (whitebox call graph engine)?"; then
        warn "gitnexus skipped"
        return 0
    fi
    echo "Installing gitnexus via npm..."
    local version="latest"
    # --ignore-scripts: onnxruntime-node's post-install downloads a "build
    # list" that fails on HTTP 302 (network/CDN) in many environments.
    # Skipping scripts avoids it; ONNX still works without the post-install.
    if ! npm install -g --ignore-scripts "gitnexus@${version}"; then
        fail "gitnexus installation failed."
        echo "  onnxruntime-node post-install can fail on HTTP 302 (network/CDN);"
        echo "  --ignore-scripts is already applied. If it still fails, try:"
        echo "    GITNEXUS_SKIP_OPTIONAL_GRAMMARS=1 npm install -g gitnexus@${version}"
        echo "  Manual: npm install -g gitnexus@${version}"
        return 1
    fi
    # --ignore-scripts also skipped @ladybugdb/core/install.js, which copies
    # the prebuilt native binary (lbugjs.node) into place. Run it now so
    # `gitnexus doctor` reports `native ✓ lbugjs.node loaded`.
    local npm_root install_js
    npm_root="$(npm root -g)"
    install_js="$npm_root/gitnexus/node_modules/@ladybugdb/core/install.js"
    if [[ -f "$install_js" ]]; then
        if ! node "$install_js"; then
            warn "@ladybugdb/core/install.js failed; native binary may be missing."
            echo "  Run \`gitnexus doctor\` to verify; manual: node \"$install_js\""
        fi
    else
        warn "@ladybugdb/core/install.js not found at $install_js"
    fi
    if has gitnexus; then
        ok "gitnexus installed"
    else
        fail "gitnexus not found after install."
        echo "  Manual: npm install -g gitnexus@${version}"
        return 1
    fi
}

install_playwright_cli() {
    if has playwright-cli; then
        ok "playwright-cli (already installed)"
        return 0
    fi
    if ! confirm "Install playwright-cli (blackbox browser automation)?"; then
        warn "playwright-cli skipped"
        return 0
    fi
    echo "Installing playwright-cli..."
    # Try the most likely package first, fallback to bare name.
    # Exact package name to be verified at install time — see spec §实现时需核实项.
    npm install -g @anthropic-ai/playwright-mcp@latest 2>/dev/null \
        || npm install -g playwright-cli@latest 2>/dev/null \
        || {
            fail "playwright-cli installation failed."
            echo "  Manual: npm install -g playwright-cli"
            return 1
        }
    if has playwright-cli; then
        ok "playwright-cli installed"
    else
        fail "playwright-cli not found after install."
        echo "  Manual: npm install -g playwright-cli"
        return 1
    fi
}

install_chromium() {
    if ! confirm "Install Chromium browser for playwright?"; then
        warn "chromium skipped"
        return 0
    fi
    echo "Installing Chromium for playwright..."
    npx playwright install chromium
    ok "chromium installed"
}

install_agent_browser() {
    if has agent-browser; then
        ok "agent-browser (already installed)"
        return 0
    fi
    if ! confirm "Install agent-browser (default blackbox browser engine)?"; then
        warn "agent-browser skipped"
        return 0
    fi
    echo "Installing agent-browser..."
    if ! npm install -g agent-browser@latest; then
        fail "agent-browser installation failed."
        echo "  Manual: npm install -g agent-browser"
        return 1
    fi
    echo "Downloading Chrome for agent-browser..."
    if ! agent-browser install; then
        fail "agent-browser install (Chrome download) failed."
        echo "  Manual: agent-browser install"
        return 1
    fi
    if has agent-browser; then
        ok "agent-browser installed"
    else
        fail "agent-browser not found after install."
        echo "  Manual: npm install -g agent-browser && agent-browser install"
        return 1
    fi
}

check_docker() {
    if has docker; then
        ok "docker"
    else
        warn "docker not found. Start infrastructure with: supernova-whitebox infra up"
    fi
}

# ── Run by profile ──────────────────────────────────────────────────

# When sourced (not executed), skip the profile dispatcher so individual
# install_* functions can be unit-tested in isolation via `source`.
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    return 0 2>/dev/null || true
fi

FAILED=0

echo ""
echo "=== Shannon Prerequisites Bootstrap (profile: $PROFILE) ==="
echo ""

case "$PROFILE" in
    whitebox)
        install_gitnexus || FAILED=1
        ;;
    blackbox)
        install_agent_browser || FAILED=1
        install_playwright_cli || FAILED=1
        install_chromium || FAILED=1
        ;;
    all)
        install_gitnexus || FAILED=1
        install_agent_browser || FAILED=1
        install_playwright_cli || FAILED=1
        install_chromium || FAILED=1
        check_docker
        ;;
    *)
        fail "Unknown profile: $PROFILE. Use: whitebox, blackbox, or all"
        exit 1
        ;;
esac

echo ""
if [[ $FAILED -eq 1 ]]; then
    fail "Some installations failed. See manual commands above."
    exit 1
else
    ok "All dependencies satisfied."
fi
