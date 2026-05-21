"""Inference engine — pure prediction logic with no I/O.

`predict()` takes a feature row dict, looks up the best model from the registry,
and returns a Signal. All I/O (QuestDB fetch, Redis read) lives in the signal loop
(Story 38-2), keeping this module unit-testable without mocking external services.

Regime gate: TRENDING_UP/DOWN always returns FLAT — mean reversion doesn't apply.
Hurst gate: hurst_60 >= 0.5 returns FLAT — window is not mean-reverting.
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import structlog

from ml_service.registry.registry import get_all

log = structlog.get_logger()

LABEL_REVERSE = {0: "REVERT", 1: "FLAT", 2: "TREND"}

# Module-level artifact cache — avoids re-loading pickle on every 1s bar
_MODEL_CACHE: dict[str, tuple[object, object]] = {}  # artifact_dir → (scaler, model)


@dataclass
class Signal:
    label: str           # REVERT / FLAT / TREND
    confidence: float    # max probability from model output
    model_id: str | None  # registry entry UUID, or None for gated/no-model FLAT
    regime: str
    cluster: int
    deviation_bps: float  # microprice_mid_delta of input row (bps)


def _load_artifacts(artifact_dir: str) -> tuple[object, object] | None:
    """Load (scaler, model) from disk, caching by artifact_dir path."""
    if artifact_dir in _MODEL_CACHE:
        return _MODEL_CACHE[artifact_dir]
    try:
        with open(Path(artifact_dir) / "scaler.pkl", "rb") as f:
            scaler = pickle.load(f)
        with open(Path(artifact_dir) / "model.pkl", "rb") as f:
            model = pickle.load(f)
        _MODEL_CACHE[artifact_dir] = (scaler, model)
        return scaler, model
    except Exception as e:
        log.warning("artifact_load_failed", dir=artifact_dir, error=str(e))
        return None


def clear_cache() -> None:
    """Evict all cached artifacts. Call after SIGHUP registry reload."""
    _MODEL_CACHE.clear()


def _flat_signal(regime: str, deviation_bps: float) -> Signal:
    return Signal(
        label="FLAT",
        confidence=1.0,
        model_id=None,
        regime=regime,
        cluster=-1,
        deviation_bps=deviation_bps,
    )


def predict(
    row: dict[str, Any],
    exchange: str,
    symbol: str,
    regime: str,
) -> Signal:
    """Predict signal for a single snapshot_1s feature row.

    Returns FLAT when:
    - regime is TRENDING_UP or TRENDING_DOWN
    - hurst_60 >= 0.5 (trending window)
    - no model found in registry for (exchange, symbol, regime)
    - artifact load fails
    - model.predict_proba raises
    """
    deviation_bps = float(row.get("microprice_mid_delta") or 0.0)

    # Regime gate: trending windows should never be faded
    if regime in ("TRENDING_UP", "TRENDING_DOWN"):
        return _flat_signal(regime, deviation_bps)

    # Hurst gate: non-mean-reverting window
    hurst = float(row.get("hurst_60") or 0.5)
    if hurst >= 0.5:
        return _flat_signal(regime, deviation_bps)

    # Find all registry candidates for this (exchange, symbol, regime)
    candidates = [
        e for e in get_all()
        if e["exchange"] == exchange
        and e["symbol"] == symbol
        and e["regime"] == regime
        and e["cluster"] != -1
    ]
    if not candidates:
        return _flat_signal(regime, deviation_bps)

    # Select model with highest CV score
    best = max(candidates, key=lambda e: e["cv_score"])
    artifacts = _load_artifacts(best["artifact_dir"])
    if artifacts is None:
        return _flat_signal(regime, deviation_bps)

    scaler, model = artifacts
    feat_cols: list[str] = best["feature_names"]

    # Build feature vector — missing features default to 0.0 (matches training imputation)
    x = np.array(
        [float(row.get(c) or 0.0) for c in feat_cols], dtype=float
    ).reshape(1, -1)
    x_scaled = scaler.transform(x)

    try:
        proba = model.predict_proba(x_scaled)[0]  # shape (3,)
        label_idx = int(np.argmax(proba))
        confidence = float(proba[label_idx])
        label = LABEL_REVERSE.get(label_idx, "FLAT")
    except Exception as e:
        log.warning("model_predict_failed", error=str(e), exchange=exchange, symbol=symbol)
        return _flat_signal(regime, deviation_bps)

    return Signal(
        label=label,
        confidence=confidence,
        model_id=best["id"],
        regime=regime,
        cluster=best["cluster"],
        deviation_bps=deviation_bps,
    )
