# Story 19.5: Divergence + CVD + Iceberg (Group D)

Status: in-progress

## Story

As a developer,
I want the candle service to emit per-bar divergence and iceberg fields,
So that the dashboard can render divergence markers and iceberg overlays, and bots can act on these signals.

**Pre-conditions:** Stories 19.1, 19.2 complete (footprint + OHLCV on Bar).

## Definitions

- **`footprint_delta_divergence`**: `1` (bearish) if `close > open` AND `net_delta < 0`; `-1` (bullish) if `close < open` AND `net_delta > 0`; `0` otherwise. `net_delta = buy_volume - sell_volume`. Per-bar, pure.
- **`cum_delta`**: running cumulative delta maintained by `accWriter`. `cum_delta += (buy_volume - sell_volume)` on each bar close. Persisted to Redis. Reset to 0 on gap event.
- **`cvd_divergence`**: `1` (bearish) if `close > prev_close` AND `cum_delta < prev_cum_delta`; `-1` (bullish) if `close < prev_close` AND `cum_delta > prev_cum_delta`; `0` otherwise.
- **`iceberg_bid_detected`**: `bid_depth_l1_close >= bid_depth_l1_open × 0.8` AND `buy_volume > 0` AND `bid_order_arrivals > 0`.
- **`iceberg_ask_detected`**: `ask_depth_l1_close >= ask_depth_l1_open × 0.8` AND `sell_volume > 0` AND `ask_order_arrivals > 0`.
- **`iceberg_price`**: `best_bid` if iceberg bid; `best_ask` if iceberg ask; `best_bid` if both.

## Acceptance Criteria

### AC 1 — FootprintDeltaDivergence pure function

1. `features.FootprintDeltaDivergence(open, close, buyVol, sellVol *float64) int`
2. Returns 1 when close > open AND buyVol < sellVol (net delta negative).
3. Returns -1 when close < open AND buyVol > sellVol (net delta positive).
4. Returns 0 when any arg is nil, or no divergence condition is met.

### AC 2 — ComputeIceberg pure function

5. `features.ComputeIceberg(bidDepthClose, bidDepthOpen, askDepthClose, askDepthOpen, buyVol, sellVol *float64, bidArrivals, askArrivals *int, bestBid, bestAsk *float64) (iceBid, iceAsk bool, icePrice *float64)`
6. `iceBid` = true iff `bidDepthOpen != nil && *bidDepthOpen > 0 && *bidDepthClose >= *bidDepthOpen*0.8 && buyVol != nil && *buyVol > 0 && bidArrivals != nil && *bidArrivals > 0`.
7. `iceAsk` similarly for ask side.
8. `icePrice` = ptr(*bestBid) when iceBid; ptr(*bestAsk) when iceAsk only; ptr(*bestBid) when both; nil when neither.

### AC 3 — New Bar fields

9. `accumulator.Bar` gains 6 new fields after `AbsorptionDetected *bool`:
   ```go
   FootprintDeltaDivergence *int
   CumDelta                 *float64
   CVDDivergence            *int
   IcebergBidDetected       *bool
   IcebergAskDetected       *bool
   IcebergPrice             *float64
   ```
10. In `CurrentBar()`, inside `if a.tradeCount > 0`: set `FootprintDeltaDivergence` via `features.FootprintDeltaDivergence(bar.Open, bar.Close, bar.BuyVolume, bar.SellVolume)`.
11. At end of `CurrentBar()` (after all depth/OB fields set), inside `if a.tradeCount > 0`: compute iceberg and set the 3 iceberg fields.
12. `CumDelta` and `CVDDivergence` are nil from `CurrentBar()` — set by `accWriter` after.

### AC 4 — accWriter cumDelta state

13. `accWriter` gains fields: `cumDelta float64`, `prevClose *float64`, `prevCumDelta float64`.
14. In `Flush(!isPartial)` when `bar.BuyVolume != nil && bar.SellVolume != nil`:
    - `aw.cumDelta += *bar.BuyVolume - *bar.SellVolume`
    - `bar.CumDelta = ptr(aw.cumDelta)`
    - If `bar.Close != nil && aw.prevClose != nil`: compute and set `bar.CVDDivergence`.
    - Update `aw.prevCumDelta = aw.cumDelta`; `aw.prevClose = bar.Close`.
15. In `Reset()`: `aw.cumDelta = 0; aw.prevCumDelta = 0; aw.prevClose = nil`.

### AC 5 — cumDelta Redis persistence

16. `candle:acc:{exchange}:{symbol}:cum_delta` STRING key stores current cumDelta as float string.
17. Written on each full bar close (after cumDelta update).
18. Restored on startup (Phase 2c) before consumer goroutines start.

### AC 6 — Pubsub publisher

19. After auction block: `FootprintDeltaDivergence` and `CVDDivergence` emitted as integers (always when non-nil). `CumDelta` emitted as float. Iceberg booleans use omit-false pattern. `IcebergPrice` gated on non-nil.

### AC 7 — QuestDB migration

20. `candle-service/migrations/007_divergence.sql`:
    ```sql
    ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS footprint_delta_divergence BYTE;
    ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS cum_delta DOUBLE;
    ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS cvd_divergence BYTE;
    ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS iceberg_bid_detected BOOLEAN;
    ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS iceberg_ask_detected BOOLEAN;
    ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS iceberg_price DOUBLE;
    ```

### AC 8 — QuestDB ILP write

21. In `writeBar()`, after auction block, gated on `bar.FootprintDeltaDivergence != nil`:
    - `Int64Column("footprint_delta_divergence", ...)`, `Float64Column("cum_delta", ...)`, `Int64Column("cvd_divergence", ...)`.
    - `BoolColumn("iceberg_bid_detected", ...)`, `BoolColumn("iceberg_ask_detected", ...)`.
    - `Float64Column("iceberg_price", ...)` gated on `bar.IcebergPrice != nil`.

### AC 9 — Flusher

22. `flusher/row.go` gains after auction fields: 6 parquet fields.
23. `flusher/flusher.go` gains 6 parse lines.

### AC 10 — L1 tests

24. `candle-service/internal/features/divergence_test.go` covers:
    - `FootprintDeltaDivergence`: bearish (close>open, net sell), bullish (close<open, net buy), neutral (no divergence), nil args → 0.
    - `ComputeIceberg`: bid iceberg (0.80×), bid not iceberg (0.79×), ask iceberg, both → bid price, neither.

## Tasks / Subtasks

- [ ] Task 1: Pure functions
  - [ ] 1.1 Create `features/divergence.go`
  - [ ] 1.2 Create `features/divergence_test.go` with AC 10 tests
  - [ ] 1.3 Tests pass

- [ ] Task 2: Bar fields + CurrentBar wiring
  - [ ] 2.1 Add 6 Bar fields to accumulator.go
  - [ ] 2.2 Wire FootprintDeltaDivergence in `if tradeCount > 0` block
  - [ ] 2.3 Wire iceberg at end of CurrentBar()

- [ ] Task 3: accWriter cumDelta state
  - [ ] 3.1 Add cumDelta/prevClose/prevCumDelta to accWriter
  - [ ] 3.2 Wire in Flush()
  - [ ] 3.3 Wire in Reset()
  - [ ] 3.4 Redis persistence (write per bar, restore on startup Phase 2c)

- [ ] Task 4: Write paths
  - [ ] 4.1 Pubsub publisher
  - [ ] 4.2 Migration 007_divergence.sql
  - [ ] 4.3 QuestDB writer
  - [ ] 4.4 flusher/row.go + flusher/flusher.go

- [ ] Task 5: Full test suite passes

## Dev Notes

### FootprintDeltaDivergence signal semantics
- net_delta = buyVol - sellVol
- Bearish: price moved UP but sellers net-dominated → unconfirmed move, potential reversal
- Bullish: price moved DOWN but buyers net-dominated → unconfirmed move, potential reversal

### Iceberg: bidDepthOpen guard
Must check `*bidDepthOpen > 0` to prevent division-by-zero and false positives on zero-depth bars.

### accWriter: CumDelta nil for zero-trade bars
On bars with no trades, cumDelta is not updated and `bar.CumDelta` remains nil. `prevClose` is also not updated on nil-Close bars.

### QuestDB BYTE type
BYTE stores -128..127. Int64Column in ILP correctly coerces to BYTE. Values for divergence fields are -1, 0, 1.

### flusher parseInt for divergence
Use existing `parseInt32` helper — values -1/0/1 fit in int32.

### File changes summary

| File | Change |
|---|---|
| `features/divergence.go` | NEW |
| `features/divergence_test.go` | NEW |
| `accumulator/accumulator.go` | Add 6 Bar fields; wire in CurrentBar |
| `cmd/candle/main.go` | Add cumDelta state to accWriter; Phase 2c restore |
| `writer/pubsub/publisher.go` | Add 6 fields |
| `migrations/007_divergence.sql` | NEW |
| `writer/questdb/writer.go` | Add 6 ILP writes |
| `flusher/row.go` | Add 6 parquet fields |
| `flusher/flusher.go` | Add 6 parse lines |

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

### File List
