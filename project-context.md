---
project_name: magnum-opus
user_name: mrqdt
date: '2026-05-08'
optimized_for_llm: true
---

# Project Context: magnum-opus

_Top-level orientation for AI agents. Read this first. Then read the service-specific context for whichever service you are working on._

---

## What This System Does

magnum-opus is an algorithmic crypto trading data pipeline running on a single Hetzner CPX41 (8 vCPU, 16 GB RAM). It captures live L2 order book and trade data from KuCoin and Bybit, computes microstructure aggregates, and stores them for downstream strategy and ML use.

It does NOT execute trades. It does NOT manage strategy logic. It is a data capture and feature pipeline.

---

## Service Map

| Service | Language | Directory | Status | Purpose |
|---|---|---|---|---|
| **Go Aggregator** | Go 1.21 | `aggregator/` | **Complete (Epics 1–4)** | WebSocket feeds → Redis Streams. Connects to KuCoin + Bybit, maintains L2 OB, emits normalized ticks and gap markers. |
| **Go Candle Service** | Go 1.21 | `candle-service/` | **In development (Epics 5–9)** | Redis Streams → QuestDB + Redis. Reads ticks, computes 67-field 1s OHLCV + microstructure, cascades to 7 timeframes, flushes daily Parquet to B2. |
| AI/ML Service | Python | _(not yet created)_ | Backlog | Reads QuestDB features, trains and serves ML models. |
| Bot Manager | Python | _(not yet created)_ | Backlog | Reads candle streams, executes trading strategies. |
| Kill Switch | Go | _(not yet created)_ | Backlog | Emergency position flattener, independent of other services. |
| Frontend API | Python/Go | _(not yet created)_ | Backlog | Serves dashboard and monitoring UI. |

---

## Data Flow

```
KuCoin WSS ──┐
             ├──► Go Aggregator ──► ticks:{exchange}:{symbol} (Redis Streams)
Bybit WSS ───┘         │                        │
                        └──► gaps:log            │
                             (Redis Stream)       ▼
                                        Go Candle Service
                                                 │
                              ┌──────────────────┼──────────────────┐
                              ▼                  ▼                  ▼
                         QuestDB            candles:{ex}:{sym}:{tf}  ob_features:{ex}:{sym}
                       snapshot_1s          (Redis Streams)         (Redis Stream)
                              │
                              ▼
                      Daily Parquet → Backblaze B2
```

---

## Shared Infrastructure

| Component | Image | Port | Notes |
|---|---|---|---|
| Redis | `redis:7-alpine` | 6379 | Streams for all inter-service data; no pub/sub |
| QuestDB | `questdb/questdb:8.2.1` | 9000 (HTTP), 9009 (ILP TCP) | Never `:latest` or `:8.x` |

Both services use:
- `github.com/redis/go-redis/v9` — never `go-redis/v8`
- `github.com/questdb/go-questdb-client/v3` — ILP over TCP port 9009; NOT goroutine-safe

---

## Key Cross-Service Contracts

### Redis Stream Schema: `ticks:{exchange}:{symbol}`
- **Producer:** Go Aggregator
- **Consumer:** Go Candle Service
- Schema is a downstream contract — field removal or type change requires schema version increment
- MAXLEN: 50,000 entries per stream (~20s buffer at 2,400 ticks/sec)

### Redis Stream: `gaps:log`
- **Producer:** Go Aggregator
- Audit log of all gap events across all symbols

### QuestDB Table: `snapshot_1s`
- **Producer:** Go Candle Service
- **Consumer:** AI/ML Service, Bot Manager, flush pipeline
- Dedup key: `(exchange, symbol, ts_second)` — WAL deduplication enforced

---

## Shared Rules (apply to all Go services)

- `github.com/redis/go-redis/v9` only — never v8, never `go-redis/redis`
- `questdb/questdb:8.2.1` image — never `:latest` or `:8.x`
- `redis:7-alpine` image
- Prometheus: always `prometheus.NewRegistry()` — never `prometheus.MustRegister` or `prometheus.DefaultRegisterer`
- `log/slog` (stdlib) for structured logging — custom handler must implement all 4 methods
- Credentials never in logs, errors, panics, or metrics at any level
- `os.Getenv` only in `internal/config/` of each service
- `context.Context` as first parameter on every IO-touching function

---

## Service-Specific Context

**Read the service context before writing any code for that service. Rules in one service context do NOT apply to another.**

| Service | Context File |
|---|---|
| Go Aggregator | [`aggregator/project-context.md`](aggregator/project-context.md) |
| Go Candle Service | [`candle-service/project-context.md`](candle-service/project-context.md) |
| Python Bot Service | [`bot-service/project-context.md`](bot-service/project-context.md) |

---

## Planning Artifacts

| Artifact | Path |
|---|---|
| PRD | `_bmad-output/planning-artifacts/prd.md` |
| Architecture | `_bmad-output/planning-artifacts/architecture.md` |
| Requirements Inventory (all services) | `_bmad-output/planning-artifacts/epics-requirements.md` |
| Aggregator Epics & Stories (Epics 1–4) | `_bmad-output/planning-artifacts/epics.md` |
| Candle Service Epics & Stories (Epics 5–9) | `_bmad-output/planning-artifacts/epics-candle.md` |
| Bot Service Epics & Stories (Epics 11–16) | `_bmad-output/planning-artifacts/epics-bot.md` |
| Sprint Status | `_bmad-output/implementation-artifacts/sprint-status.yaml` |
| Data Contract | `docs/data-contract.md` |
| Ops Runbook | `docs/ops.md` |

---

## What Is NOT in This Repo

- Exchange order execution
- Strategy logic
- Position management
- Risk engine

_Last updated: 2026-05-08_
