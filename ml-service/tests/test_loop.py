import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ml_service.inference.loop import _symbol_loop


@pytest.mark.asyncio
async def test_no_publish_when_ts_unchanged():
    """When ts doesn't change, predict should be called at most once."""
    row = {"ts": "2026-01-01T00:00:00Z", "microprice_mid_delta": 2.0, "hurst_60": 0.3}

    mock_r = AsyncMock()
    mock_r.get.return_value = b"RANGING"

    fetch_count = [0]

    async def fetch(*a, **kw):
        fetch_count[0] += 1
        return row  # always same ts

    with patch("ml_service.inference.loop._fetch_latest_row", fetch), \
         patch("ml_service.inference.loop.aioredis.from_url", return_value=mock_r), \
         patch("ml_service.inference.loop.predict") as mock_predict:
        mock_predict.return_value = MagicMock(
            label="FLAT", confidence=1.0, model_id=None,
            regime="RANGING", cluster=-1, deviation_bps=0.0,
        )
        task = asyncio.create_task(
            _symbol_loop("http://q:9000", "redis://r:6379", "bybit", "BTCUSDT", 0.01)
        )
        await asyncio.sleep(0.08)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # predict should be called at most once since ts never changes
    assert mock_predict.call_count <= 1


@pytest.mark.asyncio
async def test_stream_key_correct():
    """Loop publishes to ai:{symbol}:signals when ts advances."""
    row1 = {"ts": "2026-01-01T00:00:00Z", "microprice_mid_delta": 2.0, "hurst_60": 0.3}
    row2 = {"ts": "2026-01-01T00:00:01Z", "microprice_mid_delta": 2.0, "hurst_60": 0.3}
    fetch_iter = iter([row1, row2, row2, row2])

    mock_r = AsyncMock()
    mock_r.get.return_value = b"RANGING"
    xadd_keys = []

    async def mock_xadd(key, *a, **kw):
        xadd_keys.append(key)

    mock_r.xadd = mock_xadd

    async def fetch(*a, **kw):
        return next(fetch_iter, row2)

    with patch("ml_service.inference.loop._fetch_latest_row", fetch), \
         patch("ml_service.inference.loop.aioredis.from_url", return_value=mock_r), \
         patch("ml_service.inference.loop.predict") as mock_predict:
        mock_predict.return_value = MagicMock(
            label="REVERT", confidence=0.7, model_id="x",
            regime="RANGING", cluster=0, deviation_bps=2.0,
        )
        task = asyncio.create_task(
            _symbol_loop("http://q:9000", "redis://r:6379", "bybit", "BTCUSDT", 0.01)
        )
        await asyncio.sleep(0.08)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # At least one xadd should have been called with the right key
    assert any(k == "ai:BTCUSDT:signals" for k in xadd_keys), (
        f"Expected stream key 'ai:BTCUSDT:signals' in {xadd_keys}"
    )


@pytest.mark.asyncio
async def test_loop_continues_after_error():
    """A fetch error should be logged but the loop should continue (not crash)."""
    error_count = [0]
    success_rows = []

    row_ok = {"ts": "2026-01-01T00:00:01Z", "microprice_mid_delta": 2.0, "hurst_60": 0.3}

    async def fetch_with_error(*a, **kw):
        error_count[0] += 1
        if error_count[0] == 1:
            raise RuntimeError("QuestDB down")
        return row_ok

    mock_r = AsyncMock()
    mock_r.get.return_value = b"RANGING"
    xadd_keys = []

    async def mock_xadd(key, *a, **kw):
        xadd_keys.append(key)

    mock_r.xadd = mock_xadd

    with patch("ml_service.inference.loop._fetch_latest_row", fetch_with_error), \
         patch("ml_service.inference.loop.aioredis.from_url", return_value=mock_r), \
         patch("ml_service.inference.loop.predict") as mock_predict:
        mock_predict.return_value = MagicMock(
            label="FLAT", confidence=1.0, model_id=None,
            regime="RANGING", cluster=-1, deviation_bps=2.0,
        )
        task = asyncio.create_task(
            _symbol_loop("http://q:9000", "redis://r:6379", "bybit", "BTCUSDT", 0.01)
        )
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Loop recovered and successfully published after the error
    assert any(k == "ai:BTCUSDT:signals" for k in xadd_keys)


@pytest.mark.asyncio
async def test_stream_fields_complete():
    """All required fields must be present in the xadd call."""
    row = {"ts": "2026-01-01T00:00:00Z", "microprice_mid_delta": 3.0, "hurst_60": 0.3}
    row2 = {"ts": "2026-01-01T00:00:01Z", "microprice_mid_delta": 3.0, "hurst_60": 0.3}
    fetch_iter = iter([row, row2, row2])

    mock_r = AsyncMock()
    mock_r.get.return_value = b"HIGH_VOL"
    xadd_payloads = []

    async def mock_xadd(key, payload, **kw):
        xadd_payloads.append(payload)

    mock_r.xadd = mock_xadd

    async def fetch(*a, **kw):
        return next(fetch_iter, row2)

    with patch("ml_service.inference.loop._fetch_latest_row", fetch), \
         patch("ml_service.inference.loop.aioredis.from_url", return_value=mock_r), \
         patch("ml_service.inference.loop.predict") as mock_predict:
        mock_predict.return_value = MagicMock(
            label="FLAT", confidence=1.0, model_id="abc",
            regime="HIGH_VOL", cluster=0, deviation_bps=3.0,
        )
        task = asyncio.create_task(
            _symbol_loop("http://q:9000", "redis://r:6379", "bybit", "BTCUSDT", 0.01)
        )
        await asyncio.sleep(0.08)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert len(xadd_payloads) >= 1
    required_fields = {
        "ts", "exchange", "symbol", "regime", "cluster",
        "signal", "confidence", "model_id", "deviation_bps", "latency_ms",
    }
    for field in required_fields:
        assert field in xadd_payloads[0], f"Missing field: {field}"
