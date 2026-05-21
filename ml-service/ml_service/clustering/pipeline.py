"""Regime split and HDBSCAN clustering per regime.

Pipeline: StandardScaler → PCA(n=10) → HDBSCAN(min_cluster_size=50).
Each regime is clustered independently to isolate structurally distinct
microstructure patterns. PCA artifacts are persisted to the model registry.
"""
from __future__ import annotations

import pickle
from pathlib import Path

import duckdb
import hdbscan
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
import structlog

log = structlog.get_logger()

REGIMES = ["RANGING", "HIGH_VOL", "THIN_BOOK", "TRENDING_UP", "TRENDING_DOWN"]

# Columns excluded from feature matrix — labels, identifiers, derived targets
EXCLUDE_COLS = {
    "ts",
    "ts_date",
    "exchange",
    "symbol",
    "regime",
    "cluster",
    "label_revert_3",
    "label_revert_10",
    "label_spread",
    "adf_pvalue_60",
    "hedge_ratio_kalman",
    "spread",
    "spread_zscore",
}


def _feature_cols(df: pd.DataFrame) -> list[str]:
    """Return numeric columns suitable for the feature matrix."""
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
    """Load Parquet for date range, cluster each regime independently, write back.

    Returns per-regime stats: {regime: {n_samples, n_clusters, noise_pct}}.
    PCA artifacts saved to {registry}/{exchange}/{symbol}/{regime}/pca.pkl.
    """
    base = Path(feature_store_path) / f"exchange={exchange}" / f"symbol={symbol}"
    frames = []
    for f in sorted(base.glob("date=*/part.parquet")):
        date_str = f.parent.name.replace("date=", "")
        if start_date <= date_str <= end_date:
            frames.append(duckdb.execute(f"SELECT * FROM read_parquet('{f}')").df())

    if not frames:
        return {"error": "no_data"}

    df = pd.concat(frames, ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"])

    # Default regime when column absent (pre-Epic 35 data)
    if "regime" not in df.columns:
        df["regime"] = "RANGING"

    # Initialise cluster column to noise
    df["cluster"] = -1

    stats: dict = {}

    for regime in REGIMES:
        mask = df["regime"] == regime
        n_regime = mask.sum()

        if n_regime < min_cluster_size:
            log.warning(
                "regime_insufficient_data",
                regime=regime,
                n=int(n_regime),
                min_cluster_size=min_cluster_size,
            )
            # cluster column already -1 for this regime
            continue

        subset = df[mask].copy()
        feat_cols = _feature_cols(subset)

        if not feat_cols:
            log.warning("no_feature_cols", regime=regime)
            continue

        X = subset[feat_cols].fillna(0).values

        # StandardScaler
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # PCA — cap n_components to avoid scikit-learn constraint violations
        n_comp = min(n_pca_components, X_scaled.shape[1], X_scaled.shape[0] - 1)
        pca = PCA(n_components=n_comp, random_state=random_state)
        X_pca = pca.fit_transform(X_scaled)

        # HDBSCAN — core_dist_n_jobs=1 for determinism
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            core_dist_n_jobs=1,
        )
        labels = clusterer.fit_predict(X_pca)

        n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
        noise_pct = float((labels == -1).mean() * 100)

        df.loc[mask, "cluster"] = labels
        stats[regime] = {
            "n_samples": int(n_regime),
            "n_clusters": n_clusters,
            "noise_pct": round(noise_pct, 1),
        }

        # Persist PCA artifact
        art_dir = Path(model_registry_path) / exchange / symbol / regime
        art_dir.mkdir(parents=True, exist_ok=True)
        with open(art_dir / "pca.pkl", "wb") as fh:
            pickle.dump({"scaler": scaler, "pca": pca, "feature_cols": feat_cols}, fh)

        log.info(
            "clustering_done",
            regime=regime,
            n_clusters=n_clusters,
            noise_pct=round(noise_pct, 1),
        )

    # Write cluster column back to Parquet files by date partition
    df["ts_date"] = df["ts"].dt.date.astype(str)
    for date_val, partition_df in df.groupby("ts_date"):
        parquet_file = base / f"date={date_val}" / "part.parquet"
        if parquet_file.exists():
            partition_df = partition_df.drop(columns=["ts_date"])
            duckdb.execute(
                f"COPY (SELECT * FROM partition_df) TO '{parquet_file}' "
                f"(FORMAT PARQUET, COMPRESSION SNAPPY)"
            )

    return stats
