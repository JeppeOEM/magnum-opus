# Deferred Work

## Deferred from: code review of 2-1-exchange-interface-and-websocket-transport-layer (2026-05-06)

- **Close() blocks up to 5s when connection dies between pings** (`transport/conn.go:96`) — pre-existing behavior now narrowed by the `dropped` fix. If a connection dies silently between keepalive pings, `dropped` is still false and `Close(StatusNormalClosure, "")` waits up to 5s for a handshake that will never complete. Consider a deadline on the graceful close path.
- **Subscribe()/Close() concurrent channel-close race in kucoin.go** — if `runLoop` closes `ticks`/`signals` while `handleMarketData` is dispatching a tick, a send-on-closed-channel panic can occur. Pre-existing; address in story 2.2 when kucoin adapter is hardened.
- **Conn.Close() not idempotent; CloseNow()/Close() errors silently discarded** (`transport/conn.go`) — second Close() call reaches the underlying conn after it is already closed; library behavior is undefined. Errors are dropped with no logging. Low priority — double-close is not expected usage.
- **fetchToken calls clock.Now() twice with no atomicity** (`kucoin/token.go:62,117`) — HMAC timestamp and `fetchedAt` use separate clock reads. With a MockClock that advances per call, test assertions on token expiry can drift by one tick. Fix: capture `clock.Now()` once at the top of `fetchToken` and reuse.
