---
id: 39-4
title: Models tab — registry table
epic: 39
status: ready-for-dev
---

# Story 39-4: Models Tab — Registry Table and CV Scores

## Context

Simple registry table giving a full inventory of all trained models with their performance metrics. Refreshes every 60s.

## What to build

### `dashboard/callbacks_ml.py` — models tab additions

```python
from dash import dash_table

@callback(
    Output("models-table", "data"),
    Output("models-table", "columns"),
    Input("ml-model-interval", "n_intervals"),
)
def update_models_tab(n):
    try:
        resp = httpx.get(_ML_SERVICE_URL + "/registry", timeout=5)
        entries = resp.json()
    except Exception:
        return [], []

    if not entries:
        return [], []

    rows = []
    for e in sorted(entries, key=lambda x: x.get("created_at", ""), reverse=True):
        rows.append({
            "Symbol":     e.get("symbol", ""),
            "Exchange":   e.get("exchange", ""),
            "Regime":     e.get("regime", ""),
            "Cluster":    e.get("cluster", ""),
            "Model":      e.get("model_type", ""),
            "CV F1":      f"{e.get('cv_score', 0):.3f}",
            "Samples":    e.get("n_samples", 0),
            "Created":    e.get("created_at", "")[:19],  # truncate microseconds
        })

    columns = [{"name": c, "id": c} for c in
               ["Symbol", "Exchange", "Regime", "Cluster", "Model", "CV F1", "Samples", "Created"]]
    return rows, columns
```

### Layout addition

```python
models_tab_layout = dbc.Row([
    dbc.Col([
        html.H6("Trained Models", className="mt-2"),
        dash_table.DataTable(
            id="models-table",
            style_table={"overflowX": "auto"},
            style_cell={"backgroundColor": "#333", "color": "#CCC",
                        "border": "1px solid #555", "textAlign": "left"},
            style_header={"backgroundColor": "#444", "fontWeight": "bold"},
            style_data_conditional=[
                {"if": {"filter_query": "{Model} = xgboost"},
                 "color": "#00BCD4"},
            ],
            page_size=20,
            sort_action="native",
        ),
    ])
])
```

## Acceptance Criteria

1. Models tab shows DataTable with columns: Symbol, Exchange, Regime, Cluster, Model, CV F1, Samples, Created.
2. Rows sorted by Created descending by default.
3. CV F1 formatted to 3 decimal places.
4. Table refreshes on 60s interval.
5. XGBoost model rows highlighted in `#00BCD4`.
6. Empty table (no crash) when registry returns no entries.
