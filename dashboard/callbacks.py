import os

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
    Input("symbol-dropdown", "value"),
)
def update_candle_store(selected):
    if not selected:
        return [], None, [], "0", ""
    exchange, symbol = selected.split(":", 1)
    rows = data.fetch_history(exchange, symbol, _QUESTDB_URL)
    max_ts = rows[-1]["ts"] if rows else None  # rows is ascending (newest last) — see fetch_history
    ob_cursor = data.fetch_ob_snapshot(exchange, symbol)
    return rows, max_ts, [], ob_cursor, selected


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
    prevent_initial_call=True,
)
def live_update(n, candle_rows, last_ts, selected, ob_rows, ob_cursor, ob_cursor_symbol):
    if not selected or not last_ts:
        return no_update, no_update, no_update, no_update
    exchange, symbol = selected.split(":", 1)

    new_candles = data.fetch_new_candles(exchange, symbol, last_ts, _QUESTDB_URL)

    if ob_cursor_symbol != selected:
        # cursor not yet seeded for current symbol — skip ob fetch this tick
        new_ob, new_cursor = [], ob_cursor
    else:
        new_ob, new_cursor = data.fetch_ob_live(exchange, symbol, ob_cursor or "0")

    out_candles = no_update
    out_ts = no_update
    if new_candles:
        out_candles = (candle_rows or []) + new_candles
        out_ts = new_candles[-1]["ts"]

    out_ob = no_update
    out_cursor = no_update
    if new_ob:
        out_ob = (ob_rows or []) + new_ob
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
    return fig


def absorption_overlay(fig: go.Figure, df: "pd.DataFrame") -> go.Figure:
    # Stub: renders no markers until absorption_detected field is available (Epic 20).
    # Activation: when 'absorption_detected' column is in df, add go.Scatter
    # (mode='markers', marker_symbol='diamond') at flagged candles, row=1, col=1.
    return fig
