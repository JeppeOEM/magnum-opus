# LOCKING — Synchronization Primitives and What They Protect

Every lock, mutex, atomic, and channel in the codebase, with: what it protects,
what context (goroutine/thread) holds it, and how long it is held.

Inspired by the Linux kernel's `Documentation/locking/` documentation style.

---

## Go services

### aggregator/internal/metrics/metrics.go — `lastGapMu` (sync.Mutex)

| Attribute | Value |
|-----------|-------|
| Type | `sync.Mutex` |
| Field | `Registry.lastGapMu` |
| Protects | `Registry.lastGapTimes` map (exchange+symbol → last gap Unix timestamp) |
| Acquired by | `RecordGap` (writer), `GapHealthy` (reader) |
| Held duration | < 1 µs (map read/write only) |
| Lock ordering | Leaf lock — holds nothing else while acquired |

**Context:** Multiple goroutines call `RecordGap` (from Worker goroutines) and `GapHealthy`
(from the health-update goroutine in `main.go`). The mutex is the only protection for
`lastGapTimes`.

**Deadlock risk:** None. Single lock, no other lock held when acquired, very short hold time.

---

### aggregator/internal/coordinator — `shutdownOnce` (sync.Once)

| Attribute | Value |
|-----------|-------|
| Type | `sync.Once` |
| Field | `Coordinator.shutdownOnce` |
| Protects | `Shutdown()` idempotency — ensures WaitGroup.Wait + ILP Close run exactly once |
| Acquired by | First caller of `Coordinator.Shutdown()` |

**Context:** `Shutdown` may be called from signal handler or startup gate error path.
`sync.Once` guarantees safe multi-caller behavior.

---

### candle-service/internal/consumer — `shadowMu` (sync.Mutex)

| Attribute | Value |
|-----------|-------|
| Type | `sync.Mutex` |
| Field | `Consumer.shadowMu` |
| Protects | `lastShadowID` (last processed message ID) and `xreadCursor` (XREAD start position) |
| Acquired by | Shadow loop goroutine (writer), `Promote()` HTTP handler (read+write), `LastShadowID()` (reader), `ShadowLag()` (reader) |
| Held duration | < 1 µs (string copy only) |
| Lock ordering | Leaf lock |

**Context:** The shadow loop goroutine and the HTTP handler goroutine both access
`lastShadowID`. `shadowMu` provides the synchronization point.

**Important:** The `shadowMode` atomic is NOT protected by `shadowMu`. It is accessed
via `sync/atomic.Bool` directly. `shadowMu` only protects the cursor strings.

---

### candle-service/internal/consumer — `shadowMode` (atomic.Bool)

| Attribute | Value |
|-----------|-------|
| Type | `sync/atomic.Bool` |
| Field | `Consumer.shadowMode` |
| Protects | Read-without-lock flag for shadow loop exit |
| Writers | `Promote()` HTTP handler (sets `false`), `WithShadowMode()` constructor (sets `true`) |
| Readers | `runShadow` loop (checks at top of each iteration), `Run` |

**Memory ordering:** `atomic.Bool.Store` has release semantics on most architectures.
`Load` in `runShadow` will observe the store from `Promote()` without a mutex.

---

## Python bot-service

### bus/event_bus.py — `_handles_lock` (threading.RLock)

| Attribute | Value |
|-----------|-------|
| Type | `threading.RLock` (reentrant) |
| Field | `BusManager._handles_lock` |
| Protects | `BusManager._handles` dict (strategy name → StrategyHandle) |
| Acquired by | `_run` (initial read), `_consume_loop` (lag snapshot read), `_route` (delivery read), `dynamic_register`/`dynamic_deregister` (write) |
| Held duration | < 100 µs (dict copy or single item operation) |

**Why RLock:** `_run` (initial handles setup) and `_consume_loop` (lag reporting) both
acquire `_handles_lock`. If the bus manager internally calls something that tries to
acquire `_handles_lock` again, deadlock would occur with a plain `Lock`. RLock
prevents this.

**Context:** Bus-manager thread holds this lock during delivery iteration.
Dynamic register/deregister from strategy registry thread are brief writes.

---

### bus/event_bus.py — `_pubsub_lock` (threading.RLock)

| Attribute | Value |
|-----------|-------|
| Type | `threading.RLock` |
| Field | `BusManager._pubsub_lock` |
| Protects | `_pubsub_ob_callbacks` and `_pubsub_c1s_callbacks` dicts |
| Acquired by | `provision_pubsub` (write), `deprovision_pubsub` (write), `_dispatch_pubsub` (read) |
| Held duration | < 10 µs |

**Context:** `bus-pubsub` thread reads callbacks, strategy registry thread writes them.

---

### strategy/base.py — `_emergency_close_lock` (threading.Lock)

| Attribute | Value |
|-----------|-------|
| Type | `threading.Lock` |
| Field | `BaseStrategy._emergency_close_lock` |
| Protects | `_emergency_close_in_flight` set |
| Acquired by | `_on_bus_timeout` (check + add), `_emergency_close_symbol` (finally: remove) |
| Held duration | < 1 µs (set add/discard) |

**Context:** Bus-timeout thread calls `_on_bus_timeout`. Multiple symbols can trigger
simultaneously. `_emergency_close_lock` prevents duplicate emergency-close threads per symbol.

**Lock ordering:** Leaf lock. Never held while acquiring another lock.

---

### strategy/base.py — `_heartbeat_ack` (threading.Event)

| Attribute | Value |
|-----------|-------|
| Type | `threading.Event` |
| Field | `BaseStrategy._heartbeat_ack` |
| Protects | Heartbeat liveness signal |
| Set by | Strategy event loop (`ack_heartbeat()`) |
| Waited on by | Heartbeat OS thread (`_heartbeat_loop`) |

**Semantics:** Event is cleared every 5 seconds by the heartbeat thread, then waited for
up to 10 seconds. If the event loop ACKs within 10 seconds, clear is safe. If not → SIGTERM.

---

## Channels (Go)

### aggregator/internal/coordinator — per-symbol `deltas chan exchange.Tick`

| Attribute | Value |
|-----------|-------|
| Type | `chan exchange.Tick` (buffered, capacity `deltaChanCap = 256`) |
| Sender | Coordinator fanout goroutine (one per exchange) |
| Receiver | Per-symbol Worker goroutine (one per symbol) |

**Non-blocking send:** The fanout goroutine uses a `select { case ch <- tick: default: drop }`.
A full channel causes tick loss + WARN log. The resulting seq gap triggers re-snapshot.

**Channel lifetime:** Created in `coordinator.New()`, closed implicitly when all senders stop
(context cancellation).

---

### aggregator/internal/coordinator — `snapReqs chan SnapshotRequest`

| Attribute | Value |
|-----------|-------|
| Type | `chan SnapshotRequest` (buffered, capacity `totalSymbols + 16`) |
| Senders | Per-symbol Worker goroutines |
| Receiver | Snapshot dispatcher goroutine |

**Capacity design:** `+16` headroom prevents Workers from blocking while the dispatcher
is fetching a snapshot for another symbol (snapshot fetch can take up to 5 seconds on slow exchanges).

---

### gateway/internal/hub — per-client `send chan message`

| Attribute | Value |
|-----------|-------|
| Type | `chan message` (buffered, capacity `clientSendBuf = 16`) |
| Sender | Hub's `Broadcast`/`BroadcastSymbol` methods (from pub/sub reader goroutine) |
| Receiver | `Client.WritePump` goroutine |

**Non-blocking send:** `Client.Send()` uses `select { case send <- msg: default: drop }`.
A full buffer means the client is too slow; the message is silently dropped (WARN logged).
This prevents a slow client from blocking the Hub's pub/sub delivery to all other clients.
