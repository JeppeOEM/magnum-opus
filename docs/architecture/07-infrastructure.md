# Chapter 07 — Infrastructure

**30-second summary:** Six supporting services run alongside the four application
services: Redis (streams + pub/sub + ephemeral cache), QuestDB (all time-series
storage), Prometheus (metrics scraping), Grafana (dashboards), Loki + Promtail
(log aggregation), and Alertmanager (Telegram alerts).

All Docker Compose config is in [`docker-compose.yml`](../../docker-compose.yml).

---

## 1. Redis

Redis 7 — three distinct usage patterns in this system:

### 1.1 Streams (durable, consumer-group delivery)

| Stream key | Producer | Consumer | Content |
|------------|----------|----------|---------|
| `ticks:{exchange}:{symbol}` | Aggregator | Candle service | Raw tick (price, size, seq, bids/asks delta) |
| `candles:close:{ex}:{sym}:{tf}` | Candle service | Bot service | Completed bar for that timeframe |
| `candles:ob:{ex}:{sym}` | Candle service | Bot service | OB feature snapshot (depth, imbalance) |

Streams use `XREADGROUP` with consumer groups and `XAUTOCLAIM` for crash recovery.
The candle service and bot service both use `XACK`-before-process semantics.

### 1.2 Pub/Sub (real-time, fire-and-forget fan-out)

| Channel pattern | Publisher | Subscribers |
|----------------|-----------|-------------|
| `orderbook:{ex}:{sym}` | Aggregator | Gateway, Bot service |
| `candles1s:{ex}:{sym}` | Candle service | Gateway |

Pub/sub messages are not durable — if a subscriber is disconnected, it misses them.
The gateway caches the last OB snapshot per symbol (`lastSnap`) and sends it to
newly connected clients.

### 1.3 Sorted Sets (heatmap data)

The candle service writes bid/ask depth at each price level to Redis sorted sets.
The dashboard reads these for the OB depth heatmap. The key is
`heatmap:{exchange}:{symbol}` with score = price.

### Persistence

Default config: AOF enabled (`appendonly yes`, `appendfsync everysec`), RDB disabled
(`--save ""`). This gives crash-safe stream data without RDB snapshot overhead.

---

## 2. QuestDB

QuestDB is the append-only time-series database for all persistent data.

### Tables

| Table | Writer | Purpose |
|-------|--------|---------|
| `ticks` | Aggregator | Raw exchange tick data |
| `snapshot_1s` | Candle service | 67-feature 1-second bars |
| `snapshot_1m` | Candle service | 1-minute cascade bars |
| `snapshot_15m` | Candle service | 15-minute cascade bars |
| `order_events` | Bot service | All order lifecycle events (placed → filled/rejected) |
| `backtest_runs` | Bot service | Backtest metadata (strategy, symbol, date range) |
| `backtest_trades` | Bot service | Per-trade records from backtest runs |

### Write Protocol

All services write via **QuestDB ILP** (InfluxDB Line Protocol) over TCP port 9009.
This is a high-throughput append-only protocol. Each write is fire-and-forget — errors
are logged but never propagated.

QuestDB also exposes an HTTP API on port 9000:
- `/exec?query=...` — SQL queries (used by dashboard + reconciliation)
- `/imp` — CSV bulk import
- `/exp` — CSV export

### DDL Migrations

The candle service runs schema migrations on startup via
[`migrator/migrator.go`](../../candle-service/internal/migrator/migrator.go).
Migrations are SQL files embedded in the binary, applied idempotently.

### Memory Tuning (4 GB VPS)

```
JAVA_OPTS: -Xms256m -Xmx512m -XX:+UseG1GC
QDB_CAIRO_MAX_UNCOMMITTED_ROWS: 10000
Docker memory limit: 1g
```

This leaves ~3 GB for Redis, Go services, and OS.

### Nightly Backup

[`scripts/backup-questdb.sh`](../../scripts/backup-questdb.sh):
1. `CHECKPOINT CREATE` — consistent online backup.
2. `tar -czf questdb-backup-$(date +%Y%m%d).tar.gz questdb/data`.
3. `s3cmd put ... s3://magnum-opus-backups/`.
4. Prune remote backups older than 30 days.

---

## 3. Prometheus

Scrapes metrics from all services every 15 seconds.

Config: `monitoring/prometheus/prometheus.yml`

Scrape targets:
- `aggregator:8080/metrics`
- `candle-blue:8081/metrics`
- `candle-green:8082/metrics`
- `bot:8090/metrics`
- `gateway:8083/metrics`
- `redis-exporter:9121/metrics`
- `questdb:9003/metrics` (QuestDB built-in Prometheus endpoint)

### Key Metrics

**Aggregator:**
- `aggregator_ticks_total` — tick throughput (rate = ~ticks/s)
- `aggregator_gaps_total` — gap events by cause
- `aggregator_orderbook_health` — 1 = healthy per symbol

**Candle service:**
- `candle_bars_total` — bars flushed per TF
- `candle_consumer_lag` — how far behind the tick stream
- `candle_flush_duration_seconds` — ILP write latency histogram

**Bot service:**
- `bot_strategy_status{strategy}` — 1 = running, 0 = stopped
- `bot_position_size{strategy, symbol}` — current position
- `bot_unrealized_pnl{strategy, symbol}` — mark-to-market PnL
- `bot_drawdown{strategy}` — peak-to-current PnL ratio
- `bot_orders_placed_total` — order throughput
- `bot_orders_filled_total` — fill rate
- `bot_risk_gate_blocks_total` — blocked orders
- `bot_queue_drops_total` — overflow drops
- `bot_circuit_breaker_tripped{strategy}` — 1 if tripped

---

## 4. Grafana

Port 3000. Pre-provisioned dashboards in
`monitoring/grafana/provisioning/dashboards/magnum-opus.json`.

Dashboard rows:
- **System Health Overview** — CPU, memory, disk for all containers
- **Aggregator** — tick rate, gap events, snapshot latency by exchange/symbol
- **Candle Service** — bar rate, consumer lag, flush latency, empty seconds
- **Bot Service** — per-strategy position, PnL, drawdown, order stats
- **Gaps** — timeline of all gap events
- **Warnings & Errors** — Loki log panel filtered to WARN/ERROR

Data sources provisioned: Prometheus + Loki.

---

## 5. Loki + Promtail

Loki: log aggregation (port 3100).

**Log retention:** 14 days (`retention_period: 336h`) via built-in compactor.

Promtail scrapes container logs via the Docker JSON file driver:
- Mount `/var/lib/docker/containers:/var/lib/docker/containers:ro`.
- Parse Docker JSON log format.
- Extract `container_name` from Docker log tag.
- Forward to Loki with labels `{container_name="aggregator"}` etc.

All services use structured logging (Go: `log/slog` with JSON handler; Python:
`structlog` with JSON renderer). This makes Loki LogQL queries against fields easy:
```
{container_name="bot"} | json | level="error"
```

---

## 6. Alertmanager

Port 9093. Alert rules in `monitoring/prometheus/alerts/`.

Routes to Telegram via `monitoring/alertmanager/alertmanager.yml`.

Example alert rules:
- `AggregatorDown` — no scrape for 2 min
- `CandleServiceLag` — consumer lag > 1000 messages for 5 min
- `BotCircuitBreakerTripped` — circuit breaker gauge = 1
- `QuestDBDown` — no scrape for 2 min
- `HighGapRate` — gap rate > 10/min for 10 min

---

## 7. Docker Compose Architecture

[`docker-compose.yml`](../../docker-compose.yml) — service definitions:

```yaml
services:
  redis:       # Redis 7, AOF enabled
  questdb:     # QuestDB, ILP+HTTP, memory-tuned
  aggregator:  # Go, port 8080
  candle-blue: # Go, port 8081
  candle-green: # Go, port 8082, CANDLE_SHADOW_MODE=true by default
  gateway:     # Go, port 8083
  bot:         # Python FastAPI, port 8090
  dashboard:   # Python Dash, port 8050
  prometheus:  # port 9090
  grafana:     # port 3000
  alertmanager: # port 9093
  loki:        # port 3100
  promtail:    # no port (log scraper)
```

### Named Volumes

```
redis-data:        Redis AOF files
questdb-data:      QuestDB data directory
prometheus-data:   Prometheus TSDB
grafana-data:      Grafana dashboards + config
loki-data:         Loki chunk storage
promtail-positions: Promtail read position tracking
```

### Network

All services on a single `magnum-opus` bridge network. Services reference each other
by service name (e.g., `REDIS_ADDR=redis:6379`). Only the dashboard (8050) and
gateway (8083) are exposed to the host.

---

## 8. systemd Auto-Start

[`scripts/setup-systemd.sh`](../../scripts/setup-systemd.sh) installs
`/etc/systemd/system/magnum-opus.service`:

```ini
[Unit]
Description=magnum-opus trading stack
After=network.target docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/deploy/magnum-opus
ExecStartPre=/bin/sleep 10
ExecStartPre=/usr/bin/curl -sf http://localhost:9000/exec?query=select+1
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
```

The `ExecStartPre` steps ensure:
1. Docker has had 10 seconds to start its daemon.
2. QuestDB is healthy before the application stack starts.
