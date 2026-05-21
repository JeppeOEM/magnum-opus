import numpy as np
import pandas as pd

from ml_service.features.extractor import (
    _add_rolling_features,
    _hurst,
    _ou_halflife,
)


def test_hurst_random_walk_near_half():
    rng = np.random.default_rng(42)
    rw = np.cumsum(rng.standard_normal(200))
    h = _hurst(np.diff(rw))
    assert 0.3 < h < 0.7, f"random walk Hurst should be ~0.5, got {h:.3f}"


def test_hurst_mean_reverting_below_half():
    # AR(1) with strong negative autocorrelation → H < 0.5
    rng = np.random.default_rng(7)
    x = np.zeros(200)
    for i in range(1, 200):
        x[i] = -0.7 * x[i - 1] + rng.standard_normal() * 0.1
    h = _hurst(x)
    assert h < 0.5, f"mean-reverting AR(-0.7) should have H < 0.5, got {h:.3f}"


def test_ou_halflife_formula():
    # AR(1) ϕ=0.9 → halflife = -ln(2)/ln(0.9) ≈ 6.58
    rng = np.random.default_rng(13)
    x = np.zeros(200)
    for i in range(1, 200):
        x[i] = 0.9 * x[i - 1] + rng.standard_normal() * 0.01
    hl = _ou_halflife(x)
    assert 4.0 < hl < 12.0, f"AR(1) ϕ=0.9 half-life should be ~6.6, got {hl:.2f}"


def _make_df(n: int = 100) -> pd.DataFrame:
    rng = np.random.default_rng(99)
    return pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01", periods=n, freq="1s"),
            "best_bid": 100.0,
            "best_ask": 100.1,
            "ofi_l1": rng.standard_normal(n) * 10,
            "spread_mean": 0.1,
            "volume": 1.0,
            "buy_volume": 0.5,
            "bid_depth_l1_close": 10.0,
            "ask_depth_l1_close": 10.0,
        }
    )


def test_add_rolling_features_columns_present():
    df = _add_rolling_features(_make_df())
    expected_cols = [
        "ofi_sg", "spread_sg", "net_buy_fraction", "depth_imbalance_l1",
        "hurst_20", "hurst_60", "ou_halflife", "ofi_x_spread",
    ]
    for col in expected_cols:
        assert col in df.columns, f"missing column: {col}"


def test_savitzky_golay_reduces_variance():
    rng = np.random.default_rng(55)
    df = pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01", periods=100, freq="1s"),
            "best_bid": 100.0,
            "best_ask": 100.1,
            "ofi_l1": rng.standard_normal(100) * 100,
            "spread_mean": rng.standard_normal(100) * 0.01 + 0.1,
            "volume": 1.0,
            "buy_volume": 0.5,
            "bid_depth_l1_close": 10.0,
            "ask_depth_l1_close": 10.0,
        }
    )
    df = _add_rolling_features(df)
    assert df["ofi_sg"].std() < df["ofi_l1"].std(), "SG filter should reduce OFI variance"
