#!/usr/bin/env bash
set -euo pipefail

# scripts/bootstrap.sh — Install external dependencies for shannon-py.
# Usage: bash scripts/bootstrap.sh [whitebox|blackbox|all] [--yes]

PROFILE="${1:-all}"
AUTO_YES=false
[[ "${2:-}" == "--yes" ]] && AUTO_YES=true

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
    # GitNexus ships native deps (@ladybugdb/core, tree-sitter grammars).
    # npm runs install/build scripts by default, so no pnpm-style
    # onlyBuiltDependencies allow-list is needed — and this matches
    # GitNexus' official install method (npm install -g gitnexus).
    if ! npm install -g gitnexus@latest; then
        fail "gitnexus installation failed."
        echo "  If this is a C++ toolchain error, retry skipping optional grammars:"
        echo "    GITNEXUS_SKIP_OPTIONAL_GRAMMARS=1 npm install -g gitnexus@latest"
        echo "  Manual: npm install -g gitnexus@latest"
        return 1
    fi
    if has gitnexus; then
        ok "gitnexus installed"
    else
        fail "gitnexus not found after install."
        echo "  Manual: npm install -g gitnexus@latest"
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

check_docker() {
    if has docker; then
        ok "docker"
    else
        warn "docker not found. Start infrastructure with: shannon-whitebox infra up"
    fi
}

# ── Run by profile ──────────────────────────────────────────────────

FAILED=0

echo ""
echo "=== Shannon Prerequisites Bootstrap (profile: $PROFILE) ==="
echo ""

case "$PROFILE" in
    whitebox)
        install_gitnexus || FAILED=1
        ;;
    blackbox)
        install_playwright_cli || FAILED=1
        install_chromium || FAILED=1
        ;;
    all)
        install_gitnexus || FAILED=1
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
