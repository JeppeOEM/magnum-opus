# Operational Procedure: Blue-Green Candle-service Deploy

**Last updated:** 2026-05-21
**Purpose:** Zero-downtime candle-service deployment using blue-green slots.
**Estimated time:** 5–10 minutes.

---

## Prerequisites

- SSH access to the VPS
- `docker compose` installed
- Candle-service Docker image built and pushed (`make build-candle`)
- `scripts/deploy-candle.sh` present and executable

---

## Overview

The candle-service runs as two slots:
- **Blue** → port 8081 (`CANDLE_SLOT=blue`)
- **Green** → port 8082 (`CANDLE_SLOT=green`)

Only one slot is "active" (writes bars to QuestDB and Redis) at a time.
The deploy process starts the incoming slot in shadow mode (XREAD warmup),
waits for it to catch up, then promotes it to active and stops the old slot.

---

## Step 1: Identify the active slot

```bash
# Check which slot is currently active (returns "blue" or "green")
curl -s http://localhost:8081/healthz | jq '.slot'
curl -s http://localhost:8082/healthz | jq '.slot'
```

Or check the docker compose status:
```bash
docker compose ps candle-blue candle-green
```

The active slot is the one with `shadow_mode: false` in its healthz response.

---

## Step 2: Pull new image

```bash
docker compose pull candle-blue candle-green
```

---

## Step 3: Start incoming slot in shadow mode

If active is **blue**, start **green** in shadow mode (and vice versa):

```bash
# If blue is active, start green:
CANDLE_SHADOW_MODE=true docker compose up -d candle-green

# If green is active, start blue:
CANDLE_SHADOW_MODE=true docker compose up -d candle-blue
```

The shadow slot will begin XREAD warmup immediately.

---

## Step 4: Wait for shadow lag < 100

```bash
# Poll shadow lag every 5 seconds (run from repo root)
bash scripts/deploy-candle.sh --wait-lag

# Or manually check:
INCOMING_PORT=8082  # adjust for actual incoming slot
while true; do
    LAG=$(curl -s "http://localhost:${INCOMING_PORT}/healthz" | jq '.shadow_lag // 999999')
    echo "Shadow lag: $LAG"
    if [ "$LAG" -lt 100 ]; then
        echo "Ready to promote!"
        break
    fi
    sleep 5
done
```

**Maximum wait time:** 5 minutes. If lag does not reach < 100 in 5 minutes, abort the deploy.
Check candle-service logs: `docker compose logs candle-green --tail=50`.

---

## Step 5: Promote incoming slot

```bash
# Promote incoming slot (it transitions from shadow XREAD to XREADGROUP)
INCOMING_PORT=8082  # adjust
curl -X POST "http://localhost:${INCOMING_PORT}/promote"

# Verify it's now active:
curl -s "http://localhost:${INCOMING_PORT}/healthz" | jq '{slot, shadow_mode}'
# Expected: {"slot": "green", "shadow_mode": false}
```

**XAUTOCLAIM:** After promotion, the incoming slot automatically claims any pending
messages from the old slot's consumer group. This takes up to a few seconds.

---

## Step 6: Verify new slot is producing bars

```bash
# Check that bars are being written (wait 5–10 seconds)
# Query QuestDB for recent bars:
curl -G "http://localhost:9000/exec" \
  --data-urlencode "query=SELECT ts, exchange, symbol, close, gap_count FROM snapshot_1s ORDER BY ts DESC LIMIT 10"

# Bars should have ts within the last 5 seconds and gap_count=0 (or small integer)
```

---

## Step 7: Stop old slot

```bash
# Stop old (now-inactive) slot:
docker compose stop candle-blue   # if green is now active
docker compose stop candle-green  # if blue is now active
```

---

## Step 8: Verify monitoring

Check Grafana:
- `candle_bars_total` rate should be unchanged (no dip)
- `candle_gap_total` may show a small spike at promotion time (normal: XAUTOCLAIM recovery)
- `aggregator_consumer_lag_ms` should return to baseline within 30 seconds

---

## Rollback

If the new slot produces incorrect data (high gap_count, incorrect feature values):

```bash
# Immediately promote old slot back:
OLD_PORT=8081  # adjust
curl -X POST "http://localhost:${OLD_PORT}/promote"

# Restart old slot if it was stopped:
docker compose up -d candle-blue  # or candle-green

# Then investigate new slot logs:
docker compose logs candle-green --tail=100
```

See `docs/nasa/OPERATIONAL-PROCEDURES/rollback.md` for full rollback procedure.

---

## Automated deploy script

```bash
# Full automated deploy (handles all steps above):
bash scripts/deploy-candle.sh [blue|green]

# The script will:
# 1. Pull new image
# 2. Start incoming slot in shadow mode
# 3. Poll lag until < 100 (max 5 min)
# 4. POST /promote
# 5. Verify bars in QuestDB
# 6. Stop old slot
# 7. Exit 1 if any step fails
```
