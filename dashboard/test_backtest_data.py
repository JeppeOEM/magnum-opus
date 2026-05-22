"""Tests for backtest_data.py — dashboard data layer helpers."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import backtest_data


# ── fetch_available_symbols ───────────────────────────────────────────────────

class TestFetchAvailableSymbols:
    def test_success_returns_list(self) -> None:
        """fetch_available_symbols returns parsed symbol list on success."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = [
            {"exchange": "bybit", "symbol": "BTCUSDT", "min_ts": "2026-01-01T00:00:00Z",
             "max_ts": "2026-05-01T00:00:00Z", "row_count": 11232000},
            {"exchange": "bybit", "symbol": "ETHUSDT", "min_ts": "2026-01-01T00:00:00Z",
             "max_ts": "2026-05-01T00:00:00Z", "row_count": 11200000},
        ]
        with patch("backtest_data.requests.get", return_value=mock_resp):
            result = backtest_data.fetch_available_symbols(exchange="bybit")
        assert len(result) == 2
        assert result[0]["symbol"] == "BTCUSDT"
        assert result[1]["symbol"] == "ETHUSDT"
        assert result[0]["row_count"] == 11232000

    def test_success_no_exchange_filter(self) -> None:
        """fetch_available_symbols called without exchange sends no exchange param."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = []
        captured_params: list[dict] = []

        def _capture(url: str, **kwargs: object) -> MagicMock:
            captured_params.append(kwargs.get("params", {}))
            return mock_resp

        with patch("backtest_data.requests.get", side_effect=_capture):
            backtest_data.fetch_available_symbols()

        assert len(captured_params) == 1
        assert "exchange" not in captured_params[0]

    def test_exchange_param_forwarded(self) -> None:
        """fetch_available_symbols passes exchange as query param."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = []
        captured_params: list[dict] = []

        def _capture(url: str, **kwargs: object) -> MagicMock:
            captured_params.append(kwargs.get("params", {}))
            return mock_resp

        with patch("backtest_data.requests.get", side_effect=_capture):
            backtest_data.fetch_available_symbols(exchange="kucoin")

        assert captured_params[0].get("exchange") == "kucoin"

    def test_failure_returns_empty_list(self) -> None:
        """fetch_available_symbols returns [] on network error, does not raise."""
        with patch("backtest_data.requests.get", side_effect=Exception("connection refused")):
            result = backtest_data.fetch_available_symbols(exchange="bybit")
        assert result == []

    def test_http_error_returns_empty_list(self) -> None:
        """fetch_available_symbols returns [] when raise_for_status raises."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("404 Not Found")
        with patch("backtest_data.requests.get", return_value=mock_resp):
            result = backtest_data.fetch_available_symbols()
        assert result == []
