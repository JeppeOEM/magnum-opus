---
id: 37-1
title: ml-service scaffold
epic: 37
status: ready-for-dev
---

# Story 37-1: ml-service Scaffold

## Context

Foundation for the Python ML service. No exchange dependencies — reads only from QuestDB HTTP and Redis. Uses the same patterns as bot-service: pydantic-settings config, structlog JSON logging, FastAPI with lifespan.

## What to build

### Directory layout

```
ml-service/
├── pyproject.toml
├── Dockerfile
├── .env.example
├── ml_service/
│   ├── __init__.py
│   ├── config.py
│   ├── logging_setup.py
│   └── main.py
└── tests/
    └── test_health.py
```

### `ml-service/pyproject.toml`

```toml
[project]
name = "ml-service"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "pydantic-settings>=2.6",
    "structlog>=24.4",
    "httpx>=0.27",
    "duckdb>=1.1",
    "pandas>=2.2",
    "pyarrow>=18.0",
    "scikit-learn>=1.5",
    "xgboost>=2.1",
    "hdbscan>=0.8.38",
    "shap>=0.46",
    "scipy>=1.14",
    "redis[hiredis]>=5.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24", "httpx>=0.27"]
```

### `ml-service/ml_service/config.py`

```python
from __future__ import annotations
from functools import lru_cache
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    questdb_http_addr: str = "http://questdb:9000"
    redis_url: str = "redis://redis:6379"
    ml_feature_store_path: str = "/data/features"
    ml_model_registry_path: str = "/data/models"
    log_level: str = "info"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

### `ml-service/ml_service/logging_setup.py`

```python
import logging
import structlog

def configure(log_level: str = "info") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
    )
```

### `ml-service/ml_service/main.py`

```python
from __future__ import annotations
from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI
from ml_service.config import get_settings
from ml_service.logging_setup import configure

log = structlog.get_logger()

@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_settings()
    configure(cfg.log_level)
    log.info("ml_service_starting", questdb=cfg.questdb_http_addr)
    yield
    log.info("ml_service_stopped")

app = FastAPI(title="ml-service", lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "ok"}
```

### `ml-service/Dockerfile`

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN adduser --disabled-password --gecos "" appuser
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .
COPY ml_service/ ml_service/
USER appuser
CMD ["uvicorn", "ml_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### `docker-compose.yml` — add service

```yaml
  ml-service:
    build: ./ml-service
    ports:
      - "8000:8000"
    environment:
      - QUESTDB_HTTP_ADDR=http://questdb:9000
      - REDIS_URL=redis://redis:6379
      - ML_FEATURE_STORE_PATH=/data/features
      - ML_MODEL_REGISTRY_PATH=/data/models
    volumes:
      - ml_data:/data
    depends_on:
      - questdb
      - redis
    restart: unless-stopped

volumes:
  ml_data:
```

### `ml-service/tests/test_health.py`

```python
from fastapi.testclient import TestClient
from ml_service.main import app

def test_health():
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

## Acceptance Criteria

1. `docker compose up ml-service` starts the service and `GET /health` returns `{"status": "ok"}` with HTTP 200.
2. `pydantic-settings` resolves: `QUESTDB_HTTP_ADDR`, `REDIS_URL`, `ML_FEATURE_STORE_PATH`, `ML_MODEL_REGISTRY_PATH`, `LOG_LEVEL`.
3. `structlog` emits JSON lines with `timestamp`, `level`, `event` fields.
4. `pyproject.toml` declares all required dependencies including `xgboost`, `hdbscan`, `shap`, `duckdb`.
5. `Dockerfile` uses `python:3.12-slim` with a non-root user.
6. `docker-compose.yml` includes the `ml-service` with correct `depends_on`.
7. `tests/test_health.py` passes with `pytest`.

## Dev Notes

- No `bot-service` dependencies imported — ml-service is fully isolated.
- Data volume `ml_data` persists feature Parquet files and model artifacts across container restarts.
- `hdbscan` requires C++ build tools during `pip install` — the `python:3.12-slim` image needs `build-essential` in the Dockerfile: add `RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*` before the pip install line.
