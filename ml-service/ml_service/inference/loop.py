"""Live inference loop — polls QuestDB every ~1s per symbol, reads regime from Redis,
calls predict(), and publishes signals to Redis stream `ai:{symbol}:signals`.

Design:
- One asyncio Task per (exchange, symbol) pair.
- Idempotent: skips publish when `ts` hasn't changed since last iteration.
- Errors logged and loop continues; 3 consecutive errors → ERROR log.
- `start()` / `stop()` / `status()` manage task lifecycle.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import redis.asyncio as aioredis
import structlog

from ml_service.inference.engine import clear_cache, predict

log = structlog.get_logger()

_running_tasks: dict[str, asyncio.Task] = {}
_STREAM_MAXLEN = 10_000


async def _fetch_latest_row(questdb_url: str, exchange: str, symbol: str) -> dict | None:
    """Fetch the single most-recent snapshot_1s row for (exchange, symbol)."""
    sql = (
        f"SELECT * FROM snapshot_1s "
        f"WHERE exchange='{exchange}' AND symbol='{symbol}' "
        f"ORDER BY ts DESC LIMIT 1"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            questdb_url + "/exec", params={"query": sql}, timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("dataset"):
            return None
        cols = [c["name"] for c in data["columns"]]
        return dict(zip(cols, data["dataset"][0]))


async def _get_regime(r: aioredis.Redis, exchange: str, symbol: str) -> str:
    """Read current regime from Redis key `regime:{exchange}:{symbol}`. Default RANGING."""
    val = await r.get(f"regime:{exchange}:{symbol}")
    return val.decode() if val else "RANGING"


async def _symbol_loop(
    questdb_url: str,
    redis_url: str,
    exchange: str,
    symbol: str,
    interval_s: float = 1.0,
) -> None:
    """Core inference loop for a single (exchange, symbol) pair.

    Runs until cancelled. Redis client is opened and closed in this coroutine.
    """
    r = aioredis.from_url(redis_url)
    last_ts: str | None = None
    consecutive_errors = 0

    try:
        while True:
            t0 = time.monotonic()
            try:
                row = await _fetch_latest_row(questdb_url, exchange, symbol)
                if row is None or str(row.get("ts")) == last_ts:
                    await asyncio.sleep(max(0.0, interval_s - (time.monotonic() - t0)))
                    continue

                regime = await _get_regime(r, exchange, symbol)
                sig = predict(row, exchange, symbol, regime)
                latency_ms = int((time.monotonic() - t0) * 1000)

                stream_key = f"ai:{symbol}:signals"
                await r.xadd(
                    stream_key,
                    {
                        "ts": str(row["ts"]),
                        "exchange": exchange,
                        "symbol": symbol,
                        "regime": sig.regime,
                        "cluster": str(sig.cluster),
                        "signal": sig.label,
                        "confidence": f"{sig.confidence:.4f}",
                        "model_id": sig.model_id or "",
                        "deviation_bps": f"{sig.deviation_bps:.4f}",
                        "latency_ms": str(latency_ms),
                    },
                    maxlen=_STREAM_MAXLEN,
                    approximate=True,
                )

                last_ts = str(row["ts"])
                consecutive_errors = 0

                log.debug(
                    "signal_published",
                    symbol=symbol,
                    signal=sig.label,
                    confidence=round(sig.confidence, 4),
                    latency_ms=latency_ms,
                )

            except asyncio.CancelledError:
                raise
            except Exception as e:
                consecutive_errors += 1
                log.warning(
                    "inference_loop_error",
                    symbol=symbol,
                    error=str(e),
                    consecutive=consecutive_errors,
                )
                if consecutive_errors >= 3:
                    log.error(
                        "inference_loop_repeated_errors",
                        symbol=symbol,
                        consecutive=consecutive_errors,
                    )

            await asyncio.sleep(max(0.0, interval_s - (time.monotonic() - t0)))

    finally:
        await r.aclose()


def start(questdb_url: str, redis_url: str, symbols: list[dict]) -> dict:
    """Start per-symbol inference loops. Already-running tasks are skipped.

    symbols = [{"exchange": "bybit", "symbol": "BTCUSDT"}, ...]
    """
    started = []
    already_running = []
    for s in symbols:
        key = f"{s['exchange']}:{s['symbol']}"
        if key in _running_tasks and not _running_tasks[key].done():
            already_running.append(key)
            continue
        task = asyncio.create_task(
            _symbol_loop(questdb_url, redis_url, s["exchange"], s["symbol"])
        )
        _running_tasks[key] = task
        started.append(key)
    return {"started": started, "already_running": already_running}


async def stop(symbols: list[str] | None = None) -> dict:
    """Cancel inference loops. If symbols=None, stop all. Waits up to 2s per task."""
    keys = symbols or list(_running_tasks.keys())
    stopped = []
    for key in list(keys):
        task = _running_tasks.pop(key, None)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            stopped.append(key)
    return {"stopped": stopped}


def status() -> dict:
    """Return running/stopped state for all known tasks."""
    return {
        k: "running" if not t.done() else "stopped"
        for k, t in _running_tasks.items()
    }
