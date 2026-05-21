# FMEA — Failure Mode and Effects Analysis

Every significant component × its failure modes × blast radius × detection × mitigation.

Format per row: **Failure mode** | **Blast radius** (what breaks downstream) |
**Detection** (how do you know?) | **Mitigation** (what prevents or limits damage?) |
**Recovery** (how to restore normal state?)

Severity scale: **P0** = position at risk, **P1** = data loss, **P2** = data gap, **P3** = latency/monitoring only.

---

## Aggregator

### Exchange WebSocket — external_disconnect

| Attribute | Detail |
|-----------|--------|
| **Failure** | WebSocket connection dropped (network blip, exchange restart) |
| **Blast radius** | All ticks for affected (exchange, symbol) pairs stop. `aggregator_feed_state` → 0. Candle bars have `gap_count > 0` for affected pairs during reconnect window (typically 1–5s). |
| **Severity** | P2 |
| **Detection** | `aggregator_feed_state == 0` alert. `coordinator: gap detected` log. |
| **Mitigation** | Reconnect backoff in `reconnect.Machine`. Snapshot/delta merge resumes cleanly. |
| **Recovery** | Automatic. No human intervention needed for transient disconnects. |

### Exchange WebSocket — external_rate_limit

| Attribute | Detail |
|-----------|--------|
| **Failure** | Exchange throttles connection (HTTP 429 or WS error code 10006/TooManyRequests) |
| **Blast radius** | Same as disconnect, but indicates the aggregator is oversubscribed or the exchange is under load. Health gauge fires. |
| **Severity** | P2 |
| **Detection** | `aggregator_gap_total{cause="external_rate_limit"}` counter. `aggregator_health{condition="gaps"} == 0`. |
| **Mitigation** | Reconnect backoff adds delay before re-subscribe. Reduces request rate. |
| **Recovery** | Automatic. If persistent, reduce symbol count or increase reconnect backoff. |

### Aggregator — internal_merge_error

| Attribute | Detail |
|-----------|--------|
| **Failure** | REST snapshot is stale (its seq is older than oldest buffered delta). Book state cannot be reconstructed. |
| **Blast radius** | One retry snapshot fetch is triggered immediately. If retry also fails → repeated gap events for affected pair. Candle bars have elevated `gap_count`. |
| **Severity** | P1 (book integrity compromised during gap) |
| **Detection** | `aggregator_gap_total{cause="internal_merge_error"}` counter. Health gauge fires. |
| **Mitigation** | Machine preserves buffer across retries (NOMICON §3). Retry uses fresh snapshot. |
| **Recovery** | Automatic. If sustained → investigate snapshot fetch latency; exchange may have large books causing slow REST responses. |

### Aggregator — internal_buffer_overflow

| Attribute | Detail |
|-----------|--------|
| **Failure** | Delta channel (capacity 256) fills before a snapshot arrives during cold start |
| **Blast radius** | Gap event emitted. Snapshot re-requested. Deltas since buffer-fill are lost. |
| **Severity** | P2 |
| **Detection** | `aggregator_gap_total{cause="internal_buffer_overflow"}` counter. |
| **Mitigation** | Buffer capacity of 256 absorbs bursts. Self-healing: next snapshot covers the gap. |
| **Recovery** | Automatic. If persistent, increase `deltaChanCap` constant in coordinator.go. |

### QuestDB ILP writer failure

| Attribute | Detail |
|-----------|--------|
| **Failure** | ILP send returns error (network, QuestDB OOM, WAL suspended) |
| **Blast radius** | Aggregator: ticks still written to Redis (candle-service unaffected). QuestDB `ticks` table may have gaps. Historical analysis affected; live trading unaffected. |
| **Severity** | P2 |
| **Detection** | `questdb: ILP write failed` WARN log. `aggregator_questdb_write_latency_ms` P99 spike. |
| **Mitigation** | Fire-and-forget design. Writer retries internally. WAL auto-resume. |
| **Recovery** | Automatic. If QuestDB has WAL suspension: wait for auto-resume or run `RESUME WAL` manually. |

### Redis XADD failure (aggregator)

| Attribute | Detail |
|-----------|--------|
| **Failure** | Redis is unavailable; XADD fails |
| **Blast radius** | **P0** — Candle-service and bot-service receive no ticks. All bar production stops. No ticks = no signals = no position management. |
| **Severity** | P0 |
| **Detection** | `coordinator: stream.Write failed` WARN log (high rate). `aggregator_ticks_total` rate → 0. |
| **Mitigation** | Redis streams are durable (AOF/RDB). Reconnect with exponential backoff. |
| **Recovery** | Restore Redis. Ticks lost during outage cannot be recovered (exchange doesn't buffer them). Expect `gap_count > 0` in bars after Redis recovery. |

---

## Candle-service

### Consumer: XREADGROUP failure

| Attribute | Detail |
|-----------|--------|
| **Failure** | Redis unavailable or consumer group deleted |
| **Blast radius** | All bar production stops for all pairs on this slot. |
| **Severity** | P0 |
| **Detection** | Candle-service process exits with error. Health endpoint returns 503. |
| **Mitigation** | Exponential backoff on XREADGROUP errors. Consumer group is idempotent (recreated if missing). |
| **Recovery** | Restore Redis. Restart candle-service. XAUTOCLAIM recovers pending messages. |

### Consumer: accumulator flush failure (QuestDB)

| Attribute | Detail |
|-----------|--------|
| **Failure** | Bar data fails to write to QuestDB at second boundary |
| **Blast radius** | One second's bar data lost. Bot-service receives the bar via Redis stream (still delivered). QuestDB has a gap. |
| **Severity** | P1 (QuestDB data) / P3 (bot-service unaffected) |
| **Detection** | `accumulator flush failed` ERROR log. |
| **Mitigation** | Redis stream bar delivery is independent of QuestDB write. Candle-service continues running. |
| **Recovery** | QuestDB gap is permanent (no replay path from Redis after stream TTL). |

### Blue-green: promote with high shadow lag

| Attribute | Detail |
|-----------|--------|
| **Failure** | Incoming slot promoted before catching up with stream (lag > 100 messages) |
| **Blast radius** | Bars from the new slot have stale book state. `gap_count=0` but OFI, spread, and depth features are incorrect. Silent corruption. |
| **Severity** | P1 |
| **Detection** | Deploy script checks lag before promotion. If bypassed manually: `candle_shadow_lag` gauge. |
| **Mitigation** | Deploy script enforces lag < 100 check. Cannot be promoted programmatically above threshold. |
| **Recovery** | Roll back: promote old slot again (`POST :8081/promote` or `POST :8082/promote`). |

### Daily B2 flush failure

| Attribute | Detail |
|-----------|--------|
| **Failure** | Flusher fails to export previous day's data to B2 (network, B2 outage, credentials expired) |
| **Blast radius** | Historical Parquet data for that day is unavailable until manual backfill. QuestDB still has the data (TTL = 30 days). |
| **Severity** | P2 |
| **Detection** | `daily flush failed` ERROR log. `candle_flush_failure_total` counter. Alert published to `alerts:flush_failure` Redis stream. |
| **Mitigation** | Catch-up runs on next startup to re-flush missed days. `FLUSH_DATE_OVERRIDE` for manual backfill. |
| **Recovery** | Run candle-service with `FLUSH_DATE_OVERRIDE=YYYY-MM-DD` for each missed day. |

---

## Bot-service

### Redis bus failure (BusManager)

| Attribute | Detail |
|-----------|--------|
| **Failure** | Redis XREADGROUP fails persistently (3× `_BACKOFF_CAP` consecutive failures) |
| **Blast radius** | **P0** — All strategies receive no events. Heartbeat timeouts trigger SIGTERM if event loops freeze. Positions remain open without management. |
| **Severity** | P0 |
| **Detection** | `bus_manager_persistent_failure` CRITICAL log. Process exits via SIGTERM. |
| **Mitigation** | Exponential backoff before SIGTERM. Heartbeat watchdog separately monitors event loop health. |
| **Recovery** | Restore Redis. Restart bot-service. Reconciliation on startup will find any positions opened before the outage. |

### Strategy event loop freeze (heartbeat timeout)

| Attribute | Detail |
|-----------|--------|
| **Failure** | Strategy's asyncio event loop blocked (slow indicator, blocking I/O, CPU-bound computation) |
| **Blast radius** | **P0** — This strategy's positions unmanaged. Other strategies unaffected (separate threads). |
| **Severity** | P0 |
| **Detection** | `heartbeat_timeout` CRITICAL log. `bot_heartbeat_timeout_total` counter increment. Process exits via SIGTERM. |
| **Mitigation** | `add_indicators` exceptions are caught and logged (strategy continues). Heartbeat gives 10s grace window. |
| **Recovery** | Restart bot-service. If freeze is systematic: profile strategy handler for blocking calls. |

### Strategy queue overflow

| Attribute | Detail |
|-----------|--------|
| **Failure** | Strategy processing too slow; asyncio queue fills to `BOT_QUEUE_MAX_DEPTH` |
| **Blast radius** | Oldest events dropped. GapMarker injected. Strategy signal invalidated until `min_lookback` clean bars. |
| **Severity** | P1 (missed bars) |
| **Detection** | `queue_drop_oldest` WARN log. `bot_queue_drops_total` counter. `bot_consumer_lag` gauge. |
| **Mitigation** | Drop-oldest semantics: newest events always processed. GapMarker prevents stale signals from acting. |
| **Recovery** | Automatic: signal recovers after `min_lookback` clean bars. If chronic: reduce strategy computation time or increase queue depth. |

### Emergency close failure

| Attribute | Detail |
|-----------|--------|
| **Failure** | Market sell order fails during bus timeout (exchange unavailable, account suspended, etc.) |
| **Blast radius** | **P0** — Position remains open; strategy cannot manage it. |
| **Severity** | P0 |
| **Detection** | `emergency_close_failed` CRITICAL log (emitted on every retry). |
| **Mitigation** | Retry loop retries indefinitely (5s between attempts). Daemon thread doesn't block shutdown. |
| **Recovery** | Manual: close position via exchange UI. Fix underlying issue (network, account status). |

---

## Infrastructure

### QuestDB WAL suspension

| Attribute | Detail |
|-----------|--------|
| **Failure** | QuestDB runs out of memory or disk during a write storm, suspending the WAL |
| **Blast radius** | All ILP writes fail silently (fire-and-forget). Bar data is lost for the suspension duration. |
| **Severity** | P1 |
| **Detection** | `questdb: WAL suspended — issuing RESUME WAL` WARN log. ILP write error rate spike. |
| **Mitigation** | Auto-resume via `RESUME WAL` SQL command. Writer detects suspension via HTTP health check. |
| **Recovery** | Automatic for transient events. Persistent: add QuestDB memory/disk. See MR-08. |

### Redis OOM (maxmemory eviction)

| Attribute | Detail |
|-----------|--------|
| **Failure** | Redis evicts stream entries due to maxmemory limit |
| **Blast radius** | Tick stream data lost. Candle-service may see XREADGROUP errors or miss messages. Gap events emitted. |
| **Severity** | P1 |
| **Detection** | `XREADGROUP` returns `ERR` or stream length drops unexpectedly. |
| **Mitigation** | `REDIS_STREAM_MAXLEN ~ 50000` limits stream growth. Redis should be configured `maxmemory-policy=noeviction` to prevent silent data loss. |
| **Recovery** | Ticks lost during OOM are unrecoverable. Check Redis `INFO memory`. Increase `maxmemory` if needed. |
