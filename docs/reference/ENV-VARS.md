# Environment Variables Reference

All environment variables across all services. Format follows PostgreSQL GUC style:
**Type** — accepted values and range. **Default** — value if not set. **Context** — when read.
**Wrong value effect** — what happens if you set it incorrectly.

---

## Aggregator (`aggregator/`)

Config loaded by: `aggregator/internal/config/config.go`

### Required

| Variable | Type | Description |
|----------|------|-------------|
| `REDIS_ADDR` | `host:port` | Redis server address. e.g. `localhost:6379` |
| `QUESTDB_ILP_ADDR` | `host:port` | QuestDB ILP (InfluxDB Line Protocol) ingestion endpoint. e.g. `localhost:9009` |
| `QUESTDB_HTTP_ADDR` | `URL` | QuestDB HTTP query API. e.g. `http://localhost:9000`. `https://` prefix is stripped. |

**Wrong value effect:** Service refuses to start; prints `missing required environment variables: ...` to stderr and exits 1.

### KuCoin credentials (optional)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `KUCOIN_API_KEY` | `string` | `""` | KuCoin API key. If empty, falls back to unauthenticated `bullet-public` endpoint. |
| `KUCOIN_API_SECRET` | `string` | `""` | KuCoin API secret. |
| `KUCOIN_API_PASSPHRASE` | `string` | `""` | KuCoin API passphrase. |
| `KUCOIN_PUBLIC` | `"true"` | `""` | Force public (unauthenticated) endpoint even if credentials are present. |

**Note:** `KUCOIN_PUBLIC=true` applies when no KuCoin symbols are being tracked by Bybit — i.e., if `KUCOIN_SYMBOLS` is non-empty and no credentials are provided, public mode activates automatically.

### Bybit credentials (optional)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `BYBIT_API_KEY` | `string` | `""` | Bybit API key. Used for authenticated feeds if present. |
| `BYBIT_API_SECRET` | `string` | `""` | Bybit API secret. |

### Redis

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `REDIS_PASSWORD` | `string` | `""` | Redis AUTH password. Leave empty for unauthenticated. |
| `REDIS_STREAM_MAXLEN` | `positive int` | `50000` | Approximate maximum length for all Redis tick streams (`XADD ... MAXLEN ~ N`). |

**Wrong value for REDIS_STREAM_MAXLEN:** Non-integer or `≤0` → treated as missing required variable, service refuses to start.

### Service

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `LOG_LEVEL` | `debug\|info\|warn\|error` | `info` | Structured log level. Unknown values silently default to `info`. |
| `HTTP_ADDR` | `host:port` | `:8080` | HTTP server listen address. Exposes `/healthz`, `/readyz`, `/metrics`, `/version`. |
| `STARTUP_TIMEOUT_SEC` | `positive int` | `90` | Seconds to wait for all feeds to reach `feed_state=1` before aborting startup. |
| `CONFIG_FILE` | `path` | `config.yaml` | YAML config file path. Contains `exchanges.kucoin.symbols` and `exchanges.bybit.symbols` lists. |

**Wrong value for STARTUP_TIMEOUT_SEC:** Non-integer or `≤0` → treated as missing required variable.

---

## Candle-service (`candle-service/`)

Config loaded by: `candle-service/cmd/candle/main.go` (direct `os.Getenv` calls).

### Required

| Variable | Type | Description |
|----------|------|-------------|
| `REDIS_ADDR` | `host:port` | Redis server address. |
| `QUESTDB_ILP_ADDR` | `host:port` | QuestDB ILP ingestion endpoint. |
| `QUESTDB_HTTP_ADDR` | `URL` | QuestDB HTTP REST endpoint for migrations and WAL checks. |

### Optional

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `REDIS_PASSWORD` | `string` | `""` | Redis AUTH password. |
| `CANDLE_SLOT` | `blue\|green` | `blue` | Which deployment slot this instance is. Exposed in logs and `/healthz`. |
| `CANDLE_SHADOW_MODE` | `true\|false` | `false` | Start in shadow XREAD warmup mode (no output writes). Set `true` on the incoming slot during blue-green deploy. |
| `FLUSH_TIME_UTC` | `HH:MM` | `03:00` | Daily time to flush previous day's `snapshot_1s` data to B2. |
| `FLUSH_DATE_OVERRIDE` | `YYYY-MM-DD` | `""` | If set, flush only this specific date and exit. Used for backfill. |
| `B2_KEY_ID` | `string` | `""` | Backblaze B2 application key ID. Required for daily flush; flush is skipped if empty. |
| `B2_APP_KEY` | `string` | `""` | Backblaze B2 application key. |
| `B2_BUCKET` | `string` | `""` | Backblaze B2 bucket name for Parquet storage. |
| `B2_ENDPOINT` | `URL` | `""` | Backblaze B2 S3-compatible endpoint URL. |
| `LOG_LEVEL` | `debug\|info\|warn\|error` | `info` | Structured log level. |

---

## Bot-service (`bot-service/`)

Config loaded by: `bot-service/bot_service/config.py` via `pydantic-settings`.
All variables are also readable from `.env` file in the working directory.

### Infrastructure

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `REDIS_URL` | `redis://[:[password]@]host:port[/db]` | `redis://localhost:6379` | Full Redis URL including optional auth. |
| `QUESTDB_ILP_ADDR` | `host:port` | `localhost:9009` | QuestDB ILP address for metrics writes. |
| `QUESTDB_HTTP_ADDR` | `URL` | `http://localhost:9000` | QuestDB HTTP API for history queries from strategies. |

### Exchange credentials

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `KUCOIN_API_KEY` | `SecretStr` | `""` | KuCoin API key for live trading. Empty = paper trading only. |
| `KUCOIN_API_SECRET` | `SecretStr` | `""` | KuCoin API secret. |
| `KUCOIN_API_PASSPHRASE` | `SecretStr` | `""` | KuCoin API passphrase. |
| `BYBIT_API_KEY` | `SecretStr` | `""` | Bybit API key for live trading. |
| `BYBIT_API_SECRET` | `SecretStr` | `""` | Bybit API secret. |

**Note:** Credentials are stored as `pydantic.SecretStr`. They are redacted in logs via the `redact_credentials` structlog processor. Never appear in tracebacks.

### Bus Manager

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `BOT_CONSUMER_GROUP` | `string` | `bot-service` | Redis consumer group name for all strategy stream subscriptions. |
| `BOT_QUEUE_MAX_DEPTH` | `positive int` | `1000` | Per-strategy asyncio queue depth. Overflow triggers drop-oldest + GapMarker injection. |
| `BOT_SUBSCRIBE_TIMEOUT_S` | `positive int` | `30` | Seconds to wait for each strategy's `subscribe()` to complete. |

### Lifecycle

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `BOT_RECONCILIATION_TIMEOUT_S` | `positive int` | `120` | Max seconds for position reconciliation at startup. |
| `BOT_SHUTDOWN_TIMEOUT_S` | `positive int` | `30` | Seconds to wait for graceful shutdown before forcing exit. |
| `BOT_FILEWATCHER_INTERVAL_S` | `positive int` | `60` | How often to check `BOT_STRATEGIES_DIR` for hot-reload. |
| `BOT_STRATEGIES_DIR` | `path` | `strategies/active` | Directory scanned for strategy Python files (hot-reload). |
| `BOT_EXCHANGE` | `string` | `bybit` | Default exchange for strategies that do not specify one. |

### Barrier timeouts

Barriers gate strategy signals until all required timeframe data has arrived.
Timeout applies per-timeframe; strategy receives GapMarker if timeout expires.

| Variable | Type | Default (ms) | Timeframe |
|----------|------|-------------|-----------|
| `BOT_BARRIER_TIMEOUT_MS_1S` | `positive int` | `250` | 1-second bars |
| `BOT_BARRIER_TIMEOUT_MS_1M` | `positive int` | `500` | 1-minute bars |
| `BOT_BARRIER_TIMEOUT_MS_5M` | `positive int` | `1000` | 5-minute bars |
| `BOT_BARRIER_TIMEOUT_MS_15M` | `positive int` | `2000` | 15-minute bars |
| `BOT_BARRIER_TIMEOUT_MS_1H` | `positive int` | `5000` | 1-hour bars |
| `BOT_BARRIER_TIMEOUT_MS_4H` | `positive int` | `10000` | 4-hour bars |
| `BOT_BARRIER_TIMEOUT_MS_1D` | `positive int` | `30000` | Daily bars |
| `BOT_BARRIER_TIMEOUT_MS_1W` | `positive int` | `60000` | Weekly bars |

**Wrong value effect:** Barrier never fires for that timeframe (effectively disabled).

### Risk management

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `BOT_PORTFOLIO_VALUE_USD` | `float` | `10000.0` | Total portfolio value; used to compute position sizes from `max_position_pct`. |
| `DAILY_LOSS_LIMIT_USD` | `float` | `0.0` | Daily PnL loss limit in USD. `0.0` = disabled. |
| `MAX_ORDER_NOTIONAL_USD` | `float` | `0.0` | Maximum notional value per order. `0.0` = disabled. |

### Paper trading

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `BOT_PAPER_LATENCY_MIN_MS` | `positive int` | `50` | Minimum simulated fill latency for paper orders. |
| `BOT_PAPER_LATENCY_MAX_MS` | `positive int` | `250` | Maximum simulated fill latency. Actual latency is uniform random in [min, max]. |
| `BOT_PAPER_SLIPPAGE_BPS` | `positive int` | `5` | Simulated price slippage in basis points (0.01% per bps). |

### Funding rate poller

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `BOT_FUNDING_POLL_INTERVAL_S` | `positive int` | `60` | Seconds between funding rate polls. |
| `BOT_FUNDING_SYMBOLS` | `comma-separated string` | `""` | Symbols to poll, format: `exchange:symbol,exchange:symbol`. e.g. `bybit:BTCUSDT,kucoin:XBTUSDM`. Empty = no polling. |

### Logging

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `LOG_LEVEL` | `debug\|info\|warning\|error\|critical` | `info` | structlog level. Unknown values silently default to `info`. |

---

## Gateway (`gateway/`)

Config loaded by: `gateway/internal/config/config.go`

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `REDIS_ADDR` | `host:port` | `localhost:6379` | Redis pub/sub subscription endpoint. |
| `REDIS_PASSWORD` | `string` | `""` | Redis AUTH password. |
| `HTTP_ADDR` | `host:port` | `:8090` | WebSocket server listen address. |
| `LOG_LEVEL` | `debug\|info\|warn\|error` | `info` | Log level. |

---

## Dashboard (`dashboard/`)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `QUESTDB_HTTP_ADDR` | `URL` | `http://localhost:9000` | QuestDB REST endpoint for chart data queries. |
| `GATEWAY_WS_URL` | `ws://host:port` | `ws://localhost:8090` | Gateway WebSocket URL for real-time data. |
| `DASH_PORT` | `int` | `8050` | Dash web server port. |
| `DASH_DEBUG` | `true\|false` | `false` | Enable Dash hot-reload and debug toolbar. Do not use in production. |
