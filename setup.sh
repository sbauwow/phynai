#!/bin/bash
# ============================================================================
# PhynAI Agent Setup Script
# ============================================================================
# Quick setup for developers who cloned the repo manually.
#
# Usage:
#   ./setup.sh
#
# This script:
# 1. Finds Python 3.11+
# 2. Creates a virtual environment
# 3. Installs all dependencies via pip
# 4. Creates .env from template (if not exists)
# 5. Symlinks the 'phynai' CLI command into ~/.local/bin
# 6. Checks for optional tools (ripgrep, adb)
# ============================================================================

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo -e "${CYAN}⚡ PhynAI Agent Setup${NC}"
echo ""

# ============================================================================
# Python check
# ============================================================================

echo -e "${CYAN}→${NC} Checking Python 3.11+..."

PYTHON_CMD=""
for cmd in python3.11 python3.12 python3.13 python3; do
    if command -v "$cmd" &> /dev/null; then
        PY_MAJOR=$("$cmd" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
        PY_MINOR=$("$cmd" -c "import sys; print(sys.version_info.minor)" 2>/dev/null)
        if [ "$PY_MAJOR" = "3" ] && [ "$PY_MINOR" -ge "11" ]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [ -n "$PYTHON_CMD" ]; then
    PYTHON_FOUND_VERSION=$($PYTHON_CMD --version 2>/dev/null)
    echo -e "${GREEN}✓${NC} $PYTHON_FOUND_VERSION found"
else
    echo -e "${RED}✗${NC} Python 3.11+ not found."
    echo "  Install Python 3.11 or later:"
    echo "    sudo apt install python3.11 python3.11-venv   # Debian/Ubuntu"
    echo "    brew install python@3.11                      # macOS"
    echo "    sudo apk add python3                          # Alpine"
    exit 1
fi

# ============================================================================
# Virtual environment
# ============================================================================

echo -e "${CYAN}→${NC} Setting up virtual environment..."

if [ -d ".venv" ]; then
    echo -e "${CYAN}→${NC} Removing old .venv..."
    rm -rf .venv
fi

$PYTHON_CMD -m venv .venv
echo -e "${GREEN}✓${NC} .venv created ($PYTHON_FOUND_VERSION)"

source .venv/bin/activate

# ============================================================================
# Dependencies
# ============================================================================

echo -e "${CYAN}→${NC} Installing dependencies..."

pip install --upgrade pip -q
pip install -e . -q
pip install pytest pytest-asyncio ruff -q
echo -e "${GREEN}✓${NC} Dependencies installed"

echo -e "${CYAN}→${NC} Optional integrations (install separately if needed):"
echo -e "    Slack:   ${YELLOW}pip install 'phynai-agent[slack]'${NC}"
echo -e "    GitHub:  No extra packages — configure via ${YELLOW}phynai setup github${NC}"
echo -e "    Jira:    No extra packages — configure via ${YELLOW}phynai setup jira${NC}"
echo -e "    Google:  No extra packages — configure via ${YELLOW}phynai setup google${NC}"
echo -e "    Okta:    No extra packages — configure via ${YELLOW}phynai setup okta${NC}"
echo -e "    MS365:   No extra packages — configure via ${YELLOW}phynai setup ms365${NC}"

# ============================================================================
# Optional: ripgrep (for faster file search)
# ============================================================================

echo -e "${CYAN}→${NC} Checking ripgrep (optional, for faster search)..."

if command -v rg &> /dev/null; then
    echo -e "${GREEN}✓${NC} ripgrep found"
else
    echo -e "${YELLOW}⚠${NC} ripgrep not found (file search will use grep fallback)"
    read -p "Install ripgrep for faster search? [Y/n] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
        INSTALLED=false

        if command -v sudo &> /dev/null && sudo -n true 2>/dev/null; then
            if command -v apt &> /dev/null; then
                sudo apt install -y ripgrep && INSTALLED=true
            elif command -v dnf &> /dev/null; then
                sudo dnf install -y ripgrep && INSTALLED=true
            fi
        fi

        if [ "$INSTALLED" = false ] && command -v brew &> /dev/null; then
            brew install ripgrep && INSTALLED=true
        fi

        if [ "$INSTALLED" = false ] && command -v cargo &> /dev/null; then
            echo -e "${CYAN}→${NC} Trying cargo install (no sudo required)..."
            cargo install ripgrep && INSTALLED=true
        fi

        if [ "$INSTALLED" = true ]; then
            echo -e "${GREEN}✓${NC} ripgrep installed"
        else
            echo -e "${YELLOW}⚠${NC} Auto-install failed. Install manually:"
            echo "    sudo apt install ripgrep     # Debian/Ubuntu"
            echo "    brew install ripgrep         # macOS"
            echo "    cargo install ripgrep        # With Rust (no sudo)"
        fi
    fi
fi

# ============================================================================
# Optional: ADB (for Android tools)
# ============================================================================

echo -e "${CYAN}→${NC} Checking ADB (optional, for Android tools)..."

if command -v adb &> /dev/null; then
    ADB_VERSION=$(adb version 2>/dev/null | head -1)
    echo -e "${GREEN}✓${NC} $ADB_VERSION"
    DEVICE_COUNT=$(adb devices 2>/dev/null | grep -c 'device$' || true)
    if [ "$DEVICE_COUNT" -gt 0 ]; then
        echo -e "${GREEN}✓${NC} $DEVICE_COUNT Android device(s) connected"
    else
        echo -e "${YELLOW}⚠${NC} No Android devices connected (55 Android tools will be unavailable)"
    fi
else
    echo -e "${YELLOW}⚠${NC} ADB not found (55 Android tools will be unavailable)"
    echo "    sudo apt install android-tools-adb   # Debian/Ubuntu"
    echo "    brew install android-platform-tools   # macOS"
fi

# ============================================================================
# Environment file
# ============================================================================

if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        chmod 600 .env
        echo -e "${GREEN}✓${NC} Created .env from template (mode 600) — edit it to set your API key"
    fi
else
    echo -e "${GREEN}✓${NC} .env exists"
    ENV_PERMS=$(stat -c "%a" .env 2>/dev/null || stat -f "%Lp" .env 2>/dev/null)
    if [ "$ENV_PERMS" != "600" ]; then
        chmod 600 .env
        echo -e "${YELLOW}⚠${NC} Fixed .env permissions: ${ENV_PERMS} → 600 (owner read/write only)"
    fi
fi

if [ -f "$HOME/.phynai/.env" ]; then
    chmod 600 "$HOME/.phynai/.env" 2>/dev/null
fi
chmod 700 "$HOME/.phynai" 2>/dev/null

# ============================================================================
# PATH setup — symlink phynai into ~/.local/bin
# ============================================================================

echo -e "${CYAN}→${NC} Setting up phynai command..."

PHYNAI_BIN="$SCRIPT_DIR/.venv/bin/phynai"
mkdir -p "$HOME/.local/bin"
ln -sf "$PHYNAI_BIN" "$HOME/.local/bin/phynai"
echo -e "${GREEN}✓${NC} Symlinked phynai → ~/.local/bin/phynai"

SHELL_CONFIG=""
if [[ "$SHELL" == *"zsh"* ]]; then
    SHELL_CONFIG="$HOME/.zshrc"
elif [[ "$SHELL" == *"bash"* ]]; then
    SHELL_CONFIG="$HOME/.bashrc"
    [ ! -f "$SHELL_CONFIG" ] && SHELL_CONFIG="$HOME/.bash_profile"
else
    if [ -f "$HOME/.zshrc" ]; then
        SHELL_CONFIG="$HOME/.zshrc"
    elif [ -f "$HOME/.bashrc" ]; then
        SHELL_CONFIG="$HOME/.bashrc"
    elif [ -f "$HOME/.bash_profile" ]; then
        SHELL_CONFIG="$HOME/.bash_profile"
    fi
fi

if [ -n "$SHELL_CONFIG" ]; then
    touch "$SHELL_CONFIG" 2>/dev/null || true

    if ! echo "$PATH" | tr ':' '\n' | grep -q "^$HOME/.local/bin$"; then
        if ! grep -q '\.local/bin' "$SHELL_CONFIG" 2>/dev/null; then
            echo "" >> "$SHELL_CONFIG"
            echo "# PhynAI Agent — ensure ~/.local/bin is on PATH" >> "$SHELL_CONFIG"
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_CONFIG"
            echo -e "${GREEN}✓${NC} Added ~/.local/bin to PATH in $SHELL_CONFIG"
        else
            echo -e "${GREEN}✓${NC} ~/.local/bin already in $SHELL_CONFIG"
        fi
    else
        echo -e "${GREEN}✓${NC} ~/.local/bin already on PATH"
    fi
fi

# ============================================================================
# Create ~/.phynai directory
# ============================================================================

mkdir -p "$HOME/.phynai/sessions"
mkdir -p "$HOME/.phynai/logs"
echo -e "${GREEN}✓${NC} Created ~/.phynai/ directories"

# ============================================================================
# Done
# ============================================================================

echo ""
echo -e "${GREEN}✓ Setup complete!${NC}"
echo ""
echo "Next steps:"
echo ""
echo "  1. Edit your API key:"
echo "     vi $SCRIPT_DIR/.env"
echo ""
echo "  2. Start chatting:"
echo "     phynai chat"
echo ""
echo "  Or reload your shell first if phynai isn't found:"
echo "     source $SHELL_CONFIG"
echo ""
echo "Other commands:"
echo "  phynai run \"do something\"   # One-shot execution"
echo "  phynai serve                 # HTTP API on :8080"
echo "  phynai gateway telegram      # Telegram bot"
echo "  phynai version               # Check version"
echo ""
