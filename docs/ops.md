# Ops Runbook — magnum-opus aggregator

## Prerequisites

- Docker Engine ≥ 24 + Compose plugin
- Linode account with a running VM (tested on Linode 4GB)
- `.env` file in the project root (`chmod 600 .env`)

---

## Deployment Procedure

### 1. Take a VM snapshot before every deploy

**Before touching a running instance**, create a Linode VM snapshot. This is the rollback point.

Via Linode Cloud Manager:
1. Navigate to the VM → **Backups** → **Take Snapshot**.
2. Label the snapshot `pre-deploy-<git-sha>` so you can identify it later.
3. Wait for the snapshot to complete (typically 1–5 minutes for a 4 GB VM).

Via Linode CLI (if installed):
```bash
linode-cli linodes snapshot <linode-id> --label "pre-deploy-$(git rev-parse --short HEAD)"
```

### 2. Pull the new image and restart

```bash
# On the Linode VM, from the project directory:
git pull origin main
docker compose pull
docker compose up -d --remove-orphans
```

Compose will perform a rolling restart of the `aggregator` container only — Redis and QuestDB are not restarted unless their image tag changed.

### 3. Verify the deployment

```bash
# Check /version reports the expected git SHA:
curl -s http://localhost:8080/version | jq .

# Check /health reports "ok" (may be "starting" for up to 90 seconds):
curl -s http://localhost:8080/health | jq .
```

Expected `/version` response:
```json
{
  "version": "v1.2.3",
  "git_sha": "abc1234",
  "build_time": "2026-05-07T12:00:00Z",
  "go_version": "go1.24.0"
}
```

Expected `/health` response once all feeds are live:
```json
{
  "status": "ok",
  "connected_feeds": 4,
  "gap_count_24h": 0,
  "uptime_seconds": 42
}
```

### 4. Monitor for regressions

Watch logs for 2–5 minutes after deploy:
```bash
docker compose logs -f aggregator
```

Check Prometheus metrics for feed state:
```bash
curl -s http://localhost:8080/metrics | grep aggregator_feed_state
```

All values should be `1`. If any are `0` after 90 seconds, a feed failed to subscribe.

---

## Rollback Procedure (target: 5 minutes)

Use this procedure when the new version is broken and the pre-deploy snapshot exists.

### 1. Stop the broken instance

SSH to the Linode VM and stop compose:
```bash
docker compose down
```

### 2. Restore the Linode VM snapshot

Via Linode Cloud Manager:
1. Navigate to the VM → **Backups**.
2. Find the `pre-deploy-<git-sha>` snapshot taken before the failed deploy.
3. Click **Restore to Existing Linode** → select the current VM → confirm.
4. The VM reboots into the previous snapshot state (~2–4 minutes).

> **Warning:** restoring a snapshot overwrites all disk state. Ensure QuestDB data is either replicated or acceptable to lose back to snapshot time.

### 3. Verify rollback

Once the VM is back online:
```bash
curl -s http://localhost:8080/version | jq .git_sha
```

Confirm the SHA matches the previous version. Feeds should reconnect within 90 seconds.

---

## Credential Rotation

### Rotating exchange API keys

1. Generate new API key/secret on the exchange (KuCoin or Bybit UI).
2. Update `.env` on the VM: replace `KUCOIN_API_KEY` / `KUCOIN_API_SECRET` / `KUCOIN_API_PASSPHRASE` (or `BYBIT_*`).
3. Restart only the aggregator (no need to touch Redis or QuestDB):
   ```bash
   docker compose restart aggregator
   ```
4. Verify `/health` returns `"status":"ok"` within 90 seconds.
5. Revoke the old API key on the exchange UI.

### Rotating Redis password

1. Update Redis config and restart Redis: `docker compose restart redis`.
2. Update `REDIS_PASSWORD` in `.env`.
3. Restart aggregator: `docker compose restart aggregator`.

### Credential safety rules

- `.env` must be `chmod 600` and owned by the deploy user.
- Never log credentials — the aggregator binary redacts all keys matching `key`, `secret`, `password`, `passphrase`, `token` substrings from slog output.
- Never commit `.env` to git — it is listed in `.gitignore`.
- Rotate credentials immediately if the VM is compromised or a snapshot is shared with an untrusted party.

---

## Resource Limits Reference

| Service    | Memory | CPUs |
|------------|--------|------|
| aggregator | 600 MB | 2.0  |
| redis      | 2 GB   | —    |
| questdb    | 8 GB   | —    |
| bot        | 2 GB   | —    |

Adjust in `docker-compose.yml` under `deploy.resources.limits` if the VM has more or less RAM.

---

## Useful Commands

```bash
# View live logs
docker compose logs -f aggregator

# Restart only the aggregator (after .env change)
docker compose restart aggregator

# Stop everything
docker compose down

# Hard reset (destroys all volumes — DATA LOSS)
docker compose down -v

# Check resource usage
docker stats
```

---

## Bot Service

The bot service (`bot/`) runs strategy threads that consume Redis candle streams, compute signals, and execute orders via exchange REST APIs in paper mode. Strategies live in `bot-service/strategies/active/` and hot-reload without restart.

### Deploy

**Pre-deploy checklist** (run from `bot-service/` on the VM):

1. Verify all active strategies are in paper mode: `grep -r "paper_trading" strategies/active/` — all must return `True`.
2. Verify result files exist and pass for each strategy in `strategies/active/`:
   ```bash
   for s in OFIBot MACrossBot; do
     python3 -c "import json; d=json.load(open('_results/$s/fee_impact.json')); assert d['passes'], f'$s fee_impact FAILED'"
     python3 -c "import json; d=json.load(open('_results/$s/validation_report.json')); assert d['passes'], f'$s validation FAILED'"
     echo "$s: OK"
   done
   ```
3. Check current `/health` before touching anything:
   ```bash
   curl -s http://localhost:8090/health | jq .
   ```

**Deploy command:**
```bash
docker compose pull bot
docker compose up -d bot
```

Confirm `/health` returns `"status": "ok"` within 60 seconds:
```bash
curl -s http://localhost:8090/health | jq .status
```

Watch logs for 2–5 minutes:
```bash
docker compose logs -f bot
```

### Rollback

Tag-based rollback:

```bash
# 1. Stop the broken bot service
docker compose down bot

# 2. In docker-compose.yml, add or change the bot service `image:` field to pin
#    the previous known-good tag (e.g. image: ghcr.io/mrqdt/magnum-opus/bot:v1.2.3),
#    then pull and restart:
docker compose pull bot
docker compose up -d bot

# 3. Confirm /health returns ok within 60 seconds
curl -s http://localhost:8090/health | jq .
```

If Docker image isn't available, roll back via git:
```bash
git checkout <previous-commit> -- bot-service/
docker compose up -d --build bot
```

### Credential Rotation

The bot service uses up to 5 exchange credentials (KuCoin: API key, secret, passphrase; Bybit: API key, secret). All credentials live exclusively in `bot-service/.env` — never in the compose `environment:` block.

**Rotation steps (no-downtime):**

1. Generate new API key/secret on the exchange UI (KuCoin or Bybit).
2. Update `bot-service/.env` with the new credential values.
3. Restart the bot service:
   ```bash
   docker compose restart bot
   ```
4. Confirm `/health` returns `"status": "ok"` within 60 seconds:
   ```bash
   curl -s http://localhost:8090/health | jq .
   ```
5. Revoke the **old** credential on the exchange UI — only after the new one is confirmed working.
6. Verify logs show no auth errors:
   ```bash
   docker compose logs --tail=50 bot | grep -i "auth\|credential\|401\|403"
   ```

**Credential file format** (`bot-service/.env`):
```
KUCOIN_API_KEY=...
KUCOIN_API_SECRET=...
KUCOIN_API_PASSPHRASE=...
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
```

Do NOT commit `.env` to git. The file is in `.gitignore`.

### Alert Playbooks

#### `bot_heartbeat_timeout_total` spiking

**Meaning:** A strategy's event loop received no BarClose/Tick events for `bus_timeout_seconds` (300s for MACrossBot, 30s for OFIBot). The strategy may have been silently starved of events.

**Steps:**
1. Check strategy logs for the timeout:
   ```bash
   docker compose logs bot | grep "bus_timeout\|heartbeat_timeout"
   ```
2. Check Redis consumer lag for that strategy in the Grafana **Bot Service** dashboard under "Consumer Lag (Redis entries behind) by Strategy", or via:
   ```bash
   curl -s http://localhost:8090/metrics | grep bot_consumer_lag
   ```
3. If `close_on_bus_timeout=True` (OFIBot), verify open positions were closed:
   ```bash
   curl -s http://localhost:8090/health | jq .strategies
   ```
4. If positions were NOT closed and the strategy shows "restarting" — escalate to **manual close**: query `order_events` in QuestDB for open orders and cancel manually on the exchange UI.
5. If the timeout is intermittent (Redis slow), investigate Redis CPU and stream backlog.

#### `bot_strategy_restart_total` spiking

**Meaning:** The watchdog detected a crashed strategy thread and restarted it with exponential backoff (5s → 10s → 30s → 60s cap).

**Steps:**
1. Identify the failing strategy:
   ```bash
   curl -s http://localhost:8090/metrics | grep bot_strategy_restart_total
   ```
2. Check logs for the crash cause:
   ```bash
   docker compose logs bot | grep "strategy_thread_crashed\|strategy_load_error"
   ```
3. If the strategy crashes consistently (unrecoverable bug), move it to inactive to stop the restart loop:
   ```bash
   mv bot-service/strategies/active/ofi_bot.py bot-service/strategies/inactive/
   # FileWatcher detects the removal and stops the thread within bot_filewatcher_interval_s
   ```
4. Fix the underlying bug, move back to active, and verify it stays "running":
   ```bash
   curl -s http://localhost:8090/health | jq .strategies
   ```

#### `bot_orphaned_order_total` > 0

**Meaning:** The startup reconciliation found an order on the exchange that has no matching record in QuestDB `order_events`. This means either the ILP write failed after the order was placed, or the order was placed by a previous process and not recovered.

**Critical rule: Do NOT auto-cancel orphaned orders.**

**Steps:**
1. Identify the orphaned order exchange:
   ```bash
   curl -s http://localhost:8090/metrics | grep bot_orphaned_order_total
   ```
2. Query the `order_alerts` table in QuestDB to see the alert:
   ```sql
   SELECT * FROM order_alerts WHERE alert_type = 'orphaned_order' ORDER BY timestamp DESC LIMIT 10;
   ```
3. Log into the exchange UI and verify the orphaned order's current state (open, filled, or cancelled).
4. Check if there is a hedging position (a bot holding a long should not have an orphaned short cancelled without closing the long first).
5. Only cancel the orphaned order after confirming:
   - There is no open hedge that depends on it.
   - The order size is consistent with expected strategy position sizes.
6. If in doubt, close the entire position set manually and restart the strategy from scratch.
