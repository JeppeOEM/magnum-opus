---
stepsCompleted: [1, 2]
inputDocuments: []
session_topic: 'Unified real-time metrics monitoring across magnum-opus microservices'
session_goals: 'Candle-service metrics exposure matching aggregator pattern; single consolidated monitoring view; extensible pattern for future services (trading bot etc.)'
selected_approach: 'ai-recommended'
techniques_used: ['Question Storming', 'Cross-Pollination', 'SCAMPER Method']
ideas_generated: []
context_file: ''
---

# Brainstorming Session Results

**Facilitator:** mrqdt
**Date:** 2026-05-09

## Session Overview

**Topic:** Unified real-time metrics monitoring across magnum-opus microservices
**Goals:** Candle-service metrics exposure matching aggregator pattern; single consolidated monitoring view; extensible pattern for future services (trading bot etc.)

## Technique Selection

**Approach:** AI-Recommended Techniques
**Analysis Context:** Technical architecture + extensibility design for multi-service observability

**Recommended Techniques:**
- **Question Storming:** Surface hidden requirements before locking in any design
- **Cross-Pollination:** Raid the observability/SRE world for proven patterns to adapt
- **SCAMPER Method:** Systematically stress-test the best ideas across seven lenses

**AI Rationale:** Topic is concrete/technical but has strategic depth (future services). Sequence moves from problem clarity → inspiration from industry → systematic evaluation.

## Technique Execution

### Technique 1: Question Storming + Reality Grounding

**Key Discovery:** Candle-service `/metrics` endpoint is already live at `localhost:8081/metrics` — `health.WithMetrics(reg)` is wired in `main.go:487`. No code changes needed for metric exposure.

**Monitoring Landscape Mapped:**

**[Discovery #1]**: Candle Metrics Already Live
_Concept_: `health.Server.WithMetrics(prometheus.Gatherer)` is already called in candle-service main.go. Both services expose Prometheus-format metrics on their respective HTTP ports (8080, 8081) using isolated custom registries.
_Novelty_: The user's "problem" is already half-solved — the effort is purely in the unified view layer.

**[Path A]**: Prometheus + Grafana
_Concept_: Run Prometheus as a scrape aggregator pulling from `:8080/metrics` and `:8081/metrics`. Grafana connects to Prometheus as a datasource. One dashboard shows metrics across all services. Adding future services = one line in `prometheus.yml`.
_Novelty_: Industry standard, zero custom code, alerting built in, PromQL lets you correlate across services.

**[Path B]**: Prometheus alone (built-in UI)
_Concept_: Same scraping setup, use Prometheus Expression Browser at `:9090` instead of Grafana. Uglier but fewer moving parts.
_Novelty_: Lowest friction for a first pass — can layer Grafana on top later.

**[Path C]**: Extend logfmt.py (zero new infrastructure)
_Concept_: `logfmt.py` already scrapes `localhost:8080/metrics` every second for tick rates. Extend to also scrape `:8081/metrics` and show candle stats in the same terminal status line.
_Novelty_: No new containers, immediate terminal-integrated feedback.

**[Path D]**: VictoriaMetrics single binary
_Concept_: Drop-in Prometheus replacement with built-in `vmui` dashboard. Lighter than Prometheus + Grafana combined.
_Novelty_: Fewer moving parts than Path A, still Prometheus-compatible scrape format.

**[Path E]**: Push model via OpenTelemetry Collector
_Concept_: Services push metrics instead of being scraped. Collector fans out to any backend.
_Novelty_: Decouples services from monitoring topology — services don't know who's watching.

**Decision:** Path A — Prometheus + Grafana selected.

### Technique 2: Cross-Pollination + SCAMPER — Health Metric Design

**Dashboard Structure (from SRE + trading terminal patterns):**
```
ROW 1 — System Health overview: one stat panel per service, color = min(service_health)
ROW 2 — aggregator USE: ticks/s | consumer_lag_ms | gap_total + feed_state
ROW 3 — candle-service USE: bars/s | consumer_lag | flush_failure + redis_publish_failure
ROW N — trading-bot USE: (future, auto-added)
```

**[Design #1]**: Single Rolled-Up Value
_Concept_: One gauge per service (e.g. `aggregator_health 2`) with 0=critical/1=degraded/2=ok. Priority logic lives in the service binary.
_Novelty_: Simple alerting, but diagnostic power is low — you know something is wrong but not what.

**[Design #2]**: Per-Condition Gauge ← SELECTED
_Concept_: `aggregator_health{condition="feeds"} 1`, `aggregator_health{condition="gaps"} 0`. Overview uses `min(aggregator_health)`. Condition matrix panel auto-discovers new services and conditions.
_Novelty_: Each condition can carry its own alert severity. Adding a new service adds a new dashboard row with zero Grafana config changes. Health logic stays unit-tested inside the service.

**[Design #3]**: Prometheus-Derived Health
_Concept_: No service code changes — PromQL recording rules derive health from existing metrics.
_Novelty_: Clean separation but PromQL has no unit tests and health semantics are split across two systems.

**[Alerting]**: Prometheus Alertmanager → Telegram webhook
_Concept_: Alertmanager runs as an independent container. Alert rules defined in Prometheus (`alerts.yml`). Per-condition rules with different urgency windows (feeds=1m, lag=5m, flush=15m). Alertmanager routes to Telegram via webhook receiver. Grafana is purely a visualization layer — has no alerting role.
_Novelty_: Alertmanager independence means a Grafana crash cannot silence alerts. Per-condition rules enable fine-grained routing — feeds down is immediate, flush failure is a low-urgency notification.

**Key implication table:**

| | Single rollup | Per-condition | Prometheus-derived |
|---|---|---|---|
| Health logic lives in | Service binary | Service binary | Prometheus config |
| Diagnostic power | Low | High | Medium |
| Alerting granularity | One alert/service | One alert/condition | One alert/rule |
| Future service onboarding | Copy logic by convention | Register gauges by convention | Write PromQL by convention |
| Dashboard changes for new service | Add stat panel | Zero (auto-discovered) | Add stat panel |
| Test coverage of health logic | Unit tested | Unit tested | None |

### ADR Findings (First Principles + Architecture Decision Records)

**ADR-1: Scrape interval** → **10s** (faster than default 15s, catches feed drops promptly, negligible overhead)

**ADR-2: Health gauge updates** → driven by existing goroutines (lag poller, WAL probe) — not recomputed at scrape time. `/metrics` handler reads pre-computed atomic values.

**ADR-3: Alert thresholds** (initial — tune after one week of observation):

| Condition | Threshold | For | Urgency |
|---|---|---|---|
| `*_health{condition="feeds"} == 0` | any | 1m | critical |
| `*_health{condition="consumer_lag"} == 0` | lag > 1000 | 3m | warning |
| `*_health{condition="questdb"} == 0` | WAL suspended | 2m | warning |
| `*_health{condition="flush"} == 0` | any failure | 10m | info |

**ADR-4: Grafana provisioning** → code-first, `grafana/provisioning/` in repo. `make up` produces a fully wired stack with no manual UI steps.

**ADR-5: Alertmanager** → single container + named volume for silence persistence.

**ADR-6: Telegram** → future-ready, not wired. Ships with `null` receiver. Wiring = uncomment + two env vars.

**ADR-7 (First Principles):**
- Primary deliverable = **awareness system** (Alertmanager → Telegram)
- Secondary deliverable = **diagnostic system** (Grafana dashboard)
- Complexity = **medium-high** (alert threshold design for trading is non-trivial)
- Alertmanager independence = Grafana crash cannot silence alerts

## Final Decisions

1. **Metrics exposure**: both services already expose `/metrics` — candle-service at `:8081/metrics` is live now
2. **Monitoring stack**: Prometheus + Grafana (docker-compose)
3. **Health metric design**: per-condition gauge (`{prefix}_health{condition="..."}`, value 1=ok / 0=failing)
4. **Dashboard layout**: overview row (stat panels, `min(*_health)`) + USE row per service
5. **Alerting**: Prometheus Alertmanager → Telegram webhook. Runs independently of Grafana — dashboard crash cannot silence alerts.
6. **Future services**: register `{prefix}_health{condition="..."}` gauges — dashboard auto-discovers them
