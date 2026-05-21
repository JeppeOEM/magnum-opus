---
id: 31-2
title: Dashboard imbalance and auction signals panel
epic: 31
status: done
---

# Story 31-2: Dashboard imbalance and auction signals panel

## Context

`imbalance_ratio` (float, -1 to +1), `imbalance_stack_buy`, `imbalance_stack_sell`, `unfinished_top` (bool), and `unfinished_bottom` (bool) are fetched from QuestDB in `data.py` (lines 142–158) but never rendered in any chart. This story adds a compact signals panel below the CVD+bidask row showing imbalance and unfinished-auction signals.

## What to build

### `dashboard/charts.py` — add `build_signals_panel`

```python
def build_signals_panel(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**_DARK, uirevision="signals",
                      margin=dict(l=40, r=10, t=10, b=30), barmode="overlay")
    if df.empty or "ts" not in df.columns or "imbalance_ratio" not in df.columns:
        return fig
    df = df.copy()
    df["imbalance_ratio"] = pd.to_numeric(df["imbalance_ratio"], errors="coerce").fillna(0)
    df = df.sort_values("ts")

    pos = df["imbalance_ratio"].clip(lower=0)
    neg = df["imbalance_ratio"].clip(upper=0)
    fig.add_trace(go.Bar(x=df["ts"], y=pos, name="Buy imb",
                         marker_color="#26A69A", showlegend=False))
    fig.add_trace(go.Bar(x=df["ts"], y=neg, name="Sell imb",
                         marker_color="#EF5350", showlegend=False))

    _TRUE_VALS = {True, "true", "True", 1, "1"}

    if "unfinished_top" in df.columns:
        ut = df[df["unfinished_top"].isin(_TRUE_VALS)]
        if not ut.empty:
            fig.add_trace(go.Scatter(
                x=ut["ts"], y=[1.0] * len(ut), mode="markers",
                marker=dict(symbol="triangle-down", size=8, color="#FF1744"),
                name="Unfinished top", showlegend=False,
            ))

    if "unfinished_bottom" in df.columns:
        ub = df[df["unfinished_bottom"].isin(_TRUE_VALS)]
        if not ub.empty:
            fig.add_trace(go.Scatter(
                x=ub["ts"], y=[-1.0] * len(ub), mode="markers",
                marker=dict(symbol="triangle-up", size=8, color="#00E676"),
                name="Unfinished bottom", showlegend=False,
            ))

    fig.update_yaxes(range=[-1.1, 1.1], zeroline=True, zerolinecolor="#555")
    return fig
```

### `dashboard/layout.py` — add signals-graph row

After the `dbc.Row([cvd-graph, bidask-graph])` block (after line 101), add:
```python
dbc.Row(
    dcc.Graph(id="signals-graph", figure={}, style={"height": "160px"}),
),
```

### `dashboard/callbacks.py` — add callback

```python
@callback(Output("signals-graph", "figure"), Input("candle-store", "data"))
def update_signals(candle_rows):
    df = pd.DataFrame(candle_rows or [])
    return charts.build_signals_panel(df)
```

## Acceptance Criteria

1. When `imbalance_ratio > 0` bars exist, green bars appear in the signals panel.
2. When `imbalance_ratio < 0` bars exist, red bars appear.
3. When `unfinished_top == true` rows exist, red triangle-down markers appear at y=1.0.
4. When `unfinished_bottom == true` rows exist, green triangle-up markers appear at y=-1.0.
5. When columns are missing, no error; empty figure returned.
6. Y-axis is fixed to [-1.1, 1.1].
7. `signals-graph` ID is present in `layout.py` and connected to a callback.

## Dev Notes

- `unfinished_top`/`unfinished_bottom` come from QuestDB as strings: `"true"` / `"false"`. Use `.isin({True, "true", "True", 1, "1"})` to normalise.
- `imbalance_ratio` is a float column; use `pd.to_numeric(..., errors="coerce").fillna(0)`.
- Keep the panel at 160px height — it's secondary context.

### Review Findings

- [x] Clean review — all 7 findings dismissed (imbalance_ratio is normalized to [-1,1] by design; QuestDB returns booleans as strings so float 1.0 cannot occur; marker placement at ±1.0 is within the ±1.1 axis range)

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### File List

- `dashboard/charts.py`
- `dashboard/layout.py`
- `dashboard/callbacks.py`
