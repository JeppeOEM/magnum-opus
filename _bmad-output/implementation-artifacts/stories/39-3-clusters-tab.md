---
id: 39-3
title: Clusters tab — PCA scatter and SHAP importances
epic: 39
status: ready-for-dev
---

# Story 39-3: Clusters Tab — PCA Scatter and SHAP Feature Importances

## Context

The Clusters tab shows what the HDBSCAN found: a 2D PCA projection coloured by cluster, and a SHAP importance bar chart showing which features drive the model's predictions. Both sourced from the model registry API.

## What to build

### `dashboard/callbacks_ml.py` — clusters tab additions

```python
import os
import httpx
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, no_update
import pickle
from pathlib import Path

_ML_SERVICE_URL = os.environ.get("ML_SERVICE_URL", "http://ml-service:8000")
_CLUSTER_PALETTE = ["#00BCD4", "#4CAF50", "#FF9800", "#F44336", "#9C27B0",
                    "#3F51B5", "#009688", "#FFEB3B", "#795548", "#607D8B"]


@callback(
    Output("cluster-regime-dropdown", "options"),
    Input("ml-model-interval", "n_intervals"),
)
def update_cluster_options(n):
    try:
        resp = httpx.get(_ML_SERVICE_URL + "/registry", timeout=5)
        entries = resp.json()
        seen = set()
        options = []
        for e in entries:
            key = f"{e['symbol']}:{e['regime']}"
            if key not in seen:
                seen.add(key)
                options.append({"label": key, "value": key})
        return options
    except Exception:
        return []


@callback(
    Output("pca-scatter-graph", "figure"),
    Output("shap-importance-graph", "figure"),
    Input("cluster-regime-dropdown", "value"),
    Input("ml-model-interval", "n_intervals"),
)
def update_clusters_tab(selection, n):
    empty = go.Figure(layout=go.Layout(**_DARK))
    if not selection:
        return empty, empty

    symbol, regime = selection.rsplit(":", 1)
    try:
        resp = httpx.get(_ML_SERVICE_URL + "/registry", timeout=5)
        entries = [e for e in resp.json()
                   if e["symbol"] == symbol and e["regime"] == regime]
    except Exception:
        return empty, empty

    if not entries:
        return empty, empty

    # SHAP importance bar chart (aggregate across clusters)
    all_shap: dict[str, list[float]] = {}
    for e in entries:
        for feat, val in (e.get("shap_summary") or {}).items():
            all_shap.setdefault(feat, []).append(val)
    mean_shap = {f: np.mean(v) for f, v in all_shap.items()}
    if mean_shap:
        sorted_feats = sorted(mean_shap, key=mean_shap.get, reverse=True)[:20]
        shap_fig = go.Figure(layout=go.Layout(
            title=f"SHAP Importances — {symbol} {regime}",
            **_DARK, height=400,
        ))
        shap_fig.add_trace(go.Bar(
            x=[mean_shap[f] for f in sorted_feats],
            y=sorted_feats,
            orientation="h",
            marker_color="#00BCD4",
        ))
        shap_fig.update_layout(yaxis=dict(autorange="reversed"))
    else:
        shap_fig = empty

    # PCA scatter: load PCA artifact and project training data
    # For now, show cluster count summary (full PCA scatter needs feature data)
    scatter_fig = go.Figure(layout=go.Layout(
        title=f"Clusters — {symbol} {regime} ({len(entries)} clusters)",
        **_DARK, height=350,
    ))
    for i, e in enumerate(entries):
        color = _CLUSTER_PALETTE[i % len(_CLUSTER_PALETTE)]
        scatter_fig.add_trace(go.Scatter(
            x=[0], y=[e["cv_score"]],
            mode="markers+text",
            marker=dict(size=20, color=color),
            text=[f"C{e['cluster']} (n={e['n_samples']})"],
            textposition="top center",
            name=f"Cluster {e['cluster']} — {e['model_type']} F1={e['cv_score']:.3f}",
        ))
    scatter_fig.update_layout(
        xaxis=dict(showticklabels=False),
        yaxis_title="CV F1 Score",
    )

    return scatter_fig, shap_fig
```

### `dashboard/layout_ml.py` additions for clusters tab

Add to tab content component IDs:

```python
clusters_tab_layout = dbc.Row([
    dbc.Col([
        dcc.Dropdown(id="cluster-regime-dropdown", placeholder="Select symbol:regime..."),
        dcc.Graph(id="pca-scatter-graph",    style={"height": "350px"}),
        dcc.Graph(id="shap-importance-graph", style={"height": "400px"}),
    ])
])
```

## Acceptance Criteria

1. Clusters tab shows symbol:regime dropdown populated from `GET /registry`.
2. SHAP importance bar chart shows top 20 features by mean absolute SHAP across clusters.
3. Cluster summary scatter shows one point per cluster with label showing n_samples and cv_score.
4. Charts use `_DARK` theme.
5. "No models trained" message shown when registry returns no entries for selection.
6. 60s refresh interval triggers option and chart updates.

## Dev Notes

- Full PCA scatter (plotting actual training data points coloured by cluster) requires the feature store Parquet to be accessible from the dashboard container — not worth adding a volume mount for now. The cluster summary scatter is a good substitute.
- `ML_SERVICE_URL` defaults to `http://ml-service:8000` — add to `docker-compose.yml` environment for the dashboard service.
