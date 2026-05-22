"""Callbacks for /strategies and /strategies/<name> pages."""
from __future__ import annotations

import ast
import re
from typing import Any

import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dcc, html, no_update

import backtest_data
from layout_strategies import folder_badge, tag_pill


# ── Source-code parsers ───────────────────────────────────────────────────────

def _parse_str_const(code: str, name: str) -> str:
    """Extract a module-level string constant, e.g. STRATEGY_GROUP = "Foo"."""
    m = re.search(rf'^{name}\s*=\s*["\']([^"\']+)["\']', code, re.MULTILINE)
    return m.group(1) if m else ""


def _parse_list_const(code: str, name: str) -> list[str]:
    """Extract a module-level list literal, e.g. STRATEGY_TAGS = ["a", "b"]."""
    m = re.search(rf'^{name}\s*=\s*(\[.*?\])', code, re.MULTILINE | re.DOTALL)
    if not m:
        return []
    try:
        val = ast.literal_eval(m.group(1))
        return [str(t) for t in val] if isinstance(val, list) else []
    except Exception:
        return []


def _parse_module_const(code: str, name: str) -> str:
    """Extract a short module-level constant (string or number)."""
    m = re.search(rf'^{name}\s*=\s*["\']?([^\n"\'#]+)["\']?', code, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _parse_property_return(code: str, prop_name: str) -> str:
    """Extract the return value of a simple @property method."""
    pattern = rf'def {prop_name}\(self\)[^:]*:\s*\n\s+return\s+([^\n]+)'
    m = re.search(pattern, code)
    return m.group(1).strip() if m else "?"


def _parse_docstring(code: str) -> str:
    """Return the module-level docstring (first triple-quoted block)."""
    m = re.search(r'^"""(.*?)"""', code, re.DOTALL)
    if m:
        return m.group(1).strip().split("\n")[0]   # first line only
    m = re.search(r"^'''(.*?)'''", code, re.DOTALL)
    if m:
        return m.group(1).strip().split("\n")[0]
    # Fall back to the class docstring
    m = re.search(r'class \w+[^:]*:\s*\n\s+"""(.*?)"""', code, re.DOTALL)
    if m:
        return m.group(1).strip().split("\n")[0]
    return ""


def _enrich(s: dict[str, Any]) -> dict[str, Any]:
    """Add parsed metadata fields to a raw strategy dict from the bot service."""
    code = s.get("code", "")
    s["group"]       = _parse_str_const(code, "STRATEGY_GROUP") or "Ungrouped"
    s["tags"]        = _parse_list_const(code, "STRATEGY_TAGS")
    s["description"] = _parse_docstring(code)
    s["symbol"]      = _parse_module_const(code, "_SYMBOL")
    s["tf"]          = _parse_module_const(code, "_TF")
    s["stop_loss"]   = _parse_property_return(code, "stop_loss_pct")
    s["max_pos"]     = _parse_property_return(code, "max_position_pct")
    s["lookback"]    = _parse_property_return(code, "min_lookback")
    s["paper"]       = _parse_property_return(code, "paper_trading")
    s["ob_mode"]     = _parse_property_return(code, "orderbook_mode")
    return s


# ── Load & store strategies ───────────────────────────────────────────────────

@callback(
    Output("strat-all-data", "data"),
    Output("strat-status-line", "children"),
    Output("strat-group-filter", "options"),
    Output("strat-tag-filter", "options"),
    Input("strat-all-data", "id"),          # fires on page mount
    Input("strat-refresh-btn", "n_clicks"),
)
def load_strategies(_id, _n_clicks):
    raw = backtest_data.fetch_strategies()
    if not raw:
        return [], "⚠ No strategies found — is bot-service running? Click ↺ to retry.", [], []

    enriched = [_enrich(s) for s in raw]

    groups = sorted({s["group"] for s in enriched if s["group"]})
    group_opts = [{"label": g, "value": g} for g in groups]

    all_tags = sorted({tag for s in enriched for tag in s["tags"]})
    tag_opts = [{"label": t, "value": t} for t in all_tags]

    return enriched, "", group_opts, tag_opts


# ── Folder filter buttons (highlight active button) ───────────────────────────

@callback(
    Output("strat-folder-filter", "data"),
    Output("strat-filter-all",      "color"),
    Output("strat-filter-active",   "color"),
    Output("strat-filter-inactive", "color"),
    Input("strat-filter-all",      "n_clicks"),
    Input("strat-filter-active",   "n_clicks"),
    Input("strat-filter-inactive", "n_clicks"),
    State("strat-folder-filter", "data"),
    prevent_initial_call=True,
)
def set_folder_filter(n_all, n_active, n_inactive, current):
    from dash import ctx
    btn = ctx.triggered_id
    mapping = {
        "strat-filter-all":      "all",
        "strat-filter-active":   "active",
        "strat-filter-inactive": "inactive",
    }
    folder = mapping.get(btn, current or "all")
    colors = {
        "all":      ("primary", "secondary", "secondary"),
        "active":   ("secondary", "primary", "secondary"),
        "inactive": ("secondary", "secondary", "primary"),
    }
    return (folder, *colors[folder])


# ── Render cards grid ─────────────────────────────────────────────────────────

@callback(
    Output("strat-cards-grid", "children"),
    Input("strat-all-data",     "data"),
    Input("strat-folder-filter","data"),
    Input("strat-group-filter", "value"),
    Input("strat-tag-filter",   "value"),
)
def render_cards(all_data, folder_filter, group_filter, tag_filter):
    if not all_data:
        return html.Div("No strategies loaded.", style={"color": "#888", "fontSize": "13px"})

    strategies = list(all_data)

    # Apply folder filter
    if folder_filter and folder_filter != "all":
        strategies = [s for s in strategies if s.get("folder") == folder_filter]

    # Apply group filter
    if group_filter:
        strategies = [s for s in strategies if s.get("group") == group_filter]

    # Apply tag filter (strategy must match ALL selected tags)
    if tag_filter:
        strategies = [
            s for s in strategies
            if all(t in s.get("tags", []) for t in tag_filter)
        ]

    if not strategies:
        return html.Div("No strategies match the current filters.", style={"color": "#888", "fontSize": "13px"})

    # Group cards by STRATEGY_GROUP for visual organisation
    by_group: dict[str, list[dict]] = {}
    for s in strategies:
        by_group.setdefault(s["group"], []).append(s)

    sections = []
    for group_name, group_strats in sorted(by_group.items()):
        cards = []
        for s in group_strats:
            name   = s.get("name", "")
            folder = s.get("folder", "inactive")
            tags   = s.get("tags", [])
            desc   = s.get("description", "")
            symbol = s.get("symbol", "—")
            tf     = s.get("tf", "—")

            card = dbc.Col(
                dcc.Link(
                    dbc.Card(
                        dbc.CardBody(
                            [
                                # Name + folder badge
                                dbc.Row(
                                    [
                                        dbc.Col(
                                            html.Div(
                                                name,
                                                style={"fontWeight": "bold", "color": "#e0e0e0", "fontSize": "13px"},
                                            ),
                                        ),
                                        dbc.Col(
                                            folder_badge(folder),
                                            width="auto",
                                            className="d-flex align-items-center",
                                        ),
                                    ],
                                    align="center",
                                    className="mb-2",
                                ),
                                # Description
                                html.Div(
                                    desc,
                                    style={"color": "#888", "fontSize": "11px", "marginBottom": "8px",
                                           "whiteSpace": "nowrap", "overflow": "hidden", "textOverflow": "ellipsis"},
                                    title=desc,
                                ),
                                # Symbol / TF
                                html.Div(
                                    [
                                        html.Span(symbol, style={"color": "#ccc", "fontSize": "12px", "marginRight": "8px"}),
                                        html.Span(tf, style={"color": "#80cbc4", "fontSize": "12px"}),
                                    ],
                                    style={"marginBottom": "8px"},
                                ),
                                # Tags
                                html.Div([tag_pill(t) for t in tags[:6]]),
                            ]
                        ),
                        style={
                            "backgroundColor": "#1e1e1e",
                            "border": "1px solid #333",
                            "borderRadius": "6px",
                            "cursor": "pointer",
                            "transition": "border-color 0.15s",
                        },
                        class_name="h-100 strategy-card",
                    ),
                    href=f"/strategies/{name}",
                    style={"textDecoration": "none"},
                ),
                xs=12, sm=6, md=4, lg=3,
                className="mb-3",
            )
            cards.append(card)

        section = html.Div(
            [
                html.Div(
                    group_name,
                    style={"color": "#90caf9", "fontSize": "12px", "fontWeight": "bold",
                           "textTransform": "uppercase", "letterSpacing": "0.08em",
                           "marginBottom": "10px", "borderBottom": "1px solid #263238",
                           "paddingBottom": "4px"},
                ),
                dbc.Row(cards, className="g-3"),
            ],
            style={"marginBottom": "24px"},
        )
        sections.append(section)

    return html.Div(sections)


# ── Detail page callbacks ─────────────────────────────────────────────────────

@callback(
    Output("strat-detail-header",   "children"),
    Output("strat-detail-settings", "children"),
    Output("strat-detail-tags",     "children"),
    Output("strat-detail-code",     "children"),
    Input("strat-detail-name", "data"),
)
def populate_detail(strategy_name: str | None):
    if not strategy_name:
        return "—", [], [], ""

    raw = backtest_data.fetch_strategies()
    match = next((s for s in raw if s.get("name") == strategy_name), None)
    if not match:
        return (
            html.Span(f"{strategy_name} — not found", style={"color": "#f44336"}),
            [], [], "Strategy source not available.",
        )

    s = _enrich(match)

    # ── Header ──
    header = html.Div(
        [
            html.Span(s["name"], style={"color": "#e0e0e0", "fontWeight": "bold", "fontSize": "16px", "marginRight": "10px"}),
            folder_badge(s.get("folder", "inactive")),
            html.Span(
                f"  {s['group']}",
                style={"color": "#90caf9", "fontSize": "12px", "marginLeft": "12px"},
            ),
        ],
        className="d-flex align-items-center",
    )

    # ── Settings cards ──
    def _setting_row(label: str, value: str, color: str = "#ccc") -> html.Div:
        return html.Div(
            [
                html.Span(label, style={"color": "#888", "fontSize": "11px", "display": "block"}),
                html.Span(value, style={"color": color, "fontSize": "14px", "fontWeight": "bold"}),
            ],
            style={"marginBottom": "12px"},
        )

    paper_val = s["paper"].lower()
    paper_color = "#ff9800" if "true" in paper_val else "#4caf50"

    settings = html.Div(
        [
            _setting_row("Symbol",       s["symbol"] or "—"),
            _setting_row("Timeframe",    s["tf"] or "—"),
            _setting_row("Min Lookback", s["lookback"]),
            _setting_row("Stop Loss",    _pct_fmt(s["stop_loss"])),
            _setting_row("Max Position", _pct_fmt(s["max_pos"])),
            _setting_row("OB Mode",      s["ob_mode"]),
            _setting_row("Paper Trading", "Yes" if "true" in paper_val else "No", paper_color),
        ]
    )

    # ── Tags ──
    tags_section = html.Div(
        [
            html.Div("Tags", style={"color": "#888", "fontSize": "11px", "marginBottom": "6px"}),
            html.Div([tag_pill(t) for t in s["tags"]]),
        ]
    ) if s["tags"] else html.Div()

    return header, settings, tags_section, s.get("code", "")


def _pct_fmt(val: str) -> str:
    """Try to format a float string as a percentage; fall back to raw."""
    try:
        return f"{float(val) * 100:.1f}%"
    except (ValueError, TypeError):
        return val
