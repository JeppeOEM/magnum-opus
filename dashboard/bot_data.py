"""Bot management data layer — queries order_events from QuestDB + bot-service /strategies/detail."""
from __future__ import annotations

import logging
import re
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_SAFE_IDENT = re.compile(r"^[A-Za-z0-9._\-]+$")


def _q(questdb_url: str, sql: str) -> list[dict[str, Any]]:
    """Execute a SQL query against QuestDB and return rows as dicts."""
    try:
        resp = requests.get(
            f"{questdb_url}/exec",
            params={"query": sql},
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            logger.warning("questdb_query_error: %s", body["error"])
            return []
        cols = [c["name"] for c in body.get("columns", [])]
        return [dict(zip(cols, row)) for row in body.get("dataset", [])]
    except Exception as exc:
        logger.warning("questdb_query_failed: %s", exc)
        return []


def _mode_filter(paper: bool) -> str:
    return "paper_trading = true" if paper else "paper_trading = false"


# ── Live bot-service introspection ────────────────────────────────────────────

def fetch_running_bots(bot_service_url: str) -> list[dict[str, Any]]:
    """Return live metadata from the bot service for every loaded strategy.

    Each dict contains: name, status, started_at (unix float), exchange, symbol,
    tf, paper_trading, stop_loss_pct, max_position_pct, min_lookback.
    Returns [] if the bot service is unreachable.
    """
    try:
        resp = requests.get(
            f"{bot_service_url}/strategies/detail",
            timeout=3,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("fetch_running_bots failed: %s", exc)
        return []


# ── QuestDB trade queries ─────────────────────────────────────────────────────

def fetch_bot_overview(paper: bool, questdb_url: str) -> list[dict[str, Any]]:
    """Return one row per active bot (strategy/exchange/symbol) with PnL, trade stats, win rate."""
    f = _mode_filter(paper)
    sql = f"""
        SELECT
            strategy,
            exchange,
            symbol,
            count() AS trade_count,
            round(sum(realized_pnl), 4) AS total_pnl,
            round(avg(realized_pnl), 4) AS avg_pnl_per_trade,
            round(
                sum(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END) / count() * 100.0,
                1
            ) AS win_rate_pct,
            max(ts) AS last_trade_ts
        FROM order_events
        WHERE status = 'filled'
          AND {f}
          AND backtest = false
        GROUP BY strategy, exchange, symbol
        ORDER BY total_pnl DESC
    """
    return _q(questdb_url, sql)


def fetch_total_pnl(paper: bool, questdb_url: str) -> float:
    """Return aggregate PnL across all filled orders for the given mode."""
    f = _mode_filter(paper)
    sql = f"""
        SELECT round(sum(realized_pnl), 4) AS total_pnl
        FROM order_events
        WHERE status = 'filled'
          AND {f}
          AND backtest = false
    """
    rows = _q(questdb_url, sql)
    if rows and rows[0].get("total_pnl") is not None:
        return float(rows[0]["total_pnl"])
    return 0.0


def fetch_recent_trades(paper: bool, questdb_url: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent filled orders."""
    f = _mode_filter(paper)
    sql = f"""
        SELECT
            ts,
            strategy,
            exchange,
            symbol,
            side,
            filled_size,
            avg_fill_price,
            round(realized_pnl, 4) AS realized_pnl,
            signal_type
        FROM order_events
        WHERE status = 'filled'
          AND {f}
          AND backtest = false
        ORDER BY ts DESC
        LIMIT {int(limit)}
    """
    return _q(questdb_url, sql)


def fetch_bot_trades(
    strategy: str,
    exchange: str,
    symbol: str,
    paper: bool,
    questdb_url: str,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return trade history for a specific bot (strategy/exchange/symbol)."""
    if not all(_SAFE_IDENT.match(v) for v in [strategy, exchange, symbol]):
        logger.warning("fetch_bot_trades: invalid identifier")
        return []
    f = _mode_filter(paper)
    sql = f"""
        SELECT
            ts,
            side,
            filled_size,
            avg_fill_price,
            round(realized_pnl, 4) AS realized_pnl,
            signal_type,
            order_id
        FROM order_events
        WHERE strategy = '{strategy}'
          AND exchange = '{exchange}'
          AND symbol = '{symbol}'
          AND status = 'filled'
          AND {f}
        ORDER BY ts ASC
        LIMIT {int(limit)}
    """
    return _q(questdb_url, sql)


# ── Merge helpers ─────────────────────────────────────────────────────────────

def format_uptime(started_at: float | None) -> str | None:
    """Format seconds since started_at as 'Xd Xh Xm' or 'Xh Xm' or 'Xm'."""
    import time
    if started_at is None:
        return None
    elapsed = int(time.time() - started_at)
    if elapsed < 0:
        return "0m"
    days, rem = divmod(elapsed, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def merge_bots(
    running: list[dict[str, Any]],
    trade_stats: list[dict[str, Any]],
    paper: bool,
) -> list[dict[str, Any]]:
    """Merge live running bots with QuestDB trade stats.

    Running bots always appear.  Trade stats fill in when available; otherwise
    numeric fields are None so the table shows blank cells.

    Filtering by paper_trading is applied here: bots whose paper_trading property
    doesn't match the selected mode are excluded.
    """
    # Index trade stats by (strategy, exchange, symbol)
    stats_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in trade_stats:
        key = (row["strategy"], row.get("exchange", ""), row.get("symbol", ""))
        stats_index[key] = row

    merged: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for bot in running:
        # Filter by paper/live mode
        if bot.get("paper_trading") is not None and bool(bot["paper_trading"]) != paper:
            continue

        name = bot.get("name", "")
        exchange = bot.get("exchange") or ""
        symbol = bot.get("symbol") or ""
        key = (name, exchange, symbol)
        seen_keys.add(key)

        stats = stats_index.get(key, {})
        merged.append({
            "strategy": name,
            "exchange": exchange,
            "symbol": symbol,
            "tf": bot.get("tf"),
            "status": bot.get("status", "unknown"),
            "uptime": format_uptime(bot.get("started_at")),
            "stop_loss_pct": bot.get("stop_loss_pct"),
            "max_position_pct": bot.get("max_position_pct"),
            "trade_count": stats.get("trade_count"),
            "win_rate_pct": stats.get("win_rate_pct"),
            "total_pnl": float(stats["total_pnl"]) if stats.get("total_pnl") is not None else None,
            "avg_pnl_per_trade": float(stats["avg_pnl_per_trade"]) if stats.get("avg_pnl_per_trade") is not None else None,
            "last_trade_ts": str(stats.get("last_trade_ts", ""))[:19] if stats.get("last_trade_ts") else None,
        })

    return merged


# ── Metrics / equity curve ────────────────────────────────────────────────────

def compute_equity_curve(trades: list[dict[str, Any]]) -> pd.DataFrame:
    """Compute cumulative PnL curve from a list of filled trade dicts."""
    if not trades:
        return pd.DataFrame(columns=["ts", "cumulative_pnl", "realized_pnl"])
    df = pd.DataFrame(trades)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df["realized_pnl"] = pd.to_numeric(df["realized_pnl"], errors="coerce").fillna(0.0)
    df = df.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    df["cumulative_pnl"] = df["realized_pnl"].cumsum()
    return df[["ts", "cumulative_pnl", "realized_pnl"]]


def compute_bot_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary metrics for a bot from its filled trades."""
    if not trades:
        return {
            "trade_count": 0,
            "total_pnl": 0.0,
            "win_rate": 0.0,
            "max_drawdown": 0.0,
            "avg_pnl_per_trade": 0.0,
        }
    df = pd.DataFrame(trades)
    pnl = pd.to_numeric(df["realized_pnl"], errors="coerce").fillna(0.0)
    total_pnl = float(pnl.sum())
    trade_count = len(df)
    win_rate = float((pnl > 0).sum() / trade_count) if trade_count > 0 else 0.0
    avg_pnl = float(pnl.mean()) if trade_count > 0 else 0.0

    equity = pnl.cumsum()
    running_max = equity.cummax()
    drawdown = equity - running_max
    max_drawdown = float(drawdown.min())

    return {
        "trade_count": trade_count,
        "total_pnl": round(total_pnl, 4),
        "win_rate": round(win_rate * 100, 1),
        "max_drawdown": round(max_drawdown, 4),
        "avg_pnl_per_trade": round(avg_pnl, 4),
    }
