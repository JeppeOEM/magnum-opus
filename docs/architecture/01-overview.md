# Chapter 01 — System Overview

**30-second summary:** Six services talk over Redis. The aggregator pulls live order
book data from exchanges and writes it to Redis streams and QuestDB. The candle
service reads those streams and emits 1-second feature bars (and coarser timeframes)
back into Redis and QuestDB. The bot service subscribes to candle bars, runs
strategies, and places orders via exchange REST APIs. The gateway bridges Redis
pub/sub to browser WebSockets. The dashboard renders live charts. QuestDB stores
everything for backtests and analysis.

---

## Service Map

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │                          KuCoin / Bybit                              │
 │                        WebSocket Feeds                               │
 └──────────────────────────┬──────────────────────────────────────────┘
                            │  L2 orderbook diffs + trades
                            ▼
 ┌─────────────────────────────────────┐
 │          AGGREGATOR  (Go)           │
 │  Coordinator → per-symbol Workers   │
 │  OrderBook state machine            │
 │  GapDetector                        │
 │  Port 8080 /health                  │
 └────────────┬────────────────────────┘
              │ Redis Streams: ticks:{exchange}:{symbol}
              │ QuestDB ILP (fire-and-forget)
              │ Redis Pub/Sub: orderbook:{exchange}:{symbol}
              ▼
 ┌─────────────────────────────────────┐
 │       CANDLE SERVICE  (Go)          │
 │  Consumer (XREADGROUP)              │
 │  Accumulator (pure, per-symbol)     │
 │  Cascade Engine (7 TFs)            │
 │  Flusher → QuestDB + Redis          │
 │  Port 8081/8082 (blue/green)        │
 └────────────┬────────────────────────┘
              │ Redis Streams: candles:close:{ex}:{sym}:{tf}
              │                candles:ob:{ex}:{sym}
              │ Redis Pub/Sub:  candles1s:{ex}:{sym}
              │                 orderbook:{ex}:{sym}  (forwarded)
              │ QuestDB ILP: snapshot_1s, snapshot_1m, snapshot_15m
              ▼
 ┌─────────────────────────────────────┐     ┌─────────────────────────┐
 │        BOT SERVICE  (Python)        │     │     GATEWAY  (Go)        │
 │  FastAPI app                        │     │  Redis PSubscribe        │
 │  BusManager (XREADGROUP thread)     │     │  Hub → WS fan-out        │
 │  FileWatcher + hot-reload           │     │  Port 8083 /ws /health   │
 │  OrderQueueWorker                   │     └──────────┬──────────────┘
 │  CircuitBreaker                     │                │  WebSocket
 │  Port 8090 /health                  │                ▼
 └────────────┬────────────────────────┘     ┌─────────────────────────┐
              │ Exchange REST                │     DASHBOARD (Python)   │
              │ Exchange WS (fills)          │  Dash / Plotly           │
              ▼                             │  QuestDB HTTP queries    │
         Exchange APIs                      │  Port 8050               │
                                           └─────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────┐
 │                    SHARED INFRASTRUCTURE                            │
 │  Redis 7   — streams, pub/sub, ephemeral OB snapshots               │
 │  QuestDB   — all time-series: ticks, bars, orders, backtest runs    │
 │  Prometheus — scrapes /metrics from aggregator + candle + bot        │
 │  Grafana   — dashboards                                             │
 │  Loki + Promtail — log aggregation                                  │
 │  Alertmanager — Telegram alerts                                     │
 └────────────────────────────────────────────────────────────────────┘
```

---

## End-to-End Data Flow (happy path)

1. **Exchange WebSocket** sends an L2 diff or trade tick.
2. **Aggregator** parses it, applies it to the `OrderBook` state machine, detects
   sequence gaps via `GapDetector`, then emits:
   - A `Tick` struct to the per-symbol `Worker`.
   - The worker writes the tick to `ticks:{exchange}:{symbol}` Redis stream.
   - The QuestDB ILP writer fires-and-forgets the tick row.
   - The pub/sub publisher pushes the full OB snapshot JSON to `orderbook:{ex}:{sym}`.
3. **Candle Service** `Consumer` reads `ticks:*` streams via `XREADGROUP`, ACKs before
   processing, and hands ticks to the `Accumulator` (1-second bar builder).
   - Every second boundary, a complete `Bar` (67 fields) is emitted.
   - The `Cascade` engine folds that bar into 7 coarser timeframes (1m → 1w).
   - The `Flusher` writes bars to QuestDB and publishes to Redis streams/pub/sub.
4. **Bot Service** `BusManager` reads `candles:close:*` streams, routes each
   `BarClose` event to every loaded strategy's asyncio queue.
   - The strategy's `on_bar()` decides whether to trade.
   - `OrderQueueWorker` validates, risk-gates, and places orders via REST.
   - Fills arrive on the exchange WS private channel and are recorded to QuestDB.
5. **Gateway** `runSubscriber` psubscribes `orderbook:*` and `candles1s:*` from Redis
   and forwards them to browser WebSocket clients who have subscribed to that symbol.
6. **Dashboard** polls QuestDB every second for new candle rows and renders
   candlestick + depth heatmap + footprint on the charts page.

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Go for aggregator + candle-service + gateway | Zero-copy JSON parsing, goroutine-per-symbol, no GC pressure on the hot path |
| Python for bot + dashboard | Fast iteration on strategy logic; Dash/Plotly for live charts |
| Redis Streams (`XREADGROUP`) for tick delivery | Durable, consumer-group delivery; `XAUTOCLAIM` for crash recovery |
| Redis Pub/Sub for real-time OB | Instant fan-out to gateway and bot without stream lag |
| QuestDB ILP (fire-and-forget) | Line protocol over TCP is the fastest write path; errors logged not propagated |
| Blue-green candle deployment | Zero-downtime upgrades; shadow slot warms up before promotion |
| `time.Now()` banned in `internal/` | All clocks injected → fully deterministic L1 tests |
| No goroutines in orderbook | Pure state machine with single-goroutine ownership eliminates data races |

---

## Port Reference

| Service | Port | Endpoints |
|---------|------|-----------|
| Aggregator | 8080 | `/health`, `/metrics`, `/version` |
| Candle (blue) | 8081 | `/health`, `/metrics`, `/promote` |
| Candle (green) | 8082 | `/health`, `/metrics`, `/promote` |
| Bot | 8090 | `/health`, `/metrics`, `/backtest`, `/validate`, `/strategies` |
| Gateway | 8083 | `/health`, `/ws` |
| Dashboard | 8050 | `/` (Dash app) |
| QuestDB HTTP | 9000 | `/exec`, `/imp`, `/exp` |
| QuestDB ILP | 9009 | TCP line protocol |
| Redis | 6379 | RESP protocol |
| Prometheus | 9090 | `/graph`, `/api/v1/*` |
| Grafana | 3000 | Web UI |
| Alertmanager | 9093 | `/api/v1/alerts` |
| Loki | 3100 | `/loki/api/v1/*` |
