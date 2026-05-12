import logging

import dash
import dash_bootstrap_components as dbc
from plotly.subplots import make_subplots

from layout import layout

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    suppress_callback_exceptions=True,
)

# ADR-18-01: subplot grid — established once here; never modified by panel stories.
# Panel stories copy this via go.Figure(BASE_FIGURE) then add_traces(row=N, col=M).
BASE_FIGURE = make_subplots(
    rows=2, cols=3,
    shared_yaxes='rows',
    column_widths=[0.38, 0.12, 0.50],
    row_heights=[0.70, 0.30],
    specs=[[{}, {}, {"rowspan": 2}], [{}, {"secondary_y": True}, None]]
)

app.layout = layout

import callbacks  # noqa: F401, E402 — must be last; callbacks import app from this module

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=False)
