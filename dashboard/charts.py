import copy

import pandas as pd
import plotly.graph_objects as go

from app import BASE_FIGURE


def _base_fig() -> go.Figure:
    # Use deepcopy to avoid accumulating traces on the BASE_FIGURE singleton
    # across repeated callback invocations under live updates.
    return copy.deepcopy(BASE_FIGURE)


def build_candlestick(df: pd.DataFrame) -> go.Figure:
    fig = _base_fig()
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
        ),
        row=1, col=1,
    )
    fig.update_layout(
        paper_bgcolor="#222222",
        plot_bgcolor="#222222",
        font_color="#CCCCCC",
        xaxis_rangeslider_visible=False,
        margin=dict(l=40, r=10, t=20, b=30),
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

    # Value Area: levels sorted by volume descending, cumsum to 70%
    sorted_levels = level_vol.sort_values(ascending=False)
    cumsum = sorted_levels.cumsum()
    va_levels = sorted_levels[cumsum <= total_vol * 0.70].index
    va_low = float(va_levels.min()) if len(va_levels) > 0 else None
    va_high = float(va_levels.max()) if len(va_levels) > 0 else None

    # Value Area band
    if va_low is not None and va_high is not None:
        fig.add_hrect(
            y0=va_low, y1=va_high,
            fillcolor="rgba(255,200,0,0.08)",
            line_width=0,
            row=1, col=1,
        )

    # Top 20 price levels rendered as horizontal shapes with xref="x domain"
    # (subplot-relative x-coordinates) to avoid clash with the timestamp x-axis.
    top_levels = level_vol.nlargest(20)
    max_level_vol = float(top_levels.max())
    for price, vol in top_levels.items():
        bar_width = float(vol) / max_level_vol * 0.15  # up to 15% of subplot width
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
            row=1, col=1,
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
        ),
        row=1, col=1,
    )
    return fig
