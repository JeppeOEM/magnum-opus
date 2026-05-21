# Operational Procedure: Emergency Response

Runbook for urgent situations. Each section is a named scenario; jump to the one that matches.

---

## WAL-suspension: QuestDB WAL suspended

**Symptoms:** `questdb: WAL suspended — issuing RESUME WAL` appearing in logs repeatedly.
ILP write error rate spike. Bar data gaps in QuestDB.

**Automatic recovery:** The aggregator and candle-service automatically detect WAL suspension
and issue `RESUME WAL`. Wait 60 seconds to see if auto-recovery works.

**If auto-recovery fails:**

```bash
# 1. Check QuestDB WAL status:
curl -G "http://localhost:9000/exec" --data-urlencode "query=tables() where walEnabled = true and suspended = true"

# 2. Manually resume:
curl -G "http://localhost:9000/exec" --data-urlencode "query=RESUME WAL FROM snapshot_1s"
curl -G "http://localhost:9000/exec" --data-urlencode "query=RESUME WAL FROM snapshot_1m"
curl -G "http://localhost:9000/exec" --data-urlencode "query=RESUME WAL FROM snapshot_15m"

# 3. Check QuestDB memory:
curl http://localhost:9000/metrics | grep questdb_memory_total_bytes
```

**If QuestDB is OOM:** Restart QuestDB with more memory (`-Xmx` flag). If disk is full:
clean up old partitions beyond TTL:
```sql
ALTER TABLE snapshot_1s DROP PARTITION LIST '2026-01-01' ,'2026-01-02'; -- adjust dates
```

**See:** MR-08 (mission rule: don't auto-resume more than 3 times per hour).

---

## FEED-DOWN: Exchange feed disconnected > 5 minutes

**Symptoms:** `aggregator_feed_state == 0` alert. No ticks in `aggregator_ticks_total` for >5 min.
`coordinator: gap detected` appearing frequently.

**Step 1: Check exchange status**
- KuCoin status: https://kucoin.com/status
- Bybit status: https://status.bybit.com

If exchange has known outage: wait. Reconnect is automatic.

**Step 2: Check aggregator process**
```bash
docker compose logs aggregator --tail=50 --follow
```

Look for reconnect attempts: `kucoin: reconnect dial` or `bybit mux: reconnect factory failed`.

**Step 3: If reconnect loops without success**
```bash
# Restart aggregator (will reconnect from scratch):
docker compose restart aggregator

# Verify feeds come online:
watch -n 2 'curl -s http://localhost:8080/readyz'
# Wait for: {"status":"ready"}
```

**Step 4: Check candle-service for stream overflow**
After a long feed outage, the Redis stream may have been written with a backlog.
The candle-service emits `stream overflow gap emitted` if len > 45,000 on first connect.
This is expected — check that `gap_count` returns to 0 within 60 seconds of feed recovery.

---

## BOT-TIMEOUT: Bot-service positions unmanaged

**Symptoms:** `bus_timeout` WARN appearing in bot-service logs. `bot_heartbeat_timeout_total` spike.
Strategies not receiving events.

**Step 1: Identify affected strategies and their positions**
```bash
# From bot-service logs:
grep "bus_timeout" bot-service.log | tail -20

# Or from Prometheus:
curl -s http://localhost:9090/metrics | grep bot_pnl_usd
```

**Step 2: Check Redis and candle-service health**
```bash
# Is candle-service producing bars?
docker compose ps candle-blue candle-green

# Is Redis reachable?
redis-cli -u "$REDIS_URL" ping
```

**Step 3: If Redis is down — manually manage positions**

> **If positions are small and market is orderly:** Hold. Do not trade manually while
> bot-service is recovering — you may double up.
>
> **If positions are large (> 1% portfolio) or market is volatile:** Close via exchange UI.

Log your manual actions with timestamp for reconciliation.

**Step 4: Restart bot-service**
```bash
docker compose restart bot-service

# Watch for reconciliation output:
docker compose logs bot-service --follow | grep -E "reconcil|CRITICAL|WARNING"
```

Reconciliation will identify any positions not matching active strategies.

---

## CREDENTIAL-LEAK: API key found in log output

**Symptoms:** Raw API key string found in log output (Loki, terminal, CI logs).

**Immediate actions (within 5 minutes):**

1. **Rotate credentials immediately** on the exchange:
   - KuCoin: Account → API Management → Delete → Create new
   - Bybit: Account → API → Delete → Create new

2. **Update secrets in deployment:**
   ```bash
   # Update .env file (or secrets manager)
   # Restart services with new credentials:
   docker compose up -d aggregator bot-service
   ```

3. **Check Loki for extent of leak:**
   ```
   {service="aggregator"} |= "your_api_key_value"
   ```
   Note the time range of matching logs.

4. **Assess blast radius:**
   - Check exchange for unexpected orders or withdrawals in the leak window
   - If read-only key: assess what data was exposed
   - If trading key: check for unauthorized trades

5. **Rotate Loki retention if needed:** If logs are indexed externally, contact the log
   provider to purge the specific time range.

---

## POSITION-DRIFT: Position size doesn't match expected

**Symptoms:** Strategy's `_open_positions` dict shows different size than exchange reports.

**Step 1: Run reconciliation manually**
```bash
# Restart bot-service to trigger reconciliation:
docker compose restart bot-service

# Check reconciliation log output:
docker compose logs bot-service | grep -i reconcil
```

**Step 2: If reconciliation shows drift**
```bash
# Query QuestDB for recent fills:
curl -G "http://localhost:9000/exec" \
  --data-urlencode "query=SELECT ts, symbol, side, size FROM fills WHERE ts > now()-3600000000 ORDER BY ts DESC LIMIT 50"
```

Compare with exchange order history.

**Step 3: Correct the drift**
- If bot-service size is LARGER than actual: bot will try to close more than exists.
  Manually update `_open_positions` (restart with corrected reconciliation logic).
- If bot-service size is SMALLER than actual: there are uncounted positions.
  Close excess via exchange UI, then restart for clean reconciliation.

---

## DISK-FULL: VPS disk at 90%+

**Step 1: Find largest directories**
```bash
du -sh /var/lib/docker/volumes/*
du -sh /opt/magnum-opus/*
```

**Step 2: QuestDB cleanup (safest)**
```bash
# Drop old QuestDB partitions (confirm with TTL policy first):
# snapshot_1s: TTL=30 days — delete partitions > 35 days old
# snapshot_1m: TTL=365 days
# snapshot_15m: TTL=5 years
```

**Step 3: Docker cleanup**
```bash
docker system prune --volumes  # WARNING: removes stopped containers and unused volumes
docker image prune -a
```

**Step 4: Log cleanup**
```bash
# Loki data (if running locally):
find /var/lib/docker/volumes/ -name "*.log" -mtime +7 -delete
```
