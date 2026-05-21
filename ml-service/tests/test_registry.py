import json
from pathlib import Path

import pytest

from ml_service.registry import registry


def _reset(tmp_path: Path):
    """Reset module-level state for test isolation."""
    registry._registry_path = tmp_path / "models.json"
    registry._entries = []


def test_upsert_and_get(tmp_path):
    _reset(tmp_path)
    eid = registry.upsert(
        "bybit", "BTCUSDT", "RANGING", 0,
        "xgboost", 0.65, 500, ["ofi_l1"], {"ofi_l1": 0.5}, str(tmp_path),
    )
    entries = registry.get_all()
    assert len(entries) == 1
    assert entries[0]["id"] == eid
    assert entries[0]["cv_score"] == 0.65
    assert entries[0]["regime"] == "RANGING"


def test_upsert_replaces_same_key(tmp_path):
    """Second upsert for the same (exchange, symbol, regime, cluster) replaces, not appends."""
    _reset(tmp_path)
    registry.upsert("bybit", "BTCUSDT", "RANGING", 0, "xgboost", 0.5, 100, [], {}, str(tmp_path))
    registry.upsert("bybit", "BTCUSDT", "RANGING", 0, "random_forest", 0.7, 200, [], {}, str(tmp_path))
    entries = registry.get_all()
    assert len(entries) == 1, f"Expected 1 entry (replaced), got {len(entries)}"
    assert entries[0]["model_type"] == "random_forest"
    assert entries[0]["cv_score"] == 0.7


def test_upsert_different_cluster_both_kept(tmp_path):
    """Different clusters within same regime are stored as separate entries."""
    _reset(tmp_path)
    registry.upsert("bybit", "BTCUSDT", "RANGING", 0, "xgboost", 0.5, 100, [], {}, str(tmp_path))
    registry.upsert("bybit", "BTCUSDT", "RANGING", 1, "xgboost", 0.6, 120, [], {}, str(tmp_path))
    entries = registry.get_all()
    assert len(entries) == 2


def test_delete(tmp_path):
    _reset(tmp_path)
    eid = registry.upsert("bybit", "BTCUSDT", "RANGING", 0, "xgboost", 0.5, 100, [], {}, str(tmp_path))
    assert registry.delete(eid) is True
    assert registry.get_all() == []


def test_delete_nonexistent_returns_false(tmp_path):
    _reset(tmp_path)
    assert registry.delete("no-such-id") is False


def test_get_model_returns_none_when_missing(tmp_path):
    _reset(tmp_path)
    result = registry.get_model("bybit", "BTCUSDT", "RANGING", 99)
    assert result is None


def test_get_model_returns_correct_entry(tmp_path):
    _reset(tmp_path)
    registry.upsert("bybit", "BTCUSDT", "RANGING", 0, "xgboost", 0.5, 100, [], {}, str(tmp_path))
    registry.upsert("bybit", "BTCUSDT", "HIGH_VOL", 0, "random_forest", 0.6, 200, [], {}, str(tmp_path))
    entry = registry.get_model("bybit", "BTCUSDT", "HIGH_VOL", 0)
    assert entry is not None
    assert entry["regime"] == "HIGH_VOL"
    assert entry["model_type"] == "random_forest"


def test_persists_to_disk(tmp_path):
    """upsert writes to models.json; content is valid JSON."""
    _reset(tmp_path)
    registry.upsert("bybit", "BTCUSDT", "RANGING", 0, "xgboost", 0.65, 500, ["ofi_l1"], {"ofi_l1": 0.5}, str(tmp_path))
    path = tmp_path / "models.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert len(data) == 1
    assert data[0]["cv_score"] == 0.65


def test_load_from_disk(tmp_path):
    """init() restores entries written by a previous run."""
    _reset(tmp_path)
    registry.upsert("bybit", "BTCUSDT", "RANGING", 0, "xgboost", 0.65, 500, ["ofi_l1"], {}, str(tmp_path))

    # Simulate a fresh init
    registry._entries = []
    registry._entries = registry._load(registry._registry_path)
    entries = registry.get_all()
    assert len(entries) == 1
    assert entries[0]["cv_score"] == 0.65


def test_delete_removes_artifact_dir(tmp_path):
    """delete() removes the artifact directory from the filesystem."""
    _reset(tmp_path)
    art_dir = tmp_path / "bybit" / "BTCUSDT" / "RANGING" / "0"
    art_dir.mkdir(parents=True)
    (art_dir / "model.pkl").write_bytes(b"fake")

    eid = registry.upsert("bybit", "BTCUSDT", "RANGING", 0, "xgboost", 0.5, 100, [], {}, str(art_dir))
    assert art_dir.exists()

    registry.delete(eid)
    assert not art_dir.exists()
