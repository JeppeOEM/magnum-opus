---
id: 35-1
title: Hawkes intensity accumulator
epic: 35
status: ready-for-dev
---

# Story 35-1: Hawkes Intensity Accumulator

## Context

The Hawkes process models self-exciting order arrival bursts — a spike in order flow makes further spikes more likely. The intensity `λ(t) = Σ α·exp(-β·(t-tᵢ))` decays exponentially between events and jumps by α on each new event. Computed O(1) per tick as a running decay sum. This is a top feature for detecting burst periods where mean reversion is unreliable.

Currently `snapshot_1s` has no self-excitation feature. This story adds `hawkes_intensity` (the running decay sum at bar close) to the accumulator, DDL, and ILP writer.

Parameters: α=0.8 (jump size), β=10.0 (decay rate in s⁻¹ — half-life ≈70ms).

## What to build

### `candle-service/internal/accumulator/accumulator.go`

Add two fields to the `Accumulator` struct (after `hasOBActivity bool`):

```go
// Hawkes process intensity — continuous across bars (not reset in BarReset)
hawkesDecaySum float64
lastTickTsMs   int64 // ms timestamp of last Apply() call (any tick)
```

In `Apply()`, immediately after `a.hasTicks = true`:

```go
// Hawkes intensity: O(1) exponential decay + jump
const hawkesAlpha = 0.8
const hawkesBeta  = 10.0
if a.lastTickTsMs > 0 && tsMs > a.lastTickTsMs {
    dt := float64(tsMs-a.lastTickTsMs) / 1000.0 // seconds
    a.hawkesDecaySum *= math.Exp(-hawkesBeta * dt)
}
a.hawkesDecaySum += hawkesAlpha
a.lastTickTsMs = tsMs
```

Add to `Bar` struct (after `OBModifyCount *int`):

```go
HawkesIntensity *float64 // nil if no ticks this bar
```

In `CurrentBar()`, after the `a.hasTicks` block that sets `bar.OFI`:

```go
if a.hasTicks {
    bar.HawkesIntensity = ptr(a.hawkesDecaySum)
}
```

In `BarReset()` — **do NOT add hawkesDecaySum or lastTickTsMs** — they survive across bar boundaries (continuous process).

### `candle-service/migrations/027_hawkes_intensity.sql`

```sql
ALTER TABLE snapshot_1s ADD COLUMN IF NOT EXISTS hawkes_intensity DOUBLE;
```

### `candle-service/internal/writer/questdb/writer.go`

After the OFI block (near `row = row.Float64Column("ofi", ...)`), add:

```go
if bar.HawkesIntensity != nil {
    row = row.Float64Column("hawkes_intensity", *bar.HawkesIntensity)
}
```

### Tests — `candle-service/internal/accumulator/accumulator_test.go`

```go
func TestHawkesIntensity_FirstTick(t *testing.T) {
    // single tick → intensity = α = 0.8
    acc := New("bybit", "BTCUSDT", fixedClock{})
    acc.Apply("100", "1", false, "buy", 1000, emptyQ, emptyQ)
    bar := acc.CurrentBar(1000, false)
    require.NotNil(t, bar.HawkesIntensity)
    assert.InDelta(t, 0.8, *bar.HawkesIntensity, 1e-9)
}

func TestHawkesIntensity_DecayBetweenTicks(t *testing.T) {
    // two ticks 100ms apart: intensity = 0.8*exp(-10*0.1) + 0.8
    acc := New("bybit", "BTCUSDT", fixedClock{})
    acc.Apply("100", "1", false, "buy", 1000, emptyQ, emptyQ)
    acc.Apply("100", "1", false, "buy", 1100, emptyQ, emptyQ) // +100ms
    bar := acc.CurrentBar(1100, false)
    expected := 0.8*math.Exp(-10.0*0.1) + 0.8
    assert.InDelta(t, expected, *bar.HawkesIntensity, 1e-9)
}

func TestHawkesIntensity_SurvivesBarReset(t *testing.T) {
    acc := New("bybit", "BTCUSDT", fixedClock{})
    acc.Apply("100", "1", false, "buy", 1000, emptyQ, emptyQ)
    _ = acc.CurrentBar(1000, false)
    acc.BarReset()
    // next tick 500ms later — decay should continue from pre-reset value
    acc.Apply("100", "1", false, "buy", 1500, emptyQ, emptyQ)
    bar := acc.CurrentBar(1500, false)
    expected := 0.8*math.Exp(-10.0*0.5) + 0.8
    assert.InDelta(t, expected, *bar.HawkesIntensity, 1e-6)
}

func TestHawkesIntensity_NilWhenNoTicks(t *testing.T) {
    acc := New("bybit", "BTCUSDT", fixedClock{})
    bar := acc.CurrentBar(1000, false)
    assert.Nil(t, bar.HawkesIntensity)
}
```

## Acceptance Criteria

1. `Accumulator` has `hawkesDecaySum float64` and `lastTickTsMs int64` fields.
2. First tick in a fresh accumulator: `hawkesIntensity = 0.8` (no prior decay).
3. Two ticks separated by dt seconds: `intensity = 0.8 × exp(-10×dt) + 0.8`.
4. `HawkesIntensity` is non-nil in `CurrentBar()` when `hasTicks=true`.
5. `hawkesDecaySum` and `lastTickTsMs` are NOT zeroed in `BarReset()` — carry across bars.
6. `HawkesIntensity` is nil when no ticks arrive in a bar.
7. Migration `027_hawkes_intensity.sql` adds `hawkes_intensity DOUBLE` column.
8. ILP writer emits `hawkes_intensity` when `bar.HawkesIntensity != nil`.
9. All 4 L1 tests pass.

## Dev Notes

- `math.Exp` is already imported in accumulator.go.
- α=0.8 and β=10.0 are package-level constants, not configurable at runtime.
- `lastTickTsMs` initialises to 0 — the `a.lastTickTsMs > 0` guard handles the first-tick case correctly (no decay on first event).
- Migration number 027 — next after 026_snapshot_15m.sql.
