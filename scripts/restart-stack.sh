#!/usr/bin/env bash
# PhynAI Agent — Stack Manager
# Usage: restart-stack.sh [gateway|paperclip|status|stop]
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${CYAN}[phynai]${NC} $*"; }
ok()   { echo -e "${GREEN}  ✔${NC} $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $*"; }
err()  { echo -e "${RED}  ✘${NC} $*"; }

PHYNAI_HOME="$HOME/.phynai"
PID_DIR="$PHYNAI_HOME/pids"
LOG_DIR="$PHYNAI_HOME/logs"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "$PID_DIR" "$LOG_DIR"

CMD="${1:-status}"

is_running() {
  local name="$1"
  local pidfile="$PID_DIR/${name}.pid"
  if [[ -f "$pidfile" ]]; then
    local pid
    pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    rm -f "$pidfile"
  fi
  return 1
}

start_process() {
  local name="$1"; shift
  local pidfile="$PID_DIR/${name}.pid"
  local logfile="$LOG_DIR/${name}.log"

  if is_running "$name"; then
    warn "$name is already running (PID $(cat "$pidfile"))"
    return
  fi

  log "Starting $name..."
  cd "$REPO_DIR"
  nohup "$@" >> "$logfile" 2>&1 &
  echo $! > "$pidfile"
  ok "$name started (PID $!, log: $logfile)"
}

stop_process() {
  local name="$1"
  local pidfile="$PID_DIR/${name}.pid"

  if ! is_running "$name"; then
    warn "$name is not running"
    return
  fi

  local pid
  pid=$(cat "$pidfile")
  log "Stopping $name (PID $pid)..."
  kill "$pid" 2>/dev/null || true
  sleep 1
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
  rm -f "$pidfile"
  ok "$name stopped"
}

show_status() {
  echo ""
  echo -e "${BOLD}  PhynAI Stack Status${NC}"
  echo ""
  for name in gateway paperclip; do
    if is_running "$name"; then
      echo -e "  ${GREEN}●${NC} $name  (PID $(cat "$PID_DIR/${name}.pid"))"
    else
      echo -e "  ${RED}●${NC} $name  (stopped)"
    fi
  done
  echo ""
}

case "$CMD" in
  gateway)
    stop_process gateway
    start_process gateway "$REPO_DIR/.venv/bin/phynai" gateway start
    ;;
  paperclip)
    stop_process paperclip
    start_process paperclip "$REPO_DIR/.venv/bin/phynai" paperclip start
    ;;
  status)
    show_status
    ;;
  stop)
    stop_process gateway
    stop_process paperclip
    ok "All processes stopped"
    ;;
  *)
    echo "Usage: $(basename "$0") [gateway|paperclip|status|stop]"
    echo ""
    echo "  gateway    (Re)start the PhynAI gateway"
    echo "  paperclip  (Re)start the paperclip service"
    echo "  status     Show running processes"
    echo "  stop       Stop all processes"
    exit 1
    ;;
esac
