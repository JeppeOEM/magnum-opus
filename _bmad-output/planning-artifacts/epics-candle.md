---
stepsCompleted: ['step-01-validate-prerequisites', 'step-02-design-epics']
status: complete
service: candle-service
inputDocuments:
  - '_bmad-output/planning-artifacts/prd.md'
  - '_bmad-output/planning-artifacts/architecture.md'
  - 'docs/data-contract.md'
requirementsRef: '_bmad-output/planning-artifacts/epics-requirements.md'
---

# magnum-opus — Candle Service Epic Breakdown

> Requirements inventory (CS-FR, CS-NFR definitions) → [epics-requirements.md](epics-requirements.md)

This section documents the epic and story breakdown for the Go Candle Service, the second major component of magnum-opus. It reads normalized ticks from Redis Streams produced by the Go aggregator and computes 1-second OHLCV + microstructure aggregates, multi-timeframe candles, and OB feature snapshots.

Input documents: `prd.md`, `architecture.md`, `docs/data-contract.md`

## Candle Service Epic List

- Epic 5: Candle Service Foundation
- Epic 6: OB-Derived Features
- Epic 7: Trade & Quality Features
- Epic 8: Multi-Timeframe Cascade & Redis Output
- Epic 9: Cold Storage & Operations

---

## Epic 5: Candle Service Foundation

The operator can run the Candle Service alongside the aggregator, confirm it reads from Redis, and see basic OHLCV rows appearing in `snapshot_1s` in QuestDB.

**FRs covered:** CS-FR1, CS-FR2, CS-FR3, CS-FR4, CS-FR18 (OHLCV fields), CS-FR25, CS-FR26, CS-FR27, CS-FR28

**Implementation notes:**
- Story 1: Go project setup — `candle-service/` directory, `go.mod` (module `github.com/mrqdt/magnum-opus/candle-service`), multi-stage Dockerfile, docker-compose update (add candle-service profiles `candle-blue` and `candle-green`, each with its own `HTTP_PORT` mapping, `CANDLE_SLOT` env var, mem_limit, cpus, stop_grace_period). Graceful shutdown on SIGTERM: flush current in-flight 1s accumulator to QuestDB with `is_partial=true`, XACK all pending Redis messages, close ILP connection cleanly. Shutdown timeout configurable via `SHUTDOWN_TIMEOUT_S` (default 10s); after timeout the process exits regardless.
- Story 2: Migration runner — `candle-service/migrations/` directory of numbered SQL files (`001_snapshot_1s.sql`, `002_flush_manifest.sql`, …). On startup, before the Redis consumer starts: connect to QuestDB REST `/exec`, create `schema_migrations` table if absent (`version long, name string, applied_at timestamp, checksum string, TIMESTAMP(applied_at) PARTITION BY YEAR WAL`), then apply all unapplied migration files in ascending version order. Verify the SHA-256 checksum of already-applied migrations — refuse to start if a previously-applied file has changed. Service exits non-zero if any migration fails; never start consuming until all migrations have been applied successfully. Migration rules for QuestDB: additive only — `ALTER TABLE ADD COLUMN` is permitted; `DROP COLUMN` and `RENAME COLUMN` are not supported by QuestDB and must never appear in a migration file.
- Story 3: `snapshot_1s` DDL — migration file `001_snapshot_1s.sql`: full 67-column schema including `best_bid_open` and `best_ask_open`, all non-identity columns nullable. Table must declare `DEDUP UPSERT KEYS(ts, exchange, symbol)` — required for ILP upsert semantics; without it every ILP write appends unconditionally and correction writes produce duplicate rows.
- Story 4: Redis consumer — consumer group per symbol, parse tick/gap/unknown messages, dedup gap markers on `(exchange, symbol, seq_before, seq_after, gap_cause)` using a Redis `ZSET` (`candle:gap_dedup:{exchange}:{symbol}`, score=unix_ms, bounded to 10,000 entries via `ZREMRANGEBYRANK`); XACK the message before updating accumulator state so redelivery after a crash cannot double-count `gap_count`; startup MAXLEN check; on snapshot event flush accumulator (is_partial=true) + reinit OB state + reset OFI (CS-FR27)
- Story 5: L2 OB state machine — in-memory per symbol, snapshot resets, zero-size delta removes level, cold-start delta buffering until first snapshot (buffer size `COLD_START_BUFFER_SIZE`, default 10,000 deltas — approximately 10s of ticks at peak rate; chosen to cover the typical snapshot delivery window without unbounded memory growth); buffer overflow → gap marker with `gap_cause=cold_start_buffer_overflow`
- Story 6: 1s OHLCV accumulator + QuestDB ILP writer — second boundaries driven by `time.Ticker` goroutine (NOT the Redis consumer); upsert key `(exchange, symbol, ts_second)` (deduplication enforced by the `DEDUP UPSERT KEYS` declared in Story 2 DDL — ILP itself is append-only); write OHLCV fields, leave remaining columns null; WAL suspension detection: poll QuestDB REST `/exec` with a probe query every `WAL_PROBE_INTERVAL_S` (default 5s) when last successful write is >10s ago — a WAL-suspended error response triggers auto-resume via `ALTER TABLE snapshot_1s RESUME WAL`; ticks arriving during suspension buffered in a bounded in-memory queue (`WAL_BUFFER_SIZE`, default 10,000 rows); overflow drops oldest rows and increments `candle_wal_drop_total` counter; ILP retry with exponential backoff (initial=1s, max=30s)
- Story 7: Observability — `/health`, `/version`, `/metrics` endpoints; Prometheus registry; slog handler wrapper for credential redaction. `/health` response: `{"status":"ok|degraded|critical","consumer_lag_max":N,"shadow_lag":N,"questdb_write_state":"ok|suspended","slot":"blue|green","version":"git-sha"}`; `shadow_lag` is the number of messages between the slot's `lastShadowID` and the stream tip — zero once promoted; deploy script uses this field to gate promotion; must respond within 100ms

**Done when:** `docker-compose up` starts the service, it connects to Redis, QuestDB shows OHLCV rows in `snapshot_1s` for all configured symbols, and `/health` returns ok.

---

## Epic 6: OB-Derived Features

The `snapshot_1s` rows contain all order-book-derived fields: mid-price path, spread, depth at open/close, book shape, market impact, and OFI.

**FRs covered:** CS-FR5, CS-FR6, CS-FR7, CS-FR8, CS-FR9, CS-FR10

**Implementation notes:**
- All features in this epic are derived from the L2 OB state machine built in Epic 5
- Validation story required first: confirm OB state machine produces correct books against known tick fixtures before computing features from it. This story must be closed before any feature story in this epic begins — if the OB state machine has a subtle bug, every field in this epic is silently wrong and there is no downstream signal to detect it.
- CS-FR7: `best_bid_open`/`best_ask_open` captured at first tick of second; if no ticks arrive but book state is valid, carry last-known OB state into open and close fields — OB fields never null when state is known
- CS-FR8: guard against empty book — return null if total volume on either side is zero
- CS-FR9: guard against insufficient depth — return max available depth, not an error or panic
- CS-FR10: OFI uses Cont et al. (2014) queue-flow imbalance definition; `features.OFIDelta()` is a pure stateless function; running sum lives in `accumulator/`; reset on gap event or snapshot reinit

**Done when:** QuestDB `snapshot_1s` rows contain populated best_bid_open, best_ask_open, mid_price_*, spread_*, bid/ask_depth_*, weighted_*_price, depth_to_1pct_*, ofi, ofi_l1 fields.

---

## Epic 7: Trade & Quality Features

The `snapshot_1s` rows contain all trade-derived fields and quality metadata. The 67-field schema is fully populated for all active seconds.

**FRs covered:** CS-FR11, CS-FR12, CS-FR13, CS-FR14, CS-FR15, CS-FR16, CS-FR17, CS-FR18 (null-row behaviour for empty seconds)

**Implementation notes:**
- All features in this epic are derived from trade events (`event_type=trade`) in the tick stream — independent of the OB state machine
- CS-FR12 block trade threshold: rolling 99th-percentile over last N trades per symbol (N = `BLOCK_TRADE_WINDOW`, default 1000); cold-start writes null until `BLOCK_TRADE_MIN_SAMPLE` (default 100) trades seen. Rolling window serialised to Redis (`candle:btw:{exchange}:{symbol}` as a capped list) on each 1s bar close; on startup restore from Redis before consuming any ticks so that routine restarts and deploys do not reset the window. The cold-start null period only applies when Redis state is genuinely absent (true first run). Must be its own story due to the Redis persistence complexity.
- Empty-second null-row: if OB state is valid (no gap or snapshot reinit since last known state), carry last-known top-of-book into `best_bid_open`/`best_ask_open` for that null row; all other computed fields null. If OB state has been reinitialised by a gap or snapshot event since the last row, OB fields are also null — last-known state is NOT carried across a gap boundary. This resolves the apparent conflict with CS-FR7: "never null when state is known" means post-gap seconds have null OB fields because state is unknown, not invalid (CS-FR18)
- CS-FR17: gap_count attributed to the second containing `gap_ts`, not the second the marker is consumed; bar_count always increments

**Done when:** QuestDB `snapshot_1s` rows contain all 67 fields populated for active seconds; empty seconds produce null rows; gap markers correctly increment gap_count.

---

## Epic 8: Multi-Timeframe Cascade & Redis Output

Bots can subscribe to 1m–1w candles via Redis streams. Both in-progress and closed bars are published. OB feature snapshots are available for real-time signal consumers.

**FRs covered:** CS-FR19, CS-FR20, CS-FR21

**Implementation notes:**

**Story 8-1: Cascade Engine & Clock Boundary**
- `internal/cascade/` package: pure, Clock-injected. Implements 1s → 1m → 5m → 15m → 1h → 4h → 1d → 1w cascade. Bar-close boundary owned by a `time.Ticker` goroutine in `cmd/candle/` (NOT the Redis consumer). Bar-close signal through a capacity-1 channel per symbol (non-blocking send; dropped signal increments `candle_bar_close_dropped_total`).
- `AccumulatorApplier.Apply()` is NOT extended in Epic 8 — cascade works at bar-close granularity. Do not add new parameters to the existing interface.
- Cascade accumulator persistence: write 9-field HASH (`open_ts open high low volume quote_volume trade_count bar_count gap_count`) to Redis `candle:acc:{exchange}:{symbol}:{tf}` on each 1s close using `backoff/` with max 3 retries. On failure: log ERROR, increment `candle_cascade_state_write_failure_total{exchange,symbol}`, continue.
- Startup reconstruction sequence (synchronous, before consumer goroutines start): (1) read Redis HASH per `(exchange,symbol,tf)`, (2) if valid → restore cascade accumulator, (3) run `SELECT COUNT(*) FROM snapshot_1s WHERE exchange=? AND symbol=? AND ts_second >= open_ts AND ts_second < boundary` — one COUNT query, not row replay, (4) if count < expected×0.95 → emit `gap_cause=reconstruction_incomplete`, (5) if HASH absent/malformed → start empty, emit gap marker; if QuestDB unavailable at step 3 → skip check, log ERROR.
- Consumer goroutines start only after reconstruction is complete for all symbols.
- Cascade nil aggregation: OHLCV-only (9 fields). Nil 1s bar: open/high/low = skip if nil; close = carry last known; volume/quote_volume/trade_count/gap_count = +0; bar_count = always +1.
- Expose `cascade.CurrentBar(tf) Bar` read-only API on cascade accumulator for use by 8-2 partial publish.
- UTC boundaries: 1m/5m/15m/1h/4h aligned to UTC clock; 1d at 00:00:00 UTC; 1w at Monday 00:00:00 UTC (use `time.Weekday() == time.Monday`, not `time.Truncate`).

**Story 8-2: Redis Candle Stream Publisher**
- `internal/writer/redis/` package: implements `candles:{exchange}:{symbol}:{tf}` stream (partial + close, MAXLEN `CANDLE_STREAM_MAXLEN` default 10,000) AND `candles:close:{exchange}:{symbol}:{tf}` stream (close-only, MAXLEN `CANDLE_CLOSE_STREAM_MAXLEN` default 500).
- **No weekly alias key.** `candles:close:{exchange}:{symbol}:1w` is the canonical weekly close signal.
- 250ms partial publish ticker in `cmd/candle/` (second ticker, same pattern as 1s ticker; non-blocking `PartialPublish` channel per symbol). Calls `cascadeAcc.CurrentBar(tf)` from consumer goroutine. Idle symbols (no tick since last publish) → skip, no stale repeat.
- Partial bars: `is_complete: "false"`. Closed bars: `is_complete: "true"` published to both `candles:` and `candles:close:` streams simultaneously.

**Story 8-3: OB Feature Snapshot Publisher**
- `ob_features:{exchange}:{symbol}` stream, MAXLEN `CANDLE_STREAM_MAXLEN` (default 10,000).
- Published on each 1s bar close from the **closed `Bar` struct** (after `acc.CurrentBar()`, before `acc.BarReset()`). Must NOT read from live OB state after BarReset. Flush sequence: (1) SetCloseDepth, (2) bar = CurrentBar(), (3) write QuestDB, (4) publish candles streams, (5) publish ob_features from bar, (6) BarReset.

**Done when:** Redis streams receive candle messages at all 8 timeframes; `candles:close:*` streams exist and contain only `is_complete: "true"` messages; OB feature snapshots appear in `ob_features:*` streams; 1h/1d/1w bars survive a service restart without truncation; QuestDB-unavailable at startup logs ERROR and starts with empty accumulators; a simulated Redis cascade HASH write failure increments `candle_cascade_state_write_failure_total` and does not block the consumer loop.

---

## Epic 9: Cold Storage & Operations

Daily snapshots of `snapshot_1s` are archived to Backblaze B2. Flush failures are alerted. The complete system runs in production via docker-compose.

**FRs covered:** CS-FR22, CS-FR23, CS-FR24

**Implementation notes:**
- Story 9.1: `flush_manifest` DDL — migration file `002_flush_manifest.sql`: `CREATE TABLE flush_manifest (ts_flush timestamp, exchange symbol, date_flushed date, row_count long, b2_path string, success boolean, error_msg string nullable, duration_ms long, TIMESTAMP(ts_flush) PARTITION BY YEAR WAL)`. Written before any flush code; the migration runner (Epic 5, Story 1) applies it on startup. Future field additions go in numbered migration files (003, 004, …) — additive only.
- Daily Parquet flush: zstd compressed, Hive-partitioned by date, runs at configurable daily time (`FLUSH_TIME_UTC`, default `03:00`)
- Upload wrapped in `context.WithTimeout(4h)`; on cancellation or failure call `s3.AbortMultipartUpload` with a fresh context to prevent leaked B2 incomplete parts
- Catch-up on startup: read `last_flush_date` from Redis; if key absent, query `flush_manifest` for the most recent `success=true` row to determine `last_flush_date` — this prevents silent data loss when Redis is reset in production. Only if `flush_manifest` is also empty (true first run) does the service skip catch-up and start from the current day. Flush all missing days sequentially. Before flushing each date, query `flush_manifest` for an existing `success=true` row — skip if found (idempotent).
- On failure: write flush_manifest row with error field AND publish to `alerts:flush_failure`; if Redis write also fails, increment `candle_flush_alert_failure_total` Prometheus counter
- Story 9.2: Blue/green deployment — `scripts/deploy-candle.sh NEW_SLOT OLD_SLOT` orchestrates zero-downtime, zero-data-loss updates using shadow read warmup + position-anchored promotion + XAUTOCLAIM recovery:
  - **Shadow read warmup:** new slot reads the live Redis stream via `XREAD` (no consumer group, no message claiming), building OB state machine and rolling windows without writing to QuestDB. Tracks `lastShadowID` — the last message ID processed via XREAD — continuously.
  - **Promotion is anchored to `lastShadowID`, not `>`:** on promotion, new slot starts `XREADGROUP` from `lastShadowID` (not stream tip). This ensures every message is processed exactly once — no double-counting, no skips at the XREAD→XREADGROUP boundary.
  - **OHLCV accumulator reset at promotion:** new slot discards its in-memory OHLCV accumulator on promotion (which may contain partially shadow-processed ticks for the current second) and reconstructs from QuestDB's last `is_partial=true` row per symbol. OB state machine and rolling windows are kept warm — only the current in-flight second's OHLCV is reset.
  - **XAUTOCLAIM on promotion:** after switching to XREADGROUP from `lastShadowID`, new slot calls `XAUTOCLAIM` for all pending messages > 0ms — recovers any messages old slot claimed but never ACKed (covers both graceful and ungraceful old-slot shutdown).
  - **Deploy sequence:**
    1. Start new slot (`docker-compose --profile candle-${NEW_SLOT} up -d`) — runs migrations, begins shadow XREAD, tracks `lastShadowID`
    2. Poll `/health` until `status=ok` AND `shadow_lag=0` (new slot's `lastShadowID` has caught up to stream tip) within `DEPLOY_HEALTH_TIMEOUT_S` (default 60s) — if timeout/degraded, stop new slot and abort; old slot untouched. `shadow_lag` exposed in `/health` response.
    3. SIGTERM old slot — old slot flushes in-flight accumulator (`is_partial=true`), XACKs all pending messages, closes ILP, exits
    4. If old slot doesn't exit within `SHUTDOWN_TIMEOUT_S + 5s`, SIGKILL it — safe because XAUTOCLAIM covers any unACKed messages
    5. New slot promotes: `XREADGROUP` from `lastShadowID` → `XAUTOCLAIM` pending → reconstruct OHLCV from QuestDB → resume consuming. If XAUTOCLAIM fails, page operator — do not continue silently.
    6. Verify new slot consuming: poll `/health` for `consumer_lag_max` trending down over 30s window
    7. If new slot fails post-promotion: restart old slot — rejoins consumer group, runs XAUTOCLAIM to recover new slot's orphaned messages, restores state from Redis. Restart IS rollback — no special path needed.
  - **`/health` response fields required for deploy script:** `status`, `shadow_lag` (max messages-behind-stream-tip across all configured symbols during warmup), `shadow_lag_symbol` (symbol with highest lag when shadow_lag > 0), `consumer_lag_max` (after promotion), `slot`, `version`
  - **Three deployment rules:**
    - Rule 1: Abort before SIGTERM is always safe — old slot untouched
    - Rule 2: SIGKILL after timeout is safe — XAUTOCLAIM covers unACKed messages
    - Rule 3: If new slot fails post-promotion, restart old slot — Redis state is intact, XAUTOCLAIM-on-startup is the universal recovery mechanism
  - docker-compose profiles `candle-blue` and `candle-green` each bind a distinct `HTTP_PORT` (default: blue=8080, green=8081); `CANDLE_SLOT` env var included in all log lines and `/health` response

**Done when:** A flush of a specific past date (triggered via `FLUSH_DATE_OVERRIDE` env var) completes successfully and the resulting Parquet file is readable in B2; `flush_manifest` shows a `success=true` row for that date; a simulated failure (e.g. invalid B2 credentials) produces a `success=false` row in `flush_manifest` and a message in `alerts:flush_failure`; a simulated Redis reset followed by restart correctly resumes catch-up from the last `flush_manifest` success row rather than skipping to the current day; `scripts/deploy-candle.sh green blue` completes without error — the green slot passes smoke tests and the blue slot stops cleanly, with no gap in `snapshot_1s` rows during the transition window.
