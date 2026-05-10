# Story 11.1: Project Scaffold, Typed Configuration & Credential Redaction

Status: done

## Story

As mrqdt,
I want a fully typed Python project scaffold with strict mypy, pydantic-settings configuration, structlog credential redaction, and a documented env file,
so that all subsequent bot service stories start from a consistent, type-safe, and credential-secure foundation.

## Acceptance Criteria

1. `pyproject.toml` exists with `[tool.mypy]` (`strict=true`, `python_version="3.11"`, `warn_return_any=true`, `warn_unused_ignores=true`) and `[tool.pytest.ini_options]` registering marks `l1`, `l2`, `l3`, `l4`.
2. `bot_service/config.py` is a `pydantic_settings.BaseSettings` subclass with all required env vars; credentials use `SecretStr`; no other file may call `os.environ` directly.
3. All log output produced via structlog has credential values (the 5 exchange secrets plus Redis URL password) replaced with `[REDACTED]` — including values embedded in exception tracebacks.
4. `bot-service/.env.example` documents every env var with its default and a one-line description.
5. `Makefile` targets: `typecheck` (mypy --strict), `test-l1`, `test-l2`, `test-all` (mypy + pytest l1+l2).
6. Multi-stage `Dockerfile` — builder stage (deps) + minimal runtime stage; runs as non-root user.
7. Every `.py` file has `from __future__ import annotations` as first non-comment line; `mypy --strict` passes with zero errors.
8. L1 test: credential redaction processor correctly replaces secret values, including embedded in tracebacks; Redis URL password regex replaces the password component.

## Tasks / Subtasks

- [ ] Init `bot-service/` directory structure (AC: 7)
  - [ ] Create `bot_service/` package with all subdirectories from project-context layout
  - [ ] Add `from __future__ import annotations` to all `__init__.py` files
- [ ] Write `pyproject.toml` (AC: 1)
  - [ ] `[build-system]` with setuptools
  - [ ] `[project]` with Python 3.11 requirement and all dependencies
  - [ ] `[tool.mypy]` section — strict=true, warn_return_any, warn_unused_ignores
  - [ ] `[tool.pytest.ini_options]` with markers l1/l2/l3/l4
- [ ] Write `bot_service/config.py` (AC: 2)
  - [ ] `pydantic_settings.BaseSettings` subclass `Settings`
  - [ ] All 5 credentials as `SecretStr`
  - [ ] All env vars from the naming conventions list with correct defaults
  - [ ] `get_settings()` cached singleton via `@lru_cache`
- [ ] Write structlog credential redaction processor (AC: 3)
  - [ ] Processor function added to structlog chain before any sink
  - [ ] Redacts values of all 5 credentials using regex on serialized event dict
  - [ ] Redacts Redis URL password via `redis://:.*@` pattern
  - [ ] Works on string fields AND embedded traceback strings
- [ ] Write `bot-service/.env.example` (AC: 4)
  - [ ] Every var from naming-conventions section with default and description
- [ ] Write `Makefile` (AC: 5)
  - [ ] `typecheck`: `mypy --strict bot_service/`
  - [ ] `test-l1`: `pytest -m "l1" -x`
  - [ ] `test-l2`: `pytest -m "l2" -x`
  - [ ] `test-all`: `mypy --strict bot_service/ && pytest -m "l1 or l2" --tb=short`
- [ ] Write multi-stage `Dockerfile` (AC: 6)
  - [ ] Builder: `python:3.11-slim`, install deps via `pip install -r requirements.txt`
  - [ ] Runtime: copy installed packages + source; `USER 1000`; `CMD uvicorn`
- [ ] Write `requirements.txt` and `requirements-dev.txt`
- [ ] Write L1 tests for credential redaction (AC: 8)
  - [ ] Test: raw secret value in log event dict → `[REDACTED]` in output
  - [ ] Test: secret embedded in exception message → `[REDACTED]`
  - [ ] Test: Redis URL with password → password component `[REDACTED]`
  - [ ] Test: value NOT in credentials list → unchanged

## Dev Notes

### Directory Structure to Create

Exact layout per `bot-service/project-context.md § Package Layout`:

```
bot-service/
  bot_service/
    __init__.py
    config.py
    bus/
      __init__.py
      event_types.py      # stub — populated in Story 11.3
      event_bus.py        # stub — populated in Story 11.4
      barrier.py          # stub — populated in Story 11.5
    strategy/
      __init__.py
      base.py             # stub — populated in Story 11.6
      registry.py         # stub — populated in Story 13.2
      signals/
        __init__.py
    backtest/
      __init__.py
    exchange/
      __init__.py
    persistence/
      __init__.py
    metrics/
      __init__.py
  strategies/
    active/
      .gitkeep
    inactive/
      .gitkeep
  tests/
    __init__.py
    test_config_redaction.py
  .env.example
  Dockerfile
  Makefile
  pyproject.toml
  requirements.txt
  requirements-dev.txt
```

Stub files (`event_types.py`, `event_bus.py`, etc.) should be valid Python with `from __future__ import annotations` and a placeholder comment — not empty, so `mypy --strict` can parse them.

### Config Fields (Full List)

All fields in `Settings(BaseSettings)`:

```python
from __future__ import annotations
from functools import lru_cache
from pydantic import SecretStr
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Exchange credentials
    kucoin_api_key: SecretStr
    kucoin_api_secret: SecretStr
    kucoin_api_passphrase: SecretStr
    bybit_api_key: SecretStr
    bybit_api_secret: SecretStr

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

    # Paper trading
    bot_paper_latency_min_ms: int = 50
    bot_paper_latency_max_ms: int = 250
    bot_paper_slippage_bps: int = 5

    # Logging
    log_level: str = "info"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

### Credential Redaction Processor

The processor must be added to the structlog processor chain **before any sink** (before `JSONRenderer` or `ConsoleRenderer`). Pattern:

```python
from __future__ import annotations
import re
from typing import Any
from structlog.types import EventDict, WrappedLogger

def redact_credentials(
    logger: WrappedLogger,
    method: str,
    event_dict: EventDict,
) -> EventDict:
    """Replace credential values with [REDACTED] in all string fields."""
    settings = get_settings()
    secrets = [
        settings.kucoin_api_key.get_secret_value(),
        settings.kucoin_api_secret.get_secret_value(),
        settings.kucoin_api_passphrase.get_secret_value(),
        settings.bybit_api_key.get_secret_value(),
        settings.bybit_api_secret.get_secret_value(),
    ]
    redis_url_pattern = re.compile(r"redis://:([^@]+)@")

    def _scrub(value: Any) -> Any:
        if isinstance(value, str):
            for secret in secrets:
                if secret and secret in value:
                    value = value.replace(secret, "[REDACTED]")
            value = redis_url_pattern.sub("redis://:[REDACTED]@", value)
        elif isinstance(value, dict):
            return {k: _scrub(v) for k, v in value.items()}
        elif isinstance(value, (list, tuple)):
            return type(value)(_scrub(v) for v in value)
        return value

    return {k: _scrub(v) for k, v in event_dict.items()}
```

Key constraint: `get_settings()` is called inside the processor (not at module import time) to avoid circular imports. The processor handles nested dicts and lists — not just top-level fields.

### Requirements

`requirements.txt`:
```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
pydantic>=2.7.0
pydantic-settings>=2.2.0
structlog>=24.1.0
redis>=5.0.0
questdb>=2.0.0
httpx>=0.27.0
prometheus-client>=0.20.0
backtrader>=1.9.78
pandas>=2.2.0
```

`requirements-dev.txt`:
```
mypy>=1.9.0
pytest>=8.1.0
pytest-asyncio>=0.23.0
types-redis
```

### pyproject.toml Key Sections

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "bot-service"
version = "0.1.0"
requires-python = ">=3.11"

[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_ignores = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = [
    "l1: pure-function unit tests, no IO",
    "l2: integration tests with mocked Redis and exchange clients",
    "l3: chaos tests requiring live Redis and QuestDB containers",
    "l4: exchange testnet tests (manual pre-deploy only)",
]
```

### Dockerfile Pattern

```dockerfile
FROM python:3.11-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /install /usr/local
COPY bot_service/ ./bot_service/
COPY strategies/ ./strategies/
RUN useradd -m -u 1000 bot && chown -R bot:bot /app
USER bot
CMD ["uvicorn", "bot_service.main:app", "--host", "0.0.0.0", "--port", "8090"]
```

### Critical Rules from project-context.md

- `from __future__ import annotations` in EVERY `.py` file — first non-comment line
- `os.environ` access ONLY inside `config.py` (via pydantic-settings) — banned everywhere else
- `Optional[X]` and `Union[X, Y]` are FORBIDDEN — use `X | None` and `X | Y`
- `Any` without inline comment is FORBIDDEN — justify every use
- Credentials never in logs, errors, tracebacks, or Prometheus labels

### What Does NOT Exist Yet

The bot-service directory has only `project-context.md`. Everything else is net-new. No existing code to break. Do NOT create `main.py` yet — that belongs to Story 11.7. Do NOT implement `event_types.py`, `event_bus.py`, `barrier.py`, `base.py` beyond stubs — those belong to Stories 11.3–11.6.

### Project Structure Notes

- Service lives at `bot-service/` (repo root), not nested inside any other service
- Package name is `bot_service` (underscore) — the directory is `bot-service/` (hyphen)
- `strategies/active/` and `strategies/inactive/` sit inside `bot-service/`, NOT inside `bot_service/` — they are runtime directories, not a Python package
- Story output file: `_bmad-output/implementation-artifacts/stories/11-1-*.md` (already exists — this file)

### References

- [Source: bot-service/project-context.md § Technology Stack]
- [Source: bot-service/project-context.md § Package Layout]
- [Source: bot-service/project-context.md § Python Code Standards]
- [Source: bot-service/project-context.md § Naming Conventions]
- [Source: bot-service/project-context.md § Forbidden Anti-Patterns]
- [Source: _bmad-output/planning-artifacts/epics-bot.md § Story 11.1]

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

(none — no unexpected failures)

### Completion Notes List

- System Python is 3.12.3 (Ubuntu LTS), not 3.11 as specified. pyproject.toml sets `python_version = "3.11"` for mypy as required; the runtime venv uses 3.12 which is a superset and all code is 3.11-compatible.
- `pip` and `python3-venv` were not available on the system. Created venv with `--without-pip` and bootstrapped pip via `get-pip.py`. The `.venv/` directory is gitignored by convention; a `python3 -m venv .venv && pip install -r requirements.txt -r requirements-dev.txt` step is needed on fresh clone.
- `Settings.model_config` uses Pydantic v2 dict-style config (`model_config = {...}`) instead of the inner `class Config:` shown in the story. The story snippet used Pydantic v1 style; pydantic-settings 2.14.1 requires the v2 pattern. The `get_settings()` function uses `# type: ignore[call-arg]` with justifying comment because mypy cannot statically resolve that pydantic-settings will supply required fields from the environment.
- `redact_credentials` uses `_REDIS_URL_PATTERN` as a module-level compiled regex (not compiled inside the processor on every call) to avoid repeated compilation; behaviour is identical to the story's inline pattern.
- All 8 L1 tests pass. `mypy --strict bot_service/` reports zero errors across 14 source files.
- Makefile targets use bare `mypy` and `pytest` per spec — caller must activate `.venv` or add it to PATH.

### File List

- `bot-service/pyproject.toml` (created)
- `bot-service/requirements.txt` (created)
- `bot-service/requirements-dev.txt` (created)
- `bot-service/Makefile` (created)
- `bot-service/Dockerfile` (created)
- `bot-service/.env.example` (created)
- `bot-service/bot_service/__init__.py` (created)
- `bot-service/bot_service/config.py` (created)
- `bot-service/bot_service/bus/__init__.py` (created)
- `bot-service/bot_service/bus/event_types.py` (stub, created)
- `bot-service/bot_service/bus/event_bus.py` (stub, created)
- `bot-service/bot_service/bus/barrier.py` (stub, created)
- `bot-service/bot_service/strategy/__init__.py` (created)
- `bot-service/bot_service/strategy/base.py` (stub, created)
- `bot-service/bot_service/strategy/registry.py` (stub, created)
- `bot-service/bot_service/strategy/signals/__init__.py` (created)
- `bot-service/bot_service/backtest/__init__.py` (created)
- `bot-service/bot_service/exchange/__init__.py` (created)
- `bot-service/bot_service/persistence/__init__.py` (created)
- `bot-service/bot_service/metrics/__init__.py` (created)
- `bot-service/tests/__init__.py` (created)
- `bot-service/tests/test_config_redaction.py` (created)
- `bot-service/strategies/active/.gitkeep` (created)
- `bot-service/strategies/inactive/.gitkeep` (created)
