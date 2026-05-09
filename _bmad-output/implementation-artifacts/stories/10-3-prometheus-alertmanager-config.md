# Story 10.3: Prometheus and Alertmanager Configuration

Status: done

## Story

As an operator,
I want Prometheus to scrape both services and Alertmanager to route three-tier alerts,
so that I have automated failure detection with Telegram-ready delivery.

## Acceptance Criteria

1. `monitoring/prometheus/prometheus.yml` scrapes `aggregator:8080` and `candle-service:8081` every 10s
2. `monitoring/prometheus/alerts.yml` contains 5 alert rules covering all health conditions across both services
3. `monitoring/alertmanager/alertmanager.yml` routes to `null` receiver; Telegram config is present but commented out
4. Alert rules use three-tier severity: feeds=critical, gaps+consumer_lag+questdb=warning/info, flush=info
5. Prometheus retention set to 15 days via command flag (in docker-compose — story 10.5)
6. Config files follow the Docker Compose internal DNS convention (`aggregator:8080`, not `localhost:8080`)

## Tasks / Subtasks

- [x] Create `monitoring/prometheus/prometheus.yml` — scrape config + alertmanager reference (AC: 1, 6)
- [x] Create `monitoring/prometheus/alerts.yml` — 5 alert rules covering feeds, gaps, consumer_lag, questdb, flush (AC: 2, 4)
- [x] Create `monitoring/alertmanager/alertmanager.yml` — null receiver + commented Telegram config (AC: 3)

## Dev Notes

### Alert rules

5 rules defined (one per health condition):
- `FeedsDown`: `min(aggregator_health{condition="feeds"}) == 0` for 1m → critical
- `AggregatorGapsDetected`: `min(aggregator_health{condition="gaps"}) == 0` for 5m → info
- `ConsumerLagHigh`: `min(candle_health{condition="consumer_lag"}) == 0` for 3m → warning
- `QuestDBSuspended`: `min(candle_health{condition="questdb"}) == 0` for 2m → warning
- `FlushFailing`: `min(candle_health{condition="flush"}) == 0` for 10m → info

### Telegram wiring (future)

Uncomment the `telegram` receiver in `alertmanager.yml`, set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` env vars, change `route.receiver` to `'telegram'`, restart Alertmanager.

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Completion Notes List

- Created `monitoring/prometheus/prometheus.yml`: 10s scrape + evaluation intervals, references `alerts.yml`, scrapes both services by Docker Compose service name.
- Created `monitoring/prometheus/alerts.yml`: 5 alert rules across both services, three-tier severity.
- Created `monitoring/alertmanager/alertmanager.yml`: null receiver active, Telegram config commented with inline instructions.

### File List

- `monitoring/prometheus/prometheus.yml` — created
- `monitoring/prometheus/alerts.yml` — created
- `monitoring/alertmanager/alertmanager.yml` — created
