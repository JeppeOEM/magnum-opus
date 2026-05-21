"""L1 unit tests for FundingRatePoller and helpers."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bot_service.exchange.funding_poller import (
    FundingRatePoller,
    _fetch_bybit,
    _fetch_kucoin,
)
from bot_service.main import _parse_funding_symbols


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(json_data: dict) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# _parse_funding_symbols
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_parse_funding_symbols_happy_path() -> None:
    result = _parse_funding_symbols("bybit:BTCUSDT,kucoin:XBTUSDM")
    assert result == [("bybit", "BTCUSDT"), ("kucoin", "XBTUSDM")]


@pytest.mark.l1
def test_parse_funding_symbols_empty_string() -> None:
    assert _parse_funding_symbols("") == []


@pytest.mark.l1
def test_parse_funding_symbols_whitespace_and_blanks() -> None:
    result = _parse_funding_symbols("  bybit:BTCUSDT ,  , kucoin:XBTUSDM  ")
    assert result == [("bybit", "BTCUSDT"), ("kucoin", "XBTUSDM")]


@pytest.mark.l1
def test_parse_funding_symbols_no_colon_skipped() -> None:
    result = _parse_funding_symbols("bybit:BTCUSDT,badentry,kucoin:XBTUSDM")
    assert result == [("bybit", "BTCUSDT"), ("kucoin", "XBTUSDM")]


# ---------------------------------------------------------------------------
# _fetch_bybit
# ---------------------------------------------------------------------------


@pytest.mark.l1
@pytest.mark.asyncio
async def test_fetch_bybit_returns_rate_and_next_ts() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _mock_response({
        "result": {
            "list": [
                {"fundingRate": "0.0001", "nextFundingTime": "1748000000000"}
            ]
        }
    })
    rate, next_ts = await _fetch_bybit(client, "BTCUSDT")
    assert rate == pytest.approx(0.0001)
    assert next_ts == 1748000000000


@pytest.mark.l1
@pytest.mark.asyncio
async def test_fetch_bybit_empty_list_raises() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _mock_response({"result": {"list": []}})
    with pytest.raises(ValueError, match="no data for BTCUSDT"):
        await _fetch_bybit(client, "BTCUSDT")


# ---------------------------------------------------------------------------
# _fetch_kucoin
# ---------------------------------------------------------------------------


@pytest.mark.l1
@pytest.mark.asyncio
async def test_fetch_kucoin_derives_next_ts() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _mock_response({
        "data": {
            "value": 0.0002,
            "granularity": 28800000,
            "timePoint": 1748000000000,
        }
    })
    rate, next_ts = await _fetch_kucoin(client, "XBTUSDM")
    assert rate == pytest.approx(0.0002)
    assert next_ts == 1748000000000 + 28800000


@pytest.mark.l1
@pytest.mark.asyncio
async def test_fetch_kucoin_negative_rate() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _mock_response({
        "data": {
            "value": -0.0003,
            "granularity": 28800000,
            "timePoint": 1748000000000,
        }
    })
    rate, _ = await _fetch_kucoin(client, "XBTUSDM")
    assert rate == pytest.approx(-0.0003)


# ---------------------------------------------------------------------------
# FundingRatePoller.run() — unknown exchange skipped, errors caught
# ---------------------------------------------------------------------------


@pytest.mark.l1
@pytest.mark.asyncio
async def test_poller_skips_unknown_exchange_and_continues() -> None:
    """Poller skips unknown exchange, calls known fetcher, then stops after one round."""
    from bot_service.exchange.funding_poller import _FETCHERS

    bybit_called: list[str] = []
    published: list[str] = []

    async def fake_fetch_bybit(client: object, symbol: str) -> tuple[float, int]:
        bybit_called.append(symbol)
        return 0.0001, 1748000000000

    async def fake_publish(r: object, exchange: str, symbol: str, rate: float, next_ts: int) -> None:
        published.append(f"{exchange}:{symbol}")

    with (
        patch.dict(_FETCHERS, {"bybit": fake_fetch_bybit}, clear=False),
        patch("bot_service.exchange.funding_poller._publish", side_effect=fake_publish),
        patch("bot_service.exchange.funding_poller.aioredis.from_url", return_value=AsyncMock()),
        patch("asyncio.sleep", side_effect=asyncio.CancelledError),
    ):
        poller = FundingRatePoller(
            redis_url="redis://localhost:6379",
            symbols=[("unknown_exchange", "BTCUSDT"), ("bybit", "ETHUSDT")],
            poll_interval_s=999,
        )
        with pytest.raises(asyncio.CancelledError):
            await poller.run()

    assert bybit_called == ["ETHUSDT"]
    assert published == ["bybit:ETHUSDT"]
    # unknown_exchange was skipped — no fetch for it
    assert len(bybit_called) == 1


@pytest.mark.l1
def test_parse_funding_symbols_empty_exchange_or_symbol_filtered() -> None:
    """Entries with empty exchange or symbol after splitting are dropped."""
    assert _parse_funding_symbols(":BTCUSDT") == []
    assert _parse_funding_symbols("bybit:") == []
    assert _parse_funding_symbols(":") == []


@pytest.mark.l1
@pytest.mark.asyncio
async def test_publish_writes_correct_stream_key_and_fields() -> None:
    """_publish sends ts, funding_rate, next_funding_ts to funding:{exchange}:{symbol}."""
    from bot_service.exchange.funding_poller import _publish

    mock_redis = AsyncMock()
    await _publish(mock_redis, "bybit", "BTCUSDT", 0.0001, 1748000000000)

    mock_redis.xadd.assert_called_once()
    call_args = mock_redis.xadd.call_args
    assert call_args.args[0] == "funding:bybit:BTCUSDT"
    fields: dict = call_args.args[1]
    assert "ts" in fields
    assert fields["funding_rate"] == "0.0001"
    assert fields["next_funding_ts"] == "1748000000000"
