"""Tests for data.py aggregation functions (Epic 21 — multi-TF on-demand aggregation)."""
import json
from datetime import datetime, timezone, timedelta

import pytest

from data import (
    _merge_footprint_jsons,
    _merge_single_print_levels,
    _aggregate_rows,
    last_source_ts,
)


# ---------------------------------------------------------------------------
# last_source_ts — live-update cursor advancement
# ---------------------------------------------------------------------------

class TestLastSourceTs:
    def test_direct_tf_returns_unchanged(self):
        ts = "2024-01-01T00:01:00.000000Z"
        assert last_source_ts("1s", ts) == ts
        assert last_source_ts("1m", ts) == ts
        assert last_source_ts("15m", ts) == ts

    def test_2m_advances_by_1m(self):
        # 2m bar open = 00:00 → last source row = 00:01
        result = last_source_ts("2m", "2024-01-01T00:00:00.000000Z")
        assert result == "2024-01-01T00:01:00.000000Z"

    def test_3m_advances_by_2m(self):
        result = last_source_ts("3m", "2024-01-01T00:00:00.000000Z")
        assert result == "2024-01-01T00:02:00.000000Z"

    def test_30m_advances_by_15m(self):
        # 30m bar = 2 × 15m rows; last source row = open + 15m
        result = last_source_ts("30m", "2024-01-01T01:00:00.000000Z")
        assert result == "2024-01-01T01:15:00.000000Z"

    def test_1h_advances_by_45m(self):
        # 1h bar = 4 × 15m rows; last source row = open + 45m
        result = last_source_ts("1h", "2024-01-01T02:00:00.000000Z")
        assert result == "2024-01-01T02:45:00.000000Z"

    def test_unknown_tf_returns_unchanged(self):
        ts = "2024-01-01T00:00:00.000000Z"
        assert last_source_ts("99m", ts) == ts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(ts: str, open_: float, high: float, low: float, close: float,
         volume: float = 1.0, **extra) -> dict:
    return {"ts": ts, "open": open_, "high": high, "low": low,
            "close": close, "volume": volume, **extra}


# Two 1m rows that aggregate into one 2m bar.
_ROW_A = _row("2024-01-01T00:00:00.000000Z", 100, 110, 95, 105, volume=10,
              spread_high=1.0, mid_price_low=97.0, buy_volume=6.0, sell_volume=4.0)
_ROW_B = _row("2024-01-01T00:01:00.000000Z", 105, 115, 100, 112, volume=15,
              spread_high=5.0, mid_price_low=102.0, buy_volume=9.0, sell_volume=6.0)


# ---------------------------------------------------------------------------
# _merge_footprint_jsons
# ---------------------------------------------------------------------------

class TestMergeFootprintJsons:
    def test_sums_b_and_s_per_price(self):
        fp1 = json.dumps({"100.0": {"b": 1.0, "s": 0.5}})
        fp2 = json.dumps({"100.0": {"b": 2.0, "s": 0.3}, "101.0": {"b": 0.0, "s": 1.0}})
        result = json.loads(_merge_footprint_jsons([fp1, fp2]))
        assert result["100.0"]["b"] == pytest.approx(3.0)
        assert result["100.0"]["s"] == pytest.approx(0.8)
        assert result["101.0"]["s"] == pytest.approx(1.0)

    def test_skips_none(self):
        fp = json.dumps({"99.0": {"b": 1.0, "s": 0.0}})
        result = json.loads(_merge_footprint_jsons([None, fp]))
        assert "99.0" in result

    def test_skips_invalid_json(self):
        fp = json.dumps({"99.0": {"b": 1.0, "s": 0.0}})
        result = json.loads(_merge_footprint_jsons(["not_json", fp]))
        assert "99.0" in result

    def test_all_none_returns_none(self):
        assert _merge_footprint_jsons([None, None]) is None

    def test_empty_list_returns_none(self):
        assert _merge_footprint_jsons([]) is None

    def test_missing_b_or_s_defaults_to_zero(self):
        fp = json.dumps({"50.0": {"b": 2.0}})  # no "s"
        result = json.loads(_merge_footprint_jsons([fp]))
        assert result["50.0"]["s"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _merge_single_print_levels
# ---------------------------------------------------------------------------

class TestMergeSinglePrintLevels:
    def test_unions_levels_from_multiple_bars(self):
        spl1 = json.dumps(["100.0", "101.0"])
        spl2 = json.dumps(["101.0", "102.0"])
        result = json.loads(_merge_single_print_levels([spl1, spl2]))
        assert set(result) == {"100.0", "101.0", "102.0"}

    def test_deduplicates_repeated_levels(self):
        spl = json.dumps(["100.0", "100.0"])
        result = json.loads(_merge_single_print_levels([spl, spl]))
        assert result.count("100.0") == 1

    def test_skips_none_entries(self):
        spl = json.dumps(["99.0"])
        result = json.loads(_merge_single_print_levels([None, spl]))
        assert "99.0" in result

    def test_all_none_returns_none(self):
        assert _merge_single_print_levels([None]) is None

    def test_result_is_sorted(self):
        spl = json.dumps(["200.0", "100.0", "150.0"])
        result = json.loads(_merge_single_print_levels([spl]))
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# _aggregate_rows — OHLCV correctness
# ---------------------------------------------------------------------------

class TestAggregateRowsOHLCV:
    def test_open_from_first_close_from_last(self):
        result = _aggregate_rows([_ROW_A, _ROW_B], "2min", "2m")
        assert len(result) == 1
        assert result[0]["open"] == pytest.approx(100.0)
        assert result[0]["close"] == pytest.approx(112.0)

    def test_high_is_max(self):
        result = _aggregate_rows([_ROW_A, _ROW_B], "2min", "2m")
        assert result[0]["high"] == pytest.approx(115.0)

    def test_low_is_min(self):
        result = _aggregate_rows([_ROW_A, _ROW_B], "2min", "2m")
        assert result[0]["low"] == pytest.approx(95.0)

    def test_volume_is_sum(self):
        result = _aggregate_rows([_ROW_A, _ROW_B], "2min", "2m")
        assert result[0]["volume"] == pytest.approx(25.0)

    def test_buy_volume_is_sum(self):
        result = _aggregate_rows([_ROW_A, _ROW_B], "2min", "2m")
        assert result[0]["buy_volume"] == pytest.approx(15.0)

    def test_empty_input_returns_empty(self):
        assert _aggregate_rows([], "2min", "2m") == []


# ---------------------------------------------------------------------------
# P4 fix: spread_high must be max, not first
# ---------------------------------------------------------------------------

class TestAggregateRowsSpreadHigh:
    def test_spread_high_is_max_not_first(self):
        # ROW_A: spread_high=1.0, ROW_B: spread_high=5.0 → max=5.0 (not first=1.0)
        result = _aggregate_rows([_ROW_A, _ROW_B], "2min", "2m")
        assert len(result) == 1
        assert result[0].get("spread_high") == pytest.approx(5.0), (
            "spread_high must be max across merged bars (was incorrectly in first_cols)"
        )

    def test_spread_high_max_regardless_of_row_order(self):
        # Confirm it's not just returning the last value
        result = _aggregate_rows([_ROW_B, _ROW_A], "2min", "2m")
        assert len(result) == 1
        assert result[0].get("spread_high") == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# P5 fix: mid_price_low must be min, not last
# ---------------------------------------------------------------------------

class TestAggregateRowsMidPriceLow:
    def test_mid_price_low_is_min_not_last(self):
        # ROW_A: mid_price_low=97.0, ROW_B: mid_price_low=102.0 → min=97.0 (not last=102.0)
        result = _aggregate_rows([_ROW_A, _ROW_B], "2min", "2m")
        assert len(result) == 1
        assert result[0].get("mid_price_low") == pytest.approx(97.0), (
            "mid_price_low must be min across merged bars (was incorrectly in last_cols)"
        )

    def test_mid_price_low_min_when_later_row_has_lower_value(self):
        row_a = _row("2024-01-01T00:00:00.000000Z", 100, 110, 95, 105, mid_price_low=98.0)
        row_b = _row("2024-01-01T00:01:00.000000Z", 105, 115, 100, 112, mid_price_low=94.0)
        result = _aggregate_rows([row_a, row_b], "2min", "2m")
        assert result[0].get("mid_price_low") == pytest.approx(94.0)


# ---------------------------------------------------------------------------
# P6 fix: weekly TF must use W-MON anchor (not W-SUN pandas default)
# ---------------------------------------------------------------------------

class TestAggregateRowsWeeklyAlignment:
    def test_w_mon_groups_mon_to_sun_as_one_week(self):
        # 2024-01-01 = Monday, 2024-01-07 = Sunday → same W-MON week.
        # 2024-01-08 = Monday → new week.
        rows = [
            _row("2024-01-01T00:00:00.000000Z", 40000, 41000, 39000, 40500, volume=100),
            _row("2024-01-07T00:00:00.000000Z", 40500, 42000, 39500, 41000, volume=120),
            _row("2024-01-08T00:00:00.000000Z", 41000, 43000, 40000, 42000, volume=80),
        ]
        result = _aggregate_rows(rows, "W-MON", "1w")
        assert len(result) == 2, (
            f"W-MON must group Mon–Sun as one week, Mon as next week start; got {len(result)} bars"
        )
        # First week: open=Jan1, close=Jan7
        assert result[0]["open"] == pytest.approx(40000.0)
        assert result[0]["close"] == pytest.approx(41000.0)
        assert result[0]["volume"] == pytest.approx(220.0)
        # Second week: only Jan8
        assert result[1]["open"] == pytest.approx(41000.0)
        assert result[1]["volume"] == pytest.approx(80.0)

    def test_w_sun_would_give_wrong_result(self):
        # Confirm that "W-SUN" (pandas default "1W") splits differently.
        # Under W-SUN: week boundary is Sunday midnight, so Jan7 (Sunday) starts a new week.
        rows = [
            _row("2024-01-01T00:00:00.000000Z", 40000, 41000, 39000, 40500, volume=100),
            _row("2024-01-07T00:00:00.000000Z", 40500, 42000, 39500, 41000, volume=120),
        ]
        result_wrong = _aggregate_rows(rows, "W-SUN", "1w")
        # Under W-SUN these split into 2 bars; under W-MON they'd be 1 bar.
        # This test shows the behaviour differs — use W-MON for Go-compatible weekly bars.
        result_correct = _aggregate_rows(rows, "W-MON", "1w")
        assert len(result_correct) == 1, "W-MON must keep Mon+Sun in the same week"
        assert len(result_wrong) == 2, "W-SUN incorrectly splits Mon–Sun into two weeks"


# ---------------------------------------------------------------------------
# P7 fix: single_print_levels_json must union across merged bars
# ---------------------------------------------------------------------------

class TestAggregateRowsSinglePrintUnion:
    def test_levels_from_all_bars_are_unioned(self):
        rows = [
            _row("2024-01-01T00:00:00.000000Z", 100, 110, 95, 105,
                 single_print_levels_json=json.dumps(["100.0", "101.0"])),
            _row("2024-01-01T00:01:00.000000Z", 105, 115, 100, 112,
                 single_print_levels_json=json.dumps(["102.0"])),
        ]
        result = _aggregate_rows(rows, "2min", "2m")
        spl = json.loads(result[0]["single_print_levels_json"])
        assert set(spl) == {"100.0", "101.0", "102.0"}, (
            "single_print_levels_json must union levels from all merged bars (was 'last')"
        )

    def test_none_single_print_does_not_erase_earlier_levels(self):
        rows = [
            _row("2024-01-01T00:00:00.000000Z", 100, 110, 95, 105,
                 single_print_levels_json=json.dumps(["100.0"])),
            _row("2024-01-01T00:01:00.000000Z", 105, 115, 100, 112,
                 single_print_levels_json=None),
        ]
        result = _aggregate_rows(rows, "2min", "2m")
        spl_raw = result[0].get("single_print_levels_json")
        assert spl_raw is not None
        spl = json.loads(spl_raw)
        assert "100.0" in spl, "None second bar must not erase levels from first bar"


# ---------------------------------------------------------------------------
# Incomplete-window filter
# ---------------------------------------------------------------------------

class TestAggregateRowsIncompleteWindowFilter:
    def test_current_incomplete_bar_is_dropped(self):
        # Align to the current hour boundary so each row's 1h bin is deterministic.
        # Row A: in the previous (complete) 1h window.
        # Row B: at the START of the current (incomplete) 1h window.
        now = datetime.now(timezone.utc)
        hour_floor = now.replace(minute=0, second=0, microsecond=0)
        ts_a = (hour_floor - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        ts_b = hour_floor.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        rows = [
            _row(ts_a, 100, 110, 95, 105),
            _row(ts_b, 105, 115, 100, 112),
        ]
        result = _aggregate_rows(rows, "1h", "1h")
        closes = [r["close"] for r in result if r.get("close") == r.get("close")]
        assert 112.0 not in closes, "bar at current 1h window open must be filtered"
        assert 105.0 in closes, "complete historical bar must be kept"

    def test_complete_historical_bars_are_kept(self):
        now = datetime.now(timezone.utc)
        hour_floor = now.replace(minute=0, second=0, microsecond=0)
        ts_a = (hour_floor - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        ts_b = (hour_floor - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        rows = [
            _row(ts_a, 100, 110, 95, 105, volume=10),
            _row(ts_b, 105, 115, 100, 112, volume=15),
        ]
        result = _aggregate_rows(rows, "1h", "1h")
        assert len(result) == 2
