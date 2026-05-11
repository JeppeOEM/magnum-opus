# Deferred Work

## Deferred from: code review of 17-4-bot-service-orderbook-subscription (2026-05-11)

- **D-17-4-1: No reconnect in `_run_pubsub`** (`event_bus.py`) — Single Redis error or network blip exits the pub/sub thread permanently; only one `pubsub_thread_error` log line as signal. Explicit "future hardening item" per story spec. Add a retry loop matching `_consume_loop`'s exponential backoff.
- **D-17-4-2: `_handle_crash` doesn't call `deprovision_pubsub`** (`registry.py`) — Callbacks leak on crash path; old strategy instance could receive pub/sub messages during backoff window. No current impact (all strategies are mode="none"). Fix: add `deprovision_pubsub` call in `_handle_crash` before `dynamic_deregister`.
- **D-17-4-3: `stop_all()` doesn't call `deprovision_pubsub`** (`registry.py`) — Same risk as D-17-4-2; callbacks for stopped strategies remain registered if stop_all bypasses _unload. Fix: call `deprovision_pubsub` in `stop_all` loop.
- **D-17-4-4: `ps.listen()` blocks indefinitely; stop() can't unblock it** (`event_bus.py`) — `_stop_event.is_set()` is only checked between messages; `join(timeout=5s)` silently times out in quiet periods. Fix: call `ps.unsubscribe()` or `ps.close()` before joining to wake the generator.
- **D-17-4-5: TOCTOU gap — deprovisioned strategy may receive one callback after deprovision** (`event_bus.py`) — Snapshot-under-lock-iterate-outside pattern: `deprovision_pubsub` could complete between the lock release and the callback call. No current impact with no-op base callbacks.

## Deferred from: code review of 15-3-ma-cross-baseline (2026-05-10)

- **D-15-3-1: No position-flip logic — sell entry posted without closing existing long** (`ma_cross_bot.py`) — `order_role="entry"` for both buy and sell; no exit order before reversing. On a paper baseline this is acceptable but creates incorrect P&L accounting. Fix: track open side and post exit before new entry.
- **D-15-3-2: Fallback exchange hardcoded as `"kucoin"` while configured default is `"bybit"`** (`ma_cross_bot.py:57`, also `ofi_bot.py`) — If `_exchange` injection fails, orders silently route to kucoin instead of configured exchange. Fix: remove hardcoded fallback or use settings default.
- **D-15-3-3: `BaseStrategy.get_history` hardcodes `snapshot_1s` table for all TFs** (`base.py`) — `MACrossBot` requests `tf="1m"` history but the query always uses `snapshot_1s WHERE tf='1m'`. If 1m aggregates are not in that table, history returns empty silently and the bot starts cold. Fix: either store 1m bars in `snapshot_1s` with correct `tf` tag, or add a tf→table mapping in BaseStrategy.
- **D-15-3-4: `has_gap=True` rows included in EMA computation** (`ma_cross.py`) — Gap-marked rows contain price at gap time; if price was anomalous, the contaminated EMA persists through ewm decay. Pre-existing in ma_cross_signal; do not modify without test coverage.
- **D-15-3-5: `isna().all()` check allows single mid-series NaN to silently corrupt EMA** (`ma_cross.py`) — `pandas.ewm` forward-fills through mid-series NaN by default; the all-NaN guard misses partial NaN series. Pre-existing; do not modify.
- **D-15-3-6: `_signal_invalid` guard adds 50-bar dead zone on top of signal's own guard** (`ma_cross_bot.py`) — After a gap, `_on_bar` is suppressed for 50 bars by `_signal_invalid`, then `compute_ma_cross_signal` suppresses for another bar until `slow+1` accumulated. Doubles the dead zone. Design decision.
- **D-15-3-7: Gap-recovery counter shared across all timeframes for same symbol** (`base.py`) — `_on_clean_bar` counts ALL `on_bar` calls for a symbol regardless of tf. A future multi-TF strategy on BTCUSDT would advance the recovery counter faster than intended.

## Deferred from: code review of 15-2-ofi-microstructure-bot (2026-05-10)

- **D-15-2-1: `_order_worker` typed as `object|None` lacks Protocol contract** (`base.py:106`) — Using `object` type forces `# type: ignore[attr-defined]` at every call site. A `Protocol` with `post(req: OrderRequest) -> None` would catch mismatches statically. Intentional for now to avoid import complexity.
- **D-15-2-2: `size=max_position_pct` (0.05) passed directly to OrderRequest** (`ofi_bot.py:60`) — If OrderQueueWorker interprets `size` as a coin quantity rather than a portfolio fraction, the trade size will be wrong (0.05 BTC ≈ $5000). Needs validation against OrderQueueWorker's size interpretation when wiring live orders.
- **D-15-2-3: Market orders bypass risk gate notional check** (`order_worker.py`) — `projected_notional = req.size * (req.limit_price or 0.0)` always evaluates to 0.0 for market orders (no limit_price), so the risk gate is permanently inert for all market entries.
- **D-15-2-4: asyncio.Queue cross-loop safety for `_order_worker.post()`** (`order_worker.py`) — If OrderQueueWorker's run loop uses a different asyncio event loop than the strategy, `put_nowait` won't wake the consumer. Currently implicit that they share a loop; should be enforced or use thread-safe communication.
- **D-15-2-5: `paper_trading=True` + `close_on_bus_timeout=True` spawns no-op daemon threads** (`base.py`) — On bus silence, `_bus_timeout_loop` spawns emergency-close threads that immediately return on the paper_trading guard. Under prolonged silence with open positions this floods daemon threads. Low impact while paper trading only.
- **D-15-2-6: `ofi_signal` z-score references full `ofi` series for last value** (`ofi_signal.py`) — After `recent = ofi.iloc[-lookback:].dropna()`, the mean/std are computed on `recent` but `ofi.iloc[-1]` refers to the full (pre-dropna) series. If Inf values appear just before the window, `recent` fails the count guard while a valid signal exists. Pre-existing in ofi_signal.py; do not modify without test coverage.
- **D-15-2-7: `sys.path.insert` repeated in every test function** (`test_ofi_bot.py`) — Five test functions each do `sys.path.insert(0, .../strategies/active)`. Should be moved to a conftest.py fixture or the strategies dir should be a proper package.
- **D-15-2-8: `subscribe()` history retry creates a thread-safety race on `_dfs`** (`base.py`) — `_schedule_history_retry` fires a coroutine that writes to `_dfs` while subscribe may still be running in a ThreadPoolExecutor thread. No lock protects `_dfs`.

## Deferred from: code review of 15-1-fee-impact-analysis-gate (2026-05-10)

- **D-15-1-1: `commission_info` without `.p` attribute silently defaults `taker_rate=0.0`** (`fee_impact.py`) — `getattr(commission_info, "p", None)` then `getattr(params, "taker_rate", 0.0)` silently treats any unknown CommissionInfo subclass as zero-fee, making the gate always pass. Fix: add a type check or protocol assertion in `fee_impact_gate`.
- **D-15-1-2: Bybit `maker_rate=-0.0001` causes `required_edge < 0` → gate vacuously passes** (`fee_impact.py`) — `fee_impact_gate` reads `taker_rate` but when `taker_rate` is negative (maker rebate), `required_edge` goes negative and any positive-mean strategy passes. Fix: add `required_edge = max(required_edge, 0.0)` or guard against negative taker rates.
- **D-15-1-3: Mixed-NaN series (not all-NaN) passes guard with potentially single-sample mean** (`fee_impact.py`) — `isna().all()` allows `[NaN, NaN, 0.05]` through; `mean()` returns `0.05` based on 1 sample. Consider adding a minimum non-NaN sample count guard (e.g., `strategy_signals.dropna().size < 10` → raise ValueError).



## Deferred from: code review of 14-5-backtest-results-persistence (2026-05-10)

- **D-14-5-1: TCP connection opened per fill row** (`writer.py`) — `_sync_ilp_write` calls `Sender.from_conf(...)` inside the write method, creating a new TCP connection for every completed order. For a large backtest with thousands of fills, this results in thousands of separate connects and TLS/auth handshakes. Fix: open one `Sender` connection at `BacktestResultWriter` construction time (or per `run_backtest_and_persist` call) and reuse across all `write_fill` calls.
- **D-14-5-2: `realized_pnl` always written as 0.0** (`writer.py`) — Backtrader does not natively expose realized P&L per fill in `notify_order`; it must be computed from broker position changes or tracked across buy/sell pairs. Left as 0.0 for now. Fix: compute `realized_pnl` using average entry price × (fill_size) for close legs; requires tracking open position cost basis across fills.

## Deferred from: code review of 14-4-walk-forward-stress-test-and-monte-carlo-harnesses (2026-05-10)

- **D-14-4-1: Stress test results not gating `passes`** (`validation.py`) — `StressTestReport` is recorded as run but per-window Sharpe/drawdown values do not contribute to `ValidationReport.passes`. The stress test is currently informational only. Fix: add configurable max-drawdown threshold for stress windows; if any window exceeds it, `passes=False`.
- **D-14-4-2: Monte Carlo sum is commutative — shuffle is a no-op for total P&L** (`validation.py`) — `sum(shuffled)` equals `sum(trade_pnls)` for any permutation (addition is commutative), so all `n_shuffles` simulations produce the same value and the 5th percentile equals the total P&L. This is the spec-defined behavior. Future fix: replace with bootstrap resampling with replacement (samples N trades with replacement, computing path-dependent cumulative P&L), which would capture genuine sequence-order risk.

## Deferred from: code review of 12-2-private-websocket-fill-feed-and-rest-poll-fallback (2026-05-10)

- **D1: REST fallback queries open-orders endpoint, not fills endpoint** (`kucoin/ws_private.py`, `bybit/ws_private.py`) — `get_open_orders()` returns only currently-open orders; filled orders are never returned. REST fallback is a structural no-op for fills. Needs fills/history endpoint: KuCoin `/api/v1/fills`, Bybit `/v5/order/history`. Add `get_recent_fills(symbol, since_ts)` to both REST clients in story 12.3 and re-wire fallback.
- **D2: Sort by `ts_placed` instead of `ts_exchange`** (AC5 violation) — `OpenOrder.ts_placed` is order creation time; fill time (`ts_exchange`) is unavailable from the open-orders endpoint. Resolved automatically when D1 is implemented (fills endpoint returns fill time).
- **D3: `_get_ws_token` calls private `_request` method** (`kucoin/ws_private.py:52`) — tightly coupled to REST client internals. Add public `get_private_ws_token() -> tuple[str, str]` method to `KuCoinRESTClient`.
- **D4: KuCoin ping interval hardcoded at 30s** (`kucoin/ws_private.py`) — exchange-negotiated `pingInterval` from bullet-private response is ignored; token `tokenLife` (lifetime) is also ignored. Low-risk for now.
- **D5: Bybit auth failure retried with backoff forever** (`bybit/ws_private.py`) — no ERROR-level escalation after N failed auth attempts; bad API key loops silently.
- **D6: Missing test for empty `orderId` in KuCoin `_parse_fill`** — `_parse_fill` uses `.get("orderId", "")` so missing field silently produces `order_id=""`.
- **D7: L2 fallback tests use 1.5s real sleep** (`tests/test_ws_private.py`) — fragile under CI load; liveness check interval should be injectable for faster test feedback.

## Deferred from: code review of 12-1-exchange-auth-and-httpx-rest-client (2026-05-10)

- **D1: Bybit `category` hardcoded to `"spot"`** (`bot_service/exchange/bybit/rest.py`) — `place_order`, `cancel_order`, `get_open_orders` all use `"spot"`; futures/inverse support requires adding `market_type` field to `OrderRequest` and threading it through. Deferred until story 12-5 or when futures support is explicitly planned.
- **D2: `ts_exchange` from local clock** (`kucoin/rest.py`, `bybit/rest.py`) — `PlacedOrder.ts_exchange` is populated with `int(time.time() * 1000)` instead of the timestamp returned in the exchange's response body. Requires parsing `createdAt` / `createdTime` from place-order response.
- **D3: `AsyncClient` allocated per retry** (`kucoin/rest.py`, `bybit/rest.py`) — `async with httpx.AsyncClient()` inside the retry loop creates a new client (with new TLS handshake) on each 5xx retry instead of reusing an existing one. Low priority — retries are rare and low-frequency.
- **D4: `cancel_order` `symbol` param unused in KuCoin** (`kucoin/rest.py`) — KuCoin DELETE `/api/v1/orders/{id}` does not require `symbol`; the parameter is accepted for `ExchangeClient` protocol compatibility but goes unused (`# noqa: ARG002`). Document in ExchangeClient protocol docstring.

## Deferred from: code review of 11-7-fastapi-service-entry-point-and-startup-sequence (2026-05-10)

- **`sys.exit(1)` inside ASGI lifespan** — spec-mandated; uvicorn/systemd handle SystemExit cleanly in single-process deployments; multi-worker supervisor coordination not planned for this service.
- **BusManager reports healthy with zero registered streams** — `is_alive()` is True before strategies register; by design pre-Epic 12; operators should use strategy-level metrics for liveness.
- **`pandas-ta>=0.3.14b` no upper version pin** — library is sparsely maintained; evaluate upper bound when Epic 15 adds concrete indicator usage.
- **No test for `add_indicators()` override that raises or produces NaN** — Epic 15 strategy implementations will exercise this path; add fixtures then.
- **Heartbeat thread sends `os.kill(SIGTERM)` from base.py** — pre-existing Story 11.6 design; conflicts with uvicorn signal ownership only when heartbeat fires during clean shutdown; monitored via `heartbeat_timeout` log event.

## Deferred from: code review of 9-2-daily-parquet-flush-and-catchup (2026-05-08)

- **Full-day in-memory materialization without size bound** — entire CSV and Parquet bytes in memory simultaneously; hundreds of MB on dense days; streaming to S3 requires significant rework.
- **`errMsg` SQL-escape insufficient** — single-quote doubling only; internal Go error strings contain no SQL-special characters in practice; low risk.
- **`s3Client()` allocates new HTTP transport per flush** — no TLS session reuse; negligible overhead for once-daily operation.
- **`FlushDateOverride` accepts today or future date without guard** — operator-controlled override; partial-day data flushed silently; document in ops runbook.
- **`readManifestLastSuccess` ORDER BY date_flushed returns oldest backfill target after FLUSH_DATE_OVERRIDE** — idempotency guard in `runCatchup` prevents double-upload; wasted manifest queries only.
- **`publishFlushAlert` calls `f.clk.Now()` without documented Clock concurrency guarantee** — production wall-clock is safe; no fake Clock planned for this path.
- **`parseInt32` silently drops values exceeding int32 range** — OB activity counts well within int32 for 1-second windows; add logging if large-value symbols emerge.
- **`flush_manifest` written via HTTP /exec INSERT instead of ILP** — dev notes explicitly authorize this as the simpler testable alternative; no ILP dependency in flusher.

## Deferred from: code review of 9-1-flush-manifest-ddl (2026-05-08)

- **No DEDUP UPSERT KEYS on flush_manifest** — retried flushes produce duplicate rows; idempotency is application-level (story 9-2 catch-up checks `success=true` before inserting).
- **PARTITION BY YEAR with no TTL** — rows accumulate forever; 1 row/day volume keeps this manageable; can add `TTL 3y` or similar in a future ops migration.
- **`success BOOLEAN` nullable** — QuestDB WAL has no NOT NULL constraint syntax; application always populates this field.
- **`error_msg STRING` unbounded in schema** — story 9-3 truncates to 512 chars at application layer before insert.
- **`b2_path STRING` unconstrained** — empty vs null indistinguishable in schema; application enforces non-empty on success.
- **`duration_ms LONG` signed** — QuestDB has no CHECK constraints; negative values (clock skew) silently accepted; application concern.
- **SYMBOL CAPACITY hint absent on `exchange`** — defaults to 128 vs 2–3 actual values; cosmetic inconsistency with `snapshot_1s` pattern (`SYMBOL CAPACITY 8` there).

## Deferred from: code review of 5-2-migration-runner (2026-05-08)

- **Phantom migration**: DDL applied but `schema_migrations` INSERT fails → blocks startup permanently on re-run for non-idempotent DDL (`candle-service/internal/migrator/migrator.go:240`) — QuestDB REST has no multi-statement transactions; current migration files use `IF NOT EXISTS`; inherent limitation documented in package comment.
- **Context cancellation only at `Run` entry**: inter-migration cancellation relies on http.Client context propagation (`migrator.go:51`) — http.Client propagates ctx on each call; startup-only path with small file count.
- **Concurrent blue/green starts produce duplicate `schema_migrations` rows** (`migrator.go:149`) — benign (duplicate checksums overwrite identically in map); deployment strategy avoids simultaneous starts.
- **`exec`/`queryRows` response body unbounded** — no `io.LimitReader` (`migrator.go:105`) — startup-only runner against known QuestDB; not a realistic attack surface.
- **`TestRun_ContextCancellation` tests pre-cancellation only** (`migrator_l2_test.go:165`) — fast-path guard tested; mid-flight handled by http.Client ctx propagation.

## Deferred from: code review of 4-3-service-composition-root-and-credential-sanitizing-logger (2026-05-07)

- **`gapwindow.Window` allocated but never populated — gap_count_24h always 0** (`cmd/aggregator/main.go:132`) — the coordinator Workers detect and emit gap events to Redis/QuestDB but never call `gapWin.Add()`. The HTTP /health endpoint reports `gap_count_24h: 0` for the entire process lifetime. Wiring requires adding gapwindow as a coordinator dependency (new constructor parameter). Pre-existing architectural gap from story 3.x; out of scope for story 4.3.
- **No `cmd/aggregator/main_test.go` verifying slogredact handler is active in test binary** (`cmd/aggregator/`) — the epic-level spec for story 4.3 states "a test in cmd/aggregator/ confirms the handler is registered before any test log output is produced." The story spec did not carry this task forward. Deferred — story spec is the binding document for this story.

## Deferred from: code review of 4-2-health-version-and-metrics-http-endpoints (2026-05-07)

- **Gather() errors silently swallowed in GathererFeedStatus** (`httpapi/server.go:FeedCounts`) — if the prometheus registry returns an error, returns 0/0 which reports status=ok; acceptable tradeoff for in-memory operation, no production impact.
- **Populate() is not an atomic batch** (`gapwindow/window.go:Populate`) — acquires mutex N times; other goroutines can interleave; startup-only call in practice so concurrent access is not expected before Run().
- **Window.Add negative Unix timestamp guard incomplete** (`gapwindow/window.go`) — `int(h%24+24)%24` is incorrect for `h < -24`; pre-epoch gap timestamps never occur in production.
- **Count() window boundary includes events in the cutoff hour** (`gapwindow/window.go`) — events up to 1h older than exactly 24h ago may be counted due to hourly-bucket granularity; by design, consistent with spec's "older than 24h" interpreted at hourly resolution.
- **Package-level `Version`, `GitSHA`, `BuildTime` vars are mutable global state** (`httpapi/version.go`) — ldflags injection requires exported package vars; tests use VersionInfo struct injection instead; no mutation in production.
- **No HTTP method restriction on /health, /version, /metrics** (`httpapi/server.go`) — POST/PUT/DELETE return 200; internal monitoring endpoints, no auth, low risk.
- **JSON encoding errors silently discarded** (`httpapi/server.go:handleHealth, handleVersion`) — encoding simple value structs never fails in practice; if ResponseWriter fails mid-write, client sees truncated response.
- **`TestHealth_UptimeIncreases` tests lower bound only** (`httpapi/server_test.go`) — asserts `>= 10s`, not strictly increasing; sufficient for the AC but could be more precise.
- **BucketEviction test only exercises one 24h wrap** (`gapwindow/window_test.go`) — second wrap (48h advance) not tested; modulo logic is the same for all wraps.

## Deferred from: code review of 4-1-prometheus-metrics-registry (2026-05-07)

- **metricsReg field stored but never read** (`coordinator/coordinator.go:WithMetrics`) — set in WithMetrics but not referenced elsewhere; will be needed if coordinator-level metrics (e.g. snapshot dispatch latency) are added in a later story.
- **WithMetrics after Run() is an unsynchronized data race** (`coordinator/coordinator.go:WithMetrics`) — writes `worker.metrics` field without synchronization; consistent with existing WithSleep pattern; doc comment "Call before Run()" is the contract enforcement.
- **gapCauses is a mutable package-level var slice** (`metrics/metrics.go`) — `var gapCauses = []string{...}` could be appended to; package-private and never mutated in practice. Cosmetic.
- **ConsumerLagMs has no writer** (`coordinator/`) — metric is defined and pre-initialized but no code path calls `ConsumerLagMs.Set(...)` yet. Will be wired when Redis Stream consumer lag is tracked (story 4.2 or 4.3).

## Deferred from: code review of 3-5-snapshot-dispatch-and-full-coordinator-orchestration (2026-05-07)

- **AC3 Redis drain not implemented** (`coordinator/coordinator.go`, `coordinator/symbol.go`) — Workers pass the cancelled root `ctx` to `stream.Write()`; in-flight Redis writes abort on SIGTERM. Lost tick falls inside the restart gap marker so audit trail is complete. Wire a proper drain context in story 4.3 (service composition root) where the full shutdown sequence is assembled.
- **Stale snapshot sequencing hazard after panic recovery** (`coordinator/snapshot.go`) — when a panic fires during StateBuffering, recovery enqueues a second SnapshotRequest; the first result is consumed by the panic-recovered Worker, the second result sits in resultCh and is consumed by the next snapshot request cycle with potentially stale data. Root cause: all SnapshotRequests share the same `w.resultCh`. Fix requires per-request ResultCh. Low probability in practice.
- **`parseSide` silent default to `SideBid` for unknown values** (`coordinator/symbol.go`) — any tick whose Side field is not `"ask"` (including empty string) silently becomes a bid. Pre-existing from story 3.4.
- **Gap marker write errors intentionally discarded** (`coordinator/symbol.go`) — `_ = w.stream.WriteGap(...)` in the shutdown path. Best-effort semantics per NFR9 (must-not-halt). By design.
- **Fixed-index test assertions in `TestWorker_GapDetected_EmitsMarker`** (`coordinator/symbol_test.go`) — assertions on `entries[1]` and `entries[2]` by fixed index could misfire if the Worker's retry logic produces extra entries before the snapshot. Pre-existing from story 3.4.
- **Replay buffer not applied after snapshot merge** (`coordinator/symbol.go`) — `replay` return value from `recon.MergeSnapshot()` is discarded; buffered deltas newer than the snapshot are never applied to the order book. Acknowledged design limitation in Dev Notes (story 3.4).
- **`time.Sleep(25ms)` synchronization in E2E tests** (`coordinator/coordinator_test.go`) — used to allow the Worker goroutine to receive from `resultCh` and call `GoLive` after `WaitCalled` returns. Explicitly documented trade-off in Dev Notes.
- **`Shutdown()` called before `Run()` leaves ILP writer broken** (`coordinator/coordinator.go`) — `ilp.Close()` closes `w.stop`; subsequent Worker goroutines from `Run()` write to an undrained channel. API contract issue; low practical risk.
- **Unknown-symbol tick drop unlogged in `runTickFanout`** (`coordinator/coordinator.go`) — ticks for symbols not in `symMap` are silently dropped with no log; a buffer-full drop does log. Minor observability gap.
- **len != 2 snapshot entries silently skipped** (`exchange/kucoin/snapshot.go`, `exchange/bybit/snapshot.go`) — the `if len(level) == 2` guard silently ignores malformed entries. Correct for current API format (pairs); would silently corrupt book if API returns triples.
- **Extra HTTP call when ctx already cancelled at entry to `fetchWithRetry`** (`coordinator/snapshot.go`) — attempt 0 runs before `ctx.Err()` is checked; under clean shutdown one extra immediately-failing network call is made per in-flight snapshot request.
- **`seq_gap=0` when `lastSeq=0` on shutdown gap marker** (`coordinator/symbol.go`) — when no ticks have arrived (lastSeq=0), the shutdown gap marker has `SeqBefore=0, SeqAfter=1`, producing `seq_gap=0`. Downstream consumers filtering on `seq_gap > 0` will miss this event. Acknowledged in Dev Notes.

## Deferred from: code review of 2-1-exchange-interface-and-websocket-transport-layer (2026-05-06)

- **Close() blocks up to 5s when connection dies between pings** (`transport/conn.go:96`) — pre-existing behavior now narrowed by the `dropped` fix. If a connection dies silently between keepalive pings, `dropped` is still false and `Close(StatusNormalClosure, "")` waits up to 5s for a handshake that will never complete. Consider a deadline on the graceful close path.
- **Subscribe()/Close() concurrent channel-close race in kucoin.go** — if `runLoop` closes `ticks`/`signals` while `handleMarketData` is dispatching a tick, a send-on-closed-channel panic can occur. Pre-existing; address in story 2.2 when kucoin adapter is hardened.
- **Conn.Close() not idempotent; CloseNow()/Close() errors silently discarded** (`transport/conn.go`) — second Close() call reaches the underlying conn after it is already closed; library behavior is undefined. Errors are dropped with no logging. Low priority — double-close is not expected usage.
- ~~**fetchToken calls clock.Now() twice with no atomicity**~~ — **Fixed in Story 2.3** (`token.go`: `now := clock.Now()` captured once).

## Deferred from: code review of 2-2-kucoin-websocket-feed-adapter (2026-05-06)

- **`confirmWatcher` hardcodes `FeedTypeOrderBook` in retry** (`kucoin.go:confirmWatcher`) — trade-feed subscription timeouts would be retried as order book subscriptions. Pre-existing; address when trade feed subscriptions are added.
- ~~**`parseTrade` silently maps unknown `side` to `"bid"`**~~ — **Fixed** (`parser.go`: switch now returns `fmt.Errorf("unknown trade side %q", ...)` for unexpected values; test `TestParseTrade_UnknownSide` added).
- **Subscribe during reconnect backoff still subject to ack-reset race** (`kucoin.go:runLoop`) — the window fixed for initial connect persists during reconnect: a concurrent `Subscribe()` call while in backoff registers in `pendingAcks`, then `resetAcks()` clears it on reconnect. Pre-existing design; consider a connection-scoped subscribe queue.
- **`symbolFromTopic` with multi-symbol batch topic returns wrong symbol** (`parser.go`) — `LastIndex(":")` on a comma-separated topic yields the full symbol list as a single key. Latent because level2 wire messages arrive per-symbol; pre-existing.
- **End-to-end WS server-push → readLoop → tick path not covered at L3** (`kucoin_test.go`) — `TestAdapter_ConnectSubscribeReceiveTick` injects via `a.dispatch()` directly. A corrupt server push breaking `readLoop` would go undetected here. Acknowledged test design choice; the transport L3 tests cover the read path.

## Deferred from: code review of 2-3-kucoin-token-auto-renewal (2026-05-06)

- **Double `Connect()` goroutine leak** (`kucoin.go:Connect`) — calling `Connect()` twice launches a second `tokenRenewalLoop` goroutine; pre-existing pattern shared with `runLoop`; needs a global guard.
- **Real wall-clock sleep in `TestTokenRenewal_RetryThenSucceed`** (`kucoin_test.go`) — `backoff.Duration` returns a real-time duration consumed by `time.After`; MockClock cannot short-circuit it; test takes ~1s real time; architectural trade-off.
- **`TestTokenRenewal_ExhaustRetries` assertion conflates renewal and reconnect token requests** (`kucoin_test.go`) — assertion `tokenRequestCount > initialCount+renewalMaxAttempts` passes due to reconnect's own failing fetches; correct behavior but assertion is imprecise.
- **No concurrent test for `tokMu` atomicity under simultaneous renewal write and heartbeat read** (`kucoin_test.go`) — AC4 production code is correct; test gap only.

## Deferred from: code review of 2-4-bybit-connection-multiplexer (2026-05-06)

- **confirmWatcher timer can fire sooner than expected post-reconnect** (`mux.go:confirmWatcher`) — after `resetAcks()` and `sendSlotSubscriptions`, the watcher's timer from the previous cycle is already ticking; the first post-reconnect check may arrive before the full `confirmTimeout` window has elapsed. Low production impact; design limitation of a watcher whose lifetime spans reconnects.
- **sendSymbolSubscriptions write-failure window lets watcher observe in-flight pendingAck** (`mux.go:sendSymbolSubscriptions`) — between `s.mu.Unlock()` (after inserting the reqID) and the `conn.Write` call, `confirmWatcher` can acquire the lock and see a reqID whose write hasn't succeeded yet. If the write fails, the reqID is deleted and the watcher's retry is spurious for one cycle. Benign and self-healing.
- **readLoop exits silently on non-context read error without triggering reconnect** (`mux.go:readLoop`) — if the transport returns a read error that is not due to context cancellation AND the transport's `Reconnect()` channel doesn't fire, `runSlot` hangs on its select indefinitely. This is a transport contract assumption (transport must signal `Reconnect()` on fatal errors) that is not enforced in the mux.

## Deferred from: code review of 3-1-coordinator-interface-definitions-and-stream-schema (2026-05-06)

- **GapEvent.SeqBefore >= SeqAfter produces uint64 underflow in seq_gap formula** (`gapdetector/types.go`) — gapdetector.Detect fires on `next != prev+1`; a retransmit or rollback where `next <= prev` produces a GapEvent with SeqBefore > SeqAfter. The seq_gap formula `SeqAfter - SeqBefore - 1` then wraps. Pre-existing in gapdetector (Epic 1).
- **gapdetector.Detect treats next < prev as a gap, producing SeqBefore > SeqAfter** (`gapdetector/gapdetector.go`) — no special handling for sequence rollback or retransmit. Pre-existing behavior from Epic 1.
- **ts_exchange can be zero/epoch if exchange sends malformed timestamp** (`exchange/bybit/parser.go`, `exchange/kucoin/parser.go`) — parsers do not validate TsExchange > 0; a zero value writes a row into the 1970-01-01 QuestDB partition. Pre-existing parser issue from Epic 2.
- **FakeRedis not goroutine-safe** (`internal/testutil/fakeredis.go`) — `calls`, `streams`, and `FailAfter` are read/written without mutex. FakeQuestDB is correctly mutex-protected. Pre-existing in testutil (Epic 1).
- **Bybit parseTrade silently drops all but the first trade in a multi-trade batch frame** (`exchange/bybit/parser.go`) — only `trades[0]` is processed; remaining trades vanish with no error, log, or gap marker. Pre-existing from Story 2.5.
- **price/size STRING columns are unbounded in raw_ticks** (`internal/writer/questdb/schema.sql`) — QuestDB STRING has no length constraint; adversarial or malformed exchange messages could write arbitrarily large values. Parser validation is the appropriate enforcement boundary.

## Deferred from: code review of 3-2-redis-stream-writer (2026-05-06)

- **seq_gap=-1 when Write() emits best-effort gap with SeqBefore=SeqAfter=tick.Seq** (`internal/writer/redis/writer.go:83-88`) — on tick write exhaustion, the emitted GapEvent has SeqBefore=SeqAfter=tick.Seq, producing seq_gap = -1 in gaps:log. No better sequence info is available at the failure site; downstream interprets -1 as an undefined sentinel. Fix requires tracking last-good sequence in Writer state.
- **Gap write error swallowed silently in Write()** (`internal/writer/redis/writer.go:83`) — `_ = w.WriteGap(...)` discards the error when the best-effort gap marker also fails. No log entry, no metric. By design (NFR9 must-not-halt), but creates a silent data loss window.
- **FailFirst/FailAfter share global calls counter across all streams in FakeRedis** (`internal/testutil/mock/fake_redis.go`) — failure injection triggers at a global call count regardless of stream; multi-stream write sequences may have non-obvious failure points.
- **XACK is no-op / Redis PEL semantics not modeled in FakeRedis** (`internal/testutil/mock/fake_redis.go`) — FakeRedis advances position on read (not ACK); real Redis re-delivers unACKed entries on consumer restart. Tests verify position advancement, not re-delivery behavior.
- **Production wiring of 10s retry cap not enforced** — `New()` accepts arbitrary maxRetryDur; no production instantiation enforces 10s. Will be wired in story 4.3 (service composition root).

## Deferred from: code review of 5-1-go-project-setup-and-docker (2026-05-08)

- No `IdleTimeout` on `http.Server` in `cmd/candle/main.go` — best practice; add when service has long-lived connections
- No `HEALTHCHECK` in Dockerfile — add when deploying to an orchestrator that uses it
- `test-l1` Makefile target explicitly lists packages instead of `./...` — update as new L1 packages are added
- `handleHealth`/`handleVersion` respond to any HTTP method — add method check when tightening the HTTP surface
- `slotHandler.WithGroup` adds `slot` outside the named group — revisit when log structure requirements are finalized
- `stop_grace_period: 15s` (docker-compose) vs `SHUTDOWN_TIMEOUT_S: 10s` (env) — intentionally different; link them in a comment if both change
- `CANDLE_SERVICE_PORT` not validated — invalid value fails at runtime with unhelpful error; validate in config.Load() when hardening startup
- No `healthcheck:` stanza in docker-compose candle profiles — needed for Story 9-4 promotion gating


## Deferred from: code review of 8-1-cascade-engine-and-clock-boundary (2026-05-08)

- **SQL injection in `querySnapshotCount`** (`cmd/candle/main.go`): `exchange`/`symbol` interpolated directly via `fmt.Sprintf` without escaping. Low risk — values come from operator-controlled env vars, not user input. Add input validation or note in ops docs when hardening startup.
- **`getEnvInt`/`getDurationSeconds` silently treat 0 as unset** (`internal/config/config.go`): guard `n > 0` makes it impossible to configure zero-value for e.g. `SHUTDOWN_TIMEOUT_S=0`. Pre-existing pattern across all config fields.
- **`persistCascadeHashes` worst-case blocking: ~2.45s on full Redis failure** (`cmd/candle/main.go`): 7 TFs × 3 retries × max sleep = 2.45s blocks consumer goroutine, dropping bar-close signals. Design-level issue per spec (backoff-per-TF); revisit with async write or per-batch timeout when measuring production impact.
- **`Engine.clk` field stored but never called** (`internal/cascade/cascade.go`): `Clock` interface defined for future use by 250ms partial-publish ticker in story 8-2. Not dead code — spec-intentional placeholder.
- **`Flush()` uses `time.Now()` for `tsSecMs` — late flush can miss cascade boundary** (`cmd/candle/main.go`): if consumer goroutine is delayed past a second boundary, `IsBarClose` receives the wrong second and the boundary is permanently missed. Pre-existing design issue in original `Flush()` (predates story 8-1); the cascade fold inherited the same wall-clock coupling.

## Deferred from: code review of 7-2-block-trades-rolling-percentile (2026-05-08)

- `blockwindow.Window.Add` uses O(n) copy-shift — 999 element copies per tick at default 1000-entry window; replace with circular buffer when CPU cost becomes measurable
- Redis key `candle:btw:{exchange}:{symbol}` has no TTL — removed symbols leave stale entries indefinitely; add TTL or cleanup sweep when symbol rotation is implemented
- `blockwindow.New(windowSize, minSample)` silently produces a permanently cold window when `minSample > windowSize`; add validation log or error when formalizing config hardening

## Deferred from: code review of 8-3-ob-feature-snapshot-publisher (2026-05-08)

- `OFI` and `OFIL1` are identical in the accumulator — both assigned from `ofiSum` with no separate L1-only delta path; `ofi_l1` provides no L1-isolated signal downstream (`accumulator.go:529–530`)
- `best_bid`/`best_ask` in `ob_features` reflect the last trade-tick close quote, not the current OB top — `hasCloseQuote` is not set by `SeedFromLastKnown`, so empty seconds show `""` even when OB state is fully known (`accumulator.go:568–571`)


## Deferred from: code review of 13-1-startup-reconciliation-exchange-rest-vs-questdb (2026-05-10)

- **D1: exchange="" in restored OrderRequest** — `_build_from_questdb_row` sets `exchange=""` because exchange is not stored in `order_events` DDL; fill events written after crash-recovery will have blank exchange label in QuestDB; downstream analytics/P&L attribution affected. Dev notes acknowledge this is unknown at restore time. Fix: add `exchange` symbol to `order_events` DDL in a future story.
- **D2: ts_exchange=0 sentinel in restored PlacedOrder** — `PlacedOrder(ts_exchange=0)` is used as "unknown"; latency calculations using `now - ts_exchange` would produce nonsensical values. Dev notes explicitly state 0 as sentinel. Fix: type `ts_exchange` as `int | None` and gate latency calculations on non-None.

## Deferred from: code review of 14-3-custom-commissioninfo-exchange-fee-models (2026-05-10)

- **D-14-3-1: tz-naive/tz-aware mismatch in `get_funding_cost` silently returns 0.0** (`commission.py`) — passing a tz-naive datetime when `_funding_rates` has a tz-aware index causes `asof()` to raise `TypeError`, which is swallowed by the `except (KeyError, TypeError)` clause. Cost returns 0.0 with a "funding_rate_not_found" log that doesn't indicate the root cause. Fix: validate tz consistency at `get_funding_cost` entry, or coerce `pd.Timestamp(timestamp, tz='UTC')` before lookup.
- **D-14-3-2: Duplicate timestamps in `funding_rates` not validated** (`commission.py`) — `pd.Series.asof()` silently picks the last duplicate. Add a validation step in `__init__` or `get_funding_cost` to log a warning if the index has duplicates.

## Deferred from: code review of 16-1, 16-2, 16-3 (2026-05-10)

- **D-16-1-1 (partial): Per-strategy position/P&L gauges not wired** (`prometheus.py`, `base.py`) — `set_position_size`, `set_unrealized_pnl`, `set_drawdown` still never called; Grafana position/P&L panels remain zero. `set_consumer_lag` fixed 2026-05-10: now emitted from `BusManager._consume_loop` after every XREADGROUP poll so Active Strategies count and Consumer Lag panels are live.
- **D-16-2-1: `get_strategy_statuses()` reads `_loaded`/`_pending_restart` without holding FileWatcher lock** (`registry.py`) — FastAPI sync threadpool reads these dicts concurrently with the FileWatcher asyncio loop writer. Under normal operation (single watcher thread + cooperative asyncio) this is safe, but formally a data race. Fix: expose statuses via a thread-safe snapshot (lock + copy) or via asyncio.run_coroutine_threadsafe.
- ~~**D-16-2-2: QuestDB healthcheck uses `/health` endpoint**~~ — **Fixed 2026-05-10**: reverted to `/exec?query=select+1` after `/health` caused `unhealthy` on the running QuestDB version.
- **D-16-3-1: Alert playbook references `bot_consumer_lag` metric but no Grafana panel exists** (`docs/ops.md`) — playbook for `bot_heartbeat_timeout_total` tells operators to `curl .../metrics | grep bot_consumer_lag` but no Grafana panel visualises this metric. Add a consumer_lag panel to the Grafana dashboard in a future monitoring story.

## Deferred from: quick-dev fix — make watch startup visibility (2026-05-10)

- **D-QD-1: `_ALERT_ERROR_KEYWORDS` broad substrings produce false-positive noise** (`scripts/logfmt.py`) — keywords like `"error"` and `"failed"` are substrings that match Docker pull retry progress lines (e.g. "Retrying failed download...") and build step annotations, producing dim grey noise during image pulls. Fix: tighten to whole-word patterns (`re.search(r'\b(error|fail|fatal)\b', lower)`) or add a known-noisy prefix exclusion list (e.g. skip lines starting with `#`/`---`/`=>`).

## Deferred from: code review of 17-1-aggregator-orderbook-pubsub (2026-05-10)

- **D-17-1-1: Synchronous Redis PUBLISH adds per-tick latency in Worker goroutine** (`aggregator/internal/writer/pubsub/publisher.go`) — `p.client.Publish` is a synchronous round-trip with up to 3s default go-redis write timeout. Under Redis stall this blocks `handleTick` and causes the 256-entry delta channel to fill, triggering spurious seq-gap reconnects. Pre-existing pattern: `stream.Write` and `ilp.Write` are also synchronous. Decouple via a goroutine+channel if pub/sub latency becomes a production concern.
- **D-17-1-2: `sort.Slice` non-stable for price strings that parse to the same float64** (`aggregator/internal/writer/pubsub/publisher.go:sortedLevels`) — Two textually-different price strings (e.g. "29500.0" and "29500.00") producing the same float64 yield non-deterministic level ordering. Exchange price strings are canonical in practice; use `sort.Stable` if determinism becomes a requirement.
- **D-17-1-3: `WithOBPublisher` has no post-Run() call guard** (`aggregator/internal/coordinator/coordinator.go`) — Calling `WithOBPublisher` after `coordinator.Run()` is a data race on `w.pub`. Same contract as `WithMetrics` (pre-existing). Add a `started` guard if the API needs to be made safe.
- **D-17-1-4: `sendAndWait` and absence tests use wall-clock `time.Sleep`** (`aggregator/internal/coordinator/obpublisher_test.go`) — Polling loop and fixed 50ms sleep are fragile under CI load. Replace with channel-based notification from `fakeOBPublisher` for deterministic test timing.


## Deferred from: code review of 17-3-depthview-gateway-dual-channel-subscription (2026-05-11)

- **D-17-3-1: InjectType int64 precision via map[string]any round-trip** (`gateway/internal/codec/codec.go:66-73`) — JSON decode into `map[string]any` converts int64 fields (e.g. ts_ns) to float64, losing ~128ns precision on re-encode. No practical impact for 1s candle display; use direct JSON byte injection if precision matters in future.
- **D-17-3-2: No protocol version byte in binary frame header** (`gateway/internal/codec/codec.go`) — Binary frames have no version field; breaking protocol changes will silently corrupt clients. Premature for v1 single-consumer deployment; add version byte when second consumer is introduced.
