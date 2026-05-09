# magnum-opus

Real-time crypto market data pipeline. Two Go services connect live exchange feeds to a queryable analytics store.

- **aggregator** — WebSocket feeds from KuCoin and Bybit → Redis tick streams + QuestDB raw storage
- **candle-service** — Consumes ticks → 1s OHLCV with 17 microstructure features → 7-timeframe cascade → QuestDB + Redis output + daily Parquet cold storage to Backblaze B2

## Architecture

```
KuCoin / Bybit WebSocket
         │
    aggregator :8080
         │
    Redis  ticks:{exchange}:{symbol}
         │
    candle-service :8081  (blue slot)
    ├── Redis  candles:{exchange}:{symbol}        rolling + close bars
    ├── Redis  ob_features:{exchange}:{symbol}    OB snapshot at bar close
    └── QuestDB  snapshot_1s … snapshot_1w        persistent OHLCV + features
                 flush_manifest                   daily Parquet audit log
```

## Prerequisites

- Docker and Docker Compose v2 (`docker compose version`)
- Go 1.22+ (local dev only)
- `jq` and `curl` (blue-green deploy script only)

## Quick Start

```bash
# 1. Copy credentials template
cp .env.example .env
# Edit .env — KuCoin and Bybit credentials are optional;
# public KuCoin mode (KUCOIN_PUBLIC=true) works without them.

# 2. Start infrastructure + aggregator + candle blue slot
docker compose --profile candle-blue up --build

# 3. Verify health
curl -s http://localhost:8080/health | jq .   # aggregator
curl -s http://localhost:8081/health | jq .   # candle-service
```

### Symbols

Edit `config.yaml` to control which symbols are subscribed on the aggregator side:

```yaml
exchanges:
  kucoin:
    symbols: [BTC-USDT, ETH-USDT]
  bybit:
    symbols: [BTCUSDT, ETHUSDT]
```

Set matching symbols for the candle service in `candle-service/.env`:

```
SYMBOLS_KUCOIN=BTC-USDT,ETH-USDT
SYMBOLS_BYBIT=BTCUSDT,ETHUSDT
```

## Ports

| Service | Port | Endpoints |
|---------|------|-----------|
| Aggregator | 8080 | `/health`, `/version`, `/metrics` |
| Candle (blue) | 8081 | `/health`, `/version`, `/metrics`, `/promote` |
| Candle (green) | 8082 | same |
| QuestDB console | 9000 | browser query UI |
| QuestDB ILP | 9009 | write-only (internal) |

## Dev Mode

Runs Redis and QuestDB in Docker, both Go services locally via `go run` for fast iteration.

### Setup

```bash
cp .env.example .env
cp candle-service/.env.example candle-service/.env
```

Edit both files. At minimum, set `SYMBOLS_KUCOIN` / `SYMBOLS_BYBIT` in `candle-service/.env` to match the symbols in `config.yaml`.

### Start infrastructure

```bash
make dev-infra          # starts Redis + QuestDB detached
```

### Start services

**Both at once (logs interleaved, Ctrl+C stops both):**

```bash
make dev
```

**Separate terminals:**

```bash
# terminal 1
make dev-aggregator

# terminal 2
make dev-candle
```

Services default to `localhost:6379` for Redis and `localhost:9009` / `localhost:9000` for QuestDB.
Override any variable inline:

```bash
LOG_LEVEL=debug REDIS_ADDR=myredis:6379 make dev-aggregator
```

### Stop infrastructure

```bash
make dev-infra-down
```

## Tests

```bash
# Aggregator — L1 + L2 + L3 (no infrastructure needed)
make test

# Aggregator — L1 only (fast, pure functions)
make test-l1

# Candle service — L1 + L2
make test-candle

# Full fault injection suite (L1–L4, starts Toxiproxy automatically)
make test-chain
```

From within each service directory:

```bash
cd aggregator    && make test-all     # L1 + L2 + L3
cd candle-service && make test-l1    # pure functions + shadow tests
cd candle-service && make test-l2    # mock Redis / QuestDB
```

## Configuration Reference

### Aggregator

| Env Var | Default | Description |
|---------|---------|-------------|
| `REDIS_ADDR` | `redis:6379` | Redis address |
| `QUESTDB_ILP_ADDR` | `questdb:9009` | QuestDB ILP write endpoint |
| `QUESTDB_HTTP_ADDR` | `questdb:9000` | QuestDB HTTP (WAL health checks) |
| `CONFIG_FILE` | `config.yaml` | Symbol config file path |
| `KUCOIN_PUBLIC` | `true` | Use public KuCoin endpoint (no credentials) |
| `KUCOIN_API_KEY` | — | KuCoin API key (optional) |
| `KUCOIN_API_SECRET` | — | KuCoin API secret (optional) |
| `KUCOIN_API_PASSPHRASE` | — | KuCoin passphrase (optional) |
| `BYBIT_API_KEY` | — | Bybit API key |
| `BYBIT_API_SECRET` | — | Bybit API secret |
| `REDIS_STREAM_MAXLEN` | `50000` | Max entries per `ticks` stream |
| `LOG_LEVEL` | `info` | `debug` / `info` / `warn` / `error` |
| `HTTP_ADDR` | `:8080` | HTTP server bind address |
| `STARTUP_TIMEOUT_SEC` | `90` | Feed readiness timeout (seconds) |

Full template: `.env.example`

### Candle Service

| Env Var | Default | Description |
|---------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection string |
| `QUESTDB_ILP_ADDR` | `localhost:9009` | QuestDB ILP write endpoint |
| `QUESTDB_HTTP_ADDR` | `localhost:9000` | QuestDB HTTP (WAL health checks) |
| `SYMBOLS_KUCOIN` | — | Comma-separated KuCoin symbols |
| `SYMBOLS_BYBIT` | — | Comma-separated Bybit symbols |
| `CANDLE_SLOT` | `blue` | Slot identity (`blue` or `green`) |
| `CANDLE_SERVICE_PORT` | `8081` | HTTP server port |
| `LOG_LEVEL` | `info` | `debug` / `info` / `warn` / `error` |
| `CANDLE_STREAM_MAXLEN` | `10000` | Max entries per `candles:*` stream |
| `CANDLE_PARTIAL_PUBLISH_MS` | `250` | Partial bar publish interval (ms) |
| `FLUSH_TIME_UTC` | `03:00` | Daily Parquet flush time (HH:MM UTC) |
| `B2_ACCESS_KEY_ID` | — | Backblaze B2 key ID (cold storage) |
| `B2_SECRET_ACCESS_KEY` | — | Backblaze B2 secret key |
| `B2_BUCKET_NAME` | — | Backblaze B2 bucket name |
| `B2_ENDPOINT` | — | Backblaze B2 S3-compatible endpoint |
| `CANDLE_SHADOW_MODE` | `false` | Enable shadow XREAD mode for blue-green deploy |

Full template: `candle-service/.env.example`

## Blue-Green Deployment

Zero-downtime candle service deploy. The new slot starts in shadow mode (reads ticks via XREAD, no consumer group), catches up to stream tip, then promotes to XREADGROUP ownership while the old slot is stopped.

```bash
# Deploy green, retire blue
./scripts/deploy-candle.sh green blue

# Deploy blue, retire green
./scripts/deploy-candle.sh blue green
```

Test XAUTOCLAIM recovery after SIGKILL (bypasses graceful shutdown):

```bash
FORCE_SIGKILL=true ./scripts/deploy-candle.sh green blue
```

The script polls `/health` for `shadow_lag=0` before promoting. `DEPLOY_HEALTH_TIMEOUT_S` (default 60) controls the wait limit.

## Useful Commands

```bash
# Live logs (Docker)
docker compose logs -f aggregator
docker compose logs -f candle-blue

# Check deployed SHA
curl -s http://localhost:8080/version | jq .git_sha
curl -s http://localhost:8081/version | jq .git_sha

# Prometheus metrics
curl -s http://localhost:8081/metrics | grep candle_

# QuestDB browser console
open http://localhost:9000

# Build binaries
cd aggregator     && make build   # → bin/aggregator
cd candle-service && make build   # → bin/candle
```
