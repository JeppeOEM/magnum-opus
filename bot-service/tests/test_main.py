from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import bot_service.main as main_module
from bot_service.main import app
from bot_service.strategy.circuit_breaker import DailyLossCircuitBreaker


@pytest.fixture(autouse=True)
def reset_singletons() -> None:
    """Isolate module-level singletons between tests."""
    yield
    main_module._bus_manager = None
    main_module._file_watcher = None
    main_module._circuit_breaker = None


def _client(alive: bool, strategy_statuses: dict[str, str] | None = None) -> TestClient:
    mock_bm = MagicMock()
    mock_bm.is_alive.return_value = alive
    main_module._bus_manager = mock_bm
    if strategy_statuses is not None:
        mock_fw = MagicMock()
        mock_fw.get_strategy_statuses.return_value = strategy_statuses
        main_module._file_watcher = mock_fw
    else:
        main_module._file_watcher = None
    # No context manager — Starlette 1.0 only runs lifespan inside __enter__;
    # without it, requests go through the routing layer directly, so singletons
    # are whatever we set above.
    return TestClient(app, raise_server_exceptions=False)


# ── /health — bus manager state ───────────────────────────────────────────────

@pytest.mark.l1
def test_health_ok_when_bus_alive_no_strategies() -> None:
    client = _client(alive=True)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["bus_manager"] == "running"
    assert data["strategies"] == {}


@pytest.mark.l1
def test_health_degraded_when_bus_dead() -> None:
    client = _client(alive=False)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["bus_manager"] == "dead"


@pytest.mark.l1
def test_health_degraded_when_bus_manager_none() -> None:
    main_module._bus_manager = None
    main_module._file_watcher = None
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["bus_manager"] == "dead"


# ── /health — per-strategy status ─────────────────────────────────────────────

@pytest.mark.l1
def test_health_ok_when_all_strategies_running() -> None:
    client = _client(alive=True, strategy_statuses={"OFIBot": "running", "MACrossBot": "running"})
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["strategies"] == {"OFIBot": "running", "MACrossBot": "running"}


@pytest.mark.l1
def test_health_degraded_when_strategy_restarting() -> None:
    client = _client(alive=True, strategy_statuses={"OFIBot": "running", "MACrossBot": "restarting"})
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["strategies"]["MACrossBot"] == "restarting"


@pytest.mark.l1
def test_health_degraded_when_strategy_stopped() -> None:
    client = _client(alive=True, strategy_statuses={"OFIBot": "stopped"})
    resp = client.get("/health")
    data = resp.json()
    assert data["status"] == "degraded"


@pytest.mark.l1
def test_health_includes_strategy_status_in_response() -> None:
    client = _client(alive=True, strategy_statuses={"OFIBot": "running"})
    resp = client.get("/health")
    data = resp.json()
    assert "strategies" in data
    assert data["strategies"]["OFIBot"] == "running"


# ── /health — circuit_breaker_tripped field ───────────────────────────────────

@pytest.mark.l1
def test_health_includes_circuit_breaker_tripped_false() -> None:
    client = _client(alive=True)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["circuit_breaker_tripped"] is False


@pytest.mark.l1
def test_health_degraded_when_circuit_breaker_tripped() -> None:
    mock_bm = MagicMock()
    mock_bm.is_alive.return_value = True
    main_module._bus_manager = mock_bm
    cb = DailyLossCircuitBreaker(limit_usd=100.0)
    cb.record_pnl(-101.0)
    main_module._circuit_breaker = cb
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/health")
    data = resp.json()
    assert data["circuit_breaker_tripped"] is True
    assert data["status"] == "degraded"


# ── /stop-all ─────────────────────────────────────────────────────────────────

@pytest.mark.l1
def test_stop_all_calls_file_watcher_stop_all() -> None:
    mock_fw = MagicMock()
    main_module._file_watcher = mock_fw
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/stop-all")
    assert resp.status_code == 200
    assert resp.json() == {"status": "stopped"}
    mock_fw.stop_all.assert_called_once()


@pytest.mark.l1
def test_stop_all_returns_stopped_when_no_file_watcher() -> None:
    main_module._file_watcher = None
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/stop-all")
    assert resp.status_code == 200
    assert resp.json() == {"status": "stopped"}


@pytest.mark.l1
def test_health_still_responds_after_stop_all() -> None:
    mock_bm = MagicMock()
    mock_bm.is_alive.return_value = True
    main_module._bus_manager = mock_bm
    mock_fw = MagicMock()
    mock_fw.get_strategy_statuses.return_value = {}
    main_module._file_watcher = mock_fw
    client = TestClient(app, raise_server_exceptions=False)
    client.post("/stop-all")
    resp = client.get("/health")
    assert resp.status_code == 200


# ── /version ──────────────────────────────────────────────────────────────────

@pytest.mark.l1
def test_version_returns_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUILD_VERSION", "1.2.3")
    monkeypatch.setenv("GIT_COMMIT", "abc123")
    client = _client(alive=True)
    resp = client.get("/version")
    assert resp.status_code == 200
    assert resp.json() == {"version": "1.2.3", "commit": "abc123"}


@pytest.mark.l1
def test_version_returns_unknown_when_vars_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUILD_VERSION", raising=False)
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    client = _client(alive=True)
    resp = client.get("/version")
    assert resp.status_code == 200
    assert resp.json() == {"version": "unknown", "commit": "unknown"}
