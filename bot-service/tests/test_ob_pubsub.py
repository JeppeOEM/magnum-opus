"""L1 tests for orderbook pub/sub provisioning."""
from __future__ import annotations

import structlog.testing
from typing import Any

import pytest

from bot_service.bus.event_bus import BusManager
from bot_service.strategy.base import BaseStrategy


def _noop_ob(payload: dict[str, Any]) -> None: ...
def _noop_c1s(payload: dict[str, Any]) -> None: ...


@pytest.mark.l1
def test_provision_pubsub_full_stream_registers_ob_callback() -> None:
    mgr = BusManager()
    mgr.provision_pubsub("s1", "full_stream", _noop_ob, _noop_c1s)
    assert "s1" in mgr._pubsub_ob_callbacks
    assert "s1" not in mgr._pubsub_c1s_callbacks


@pytest.mark.l1
def test_provision_pubsub_none_registers_no_callbacks() -> None:
    mgr = BusManager()
    mgr.provision_pubsub("s1", "none", _noop_ob, _noop_c1s)
    assert "s1" not in mgr._pubsub_ob_callbacks
    assert "s1" not in mgr._pubsub_c1s_callbacks


@pytest.mark.l1
def test_base_strategy_invalid_mode_raises() -> None:
    class BadStrategy(BaseStrategy):
        @property
        def min_lookback(self) -> int: return 1
        @property
        def max_position_pct(self) -> float: return 0.1
        @property
        def stop_loss_pct(self) -> float: return 0.05
        @property
        def paper_trading(self) -> bool: return True
        @property
        def bus_timeout_seconds(self) -> int: return 30
        @property
        def close_on_bus_timeout(self) -> bool: return False
        @property
        def orderbook_mode(self) -> str: return "invalid_mode"
        def subscribe(self) -> None: ...

    from bot_service.config import Settings
    with pytest.raises(ValueError, match="invalid_mode"):
        BadStrategy(name="bad", settings=Settings())


@pytest.mark.l1
def test_dispatch_pubsub_malformed_json_logs_error_does_not_raise() -> None:
    mgr = BusManager()
    received: list[dict[str, Any]] = []
    mgr.provision_pubsub("s1", "full_stream", received.append, _noop_c1s)

    with structlog.testing.capture_logs() as log_entries:
        mgr._dispatch_pubsub("orderbook:kucoin:BTCUSDT", b"not json {{{")

    assert any(
        e.get("event") == "pubsub_json_parse_error" and e.get("log_level") == "error"
        for e in log_entries
    )
    assert received == []
