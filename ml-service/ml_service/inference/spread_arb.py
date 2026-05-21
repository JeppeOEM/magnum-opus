"""Stat arb BTC/ETH spread inference loop.

Maintains in-memory Kalman hedge ratio and 60-bar rolling z-score window.
Publishes CONVERGE/DIVERGE/NEUTRAL signals to `ai:BTC-ETH-spread:signals`.
Staleness guard: if either symbol's data is > 5s old, skip the bar.
"""
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

WINDOW = 60           # rolling z-score window (bars)
ZSCORE_THRESHOLD = 1.5
STALE_SECONDS = 5.0
STREAM_KEY = "ai:BTC-ETH-spread:signals"
STREAM_MAXLEN = 10_000


class _KalmanState:
    """1D Kalman filter maintaining live hedge ratio β_t.

    State: β_t in log_btc = β_t × log_eth + ε
    Process noise Q controls β drift speed.
    """

    def __init__(self, Q: float = 1e-5, R: float = 1e-3) -> None:
        self.beta = 1.0
        self.P = 1.0
        self.Q = Q
        self.R = R

    def update(self, log_btc: float, log_eth: float) -> float:
        """Update filter with new observation. Returns current β estimate."""
        self.P += self.Q
        H = log_eth
        S = H * self.P * H + self.R
        K = self.P * H / S if S != 0 else 0.0
        self.beta += K * (log_btc - self.beta * H)
        self.P = (1 - K * H) * self.P
        return self.beta


async def _fetch_latest(
    questdb_url: str, exchange: str, symbol: str
) -> tuple[dict | None, float]:
    """Fetch the most-recent snapshot_1s row. Returns (row | None, fetch_wall_time)."""
    sql = (
        f"SELECT ts, best_bid, best_ask, hurst_60, adf_pvalue_60 "
        f"FROM snapshot_1s "
        f"WHERE exchange='{exchange}' AND symbol='{symbol}' "
        f"ORDER BY ts DESC LIMIT 1"
    )
    t = time.time()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                questdb_url + "/exec", params={"query": sql}, timeout=5
            )
            data = resp.json()
            if not data.get("dataset"):
                return None, t
            cols = [c["name"] for c in data["columns"]]
            return dict(zip(cols, data["dataset"][0])), t
    except Exception as e:
        log.warning("spread_fetch_failed", exchange=exchange, symbol=symbol, error=str(e))
        return None, t


def _mid(row: dict) -> float:
    return (float(row.get("best_bid") or 0) + float(row.get("best_ask") or 0)) / 2


def _neutral(reason: str = "") -> dict:
    return {
        "signal": "NEUTRAL",
        "confidence": 1.0,
        "model_id": None,
        "zscore": 0.0,
        "spread": 0.0,
        "reason": reason,
    }


def _predict_spread(row: dict, zscore: float, spread: float, hedge: float) -> dict:
    """Find spread model in registry and predict CONVERGE/DIVERGE/NEUTRAL."""
    candidates = [e for e in get_all() if e.get("symbol") == "BTC-ETH-spread"]
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
        return {
            "signal": SPREAD_LABELS.get(idx, "NEUTRAL"),
            "confidence": float(proba[idx]),
            "model_id": best["id"],
            "zscore": zscore,
            "spread": spread,
        }
    except Exception as e:
        log.warning("spread_predict_failed", error=str(e))
        return {**_neutral("predict_error"), "zscore": zscore, "spread": spread}


async def run_spread_loop(
    questdb_url: str,
    redis_url: str,
    btc_exchange: str = "bybit",
    eth_exchange: str = "bybit",
    btc_symbol: str = "BTCUSDT",
    eth_symbol: str = "ETHUSDT",
    interval_s: float = 1.0,
) -> None:
    """Main spread inference loop. Runs until cancelled."""
    r = aioredis.from_url(redis_url)
    kalman = _KalmanState()
    spreads: deque[float] = deque(maxlen=WINDOW)
    last_ts_btc: str | None = None
    last_ts_eth: str | None = None

    try:
        while True:
            t0 = time.monotonic()
            try:
                row_btc, t_btc = await _fetch_latest(questdb_url, btc_exchange, btc_symbol)
                row_eth, t_eth = await _fetch_latest(questdb_url, eth_exchange, eth_symbol)

                now = time.time()
                btc_age = now - t_btc
                eth_age = now - t_eth

                # Staleness guard
                if (
                    row_btc is None
                    or row_eth is None
                    or btc_age > STALE_SECONDS
                    or eth_age > STALE_SECONDS
                ):
                    log.warning(
                        "spread_inference_stale_data",
                        btc_age=round(btc_age, 1),
                        eth_age=round(eth_age, 1),
                    )
                    await asyncio.sleep(max(0.0, interval_s - (time.monotonic() - t0)))
                    continue

                # Skip if no new data
                if str(row_btc["ts"]) == last_ts_btc and str(row_eth["ts"]) == last_ts_eth:
                    await asyncio.sleep(max(0.0, interval_s - (time.monotonic() - t0)))
                    continue

                last_ts_btc = str(row_btc["ts"])
                last_ts_eth = str(row_eth["ts"])

                mid_btc = _mid(row_btc)
                mid_eth = _mid(row_eth)
                if mid_btc <= 0 or mid_eth <= 0:
                    await asyncio.sleep(max(0.0, interval_s - (time.monotonic() - t0)))
                    continue

                log_btc = np.log(mid_btc)
                log_eth = np.log(mid_eth)
                hedge = kalman.update(log_btc, log_eth)
                spread = float(log_btc - hedge * log_eth)
                spreads.append(spread)

                # Need full window for valid z-score
                if len(spreads) < WINDOW:
                    await asyncio.sleep(max(0.0, interval_s - (time.monotonic() - t0)))
                    continue

                arr = np.array(spreads)
                mean, std = float(arr.mean()), float(arr.std())
                zscore = (spread - mean) / std if std > 1e-10 else 0.0

                adf_pvalue = float(row_btc.get("adf_pvalue_60") or 1.0)

                if abs(zscore) <= ZSCORE_THRESHOLD or adf_pvalue > 0.1:
                    reason = "neutral_zone" if abs(zscore) <= ZSCORE_THRESHOLD else "adf_fail"
                    result = {
                        **_neutral(reason),
                        "zscore": round(zscore, 4),
                        "spread": round(spread, 6),
                    }
                else:
                    result = _predict_spread(row_btc, zscore, spread, hedge)

                await r.xadd(
                    STREAM_KEY,
                    {
                        "ts": str(row_btc["ts"]),
                        "btc_ts": str(row_btc["ts"]),
                        "eth_ts": str(row_eth["ts"]),
                        "spread": f"{spread:.6f}",
                        "zscore": f"{zscore:.4f}",
                        "hedge_ratio": f"{hedge:.4f}",
                        "adf_pvalue": f"{adf_pvalue:.4f}",
                        "signal": result["signal"],
                        "confidence": f"{result['confidence']:.4f}",
                        "model_id": result.get("model_id") or "",
                    },
                    maxlen=STREAM_MAXLEN,
                    approximate=True,
                )

                log.debug(
                    "spread_signal_published",
                    signal=result["signal"],
                    zscore=round(zscore, 4),
                    hedge=round(hedge, 4),
                )

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("spread_loop_error", error=str(e))

            await asyncio.sleep(max(0.0, interval_s - (time.monotonic() - t0)))

    finally:
        await r.aclose()
