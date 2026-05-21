---
id: 37-6
title: Model registry
epic: 37
status: ready-for-dev
---

# Story 37-6: Model Registry

## Context

The model registry is a `models.json` manifest on disk that tracks all trained models. The inference engine reads it at startup and on SIGHUP to hot-reload newly trained models. The registry also exposes SHAP summaries for the dashboard.

## What to build

### `ml-service/ml_service/registry/registry.py`

```python
from __future__ import annotations
import json
import signal
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import structlog

log = structlog.get_logger()

_LOCK = threading.RLock()
_entries: list[dict] = []
_registry_path: Path | None = None


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as fh:
        return json.load(fh)


def _save(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(entries, fh, indent=2)


def init(model_registry_path: str) -> None:
    global _registry_path, _entries
    _registry_path = Path(model_registry_path) / "models.json"
    with _LOCK:
        _entries = _load(_registry_path)
    signal.signal(signal.SIGHUP, _reload_handler)
    log.info("registry_loaded", n=len(_entries))


def _reload_handler(signum, frame) -> None:
    global _entries
    with _LOCK:
        _entries = _load(_registry_path)
    log.info("registry_reloaded_sighup", n=len(_entries))


def upsert(
    exchange: str,
    symbol: str,
    regime: str,
    cluster: int,
    model_type: str,
    cv_score: float,
    n_samples: int,
    feature_names: list[str],
    shap_summary: dict,
    artifact_dir: str,
) -> str:
    """Add or replace an entry; returns the entry id."""
    entry_id = str(uuid.uuid4())
    entry = {
        "id": entry_id,
        "exchange": exchange,
        "symbol": symbol,
        "regime": regime,
        "cluster": cluster,
        "model_type": model_type,
        "cv_score": cv_score,
        "n_samples": n_samples,
        "feature_names": feature_names,
        "shap_summary": shap_summary,
        "artifact_dir": artifact_dir,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _LOCK:
        # Remove existing entry for same (exchange, symbol, regime, cluster)
        _entries[:] = [
            e for e in _entries
            if not (e["exchange"] == exchange and e["symbol"] == symbol
                    and e["regime"] == regime and e["cluster"] == cluster)
        ]
        _entries.append(entry)
        _save(_registry_path, _entries)
    return entry_id


def get_all() -> list[dict]:
    with _LOCK:
        return list(_entries)


def get_model(exchange: str, symbol: str, regime: str, cluster: int) -> dict | None:
    with _LOCK:
        for e in _entries:
            if (e["exchange"] == exchange and e["symbol"] == symbol
                    and e["regime"] == regime and e["cluster"] == cluster):
                return e
    return None


def delete(entry_id: str) -> bool:
    import shutil
    with _LOCK:
        before = len(_entries)
        entry = next((e for e in _entries if e["id"] == entry_id), None)
        if not entry:
            return False
        _entries[:] = [e for e in _entries if e["id"] != entry_id]
        _save(_registry_path, _entries)
        # Delete artifacts
        art_dir = Path(entry["artifact_dir"])
        if art_dir.exists():
            shutil.rmtree(art_dir)
    return True
```

### Wire into `main.py`

```python
from ml_service.registry import registry as _registry

# In lifespan after configure():
cfg = get_settings()
_registry.init(cfg.ml_model_registry_path)

# Also call upsert from /train endpoint after run_training() returns
```

### FastAPI endpoints

```python
@app.get("/registry")
async def registry_list():
    return _registry.get_all()

@app.delete("/registry/{entry_id}")
async def registry_delete(entry_id: str):
    ok = _registry.delete(entry_id)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(404, "entry not found")
    return {"deleted": entry_id}
```

### `ml-service/tests/test_registry.py`

```python
import tempfile, os
from pathlib import Path

def test_upsert_and_get(tmp_path):
    from ml_service.registry import registry
    registry._registry_path = tmp_path / "models.json"
    registry._entries = []
    eid = registry.upsert("bybit", "BTCUSDT", "RANGING", 0, "xgboost",
                           0.65, 500, ["ofi_l1"], {"ofi_l1": 0.5}, str(tmp_path))
    entries = registry.get_all()
    assert len(entries) == 1
    assert entries[0]["id"] == eid
    assert entries[0]["cv_score"] == 0.65

def test_upsert_replaces_same_key(tmp_path):
    from ml_service.registry import registry
    registry._registry_path = tmp_path / "models.json"
    registry._entries = []
    registry.upsert("bybit", "BTCUSDT", "RANGING", 0, "xgboost", 0.5, 100, [], {}, str(tmp_path))
    registry.upsert("bybit", "BTCUSDT", "RANGING", 0, "random_forest", 0.7, 200, [], {}, str(tmp_path))
    entries = registry.get_all()
    assert len(entries) == 1  # replaced, not appended
    assert entries[0]["model_type"] == "random_forest"

def test_delete(tmp_path):
    from ml_service.registry import registry
    registry._registry_path = tmp_path / "models.json"
    registry._entries = []
    eid = registry.upsert("bybit", "BTCUSDT", "RANGING", 0, "xgboost", 0.5, 100, [], {}, str(tmp_path))
    assert registry.delete(eid) is True
    assert registry.get_all() == []

def test_get_model_returns_none_when_missing(tmp_path):
    from ml_service.registry import registry
    registry._registry_path = tmp_path / "models.json"
    registry._entries = []
    result = registry.get_model("bybit", "BTCUSDT", "RANGING", 99)
    assert result is None
```

## Acceptance Criteria

1. `registry.init()` loads `models.json` from disk on startup; creates empty file if not present.
2. SIGHUP reloads `models.json` from disk without restart.
3. `upsert()` adds entry and persists to disk; replaces existing entry for same (exchange, symbol, regime, cluster).
4. `GET /registry` returns all entries.
5. `DELETE /registry/{id}` removes entry from JSON and deletes artifact directory.
6. `get_model()` returns the correct entry for (exchange, symbol, regime, cluster); None if not found.
7. Concurrent reads are safe (RLock used for all mutations).
8. All 4 unit tests pass.

## Dev Notes

- `_LOCK = threading.RLock()` — uvicorn runs sync endpoints in a thread pool; the lock prevents concurrent writes from two `/train` calls.
- SIGHUP handler is registered in `init()` — only works on Linux/Mac (not Windows). That's fine for the Docker deployment target.
- `upsert` is called from `run_training()` result processing in the `/train` endpoint to persist results immediately.
