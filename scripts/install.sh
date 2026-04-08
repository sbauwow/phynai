#!/usr/bin/env bash
# PhynAI Agent — Curl-pipe installer
# Usage: curl -fsSL https://raw.githubusercontent.com/sbauwow/phynai-agent/master/scripts/install.sh | bash
# Options: --no-venv, --branch <branch>
set -euo pipefail

# ── Colors ───────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${CYAN}[phynai]${NC} $*"; }
ok()   { echo -e "${GREEN}  ✔${NC} $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $*"; }
err()  { echo -e "${RED}  ✘${NC} $*"; exit 1; }

# ── Parse args ───────────────────────────────────────────────────────
BRANCH="main"
USE_VENV=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-venv)  USE_VENV=false; shift ;;
    --branch)   BRANCH="$2"; shift 2 ;;
    *)          warn "Unknown option: $1"; shift ;;
  esac
done

# ── Banner ───────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║        PhynAI Agent Installer        ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${NC}"

# ── Detect OS ────────────────────────────────────────────────────────
detect_os() {
  case "$(uname -s)" in
    Linux*)  OS="linux" ;;
    Darwin*) OS="macos" ;;
    *)       err "Unsupported OS: $(uname -s). PhynAI supports Linux and macOS." ;;
  esac
  log "Detected OS: ${BOLD}${OS}${NC}"
}

# ── Install uv ──────────────────────────────────────────────────────
install_uv() {
  if command -v uv &>/dev/null; then
    ok "uv already installed ($(uv --version))"
    return
  fi
  log "Installing uv package manager..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  if command -v uv &>/dev/null; then
    ok "uv installed ($(uv --version))"
  else
    err "Failed to install uv. Install manually: https://docs.astral.sh/uv/"
  fi
}

# ── Directories ──────────────────────────────────────────────────────
INSTALL_DIR="${PHYNAI_INSTALL_DIR:-$HOME/.phynai/phynai-agent}"
PHYNAI_HOME="$HOME/.phynai"
BIN_DIR="$HOME/.local/bin"

setup_dirs() {
  log "Creating PhynAI directory structure..."
  mkdir -p "$PHYNAI_HOME/sessions"
  mkdir -p "$PHYNAI_HOME/config"
  mkdir -p "$PHYNAI_HOME/logs"
  mkdir -p "$PHYNAI_HOME/pids"
  mkdir -p "$BIN_DIR"
  ok "Directories ready at $PHYNAI_HOME"
}

# ── Clone repo ───────────────────────────────────────────────────────
clone_repo() {
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    log "Existing install found, pulling latest..."
    cd "$INSTALL_DIR"
    git fetch origin
    git checkout "$BRANCH"
    git pull origin "$BRANCH"
    ok "Updated to latest on branch $BRANCH"
  else
    log "Cloning phynai-agent (branch: $BRANCH)..."
    git clone --branch "$BRANCH" --depth 1 \
      https://github.com/sbauwow/phynai-agent.git "$INSTALL_DIR"
    ok "Cloned to $INSTALL_DIR"
  fi
  cd "$INSTALL_DIR"
}

# ── Python env ───────────────────────────────────────────────────────
setup_python() {
  if [[ "$USE_VENV" == false ]]; then
    warn "Skipping venv creation (--no-venv)"
    return
  fi
  log "Creating Python 3.11 virtual environment..."
  uv venv --python 3.11 .venv
  ok "Virtual environment created"

  log "Installing dependencies with uv sync..."
  uv sync
  ok "Dependencies installed"
}

# ── Symlink CLI ──────────────────────────────────────────────────────
link_cli() {
  local cli_src="$INSTALL_DIR/phynai"
  local cli_dst="$BIN_DIR/phynai"

  # If there's a pyproject entry point, link .venv/bin/phynai instead
  if [[ -f "$INSTALL_DIR/.venv/bin/phynai" ]]; then
    cli_src="$INSTALL_DIR/.venv/bin/phynai"
  fi

  if [[ -L "$cli_dst" ]]; then
    rm "$cli_dst"
  fi
  ln -sf "$cli_src" "$cli_dst"
  ok "Symlinked phynai → $cli_dst"
}

# ── Env template ─────────────────────────────────────────────────────
create_env() {
  local env_file="$INSTALL_DIR/.env"
  if [[ -f "$env_file" ]]; then
    ok ".env already exists, not overwriting"
    return
  fi
  log "Creating .env template..."
  cat > "$env_file" <<'EOF'
# PhynAI Agent Configuration
PHYNAI_API_KEY=
PHYNAI_PROVIDER=openai
PHYNAI_MODEL=gpt-4o
PHYNAI_TELEGRAM_TOKEN=
PHYNAI_DISCORD_TOKEN=
PHYNAI_SESSION_DIR=~/.phynai/sessions
PHYNAI_LOG_LEVEL=warning
EOF
  ok ".env template created — edit $env_file to add your API key"
}

# ── Check PATH ───────────────────────────────────────────────────────
check_path() {
  if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    warn "$BIN_DIR is not in your PATH"
    echo ""
    echo "  Add this to your shell profile (~/.bashrc, ~/.zshrc, etc.):"
    echo ""
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
  fi
}

# ── Run ──────────────────────────────────────────────────────────────
detect_os
install_uv
setup_dirs
clone_repo
setup_python
link_cli
create_env
check_path

# ── Success ──────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}  ╔══════════════════════════════════════╗"
echo "  ║     PhynAI Agent installed! 🚀      ║"
echo -e "  ╚══════════════════════════════════════╝${NC}"
echo ""
echo "  Next steps:"
echo ""
echo "    1. Set your API key:"
echo "       export PHYNAI_API_KEY=sk-..."
echo ""
echo "    2. Run PhynAI:"
echo "       phynai chat"
echo ""
echo "    3. Optional gateway setup:"
echo "       phynai-agent/scripts/setup-telegram.sh"
echo "       phynai-agent/scripts/setup-discord.sh"
echo ""
echo -e "  Config: ${CYAN}$PHYNAI_HOME${NC}"
echo -e "  Install: ${CYAN}$INSTALL_DIR${NC}"
echo ""
