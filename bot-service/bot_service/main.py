from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Response
from prometheus_client.exposition import generate_latest
from pydantic import BaseModel, ValidationError

from bot_service.bus.event_bus import BusManager
from bot_service.config import get_settings, redact_credentials
from bot_service.exchange import ExchangeClient
from bot_service.exchange.bybit.rest import BybitRESTClient
from bot_service.exchange.funding_poller import FundingRatePoller
from bot_service.exchange.kucoin.rest import KuCoinRESTClient
from bot_service.metrics.prometheus import get_registry
from bot_service.persistence.schema import SchemaApplyError, apply_schema
from bot_service.backtest.runner import BacktestResult, hash_file, run_backtest
from bot_service.strategy.circuit_breaker import DailyLossCircuitBreaker
from bot_service.strategy.registry import FileWatcher

# Module-level singletons — set inside lifespan, read by /health endpoint
_bus_manager: BusManager | None = None
_file_watcher: FileWatcher | None = None
_circuit_breaker: DailyLossCircuitBreaker | None = None

# Backtest async task tracking
_IDENT_RE = re.compile(r"^[A-Za-z0-9_\-\.]+$")
_BACKTEST_RESULTS_MAX = 500
_backtest_tasks: dict[str, asyncio.Task[None]] = {}
_backtest_results: OrderedDict[str, dict[str, Any]] = OrderedDict()


def _put_result(run_id: str, data: dict[str, Any]) -> None:
    _backtest_results[run_id] = data
    while len(_backtest_results) > _BACKTEST_RESULTS_MAX:
        _backtest_results.popitem(last=False)


def _parse_funding_symbols(raw: str) -> list[tuple[str, str]]:
    """Parse "bybit:BTCUSDT,kucoin:XBTUSDM" → [("bybit", "BTCUSDT"), ("kucoin", "XBTUSDM")]."""
    result = []
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        exchange, symbol = part.split(":", 1)
        exchange, symbol = exchange.strip(), symbol.strip()
        if not exchange or not symbol:
            continue
        result.append((exchange, symbol))
    return result

log = structlog.get_logger()


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
    global _bus_manager, _file_watcher, _circuit_breaker

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

    # Step 4a2: create circuit breaker (disabled when daily_loss_limit_usd == 0)
    _circuit_breaker = DailyLossCircuitBreaker(settings.daily_loss_limit_usd)

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

    def _on_circuit_breaker_trip() -> None:
        if _file_watcher is not None:
            _file_watcher.stop_all()

    _file_watcher = FileWatcher(
        bus_manager=_bus_manager,
        exchange_client=exchange_client,
        exchange=exchange,
        settings=settings,
        questdb_http_addr=settings.questdb_http_addr,
        questdb_ilp_addr=settings.questdb_ilp_addr,
        circuit_breaker=_circuit_breaker,
        on_circuit_breaker_trip=_on_circuit_breaker_trip,
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

    # Step 4e: start funding rate poller (opt-in; disabled when symbols empty)
    _poller_task: asyncio.Task[None] | None = None
    _funding_symbols = _parse_funding_symbols(settings.bot_funding_symbols)
    if _funding_symbols:
        poller = FundingRatePoller(
            redis_url=settings.redis_url,
            symbols=_funding_symbols,
            poll_interval_s=settings.bot_funding_poll_interval_s,
        )
        _poller_task = asyncio.create_task(poller.run())
        log.info("funding_rate_poller_started", symbols=_funding_symbols)

    yield  # service is running

    # Teardown: stop background tasks, then all strategy threads
    log.info("bot_service_stopping")
    if _poller_task is not None:
        _poller_task.cancel()
        try:
            await _poller_task
        except (asyncio.CancelledError, Exception):
            pass
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
def health() -> dict[str, object]:
    bus_alive = _bus_manager is not None and _bus_manager.is_alive()
    strategies: dict[str, str] = (
        _file_watcher.get_strategy_statuses() if _file_watcher is not None else {}
    )
    cb_tripped = _circuit_breaker is not None and _circuit_breaker.is_tripped()
    all_running = not strategies or all(v == "running" for v in strategies.values())
    ok = bus_alive and all_running and not cb_tripped
    return {
        "status": "ok" if ok else "degraded",
        "bus_manager": "running" if bus_alive else "dead",
        "strategies": strategies,
        "circuit_breaker_tripped": cb_tripped,
    }


@app.post("/stop-all")
def stop_all() -> dict[str, str]:
    if _file_watcher is not None:
        _file_watcher.stop_all()
    return {"status": "stopped"}


@app.get("/metrics")
def metrics() -> Response:
    return Response(
        generate_latest(get_registry()),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/version")
def version() -> dict[str, str]:
    return {
        "version": os.environ.get("BUILD_VERSION", "unknown"),
        "commit": os.environ.get("GIT_COMMIT", "unknown"),
    }


# ── Backtest API ──────────────────────────────────────────────────────────────

class BacktestRunRequest(BaseModel):
    strategy_name: str
    symbol: str
    tf: str = "1s"
    exchange: str = "bybit"
    start_date: str
    end_date: str
    initial_capital: float = 10_000.0
    sample_every: int = 60


def _ilp_write_snapshot(questdb_ilp_addr: str, result: BacktestResult, code: str) -> None:
    from questdb.ingress import Sender, TimestampNanos
    host, port_str = questdb_ilp_addr.split(":")
    ts_ns = TimestampNanos(int(time.time() * 1e9))
    with Sender.from_conf(f"tcp::addr={host}:{port_str};") as sender:
        sender.row(
            "strategy_snapshots",
            symbols={"strategy_name": result.strategy_name, "hash": result.hash},
            columns={"code": code},
            at=ts_ns,
        )
        sender.flush()


def _ilp_write_run(questdb_ilp_addr: str, result: BacktestResult) -> None:
    from questdb.ingress import Sender, TimestampNanos
    host, port_str = questdb_ilp_addr.split(":")
    ts_ns = TimestampNanos(int(time.time() * 1e9))
    with Sender.from_conf(f"tcp::addr={host}:{port_str};") as sender:
        sender.row(
            "backtest_runs",
            symbols={
                "run_id": result.run_id,
                "strategy_name": result.strategy_name,
                "hash": result.hash,
                "symbol": result.symbol,
                "tf": result.tf,
                "exchange": result.exchange,
                "start_date": result.start_date,
                "end_date": result.end_date,
            },
            columns={
                "initial_capital": result.initial_capital,
                "final_value": result.final_value,
                "total_return_pct": result.total_return_pct,
                "sharpe_ratio": result.sharpe_ratio,
                "max_drawdown_pct": result.max_drawdown_pct,
                "n_trades": result.n_trades,
                "win_rate_pct": result.win_rate_pct,
                "avg_pnl_per_trade": result.avg_pnl_per_trade,
                "total_fees_usd": result.total_fees_usd,
                "passes_fee_gate": result.passes_fee_gate,
            },
            at=ts_ns,
        )
        sender.flush()


def _ilp_write_equity(questdb_ilp_addr: str, result: BacktestResult) -> None:
    from questdb.ingress import Sender, TimestampNanos
    from datetime import datetime, timezone
    host, port_str = questdb_ilp_addr.split(":")
    run_at_int = int(time.time() * 1e9)
    with Sender.from_conf(f"tcp::addr={host}:{port_str};") as sender:
        for iso_ts, value in result.equity_curve:
            dt = datetime.fromisoformat(iso_ts).replace(tzinfo=timezone.utc)
            bar_ns = TimestampNanos(int(dt.timestamp() * 1e9))
            sender.row(
                "backtest_equity",
                symbols={"run_id": result.run_id, "strategy_name": result.strategy_name},
                columns={"portfolio_value": value, "run_at": run_at_int},
                at=bar_ns,
            )
        sender.flush()


def _snapshot_exists(questdb_http_addr: str, strategy_name: str, file_hash: str) -> bool:
    try:
        resp = httpx.get(
            f"{questdb_http_addr}/exec",
            params={
                "query": (
                    f"SELECT count() FROM strategy_snapshots "
                    f"WHERE strategy_name = '{strategy_name}' AND hash = '{file_hash}'"
                )
            },
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("dataset", [])
        return bool(rows and rows[0][0] > 0)
    except Exception:
        return False


async def _run_backtest_task(
    run_id: str, req: BacktestRunRequest, path: Path, file_bytes: bytes
) -> None:
    settings = get_settings()
    try:
        result = await asyncio.to_thread(
            run_backtest,
            path=path,
            symbol=req.symbol,
            tf=req.tf,
            exchange=req.exchange,
            start_date=req.start_date,
            end_date=req.end_date,
            initial_capital=req.initial_capital,
            questdb_http_addr=settings.questdb_http_addr,
            questdb_ilp_addr=settings.questdb_ilp_addr,
            sample_every=req.sample_every,
        )
        code = file_bytes.decode("utf-8")
        if not _snapshot_exists(settings.questdb_http_addr, result.strategy_name, result.hash):
            await asyncio.to_thread(_ilp_write_snapshot, settings.questdb_ilp_addr, result, code)
        await asyncio.to_thread(_ilp_write_run, settings.questdb_ilp_addr, result)
        if result.equity_curve:
            await asyncio.to_thread(_ilp_write_equity, settings.questdb_ilp_addr, result)
        from dataclasses import asdict
        metrics = asdict(result)
        metrics.pop("equity_curve")
        metrics["equity_curve_points"] = len(result.equity_curve)
        _put_result(run_id, {"status": "done", "result": metrics})
    except Exception as exc:
        log.error("backtest_task_failed", run_id=run_id, error=str(exc))
        _put_result(run_id, {"status": "failed", "error": str(exc)})
    finally:
        _backtest_tasks.pop(run_id, None)


@app.get("/strategies/detail")
def strategies_detail() -> list[dict[str, object]]:
    """Return live metadata for every loaded strategy (status, uptime, config params).

    Used by the dashboard to display running bots even before any trades occur.
    """
    if _file_watcher is None:
        return []
    return _file_watcher.get_strategy_details()


@app.get("/strategies")
def list_strategies() -> list[dict[str, str]]:
    settings = get_settings()
    strategies_dir = Path(settings.bot_strategies_dir)
    if not strategies_dir.is_dir():
        return []
    result = []
    for py_file in sorted(strategies_dir.glob("*.py")):
        try:
            code = py_file.read_text(encoding="utf-8")
            file_hash = hash_file(py_file)
            result.append({"name": py_file.stem, "hash": file_hash, "code": code})
        except Exception:
            pass
    return result


@app.post("/backtest/run")
async def backtest_run(req: BacktestRunRequest) -> dict[str, str]:
    import uuid
    settings = get_settings()
    strategies_dir = Path(settings.bot_strategies_dir).resolve()
    path = (strategies_dir / f"{req.strategy_name}.py").resolve()
    try:
        path.relative_to(strategies_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid strategy name")
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Strategy not found: {req.strategy_name}")
    try:
        file_bytes = path.read_bytes()
    except OSError:
        raise HTTPException(status_code=404, detail=f"Strategy not found: {req.strategy_name}")
    run_id = str(uuid.uuid4())
    _put_result(run_id, {"status": "running"})
    task = asyncio.create_task(_run_backtest_task(run_id, req, path, file_bytes))
    _backtest_tasks[run_id] = task
    return {"run_id": run_id, "status": "running"}


@app.get("/backtest/run/{run_id}")
def backtest_status(run_id: str) -> dict[str, Any]:
    result = _backtest_results.get(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return {"run_id": run_id, **result}


@app.get("/backtest/runs")
def backtest_runs(
    strategy_name: str | None = Query(default=None),
    hash: str | None = Query(default=None),
    limit: int = Query(default=100, le=1000),
) -> list[dict[str, Any]]:
    settings = get_settings()
    where_clauses = []
    if strategy_name:
        if not _IDENT_RE.match(strategy_name):
            raise HTTPException(status_code=400, detail="Invalid strategy_name")
        where_clauses.append(f"strategy_name = '{strategy_name}'")
    if hash:
        if not _IDENT_RE.match(hash):
            raise HTTPException(status_code=400, detail="Invalid hash")
        where_clauses.append(f"hash = '{hash}'")
    where = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    query = f"SELECT * FROM backtest_runs{where} ORDER BY run_at DESC LIMIT {limit}"
    try:
        resp = httpx.get(
            f"{settings.questdb_http_addr}/exec",
            params={"query": query},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        cols = [c["name"] for c in data.get("columns", [])]
        return [dict(zip(cols, row)) for row in data.get("dataset", [])]
    except Exception as exc:
        log.warning("backtest_runs_query_failed", error=str(exc))
        return []


# ── Validation REST API ────────────────────────────────────────────────────────

_VALIDATE_RESULTS_MAX = 50
_validate_tasks: dict[str, asyncio.Task[None]] = {}
_validate_results: OrderedDict[str, dict[str, Any]] = OrderedDict()


def _put_validate_result(run_id: str, data: dict[str, Any]) -> None:
    _validate_results[run_id] = data
    while len(_validate_results) > _VALIDATE_RESULTS_MAX:
        _validate_results.popitem(last=False)


class BacktestValidateRequest(BaseModel):
    strategy_name: str
    symbol: str
    exchange: str = "bybit"
    timeframe: str = "1s"
    start_date: str
    end_date: str
    n_splits: int = 3
    starting_cash: float = 10_000.0
    min_sharpe: float = 1.0
    max_drawdown_threshold: float = 0.15
    max_degradation: float = 0.30
    stress_max_drawdown_threshold: float = 0.30
    stress_windows: list[tuple[str, str, str]] = []


def _run_validate_sync(req: BacktestValidateRequest, path: Path) -> dict[str, Any]:
    """Synchronous validation run — called via asyncio.to_thread."""
    from bot_service.backtest.feeds import QuestDBFeed
    from bot_service.backtest.validation import (
        generate_validation_report,
        run_monte_carlo,
        run_stress_test,
        run_walk_forward,
    )

    settings = get_settings()
    # Load the strategy class from the file
    import importlib.util
    spec = importlib.util.spec_from_file_location("_validate_strategy", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load strategy from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    strategy_cls = None
    import inspect
    import backtrader as bt
    for name, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, bt.Strategy) and obj is not bt.Strategy:
            strategy_cls = obj
            break
    if strategy_cls is None:
        raise ValueError(f"No bt.Strategy subclass found in {path.name}")

    feed = QuestDBFeed(
        questdb_http_addr=settings.questdb_http_addr,
        symbol=req.symbol,
        exchange=req.exchange,
        start_date=req.start_date,
        end_date=req.end_date,
        tf=req.timeframe,
    )

    wf = run_walk_forward(
        strategy_cls=strategy_cls,
        feed=feed,
        n_splits=req.n_splits,
        starting_cash=req.starting_cash,
    )

    stress = None
    if req.stress_windows:
        stress = run_stress_test(
            strategy_cls=strategy_cls,
            feed=feed,
            windows=req.stress_windows,
            starting_cash=req.starting_cash,
        )

    # Use OOS fold P&L values as Monte Carlo input (fold-level net P&L in base currency)
    trade_pnls: list[float] = [fold.oos_pnl for fold in wf.folds]
    mc_pct5 = run_monte_carlo(trade_pnls) if trade_pnls else None

    report = generate_validation_report(
        walk_forward=wf,
        stress=stress,
        monte_carlo_pct5=mc_pct5,
        fee_gate=None,
        min_sharpe=req.min_sharpe,
        max_drawdown_threshold=req.max_drawdown_threshold,
        max_degradation=req.max_degradation,
        stress_max_drawdown_threshold=req.stress_max_drawdown_threshold,
    )
    import json
    return json.loads(report.to_json())


async def _run_validate_task(run_id: str, req: BacktestValidateRequest, path: Path) -> None:
    try:
        result = await asyncio.to_thread(_run_validate_sync, req, path)
        _put_validate_result(run_id, {"status": "done", "result": result})
    except Exception as exc:
        log.error("validate_task_failed", run_id=run_id, error=str(exc))
        _put_validate_result(run_id, {"status": "failed", "error": str(exc)})
    finally:
        _validate_tasks.pop(run_id, None)


@app.post("/backtest/validate")
async def backtest_validate(req: BacktestValidateRequest) -> dict[str, str]:
    import uuid
    settings = get_settings()
    strategies_dir = Path(settings.bot_strategies_dir).resolve()
    path = (strategies_dir / f"{req.strategy_name}.py").resolve()
    try:
        path.relative_to(strategies_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid strategy name")
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Strategy not found: {req.strategy_name}")
    run_id = str(uuid.uuid4())
    _put_validate_result(run_id, {"status": "running"})
    task = asyncio.create_task(_run_validate_task(run_id, req, path))
    _validate_tasks[run_id] = task
    return {"run_id": run_id, "status": "running"}


@app.get("/backtest/validate/{run_id}")
def validate_status(run_id: str) -> dict[str, Any]:
    result = _validate_results.get(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Validation run not found: {run_id}")
    return {"run_id": run_id, **result}


if __name__ == "__main__":
    uvicorn.run("bot_service.main:app", host="0.0.0.0", port=8090, log_config=None)
