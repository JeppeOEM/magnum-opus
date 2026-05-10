# _bmad-output Directory Index

## Top-level Files

- **[project-context.md](./project-context.md)** - AI rules, tech stack, and code quality standards

---

## brainstorming/

- **[brainstorming-session-2026-05-03-1200.md](./brainstorming/brainstorming-session-2026-05-03-1200.md)** - System design: multi-service crypto data pipeline and trading signals
- **[brainstorming-session-2026-05-08-1000.md](./brainstorming/brainstorming-session-2026-05-08-1000.md)** - Blue/green deployment mechanics for Go candle service
- **[brainstorming-session-2026-05-09-0656.md](./brainstorming/brainstorming-session-2026-05-09-0656.md)** - Unified real-time metrics monitoring across microservices
- **[brainstorming-session-2026-05-09-1116.md](./brainstorming/brainstorming-session-2026-05-09-1116.md)** - Bot service architecture: Redis stream consumption and execution layer

---

## planning-artifacts/

- **[architecture.md](./planning-artifacts/architecture.md)** - Full system architecture decisions and patterns (complete)
- **[architecture-monitoring.md](./planning-artifacts/architecture-monitoring.md)** - Monitoring stack architecture decisions (complete)
- **[epic-8-adversarial-review-2026-05-08.md](./planning-artifacts/epic-8-adversarial-review-2026-05-08.md)** - Adversarial review findings for Epic 8 cascade/Redis spec
- **[epics.md](./planning-artifacts/epics.md)** - Aggregator epics 1–4 with stories (complete)
- **[epics-bot.md](./planning-artifacts/epics-bot.md)** - Bot service epics 11–16 with stories (complete)
- **[epics-candle.md](./planning-artifacts/epics-candle.md)** - Candle service epics 5–9 design (complete)
- **[epics-monitoring.md](./planning-artifacts/epics-monitoring.md)** - Monitoring epic 10 with stories (complete)
- **[epics-requirements.md](./planning-artifacts/epics-requirements.md)** - Requirements inventories for all services (referenced by all epic files)
- **[implementation-readiness-report-2026-05-05.md](./planning-artifacts/implementation-readiness-report-2026-05-05.md)** - Readiness gate assessment before aggregator implementation
- **[implementation-readiness-report-2026-05-08.md](./planning-artifacts/implementation-readiness-report-2026-05-08.md)** - Readiness gate assessment before monitoring/bot implementation
- **[prd.md](./planning-artifacts/prd.md)** - Product requirements document for aggregator and candle service
- **[prd-monitoring.md](./planning-artifacts/prd-monitoring.md)** - Product requirements document for monitoring stack
- **[product-brief-magnum-opus-aggregator.md](./planning-artifacts/product-brief-magnum-opus-aggregator.md)** - Original product brief for the Go order book aggregation service

### planning-artifacts/research/

- **[domain-crypto-market-microstructure-signals-research-2026-05-03.md](./planning-artifacts/research/domain-crypto-market-microstructure-signals-research-2026-05-03.md)** - Domain research: microstructure signals for crypto trading
- **[technical-storage-architecture-crypto-trading-research-2026-05-03.md](./planning-artifacts/research/technical-storage-architecture-crypto-trading-research-2026-05-03.md)** - Technical research: storage architecture for crypto trading systems

---

## implementation-artifacts/

- **[1-1-go-module-initialization-and-dependency-verification.md](./implementation-artifacts/1-1-go-module-initialization-and-dependency-verification.md)** - Story 1.1: aggregator Go module scaffold (archived pre-renumber)
- **[deferred-work.md](./implementation-artifacts/deferred-work.md)** - Known technical debt deferred from code reviews
- **[epic-1-retro-2026-05-06.md](./implementation-artifacts/epic-1-retro-2026-05-06.md)** - Retrospective: Epic 1 aggregator foundations
- **[epic-2-retro-2026-05-06.md](./implementation-artifacts/epic-2-retro-2026-05-06.md)** - Retrospective: Epic 2 exchange adapters
- **[epic-3-retro-2026-05-07.md](./implementation-artifacts/epic-3-retro-2026-05-07.md)** - Retrospective: Epic 3 coordinator and writers
- **[epic-4-retro-2026-05-07.md](./implementation-artifacts/epic-4-retro-2026-05-07.md)** - Retrospective: Epic 4 observability and deployment
- **[epic-5-retro-2026-05-08.md](./implementation-artifacts/epic-5-retro-2026-05-08.md)** - Retrospective: Epic 5 candle service setup
- **[epic-6-retro-2026-05-08.md](./implementation-artifacts/epic-6-retro-2026-05-08.md)** - Retrospective: Epic 6 order book features
- **[epic-7-retro-2026-05-08.md](./implementation-artifacts/epic-7-retro-2026-05-08.md)** - Retrospective: Epic 7 trade flow features
- **[epic-8-retro-2026-05-08.md](./implementation-artifacts/epic-8-retro-2026-05-08.md)** - Retrospective: Epic 8 multi-timeframe cascade
- **[epic-9-retro-2026-05-09.md](./implementation-artifacts/epic-9-retro-2026-05-09.md)** - Retrospective: Epic 9 Parquet flush and blue-green
- **[epic-10-retro-2026-05-09.md](./implementation-artifacts/epic-10-retro-2026-05-09.md)** - Retrospective: Epic 10 monitoring stack
- **[sprint-status.yaml](./implementation-artifacts/sprint-status.yaml)** - Sprint tracking: epic and story completion status

### implementation-artifacts/stories/

#### Epic 2 — Exchange Adapters (Aggregator)

- **[2-1-exchange-interface-and-websocket-transport-layer.md](./implementation-artifacts/stories/2-1-exchange-interface-and-websocket-transport-layer.md)** - Exchange interface and WebSocket transport abstraction
- **[2-2-kucoin-websocket-feed-adapter.md](./implementation-artifacts/stories/2-2-kucoin-websocket-feed-adapter.md)** - KuCoin order book WebSocket feed adapter
- **[2-3-kucoin-token-auto-renewal.md](./implementation-artifacts/stories/2-3-kucoin-token-auto-renewal.md)** - KuCoin WebSocket token auto-renewal
- **[2-4-bybit-connection-multiplexer.md](./implementation-artifacts/stories/2-4-bybit-connection-multiplexer.md)** - Bybit multi-symbol connection multiplexer
- **[2-5-bybit-websocket-feed-adapter.md](./implementation-artifacts/stories/2-5-bybit-websocket-feed-adapter.md)** - Bybit order book WebSocket feed adapter

#### Epic 3 — Coordinator & Writers (Aggregator)

- **[3-1-coordinator-interface-definitions-and-stream-schema.md](./implementation-artifacts/stories/3-1-coordinator-interface-definitions-and-stream-schema.md)** - Coordinator interfaces and Redis stream schema
- **[3-2-redis-stream-writer.md](./implementation-artifacts/stories/3-2-redis-stream-writer.md)** - Redis tick stream writer
- **[3-3-questdb-ilp-writer.md](./implementation-artifacts/stories/3-3-questdb-ilp-writer.md)** - QuestDB ILP fire-and-forget writer
- **[3-4-per-symbol-coordinator-worker.md](./implementation-artifacts/stories/3-4-per-symbol-coordinator-worker.md)** - Per-symbol coordinator goroutine worker
- **[3-5-snapshot-dispatch-and-full-coordinator-orchestration.md](./implementation-artifacts/stories/3-5-snapshot-dispatch-and-full-coordinator-orchestration.md)** - Snapshot dispatch and coordinator orchestration

#### Epic 4 — Observability & Deployment (Aggregator)

- **[4-1-prometheus-metrics-registry.md](./implementation-artifacts/stories/4-1-prometheus-metrics-registry.md)** - Prometheus metrics registry definition
- **[4-2-health-version-and-metrics-http-endpoints.md](./implementation-artifacts/stories/4-2-health-version-and-metrics-http-endpoints.md)** - Health, version, and /metrics HTTP endpoints
- **[4-3-service-composition-root-and-credential-sanitizing-logger.md](./implementation-artifacts/stories/4-3-service-composition-root-and-credential-sanitizing-logger.md)** - Composition root and credential-redacting logger
- **[4-4-dockerfile-and-docker-compose-deployment.md](./implementation-artifacts/stories/4-4-dockerfile-and-docker-compose-deployment.md)** - Dockerfile and Docker Compose deployment config
- **[4-5-cicd-pipeline-and-l4-fault-injection-tests.md](./implementation-artifacts/stories/4-5-cicd-pipeline-and-l4-fault-injection-tests.md)** - CI/CD pipeline and L4 network fault injection tests
- **[4-6-live-exchange-tests.md](./implementation-artifacts/stories/4-6-live-exchange-tests.md)** - Live exchange integration tests

#### Epic 5 — Candle Service Foundations

- **[5-1-go-project-setup-and-docker.md](./implementation-artifacts/stories/5-1-go-project-setup-and-docker.md)** - Candle service Go module and Docker setup
- **[5-2-migration-runner.md](./implementation-artifacts/stories/5-2-migration-runner.md)** - QuestDB schema migration runner
- **[5-3-snapshot-1s-questdb-ddl.md](./implementation-artifacts/stories/5-3-snapshot-1s-questdb-ddl.md)** - snapshot_1s table DDL (67-field schema)
- **[5-4-redis-consumer-and-gap-parser.md](./implementation-artifacts/stories/5-4-redis-consumer-and-gap-parser.md)** - Redis stream consumer and gap detection parser
- **[5-5-l2-order-book-state-machine.md](./implementation-artifacts/stories/5-5-l2-order-book-state-machine.md)** - L2 order book pure state machine
- **[5-6-ohlcv-accumulator-and-questdb-writer.md](./implementation-artifacts/stories/5-6-ohlcv-accumulator-and-questdb-writer.md)** - OHLCV accumulator and QuestDB ILP writer
- **[5-7-observability-health-version-metrics.md](./implementation-artifacts/stories/5-7-observability-health-version-metrics.md)** - Candle service health, version, and metrics endpoints

#### Epic 6 — Order Book Features

- **[6-1-ob-state-machine-fixture-validation.md](./implementation-artifacts/stories/6-1-ob-state-machine-fixture-validation.md)** - Order book state machine fixture-based validation
- **[6-2-mid-price-and-spread-features.md](./implementation-artifacts/stories/6-2-mid-price-and-spread-features.md)** - Mid-price, spread, and quoted size features
- **[6-3-ob-depth-and-book-shape-features.md](./implementation-artifacts/stories/6-3-ob-depth-and-book-shape-features.md)** - Order book depth and imbalance shape features
- **[6-4-market-impact-and-ofi-features.md](./implementation-artifacts/stories/6-4-market-impact-and-ofi-features.md)** - Market impact and order flow imbalance features

#### Epic 7 — Trade Flow Features

- **[7-1-trade-direction-and-flow.md](./implementation-artifacts/stories/7-1-trade-direction-and-flow.md)** - Trade direction, buy/sell ratio, and flow features
- **[7-2-block-trades-rolling-percentile.md](./implementation-artifacts/stories/7-2-block-trades-rolling-percentile.md)** - Block trade detection and rolling percentile
- **[7-3-trade-distribution-and-volatility.md](./implementation-artifacts/stories/7-3-trade-distribution-and-volatility.md)** - Trade size distribution and realized volatility
- **[7-4-ob-activity-and-microstructure.md](./implementation-artifacts/stories/7-4-ob-activity-and-microstructure.md)** - OB activity rate and microstructure features
- **[7-5-quality-fields-and-null-rows.md](./implementation-artifacts/stories/7-5-quality-fields-and-null-rows.md)** - Data quality fields and null-row handling

#### Epic 8 — Multi-Timeframe Cascade & Redis Output

- **[8-1-cascade-engine-and-clock-boundary.md](./implementation-artifacts/stories/8-1-cascade-engine-and-clock-boundary.md)** - 7-timeframe cascade engine and UTC clock boundary
- **[8-2-redis-candle-stream-publisher.md](./implementation-artifacts/stories/8-2-redis-candle-stream-publisher.md)** - Redis candle stream publisher (candles:close:*)
- **[8-3-ob-feature-snapshot-publisher.md](./implementation-artifacts/stories/8-3-ob-feature-snapshot-publisher.md)** - Redis OB feature snapshot publisher (candles:ob:*)

#### Epic 9 — Cold Storage & Operational Hardening

- **[9-1-flush-manifest-ddl.md](./implementation-artifacts/stories/9-1-flush-manifest-ddl.md)** - Parquet flush manifest QuestDB DDL
- **[9-2-daily-parquet-flush-and-catchup.md](./implementation-artifacts/stories/9-2-daily-parquet-flush-and-catchup.md)** - Daily Parquet export to S3 with catchup logic
- **[9-3-flush-failure-alerting.md](./implementation-artifacts/stories/9-3-flush-failure-alerting.md)** - Flush failure alerting via Redis stream
- **[9-4-blue-green-deployment.md](./implementation-artifacts/stories/9-4-blue-green-deployment.md)** - Blue-green zero-downtime deployment and promotion

#### Epic 10 — Monitoring Stack

- **[10-1-aggregator-health-gauge.md](./implementation-artifacts/stories/10-1-aggregator-health-gauge.md)** - Aggregator Prometheus health gauge metric
- **[10-2-candle-service-health-gauge.md](./implementation-artifacts/stories/10-2-candle-service-health-gauge.md)** - Candle service Prometheus health gauge metric
- **[10-3-prometheus-alertmanager-config.md](./implementation-artifacts/stories/10-3-prometheus-alertmanager-config.md)** - Prometheus scrape config and Alertmanager rules
- **[10-4-grafana-provisioned-dashboard.md](./implementation-artifacts/stories/10-4-grafana-provisioned-dashboard.md)** - Grafana provisioned dashboard for all services
- **[10-5-docker-compose-and-makefile.md](./implementation-artifacts/stories/10-5-docker-compose-and-makefile.md)** - Docker Compose and Makefile for monitoring stack

#### Epic 11 — Bot Service Foundations

- **[11-1-project-scaffold-typed-configuration-and-credential-redaction.md](./implementation-artifacts/stories/11-1-project-scaffold-typed-configuration-and-credential-redaction.md)** - Python project scaffold, typed config, and credential redaction
- **[11-2-questdb-schema-order-events-and-order-alerts.md](./implementation-artifacts/stories/11-2-questdb-schema-order-events-and-order-alerts.md)** - QuestDB schema for order events and alerts tables
- **[11-3-event-type-definitions-and-stream-entry-parser.md](./implementation-artifacts/stories/11-3-event-type-definitions-and-stream-entry-parser.md)** - Event type dataclasses and Redis stream entry parser
- **[11-4-bus-manager-redis-consumer-and-event-router.md](./implementation-artifacts/stories/11-4-bus-manager-redis-consumer-and-event-router.md)** - BusManager: Redis consumer loop and event router
- **[11-5-multibarclose-barrier.md](./implementation-artifacts/stories/11-5-multibarclose-barrier.md)** - MultiBarClose barrier for multi-timeframe synchronization
- **[11-6-basestrategy-signal-computation-foundation.md](./implementation-artifacts/stories/11-6-basestrategy-signal-computation-foundation.md)** - BaseStrategy abstract class and signal computation foundation
- **[11-7-fastapi-service-entry-point-and-startup-sequence.md](./implementation-artifacts/stories/11-7-fastapi-service-entry-point-and-startup-sequence.md)** - FastAPI entry point and service startup sequence

---

## test-artifacts/

- **[atdd-checklist-2-1-exchange-interface-and-websocket-transport-layer.md](./test-artifacts/atdd-checklist-2-1-exchange-interface-and-websocket-transport-layer.md)** - Acceptance test checklist for Story 2.1 exchange interface
