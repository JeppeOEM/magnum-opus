# MAINTAINERS — Package Ownership and Invariants

Inspired by the Linux kernel `MAINTAINERS` file. Each package entry lists:
owner goroutine/thread, maintained invariants, and which NOMICON sections apply.

---

## aggregator/internal/orderbook

**Context:** Single-goroutine. No concurrent access.
**Owner:** The per-symbol `Worker` goroutine spawned by the coordinator.
**NOMICON:** §1 (single-goroutine ownership)

**Invariants:**
1. `OrderBook` is never shared between goroutines (enforced by opaque struct + no public mutex)
2. `Apply` is idempotent for duplicates (seq ≤ lastSeq → no-op)
3. `Snapshot()` returns a deep copy — mutations do not affect the book
4. Unknown `Side` values are silently discarded (never route to a default side)
5. Empty `Price` strings create no phantom map keys (discarded)

**Tests:** `aggregator/internal/orderbook/orderbook_test.go`

---

## aggregator/internal/reconnect

**Context:** Single-goroutine. No concurrent access.
**Owner:** The per-symbol `Worker` goroutine.
**NOMICON:** §3 (double-NeedsSnapshot preservation)

**Invariants:**
1. Buffer is only cleared in `MergeSnapshot` on success — never in `NeedsSnapshotNow`
2. State machine transitions: Initial → Buffering (→ Buffering on double-NeedsSnapshot) → Live
3. `Feed(seq)` is a no-op in StateLive — caller is responsible for applying deltas directly
4. MaxUint64 overflow guard prevents false stale classification at seq boundary

**Tests:** `aggregator/internal/reconnect/*_test.go`

---

## aggregator/internal/gapdetector

**Context:** Stateless. Thread-safe (pure function).
**Owner:** N/A (pure function, no state)

**Invariants:**
1. `Detect(prev, next, cause, clock)` returns nil iff `next == prev+1`
2. nil clock panics with a clear message (fail-fast for injection failures)
3. Exactly 4 valid `Cause` values defined (verified by `audit-docs.sh`)

**Tests:** `aggregator/internal/gapdetector/*_test.go`

---

## aggregator/internal/metrics

**Context:** Thread-safe. All methods protected by Prometheus internal locks + `lastGapMu`.
**Owner:** Multiple goroutines (health goroutine, per-symbol workers, coordinator)

**Invariants:**
1. `PreInit` must be called before `Run` — sets all series to 0 before any feed connects
2. `RecordGap` filters `external_disconnect` and `internal_buffer_overflow` from health
3. `GapHealthy` returns 1.0 if no non-transient gap in `windowSecs`
4. `lastGapTimes` protected by `lastGapMu` mutex

**NOMICON:** §4 (gap cause filtering), §9 (pre-initialization)
**Tests:** `aggregator/internal/metrics/metrics_test.go`

---

## aggregator/internal/coordinator

**Context:** Multiple goroutines. Fan-out + per-symbol workers + snapshot dispatcher.
**Owner:** Entry point `cmd/aggregator/main.go`

**Goroutines spawned:**
- 1 × snapshot dispatcher goroutine
- N_exchanges × fanout goroutine (one per exchange)
- N_exchanges × signal drain goroutine (one per exchange)
- N_symbols × per-symbol Worker goroutine

**Invariants:**
1. Each `symbolEntry.deltas` channel has capacity `deltaChanCap=256`
2. Tick fanout uses non-blocking send (full channel → tick dropped + WARN log)
3. `Shutdown()` is idempotent (`shutdownOnce`)
4. `WithMetrics` must be called before `Run` (coordinator builder pattern)
5. ILP close on shutdown has 5-second timeout

**Tests:** `aggregator/internal/coordinator/*_test.go`

---

## aggregator/internal/config

**Context:** Read-only after Load(). Thread-safe.
**Owner:** Loaded once by `cmd/aggregator/main.go`, then passed to constructors.

**Invariants:**
1. `Credential.Format` redacts all `fmt` verbs (verified by tests)
2. `Credential.LogValue` returns `slog.StringValue("[REDACTED]")`
3. `Credential.MarshalText` returns `[REDACTED]` bytes
4. Missing required env vars → single error listing all of them (not first-fail)

**NOMICON:** §5 (credential sealing)
**Tests:** `aggregator/internal/config/config_test.go`

---

## candle-service/internal/consumer

**Context:** Single goroutine per (exchange, symbol). Exception: `Promote()` and `LastShadowID()` called from HTTP handler goroutine.
**Owner:** One goroutine per Consumer, started by `cmd/candle/main.go`

**Invariants:**
1. XACK before dispatch (see NOMICON §2)
2. `seenIDs` dedup set is per-second (cleared on BarClose)
3. `shadowMode` is an atomic — safe to read from HTTP handler goroutine
4. `lastShadowID` and `xreadCursor` protected by `shadowMu`
5. Shadow loop exits when `shadowMode.Store(false)` is observed at loop top

**NOMICON:** §2 (XACK ordering), §6 (shadow mode cursor management), §7 (gap dedup ZSET)
**Tests:** `candle-service/internal/consumer/*_test.go`

---

## candle-service/internal/flusher

**Context:** Single goroutine (flusher.Run).
**Owner:** One goroutine started by `cmd/candle/main.go`

**Invariants:**
1. `AbortMultipartUpload` uses a fresh `context.Background()` (not the cancelled context)
2. `candle:last_flush_date` Redis key tracks the last successfully flushed date
3. Catch-up runs on startup to recover any missed days
4. `FLUSH_DATE_OVERRIDE` mode: flush one date and return (used for backfill/testing)

**Tests:** `candle-service/internal/flusher/*_test.go`

---

## bot-service/bot_service/bus/event_bus.py (BusManager)

**Context:** Multi-threaded. `bus-manager` OS thread + `bus-pubsub` OS thread + per-strategy threads.
**Owner:** `bot-service/bot_service/main.py`

**Invariants:**
1. `_handles` dict is protected by `_handles_lock` (RLock — reentrant for `dynamic_register`)
2. `_deliver` uses drop-oldest semantics on overflow (not drop-newest)
3. `_drop_oldest` uses `get_nowait()` from bus-manager thread — CPython GIL makes this safe
4. `run_coroutine_threadsafe` is the only cross-thread asyncio queue enqueue path
5. Backoff caps at `_BACKOFF_CAP=60s`; 3 consecutive cap hits → SIGTERM

**NOMICON:** §8 (BusManager cross-thread queue access)

---

## bot-service/bot_service/strategy/base.py (BaseStrategy)

**Context:** Multi-threaded. Per-strategy asyncio event loop + heartbeat OS thread + bus-timeout OS thread + emergency-close daemon threads.
**Owner:** `StrategyRegistry`

**Invariants:**
1. `_on_bus_timeout` is called from bus-timeout thread — must only use thread-safe operations
2. `_emergency_close_in_flight` protected by `_emergency_close_lock`
3. `_open_positions` is mutated by order-worker thread AND read by bus-timeout thread — not synchronized; CPython dict operations are GIL-atomic for simple reads/writes
4. `_last_event_ts` is a float set by event loop, read by bus-timeout thread — atomic on CPython
5. Emergency close retries indefinitely (5s sleep between attempts)

---

## gateway/internal/hub

**Context:** Thread-safe. Hub methods acquire `sync.RWMutex`.
**Owner:** `cmd/gateway/main.go`

**Invariants:**
1. All `Hub` exported methods are goroutine-safe (RWMutex)
2. `Client.Send` is non-blocking (drops if buffer full — capacity 16)
3. `lastSnap` stores last binary snapshot per symbol for new subscriber delivery
4. `WritePump` exits on context cancel or send channel close

**Tests:** `gateway/internal/hub/hub_test.go`
