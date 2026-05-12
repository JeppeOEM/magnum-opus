import copy
import json
import os
from datetime import datetime, timezone

import pandas as pd
from dash import Input, Output, State, callback, no_update
import plotly.graph_objects as go

import charts
import data

_QUESTDB_URL = os.environ.get("QUESTDB_HTTP_ADDR", "http://questdb:9000")


@callback(
    Output("candle-store", "data"),
    Output("last-ts", "data"),
    Output("ob-store", "data"),
    Output("ob-cursor", "data"),
    Output("ob-cursor-symbol", "data"),
    Output("active-tf", "data"),
    Input("symbol-dropdown", "value"),
    Input("tf-dropdown", "value"),
)
def update_candle_store(selected, tf):
    tf = tf or "1s"
    if not selected:
        return [], None, [], "0", "", tf
    exchange, symbol = selected.split(":", 1)
    rows = data.fetch_history(exchange, symbol, _QUESTDB_URL, tf=tf)
    max_ts = rows[-1]["ts"] if rows else None  # rows is ascending (newest last) — see fetch_history
    ob_cursor = data.fetch_ob_snapshot(exchange, symbol)
    return rows, max_ts, [], ob_cursor, selected, tf


@callback(
    Output("candle-store", "data", allow_duplicate=True),
    Output("last-ts", "data", allow_duplicate=True),
    Output("ob-store", "data", allow_duplicate=True),
    Output("ob-cursor", "data", allow_duplicate=True),
    Input("live-interval", "n_intervals"),
    State("candle-store", "data"),
    State("last-ts", "data"),
    State("symbol-dropdown", "value"),
    State("ob-store", "data"),
    State("ob-cursor", "data"),
    State("ob-cursor-symbol", "data"),
    State("active-tf", "data"),
    prevent_initial_call=True,
)
def live_update(n, candle_rows, last_ts, selected, ob_rows, ob_cursor, ob_cursor_symbol, active_tf):
    if not selected or not last_ts:
        return no_update, no_update, no_update, no_update
    exchange, symbol = selected.split(":", 1)
    tf = active_tf or "1s"

    new_candles = data.fetch_new_candles(exchange, symbol, last_ts, _QUESTDB_URL, tf=tf)

    if ob_cursor_symbol != selected:
        # cursor not yet seeded for current symbol — skip ob fetch this tick
        new_ob, new_cursor = [], ob_cursor
    else:
        new_ob, new_cursor = data.fetch_ob_live(exchange, symbol, ob_cursor or "0")

    out_candles = no_update
    out_ts = no_update
    if new_candles:
        out_candles = ((candle_rows or []) + new_candles)[-500:]
        out_ts = new_candles[-1]["ts"]

    out_ob = no_update
    out_cursor = no_update
    if new_ob:
        out_ob = ((ob_rows or []) + new_ob)[-1000:]
        out_cursor = new_cursor

    return out_candles, out_ts, out_ob, out_cursor


@callback(
    Output("candlestick-graph", "figure"),
    Input("candle-store", "data"),
)
def update_candlestick(candle_rows):
    df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
    fig = charts.build_candlestick(df)
    if not df.empty:
        fig = charts.add_volume_levels(fig, df)
        fig = charts.add_volume_bubbles(fig, df)
        fig = absorption_overlay(fig, df)
        fig = liquidity_overlay(fig, df)
    return fig


def absorption_overlay(fig: go.Figure, df: "pd.DataFrame") -> go.Figure:
    if df.empty or "absorption_detected" not in df.columns:
        return fig
    mask = pd.to_numeric(df["absorption_detected"], errors="coerce").fillna(0).astype(bool)
    absorbed = df[mask]
    if absorbed.empty:
        return fig
    if "close" not in absorbed.columns:
        return fig
    close_vals = pd.to_numeric(absorbed["close"], errors="coerce")
    if close_vals.isna().all():
        return fig
    fig.add_trace(
        go.Scatter(
            x=absorbed["ts"],
            y=close_vals,
            mode="markers",
            marker=dict(symbol="diamond", size=10, color="#FF9800", opacity=0.8),
            name="Absorption",
            showlegend=False,
            hoverinfo="skip",
        )
    )
    return fig


def liquidity_overlay(fig: go.Figure, df: "pd.DataFrame") -> go.Figure:
    if df.empty or len(df) < 2:
        return fig
    required = ["poc_price", "value_area_high", "value_area_low", "ts"]
    if not all(c in df.columns for c in required):
        return fig
    valid = df[["poc_price", "value_area_high", "value_area_low"]].apply(pd.to_numeric, errors="coerce").dropna()
    if valid.empty:
        return fig
    va_low = float(valid["value_area_low"].iloc[-1])
    va_high = float(valid["value_area_high"].iloc[-1])
    if va_low > va_high:
        va_low, va_high = va_high, va_low
    poc = float(valid["poc_price"].iloc[-1])
    fig.add_hrect(y0=va_low, y1=va_high, fillcolor="rgba(255,200,0,0.08)", line_width=0)
    fig.add_trace(
        go.Scatter(
            x=[df["ts"].iloc[0], df["ts"].iloc[-1]],
            y=[poc, poc],
            mode="lines",
            line=dict(color="#FFD700", dash="dot", width=1),
            name="POC",
            showlegend=False,
        )
    )
    return fig


@callback(
    Output("heatmap-graph", "figure"),
    Input("candle-store", "data"),
    Input("ob-store", "data"),
)
def update_heatmap(candle_rows, ob_rows):
    df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
    fig = charts.build_delta_heatmap(df)
    fig = charts.add_ob_depth_heatmap(fig, ob_rows or [])
    fig = charts.add_iceberg_borders(fig, df)
    return fig


@callback(
    Output("vol-profile-graph", "figure"),
    Input("candle-store", "data"),
)
def update_vol_profile(candle_rows):
    df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
    return charts.build_vol_profile(df)


@callback(
    Output("heatmap-graph", "figure", allow_duplicate=True),
    Input("candlestick-graph", "relayoutData"),
    State("heatmap-graph", "figure"),
    prevent_initial_call=True,
)
def sync_yaxis_zoom(relay_data, heatmap_fig):
    if not relay_data or not heatmap_fig:
        return no_update
    if "yaxis.range[0]" in relay_data and "yaxis.range[1]" in relay_data:
        fig = copy.deepcopy(heatmap_fig)
        if "layout" not in fig:
            fig["layout"] = {}
        if "yaxis" not in fig["layout"]:
            fig["layout"]["yaxis"] = {}
        fig["layout"]["yaxis"]["range"] = [
            float(relay_data["yaxis.range[0]"]),
            float(relay_data["yaxis.range[1]"]),
        ]
        return fig
    return no_update


@callback(
    Output("footprint-modal", "is_open"),
    Output("footprint-chart", "figure"),
    Output("footprint-modal-title", "children"),
    Input("candlestick-graph", "clickData"),
    State("candle-store", "data"),
    prevent_initial_call=True,
)
def open_footprint_modal(click_data, candle_rows):
    if not click_data or not click_data.get("points"):
        return no_update, no_update, no_update
    try:
        clicked_ts = click_data["points"][0]["x"]
    except (KeyError, IndexError):
        return no_update, no_update, no_update
    if not candle_rows:
        return no_update, no_update, no_update

    # Plotly normalizes datetime x-values (strips T/Z/microseconds); compare at second precision
    try:
        clicked_sec = pd.Timestamp(clicked_ts).floor("s")
    except Exception:
        return no_update, no_update, no_update

    row = None
    for r in candle_rows:
        try:
            if pd.Timestamp(r.get("ts", "")).floor("s") == clicked_sec:
                row = r
                break
        except Exception:
            continue
    if row is None:
        return no_update, no_update, no_update

    fp_json = row.get("footprint_json")
    if not fp_json:
        return no_update, no_update, no_update

    sp_levels = None
    sp_json = row.get("single_print_levels_json")
    if sp_json:
        try:
            parsed = json.loads(sp_json)
            if isinstance(parsed, list):
                sp_levels = frozenset(
                    str(float(p)) for p in parsed if isinstance(p, str)
                )
        except Exception:
            pass

    fig = charts.build_footprint_chart(fp_json, single_print_levels=sp_levels)
    if not fig.data:
        return no_update, no_update, no_update

    return True, fig, f"Footprint: {clicked_ts}"


@callback(
    Output("cvd-graph", "figure"),
    Input("candle-store", "data"),
)
def update_cvd(candle_rows):
    df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
    return charts.build_cvd_panel(df)


@callback(
    Output("bidask-graph", "figure"),
    Input("candle-store", "data"),
)
def update_bidask(candle_rows):
    df = pd.DataFrame(candle_rows) if candle_rows else pd.DataFrame()
    return charts.build_bidask_panel(df)


@callback(
    Output("status-bar", "children"),
    Input("candle-store", "data"),
    Input("ob-store", "data"),
)
def update_status_bar(candle_rows, ob_rows):
    now = datetime.now(timezone.utc)
    parts = []

    if candle_rows:
        n = len(candle_rows)
        last_ts_raw = candle_rows[-1].get("ts", "")
        try:
            last_ts = pd.Timestamp(last_ts_raw, tz="UTC")
            age_s = int((now - last_ts.to_pydatetime()).total_seconds())
            age_str = f"{age_s}s ago" if age_s < 120 else f"{age_s // 60}m ago"
            parts.append(f"candles: {n}  |  last bar: {last_ts.strftime('%H:%M:%S')} UTC  |  age: {age_str}")
        except Exception:
            parts.append(f"candles: {n}")
    else:
        parts.append("candles: no data — is the stack running?")

    if ob_rows:
        parts.append(f"ob ticks: {len(ob_rows)}")

    return "  ·  ".join(parts)
