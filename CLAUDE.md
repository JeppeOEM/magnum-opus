# magnum-opus — Claude context

## What this repo is

Real-time crypto market data pipeline. Two Go services:
- **aggregator** — WebSocket feeds (KuCoin, Bybit) → order book → Redis tick streams + QuestDB
- **candle-service** — Redis tick streams → 1-second OHLCV bars (67 microstructure features) → 7-timeframe cascade → QuestDB + Redis + daily Parquet cold storage

## Docs sync rules

### README.md is the authoritative user-facing reference. Keep it in sync automatically.

**When you add, rename, or remove a `slog.Warn` / `slog.Error` / `logger.Warn` / `logger.Error` / `.WarnContext` / `.ErrorContext` call in any `.go` file:**

1. Find the corresponding entry in `README.md` under `## Log Message Reference`.
2. If the message is new: add a row to the correct service table. Match the section by where the call site lives:
   - `aggregator/cmd/aggregator/main.go` → "Aggregator — startup"
   - `aggregator/internal/coordinator/` → "Aggregator — coordinator" or "Aggregator — QuestDB writer"
   - `aggregator/internal/exchange/kucoin/` → "Aggregator — KuCoin exchange"
   - `aggregator/internal/exchange/bybit/` → "Aggregator — Bybit exchange"
   - `aggregator/internal/writer/questdb/` → "Aggregator — QuestDB writer"
   - `candle-service/cmd/candle/main.go` → "Candle service — startup" or "Candle service — blue-green"
   - `candle-service/internal/consumer/` → "Candle service — consumer"
   - `candle-service/internal/flusher/` → "Candle service — flusher"
   - `candle-service/internal/writer/questdb/` → "Candle service — QuestDB writer"
   - `candle-service/internal/writer/redis/` → "Candle service — Redis publisher"
3. If the message string changed: update the `Message` column in the existing row.
4. If the call was removed: delete the row.
5. Also update the "What to look for" quick-reference table near the top of `## Monitoring & Observability` if the message is one of the six highlighted there.

**When you add, rename, or remove a Grafana panel in `monitoring/grafana/provisioning/dashboards/magnum-opus.json`:**

1. Find the corresponding entry in `README.md` under `## Monitoring & Observability → Grafana`.
2. If a panel was added: add a row to the table for its row section (System Health Overview, Condition Matrix, Aggregator — USE, Candle Service — USE, Gaps, Warnings & Errors). Include the PromQL/LogQL query, what the panel visualises, and what non-zero / red / high values indicate.
3. If a panel was renamed: update the panel name in the table.
4. If a panel was removed: delete its row.
5. If a new dashboard row was added: add a new `#### Row:` subsection following the same format as the existing ones.

**When you add or change a Prometheus metric:**

Update the metrics table in `## Monitoring & Observability → Prometheus`.

**When you add or change an alert rule in `monitoring/prometheus/`:**

Update the alerts table in `## Monitoring & Observability → Alertmanager`.

## Architecture constraints

- The aggregator and candle-service are separate Go modules. Never import candle-service packages from the aggregator or vice versa.
- `time.Now()` is banned in all `internal/` packages in both services. Use injected clocks (`clock.Now()`). Only `cmd/` entry points call `time.Now()`.
- No goroutines in `internal/orderbook`. It is a pure, zero-IO state machine with single-goroutine ownership.
- The orderbook `ApplyGap` must only reset `snapshotSeen=false` for `internal_merge_error`. Other gap causes (external_disconnect, external_rate_limit, cold_start_buffer_overflow) leave `snapshotSeen` unchanged.
- `RecordGap` in `aggregator/internal/metrics/metrics.go` filters out `external_disconnect` and `internal_buffer_overflow` — those are transient and must not affect the health gauge.
- Redis stream keys follow the pattern `ticks:{exchange}:{symbol}` (aggregator → candle), `candles:close:{exchange}:{symbol}` (candle output), `candles:ob:{exchange}:{symbol}` (OB features).
- QuestDB ILP writes are fire-and-forget inside the coordinator. Errors are logged but not propagated up the call stack.
- The consumer ACKs (`XACK`) before processing, not after. Unacked messages on crash are recovered via `XAUTOCLAIM` on next start.

## Blue-green deployment

- `candle-blue` listens on port 8081; `candle-green` on 8082.
- `CANDLE_SHADOW_MODE=true` starts the incoming slot in shadow mode (reads stream, does not write output).
- `POST /promote` on the shadow slot switches it live. The deploy script is `scripts/deploy-candle.sh`.
- Both slots share the same Redis streams and QuestDB tables; the active slot is identified by `CANDLE_SLOT` env var.

## Test conventions

- Tests use real Redis/QuestDB via Docker when testing integration paths; unit tests use fakes from `internal/testutil`.
- No mocking of the database in integration tests (past incident: mock/prod divergence masked a broken migration).
- Red-green-refactor: write failing tests first, then implement.
- `go test ./...` from either service root runs all tests.
- ATDD is not used; implementation-first TDD is accepted practice.

## Running

```bash
# Full stack (requires Docker)
make up          # from repo root — starts all services with docker compose

# Dev mode (requires local Redis + QuestDB)
cd aggregator     && make run
cd candle-service && make run

# Tests
cd aggregator     && go test ./...
cd candle-service && go test ./...
```
