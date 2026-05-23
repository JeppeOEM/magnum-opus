"""Layout for the /profiles page -- customise candle hover field profiles."""
from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import dcc, html

# ── All snapshot_1s fields grouped by category ────────────────────────────────

FIELD_GROUPS: list[tuple[str, list[str]]] = [
    ("OHLCV", [
        "open", "high", "low", "close",
        "volume", "quote_volume", "trade_count", "twap",
    ]),
    ("Mid-price", [
        "mid_price_open", "mid_price_high", "mid_price_low", "vwmp",
    ]),
    ("Spread", [
        "spread_high", "spread_low", "spread_mean", "effective_spread",
    ]),
    ("OB Best Quotes", [
        "best_bid_open", "best_ask_open", "best_bid", "best_ask",
    ]),
    ("Trade Flow", [
        "buy_volume", "sell_volume", "buy_count",
        "block_buy_volume", "block_sell_volume",
    ]),
    ("Trade Distribution", [
        "max_trade_size", "large_bid_orders", "large_ask_orders",
        "first_trade_offset_ms", "last_trade_offset_ms",
        "trade_clustering", "max_consecutive_run",
    ]),
    ("Volatility", [
        "realized_vol", "realized_skewness", "uptick_count", "downtick_count",
    ]),
    ("OFI", ["ofi", "ofi_l1"]),
    ("CVD", ["cum_delta", "cvd_divergence"]),
    ("OB Activity", [
        "bid_order_arrivals", "ask_order_arrivals",
        "bid_cancel_count", "ask_cancel_count", "ob_modify_count",
        "avg_bid_order_size", "avg_ask_order_size",
        "best_bid_changes", "best_ask_changes", "quote_stuff_ratio",
    ]),
    ("Microstructure Signals", [
        "hawkes_intensity", "microprice", "microprice_mid_delta",
        "cancel_bias", "trade_aggressiveness",
        "trade_sign_autocorr", "inter_trade_interval_std_ms",
        "num_trade_price_levels",
    ]),
    ("Footprint", [
        "poc_price", "value_area_high", "value_area_low", "poc_volume",
        "imbalance_ratio", "imbalance_buy_count", "imbalance_sell_count",
        "imbalance_stack_buy", "imbalance_stack_sell", "single_print_count",
        "unfinished_top", "unfinished_bottom", "absorption_detected",
        "footprint_delta_divergence",
    ]),
    ("Iceberg", [
        "iceberg_bid_detected", "iceberg_ask_detected", "iceberg_price",
    ]),
    ("VWAP Deviation", [
        "buy_vwap_deviation_bps", "sell_vwap_deviation_bps",
    ]),
    ("Quality", ["is_partial", "gap_count", "bar_count"]),
]

DEFAULT_PROFILE_FIELDS: list[str] = [
    "open", "high", "low", "close",
    "volume", "buy_volume", "sell_volume",
    "trade_count", "realized_vol",
    "cum_delta", "imbalance_ratio",
]

DEFAULT_PROFILES: dict = {
    "profiles": {
        "Default": {"fields": DEFAULT_PROFILE_FIELDS},
        "Order Flow": {"fields": [
            "ofi", "ofi_l1", "cum_delta", "cvd_divergence",
            "buy_volume", "sell_volume", "imbalance_ratio",
            "bid_order_arrivals", "ask_order_arrivals",
            "bid_cancel_count", "ask_cancel_count",
        ]},
        "Microstructure": {"fields": [
            "hawkes_intensity", "microprice", "microprice_mid_delta",
            "cancel_bias", "trade_aggressiveness",
            "trade_sign_autocorr", "inter_trade_interval_std_ms",
            "realized_vol", "realized_skewness",
        ]},
    },
    "active": "Default",
}

_label_style = {"color": "#888", "fontSize": "11px", "marginBottom": "2px"}


def _build_field_checklist() -> list:
    cols: list = []
    for group_name, fields in FIELD_GROUPS:
        group_items = [{"label": f, "value": f} for f in fields]
        cols.append(
            dbc.Col(
                [
                    html.Div(
                        group_name,
                        style={
                            "color": "#4fc3f7", "fontSize": "11px",
                            "fontWeight": "bold", "marginBottom": "4px",
                            "borderBottom": "1px solid #2a2a2a",
                            "paddingBottom": "2px",
                        },
                    ),
                    dcc.Checklist(
                        id={"type": "profile-field-check", "group": group_name},
                        options=group_items,
                        value=[],
                        labelStyle={
                            "display": "block",
                            "color": "#ccc",
                            "fontSize": "12px",
                            "cursor": "pointer",
                            "marginBottom": "2px",
                        },
                        inputStyle={"marginRight": "6px"},
                    ),
                ],
                width=3,
                style={"marginBottom": "12px"},
            )
        )
    return cols


profiles_page_layout = dbc.Container(
    [
        dbc.Row(
            dbc.Col(
                html.H5("Candle Hover Profiles",
                        style={"color": "#ccc", "marginBottom": "4px"}),
                width=12,
            ),
            className="mb-1",
        ),
        dbc.Row(
            dbc.Col(
                html.Div(
                    "Customise which fields appear in the candle tooltip. "
                    "Browser storage keeps profiles in this browser; "
                    "use the file controls to persist across machines.",
                    style={"color": "#888", "fontSize": "12px"},
                ),
                width=12,
            ),
            className="mb-2",
        ),

        # ── File persistence controls ──────────────────────────────────────────
        dbc.Row(
            dbc.Col(
                dbc.Card(
                    dbc.CardBody(
                        dbc.Row(
                            [
                                dbc.Col(
                                    html.Div(
                                        id="profile-file-info",
                                        style={
                                            "fontSize": "12px",
                                            "color": "#aaa",
                                            "fontFamily": "monospace",
                                            "lineHeight": "30px",
                                        },
                                    ),
                                ),
                                dbc.Col(
                                    dbc.ButtonGroup([
                                        dbc.Button(
                                            "Load from file",
                                            id="profile-load-file-btn",
                                            color="secondary",
                                            size="sm",
                                            outline=True,
                                            title="Replace browser profiles with profiles.json",
                                        ),
                                        dbc.Button(
                                            "Save to file",
                                            id="profile-save-file-btn",
                                            color="primary",
                                            size="sm",
                                            title="Write current browser profiles to profiles.json on server",
                                        ),
                                        dbc.Button(
                                            "Export JSON",
                                            id="profile-export-btn",
                                            color="secondary",
                                            size="sm",
                                            outline=True,
                                            title="Download profiles as a JSON file to your browser",
                                        ),
                                    ]),
                                    width="auto",
                                ),
                            ],
                            align="center",
                        ),
                        style={"padding": "8px 12px"},
                    ),
                    style={"backgroundColor": "#1e1e1e", "border": "1px solid #333"},
                ),
                width=12,
            ),
            className="mb-3",
        ),

        # ── Profile selector + management ─────────────────────────────────────
        dbc.Row(
            [
                dbc.Col(
                    [
                        html.Div("Active Profile", style=_label_style),
                        dcc.Dropdown(
                            id="profile-active-dd",
                            clearable=False,
                            style={"minWidth": "160px"},
                        ),
                    ],
                    width=3,
                ),
                dbc.Col(
                    [
                        html.Div("New Profile Name", style=_label_style),
                        dbc.InputGroup([
                            dbc.Input(
                                id="profile-new-name-inp",
                                placeholder="My Profile",
                                size="sm",
                            ),
                            dbc.Button(
                                "+ Create",
                                id="profile-create-btn",
                                color="primary",
                                size="sm",
                            ),
                        ]),
                    ],
                    width=3,
                ),
                dbc.Col(
                    [
                        html.Div(" ", style=_label_style),
                        dbc.ButtonGroup([
                            dbc.Button(
                                "Save",
                                id="profile-save-btn",
                                color="success",
                                size="sm",
                                title="Save selected fields into the active profile (browser storage)",
                            ),
                            dbc.Button(
                                "Delete",
                                id="profile-delete-btn",
                                color="danger",
                                size="sm",
                                outline=True,
                            ),
                        ]),
                    ],
                    width=3,
                ),
                dbc.Col(
                    html.Div(
                        id="profile-status-div",
                        style={
                            "color": "#80cbc4",
                            "fontSize": "12px",
                            "lineHeight": "32px",
                            "marginTop": "14px",
                        },
                    ),
                    width=3,
                ),
            ],
            className="mb-3",
            align="end",
        ),

        # ── Field selection checklists ─────────────────────────────────────────
        dbc.Row(
            dbc.Col(
                html.Div(
                    id="profile-selected-count",
                    style={"color": "#888", "fontSize": "12px", "marginBottom": "8px"},
                ),
                width=12,
            ),
        ),
        dbc.Row(_build_field_checklist(), id="profile-checklist-row"),

        # ── Preview ────────────────────────────────────────────────────────────
        dbc.Row(
            dbc.Col(
                [
                    html.Div(
                        "Hover Tooltip Preview",
                        style={
                            "color": "#888", "fontSize": "11px",
                            "marginBottom": "4px", "marginTop": "12px",
                        },
                    ),
                    html.Div(
                        id="profile-hover-preview",
                        style={
                            "background": "#2a2a2a",
                            "border": "1px solid #444",
                            "borderRadius": "4px",
                            "padding": "8px 12px",
                            "fontSize": "12px",
                            "fontFamily": "monospace",
                            "color": "#ccc",
                            "maxWidth": "320px",
                            "whiteSpace": "pre-line",
                        },
                    ),
                ],
                width=4,
            ),
        ),

        # ── Hidden: browser download component ────────────────────────────────
        dcc.Download(id="profile-download"),
    ],
    fluid=True,
    style={"padding": "12px"},
)
