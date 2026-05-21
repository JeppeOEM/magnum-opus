# magnum-opus Documentation

Complete documentation for the magnum-opus real-time crypto trading system.

---

## Navigation

### Start here

| | |
|-|-|
| **New to the codebase?** | Read [architecture/01-overview.md](architecture/01-overview.md) first, then your service chapter. |
| **Debugging a production issue?** | Jump to [nasa/OPERATIONAL-PROCEDURES/emergency.md](nasa/OPERATIONAL-PROCEDURES/emergency.md). |
| **Deploying candle-service?** | See [nasa/OPERATIONAL-PROCEDURES/deploy.md](nasa/OPERATIONAL-PROCEDURES/deploy.md). |
| **Adding a new strategy?** | Read [linux/CODING-STANDARDS.md](linux/CODING-STANDARDS.md) CS-P rules + [linux/DANGEROUS.md](linux/DANGEROUS.md). |
| **Understanding a log message?** | [reference/LOG-REFERENCE.md](reference/LOG-REFERENCE.md). |
| **Confused by a term?** | [reference/GLOSSARY.md](reference/GLOSSARY.md). |
| **Confused by a function that "looks wrong"?** | [verification/NOMICON.md](verification/NOMICON.md) and [linux/DANGEROUS.md](linux/DANGEROUS.md). |
| **Checking test coverage?** | [verification/VERIFICATION-MATRIX.md](verification/VERIFICATION-MATRIX.md). |

---

## Architecture (service-by-service)

→ [architecture/README.md](architecture/README.md)

| Chapter | Coverage |
|---------|---------|
| [01 — Overview](architecture/01-overview.md) | System map, 6-step data flow, design decisions |
| [02 — Aggregator](architecture/02-aggregator.md) | Go WS feeds → order book → Redis + QuestDB |
| [03 — Candle Service](architecture/03-candle-service.md) | Tick streams → 67-feature bars + 7-TF cascade |
| [04 — Bot Service](architecture/04-bot-service.md) | Strategy runner, order worker, hot-reload |
| [05 — Gateway](architecture/05-gateway.md) | Pub/sub bridge → WebSocket fan-out |
| [06 — Dashboard](architecture/06-dashboard.md) | Dash/Plotly live charts |
| [07 — Infrastructure](architecture/07-infrastructure.md) | Redis, QuestDB, Prometheus, Grafana |
| [08 — Data Contracts](architecture/08-data-contracts.md) | Every Redis key, stream schema, DDL |

---

## Verification Layer

→ [verification/README.md](verification/README.md)

| File | Purpose |
|------|---------|
| [NOMICON.md](verification/NOMICON.md) | 10 dark corners with proof sketches — things that look ordinary but break catastrophically if misunderstood |
| [VERIFICATION-MATRIX.md](verification/VERIFICATION-MATRIX.md) | 64 system claims × AUTOMATED/MANUAL/UNTESTED status |

**Run automated verification:** `bash scripts/audit-docs.sh` (14 checks, all should pass)

---

## Reference

→ [reference/README.md](reference/README.md)

| File | Purpose |
|------|---------|
| [GLOSSARY.md](reference/GLOSSARY.md) | 60+ precisely defined terms |
| [ENV-VARS.md](reference/ENV-VARS.md) | All environment variables (type, default, wrong-value effect) |
| [QUESTDB-TABLES.md](reference/QUESTDB-TABLES.md) | All table schemas with NULL semantics and example queries |
| [LOG-REFERENCE.md](reference/LOG-REFERENCE.md) | Every Warn/Error/Critical message and action to take |
| [PROMETHEUS-METRICS.md](reference/PROMETHEUS-METRICS.md) | All metrics with semantics, staleness, and alerting guidance |

---

## NASA Safety Layer

→ [nasa/README.md](nasa/README.md)

| File | Purpose |
|------|---------|
| [MISSION-RULES.md](nasa/MISSION-RULES.md) | 10 pre-decided operational rules |
| [FMEA.md](nasa/FMEA.md) | Failure modes × blast radius × detection × recovery for every component |
| [HAZARD-ANALYSIS.md](nasa/HAZARD-ANALYSIS.md) | Trading-specific hazards (unintended orders, position leaks, etc.) |
| [OPERATIONAL-PROCEDURES/deploy.md](nasa/OPERATIONAL-PROCEDURES/deploy.md) | Blue-green deploy runbook |
| [OPERATIONAL-PROCEDURES/emergency.md](nasa/OPERATIONAL-PROCEDURES/emergency.md) | Emergency response runbook |
| [OPERATIONAL-PROCEDURES/rollback.md](nasa/OPERATIONAL-PROCEDURES/rollback.md) | Rollback procedures |
| [ICD/ICD-001-ticks-stream.md](nasa/ICD/ICD-001-ticks-stream.md) | Ticks stream interface spec |
| [ICD/ICD-002-candles-close.md](nasa/ICD/ICD-002-candles-close.md) | Candles close stream interface spec |
| [ICD/ICD-003-orderbook-pubsub.md](nasa/ICD/ICD-003-orderbook-pubsub.md) | Order book pub/sub interface spec |
| [ICD/ICD-004-candles1s-pubsub.md](nasa/ICD/ICD-004-candles1s-pubsub.md) | Candles1s pub/sub interface spec |

---

## Linux Subsystem Layer

→ [linux/README.md](linux/README.md)

| File | Purpose |
|------|---------|
| [MAINTAINERS.md](linux/MAINTAINERS.md) | Every package with owner goroutine, invariants, and NOMICON cross-refs |
| [LOCKING.md](linux/LOCKING.md) | Every mutex, atomic, and channel — what it protects and how long it's held |
| [DANGEROUS.md](linux/DANGEROUS.md) | Functions with surprising contracts or hidden preconditions |
| [CODING-STANDARDS.md](linux/CODING-STANDARDS.md) | Rules + counterexamples for Go and Python code |

---

## Other docs

| File | Purpose |
|------|---------|
| [data-contract.md](data-contract.md) | Original data contract (partially superseded by architecture/08-data-contracts.md) |
| [dev-guide.md](dev-guide.md) | Developer setup and workflow |
| [ops.md](ops.md) | Operations reference |
