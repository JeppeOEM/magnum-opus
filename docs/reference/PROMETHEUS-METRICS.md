# Prometheus Metrics Reference

All Prometheus metrics exposed by all services. Format: name, type, labels, semantics,
staleness, and what non-zero / abnormal values mean.

Metrics are exposed at `/metrics` on each service's HTTP port.

---

## Aggregator (`:8080/metrics`)

Metrics registry: `aggregator/internal/metrics/metrics.go`
All metrics are pre-initialized via `PreInit()` before any feed connects (NFR19).

### aggregator_ticks_total

| Field | Value |
|-------|-------|
| Type | Counter |
| Labels | `exchange`, `symbol` |
| Unit | ticks |
| Pre-initialized | Yes (value 0) |

Total number of ticks (trades + L2 updates) received and forwarded to the Redis
stream for each (exchange, symbol) pair.

**Zero value:** Feed never connected or is stuck. Check `aggregator_feed_state`.
**Monotonically increasing:** A rate of 0 for >30s indicates a silent feed failure.

```promql
# Tick rate per symbol
rate(aggregator_ticks_total[1m])

# Symbols with no ticks in last 5 minutes
aggregator_ticks_total unless (increase(aggregator_ticks_total[5m]) > 0)
```

---

### aggregator_gap_total

| Field | Value |
|-------|-------|
| Type | Counter |
| Labels | `exchange`, `symbol`, `cause` |
| Unit | gap events |
| Pre-initialized | Yes (all 4 causes × all symbols = 0) |

Total sequence gap events by cause. The four causes:
- `external_disconnect` — WebSocket connection lost (transient, expected)
- `external_rate_limit` — Exchange throttled the connection
- `internal_merge_error` — Snapshot/delta merge failed (bug in this service)
- `internal_buffer_overflow` — Delta buffer filled before snapshot arrived

**Alerting threshold:** Any `internal_merge_error` or `external_rate_limit` is a
signal to investigate. A sustained rate of `external_disconnect` > 1/minute is
abnormal; occasional disconnects are normal.

```promql
# Gap rate by cause
rate(aggregator_gap_total[5m])

# Internal gaps (potential bugs)
increase(aggregator_gap_total{cause=~"internal_.*"}[1h])
```

---

### aggregator_feed_state

| Field | Value |
|-------|-------|
| Type | Gauge |
| Labels | `exchange`, `symbol` |
| Unit | boolean (0 or 1) |
| Pre-initialized | Yes (value 0) |

Feed connectivity state:
- **1.0** = Connected and live (in `StateLive` from the reconnect machine)
- **0.0** = Disconnected, buffering, or not yet initialized

**Alert:** Any feed at 0 for >60 seconds is a connectivity problem.

```promql
# Feeds that are down
aggregator_feed_state == 0

# Min feed state across all feeds (0 if any feed is down)
min(aggregator_feed_state)
```

---

### aggregator_consumer_lag_ms

| Field | Value |
|-------|-------|
| Type | Gauge |
| Labels | `exchange`, `symbol` |
| Unit | milliseconds |
| Pre-initialized | Yes (value 0) |

Redis Stream consumer group lag for the candle-service consumer. Computed as the
age of the oldest pending message.

**Normal range:** 0–500ms. Occasional spikes to a few seconds are acceptable.
**Alert:** Sustained lag >5000ms indicates candle-service is not keeping up.

**Staleness:** Updated every 5 seconds by the aggregator health goroutine.

```promql
# Feeds with high consumer lag
aggregator_consumer_lag_ms > 5000
```

---

### aggregator_questdb_write_latency_ms

| Field | Value |
|-------|-------|
| Type | Histogram |
| Labels | (none) |
| Unit | milliseconds |
| Buckets | Prometheus default: 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10 |

ILP batch write latency from the aggregator's QuestDB writer. Measures the round-trip
time for each `Flush()` call.

**Normal range:** <10ms on local network, <50ms on WAN.
**Alert:** P99 > 100ms indicates QuestDB performance degradation.

```promql
# P99 write latency
histogram_quantile(0.99, rate(aggregator_questdb_write_latency_ms_bucket[5m]))
```

---

### aggregator_health

| Field | Value |
|-------|-------|
| Type | Gauge |
| Labels | `condition` |
| Unit | boolean (0 or 1) |
| Pre-initialized | Yes (value 1) |

Service health by condition. Current conditions:
- `feeds` — Minimum `feed_state` across all feeds. 0 if any feed is disconnected.
- `gaps` — 0 if a non-transient gap occurred within the last 300 seconds (5 min window).

**Alert:** Either condition at 0 requires investigation.

**Update frequency:** Every 5 seconds by the health goroutine in `cmd/aggregator/main.go`.

**Gap filtering:** Only `internal_merge_error` and `external_rate_limit` affect `gaps` health.
`external_disconnect` and `internal_buffer_overflow` are excluded (transient). See NOMICON §4.

```promql
# Any health condition failing
aggregator_health == 0

# Gap health specifically
aggregator_health{condition="gaps"} == 0
```

---

## Candle-service (`:8081/metrics` or `:8082/metrics`)

Metrics defined in `candle-service/internal/metrics/metrics.go`.

### candle_bars_total

| Field | Value |
|-------|-------|
| Type | Counter |
| Labels | `exchange`, `symbol`, `tf` |

Total bars written to QuestDB per timeframe.

```promql
rate(candle_bars_total{tf="1s"}[1m])
```

---

### candle_gap_total

| Field | Value |
|-------|-------|
| Type | Counter |
| Labels | `exchange`, `symbol` |

Total gap events received from the Redis stream by the candle consumer.

---

### candle_consumer_lag

| Field | Value |
|-------|-------|
| Type | Gauge |
| Labels | `exchange`, `symbol` |
| Unit | messages |

Pending messages in the Redis stream consumer group for this (exchange, symbol).
Computed via XPENDING.

**Alert:** Lag > 1000 messages indicates processing is falling behind.

---

### candle_flush_success_total / candle_flush_failure_total

| Field | Value |
|-------|-------|
| Type | Counter |
| Labels | (none) |

B2 flush success and failure counters. Updated daily by the flusher.

---

### candle_shadow_lag

| Field | Value |
|-------|-------|
| Type | Gauge |
| Labels | `exchange`, `symbol` |
| Unit | messages |

Number of stream entries behind tip for the incoming shadow slot.
Only non-zero during blue-green warmup. Used by the deploy script to decide when to promote.

**Interpretation:** 0 means caught up. Deploy script waits for this to be < 100 before promoting.

---

## Bot-service (`:9090/metrics`)

Metrics defined in `bot-service/bot_service/metrics/prometheus.py`.

### bot_consumer_lag

| Field | Value |
|-------|-------|
| Type | Gauge |
| Labels | `strategy` |
| Unit | events |

Per-strategy asyncio queue depth (events waiting to be processed). Updated on every
successful XREADGROUP poll.

**Alert:** Lag > 500 for >60 seconds indicates strategy processing is too slow.

```promql
bot_consumer_lag > 500
```

---

### bot_queue_drops_total

| Field | Value |
|-------|-------|
| Type | Counter |
| Labels | `strategy` |

Total events dropped due to queue overflow (drop-oldest). Each drop is preceded by
a `GapMarker` injection into the strategy's queue.

**Alert:** Any drops in a 5-minute window indicates the strategy is overloaded.

---

### bot_heartbeat_timeout_total

| Field | Value |
|-------|-------|
| Type | Counter |
| Labels | `strategy` |

Number of times the heartbeat watchdog triggered (event loop unresponsive >10s).
Each increment is immediately followed by SIGTERM.

**Alert:** Any non-zero value requires investigation of blocking strategy code.

---

### bot_nan_guard_total

| Field | Value |
|-------|-------|
| Type | Counter |
| Labels | `strategy`, `symbol` |

Number of times the NaN guard suppressed a handler invocation. Normal during
indicator warmup at startup. Persistent high rate = indicator always producing NaN.

---

### bot_coldstart_gap_fraction

| Field | Value |
|-------|-------|
| Type | Gauge |
| Labels | `strategy`, `symbol`, `tf` |

Gap fraction in the initial history loaded from QuestDB (`has_gap=true / total rows`).
Emitted when > 5%.

**Alert:** Fraction > 0.10 means significant historical data quality issues.

---

### bot_order_fills_total

| Field | Value |
|-------|-------|
| Type | Counter |
| Labels | `strategy`, `exchange`, `symbol`, `side` |

Total filled orders.

---

### bot_pnl_usd

| Field | Value |
|-------|-------|
| Type | Gauge |
| Labels | `strategy`, `symbol` |
| Unit | USD |

Current unrealized PnL per position. Updated on every fill event.

---

## Common patterns

### Grafana alerting

```yaml
# Alert: any feed down > 60s
- alert: FeedDown
  expr: aggregator_feed_state == 0
  for: 1m
  annotations:
    summary: "Feed {{ $labels.exchange }}/{{ $labels.symbol }} is disconnected"

# Alert: internal gaps in last hour
- alert: InternalGap
  expr: increase(aggregator_gap_total{cause=~"internal_.*"}[1h]) > 0
  annotations:
    summary: "Internal gap detected — possible bug"

# Alert: strategy event loop frozen
- alert: HeartbeatTimeout
  expr: increase(bot_heartbeat_timeout_total[5m]) > 0
  annotations:
    summary: "Strategy {{ $labels.strategy }} event loop frozen — SIGTERM sent"
```
