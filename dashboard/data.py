import logging
import os
import re

import redis as redis_lib
import requests

logger = logging.getLogger(__name__)

_SAFE_IDENT = re.compile(r'^[A-Za-z0-9._\-]+$')
_SAFE_TS = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$')

_redis_client = redis_lib.from_url(os.environ.get("REDIS_URL", "redis://redis:6379"))


def _decode(v) -> str:
    return v.decode() if isinstance(v, bytes) else v


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


def fetch_new_candles(exchange: str, symbol: str, last_ts: str, questdb_url: str) -> list[dict]:
    if not _SAFE_IDENT.match(exchange) or not _SAFE_IDENT.match(symbol):
        logger.error("fetch_new_candles: unsafe exchange=%r symbol=%r", exchange, symbol)
        return []
    if not last_ts:
        logger.error("fetch_new_candles: last_ts is None or empty")
        return []
    if not _SAFE_TS.match(last_ts):
        logger.error("fetch_new_candles: unsafe last_ts=%r", last_ts)
        return []

    query = (
        f"SELECT * FROM snapshot_1s "
        f"WHERE exchange='{exchange}' AND symbol='{symbol}' AND ts > '{last_ts}' "
        f"ORDER BY ts ASC LIMIT 100"
    )
    try:
        resp = requests.get(f"{questdb_url}/exec", params={"query": query}, timeout=5)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("QuestDB live poll failed exchange=%s symbol=%s: %s", exchange, symbol, e)
        return []

    try:
        body = resp.json()
    except ValueError as e:
        logger.error("QuestDB live poll response not JSON exchange=%s symbol=%s: %s", exchange, symbol, e)
        return []

    cols = [c["name"] for c in body.get("columns", [])]
    return [dict(zip(cols, row)) for row in body.get("dataset", [])]


def fetch_ob_snapshot(exchange: str, symbol: str) -> str:
    stream_key = f"ob_features:{exchange}:{symbol}"
    try:
        entries = _redis_client.xrevrange(stream_key, count=1)
        if entries:
            return _decode(entries[0][0])
        return '0'
    except redis_lib.RedisError as e:
        logger.error("ob_features cursor seed failed %s: %s", stream_key, e)
        return '0'


def fetch_ob_live(exchange: str, symbol: str, cursor_id: str) -> tuple[list[dict], str]:
    stream_key = f"ob_features:{exchange}:{symbol}"
    try:
        result = _redis_client.xread({stream_key: cursor_id}, count=100)
        if not result:
            return [], cursor_id
        entries_raw = result[0][1]
        entries = []
        new_cursor = cursor_id
        for entry_id, fields in entries_raw:
            entry_id_str = _decode(entry_id)
            row = {_decode(k): _decode(v) for k, v in fields.items()}
            row["_id"] = entry_id_str
            entries.append(row)
            new_cursor = entry_id_str
        return entries, new_cursor
    except redis_lib.RedisError as e:
        logger.error("ob_features XREAD failed %s cursor=%s: %s", stream_key, cursor_id, e)
        return [], cursor_id
