---
stepsCompleted: ['step-01-init', 'step-02-context', 'step-03-starter', 'step-04-decisions', 'step-05-patterns', 'step-06-structure', 'step-07-validation', 'step-08-complete']
workflowType: 'architecture'
status: 'complete'
completedAt: '2026-05-09'
inputDocuments:
  - '_bmad-output/brainstorming/brainstorming-session-2026-05-09-0656.md'
  - '_bmad-output/planning-artifacts/prd-monitoring.md'
  - '_bmad-output/project-context.md'
  - '_bmad-output/planning-artifacts/architecture.md'
project_name: 'magnum-opus'
feature: 'unified-monitoring'
---

# Architecture Decision Document — Unified Monitoring

_Observability infrastructure for magnum-opus: Prometheus + Alertmanager + Grafana._
_Derived from brainstorming session 2026-05-09 and advanced elicitation (First Principles + ADR panel)._

---

## 1. Context & Scope

### What exists today

| Service | Metrics endpoint | Port | Status |
|---|---|---|---|
| aggregator | `/metrics` | 8080 | Live — custom registry, `promhttp.HandlerFor` |
| candle-service | `/metrics` | 8081 | Live — `health.WithMetrics(reg)` wired in `main.go:487` |

Both services use `prometheus.NewRegistry()` (never `DefaultRegisterer`) and expose Prometheus text format. No changes to the scrape endpoints are required.

### What we are adding

1. **Per-condition health gauges** in each service — expose health as a fact, not a verdict
2. **Prometheus** — scrapes both services, evaluates alert rules
3. **Alertmanager** — routes alerts, ships with null receiver (Telegram-ready, not wired)
4. **Grafana** — visualization only, fully provisioned as code, zero manual UI setup

### Out of scope

- Telegram bot creation and wiring (future work — config structure is in place)
- L3/L4 integration tests for the monitoring stack itself
- Metrics for future services (trading bot, AI/ML, kill switch) — they follow the same pattern

---

## 2. Primary Deliverables

First principles analysis identified two distinct deliverables with different criticality:

| Deliverable | Tool | Purpose | Used when |
|---|---|---|---|
| **Awareness system** | Alertmanager → Telegram | Know something broke within minutes | 3am on a phone |
| **Diagnostic system** | Grafana dashboard | Understand why it broke | Sitting at a laptop |

Grafana failure must never silence alerts. Alertmanager runs as an independent container.

---

## 3. Health Gauge Design

### Convention

Every service exposes a `{prefix}_health` GaugeVec with label `condition`:

```
aggregator_health{condition="feeds"}         1  ← 1=ok, 0=failing
aggregator_health{condition="gaps"}          1
candle_health{condition="consumer_lag"}      1
candle_health{condition="questdb"}           1
candle_health{condition="flush"}             1
```

**Values:** `1` = healthy, `0` = failing. No intermediate states — the gauge reports a fact.

**Update rule:** Health gauges are updated by existing goroutines (lag poller, WAL probe, feed state tracker) — not recomputed at scrape time. The `/metrics` handler reads pre-computed atomic values. Zero scrape-time overhead.

### Aggregation in Grafana/Prometheus

- **Overview stat panel:** `min(aggregator_health)` — goes red if any condition is 0
- **Condition matrix:** query all `*_health` metrics — auto-discovers new services and conditions
- **Future services:** register `{prefix}_health{condition="..."}` — dashboard gains new rows with zero config changes

### Per-service conditions

**aggregator:**

| Condition | Source | Failing when |
|---|---|---|
| `feeds` | `aggregator_feed_state` gauge | any feed_state == 0 |
| `gaps` | `aggregator_gap_total` rate | gap rate > 0 in last 5m |

**candle-service:**

| Condition | Source | Failing when |
|---|---|---|
| `consumer_lag` | lag poller (5s) | lag > 1000 messages |
| `questdb` | WAL probe | WAL suspended |
| `flush` | flush counters | flush_failure_total increased in last 15m |

### Implementation rule (project-context.md constraint)

```go
// Correct — custom registry, GaugeVec with condition label
healthGauge := prometheus.NewGaugeVec(prometheus.GaugeOpts{
    Name: "aggregator_health",
    Help: "Service health per condition: 1=ok, 0=failing.",
}, []string{"condition"})
reg.MustRegister(healthGauge)  // reg = prometheus.NewRegistry(), never DefaultRegisterer
```

---

## 4. Alert Rules (Prometheus)

Stored in `monitoring/prometheus/alerts.yml`. Three urgency tiers. All thresholds marked as **initial — tune after one week of production observation**.

```yaml
groups:
  - name: magnum_opus_health
    rules:

      # CRITICAL — trading data stopped flowing
      - alert: FeedsDown
        expr: min(aggregator_health{condition="feeds"}) == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Aggregator feed(s) disconnected"
          description: "One or more exchange feeds have been disconnected for >1m"

      # WARNING — candle consumer falling behind
      - alert: ConsumerLagHigh
        expr: min(candle_health{condition="consumer_lag"}) == 0
        for: 3m
        labels:
          severity: warning
        annotations:
          summary: "Candle consumer lag high"
          description: "Consumer lag >1000 messages for >3m"

      # WARNING — QuestDB WAL suspended
      - alert: QuestDBSuspended
        expr: min(candle_health{condition="questdb"}) == 0
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "QuestDB WAL suspended"
          description: "Candle service WAL buffer active — writes buffered, not committed"

      # INFO — Parquet flush failing
      - alert: FlushFailing
        expr: min(candle_health{condition="flush"}) == 0
        for: 10m
        labels:
          severity: info
        annotations:
          summary: "Parquet flush failing"
          description: "Daily Parquet flush has been failing for >10m"
```

---

## 5. Alertmanager Configuration

Stored in `monitoring/alertmanager/alertmanager.yml`.

Ships with `null` receiver. Telegram structure is in place — wiring requires uncommenting and setting two env vars.

```yaml
route:
  receiver: 'null'
  group_by: ['alertname', 'severity']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h

receivers:
  - name: 'null'

  # Uncomment when Telegram bot is ready:
  # - name: 'telegram'
  #   telegram_configs:
  #     - bot_token: '${TELEGRAM_BOT_TOKEN}'
  #       chat_id: ${TELEGRAM_CHAT_ID}
  #       parse_mode: 'HTML'
  #       message: |
  #         <b>{{ .Status | toUpper }}</b> — {{ .CommonAnnotations.summary }}
  #         {{ .CommonAnnotations.description }}
```

To wire Telegram: set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` env vars, change `receiver: 'null'` to `receiver: 'telegram'`, restart Alertmanager.

---

## 6. Prometheus Configuration

Stored in `monitoring/prometheus/prometheus.yml`.

```yaml
global:
  scrape_interval: 10s        # ADR-1: faster than default 15s
  evaluation_interval: 10s

rule_files:
  - /etc/prometheus/alerts.yml

alerting:
  alertmanagers:
    - static_configs:
        - targets: ['alertmanager:9093']

scrape_configs:
  - job_name: 'aggregator'
    static_configs:
      - targets: ['aggregator:8080']

  - job_name: 'candle-service'
    static_configs:
      - targets: ['candle-service:8081']

  # Future services: add one stanza here
  # - job_name: 'trading-bot'
  #   static_configs:
  #     - targets: ['trading-bot:8082']
```

---

## 7. Grafana Provisioning

All configuration is code-first. `make up` produces a fully wired Grafana with no manual UI steps.

### Directory structure

```
monitoring/
  grafana/
    provisioning/
      datasources/
        prometheus.yml       ← Prometheus datasource
      dashboards/
        provider.yml         ← tells Grafana where to find dashboard JSON
        magnum-opus.json     ← dashboard definition
```

### Datasource (`prometheus.yml`)

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    url: http://prometheus:9090
    isDefault: true
    editable: false
```

### Dashboard layout

**Row 1 — System Health Overview**
- One stat panel per service: `min(aggregator_health)`, `min(candle_health)`
- Color thresholds: 1→green, 0→red
- Shows service name + ok/failing label
- Future services auto-added by adding a stat panel

**Row 2 — Condition Matrix**
- Table panel: rows=services, columns=conditions
- Query: all `*_health` metrics — auto-discovers new services and conditions as they are added

**Row 3 — aggregator USE**
- Utilization: `rate(aggregator_ticks_total[1m])` by symbol
- Saturation: `aggregator_consumer_lag_ms` by symbol
- Errors: `rate(aggregator_gap_total[5m])`, `aggregator_feed_state`

**Row 4 — candle-service USE**
- Utilization: `rate(candle_bars_total[1m])` by symbol
- Saturation: `candle_consumer_lag` by symbol
- Errors: `rate(candle_flush_failure_total[5m])`, `rate(candle_redis_publish_failure_total[5m])`

---

## 8. docker-compose Additions

Three new services appended to `docker-compose.yml`. All use explicit image tags (never `:latest`).

```yaml
services:
  prometheus:
    image: prom/prometheus:v2.51.0
    volumes:
      - ./monitoring/prometheus:/etc/prometheus:ro
      - prometheus-data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.retention.time=15d'
    ports:
      - "9090:9090"
    depends_on:
      - aggregator
      - candle-service

  alertmanager:
    image: prom/alertmanager:v0.27.0
    volumes:
      - ./monitoring/alertmanager:/etc/alertmanager:ro
      - alertmanager-data:/alertmanager
    command:
      - '--config.file=/etc/alertmanager/alertmanager.yml'
      - '--storage.path=/alertmanager'
    ports:
      - "9093:9093"

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

volumes:
  prometheus-data:
  alertmanager-data:
  grafana-data:
```

---

## 9. File Tree

```
monitoring/
  prometheus/
    prometheus.yml
    alerts.yml
  alertmanager/
    alertmanager.yml
  grafana/
    provisioning/
      datasources/
        prometheus.yml
      dashboards/
        provider.yml
        magnum-opus.json
```

---

## 10. Implementation Sequence

Do not reorder — each step depends on the previous.

1. **Health gauge metrics** — add `aggregator_health` GaugeVec to `aggregator/internal/metrics/metrics.go`; add `candle_health` GaugeVec to `candle-service/internal/metrics/metrics.go`
2. **Health update wiring** — update existing goroutines (lag poller, WAL probe, feed state) to call `healthGauge.WithLabelValues("condition").Set(1/0)`
3. **Monitoring config files** — create `monitoring/` directory tree with `prometheus.yml`, `alerts.yml`, `alertmanager.yml`, Grafana provisioning
4. **docker-compose additions** — append prometheus, alertmanager, grafana services + named volumes
5. **Dashboard JSON** — build and export Grafana dashboard JSON via UI, commit to `monitoring/grafana/provisioning/dashboards/`
6. **Makefile targets** — add `make monitoring-up`, `make monitoring-down` or integrate into existing `make dev-infra`
7. **Smoke test** — verify `/metrics` endpoints are scraped, health gauges appear, alert rules evaluate, Grafana dashboard renders

---

## 11. Critical Constraints (from project-context.md)

- `prometheus.NewRegistry()` always — never `DefaultRegisterer` or `MustRegister` on default
- `promhttp.HandlerFor(registry, promhttp.HandlerOpts{})` — never `promhttp.Handler()`
- Health gauge updated by existing goroutines — not on scrape path
- No `time.Now()` or `time.Sleep()` in `internal/` packages
- No new external dependencies beyond `prom/prometheus`, `prom/alertmanager`, `grafana/grafana` Docker images
- Pinned image tags — never `:latest`
