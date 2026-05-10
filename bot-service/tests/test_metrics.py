from __future__ import annotations

import unittest.mock as mock

import pytest
from fastapi.testclient import TestClient

import bot_service.main as main_module
from bot_service.main import app
from bot_service.metrics import prometheus as prom_module


@pytest.fixture(autouse=True)
def reset_bus_manager() -> None:
    yield
    main_module._bus_manager = None


def _client() -> TestClient:
    mock_bm = mock.MagicMock()
    mock_bm.is_alive.return_value = True
    main_module._bus_manager = mock_bm
    return TestClient(app, raise_server_exceptions=False)


# ── /metrics endpoint ─────────────────────────────────────────────────────────

@pytest.mark.l1
def test_metrics_endpoint_returns_200() -> None:
    resp = _client().get("/metrics")
    assert resp.status_code == 200


@pytest.mark.l1
def test_metrics_endpoint_content_type_is_text() -> None:
    resp = _client().get("/metrics")
    assert "text/plain" in resp.headers["content-type"]


@pytest.mark.l1
def test_metrics_endpoint_contains_position_size_after_set() -> None:
    prom_module.set_position_size("TestStrategy", "BTCUSDT", 1.23)
    resp = _client().get("/metrics")
    assert "bot_position_size" in resp.text


@pytest.mark.l1
def test_metrics_endpoint_contains_unrealized_pnl_after_set() -> None:
    prom_module.set_unrealized_pnl("TestStrategy", "BTCUSDT", 50.0)
    resp = _client().get("/metrics")
    assert "bot_unrealized_pnl" in resp.text


@pytest.mark.l1
def test_metrics_endpoint_contains_drawdown_after_set() -> None:
    prom_module.set_drawdown("TestStrategy", 0.05)
    resp = _client().get("/metrics")
    assert "bot_drawdown" in resp.text


@pytest.mark.l1
def test_metrics_endpoint_contains_consumer_lag_after_set() -> None:
    prom_module.set_consumer_lag("TestStrategy", 10.0)
    resp = _client().get("/metrics")
    assert "bot_consumer_lag" in resp.text


# ── reset_strategy_gauges ─────────────────────────────────────────────────────

@pytest.mark.l1
def test_reset_strategy_gauges_zeroes_position_size() -> None:
    prom_module.set_position_size("DeadBot", "BTCUSDT", 9999.0)
    prom_module.reset_strategy_gauges("DeadBot")
    resp = _client().get("/metrics")
    # After reset the label combination should exist at 0.0
    assert 'bot_position_size{strategy="DeadBot"' in resp.text
    # Confirm value is 0 not 9999
    for line in resp.text.splitlines():
        if 'bot_position_size{' in line and 'DeadBot' in line and not line.startswith("#"):
            assert float(line.split()[-1]) == 0.0


@pytest.mark.l1
def test_reset_strategy_gauges_zeroes_drawdown() -> None:
    prom_module.set_drawdown("DeadBot2", 0.99)
    prom_module.reset_strategy_gauges("DeadBot2")
    resp = _client().get("/metrics")
    for line in resp.text.splitlines():
        if 'bot_drawdown{' in line and 'DeadBot2' in line and not line.startswith("#"):
            assert float(line.split()[-1]) == 0.0


@pytest.mark.l1
def test_reset_strategy_gauges_is_noop_when_gauges_not_initialised() -> None:
    # Calling reset before any set_* calls should not raise
    prom_module.reset_strategy_gauges("NeverSeenStrategy")


# ── order lifecycle counters ──────────────────────────────────────────────────

@pytest.mark.l1
def test_metrics_contains_order_placed_counter() -> None:
    prom_module.inc_order_placed("S1", "kucoin", "BTCUSDT", "buy")
    resp = _client().get("/metrics")
    assert "bot_order_placed_total" in resp.text


@pytest.mark.l1
def test_metrics_contains_order_filled_counter() -> None:
    prom_module.inc_order_filled("S1", "kucoin", "BTCUSDT", "buy")
    resp = _client().get("/metrics")
    assert "bot_order_filled_total" in resp.text


@pytest.mark.l1
def test_metrics_contains_order_rejected_counter() -> None:
    prom_module.inc_order_rejected("S1", "kucoin", "BTCUSDT")
    resp = _client().get("/metrics")
    assert "bot_order_rejected_total" in resp.text


@pytest.mark.l1
def test_metrics_contains_execution_latency_histogram() -> None:
    prom_module.observe_order_execution_latency_ms("S1", "kucoin", 42.0)
    resp = _client().get("/metrics")
    assert "bot_order_execution_latency_ms" in resp.text
