---
id: 38-3
title: Stat arb BTC/ETH spread inference
epic: 38
status: ready-for-dev
---

# Story 38-3: Stat Arb BTC/ETH Spread Inference

## Context

The spread inference loop maintains an in-memory rolling window of BTC and ETH mid-prices, computes a live Kalman hedge ratio and z-score, then predicts CONVERGE/DIVERGE/NEUTRAL. It runs alongside per-symbol loops but publishes to a dedicated `ai:BTC-ETH-spread:signals` stream. Staleness guard: if either symbol's latest row is > 5 seconds old, skip publish.

## What to build

### `ml-service/ml_service/inference/spread_arb.py`

```python
from __future__ import annotations
import asyncio
import time
from collections import deque

import httpx
import numpy as np
import redis.asyncio as aioredis
import structlog

from ml_service.inference.engine import _load_artifacts
from ml_service.registry.registry import get_all

log = structlog.get_logger()

WINDOW = 60          # rolling z-score window
ZSCORE_THRESHOLD = 1.5
STALE_SECONDS = 5.0
STREAM_KEY = "ai:BTC-ETH-spread:signals"
STREAM_MAXLEN = 10_000


class _KalmanState:
    """1D Kalman filter for dynamic hedge ratio."""
    def __init__(self, Q: float = 1e-5, R: float = 1e-3):
        self.beta = 1.0
        self.P = 1.0
        self.Q = Q
        self.R = R

    def update(self, log_btc: float, log_eth: float) -> float:
        self.P += self.Q
        H = log_eth
        S = H * self.P * H + self.R
        K = self.P * H / S if S != 0 else 0.0
        self.beta += K * (log_btc - self.beta * H)
        self.P = (1 - K * H) * self.P
        return self.beta


async def _fetch_latest(questdb_url: str, exchange: str, symbol: str) -> tuple[dict | None, float]:
    """Returns (row, fetch_time_unix)."""
    sql = (
        f"SELECT ts, best_bid, best_ask, hurst_60, adf_pvalue_60 "
        f"FROM snapshot_1s "
        f"WHERE exchange='{exchange}' AND symbol='{symbol}' "
        f"ORDER BY ts DESC LIMIT 1"
    )
    t = time.time()
    async with httpx.AsyncClient() as client:
        resp = await client.get(questdb_url + "/exec", params={"query": sql}, timeout=5)
        data = resp.json()
        if not data.get("dataset"):
            return None, t
        cols = [c["name"] for c in data["columns"]]
        return dict(zip(cols, data["dataset"][0])), t


def _mid(row: dict) -> float:
    return (float(row["best_bid"] or 0) + float(row["best_ask"] or 0)) / 2


def _neutral(reason: str = "") -> dict:
    return {"signal": "NEUTRAL", "confidence": 1.0, "model_id": None,
            "zscore": 0.0, "spread": 0.0, "reason": reason}


async def run_spread_loop(
    questdb_url: str,
    redis_url: str,
    btc_exchange: str = "bybit",
    eth_exchange: str = "bybit",
    btc_symbol: str = "BTCUSDT",
    eth_symbol: str = "ETHUSDT",
    interval_s: float = 1.0,
) -> None:
    r = aioredis.from_url(redis_url)
    kalman = _KalmanState()
    spreads: deque[float] = deque(maxlen=WINDOW)
    last_ts_btc: str | None = None
    last_ts_eth: str | None = None

    try:
        while True:
            t0 = time.monotonic()

            row_btc, t_btc = await _fetch_latest(questdb_url, btc_exchange, btc_symbol)
            row_eth, t_eth = await _fetch_latest(questdb_url, eth_exchange, eth_symbol)

            # Staleness guard
            now = time.time()
            btc_age = now - t_btc
            eth_age = now - t_eth
            if row_btc is None or row_eth is None or btc_age > STALE_SECONDS or eth_age > STALE_SECONDS:
                log.warning("spread_inference_stale_data",
                            btc_age=round(btc_age, 1), eth_age=round(eth_age, 1))
                await asyncio.sleep(max(0, interval_s - (time.monotonic() - t0)))
                continue

            # Skip if no new data
            if row_btc["ts"] == last_ts_btc and row_eth["ts"] == last_ts_eth:
                await asyncio.sleep(max(0, interval_s - (time.monotonic() - t0)))
                continue

            last_ts_btc, last_ts_eth = row_btc["ts"], row_eth["ts"]

            mid_btc = _mid(row_btc)
            mid_eth = _mid(row_eth)
            if mid_btc <= 0 or mid_eth <= 0:
                await asyncio.sleep(max(0, interval_s - (time.monotonic() - t0)))
                continue

            log_btc = np.log(mid_btc)
            log_eth = np.log(mid_eth)
            hedge = kalman.update(log_btc, log_eth)
            spread = log_btc - hedge * log_eth
            spreads.append(spread)

            # Need full window for z-score
            if len(spreads) < WINDOW:
                await asyncio.sleep(max(0, interval_s - (time.monotonic() - t0)))
                continue

            arr = np.array(spreads)
            mean, std = arr.mean(), arr.std()
            zscore = (spread - mean) / std if std > 1e-10 else 0.0

            # ADF gate
            adf_pvalue = float(row_btc.get("adf_pvalue_60") or 1.0)

            # Neutral zone or invalid cointegration
            if abs(zscore) <= ZSCORE_THRESHOLD or adf_pvalue > 0.1:
                result = {**_neutral("neutral_zone" if abs(zscore) <= ZSCORE_THRESHOLD else "adf_fail"),
                          "zscore": round(zscore, 4), "spread": round(spread, 6)}
            else:
                # Model prediction
                result = _predict_spread(row_btc, zscore, spread, hedge)

            await r.xadd(STREAM_KEY, {
                "ts":           str(row_btc["ts"]),
                "btc_ts":       str(row_btc["ts"]),
                "eth_ts":       str(row_eth["ts"]),
                "spread":       f"{spread:.6f}",
                "zscore":       f"{zscore:.4f}",
                "hedge_ratio":  f"{hedge:.4f}",
                "adf_pvalue":   f"{adf_pvalue:.4f}",
                "signal":       result["signal"],
                "confidence":   f"{result['confidence']:.4f}",
                "model_id":     result.get("model_id") or "",
            }, maxlen=STREAM_MAXLEN, approximate=True)

            await asyncio.sleep(max(0, interval_s - (time.monotonic() - t0)))

    finally:
        await r.aclose()


def _predict_spread(row: dict, zscore: float, spread: float, hedge: float) -> dict:
    """Find spread model in registry and predict CONVERGE/DIVERGE/NEUTRAL."""
    candidates = [
        e for e in get_all()
        if e["symbol"] == "BTC-ETH-spread"
    ]
    if not candidates:
        return {**_neutral("no_spread_model"), "zscore": zscore, "spread": spread}

    best = max(candidates, key=lambda e: e["cv_score"])
    artifacts = _load_artifacts(best["artifact_dir"])
    if artifacts is None:
        return {**_neutral("artifact_load_fail"), "zscore": zscore, "spread": spread}

    scaler, model = artifacts
    feat_cols = best["feature_names"]
    x = np.array([float(row.get(c) or 0.0) for c in feat_cols]).reshape(1, -1)
    x_scaled = scaler.transform(x)

    SPREAD_LABELS = {0: "CONVERGE", 1: "NEUTRAL", 2: "DIVERGE"}
    try:
        proba = model.predict_proba(x_scaled)[0]
        idx = int(np.argmax(proba))
        return {"signal": SPREAD_LABELS.get(idx, "NEUTRAL"),
                "confidence": float(proba[idx]), "model_id": best["id"],
                "zscore": zscore, "spread": spread}
    except Exception as e:
        log.warning("spread_predict_failed", error=str(e))
        return {**_neutral("predict_error"), "zscore": zscore, "spread": spread}
```

### `ml-service/tests/test_spread_arb.py`

```python
import numpy as np
from ml_service.inference.spread_arb import _KalmanState, _neutral

def test_kalman_converges_to_true_beta():
    rng = np.random.default_rng(0)
    eth = np.log(np.cumsum(rng.standard_normal(300) * 0.01) + 100)
    btc = 2.0 * eth + rng.standard_normal(300) * 0.001
    ks = _KalmanState()
    for b, e in zip(btc, eth):
        beta = ks.update(b, e)
    assert 1.5 < beta < 2.5

def test_zscore_neutral_when_small():
    # If |zscore| < threshold → NEUTRAL
    result = _neutral("neutral_zone")
    assert result["signal"] == "NEUTRAL"
    assert result["confidence"] == 1.0
```

## Acceptance Criteria

1. Spread inference maintains an in-memory `_KalmanState` and 60-bar deque — no QuestDB history needed.
2. Kalman hedge ratio converges near true β for synthetic cointegrated pair (1.5 < β < 2.5 when true β=2).
3. When `|zscore| <= 1.5` or `adf_pvalue > 0.1`, publishes NEUTRAL with `confidence=1.0`.
4. When BTC or ETH row is stale (> 5s old), logs `spread_inference_stale_data` and skips.
5. Stream `ai:BTC-ETH-spread:signals` fields: `ts`, `spread`, `zscore`, `hedge_ratio`, `adf_pvalue`, `signal`, `confidence`, `model_id`.
6. Both unit tests pass.

## Dev Notes

- `_KalmanState` is stateful across loop iterations — do not recreate per-iteration.
- The `adf_pvalue_60` field comes from the feature store (written by Story 37-3) — if column is null in QuestDB, defaults to 1.0 (NEUTRAL gate fires).
- `run_spread_loop` can be started from `/inference/start` when `spread_pair` is specified in the request.
