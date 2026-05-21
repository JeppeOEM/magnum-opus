# DANGEROUS — Functions Requiring Special Understanding

Every function or pattern in this codebase that:
- Has a surprising contract (the name implies something different from what it does)
- Has a hidden precondition that is not enforced by the type system
- Has a side effect that outlives the call
- Will silently corrupt state if misused

This file supplements NOMICON.md. NOMICON covers system-level invariants;
DANGEROUS covers specific call sites.

---

## `Consumer.Promote(lastShadowID string)` [candle-service]

**File:** `candle-service/internal/consumer/consumer.go`
**Danger level:** HIGH

**What it does:** Transitions the consumer from shadow XREAD mode to active XREADGROUP mode.
Creates the consumer group with cursor set to `lastShadowID` (or `$` if empty).

**Hidden precondition:** Must be called from a goroutine that does NOT own the consumer loop.
If called from the consumer goroutine itself, the Redis `XGroupCreateMkStream` call will
block on the same connection pool as the running XREAD, potentially causing a deadlock or
timeout under connection pool exhaustion.

**Correct caller:** HTTP `/promote` handler goroutine.
**Wrong caller:** Any goroutine running `Consumer.Run()`.

**Side effect:** After `Promote()` returns, the shadow XREAD loop exits at its next iteration.
The `Run()` method then calls `XAutoClaimPending()` synchronously before entering XREADGROUP.
During this window, no new messages are processed.

---

## `Coordinator.WithMetrics(reg *metrics.Registry)` [aggregator]

**File:** `aggregator/internal/coordinator/coordinator.go`
**Danger level:** MEDIUM

**What it does:** Calls `reg.PreInit(pairs)` which sets all Prometheus label series to 0.

**Hidden precondition:** Must be called BEFORE `Coordinator.Run(ctx)`. If called after Run,
the PreInit call races with Worker goroutines that are already updating the same metrics.

**Why not enforced:** The coordinator builder pattern (`New().WithMetrics().Run()`) is the
intended usage, but Go cannot enforce call ordering at compile time.

**Consequence of wrong ordering:** Data race on Prometheus internal registry state.
Go race detector will flag this; production may silently corrupt counters.

---

## `metrics.Registry.RecordGap(exchange, symbol, cause string, nowUnix int64)` [aggregator]

**File:** `aggregator/internal/metrics/metrics.go`
**Danger level:** MEDIUM

**What it does:** Updates `lastGapTimes` for health tracking, BUT only for non-transient causes.
`external_disconnect` and `internal_buffer_overflow` are silently dropped.

**Surprising behavior:** This function does NOT increment `GapTotal` counter.
`GapTotal` must be incremented separately with `GapTotal.WithLabelValues(...).Inc()`.
`RecordGap` only updates the health window.

**Pattern:** Always called as a pair:
```go
reg.GapTotal.WithLabelValues(exchange, symbol, string(cause)).Inc()  // count the gap
reg.RecordGap(exchange, symbol, string(cause), clk.Now().Unix())     // update health
```

---

## `reconnect.Machine.MergeSnapshot(snapshotSeq uint64)` [aggregator]

**File:** `aggregator/internal/reconnect/reconnect.go`
**Danger level:** HIGH

**What it does:** Attempts to merge a snapshot with buffered deltas.

**Two return modes:**
1. `(seqs, nil)` — merge succeeded; seqs are delta seq numbers to replay; machine is now Live
2. `(nil, *NeedsSnapshot)` — merge failed (stale snapshot); machine stays in Buffering

**Critical contract:** When called in `StateLive`, this function is a no-op and returns
`(nil, nil)` — NOT an error. The caller must check `machine.State()` before calling.

**MaxUint64 edge case:** If `snapshotSeq == math.MaxUint64`, the stale-snapshot check
`m.buffer[0].seq > snapshotSeq+1` would wrap to 0, falsely claiming every buffered delta
is stale. The code skips the check at MaxUint64. Do not send a snapshot with seq=MaxUint64.

---

## `BusManager._drop_oldest(handle)` [bot-service]

**File:** `bot-service/bot_service/bus/event_bus.py`
**Danger level:** MEDIUM (CPython-only safety)

**What it does:** Calls `handle.queue.get_nowait()` from the bus-manager OS thread, while
the strategy's asyncio event loop may be waiting on `queue.get()` in another thread.

**Surprising contract:** `asyncio.Queue` is documented as NOT thread-safe. This call is
safe only on CPython due to the GIL providing atomic `deque.popleft()` semantics.

**If ported to PyPy or GraalPy:** Replace with a thread-safe queue implementation.
Do not assume this pattern is universally portable.

---

## `BaseStrategy._emergency_close_symbol(symbol, qty)` [bot-service]

**File:** `bot-service/bot_service/strategy/base.py`
**Danger level:** HIGH

**What it does:** Loops indefinitely, retrying a market-sell order until success.

**Dangerous behaviors:**
1. **Never gives up.** If the exchange is permanently down, this loop runs until process exit.
   The daemon thread flag means it won't block shutdown — but it means the position is
   NEVER closed programmatically if the exchange is unreachable long-term.

2. **Uses `asyncio.run()`** in a non-async context (daemon thread). This creates a new
   event loop each call. Do not call `asyncio.run()` elsewhere in the same thread.

3. **Skips paper trading** without closing. If `paper_trading=True`, the function returns
   immediately with only a WARN log. The emergency "close" never happens.

4. **`_emergency_close_in_flight` guard.** The lock prevents concurrent emergency-close
   threads for the same symbol. However, if the first thread is stuck in the retry loop
   and the bus timeout fires again, a second thread is NOT started (the in-flight check
   prevents it). The first thread is the only one managing the close.

---

## `OrderBook.Apply(d Delta)` with `Size == ""` [aggregator]

**File:** `aggregator/internal/orderbook/orderbook.go`
**Danger level:** LOW (well-documented but surprising)

**What it does:** If `Size == ""` (empty string, not `"0"`), the price level is deleted.

**Surprising:** The explicit case for deletion is `Size == "0"`. An empty size string is
treated as "also delete" rather than "ignore". This matches exchange behavior where some
exchanges send empty strings for deletions.

**Correct producer contract:** Always send `"0"` for explicit removals; never send empty
strings unless the exchange specifically uses them for removals.

---

## `Consumer.XAutoClaimPending(ctx context.Context)` [candle-service]

**File:** `candle-service/internal/consumer/consumer.go`
**Danger level:** MEDIUM

**What it does:** Claims ALL pending messages from the consumer group, regardless of their
min-idle-time. Uses `min-idle=0`.

**Surprising:** `XAUTOCLAIM` with `min-idle=0` claims messages that were just delivered
to the old consumer but not yet ACKed. If the old consumer is still running (race during
blue-green promotion), both consumers may process the same message.

**Mitigation:** The old slot is stopped immediately after promotion (Step 7 of deploy.md).
The window for race is the time between `POST /promote` and `docker compose stop`. During
this window, duplicate processing may occur — the ZSET gap dedup and the in-window dedup
set both guard against the most harmful consequences.

---

## `flusher.flushDate` — AbortMultipartUpload on cancelled context [candle-service]

**File:** `candle-service/internal/flusher/flusher.go`
**Danger level:** MEDIUM

**What it does:** On B2 multipart upload failure, calls `AbortMultipartUpload` to clean up
partial state.

**Critical pattern:** Uses `context.Background()` (not the caller's `ctx`) for the abort call.

**Why dangerous:** The caller's `ctx` is likely cancelled (that's why the upload failed).
Using the cancelled context for `AbortMultipartUpload` would return immediately with a
context error, leaving the multipart upload in an incomplete state on B2 (incurring storage costs
and potentially causing confusion on retry).

**Correct pattern:** Always use a fresh context for cleanup operations that must complete
even when the parent context is cancelled.
