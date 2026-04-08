#!/usr/bin/env bash
# PhynAI Agent — Guided Discord Bot Setup
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
echo "  ║    PhynAI — Discord Bot Setup  🎮   ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${NC}"

# ── Instructions ─────────────────────────────────────────────────────
echo "  Follow these steps to create your Discord bot:"
echo ""
echo "  1. Go to ${BOLD}https://discord.com/developers/applications${NC}"
echo "  2. Click ${BOLD}\"New Application\"${NC} → name it (e.g. \"PhynAI\")"
echo "  3. Go to the ${BOLD}Bot${NC} tab on the left"
echo "  4. Click ${BOLD}\"Reset Token\"${NC} and copy the token"
echo "  5. Under ${BOLD}Privileged Gateway Intents${NC}, enable:"
echo "     - Message Content Intent"
echo "     - Server Members Intent (optional)"
echo ""

# ── Prompt for token ─────────────────────────────────────────────────
read -rp "  Paste your bot token: " TOKEN

if [[ -z "$TOKEN" ]]; then
  err "No token provided."
fi

# ── Basic validation (Discord tokens are base64-ish, 50+ chars) ─────
if [[ ${#TOKEN} -lt 50 ]]; then
  warn "Token seems short — Discord tokens are usually 70+ characters."
  read -rp "  Continue anyway? (y/N): " CONFIRM
  [[ "$CONFIRM" =~ ^[Yy]$ ]] || exit 1
fi
ok "Token accepted"

# ── Write to .env ────────────────────────────────────────────────────
if [[ -f "$ENV_FILE" ]]; then
  if grep -q "^PHYNAI_DISCORD_TOKEN=" "$ENV_FILE"; then
    sed -i.bak "s|^PHYNAI_DISCORD_TOKEN=.*|PHYNAI_DISCORD_TOKEN=${TOKEN}|" "$ENV_FILE"
    rm -f "$ENV_FILE.bak"
    ok "Updated PHYNAI_DISCORD_TOKEN in .env"
  else
    echo "PHYNAI_DISCORD_TOKEN=${TOKEN}" >> "$ENV_FILE"
    ok "Added PHYNAI_DISCORD_TOKEN to .env"
  fi
else
  echo "PHYNAI_DISCORD_TOKEN=${TOKEN}" > "$ENV_FILE"
  ok "Created .env with PHYNAI_DISCORD_TOKEN"
fi

# ── Generate invite URL ─────────────────────────────────────────────
echo ""
log "To invite the bot to your server, you need the Application ID."
echo ""
read -rp "  Paste your Application ID (from General Information): " APP_ID

if [[ -n "$APP_ID" ]]; then
  INVITE_URL="https://discord.com/api/oauth2/authorize?client_id=${APP_ID}&permissions=274877958144&scope=bot%20applications.commands"
  echo ""
  echo "  Invite URL:"
  echo -e "  ${CYAN}${INVITE_URL}${NC}"
  echo ""
  ok "Open this URL in your browser to add the bot to a server"
else
  warn "Skipped invite URL generation (no Application ID provided)"
fi

# ── Success ──────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}  Discord bot configured! 🎉${NC}"
echo ""
echo "  Start the Discord gateway:"
echo ""
echo "    phynai gateway discord"
echo ""
