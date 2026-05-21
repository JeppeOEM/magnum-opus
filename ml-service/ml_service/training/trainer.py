"""Per-cluster model training with purged walk-forward CV and SHAP.

Pipeline per (regime, cluster):
  1. Purged walk-forward CV — gap between train and test prevents bar-boundary leakage.
  2. Three candidates: XGBoost (multi:softprob), RandomForest, LogisticRegression.
  3. Best model selected by mean weighted F1-macro across CV folds.
  4. Final retrain on full data.
  5. SHAP TreeExplainer summary stored in result.

TRENDING regimes are skipped — mean reversion signal does not apply.
"""
from __future__ import annotations

import pickle
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import shap
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.preprocessing import StandardScaler
import xgboost as xgb
import structlog

log = structlog.get_logger()

SKIP_REGIMES = {"TRENDING_UP", "TRENDING_DOWN"}  # mean reversion doesn't apply
LABEL_MAP = {"REVERT": 0, "FLAT": 1, "TREND": 2}
LABEL_REVERSE = {0: "REVERT", 1: "FLAT", 2: "TREND"}
CLASS_WEIGHTS = {0: 2.0, 1: 1.0, 2: 2.0}  # upweight minority REVERT and TREND

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

MIN_SAMPLES = 200
PURGE_GAP = 5  # bars between train end and test start


def _purged_wf_splits(
    n: int, n_splits: int = 5, purge_gap: int = PURGE_GAP
):
    """Yield (train_idx, test_idx) for purged walk-forward CV.

    Each fold: train on [0, train_end), skip purge_gap bars, test on [test_start, test_end).
    Ensures test_idx.min() >= train_idx.max() + purge_gap.
    """
    fold_size = n // (n_splits + 1)
    for i in range(n_splits):
        train_end = fold_size * (i + 1)
        test_start = train_end + purge_gap
        test_end = test_start + fold_size
        if test_end > n:
            break
        train_idx = np.arange(0, train_end)
        test_idx = np.arange(test_start, test_end)
        yield train_idx, test_idx


def _make_sample_weights(y: np.ndarray) -> np.ndarray:
    """Convert integer class labels to per-sample weights."""
    return np.array([CLASS_WEIGHTS[int(v)] for v in y], dtype=float)


def _make_candidates() -> dict:
    """Create fresh model instances for each training run."""
    return {
        "xgboost_cv": xgb.XGBClassifier(
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
            solver="lbfgs",
            random_state=42,
        ),
    }


def _train_one(
    X: np.ndarray,
    y: np.ndarray,
    n_splits: int = 5,
) -> tuple:
    """Train 3 models with purged CV, return (best_model, scaler, cv_f1, model_type, cv_scores)."""
    scaler = StandardScaler()
    candidates = _make_candidates()

    # Map xgboost_cv key → public name
    name_map = {
        "xgboost_cv": "xgboost",
        "random_forest": "random_forest",
        "logistic_regression": "logistic_regression",
    }

    best_name, best_score = "logistic_regression", -1.0
    cv_scores: dict[str, float] = {}

    for internal_name, model in candidates.items():
        fold_scores = []
        # Create fresh model per fold to avoid state bleed (especially for early stopping)
        for train_idx, test_idx in _purged_wf_splits(len(X), n_splits):
            X_tr_raw, X_te_raw = X[train_idx], X[test_idx]
            y_tr, y_te = y[train_idx], y[test_idx]

            sc = StandardScaler()
            X_tr = sc.fit_transform(X_tr_raw)
            X_te = sc.transform(X_te_raw)

            sw = _make_sample_weights(y_tr)

            try:
                if internal_name == "xgboost_cv":
                    # Fresh XGBoost per fold to reset early stopping state
                    fold_model = xgb.XGBClassifier(
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
                    )
                    fold_model.fit(
                        X_tr, y_tr,
                        sample_weight=sw,
                        eval_set=[(X_te, y_te)],
                        verbose=False,
                    )
                else:
                    import copy
                    fold_model = copy.deepcopy(model)
                    fold_model.fit(X_tr, y_tr, sample_weight=sw)
                preds = fold_model.predict(X_te)
                fold_scores.append(
                    f1_score(y_te, preds, average="weighted", zero_division=0)
                )
            except Exception as e:
                log.warning("cv_fold_failed", model=internal_name, error=str(e))
                fold_scores.append(0.0)

        mean_score = float(np.mean(fold_scores)) if fold_scores else 0.0
        public_name = name_map[internal_name]
        cv_scores[public_name] = mean_score
        if mean_score > best_score:
            best_score = mean_score
            best_name = public_name

    # Retrain best model on full data (no early stopping for XGBoost)
    X_scaled = scaler.fit_transform(X)
    sw_full = _make_sample_weights(y)

    if best_name == "xgboost":
        # Fresh XGBClassifier without early_stopping_rounds for final training
        best_model = xgb.XGBClassifier(
            objective="multi:softprob",
            num_class=3,
            n_estimators=300,  # conservative fixed count — no eval_set needed
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_lambda=1.0,
            tree_method="hist",
            random_state=42,
            verbosity=0,
        )
        best_model.fit(X_scaled, y, sample_weight=sw_full, verbose=False)
    elif best_name == "random_forest":
        best_model = candidates["random_forest"]
        best_model.fit(X_scaled, y, sample_weight=sw_full)
    else:
        best_model = candidates["logistic_regression"]
        best_model.fit(X_scaled, y, sample_weight=sw_full)

    return best_model, scaler, best_score, best_name, cv_scores


def _shap_summary(
    model,
    model_type: str,
    X_scaled: np.ndarray,
    feature_names: list[str],
) -> dict:
    """Compute mean absolute SHAP per feature (summed over classes, capped at 500 rows)."""
    try:
        explainer = shap.TreeExplainer(model)
        sv = explainer.shap_values(X_scaled[:500])
        # sv shape: list of (n_samples, n_features) for multi-class, or (n_samples, n_features)
        if isinstance(sv, list):
            sv_abs = np.abs(np.array(sv)).sum(axis=0)  # sum over classes → (n, features)
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
    """Train per-(regime, cluster) classifiers; return results dict keyed by 'regime/cluster'."""
    base = Path(feature_store_path) / f"exchange={exchange}" / f"symbol={symbol}"
    frames = [
        duckdb.execute(f"SELECT * FROM read_parquet('{f}')").df()
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

    # Default regime/cluster when absent
    if "regime" not in df.columns:
        df["regime"] = "RANGING"
    if "cluster" not in df.columns:
        df["cluster"] = 0

    feat_cols = [
        c for c in df.select_dtypes(include="number").columns
        if c not in EXCLUDE_COLS
    ]

    results: dict = {}

    for regime in df["regime"].dropna().unique():
        if regime in SKIP_REGIMES:
            log.info("skip_regime_trending", regime=regime)
            continue

        regime_df = df[df["regime"] == regime]

        for cluster in regime_df["cluster"].dropna().unique():
            if cluster == -1:
                continue  # noise points

            mask = (df["regime"] == regime) & (df["cluster"] == cluster)
            subset = df[mask].copy()
            valid = subset[target].notna() & (subset[target] != "")
            subset = subset[valid]

            if len(subset) < MIN_SAMPLES:
                log.warning(
                    "skip_insufficient_data",
                    regime=regime,
                    cluster=int(cluster),
                    n=len(subset),
                    min_samples=MIN_SAMPLES,
                )
                continue

            X = subset[feat_cols].fillna(0).values
            y = (
                subset[target]
                .map(LABEL_MAP)
                .fillna(1)  # FLAT=1 for unmapped values
                .astype(int)
                .values
            )

            model, fitted_scaler, cv_score, model_type, cv_scores = _train_one(X, y)

            # SHAP on scaled feature matrix
            shap_summary = _shap_summary(
                model, model_type, fitted_scaler.transform(X), feat_cols
            )

            # Persist artifacts
            art_dir = (
                Path(model_registry_path)
                / exchange
                / symbol
                / str(regime)
                / str(int(cluster))
            )
            art_dir.mkdir(parents=True, exist_ok=True)
            with open(art_dir / "model.pkl", "wb") as fh:
                pickle.dump(model, fh)
            with open(art_dir / "scaler.pkl", "wb") as fh:
                pickle.dump(fitted_scaler, fh)

            entry_key = f"{regime}/{cluster}"
            results[entry_key] = {
                "regime": regime,
                "cluster": int(cluster),
                "model_type": model_type,
                "cv_score": round(cv_score, 4),
                "n_samples": len(subset),
                "feature_names": feat_cols,
                "shap_summary": shap_summary,
                "cv_scores_by_model": cv_scores,
                "artifact_dir": str(art_dir),
            }
            log.info(
                "model_trained",
                regime=regime,
                cluster=int(cluster),
                model_type=model_type,
                cv_score=round(cv_score, 4),
            )

    return results
