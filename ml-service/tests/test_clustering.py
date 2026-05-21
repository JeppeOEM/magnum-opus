import os

import duckdb
import numpy as np
import pandas as pd
import pytest

from ml_service.clustering.pipeline import _feature_cols, run_clustering


def _write_parquet(df: pd.DataFrame, store: str, exchange: str, symbol: str, date: str) -> None:
    out_dir = f"{store}/exchange={exchange}/symbol={symbol}/date={date}"
    os.makedirs(out_dir, exist_ok=True)
    path = f"{out_dir}/part.parquet"
    duckdb.execute(f"COPY (SELECT * FROM df) TO '{path}' (FORMAT PARQUET, COMPRESSION SNAPPY)")


def _make_clustering_df(n: int = 300, regime: str = "RANGING") -> pd.DataFrame:
    """Synthetic DataFrame with two distinct clusters in the specified regime."""
    rng = np.random.default_rng(42)
    return pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01", periods=n, freq="1s"),
            "exchange": "bybit",
            "symbol": "BTCUSDT",
            "regime": regime,
            # Two separated clusters in ofi_l1 space
            "ofi_l1": np.concatenate(
                [rng.normal(10, 0.5, n // 2), rng.normal(-10, 0.5, n // 2)]
            ),
            "spread_mean": rng.normal(0.1, 0.01, n),
            "realized_vol": rng.normal(0.001, 0.0001, n),
        }
    )


def test_clustering_finds_two_clusters(tmp_path):
    """Well-separated synthetic data in RANGING should yield ≥2 clusters."""
    df = _make_clustering_df(n=300)
    store = str(tmp_path / "features")
    _write_parquet(df, store, "bybit", "BTCUSDT", "2026-01-01")
    models = str(tmp_path / "models")

    stats = run_clustering(
        store, models, "bybit", "BTCUSDT",
        "2026-01-01", "2026-01-01",
        n_pca_components=2, min_cluster_size=20,
    )

    assert "RANGING" in stats
    assert stats["RANGING"]["n_clusters"] >= 2, (
        f"Expected ≥2 clusters, got {stats['RANGING']['n_clusters']}"
    )


def test_clustering_deterministic(tmp_path):
    """Two runs on same data with same random_state must produce identical stats."""
    df = _make_clustering_df(n=300)
    store = str(tmp_path / "features")
    _write_parquet(df, store, "bybit", "BTCUSDT", "2026-01-01")
    models = str(tmp_path / "models")

    kwargs = dict(
        feature_store_path=store,
        model_registry_path=models,
        exchange="bybit",
        symbol="BTCUSDT",
        start_date="2026-01-01",
        end_date="2026-01-01",
        n_pca_components=2,
        min_cluster_size=20,
        random_state=42,
    )
    stats1 = run_clustering(**kwargs)
    stats2 = run_clustering(**kwargs)

    assert stats1 == stats2, f"Non-deterministic: {stats1} != {stats2}"


def test_noise_only_when_insufficient_data(tmp_path):
    """Regime with fewer rows than min_cluster_size → no stats entry (warning only)."""
    n = 10
    df = pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01", periods=n, freq="1s"),
            "exchange": "bybit",
            "symbol": "BTCUSDT",
            "regime": "RANGING",
            "ofi_l1": np.random.randn(n),
            "spread_mean": 0.1,
        }
    )
    store = str(tmp_path / "features")
    _write_parquet(df, store, "bybit", "BTCUSDT", "2026-01-01")

    stats = run_clustering(
        store, str(tmp_path / "models"), "bybit", "BTCUSDT",
        "2026-01-01", "2026-01-01", min_cluster_size=50,
    )

    # Insufficient data → no stats entry for RANGING
    assert "RANGING" not in stats


def test_pca_artifact_saved(tmp_path):
    """PCA pickle is written to the model registry directory."""
    import pickle

    df = _make_clustering_df(n=300)
    store = str(tmp_path / "features")
    _write_parquet(df, store, "bybit", "BTCUSDT", "2026-01-01")
    models = str(tmp_path / "models")

    run_clustering(
        store, models, "bybit", "BTCUSDT",
        "2026-01-01", "2026-01-01",
        n_pca_components=2, min_cluster_size=20,
    )

    artifact_path = tmp_path / "models" / "bybit" / "BTCUSDT" / "RANGING" / "pca.pkl"
    assert artifact_path.exists(), "pca.pkl not saved"

    with open(artifact_path, "rb") as fh:
        art = pickle.load(fh)

    assert "scaler" in art
    assert "pca" in art
    assert "feature_cols" in art
    assert isinstance(art["feature_cols"], list)


def test_cluster_column_written_to_parquet(tmp_path):
    """cluster column is present in the Parquet file after run_clustering."""
    df = _make_clustering_df(n=300)
    store = str(tmp_path / "features")
    _write_parquet(df, store, "bybit", "BTCUSDT", "2026-01-01")

    run_clustering(
        store, str(tmp_path / "models"), "bybit", "BTCUSDT",
        "2026-01-01", "2026-01-01",
        n_pca_components=2, min_cluster_size=20,
    )

    result = duckdb.execute(
        f"SELECT cluster FROM read_parquet('{store}/exchange=bybit/symbol=BTCUSDT/date=2026-01-01/part.parquet')"
    ).df()
    assert "cluster" in result.columns
    # Should have at least some non-noise points
    assert (result["cluster"] >= 0).any()


def test_label_cols_excluded_from_features():
    """Label and target columns must not appear in the feature matrix."""
    df = pd.DataFrame(
        {
            "ofi_l1": [1.0],
            "spread_mean": [0.1],
            "ts": pd.Timestamp("2026-01-01"),
            "exchange": "bybit",
            "symbol": "BTCUSDT",
            "regime": "RANGING",
            "cluster": [0],
            "label_revert_3": ["REVERT"],
            "label_revert_10": ["FLAT"],
            "label_spread": ["NEUTRAL"],
        }
    )
    cols = _feature_cols(df)
    assert "label_revert_3" not in cols
    assert "label_revert_10" not in cols
    assert "label_spread" not in cols
    assert "cluster" not in cols
    assert "ofi_l1" in cols
    assert "spread_mean" in cols


def test_no_data_returns_error(tmp_path):
    """Returns {'error': 'no_data'} when no Parquet files match the date range."""
    stats = run_clustering(
        str(tmp_path / "empty"), str(tmp_path / "models"),
        "bybit", "BTCUSDT", "2026-01-01", "2026-01-01",
    )
    assert stats == {"error": "no_data"}
