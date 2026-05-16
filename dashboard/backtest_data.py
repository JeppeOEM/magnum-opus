"""Backtest data layer — REST calls to bot-service and QuestDB queries."""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BOT_SERVICE_URL = os.environ.get("BOT_SERVICE_ADDR", "http://bot-service:8090")
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
