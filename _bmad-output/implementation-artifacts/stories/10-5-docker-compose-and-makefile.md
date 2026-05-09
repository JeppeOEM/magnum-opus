# Story 10.5: Docker Compose Wiring and Makefile Integration

Status: done

## Story

As a developer,
I want `make up` to start the full monitoring stack alongside existing services,
so that monitoring is available in every local session without extra steps.

## Acceptance Criteria

1. `make up` starts prometheus (port 9090), alertmanager (9093), and grafana (3000)
2. Named volumes `prometheus-data`, `alertmanager-data`, `grafana-data` persist across `make down` / `make up`
3. `make monitoring-logs` tails prometheus, alertmanager, and grafana logs
4. All three monitoring services use `restart: unless-stopped`
5. Pinned image tags: `prom/prometheus:v2.51.0`, `prom/alertmanager:v0.27.0`, `grafana/grafana:10.4.2`
6. `docker compose config` validates successfully

## Tasks / Subtasks

- [x] Add prometheus service to `docker-compose.yml` (AC: 1, 2, 4, 5)
- [x] Add alertmanager service to `docker-compose.yml` (AC: 1, 2, 4, 5)
- [x] Add grafana service to `docker-compose.yml` (AC: 1, 2, 4, 5)
- [x] Add `prometheus-data`, `alertmanager-data`, `grafana-data` named volumes (AC: 2)
- [x] Add `make monitoring-logs` target to root `Makefile` (AC: 3)
- [x] Validate `docker compose config` (AC: 6)

## Dev Notes

### docker-compose additions

- `prometheus`: mounts `./monitoring/prometheus:/etc/prometheus:ro`, `prometheus-data:/prometheus`; passes `--storage.tsdb.retention.time=15d`; `depends_on: aggregator`
- `alertmanager`: mounts `./monitoring/alertmanager:/etc/alertmanager:ro`, `alertmanager-data:/alertmanager`
- `grafana`: mounts `./monitoring/grafana/provisioning:/etc/grafana/provisioning:ro`, `grafana-data:/var/lib/grafana`; `depends_on: prometheus`; anonymous viewer access via env vars

### Makefile

Added `monitoring-logs` to `.PHONY` and as a target before the dev section.

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Completion Notes List

- Added prometheus, alertmanager, grafana services to `docker-compose.yml` with pinned image tags, named volumes, and `restart: unless-stopped`.
- Added `prometheus-data`, `alertmanager-data`, `grafana-data` to the `volumes:` block.
- Added `monitoring-logs` target to root `Makefile`.
- `docker compose config --quiet` validates OK.

### File List

- `docker-compose.yml` — modified
- `Makefile` — modified
