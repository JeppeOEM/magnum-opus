# Log Message Reference

Every `Warn`, `Error`, and `Critical` log message produced by all services.
`Info` messages are excluded — this reference focuses on actionable signals.

Log format: structured JSON (Go services) or structlog (Python bot-service).

---

## How to read log output

**Go services (aggregator, candle-service, gateway):**
```json
{"time":"2026-05-21T03:00:00Z","level":"WARN","msg":"coordinator: tick dropped — symbol channel full","exchange":"kucoin","symbol":"BTC-USDT","seq":12345678}
```

**Python bot-service:**
```json
{"event":"bus_timeout","strategy":"my_strategy","open_positions":{"BTCUSDT":0.01},"elapsed_seconds":62.3,"level":"warning","timestamp":"2026-05-21T03:00:00Z"}
```

---

## Aggregator

### Startup

| Level | Message | When | Action |
|-------|---------|------|--------|
| ERROR | `aggregator: config error: ...` | Missing required env vars at startup | Set the missing variables listed in the message |
| ERROR | `aggregator: kucoin connect failed` | KuCoin WebSocket connection failed at startup | Check network, API credentials, `KUCOIN_API_KEY/SECRET/PASSPHRASE` |
| ERROR | `aggregator: bybit connect failed` | Bybit WebSocket connection failed at startup | Check network, `BYBIT_API_KEY/SECRET` |
| ERROR | `aggregator: kucoin subscribe failed` | Failed to subscribe to KuCoin symbols | Check symbol format in `config.yaml` |
| ERROR | `aggregator: bybit subscribe failed` | Failed to subscribe to Bybit symbols | Check symbol format in `config.yaml` |
| ERROR | `aggregator: startup gate failed` | All feeds did not connect within `STARTUP_TIMEOUT_SEC` | Increase timeout or investigate exchange connectivity; unconfirmed symbols listed |
| ERROR | `aggregator: failed to connect to QuestDB ILP` | QuestDB not reachable at startup | Check `QUESTDB_ILP_ADDR`, verify QuestDB is running |
| ERROR | `aggregator: no exchanges configured` | Neither KuCoin nor Bybit symbols are configured | Set symbols in `config.yaml` |

### Coordinator

| Level | Message | When | Action |
|-------|---------|------|--------|
| WARN | `coordinator: tick dropped — symbol channel full` | The per-symbol delta channel (capacity 256) is full; tick was silently dropped | This will trigger a seq gap and snapshot re-fetch. If frequent, investigate slow Workers |
| WARN | `coordinator: gap detected` | A sequence gap was detected for a symbol | Normal on reconnects. Persistent occurrences → check `aggregator_gap_total` metrics |
| WARN | `coordinator: snapshot fetch failed, retrying` | REST snapshot request returned an error | Will retry with backoff. Check exchange REST API availability |
| WARN | `coordinator: stale snapshot, requesting new` | Snapshot seq is older than buffered deltas (merge window missed) | See NOMICON §3. Will retry snapshot fetch automatically |
| WARN | `coordinator: stale snapshot re-request failed` | Second snapshot attempt also failed | Exchange REST API issue; check rate limits |
| WARN | `coordinator: stream.Write failed` | Redis XADD failed for a tick | Check Redis connectivity; `REDIS_ADDR` |
| WARN | `coordinator: ilp.Write failed` | QuestDB ILP write failed | Non-fatal (fire-and-forget). Check `QUESTDB_ILP_ADDR` |
| WARN | `coordinator: WriteGap (stream) failed` | Failed to write gap marker to Redis stream | Candle-service may miss this gap event |
| WARN | `coordinator: WriteGap (ilp) failed` | Failed to write gap event to QuestDB | Monitoring data incomplete |
| WARN | `coordinator: WriteSnapshot failed — candle service stays in cold-start` | Failed to write snapshot to Redis stream | Candle-service book will be stale until next snapshot |
| WARN | `coordinator: ob pubsub publish failed` | Order book Redis pub/sub publish failed | Gateway and dashboard won't receive this OB update |
| WARN | `coordinator: unexpected snapshot result discarded` | Snapshot arrived for a symbol in Live state (race) | Harmless; the book is already live |
| ERROR | `coordinator: panic in symbol goroutine` | A Worker goroutine panicked | Service will attempt to recover; check stack trace in following log lines |
| ERROR | `coordinator: ILP writer close failed during shutdown` | QuestDB ILP connection failed to close cleanly | Non-critical on shutdown path |

### Aggregator — QuestDB writer

| Level | Message | When | Action |
|-------|---------|------|--------|
| WARN | `questdb: WAL check failed` | HTTP call to QuestDB WAL status endpoint failed | Check `QUESTDB_HTTP_ADDR`; may be transient |
| WARN | `questdb: WAL probe failed` | WAL health probe request failed | Transient; writer will retry |
| WARN | `questdb: WAL suspended — issuing RESUME WAL` | QuestDB WAL is suspended (usually after OOM) | Normal recovery action; if frequent → check QuestDB memory |
| WARN | `questdb: RESUME WAL failed` | RESUME WAL SQL command failed | Manual intervention: `RESUME WAL` in QuestDB console |
| WARN | `questdb: WAL drain write failed` | Failed to write during WAL drain phase | Transient; will retry |
| WARN | `questdb: ILP write failed` | ILP send failed | Retried; if persistent, check QuestDB health |
| WARN | `questdb: ILP write failed, retrying` | Retry attempt for ILP write | Normal retry |
| WARN | `questdb: flush retry exhausted` | ILP write retries all failed | Data loss possible for this batch; check QuestDB |
| WARN | `questdb: flush on close failed` | Final flush at shutdown failed | Some rows may not be persisted |
| WARN | `questdb: ILP write failed during drain` | Write failed while draining buffered rows | Check QuestDB health |

### Aggregator — KuCoin exchange

| Level | Message | When | Action |
|-------|---------|------|--------|
| WARN | `kucoin: pong timeout` | WebSocket heartbeat missed | Reconnect will follow automatically |
| WARN | `kucoin: reconnect dial` | WebSocket reconnect attempt | Normal recovery; excessive = network instability |
| WARN | `kucoin: reconnect token fetch` | Failed to fetch new connection token during reconnect | Check KuCoin API credentials |
| WARN | `kucoin: token renewal failed` | Periodic token refresh failed | Will retry; if exhausted → reconnect triggered |
| WARN | `kucoin: token renewal exhausted retries, triggering reconnect` | All token renewal retries failed | Full WebSocket reconnect will follow |
| WARN | `kucoin: re-subscribe after reconnect` | Reconnect succeeded; re-subscribing | Normal |
| WARN | `kucoin: symbols not confirmed within timeout, retrying` | Subscribe ACK not received in time | Exchange may be slow; will retry |
| WARN | `kucoin: ticks channel full, dropping` | KuCoin tick fan-in channel is full | Seq gap will follow; check coordinator backpressure |
| WARN | `kucoin: signals channel full` | Signal channel full; a lifecycle signal was dropped | Harmless; gap detection handles reconnect flow |
| WARN | `kucoin: parse l2 update` | Failed to parse an L2 delta message | Malformed exchange message; logged with raw bytes |
| WARN | `kucoin: parse trade` | Failed to parse a trade message | Malformed exchange message |
| WARN | `kucoin: spurious ack for unknown subscription ID` | ACK received for a subscription this client didn't make | Indicates subscription state mismatch |
| WARN | `kucoin: server error message` | Exchange sent an error-type system message | Check error code and message in log fields |
| WARN | `kucoin: confirm retry failed` | Subscription confirmation retry failed | Will escalate to full reconnect |

### Aggregator — Bybit exchange

| Level | Message | When | Action |
|-------|---------|------|--------|
| WARN | `bybit mux: pong timeout, closing connection` | Bybit WebSocket heartbeat missed | Reconnect will follow |
| WARN | `bybit mux: reconnect factory failed` | WebSocket reconnect factory error | Network or endpoint issue |
| WARN | `bybit mux: re-subscribe after reconnect` | Reconnecting and re-subscribing | Normal recovery |
| WARN | `bybit mux: symbols unconfirmed after timeout, retrying` | Subscribe ACK not received | Bybit may be slow; will retry |
| WARN | `bybit mux: subscribe nack` | Bybit returned error response to subscribe request | Check symbol format; may be delisted |
| WARN | `bybit mux: spurious ack for unknown req_id` | ACK for unknown request ID | Indicates request ID state mismatch |
| WARN | `bybit mux: ticks channel full, dropping tick` | Bybit tick channel capacity exceeded | Seq gap will follow |
| WARN | `bybit mux: signals channel full, dropping` | Signal channel full | Harmless; gap detection handles reconnect |
| WARN | `bybit: parse l2 update` | Failed to parse Bybit L2 delta | Malformed message; logged with raw bytes |
| WARN | `bybit: parse trade` | Failed to parse Bybit trade | Malformed message |
| WARN | `bybit mux: confirm retry failed` | Subscription confirmation retry failed | Will escalate to reconnect |

---

## Candle-service

### Consumer

| Level | Message | When | Action |
|-------|---------|------|--------|
| WARN | `unknown message type — skipping` | Redis stream message has unrecognized `type` field | Aggregator and candle-service may be version-mismatched |
| WARN | `xack failed for dup` | XACK for a duplicate message failed | Benign; the message was already processed |
| WARN | `xack failed for zero-vol trade` | XACK for discarded zero-volume trade failed | Benign |
| WARN | `consumer: lag query failed` | XPENDING query failed | Advisory metric; does not block processing |
| WARN | `consumer: shadow lag query failed` | Shadow XRANGE query failed | Advisory; shadow warmup continues |
| WARN | `stream overflow gap emitted` | Stream length exceeded 45,000 entries | Candle-service was down while aggregator wrote. Gap is injected into the accumulator. |
| WARN | `partial flush on snapshot failed` | Failed to write a partial bar when a snapshot arrived | Bar may be incomplete |
| WARN | `xautoclaim on promotion failed` | XAutoClaimPending after blue-green promotion failed | Old slot's messages may be lost; monitor `gap_count` |
| ERROR | `consumer: xautoclaim dispatch failed` | A message recovered via XAUTOCLAIM failed to dispatch | Bar contamination possible |
| ERROR | `accumulator flush failed` | Bar write to QuestDB failed at bar close | Bar data lost for this second |

### Flusher (B2 export)

| Level | Message | When | Action |
|-------|---------|------|--------|
| WARN | `catch-up: Redis last_flush_date unparseable, falling back to manifest` | Redis key has invalid date format | Manual fix: `DEL candle:last_flush_date` |
| WARN | `catch-up: manifest check failed, attempting flush anyway` | QuestDB flush_manifest query failed | Will attempt flush anyway; check QuestDB |
| WARN | `catch-up: flush failed, continuing` | A catch-up flush day failed | B2 will have a gap; run manual backfill with `FLUSH_DATE_OVERRIDE` |
| WARN | `flush: failed to update last_flush_date in Redis` | Redis SET after successful flush failed | Next startup will re-flush this day; extra B2 upload is idempotent |
| WARN | `flush manifest: QuestDB exec error` | Manifest write to QuestDB failed | Monitoring gap; flush itself may have succeeded |
| WARN | `flush manifest: request build failed` | Internal HTTP request build error | Should not happen; check for nil URLs |
| WARN | `flush manifest: write failed` | QuestDB INSERT for manifest failed | Non-critical; check QuestDB health |
| WARN | `flush alert publish failed` | Failed to publish flush failure alert to Redis | Monitoring alert not delivered |
| ERROR | `catch-up failed` | Entire catch-up process failed | Investigate and run manual backfill |
| ERROR | `daily flush failed` | Scheduled flush failed | Run `FLUSH_DATE_OVERRIDE=YYYY-MM-DD` to backfill |

### Candle-service writers

| Level | Message | When | Action |
|-------|---------|------|--------|
| WARN | `candle stream xadd failed` | Redis XADD for 1s candle bar failed | Bot-service won't receive this bar |
| WARN | `candle close stream xadd failed` | XADD for candle close stream failed | Bars may gap |
| WARN | `candle partial stream xadd failed` | XADD for partial bar failed | Dashboard update delayed |
| WARN | `ob features stream xadd failed` | XADD for OB features stream failed | Bot-service OB features delayed |
| WARN | `candles1s pubsub: marshal failed` | JSON encode of candles1s pub/sub payload failed | Gateway won't receive this bar |
| WARN | `candles1s pubsub: publish failed` | Redis pub/sub PUBLISH failed | Gateway won't receive this bar |
| WARN | `pubsub: publish failed` | Order book pub/sub PUBLISH failed | Gateway won't receive this OB update |
| WARN | `heatmap: ilp write failed` | Heatmap ILP write to QuestDB failed | Heatmap data point missing |
| WARN | `heatmap: ilp flush failed` | Heatmap ILP flush failed | Multiple heatmap points may be missing |

---

## Gateway

| Level | Message | When | Action |
|-------|---------|------|--------|
| WARN | `gateway: client send buffer full — dropping message` | WebSocket outbound buffer (16 messages) is full | Client connection is too slow; message dropped |
| WARN | `gateway: ws write failed` | WebSocket write to client failed | Client likely disconnected; connection will be closed |
| WARN | `gateway: failed to encode orderbook binary` | Binary codec encode failed for OB update | Dashboard won't receive this OB snapshot |
| WARN | `gateway: failed to inject type into candles1s payload` | JSON manipulation failed for candles1s message | Dashboard won't receive this candle update |
| WARN | `gateway: parseLevels skipping malformed pair` | OB level JSON has wrong structure | Single level dropped; rest of update applies |
| WARN | `gateway: parseLevels skipping unparseable pair` | OB level price/size not parseable as float | Single level dropped |

---

## Bot-service

### BusManager

| Level | Message | When | Action |
|-------|---------|------|--------|
| WARN | `strategy_not_ready_skipped` | Strategy `subscribe()` timed out during startup | Strategy will not receive events until manually restarted |
| WARN | `bus_manager_redis_error` | Redis XREADGROUP error; will retry with backoff | Transient Redis issue; check Redis connectivity |
| WARN | `queue_drop_oldest` | Strategy queue overflow; oldest event was dropped | GapMarker injected; strategy signal invalidated. Reduce bar processing latency or increase `BOT_QUEUE_MAX_DEPTH` |
| CRITICAL | `bus_manager_persistent_failure` | Redis errors persisted at cap for 3× cap limit | SIGTERM sent to process; immediate restart required |
| CRITICAL | `bus_manager_unhandled_error` | Unhandled exception in bus manager thread | SIGTERM sent; investigate exception in preceding log lines |

### Strategy base

| Level | Message | When | Action |
|-------|---------|------|--------|
| WARN | `gap_invalidated_signal` | Gap marker received for a symbol | Strategy signal invalidated for this symbol until `min_lookback` clean bars |
| WARN | `bus_timeout` | No events received for `bus_timeout_seconds` | Check Redis and candle-service; positions listed in log |
| WARN | `coldstart_high_gap_fraction` | >5% of history bars have `has_gap=true` | Historical data quality issue; strategy signals may be unreliable |
| WARN | `get_history_failed_returning_empty` | QuestDB history query failed | Strategy starts with empty rolling DF; scheduled retry in 30s |
| WARN | `add_indicators_failed` | `add_indicators()` raised an exception | Bar processed without indicators; NaN guard will suppress handler |
| WARN | `emergency_close_skipped_paper_trading` | Bus timeout triggered but paper trading is active | Emergency close skipped for paper mode |
| CRITICAL | `heartbeat_timeout` | Event loop did not ACK heartbeat within 10s | Process frozen; SIGTERM sent. Check strategy handler for blocking calls |
| CRITICAL | `emergency_close_failed` | Market order for emergency close failed | Position remains open; manual intervention required |

### Order worker

| Level | Message | When | Action |
|-------|---------|------|--------|
| WARN | `order_size_zero` | Computed order size rounded to zero | Check `max_position_pct` and `BOT_PORTFOLIO_VALUE_USD` |
| WARN | `risk_gate_daily_loss` | Daily loss limit hit; order rejected | Normal risk gate; no action needed unless limit is misconfigured |
| WARN | `risk_gate_max_notional` | Order notional exceeds `MAX_ORDER_NOTIONAL_USD` | Order rejected; check notional limit configuration |
| ERROR | `order_failed` | Exchange order placement returned error | Order not submitted; check exchange error in log fields |
| CRITICAL | `reconciliation_critical` | Position reconciliation at startup found unmanaged positions | Manual review required; positions listed in log |

### Exchange adapters (bot-service)

| Level | Message | When | Action |
|-------|---------|------|--------|
| WARN | `ws_reconnect` | WebSocket reconnect triggered | Normal for KuCoin/Bybit private WS |
| WARN | `fill_parse_failed` | Failed to parse fill event from exchange | Fill may not be recorded in bot state |
| WARN | `position_query_failed` | REST position query returned error | Reconciliation may use stale data |
| CRITICAL | `bybit_ws_persistent_failure` | Bybit private WS failed to reconnect repeatedly | Process exit will follow; manual restart required |
