from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from ml_service.inference.engine import Signal, _flat_signal, clear_cache, predict


def test_trending_up_returns_flat():
    """TRENDING_UP always returns FLAT with confidence=1.0 and model_id=None."""
    sig = predict({}, "bybit", "BTCUSDT", "TRENDING_UP")
    assert sig.label == "FLAT"
    assert sig.confidence == 1.0
    assert sig.model_id is None


def test_trending_down_returns_flat():
    """TRENDING_DOWN always returns FLAT."""
    sig = predict({"microprice_mid_delta": 5.0}, "bybit", "BTCUSDT", "TRENDING_DOWN")
    assert sig.label == "FLAT"
    assert sig.model_id is None


def test_high_hurst_returns_flat():
    """hurst_60 >= 0.5 should gate the prediction and return FLAT."""
    row = {"hurst_60": 0.7, "microprice_mid_delta": 5.0}
    sig = predict(row, "bybit", "BTCUSDT", "RANGING")
    assert sig.label == "FLAT"


def test_hurst_exactly_half_returns_flat():
    """hurst_60 == 0.5 is the boundary — should be FLAT (>= 0.5)."""
    row = {"hurst_60": 0.5, "microprice_mid_delta": 5.0}
    sig = predict(row, "bybit", "BTCUSDT", "RANGING")
    assert sig.label == "FLAT"


def test_no_model_in_registry_returns_flat():
    with patch("ml_service.inference.engine.get_all", return_value=[]):
        sig = predict({"hurst_60": 0.3}, "bybit", "BTCUSDT", "RANGING")
    assert sig.label == "FLAT"
    assert sig.model_id is None


def test_predict_uses_highest_cv_score_model():
    """When two models exist for the same regime, pick the one with higher cv_score."""
    mock_entries = [
        {
            "id": "low",
            "exchange": "bybit", "symbol": "BTCUSDT",
            "regime": "RANGING", "cluster": 0,
            "cv_score": 0.5, "feature_names": ["ofi_l1"],
            "artifact_dir": "/fake/low",
        },
        {
            "id": "high",
            "exchange": "bybit", "symbol": "BTCUSDT",
            "regime": "RANGING", "cluster": 1,
            "cv_score": 0.8, "feature_names": ["ofi_l1"],
            "artifact_dir": "/fake/high",
        },
    ]
    mock_scaler = MagicMock()
    mock_scaler.transform.return_value = np.array([[0.5]])
    mock_model = MagicMock()
    mock_model.predict_proba.return_value = np.array([[0.6, 0.2, 0.2]])

    with patch("ml_service.inference.engine.get_all", return_value=mock_entries), \
         patch("ml_service.inference.engine._load_artifacts", return_value=(mock_scaler, mock_model)):
        sig = predict({"hurst_60": 0.3, "ofi_l1": 5.0}, "bybit", "BTCUSDT", "RANGING")

    assert sig.model_id == "high"
    assert sig.label == "REVERT"  # argmax([0.6, 0.2, 0.2]) == 0 == REVERT
    assert sig.confidence == pytest.approx(0.6)


def test_deviation_bps_captured():
    """deviation_bps in Signal reflects microprice_mid_delta from row."""
    with patch("ml_service.inference.engine.get_all", return_value=[]):
        sig = predict(
            {"hurst_60": 0.3, "microprice_mid_delta": 7.5},
            "bybit", "BTCUSDT", "RANGING",
        )
    assert sig.deviation_bps == pytest.approx(7.5)


def test_artifact_load_failure_returns_flat():
    """When artifact loading fails, return FLAT gracefully."""
    mock_entries = [
        {
            "id": "a", "exchange": "bybit", "symbol": "BTCUSDT",
            "regime": "RANGING", "cluster": 0,
            "cv_score": 0.6, "feature_names": ["ofi_l1"],
            "artifact_dir": "/nonexistent/path",
        }
    ]
    with patch("ml_service.inference.engine.get_all", return_value=mock_entries), \
         patch("ml_service.inference.engine._load_artifacts", return_value=None):
        sig = predict({"hurst_60": 0.3}, "bybit", "BTCUSDT", "RANGING")
    assert sig.label == "FLAT"
    assert sig.model_id is None


def test_clear_cache_empties_model_cache():
    """clear_cache() should empty _MODEL_CACHE."""
    from ml_service.inference import engine
    engine._MODEL_CACHE["fake_key"] = ("scaler", "model")
    assert len(engine._MODEL_CACHE) > 0
    clear_cache()
    assert len(engine._MODEL_CACHE) == 0


def test_predict_model_failure_returns_flat():
    """When predict_proba raises, return FLAT without crashing."""
    mock_entries = [
        {
            "id": "x", "exchange": "bybit", "symbol": "BTCUSDT",
            "regime": "RANGING", "cluster": 0,
            "cv_score": 0.6, "feature_names": ["ofi_l1"],
            "artifact_dir": "/fake/path",
        }
    ]
    mock_scaler = MagicMock()
    mock_scaler.transform.return_value = np.array([[0.5]])
    mock_model = MagicMock()
    mock_model.predict_proba.side_effect = RuntimeError("model broken")

    with patch("ml_service.inference.engine.get_all", return_value=mock_entries), \
         patch("ml_service.inference.engine._load_artifacts", return_value=(mock_scaler, mock_model)):
        sig = predict({"hurst_60": 0.3, "ofi_l1": 1.0}, "bybit", "BTCUSDT", "RANGING")
    assert sig.label == "FLAT"
