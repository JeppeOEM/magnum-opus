"""Server-side JSON persistence for candle hover profiles.

Profiles are stored in a single JSON file whose path is:
  1. $PROFILES_PATH env var (absolute or relative to cwd), or
  2. <directory containing this file>/profiles.json  (default)

All public functions are safe to call concurrently from Dash callbacks
because they use a per-process threading lock.  Cross-process safety
(multiple gunicorn workers) is *not* guaranteed, but a single-worker
dashboard is the expected deployment.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_lock = threading.Lock()

_DEFAULT_PATH = Path(__file__).parent / "profiles.json"


def _resolve_path() -> Path:
    env = os.environ.get("PROFILES_PATH", "")
    if env:
        p = Path(env)
        return p if p.is_absolute() else Path.cwd() / p
    return _DEFAULT_PATH


def file_path() -> Path:
    """Return the resolved profiles file path."""
    return _resolve_path()


def exists() -> bool:
    return _resolve_path().exists()


def load() -> dict | None:
    """Read profiles from disk.

    Returns the parsed dict on success, or None if the file does not exist
    or cannot be parsed.
    """
    p = _resolve_path()
    with _lock:
        if not p.exists():
            return None
        try:
            text = p.read_text(encoding="utf-8")
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("Root must be a JSON object")
            return data
        except Exception as exc:
            logger.warning("profile_storage_load_failed: %s", exc)
            return None


def save(data: dict) -> bool:
    """Write *data* to disk as pretty-printed JSON.

    Returns True on success, False on error.
    """
    p = _resolve_path()
    with _lock:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(p)  # atomic on POSIX
            return True
        except Exception as exc:
            logger.warning("profile_storage_save_failed: %s", exc)
            return False
