---
id: 38-2
title: Live signal loop and Redis publisher
epic: 38
status: ready-for-dev
---

# Story 38-2: Live Signal Loop and Redis Publisher

## Context

The inference loop polls QuestDB for the latest `snapshot_1s` row every ~1s per symbol, reads current regime from Redis, calls the engine, and publishes to `ai:{symbol}:signals`. Idempotent on repeated same-ts rows. Errors logged but loop continues.

## What to build

### `ml-service/ml_service/inference/loop.py`

```python
from __future__ import annotations
import asyncio
import time
from typing import Any

import httpx
import redis.asyncio as aioredis
import structlog

from ml_service.inference.engine import predict, clear_cache
from ml_service.registry.registry import get_all

log = structlog.get_logger()

_running_tasks: dict[str, asyncio.Task] = {}
_STREAM_MAXLEN = 10_000


async def _fetch_latest_row(questdb_url: str, exchange: str, symbol: str) -> dict | None:
    sql = (
        f"SELECT * FROM snapshot_1s "
        f"WHERE exchange='{exchange}' AND symbol='{symbol}' "
        f"ORDER BY ts DESC LIMIT 1"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(questdb_url + "/exec", params={"query": sql}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("dataset"):
            return None
        cols = [c["name"] for c in data["columns"]]
        return dict(zip(cols, data["dataset"][0]))


async def _get_regime(r: aioredis.Redis, exchange: str, symbol: str) -> str:
    val = await r.get(f"regime:{exchange}:{symbol}")
    return val.decode() if val else "RANGING"


async def _symbol_loop(
    questdb_url: str,
    redis_url: str,
    exchange: str,
    symbol: str,
    interval_s: float = 1.0,
) -> None:
    r = aioredis.from_url(redis_url)
    last_ts: str | None = None
    consecutive_errors = 0

    try:
        while True:
            t0 = time.monotonic()
            try:
                row = await _fetch_latest_row(questdb_url, exchange, symbol)
                if row is None or row.get("ts") == last_ts:
                    await asyncio.sleep(max(0, interval_s - (time.monotonic() - t0)))
                    continue

                regime = await _get_regime(r, exchange, symbol)
                sig = predict(row, exchange, symbol, regime)
                latency_ms = int((time.monotonic() - t0) * 1000)

                stream_key = f"ai:{symbol}:signals"
                await r.xadd(stream_key, {
                    "ts":           str(row["ts"]),
                    "exchange":     exchange,
                    "symbol":       symbol,
                    "regime":       sig.regime,
                    "cluster":      str(sig.cluster),
                    "signal":       sig.label,
                    "confidence":   f"{sig.confidence:.4f}",
                    "model_id":     sig.model_id or "",
                    "deviation_bps": f"{sig.deviation_bps:.4f}",
                    "latency_ms":   str(latency_ms),
                }, maxlen=_STREAM_MAXLEN, approximate=True)

                last_ts = row["ts"]
                consecutive_errors = 0

                log.debug("signal_published", symbol=symbol, signal=sig.label,
                          confidence=sig.confidence, latency_ms=latency_ms)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                consecutive_errors += 1
                log.warning("inference_loop_error", symbol=symbol,
                            error=str(e), consecutive=consecutive_errors)
                if consecutive_errors >= 3:
                    log.error("inference_loop_repeated_errors", symbol=symbol)

            await asyncio.sleep(max(0, interval_s - (time.monotonic() - t0)))

    finally:
        await r.aclose()


def start(questdb_url: str, redis_url: str, symbols: list[dict]) -> dict:
    """Start inference loops. symbols = [{"exchange": "bybit", "symbol": "BTCUSDT"}]"""
    started = []
    for s in symbols:
        key = f"{s['exchange']}:{s['symbol']}"
        if key in _running_tasks and not _running_tasks[key].done():
            continue
        task = asyncio.create_task(
            _symbol_loop(questdb_url, redis_url, s["exchange"], s["symbol"])
        )
        _running_tasks[key] = task
        started.append(key)
    return {"started": started, "already_running": [k for k in _running_tasks if k not in started]}


async def stop(symbols: list[str] | None = None) -> dict:
    """Stop loops. If symbols=None, stop all."""
    keys = symbols or list(_running_tasks.keys())
    stopped = []
    for key in keys:
        task = _running_tasks.pop(key, None)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            stopped.append(key)
    return {"stopped": stopped}


def status() -> dict:
    return {
        k: "running" if not t.done() else "stopped"
        for k, t in _running_tasks.items()
    }
```

### FastAPI endpoints in `main.py`

```python
from ml_service.inference import loop as _loop
from ml_service.config import get_settings

class StartRequest(BaseModel):
    symbols: list[dict]  # [{"exchange": "bybit", "symbol": "BTCUSDT"}]

@app.post("/inference/start")
async def inference_start(req: StartRequest):
    cfg = get_settings()
    return _loop.start(cfg.questdb_http_addr, cfg.redis_url, req.symbols)

@app.post("/inference/stop")
async def inference_stop():
    return await _loop.stop()

@app.get("/inference/status")
async def inference_status():
    return _loop.status()
```

### `ml-service/tests/test_loop.py`

```python
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from ml_service.inference.loop import _symbol_loop

@pytest.mark.asyncio
async def test_no_publish_when_ts_unchanged():
    row = {"ts": "2026-01-01T00:00:00Z", "microprice_mid_delta": 2.0, "hurst_60": 0.3}
    mock_r = AsyncMock()
    mock_r.get.return_value = b"RANGING"

    call_count = 0
    async def fetch(*a, **kw):
        nonlocal call_count
        call_count += 1
        return row  # always same ts

    with patch("ml_service.inference.loop._fetch_latest_row", fetch), \
         patch("ml_service.inference.loop.aioredis.from_url", return_value=mock_r), \
         patch("ml_service.inference.loop.predict") as mock_predict:
        mock_predict.return_value = MagicMock(label="FLAT", confidence=1.0, model_id=None,
                                               regime="RANGING", cluster=-1, deviation_bps=0.0)
        task = asyncio.create_task(_symbol_loop("http://q:9000", "redis://r:6379", "bybit", "BTCUSDT", 0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass

    # predict called at most once (ts doesn't change → skip subsequent)
    assert mock_predict.call_count <= 1

@pytest.mark.asyncio
async def test_stream_key_correct():
    row1 = {"ts": "2026-01-01T00:00:00Z", "microprice_mid_delta": 2.0, "hurst_60": 0.3}
    row2 = {"ts": "2026-01-01T00:00:01Z", "microprice_mid_delta": 2.0, "hurst_60": 0.3}
    rows = iter([row1, row2, row2])

    mock_r = AsyncMock()
    mock_r.get.return_value = b"RANGING"
    xadd_calls = []
    async def mock_xadd(key, *a, **kw): xadd_calls.append(key)
    mock_r.xadd = mock_xadd

    with patch("ml_service.inference.loop._fetch_latest_row", AsyncMock(side_effect=lambda *a,**k: next(rows))), \
         patch("ml_service.inference.loop.aioredis.from_url", return_value=mock_r), \
         patch("ml_service.inference.loop.predict") as mock_predict:
        mock_predict.return_value = MagicMock(label="REVERT", confidence=0.7, model_id="x",
                                               regime="RANGING", cluster=0, deviation_bps=2.0)
        task = asyncio.create_task(_symbol_loop("http://q:9000", "redis://r:6379", "bybit", "BTCUSDT", 0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass

    assert any("ai:BTCUSDT:signals" == k for k in xadd_calls)
```

## Acceptance Criteria

1. Loop polls QuestDB every ~1s; skips publish when `ts` unchanged from last iteration (idempotent).
2. Stream key: `ai:{symbol}:signals` with `maxlen=10000, approximate=True`.
3. Stream entry fields: `ts`, `exchange`, `symbol`, `regime`, `cluster`, `signal`, `confidence`, `model_id`, `deviation_bps`, `latency_ms`.
4. `POST /inference/start {"symbols": [...]}` starts per-symbol asyncio tasks.
5. `POST /inference/stop` cancels all loops within 2 seconds.
6. `GET /inference/status` returns running/stopped state per symbol.
7. Redis errors logged, loop continues (no crash).
8. 3 consecutive errors → additional ERROR log (but loop still continues).
9. Both async tests pass.

## Dev Notes

- `asyncio.create_task` requires a running event loop — FastAPI's lifespan provides this.
- `latency_ms` measures wall time from start of iteration to XADD — includes QuestDB poll + predict.
- `clear_cache()` from engine.py is called by the SIGHUP registry reload handler — new models loaded automatically on next predict call.
