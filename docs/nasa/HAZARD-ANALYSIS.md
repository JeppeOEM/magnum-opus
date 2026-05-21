# Hazard Analysis — Trading-specific Risks

This document enumerates hazards specific to running an automated trading system.
Hazards here are distinct from technical failures (see FMEA.md): they describe scenarios
where the system functions correctly but produces harmful trading outcomes.

Each hazard has: description, conditions that enable it, severity, detection, and prevention.

---

## H-01: Unintended orders from stale signals after a gap

**Description:** A strategy fires an order based on indicator values computed from
a rolling DataFrame that contains bars with `has_gap=True`. The signal may appear
valid (no NaN, sufficient lookback) but is derived from corrupted input data.

**Conditions enabling this hazard:**
1. A gap event occurs (order book or stream gap)
2. Strategy's `_signal_invalid` is NOT set (e.g., gap marker not delivered due to queue overflow)
3. Indicator values appear numerically valid but represent a discontinuous price path

**Severity:** P0 — real position opened based on invalid signal

**Detection:** Monitor `bot_queue_drops_total` — drops indicate GapMarkers may be lost.
Check `has_gap=True` bars in `snapshot_1m` via QuestDB query.

**Prevention:**
1. `BaseStrategy.handle_gap` sets `_signal_invalid[symbol] = True` and marks `has_gap=True`
   on the last rolling DF row
2. `register_bar_handler` lookback gate re-requires `min_lookback` clean bars before handlers fire
3. `BOT_QUEUE_MAX_DEPTH` should be set high enough that GapMarkers are not dropped
4. Strategy handlers should check `df.iloc[-1]["has_gap"]` as an additional guard before ordering

**Residual risk:** If the queue overflows and drops a GapMarker (queue_overflow GapMarker is injected, not the original one), the strategy sees a GapMarker with `symbol=""` which may not match the affected symbol. Mitigation: keep queue depth high relative to expected event rate.

---

## H-02: Position left open after strategy removal

**Description:** An operator removes a strategy file (hot-reload) or stops the bot-service
while the strategy holds an open position. The position continues to exist on the exchange
with no software managing it.

**Conditions enabling this hazard:**
1. Strategy has `close_on_bus_timeout=False` (or bus timeout hasn't elapsed yet)
2. Strategy is hot-reloaded out or process is stopped
3. No manual position closure before stopping

**Severity:** P0 — unmanaged live position

**Detection:** `reconciliation.py` detects positions not belonging to any active strategy
on next startup and logs `CRITICAL: reconciliation_critical`.

**Prevention:**
1. Always check open positions before stopping bot-service: query exchange position endpoint or check `bot_pnl_usd` gauges
2. Use `close_on_bus_timeout=True` for strategies that should not hold positions through service gaps
3. The strategy registry gracefully stops a strategy and its `_emergency_close_symbol` thread before removing it

**Recovery:** After restart, reconciliation surfaces unmanaged positions. Operator decision:
close via exchange UI or re-assign to a strategy.

---

## H-03: Double order submission on retry

**Description:** An order is submitted to the exchange, the network acknowledgment
is lost (timeout), and the bot-service retries — resulting in two identical orders executed.

**Conditions enabling this hazard:**
1. Exchange REST call times out without confirmation
2. Bot-service retry logic does not check for existing orders before re-submitting
3. Exchange accepts both submissions (no client-order-ID deduplication)

**Severity:** P0 — double position size

**Detection:** Check exchange order history for duplicate orders. `bot_order_fills_total`
spike vs. expected. `_open_positions` size larger than expected.

**Prevention:**
1. All order submissions include a `clientOrderId` derived from `strategy + symbol + timestamp-bucket`
2. Retry checks exchange for existing pending orders for the same `clientOrderId`
3. KuCoin and Bybit both support `clientOrderId` deduplication (exchange-side guard)

**Residual risk:** If `timestamp-bucket` granularity is too coarse, retries within the bucket may
collide. Use millisecond-resolution IDs.

---

## H-04: Emergency close during paper trading

**Description:** The bus timeout fires while paper trading is active. The emergency close
skips (`emergency_close_skipped_paper_trading`) but the log entry may alarm an operator
who incorrectly believes a live position is at risk.

**Conditions enabling this hazard:**
1. `paper_trading=True` but strategy has `close_on_bus_timeout=True` (misconfiguration)
2. Bus timeout elapses (network issue, candle-service down)

**Severity:** P3 — no actual financial risk; operator confusion only

**Detection:** `emergency_close_skipped_paper_trading` WARN log.

**Prevention:** Paper trading strategies should set `close_on_bus_timeout=False`.
Emergency close is a live-trading mechanism.

---

## H-05: NaN propagation from indicator into order size

**Description:** A pandas-ta indicator returns NaN for a specific bar (e.g., RSI during
warmup, ATR when insufficient data). The NaN is not caught by the NaN guard because the
indicator is applied to a column that the NaN guard does not check (non-last-row NaN).

**Conditions enabling this hazard:**
1. `add_indicators` appends a new indicator column where only the last row can be NaN
2. Strategy computes position size using a column that has NaN in earlier rows but not the last
3. pandas arithmetic with NaN produces NaN silently

**Severity:** P1 — order with NaN size is either rejected by exchange (protection) or
interpreted as 0 (no harm) or maximum (catastrophic, exchange-dependent)

**Detection:** `bot_nan_guard_total` increase. Order worker: `order_size_zero` WARN.

**Prevention:**
1. `register_bar_handler` NaN guard checks `df.iloc[-1:].isnull().any().any()` — covers all columns in last row
2. Strategy handlers should compute size with explicit `pd.isna()` checks
3. `add_indicators` must only append columns; never overwrite the last row with NaN

---

## H-06: Funding rate arbitrage strategy misidentifies direction

**Description:** The funding-rate arbitrage signal fires based on a poll that is
`bot_funding_poll_interval_s` seconds old. If funding rates change sign between polls,
the strategy may trade in the wrong direction.

**Conditions enabling this hazard:**
1. `bot_funding_poll_interval_s` is large (e.g., 60s) and funding rates are volatile
2. Funding rate flips sign between polls
3. Strategy does not re-validate direction before submitting order

**Severity:** P1 — wrong-direction trade with funding rate against the position

**Detection:** Compare `FundingRate.rate` sign in logs with exchange funding rate dashboard.

**Prevention:**
1. Reduce `bot_funding_poll_interval_s` for volatile markets (default 60s is for stable markets)
2. Strategy should check funding rate sign at order submission time, not just at signal generation
3. Set a minimum rate threshold to avoid triggering on near-zero funding rates

---

## H-07: Redis maxmemory eviction silently drops ticks

**Description:** Redis is configured with `maxmemory-policy=allkeys-lru` (or similar).
Under memory pressure, Redis evicts stream entries from tick streams before candle-service
consumes them. Candle-service sees no error — XREADGROUP simply returns fewer messages.

**Conditions enabling this hazard:**
1. Redis is configured with an eviction policy (NOT `noeviction`)
2. Tick stream grows faster than candle-service processes
3. Candle-service doesn't detect the missing messages (no seq number checking in streams)

**Severity:** P1 — silent bar data loss with `gap_count=0` (no gap marker injected)

**Detection:** Monitor Redis `INFO memory` → `used_memory` vs `maxmemory`. Check `xlen ticks:*` keys.
Candle-service `stream_overflow` gap is only emitted when len > 45,000 on first connect — not during eviction.

**Prevention:** Configure Redis with `maxmemory-policy=noeviction`. Let Redis reject writes
and let the aggregator log errors rather than silently lose data. Stream length is controlled
by `REDIS_STREAM_MAXLEN`.

---

## H-08: Clock skew between services causes bar misalignment

**Description:** If the VPS or Docker host clock drifts, `ts` values in QuestDB may be
misaligned between aggregator (writes tick timestamps from exchange) and candle-service
(writes bar close timestamps from host clock). Bars may appear to be in the wrong second.

**Conditions enabling this hazard:**
1. NTP is not running or clock drift exceeds 500ms
2. Candle-service bar close timestamp (`realClock.Now()`) differs from the exchange-reported tick timestamps

**Severity:** P2 — bar data is correct but timestamps are misaligned

**Detection:** Check `ts` consistency in QuestDB: `SELECT min(ts), max(ts) FROM snapshot_1s WHERE ts > now()-60s`.
Bars should align to whole seconds.

**Prevention:** Ensure NTP (`chronyd` or `systemd-timesyncd`) is running on all hosts.
Monitor clock offset via `timedatectl timesync-status`.
