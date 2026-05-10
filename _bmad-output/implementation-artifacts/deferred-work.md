# Deferred Work

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
