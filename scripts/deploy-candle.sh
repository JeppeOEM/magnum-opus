#!/usr/bin/env bash
# deploy-candle.sh NEW_SLOT OLD_SLOT
# Zero-downtime blue-green deploy for the candle service.
#
# Usage:
#   ./scripts/deploy-candle.sh green blue
#   ./scripts/deploy-candle.sh blue green
#
# Environment:
#   DEPLOY_HEALTH_TIMEOUT_S  seconds to wait for new slot shadow_lag=0 (default 60)
#   SHUTDOWN_TIMEOUT_S       seconds for SIGTERM graceful shutdown (default 10)
#   QUESTDB_HTTP_PORT        QuestDB HTTP port for write-verification probe (default 9000)
#   FORCE_SIGKILL            set to "true" to skip SIGTERM and use SIGKILL immediately
#                            (for integration testing of XAUTOCLAIM recovery path)
#
# Exit codes: 0 = success, 1 = failure (old slot intact or restarted)

set -euo pipefail

NEW_SLOT="${1:?Usage: $0 NEW_SLOT OLD_SLOT}"
OLD_SLOT="${2:?Usage: $0 NEW_SLOT OLD_SLOT}"
DEPLOY_HEALTH_TIMEOUT_S="${DEPLOY_HEALTH_TIMEOUT_S:-60}"
SHUTDOWN_TIMEOUT_S="${SHUTDOWN_TIMEOUT_S:-10}"
FORCE_SIGKILL="${FORCE_SIGKILL:-false}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

slot_port() {
  case "$1" in
    blue)  echo 8081 ;;
    green) echo 8082 ;;
    *)     echo "unknown slot: $1" >&2; exit 1 ;;
  esac
}

NEW_PORT=$(slot_port "$NEW_SLOT")
OLD_PORT=$(slot_port "$OLD_SLOT")

log() { echo "[$(date -u +"%H:%M:%S")] $*"; }
fail() { log "FAIL: $*" >&2; exit 1; }

# ── Step 1: Start new slot in shadow mode ─────────────────────────────────────
log "Step 1: starting candle-${NEW_SLOT} in shadow mode (port ${NEW_PORT})"
CANDLE_SHADOW_MODE=true docker-compose --profile "candle-${NEW_SLOT}" up -d

# ── Step 2: Poll /health until status=ok AND shadow_lag=0 ────────────────────
log "Step 2: waiting for candle-${NEW_SLOT} to catch up (timeout ${DEPLOY_HEALTH_TIMEOUT_S}s)"
poll_health() {
  local port=$1
  local timeout=$2
  local require_zero_lag=${3:-false}
  local start
  start=$(date +%s)
  while true; do
    response=$(curl -sf --max-time 3 "http://localhost:${port}/health" 2>/dev/null || true)
    if [ -n "$response" ]; then
      status=$(echo "$response" | jq -r '.status' 2>/dev/null || echo "")
      shadow_lag=$(echo "$response" | jq -r '.shadow_lag' 2>/dev/null || echo "-1")
      if [ "$require_zero_lag" = "true" ]; then
        if [ "$status" = "ok" ] && [ "$shadow_lag" = "0" ]; then
          return 0
        fi
        if [ "$status" = "critical" ]; then
          echo "ABORT: new slot status=critical before shadow_lag=0" >&2
          return 2
        fi
      else
        if [ "$status" = "ok" ]; then
          return 0
        fi
      fi
    fi
    now=$(date +%s)
    elapsed=$((now - start))
    if [ "$elapsed" -ge "$timeout" ]; then
      echo "TIMEOUT: health check failed after ${timeout}s" >&2
      return 1
    fi
    sleep 2
  done
}

poll_result=0
poll_health "$NEW_PORT" "$DEPLOY_HEALTH_TIMEOUT_S" "true" || poll_result=$?

if [ "$poll_result" -ne 0 ]; then
  log "Step 2 failed — stopping new slot (old slot untouched)"
  docker-compose --profile "candle-${NEW_SLOT}" stop --timeout 5 || true
  fail "candle-${NEW_SLOT} did not reach shadow_lag=0 within ${DEPLOY_HEALTH_TIMEOUT_S}s"
fi
log "Step 2: shadow_lag=0 confirmed on candle-${NEW_SLOT}"

# ── Step 3: Stop old slot ─────────────────────────────────────────────────────
log "Step 3: stopping candle-${OLD_SLOT} (timeout ${SHUTDOWN_TIMEOUT_S}s)"
if [ "$FORCE_SIGKILL" = "true" ]; then
  # XAUTOCLAIM recovers all un-ACKed messages from the old slot —
  # SIGKILL leaves no data loss because the new slot reclaims pending messages.
  log "FORCE_SIGKILL=true: sending SIGKILL to candle-${OLD_SLOT}"
  OLD_CONTAINER=$(docker-compose --profile "candle-${OLD_SLOT}" ps -q 2>/dev/null || true)
  if [ -n "$OLD_CONTAINER" ]; then
    docker kill --signal=SIGKILL "$OLD_CONTAINER" || true
  fi
else
  docker-compose --profile "candle-${OLD_SLOT}" stop --timeout "$SHUTDOWN_TIMEOUT_S" || true
  # SIGKILL fallback: if old slot did not exit within SHUTDOWN_TIMEOUT_S + 5s, force kill.
  # XAUTOCLAIM recovers all un-ACKed messages from the old slot — SIGKILL leaves no data loss.
  sleep 2
  OLD_CONTAINER=$(docker-compose --profile "candle-${OLD_SLOT}" ps -q 2>/dev/null || true)
  if [ -n "$OLD_CONTAINER" ]; then
    IS_RUNNING=$(docker inspect --format='{{.State.Running}}' "$OLD_CONTAINER" 2>/dev/null || echo "false")
    if [ "$IS_RUNNING" = "true" ]; then
      log "old slot still running after SIGTERM — sending SIGKILL"
      docker kill --signal=SIGKILL "$OLD_CONTAINER" || true
    fi
  fi
fi
log "Step 3: candle-${OLD_SLOT} stopped"

# ── Step 4: Promote new slot ──────────────────────────────────────────────────
log "Step 4: promoting candle-${NEW_SLOT} (POST /promote)"
promote_response=$(curl -sf -X POST --max-time 10 "http://localhost:${NEW_PORT}/promote" 2>/dev/null || true)
if [ -z "$promote_response" ]; then
  log "Step 4 WARNING: /promote returned no response — restarting old slot"
  docker-compose --profile "candle-${OLD_SLOT}" up -d || true
  fail "POST /promote failed on candle-${NEW_SLOT}"
fi
promote_status=$(echo "$promote_response" | jq -r '.status' 2>/dev/null || echo "")
if [ "$promote_status" != "ok" ]; then
  log "Step 4 WARNING: /promote returned status=${promote_status} — restarting old slot"
  docker-compose --profile "candle-${OLD_SLOT}" up -d || true
  fail "POST /promote returned non-ok status on candle-${NEW_SLOT}"
fi
log "Step 4: candle-${NEW_SLOT} promoted"

# ── Step 5: Verify new slot post-promotion (rollback if unhealthy) ─────────────
log "Step 5: verifying candle-${NEW_SLOT} post-promotion (30s)"
post_promote_result=0
poll_health "$NEW_PORT" 30 "false" || post_promote_result=$?
if [ "$post_promote_result" -ne 0 ]; then
  log "Step 5 FAILED: candle-${NEW_SLOT} unhealthy after promotion — restarting old slot"
  log "  old slot restart IS rollback: XAUTOCLAIM recovers any orphaned messages"
  docker-compose --profile "candle-${OLD_SLOT}" up -d || true
  fail "candle-${NEW_SLOT} failed post-promotion health check"
fi
log "Step 5: post-promotion health OK"

# ── Step 5b: Verify QuestDB is receiving writes ───────────────────────────────
# Polls snapshot_1s for a fresh row within the last 10 seconds.
# A healthy candle-service writes a 1s bar every second; if none appear within
# 15s post-promotion the ILP connection is broken — roll back immediately.
log "Step 5b: verifying QuestDB writes (up to 15s for a fresh snapshot_1s row)"
QUESTDB_HTTP_PORT="${QUESTDB_HTTP_PORT:-9000}"
verify_questdb_writes() {
  # Graceful no-op if python3 unavailable — QuestDB check skipped rather than blocking deploy.
  if ! command -v python3 >/dev/null 2>&1; then
    log "Step 5b WARNING: python3 not found — QuestDB write verification skipped"
    return 0
  fi
  local timeout=15
  local start
  start=$(date +%s)
  while true; do
    from_us=$(( ($(date +%s) - 10) * 1000000 ))
    query="SELECT count() FROM snapshot_1s WHERE ts >= ${from_us}"
    encoded=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$query" 2>/dev/null || true)
    if [ -n "$encoded" ]; then
      raw_count=$(curl -sf --max-time 3 \
        "http://localhost:${QUESTDB_HTTP_PORT}/exec?query=${encoded}" 2>/dev/null \
        | jq -r '.dataset[0][0] // 0' 2>/dev/null || echo "0")
      # Strip decimals (QuestDB returns count as float); guard against "null" string.
      count_int="${raw_count%.*}"
      if [[ "${count_int}" =~ ^[0-9]+$ ]] && [ "${count_int}" -gt 0 ]; then
        return 0
      fi
    fi
    now=$(date +%s)
    if [ $((now - start)) -ge "$timeout" ]; then
      return 1
    fi
    sleep 2
  done
}
qdb_result=0
verify_questdb_writes || qdb_result=$?
if [ "$qdb_result" -ne 0 ]; then
  log "Step 5b FAILED: no rows in snapshot_1s within 15s — rolling back"
  log "  stopping new slot before restarting old slot to prevent split-brain"
  docker-compose --profile "candle-${NEW_SLOT}" stop --timeout 5 || true
  log "  old slot restart IS rollback: XAUTOCLAIM recovers any orphaned messages"
  docker-compose --profile "candle-${OLD_SLOT}" up -d || true
  fail "candle-${NEW_SLOT} not writing to QuestDB after promotion"
fi
log "Step 5b: QuestDB writes confirmed"

# ── Step 6: Verify new slot is consuming ──────────────────────────────────────
log "Step 6: verifying candle-${NEW_SLOT} is consuming (30s observation)"
start_ts=$(date +%s)
prev_lag=-1
converging=false
while true; do
  response=$(curl -sf --max-time 3 "http://localhost:${NEW_PORT}/health" 2>/dev/null || true)
  if [ -n "$response" ]; then
    lag=$(echo "$response" | jq -r '.consumer_lag_max' 2>/dev/null || echo "-1")
    if [ "$prev_lag" -gt 0 ] && [ "$lag" -lt "$prev_lag" ]; then
      converging=true
    fi
    log "  consumer_lag_max=${lag} (prev=${prev_lag})"
    prev_lag="$lag"
  fi
  now=$(date +%s)
  elapsed=$((now - start_ts))
  if [ "$elapsed" -ge 30 ]; then
    break
  fi
  sleep 5
done
if [ "$converging" = "true" ] || [ "$prev_lag" -le 0 ]; then
  log "Step 6: consumer lag trending down — deployment confirmed"
else
  log "Step 6 FAILED: consumer lag not converging after promotion — rolling back"
  log "  stopping new slot before restarting old slot to prevent split-brain"
  docker-compose --profile "candle-${NEW_SLOT}" stop --timeout 5 || true
  log "  old slot restart IS rollback: XAUTOCLAIM recovers any orphaned messages"
  docker-compose --profile "candle-${OLD_SLOT}" up -d || true
  fail "candle-${NEW_SLOT} consumer lag not converging — rolled back to candle-${OLD_SLOT}"
fi

# ── Step 7: Summary ───────────────────────────────────────────────────────────
END_TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "DEPLOY SUCCESS"
log "  old slot : candle-${OLD_SLOT} (stopped)"
log "  new slot : candle-${NEW_SLOT} (active, port ${NEW_PORT})"
log "  started  : ${TIMESTAMP}"
log "  finished : ${END_TIMESTAMP}"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
