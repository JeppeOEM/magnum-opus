"""Model registry — thread-safe in-memory manifest backed by models.json on disk.

`init()` loads the manifest at startup and registers a SIGHUP handler for
zero-downtime reload when new models are trained outside the current process.

All mutations use an RLock — uvicorn's thread pool can call `/train` concurrently.
"""
from __future__ import annotations

import json
import shutil
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
        try:
            return json.load(fh)
        except json.JSONDecodeError:
            log.warning("registry_json_corrupt", path=str(path))
            return []


def _save(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to a temp file then rename
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as fh:
        json.dump(entries, fh, indent=2)
    tmp.rename(path)


def init(model_registry_path: str) -> None:
    """Load registry from disk and register SIGHUP handler for hot-reload."""
    global _registry_path, _entries
    _registry_path = Path(model_registry_path) / "models.json"
    with _LOCK:
        _entries = _load(_registry_path)
    # SIGHUP can only be registered from the main thread (not from test workers)
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGHUP, _reload_handler)
    log.info("registry_loaded", path=str(_registry_path), n=len(_entries))


def _reload_handler(signum, frame) -> None:
    """SIGHUP handler — reload models.json from disk without restarting.

    Also clears the inference engine's artifact cache so the next predict() call
    loads the new model artifacts for any updated artifact_dir paths.
    """
    global _entries
    with _LOCK:
        _entries = _load(_registry_path)
    # Local import avoids circular dependency (engine imports registry.get_all).
    from ml_service.inference.engine import clear_cache  # noqa: PLC0415
    clear_cache()
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
    """Add or replace an entry; returns the UUID entry id.

    If an entry already exists for (exchange, symbol, regime, cluster) it is
    removed before adding the new one — each slot holds exactly one model.
    """
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
        # Remove existing entry for the same (exchange, symbol, regime, cluster) slot
        _entries[:] = [
            e for e in _entries
            if not (
                e["exchange"] == exchange
                and e["symbol"] == symbol
                and e["regime"] == regime
                and e["cluster"] == cluster
            )
        ]
        _entries.append(entry)
        _save(_registry_path, _entries)
    log.info(
        "registry_upserted",
        id=entry_id,
        regime=regime,
        cluster=cluster,
        model_type=model_type,
    )
    return entry_id


def get_all() -> list[dict]:
    """Return a snapshot of all registry entries."""
    with _LOCK:
        return list(_entries)


def get_model(exchange: str, symbol: str, regime: str, cluster: int) -> dict | None:
    """Return the entry for (exchange, symbol, regime, cluster), or None."""
    with _LOCK:
        for e in _entries:
            if (
                e["exchange"] == exchange
                and e["symbol"] == symbol
                and e["regime"] == regime
                and e["cluster"] == cluster
            ):
                return e
    return None


def delete(entry_id: str) -> bool:
    """Remove entry by id and delete artifact directory. Returns False if not found."""
    with _LOCK:
        entry = next((e for e in _entries if e["id"] == entry_id), None)
        if not entry:
            return False
        _entries[:] = [e for e in _entries if e["id"] != entry_id]
        _save(_registry_path, _entries)
        # Delete artifact directory
        art_dir = Path(entry["artifact_dir"])
        if art_dir.exists():
            shutil.rmtree(art_dir)
    log.info("registry_deleted", id=entry_id)
    return True
