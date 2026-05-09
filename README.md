# magnum-opus

Real-time crypto market data pipeline. Two Go services connect live exchange WebSocket feeds to a queryable time-series store and publish structured bars for downstream consumption.

- **aggregator** — WebSocket feeds from KuCoin (and optionally Bybit) → order book maintenance → Redis tick streams + QuestDB raw storage
- **candle-service** — Consumes ticks → 1-second OHLCV bars with 67 microstructure features → 7-timeframe cascade (1s → 1m → 5m → 15m → 1h → 4h → 1d) → QuestDB + Redis output + daily Parquet cold storage

## Table of Contents

- [What the system produces](#what-the-system-produces)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Running with Docker Compose](#running-with-docker-compose)
- [Dev Mode](#dev-mode)
- [Monitoring & Observability](#monitoring--observability)
- [Viewing Logs](#viewing-logs)
- [Health Endpoints](#health-endpoints)
- [Tests](#tests)
- [Blue-Green Deployment](#blue-green-deployment)
- [Configuration Reference](#configuration-reference)
- [Useful Commands](#useful-commands)
- [Log Message Reference](#log-message-reference)

---

## What the system produces

### Per-second bars (QuestDB — `snapshot_1s`)

Every second, for every subscribed symbol, the candle service flushes a row with 67 fields including:

- **OHLCV** — open, high, low, close price; buy/sell volume split by side
- **Order book** — mid-price, spread (bps), bid/ask depth, OFI (order flow imbalance)
- **Trade microstructure** — tick direction, max consecutive runs, block trade detection, volume-weighted average price
- **Volatility** — realized volatility (mid-price returns), spread variability
- **Quote stuffing** — stuffing ratio detection for anomaly monitoring
- **Gap audit** — `is_gap=true` marker rows when sequence gaps are detected

Bars cascade to wider timeframes automatically: `snapshot_1m`, `snapshot_5m`, `snapshot_15m`, `snapshot_1h`, `snapshot_4h`, `snapshot_1d`.

### Redis streams

| Stream | Content |
|--------|---------|
| `ticks:{exchange}:{symbol}` | Raw normalized ticks from aggregator (`type=tick\|gap\|snapshot`) |
| `candles:{exchange}:{symbol}` | Rolling partial bars (250ms publish interval) |
| `candles:close:{exchange}:{symbol}` | Closed 1s bars only |
| `ob_features:{exchange}:{symbol}` | Order book snapshot at each bar close |
| `gaps:log` | All gap events with cause and sequence count |

### Daily Parquet export

At 03:00 UTC the candle service flushes the previous day's `snapshot_1s` rows to Backblaze B2 in Parquet format. Configure `B2_*` env vars in `candle-service/.env` to enable; leave them blank to disable.

---

## Architecture

```
KuCoin WS / Bybit WS
        │
   aggregator :8080
   ├── maintains per-symbol order book
   ├── gap detection (seq number tracking)
   ├── snapshot state machine (buffering → live)
   └── writes to:
        ├── Redis  ticks:{exchange}:{symbol}   (normalized ticks + gaps + snapshot signals)
        └── QuestDB  raw_ticks                 (ILP, 500ms flush)
        │
   candle-service :8081  (blue slot)
   ├── XREADGROUP consumer on ticks streams
   ├── order book maintained per-symbol (seeded from snapshot signal)
   ├── 1s bar accumulation with 67 features
   ├── 7-timeframe cascade engine
   └── writes to:
        ├── QuestDB  snapshot_1s … snapshot_1d  (ILP, 500ms flush)
        ├── Redis    candles:*                   (partial + closed bars)
        └── Redis    ob_features:*               (order book snapshots)
        │
   Prometheus :9090    scrapes :8080 and :8081 every 10s
   Alertmanager :9093  evaluates alert rules
   Grafana :3000       dashboards + Loki log view
   Loki :3100          log aggregation backend
   Promtail            ships WARN/ERROR logs from all containers → Loki
```

---

## Prerequisites

- **Docker** and **Docker Compose v2** — `docker compose version` must return v2.x
- **Go 1.22+** — only needed for `make dev` and `make test`
- **jq** and **curl** — only needed for the blue-green deploy script
- **Python 3** — used by `scripts/logfmt.py` (bundled, no install needed)

---

## Quick Start

```bash
# 1. Copy credential templates
cp .env.example .env
cp candle-service/.env.example candle-service/.env

# 2. Start everything (builds images, starts candle-blue slot)
make up

# 3. Verify both services are healthy
curl -s http://localhost:8080/health | jq .   # aggregator
curl -s http://localhost:8081/health | jq .   # candle-service

# 4. Open Grafana dashboard
open http://localhost:3000
```

`make up` uses `KUCOIN_PUBLIC=true` by default — no API credentials required for KuCoin public feeds.

After ~30 seconds you should see ticks flowing and the aggregator status line updating in the terminal.

---

## Running with Docker Compose

### Start

```bash
make up                   # candle-blue slot, formatted logs
make up SLOT=green        # green slot instead
make up VERBOSE=1         # raw JSON logs (no formatting)
make watch                # warnings and errors only (no status noise)
```

`make up` builds images, starts all services, and pipes output through `scripts/logfmt.py` which gives a clean status line with per-symbol tick rates.

### Stop

```bash
make down                 # stops and removes all containers
```

### Individual service logs (when running detached)

```bash
docker compose --profile candle-blue up -d --build   # detached
make logs                                              # tail aggregator
docker compose logs -f candle-blue                    # tail candle
docker compose logs -f prometheus alertmanager grafana loki
```

### Adding symbols

Edit `config.yaml` to control which symbols the aggregator subscribes to:

```yaml
exchanges:
  kucoin:
    symbols: [BTC-USDT, ETH-USDT, SOL-USDT]
  # bybit:
  #   symbols: [BTCUSDT, ETHUSDT]
```

Then update the matching list in `candle-service/.env`:

```
SYMBOLS_KUCOIN=BTC-USDT,ETH-USDT,SOL-USDT
```

Both files must list the same symbols or the candle service will have consumers with no corresponding tick streams.

### Adding Bybit

1. Uncomment the `bybit:` section in `config.yaml`
2. Add credentials to `.env`: `BYBIT_API_KEY` and `BYBIT_API_SECRET`
3. Set `SYMBOLS_BYBIT=BTCUSDT,ETHUSDT` in `candle-service/.env`

---

## Dev Mode

Runs Redis and QuestDB in Docker; both Go services run locally via `go run` for fast iteration without rebuilding images.

### Setup

```bash
cp .env.example .env
cp candle-service/.env.example candle-service/.env
# Set REDIS_ADDR=localhost:6379, QUESTDB_ILP_ADDR=localhost:9009, etc. in .env
# Set REDIS_URL=redis://localhost:6379 in candle-service/.env
```

### Start

```bash
make dev-infra          # Redis + QuestDB detached
make dev                # aggregator + candle-service, interleaved logs (Ctrl+C stops both)
```

Or run them in separate terminals:

```bash
# Terminal 1
make dev-aggregator

# Terminal 2
make dev-candle
```

Override any variable inline:

```bash
LOG_LEVEL=debug REDIS_ADDR=myredis:6379 make dev-aggregator
```

### Stop

```bash
make dev-infra-down     # stops and removes Redis + QuestDB
```

---

## Monitoring & Observability

### Grafana — http://localhost:3000

No login required (anonymous Viewer). One pre-provisioned dashboard: **Magnum Opus**.

| Row | What it shows |
|-----|---------------|
| **System Health Overview** | Single-stat OK/FAILING for aggregator and candle service |
| **Condition Matrix** | All individual health conditions across both services as a colour-coded table |
| **Aggregator — USE** | Ticks/s per symbol, consumer lag (ms), gap rate + feed state |
| **Candle Service — USE** | Bars/s per symbol, consumer lag (messages), flush + publish failures |
| **Gaps** | Gap rate by cause (why), gap rate by symbol (which), cumulative gap counts table |
| **Warnings & Errors** | Aggregated WARN/ERROR counts by service + message; recent log stream with deduplication |

The dashboard time range controls all panels. Set it to "Last 1 hour" for normal monitoring; "Last 15 minutes" for debugging an active incident.

### Prometheus — http://localhost:9090

Raw metrics scrape from both services. Useful for ad-hoc PromQL queries and confirming alert state.

Key metrics:

| Metric | Type | Description |
|--------|------|-------------|
| `aggregator_health{condition}` | Gauge | 1=ok, 0=failing — one per condition |
| `aggregator_ticks_total{exchange,symbol}` | Counter | Total ticks processed |
| `aggregator_gap_total{exchange,symbol,cause}` | Counter | Total gap events by cause |
| `aggregator_feed_state{exchange,symbol}` | Gauge | 1=live, 0=buffering |
| `candle_health{condition}` | Gauge | 1=ok, 0=failing |
| `candle_bars_total{exchange,symbol}` | Counter | Closed 1s bars written |
| `candle_consumer_lag{exchange,symbol}` | Gauge | Redis stream consumer lag (messages) |
| `candle_flush_failures_total` | Counter | Parquet flush failures |

### Alertmanager — http://localhost:9093

Evaluates alert rules every 10 seconds. Configured alerts:

| Alert | Severity | Condition | Fires after |
|-------|----------|-----------|-------------|
| `FeedsDown` | critical | Any exchange feed disconnected | 1 minute |
| `ConsumerLagHigh` | warning | Consumer lag > 1000 messages | 3 minutes |
| `QuestDBSuspended` | warning | QuestDB WAL suspended | 2 minutes |
| `FlushFailing` | info | Daily Parquet flush failing | 10 minutes |
| `AggregatorGapsDetected` | info | Gap events occurring | 5 minutes |

Alertmanager routes are in `monitoring/alertmanager/alertmanager.yml`. Add receivers (email, Slack, PagerDuty) there.

### QuestDB — http://localhost:9000

Browser SQL console. Run queries against the live tables:

```sql
-- Last 10 closed 1s bars for BTC-USDT
SELECT ts, open, high, low, close, volume, ofi, spread_bps
FROM snapshot_1s
WHERE symbol = 'BTC-USDT' AND exchange = 'kucoin'
ORDER BY ts DESC
LIMIT 10;

-- Check for gap markers in the last hour
SELECT ts, exchange, symbol, gap_cause
FROM snapshot_1s
WHERE is_gap = true
  AND ts > dateadd('h', -1, now())
ORDER BY ts DESC;

-- Recent raw ticks
SELECT ts, exchange, symbol, price, size, side
FROM raw_ticks
ORDER BY ts DESC
LIMIT 20;
```

---

## Viewing Logs

### Terminal (live)

```bash
# All services (formatted, status line every second)
make up

# Aggregator only
make logs
# or
docker compose logs -f aggregator

# Candle service
docker compose logs -f candle-blue

# Monitoring stack
make monitoring-logs
# or
docker compose logs -f prometheus alertmanager grafana loki promtail

# Warnings and errors only (no INFO noise)
make watch
```

### Grafana — Warnings & Errors row

The **Warnings & Errors** row at the bottom of the Grafana dashboard (http://localhost:3000) shows:

- **Warn/Error counts table** — all unique (service, level, message) combinations with occurrence counts over the selected time range, sorted by count descending. Turns yellow at 5, red at 20.
- **Recent Warnings & Errors** — live log stream from all services filtered to WARN and ERROR. Identical consecutive entries are collapsed (`dedupStrategy: exact`). Expand any entry to see the full structured fields.

Promtail ships logs from all containers to Loki automatically. Only WARN and ERROR lines reach Loki — INFO/DEBUG are dropped at the shipper to keep storage lean.

### Log format

Both services emit structured JSON to stdout (via Go's `slog.NewJSONHandler`). Each line is a JSON object:

```json
{
  "time": "2024-01-15T12:00:01.234Z",
  "level": "WARN",
  "msg": "coordinator: gap detected",
  "exchange": "kucoin",
  "symbol": "BTC-USDT",
  "cause": "external_disconnect",
  "seq_before": 1000,
  "seq_after": 1005
}
```

When running via `make up`, `scripts/logfmt.py` parses these and renders a readable status line. When running via `docker compose logs`, raw JSON is shown.

### What to look for

| Log message | Level | Meaning |
|-------------|-------|---------|
| `aggregator starting` | INFO | Normal startup |
| `coordinator: book live` | INFO | Order book seeded — data quality good from this point |
| `coordinator: gap detected` | WARN | Sequence gap in exchange feed — see `gap_cause` field |
| `coordinator: panic in symbol goroutine` | ERROR | Rare internal error; service auto-recovers |
| `candle: consumer cold-start buffer overflow` | WARN | Buffer filled before snapshot signal; gap emitted |
| `candle: QuestDB WAL suspended` | WARN | QuestDB internal write stall; bars buffered in memory |
| `candle: flusher: B2 upload failed` | ERROR | Parquet cold storage upload failed |

---

## Health Endpoints

### Aggregator — GET http://localhost:8080/health

```json
{
  "status": "ok",
  "connected_feeds": 2,
  "gap_count_24h": 0,
  "uptime_seconds": 3601
}
```

`status` values: `starting` (within startup window), `ok`, `degraded` (feeds < total), `critical` (gaps in last 24h).

### Candle service — GET http://localhost:8081/health

```json
{
  "status": "ok",
  "slot": "blue",
  "version": "v1.2.3",
  "shadow_lag": 0,
  "consumer_lag_max": 0,
  "questdb_write_state": "ok"
}
```

`status` values: `ok`, `degraded` (lag > 1000 or QuestDB suspended), `critical` (lag > 10000).
`shadow_lag` is non-zero only during a blue-green deploy; it reaches 0 when the new slot has caught up.

### Version — GET http://localhost:8080/version (or :8081)

```json
{
  "version": "dev",
  "git_sha": "abc1234",
  "build_time": "2024-01-15T12:00:00Z",
  "go_version": "go1.22.0"
}
```

---

## Tests

### Run all tests

```bash
make test-all       # aggregator (L1+L2+L3) + candle-service (L1+L2)
make test           # aggregator L1+L2+L3 only
make test-candle    # candle-service L1+L2 only
make test-chain     # full suite including L4 fault injection (auto-starts Toxiproxy)
```

### Test levels

| Level | What it tests | Infrastructure needed |
|-------|---------------|----------------------|
| L1 | Pure functions, config, shadow logic | None |
| L2 | Mock Redis/QuestDB via fake implementations | None |
| L3 | Real Redis (mock WebSocket) | Docker Compose running |
| L4 | Fault injection — Redis partitions, reconnects | Docker Compose + Toxiproxy |

### Per-service

```bash
# Aggregator
cd aggregator
make test-l1        # fast, no deps
make test-l2        # fake Redis
make test-l3        # real Redis
make test-l4        # fault injection

# Candle service
cd candle-service
make test-l1        # unit + shadow consumer tests
make test-l2        # mock infrastructure
make test-cover     # coverage report for L1+L2
```

---

## Blue-Green Deployment

Zero-downtime candle service updates. The new slot starts in **shadow mode** (reads ticks via XREAD without claiming messages, so it doesn't advance the consumer group position), catches up to stream tip, then atomically takes over XREADGROUP ownership.

```bash
# Deploy green, retire blue
./scripts/deploy-candle.sh green blue

# Deploy blue, retire green
./scripts/deploy-candle.sh blue green
```

The script:
1. Starts the new slot with `CANDLE_SHADOW_MODE=true`
2. Polls `/health` until `shadow_lag=0` (new slot has caught up)
3. Posts to `/promote` on the new slot (disables shadow mode)
4. Sends SIGTERM to the old slot and waits for graceful shutdown

`DEPLOY_HEALTH_TIMEOUT_S` (default 60) controls how long step 2 waits. Adjust in `candle-service/.env` if your symbols need more catch-up time.

**Test XAUTOCLAIM recovery** (what happens if the old slot dies mid-message):

```bash
FORCE_SIGKILL=true ./scripts/deploy-candle.sh green blue
```

Verify the deployed SHA matches the git HEAD:

```bash
make verify-versions   # from candle-service directory
```

---

## Configuration Reference

### Aggregator (`/.env`)

| Env Var | Default | Description |
|---------|---------|-------------|
| `REDIS_ADDR` | `redis:6379` | Redis host:port |
| `REDIS_PASSWORD` | — | Redis password (leave blank if none) |
| `REDIS_STREAM_MAXLEN` | `50000` | Approximate max entries per ticks stream |
| `QUESTDB_ILP_ADDR` | `questdb:9009` | QuestDB ILP write endpoint |
| `QUESTDB_HTTP_ADDR` | `questdb:9000` | QuestDB HTTP (WAL health checks) |
| `CONFIG_FILE` | `config.yaml` | Path to symbol config YAML |
| `KUCOIN_PUBLIC` | `true` | Use public KuCoin endpoint (no credentials) |
| `KUCOIN_API_KEY` | — | KuCoin API key |
| `KUCOIN_API_SECRET` | — | KuCoin API secret |
| `KUCOIN_API_PASSPHRASE` | — | KuCoin passphrase |
| `BYBIT_API_KEY` | — | Bybit API key |
| `BYBIT_API_SECRET` | — | Bybit API secret |
| `LOG_LEVEL` | `info` | `debug` / `info` / `warn` / `error` |
| `HTTP_ADDR` | `:8080` | HTTP server bind address |
| `STARTUP_TIMEOUT_SEC` | `90` | Feed readiness timeout (seconds) |

Full template: `.env.example`

### Candle service (`/candle-service/.env`)

| Env Var | Default | Description |
|---------|---------|-------------|
| `CANDLE_SLOT` | `blue` | Slot identity: `blue` or `green` |
| `CANDLE_SERVICE_PORT` | `8081` | HTTP server port |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `QUESTDB_ILP_ADDR` | `localhost:9009` | QuestDB ILP write endpoint |
| `QUESTDB_HTTP_ADDR` | `localhost:9000` | QuestDB HTTP |
| `SYMBOLS_KUCOIN` | — | Comma-separated KuCoin symbols |
| `SYMBOLS_BYBIT` | — | Comma-separated Bybit symbols (blank = disabled) |
| `CANDLE_STREAM_MAXLEN` | `10000` | Max entries per `candles:*` stream |
| `CANDLE_CLOSE_STREAM_MAXLEN` | `500` | Max entries per `candles:close:*` stream |
| `CANDLE_PARTIAL_PUBLISH_MS` | `250` | Partial bar publish interval (ms) |
| `COLD_START_BUFFER_SIZE` | `10000` | Messages buffered during cold-start |
| `BLOCK_TRADE_WINDOW` | `1000` | Trade samples for block trade percentile |
| `BLOCK_TRADE_MIN_SAMPLE` | `100` | Minimum samples before block trade classification |
| `WAL_PROBE_INTERVAL_S` | `5` | QuestDB WAL health check interval (seconds) |
| `WAL_BUFFER_SIZE` | `10000` | Max rows buffered during WAL suspension |
| `QUESTDB_ILP_FLUSH_MS` | `500` | ILP batch flush interval (ms) |
| `FLUSH_TIME_UTC` | `03:00` | Daily Parquet flush time (HH:MM UTC) |
| `FLUSH_DATE_OVERRIDE` | — | Override flush target date for testing (YYYY-MM-DD) |
| `B2_ACCESS_KEY_ID` | — | Backblaze B2 key ID (blank = cold storage disabled) |
| `B2_SECRET_ACCESS_KEY` | — | Backblaze B2 secret key |
| `B2_BUCKET_NAME` | — | Backblaze B2 bucket name |
| `B2_ENDPOINT` | — | Backblaze B2 S3-compatible endpoint |
| `DEPLOY_HEALTH_TIMEOUT_S` | `60` | Blue-green shadow catch-up timeout |
| `CANDLE_SHADOW_MODE` | `false` | Shadow XREAD mode (set by deploy script, not manually) |
| `LOG_LEVEL` | `info` | `debug` / `info` / `warn` / `error` |

Full template: `candle-service/.env.example`

### Symbols (`/config.yaml`)

```yaml
exchanges:
  kucoin:
    symbols:
      - BTC-USDT
      - ETH-USDT
  # bybit:
  #   symbols:
  #     - BTCUSDT
  #     - ETHUSDT
```

---

## Useful Commands

```bash
# ── Health checks ─────────────────────────────────────────────────────────────
curl -s http://localhost:8080/health | jq .
curl -s http://localhost:8081/health | jq .

# ── Version / build info ──────────────────────────────────────────────────────
curl -s http://localhost:8080/version | jq .
curl -s http://localhost:8081/version | jq .git_sha

# ── Raw Prometheus metrics ────────────────────────────────────────────────────
curl -s http://localhost:8080/metrics | grep aggregator_
curl -s http://localhost:8081/metrics | grep candle_

# ── Redis stream inspection ───────────────────────────────────────────────────
# Last 5 entries on a tick stream
docker compose exec redis redis-cli XREVRANGE ticks:kucoin:BTC-USDT + - COUNT 5

# Consumer group lag
docker compose exec redis redis-cli XINFO GROUPS ticks:kucoin:BTC-USDT

# Last closed candle bar
docker compose exec redis redis-cli XREVRANGE candles:close:kucoin:BTC-USDT + - COUNT 1

# ── Interfaces ────────────────────────────────────────────────────────────────
open http://localhost:3000   # Grafana dashboard
open http://localhost:9090   # Prometheus
open http://localhost:9093   # Alertmanager
open http://localhost:9000   # QuestDB SQL console

# ── Ports reference ───────────────────────────────────────────────────────────
# 8080  aggregator       /health /version /metrics
# 8081  candle-blue      /health /version /metrics  (/promote in shadow mode)
# 8082  candle-green     same
# 6379  Redis
# 9000  QuestDB HTTP     browser SQL console
# 9009  QuestDB ILP      write-only (internal)
# 9090  Prometheus
# 9093  Alertmanager
# 3000  Grafana
# 3100  Loki             log backend (internal)

# ── Build binaries ────────────────────────────────────────────────────────────
cd aggregator     && make build     # → bin/aggregator
cd candle-service && make build     # → bin/candle

# ── Lint ──────────────────────────────────────────────────────────────────────
cd aggregator     && go vet ./...
cd candle-service && go vet ./...
```

---

## Log Message Reference

Every log line is structured JSON. Entries are grouped by service and level. **WARN** means something unexpected happened but the system recovered automatically. **ERROR** means either a fatal startup failure or a persistent problem that requires attention.

---

### Aggregator — startup

| Level | Message | Why it happens |
|-------|---------|----------------|
| ERROR | `aggregator: failed to connect to QuestDB ILP` | QuestDB is not reachable at the configured `QUESTDB_ILP_ADDR`. Usually means QuestDB hasn't finished starting yet — with Docker Compose healthchecks this should not appear during normal startup. Appears in dev mode if QuestDB is down. |
| ERROR | `aggregator: kucoin connect failed` | The KuCoin WebSocket handshake failed (token fetch or dial error). The process exits; Docker will restart it. |
| ERROR | `aggregator: kucoin subscribe failed` | Connected to KuCoin but the subscription request was rejected or timed out. The process exits. |
| ERROR | `aggregator: bybit connect failed` | Same as the KuCoin variant but for Bybit. |
| ERROR | `aggregator: bybit subscribe failed` | Same as the KuCoin variant but for Bybit. |
| ERROR | `aggregator: no exchanges configured` | Neither `KUCOIN_SYMBOLS` nor `BYBIT_SYMBOLS` has any entries. Nothing to subscribe to; the process exits immediately. |
| ERROR | `aggregator: startup gate failed` | The coordinator's startup health check failed. Rare — indicates a programming error in coordinator initialization. |
| ERROR | `aggregator: HTTP server error` | The `/health /metrics /version` HTTP server crashed. The process is still running but you cannot scrape metrics or health. |

---

### Aggregator — coordinator

| Level | Message | Why it happens |
|-------|---------|----------------|
| WARN | `coordinator: tick dropped — symbol channel full` | The internal channel from the exchange feed to the per-symbol worker is full. This means the worker goroutine is too slow to keep up (e.g., QuestDB writes are blocking). Ticks are lost for that symbol until the channel drains. Sustained occurrences mean the QuestDB write path is a bottleneck. |
| WARN | `coordinator: gap detected` (with `cause=external_disconnect`) | A sequence number skip was detected in the live tick stream. The exchange had a brief reconnect or internal gap event. The candle service receives a gap marker via the Redis stream and clears its order book levels; the book refills from the live stream without needing a new snapshot. This appears at every startup due to the WebSocket reconnect sequence and is normal. |
| WARN | `coordinator: unexpected snapshot result discarded` | A REST snapshot result arrived while the worker was in `StateLive` (no longer waiting for one). This happens when a second snapshot completes after the book was already built. The result is discarded safely. |
| WARN | `coordinator: stale snapshot, requesting new` | The REST snapshot arrived but its sequence number was older than the oldest buffered delta — the book could not be built from it. A new snapshot is requested. Sustained occurrences mean the snapshot endpoint is very slow relative to the exchange tick rate. |
| ERROR | `coordinator: panic in symbol goroutine` | A Go panic occurred inside a per-symbol worker. The coordinator catches it, emits an `internal_merge_error` gap marker, requests a fresh snapshot, and restarts the goroutine. The stack trace is logged alongside this message. |
| ERROR | `coordinator: stale snapshot re-request failed` | After detecting a stale snapshot, the re-request to the snapshot goroutine failed. The symbol stays in cold-start state until the next reconnect cycle. |
| ERROR | `coordinator: WriteGap (stream) failed` | Writing a gap marker to the Redis tick stream failed. The candle service will not receive this gap notification and its order book may drift. Usually a Redis connectivity issue. |
| ERROR | `coordinator: WriteGap (ilp) failed` | Writing a gap marker to QuestDB via ILP failed. The gap event is missing from the `snapshot_1s` audit trail. Usually a QuestDB connectivity issue. |
| ERROR | `coordinator: stream.Write failed` | Writing a tick event to the Redis stream failed. That tick is lost from the stream permanently; the candle service will not see it. |
| ERROR | `coordinator: ilp.Write failed` | Writing a tick event to QuestDB via ILP failed. The raw tick row is missing from storage. |
| ERROR | `coordinator: WriteSnapshot failed — candle service stays in cold-start` | The snapshot signal could not be written to the Redis stream. The candle service's order book stays in cold-start (buffering deltas) until the next reconnect brings a new snapshot signal. This is the most impactful single-stream error. |
| ERROR | `coordinator: ILP writer close failed during shutdown` | On graceful shutdown, flushing the QuestDB ILP buffer failed. Some buffered rows may be lost. |

---

### Aggregator — KuCoin exchange

| Level | Message | Why it happens |
|-------|---------|----------------|
| ERROR | `kucoin: reconnect token fetch` | Fetching a new WebSocket token from the KuCoin REST API failed (network error or auth failure). Retried with exponential backoff. |
| ERROR | `kucoin: reconnect dial` | WebSocket dial to KuCoin's endpoint failed. Retried with exponential backoff. |
| ERROR | `kucoin: re-subscribe after reconnect` | Reconnected but the subscription message was rejected. |
| ERROR | `kucoin: server error message` | KuCoin sent an explicit error frame. The `data` field contains the raw exchange error payload. |
| ERROR | `kucoin: parse l2 update` | A level-2 order book update message could not be parsed. The tick is dropped. |
| ERROR | `kucoin: parse trade` | A trade message could not be parsed. The tick is dropped. |
| ERROR | `kucoin: symbols not confirmed within timeout, retrying` | After subscribing, KuCoin did not send acknowledgements for all symbols within the confirmation timeout. |
| ERROR | `kucoin: confirm retry failed` | The retry after a confirmation timeout also failed. |
| WARN | `kucoin: spurious ack for unknown subscription ID` | An acknowledgement arrived for a subscription ID that is not tracked. Indicates a race between reconnect and ack delivery. Benign. |
| WARN | `kucoin: ticks channel full, dropping` | The internal buffer between the KuCoin read loop and the coordinator is full. Same root cause as `coordinator: tick dropped`. |
| WARN | `kucoin: pong timeout` | KuCoin did not respond to a WebSocket ping within the timeout. The connection is treated as dead and a reconnect begins. |
| WARN | `kucoin: signals channel full` | The subscription-signal channel is full during a reconnect. The reconnect path is too slow relative to incoming events. |
| WARN | `kucoin: token renewal failed` | Background token renewal (KuCoin tokens expire) failed for one attempt. Retried automatically. |
| ERROR | `kucoin: token renewal exhausted retries, triggering reconnect` | All token renewal attempts failed. A full WebSocket reconnect is forced to obtain a fresh token. |

---

### Aggregator — Bybit exchange

| Level | Message | Why it happens |
|-------|---------|----------------|
| ERROR | `bybit mux: reconnect factory failed` | Creating a new WebSocket connection to Bybit failed during a reconnect attempt. |
| ERROR | `bybit mux: re-subscribe after reconnect` | Reconnected but the re-subscription failed. |
| ERROR | `bybit: parse l2 update` | An L2 order book update message from Bybit could not be parsed. The tick is dropped. |
| ERROR | `bybit: parse trade` | A trade message could not be parsed. The tick is dropped. |
| ERROR | `bybit mux: symbols unconfirmed after timeout, retrying` | Subscription acknowledgements did not arrive within the timeout. |
| ERROR | `bybit mux: confirm retry failed` | Retry after confirmation timeout also failed. |
| WARN | `bybit mux: subscribe nack` | Bybit explicitly rejected a subscription. The `ret_msg` field contains the exchange's reason. |
| WARN | `bybit mux: spurious ack for unknown req_id` | An acknowledgement arrived for an unknown request ID. Benign race during reconnect. |
| WARN | `bybit mux: ticks channel full, dropping tick` | Same as the KuCoin equivalent. |
| WARN | `bybit mux: signals channel full, dropping` | Same as the KuCoin equivalent. |
| WARN | `bybit mux: pong timeout, closing connection` | No pong received; connection closed and reconnect begins. |

---

### Aggregator — QuestDB writer

| Level | Message | Why it happens |
|-------|---------|----------------|
| ERROR | `questdb: ILP write failed` | A batch of rows could not be sent to QuestDB over ILP. Retried. Sustained occurrences mean QuestDB is down or overloaded. |
| ERROR | `questdb: ILP write failed during drain` | Same as above but during the shutdown drain flush. Some rows may be lost. |
| ERROR | `questdb: flush retry exhausted` | All retry attempts for a batch failed. That batch of rows is permanently lost. |
| WARN | `questdb: WAL check failed` | The background WAL health probe could not query QuestDB's WAL status. Transient network hiccup. |
| WARN | `questdb: WAL suspended, issuing RESUME WAL` | QuestDB's WAL engine has suspended (usually because the disk is full or the write batch size exceeded limits). The writer automatically issues `RESUME WAL` to recover. |
| WARN | `questdb: RESUME WAL failed` | The `RESUME WAL` command itself failed. Manual intervention may be needed via the QuestDB SQL console at port 9000. |
| WARN | `questdb: WAL resumed successfully` | Confirms that `RESUME WAL` worked. The writer is healthy again. |

---

### Candle service — startup

| Level | Message | Why it happens |
|-------|---------|----------------|
| ERROR | `metrics registration failed` | Prometheus metric registration failed — duplicate metric names or incompatible label sets. Indicates a code bug; the process exits. |
| ERROR | `migration failed` | The QuestDB SQL migration (e.g., creating `snapshot_1s` or `flush_manifest`) failed. QuestDB may be down or the schema already has an incompatible definition. The process exits. |
| ERROR | `redis connect failed` | Cannot reach Redis. The process exits. |
| ERROR | `questdb writer init failed` | Could not establish the QuestDB ILP connection for a symbol. The process exits. |
| ERROR | `cascade reconstruction: QuestDB check failed` | On startup, while verifying that in-progress candle bars were correctly restored, the QuestDB row count query failed. The bar continues with what was restored from Redis. |
| WARN | `cascade reconstruction incomplete` | Startup verification found that a partially-open bar has fewer QuestDB rows than expected (less than 95% of the expected count based on elapsed time). The bars will still be flushed at close time, but the incomplete QuestDB history means any queries over that time range will show gaps. Usually caused by a crash during a period of sustained QuestDB write failures. |

---

### Candle service — consumer

| Level | Message | Why it happens |
|-------|---------|----------------|
| ERROR | `consumer exited with error` | The main consumer read loop returned a non-cancellation error. The goroutine for that (exchange, symbol) pair has stopped; no more bars will be produced until the service is restarted. |
| ERROR | `consumer: xautoclaim dispatch failed` | During promotion from shadow to live mode, a pending message claimed via XAUTOCLAIM could not be processed. The message is skipped; a gap may appear in the bar sequence. |
| ERROR | `xautoclaim on promotion failed` | The XAUTOCLAIM call itself failed when taking ownership of the old slot's pending messages. Non-fatal — the consumer continues into XREADGROUP but the old slot's unacked messages stay pending until they time out. |
| ERROR | `accumulator flush failed` | At bar close time (every second), the accumulator could not flush its computed fields to the QuestDB writer. The bar for that second is lost. Usually caused by a full write buffer or QuestDB connectivity issue. |
| WARN | `stream overflow gap emitted` | The Redis tick stream for a symbol has grown beyond the overflow threshold. This happens when the candle service falls too far behind (e.g., candle service was down while the aggregator kept writing). The order book is cleared to avoid stale state. The stream self-heals as the consumer catches up. |
| WARN | `orderbook gap emitted` | The order book's internal state machine emitted a gap. The `gap_cause` field explains which sub-case triggered it (see below). |
| WARN | `partial flush on snapshot failed` | When a snapshot signal arrives, the candle service tries to flush the current in-progress bar before resetting the order book. That flush failed; the partial bar data for that second is lost. |
| WARN | `unknown message type — skipping` | A Redis stream message had an unrecognised `type` field. This would indicate the aggregator wrote a message type that this version of the candle service does not understand. Check for version mismatches between the two services. |
| WARN | `xack failed for dup` | A duplicate message was detected (already processed this second) but the XACK to remove it from the pending list failed. The message will be reclaimed by XAUTOCLAIM on the next start and re-deduplicated harmlessly. |
| WARN | `xack failed for zero-vol trade` | A zero-volume trade tick was discarded (these carry no information) but the XACK failed. Same recovery as above. |
| WARN | `consumer: lag query failed` | The Redis XPENDING lag query failed. The lag metric will not be updated for this cycle but the consumer continues. |
| WARN | `consumer: shadow lag query failed` | Same as above but during shadow mode. |

---

### Candle service — order book gap causes

These appear as the `gap_cause` field alongside `orderbook gap emitted`.

| `gap_cause` | Meaning |
|-------------|---------|
| `external_disconnect` | The aggregator detected a sequence skip in the live exchange feed. Levels are cleared; the book refills from the live stream without needing a new snapshot. Normal at startup; occasional during operation. |
| `internal_merge_error` | The aggregator's REST snapshot arrived stale (older than the delta buffer). The aggregator requests a new snapshot and the candle service waits for it in cold-start mode. |
| `cold_start_buffer_overflow` | Before the first snapshot arrived, the pre-snapshot delta buffer filled up completely. The buffer is discarded and the book waits for the next snapshot. This means the snapshot is taking too long relative to the tick rate. Should not appear in normal operation — if it repeats, the snapshot endpoint is unreachable or the cold-start buffer size (`CANDLE_COLD_BUF_SIZE`) is too small. |
| `snapshot_superseded` | A second snapshot signal arrived before the book finished applying the first. The first is discarded and the book rebuilds from the new one. |
| `seq_reset` | The snapshot's sequence number was dramatically lower than the highest buffered delta, indicating the exchange reset its sequence counter (common after Bybit reconnects). The cold buffer is discarded and the book waits for the next snapshot. |
| `stream_overflow` | The Redis tick stream grew beyond the overflow threshold (consumer too far behind). Levels cleared; book refills from the live stream. |

---

### Candle service — flusher (daily Parquet export)

| Level | Message | Why it happens |
|-------|---------|----------------|
| ERROR | `daily flush failed` | The scheduled daily Parquet flush for yesterday's data failed. The `date` field identifies which day. The flusher will retry on the next day's schedule and catch-up logic will attempt to re-flush missed days on restart. |
| ERROR | `catch-up failed` | On startup, the catch-up loop (which flushes any days missed since the last successful flush) encountered a fatal error before completing. Individual day failures are logged separately; this covers failures in the catch-up coordination itself. |
| ERROR | `catch-up: flush failed, continuing` | One specific missed day could not be flushed during catch-up. The flusher continues with the remaining days rather than aborting. |
| ERROR | `flush manifest: request build failed` | Building the SQL INSERT for the `flush_manifest` audit table failed. The flush happened but is not recorded as successful; catch-up will re-attempt it on the next restart. |
| ERROR | `flush manifest: write failed` | The `flush_manifest` INSERT could not be sent to QuestDB. Same consequence as above. |
| ERROR | `flush manifest: QuestDB exec error` | QuestDB accepted the manifest write request but returned an error in the response body. |
| ERROR | `flush alert publish failed` | After a flush failure, the candle service tried to publish a Redis alert notification and that also failed. |
| WARN | `catch-up: manifest check failed, attempting flush anyway` | Checking whether a date was already flushed (idempotency check) failed. The flusher re-attempts the flush regardless, which is safe because Parquet flush is idempotent. |
| WARN | `flush: failed to update last_flush_date in Redis` | The flush succeeded but the Redis key tracking the last successful flush date could not be updated. On the next restart, catch-up will see this date as un-flushed and attempt it again (harmlessly, due to idempotency). |
| WARN | `catch-up: Redis last_flush_date unparseable, falling back to manifest` | The stored last flush date in Redis could not be parsed as a date. The flusher falls back to querying the `flush_manifest` table to determine what has already been done. |

---

### Candle service — QuestDB writer

| Level | Message | Why it happens |
|-------|---------|----------------|
| WARN | `questdb: ILP write failed, retrying` | A write attempt failed and will be retried. Transient network hiccup to QuestDB. |
| WARN | `questdb: flush on close failed` | During shutdown, the final buffer flush to QuestDB failed. Some in-flight bar data may be lost. |
| WARN | `questdb: WAL probe failed` | The background WAL health check query failed. Same as the aggregator equivalent. |
| WARN | `questdb: WAL suspended — issuing RESUME WAL` | WAL engine suspended. Automatic recovery via `RESUME WAL`. |
| ERROR | `questdb: RESUME WAL failed` | Automatic recovery failed. Check QuestDB logs and disk space. |
| ERROR | `questdb: WAL drain write failed` | During shutdown drain, a WAL-related write failed. |

---

### Candle service — Redis publisher

| Level | Message | Why it happens |
|-------|---------|----------------|
| ERROR | `candle stream xadd failed` | Writing a completed 1s bar to the `candles:kucoin:BTC-USDT` Redis stream failed. Downstream consumers will not see this bar. |
| ERROR | `candle close stream xadd failed` | Writing a bar-close event to the close stream failed. Downstream consumers that rely on close events will miss this one. |
| ERROR | `candle partial stream xadd failed` | Writing a partial (in-progress) bar update to the partials stream failed. |
| ERROR | `ob features stream xadd failed` | Writing order book feature fields to the OB features stream failed. |

---

### Candle service — blue-green (deployment)

| Level | Message | Why it happens |
|-------|---------|----------------|
| ERROR | `promote failed` | The `POST /promote` call (switching from shadow to live mode) failed for a symbol. That symbol stays in shadow mode. |
| ERROR | `final flush failed` | On shutdown, the final bar flush for a symbol failed. The last partial second of data is lost. |
| ERROR | `writer close failed` | On shutdown, closing the QuestDB ILP writer for a symbol failed. Some buffered rows may be lost. |
| ERROR | `http shutdown error` | The HTTP server failed to shut down cleanly within the grace period. |
| WARN | `block trade window persist failed` | The rolling block-trade size window could not be saved to Redis at bar close. On restart, the window will be empty and the block trade threshold will take a few minutes to warm up. |
| ERROR | `cascade state write failed` | After a bar close, writing the updated cascade state (partially-built multi-timeframe bars) to Redis failed. On the next restart, those bars will be partially missing from the reconstructed state. |
