import os

from dash import Input, Output, callback

import data

_QUESTDB_URL = os.environ.get("QUESTDB_HTTP_ADDR", "http://questdb:9000")


@callback(
    Output("candle-store", "data"),
    Output("last-ts", "data"),
    Input("symbol-dropdown", "value"),
)
def update_candle_store(selected):
    if not selected:
        return [], None
    exchange, symbol = selected.split(":", 1)
    rows = data.fetch_history(exchange, symbol, _QUESTDB_URL)
    max_ts = rows[-1]["ts"] if rows else None  # rows is ascending (newest last) — see fetch_history
    return rows, max_ts
