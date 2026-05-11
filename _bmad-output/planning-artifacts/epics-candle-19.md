---
stepsCompleted: [1, 2]
inputDocuments:
  - "_bmad-output/brainstorming/brainstorming-session-2026-05-11-0000.md"
  - "candle-service/internal/accumulator/accumulator.go"
  - "candle-service/internal/writer/redis/publisher.go"
  - "candle-service/internal/flusher/row.go"
  - "candle-service/project-context.md"
---

# magnum-opus — Epic 19: Candle Service Footprint + Signals

## Overview

Adds ~17 new fields to the candle service: footprint data (per-price buy/sell vol), Value Area, imbalance signals, unfinished auction, absorption, divergence, and iceberg detection. All fields written to Redis candle stream and QuestDB `snapshot_1s`. No aggregator changes. Must come before Epic 18 (dashboard) so the dashboard has full live data to test against.

**Architecture rules (from candle-service/project-context.md that apply here):**
- All new computation goes in `accumulator/` (pure, zero IO) or `features/` (pure functions) — never in `consumer/` or `cmd/`
- New accumulator fields follow `BarReset()` pattern: cleared on each bar boundary, carried if intended to survive bar
- New Redis fields go in `barFields()` in `internal/writer/redis/publisher.go`
- New QuestDB columns go as `ALTER TABLE ADD COLUMN` migrations in `candle-service/migrations/`
- New `Snapshot1sRow` fields go in `internal/flusher/row.go`
- New QuestDB ILP writes go in `internal/writer/questdb/writer.go`
- L1 tests required for all pure computation; L2 tests for Redis/QuestDB write paths
- `time.Now()` banned in `internal/`; all new pure functions take no Clock
- Prices remain strings in Redis; floats in QuestDB ILP

---

## Field Inventory

### Already in QuestDB but NOT in Redis candle stream
- `buy_volume` — in `accumulator.Bar.BuyVolume`, written to QuestDB, missing from `barFields`. Add in 19.1.

### New fields (all stories)

| Field | Type | Story | Source |
|---|---|---|---|
| `sell_volume` | float64 | 19.1 | `volume - buy_volume` |
| `footprint_json` | string (JSON) | 19.1 | `map[price]{buyVol,sellVol}` |
| `poc_price` | float64 | 19.2 | footprint |
| `value_area_high` | float64 | 19.2 | footprint |
| `value_area_low` | float64 | 19.2 | footprint |
| `poc_volume` | float64 | 19.2 | footprint |
| `imbalance_buy_count` | int | 19.3 | footprint |
| `imbalance_sell_count` | int | 19.3 | footprint |
| `imbalance_stack_buy` | int | 19.3 | footprint |
| `imbalance_stack_sell` | int | 19.3 | footprint |
| `imbalance_ratio` | float64 | 19.3 | footprint |
| `single_print_count` | int | 19.4 | footprint + OHLCV |
| `single_print_levels_json` | string (JSON) | 19.4 | footprint + OHLCV |
| `unfinished_top` | bool | 19.4 | footprint + High |
| `unfinished_bottom` | bool | 19.4 | footprint + Low |
| `absorption_detected` | bool | 19.4 | OHLCV + footprint |
| `footprint_delta_divergence` | int8 (−1/0/1) | 19.5 | footprint + OHLCV |
| `cum_delta` | float64 | 19.5 | running sum across bars (accWriter) |
| `cvd_divergence` | int8 (−1/0/1) | 19.5 | cum_delta + close (accWriter) |
| `iceberg_bid_detected` | bool | 19.5 | depth open/close + buy_volume |
| `iceberg_ask_detected` | bool | 19.5 | depth open/close + sell_volume |
| `iceberg_price` | float64 | 19.5 | best_bid or best_ask |

---

## Epic List

### Epic 19: Candle Service Footprint + Signals
Enriches the 1s candle stream and QuestDB table with footprint data and microstructure signals required by the Epic 18 dashboard and future bot strategies.
**Fields:** all 22 new fields above.

---

## Epic 19: Candle Service Footprint + Signals

### Story 19.1: Footprint Accumulator + Redis Stream Enrichment

As a developer,
I want the candle service to accumulate per-price buy/sell volume and publish it to the Redis candle stream alongside buy_volume and sell_volume,
So that the dashboard and downstream consumers have the raw footprint data required for all signal computations.

**Acceptance Criteria:**

**Given** a trade tick with `side="buy"`, `price="67000.5"`, `size="0.1"`,
**When** `acc.Apply()` processes it,
**Then** `acc.footprintMap["67000.5"].BuyVol += 0.1`; `SellVol` unchanged.

**Given** a trade tick with `side="sell"`, `price="67000.5"`, `size="0.2"`,
**When** `acc.Apply()` processes it,
**Then** `acc.footprintMap["67000.5"].SellVol += 0.2`; `BuyVol` unchanged.

**Given** bar flush via `acc.BarReset()`,
**When** called,
**Then** `footprintMap` is reset to empty map (not nil — never allocate on each Apply); all `FootprintJSON`, `BuyVolume` fields on the returned `Bar` are populated from the bar's state before reset.

**Given** `acc.CurrentBar()` returns a `Bar` with trades,
**When** `barFields(bar, true)` is called in `publisher.go`,
**Then** the resulting map contains:
- `"buy_volume"`: `bar.BuyVolume` serialized as float string (non-nil guard: omit if nil)
- `"sell_volume"`: `bar.Volume - bar.BuyVolume` serialized as float string (omit if either nil)
- `"footprint_json"`: valid JSON string, e.g. `{"67000.5":{"b":0.1,"s":0.2},"67001.0":{"b":0.5,"s":0.0}}`; key is price string; value keys are `"b"` (buy) and `"s"` (sell) for compactness

**Given** a bar with zero trades,
**When** `barFields` serializes it,
**Then** `"buy_volume"`, `"sell_volume"`, and `"footprint_json"` are absent from the map (omit, not empty string).

**Given** the QuestDB migration,
**When** the candle service starts,
**Then** migration file `candle-service/migrations/NNN_footprint.sql` runs and adds columns `footprint_json VARCHAR` to `snapshot_1s` without error; migration is idempotent (re-running is a no-op).

**Given** `internal/writer/questdb/writer.go`,
**When** writing a bar with non-nil `FootprintJSON`,
**Then** `footprint_json` column is written via `.StringColumn("footprint_json", *bar.FootprintJSON)`.

**Given** L1 tests in `accumulator/accumulator_test.go`,
**When** run with `go test ./...`,
**Then** tests cover: (a) buy/sell accumulation across multiple trades at same price, (b) BarReset clears map, (c) zero-trade bar has nil FootprintJSON, (d) JSON format matches expected schema.

---

### Story 19.2: Value Area (Signal Group C)

As a developer,
I want the candle service to compute POC and Value Area from the footprint map on each bar close,
So that dashboard and bot consumers have `poc_price`, `value_area_high`, `value_area_low`, `poc_volume` per second.

**Pre-conditions:** Story 19.1 complete (footprint accumulator + `FootprintJSON` on `Bar`).

**Acceptance Criteria:**

**Given** a pure function `features.ComputeValueArea(footprint map[string]FootprintCell) (pocPrice, vahPrice, valPrice, pocVolume float64, ok bool)`,
**When** called with a non-empty footprint map,
**Then**:
- `pocPrice` = price string (parsed to float64) of the cell with highest `buyVol + sellVol`
- `pocVolume` = total volume at POC price (`buyVol + sellVol`)
- Value Area = set of adjacent price levels containing ≥ 70% of total footprint volume, expanding outward from POC (highest total first); `vahPrice` = highest price in Value Area; `valPrice` = lowest
- Prices compared numerically (parse before compare); if two levels tie on volume, use higher price as POC

**Given** a footprint map with a single price level,
**When** `ComputeValueArea` is called,
**Then** `pocPrice = vahPrice = valPrice` = that price; `pocVolume` = total vol; `ok = true`.

**Given** an empty footprint map (zero-trade bar),
**When** `ComputeValueArea` is called,
**Then** `ok = false`; caller must not write nil-guarded fields.

**Given** `accumulator.Bar` after `CurrentBar()`,
**When** `FootprintMap` is non-empty,
**Then** `Bar.POCPrice`, `Bar.ValueAreaHigh`, `Bar.ValueAreaLow`, `Bar.POCVolume` are non-nil and set from `ComputeValueArea`.

**Given** `barFields` in `publisher.go`,
**When** `bar.POCPrice != nil`,
**Then** `"poc_price"`, `"value_area_high"`, `"value_area_low"`, `"poc_volume"` are present in the Redis stream field map as float strings.

**Given** the QuestDB migration,
**When** the service starts,
**Then** columns `poc_price DOUBLE`, `value_area_high DOUBLE`, `value_area_low DOUBLE`, `poc_volume DOUBLE` exist in `snapshot_1s`.

**Given** L1 tests in `features/`,
**When** run,
**Then** tests cover: standard distribution, uniform distribution, single-level, two-level, 70% boundary precision, tied-volume tie-breaking.

---

### Story 19.3: Imbalance Signals (Signal Group A)

As a developer,
I want the candle service to emit per-bar imbalance signal fields from the footprint map,
So that the dashboard and bots can identify price levels where aggressive buying or selling dominates.

**Pre-conditions:** Story 19.1 complete.

**Imbalance definition:** a footprint cell is buy-imbalanced if `buyVol > 3 × sellVol` (and `sellVol > 0`); sell-imbalanced if `sellVol > 3 × buyVol` (and `buyVol > 0`). Cells with zero on either side are excluded from imbalance counts (one-sided volume is a different signal).

**Acceptance Criteria:**

**Given** a pure function `features.ComputeImbalance(footprint map[string]FootprintCell, sortedPrices []float64) ImbalanceSignals`,
**When** called,
**Then**:
- `ImbalanceBuyCount` = number of cells where `buyVol > 3 × sellVol` (and `sellVol > 0`)
- `ImbalanceSellCount` = number of cells where `sellVol > 3 × buyVol` (and `buyVol > 0`)
- `ImbalanceStackBuy` = length of longest consecutive run of buy-imbalanced cells (sorted by price ascending)
- `ImbalanceStackSell` = length of longest consecutive run of sell-imbalanced cells
- `ImbalanceRatio` = `(ImbalanceBuyCount - ImbalanceSellCount) / len(footprint)` — range [−1, 1]; 0 if footprint empty

**Given** a footprint with no imbalanced cells,
**When** `ComputeImbalance` is called,
**Then** all counts are 0; `ImbalanceRatio = 0.0`.

**Given** a zero-trade bar,
**When** `ComputeImbalance` is called with an empty footprint,
**Then** all fields are nil on the `Bar`; not written to Redis or QuestDB.

**Given** `barFields` in `publisher.go`,
**When** imbalance fields are non-nil,
**Then** `"imbalance_buy_count"`, `"imbalance_sell_count"`, `"imbalance_stack_buy"`, `"imbalance_stack_sell"`, `"imbalance_ratio"` appear in the Redis field map.

**Given** QuestDB migration,
**When** applied,
**Then** `snapshot_1s` gains columns: `imbalance_buy_count INT`, `imbalance_sell_count INT`, `imbalance_stack_buy INT`, `imbalance_stack_sell INT`, `imbalance_ratio DOUBLE`.

**Given** L1 tests in `features/`,
**When** run,
**Then** tests cover: all buy-imbalanced, all sell-imbalanced, mixed, consecutive stack detection, ratio bounds, empty footprint.

---

### Story 19.4: Unfinished Auction + Single Prints + Absorption

As a developer,
I want the candle service to emit unfinished auction, single print, and absorption fields per bar,
So that the dashboard can mark unfilled price levels and absorption candles directly from candle stream data.

**Pre-conditions:** Story 19.1 complete (footprint + OHLCV fields on `Bar`).

**Definitions:**
- **Single print**: a price level within `[bar.Low, bar.High]` where total footprint volume (`buyVol + sellVol`) is < 10% of the mean per-level volume for that bar. Indicates price moved through quickly with no two-way trading.
- **Unfinished top**: bar's High price level has `sellVol == 0` (only buyers present at the extreme — auction unfinished, price may return).
- **Unfinished bottom**: bar's Low price level has `buyVol == 0` (only sellers at extreme).
- **Absorption**: `trade_count >= 5` AND `buy_volume / volume > 0.6` or `buy_volume / volume < 0.4` (strong directional skew) AND `abs(close - open) / close < 0.0005` (price barely moved despite skewed volume — opposing side absorbed all pressure).

**Acceptance Criteria:**

**Given** a pure function `features.ComputeAuctionSignals(footprint, high, low float64) AuctionSignals`,
**When** called with a footprint spanning [low, high],
**Then**:
- `SinglePrintCount` = count of price levels in [low, high] present in footprint with volume < 10% of mean level volume
- `SinglePrintLevelsJSON` = JSON array of those price strings, sorted ascending; `"[]"` if none
- `UnfinishedTop` = true if the footprint entry at `high` has `SellVol == 0` (entry must exist; if high not in footprint, `UnfinishedTop = false`)
- `UnfinishedBottom` = true if the footprint entry at `low` has `BuyVol == 0`

**Given** a pure function `features.DetectAbsorption(buyVol, totalVol, open, close float64, tradeCount int) bool`,
**When** called,
**Then** returns true iff `tradeCount >= 5` AND (`buyVol/totalVol > 0.6` OR `buyVol/totalVol < 0.4`) AND `math.Abs(close-open)/close < 0.0005`; returns false for zero `totalVol`.

**Given** a zero-trade bar,
**When** computing auction signals,
**Then** `SinglePrintCount = 0`, `SinglePrintLevelsJSON` is nil, `UnfinishedTop = false`, `UnfinishedBottom = false`, `AbsorptionDetected = false`; all nil-guarded fields omitted from Redis and QuestDB.

**Given** `barFields` in `publisher.go`,
**When** fields are non-nil/non-false (write booleans as `"true"`/`"false"` strings only when true — omit false),
**Then** `"single_print_count"`, `"single_print_levels_json"`, `"unfinished_top"`, `"unfinished_bottom"`, `"absorption_detected"` appear in the Redis stream map.

**Given** QuestDB migration,
**When** applied,
**Then** `snapshot_1s` gains: `single_print_count INT`, `single_print_levels_json VARCHAR`, `unfinished_top BOOLEAN`, `unfinished_bottom BOOLEAN`, `absorption_detected BOOLEAN`.

**Given** L1 tests,
**When** run,
**Then** tests cover: single print detection with mean threshold, unfinished top/bottom edge cases (price at extreme has zero on one side), absorption boundary conditions (tradeCount=4 → false, skew=0.59 → false, skew=0.61 → true).

---

### Story 19.5: Footprint Divergence + CVD Divergence + Iceberg Detection

As a developer,
I want the candle service to emit per-bar divergence and iceberg fields,
So that the dashboard can render divergence markers and iceberg overlays, and bots can act on these signals.

**Pre-conditions:** Stories 19.1, 19.2 complete (footprint + OHLCV on `Bar`).

**Definitions:**
- **`footprint_delta_divergence`**: `1` (bearish) if `close > open` AND `net_delta < 0`; `-1` (bullish) if `close < open` AND `net_delta > 0`; `0` otherwise. `net_delta = buy_volume - sell_volume`. Per-bar, pure.
- **`cum_delta`**: running cumulative delta maintained by `accWriter` in `cmd/candle/`. `cum_delta += (buy_volume - sell_volume)` on each bar close. Persisted to Redis HASH alongside cascade accumulator state. Reset to 0 on `accumulator.Reset()` (gap event).
- **`cvd_divergence`**: `1` (bearish) if this bar's `close > prev_close` AND `cum_delta < prev_cum_delta`; `-1` (bullish) if `close < prev_close` AND `cum_delta > prev_cum_delta`; `0` otherwise. `accWriter` tracks `prevClose` and `prevCumDelta` per symbol.
- **`iceberg_bid_detected`**: true if `bid_depth_l1_close >= bid_depth_l1_open × 0.8` AND `buy_volume > 0` AND `bid_order_arrivals > 0`. Meaning: the best bid level absorbed buy pressure and the size replenished.
- **`iceberg_ask_detected`**: true if `ask_depth_l1_close >= ask_depth_l1_open × 0.8` AND `sell_volume > 0` AND `ask_order_arrivals > 0`.
- **`iceberg_price`**: `best_bid` if `iceberg_bid_detected`; `best_ask` if `iceberg_ask_detected`; `best_bid` if both.

**Acceptance Criteria:**

**Given** a pure function `features.FootprintDeltaDivergence(open, close, buyVol, sellVol *float64) int8`,
**When** called with an up-candle (`close > open`) and net sell delta (`buyVol < sellVol`),
**Then** returns `1` (bearish divergence).
**When** called with a down-candle (`close < open`) and net buy delta (`buyVol > sellVol`),
**Then** returns `-1` (bullish divergence).
**When** either is nil or no divergence,
**Then** returns `0`.

**Given** `accWriter` per-symbol state,
**When** bar closes with `buy_volume` and `sell_volume` non-nil,
**Then** `cumDelta += buyVol - sellVol`; `bar.CumDelta = ptr(cumDelta)`.

**Given** `accWriter` per-symbol state after a gap event (`accumulator.Reset()`),
**When** gap is processed,
**Then** `cumDelta = 0`; `prevClose = nil`; `prevCumDelta = 0` — CVD state reset.

**Given** `accWriter` computes `cvd_divergence`,
**When** `prevClose` and `prevCumDelta` are set and current bar has close and cumDelta,
**Then** `bar.CVDDivergence` is set per the definition above.

**Given** iceberg detection in `accumulator.CurrentBar()`,
**When** `bid_depth_l1_close >= bid_depth_l1_open × 0.8` AND `BuyVolume > 0` AND `BidOrderArrivals > 0`,
**Then** `bar.IcebergBidDetected = true`; `bar.IcebergPrice = bar.BestBid`.

**Given** `barFields` in `publisher.go`,
**When** divergence/iceberg fields are set,
**Then** `"footprint_delta_divergence"`, `"cum_delta"`, `"cvd_divergence"`, `"iceberg_bid_detected"`, `"iceberg_ask_detected"`, `"iceberg_price"` appear in the Redis stream map with correct values.

**Given** QuestDB migration,
**When** applied,
**Then** `snapshot_1s` gains: `footprint_delta_divergence BYTE`, `cum_delta DOUBLE`, `cvd_divergence BYTE`, `iceberg_bid_detected BOOLEAN`, `iceberg_ask_detected BOOLEAN`, `iceberg_price DOUBLE`.

**Given** `cum_delta` persistence (like cascade accumulator state),
**When** the service restarts,
**Then** `cumDelta` per symbol is restored from `candle:acc:{exchange}:{symbol}:cum_delta` Redis key (simple STRING, not part of the cascade HASH — separate key per symbol to avoid changing the cascade HASH schema).

**Given** L1 tests,
**When** run,
**Then** tests cover: all four divergence cases + no-divergence, gap resets cumDelta, iceberg boundary (0.79× → false, 0.80× → true), iceberg_price selection when both detected.
