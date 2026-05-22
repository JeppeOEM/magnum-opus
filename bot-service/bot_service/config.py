from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from pydantic import SecretStr
from pydantic_settings import BaseSettings
from structlog.types import EventDict, WrappedLogger


class Settings(BaseSettings):
    # Exchange credentials — optional; empty defaults allow paper-trading without any API keys.
    # Set real values only when switching a strategy to live trading.
    kucoin_api_key: SecretStr = SecretStr("")
    kucoin_api_secret: SecretStr = SecretStr("")
    kucoin_api_passphrase: SecretStr = SecretStr("")
    bybit_api_key: SecretStr = SecretStr("")
    bybit_api_secret: SecretStr = SecretStr("")

    # Infrastructure
    redis_url: str = "redis://localhost:6379"
    questdb_ilp_addr: str = "localhost:9009"
    questdb_http_addr: str = "http://localhost:9000"

    # Bus Manager
    bot_consumer_group: str = "bot-service"
    bot_queue_max_depth: int = 1000

    # Subscribe / Lifecycle
    bot_subscribe_timeout_s: int = 30
    bot_reconciliation_timeout_s: int = 120
    bot_shutdown_timeout_s: int = 30
    bot_filewatcher_interval_s: int = 60
    bot_strategies_dir: str = "strategies/active"
    bot_strategies_inactive_dir: str = "strategies/inactive"
    bot_exchange: str = "bybit"

    # WebSocket fallback
    bot_ws_fallback_timeout_live: int = 10
    bot_ws_fallback_timeout_paper: int = 30

    # Barrier timeouts (ms)
    bot_barrier_timeout_ms_1s: int = 250
    bot_barrier_timeout_ms_1m: int = 500
    bot_barrier_timeout_ms_5m: int = 1000
    bot_barrier_timeout_ms_15m: int = 2000
    bot_barrier_timeout_ms_1h: int = 5000
    bot_barrier_timeout_ms_4h: int = 10000
    bot_barrier_timeout_ms_1d: int = 30000
    bot_barrier_timeout_ms_1w: int = 60000

    # Risk management
    bot_portfolio_value_usd: float = 10000.0
    daily_loss_limit_usd: float = 0.0
    max_order_notional_usd: float = 0.0

    # Paper trading
    bot_paper_latency_min_ms: int = 50
    bot_paper_latency_max_ms: int = 250
    bot_paper_slippage_bps: int = 5

    # Funding rate poller
    bot_funding_poll_interval_s: int = 60
    bot_funding_symbols: str = ""  # comma-separated "exchange:symbol" pairs, e.g. "bybit:BTCUSDT,kucoin:XBTUSDM"

    # Logging
    log_level: str = "info"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # pydantic-settings resolves fields from env


_REDIS_URL_PATTERN: re.Pattern[str] = re.compile(r"redis://:([^@]+)@")


def redact_credentials(
    logger: WrappedLogger,
    method: str,
    event_dict: EventDict,
) -> EventDict:
    """Replace credential values with [REDACTED] in all string fields.

    Handles values embedded in longer strings (e.g. exception tracebacks),
    not just exact top-level field matches. Called inside the structlog
    processor chain before any sink.
    """
    settings = get_settings()
    secrets = [
        settings.kucoin_api_key.get_secret_value(),
        settings.kucoin_api_secret.get_secret_value(),
        settings.kucoin_api_passphrase.get_secret_value(),
        settings.bybit_api_key.get_secret_value(),
        settings.bybit_api_secret.get_secret_value(),
    ]

    def _scrub(value: Any) -> Any:  # Any: structlog event dict values are untyped at this boundary
        if isinstance(value, str):
            for secret in secrets:
                if secret and secret in value:
                    value = value.replace(secret, "[REDACTED]")
            value = _REDIS_URL_PATTERN.sub("redis://:[REDACTED]@", value)
        elif isinstance(value, dict):
            return {k: _scrub(v) for k, v in value.items()}
        elif isinstance(value, (list, tuple)):
            return type(value)(_scrub(v) for v in value)
        return value

    return {k: _scrub(v) for k, v in event_dict.items()}
