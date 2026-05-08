# Epic 8 Adversarial Review Findings
**Date:** 2026-05-08
**Scope:** Epic 8 spec — Multi-Timeframe Cascade & Redis Output
**Focus areas:** capacity-1 channel design, Accumulator HASH serialization, QuestDB reconstruction, blue-green promotion sequence, UTC boundaries, story carve

---

## Findings

1. **Capacity-1 channel silent data loss for slow consumers.** If a symbol's consumer goroutine takes > 1 second to drain its batch (high tick-rate burst), the capacity-1 channel is already at capacity when the next second's close signal fires. The ticker goroutine either blocks (stalling all subsequent close signals if one shared ticker) or drops the signal silently. Two missed closes means bars for those seconds are never written. The spec says "no mutex needed" but doesn't define what happens when the consumer falls behind — potential silent bar loss for high-tick-rate symbols is unaddressed.

2. **Accumulator HASH serialization: no spec on which fields to serialize.** The cascade `Accumulator` has ~60 state fields. The spec says "write accumulator state to Redis as a HASH" but defines neither the field set, the key names, the serialization format (JSON? individual HSET values?), nor how schema evolution is handled (a new field added in a future story produces a HASH with missing keys on the old running instance). A corrupt or partial HASH on restart produces silently wrong cascade bars with no detectability.

3. **QuestDB startup reconstruction for 1w bars is potentially catastrophic.** "Reconstruct in-progress bars from `snapshot_1s` rows since the last closed boundary." For a 1w bar, the last closed boundary is the previous Monday — up to 7 days back. At 200 symbols × 7 days × 86,400 s/day = ~121 million rows. The spec provides no pagination strategy, no memory budget, and no timeout. A Thursday restart attempts to load 4+ days of snapshot_1s into memory for reconstruction. This will either OOM the service or time out — neither outcome is handled.

4. **Reconstruction race with live tick processing.** The spec does not state whether the Redis consumer is paused during QuestDB startup reconstruction. If reconstruction takes 30 seconds and the consumer is already reading live ticks, both code paths write to the same in-memory cascade accumulators concurrently. This is an unguarded data race. The spec says nothing about a "reconstruction complete before consuming" gate.

5. **"Idle symbols skipped" is undefined.** The partial-publish cadence spec says "Idle symbols (no updates in the 250ms window) are skipped" but does not define "update." A symbol receiving OB-only ticks (no trade) that affect depth and mid-price features has "updates" by one definition and "idle" by another (no bar value change). Without a precise definition, implementations will make different choices and the story acceptance criteria cannot be verified.

6. **is_complete=true messages for 1w bars will be trimmed away within hours.** `CANDLE_STREAM_MAXLEN=10,000` is applied to all `candles:*` streams. Partial bar publishes fire every 250ms for every active symbol. For an active 1w symbol: 4 × 60 × 60 = 14,400 partial updates per hour. At MAXLEN=10,000, the stream retains only the last ~41 minutes of partial updates. The single `is_complete=true` close message written at Monday 00:00:00 UTC is trimmed out of the stream within 40 minutes by subsequent partial publishes. Any subscriber not reading at the exact close moment never sees the weekly completion event.

7. **Story boundary between 8-1 and 8-2 has no interface definition.** The 250ms partial bar publish requires both cascade accumulator state (owned by 8-1) and Redis stream writing (owned by 8-2). If 8-1 implements only the 1s-close cascade, 8-2 needs to call `CurrentBar()` on live (not yet closed) cascade accumulators at arbitrary times — no read interface is defined. Either story can own the 250ms ticker, but the seam between them is unresolved and creates a hidden dependency.

8. **OB feature snapshot data source is ambiguous.** "Publish to `ob_features:{exchange}:{symbol}` on each 1s bar close." The closed 1s `Bar` struct is available between `CurrentBar()` and `BarReset()`. After `BarReset()` those values are gone. The spec does not define whether the snapshot is taken from the closed Bar struct or the live OB state — these produce different values for depth features if read at the wrong point in the flush sequence.

9. **Weekly bar duplicate key: canonical key is unspecified.** Both `candles:{exchange}:{symbol}:1w` and `candles:1w:{exchange}:{symbol}` are trimmed to the same MAXLEN. The spec says "downstream must subscribe to only one" but never designates which key is canonical. Any bot choosing the "wrong" key silently receives no weekly close events after MAXLEN trimming. The spec should designate the canonical key and explain why the alias exists for 1w only and not 1d or 1h.

10. **Blue-green: SIGTERM drain sequence conflicts with pre-ACK design.** Epic 5's consumer uses "XACK before accumulator update" to prevent double-counting on crash. But the graceful shutdown sequence says the old slot "XACKs all pending messages" — meaning messages whose QuestDB write timed out at shutdown are ACKed and silently dropped. The pre-ACK design and the drain-on-shutdown design have conflicting failure semantics that are not reconciled.

11. **`shadow_lag=0` promotion gate is undefined for multi-symbol deployments.** Each symbol has its own Redis stream and its own `lastShadowID`. The `/health` endpoint has a single `shadow_lag` field. If it's the max across all symbols, one persistently backlogged symbol keeps `shadow_lag > 0` indefinitely and the deploy script's promotion gate never fires. The spec says nothing about aggregation strategy or how a permanently lagged symbol is handled.

12. **Cascade accumulator interface for nil 1s fields is unspecified.** Higher-timeframe bars aggregate closed 1s `Bar` structs. ~40 of 67 fields are nullable. The spec does not define how a 1m accumulator handles nil: does nil `TradeCount` mean 0 (additive fold) or skip (null propagation)? Fields like `TWAP` are already time-weighted aggregates — folding TWAP-of-TWAPs is not equivalent to TWAP of raw prices. The aggregation semantics for every field group need to be defined before 8-1 is implemented.

13. **`candle_cascade_state_write_failure_total` metric is in CS-FR28 but not wired anywhere.** CS-FR28 (implemented in Epic 5 story 7) lists `candle_cascade_state_write_failure_total{exchange,symbol}` as a required metric. Epic 5's story 7 was implemented before the cascade existed — this counter was either wired to nothing or omitted. Epic 8's Done when criteria never mention it. Redis HASH write failures will silently not increment this counter.
