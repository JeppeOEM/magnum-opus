import json

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

_DARK = dict(
    paper_bgcolor="#222222",
    plot_bgcolor="#222222",
    font_color="#CCCCCC",
)


def build_candlestick(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**_DARK, xaxis_rangeslider_visible=False, margin=dict(l=40, r=10, t=20, b=30))
    if df.empty:
        return fig
    fig.add_trace(
        go.Candlestick(
            x=df["ts"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            increasing_line_color="#26A69A",
            decreasing_line_color="#EF5350",
            name="OHLCV",
        )
    )
    return fig


def add_volume_levels(fig: go.Figure, df: pd.DataFrame) -> go.Figure:
    df = df.copy()
    price_range = df["high"].max() - df["low"].min()
    if price_range <= 0:
        return fig
    bucket_size = max(price_range / 100, 0.01)
    df["price_level"] = (df["close"] / bucket_size).round() * bucket_size

    level_vol = df.groupby("price_level")["volume"].sum()
    if level_vol.empty:
        return fig

    poc = level_vol.idxmax()
    total_vol = level_vol.sum()

    sorted_levels = level_vol.sort_values(ascending=False)
    cumsum = sorted_levels.cumsum()
    va_levels = sorted_levels[cumsum <= total_vol * 0.70].index
    va_low = float(va_levels.min()) if len(va_levels) > 0 else None
    va_high = float(va_levels.max()) if len(va_levels) > 0 else None

    if va_low is not None and va_high is not None:
        fig.add_hrect(y0=va_low, y1=va_high, fillcolor="rgba(255,200,0,0.08)", line_width=0)

    top_levels = level_vol.nlargest(20)
    max_level_vol = float(top_levels.max())
    for price, vol in top_levels.items():
        bar_width = float(vol) / max_level_vol * 0.15
        color = "#FFD700" if float(price) == float(poc) else "rgba(100,130,210,0.35)"
        opacity = 0.7 if float(price) == float(poc) else 0.4
        fig.add_shape(
            type="rect",
            xref="x domain", yref="y",
            x0=0.0, x1=bar_width,
            y0=float(price) - bucket_size * 0.4,
            y1=float(price) + bucket_size * 0.4,
            fillcolor=color,
            opacity=opacity,
            line_width=0,
        )

    return fig


def add_volume_bubbles(fig: go.Figure, df: pd.DataFrame) -> go.Figure:
    required = ["buy_volume", "volume", "high", "low", "ts"]
    if not all(c in df.columns for c in required):
        return fig

    valid = df.dropna(subset=["buy_volume", "volume"]).copy()
    valid = valid[valid["volume"] > 0]
    if valid.empty:
        return fig

    mid_price = (valid["high"] + valid["low"]) / 2
    max_vol = valid["volume"].max()
    marker_size = (valid["volume"] / max_vol * 30).clip(lower=4).tolist()
    colors = [
        "#26A69A" if float(b) / float(v) > 0.5 else "#EF5350"
        for b, v in zip(valid["buy_volume"], valid["volume"])
    ]

    fig.add_trace(
        go.Scatter(
            x=valid["ts"],
            y=mid_price,
            mode="markers",
            marker=dict(size=marker_size, color=colors, opacity=0.55),
            name="Vol Bubbles",
            showlegend=False,
            hoverinfo="skip",
        )
    )
    return fig


def build_delta_heatmap(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**_DARK, margin=dict(l=10, r=10, t=20, b=30))
    if df.empty:
        return fig
    required = ["ts", "close", "buy_volume", "volume"]
    if not all(c in df.columns for c in required):
        return fig

    df = df.dropna(subset=["buy_volume", "volume", "close"]).copy()
    df["delta"] = 2 * pd.to_numeric(df["buy_volume"], errors="coerce") - pd.to_numeric(df["volume"], errors="coerce")
    df = df.dropna(subset=["delta"])

    fig.add_trace(
        go.Heatmap(
            x=df["ts"],
            y=pd.to_numeric(df["close"], errors="coerce"),
            z=df["delta"],
            colorscale=[[0.0, "#EF5350"], [0.5, "#222222"], [1.0, "#26A69A"]],
            zmid=0,
            showscale=False,
            name="Delta",
        )
    )
    return fig


def add_ob_depth_heatmap(fig: go.Figure, ob_rows: list) -> go.Figure:
    if not ob_rows:
        return fig
    ob_df = pd.DataFrame(ob_rows)
    required_ob = ["bid_depth_l1", "ask_depth_l1", "best_bid"]
    if not all(c in ob_df.columns for c in required_ob):
        return fig

    for col in ["bid_depth_l1", "ask_depth_l1", "best_bid"]:
        ob_df[col] = pd.to_numeric(ob_df[col], errors="coerce")
    ob_df = ob_df.dropna(subset=["bid_depth_l1", "ask_depth_l1", "best_bid"])
    if ob_df.empty:
        return fig

    ob_df["depth"] = ob_df["bid_depth_l1"] + ob_df["ask_depth_l1"]
    ts_col = "ts" if "ts" in ob_df.columns else None
    fig.add_trace(
        go.Heatmap(
            x=ob_df[ts_col] if ts_col else list(range(len(ob_df))),
            y=ob_df["best_bid"],
            z=ob_df["depth"],
            colorscale="Blues",
            showscale=False,
            opacity=0.6,
            name="OB Depth",
        )
    )
    return fig


def build_vol_profile(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**_DARK, margin=dict(l=5, r=5, t=20, b=30), bargap=0.05)
    if df.empty:
        return fig
    required = ["close", "volume"]
    if not all(c in df.columns for c in required):
        return fig

    df = df.copy()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df = df.dropna(subset=["close", "volume"])

    price_range = df["close"].max() - df["close"].min()
    if price_range <= 0:
        return fig
    bucket_size = max(price_range / 50, 0.01)
    df["price_level"] = (df["close"] / bucket_size).round() * bucket_size
    level_vol = df.groupby("price_level")["volume"].sum().reset_index()

    fig.add_trace(
        go.Bar(
            x=level_vol["volume"],
            y=level_vol["price_level"],
            orientation="h",
            marker_color="rgba(100,130,210,0.5)",
            name="Vol Profile",
            showlegend=False,
        )
    )
    return fig


def build_cvd_panel(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**_DARK, margin=dict(l=40, r=10, t=10, b=30))
    if df.empty:
        return fig
    required = ["ts", "buy_volume", "volume"]
    if not all(c in df.columns for c in required):
        return fig

    df = df.copy()
    df["buy_volume"] = pd.to_numeric(df["buy_volume"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df = df.dropna(subset=["buy_volume", "volume", "ts"]).sort_values("ts")
    if df.empty:
        return fig

    df["cvd"] = (2 * df["buy_volume"] - df["volume"]).cumsum()
    pos = df["cvd"].clip(lower=0)
    neg = df["cvd"].clip(upper=0)

    fig.add_trace(
        go.Scatter(
            x=df["ts"], y=pos,
            fill="tozeroy",
            fillcolor="rgba(38,166,154,0.3)",
            line=dict(color="#26A69A", width=1),
            mode="lines",
            name="CVD+",
            showlegend=False,
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df["ts"], y=neg,
            fill="tozeroy",
            fillcolor="rgba(239,83,80,0.3)",
            line=dict(color="#EF5350", width=1),
            mode="lines",
            name="CVD-",
            showlegend=False,
        )
    )
    return fig


def build_bidask_panel(df: pd.DataFrame) -> go.Figure:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.update_layout(**_DARK, margin=dict(l=40, r=10, t=10, b=30), barmode="overlay")
    if df.empty:
        return fig
    required = ["ts", "buy_volume", "volume"]
    if not all(c in df.columns for c in required):
        return fig

    df = df.copy()
    df["buy_volume"] = pd.to_numeric(df["buy_volume"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df = df.dropna(subset=["buy_volume", "volume", "ts"]).sort_values("ts")
    df = df[df["volume"] > 0].copy()
    if df.empty:
        return fig

    ask_vol = df["buy_volume"]
    bid_vol = -(df["volume"] - df["buy_volume"])
    ratio = (df["buy_volume"] / df["volume"]).clip(0, 1)

    fig.add_trace(
        go.Bar(x=df["ts"], y=ask_vol, marker_color="#26A69A", name="Ask Vol", showlegend=False),
        secondary_y=False,
    )
    fig.add_trace(
        go.Bar(x=df["ts"], y=bid_vol, marker_color="#EF5350", name="Bid Vol", showlegend=False),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=df["ts"], y=ratio,
            line=dict(color="#CCCCCC", width=1),
            mode="lines",
            name="Ratio",
            showlegend=False,
        ),
        secondary_y=True,
    )
    fig.update_yaxes(range=[0, 1], secondary_y=True)
    return fig


def _longest_run(flags: list) -> int:
    max_len = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        max_len = max(max_len, cur)
    return max_len


def build_footprint_chart(footprint_json_str: str, single_print_levels=None) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**_DARK, margin=dict(l=60, r=10, t=30, b=30), barmode="overlay")
    try:
        fp = json.loads(footprint_json_str)
        if not fp or not isinstance(fp, dict):
            return fig
        # Sort by numeric value descending; keep original string keys for dict lookup
        price_pairs = sorted(
            ((float(p), p) for p in fp.keys()),
            reverse=True,
        )
        prices_numeric = [num for num, _ in price_pairs]
        buy_vols = []
        sell_vols = []
        buy_imb = []
        sell_imb = []
        for _, key in price_pairs:
            cell = fp[key]
            if not isinstance(cell, dict):
                cell = {}
            b = float(cell.get("b") or 0)
            s = float(cell.get("s") or 0)
            buy_vols.append(b)
            sell_vols.append(-s)
            buy_imb.append(s > 0 and b > 3.0 * s)
            sell_imb.append(b > 0 and s > 3.0 * b)

        bar_colors_buy = ["#00E676" if imb else "#26A69A" for imb in buy_imb]
        bar_colors_sell = ["#FF1744" if imb else "#EF5350" for imb in sell_imb]
        stack_buy = _longest_run(buy_imb)
        stack_sell = _longest_run(sell_imb)

        sp_count = 0
        if single_print_levels:
            for i, (_, key) in enumerate(price_pairs):
                if str(float(key)) in single_print_levels:
                    bar_colors_buy[i] = "#FFC107"
                    bar_colors_sell[i] = "#FF6F00"
                    sp_count += 1

        fig.add_trace(
            go.Bar(
                y=prices_numeric,
                x=buy_vols,
                orientation="h",
                name="Buy",
                marker_color=bar_colors_buy,
                showlegend=False,
            )
        )
        fig.add_trace(
            go.Bar(
                y=prices_numeric,
                x=sell_vols,
                orientation="h",
                name="Sell",
                marker_color=bar_colors_sell,
                showlegend=False,
            )
        )
        fig.update_layout(
            title=dict(
                text=f"↑ Stack {stack_buy}  ↓ Stack {stack_sell}  | SP: {sp_count}",
                font=dict(size=12, color="#CCCCCC"),
                x=0.5,
            )
        )
    except Exception:
        return fig
    return fig


def add_iceberg_borders(fig: go.Figure, df: pd.DataFrame) -> go.Figure:
    if df.empty or "ts" not in df.columns:
        return fig
    if "iceberg_bid_detected" not in df.columns and "iceberg_ask_detected" not in df.columns:
        return fig
    if "iceberg_price" not in df.columns:
        return fig

    prices = pd.to_numeric(df["iceberg_price"], errors="coerce")

    if "iceberg_bid_detected" in df.columns:
        raw_bid = df["iceberg_bid_detected"].replace({"true": 1, "false": 0, "True": 1, "False": 0})
        mask = pd.to_numeric(raw_bid, errors="coerce").fillna(0).astype(bool)
        bid_df = df[mask].copy()
        bid_df["_price"] = prices[mask]
        bid_df = bid_df.dropna(subset=["_price"])
        if not bid_df.empty:
            fig.add_trace(
                go.Scatter(
                    x=bid_df["ts"],
                    y=bid_df["_price"],
                    mode="markers",
                    marker=dict(symbol="square-open", size=14, color="#00BCD4", line=dict(width=2)),
                    name="Iceberg Bid",
                    showlegend=False,
                    hoverinfo="skip",
                )
            )

    if "iceberg_ask_detected" in df.columns:
        raw_ask = df["iceberg_ask_detected"].replace({"true": 1, "false": 0, "True": 1, "False": 0})
        mask = pd.to_numeric(raw_ask, errors="coerce").fillna(0).astype(bool)
        ask_df = df[mask].copy()
        ask_df["_price"] = prices[mask]
        ask_df = ask_df.dropna(subset=["_price"])
        if not ask_df.empty:
            fig.add_trace(
                go.Scatter(
                    x=ask_df["ts"],
                    y=ask_df["_price"],
                    mode="markers",
                    marker=dict(symbol="square-open", size=14, color="#E040FB", line=dict(width=2)),
                    name="Iceberg Ask",
                    showlegend=False,
                    hoverinfo="skip",
                )
            )

    return fig
