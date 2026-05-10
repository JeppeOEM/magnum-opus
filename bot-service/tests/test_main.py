from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import bot_service.main as main_module
from bot_service.main import app


@pytest.fixture(autouse=True)
def reset_bus_manager() -> None:
    """Isolate _bus_manager between tests."""
    yield
    main_module._bus_manager = None


def _client(alive: bool) -> TestClient:
    mock_bm = MagicMock()
    mock_bm.is_alive.return_value = alive
    main_module._bus_manager = mock_bm
    # No context manager — Starlette 1.0 only runs lifespan inside __enter__;
    # without it, requests go through the routing layer directly, so _bus_manager
    # is whatever we set above.
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.l1
def test_health_running() -> None:
    client = _client(alive=True)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "bus_manager": "running"}


@pytest.mark.l1
def test_health_degraded_when_bus_dead() -> None:
    client = _client(alive=False)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "degraded", "bus_manager": "dead"}


@pytest.mark.l1
def test_health_degraded_when_bus_manager_none() -> None:
    main_module._bus_manager = None
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "degraded", "bus_manager": "dead"}


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
