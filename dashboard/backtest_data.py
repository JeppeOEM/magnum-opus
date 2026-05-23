"""Backtest data layer — REST calls to bot-service and QuestDB queries."""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BOT_SERVICE_URL = os.environ.get("BOT_SERVICE_URL", "http://localhost:8090")
QUESTDB_URL = os.environ.get("QUESTDB_HTTP_ADDR", "http://questdb:9000")

_SAFE_IDENT = re.compile(r"^[A-Za-z0-9._\-]+$")


def _qdb(sql: str) -> list[dict[str, Any]]:
    try:
        resp = requests.get(f"{QUESTDB_URL}/exec", params={"query": sql}, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            logger.warning("questdb_error: %s", body["error"])
            return []
        cols = [c["name"] for c in body.get("columns", [])]
        return [dict(zip(cols, row)) for row in body.get("dataset", [])]
    except Exception as exc:
        logger.warning("questdb_query_failed: %s", exc)
        return []


def fetch_strategies() -> list[dict[str, Any]]:
    try:
        resp = requests.get(f"{BOT_SERVICE_URL}/strategies", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("fetch_strategies_failed: %s", exc)
        return []


def fetch_available_symbols(exchange: str | None = None) -> list[dict[str, Any]]:
    """Return distinct (exchange, symbol) pairs that have data in snapshot_1s."""
    params: dict[str, str] = {}
    if exchange:
        params["exchange"] = exchange
    try:
        resp = requests.get(
            f"{BOT_SERVICE_URL}/backtest/available-symbols",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("fetch_available_symbols_failed: %s", exc)
        return []


def submit_backtest(
    strategy_name: str,
    symbol: str,
    tf: str,
    exchange: str,
    start_date: str,
    end_date: str,
    capital: float,
    sample_every: int = 60,
) -> dict[str, Any]:
    try:
        resp = requests.post(
            f"{BOT_SERVICE_URL}/backtest/run",
            json={
                "strategy_name": strategy_name,
                "symbol": symbol,
                "tf": tf,
                "exchange": exchange,
                "start_date": start_date,
                "end_date": end_date,
                "initial_capital": capital,
                "sample_every": sample_every,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("submit_backtest_failed: %s", exc)
        return {"error": str(exc)}


def poll_run_status(run_id: str) -> dict[str, Any]:
    try:
        resp = requests.get(f"{BOT_SERVICE_URL}/backtest/run/{run_id}", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("poll_run_status_failed: %s", exc)
        return {"error": str(exc)}


def fetch_run_history(
    strategy_name: str | None = None,
    hash_filter: str | None = None,
    limit: int = 200,
) -> pd.DataFrame:
    wheres: list[str] = []
    if strategy_name and _SAFE_IDENT.match(strategy_name):
        wheres.append(f"strategy_name = '{strategy_name}'")
    if hash_filter and _SAFE_IDENT.match(hash_filter):
        wheres.append(f"hash = '{hash_filter}'")
    where = f" WHERE {' AND '.join(wheres)}" if wheres else ""
    sql = f"SELECT * FROM backtest_runs{where} ORDER BY run_at DESC LIMIT {int(limit)}"
    rows = _qdb(sql)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


_CHART_TABLE: dict[str, str] = {
    "1s":  "snapshot_1s",
    "1m":  "snapshot_1m",
    "5m":  "snapshot_1m",
    "15m": "snapshot_15m",
    "1h":  "snapshot_15m",
    "4h":  "snapshot_15m",
    "1d":  "snapshot_15m",
    "1w":  "snapshot_15m",
}

_TF_BAR_SECONDS: dict[str, int] = {
    "1s": 1, "1m": 60, "5m": 300, "15m": 900,
    "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800,
}


def fetch_candles_around(
    exchange: str,
    symbol: str,
    center_ts: str,
    tf: str = "1s",
    window: int = 300,
) -> list[dict]:
    """Return up to ``2*window`` candles centred on *center_ts*.

    Fetches from the raw snapshot table for *tf* (no aggregation — raw bars
    give the highest resolution for chart inspection).  Returns dicts with
    the same keys as the snapshot table rows.
    """
    from datetime import datetime, timezone, timedelta

    if not _SAFE_IDENT.match(exchange) or not _SAFE_IDENT.match(symbol):
        return []
    table = _CHART_TABLE.get(tf, "snapshot_1s")
    bar_sec = _TF_BAR_SECONDS.get(tf, 1)
    half_sec = window * bar_sec

    try:
        ts = center_ts.replace("Z", "+00:00")
        center_dt = datetime.fromisoformat(ts)
        if center_dt.tzinfo is None:
            center_dt = center_dt.replace(tzinfo=timezone.utc)
    except Exception:
        logger.warning("fetch_candles_around: bad center_ts %r", center_ts)
        return []

    start_dt = center_dt - timedelta(seconds=half_sec)
    end_dt   = center_dt + timedelta(seconds=half_sec)
    start_str = start_dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    end_str   = end_dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    sql = (
        f"SELECT * FROM {table} "
        f"WHERE exchange='{exchange}' AND symbol='{symbol}' "
        f"AND ts >= '{start_str}' AND ts <= '{end_str}' "
        f"ORDER BY ts ASC LIMIT {window * 2 + 50}"
    )
    return _qdb(sql)


def fetch_equity_curve(run_id: str) -> pd.DataFrame:
    if not _SAFE_IDENT.match(run_id):
        return pd.DataFrame()
    sql = (
        f"SELECT bar_ts, portfolio_value FROM backtest_equity "
        f"WHERE run_id = '{run_id}' ORDER BY bar_ts ASC"
    )
    rows = _qdb(sql)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["bar_ts"] = pd.to_datetime(df["bar_ts"], utc=True, errors="coerce")
    return df


def build_equity_from_result(result: dict) -> pd.DataFrame:
    """Build equity curve DataFrame from the in-memory result dict.

    Avoids the QuestDB WAL-commit latency that would cause an empty chart
    immediately after a run completes.  Falls back to empty DF on bad data.
    """
    eq = result.get("equity_curve", [])
    if not eq:
        return pd.DataFrame()
    # equity_curve is [[iso_ts, value], ...] from dataclasses.asdict
    df = pd.DataFrame(eq, columns=["bar_ts", "portfolio_value"])
    df["bar_ts"] = pd.to_datetime(df["bar_ts"], utc=True, errors="coerce")
    df = df.dropna(subset=["bar_ts"])
    return df


def fetch_candle_detail(
    exchange: str,
    symbol: str,
    ts: str,
    tf: str = "1s",
) -> dict:
    """Return all columns for the single candle that contains *ts*.

    Uses the same table mapping as fetch_candles_around.  Returns an empty
    dict when the row is not found or inputs are invalid.
    """
    from datetime import datetime, timezone, timedelta

    if not _SAFE_IDENT.match(exchange) or not _SAFE_IDENT.match(symbol):
        return {}

    table = _CHART_TABLE.get(tf, "snapshot_1s")
    bar_sec = _TF_BAR_SECONDS.get(tf, 1)

    try:
        ts_clean = ts.replace("Z", "+00:00")
        bar_dt = datetime.fromisoformat(ts_clean)
        if bar_dt.tzinfo is None:
            bar_dt = bar_dt.replace(tzinfo=timezone.utc)
    except Exception:
        logger.warning("fetch_candle_detail: bad ts %r", ts)
        return {}

    # Widen window slightly to account for sub-second precision mismatches
    start_dt = bar_dt - timedelta(milliseconds=500)
    end_dt = bar_dt + timedelta(seconds=bar_sec + 1)
    start_str = start_dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    end_str = end_dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    sql = (
        f"SELECT * FROM {table} "
        f"WHERE exchange='{exchange}' AND symbol='{symbol}' "
        f"AND ts >= '{start_str}' AND ts < '{end_str}' "
        f"LIMIT 1"
    )
    rows = _qdb(sql)
    return rows[0] if rows else {}
