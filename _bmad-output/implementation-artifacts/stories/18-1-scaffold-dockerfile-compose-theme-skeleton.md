# Story 18.1: Dashboard Service Scaffold

Status: done

## Story

As a developer,
I want a runnable `dashboard/` Python service skeleton in docker-compose with the correct subplot grid structure,
So that subsequent stories have a stable structural foundation to build panels into.

**Pre-conditions:** Epic 19 complete (all 17 new footprint/signal fields in QuestDB snapshot_1s).

## Acceptance Criteria

**AC 1 — Container starts and responds on port 8050**
- `make up` starts a `dashboard` container on port 8050
- `curl localhost:8050` returns HTTP 200
- `curl localhost:8050/_dash-layout` returns a valid JSON layout response (Dash built-in endpoint)

**AC 2 — Directory structure**
`dashboard/` contains:
- `Dockerfile` — multi-stage Python 3.12-slim build
- `requirements.txt` — pinned versions: dash, dash-bootstrap-components, plotly, redis-py, requests, pandas
- `app.py` — Dash app entry point with `make_subplots` call matching ADR-18-01 exactly
- `layout.py` — page structure (app.layout)
- `callbacks.py` — empty stub functions for all 5 panel slots

**AC 3 — DARKLY theme**
- `external_stylesheets=[dbc.themes.DARKLY]` is the only theme constant used
- No ad-hoc dark CSS in any file

**AC 4 — ADR-18-01 make_subplots call**
`app.py` contains exactly:
```python
make_subplots(
    rows=2, cols=3,
    shared_yaxes='rows',
    column_widths=[0.38, 0.12, 0.50],
    row_heights=[0.70, 0.30],
    specs=[[{}, {}, {"rowspan": 2}], [{}, {}, None]]
)
```

**AC 5 — Dockerfile HEALTHCHECK**
Dockerfile includes:
```
HEALTHCHECK CMD curl -f http://localhost:8050/_dash-layout || exit 1
```

**AC 6 — Layout page**
- Symbol `dcc.Dropdown` at top of page above chart area
- Placeholder `dbc.Card` (grey) for each of the 5 panel positions:
  - Panel 1 (row=1, col=1): candlestick placeholder
  - Panel 2 (row=1, col=2): volume profile placeholder
  - Panel 3 (row=1,2 col=3): heatmaps placeholder
  - Panel 4 (row=2, col=1): CVD placeholder
  - Panel 5 (row=2, col=2): bid/ask placeholder
- `dcc.Loading` wrapper around each placeholder
- Vite frontend NOT removed (stays until Story 18.7)

**AC 7 — docker-compose entry**
`docker-compose.yml` gains a `dashboard` service with:
- `build: context: ./dashboard`
- `ports: - "8050:8050"`
- `depends_on: redis: condition: service_healthy` and `questdb: condition: service_healthy`
- `profiles: [dashboard]` — opt-in like bot, candle-blue etc.
- Environment: `QUESTDB_HTTP_ADDR: http://questdb:9000`, `REDIS_URL: redis://redis:6379`

## Tasks / Subtasks

- [ ] Task 1: Create dashboard/ directory structure
  - [ ] 1.1 Create `dashboard/requirements.txt` with pinned versions
  - [ ] 1.2 Create `dashboard/Dockerfile` (multi-stage, Python 3.12-slim, HEALTHCHECK)
  - [ ] 1.3 Create `dashboard/app.py` with Dash app init, DARKLY theme, make_subplots skeleton, Figure object
  - [ ] 1.4 Create `dashboard/layout.py` with page layout: symbol dropdown + 5 placeholder cards + dcc.Loading wrappers
  - [ ] 1.5 Create `dashboard/callbacks.py` with empty stub functions (pass bodies)

- [ ] Task 2: Wire docker-compose
  - [ ] 2.1 Add `dashboard` service entry to `docker-compose.yml` (profile: dashboard)
  - [ ] 2.2 Update Makefile `up` target to document port 8050

- [ ] Task 3: Verify
  - [ ] 3.1 `docker compose --profile dashboard build dashboard` succeeds
  - [ ] 3.2 Container starts and `curl localhost:8050` returns 200
  - [ ] 3.3 `curl localhost:8050/_dash-layout` returns JSON

## Dev Notes

### File structure to create

```
dashboard/
├── Dockerfile
├── requirements.txt
├── app.py
├── layout.py
└── callbacks.py
```

### requirements.txt — pinned versions

Use the same library versions the bot-service already pins where applicable. For new libraries, use latest stable as of 2026-05-11:

```
dash==2.18.2
dash-bootstrap-components==1.7.1
plotly==5.24.1
redis==7.4.0
requests==2.32.3
pandas==3.0.2
```

**Why pin:** All other services pin versions. Don't use loose `>=` constraints.

### app.py structure

```python
import dash
import dash_bootstrap_components as dbc
from plotly.subplots import make_subplots
import plotly.graph_objects as go

from layout import layout

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    suppress_callback_exceptions=True,
)

# ADR-18-01: subplot grid — established here, never changed by panel stories
BASE_FIGURE = make_subplots(
    rows=2, cols=3,
    shared_yaxes='rows',
    column_widths=[0.38, 0.12, 0.50],
    row_heights=[0.70, 0.30],
    specs=[[{}, {}, {"rowspan": 2}], [{}, {}, None]]
)

app.layout = layout

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=False)
```

**Important:** `BASE_FIGURE` is a module-level constant. Panel stories will call `go.Figure(BASE_FIGURE)` to copy this template then add traces. This prevents grid rewiring in later stories.

### layout.py structure

```python
import dash_bootstrap_components as dbc
from dash import dcc, html

SYMBOL_OPTIONS = [
    {"label": "BTC-USDT (KuCoin)", "value": "kucoin:BTC-USDT"},
    {"label": "ETH-USDT (KuCoin)", "value": "kucoin:ETH-USDT"},
    {"label": "BTC-USDT (Bybit)", "value": "bybit:BTC-USDT"},
]

layout = dbc.Container(
    [
        dbc.Row(dbc.Col(
            dcc.Dropdown(
                id="symbol-dropdown",
                options=SYMBOL_OPTIONS,
                value="kucoin:BTC-USDT",
                clearable=False,
            )
        )),
        dbc.Row([
            dbc.Col(dcc.Loading(dbc.Card("Candlestick", style={"height": "400px", "background": "#2a2a2a"})), width=5),
            dbc.Col(dcc.Loading(dbc.Card("Vol Profile", style={"height": "400px", "background": "#2a2a2a"})), width=2),
            dbc.Col(dcc.Loading(dbc.Card("Heatmaps", style={"height": "800px", "background": "#2a2a2a"})), width=5),
        ]),
        dbc.Row([
            dbc.Col(dcc.Loading(dbc.Card("CVD", style={"height": "300px", "background": "#2a2a2a"})), width=6),
            dbc.Col(dcc.Loading(dbc.Card("Bid/Ask", style={"height": "300px", "background": "#2a2a2a"})), width=6),
        ]),
    ],
    fluid=True,
)
```

**Note:** Placeholder cards are replaced by actual `dcc.Graph` components in Stories 18.3–18.6. The layout structure (row/col proportions) does NOT change.

### callbacks.py structure

Empty stubs only — no logic, no imports beyond dash:
```python
from dash import Input, Output, callback

@callback(Output("symbol-dropdown", "value"), Input("symbol-dropdown", "value"))
def on_symbol_change(value):
    pass
```

This file grows in each subsequent story. In 18.1, it only needs to exist and not crash on import.

### Dockerfile structure

Pattern from `bot-service/Dockerfile` — multi-stage build:

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app.py layout.py callbacks.py ./
RUN useradd -m -u 1000 dashboard && chown -R dashboard:dashboard /app
USER dashboard
HEALTHCHECK --interval=10s --timeout=5s --retries=3 CMD curl -f http://localhost:8050/_dash-layout || exit 1
CMD ["python", "app.py"]
```

**Important:** `curl` must be installed in the final stage for HEALTHCHECK to work (Python slim has no curl). Add `apt-get install -y curl` before running as non-root user.

### docker-compose.yml entry

Add after the `bot` service entry:

```yaml
  dashboard:
    profiles: [dashboard]
    build:
      context: ./dashboard
    environment:
      QUESTDB_HTTP_ADDR: http://questdb:9000
      REDIS_URL: redis://redis:6379
    ports:
      - "8050:8050"
    depends_on:
      redis:
        condition: service_healthy
      questdb:
        condition: service_healthy
    deploy:
      resources:
        limits:
          memory: 512m
    restart: unless-stopped
```

### Makefile updates

Update the `up` target print block to include dashboard:
```makefile
@printf   "  %-14s %s\n"  "dashboard"     "http://localhost:8050   (start with: --profile dashboard)"
```

Also update `down` target to include `--profile dashboard`:
```makefile
down:
    docker compose --profile candle-blue --profile candle-green --profile bot --profile dashboard down
```

Add new `dev-dashboard` target for local dev:
```makefile
## Run the Dash dashboard (requires local Redis + QuestDB)
dev-dashboard:
    cd dashboard && \
    QUESTDB_HTTP_ADDR=http://localhost:9000 \
    REDIS_URL=redis://localhost:6379 \
    python app.py
```

### Data sources for future stories (reference)

Panel stories build into this scaffold:
- **candle-store** (`id='candle-store'`, `dcc.Store`) — QuestDB `snapshot_1s` rows as list of dicts
- **ob-store** (`id='ob-store'`, `dcc.Store`) — Redis `ob_features:{exchange}:{symbol}` entries
- **symbol**: parsed from dropdown value `"kucoin:BTC-USDT"` → `exchange="kucoin"`, `symbol="BTC-USDT"`
- **QuestDB REST**: `GET http://questdb:9000/exec?query=...` returns `{"dataset": [[...]], "columns": [...]}`
- **Redis ob_features stream key**: `ob_features:{exchange}:{symbol}`

Note: `dcc.Store` components are declared in `layout.py` alongside the visible layout (added in Story 18.2a when data layer is implemented). This story's layout.py does NOT include them — they're added in 18.2a.

### Symbol format convention

All subsequent stories parse the dropdown value as `exchange:symbol`. The split logic:
```python
exchange, symbol = selected.split(":", 1)  # "kucoin:BTC-USDT" → ("kucoin", "BTC-USDT")
```
This convention is established by the dropdown `value` format here and must be maintained.

### What this story does NOT do
- No data fetching (18.2a)
- No live polling (18.2b)
- No actual chart content (18.3–18.6)
- No frontend retirement (18.7)
- No dcc.Store declarations (18.2a)

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

### File List

- `dashboard/Dockerfile` — NEW
- `dashboard/requirements.txt` — NEW
- `dashboard/app.py` — NEW
- `dashboard/layout.py` — NEW
- `dashboard/callbacks.py` — NEW
- `docker-compose.yml` — UPDATED (dashboard service added with profile: dashboard)
- `Makefile` — UPDATED (dev-dashboard target, down profile update, .PHONY update)

### Review Findings

- [x] [Review][Patch] P1: `import callbacks` was before `app = dash.Dash(...)` — future stories' `from app import app` would fail with circular import. Moved import to bottom of `app.py` after `app.layout = layout` [`dashboard/app.py`] — applied
- [x] [Review][Patch] P2: `dev-dashboard` Makefile target used bare `python` (not in PATH on Linux) — changed to `.venv/bin/python` with setup instructions matching bot-service pattern [`Makefile`] — applied
- [x] [Review][Defer] `shared_yaxes='rows'` couples CVD y-axis with Bid/Ask y-axis in row 2 — ADR-18-01 spec; row-2 panel stories will need explicit `update_yaxes` overrides — deferred to 18.5
- [x] [Review][Defer] Bootstrap column widths (5+2+5=41.7/16.7/41.7%) don't match Plotly subplot proportions (38/12/50%) — placeholders only, real `dcc.Graph` components replace them in panel stories — deferred, by design
