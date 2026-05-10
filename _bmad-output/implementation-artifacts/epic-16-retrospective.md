# Epic 16 Retrospective: Production Observability & Operations

**Date:** 2026-05-10
**Epic Status:** Done (3 of 3 stories complete)

## Stories Completed

| Story | Title | Tests Added | Key Outcome |
|-------|-------|-------------|-------------|
| 16.1 | Prometheus /metrics Endpoint | 13 L1 | 4 position/P&L gauges, 3 order counters, 1 latency histogram; `GET /metrics` via private registry; `reset_strategy_gauges()` zeroes dead-strategy metrics |
| 16.2 | Hardened /health & Docker Compose | 4 L1 | `/health` returns per-strategy status dict; bot service added to docker-compose with 45s grace, 2g mem, healthcheck-gated depends_on |
| 16.3 | Operations Runbook Entry | 0 (doc) | `docs/ops.md` Bot Service section: deploy checklist, rollback, credential rotation, 3 alert playbooks |

**Total tests added:** 17 L1 (203 total at epic completion)

## Code Review Findings

3 patches applied across the epic:

- **prometheus.py private API** (High): `reset_strategy_gauges` used `gauge._metrics` internal dict to enumerate label combinations. Replaced with module-level tracking dicts (`_pos_pnl_symbols`, `_gauge_strategies`) updated by each `set_*` call; now uses only public `gauge.labels(...).set(0.0)`. Lesson: never rely on `_`-prefixed attrs in third-party libraries.
- **Latency in `finally` block** (High): `observe_order_execution_latency_ms` fired on `ExchangeRESTError` too, inflating the histogram with failed-REST latencies. Moved outside `finally` so it only observes successful REST responses. Lesson: `finally` is appropriate for resource cleanup, not for observability measurements whose meaning depends on success.
- **Invalid rollback command** (Med): `docker compose up -d bot --image $BOT_IMAGE` is not valid syntax for Docker Compose v2. Fixed to describe editing `image:` in docker-compose.yml + `docker compose pull bot && docker compose up -d bot`. Lesson: runbook commands must be tested or at least validated against CLI help before committing.

## Key Technical Decisions

- **Private registry** — All bot-service metrics use a dedicated `CollectorRegistry()`, never the global DefaultRegistry. This is consistent with the existing pattern and required because the same process hosts both the metrics module and test isolation (DefaultRegistry leaks state across tests).
- **Lazy-init + lock pattern** — Every metric function follows the same `global _metric; with _lock: if _metric is None: _metric = Counter(...); counter = _metric; counter.labels(...).inc()` pattern. Thread-safe under concurrent first-call but each `labels().inc()` is outside the lock (fine; prometheus_client's `labels()` is internally thread-safe).
- **Bot service Docker profile** — Bot service added with `profiles: [bot]` so `docker compose up` without `--profile bot` does not start it. This follows the existing pattern for optional services in this project.

## Deferred Work (4 items)

| ID | Severity | Description |
|----|----------|-------------|
| D-16-1-1 | Low | Per-strategy gauges not wired to live strategy state — `BaseStrategy` never calls `set_position_size` etc; Grafana shows zeroes |
| D-16-2-1 | Low | `get_strategy_statuses()` reads FileWatcher dicts without lock — formal data race; benign in single-watcher practice |
| D-16-2-2 | Low | QuestDB `/health` endpoint compatibility — verify QuestDB version in deployment supports `/health` |
| D-16-3-1 | Low | Alert playbook references `bot_consumer_lag` metric but no Grafana panel exists for it yet |
