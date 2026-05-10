# Story 15.4: Funding Rate Arb — Signal Function (BLOCKED)

Status: blocked

## Story

As mrqdt,
I want a funding rate arbitrage strategy that profits from positive funding by holding a spot long and perp short simultaneously,
so that I can capture funding payments as a low-directional-risk income stream.

## BLOCKED — Do Not Implement

**Prerequisite Gate:** `redis-cli XLEN funding:bybit:BTCUSDT` returns > 0 on the production Redis instance, confirming live funding rate events are flowing.

This story **cannot start** until:
1. The aggregator perpetuals feed epic is complete (FR37-42: funding rate, open interest, perp mark price, basis streams)
2. `funding:{exchange}:{symbol}` Redis stream entries are verifiable in Redis

The aggregator currently does **not** produce funding rate, open interest, perp mark price, or basis streams. FR37-42 are planned but not yet implemented.

## Acceptance Criteria

*(Preserved for future implementation — do not act on these until the prerequisite gate is met.)*

- **AC1:** Given `bot_service/strategy/signals/funding_arb.py` with `compute_funding_arb_signal(funding_rate: float, next_funding_ts: int, threshold: float) -> SignalResult`, when called, then it returns `buy` (open arb) when `funding_rate > threshold` and `time.time() < next_funding_ts - entry_buffer_s`; returns `sell` (close arb) when position is open and `time.time() >= next_funding_ts - exit_buffer_s`; returns `hold` otherwise.

- **AC2:** Given a `FundingRate` event with `next_funding_ts` older than 2× the funding interval (typically 16 hours for KuCoin/Bybit 8-hour funding), when `compute_funding_arb_signal` is called, then the event is rejected as stale; WARN is logged; `bot_funding_rate_stale_total{strategy}` is incremented; no signal is computed.

- **AC3:** Given `strategies/active/funding_arb_bot.py` with `FundingArbBot(BaseStrategy)`, when inspected, then it subscribes to `FundingRate` events; calls `compute_funding_arb_signal` on each event; posts spot `OrderRequest` then perp `OrderRequest` on open arb; posts reverse orders on close arb; aborts if spot close fails; logs CRITICAL and retries every 5s if perp close fails after spot close succeeds (half-unwound position).

- **AC4:** Given `_results/FundingArb/fee_impact.json` and `_results/FundingArb/validation_report.json`, when read, then both exist and show `passes=true`.

## Tasks / Subtasks

*(Not started — story is BLOCKED on aggregator perp feed prerequisite)*

- [ ] T1: Verify prerequisite gate — `redis-cli XLEN funding:bybit:BTCUSDT` returns > 0
- [ ] T2: Create `bot_service/strategy/signals/funding_arb.py` with `compute_funding_arb_signal` (AC1, AC2)
- [ ] T3: Create `strategies/active/funding_arb_bot.py` with `FundingArbBot(BaseStrategy)` (AC3)
  - [ ] T3.1: Implement unwind atomicity: spot-close first; abort on spot failure; CRITICAL log + 5s retry loop on perp failure
  - [ ] T3.2: Implement stale event rejection with `bot_funding_rate_stale_total` counter
- [ ] T4: Generate result files in `_results/FundingArb/` (AC4)
- [ ] T5: Write L1 unit tests — `compute_funding_arb_signal` edge cases; stale event rejection; unwind sequencing with mocked REST clients
- [ ] T6: Run full test suite — no regressions

## Dev Notes

### Why This Is Blocked

The aggregator WebSocket adapters for KuCoin and Bybit cover spot order book and trade feeds only. Perpetual futures feeds (funding rate, open interest, perp mark price, basis spread) are documented in the architecture as FR37-42 but not yet implemented. Without a running `funding:{exchange}:{symbol}` Redis stream, the `FundingArbBot` cannot receive `FundingRate` events.

### Signal Logic (for future reference)

```python
def compute_funding_arb_signal(
    funding_rate: float,
    next_funding_ts: int,
    threshold: float = 0.0001,  # 0.01% per 8h — minimum edge after fees
    entry_buffer_s: int = 600,  # enter 10min before funding
    exit_buffer_s: int = 60,    # exit 1min before funding
) -> SignalResult:
    now = int(time.time())
    funding_interval_s = 8 * 3600  # 8h standard for KuCoin/Bybit
    if now > next_funding_ts + funding_interval_s * 2:
        # Stale event — older than 2x funding interval
        return SignalResult(action="hold", confidence=0.0, reason="stale_funding_event")
    if funding_rate > threshold and now < next_funding_ts - entry_buffer_s:
        return SignalResult(action="buy", confidence=funding_rate / threshold, reason="funding_arb_open")
    if now >= next_funding_ts - exit_buffer_s:
        return SignalResult(action="sell", confidence=1.0, reason="funding_arb_close")
    return SignalResult(action="hold", confidence=0.0, reason="no_signal")
```

### Unwind Atomicity

When closing the arb position:
1. Post spot SELL (close long) — if fails, abort and alert; do NOT touch perp leg
2. If spot close fills → post perp BUY (close short)
3. If perp close fails after spot fills → log CRITICAL with both position sizes + current PnL; retry perp every 5s until filled or operator intervenes; never leave half-unwound silently

### Key Deferred Items (from epic analysis)

- Funding rate events older than 2× interval (16h) must be rejected as stale [Gap Q]
- Unwind must be atomic in intent: spot-first, perp-second; half-unwound state triggers CRITICAL alert and 5s retry loop until perp fills [Gap R]

## Dev Agent Record

### Agent Model Used

*Not started — story blocked*

### Completion Notes List

*None — story blocked on aggregator perpetuals feed prerequisite*

### File List

*None — no files created or modified*
