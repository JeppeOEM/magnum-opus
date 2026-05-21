---
id: 37-5
title: Per-cluster model training with purged CV and SHAP
epic: 37
status: ready-for-dev
---

# Story 37-5: Per-Cluster Model Training with Purged Walk-Forward CV and SHAP

## Context

Trains XGBoost (primary), RandomForest, and LogisticRegression per (symbol, regime, cluster) with purged walk-forward CV — a time-series cross-validation that inserts a gap between train and test to prevent leakage at bar boundaries. XGBoost uses `multi:softprob` for 3-class probability output (REVERT/FLAT/TREND) with class weights upweighting the minority signal classes. SHAP TreeExplainer stored with each model artifact to power dashboard explainability.

TRENDING regimes are skipped — mean reversion doesn't apply there.

## What to build

### `ml-service/ml_service/training/trainer.py`

```python
from __future__ import annotations
import pickle
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import shap
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import f1_score
import xgboost as xgb
import structlog

log = structlog.get_logger()

SKIP_REGIMES = {"TRENDING_UP", "TRENDING_DOWN"}  # mean reversion doesn't apply
LABEL_MAP = {"REVERT": 0, "FLAT": 1, "TREND": 2}
LABEL_REVERSE = {0: "REVERT", 1: "FLAT", 2: "TREND"}
CLASS_WEIGHTS = {0: 2.0, 1: 1.0, 2: 2.0}  # upweight REVERT and TREND

EXCLUDE_COLS = {
    "ts", "exchange", "symbol", "regime", "cluster",
    "label_revert_3", "label_revert_10", "label_spread",
    "adf_pvalue_60", "hedge_ratio_kalman",
}

MIN_SAMPLES = 200
PURGE_GAP   = 5   # bars between train end and test start


def _purged_wf_splits(n: int, n_splits: int = 5, purge_gap: int = PURGE_GAP):
    """Purged walk-forward CV splits. Yields (train_idx, test_idx)."""
    fold_size = n // (n_splits + 1)
    for i in range(n_splits):
        train_end = fold_size * (i + 1)
        test_start = train_end + purge_gap
        test_end   = test_start + fold_size
        if test_end > n:
            break
        train_idx = np.arange(0, train_end)
        test_idx  = np.arange(test_start, test_end)
        yield train_idx, test_idx


def _make_sample_weights(y: np.ndarray) -> np.ndarray:
    return np.array([CLASS_WEIGHTS[int(v)] for v in y], dtype=float)


def _train_one(
    X: np.ndarray,
    y: np.ndarray,
    n_splits: int = 5,
) -> tuple[object, float, str]:
    """Train 3 models with purged CV, return (best_model, cv_f1, model_type)."""
    scaler = StandardScaler()

    candidates = {
        "xgboost": xgb.XGBClassifier(
            objective="multi:softprob",
            num_class=3,
            n_estimators=500,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_lambda=1.0,
            early_stopping_rounds=50,
            eval_metric="mlogloss",
            tree_method="hist",
            random_state=42,
            verbosity=0,
        ),
        "random_forest": RandomForestClassifier(
            n_estimators=200,
            max_depth=8,
            class_weight="balanced",
            random_state=42,
            n_jobs=1,
        ),
        "logistic_regression": LogisticRegression(
            max_iter=1000,
            class_weight="balanced",
            multi_class="multinomial",
            random_state=42,
        ),
    }

    best_name, best_score = "logistic_regression", -1.0
    cv_scores: dict[str, float] = {}

    for name, model in candidates.items():
        fold_scores = []
        for train_idx, test_idx in _purged_wf_splits(len(X), n_splits):
            X_tr_raw, X_te_raw = X[train_idx], X[test_idx]
            y_tr, y_te = y[train_idx], y[test_idx]

            sc = StandardScaler()
            X_tr = sc.fit_transform(X_tr_raw)
            X_te = sc.transform(X_te_raw)

            sw = _make_sample_weights(y_tr)

            try:
                if name == "xgboost":
                    eval_set = [(X_te, y_te)]
                    model.fit(X_tr, y_tr, sample_weight=sw, eval_set=eval_set, verbose=False)
                else:
                    model.fit(X_tr, y_tr, sample_weight=sw)
                preds = model.predict(X_te)
                fold_scores.append(f1_score(y_te, preds, average="weighted", zero_division=0))
            except Exception as e:
                log.warning("cv_fold_failed", model=name, error=str(e))
                fold_scores.append(0.0)

        mean_score = float(np.mean(fold_scores)) if fold_scores else 0.0
        cv_scores[name] = mean_score
        if mean_score > best_score:
            best_score = mean_score
            best_name = name

    # Retrain best model on full data
    X_scaled = scaler.fit_transform(X)
    sw_full  = _make_sample_weights(y)
    best_model = candidates[best_name]
    if best_name == "xgboost":
        best_model.fit(X_scaled, y, sample_weight=sw_full, verbose=False)
    else:
        best_model.fit(X_scaled, y, sample_weight=sw_full)

    return best_model, scaler, best_score, best_name, cv_scores


def _shap_summary(model, model_type: str, X_sample: np.ndarray, feature_names: list[str]) -> dict:
    """Compute mean absolute SHAP per feature (summed over classes)."""
    try:
        explainer = shap.TreeExplainer(model)
        sv = explainer.shap_values(X_sample[:500])
        # sv shape: (n_samples, n_features) or (n_samples, n_features, n_classes)
        if isinstance(sv, list):
            sv_abs = np.abs(np.array(sv)).sum(axis=0)  # sum over classes
        else:
            sv_abs = np.abs(sv)
        mean_abs = sv_abs.mean(axis=0)
        return {f: round(float(v), 6) for f, v in zip(feature_names, mean_abs)}
    except Exception as e:
        log.warning("shap_failed", model_type=model_type, error=str(e))
        return {}


def run_training(
    feature_store_path: str,
    model_registry_path: str,
    exchange: str,
    symbol: str,
    start_date: str,
    end_date: str,
    target: str = "label_revert_10",
) -> dict:
    # Load data
    base = Path(feature_store_path) / f"exchange={exchange}" / f"symbol={symbol}"
    frames = [
        duckdb.execute(f"SELECT * FROM '{f}'").df()
        for f in sorted(base.glob("date=*/part.parquet"))
        if start_date <= f.parent.name.replace("date=", "") <= end_date
    ]
    if not frames:
        return {"error": "no_data"}

    df = pd.concat(frames, ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.sort_values("ts").reset_index(drop=True)

    if target not in df.columns:
        return {"error": f"target column '{target}' not found — run labels first"}

    feat_cols = [c for c in df.select_dtypes(include="number").columns if c not in EXCLUDE_COLS]
    results = {}

    for regime in df["regime"].unique():
        if regime in SKIP_REGIMES:
            log.info("skip_regime_trending", regime=regime)
            continue

        for cluster in df[df["regime"] == regime]["cluster"].unique():
            if cluster == -1:
                continue  # noise points

            mask = (df["regime"] == regime) & (df["cluster"] == cluster)
            subset = df[mask].copy()
            valid = subset[target].notna()
            subset = subset[valid]

            if len(subset) < MIN_SAMPLES:
                log.warning("skip_insufficient_data", regime=regime, cluster=cluster, n=len(subset))
                continue

            X = subset[feat_cols].fillna(0).values
            y = subset[target].map(LABEL_MAP).fillna(1).astype(int).values  # FLAT=1 default

            model, scaler, cv_score, model_type, cv_scores = _train_one(X, y)

            # SHAP
            shap_summary = _shap_summary(model, model_type, scaler.transform(X), feat_cols)

            # Persist artifacts
            art_dir = Path(model_registry_path) / exchange / symbol / str(regime) / str(int(cluster))
            art_dir.mkdir(parents=True, exist_ok=True)
            with open(art_dir / "model.pkl", "wb") as fh:
                pickle.dump(model, fh)
            with open(art_dir / "scaler.pkl", "wb") as fh:
                pickle.dump(scaler, fh)

            entry_key = f"{regime}/{cluster}"
            results[entry_key] = {
                "regime": regime, "cluster": int(cluster),
                "model_type": model_type, "cv_score": round(cv_score, 4),
                "n_samples": len(subset), "feature_names": feat_cols,
                "shap_summary": shap_summary, "cv_scores_by_model": cv_scores,
                "artifact_dir": str(art_dir),
            }
            log.info("model_trained", regime=regime, cluster=cluster,
                     model_type=model_type, cv_score=cv_score)

    return results
```

### FastAPI endpoint

```python
class TrainRequest(BaseModel):
    exchange: str
    symbol: str
    start_date: str
    end_date: str
    target: str = "label_revert_10"

@app.post("/train")
async def train(req: TrainRequest):
    cfg = get_settings()
    return run_training(
        cfg.ml_feature_store_path, cfg.ml_model_registry_path,
        req.exchange, req.symbol, req.start_date, req.end_date, req.target,
    )
```

### `ml-service/tests/test_trainer.py`

```python
import numpy as np
from ml_service.training.trainer import _purged_wf_splits, _make_sample_weights

def test_purge_gap_respected():
    splits = list(_purged_wf_splits(n=1000, n_splits=5, purge_gap=5))
    for train_idx, test_idx in splits:
        assert test_idx.min() >= train_idx.max() + 5  # purge gap enforced

def test_no_overlap_between_train_and_test():
    for train_idx, test_idx in _purged_wf_splits(1000, 5):
        assert len(set(train_idx) & set(test_idx)) == 0

def test_class_weights_applied():
    y = np.array([0, 1, 2, 0, 1])  # REVERT, FLAT, TREND, REVERT, FLAT
    sw = _make_sample_weights(y)
    assert sw[0] == 2.0  # REVERT upweighted
    assert sw[1] == 1.0  # FLAT normal
    assert sw[2] == 2.0  # TREND upweighted

def test_trending_regimes_skipped():
    import pandas as pd, tempfile, os
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=300, freq="1s"),
        "exchange": "bybit", "symbol": "BTCUSDT",
        "regime": "TRENDING_UP", "cluster": 0,
        "label_revert_10": "REVERT",
        "ofi_l1": np.random.randn(300),
    })
    store = tempfile.mkdtemp()
    os.makedirs(f"{store}/exchange=bybit/symbol=BTCUSDT/date=2026-01-01", exist_ok=True)
    import duckdb
    duckdb.execute(f"COPY (SELECT * FROM df) TO '{store}/exchange=bybit/symbol=BTCUSDT/date=2026-01-01/part.parquet' (FORMAT PARQUET)")
    from ml_service.training.trainer import run_training
    results = run_training(store, tempfile.mkdtemp(), "bybit", "BTCUSDT", "2026-01-01", "2026-01-01")
    assert results == {}  # TRENDING_UP skipped, no models trained
```

## Acceptance Criteria

1. `XGBClassifier(objective="multi:softprob", num_class=3)` trained per (regime, cluster).
2. Class weights: REVERT=2.0, TREND=2.0, FLAT=1.0 applied via `sample_weight`.
3. Purged walk-forward CV: `test_idx.min() >= train_idx.max() + purge_gap` for all folds.
4. TRENDING_UP and TRENDING_DOWN regimes produce no models (skipped).
5. Best model selected by mean weighted F1-macro across CV folds.
6. SHAP `TreeExplainer` computed on ≤500 training samples; `shap_summary: {feature: mean_abs_shap}` stored in result.
7. Artifacts written: `{registry}/{exchange}/{symbol}/{regime}/{cluster}/model.pkl` and `scaler.pkl`.
8. Groups with fewer than 200 samples skipped with WARNING log.
9. All 4 unit tests pass.

## Dev Notes

- XGBoost `early_stopping_rounds=50` needs an `eval_set` — during CV use the test fold; during final retraining, skip early stopping (or use a 10% holdout).
- `shap.TreeExplainer` works directly on XGBoost and RF; it does NOT support LogisticRegression (TreeExplainer). Fall back gracefully: if shap fails, return `{}` and log warning.
- `LABEL_MAP` maps string labels to integers 0/1/2 — XGBoost requires integer class labels for `multi:softprob`.
