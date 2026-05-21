# Operational Procedure: Rollback

Procedures for rolling back to a previous working state after a bad deployment.

---

## Candle-service rollback

**When:** New candle-service slot is producing incorrect bars (high `gap_count`,
wrong feature values, or bars not being written).

**Step 1: Verify the problem**
```bash
# Check recent bars from the new slot:
curl -G "http://localhost:9000/exec" \
  --data-urlencode "query=SELECT ts, gap_count, is_partial FROM snapshot_1s WHERE ts > now()-60000000 ORDER BY ts DESC LIMIT 20"

# If gap_count is persistently > 0 or bars are not appearing every second → rollback
```

**Step 2: Promote old slot back to active**

The old slot was stopped (Step 7 of deploy.md) but is still running in background.
If it was fully stopped (docker compose stop), restart it first:

```bash
# If blue was old slot and is now stopped:
docker compose up -d candle-blue

# Wait 10 seconds for it to start, then promote:
curl -X POST "http://localhost:8081/promote"

# Verify:
curl -s http://localhost:8081/healthz | jq '{slot, shadow_mode}'
# Expected: {"slot": "blue", "shadow_mode": false}
```

**Step 3: Stop the bad slot**
```bash
docker compose stop candle-green  # adjust to the bad slot
```

**Step 4: Verify bars resume**
```bash
# Wait 5 seconds, then check:
curl -G "http://localhost:9000/exec" \
  --data-urlencode "query=SELECT ts, gap_count FROM snapshot_1s ORDER BY ts DESC LIMIT 5"
```

**Duration of disruption:** Typically 15–30 seconds (time to restart old slot + XAUTOCLAIM recovery).

---

## Aggregator rollback

**When:** New aggregator produces incorrect ticks or high gap rates.

**Step 1: Stop new aggregator**
```bash
docker compose stop aggregator
```

**Step 2: Pull and start previous version**
```bash
# Tag the previous version image:
docker pull your-registry/aggregator:previous-tag

# Update docker-compose to use previous tag, then:
docker compose up -d aggregator
```

**Step 3: Verify feeds reconnect**
```bash
# Wait for startup gate:
watch -n 2 'curl -s http://localhost:8080/readyz'
# Expected: {"status":"ready"}

# Check metrics:
curl -s http://localhost:8080/metrics | grep aggregator_feed_state
# All values should be 1
```

**Duration of disruption:** Feed reconnect time + startup gate (up to STARTUP_TIMEOUT_SEC).

---

## Bot-service rollback

**When:** New bot-service version has bugs in strategy execution or order logic.

> ⚠️ **Before rolling back:** Close any open positions manually or wait for strategies
> to close them. A rollback with open positions may confuse the old version's reconciliation.

**Step 1: Stop new bot-service**
```bash
docker compose stop bot-service
```

**Step 2: Manually verify open positions**
Check exchange UI or:
```bash
# If bot-service is still responsive (fast query):
curl -s http://localhost:9090/metrics | grep bot_pnl_usd
```

**Step 3: Start previous version**
```bash
docker compose up -d bot-service --image your-registry/bot-service:previous-tag
```

**Step 4: Watch reconciliation**
```bash
docker compose logs bot-service --follow | grep -iE "reconcil|critical|warning" | head -20
```

Any previously-open positions will be flagged. Follow H-02 (unmanaged positions) if needed.

---

## Full system rollback

**When:** A major infrastructure change broke multiple services simultaneously.

```bash
# 1. Stop everything:
docker compose down

# 2. Checkout previous release tag:
git checkout v1.2.3  # adjust to last known good tag

# 3. Pull previous images:
docker compose pull

# 4. Bring everything up:
docker compose up -d

# 5. Verify in order:
# a. Redis: redis-cli ping
# b. QuestDB: curl http://localhost:9000/exec?query=SELECT+1
# c. Aggregator: curl http://localhost:8080/healthz
# d. Candle-service: curl http://localhost:8081/healthz
# e. Bot-service: docker compose logs bot-service | tail -20
# f. Gateway: curl http://localhost:8090/healthz
# g. Dashboard: curl http://localhost:8050/
```

**Data recovery after full restart:**
- Redis: streams survive if AOF/RDB are configured (check Redis persistence settings)
- QuestDB: data survives unless disk was corrupted
- B2 Parquet: immutable, always intact
- Open positions: check via exchange UI and reconciliation
