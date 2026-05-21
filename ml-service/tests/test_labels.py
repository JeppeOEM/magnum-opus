import numpy as np
import pandas as pd
import pytest

from ml_service.labels.generator import (
    _kalman_hedge,
    _revert_label,
    generate_reversion_labels,
)


def test_revert_label_when_deviation_closes():
    """REVERT label when forward deviation is < 50% of current deviation."""
    df = pd.DataFrame(
        {
            "microprice_mid_delta": [5.0, 1.0] + [0.0] * 10,
            "hurst_60": [0.3] * 12,  # mean-reverting
            "ts": pd.date_range("2026-01-01", periods=12, freq="1s"),
        }
    )
    df = generate_reversion_labels(df, horizons=[1])
    assert df["label_revert_1"].iloc[0] == "REVERT"


def test_flat_when_hurst_above_half():
    """No label (FLAT) when hurst_60 >= 0.5 even if spread is extended."""
    df = pd.DataFrame(
        {
            "microprice_mid_delta": [5.0, 0.0] + [0.0] * 10,
            "hurst_60": [0.6] * 12,  # trending — label suppressed
            "ts": pd.date_range("2026-01-01", periods=12, freq="1s"),
        }
    )
    df = generate_reversion_labels(df, horizons=[1])
    assert df["label_revert_1"].iloc[0] == "FLAT"


def test_trend_label_when_deviation_widens():
    """TREND label when forward deviation > 120% of current deviation."""
    df = pd.DataFrame(
        {
            "microprice_mid_delta": [2.0, 5.0] + [0.0] * 10,
            "hurst_60": [0.3] * 12,
            "ts": pd.date_range("2026-01-01", periods=12, freq="1s"),
        }
    )
    df = generate_reversion_labels(df, horizons=[1])
    assert df["label_revert_1"].iloc[0] == "TREND"


def test_kalman_hedge_converges():
    """Kalman filter should converge near true β=2 for a synthetic cointegrated pair."""
    rng = np.random.default_rng(0)
    eth = np.log(np.cumsum(rng.standard_normal(200) * 0.01) + 100)
    btc = 2.0 * eth + rng.standard_normal(200) * 0.001
    beta = _kalman_hedge(btc, eth)
    # After warm-up, beta should converge near 2.0
    assert 1.5 < beta[-1] < 2.5, f"Kalman β converged to {beta[-1]:.3f}, expected ~2.0"


def test_tail_rows_are_flat():
    """Last N rows of each horizon must always be FLAT (no lookahead)."""
    df = pd.DataFrame(
        {
            "microprice_mid_delta": [5.0] * 15,
            "hurst_60": [0.3] * 15,
            "ts": pd.date_range("2026-01-01", periods=15, freq="1s"),
        }
    )
    df = generate_reversion_labels(df, horizons=[10])
    # Last 10 rows should be FLAT (no lookahead)
    assert all(df["label_revert_10"].iloc[-10:] == "FLAT"), (
        "Tail rows should be FLAT: " + str(df["label_revert_10"].tail(10).tolist())
    )


def test_flat_when_delta_below_threshold():
    """Rows with |microprice_mid_delta| < threshold_bps should be FLAT regardless of Hurst."""
    df = pd.DataFrame(
        {
            "microprice_mid_delta": [0.5, 0.0] + [0.0] * 10,  # 0.5 < default 1.0 threshold
            "hurst_60": [0.3] * 12,
            "ts": pd.date_range("2026-01-01", periods=12, freq="1s"),
        }
    )
    df = generate_reversion_labels(df, horizons=[1], threshold_bps=1.0)
    assert df["label_revert_1"].iloc[0] == "FLAT"


def test_both_horizons_generated():
    """Both label_revert_3 and label_revert_10 columns are added in one pass."""
    df = pd.DataFrame(
        {
            "microprice_mid_delta": [5.0] * 20,
            "hurst_60": [0.3] * 20,
            "ts": pd.date_range("2026-01-01", periods=20, freq="1s"),
        }
    )
    df = generate_reversion_labels(df, horizons=[3, 10])
    assert "label_revert_3" in df.columns
    assert "label_revert_10" in df.columns


def test_revert_label_helper_edge_cases():
    """_revert_label returns FLAT when delta is near-zero."""
    assert _revert_label(0.0, 5.0) == "FLAT"
    assert _revert_label(1e-11, 0.0) == "FLAT"
    # Exact boundary: ratio=0.5 is NOT < 0.5, so not REVERT
    assert _revert_label(4.0, 2.0) == "FLAT"
    # Just below 0.5: REVERT
    assert _revert_label(4.0, 1.9) == "REVERT"
    # ratio=1.2 is NOT > 1.2, so not TREND
    assert _revert_label(4.0, 4.8) == "FLAT"
    # Just above 1.2: TREND
    assert _revert_label(4.0, 4.9) == "TREND"
