---
stepsCompleted: [1, 2, 3, 4]
inputDocuments: []
session_topic: 'Frontend charting refactor — maintainable library-driven stack for order flow and volume analytics'
session_goals: 'Pick right charting library, design panel layout for candlestick+volume, CVD, Bid/Ask, Delta Heatmap, Volume Profile, absorption detection, historical liquidity, volume bubbles. All bot-relevant calculations stay in candle service.'
selected_approach: 'ai-recommended'
techniques_used: ['Solution Matrix', 'Cross-Pollination', 'Constraint Mapping']
ideas_generated: [35]
session_active: false
workflow_completed: true
---

# Brainstorming Session Results

**Facilitator:** mrqdt
**Date:** 2026-05-11

## Session Overview

**Topic:** Frontend charting refactor — maintainable library-driven stack for order flow and volume analytics
**Goals:** Pick the right charting library and design the visual panel layout to support candlestick + historical volume concentration overlay, CVD, Bid vs Ask volume, Delta Heatmap, Volume Profile, absorption detection, historical liquidity tracking, volume bubbles, and full DeepCharts feature parity.

**Refined constraint:** Bot-relevant calculations stay in candle service. Purely visual calculations (heatmap matrix assembly, rolling windows, display scaling, bubble sizing) are owned by the frontend.

### Session Setup

_Fresh session started 2026-05-11. Topic confirmed by user._

---

## Technique Selection

**Approach:** AI-Recommended Techniques
**Analysis Context:** Technical decision-making + creative layout design for order flow analytics dashboard

**Recommended Techniques:**
- **Solution Matrix:** Score library options against real panel requirements — no gut-feeling bias
- **Cross-Pollination:** Steal UI/UX patterns from DeepCharts, Bookmap, and other professional trading tools
- **Constraint Mapping:** Surface real blockers (Redis retention, port conflicts, data availability) before committing

---

## Technique Execution Results

### Phase 1 — Solution Matrix: Library Decision

**Decision: Dash + Plotly**

Key pivot: user confirmed 1-second refresh is sufficient (not tick-level). This ruled out the complexity of native WebSocket charting and made Dash a clean win.

All candidate libraries evaluated:

| Library | Verdict | Reason |
|---|---|---|
| Dash + Plotly | ✅ Selected | Native candlestick, go.Heatmap, go.Bar — all 9 panels first-class. Python matches user skillset. |
| Lightweight Charts | ❌ | Heatmap/custom panels require painful canvas work |
| Chart.js | ❌ | No native candlestick, canvas limits |
| uPlot | ❌ | Too low-level — you'd write every renderer |
| Apache ECharts | ❌ | Heavy, complex config, WebSocket latency concerns |
| D3.js | ❌ | Full renderer from scratch |

**Calculation ownership boundary established:**
- **Candle service owns:** CVD, delta, divergence flags, absorption, imbalances, footprint signals — anything a bot might trade on
- **Dash owns:** heatmap matrix assembly, rolling windows, display scaling, bubble sizing, POC/VA from emitted fields — purely visual

### Phase 2 — Cross-Pollination: DeepCharts + Bookmap Feature Harvest

Surveyed DeepCharts and Bookmap for features worth copying.

**From DeepCharts:**
- Footprint chart (bid/ask volume per price row per candle)
- Diagonal imbalance stacks (consecutive cells where one side > 3× the other)
- Single prints / unfinished auction levels
- POC (Point of Control) + Value Area High/Low
- Delta divergence overlay on footprint

**From Bookmap:**
- Order book depth heatmap (resting limit order walls over time)
- Iceberg detection (large resting orders that keep refilling despite hits)
- Volume bubbles on price chart

**Data gap identified and resolved:** Footprint requires per-price `{buy_vol, sell_vol}` within each candle. Candle service currently stores only `num_trade_price_levels` (a count). Resolution: candle service adds a `footprint_json` field — one extra accumulator in the existing tick loop, no new infrastructure.

### Phase 3 — Constraint Mapping: No Blockers Found

| Constraint | Finding |
|---|---|
| Redis tick stream retention | MAXLEN=50,000 ticks (~40 min at BTC velocity) — fine for Dash heatmap assembly |
| No gateway in docker-compose | Gateway runs separately; Dash bypasses it entirely, reads Redis directly |
| Historical data | Two-phase: QuestDB REST for history on startup, Redis stream for live tail |
| Ports | 8050 (Dash default) is free — all other ports occupied |
| Footprint JSON size | ~3-5KB per active second — within Redis MAXLEN budget |
| Frontend retirement | Clean break: 9 TypeScript files replaced, Vite + port 5173 retired |

---

## Complete Idea Inventory

### Architecture & Infrastructure

**[Architecture #1]: Split Calculation Ownership**
_Concept:_ Candle service owns all trading-relevant math and pushes to Redis. Dash owns all purely visual math. No duplication, clean boundary.
_Novelty:_ Visual tool and bot are guaranteed to agree on every signal — single source of truth.

**[Architecture #2]: Signal-Worthy Indicators in Candle Service**
_Concept:_ Any calculation that could inform a trading decision lives in the candle service and arrives as a field. CVD divergence, absorption, imbalances — all computed once, shared by bot and frontend.
_Novelty:_ Eliminates the risk of the chart showing a signal the bot never sees.

**[Architecture #3]: Candle Service Emits Footprint Blob**
_Concept:_ One extra accumulator in the existing tick loop builds `map[price]→{buy_vol, sell_vol}` per second, serialised as `footprint_json` in the Redis candle stream.
_Novelty:_ Zero new infrastructure — one field, permanent data product usable by bot too.

**[Architecture #4]: Full Frontend Replacement**
_Concept:_ Dash replaces the Vite/TypeScript frontend entirely. `frontend/` directory retired. New `dashboard` service in docker-compose on port 8050.
_Novelty:_ No dual maintenance. Single Python stack end-to-end.

**[Architecture #5]: Two-Phase Data Load**
_Concept:_ On startup, Dash queries QuestDB REST API for last N candles (history). Then `dcc.Interval(1000)` tails the Redis candle stream for live updates.
_Novelty:_ Seamless history-to-live transition with no separate backfill service.

**[Architecture #6]: Footprint Signals as First-Class Output**
_Concept:_ Candle service analyses the footprint map and emits structured signal fields alongside the raw blob. ~17 new fields written to Redis and QuestDB.
_Novelty:_ Bot can trade on imbalance stacks, single prints, and iceberg events using the same computation the chart renders.

### Panel Layout

**[Layout #5 — Final]: Dual Primary + Stacked Heatmaps**

```
┌─────────────────────┬───────┬─────────────────────┐
│  Candlestick        │  Vol  │  Delta Heatmap       │
│  + Vol Levels       │  Prof │  (price × time)      │
│  + Vol Bubbles      │  ile  ├─────────────────────┤
│  + Absorption marks │       │  OB Depth Heatmap    │
│  + Liquidity overlay│       │  (resting walls)     │
├──────────┬──────────┴───────┴─────────────────────┤
│  CVD     │  Bid/Ask Volume (bars + ratio line)     │
└──────────┴─────────────────────────────────────────┘
         [Symbol Dropdown — top of page]
```

_Novelty:_ Shared price Y-axis across candlestick, volume profile, and both heatmaps. Eye scans horizontally at any price level to read four simultaneous views.

### Candlestick Panel Overlays

**[Panel Overlay #1]: Volume Levels**
_Concept:_ Historical volume concentration as semi-transparent horizontal `go.Bar` traces overlaid on the candlestick. POC highlighted, Value Area shaded.
_Novelty:_ Price action and market memory readable in the same glance.

**[Panel Overlay #2]: Volume Bubbles**
_Concept:_ `go.Scatter` markers on candlestick chart. Marker size = volume magnitude, color = buy (green) / sell (red), position = trade price. Absorption candles get distinct marker shape.
_Novelty:_ Four information streams in one panel — price, volume, aggressor side, absorption.

**[Panel Overlay #3]: Absorption Detection Markers**
_Concept:_ Candle service emits absorption flag. Dash renders a distinct marker (diamond/star) on the candlestick at the flagged candle.

**[Panel Overlay #4]: Liquidity Tracking Overlay**
_Concept:_ Horizontal profile overlay showing price levels where historically high volume traded repeatedly. POC line + Value Area band computed in Dash from emitted fields.

### Standalone Panels

**[Panel #1]: Volume Profile**
_Concept:_ Horizontal `go.Bar` (orientation='h') in the bridge column between candlestick and heatmaps. Price on Y-axis, volume on X-axis. Shares price axis with both primary panels.

**[Panel #2]: CVD — Filled Area + Divergence Markers**
_Concept:_ `go.Scatter` with `fill='tozeroy'`. Green fill when positive (buyers in control), red fill when negative. Divergence markers from candle service `cvd_divergence` field — no signal math in Dash.

**[Panel #3]: Bid/Ask Volume — Stacked Bars + Ratio Line**
_Concept:_ Ask bars up (green), bid bars down (red), plus `go.Scatter` ratio line (`ask_vol / total_vol`) on secondary Y-axis (0–1). Bars show magnitude, ratio line shows aggressor dominance.

**[Panel #4]: Delta Heatmap**
_Concept:_ `go.Heatmap` with diverging colorscale (red/green) centred at zero. Price × time grid assembled in Dash from candle stream history. Purely visual math.

**[Panel #5]: OB Depth Heatmap**
_Concept:_ `go.Heatmap` showing resting limit order depth over time. Color intensity = how much size resting at that price level. Stacked below Delta Heatmap, sharing the price Y-axis.

### DeepCharts Feature Parity

**[Feature #1]: Per-Candle Footprint Modal**
_Concept:_ Dash `clickData` callback on candlestick. Click a candle → modal shows per-price grid: bid vol (red) left, ask vol (green) right. POC row highlighted. Imbalance cells flagged. Divergence banner if applicable.

**[Feature #2]: Diagonal Imbalance Stacks**
_Concept:_ Consecutive footprint cells where `ask/bid > 3×` (aggressive buying) or `bid/ask > 3×` (aggressive selling). Rendered as coloured cell borders in the footprint modal.

**[Feature #3]: Single Prints / Unfinished Auction**
_Concept:_ Price levels inside a candle's High-Low range with near-zero volume. Highlighted in footprint modal. Bot-tradeable as magnet levels.

**[Feature #4]: POC + Value Area**
_Concept:_ POC line and 70% Value Area band rendered from candle service fields `poc_price`, `value_area_high`, `value_area_low`. Overlay on candlestick and in footprint modal.

**[Feature #5]: Footprint Delta Divergence**
_Concept:_ `footprint_delta_divergence` field from candle service. Footprint modal shows divergence banner (price up, delta net negative = bear divergence).

**[Feature #6]: Iceberg Detection**
_Concept:_ OB Depth Heatmap cells get a coloured border where `iceberg_bid_detected` or `iceberg_ask_detected` is true. Indicates a large player defending a level.

### UX

**[UX #1]: Symbol Switcher Dropdown**
_Concept:_ `dcc.Dropdown` at top of page. Selection drives all chart callbacks — all panels re-subscribe to the correct Redis stream key (`candles:{exchange}:{symbol}:{tf}`).

### New Candle Service Fields (~17 total)

| Group | Fields |
|---|---|
| Footprint | `footprint_json` |
| Imbalance (A) | `imbalance_buy_count`, `imbalance_sell_count`, `imbalance_stack_buy`, `imbalance_stack_sell`, `imbalance_ratio` |
| Unfinished Auction (B) | `single_print_count`, `single_print_levels_json`, `unfinished_top`, `unfinished_bottom` |
| Value Area (C) | `poc_price`, `value_area_high`, `value_area_low`, `poc_volume` |
| Footprint Divergence (D) | `footprint_delta_divergence` |
| Iceberg | `iceberg_bid_detected`, `iceberg_ask_detected`, `iceberg_price` |

### Deferred to Future Sessions

- DOM Ladder (order book depth table)
- Volume Speed strip
- Multi-symbol split-screen view
- Correlation strip (CVD correlation between symbols)

---

## Idea Organization and Prioritization

### Prioritisation Results

**Top Priority — Epic 18: Dash Dashboard Core**
_Why first:_ Delivers immediate value — all existing frontend functionality replaced plus CVD, Bid/Ask, Delta Heatmap. No candle service changes needed for core panels.

**Second — Epic 19: Candle Service Footprint + Signals**
_Why second:_ Prerequisite for all DeepCharts parity. One focused candle service sprint unlocks the entire footprint feature set for both the chart and the bot.

**Third — Epic 20: DeepCharts Parity Rendering**
_Why third:_ Depends on Epic 19. Render the signals, add the footprint modal, surface all imbalance/single-print/iceberg visuals.

### Action Plans

#### Epic 18: Dash Dashboard — Core Panels
1. Create `dashboard/` Python service directory with `Dockerfile` and `requirements.txt` (dash, plotly, redis, requests)
2. Add `dashboard` service to `docker-compose.yml` on port 8050, depends on redis + questdb
3. Implement QuestDB REST history loader + Redis stream live tail
4. Build candlestick panel with vol levels, bubbles, absorption markers, liquidity overlay
5. Build Volume Profile, Delta Heatmap, CVD, Bid/Ask panels
6. Add symbol switcher dropdown wiring all callbacks
7. Delete `frontend/` directory, remove Vite service from docker-compose

**Resources needed:** Python 3.11+, dash, plotly, redis-py, requests
**Timeline:** 1 sprint
**Success indicator:** Full-stack `make up` serves the dashboard at `localhost:8050` with live data

#### Epic 19: Candle Service — Footprint & Signals
1. Add footprint accumulator to tick loop — `map[string]{buyVol, sellVol float64}`
2. Serialise to `footprint_json` field in Redis candle stream write
3. Compute imbalance stack signals (Signal Group A) from footprint map
4. Compute unfinished auction / single print detection (Signal Group B)
5. Compute POC + Value Area (Signal Group C)
6. Compute footprint delta divergence (Signal Group D)
7. Add iceberg detection from OB arrivals + trade data
8. Write all 17 fields to Redis + QuestDB (new DDL columns)

**Resources needed:** Candle service Go changes + QuestDB DDL migration
**Timeline:** 1 sprint
**Success indicator:** `XREAD` on candle stream shows `footprint_json` and all signal fields populated

#### Epic 20: DeepCharts Parity Rendering
1. Add OB Depth Heatmap panel (stacked below Delta Heatmap)
2. Wire `clickData` callback on candlestick → footprint modal
3. Render imbalance stack cell borders in modal
4. Render single print highlighted cells
5. Add POC line + Value Area band overlay on candlestick
6. Add iceberg border highlights on OB Depth Heatmap

**Resources needed:** Epic 19 complete
**Timeline:** 1 sprint
**Success indicator:** Click any candle → footprint modal shows correct per-price bid/ask grid with all overlays

---

## Session Summary and Insights

### Key Achievements

- **Library decision made with zero ambiguity** — Dash + Plotly selected, all alternatives ruled out with clear rationale
- **Complete panel blueprint** — every panel placed, every visual element specified before a line of code is written
- **DeepCharts parity mapped** — 6 professional trading features identified, data gaps resolved, implementation path clear
- **17 new candle service fields** designed and grouped into logical signal families
- **No infrastructure surprises** — constraint mapping found all ports, retention limits, and data gaps before they could block implementation
- **Clean architecture boundary** — calculation ownership rule sharpened: bot-relevant in candle service, visual-only in Dash

### Creative Breakthroughs

- **Python charting pivot** — mid-session realisation that Dash eliminates all JavaScript complexity while 1-second refresh is sufficient for human viewing
- **Shared price Y-axis** — the insight that candlestick + volume profile + both heatmaps can share a single price axis, enabling cross-panel reading at a glance
- **Footprint as permanent data product** — reframing the footprint blob from "visual helper" to a first-class candle service output usable by the bot

### Session Reflections

Three-phase structure worked well for this topic: the Solution Matrix eliminated library indecision quickly, Cross-Pollination from DeepCharts/Bookmap gave a concrete feature checklist rather than vague goals, and Constraint Mapping grounded everything in the actual stack before work begins. The mid-session pivot to Python charting was the highest-value moment — it simplified the entire implementation path.
