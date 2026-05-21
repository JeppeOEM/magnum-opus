"""Models tab layout — registry table with CV scores."""
import dash_bootstrap_components as dbc
from dash import dash_table, html

models_tab_layout = dbc.Row(
    [
        dbc.Col(
            [
                html.H6("Trained Models", className="mt-2"),
                dash_table.DataTable(
                    id="models-table",
                    style_table={"overflowX": "auto"},
                    style_cell={
                        "backgroundColor": "#333",
                        "color": "#CCC",
                        "border": "1px solid #555",
                        "textAlign": "left",
                        "padding": "6px 10px",
                    },
                    style_header={
                        "backgroundColor": "#444",
                        "fontWeight": "bold",
                        "color": "#EEE",
                    },
                    style_data_conditional=[
                        {
                            "if": {"filter_query": "{Model} = xgboost"},
                            "color": "#00BCD4",
                        },
                    ],
                    page_size=20,
                    sort_action="native",
                ),
            ]
        )
    ]
)
