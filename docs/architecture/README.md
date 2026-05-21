# Magnum-Opus Architecture Documentation

A service-by-service deep-dive into how every part of this trading system works,
with direct links to the source code.

---

## Contents

| Chapter | What it covers |
|---------|---------------|
| [01 — System Overview](01-overview.md) | Bird's-eye view, service map, end-to-end data flow |
| [02 — Aggregator](02-aggregator.md) | Go WebSocket feeds → order book → Redis + QuestDB |
| [03 — Candle Service](03-candle-service.md) | Redis tick streams → 67-feature 1 s bars + 7-TF cascade |
| [04 — Bot Service](04-bot-service.md) | Python strategy runner, order worker, hot-reload, circuit breaker |
| [05 — Gateway](05-gateway.md) | Go pub/sub bridge → WebSocket fan-out to browsers |
| [06 — Dashboard](06-dashboard.md) | Dash/Plotly live charts, footprint, bots tab, backtests tab |
| [07 — Infrastructure](07-infrastructure.md) | Redis, QuestDB, Prometheus, Grafana, Loki, Alertmanager |
| [08 — Data Contracts](08-data-contracts.md) | Every Redis key, stream schema, QuestDB table, ILP wire format |

---

## How to read this

- **Code links** are `path/to/file.go:LINE` — click them in VS Code or open via `gh browse`.
- Every chapter starts with a **30-second summary** so you can skip deep sections you already know.
- Architecture constraints from [`CLAUDE.md`](../../CLAUDE.md) are called out in `> ⚠ Constraint` blocks.
