import logging
import re

import requests

logger = logging.getLogger(__name__)

_SAFE_IDENT = re.compile(r'^[A-Za-z0-9._\-]+$')


def fetch_history(exchange: str, symbol: str, questdb_url: str, limit: int = 500) -> list[dict]:
    if not _SAFE_IDENT.match(exchange) or not _SAFE_IDENT.match(symbol):
        logger.error("fetch_history: unsafe exchange=%r symbol=%r rejected", exchange, symbol)
        return []

    query = (
        f"SELECT * FROM snapshot_1s "
        f"WHERE exchange='{exchange}' AND symbol='{symbol}' "
        f"ORDER BY ts DESC LIMIT {limit}"
    )
    try:
        resp = requests.get(f"{questdb_url}/exec", params={"query": query}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("QuestDB history fetch failed exchange=%s symbol=%s: %s", exchange, symbol, e)
        return []

    try:
        body = resp.json()
    except ValueError as e:
        logger.error("QuestDB response not JSON exchange=%s symbol=%s: %s", exchange, symbol, e)
        return []

    cols = [c["name"] for c in body.get("columns", [])]
    rows = [dict(zip(cols, row)) for row in body.get("dataset", [])]
    rows.reverse()  # ORDER BY ts DESC → ascending (newest last)
    return rows
