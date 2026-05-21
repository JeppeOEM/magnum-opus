---
id: 33-1
title: Funding rate Redis stream poller
epic: 33
status: done
---

# Story 33-1: Funding rate Redis stream poller

## Context

`event_types.py` defines `FundingRate` and `_parse_funding_rate` routing `funding:{exchange}:{symbol}` Redis stream entries to the BusManager. The stream parser and event type exist but no component writes to the stream — `funding_rate_arb.py` is a stub waiting for live data. This story adds a `FundingRatePoller` that periodically fetches funding rates from Bybit and KuCoin public REST APIs and publishes them to the `funding:{exchange}:{symbol}` Redis streams.

## What to build

### `bot-service/bot_service/exchange/funding_poller.py`

```python
#!/usr/bin/env python3
"""Funding rate poller — publishes to funding:{exchange}:{symbol} Redis streams."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import redis.asyncio as aioredis
import structlog

log = structlog.get_logger()

_BYBIT_BASE = "https://api.bybit.com"
_KUCOIN_FUTURES_BASE = "https://api-futures.kucoin.com"
_HTTP_TIMEOUT = httpx.Timeout(10.0, read=20.0)


async def _fetch_bybit(client: httpx.AsyncClient, symbol: str) -> tuple[float, int]:
    """Return (funding_rate, next_funding_ts_ms) from Bybit linear tickers."""
    resp = await client.get(
        f"{_BYBIT_BASE}/v5/market/tickers",
        params={"category": "linear", "symbol": symbol},
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    items: list[dict[str, Any]] = data.get("result", {}).get("list", [])
    if not items:
        raise ValueError(f"Bybit tickers: no data for {symbol}")
    item = items[0]
    return float(item["fundingRate"]), int(item["nextFundingTime"])


async def _fetch_kucoin(client: httpx.AsyncClient, symbol: str) -> tuple[float, int]:
    """Return (funding_rate, next_funding_ts_ms) from KuCoin Futures funding-rate endpoint."""
    resp = await client.get(
        f"{_KUCOIN_FUTURES_BASE}/api/v1/funding-rate/{symbol}/current",
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    item: dict[str, Any] = data.get("data", {})
    rate = float(item["value"])
    granularity_ms = int(item["granularity"])
    time_point_ms = int(item["timePoint"])
    next_funding_ts = time_point_ms + granularity_ms
    return rate, next_funding_ts


_FETCHERS = {
    "bybit": _fetch_bybit,
    "kucoin": _fetch_kucoin,
}


async def _publish(r: aioredis.Redis, exchange: str, symbol: str, rate: float, next_ts: int) -> None:
    stream_key = f"funding:{exchange}:{symbol}"
    await r.xadd(
        stream_key,
        {
            "ts": str(int(time.time() * 1000)),
            "funding_rate": str(rate),
            "next_funding_ts": str(next_ts),
        },
    )


class FundingRatePoller:
    """Polls funding rates from Bybit and KuCoin and writes to Redis streams.

    Args:
        redis_url: Redis connection URL.
        symbols: List of ``(exchange, symbol)`` pairs to poll.
        poll_interval_s: Seconds between poll rounds.
    """

    def __init__(
        self,
        redis_url: str,
        symbols: list[tuple[str, str]],
        poll_interval_s: int = 60,
    ) -> None:
        self._redis_url = redis_url
        self._symbols = symbols
        self._poll_interval_s = poll_interval_s

    async def run(self) -> None:
        """Poll funding rates in a loop until cancelled."""
        r: aioredis.Redis = aioredis.from_url(self._redis_url)
        try:
            async with httpx.AsyncClient() as client:
                while True:
                    for exchange, symbol in self._symbols:
                        fetcher = _FETCHERS.get(exchange)
                        if fetcher is None:
                            log.warning("funding_poller_unknown_exchange", exchange=exchange)
                            continue
                        try:
                            rate, next_ts = await fetcher(client, symbol)
                            await _publish(r, exchange, symbol, rate, next_ts)
                            log.info(
                                "funding_rate_published",
                                exchange=exchange,
                                symbol=symbol,
                                rate=rate,
                            )
                        except Exception as exc:
                            log.warning(
                                "funding_poller_fetch_error",
                                exchange=exchange,
                                symbol=symbol,
                                error=str(exc),
                            )
                    await asyncio.sleep(self._poll_interval_s)
        finally:
            await r.aclose()
```

### `bot-service/bot_service/config.py` — add two settings

```python
    # Funding rate poller
    bot_funding_poll_interval_s: int = 60
    bot_funding_symbols: str = ""  # comma-separated "exchange:symbol" pairs, e.g. "bybit:BTCUSDT,kucoin:XBTUSDM"
```

### `bot-service/bot_service/main.py` — start poller in lifespan

After `_bus_manager.start()`:
```python
    from bot_service.exchange.funding_poller import FundingRatePoller
    _funding_symbols = _parse_funding_symbols(settings.bot_funding_symbols)
    _poller_task: asyncio.Task[None] | None = None
    if _funding_symbols:
        poller = FundingRatePoller(
            redis_url=settings.redis_url,
            symbols=_funding_symbols,
            poll_interval_s=settings.bot_funding_poll_interval_s,
        )
        _poller_task = asyncio.create_task(poller.run())
        log.info("funding_rate_poller_started", symbols=_funding_symbols)
```

Add `_parse_funding_symbols` helper at module level:
```python
def _parse_funding_symbols(raw: str) -> list[tuple[str, str]]:
    """Parse "bybit:BTCUSDT,kucoin:XBTUSDM" → [("bybit", "BTCUSDT"), ("kucoin", "XBTUSDM")]."""
    result = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            continue
        exchange, symbol = part.split(":", 1)
        result.append((exchange.strip(), symbol.strip()))
    return result
```

In teardown, cancel and await the poller task.

### `bot-service/tests/test_funding_poller.py` — L1 tests

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

@pytest.mark.l1
async def test_fetch_bybit_returns_rate_and_next_ts():
    ...  # mock httpx response, verify return values

@pytest.mark.l1
async def test_fetch_kucoin_derives_next_ts_from_timepoint_plus_granularity():
    ...

@pytest.mark.l1
async def test_poller_skips_unknown_exchange_without_crash():
    ...

@pytest.mark.l1
async def test_parse_funding_symbols_happy_path():
    from bot_service.main import _parse_funding_symbols
    assert _parse_funding_symbols("bybit:BTCUSDT,kucoin:XBTUSDM") == [("bybit", "BTCUSDT"), ("kucoin", "XBTUSDM")]

@pytest.mark.l1
async def test_parse_funding_symbols_empty():
    from bot_service.main import _parse_funding_symbols
    assert _parse_funding_symbols("") == []
```

## Acceptance Criteria

1. `FundingRatePoller` publishes to `funding:{exchange}:{symbol}` with fields `ts`, `funding_rate`, `next_funding_ts`.
2. Bybit fetch uses `GET /v5/market/tickers?category=linear&symbol=<S>`.
3. KuCoin fetch uses `GET /api/v1/funding-rate/{symbol}/current`; `next_funding_ts = timePoint + granularity`.
4. HTTP errors are caught per (exchange, symbol); other pairs continue polling.
5. Unknown exchange names are logged and skipped.
6. `bot_funding_poll_interval_s` and `bot_funding_symbols` settings exist.
7. When `bot_funding_symbols` is empty string, no poller task is started.
8. L1 unit tests cover: Bybit parse, KuCoin next_ts derivation, unknown exchange skip, symbol config parsing.

## Dev Notes

- Stream key: `funding:{exchange}:{symbol}` — matches `_parse_funding_rate` in `event_types.py`.
- Public endpoints used (no auth required).
- Poller is opt-in: empty `bot_funding_symbols` disables it (default).
- KuCoin `granularity` is in milliseconds (28800000 = 8 hours).

## Review Findings

- [x] [Review][Patch] Redis stream unbounded — `r.xadd()` has no `maxlen`; all other streams in project use approximate trim [`funding_poller.py:_publish`]
- [x] [Review][Patch] KuCoin business-error code not checked — `data["code"] != "200000"` missing; `data["data"]` is `None` on error, causing misleading TypeError instead of descriptive message [`funding_poller.py:_fetch_kucoin`]
- [x] [Review][Patch] Empty exchange or symbol passes through `_parse_funding_symbols` unvalidated — `":BTCUSDT"` produces `("", "BTCUSDT")` and stream key `funding::BTCUSDT` [`main.py:_parse_funding_symbols`]
- [x] [Review][Patch] Teardown `await _poller_task` only catches `CancelledError` — abnormal exit exception propagates and aborts remaining teardown [`main.py:lifespan teardown`]
- [x] [Review][Patch] Bare `await r.aclose()` in `finally` swallows original `CancelledError` if Redis close raises [`funding_poller.py:run finally`]
- [x] [Review][Patch] `test_poller_skips_unknown_exchange` is dead code — `fake_run` never applied, `poller.run()` never called, only static dict assertions [`tests/test_funding_poller.py`]
- [x] [Review][Patch] `test_poller_no_task_started_when_symbols_empty` tests parser only, not AC7 lifespan guard [`tests/test_funding_poller.py`]
- [x] [Review][Patch] No test asserting `_publish` writes fields `ts`, `funding_rate`, `next_funding_ts` to correct stream key (AC1 gap) [`tests/test_funding_poller.py`]
- [x] [Review][Defer] `poll_interval_s=0` causes hot spin-loop — add `Field(ge=1)` to config in follow-up [`config.py`]
- [x] [Review][Defer] KuCoin `next_funding_ts` may be in the past if response is stale — staleness check out of scope for 33-1 [`funding_poller.py:_fetch_kucoin`]
- [x] [Review][Defer] Malformed symbol entry (no colon) silently dropped with no log warning — minor UX gap [`main.py:_parse_funding_symbols`]

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### File List

- `bot-service/bot_service/exchange/funding_poller.py`
- `bot-service/bot_service/config.py`
- `bot-service/bot_service/main.py`
- `bot-service/tests/test_funding_poller.py`
