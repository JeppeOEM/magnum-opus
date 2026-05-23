# Logs & Error Messages Guide

How to view logs from every service in this stack, from a quick one-liner to
persistent search in Grafana/Loki.

---

## Quick reference

| Goal | Command |
|------|---------|
| Start stack, see all logs | `make up` |
| See **only warnings and errors** | `make watch` |
| See **everything unfiltered** | `VERBOSE=1 make up` |
| Follow aggregator logs only | `make logs` |
| Follow monitoring stack logs | `make monitoring-logs` |
| Follow any single service | `docker compose logs -f <service>` |
| Last 100 lines from a service | `docker compose logs --tail=100 <service>` |
| Search for errors in a service | `docker compose logs bot 2>&1 \| grep '"level":"error"'` |
| Open Grafana (persistent search) | http://localhost:3000 → Explore → Loki |

---

## `make up` vs `make watch` vs `VERBOSE=1`

### `make up` — normal startup
```
make up
```
Pipes all compose output through `scripts/logfmt.py`.

**What logfmt.py does:**
- **Aggregator** — parses Go structured logs; suppresses noisy DEBUG/INFO; highlights WARN/ERROR; batches rapid tick processing lines into a summary counter
- **All other services** (bot, candle, ml, dashboard, etc.) — passes logs through **unchanged** as raw JSON lines
- The result is readable but still shows every bot/candle/dashboard log line

### `make watch` — warnings and errors only
```
make watch
```
Same as `make up` but runs `logfmt.py --alerts`.

**What `--alerts` does:**
- Aggregator: same filtering as above but only WARN/ERROR
- All other services: only lines that contain `"level":"warning"` or `"level":"error"` pass through
- Suppresses all INFO noise from the bot, candle service, dashboard, etc.

Use this when the stack is running and you only care about things going wrong.

### `VERBOSE=1 make up` — raw unfiltered
```
VERBOSE=1 make up
```
Skips `logfmt.py` entirely. Every line from every container is printed as-is.
Useful when `logfmt.py` is hiding something you need to see.

---

## Following a specific service

All services run under Docker Compose profiles. The service names are:

| Service | Profile | Port |
|---------|---------|------|
| `aggregator` | (always on) | 8080 |
| `candle-blue` | `candle-blue` | 8081 |
| `candle-green` | `candle-green` | 8082 |
| `bot` | `bot` | 8090 |
| `dashboard` | `dashboard` | 8050 |
| `ml-service` | (always on) | 8000 |
| `questdb` | (always on) | 9000 |
| `redis` | (always on) | 6379 |
| `grafana` | (always on) | 3000 |
| `prometheus` | (always on) | 9090 |
| `alertmanager` | (always on) | 9093 |
| `loki` | (always on) | 3100 |

```bash
# Follow the bot in real time
docker compose logs -f bot

# Follow candle-blue
docker compose logs -f candle-blue

# Follow multiple services at once
docker compose logs -f bot candle-blue aggregator

# Last 200 lines from the bot without following
docker compose logs --tail=200 bot
```

> **Tip:** if `make up` is already running in another terminal, you can open a
> second terminal and run `docker compose logs -f bot` there — it reads the same
> container output independently.

---

## Bot service logs

The bot uses [structlog](https://www.structlog.org/) which emits **JSON** on every line. Fields:

| Field | Meaning |
|-------|---------|
| `timestamp` | ISO-8601 UTC |
| `level` | `debug` / `info` / `warning` / `error` / `critical` |
| `event` | Short machine-readable key, e.g. `order_filled`, `paper_fill_no_price` |
| `strategy` | Strategy name, e.g. `MarketOrderTest` |
| `order_id` | UUID |
| `symbol` | e.g. `BTC-USDT` |

**Useful one-liners:**

```bash
# All errors and criticals from the bot
docker compose logs bot 2>&1 | grep -E '"level":"(error|critical)"'

# All log lines for a specific strategy
docker compose logs bot 2>&1 | grep '"strategy":"MarketOrderTest"'

# Watch fills arriving in real time
docker compose logs -f bot | grep '"event":"order_filled"'

# Watch the risk gate blocking orders
docker compose logs -f bot | grep 'order_risk_gate'

# Watch paper exchange fills
docker compose logs -f bot | grep -E '"event":"(paper_fill|order_filled|order_placed)"'

# Pretty-print JSON with jq (if installed)
docker compose logs -f bot | jq 'select(.level == "error")'
```

---

## Aggregator logs (Go)

The aggregator uses Go `slog` structured logging. Key things to look for:

```bash
# Follow aggregator only (same as `make logs`)
docker compose logs -f aggregator

# Only warnings and errors
docker compose logs aggregator 2>&1 | grep -E '"level":"(WARN|ERROR)"'
```

See `README.md → Log Message Reference` for the full table of every
`slog.Warn` / `slog.Error` call and what they mean.

---

## Candle service logs (Python)

The candle service also uses structlog JSON. Same format as the bot.

```bash
docker compose logs -f candle-blue      # or candle-green

# Only errors
docker compose logs candle-blue 2>&1 | grep '"level":"error"'
```

---

## Health endpoints

Every service exposes a `/health` endpoint. Hit them to confirm a service is up
and its dependencies are reachable:

```bash
curl -s http://localhost:8080/health | jq .    # aggregator
curl -s http://localhost:8081/health | jq .    # candle-blue
curl -s http://localhost:8082/health | jq .    # candle-green
curl -s http://localhost:8090/health | jq .    # bot
curl -s http://localhost:8000/health | jq .    # ml-service
```

---

## Prometheus metrics

Every service exposes `/metrics` in Prometheus text format:

```bash
curl -s http://localhost:8080/metrics | grep -v "^#"   # aggregator
curl -s http://localhost:8090/metrics | grep -v "^#"   # bot
```

Or open the Prometheus query UI: **http://localhost:9090**

Useful bot metrics:
- `bot_orders_placed_total` — orders sent to exchange
- `bot_orders_filled_total` — confirmed fills
- `bot_orphaned_orders_total` — orders on exchange with no QuestDB record
- `bot_circuit_breaker_trips_total` — circuit breaker triggers

---

## Grafana + Loki (persistent log search)

Loki captures logs from all containers via Promtail. Grafana provides a search UI.

1. Open **http://localhost:3000** (default login: `admin` / `admin`)
2. Go to **Explore** (compass icon in the left sidebar)
3. Select **Loki** as the data source

### Example LogQL queries

```
# All bot logs
{container="magnum-opus-bot-1"}

# Bot errors only
{container="magnum-opus-bot-1"} | json | level = "error"

# Bot fills
{container="magnum-opus-bot-1"} | json | event = "order_filled"

# All errors across all services
{job="magnum-opus"} | json | level =~ "error|critical"

# Aggregator warnings
{container="magnum-opus-aggregator-1"} | json | level = "WARN"

# A specific strategy's logs
{container="magnum-opus-bot-1"} | json | strategy = "MarketOrderTest"
```

> **Advantage over `docker compose logs`:** Loki retains history even after
> containers restart. You can scroll back hours and set time ranges.

---

## QuestDB (checking trade data)

Open the QuestDB web console at **http://localhost:9000** → "SQL" tab.

```sql
-- Recent order events (all strategies)
SELECT * FROM order_events ORDER BY ts DESC LIMIT 50;

-- Only fills
SELECT * FROM order_events WHERE status = 'filled' ORDER BY ts DESC LIMIT 20;

-- Fills per strategy
SELECT strategy, count() fills
FROM order_events
WHERE status = 'filled'
ORDER BY fills DESC;

-- Non-terminal orders (should be empty if no positions open)
SELECT order_id, strategy, symbol, status, ts
FROM (
  SELECT order_id, strategy, symbol, status, ts
  FROM order_events
  LATEST ON ts PARTITION BY order_id
)
WHERE status NOT IN ('filled','cancelled','rejected','failed');
```

---

## Common error patterns

| What you see | Where to look | Likely cause |
|---|---|---|
| Bot placed orders but no fills | `docker compose logs -f bot \| grep paper_fill` | Price stream empty — check `candles:close:kucoin:BTC-USDT:1m` exists in Redis |
| `order_risk_gate_blocked` | bot logs | Order notional > `max_order_notional_usd` in `.env` |
| `paper_fill_no_price` | bot logs | 1m candle stream not yet populated — wait 60s after aggregator starts |
| `circuit_breaker_tripped` | bot logs | Too many failures in a short window — check exchange connectivity |
| `reconciliation_questdb_unavailable` | bot logs | QuestDB not reachable at startup |
| Aggregator `WARN gap_detected` | aggregator logs | WebSocket reconnect — normal, watch for repeated occurrences |
| Dashboard shows strategies as "unknown" | dashboard + bot logs | Bot `/strategies/detail` returning 500 — check bot health |

---

## Combining terminals (recommended workflow)

When debugging, open **three terminals**:

```
Terminal 1: make up              ← full stack, logfmt-filtered
Terminal 2: docker compose logs -f bot candle-blue   ← raw service logs
Terminal 3: curl / questdb SQL   ← verify data
```

Or use **`make watch`** in terminal 1 (errors only) and terminal 2 for the
specific service you're investigating.
