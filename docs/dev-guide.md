# magnum-opus aggregator — Developer Guide

## Prerequisites

- Go 1.24+
- Docker Engine 24+ with Compose plugin (for infrastructure and L4 tests)
- `curl`, `python3` (for endpoint checks and `make verify-versions`)
- Redis and QuestDB running locally, or via Docker Compose

---

## Repository layout

```
magnum-opus/
├── aggregator/              # Go service (this is your working directory for most commands)
│   ├── cmd/aggregator/      # main.go — sole composition root
│   ├── internal/
│   │   ├── config/          # env var loading, Credential sealed type
│   │   ├── coordinator/     # per-symbol workers, snapshot dispatch, fanout
│   │   ├── exchange/        # exchange.Tick type, FeedType, transport layer
│   │   │   ├── bybit/       # Bybit WS adapter + 20-connection mux
│   │   │   └── kucoin/      # KuCoin WS adapter + token renewal
│   │   ├── gapdetector/     # 4-cause gap classification (pure)
│   │   ├── gapwindow/       # rolling 24h gap counter (pure)
│   │   ├── httpapi/         # /health, /version, /metrics server
│   │   ├── metrics/         # Prometheus registry (leaf package)
│   │   ├── orderbook/       # L2 order book state machine (pure)
│   │   ├── reconnect/       # snapshot/delta merge state machine (pure)
│   │   ├── slogredact/      # credential-sanitizing slog handler
│   │   ├── symbol/          # canonical Symbol type + Normalize
│   │   ├── writer/
│   │   │   ├── redis/       # Redis Stream writer
│   │   │   └── questdb/     # QuestDB ILP writer
│   │   ├── l4/              # L4 fault-injection tests (//go:build l4)
│   │   └── live/            # L5 live exchange tests (//go:build live)
│   ├── scripts/
│   │   └── wait-for-toxiproxy.sh
│   ├── Dockerfile
│   └── Makefile
├── docker-compose.yml       # production: aggregator + redis + questdb
├── docker-compose.test.yml  # L4: toxiproxy + redis + questdb (no aggregator)
├── .env.example
└── docs/
    └── ops.md
```

All `make` commands are run from `aggregator/`. All `docker compose` commands are run from the project root.

---

## Local setup (no Docker)

### 1. Install infrastructure

Start Redis and QuestDB however you like. The simplest way:

```bash
# From project root
docker compose up redis questdb -d
```

Redis listens on `localhost:6379`. QuestDB ILP on `localhost:9009`, HTTP UI on `localhost:9000`.

### 2. Create your `.env`

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` with real exchange credentials and local addresses:

```bash
# Override the Docker Compose DNS names for local dev
REDIS_ADDR=localhost:6379
QUESTDB_ILP_ADDR=localhost:9009
QUESTDB_HTTP_ADDR=localhost:9000
```

### 3. Create the QuestDB table

The schema needs to exist before the aggregator starts writing. Run once:

```bash
curl -G http://localhost:9000/exec \
  --data-urlencode "query=$(cat aggregator/internal/writer/questdb/schema.sql)"
```

### 4. Build and run

```bash
cd aggregator
make build
./bin/aggregator
```

Or run without building (slower startup, useful during development):

```bash
cd aggregator
export $(grep -v '^#' ../.env | xargs)
go run ./cmd/aggregator/
```

Within 90 seconds (configurable via `STARTUP_TIMEOUT_SEC`) all configured feeds must confirm subscription or the process exits 1. Once all feeds are live you'll see:

```
{"level":"INFO","msg":"aggregator: all feeds confirmed","feeds":4}
```

---

## Running with Docker Compose

```bash
# From project root
docker compose up -d

# Follow logs
docker compose logs -f aggregator

# Stop
docker compose down
```

The aggregator image is built from `aggregator/Dockerfile` on first `up`. To rebuild after a code change:

```bash
docker compose build aggregator
docker compose up -d aggregator
```

---

## Tests

The test suite has five layers with increasing infrastructure requirements.

### L1 — Pure unit tests (no build tag, no infrastructure)

Covers: `orderbook`, `reconnect`, `gapdetector`, `symbol`, `backoff`, `config`, `metrics`, `gapwindow`, `httpapi`, `slogredact`.

Includes three static checks:
- **95% branch coverage gate** on `orderbook`, `reconnect`, `gapdetector`
- **`time.Now()` / `time.Sleep()` ban** in all `internal/` production code
- **`init()` logging ban** — no package may call `slog.*` or `log.*` inside `func init()`

```bash
cd aggregator
make test-l1
```

Run a single package verbosely:

```bash
go test ./internal/gapwindow/... -v
go test ./internal/httpapi/... -v -run TestHealth
```

View coverage in the browser after a run:

```bash
go tool cover -html=coverage-l1.out
```

### L2 — Mock infrastructure tests (`//go:build l2`)

Uses `FakeRedis` and `FakeQuestDB` to test the coordinator, Redis writer, and QuestDB writer under controlled failure injection. No real network.

```bash
cd aggregator
make test-l2

# or equivalently
go test -count=1 -tags l2 ./...
```

Run a specific L2 test:

```bash
go test -tags l2 ./internal/coordinator/... -v -run TestWorker_GapDetected
```

### L3 — Mock WebSocket server tests (`//go:build l3`)

Tests the exchange adapter protocol against an in-process fake WebSocket server. Exercises KuCoin ping/pong, subscription confirmations, and Bybit connection mux.

```bash
cd aggregator
make test-l3
```

### L4 — Fault injection via Toxiproxy (`//go:build l4`)

Tests real Redis/QuestDB writers with network partitions injected by Toxiproxy. Requires the test compose stack running.

```bash
# From project root — start infrastructure (no aggregator)
docker compose -f docker-compose.test.yml up -d

# From aggregator/
make test-l4
```

`make test-l4` runs `scripts/wait-for-toxiproxy.sh` first, which polls `localhost:8474` until Toxiproxy is ready (up to 30 seconds), then exits 1 with a clear message if it times out.

The three L4 tests:
- `TestL4_RedisPartition` — cuts Redis TCP, verifies a gap marker is emitted, verifies writes resume after the partition heals
- `TestL4_QuestDBPartition` — cuts QuestDB ILP TCP, verifies tick capture continues (QuestDB writes are non-blocking)
- `TestL4_NetworkFlap` — simulates a full disconnect and reconnect cycle, verifies the reconnect/merge state machine runs correctly

Teardown:

```bash
docker compose -f docker-compose.test.yml down
```

### L5 — Live exchange tests (`//go:build live`)

Connects to real KuCoin and Bybit feeds using your credentials. 20 tests (10 per exchange). If credentials are missing it exits with a clear message before running any test.

```bash
cd aggregator

# Source your credentials
export $(grep -v '^#' ../.env | xargs)

make test-live
```

What each test asserts:
- Feed connects and subscribes without error
- Ticks arrive within a timeout
- `Side` is `"bid"` or `"ask"` — never anything else
- `Price` and `Size` parse as positive decimal numbers
- `Seq` is monotonically increasing across consecutive ticks for the same symbol
- `TsExchange` is within ±5 minutes of local wall clock (catches epoch/zero timestamps)
- `Symbol` matches the canonical form after `symbol.Normalize`
- Multi-symbol subscriptions each receive ticks independently

### All local layers

```bash
cd aggregator
make test-all   # L1 + L2 + L3, fail-fast
```

---

## HTTP endpoints

All three endpoints require no authentication and respond in-memory with no IO per request.

### `GET /health`

```bash
curl -s http://localhost:8080/health | python3 -m json.tool
```

```json
{
  "status": "ok",
  "connected_feeds": 4,
  "gap_count_24h": 0,
  "uptime_seconds": 312
}
```

**Status values and what they mean:**

| Status | Condition |
|--------|-----------|
| `starting` | Startup gate not yet cleared — feeds not all confirmed within 90s window |
| `ok` | All feeds live, no internal gaps in the last 24h |
| `degraded` | One or more feeds disconnected/reconnecting |
| `critical` | At least one `internal_*` gap event in the last 24h — requires investigation |

Priority: `starting` > `critical` > `degraded` > `ok`. A `critical` status with a disconnected feed still shows `critical`, not `degraded`.

`gap_count_24h` uses a rolling 24×1h bucket window — it is not a process-lifetime counter. Only `internal_buffer_overflow` and `internal_merge_error` causes increment it. Exchange-side disconnects (`external_*`) do not.

### `GET /version`

```bash
curl -s http://localhost:8080/version | python3 -m json.tool
```

```json
{
  "version": "v1.0.0",
  "git_sha": "abc1234def5678",
  "build_time": "2026-05-07T14:30:00Z",
  "go_version": "go1.24.0"
}
```

`version`, `git_sha`, and `build_time` are injected at build time via `-ldflags`. When running with `go run` they show `dev`, `unknown`, and `unknown`. `go_version` always comes from `runtime.Version()`.

### `GET /metrics`

```bash
curl -s http://localhost:8080/metrics
```

Prometheus text format. Key metrics:

| Metric | Type | Labels | Meaning |
|--------|------|--------|---------|
| `aggregator_feed_state` | Gauge | `exchange`, `symbol` | `1` = live, `0` = disconnected or initializing |
| `aggregator_ticks_total` | Counter | `exchange`, `symbol` | Total ticks processed through the pipeline |
| `aggregator_gap_total` | Counter | `exchange`, `symbol`, `cause` | Gap events by cause |
| `aggregator_consumer_lag_ms` | Gauge | `exchange`, `symbol` | Redis Stream consumer group lag (not yet wired — always 0) |
| `aggregator_questdb_write_latency_ms` | Histogram | — | ILP batch write latency |

All `(exchange, symbol)` series are pre-initialized to 0 at startup before any feed connects, so scrapes never show missing series.

Check a specific feed's state:

```bash
curl -s http://localhost:8080/metrics | grep aggregator_feed_state
```

```
aggregator_feed_state{exchange="bybit",symbol="BTC-USDT"} 1
aggregator_feed_state{exchange="kucoin",symbol="BTC-USDT"} 1
```

---

## Redis Streams

The aggregator writes two types of streams.

### Tick streams — `ticks:{exchange}:{symbol}`

One stream per (exchange, symbol) pair. Each entry is one of:

**Tick entry** (`type=tick`):
```
type        "tick"
exchange    "kucoin"
symbol      "BTC-USDT"
seq         "1234567890"
ts_exchange "1715089200000"   # Unix milliseconds from exchange
ts_local    "1715089200012"   # Unix milliseconds, local receipt time
side        "bid"             # or "ask", or "" for trades
price       "62500.50"        # exact wire string, never float
size        "0.012"           # "0" means level removed
event_type  "update"          # or "snapshot" or "trade"
```

**Gap marker entry** (`type=gap`):
```
type        "gap"
exchange    "kucoin"
symbol      "BTC-USDT"
gap_ts      "1715089200000"
gap_cause   "external_disconnect"
seq_before  "1234567889"
seq_after   "1234567950"
```

Inspect live:

```bash
# Read last 10 entries from a stream
redis-cli XREVRANGE ticks:kucoin:BTC-USDT + - COUNT 10

# Tail a stream (block forever, print new entries as they arrive)
redis-cli XREAD COUNT 10 BLOCK 0 STREAMS ticks:kucoin:BTC-USDT $

# Check stream length
redis-cli XLEN ticks:kucoin:BTC-USDT
```

### Gap log — `gaps:log`

A single global stream containing every gap event from all symbols, with an extra field:

```
seq_gap     "60"   # SeqAfter - SeqBefore - 1 (number of missing sequence numbers)
```

Inspect:

```bash
# All gaps in the last hour
redis-cli XRANGE gaps:log - + COUNT 100

# Count gaps by cause
redis-cli XRANGE gaps:log - + | grep gap_cause
```

---

## QuestDB

The `raw_ticks` table is the complete audit trail. Every tick and gap marker the coordinator processes ends up here.

Access the QuestDB web console at `http://localhost:9000`.

Useful queries:

```sql
-- Most recent 50 ticks
SELECT * FROM raw_ticks ORDER BY ts_exchange DESC LIMIT 50;

-- Ticks per symbol in the last hour
SELECT exchange, symbol, count() AS tick_count
FROM raw_ticks
WHERE ts_exchange > dateadd('h', -1, now()) AND is_gap = false
GROUP BY exchange, symbol
ORDER BY tick_count DESC;

-- All gap events in the last 24 hours
SELECT ts_exchange, exchange, symbol, gap_cause, seq_before, seq_after
FROM raw_ticks
WHERE is_gap = true
  AND ts_exchange > dateadd('d', -1, now())
ORDER BY ts_exchange DESC;

-- Internal gaps only (the ones that matter for /health status)
SELECT ts_exchange, exchange, symbol, gap_cause
FROM raw_ticks
WHERE is_gap = true
  AND gap_cause IN ('internal_buffer_overflow', 'internal_merge_error')
ORDER BY ts_exchange DESC;
```

---

## Build commands

```bash
cd aggregator

# Build binary with version metadata from git
make build
./bin/aggregator

# Build Docker image tagged with short git SHA
make docker-build

# Verify a running instance reports the expected git SHA
make verify-versions                                     # checks localhost:8080
HTTP_ADDR=http://my-server:8080 make verify-versions    # checks a remote

# Check all direct dependencies for recent activity (ARC11)
make check-deps

# Remove build artifacts and coverage files
make clean
```

---

## Configuration reference

All config is from environment variables. Running `go run ./cmd/aggregator/` will print exactly which variables are missing if any are absent.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `KUCOIN_API_KEY` | Yes | — | KuCoin API key |
| `KUCOIN_API_SECRET` | Yes | — | KuCoin API secret |
| `KUCOIN_API_PASSPHRASE` | Yes | — | KuCoin API passphrase |
| `BYBIT_API_KEY` | Yes | — | Bybit API key |
| `BYBIT_API_SECRET` | Yes | — | Bybit API secret |
| `REDIS_ADDR` | Yes | — | `host:port`, e.g. `localhost:6379` |
| `REDIS_PASSWORD` | No | `""` | Redis password (blank = no auth) |
| `REDIS_STREAM_MAXLEN` | No | `50000` | XADD approximate MAXLEN per stream |
| `QUESTDB_ILP_ADDR` | Yes | — | ILP endpoint, e.g. `localhost:9009` |
| `QUESTDB_HTTP_ADDR` | Yes | — | HTTP endpoint, e.g. `localhost:9000` |
| `KUCOIN_SYMBOLS` | No | `""` | Comma-separated KuCoin symbols, e.g. `BTC-USDT,ETH-USDT` |
| `BYBIT_SYMBOLS` | No | `""` | Comma-separated Bybit symbols, e.g. `BTCUSDT,ETHUSDT` |
| `LOG_LEVEL` | No | `info` | `debug` / `info` / `warn` / `error` |
| `HTTP_ADDR` | No | `:8080` | Bind address for HTTP server |
| `STARTUP_TIMEOUT_SEC` | No | `90` | Seconds to wait for all feeds before exit 1 |

Credentials are a sealed type — they cannot appear in logs or error messages regardless of `LOG_LEVEL`. Setting `LOG_LEVEL=debug` is safe for development.

---

## Shutdown

The service responds to `SIGTERM` and `SIGINT` with a clean shutdown:

1. Root context cancelled → all goroutines begin exiting
2. `coordinator.Shutdown()` — waits for per-symbol workers to finish and flushes the final QuestDB ILP batch
3. HTTP server closes with a 5-second drain timeout
4. Process exits 0

```bash
# Graceful stop
kill -TERM $(pgrep aggregator)

# Or if running in compose
docker compose stop aggregator   # sends SIGTERM, waits stop_grace_period (15s)
```
