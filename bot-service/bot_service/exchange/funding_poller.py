"""Funding rate poller — publishes to funding:{exchange}:{symbol} Redis streams."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import redis.asyncio as aioredis
import structlog

log: structlog.stdlib.BoundLogger = structlog.get_logger()

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
    if str(data.get("code", "200000")) != "200000":
        raise ValueError(f"KuCoin error {data.get('code')}: {data.get('msg', '')}")
    item: dict[str, Any] = data.get("data") or {}
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
        maxlen=1000,
        approximate=True,
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
            try:
                await r.aclose()
            except Exception:
                pass
