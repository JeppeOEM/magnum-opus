---
id: 37-4
title: Regime split and HDBSCAN clustering per regime
epic: 37
status: ready-for-dev
---

# Story 37-4: Regime Split and HDBSCAN Clustering per Regime

## Context

Clustering isolates structurally distinct market microstructure patterns within each regime. HDBSCAN is chosen over KMeans because: (1) doesn't require pre-specifying cluster count, (2) handles noise points (cluster=-1) robustly, (3) works well with dense clusters of varying shape. Regime isolation is critical — patterns in RANGING look nothing like patterns in HIGH_VOL; mixing them would dilute signal.

PCA before HDBSCAN reduces dimensionality and removes correlated feature noise, improving clustering quality.

## What to build

### `ml-service/ml_service/clustering/pipeline.py`

```python
from __future__ import annotations
from pathlib import Path
import pickle

import duckdb
import hdbscan
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
import structlog

log = structlog.get_logger()

REGIMES = ["RANGING", "HIGH_VOL", "THIN_BOOK", "TRENDING_UP", "TRENDING_DOWN"]

# Columns excluded from feature matrix
EXCLUDE_COLS = {"ts", "exchange", "symbol", "regime", "cluster",
                "label_revert_3", "label_revert_10", "label_spread",
                "adf_pvalue_60", "hedge_ratio_kalman"}


def _feature_cols(df: pd.DataFrame) -> list[str]:
    return [
        c for c in df.select_dtypes(include="number").columns
        if c not in EXCLUDE_COLS
    ]


def run_clustering(
    feature_store_path: str,
    model_registry_path: str,
    exchange: str,
    symbol: str,
    start_date: str,
    end_date: str,
    n_pca_components: int = 10,
    min_cluster_size: int = 50,
    random_state: int = 42,
) -> dict:
    # Load all Parquet for date range
    base = Path(feature_store_path) / f"exchange={exchange}" / f"symbol={symbol}"
    frames = []
    for f in sorted(base.glob("date=*/part.parquet")):
        date_str = f.parent.name.replace("date=", "")
        if start_date <= date_str <= end_date:
            frames.append(duckdb.execute(f"SELECT * FROM '{f}'").df())

    if not frames:
        return {"error": "no_data"}

    df = pd.concat(frames, ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"])

    if "regime" not in df.columns:
        df["regime"] = "RANGING"

    stats = {}

    for regime in REGIMES:
        subset = df[df["regime"] == regime].copy()
        if len(subset) < min_cluster_size:
            log.warning("regime_insufficient_data", regime=regime, n=len(subset))
            df.loc[df["regime"] == regime, "cluster"] = -1
            continue

        feat_cols = _feature_cols(subset)
        X = subset[feat_cols].fillna(0).values

        # StandardScaler
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # PCA
        n_comp = min(n_pca_components, X_scaled.shape[1], X_scaled.shape[0] - 1)
        pca = PCA(n_components=n_comp, random_state=random_state)
        X_pca = pca.fit_transform(X_scaled)

        # HDBSCAN
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            core_dist_n_jobs=1,  # deterministic
        )
        labels = clusterer.fit_predict(X_pca)
        n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
        noise_pct = (labels == -1).mean() * 100

        df.loc[df["regime"] == regime, "cluster"] = labels
        stats[regime] = {
            "n_samples": len(subset),
            "n_clusters": n_clusters,
            "noise_pct": round(noise_pct, 1),
        }

        # Persist PCA artifact
        art_dir = Path(model_registry_path) / exchange / symbol / regime
        art_dir.mkdir(parents=True, exist_ok=True)
        with open(art_dir / "pca.pkl", "wb") as fh:
            pickle.dump({"scaler": scaler, "pca": pca, "feature_cols": feat_cols}, fh)

        log.info("clustering_done", regime=regime, n_clusters=n_clusters, noise_pct=noise_pct)

    # Write cluster column back to Parquet files (by date)
    df["ts_date"] = df["ts"].dt.date.astype(str)
    for date_val, group in df.groupby("ts_date"):
        parquet_file = base / f"date={date_val}" / "part.parquet"
        if parquet_file.exists():
            duckdb.execute(
                f"COPY (SELECT * FROM group) TO '{parquet_file}' "
                f"(FORMAT PARQUET, COMPRESSION SNAPPY)"
            )

    return stats
```

### FastAPI endpoint

```python
class ClusterRequest(BaseModel):
    exchange: str
    symbol: str
    start_date: str
    end_date: str
    n_pca_components: int = 10
    min_cluster_size: int = 50

@app.post("/cluster")
async def cluster(req: ClusterRequest):
    cfg = get_settings()
    return run_clustering(
        cfg.ml_feature_store_path, cfg.ml_model_registry_path,
        req.exchange, req.symbol, req.start_date, req.end_date,
        req.n_pca_components, req.min_cluster_size,
    )
```

### `ml-service/tests/test_clustering.py`

```python
import numpy as np
import pandas as pd
from ml_service.clustering.pipeline import run_clustering
import tempfile, os

def test_clustering_deterministic(tmp_path):
    # Generate synthetic data with 2 clear clusters in RANGING regime
    rng = np.random.default_rng(42)
    n = 300
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="1s"),
        "exchange": "bybit", "symbol": "BTCUSDT",
        "regime": "RANGING",
        "ofi_l1": np.concatenate([rng.normal(10, 1, n//2), rng.normal(-10, 1, n//2)]),
        "spread_mean": rng.normal(0.1, 0.01, n),
        "realized_vol": rng.normal(0.001, 0.0001, n),
    })
    # Write to parquet
    store = str(tmp_path / "features")
    os.makedirs(f"{store}/exchange=bybit/symbol=BTCUSDT/date=2026-01-01", exist_ok=True)
    import duckdb
    duckdb.execute(f"COPY (SELECT * FROM df) TO '{store}/exchange=bybit/symbol=BTCUSDT/date=2026-01-01/part.parquet' (FORMAT PARQUET)")

    models = str(tmp_path / "models")
    stats1 = run_clustering(store, models, "bybit", "BTCUSDT", "2026-01-01", "2026-01-01", n_pca_components=2, min_cluster_size=20)
    stats2 = run_clustering(store, models, "bybit", "BTCUSDT", "2026-01-01", "2026-01-01", n_pca_components=2, min_cluster_size=20)

    # Deterministic: same clusters both runs
    assert stats1 == stats2

def test_noise_only_when_insufficient_data(tmp_path):
    # Only 10 rows — below min_cluster_size=50 → all noise
    n = 10
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="1s"),
        "exchange": "bybit", "symbol": "BTCUSDT", "regime": "RANGING",
        "ofi_l1": np.random.randn(n),
    })
    store = str(tmp_path / "features")
    os.makedirs(f"{store}/exchange=bybit/symbol=BTCUSDT/date=2026-01-01", exist_ok=True)
    import duckdb
    duckdb.execute(f"COPY (SELECT * FROM df) TO '{store}/exchange=bybit/symbol=BTCUSDT/date=2026-01-01/part.parquet' (FORMAT PARQUET)")
    stats = run_clustering(store, str(tmp_path / "models"), "bybit", "BTCUSDT", "2026-01-01", "2026-01-01", min_cluster_size=50)
    # Insufficient data → warning logged, no entry in stats for RANGING
    assert "RANGING" not in stats or stats.get("RANGING", {}).get("n_clusters", 0) == 0
```

## Acceptance Criteria

1. Data split by `regime` before clustering — HDBSCAN never runs across regimes.
2. Pipeline: StandardScaler → PCA(n=10) → HDBSCAN(min_cluster_size=50).
3. Cluster assignments written back to Parquet files as `cluster` column.
4. PCA artifact saved: `{registry}/{exchange}/{symbol}/{regime}/pca.pkl` containing `{scaler, pca, feature_cols}`.
5. With `random_state=42`, two identical runs on same data produce identical cluster assignments (deterministic).
6. Regime with fewer than `min_cluster_size` rows: all rows assigned `cluster=-1`, warning logged.
7. Both unit tests pass.

## Dev Notes

- `hdbscan.HDBSCAN(core_dist_n_jobs=1)` is required for determinism — parallel HDBSCAN is non-deterministic.
- `EXCLUDE_COLS` must include all label columns to prevent data leakage into feature matrix.
- PCA `n_components` is capped at `min(n_pca_components, n_features, n_samples-1)` to avoid scikit-learn errors.
- TRENDING_UP / TRENDING_DOWN models are NOT trained (see Story 37-5) but clustering still runs — the cluster labels will be available if a future story decides to train momentum models.
