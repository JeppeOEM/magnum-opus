# Deferred Work

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
