#!/usr/bin/env bash
# vps-deploy.sh — deploy one or all services to VPS
#
# Usage:
#   bash scripts/vps-deploy.sh <service>
#   bash scripts/vps-deploy.sh all
#
# Services: aggregator | candle | bot | gateway | dashboard | monitoring | infra | all
#
# Config (read from .env.deploy in repo root):
#   VPS=deploy@<tailscale-ip>
#   VPS_DIR=/home/deploy/magnum-opus
#   CANDLE_SLOT=blue          # current active candle slot (blue or green)
#   TELEGRAM_BOT_TOKEN=...    # optional — for deploy notifications

set -euo pipefail

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
info() { echo -e "${CYAN}→${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*" >&2; exit 1; }
hr()   { echo -e "${BOLD}────────────────────────────────────────────────${NC}"; }

# ── Load config ────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [ -f "${REPO_ROOT}/.env.deploy" ]; then
  set -a; source "${REPO_ROOT}/.env.deploy"; set +a
fi

VPS="${VPS:-}"
VPS_DIR="${VPS_DIR:-/home/deploy/magnum-opus}"
CANDLE_SLOT="${CANDLE_SLOT:-blue}"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID_DIGEST="${TELEGRAM_CHAT_ID_DIGEST:-}"

# ── Args ───────────────────────────────────────────────────────────────────────
SERVICE="${1:-}"
if [ -z "$SERVICE" ]; then
  echo "Usage: bash scripts/vps-deploy.sh <service>"
  echo "       bash scripts/vps-deploy.sh all"
  echo ""
  echo "Services: aggregator | candle | bot | gateway | dashboard | ml | monitoring | infra | all"
  exit 1
fi

if [ -z "$VPS" ]; then
  fail "VPS not set. Create .env.deploy with: VPS=deploy@<ip>"
fi

# ── Helpers ────────────────────────────────────────────────────────────────────
vps() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$VPS" "$@"; }

telegram_notify() {
  local msg="$1"
  [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID_DIGEST" ] && return 0
  curl -sf -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_CHAT_ID_DIGEST}" \
    -d "text=${msg}" \
    -d "parse_mode=Markdown" > /dev/null 2>&1 || true
}

git_pull_vps() {
  info "Pulling latest code on VPS..."
  vps "cd ${VPS_DIR} && git pull --ff-only" || {
    warn "git pull failed — uncommitted changes on VPS? Continuing with current code."
  }
}

tag_rollback() {
  local svc="$1"
  local img="magnum-opus-${svc}"
  vps "docker tag ${img}:latest ${img}:rollback 2>/dev/null || true"
}

smoke_test() {
  local svc="$1" url="$2" max="${3:-20}"
  info "Smoke test: ${url}"
  local i=0
  while [ $i -lt $max ]; do
    i=$((i + 1))
    local code
    code=$(vps "curl -sf -o /dev/null -w '%{http_code}' ${url} 2>/dev/null || echo 000")
    if [ "$code" = "200" ]; then
      ok "${svc} healthy (${i}/${max})"
      return 0
    fi
    printf "   waiting... %d/%d (HTTP %s)\r" "$i" "$max" "$code"
    sleep 3
  done
  echo ""
  warn "${svc} did not respond healthy after $((max * 3))s — check: make vps-logs SERVICE=${svc}"
  return 1
}

warn_open_positions() {
  local count
  count=$(vps "curl -sf 'http://localhost:9000/exec?query=select+count()+from+orders+where+status+not+in+(%27filled%27%2C%27cancelled%27%2C%27rejected%27)' 2>/dev/null \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d[\"dataset\"][0][0])' 2>/dev/null" || echo 0)
  if [ "${count:-0}" -gt 0 ]; then
    echo ""
    echo -e "${YELLOW}  ⚠  WARNING: ${count} open order(s) detected in QuestDB${NC}"
    echo "     The bot will reconcile state on restart — positions are NOT closed."
    echo "     Proceeding in 5 seconds... (Ctrl+C to abort)"
    echo ""
    sleep 5
  fi
}

confirm_step() {
  local svc="$1"
  local sha branch vps_ip
  sha=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
  branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
  vps_ip=$(echo "$VPS" | cut -d@ -f2)

  echo ""
  hr
  echo -e "  ${BOLD}Service:${NC} ${svc}"
  echo -e "  ${BOLD}VPS:${NC}     ${vps_ip}"
  echo -e "  ${BOLD}Commit:${NC}  ${sha} (${branch})"
  echo -e "  ${BOLD}Dir:${NC}     ${VPS_DIR}"
  hr
  echo ""
  printf "  Deploy in 3s... (Ctrl+C to abort) "
  for i in 3 2 1; do printf "%d " $i; sleep 1; done
  echo ""
  echo ""
}

# ── Service deploy functions ──────────────────────────────────────────────────
deploy_infra() {
  info "Starting infra (redis + questdb)..."
  vps "cd ${VPS_DIR} && docker compose up -d redis questdb"

  info "Waiting for QuestDB to be healthy..."
  local i=0
  while [ $i -lt 30 ]; do
    i=$((i + 1))
    if vps "curl -sf 'http://localhost:9000/exec?query=select+1' > /dev/null 2>&1"; then
      ok "QuestDB healthy"
      return 0
    fi
    printf "   waiting... %d/30\r" "$i"
    sleep 3
  done
  warn "QuestDB did not become healthy within 90s — continuing anyway"
}

deploy_aggregator() {
  confirm_step "aggregator"
  tag_rollback "aggregator"
  vps "cd ${VPS_DIR} && docker compose up -d --no-deps --build aggregator"
  smoke_test "aggregator" "http://localhost:8080/health"
}

deploy_candle() {
  confirm_step "candle (blue-green)"

  local new_slot old_slot
  if [ "$CANDLE_SLOT" = "blue" ]; then
    new_slot="green"; old_slot="blue"
  else
    new_slot="blue"; old_slot="green"
  fi

  info "Current slot: ${old_slot} → deploying to: ${new_slot}"

  # Build new image on VPS first
  vps "cd ${VPS_DIR} && docker compose build candle-${new_slot}"

  # Run blue-green switch on VPS
  vps "cd ${VPS_DIR} && bash scripts/deploy-candle.sh ${new_slot} ${old_slot}"

  # Update local state
  CANDLE_SLOT="$new_slot"
  # Persist to .env.deploy
  if [ -f "${REPO_ROOT}/.env.deploy" ]; then
    if grep -q "^CANDLE_SLOT=" "${REPO_ROOT}/.env.deploy"; then
      sed -i "s/^CANDLE_SLOT=.*/CANDLE_SLOT=${new_slot}/" "${REPO_ROOT}/.env.deploy"
    else
      echo "CANDLE_SLOT=${new_slot}" >> "${REPO_ROOT}/.env.deploy"
    fi
  fi
  ok "Candle deployed to ${new_slot} slot"
}

deploy_bot() {
  warn_open_positions
  confirm_step "bot"
  tag_rollback "bot"
  vps "cd ${VPS_DIR} && docker compose up -d --no-deps --build bot"
  smoke_test "bot" "http://localhost:8090/health" || {
    warn "Smoke test failed — rolling back"
    vps "cd ${VPS_DIR} && docker tag magnum-opus-bot:rollback magnum-opus-bot:latest && docker compose up -d --no-deps bot"
    fail "bot deploy failed — rolled back to previous image"
  }
}

deploy_gateway() {
  confirm_step "gateway"
  tag_rollback "gateway"
  vps "cd ${VPS_DIR} && docker compose up -d --no-deps --build gateway"
  smoke_test "gateway" "http://localhost:8083/health"
}

deploy_dashboard() {
  confirm_step "dashboard"
  tag_rollback "dashboard"
  vps "cd ${VPS_DIR} && docker compose up -d --no-deps --build dashboard"
  smoke_test "dashboard" "http://localhost:8050/health"
}

deploy_ml() {
  confirm_step "ml-service"
  tag_rollback "ml-service"
  vps "cd ${VPS_DIR} && docker compose up -d --no-deps --build ml-service"
  smoke_test "ml-service" "http://localhost:8000/health"
}

deploy_monitoring() {
  confirm_step "monitoring (prometheus / alertmanager / loki / promtail / grafana)"
  vps "cd ${VPS_DIR} && docker compose up -d --no-deps prometheus alertmanager loki promtail grafana"
  ok "Monitoring stack restarted"
}

# ── Dispatch ───────────────────────────────────────────────────────────────────
SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

case "$SERVICE" in
  infra)       git_pull_vps; deploy_infra ;;
  aggregator)  git_pull_vps; deploy_aggregator ;;
  candle)      git_pull_vps; deploy_candle ;;
  bot)         git_pull_vps; deploy_bot ;;
  gateway)     git_pull_vps; deploy_gateway ;;
  dashboard)   git_pull_vps; deploy_dashboard ;;
  ml)          git_pull_vps; deploy_ml ;;
  monitoring)  git_pull_vps; deploy_monitoring ;;

  all)
    git_pull_vps
    echo ""
    echo -e "${BOLD}Deploying all services to ${VPS}${NC}"
    echo ""

    deploy_infra
    deploy_aggregator
    deploy_candle
    deploy_gateway
    deploy_ml
    deploy_bot
    deploy_dashboard
    deploy_monitoring

    echo ""
    hr
    echo -e "  ${GREEN}${BOLD}All services deployed${NC}"
    echo -e "  Commit: ${SHA}"
    hr
    telegram_notify "✅ *magnum-opus full deploy complete*
Commit: \`${SHA}\`
VPS: $(echo "$VPS" | cut -d@ -f2)"
    exit 0
    ;;

  *)
    fail "Unknown service: ${SERVICE}. Choose: aggregator | candle | bot | gateway | dashboard | ml | monitoring | infra | all"
    ;;
esac

echo ""
ok "Done — ${SERVICE} @ ${SHA}"

telegram_notify "✅ *${SERVICE} deployed*
Commit: \`${SHA}\`"
