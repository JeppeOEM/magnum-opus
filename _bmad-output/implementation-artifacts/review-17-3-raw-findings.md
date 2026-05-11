# Code Review 17-3: Raw Findings (pre-triage)

**Status:** Gathered all 3 layers. Next step: triage (step-03) then present (step-04), then apply patches.

**Story:** `17-3-depthview-gateway-dual-channel-subscription`
**Diff scope:** `gateway/` (all new files)
**Review mode:** full (spec available)

---

## Blind Hunter Findings

1. `InsecureSkipVerify: true` hardcoded — no env-flag to disable in future
2. `srv.Shutdown` uses `context.Background()` — no timeout, hangs on misbehaving clients
3. `runSubscriber` has no reconnect on Redis pub/sub failure — silently goes dark
4. `ts_ns` encoded as `float64` — 53-bit mantissa can't hold 63-bit nanosecond timestamps precisely
5. Level counts cast to `uint16` without bounds check — wraps silently over 65535 levels
6. `parseLevels` silently skips unparseable pairs — no log warning on data loss
7. `InjectType` round-trips through `map[string]any` — can alter float precision and field order
8. `lastSnap` grows without bound — symbols never evicted after all clients leave
9. Mixed endianness without justification — ts_ns/price/size big-endian; bid/ask counts little-endian
10. `WritePump` doesn't close channel on early exit — read loop has no teardown signal
11. `Subscribe` sends snapshot outside lock but snap slice shared — potential data race (snapshot replaced not mutated, so actually safe — but flag for review)
12. No protocol version/negotiation — breaking changes will silently corrupt clients

---

## Edge Case Hunter Findings (JSON)

```json
[
  {"location":"main.go:43-44","trigger_condition":"srv.Shutdown called with context.Background() which never times out","guard_snippet":"shutCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second); defer cancel(); _ = srv.Shutdown(shutCtx)","potential_consequence":"Shutdown hangs indefinitely if active connections never close"},
  {"location":"main.go:55-56","trigger_condition":"PSubscribe fails or Redis is unavailable at startup","guard_snippet":"if err := ps.Ping(ctx); err != nil { slog.Error(\"gateway: redis subscribe failed\", \"err\", err); return }","potential_consequence":"runSubscriber silently exits; no orderbook data ever reaches hub"},
  {"location":"main.go:58-64","trigger_condition":"ch closes due to Redis disconnect mid-run (ok==false path returns silently)","guard_snippet":"case msg, ok := <-ch: if !ok { slog.Warn(\"gateway: pubsub channel closed\"); return }","potential_consequence":"Subscriber exits without reconnecting; all clients stop receiving updates"},
  {"location":"main.go:80-81","trigger_condition":"data[0] is any byte other than 0x10 or 0x11","guard_snippet":"default: slog.Warn(\"gateway: unknown message type\", \"type\", data[0])","potential_consequence":"Unknown opcodes silently discarded; protocol violations undetected"},
  {"location":"main.go:75-76","trigger_condition":"symLen is 0, producing empty symbol string","guard_snippet":"if symLen == 0 { continue }","potential_consequence":"Empty string subscriptions pollute hub.bySymbol map"},
  {"location":"main.go:75-76","trigger_condition":"data[1] is 255, attacker-controlled symbol length","guard_snippet":"if symLen > maxSymbolLen { continue }","potential_consequence":"Oversized symbol allocated from attacker-controlled length byte"},
  {"location":"codec.go:39-41","trigger_condition":"len(bids) or len(asks) exceeds 65535, truncated by uint16 cast","guard_snippet":"if len(bids) > 0xFFFF || len(asks) > 0xFFFF { return nil, errors.New(\"level count exceeds uint16\") }","potential_consequence":"Receiver reads wrong bid/ask count; frame parsed with corrupt offsets"},
  {"location":"codec.go:33-35","trigger_condition":"price or size string is non-numeric (empty string from feed)","guard_snippet":"parseLevels already handles via continue; already handled","potential_consequence":"Already handled by parseLevels - dismiss"},
  {"location":"codec.go:37","trigger_condition":"TsNs stored as float64 bits but is int64 nanosecond timestamp","guard_snippet":"binary.BigEndian.PutUint64(buf[1:9], uint64(dp.TsNs))","potential_consequence":"Client decodes wrong timestamp value; all time-series data misaligned"},
  {"location":"hub.go:Subscribe","trigger_condition":"s.Send called after hub lock released; sender may be concurrently unregistered","guard_snippet":"check h.senders[s] still true inside lock before sending snap","potential_consequence":"Snapshot sent to already-closed sender"},
  {"location":"hub.go:routeOrderbook","trigger_condition":"Sender removed from h.senders between targets slice build and s.Send call","guard_snippet":"check h.senders[s] in loop before sending","potential_consequence":"Send on a closed sender causes panic or blocked goroutine"},
  {"location":"hub.go:routeOrderbook","trigger_condition":"lastSnap updated while late-subscribing client reads stale encoded bytes","guard_snippet":"copy snap bytes under lock before storing","potential_consequence":"Subscriber receives partially overwritten snapshot buffer"}
]
```

---

## Acceptance Auditor Findings

1. **AC 12 — WebSocket frame type ignored** — `conn.Read` discards message type (`_, data, err`); text frames with matching bytes accepted as binary control messages. `main.go:87`.
2. **AC 6 — `TestEncodeBinary_TsNsEncoding` masks ~128ns precision loss** — uses `InDelta(1.0)` tolerance; real ns timestamps lose ~128 ns through float64. Spec says float64, so spec is lossy, but test does not adequately validate.
3. **ts_ns float64 precision** — confirmed spec says float64; lossy for real timestamps but matches spec intent. Auditor flags as spec-level issue.

---

## Next Steps

1. **Triage** findings (step-03): classify as patch / defer / dismiss / decision_needed
2. **Present** findings (step-04): structured report to user
3. **Apply patches** to gateway files
4. **Mark 17-3 done**, move sprint-status to `done`
5. **Create story 17-4** (bot-service-orderbook-subscription)
6. **Dev story 17-4** → **code review 17-4**
7. **Create story 17-5** (frontend) → **dev** → **code review**
