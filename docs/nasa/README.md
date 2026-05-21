# NASA Safety Layer

Pre-decided rules, failure analysis, and operational procedures for this trading system.

## Documents

| File | Purpose |
|------|---------|
| [MISSION-RULES.md](MISSION-RULES.md) | 10 pre-decided rules for the most likely operational scenarios. Decisions made in advance, when thinking clearly. |
| [FMEA.md](FMEA.md) | Failure Mode and Effects Analysis. Every significant component × failure mode × blast radius × detection × recovery. |
| [HAZARD-ANALYSIS.md](HAZARD-ANALYSIS.md) | Trading-specific hazards: unintended orders, unmanaged positions, NaN propagation, data corruption patterns. |

## Operational Procedures

| File | Purpose |
|------|---------|
| [OPERATIONAL-PROCEDURES/deploy.md](OPERATIONAL-PROCEDURES/deploy.md) | Blue-green candle-service deployment procedure. |
| [OPERATIONAL-PROCEDURES/emergency.md](OPERATIONAL-PROCEDURES/emergency.md) | Emergency response runbook: WAL suspension, feed down, bot timeout, credential leak, position drift, disk full. |
| [OPERATIONAL-PROCEDURES/rollback.md](OPERATIONAL-PROCEDURES/rollback.md) | Rollback procedures for candle-service, aggregator, bot-service, and full system. |

## Interface Control Documents (ICD)

| File | Purpose |
|------|---------|
| [ICD/ICD-001-ticks-stream.md](ICD/ICD-001-ticks-stream.md) | `ticks:{exchange}:{symbol}` Redis Stream — aggregator → candle-service |
| [ICD/ICD-002-candles-close.md](ICD/ICD-002-candles-close.md) | `candles:close:{exchange}:{symbol}:{tf}` Redis Stream — candle-service → bot-service |
| [ICD/ICD-003-orderbook-pubsub.md](ICD/ICD-003-orderbook-pubsub.md) | `orderbook:{exchange}:{symbol}` Redis pub/sub — aggregator → gateway / bot-service |
| [ICD/ICD-004-candles1s-pubsub.md](ICD/ICD-004-candles1s-pubsub.md) | `candles1s:{exchange}:{symbol}` Redis pub/sub — candle-service → gateway / bot-service |
