from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    main_module._backtest_tasks.clear()
    main_module._backtest_results.clear()


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


# ── /strategies ───────────────────────────────────────────────────────────────

@pytest.mark.l1
def test_strategies_returns_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "my_strat.py").write_text("x = 1\n")
    monkeypatch.setenv("BOT_STRATEGIES_DIR", str(tmp_path))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/strategies")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "my_strat"
    assert data[0]["code"] == "x = 1\n"
    assert len(data[0]["hash"]) == 16


@pytest.mark.l1
def test_strategies_empty_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_STRATEGIES_DIR", str(tmp_path))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/strategies")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.l1
def test_strategies_missing_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_STRATEGIES_DIR", str(tmp_path / "nonexistent"))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/strategies")
    assert resp.status_code == 200
    assert resp.json() == []


# ── /backtest/run ─────────────────────────────────────────────────────────────

@pytest.mark.l1
def test_backtest_run_returns_run_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "MyStrat.py").write_text("x = 1\n")
    monkeypatch.setenv("BOT_STRATEGIES_DIR", str(tmp_path))
    with patch("bot_service.main.asyncio.create_task") as mock_task:
        mock_task.return_value = MagicMock()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/backtest/run", json={
            "strategy_name": "MyStrat",
            "symbol": "BTCUSDT",
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    assert "run_id" in data
    assert len(data["run_id"]) == 36  # UUID4 format


@pytest.mark.l1
def test_backtest_run_parallel_runs_get_distinct_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "MyStrat.py").write_text("x = 1\n")
    monkeypatch.setenv("BOT_STRATEGIES_DIR", str(tmp_path))
    with patch("bot_service.main.asyncio.create_task") as mock_task:
        mock_task.return_value = MagicMock()
        client = TestClient(app, raise_server_exceptions=False)
        r1 = client.post("/backtest/run", json={
            "strategy_name": "MyStrat",
            "symbol": "BTCUSDT",
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
        })
        r2 = client.post("/backtest/run", json={
            "strategy_name": "MyStrat",
            "symbol": "ETHUSDT",
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
        })
    assert r1.json()["run_id"] != r2.json()["run_id"]


@pytest.mark.l1
def test_backtest_run_404_on_missing_strategy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_STRATEGIES_DIR", str(tmp_path))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/backtest/run", json={
        "strategy_name": "NonExistent",
        "symbol": "BTCUSDT",
        "start_date": "2026-01-01",
        "end_date": "2026-02-01",
    })
    assert resp.status_code == 404


# ── /backtest/run/{run_id} ────────────────────────────────────────────────────

@pytest.mark.l1
def test_backtest_status_404_on_unknown_run_id() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/backtest/run/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.l1
def test_backtest_status_returns_result_when_done() -> None:
    run_id = "test-run-123"
    main_module._backtest_results[run_id] = {"status": "done", "result": {"n_trades": 5}}
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(f"/backtest/run/{run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == run_id
    assert data["status"] == "done"
    assert data["result"]["n_trades"] == 5


@pytest.mark.l1
def test_backtest_status_returns_running_while_in_progress() -> None:
    run_id = "test-run-456"
    main_module._backtest_results[run_id] = {"status": "running"}
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(f"/backtest/run/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


# ── /backtest/runs ────────────────────────────────────────────────────────────

@pytest.mark.l1
def test_backtest_runs_returns_rows_from_questdb(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "columns": [{"name": "run_id"}, {"name": "strategy_name"}, {"name": "final_value"}],
        "dataset": [["abc", "MyStrat", 10500.0], ["def", "MyStrat", 9800.0]],
    }
    with patch("bot_service.main.httpx.get", return_value=mock_resp):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/backtest/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["run_id"] == "abc"
    assert data[0]["strategy_name"] == "MyStrat"
    assert data[1]["final_value"] == 9800.0


@pytest.mark.l1
def test_backtest_runs_empty_on_questdb_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("bot_service.main.httpx.get", side_effect=Exception("connection refused")):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/backtest/runs")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.l1
def test_backtest_runs_filters_by_strategy_name(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"columns": [{"name": "run_id"}], "dataset": [["xyz"]]}
    captured: list[str] = []
    def _capture_get(url: str, **kwargs: object) -> MagicMock:
        captured.append(str(kwargs.get("params", {}).get("query", "")))
        return mock_resp
    with patch("bot_service.main.httpx.get", side_effect=_capture_get):
        client = TestClient(app, raise_server_exceptions=False)
        client.get("/backtest/runs?strategy_name=OFIBot")
    assert "OFIBot" in captured[0]
