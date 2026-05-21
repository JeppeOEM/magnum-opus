import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from ml_service.inference.spread_arb import _KalmanState, _neutral, run_spread_loop


def test_kalman_converges_to_true_beta():
    """Kalman filter should converge near β=2 for synthetic cointegrated pair."""
    rng = np.random.default_rng(0)
    eth = np.log(np.cumsum(rng.standard_normal(300) * 0.01) + 100)
    btc = 2.0 * eth + rng.standard_normal(300) * 0.001
    ks = _KalmanState()
    for b, e in zip(btc, eth):
        beta = ks.update(b, e)
    assert 1.5 < beta < 2.5, f"Kalman β converged to {beta:.3f}, expected ~2.0"


def test_neutral_signal_structure():
    """_neutral returns NEUTRAL with confidence=1.0."""
    result = _neutral("neutral_zone")
    assert result["signal"] == "NEUTRAL"
    assert result["confidence"] == 1.0
    assert result["model_id"] is None


def test_kalman_state_stateful():
    """_KalmanState should maintain β across multiple update calls."""
    ks = _KalmanState()
    beta0 = ks.beta
    ks.update(5.0, 4.0)
    assert ks.beta != beta0  # state changed


def test_kalman_zero_eth_no_crash():
    """When log_eth=0 (S=0), K should be 0 and β unchanged."""
    ks = _KalmanState()
    beta_before = ks.beta
    ks.update(5.0, 0.0)  # H=0 → S=0 → K=0
    assert ks.beta == beta_before


@pytest.mark.asyncio
async def test_spread_loop_publishes_to_correct_stream():
    """run_spread_loop should publish to ai:BTC-ETH-spread:signals."""
    import time

    row_btc = {
        "ts": "2026-01-01T00:00:00Z",
        "best_bid": 50000.0, "best_ask": 50001.0,
        "hurst_60": 0.4, "adf_pvalue_60": 0.05,
    }
    row_eth = {
        "ts": "2026-01-01T00:00:00Z",
        "best_bid": 2500.0, "best_ask": 2501.0,
        "hurst_60": 0.4, "adf_pvalue_60": 0.05,
    }

    call_num = [0]

    async def fetch(url, exchange, symbol):
        call_num[0] += 1
        # Each call gets a unique ts so the "ts unchanged" skip never fires.
        # Both BTC and ETH get distinct counters — the only-same-ts skip needs
        # BOTH to be equal to last_ts, which only happens when neither advances.
        row = dict(row_btc if symbol == "BTCUSDT" else row_eth)
        row["ts"] = str(call_num[0])  # unique monotonically increasing
        return row, time.time()

    mock_r = AsyncMock()
    xadd_keys = []

    async def mock_xadd(key, payload, **kw):
        xadd_keys.append(key)

    mock_r.xadd = mock_xadd

    with patch("ml_service.inference.spread_arb._fetch_latest", side_effect=fetch), \
         patch("ml_service.inference.spread_arb.aioredis.from_url", return_value=mock_r):
        task = asyncio.create_task(
            run_spread_loop(
                "http://q:9000", "redis://r:6379", interval_s=0.01
            )
        )
        await asyncio.sleep(0.8)  # enough for 60-bar window to fill
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # After 60 bars, at least one publish should have happened
    assert any(k == "ai:BTC-ETH-spread:signals" for k in xadd_keys)


@pytest.mark.asyncio
async def test_spread_loop_skips_when_stale():
    """When data age > STALE_SECONDS, loop should skip and not publish."""
    import time as _time

    mock_r = AsyncMock()
    xadd_keys = []

    async def mock_xadd(key, *a, **kw):
        xadd_keys.append(key)

    mock_r.xadd = mock_xadd

    old_time = _time.time() - 10  # 10 seconds old → stale

    async def fetch_stale(url, exchange, symbol):
        row = {
            "ts": "2026-01-01T00:00:00Z",
            "best_bid": 50000.0, "best_ask": 50001.0,
            "hurst_60": 0.4, "adf_pvalue_60": 0.05,
        }
        return row, old_time  # very old fetch time

    with patch("ml_service.inference.spread_arb._fetch_latest", side_effect=fetch_stale), \
         patch("ml_service.inference.spread_arb.aioredis.from_url", return_value=mock_r):
        task = asyncio.create_task(
            run_spread_loop(
                "http://q:9000", "redis://r:6379", interval_s=0.01
            )
        )
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Stale data → no publishes
    assert len(xadd_keys) == 0
