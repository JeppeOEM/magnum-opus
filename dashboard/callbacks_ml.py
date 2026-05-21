"""ML page callbacks — tab content router and per-tab refresh logic.

Tab rendering via callback keeps initial page load fast (lazy content).
Per-tab content implemented in Stories 39-2 through 39-5.
"""
import os

import httpx
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import redis
from dash import Input, Output, callback, html, no_update

_QUESTDB_URL = os.environ.get("QUESTDB_HTTP_ADDR", "http://questdb:9000")
_ML_SERVICE_URL = os.environ.get("ML_SERVICE_URL", "http://ml-service:8000")
_REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")
_DARK = dict(paper_bgcolor="#222222", plot_bgcolor="#222222", font_color="#CCCCCC")
_CLUSTER_PALETTE = [
    "#00BCD4", "#4CAF50", "#FF9800", "#F44336", "#9C27B0",
    "#3F51B5", "#009688", "#FFEB3B", "#795548", "#607D8B",
]

REGIME_COLORS = {
    "TRENDING_UP":   "#4CAF50",
    "TRENDING_DOWN": "#F44336",
    "HIGH_VOL":      "#FF9800",
    "THIN_BOOK":     "#FFC107",
    "RANGING":       "#9E9E9E",
}


@callback(
    Output("ml-tab-content", "children"),
    Input("ml-tabs", "value"),
)
def render_ml_tab(tab: str):
    """Route ml-tabs value to the appropriate tab layout."""
    if tab == "data":
        from layout_ml_data import data_tab_layout
        return data_tab_layout
    if tab == "clusters":
        from layout_ml_clusters import clusters_tab_layout
        return clusters_tab_layout
    if tab == "models":
        from layout_ml_models import models_tab_layout
        return models_tab_layout
    if tab == "live":
        from layout_ml_live import live_tab_layout
        return live_tab_layout
    return no_update


# ── Data tab callbacks ────────────────────────────────────────────────────────

@callback(
    Output("regime-timeline-graph", "figure"),
    Output("microprice-deviation-graph", "figure"),
    Input("ml-slow-interval", "n_intervals"),
    Input("ml-symbol-dropdown", "value"),
)
def update_data_tab(n, selected):
    """Refresh regime timeline and microprice deviation charts every 30s."""
    _empty = go.Figure(layout=go.Layout(**_DARK))

    if not selected:
        return _empty, _empty

    try:
        exchange, symbol = selected.split(":", 1)
    except ValueError:
        return _empty, _empty

    sql = (
        f"SELECT ts, microprice_mid_delta "
        f"FROM snapshot_1s "
        f"WHERE exchange='{exchange}' AND symbol='{symbol}' "
        f"AND ts > dateadd('h', -24, now()) "
        f"ORDER BY ts"
    )
    try:
        resp = httpx.get(_QUESTDB_URL + "/exec", params={"query": sql}, timeout=10)
        resp.raise_for_status()
        js = resp.json()
        cols = [c["name"] for c in js["columns"]]
        df = pd.DataFrame(js["dataset"], columns=cols)
        df["ts"] = pd.to_datetime(df["ts"])
    except Exception:
        return _empty, _empty

    # Regime timeline — static indicator (full history in future story)
    regime_fig = go.Figure(
        layout=go.Layout(
            title=f"Current Regime — {exchange}:{symbol} (from Redis)",
            **_DARK,
            height=200,
            xaxis=dict(showgrid=False, showticklabels=False),
            yaxis=dict(showgrid=False, showticklabels=False),
            annotations=[
                dict(
                    text="RANGING",
                    x=0.5, y=0.5,
                    xref="paper", yref="paper",
                    showarrow=False,
                    font=dict(size=32, color=REGIME_COLORS["RANGING"]),
                )
            ],
        )
    )

    # Microprice deviation chart
    if not df.empty and "microprice_mid_delta" in df.columns:
        mp_vals = pd.to_numeric(df["microprice_mid_delta"], errors="coerce")
        dev_fig = go.Figure(
            layout=go.Layout(
                title="Microprice Deviation from Mid (bps) — last 24h",
                **_DARK,
                height=300,
                xaxis=dict(showgrid=True, gridcolor="#333"),
                yaxis=dict(showgrid=True, gridcolor="#333"),
            )
        )
        dev_fig.add_trace(
            go.Scatter(
                x=df["ts"],
                y=mp_vals,
                mode="lines",
                line=dict(color="#00BCD4", width=1),
                name="microprice_mid_delta",
            )
        )
        dev_fig.add_hline(y=0, line_dash="dot", line_color="#888888")
        dev_fig.add_hline(
            y=1.0, line_dash="dash", line_color="#4CAF5066",
            annotation_text="+1 bps",
        )
        dev_fig.add_hline(
            y=-1.0, line_dash="dash", line_color="#F4433666",
            annotation_text="-1 bps",
        )
    else:
        dev_fig = _empty

    return regime_fig, dev_fig


# ── Clusters tab callbacks ────────────────────────────────────────────────────

@callback(
    Output("cluster-regime-dropdown", "options"),
    Input("ml-model-interval", "n_intervals"),
)
def update_cluster_options(n):
    """Populate symbol:regime dropdown from the model registry."""
    try:
        resp = httpx.get(_ML_SERVICE_URL + "/registry", timeout=5)
        resp.raise_for_status()
        entries = resp.json()
        seen: set[str] = set()
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
    """Render cluster summary scatter and SHAP importance bar chart."""
    _empty = go.Figure(layout=go.Layout(**_DARK))
    if not selection:
        return _empty, _empty

    try:
        symbol, regime = selection.rsplit(":", 1)
    except ValueError:
        return _empty, _empty

    try:
        resp = httpx.get(_ML_SERVICE_URL + "/registry", timeout=5)
        resp.raise_for_status()
        entries = [
            e for e in resp.json()
            if e["symbol"] == symbol and e["regime"] == regime
        ]
    except Exception:
        return _empty, _empty

    if not entries:
        no_models_fig = go.Figure(
            layout=go.Layout(
                title=f"No models trained for {symbol} {regime}",
                **_DARK,
            )
        )
        return no_models_fig, no_models_fig

    # SHAP importance bar chart — aggregate across all clusters
    all_shap: dict[str, list[float]] = {}
    for e in entries:
        for feat, val in (e.get("shap_summary") or {}).items():
            all_shap.setdefault(feat, []).append(float(val))

    if all_shap:
        mean_shap = {f: float(np.mean(v)) for f, v in all_shap.items()}
        sorted_feats = sorted(mean_shap, key=mean_shap.__getitem__, reverse=True)[:20]
        shap_fig = go.Figure(
            layout=go.Layout(
                title=f"SHAP Importances — {symbol} {regime}",
                **_DARK,
                height=400,
            )
        )
        shap_fig.add_trace(
            go.Bar(
                x=[mean_shap[f] for f in sorted_feats],
                y=sorted_feats,
                orientation="h",
                marker_color="#00BCD4",
            )
        )
        shap_fig.update_layout(yaxis=dict(autorange="reversed"))
    else:
        shap_fig = _empty

    # Cluster summary scatter — one point per cluster showing n_samples and cv_score
    scatter_fig = go.Figure(
        layout=go.Layout(
            title=f"Clusters — {symbol} {regime} ({len(entries)} clusters)",
            **_DARK,
            height=350,
        )
    )
    for i, e in enumerate(entries):
        color = _CLUSTER_PALETTE[i % len(_CLUSTER_PALETTE)]
        scatter_fig.add_trace(
            go.Scatter(
                x=[i],
                y=[e["cv_score"]],
                mode="markers+text",
                marker=dict(size=20, color=color),
                text=[f"C{e['cluster']} (n={e['n_samples']})"],
                textposition="top center",
                name=f"Cluster {e['cluster']} — {e['model_type']} F1={e['cv_score']:.3f}",
            )
        )
    scatter_fig.update_layout(
        xaxis=dict(showticklabels=False, title="Cluster"),
        yaxis=dict(title="CV F1 Score", range=[0, 1.1]),
    )

    return scatter_fig, shap_fig


# ── Models tab callbacks ──────────────────────────────────────────────────────

@callback(
    Output("models-table", "data"),
    Output("models-table", "columns"),
    Input("ml-model-interval", "n_intervals"),
)
def update_models_tab(n):
    """Populate the models registry table every 60s."""
    try:
        resp = httpx.get(_ML_SERVICE_URL + "/registry", timeout=5)
        resp.raise_for_status()
        entries = resp.json()
    except Exception:
        return [], []

    if not entries:
        return [], []

    rows = []
    for e in sorted(entries, key=lambda x: x.get("created_at", ""), reverse=True):
        rows.append(
            {
                "Symbol":   e.get("symbol", ""),
                "Exchange": e.get("exchange", ""),
                "Regime":   e.get("regime", ""),
                "Cluster":  e.get("cluster", ""),
                "Model":    e.get("model_type", ""),
                "CV F1":    f"{e.get('cv_score', 0):.3f}",
                "Samples":  e.get("n_samples", 0),
                "Created":  (e.get("created_at") or "")[:19],  # strip microseconds
            }
        )

    columns = [
        {"name": c, "id": c}
        for c in ["Symbol", "Exchange", "Regime", "Cluster", "Model", "CV F1", "Samples", "Created"]
    ]
    return rows, columns


# ── Live tab helpers & callbacks ──────────────────────────────────────────────

SIGNAL_COLORS = {
    "REVERT": "rgba(76,175,80,0.2)",
    "TREND":  "rgba(244,67,54,0.2)",
    "FLAT":   "rgba(0,0,0,0)",
}


def _fetch_signals(symbol: str, count: int = 20) -> list[dict]:
    """Read last N entries from ai:{symbol}:signals Redis stream (sync)."""
    r = redis.from_url(_REDIS_URL, decode_responses=True)
    try:
        entries = r.xrevrange(f"ai:{symbol}:signals", count=count)
        rows = []
        for _id, fields in entries:
            rows.append(
                {
                    "Time":       (fields.get("ts") or "")[:19],
                    "Signal":     fields.get("signal", ""),
                    "Confidence": f"{float(fields.get('confidence', 0)):.1%}",
                    "Regime":     fields.get("regime", ""),
                    "Cluster":    fields.get("cluster", ""),
                    "Dev bps":    fields.get("deviation_bps", ""),
                    "Model ID":   (fields.get("model_id") or "")[:8],
                    "Latency ms": fields.get("latency_ms", ""),
                }
            )
        return rows
    except Exception:
        return []
    finally:
        r.close()


def _fetch_spread_signals(count: int = 60) -> list[dict]:
    """Read last N entries from ai:BTC-ETH-spread:signals."""
    r = redis.from_url(_REDIS_URL, decode_responses=True)
    try:
        return [
            {"ts": _id, **fields}
            for _id, fields in r.xrevrange("ai:BTC-ETH-spread:signals", count=count)
        ]
    except Exception:
        return []
    finally:
        r.close()


@callback(
    Output("live-signals-table", "data"),
    Output("live-signals-table", "style_data_conditional"),
    Output("deviation-gauge", "figure"),
    Output("spread-zscore-graph", "figure"),
    Input("ml-live-interval", "n_intervals"),
    Input("ml-symbol-dropdown", "value"),
)
def update_live_tab(n, selected):
    """Refresh signal table, gauge, and spread chart every 1s."""
    _empty_gauge = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=0,
            gauge={"axis": {"range": [-5, 5]}, "bar": {"color": "#00BCD4"}},
        ),
        layout=go.Layout(**_DARK, height=250),
    )
    _empty_spread = go.Figure(layout=go.Layout(**_DARK, height=200))

    if not selected:
        return [], [], _empty_gauge, _empty_spread

    try:
        _, symbol = selected.split(":", 1)
    except ValueError:
        return [], [], _empty_gauge, _empty_spread

    # Signal table
    rows = _fetch_signals(symbol)
    if not rows:
        rows = [
            {
                "Time": "—", "Signal": "Inference loop not running",
                "Confidence": "", "Regime": "", "Cluster": "",
                "Dev bps": "", "Model ID": "", "Latency ms": "",
            }
        ]
    style_cond = [
        {
            "if": {
                "filter_query": f'{{Signal}} = "{sig}"',
                "column_id": "Signal",
            },
            "backgroundColor": color,
        }
        for sig, color in SIGNAL_COLORS.items()
    ]

    # Deviation gauge from most recent signal row
    dev_bps = 0.0
    if rows and rows[0].get("Dev bps"):
        try:
            dev_bps = float(rows[0]["Dev bps"])
        except (ValueError, TypeError):
            pass

    gauge_color = (
        "#4CAF50" if dev_bps > 0
        else "#F44336" if dev_bps < 0
        else "#9E9E9E"
    )
    gauge_fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=round(dev_bps, 3),
            title={"text": "Microprice Deviation (bps)", "font": {"color": "#CCC"}},
            gauge={
                "axis": {"range": [-5, 5], "tickcolor": "#CCC"},
                "bar": {"color": gauge_color},
                "bgcolor": "#333",
                "threshold": {"line": {"color": "#FFF", "width": 2}, "value": 0},
            },
            number={"font": {"color": "#CCC"}},
        ),
        layout=go.Layout(**_DARK, height=250),
    )

    # Spread z-score chart
    spread_rows = _fetch_spread_signals()
    if spread_rows:
        zscores = [float(r.get("zscore", 0) or 0) for r in reversed(spread_rows)]
        spread_fig = go.Figure(
            layout=go.Layout(
                title="BTC/ETH Spread Z-Score (last 60 bars)",
                **_DARK,
                height=200,
            )
        )
        spread_fig.add_trace(
            go.Scatter(
                y=zscores,
                mode="lines",
                line=dict(color="#FF9800", width=1.5),
                name="z-score",
            )
        )
        spread_fig.add_hline(y=1.5,  line_dash="dash", line_color="#4CAF5066")
        spread_fig.add_hline(y=-1.5, line_dash="dash", line_color="#F4433666")
        spread_fig.add_hline(y=0,    line_dash="dot",  line_color="#555555")
    else:
        spread_fig = _empty_spread

    return rows, style_cond, gauge_fig, spread_fig
