import logging

import dash
import dash_bootstrap_components as dbc

from layout import layout

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    suppress_callback_exceptions=True,
)

app.layout = layout

import callbacks  # noqa: F401, E402 — must be last; callbacks import app from this module

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=False)
