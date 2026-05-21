---
id: 38-1
title: Inference engine — core prediction logic
epic: 38
status: ready-for-dev
---

# Story 38-1: Inference Engine — Core Prediction Logic

## Context

Pure prediction function: takes a feature row dict, loads the right model from the registry, and returns a `Signal`. Zero asyncio, zero IO — all I/O lives in the loop (Story 38-2). This separation makes the prediction path unit-testable without mocking QuestDB or Redis.

When `hurst_60 >= 0.5` (trending window), always returns FLAT with confidence 1.0 — the regime gate protects against fading a trend.

## What to build

### `ml-service/ml_service/inference/engine.py`

```python
from __future__ import annotations
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import structlog

from ml_service.registry.registry import get_model

log = structlog.get_logger()

LABEL_REVERSE = {0: "REVERT", 1: "FLAT", 2: "TREND"}


@dataclass
class Signal:
    label: str          # REVERT / FLAT / TREND
    confidence: float   # max probability from model
    model_id: str | None
    regime: str
    cluster: int
    deviation_bps: float  # microprice_mid_delta of input row


_MODEL_CACHE: dict[str, tuple[object, object]] = {}  # key → (scaler, model)


def _load_artifacts(artifact_dir: str) -> tuple[object, object] | None:
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
    """Call after SIGHUP registry reload to force artifact re-read."""
    _MODEL_CACHE.clear()


def _assign_cluster(
    row_scaled: np.ndarray,
    entry: dict,
) -> int:
    """Assign cluster using PCA projection distance to cluster centroid (approximate).
    For now returns entry['cluster'] — the inference loop already knows which cluster
    to route to after reading regime from Redis and matching registry entry.
    """
    return entry["cluster"]


def _flat_signal(regime: str, deviation_bps: float) -> Signal:
    return Signal(label="FLAT", confidence=1.0, model_id=None,
                  regime=regime, cluster=-1, deviation_bps=deviation_bps)


def predict(
    row: dict[str, Any],
    exchange: str,
    symbol: str,
    regime: str,
) -> Signal:
    """
    Given a snapshot_1s feature row and the current regime string,
    predict signal for the (exchange, symbol, regime).

    Returns FLAT when:
    - regime is TRENDING_UP or TRENDING_DOWN
    - hurst_60 >= 0.5 in the row
    - no model found in registry
    - artifact load fails
    """
    deviation_bps = float(row.get("microprice_mid_delta") or 0.0)

    # Regime gate: trending → never fade
    if regime in ("TRENDING_UP", "TRENDING_DOWN"):
        return _flat_signal(regime, deviation_bps)

    # Hurst gate: trending window → stay flat
    hurst = float(row.get("hurst_60") or 0.5)
    if hurst >= 0.5:
        return _flat_signal(regime, deviation_bps)

    # Find best matching model entry (highest cv_score for this regime)
    # In practice, the registry has one entry per (symbol, regime, cluster)
    # We use the one with the highest CV score as the primary model
    from ml_service.registry.registry import get_all
    candidates = [
        e for e in get_all()
        if e["exchange"] == exchange and e["symbol"] == symbol
        and e["regime"] == regime and e["cluster"] != -1
    ]
    if not candidates:
        return _flat_signal(regime, deviation_bps)

    # Pick best by cv_score
    best = max(candidates, key=lambda e: e["cv_score"])
    artifacts = _load_artifacts(best["artifact_dir"])
    if artifacts is None:
        return _flat_signal(regime, deviation_bps)

    scaler, model = artifacts
    feat_cols: list[str] = best["feature_names"]

    # Build feature vector — fill missing with 0
    x = np.array([float(row.get(c) or 0.0) for c in feat_cols], dtype=float).reshape(1, -1)
    x_scaled = scaler.transform(x)

    try:
        proba = model.predict_proba(x_scaled)[0]  # shape (3,)
        label_idx = int(np.argmax(proba))
        confidence = float(proba[label_idx])
        label = LABEL_REVERSE.get(label_idx, "FLAT")
    except Exception as e:
        log.warning("model_predict_failed", error=str(e))
        return _flat_signal(regime, deviation_bps)

    return Signal(
        label=label,
        confidence=confidence,
        model_id=best["id"],
        regime=regime,
        cluster=best["cluster"],
        deviation_bps=deviation_bps,
    )
```

### `ml-service/tests/test_inference_engine.py`

```python
from unittest.mock import MagicMock, patch
import numpy as np
from ml_service.inference.engine import predict, Signal, _flat_signal

def _mock_registry(entries):
    return entries

def test_trending_regime_returns_flat():
    sig = predict({}, "bybit", "BTCUSDT", "TRENDING_UP")
    assert sig.label == "FLAT"
    assert sig.confidence == 1.0
    assert sig.model_id is None

def test_high_hurst_returns_flat():
    row = {"hurst_60": 0.7, "microprice_mid_delta": 5.0}
    sig = predict(row, "bybit", "BTCUSDT", "RANGING")
    assert sig.label == "FLAT"

def test_no_model_in_registry_returns_flat():
    with patch("ml_service.inference.engine.get_all", return_value=[]):
        sig = predict({"hurst_60": 0.3}, "bybit", "BTCUSDT", "RANGING")
    assert sig.label == "FLAT"
    assert sig.model_id is None

def test_predict_uses_highest_cv_score_model():
    # Two models for same regime — should pick higher cv_score
    mock_entries = [
        {"id": "low", "exchange": "bybit", "symbol": "BTCUSDT", "regime": "RANGING",
         "cluster": 0, "cv_score": 0.5, "feature_names": ["ofi_l1"],
         "artifact_dir": "/fake/low"},
        {"id": "high", "exchange": "bybit", "symbol": "BTCUSDT", "regime": "RANGING",
         "cluster": 1, "cv_score": 0.8, "feature_names": ["ofi_l1"],
         "artifact_dir": "/fake/high"},
    ]
    mock_scaler = MagicMock()
    mock_scaler.transform.return_value = np.array([[0.5]])
    mock_model = MagicMock()
    mock_model.predict_proba.return_value = np.array([[0.6, 0.2, 0.2]])

    with patch("ml_service.inference.engine.get_all", return_value=mock_entries), \
         patch("ml_service.inference.engine._load_artifacts", return_value=(mock_scaler, mock_model)):
        sig = predict({"hurst_60": 0.3, "ofi_l1": 5.0}, "bybit", "BTCUSDT", "RANGING")

    assert sig.model_id == "high"
    assert sig.label == "REVERT"
    assert sig.confidence == 0.6
```

## Acceptance Criteria

1. `predict()` returns FLAT (confidence=1.0, model_id=None) for TRENDING_UP / TRENDING_DOWN regimes.
2. Returns FLAT when `hurst_60 >= 0.5`.
3. Returns FLAT when no matching model in registry.
4. Selects model with highest `cv_score` when multiple entries exist for a regime.
5. Returns `Signal(label, confidence, model_id, regime, cluster, deviation_bps)`.
6. `confidence` = `max(predict_proba)` from the best model.
7. `clear_cache()` empties `_MODEL_CACHE` (called after SIGHUP registry reload).
8. All 4 unit tests pass.

## Dev Notes

- `_MODEL_CACHE` is a module-level dict — fast for repeated calls from the 1s loop.
- Missing feature columns in `row` are filled with 0.0 — consistent with training imputation.
- `predict_proba` shape is `(1, 3)` for XGBoost multi:softprob and sklearn RF/LR.
