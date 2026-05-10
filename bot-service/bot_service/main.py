from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
import uvicorn
from fastapi import FastAPI
from pydantic import ValidationError

from bot_service.bus.event_bus import BusManager
from bot_service.config import get_settings, redact_credentials
from bot_service.exchange import ExchangeClient
from bot_service.exchange.bybit.rest import BybitRESTClient
from bot_service.exchange.kucoin.rest import KuCoinRESTClient
from bot_service.persistence.schema import SchemaApplyError, apply_schema
from bot_service.strategy.registry import FileWatcher

# Module-level singletons — set inside lifespan, read by /health endpoint
_bus_manager: BusManager | None = None
_file_watcher: FileWatcher | None = None


def _configure_logging_early() -> None:
    """Minimal console logging before Settings are available."""
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        logger_factory=structlog.PrintLoggerFactory(),
    )
    logging.basicConfig(format="%(message)s", level=logging.INFO)


def _configure_logging(log_level: str) -> None:
    """Production logging: JSON output with credential redaction."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_credentials,  # only installed after Settings loads
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
    )
    logging.basicConfig(format="%(message)s", level=level)
    logging.root.setLevel(level)  # basicConfig is no-op if handlers exist; update level explicitly


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _bus_manager, _file_watcher

    # Step 1: early logging before credentials available
    _configure_logging_early()
    log = structlog.get_logger()

    # Step 2: load Settings
    try:
        settings = get_settings()
    except ValidationError as exc:
        log.critical("settings_validation_failed", error=str(exc))
        sys.exit(1)

    # Step 1 (final): reconfigure with JSON + credential redaction
    _configure_logging(settings.log_level)
    log = structlog.get_logger()
    log.info("bot_service_starting")

    # Step 3: apply QuestDB schema (synchronous)
    try:
        apply_schema(settings.questdb_http_addr)
    except SchemaApplyError as exc:
        log.critical("schema_apply_failed", error=str(exc))
        sys.exit(1)

    # Step 4a: create Bus Manager (not yet started)
    _bus_manager = BusManager()

    # Step 4b: create exchange client and FileWatcher
    # Exchange client is selected based on bot_exchange setting.
    exchange = settings.bot_exchange
    exchange_client: ExchangeClient
    if exchange == "kucoin":
        exchange_client = KuCoinRESTClient()
    elif exchange == "bybit":
        exchange_client = BybitRESTClient()
    else:
        log.critical("unsupported_exchange", exchange=exchange)
        sys.exit(1)

    _file_watcher = FileWatcher(
        bus_manager=_bus_manager,
        exchange_client=exchange_client,
        exchange=exchange,
        settings=settings,
        questdb_http_addr=settings.questdb_http_addr,
        questdb_ilp_addr=settings.questdb_ilp_addr,
    )

    # Step 4c: initial scan — reconcile all strategies, register handles pre-start
    stream_keys = await _file_watcher.initial_scan()
    for key in stream_keys:
        _bus_manager.add_stream(key)
    log.info("file_watcher_initial_scan_complete", strategy_count=len(_file_watcher._loaded))

    # Step 4d: start Bus Manager after reconciliation + strategy registration
    _bus_manager.start()
    log.info("bus_manager_started")

    # Launch file watcher (hot-reload) and watchdog (crash restart) loops
    _watcher_task = asyncio.create_task(_file_watcher.run_loop())
    _watchdog_task = asyncio.create_task(_file_watcher.watch_loop())

    yield  # service is running

    # Teardown: stop background tasks, then all strategy threads
    log.info("bot_service_stopping")
    _watcher_task.cancel()
    try:
        await _watcher_task
    except asyncio.CancelledError:
        pass
    _watchdog_task.cancel()
    try:
        await _watchdog_task
    except asyncio.CancelledError:
        pass

    if _file_watcher is not None:
        _file_watcher.stop_all()

    if _bus_manager is not None:
        _bus_manager.stop()


# Module-level app — required for `uvicorn bot_service.main:app`
app = FastAPI(title="bot-service", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    alive = _bus_manager is not None and _bus_manager.is_alive()
    return {
        "status": "ok" if alive else "degraded",
        "bus_manager": "running" if alive else "dead",
    }


@app.get("/version")
def version() -> dict[str, str]:
    return {
        "version": os.environ.get("BUILD_VERSION", "unknown"),
        "commit": os.environ.get("GIT_COMMIT", "unknown"),
    }


if __name__ == "__main__":
    uvicorn.run("bot_service.main:app", host="0.0.0.0", port=8090, log_config=None)
