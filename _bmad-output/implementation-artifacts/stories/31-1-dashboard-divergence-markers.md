---
id: 31-1
title: Dashboard divergence signal markers on CVD panel
epic: 31
status: done
---

# Story 31-1: Dashboard divergence signal markers on CVD panel

## Context

`cvd_divergence` and `footprint_delta_divergence` are int32 signal columns (`-1` bearish, `0` none, `+1` bullish) computed by the candle-service and written to `snapshot_1s`. They are already fetched in `data.py`'s SQL select (lines 155–158). `build_cvd_panel` in `charts.py` only renders the CVD area chart — these two signals are fetched but silently dropped.

## What to build

### `dashboard/charts.py` — extend `build_cvd_panel`

After the existing CVD area traces, add two scatter-marker traces when the divergence columns are present:

```python
def _add_divergence_markers(fig, df, col, marker_symbol_up, marker_symbol_down, color_up, color_down, name):
    if col not in df.columns:
        return
    vals = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    bull = df[vals == 1]
    bear = df[vals == -1]
    if not bull.empty:
        fig.add_trace(go.Scatter(
            x=bull["ts"], y=bull["cvd"],
            mode="markers",
            marker=dict(symbol=marker_symbol_up, size=10, color=color_up),
            name=name + " bull",
            showlegend=False,
        ))
    if not bear.empty:
        fig.add_trace(go.Scatter(
            x=bear["ts"], y=bear["cvd"],
            mode="markers",
            marker=dict(symbol=marker_symbol_down, size=10, color=color_down),
            name=name + " bear",
            showlegend=False,
        ))
```

Call after the existing CVD area traces in `build_cvd_panel`:
```python
_add_divergence_markers(fig, df, "cvd_divergence",
    "triangle-up", "triangle-down", "#00E676", "#FF1744", "CVD-div")
_add_divergence_markers(fig, df, "footprint_delta_divergence",
    "diamond", "diamond", "#69F0AE", "#FF6D00", "FP-div")
```

Both helpers must gracefully no-op when columns are absent or all-zero.

## Acceptance Criteria

1. When `cvd_divergence == 1` rows exist, green `triangle-up` markers appear on the CVD panel at the corresponding timestamps.
2. When `cvd_divergence == -1` rows exist, red `triangle-down` markers appear.
3. When `footprint_delta_divergence == 1` rows exist, light-green `diamond` markers appear.
4. When `footprint_delta_divergence == -1` rows exist, orange `diamond` markers appear.
5. When either column is absent from the dataframe (old data), no error is raised.
6. When all values are 0 (no divergence bars), no markers are added and no error is raised.

## Dev Notes

- `build_cvd_panel` is in `dashboard/charts.py:201`. The df passed to it has already been `sort_values("ts")` by the time it reaches the helper.
- `df["cvd"]` is computed inside `build_cvd_panel` — the marker y-value should be `df["cvd"]` at the divergence bar so markers sit on the CVD line.
- Columns `cvd_divergence` and `footprint_delta_divergence` come from QuestDB as strings (all QuestDB HTTP returns are strings); use `pd.to_numeric(..., errors="coerce")` before comparison.
- No new dependencies. Plotly is already imported.

### Review Findings

- [x] Clean review — all 6 findings dismissed (ts/cvd guaranteed non-null by build_cvd_panel dropna guard; int truncation impossible from QuestDB int columns; pandas boolean-index alignment is guaranteed)

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### File List

- `dashboard/charts.py`
