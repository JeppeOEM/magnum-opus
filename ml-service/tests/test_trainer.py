import os
import tempfile

import duckdb
import numpy as np
import pandas as pd
import pytest

from ml_service.training.trainer import (
    _make_sample_weights,
    _purged_wf_splits,
    run_training,
)


def test_purge_gap_respected():
    """test_idx.min() >= train_idx.max() + purge_gap for every fold."""
    splits = list(_purged_wf_splits(n=1000, n_splits=5, purge_gap=5))
    assert len(splits) > 0, "Expected at least one split"
    for train_idx, test_idx in splits:
        gap = test_idx.min() - train_idx.max()
        assert gap >= 5, (
            f"Purge gap violated: test starts at {test_idx.min()}, "
            f"train ends at {train_idx.max()}, gap={gap}"
        )


def test_no_overlap_between_train_and_test():
    """Train and test index sets must be disjoint."""
    for train_idx, test_idx in _purged_wf_splits(1000, 5):
        overlap = set(train_idx) & set(test_idx)
        assert len(overlap) == 0, f"Overlap found: {len(overlap)} indices"


def test_class_weights_applied():
    """REVERT and TREND are upweighted; FLAT is normal weight."""
    y = np.array([0, 1, 2, 0, 1])  # REVERT, FLAT, TREND, REVERT, FLAT
    sw = _make_sample_weights(y)
    assert sw[0] == 2.0, "REVERT (class 0) should be upweighted to 2.0"
    assert sw[1] == 1.0, "FLAT (class 1) should have weight 1.0"
    assert sw[2] == 2.0, "TREND (class 2) should be upweighted to 2.0"


def test_trending_regimes_skipped(tmp_path):
    """TRENDING_UP and TRENDING_DOWN must not produce any trained models."""
    n = 300
    df = pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01", periods=n, freq="1s"),
            "exchange": "bybit",
            "symbol": "BTCUSDT",
            "regime": "TRENDING_UP",
            "cluster": 0,
            "label_revert_10": "REVERT",
            "ofi_l1": np.random.randn(n),
            "spread_mean": 0.1,
        }
    )
    store = str(tmp_path / "features")
    os.makedirs(f"{store}/exchange=bybit/symbol=BTCUSDT/date=2026-01-01", exist_ok=True)
    duckdb.execute(
        f"COPY (SELECT * FROM df) TO "
        f"'{store}/exchange=bybit/symbol=BTCUSDT/date=2026-01-01/part.parquet' "
        f"(FORMAT PARQUET, COMPRESSION SNAPPY)"
    )

    results = run_training(
        store,
        str(tmp_path / "models"),
        "bybit",
        "BTCUSDT",
        "2026-01-01",
        "2026-01-01",
    )
    assert results == {}, f"Expected no models for TRENDING_UP, got: {results}"


def test_trending_down_skipped(tmp_path):
    """TRENDING_DOWN must also produce no models."""
    n = 300
    df = pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01", periods=n, freq="1s"),
            "exchange": "bybit",
            "symbol": "BTCUSDT",
            "regime": "TRENDING_DOWN",
            "cluster": 0,
            "label_revert_10": "FLAT",
            "ofi_l1": np.random.randn(n),
        }
    )
    store = str(tmp_path / "features")
    os.makedirs(f"{store}/exchange=bybit/symbol=BTCUSDT/date=2026-01-01", exist_ok=True)
    duckdb.execute(
        f"COPY (SELECT * FROM df) TO "
        f"'{store}/exchange=bybit/symbol=BTCUSDT/date=2026-01-01/part.parquet' "
        f"(FORMAT PARQUET, COMPRESSION SNAPPY)"
    )

    results = run_training(
        store,
        str(tmp_path / "models"),
        "bybit",
        "BTCUSDT",
        "2026-01-01",
        "2026-01-01",
    )
    assert results == {}


def test_no_data_returns_error(tmp_path):
    """Returns {'error': 'no_data'} when no Parquet files exist."""
    results = run_training(
        str(tmp_path / "empty"),
        str(tmp_path / "models"),
        "bybit",
        "BTCUSDT",
        "2026-01-01",
        "2026-01-01",
    )
    assert results == {"error": "no_data"}


def test_missing_target_returns_error(tmp_path):
    """Returns error when target column is not present."""
    n = 300
    df = pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01", periods=n, freq="1s"),
            "exchange": "bybit",
            "symbol": "BTCUSDT",
            "regime": "RANGING",
            "cluster": 0,
            "ofi_l1": np.random.randn(n),
            # no label_revert_10 column
        }
    )
    store = str(tmp_path / "features")
    os.makedirs(f"{store}/exchange=bybit/symbol=BTCUSDT/date=2026-01-01", exist_ok=True)
    duckdb.execute(
        f"COPY (SELECT * FROM df) TO "
        f"'{store}/exchange=bybit/symbol=BTCUSDT/date=2026-01-01/part.parquet' "
        f"(FORMAT PARQUET, COMPRESSION SNAPPY)"
    )

    results = run_training(
        store,
        str(tmp_path / "models"),
        "bybit",
        "BTCUSDT",
        "2026-01-01",
        "2026-01-01",
        target="label_revert_10",
    )
    assert "error" in results


def test_insufficient_samples_skipped(tmp_path):
    """Groups with fewer than MIN_SAMPLES are skipped."""
    n = 50  # well below MIN_SAMPLES=200
    df = pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01", periods=n, freq="1s"),
            "exchange": "bybit",
            "symbol": "BTCUSDT",
            "regime": "RANGING",
            "cluster": 0,
            "label_revert_10": np.random.choice(["REVERT", "FLAT", "TREND"], n),
            "ofi_l1": np.random.randn(n),
        }
    )
    store = str(tmp_path / "features")
    os.makedirs(f"{store}/exchange=bybit/symbol=BTCUSDT/date=2026-01-01", exist_ok=True)
    duckdb.execute(
        f"COPY (SELECT * FROM df) TO "
        f"'{store}/exchange=bybit/symbol=BTCUSDT/date=2026-01-01/part.parquet' "
        f"(FORMAT PARQUET, COMPRESSION SNAPPY)"
    )

    results = run_training(
        store,
        str(tmp_path / "models"),
        "bybit",
        "BTCUSDT",
        "2026-01-01",
        "2026-01-01",
    )
    assert results == {}
