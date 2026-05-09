---
stepsCompleted: ['step-01-validate-prerequisites', 'step-02-design-epics', 'step-03-create-stories']
status: complete
inputDocuments:
  - '_bmad-output/planning-artifacts/prd-monitoring.md'
  - '_bmad-output/planning-artifacts/architecture-monitoring.md'
  - '_bmad-output/brainstorming/brainstorming-session-2026-05-09-0656.md'
  - '_bmad-output/project-context.md'
---

# magnum-opus Monitoring — Epic Breakdown

## Overview

Unified observability infrastructure for magnum-opus. Adds per-condition health gauges to both services, wires Prometheus + Alertmanager + Grafana as a fully provisioned monitoring stack, and establishes the extensibility pattern for future services (trading bot, AI/ML, kill switch).

---

## Requirements Inventory

### Functional Requirements

FR1: aggregator must expose `aggregator_health{condition}` GaugeVec (value 1=ok, 0=failing) registered with its existing custom prometheus.Registry
FR2: aggregator_health conditions: `feeds` (all feed_state gauges == 1) and `gaps` (no gap events in last 5m)
FR3: candle-service must expose `candle_health{condition}` GaugeVec registered with its existing custom prometheus.Registry
FR4: candle_health conditions: `consumer_lag` (lag ≤ 1000 messages), `questdb` (WAL not suspended), `flush` (no flush_failure increments in last 15m)
FR5: Health gauges must be updated by existing goroutines (lag poller, WAL probe, feed state) — not recomputed at scrape time
FR6: Health gauges must be pre-initialized for all configured (exchange, symbol) pairs at startup — value 1
FR7: Prometheus must scrape aggregator at `aggregator:8080/metrics` and candle-service at `candle-service:8081/metrics` every 10s
FR8: Prometheus must evaluate three-tier alert rules from `alerts.yml` and forward firing alerts to Alertmanager
FR9: Alertmanager must route alerts to a `null` receiver by default; Telegram receiver config must be present but commented out
FR10: Alertmanager must run as an independent container — Grafana crash must not affect alert routing
FR11: Grafana datasource (Prometheus) must be provisioned from code — no manual UI steps
FR12: Grafana dashboard must be provisioned from code — survives container restart with zero reconfiguration
FR13: Dashboard Row 1: one stat panel per service showing `min(*_health)` with green/red color thresholds
FR14: Dashboard Row 2: condition matrix table panel querying all `*_health` metrics — auto-discovers new services
FR15: Dashboard Row 3+: USE row per service (utilization / saturation / errors panels)
FR16: `make up` must produce a fully wired monitoring stack — Prometheus scraping, Alertmanager running, Grafana dashboard rendered

### Non-Functional Requirements

NFR1: All Docker image tags must be pinned to exact versions — never `:latest`
NFR2: Prometheus time-series retention: 15 days
NFR3: Alertmanager silence state must persist via a named Docker volume
NFR4: No new Go module dependencies — health gauges use the already-imported `prometheus/client_golang`
NFR5: Health gauge reads on the `/metrics` scrape path must perform zero computation — reads pre-computed atomic state only
NFR6: Zero manual Grafana UI configuration required after `make up`
NFR7: Adding a future service to monitoring requires: (a) health gauges in that service, (b) one scrape stanza in `prometheus.yml`, (c) one stat panel in dashboard JSON — no other changes

### Additional Requirements (Architecture)

- `prometheus.NewRegistry()` always — never `prometheus.DefaultRegisterer` or `prometheus.MustRegister`
- `promhttp.HandlerFor(registry, promhttp.HandlerOpts{})` — never `promhttp.Handler()`
- No `time.Now()` or `time.Sleep()` in `internal/` packages (health gauge logic must use injected clock or existing poller results)
- Docker Compose internal DNS: services reference each other by service name (`aggregator:8080`, not `localhost:8080`)
- Pinned image versions: `prom/prometheus:v2.51.0`, `prom/alertmanager:v0.27.0`, `grafana/grafana:10.4.2`
- Monitoring config directory structure: `monitoring/prometheus/`, `monitoring/alertmanager/`, `monitoring/grafana/provisioning/`
- Three-tier alert thresholds (initial, tune after one week): feeds=1m/critical, consumer_lag=3m/warning, questdb=2m/warning, flush=10m/info

### FR Coverage Map

| FR | Epic 10 Story |
|---|---|
| FR1, FR2, FR5, FR6 | 10.1 |
| FR3, FR4, FR5, FR6 | 10.2 |
| FR7, FR8, FR9, FR10 | 10.3 |
| FR11, FR12, FR13, FR14, FR15, FR16 | 10.4 |
| NFR1–NFR7 | All stories |

---

## Epic List

- **Epic 10: Unified Monitoring** — Per-condition health gauges in both services + Prometheus + Alertmanager + Grafana provisioned stack

---

## Epic 10: Unified Monitoring

Add observable health state to both running services and wire a fully provisioned monitoring stack (Prometheus + Alertmanager + Grafana) that gives a unified real-time view of all services and delivers alerts independently of the display layer.

**Primary deliverables:**
1. Awareness system: Alertmanager evaluates health gauge conditions → Telegram-ready (null receiver now)
2. Diagnostic system: Grafana dashboard with overview row + condition matrix + USE rows per service

**Extensibility contract:** future services add health gauges + one scrape stanza + one dashboard stat panel — nothing else changes.

---

### Story 10.1: Aggregator Per-Condition Health Gauge

As a operator,
I want the aggregator to expose a `aggregator_health{condition}` gauge per health condition,
So that Prometheus can evaluate and alert on each condition independently.

**Background:**
- `aggregator/internal/metrics/metrics.go` already has a `Registry` struct with `TicksTotal`, `GapTotal`, `FeedState`, `ConsumerLagMs`, `QuestDBWriteLatencyMs`
- `aggregator/internal/httpapi/server.go` already serves `/metrics` via `promhttp.HandlerFor`
- The coordinator updates `FeedState` per symbol; the lag poller runs every 5s
- Two conditions required: `feeds` (derived from `FeedState`) and `gaps` (derived from `GapTotal` rate)

**Acceptance Criteria:**

**Given** the aggregator starts with configured symbols
**When** `/metrics` is scraped
**Then** `aggregator_health{condition="feeds"}` exists for each configured (exchange, symbol) pair with value 1

**Given** all feeds are connected (all `aggregator_feed_state` == 1)
**When** the feed state goroutine updates health
**Then** `aggregator_health{condition="feeds"}` == 1 for all pairs

**Given** any feed drops (any `aggregator_feed_state` == 0)
**When** the feed state goroutine updates health
**Then** `aggregator_health{condition="feeds"}` == 0

**Given** no gap events have occurred in the last 5 minutes
**When** the gap health goroutine evaluates
**Then** `aggregator_health{condition="gaps"}` == 1

**Given** a gap event has occurred within the last 5 minutes
**When** the gap health goroutine evaluates
**Then** `aggregator_health{condition="gaps"}` == 0

**Given** the aggregator starts
**When** `/metrics` is scraped before any feed connects
**Then** `aggregator_health{condition="feeds"}` == 1 (pre-initialized — not 0 due to absence)

**Given** the `/metrics` endpoint is scraped
**When** the handler reads health gauge values
**Then** no computation is performed — values are read from pre-computed atomics only

**Implementation notes:**
- Add `Health *prometheus.GaugeVec` to `metrics.Registry`
- Register with label `[]string{"condition"}`
- Pre-init in `PreInit()` for all pairs: `reg.Health.WithLabelValues("feeds").Set(1)`, `reg.Health.WithLabelValues("gaps").Set(1)`
- Update `feeds` condition: in the goroutine that reads `FeedState`, compute `min(feed_state)` across all symbols and call `Health.WithLabelValues("feeds").Set(float64(minState))`
- Update `gaps` condition: track last gap timestamp per symbol; goroutine sets 0 if any gap within 5m window, 1 otherwise
- L1 test: unit test that `Health` gauge flips 0/1 correctly for both conditions

---

### Story 10.2: Candle-Service Per-Condition Health Gauge

As an operator,
I want the candle-service to expose a `candle_health{condition}` gauge per health condition,
So that Prometheus can evaluate and alert on lag, WAL state, and flush failures independently.

**Background:**
- `candle-service/internal/metrics/metrics.go` already has `Metrics` struct with `ConsumerLag`, `FlushSuccessTotal`, `FlushFailureTotal`, `WALDropTotal`
- `candle-service/internal/health/health.go` already derives `ok/degraded/critical` from `ConsumerLagMax` and `QuestDBWriteState`
- Lag is polled every 5s in `main.go` and stored in `entry.lag` atomics
- WAL state is read via `entry.w.IsWALSuspended()`
- Flush failure is tracked via `m.FlushFailureTotal` counter
- Three conditions required: `consumer_lag`, `questdb`, `flush`

**Acceptance Criteria:**

**Given** the candle-service starts with configured symbols
**When** `/metrics` is scraped
**Then** `candle_health{condition="consumer_lag"}`, `candle_health{condition="questdb"}`, and `candle_health{condition="flush"}` all exist with value 1

**Given** consumer lag ≤ 1000 messages across all symbols
**When** the lag polling goroutine updates health (every 5s)
**Then** `candle_health{condition="consumer_lag"}` == 1

**Given** consumer lag > 1000 messages for any symbol
**When** the lag polling goroutine updates health
**Then** `candle_health{condition="consumer_lag"}` == 0

**Given** the QuestDB WAL is not suspended
**When** the WAL probe goroutine checks state
**Then** `candle_health{condition="questdb"}` == 1

**Given** the QuestDB WAL is suspended
**When** the WAL probe goroutine checks state
**Then** `candle_health{condition="questdb"}` == 0

**Given** `candle_flush_failure_total` has not incremented in 15 minutes
**When** the flush health check evaluates
**Then** `candle_health{condition="flush"}` == 1

**Given** `candle_flush_failure_total` has incremented within the last 15 minutes
**When** the flush health check evaluates
**Then** `candle_health{condition="flush"}` == 0

**Given** the `/metrics` endpoint is scraped
**When** the handler reads health gauge values
**Then** no computation is performed — values are read from pre-computed state only

**Implementation notes:**
- Add `Health *prometheus.GaugeVec` to `metrics.Metrics`, registered with label `[]string{"condition"}`
- Pre-init all three conditions to 1 in `Register()`
- `consumer_lag` condition: update in the existing 5s lag polling goroutine in `main.go` — set 0 if `maxLag > 1000`, else 1
- `questdb` condition: update in the existing WAL probe goroutine — set 0 if any `entry.w.IsWALSuspended()`, else 1
- `flush` condition: track last flush failure timestamp; update goroutine (or the flush health ticker) sets 0 if within 15m window, else 1
- L1 test: unit test each condition transition independently

---

### Story 10.3: Prometheus and Alertmanager Configuration

As an operator,
I want Prometheus to scrape both services and Alertmanager to evaluate three-tier alert rules,
So that I have automated detection of failures with configurable delivery (Telegram when ready).

**Background:**
- Both `/metrics` endpoints are already live at `aggregator:8080` and `candle-service:8081` (Docker Compose internal DNS)
- Stories 10.1 and 10.2 provide the `*_health{condition}` gauges
- Alert delivery is `null` receiver now; Telegram config is commented-in-place for future wiring

**Acceptance Criteria:**

**Given** `make up` has been run
**When** Prometheus starts
**Then** it scrapes `aggregator:8080/metrics` and `candle-service:8081/metrics` on a 10s interval

**Given** Prometheus is running and scraping
**When** `aggregator_health{condition="feeds"}` drops to 0 for > 1 minute
**Then** the `FeedsDown` alert fires with severity=critical

**Given** Prometheus is running and scraping
**When** `candle_health{condition="consumer_lag"}` drops to 0 for > 3 minutes
**Then** the `ConsumerLagHigh` alert fires with severity=warning

**Given** Prometheus is running and scraping
**When** `candle_health{condition="questdb"}` drops to 0 for > 2 minutes
**Then** the `QuestDBSuspended` alert fires with severity=warning

**Given** Prometheus is running and scraping
**When** `candle_health{condition="flush"}` drops to 0 for > 10 minutes
**Then** the `FlushFailing` alert fires with severity=info

**Given** a firing alert reaches Alertmanager
**When** Alertmanager routes the alert
**Then** it is delivered to the `null` receiver (no notification sent — expected)

**Given** Grafana container is stopped
**When** a health condition fires an alert
**Then** Alertmanager still evaluates and routes correctly (independence verified)

**Given** Prometheus time-series grow over time
**When** retention is checked
**Then** data older than 15 days is purged

**Given** the monitoring stack restarts
**When** Alertmanager starts
**Then** previously configured silences are restored from the named volume

**Files to create:**
```
monitoring/prometheus/prometheus.yml   ← scrape config + alertmanager reference
monitoring/prometheus/alerts.yml       ← three-tier alert rules
monitoring/alertmanager/alertmanager.yml  ← null receiver + commented Telegram
```

**Telegram wiring note:** to activate Telegram in future — set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` env vars, change `receiver: 'null'` to `receiver: 'telegram'`, restart Alertmanager. No code changes.

---

### Story 10.4: Grafana Provisioned Dashboard

As an operator,
I want a Grafana dashboard that shows health overview and USE metrics for all services,
So that when an alert fires I can immediately see which service and condition is failing and why.

**Background:**
- Story 10.3 provides Prometheus as a datasource at `http://prometheus:9090`
- Stories 10.1 and 10.2 provide `aggregator_health{condition}` and `candle_health{condition}` gauges
- Dashboard must be provisioned as code — zero manual Grafana UI steps
- `GF_AUTH_ANONYMOUS_ENABLED=true` — no login required for read access

**Acceptance Criteria:**

**Given** `make up` has been run
**When** Grafana starts
**Then** the Prometheus datasource is available without any manual UI configuration

**Given** Grafana is running
**When** the dashboard is loaded
**Then** it is present without any manual import steps

**Given** all health conditions are 1 (ok)
**When** Row 1 (System Health Overview) is viewed
**Then** each service stat panel shows a green background

**Given** any health condition is 0 (failing)
**When** Row 1 is viewed
**Then** the affected service stat panel shows a red background

**Given** Row 2 (Condition Matrix) is viewed
**When** any *_health metric exists
**Then** the table shows service × condition cells with green/red coloring per value

**Given** Row 3 (aggregator USE) is viewed
**When** ticks are flowing
**Then** utilization panel shows `rate(aggregator_ticks_total[1m])` by symbol; saturation shows `aggregator_consumer_lag_ms`; errors show gap rate and feed state

**Given** Row 4 (candle-service USE) is viewed
**When** bars are being written
**Then** utilization shows `rate(candle_bars_total[1m])` by symbol; saturation shows `candle_consumer_lag`; errors show flush failure rate and redis publish failure rate

**Given** the Grafana container is stopped and restarted
**When** Grafana starts again
**Then** the dashboard and datasource are present without reconfiguration (provisioned from volume-backed config)

**Files to create:**
```
monitoring/grafana/provisioning/datasources/prometheus.yml
monitoring/grafana/provisioning/dashboards/provider.yml
monitoring/grafana/provisioning/dashboards/magnum-opus.json
```

**docker-compose additions for this story:**
```yaml
grafana:
  image: grafana/grafana:10.4.2
  environment:
    - GF_AUTH_ANONYMOUS_ENABLED=true
    - GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer
    - GF_AUTH_DISABLE_LOGIN_FORM=true
  volumes:
    - ./monitoring/grafana/provisioning:/etc/grafana/provisioning:ro
    - grafana-data:/var/lib/grafana
  ports:
    - "3000:3000"
  depends_on:
    - prometheus
```

---

### Story 10.5: Docker Compose Wiring and Makefile Integration

As a developer,
I want `make up` to start the full monitoring stack alongside existing services,
So that monitoring is available in every local development session without extra steps.

**Background:**
- Stories 10.3 and 10.4 define config files; this story wires them into `docker-compose.yml`
- Existing `docker-compose.yml` has `aggregator`, `candle-service`, `redis`, `questdb`
- `make watch` already scrapes `:8080/metrics` for tick rates — this does not change

**Acceptance Criteria:**

**Given** `make up` is run from the project root
**When** all containers start
**Then** Prometheus is reachable at `http://localhost:9090`, Alertmanager at `http://localhost:9093`, Grafana at `http://localhost:3000`

**Given** all three monitoring containers are running
**When** Prometheus `/targets` is checked
**Then** both `aggregator` and `candle-service` targets show state=UP

**Given** all three monitoring containers are running
**When** Alertmanager `/api/v2/status` is checked
**Then** status returns ok with the null receiver configured

**Given** `make down` is run
**When** containers stop
**Then** `prometheus-data`, `alertmanager-data`, and `grafana-data` named volumes are preserved

**Given** `make up` is run a second time after `make down`
**When** Grafana starts
**Then** no dashboard reconfiguration is needed — provisioning files restore state

**Given** the aggregator or candle-service container is not yet healthy
**When** Prometheus attempts a scrape
**Then** Prometheus marks the target as DOWN and retries — it does not crash

**docker-compose additions:**
- Services: `prometheus` (v2.51.0), `alertmanager` (v0.27.0) — both added in this story alongside Grafana from 10.4
- Volumes: `prometheus-data`, `alertmanager-data`, `grafana-data`
- All services in the monitoring group should use `restart: unless-stopped`

**Makefile changes:**
- Existing `make up` already starts all services via `docker compose up --build` — no change needed
- Existing `make down` already stops all via `docker compose down` — no change needed
- Add `make monitoring-logs` target: `docker compose logs -f prometheus alertmanager grafana`
- Verify `make watch` still works (it scrapes `:8080/metrics` directly — unaffected by Prometheus)
