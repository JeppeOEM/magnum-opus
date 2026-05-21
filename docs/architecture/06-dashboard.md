# Chapter 06 — Dashboard

**30-second summary:** The dashboard is a Python Dash/Plotly app with three pages:
Charts (live candlestick + depth heatmap + footprint + signals), Bots (strategy
status, open orders, positions, PnL), and Backtests (run and view backtest results).
It polls QuestDB every second for new candle data and reads the Redis heatmap ZADD
data for the order book depth visualisation.

All source lives under `dashboard/`.

---

## 1. Package Structure

```
dashboard/
├── app.py                  Dash app init + import all callbacks
├── layout.py               Main nav + Charts page layout
├── layout_bots.py          Bots page layout
├── layout_backtest.py      Backtests page layout
├── callbacks.py            Charts page callbacks (candles, heatmap, footprint)
├── callbacks_bots.py       Bots page callbacks (strategies, orders, PnL)
├── callbacks_backtest.py   Backtests page callbacks (run, list, detail)
├── charts.py               Plotly figure builders
├── data.py                 QuestDB HTTP query helpers + Redis OB fetch
├── bot_data.py             QuestDB queries for bot/order data
└── backtest_data.py        QuestDB queries for backtest results
```

---

## 2. App Initialization

[`dashboard/app.py`](../../dashboard/app.py)

```python
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    suppress_callback_exceptions=True,   # tabs load lazily
)
app.layout = layout
import callbacks, callbacks_bots, callbacks_backtest  # registers all @callback decorators
```

The app runs on host `0.0.0.0:8050`. The `suppress_callback_exceptions=True` flag is
needed because tab pages are rendered lazily — the callbacks for bots/backtests are
registered before their components are added to the DOM.

---

## 3. Page Routing

[`dashboard/callbacks.py`](../../dashboard/callbacks.py) — `render_page()` callback:

```
URL = /           → _charts_page
URL = /bots       → bot_page_layout
URL = /backtests  → backtest_page_layout
```

Navigation via `dcc.Location` + `dbc.Nav` pills.

---

## 4. Charts Page

### Layout [`layout.py`](../../dashboard/layout.py)

| Component | ID | Description |
|-----------|----|-------------|
| `symbol-dropdown` | `dcc.Dropdown` | Exchange:symbol selector (KuCoin/Bybit) |
| `tf-dropdown` | `dcc.Dropdown` | Timeframe: 1s through 1w |
| `candlestick-graph` | `dcc.Graph` | Main OHLCV candlestick |
| `vol-profile-graph` | `dcc.Graph` | Volume profile (price histogram) |
| `cvd-graph` | `dcc.Graph` | Cumulative Volume Delta |
| `bidask-graph` | `dcc.Graph` | Bid volume vs Ask volume per bar |
| `signals-graph` | `dcc.Graph` | Signal indicator panel |
| `heatmap-graph` | `dcc.Graph` | Delta heatmap + OB depth |
| `footprint-modal` | `dbc.Modal` | Footprint chart (click on a candle) |
| `live-interval` | `dcc.Interval` | 1 s polling trigger |
| `candle-store` | `dcc.Store` | Last 500 candle rows (client-side cache) |
| `ob-store` | `dcc.Store` | Last 1000 OB tick rows |

### Data Flow

**On symbol/TF change** (`update_candle_store`):
1. `data.fetch_history(exchange, symbol, questdb_url, tf)` — SQL query against
   `snapshot_1s` (or aggregated view for multi-minute TFs) for last 500 rows.
2. `data.fetch_ob_snapshot(exchange, symbol)` — gets OB cursor from Redis ZSET.
3. Stores rows in `candle-store`, `ob-store`.

**On every 1 s tick** (`live_update`):
1. `data.fetch_new_candles(exchange, symbol, last_ts, questdb_url, tf)` — fetches
   only rows newer than `last_ts` (timestamp cursor pattern).
2. `data.fetch_ob_live(exchange, symbol, cursor)` — reads new OB ticks from Redis
   ZADD heatmap data since the last cursor.
3. Appends to stores, trimming to last 500/1000.

**Chart updates** (all triggered by `candle-store` changing):
- `update_candlestick` → `charts.build_candlestick()` + `absorption_overlay()` +
  `liquidity_overlay()` + volume levels + volume bubbles.
- `update_heatmap` → `charts.build_delta_heatmap()` + `charts.add_ob_depth_heatmap()`.
- `update_vol_profile` → `charts.build_vol_profile()`.
- `update_cvd` → `charts.build_cvd_panel()`.
- `update_bidask` → `charts.build_bidask_panel()`.
- `update_signals` → `charts.build_signals_panel()`.

### Y-Axis Sync

`sync_yaxis_zoom` callback: when the user zooms the candlestick chart, the
`relayoutData` event carries `yaxis.range[0/1]` which is applied to the heatmap's
Y axis so both charts stay in sync.

### Footprint Modal

Clicking a candle bar triggers `open_footprint_modal`:
1. Matches the clicked timestamp to a row in `candle-store`.
2. Reads `footprint_json` from the row (the JSON blob stored in QuestDB).
3. Calls `charts.build_footprint_chart(fp_json)`.
4. Opens `dbc.Modal` with the footprint figure.

The footprint shows bid volume and ask volume at each price level within that second,
like a DOM-replay view of the bar.

---

## 5. Bots Page

[`callbacks_bots.py`](../../dashboard/callbacks_bots.py), [`layout_bots.py`](../../dashboard/layout_bots.py)

Shows live strategy state by querying:
- **Bot service API** (`GET /strategies`) — strategy names, status (running/restarting/stopped).
- **QuestDB** `order_events` — recent orders, fills, PnL.
- **QuestDB** `order_events` aggregated — daily PnL per strategy.

Refreshes on a separate interval (e.g., 5 s).

---

## 6. Backtests Page

[`callbacks_backtest.py`](../../dashboard/callbacks_backtest.py), [`layout_backtest.py`](../../dashboard/layout_backtest.py)

- **Run backtest:** `POST /backtest` to bot service with strategy file + symbol + date range.
- **List runs:** queries `backtest_runs` from QuestDB.
- **View equity curve:** queries `backtest_trades` and plots cumulative PnL.

---

## 7. Chart Builders

[`dashboard/charts.py`](../../dashboard/charts.py)

| Function | Output |
|----------|--------|
| `build_candlestick(df)` | Plotly `go.Candlestick` with Plotly template |
| `add_volume_levels(fig, df)` | Horizontal volume histograms at price levels |
| `add_volume_bubbles(fig, df)` | Circles scaled to trade volume at each bar |
| `build_delta_heatmap(df)` | `go.Heatmap` of buy/sell delta over time vs price |
| `add_ob_depth_heatmap(fig, ob_rows)` | Semi-transparent OB depth overlaid on heatmap |
| `add_iceberg_borders(fig, df)` | Markers where `iceberg_detected=True` |
| `build_vol_profile(df)` | Horizontal bar chart of volume by price |
| `build_cvd_panel(df)` | Line chart of cumulative buy − sell volume |
| `build_bidask_panel(df)` | Stacked area chart of buy vs sell volume |
| `build_signals_panel(df)` | Multi-line signals (OFI, spread, etc.) |
| `build_footprint_chart(fp_json, ...)` | Clustered bar at each price: bid vs ask vol |

---

## 8. Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `QUESTDB_HTTP_ADDR` | `http://questdb:9000` | QuestDB HTTP queries |
| `REDIS_URL` | `redis://redis:6379` | Redis OB heatmap data |
| `BOT_SERVICE_URL` | `http://bot:8090` | Bot service API |

---

## 9. Supported Symbols (hardcoded in layout.py)

```python
SYMBOL_OPTIONS = [
    {"label": "BTC-USDT (KuCoin)", "value": "kucoin:BTC-USDT"},
    {"label": "ETH-USDT (KuCoin)", "value": "kucoin:ETH-USDT"},
    {"label": "BTC-USDT (Bybit)",  "value": "bybit:BTC-USDT"},
]
```

To add a symbol, extend `SYMBOL_OPTIONS` in [`layout.py`](../../dashboard/layout.py:4).
The candle and aggregator services are configured separately via `SYMBOLS` env var.
