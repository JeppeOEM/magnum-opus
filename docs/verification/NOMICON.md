# NOMICON — Dark Corners of magnum-opus

> The Book of Things That Look Like Ordinary Code But Will Break Catastrophically If Misunderstood.
>
> Every section has: what it is, why it is dangerous, and a proof sketch showing the invariant holds.

---

## 1. OrderBook — single-goroutine ownership

**File:** `aggregator/internal/orderbook/orderbook.go`

**Danger:** `OrderBook` has no mutex. The comment says "Not safe for concurrent use" but there is no compile-time enforcement. If you share an `OrderBook` between two goroutines — even read-only — you get data races that the race detector will catch but production won't flag until corruption occurs.

**Invariant:** Every `OrderBook` instance is owned by exactly one goroutine: the per-symbol `Worker` goroutine spawned by the coordinator. The `Coordinator.New()` function creates one `Worker` and one `OrderBook` per `(exchange, symbol)` pair; no other goroutine holds a reference.

**Proof sketch:**
1. `coordinator.New()` creates `ob := orderbook.New()` and passes it only to `NewWorker(...)`.
2. `NewWorker` stores it in `w.book` (unexported field).
3. `Worker.Run(ctx)` is launched in a single goroutine (`go func() { w.Run(ctx) }`).
4. `w.book` is only accessed inside `handleTick`, which is called only from `Run` (same goroutine).
5. No other code path references `w.book`. **QED.**

**What breaks if violated:** Concurrent `Apply` + `Snapshot` produces a torn map read — partial bid/ask maps. The resulting synthetic tick carries invalid mid-price and depth figures that propagate into QuestDB.

---

## 2. XACK-before-dispatch in the candle consumer

**File:** `candle-service/internal/consumer/consumer.go:handleMessage`

**Danger:** The consumer sends `XACK` to Redis *before* dispatching the parsed message to the accumulator. A panic after ACK means the message is gone and the bar will be silently incomplete. This is intentional (see package doc: "XAUTOCLAIM on next start"), but the consequence is non-obvious.

**Invariant:** After ACK, if the process crashes, the *next* start calls `XAutoClaimPending` which reclaims messages from the *previous* consumer's pending-entry list. This works because the old slot's messages were never ACKed from the *new* slot's perspective — only the message's original consumer-name ACK is gone.

**Why ACK-before:** If we ACK *after* dispatch and the accumulator panics mid-bar, the message re-enters the PEL and gets redelivered on the next XAUTOCLAIM, causing a double-count within the same second window. The dedup set (`seenIDs`) handles this within a window but not across restarts. ACK-before + XAUTOCLAIM-on-start is the safer design.

**Proof sketch for no message loss:**
1. New slot calls `Promote(lastShadowID)` → group cursor is set to `lastShadowID`.
2. New slot calls `XAutoClaimPending` → claims all messages with min-idle-time=0 from the old group.
3. These are the messages the old slot ACKed but whose bar they contributed to may be mid-flight.
4. New slot re-processes them from a clean accumulator state. Dedup set prevents double-counting within the same second window. **QED.**

---

## 3. Reconnect machine — double-NeedsSnapshot preservation

**File:** `aggregator/internal/reconnect/reconnect.go:NeedsSnapshotNow`

**Danger:** If a snapshot fetch fails and `NeedsSnapshotNow("merge_error")` is called while the machine is already in `StateBuffering`, the buffer is preserved — not cleared. This is correct: deltas that arrived during the failed fetch are still valid candidates for the retry merge.

**Invariant:** Buffer is only cleared in `MergeSnapshot` on success (line `m.buffer = m.buffer[:0]`). `NeedsSnapshotNow` never touches the buffer.

**What breaks if you clear the buffer on double-NeedsSnapshot:** The retry snapshot must cover `T+1` through `oldest_buffered_delta`. If you cleared the buffer, you lose all deltas that arrived between fetch-start and fetch-failure, and the new merge will start from a stale book state.

**Proof sketch:**
1. `NeedsSnapshotNow` sets `m.state = StateBuffering` — buffer untouched.
2. `Feed(seq)` appends to buffer whenever `state == StateBuffering` — both before and after the failed first snapshot.
3. On second `MergeSnapshot(newSeq)`: all accumulated deltas (from both waits) are in the buffer.
4. Stale check: `buffer[0].seq > newSeq+1` → triggers third NeedsSnapshot. Buffer preserved again.
5. Eventually a snapshot seq covers all buffered deltas → transition to Live, buffer cleared. **QED.**

---

## 4. Gap cause filtering in metrics.RecordGap

**File:** `aggregator/internal/metrics/metrics.go:RecordGap`

**Danger:** `external_disconnect` and `internal_buffer_overflow` are counted in `aggregator_gap_total` (via `GapTotal.WithLabelValues`) but are **silently dropped** from `lastGapTimes` in `RecordGap`. This means the health gauge does not fire red on every gap — only on persistent, non-transient causes.

**Invariant:** Only `internal_merge_error` and `external_rate_limit` affect health. `external_disconnect` is transient — reconnects are normal. `internal_buffer_overflow` is also filtered out (a full delta buffer is self-healing via the next snapshot fetch).

**What breaks if you remove the filter:** Every WebSocket reconnect (which happens on network blips) sets the health gauge to 0 for a 5-minute window, generating false-positive alerts that train operators to ignore them.

**Proof sketch:**
- `RecordGap("external_disconnect", ...)` → early return, `lastGapTimes` not updated.
- `RecordGap("internal_merge_error", ...)` → `lastGapTimes[key] = nowUnix` updated.
- `GapHealthy(300, now)` → scans `lastGapTimes`; finds no entry for disconnect; finds entry for merge_error → returns 0.
- Health correctly identifies merge errors while ignoring reconnects. **QED.**

---

## 5. Credential sealing in config.Credential

**File:** `aggregator/internal/config/config.go:Credential`

**Danger:** Go's `fmt.Sprintf("%v", cred)` and `slog.Info("...", "key", cred)` would normally print the raw string. The `Credential` type intercepts all formatting verbs via `Format(fmt.State, rune)` and returns `[REDACTED]` for every verb.

**What breaks if you store credentials as `string`:** Any `slog.Error("...", "cred", apiKey)` call — even with `%+v` or `%#v` — would leak the raw key into structured log output that Loki indexes and retains for 30 days.

**Invariant:** `Credential.Value()` is the *only* call site that returns the raw string. Grep for `.Value()` to audit all access points. There are currently three: the KuCoin bullet-token endpoint, the KuCoin WebSocket auth, and the Bybit WebSocket auth.

---

## 6. Blue-green shadow mode cursor management

**File:** `candle-service/internal/consumer/consumer.go:WithShadowMode / Promote`

**Danger:** The shadow consumer and the active XREADGROUP loop share the same `Consumer` struct. After `Promote()` is called, the `shadowMode` atomic is flipped to false — but the `runShadow` loop checks this flag at the *top* of each iteration, not inside the `XRead` call. There is a window where `runShadow` is executing `XRead` while `shadowMode` is already false.

**Invariant:** This is safe because `runShadow` checks `shadowMode.Load()` immediately after `XRead` returns and returns `nil` without processing the messages. The `Run` method then takes over from `XAutoClaimPending` onwards.

**Critical ordering:** `Promote()` must be called from a goroutine that does *not* own `runShadow`. The shadow loop is owned by the consumer goroutine running `Run(ctx)`. The HTTP `/promote` handler calls `Promote()` from its own goroutine — correct.

**What breaks if Promote is called from the consumer goroutine:** Deadlock — `Promote` calls `rdb.XGroupCreateMkStream` (blocking Redis call) while the consumer goroutine is blocked in `rdb.XRead`. Both would be waiting on the same connection pool if the pool is exhausted.

---

## 7. Consumer group gap deduplication using Redis ZSET

**File:** `candle-service/internal/consumer/consumer.go:isGapDup / recordGapDedup`

**Danger:** The gap dedup ZSET is keyed by `(seqBefore, seqAfter, gapCause)` — not by message ID. This means two consecutive candle-service instances (blue → green deploy) both reading the same gap marker from the stream will dedup correctly even though they have different consumer names and group cursors.

**Invariant:** ZSET is trimmed to 10,000 most recent entries (`ZREMRANGEBYRANK 0 -10001`). A gap that occurred more than 10,000 gaps ago could theoretically re-enter — but a gap recurrence is not a correctness problem; it results in an extra `IncrementGap()` call which slightly inflates `gap_count` in the affected bar. Not worth the complexity of a longer retention.

---

## 8. BusManager queue drop — cross-thread asyncio.Queue access

**File:** `bot-service/bot_service/bus/event_bus.py:_drop_oldest`

**Danger:** `_drop_oldest` calls `handle.queue.get_nowait()` from the `bus-manager` OS thread, while the strategy's asyncio event loop may concurrently call `queue.get()` from its own thread. `asyncio.Queue` is not documented as thread-safe.

**Why it works on CPython:** `asyncio.Queue` uses a `collections.deque` internally. `deque.popleft()` (used by `get_nowait`) is atomic on CPython due to the GIL — it either completes fully or not at all. There is no half-removed state. On non-CPython implementations this invariant does not hold.

**Invariant documented:** Comment in code says "CPython is safe to call cross-thread when the item is already in the internal deque." This is an explicit CPython-only assumption. If ported to PyPy or other runtimes, replace with a thread-safe queue.

---

## 9. Health gauge pre-initialization (NFR19)

**File:** `aggregator/internal/metrics/metrics.go:PreInit`

**Danger:** Prometheus only exposes a time series after its first observation. If `aggregator_feed_state{exchange="kucoin",symbol="BTCUSDT"}` is never set because the feed connected on the first try, the dashboard shows "No data" instead of 1 for the first 5 seconds.

**Fix:** `PreInit` is called in `Coordinator.WithMetrics()` *before* `Run()`. It calls `WithLabelValues(...)` for every (exchange, symbol, cause) combination. This forces Prometheus to register the time series at value 0 immediately.

**Ordering invariant:** `WithMetrics` must be called before `Run`. The coordinator builder chain in `main.go` enforces this: `coordinator.New(...).WithMetrics(reg).WithOBPublisher(pub)` — all options applied before `coord.Run(ctx)`.

---

## 10. time.Now() ban in internal packages

**Affected:** All `internal/` packages in both `aggregator/` and `candle-service/`.

**Danger:** `time.Now()` in internal packages makes tests time-dependent. A test that sleeps 1 second to advance a 1-second window is slow and flaky under CI load.

**Rule:** Only `cmd/` entry points (`main.go`) call `time.Now()`. All internal packages receive a `Clock` interface (`Now() time.Time`) and the entry point injects `realClock{}`.

**Enforcement:** The `checkdeps` tool at `aggregator/internal/tools/checkdeps/main.go` statically verifies this. Run it as part of `make lint`.

**Consequence of violating:** `gapdetector.Detect` panics with "Detect called with nil clock" if you pass `nil` — an explicit fail-fast to catch injection failures.
