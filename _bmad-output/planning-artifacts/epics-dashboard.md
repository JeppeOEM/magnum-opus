---
stepsCompleted: [1, 2]
inputDocuments:
  - "_bmad-output/brainstorming/brainstorming-session-2026-05-11-0000.md"
  - "_bmad-output/planning-artifacts/architecture.md"
  - "candle-service/internal/writer/redis/publisher.go"
  - "candle-service/internal/accumulator/accumulator.go"
  - "candle-service/internal/flusher/row.go"
---

# magnum-opus — Epic 18: Dash Dashboard Core

## Overview

This document breaks down Epic 18 (Dash Dashboard Core) into implementable stories. Requirements are sourced from the brainstorming session (2026-05-11), existing architecture context, and live field verification against the candle service output.

**Epic 18 is purely a new `dashboard/` Python service — no candle service changes.**

---

## Field Verification Results

Before story creation, the actual candle service output was verified:

### Redis candle stream: `candles:close:{exchange}:{symbol}:{tf}`
Fields: `ts`, `exchange`, `symbol`, `tf`, `open`, `high`, `low`, `close`, `volume`, `quote_vol`, `trade_count`, `bar_count`, `gap_count`, `is_complete`
**Missing for dashboard panels:** `buy_volume`, `sell_volume` — these are NOT in the Redis stream.

### Redis OB stream: `ob_features:{exchange}:{symbol}`
Fields: `ts`, `exchange`, `symbol`, `best_bid`, `best_ask`, `bid_depth_l1`, `ask_depth_l1`, `bid_depth_l2`, `ask_depth_l2`, `bid_depth_top10`, `ask_depth_top10`, `bid_depth_total`, `ask_depth_total`, `ofi`, `ofi_l1`
**Available for OB Depth Heatmap:** ✅ all depth fields present.

### QuestDB `snapshot_1s` table (via REST)
All 67 fields available including: `buy_volume`, `volume` (→ sell_volume derivable as `volume - buy_volume`), full OHLCV, OFI, spread, depth, microstructure.

### Epic 19 fields (NOT YET in any output)
`absorption_detected`, `cvd_divergence`, `poc_price`, `value_area_high`, `value_area_low`, `poc_volume`, `footprint_json` — all deferred to Epic 19.

### Data strategy consequence
**Primary candle data source: QuestDB REST polling (1s interval).** This gives all 67 fields for both history and live updates. Redis candle stream is NOT read (too few fields). Only `ob_features` Redis stream is read (OB depth data not in QuestDB).

---

## Architecture Decisions (ADRs)

### ADR-18-01: Subplot Grid Structure
**Decision:** `make_subplots` skeleton defined in Story 18.1.
**Exact call:**
```python
make_subplots(
    rows=2, cols=3,
    shared_yaxes='rows',
    column_widths=[0.38, 0.12, 0.50],
    row_heights=[0.70, 0.30],
    specs=[[{}, {}, {"rowspan": 2}], [{}, {}, None]]
)
```
Col 3 spans both rows (Delta + OB heatmaps share one subplot area). All panel stories build traces into this fixed grid at `row=N, col=M`.

### ADR-18-02: Live Data Fetch
**Decision:** 1s `dcc.Interval` fires a QuestDB REST query for `snapshot_1s` rows newer than the last known `ts`. A separate 1s `dcc.Interval` reads new entries from `ob_features:{exchange}:{symbol}` via Redis `XREAD` with cursor ID.
**Rationale:** QuestDB gives all 67 fields (including `buy_volume`). Redis candle stream only has 14 fields and would require the data layer to recompute CVD/delta from OHLCV alone. QuestDB polling at 1s latency is acceptable for human viewing.
**Cursor strategy for ob_features Redis stream:** On load, `XREVRANGE ob_features:{exchange}:{symbol} + - COUNT 1` to get latest entry ID. Live tail starts from that ID. If cursor predates stream's oldest entry, fall back to timestamp dedup.

### ADR-18-03: Heatmap Y-Axis
**Decision:** The col-3 subplot (spanning both rows via `rowspan=2`) shares a single Plotly Y-axis for Delta and OB Depth. Y-axis range is updated to match candlestick visible range via `relayoutData` callback on zoom. This is the same callback as X-axis zoom sync.

### ADR-18-04: Symbol Switch Loading State
**Decision:** Blank panels + `dcc.Loading` spinner. On symbol change, dcc.Store cleared immediately. `dcc.Loading` wrapper per panel shows spinner. QuestDB history fetch repopulates store. Panels redraw atomically on store update.

---

## Requirements Inventory

### Functional Requirements

FR1: A new `dashboard/` Python service directory must be created with a Dockerfile, requirements.txt (dash, dash-bootstrap-components, plotly, redis-py, requests, pandas), and a main `app.py` entry point.
FR2: A `dashboard` service must be added to docker-compose.yml on port 8050, with depends_on: redis and questdb. Dockerfile must include a `/health` endpoint or CMD health check.
FR3: On startup, the dashboard queries QuestDB REST API (`GET http://questdb:9000/exec?query=SELECT * FROM snapshot_1s WHERE exchange=... AND symbol=... ORDER BY ts DESC LIMIT 500`) to populate dcc.Store with last 500 candles.
FR4: A symbol switcher (`dcc.Dropdown`) at the top of the page drives all chart callbacks. Selecting a symbol clears dcc.Store, shows dcc.Loading spinners, and re-fetches history for the new symbol.
FR5: A `dcc.Interval(interval=1000)` fires every second. Callback queries QuestDB for `snapshot_1s` rows with `ts > last_known_ts` and appends them to dcc.Store. A second Interval reads new entries from `ob_features:{exchange}:{symbol}` via Redis XREAD.
FR6: A candlestick panel renders `open`, `high`, `low`, `close` using `go.Candlestick` in subplot position `row=1, col=1`. Panel is built at the correct 38% column width from day one (grey placeholder div for adjacent empty columns during development).
FR7: Volume levels overlay — historical volume concentration rendered as semi-transparent horizontal `go.Bar` traces on the candlestick panel, assembled in Dash from `snapshot_1s.buy_volume` and `snapshot_1s.volume` data. POC row highlighted, Value Area shaded.
FR8: Volume bubbles overlay — `go.Scatter` markers on the candlestick; marker size scales with `volume`; colour is green when `buy_volume / volume > 0.5`, red otherwise.
FR9: Absorption detection markers — stubbed in Epic 18. When `absorption_detected` field is present (Epic 19), a distinct diamond marker will render at flagged candles. In Epic 18, the AC is: absorption marker overlay code exists but renders zero markers (field absent from data).
FR10: Volume Profile panel — horizontal `go.Bar` (`orientation='h'`) in subplot `row=1, col=2`; price on shared Y-axis; bar length represents total `volume` per price level, assembled from QuestDB history.
FR11: Delta Heatmap panel — `go.Heatmap` with diverging colorscale (red/green centred at zero); price × time grid assembled in Dash from `snapshot_1s.buy_volume` and `snapshot_1s.volume` (delta = `2 * buy_volume - volume`). Placed in subplot col=3 rowspan=2.
FR12: OB Depth Heatmap panel — `go.Heatmap` showing resting limit order depth over time, assembled from `ob_features` Redis stream history. Stacked within the same col-3 rowspan subplot as Delta. Shares single Y-axis with Delta panel.
FR13: CVD panel — `go.Scatter` with `fill='tozeroy'`; CVD computed in Dash as cumulative sum of `(2 * buy_volume - volume)` per candle; green fill when positive, red when negative. No divergence markers in Epic 18 (`cvd_divergence` field not yet emitted).
FR14: Bid/Ask Volume panel — ask volume bars above zero (green), bid volume bars below zero (red) as `go.Bar`; plus `go.Scatter` ratio line (`buy_volume / volume`) on a secondary Y-axis scaled 0–1, declared via `specs=[[{"secondary_y": True}]]` in the bottom-row subplot.
FR15: Page layout — top row: candlestick (38%) | volume profile (12%) | stacked heatmaps col-3 rowspan=2 (50%); bottom row: CVD | Bid/Ask. Symbol dropdown at top drives all panels.
FR16: Liquidity overlay (POC line + Value Area band) — stubbed in Epic 18. Code exists to render `poc_price`, `value_area_high`, `value_area_low` from candle data when fields are present. In Epic 18 renders nothing (Epic 19 fields absent).
FR17: The `frontend/` directory must be deleted and the Vite service removed from docker-compose.yml in a dedicated story after all panels are verified working.
FR18: All panels use `dbc.themes.DARKLY` theme (pinned constant, not ad-hoc CSS).

### Non-Functional Requirements

NFR1: `dcc.Interval` refresh cycle is 1000ms. No sub-second rendering requirement.
NFR2: All four top-row panels (candlestick, volume profile, Delta heatmap, OB depth) share a synchronised price Y-axis via `relayoutData` callback on zoom.
NFR3: All visual-only math (heatmap matrix assembly, rolling windows, display scaling, bubble sizing, CVD accumulation) owned by Dash. No trading signal math computed in Dash that isn't derivable purely from published QuestDB fields.
NFR4: Python 3.11+; dependencies: dash, dash-bootstrap-components, plotly, redis-py, requests, pandas.
NFR5: Service runs on port 8050.
NFR6: No changes to aggregator or candle-service code in this epic.
NFR7: On symbol change, panels show dcc.Loading spinner until QuestDB history fetch completes. No stale data from previous symbol visible after new symbol data populates.

### Additional Requirements

- New Python service is independent — no cross-service imports with Go services.
- QuestDB REST query endpoint: `http://questdb:9000/exec` (port 9000).
- Redis stream for OB features: `ob_features:{exchange}:{symbol}`.
- No gateway dependency; dashboard accesses Redis and QuestDB directly.
- `buy_volume` (QuestDB field) is the source of truth for all buy-side volume. Sell volume derived as `snapshot_1s.volume - snapshot_1s.buy_volume` in Dash.
- ADR-18-01 subplot grid must be established in 18.1 so all panel stories build into it without rewiring.

### UX Design Requirements

UX-DR1: `dbc.themes.DARKLY` applied at app level (`external_stylesheets=[dbc.themes.DARKLY]`).
UX-DR2: `dcc.Dropdown` symbol switcher at the top of the page. Selection drives all callbacks via dcc.Store.
UX-DR3: Top-row panels share a single price Y-axis range updated via `relayoutData` on zoom — enables horizontal cross-panel reading at any price level.
UX-DR4: Layout proportions: candlestick col 38%, volume profile col 12%, heatmap col 50% (rowspan=2). Bottom row: CVD 50% | Bid/Ask 50%.
UX-DR5: Each panel wrapped in `dcc.Loading` to display spinner during symbol switch.

### FR Coverage Map

| FR | Story | What it delivers |
|---|---|---|
| FR1, FR2, FR18 | 18.1 | Service scaffold, Dockerfile + health check, docker-compose, DARKLY theme, make_subplots skeleton |
| FR3, FR4 | 18.2a | QuestDB history loader, symbol switcher, dcc.Store |
| FR5 | 18.2b | 1s QuestDB poll + Redis ob_features XREAD, merge strategy, symbol switch loading state |
| FR6, FR7, FR8, FR9 | 18.3 | Candlestick + volume level overlay + volume bubbles + absorption stub |
| FR10, FR11, FR12 | 18.4 | Volume Profile + Delta Heatmap + OB Depth Heatmap + shared Y-axis |
| FR13, FR14 | 18.5 | CVD panel + Bid/Ask Volume panel + secondary Y-axis |
| FR15, FR16 | 18.6 | Full layout assembly + liquidity overlay stub |
| FR17 | 18.7 | Retire frontend/, remove Vite from docker-compose |
| NFR1–7, UX-DR1–5 | All stories | Woven through each story's ACs |

---

## Epic List

### Epic 18: Dash Dashboard Core
Replace the Vite/TypeScript frontend with a Python Dash + Plotly dashboard. New `dashboard/` service, port 8050, reads QuestDB (`snapshot_1s`) and Redis (`ob_features`). Delivers candlestick with overlays, Volume Profile, Delta Heatmap, OB Depth Heatmap, CVD, Bid/Ask. Retires `frontend/` on completion.
**FRs covered:** FR1–FR18, NFR1–7, UX-DR1–5.

---

## Epic 18: Dash Dashboard Core

### Story 18.1: Dashboard Service Scaffold

As a developer,
I want a runnable `dashboard/` Python service skeleton in docker-compose with the correct subplot grid structure,
So that subsequent stories have a stable structural foundation to build panels into.

**Acceptance Criteria:**

**Given** the repo root,
**When** `make up` is run,
**Then** a `dashboard` container starts on port 8050 and `curl localhost:8050` returns HTTP 200.
**And** `curl localhost:8050/_dash-layout` returns a valid JSON layout response (Dash built-in endpoint).

**Given** the `dashboard/` directory,
**When** inspected,
**Then** it contains: `Dockerfile`, `requirements.txt` (dash, dash-bootstrap-components, plotly, redis-py, requests, pandas), `app.py` (entry point + make_subplots call), `layout.py` (page structure), `callbacks.py` (empty stubs).

**Given** the dark theme requirement,
**When** the page loads,
**Then** `dbc.themes.DARKLY` is the sole theme constant used (`external_stylesheets=[dbc.themes.DARKLY]`); no ad-hoc dark CSS.

**Given** ADR-18-01,
**When** `app.py` is inspected,
**Then** it contains the exact `make_subplots` call:
```python
make_subplots(
    rows=2, cols=3,
    shared_yaxes='rows',
    column_widths=[0.38, 0.12, 0.50],
    row_heights=[0.70, 0.30],
    specs=[[{}, {}, {"rowspan": 2}], [{}, {}, None]]
)
```

**Given** docker-compose health checking,
**When** the Dockerfile is inspected,
**Then** it includes a `HEALTHCHECK` directive (e.g. `HEALTHCHECK CMD curl -f http://localhost:8050/_dash-layout || exit 1`).

**Given** the layout page,
**When** rendered,
**Then** the symbol `dcc.Dropdown` appears at the top of the page above the chart area; placeholder `dbc.Spinner` or grey `dbc.Card` exists for each of the 5 panel positions (candlestick, vol profile, delta heatmap, CVD, Bid/Ask); the Vite frontend is NOT removed (it remains until Story 18.7).

---

### Story 18.2a: Data Layer — QuestDB History Loader

As a developer,
I want a data module that fetches historical candles from QuestDB and stores them in dcc.Store,
So that panel stories have a clean DataFrame to build against.

**Acceptance Criteria:**

**Given** QuestDB is running and `snapshot_1s` contains data for `kucoin` / `BTC-USDT`,
**When** the page loads with that symbol selected,
**Then** `GET http://questdb:9000/exec?query=SELECT+*+FROM+snapshot_1s+WHERE+exchange%3D'kucoin'+AND+symbol%3D'BTC-USDT'+ORDER+BY+ts+DESC+LIMIT+500` returns rows;
**And** dcc.Store (`id='candle-store'`) is populated with a JSON-serialised list of those rows in ascending `ts` order.

**Given** the symbol switcher,
**When** the user selects a different symbol,
**Then** dcc.Store is cleared immediately (set to `[]`);
**And** `dcc.Loading` wrappers show spinners over all panel placeholder divs;
**And** a new QuestDB query fires for the selected (exchange, symbol) pair;
**And** dcc.Store repopulates with the new symbol's history when the query completes.

**Given** QuestDB returns zero rows (new symbol, no data yet),
**When** the query completes,
**Then** dcc.Store is set to `[]` and panels render an empty state (no crash, no Plotly exception).

**Given** QuestDB is unreachable,
**When** the history fetch fires,
**Then** the error is logged; dcc.Store remains `[]`; panels show empty state with no crash.

---

### Story 18.2b: Data Layer — Live Poll + ob_features Stream

As a developer,
I want the data layer to poll QuestDB every second for new candles and tail the Redis ob_features stream,
So that all panels update live without the dashboard reloading.

**Acceptance Criteria:**

**Given** live data is flowing,
**When** `dcc.Interval(id='live-interval', interval=1000)` fires,
**Then** the callback queries `snapshot_1s` for rows with `ts > last_known_ts`;
**And** new rows are appended to `candle-store` (no duplicates — deduplication on `ts`);
**And** `last_known_ts` is updated to the maximum `ts` in the store.

**Given** the ob_features Redis stream,
**When** the interval fires,
**Then** `XREAD COUNT 100 STREAMS ob_features:{exchange}:{symbol} {cursor_id}` is called;
**And** new entries are appended to `ob-store` (`id='ob-store'`);
**And** cursor ID is advanced to the latest entry ID returned.

**Given** ADR-18-02 cursor strategy,
**When** a symbol is first loaded,
**Then** `XREVRANGE ob_features:{exchange}:{symbol} + - COUNT 1` is called to seed the cursor with the latest entry ID;
**And** if the stream is empty, cursor is set to `'0'` (read from beginning).

**Given** symbol change (ADR-18-04),
**When** the user selects a new symbol,
**Then** both `candle-store` and `ob-store` are cleared;
**And** `dcc.Loading` spinners display on all panels;
**And** the history fetch (18.2a) fires for the new symbol;
**And** the ob_features cursor resets to the latest entry for the new symbol.

**Given** Redis is unreachable during a live poll,
**When** the XREAD call fails,
**Then** the error is logged; ob-store retains its existing data; panels do not crash or go blank.

---

### Story 18.3: Candlestick Panel — OHLCV + Volume Overlays

As a trader,
I want a candlestick panel with volume level, bubble, and absorption overlays,
So that I can read price action and volume concentration in a single view.

**Pre-conditions:** Story 18.1 complete (subplot grid at `row=1, col=1`). Story 18.2a complete (candle-store populated). Panel built at correct 38% column width from day one (grey `dbc.Card` placeholder at col=2 and col=3).

**Acceptance Criteria:**

**Given** candle-store is populated,
**When** the candlestick callback fires,
**Then** `go.Candlestick` renders OHLCV data at subplot `row=1, col=1`; up-candles green, down-candles red, on dark background.

**Given** candle-store contains `buy_volume` and `volume` columns from `snapshot_1s`,
**When** the volume levels overlay renders,
**Then** semi-transparent horizontal `go.Bar` traces (opacity ≤ 0.4) are overlaid on the candlestick panel at price levels derived from the QuestDB data; the price level with the highest total volume is highlighted in a distinct accent colour (POC); the range covering 70% of volume is shaded (Value Area).

**Given** candle-store contains `buy_volume` and `volume`,
**When** the volume bubbles overlay renders,
**Then** `go.Scatter` markers appear on the candlestick; marker size scales with `volume`; colour is green when `buy_volume / volume > 0.5`, red otherwise.

**Given** the absorption stub requirement (FR9),
**When** the panel renders,
**Then** the absorption marker callback exists in `callbacks.py` and runs without error; it renders zero markers because `absorption_detected` is not present in the data; the code is structured to render a diamond marker when the field is available (Epic 19 will provide it).

**Given** a live poll appends new candles to candle-store,
**When** the next Interval fires,
**Then** the candlestick updates to show the new candle appended at the right edge.

---

### Story 18.4: Heatmap Panels — Delta + OB Depth + Volume Profile

As a trader,
I want Delta Heatmap, OB Depth Heatmap, and Volume Profile panels with a shared price Y-axis,
So that I can read order flow pressure and resting liquidity aligned to the candlestick price levels.

**Pre-conditions:** Stories 18.1, 18.2a, 18.2b complete. ADR-18-01 grid in place (col=3 is rowspan=2).

**Acceptance Criteria:**

**Given** candle-store with `buy_volume` and `volume`,
**When** the Delta Heatmap callback fires,
**Then** a `go.Heatmap` renders at subplot col=3 using a diverging red/green colorscale centred at zero; cell value = `2 * buy_volume - volume` per candle; price on Y-axis, time on X-axis.
**And** the heatmap matrix is assembled purely in Dash from existing QuestDB fields — no delta computation occurs in the candle service.

**Given** ob-store containing `ob_features` entries,
**When** the OB Depth Heatmap renders,
**Then** a `go.Heatmap` using `bid_depth_l1 + ask_depth_l1` (or `bid_depth_total + ask_depth_total`) as colour intensity renders in the same col=3 subplot below the Delta panel.
**And** the two heatmaps share a single Y-axis (by virtue of the `rowspan=2` col-3 subplot).

**Given** ADR-18-03 (Y-axis sync via relayoutData),
**When** the user zooms the candlestick X or Y axis,
**Then** a `relayoutData` callback updates the col=3 subplot's Y-axis range to match the candlestick's visible price range within 2 seconds.
**And** if callback latency causes visible jitter during testing, the zoom sync feature is explicitly marked as a known limitation and deferred — the story does not fail on this condition.

**Given** candle-store,
**When** the Volume Profile callback fires,
**Then** a horizontal `go.Bar` (`orientation='h'`) renders at subplot `row=1, col=2`; price on Y-axis (shared with the candlestick axis by Plotly's `shared_yaxes='rows'` setting); bar length = total `volume` per price level.

**Given** an empty ob-store (ob_features Redis stream unavailable),
**When** the OB Depth Heatmap callback fires,
**Then** the panel renders an empty heatmap with axis labels and no crash.

---

### Story 18.5: Secondary Panels — CVD + Bid/Ask Volume

As a trader,
I want CVD and Bid/Ask Volume panels in the bottom row,
So that I can read cumulative aggressor direction and volume-side dominance over time.

**Pre-conditions:** Stories 18.2a, 18.2b complete. candle-store contains `buy_volume` and `volume` from `snapshot_1s`.

**Acceptance Criteria:**

**Given** candle-store contains `buy_volume` and `volume`,
**When** the CVD panel renders,
**Then** CVD is computed in Dash as `cumsum(2 * buy_volume - volume)` over the candle-store rows in ascending `ts` order;
**And** a `go.Scatter` with `fill='tozeroy'` renders at subplot `row=2, col=1`; fill is green when CVD > 0, red when CVD ≤ 0.
**And** no `cvd_divergence` markers are rendered in Epic 18 (field not in candle service output; divergence markers deferred to a future story when field is available).

**Given** candle-store contains `buy_volume` and `volume`,
**When** the Bid/Ask Volume panel renders,
**Then** ask bars (`buy_volume`) extend above zero as green `go.Bar` at subplot `row=2, col=2`;
**And** bid bars (`volume - buy_volume`) extend below zero as red `go.Bar`;
**And** a `go.Scatter` ratio line (`buy_volume / volume`) renders on a secondary Y-axis (0–1 scale) declared via `make_subplots(specs=[...[{"secondary_y": True}]...])` for the bottom-right subplot.

**Given** the secondary Y-axis requirement,
**When** `app.py` `make_subplots` call is inspected,
**Then** the `specs` parameter for the bottom row includes `{"secondary_y": True}` for the Bid/Ask subplot position.

**Given** a candle where `buy_volume` is `None` / `NaN`,
**When** the panels render,
**Then** that candle is excluded from the CVD cumsum and Bid/Ask bars without crashing; the panels remain functional.

---

### Story 18.6: Liquidity Overlay + Full Layout Assembly

As a developer,
I want the complete dashboard layout assembled with all panels at correct proportions and the liquidity overlay stub in place,
So that the dashboard matches the agreed design end-to-end and is ready for frontend retirement.

**Pre-conditions:** All of stories 18.1–18.5 complete and passing.

**Acceptance Criteria:**

**Given** ADR-18-01 layout proportions (38/12/50, 70/30),
**When** the full layout renders,
**Then** the page matches: top row = candlestick (38%) | volume profile (12%) | stacked heatmaps (50%); bottom row = CVD (50%) | Bid/Ask Volume (50%); symbol dropdown above the chart area.
**And** all panels are visible simultaneously without scrolling on a 1920×1080 display.

**Given** the liquidity overlay stub (FR16),
**When** `callbacks.py` is inspected,
**Then** a `liquidity_overlay` callback function exists that would render a POC horizontal line and Value Area band when `poc_price`, `value_area_high`, `value_area_low` fields are present in candle-store; in Epic 18, it renders nothing (fields absent); the stub is structured to activate in Epic 20 without layout changes.

**Given** the fully assembled dashboard,
**When** `make up` runs from the repo root,
**Then** `curl localhost:8050` returns HTTP 200;
**And** all five chart panels render with data (or empty-state if no data);
**And** switching symbol updates all panels within 3 seconds.

**Given** this story is complete,
**When** the developer signs off,
**Then** the `frontend/` directory and Vite service are still present (retirement is Story 18.7, which runs in the same sprint).

---

### Story 18.7: Retire Vite Frontend

As a developer,
I want the old Vite/TypeScript frontend removed from the repository and docker-compose,
So that there is a single, unambiguous frontend entry point at port 8050.

**Pre-conditions:** Story 18.6 complete and signed off. Dashboard verified working end-to-end.

**Acceptance Criteria:**

**Given** Story 18.6 is signed off,
**When** this story runs,
**Then** the `frontend/` directory is deleted from the repository root.
**And** the Vite service entry (and any associated port 5173 mapping) is removed from `docker-compose.yml`.
**And** any `make` targets referencing the frontend or port 5173 are removed or updated.

**Given** the updated docker-compose,
**When** `make up` runs,
**Then** no Vite-related container starts;
**And** `curl localhost:5173` returns connection refused;
**And** `curl localhost:8050` returns HTTP 200 with the Dash dashboard.

**Given** the repo state after this story,
**When** the README is inspected,
**Then** the Frontend section references port 8050 and the Dash dashboard; no mention of Vite, port 5173, or TypeScript frontend remains.
