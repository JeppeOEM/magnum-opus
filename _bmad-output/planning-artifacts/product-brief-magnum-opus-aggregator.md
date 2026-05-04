---
title: "Product Brief: Go Order Book Aggregation Service"
status: "reviewed"
created: "2026-05-03"
updated: "2026-05-03"
inputs:
  - "_bmad-output/brainstorming/brainstorming-session-2026-05-03-1200.md"
  - "_bmad-output/planning-artifacts/research/technical-storage-architecture-crypto-trading-research-2026-05-03.md"
  - "_bmad-output/planning-artifacts/research/domain-crypto-market-microstructure-signals-research-2026-05-03.md"
  - "memory/snapshot_1s_schema.md"
  - "memory/service_map.md"
---

# Product Brief: Go Order Book Aggregation Service

## Executive Summary

Algorithmic trading on real market data requires a foundation of absolute correctness — every missing tick, every silently-dropped sequence number, every reconnect that resumes from a stale delta is a data corruption event that poisons every downstream model and signal built on top of it. The Go Aggregation Service is that foundation. It is a single-responsibility, always-on Go service that connects to KuCoin and Bybit WebSocket feeds, maintains live L2 order book state for up to 200 symbols, and writes normalized tick data to Redis Streams and QuestDB with gap detection as a first-class feature.

This service is the only component in the magnum-opus trading system that touches raw exchange feeds. It does one thing: capture data correctly. Everything else — candle aggregation, indicator computation, ML inference, strategy execution — is only as good as the data this service provides. Built with TDD from the first line of code, the only acceptable data gaps are those caused by factors outside the service's control — exchange outages, network failures, API rate limits. A bug in this service is not a bug in a strategy; it is corruption at the root.

## The Problem

Getting clean, continuous, gap-aware L2 order book data from crypto exchanges is genuinely hard, and no existing open-source solution solves it completely:

- **Commercial vendors** (Tardis.dev ~$500+/month, Kaiko enterprise pricing) provide managed data feeds with external dependency, latency overhead, and subscription lock-in — unsuitable for a self-hosted system where cost is an explicit constraint.
- **General trading frameworks** (GoCryptoTrader, CCXT, freqtrade) are designed for execution, not data infrastructure. They abstract away the exchange-specific details that matter — KuCoin's token-based WebSocket renewal every 24h, Bybit's 10-topic-per-connection limit, per-exchange sequence number semantics.
- **DIY Python collectors** are common but fragile: the GIL limits true concurrency, reconnect logic is ad-hoc, and gap detection is usually absent entirely.

The consequence of building on a leaky data foundation: backtests and ML models that appear to work on incomplete data, strategy signals trained on gaps silently treated as continuity, and production behaviour that diverges from test behaviour in ways that are impossible to trace back to the source.

## The Solution

A purpose-built Go service with one invariant: **every tick that can be captured, is captured; every tick that cannot be captured, is explicitly flagged.**

Core behaviour:
- Maintains live L2 order book state per (exchange, symbol) from WebSocket feeds
- On every disconnect, buffers incoming deltas, requests a full snapshot, merges buffered updates against the snapshot sequence number, then resumes live feed — never resumes from a stale delta, never loses updates that arrived during the snapshot window
- Detects sequence number gaps and publishes an explicit `is_gap: true` marker to the same Redis Stream inline with the tick flow — downstream consumers see gaps as data, not as silence
- Handles reconnects with per-exchange token bucket rate limiting and exponential backoff (1s → 2s → 4s → max 60s) — no thundering herd on exchange APIs after an outage
- Writes to Redis Streams and QuestDB asynchronously — write latency never stalls data capture; if Redis is slow, oldest buffered ticks are dropped and a gap marker is emitted
- Health endpoint returns ok / degraded / down with per-exchange symbol counts and gaps-last-hour — container orchestration and monitoring can act on it
- QuestDB WAL suspension (a failure mode where QuestDB silently drops all writes) is detected proactively via `wal_tables()` polling every 30 seconds and triggers automated `ALTER TABLE RESUME WAL` recovery

## What Makes This Different

**Correctness as the primary design constraint, not throughput.** Most open-source data collectors are optimised for "works most of the time." This service makes failure modes explicit and recoverable:

- Gap markers travel in-band — downstream consumers know exactly when and where history is incomplete, with gap cause distinguishable (internal buffer drop vs external exchange outage) via structured log fields and a dedicated `gaps:log` Redis Stream
- The Exchange interface decouples correctness invariants from exchange-specific protocols — the snapshot/delta merge, sequence tracking, and reconnect behaviour are tested once against the interface; each exchange implementation is tested against a real in-process mock WebSocket server
- TDD from day one — the test suite is the correctness specification. Reconnect race conditions, gap detection edge cases, and backpressure paths are exercised by tests before any production code is written
- No existing open-source Go project combines all of: gap detection, in-band gap markers, snapshot/delta merge on reconnect, per-exchange rate limiting, and production-grade observability in a single focused service

## Who This Serves

**Primary: mrqdt (sole developer and operator)**

Needs a data foundation reliable enough that strategy failures are strategy failures, not data failures. Needs a service that runs unattended for weeks at a time. Needs observable failure modes — when something goes wrong at 3am, the `/health` endpoint and `aggregator_gaps_total` metric tell you exactly what and where, without log archaeology.

**Downstream consumers (indirect):** Candle Service, AI/ML Service, Bot Manager — all depend on the tick stream being complete and correctly gap-annotated. The aggregator's correctness contract is their data contract.

## Success Criteria

**Build quality:**
- TDD throughout — tests written before implementation for all state machine logic
- Test categories: unit (order book state machine, sequence tracking), integration (in-process mock WebSocket server round-trips, snapshot/delta merge verification), fault injection (simulated network drop mid-stream, Redis unavailable, QuestDB suspended)
- Required fault injection test case: snapshot arrives at sequence N, buffered deltas cover N−5 to N+10 — verify no gap marker is emitted, no duplicate ticks are produced, and the live feed resumes at N+11
- Coverage gate enforced: `go test -covermode=atomic -coverprofile` — line coverage is a floor, not a ceiling; critical concurrency and error paths require explicit test cases beyond line coverage

**Production correctness:**
- Zero data gaps attributable to bugs in this service — internal gaps (buffer overflow, merge error, sequence tracking bug) are distinguishable from external gaps (exchange disconnect, network timeout) via `gap_cause` field in every gap marker and `gaps:log` record
- `gap_cause` taxonomy (four values, exhaustive): `internal_buffer_overflow` (Redis write queue full, oldest tick dropped), `internal_merge_error` (snapshot/delta sequence verification failed), `external_disconnect` (WebSocket closed by exchange or network), `external_rate_limit` (reconnect throttled by per-exchange rate limiter)
- External gap SLO: gap rate must not exceed 1 gap per symbol per 24h during normal exchange trading hours — `aggregator_gaps_total{cause="external_*"}` crossing this threshold is a paging alert; `cause="internal_*"` crossing zero is a critical bug
- All external gaps explicitly flagged with `is_gap: true`, recorded in `aggregator_gaps_total` Prometheus counter and `gaps:log` Redis Stream
- QuestDB WAL suspension triggers automated recovery within one health-check interval (30 seconds); `/health` reflects suspended state immediately

**Operational:**
- Runs unattended 30+ days under systemd (`Restart=always`, `MemoryMax` set) without manual intervention
- Redis Streams trimmed at `MAXLEN ~ 50,000` per stream (~2 minutes of tick buffer per symbol) — prevents unbounded memory growth while preserving enough history for consumer catch-up after brief downtime
- Candle Service receives complete, gap-annotated tick flow it can build verified 1-second bars from

## Scope

**In for V1:**
- KuCoin and Bybit public WebSocket feeds — L2 order book, 100–200 symbols
- Multiplexed WebSocket connections: ~20 connections for 200 symbols on Bybit (10 topics/conn), ~1 connection on KuCoin (300 subs/conn)
- Redis Streams: `ticks:{exchange}:{symbol}`, gap markers in-band, `MAXLEN ~ 50,000` per stream
- QuestDB ILP write path — HTTP transport, 500ms flush interval, `cairo.commit.lag=1000ms`
- Sequence number gap detection per (exchange, symbol) with gap cause taxonomy (internal vs external)
- Per-exchange reconnect rate limiter — token bucket, max 1 reconnect/sec, burst 3, exponential backoff 1s → 60s
- Snapshot/delta merge on reconnect — buffer deltas during snapshot request, verify sequence overlap, replay before live feed
- Exchange interface + factory registry — `Connect`, `Subscribe`, `Ticks()`, `Close()`; KuCoin and Bybit implementations
- KuCoin specifics: REST token fetch before WS connect, 24h token renewal built in, ping/pong heartbeat
- `/health` — HTTP 200/206/503 + JSON (per-exchange: connected, symbols_up, symbols_total; gaps_last_hour)
- `/metrics` — Prometheus: `aggregator_connection_up`, `aggregator_ticks_total`, `aggregator_gaps_total` [critical], `aggregator_reconnects_total`, `aggregator_last_tick_timestamp`, `aggregator_ws_connections_active`
- QuestDB WAL health check — `wal_tables()` polled every 30s, auto-recovery via `ALTER TABLE RESUME WAL`
- Systemd unit file with `Restart=always` and resource limits
- Fully env-var driven configuration (EXCHANGES, SYMBOLS, per-exchange API keys, REDIS_URL, QUESTDB_URL)
- Full test suite: unit + integration + fault injection

**Explicitly out of scope:**
- Candle or indicator computation — owned by Python Candle Service
- Frontend serving or WebSocket gateway
- Private feed support — config flag exists, implementation deferred to V2
- Additional exchanges beyond KuCoin and Bybit
- L3 order book (individual order-level events)
- Order routing, execution, or any write path to exchange APIs

## Data Contract

The aggregator's output is the input contract for all downstream services. Tick message schema (JSON, published to `ticks:{exchange}:{symbol}`):

```json
{
  "exchange":    "bybit",
  "symbol":      "BTCUSDT",
  "exchange_ts": 1714732800123,
  "local_ts":    1714732800145,
  "seq":         8842001,
  "is_snapshot": false,
  "is_gap":      false,
  "bids":        [["65432.10", "0.542"], ["65431.00", "1.200"]],
  "asks":        [["65433.00", "0.100"], ["65434.50", "2.300"]]
}
```

Gap markers: same schema with `is_gap: true`, `bids: []`, `asks: []`, plus a `gap_cause` string field. Valid values: `internal_buffer_overflow`, `internal_merge_error`, `external_disconnect`, `external_rate_limit`. Downstream consumers can filter by cause family to distinguish service bugs from exchange-side events.

`gaps:log` Redis Stream record — written for **every** gap marker regardless of cause (internal and external alike); forensic record for post-mortem analysis:
```json
{
  "exchange":   "bybit",
  "symbol":     "BTCUSDT",
  "gap_ts":     1714732800000,
  "gap_cause":  "internal_buffer_overflow",
  "seq_before": 8841999,
  "seq_after":  8842050
}
```

The Candle Service is the only consumer that writes this data to QuestDB (`snapshot_1s`, 67 fields). The aggregator does not write `snapshot_1s` — it writes raw ticks only.

## Roadmap Thinking

V1 is the permanent foundation. The Exchange abstraction means adding Binance, OKX, or any future exchange is additive — implement the interface, register in the factory, done. If the system scales beyond a single VM, Redis Streams partition naturally per exchange shard. The correctness invariants — snapshot on reconnect, gap markers in-band, silent failure detection — do not change as the system grows. They are the specification, not an implementation detail.
