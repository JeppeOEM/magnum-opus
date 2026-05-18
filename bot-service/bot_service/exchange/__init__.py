from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from bot_service.bus.event_types import OrderFilled
from bot_service.config import get_settings


class ExchangeRESTError(Exception):
    """Raised by REST clients on non-retryable or exhausted-retry HTTP errors.

    The message must never contain raw credential values — truncate response
    bodies and omit headers before passing to this exception.
    """


def _scrub_for_error(text: str) -> str:
    """Replace credential substrings with [REDACTED] for safe error messages."""
    settings = get_settings()
    secrets = [
        settings.kucoin_api_key.get_secret_value(),
        settings.kucoin_api_secret.get_secret_value(),
        settings.kucoin_api_passphrase.get_secret_value(),
        settings.bybit_api_key.get_secret_value(),
        settings.bybit_api_secret.get_secret_value(),
    ]
    result = text
    for secret in secrets:
        if secret and secret in result:
            result = result.replace(secret, "[REDACTED]")
    return result


@dataclass(frozen=True)
class OrderRequest:
    """Value object posted to the order queue by a strategy."""

    strategy: str
    exchange: str           # "kucoin" | "bybit"
    symbol: str             # e.g. "BTCUSDT"
    side: str               # "buy" | "sell"
    order_type: str         # "limit" | "market"
    order_role: str         # "entry" | "exit" | "stop"
    size: float
    limit_price: float | None = None
    stop_price: float | None = None
    take_profit_price: float | None = None
    client_order_id: str = ""
    paper_trading: bool = False


@dataclass(frozen=True)
class PlacedOrder:
    """Returned by place_order() on success."""

    order_id: str
    client_order_id: str
    status: str             # "placed" | "rejected"
    ts_exchange: int        # Unix ms from exchange


@dataclass(frozen=True)
class OpenOrder:
    """One open order returned by get_open_orders()."""

    order_id: str
    symbol: str
    side: str
    order_type: str
    size: float
    limit_price: float | None
    status: str             # "open" | "partially_filled"
    ts_placed: int


class ExchangeClient(Protocol):
    """Structural protocol satisfied by KuCoinRESTClient, BybitRESTClient, PaperExchangeClient."""

    async def place_order(self, req: OrderRequest) -> PlacedOrder: ...
    async def cancel_order(self, order_id: str, symbol: str) -> None: ...
    async def get_open_orders(self, symbol: str) -> list[OpenOrder]: ...
    async def get_recent_fills(self, symbol: str, since_ms: int) -> list[OrderFilled]: ...
