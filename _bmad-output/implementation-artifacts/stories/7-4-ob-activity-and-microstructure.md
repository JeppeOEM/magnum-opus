# Story 7.4: OB Activity and Trade Microstructure

Status: done

## Story

As a developer,
I want OB activity counters (CS-FR15) and trade microstructure metrics (CS-FR16) computed per second and written to `snapshot_1s`,
so that `bid/ask_order_arrivals`, `bid/ask_cancel_count`, `ob_modify_count`, `avg_bid/ask_order_size`, `best_bid/ask_changes`, `quote_stuff_ratio`, `trade_sign_autocorr`, `inter_trade_interval_std_ms`, and `num_trade_price_levels` are populated.

## Acceptance Criteria

### Prerequisite: `HasLevel` on BookApplier and OrderBook (required for OBEventKind derivation)

1. `orderbook.OrderBook` gains `HasLevel(side, price string) bool`:
   ```go
   func (ob *OrderBook) HasLevel(side, price string) bool {
       if side == "buy" {
           _, ok := ob.bids[price]
           return ok
       }
       _, ok := ob.asks[price]
       return ok
   }
   ```
   Returns false if not in warm state (pre-snapshot levels are cleared on snapshot).

2. `consumer.BookApplier` interface gains:
   ```go
   HasLevel(side, price string) bool
   ```
   `OBAdapter` in `internal/consumer/obadapter.go` implements it by forwarding to `ob.HasLevel(side, price)`.
   `stubBook` in `consumer_l2_test.go` gets `HasLevel(side, price string) bool { return false }`.

### OBEventKind and AccumulatorApplier.ApplyOBEvent (CS-FR15 prerequisite)

3. Add `OBEventKind` and constants to `internal/consumer/consumer.go`:
   ```go
   type OBEventKind int8
   const (
       OBEventNone   OBEventKind = 0 // trade tick — never passed to ApplyOBEvent
       OBEventAdd    OBEventKind = 1 // new price level (size != "0", level not in book)
       OBEventCancel OBEventKind = 2 // removed level (size == "0")
       OBEventModify OBEventKind = 3 // existing level, size changed
   )
   ```

4. `AccumulatorApplier` interface gains:
   ```go
   ApplyOBEvent(kind OBEventKind, side string, parsedSize float64)
   ```
   `stubAcc` in `consumer_l2_test.go` gets a no-op implementation.

5. Consumer `handleMessage()` derives `OBEventKind` BEFORE `book.ApplyTick()` for non-trade ticks:
   ```go
   case EventTick:
       prevBid, prevBidSz, prevAsk, prevAskSz := c.book.BestQuote()
       isTrade := parsed.Tick.Level == 0
       var obKind OBEventKind
       if !isTrade {
           if parsed.Tick.Size == "0" {
               obKind = OBEventCancel
           } else if c.book.HasLevel(parsed.Tick.Side, parsed.Tick.Price) {
               obKind = OBEventModify
           } else {
               obKind = OBEventAdd
           }
       }
       c.book.ApplyTick(*parsed.Tick)
       currBid, currBidSz, currAsk, currAskSz := c.book.BestQuote()
       c.acc.Apply(parsed.Tick.Price, parsed.Tick.Size, isTrade, parsed.Tick.Side, parsed.Tick.TsMs,
           prevBid, prevBidSz, prevAsk, prevAskSz,
           currBid, currBidSz, currAsk, currAskSz)
       if !isTrade {
           parsedSize, _ := strconv.ParseFloat(parsed.Tick.Size, 64)
           c.acc.ApplyOBEvent(obKind, parsed.Tick.Side, parsedSize)
       }
       c.tickCount++
   ```
   Note: `HasLevel` is called BEFORE `ApplyTick` so the pre-update state determines the kind.

6. `accWriter.ApplyOBEvent(kind consumer.OBEventKind, side string, parsedSize float64)` in `cmd/candle/main.go`:
   ```go
   func (aw *accWriter) ApplyOBEvent(kind consumer.OBEventKind, side string, parsedSize float64) {
       switch kind {
       case consumer.OBEventAdd:
           aw.acc.IncrementOBAdd(side, parsedSize)
       case consumer.OBEventCancel:
           aw.acc.IncrementOBCancel(side)
       case consumer.OBEventModify:
           aw.acc.IncrementOBModify()
       }
   }
   ```
   The `accWriter` struct does NOT need a new field — it delegates directly to `aw.acc`.

### OB Activity state and Bar fields (CS-FR15)

7. `accumulator.Bar` gains ten new pointer fields (all nil when no OB data):
   ```go
   BidOrderArrivals *int
   AskOrderArrivals *int
   BidCancelCount   *int
   AskCancelCount   *int
   OBModifyCount    *int
   AvgBidOrderSize  *float64 // nil if BidOrderArrivals == 0
   AvgAskOrderSize  *float64 // nil if AskOrderArrivals == 0
   BestBidChanges   *int     // nil if no ticks with valid quotes this second
   BestAskChanges   *int     // nil if no ticks with valid quotes this second
   QuoteStuffRatio  *float64 // nil if TradeCount == 0
   ```

8. Accumulator state additions for OB activity. New counter methods on `Accumulator`:

   State fields:
   ```go
   bidOrderArrivals int
   askOrderArrivals int
   bidCancelCount   int
   askCancelCount   int
   obModifyCount    int
   bidArrivalVolSum float64
   askArrivalVolSum float64
   bestBidChanges   int
   bestAskChanges   int
   hasOBActivity    bool // set true on any IncrementOBAdd/Cancel/Modify call
   hasQuoteActivity bool // set true once any tick with valid bid+ask is seen
   ```

   New methods:
   ```go
   func (a *Accumulator) IncrementOBAdd(side string, parsedSize float64) {
       if side == "buy" {
           a.bidOrderArrivals++
           a.bidArrivalVolSum += parsedSize
       } else {
           a.askOrderArrivals++
           a.askArrivalVolSum += parsedSize
       }
       a.hasOBActivity = true
   }
   func (a *Accumulator) IncrementOBCancel(side string) {
       if side == "buy" {
           a.bidCancelCount++
       } else {
           a.askCancelCount++
       }
       a.hasOBActivity = true
   }
   func (a *Accumulator) IncrementOBModify() {
       a.obModifyCount++
       a.hasOBActivity = true
   }
   ```

   `best_bid_changes` and `best_ask_changes` are tracked INSIDE `Apply()`:
   ```go
   // Inside the valid-quote block in Apply() (after parsing bid/ask):
   if a.hasQuoteActivity {
       if bid != a.bestBid {
           a.bestBidChanges++
       }
       if ask != a.bestAsk {
           a.bestAskChanges++
       }
   }
   a.hasQuoteActivity = true
   ```
   Note: compare BEFORE updating `a.bestBid`/`a.bestAsk`.

9. `CurrentBar()` populates OB activity fields:
   ```go
   if a.hasOBActivity || a.hasQuoteActivity {
       bar.BidOrderArrivals = ptrInt(a.bidOrderArrivals)
       bar.AskOrderArrivals = ptrInt(a.askOrderArrivals)
       bar.BidCancelCount   = ptrInt(a.bidCancelCount)
       bar.AskCancelCount   = ptrInt(a.askCancelCount)
       bar.OBModifyCount    = ptrInt(a.obModifyCount)
   }
   if a.bidOrderArrivals > 0 {
       bar.AvgBidOrderSize = ptr(a.bidArrivalVolSum / float64(a.bidOrderArrivals))
   }
   if a.askOrderArrivals > 0 {
       bar.AvgAskOrderSize = ptr(a.askArrivalVolSum / float64(a.askOrderArrivals))
   }
   if a.hasQuoteActivity {
       bar.BestBidChanges = ptrInt(a.bestBidChanges)
       bar.BestAskChanges = ptrInt(a.bestAskChanges)
   }
   if a.tradeCount > 0 {
       arrivals := a.bidOrderArrivals + a.askOrderArrivals
       cancels  := a.bidCancelCount + a.askCancelCount
       bar.QuoteStuffRatio = ptr(float64(arrivals+cancels) / float64(a.tradeCount))
   }
   ```

10. `BarReset()` clears all OB activity state. `hasOBActivity` and `hasQuoteActivity` reset to false.

### Trade Microstructure state and Bar fields (CS-FR16)

11. `accumulator.Bar` gains three new pointer fields:
    ```go
    TradeSignAutocorr        *float64 // nil when TradeCount < 2 or all same sign
    InterTradeIntervalStdMs  *float64 // nil when TradeCount < 2
    NumTradePriceLevels      *int     // nil when TradeCount == 0
    ```

12. Accumulator state additions for trade microstructure:
    - Online state for lag-1 trade sign autocorrelation (O(1) space, no slice):
      ```go
      nTrades       int    // same as tradeCount — reuse a.tradeCount
      sumSigns      int    // Σ sign_i; +1 for buy, -1 for sell
      sumSignPairs  int    // Σ sign_i * sign_{i+1}
      firstSign     int8   // sign of first trade
      lastSign      int8   // sign of most recent trade
      ```
    - `tradeTimestamps []int64` — already added in story 7-3; reuse to compute `InterTradeIntervalStdMs`
    - `tradePriceLevels map[string]struct{}` — distinct trade prices; key is the raw price string

    On each trade tick (after parsing price p and side):
    ```go
    currSign := int8(1)
    if side != "buy" { currSign = -1 }
    if a.tradeCount == 0 {
        a.firstSign = currSign
    } else {
        a.sumSignPairs += int(a.lastSign) * int(currSign)
    }
    a.sumSigns += int(currSign)
    a.lastSign = currSign
    a.tradePriceLevels[price] = struct{}{}
    ```
    Note: `tradeCount` is incremented AFTER this block (existing code), so use `a.tradeCount == 0` to detect first trade.

13. `CurrentBar()` populates microstructure fields:

    `NumTradePriceLevels`:
    ```go
    if a.tradeCount > 0 {
        bar.NumTradePriceLevels = ptrInt(len(a.tradePriceLevels))
    }
    ```

    `InterTradeIntervalStdMs` — computed from `tradeTimestamps` (n-1 intervals):
    ```go
    if a.tradeCount >= 2 {
        n := len(a.tradeTimestamps)
        var sumI, sumI2 float64
        for i := 1; i < n; i++ {
            d := float64(a.tradeTimestamps[i] - a.tradeTimestamps[i-1])
            sumI += d
            sumI2 += d * d
        }
        nI := float64(n - 1)
        variance := sumI2/nI - (sumI/nI)*(sumI/nI)
        if variance > 0 {
            bar.InterTradeIntervalStdMs = ptr(math.Sqrt(variance))
        } else {
            bar.InterTradeIntervalStdMs = ptr(0.0)
        }
    }
    ```

    `TradeSignAutocorr` — lag-1 Pearson autocorrelation:
    ```go
    if a.tradeCount >= 2 {
        nP := float64(a.tradeCount - 1) // number of pairs
        sumX := float64(a.sumSigns - int(a.lastSign))  // s_0..s_{n-2}
        sumY := float64(a.sumSigns - int(a.firstSign)) // s_1..s_{n-1}
        meanX := sumX / nP
        meanY := sumY / nP
        cov := float64(a.sumSignPairs)/nP - meanX*meanY
        varX := 1.0 - meanX*meanX // since xi^2 = 1 for all ±1 values
        varY := 1.0 - meanY*meanY
        if varX > 0 && varY > 0 {
            bar.TradeSignAutocorr = ptr(cov / math.Sqrt(varX*varY))
        }
        // else nil: all same sign → degenerate (varX or varY = 0)
    }
    ```

14. `BarReset()` clears all microstructure state:
    ```go
    a.sumSigns = 0
    a.sumSignPairs = 0
    a.firstSign = 0
    a.lastSign = 0
    a.tradePriceLevels = a.tradePriceLevels[:0]   // no: it's a map
    ```
    For the map: `clear(a.tradePriceLevels)` (Go 1.21+) or delete all keys in a loop. Reuse the map to avoid alloc.
    `tradeTimestamps` reset is handled by story 7-3 (`a.tradeTimestamps = a.tradeTimestamps[:0]`).

    `Reset()` (full reset) additionally does `a.tradePriceLevels = make(map[string]struct{})`.

### Writer (AC: 15)

15. `internal/writer/questdb/writer.go` writes all new fields with nil-check pattern:
    ```go
    if bar.BidOrderArrivals != nil  { row = row.Int64Column("bid_order_arrivals", int64(*bar.BidOrderArrivals)) }
    if bar.AskOrderArrivals != nil  { row = row.Int64Column("ask_order_arrivals", int64(*bar.AskOrderArrivals)) }
    if bar.BidCancelCount   != nil  { row = row.Int64Column("bid_cancel_count", int64(*bar.BidCancelCount)) }
    if bar.AskCancelCount   != nil  { row = row.Int64Column("ask_cancel_count", int64(*bar.AskCancelCount)) }
    if bar.OBModifyCount    != nil  { row = row.Int64Column("ob_modify_count", int64(*bar.OBModifyCount)) }
    if bar.AvgBidOrderSize  != nil  { row = row.Float64Column("avg_bid_order_size", *bar.AvgBidOrderSize) }
    if bar.AvgAskOrderSize  != nil  { row = row.Float64Column("avg_ask_order_size", *bar.AvgAskOrderSize) }
    if bar.BestBidChanges   != nil  { row = row.Int64Column("best_bid_changes", int64(*bar.BestBidChanges)) }
    if bar.BestAskChanges   != nil  { row = row.Int64Column("best_ask_changes", int64(*bar.BestAskChanges)) }
    if bar.QuoteStuffRatio  != nil  { row = row.Float64Column("quote_stuff_ratio", *bar.QuoteStuffRatio) }
    if bar.TradeSignAutocorr != nil       { row = row.Float64Column("trade_sign_autocorr", *bar.TradeSignAutocorr) }
    if bar.InterTradeIntervalStdMs != nil { row = row.Float64Column("inter_trade_interval_std_ms", *bar.InterTradeIntervalStdMs) }
    if bar.NumTradePriceLevels != nil     { row = row.Int64Column("num_trade_price_levels", int64(*bar.NumTradePriceLevels)) }
    ```

16. `go test ./...` passes. New L1 tests:
    - `HasLevel` returns true for known level, false for unknown; false before snapshot.
    - `IncrementOBAdd` / `IncrementOBCancel` / `IncrementOBModify` accumulate correctly per side.
    - `AvgBidOrderSize`: nil when no bid arrivals; correct mean for known set.
    - `AvgAskOrderSize`: symmetric.
    - `BestBidChanges`: 0 after single tick; increments correctly on bid price change; does NOT increment on same price.
    - `QuoteStuffRatio`: nil when tradeCount==0; correct ratio for known (arrivals, cancels, trades).
    - `TradeSignAutocorr`: nil for single trade; nil when all same sign; correct value for `[buy, sell, buy]`.
    - `InterTradeIntervalStdMs`: nil for single trade; 0 for two trades with equal interval; correct std for known series.
    - `NumTradePriceLevels`: 1 for all trades at same price; n for n distinct prices.
    - Distinct test values: every asserted Bar field must use a different value.

## Tasks / Subtasks

- [x] Add `HasLevel` to `OrderBook`, `BookApplier`, and `OBAdapter` (AC: 1, 2)
  - [x] `func (ob *OrderBook) HasLevel(side, price string) bool` in `internal/orderbook/orderbook.go`
  - [x] Add `HasLevel` to `consumer.BookApplier` interface in `internal/consumer/consumer.go`
  - [x] Add `HasLevel` to `OBAdapter` in `internal/consumer/obadapter.go`
  - [x] Add `HasLevel` stub to `stubBook` in `internal/consumer/consumer_l2_test.go`
  - [x] L1 test for `HasLevel` in `internal/orderbook/orderbook_test.go`

- [x] Add `OBEventKind` and `ApplyOBEvent` to consumer package (AC: 3, 4)
  - [x] Add `OBEventKind` type and constants to `internal/consumer/consumer.go`
  - [x] Add `ApplyOBEvent` to `AccumulatorApplier` interface
  - [x] Add no-op `ApplyOBEvent` to `stubAcc` in `consumer_l2_test.go`

- [x] Wire consumer to derive `OBEventKind` and dispatch `ApplyOBEvent` (AC: 5)
  - [x] Update `handleMessage()` EventTick case in `internal/consumer/consumer.go`
  - [x] Add `strconv` import if not already present (for `ParseFloat` on `parsedSize`)

- [x] Implement `accWriter.ApplyOBEvent` in `cmd/candle/main.go` (AC: 6)

- [x] Add OB activity state and Bar fields to Accumulator (AC: 7, 8, 9, 10)
  - [x] Add state fields to `Accumulator` struct
  - [x] Add `IncrementOBAdd`, `IncrementOBCancel`, `IncrementOBModify` methods
  - [x] Update `Apply()` to track `bestBidChanges`/`bestAskChanges`
  - [x] Add 10 `Bar` pointer fields
  - [x] Update `CurrentBar()` to populate OB activity fields
  - [x] Update `BarReset()`
  - [x] L1 tests in `internal/accumulator/accumulator_test.go`

- [x] Add trade microstructure state and Bar fields to Accumulator (AC: 11, 12, 13, 14)
  - [x] Add `sumSigns`, `sumSignPairs`, `firstSign`, `lastSign`, `tradePriceLevels` to `Accumulator`
  - [x] Update `Apply()` trade branch to maintain sign autocorr state and price level set
  - [x] Add 3 `Bar` pointer fields
  - [x] Update `CurrentBar()` for microstructure fields
  - [x] Update `BarReset()` / `Reset()`
  - [x] L1 tests

- [x] Write all new fields in QuestDB writer (AC: 15)

## Dev Notes

### Import cycle prevention
`OBEventKind` is defined in `internal/consumer` package. `Accumulator` (in `internal/accumulator`) must NOT import `consumer`. Therefore:
- `accWriter.ApplyOBEvent()` in `cmd/candle/main.go` receives `consumer.OBEventKind` and translates via a switch into calls to `acc.IncrementOBAdd()`, `acc.IncrementOBCancel()`, `acc.IncrementOBModify()`.
- The `Accumulator` methods are simple counters — no knowledge of OBEventKind.

### HasLevel — pre-apply semantics are critical
`HasLevel` MUST be called BEFORE `book.ApplyTick()`. If called after, an Add would look like a Modify (level now exists). The consumer code must follow: HasLevel → ApplyTick → Apply → ApplyOBEvent.

### best_bid_changes tracking
In `Accumulator.Apply()`, within the valid-quote block, BEFORE updating `a.bestBid`/`a.bestAsk`:
```go
if a.hasQuoteActivity {   // at least one prior valid quote this bar
    if bid != a.bestBid { a.bestBidChanges++ }
    if ask != a.bestAsk { a.bestAskChanges++ }
}
a.hasQuoteActivity = true
// then: a.bestBid = bid, a.bestAsk = ask (existing code)
```
The first tick that establishes a quote does NOT count as a change — `hasQuoteActivity` guards this.

### tradePriceLevels map lifecycle
Allocate in `New()`: `tradePriceLevels: make(map[string]struct{})`.
In `BarReset()`: clear the map contents: `for k := range a.tradePriceLevels { delete(a.tradePriceLevels, k) }` (compatible with Go 1.20; or `clear(a.tradePriceLevels)` for Go 1.21+). Check the module's Go version first.
In `Reset()`: `a.tradePriceLevels = make(map[string]struct{})` (full reinit).

### TradeSignAutocorr degenerate cases
When all trades are the same side (all buy or all sell):
- All sign_i = +1 (or -1) → sumSigns = ±n → meanX = meanY = ±1 → varX = varY = 1 - 1 = 0 → nil.
- When tradeCount < 2 → nil.

### InterTradeIntervalStdMs for equal intervals
When all inter-trade intervals are equal (e.g., all 10ms):
- variance = 0, so `bar.InterTradeIntervalStdMs = ptr(0.0)` (not nil).
- nil is reserved for insufficient data (tradeCount < 2).

### story 7-3 dependency
This story depends on story 7-3 for:
- `tsMs int64` parameter in `AccumulatorApplier.Apply()` (AC 1 of 7-3)
- `tradeTimestamps []int64` slice in Accumulator (AC 4 of 7-3)

Implement stories in order: 7-2 → 7-3 → 7-4 → 7-5.

### Distinct test values
All accumulator tests must use distinct values for every asserted Bar field (lesson from Epic 6 retro).

## Dev Agent Record

### Completion Notes
All ACs satisfied. Key implementation decisions:
- `bestBidChanges`/`bestAskChanges` tracking placed BEFORE `a.bestBid = bid` assignment in `Apply()` — incorrect ordering caused first attempt test failure.
- `tradePriceLevels` initialized in `New()`, `clear()` builtin used in `BarReset()` (Go 1.24), full `make()` in `Reset()`.
- `TradeSignAutocorr` formula: sumX = sumSigns - lastSign (prefix), sumY = sumSigns - firstSign (suffix), lag-1 Pearson autocorrelation, nil when varX or varY ≤ 0 (all same sign).
- Go 1.24 available: used `clear()` for map reset in BarReset.

### File List
- internal/orderbook/orderbook.go (modified — HasLevel method)
- internal/orderbook/orderbook_test.go (modified — HasLevel tests)
- internal/consumer/consumer.go (modified — HasLevel in BookApplier, OBEventKind, ApplyOBEvent in AccumulatorApplier, handleMessage updates)
- internal/consumer/obadapter.go (modified — HasLevel forwarding)
- internal/consumer/consumer_l2_test.go (modified — stubBook.HasLevel, stubAcc.ApplyOBEvent)
- cmd/candle/main.go (modified — accWriter.ApplyOBEvent)
- internal/accumulator/accumulator.go (modified — 13 Bar fields, 15 struct fields, 3 new methods, Apply/CurrentBar/BarReset/Reset/New updates)
- internal/accumulator/accumulator_test.go (modified — 17 new L1 tests)
- internal/writer/questdb/writer.go (modified — 13 new ILP columns)

### Change Log
- 2026-05-08: Story 7-4 implemented — OB activity counters, OBEventKind pipeline, trade microstructure metrics
