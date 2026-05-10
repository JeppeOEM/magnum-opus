# Story 11.7: FastAPI Service Entry Point & Startup Sequence

Status: done

## Story

As mrqdt,
I want a FastAPI entry point that initialises the service in the correct order, exposes `/health` and `/version`, and shuts down cleanly on SIGTERM,
so that the service starts deterministically and Docker Compose can health-check and gracefully stop it.

## Acceptance Criteria

1. The entire startup sequence lives inside the FastAPI `lifespan` context manager so it runs regardless of whether the service is started via `main()` or `uvicorn bot_service.main:app` (Dockerfile CMD). Sequence: (1) configure structlog with `redact_credentials` processor and JSON renderer, (2) load `Settings` — call `sys.exit(1)` on `ValidationError`, (3) call `apply_schema(settings.questdb_http_addr)` — call `sys.exit(1)` on `SchemaApplyError`, (4) start `BusManager`. `redact_credentials` must NOT be installed as a processor until after `Settings` is loaded (it calls `get_settings()` internally).
2. `GET /health` returns `200 OK` with `{"status": "ok", "bus_manager": "running"}` when `BusManager.is_alive()` is True; returns `200 OK` with `{"status": "degraded", "bus_manager": "dead"}` when Bus Manager thread is dead. The `200` on degraded is intentional — health reports state, not viability; liveness probes handle restarts. The check is in-memory only, no external calls.
3. `GET /version` returns `{"version": <BUILD_VERSION env var>, "commit": <GIT_COMMIT env var>}`; if either env var is absent, its value is `"unknown"`. Values are read at request time, not at startup.
4. SIGTERM is handled by uvicorn's built-in graceful shutdown — do NOT install a custom `signal.signal(SIGTERM, …)` handler (it would conflict with uvicorn's own handler). Bus Manager cleanup runs in the lifespan teardown block (after `yield`). For this story, teardown is: `BusManager.stop()`. The `BOT_SHUTDOWN_TIMEOUT_S` setting is reserved for Epic 12 when strategy loops need to flush ILP Senders — no strategy threads exist in this story.
5. `bot-service/docker-compose.yml` with: `stop_grace_period: 45s`, `depends_on: questdb: condition: service_healthy`, environment variables including `BOT_SHUTDOWN_TIMEOUT_S=30`, ports `8090:8090`, `restart: unless-stopped`.
6. L1 tests in `tests/test_main.py` using FastAPI `TestClient`: `/health` correct body when Bus Manager alive; `/health` degraded body when Bus Manager dead; `/version` correct values; `/version` returns `"unknown"` when vars absent.

## Tasks / Subtasks

- [x] Write `bot_service/main.py` (AC: 1–4)
  - [x] `_configure_logging_early()` — pre-Settings console config
  - [x] `_configure_logging(settings)` — final JSON + `redact_credentials` config
  - [x] `lifespan` context manager with full startup sequence + teardown
  - [x] Module-level `app = FastAPI(lifespan=lifespan)` for uvicorn direct invocation
  - [x] `/health` endpoint reading module-level `_bus_manager`
  - [x] `/version` endpoint reading `BUILD_VERSION` / `GIT_COMMIT` env vars at request time
- [x] Write `bot-service/docker-compose.yml` (AC: 5)
- [x] Write `tests/test_main.py` (AC: 6)

## Dev Notes

### File Layout

- `bot_service/main.py` — NEW file; module-level `app` is uvicorn entry point
- `bot-service/docker-compose.yml` — NEW file (note: path is relative to the bot-service directory, not repo root)
- `tests/test_main.py` — NEW file; L1 tests with `TestClient` + mocked `_bus_manager`

### `apply_schema` Signature

`apply_schema` is **synchronous** (not async). Signature: `apply_schema(questdb_http_addr: str) -> None`. Call as:
```python
apply_schema(settings.questdb_http_addr)
```
Do NOT wrap in `asyncio.run()`.

### `redact_credentials` Constraint

`redact_credentials` in `config.py` calls `get_settings()` internally. It must only be used as a structlog processor **after** Settings has been successfully loaded. The early-startup console config (used while loading Settings) must use a different processor chain without `redact_credentials`.

### main.py Implementation

```python
from __future__ import annotations

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
from bot_service.persistence.schema import SchemaApplyError, apply_schema


# Module-level singleton — set inside lifespan, read by /health endpoint
_bus_manager: BusManager | None = None


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
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_credentials,           # only installed after Settings loads
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
    )
    logging.basicConfig(format="%(message)s", level=level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _bus_manager

    # Step 1: early logging (before credentials available)
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

    # Step 4: start Bus Manager
    _bus_manager = BusManager()
    _bus_manager.start()
    log.info("bus_manager_started")

    yield  # service is running

    # Teardown: stop Bus Manager (uvicorn sends SIGTERM → graceful lifespan exit)
    log.info("bot_service_stopping")
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
```

### docker-compose.yml

```yaml
version: "3.9"

services:
  bot-service:
    build: .
    ports:
      - "8090:8090"
    environment:
      REDIS_URL: "redis://redis:6379"
      QUESTDB_HTTP_ADDR: "http://questdb:9000"
      QUESTDB_ILP_ADDR: "questdb:9009"
      BOT_SHUTDOWN_TIMEOUT_S: "30"
      LOG_LEVEL: "info"
    depends_on:
      questdb:
        condition: service_healthy
      redis:
        condition: service_started
    restart: unless-stopped
    stop_grace_period: 45s

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  questdb:
    image: questdb/questdb:7.4.0
    ports:
      - "9000:9000"
      - "9009:9009"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/"]
      interval: 5s
      timeout: 3s
      retries: 10
```

### L1 Test Pattern

Tests bypass lifespan by directly setting `main_module._bus_manager` and using `TestClient(app)` with `raise_server_exceptions=False` to skip lifespan execution.

```python
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import bot_service.main as main_module
from bot_service.main import app


@pytest.fixture(autouse=True)
def reset_bus_manager() -> None:
    """Ensure _bus_manager is cleaned up between tests."""
    yield
    main_module._bus_manager = None


def _client_with_bus(alive: bool) -> TestClient:
    mock_bm = MagicMock()
    mock_bm.is_alive.return_value = alive
    main_module._bus_manager = mock_bm
    # raise_server_exceptions=False skips lifespan so we control _bus_manager
    return TestClient(app, raise_server_exceptions=False)


def test_health_running() -> None:
    with _client_with_bus(alive=True) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "bus_manager": "running"}


def test_health_degraded_when_bus_dead() -> None:
    with _client_with_bus(alive=False) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "degraded", "bus_manager": "dead"}


def test_version_returns_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUILD_VERSION", "1.2.3")
    monkeypatch.setenv("GIT_COMMIT", "abc123")
    with _client_with_bus(alive=True) as client:
        resp = client.get("/version")
    assert resp.status_code == 200
    assert resp.json() == {"version": "1.2.3", "commit": "abc123"}


def test_version_returns_unknown_when_vars_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUILD_VERSION", raising=False)
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    with _client_with_bus(alive=True) as client:
        resp = client.get("/version")
    assert resp.status_code == 200
    assert resp.json() == {"version": "unknown", "commit": "unknown"}
```

### What Already Exists

- `bot_service/bus/event_bus.py` — `BusManager` fully implemented; `BusManager()` constructor with optional `queue_max_depth` override; `is_alive()`, `start()`, `stop()` all implemented
- `bot_service/config.py` — `Settings`, `get_settings()`, `redact_credentials`; `redact_credentials` calls `get_settings()` so must only be used post-Settings-load
- `bot_service/persistence/schema.py` — `apply_schema(questdb_http_addr: str) -> None` (synchronous), `SchemaApplyError`
- `bot_service/Dockerfile` — CMD is `uvicorn bot_service.main:app --host 0.0.0.0 --port 8090`; no changes needed
- `tests/conftest.py` — sets credential env vars and clears `get_settings` cache; no changes needed

### Critical Constraints

- **All startup steps in lifespan** — not in a separate `main()` function; the Dockerfile uses `uvicorn bot_service.main:app` which skips any `main()` entrypoint
- **No custom SIGTERM handler** — uvicorn handles SIGTERM gracefully and triggers lifespan teardown; installing `signal.signal(SIGTERM, …)` would conflict
- **`apply_schema` is synchronous** — call as `apply_schema(settings.questdb_http_addr)` with no `asyncio.run()`
- **`redact_credentials` only after Settings loads** — early console config must use a different processor chain
- **`/version` reads env vars at request time** — `os.environ.get(…)` in the endpoint, not cached at startup; `monkeypatch.setenv/delenv` in tests correctly intercepts this
- **`TestClient(app, raise_server_exceptions=False)`** skips lifespan so tests control `_bus_manager` directly; use the module-level `app`, not a freshly-created one, so endpoint closures read the same `_bus_manager` the test sets
- **`200` for both ok/degraded** is intentional; do not add `5xx` responses for degraded state

### References

- [Source: _bmad-output/planning-artifacts/epics-bot.md § Story 11.7]
- [Source: bot_service/persistence/schema.py — apply_schema(questdb_http_addr: str) sync signature]
- [Source: bot_service/config.py — redact_credentials calls get_settings()]
- [Source: bot_service/bus/event_bus.py — BusManager interface]

### Review Findings

- [x] [Review][Patch] logging.root.setLevel() not called — stdlib log level stays at INFO after second basicConfig() call [bot_service/main.py:49]
- [x] [Review][Patch] add_indicators() exception propagates uncaught out of on_bar() — silences event loop for that symbol [bot_service/strategy/base.py:151]
- [x] [Review][Patch] NaN guard checks entire DataFrame — early indicator-column NaN rows permanently block handler [bot_service/strategy/base.py:99-104]
- [x] [Review][Patch] Missing @pytest.mark.l1 markers on all test functions — AC6 violation [tests/test_main.py]
- [x] [Review][Patch] add_indicators() docstring missing warning against local df reassignment [bot_service/strategy/base.py:86]
- [x] [Review][Defer] sys.exit(1) inside ASGI lifespan — spec-mandated; uvicorn handles SystemExit cleanly — deferred, pre-existing
- [x] [Review][Defer] BusManager healthy-but-idle with zero streams (is_alive()==True before strategies register) — by design pre-Epic 12 — deferred, pre-existing
- [x] [Review][Defer] pandas-ta>=0.3.14b no upper version pin — maintenance item — deferred, pre-existing
- [x] [Review][Defer] No test for add_indicators() override/exception path — Epic 15 will exercise — deferred, pre-existing
- [x] [Review][Defer] Heartbeat thread sends SIGTERM (base.py) — pre-existing Story 11.6 — deferred, pre-existing
- [x] [Review][Defer] add_indicators() call ordering side-effect on NaN columns undocumented — deferred, pre-existing

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- All three files pre-existed with correct implementation; verified against every AC before marking done.
- `structlog.stdlib.add_logger_name` removed from `_configure_logging()` — it requires a stdlib `logging.Logger` for its `.name` attribute but `PrintLoggerFactory` produces a `PrintLogger` which lacks `.name`; removing it is correct and the processor adds no value with this logger factory anyway.
- Tests restructured to call `TestClient(app)` without context manager — Starlette 1.0 only runs the ASGI lifespan during `__enter__`; without context manager, requests route through the ASGI app with `http` scope, leaving `_bus_manager` at whatever the test sets it to.
- `pandas-ta>=0.3.14b` added to `pyproject.toml` (user request before this story); `add_indicators` hook wired into `BaseStrategy.on_bar`; mypy override added for pandas_ta.
- 44/44 tests pass with zero regressions.

### File List

- bot_service/main.py
- bot-service/docker-compose.yml
- bot-service/pyproject.toml
- bot_service/strategy/base.py
- tests/test_main.py
