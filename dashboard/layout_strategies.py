"""Strategy browser and detail page layouts."""
from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import dcc, html

# ── Folder badge helpers ──────────────────────────────────────────────────────
_BADGE_ACTIVE = {
    "backgroundColor": "#1b5e20", "color": "#a5d6a7",
    "fontSize": "10px", "padding": "2px 8px", "borderRadius": "10px",
    "fontWeight": "bold",
}
_BADGE_INACTIVE = {
    "backgroundColor": "#37474f", "color": "#90a4ae",
    "fontSize": "10px", "padding": "2px 8px", "borderRadius": "10px",
}


def folder_badge(folder: str) -> html.Span:
    if folder == "active":
        return html.Span("● active", style=_BADGE_ACTIVE)
    return html.Span("○ inactive", style=_BADGE_INACTIVE)


def tag_pill(tag: str) -> html.Span:
    return html.Span(
        tag,
        style={
            "backgroundColor": "#263238",
            "color": "#80cbc4",
            "fontSize": "10px",
            "padding": "1px 6px",
            "borderRadius": "8px",
            "marginRight": "4px",
            "display": "inline-block",
            "marginTop": "2px",
        },
    )


# ── Browser page ──────────────────────────────────────────────────────────────

strategies_browser_layout = dbc.Container(
    [
        # ── Top bar ───────────────────────────────────────────────────────────
        dbc.Row(
            [
                dbc.Col(
                    html.H5("Strategies", style={"color": "#fff", "marginBottom": 0}),
                    width="auto",
                    className="d-flex align-items-center",
                ),
                # Active / Inactive / All toggle
                dbc.Col(
                    dbc.ButtonGroup(
                        [
                            dbc.Button("All",      id="strat-filter-all",      color="secondary", size="sm", n_clicks=0),
                            dbc.Button("Active",   id="strat-filter-active",   color="secondary", size="sm", n_clicks=0),
                            dbc.Button("Inactive", id="strat-filter-inactive", color="secondary", size="sm", n_clicks=0),
                        ],
                    ),
                    width="auto",
                    className="ms-3 d-flex align-items-center",
                ),
                # Group filter
                dbc.Col(
                    dcc.Dropdown(
                        id="strat-group-filter",
                        placeholder="All groups",
                        clearable=True,
                        style={"minWidth": "180px", "fontSize": "13px"},
                    ),
                    width="auto",
                    className="ms-3 d-flex align-items-center",
                ),
                # Tag filter (multi-select)
                dbc.Col(
                    dcc.Dropdown(
                        id="strat-tag-filter",
                        placeholder="Filter by tag…",
                        clearable=True,
                        multi=True,
                        style={"minWidth": "240px", "fontSize": "13px"},
                    ),
                    width="auto",
                    className="ms-2 d-flex align-items-center",
                ),
                dbc.Col(
                    dbc.Button("↺", id="strat-refresh-btn", color="secondary", size="sm", title="Refresh"),
                    width="auto",
                    className="ms-auto d-flex align-items-center",
                ),
            ],
            align="center",
            style={"marginBottom": "16px"},
        ),

        # ── Status line ───────────────────────────────────────────────────────
        html.Div(
            id="strat-status-line",
            style={"color": "#ff9800", "fontSize": "12px", "marginBottom": "10px"},
        ),

        # ── Cards grid (rendered by callback) ─────────────────────────────────
        html.Div(id="strat-cards-grid"),

        # ── Hidden state ──────────────────────────────────────────────────────
        dcc.Store(id="strat-all-data", data=[]),
        dcc.Store(id="strat-folder-filter", data="all"),
    ],
    fluid=True,
    style={"padding": "12px"},
)


# ── Detail page ───────────────────────────────────────────────────────────────

def strategies_detail_layout(strategy_name: str) -> dbc.Container:
    """Skeleton for the strategy detail page; callbacks populate the content."""
    return dbc.Container(
        [
            # Back + header row
            dbc.Row(
                [
                    dbc.Col(
                        dcc.Link(
                            "← Strategies",
                            href="/strategies",
                            style={"color": "#90caf9", "fontSize": "13px", "textDecoration": "none"},
                        ),
                        width="auto",
                        className="d-flex align-items-center",
                    ),
                    dbc.Col(
                        html.Div(id="strat-detail-header"),
                        className="d-flex align-items-center ms-3",
                    ),
                    dbc.Col(
                        dbc.Button(
                            "⏵ Backtest this strategy",
                            id="strat-detail-backtest-btn",
                            color="success",
                            size="sm",
                            href="/backtests",
                            external_link=False,
                        ),
                        width="auto",
                        className="ms-auto d-flex align-items-center",
                    ),
                ],
                align="center",
                style={"marginBottom": "14px"},
            ),

            # Settings + code side-by-side
            dbc.Row(
                [
                    # Left col: settings
                    dbc.Col(
                        [
                            html.H6("Settings", style={"color": "#aaa", "marginBottom": "10px"}),
                            html.Div(id="strat-detail-settings"),
                            html.Div(
                                id="strat-detail-tags",
                                style={"marginTop": "14px"},
                            ),
                        ],
                        width=3,
                    ),
                    # Right col: source code viewer
                    dbc.Col(
                        [
                            html.H6("Source Code", style={"color": "#aaa", "marginBottom": "10px"}),
                            dbc.Card(
                                dbc.CardBody(
                                    html.Pre(
                                        id="strat-detail-code",
                                        style={
                                            "fontSize": "12px",
                                            "color": "#ccc",
                                            "background": "transparent",
                                            "margin": 0,
                                            "whiteSpace": "pre-wrap",
                                            "wordBreak": "break-word",
                                        },
                                    ),
                                    style={"padding": "12px"},
                                ),
                                style={
                                    "backgroundColor": "#161616",
                                    "border": "1px solid #333",
                                    "maxHeight": "calc(100vh - 160px)",
                                    "overflowY": "auto",
                                },
                            ),
                        ],
                        width=9,
                    ),
                ],
            ),

            # Hidden: carries the strategy name to callbacks
            dcc.Store(id="strat-detail-name", data=strategy_name),
        ],
        fluid=True,
        style={"padding": "12px"},
    )
