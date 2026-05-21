"""L1 unit tests for FundingRateArbBot signal and dispatch."""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest

from bot_service.bus.event_types import FundingRate
from bot_service.strategy.signals.funding_rate_arb import funding_rate_arb_signal


# ---------------------------------------------------------------------------
# funding_rate_arb_signal
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_signal_positive_rate_above_threshold_returns_sell() -> None:
    result = funding_rate_arb_signal(funding_rate=0.002, threshold_bps=10.0)
    assert result.action == "sell"
    assert result.reason == "high_positive_funding"
    assert result.confidence > 0.0


@pytest.mark.l1
def test_signal_negative_rate_below_threshold_returns_buy() -> None:
    result = funding_rate_arb_signal(funding_rate=-0.002, threshold_bps=10.0)
    assert result.action == "buy"
    assert result.reason == "high_negative_funding"
    assert result.confidence > 0.0


@pytest.mark.l1
def test_signal_zero_rate_returns_hold() -> None:
    result = funding_rate_arb_signal(funding_rate=0.0, threshold_bps=10.0)
    assert result.action == "hold"
    assert result.reason == "rate_below_threshold"
    assert result.confidence == 0.0


@pytest.mark.l1
def test_signal_rate_exactly_at_threshold_returns_hold() -> None:
    threshold = 10.0 / 10_000  # 0.001
    # Exactly at threshold: not strictly greater → hold
    result = funding_rate_arb_signal(funding_rate=threshold, threshold_bps=10.0)
    assert result.action == "hold"


@pytest.mark.l1
def test_signal_rate_just_above_threshold_returns_sell() -> None:
    threshold = 10.0 / 10_000  # 0.001
    result = funding_rate_arb_signal(funding_rate=threshold + 1e-9, threshold_bps=10.0)
    assert result.action == "sell"


@pytest.mark.l1
def test_signal_confidence_capped_at_1() -> None:
    result = funding_rate_arb_signal(funding_rate=999.0, threshold_bps=10.0)
    assert result.action == "sell"
    assert result.confidence == pytest.approx(1.0)


@pytest.mark.l1
def test_signal_custom_threshold() -> None:
    result = funding_rate_arb_signal(funding_rate=0.001, threshold_bps=20.0)
    assert result.action == "hold"
    result2 = funding_rate_arb_signal(funding_rate=0.003, threshold_bps=20.0)
    assert result2.action == "sell"


# ---------------------------------------------------------------------------
# BaseStrategy.register_funding_rate_handler
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_register_funding_rate_handler_adds_stream_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """register_funding_rate_handler populates _funding_stream_keys."""
    monkeypatch.setenv("BOT_EXCHANGE", "bybit")
    from bot_service.config import get_settings
    settings = get_settings()
    from strategies.active.funding_rate_arb_bot import FundingRateArbBot
    bot = FundingRateArbBot(name="test", settings=settings)
    bot.subscribe()
    assert "funding:bybit:BTCUSDT" in bot._funding_stream_keys


# ---------------------------------------------------------------------------
# handle_funding_rate dispatch
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_handle_funding_rate_posts_order_on_high_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    """handle_funding_rate calls order_worker.post on above-threshold rate."""
    monkeypatch.setenv("BOT_EXCHANGE", "bybit")
    from bot_service.config import get_settings
    settings = get_settings()
    from strategies.active.funding_rate_arb_bot import FundingRateArbBot
    bot = FundingRateArbBot(name="test", settings=settings)
    bot._exchange = "bybit"

    posted: list = []
    mock_worker = MagicMock()
    mock_worker.post = lambda req: posted.append(req)
    bot._order_worker = mock_worker

    event = FundingRate(
        exchange="bybit", symbol="BTCUSDT",
        ts=1748000000000, funding_rate=0.002, next_funding_ts=1748028800000,
    )
    bot.handle_funding_rate(event)
    assert len(posted) == 1
    assert posted[0].side == "sell"


@pytest.mark.l1
def test_handle_funding_rate_hold_posts_no_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """handle_funding_rate does not post when rate is below threshold."""
    monkeypatch.setenv("BOT_EXCHANGE", "bybit")
    from bot_service.config import get_settings
    settings = get_settings()
    from strategies.active.funding_rate_arb_bot import FundingRateArbBot
    bot = FundingRateArbBot(name="test", settings=settings)
    bot._exchange = "bybit"

    posted: list = []
    mock_worker = MagicMock()
    mock_worker.post = lambda req: posted.append(req)
    bot._order_worker = mock_worker

    event = FundingRate(
        exchange="bybit", symbol="BTCUSDT",
        ts=1748000000000, funding_rate=0.0, next_funding_ts=1748028800000,
    )
    bot.handle_funding_rate(event)
    assert posted == []


# ---------------------------------------------------------------------------
# run_strategy_event_loop dispatches FundingRate
# ---------------------------------------------------------------------------


@pytest.mark.l1
@pytest.mark.asyncio
async def test_event_loop_dispatches_funding_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_strategy_event_loop routes FundingRate to handle_funding_rate."""
    monkeypatch.setenv("BOT_EXCHANGE", "bybit")
    from bot_service.config import get_settings
    settings = get_settings()
    from strategies.active.funding_rate_arb_bot import FundingRateArbBot
    from bot_service.strategy.registry import run_strategy_event_loop

    bot = FundingRateArbBot(name="dispatch_test", settings=settings)
    bot._exchange = "bybit"

    received: list[FundingRate] = []
    stop_event = threading.Event()

    def _handle(e: FundingRate) -> None:
        received.append(e)
        stop_event.set()  # exit the loop after the first event is processed

    bot.handle_funding_rate = _handle  # type: ignore[method-assign]

    queue: asyncio.Queue = asyncio.Queue()
    ready_event = threading.Event()

    event = FundingRate(
        exchange="bybit", symbol="BTCUSDT",
        ts=1748000000000, funding_rate=0.002, next_funding_ts=1748028800000,
    )
    await queue.put(event)

    async def _run() -> None:
        with patch.object(bot, "run_subscribe"):
            with patch.object(bot, "start_heartbeat"):
                ready_event.set()
                try:
                    await asyncio.wait_for(
                        run_strategy_event_loop(bot, queue, stop_event, ready_event),
                        timeout=0.5,
                    )
                except asyncio.TimeoutError:
                    pass

    await _run()
    assert len(received) == 1
    assert received[0].funding_rate == pytest.approx(0.002)
