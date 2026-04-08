#!/usr/bin/env bash
# PhynAI Agent — Guided Telegram Bot Setup
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${CYAN}[phynai]${NC} $*"; }
ok()   { echo -e "${GREEN}  ✔${NC} $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $*"; }
err()  { echo -e "${RED}  ✘${NC} $*"; exit 1; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_DIR/.env"

# ── Banner ───────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║   PhynAI — Telegram Bot Setup  🤖   ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${NC}"

# ── Instructions ─────────────────────────────────────────────────────
echo "  Follow these steps to create your Telegram bot:"
echo ""
echo "  1. Open Telegram and search for ${BOLD}@BotFather${NC}"
echo "  2. Send ${BOLD}/newbot${NC}"
echo "  3. Choose a display name (e.g. \"PhynAI Assistant\")"
echo "  4. Choose a username ending in 'bot' (e.g. \"phynai_dev_bot\")"
echo "  5. BotFather will give you an API token"
echo ""
echo "  The token looks like: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
echo ""

# ── Prompt for token ─────────────────────────────────────────────────
read -rp "  Paste your bot token: " TOKEN

# ── Validate format ──────────────────────────────────────────────────
if [[ ! "$TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
  err "Invalid token format. Expected: 123456789:ABCdef..."
fi
ok "Token format looks valid"

# ── Test connection ──────────────────────────────────────────────────
log "Testing connection to Telegram API..."
RESPONSE=$(curl -s "https://api.telegram.org/bot${TOKEN}/getMe" 2>/dev/null || true)

if echo "$RESPONSE" | grep -q '"ok":true'; then
  BOT_NAME=$(echo "$RESPONSE" | grep -o '"first_name":"[^"]*"' | cut -d'"' -f4)
  BOT_USER=$(echo "$RESPONSE" | grep -o '"username":"[^"]*"' | cut -d'"' -f4)
  ok "Connected! Bot: ${BOLD}${BOT_NAME}${NC} (@${BOT_USER})"
else
  err "Could not connect to Telegram API. Check your token and try again."
fi

# ── Write to .env ────────────────────────────────────────────────────
if [[ -f "$ENV_FILE" ]]; then
  if grep -q "^PHYNAI_TELEGRAM_TOKEN=" "$ENV_FILE"; then
    sed -i.bak "s|^PHYNAI_TELEGRAM_TOKEN=.*|PHYNAI_TELEGRAM_TOKEN=${TOKEN}|" "$ENV_FILE"
    rm -f "$ENV_FILE.bak"
    ok "Updated PHYNAI_TELEGRAM_TOKEN in .env"
  else
    echo "PHYNAI_TELEGRAM_TOKEN=${TOKEN}" >> "$ENV_FILE"
    ok "Added PHYNAI_TELEGRAM_TOKEN to .env"
  fi
else
  echo "PHYNAI_TELEGRAM_TOKEN=${TOKEN}" > "$ENV_FILE"
  ok "Created .env with PHYNAI_TELEGRAM_TOKEN"
fi

# ── Success ──────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}  Telegram bot configured! 🎉${NC}"
echo ""
echo "  Start the Telegram gateway:"
echo ""
echo "    phynai gateway telegram"
echo ""
echo "  Your bot: https://t.me/${BOT_USER}"
echo ""
