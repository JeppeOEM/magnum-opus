# Verification Matrix

Every testable claim about this system, cross-referenced with how it is verified.

**Status legend:**
- `AUTOMATED` — verified by a test or CI check that runs on every commit
- `MANUAL` — verified by a human procedure (link to procedure)
- `UNTESTED` — known risk; no verification exists

**UNTESTED rows are known risks.** Every UNTESTED is a decision, not an oversight.

---

## Aggregator

| # | Claim | Method | Status | Evidence |
|---|-------|--------|--------|----------|
| A-01 | OrderBook sequences deduplicate correctly (seq ≤ lastSeq discarded silently) | Unit test | AUTOMATED | `aggregator/internal/orderbook/orderbook_test.go` |
| A-02 | OrderBook Apply ignores unknown Side values | Unit test | AUTOMATED | `orderbook_test.go: TestApply_UnknownSide` |
| A-03 | OrderBook Apply ignores empty Price | Unit test | AUTOMATED | `orderbook_test.go: TestApply_EmptyPrice` |
| A-04 | Size "0" removes a price level | Unit test | AUTOMATED | `orderbook_test.go: TestApply_RemoveLevel` |
| A-05 | Snapshot() returns a deep copy (mutations don't affect book) | Unit test | AUTOMATED | `orderbook_test.go: TestSnapshot_Independence` |
| A-06 | Reconnect machine: Initial → Buffering on NeedsSnapshotNow | Unit test | AUTOMATED | `reconnect/*_test.go` |
| A-07 | Reconnect machine: double-NeedsSnapshot preserves buffer | Unit test | AUTOMATED | `reconnect/*_test.go` |
| A-08 | Reconnect machine: stale snapshot triggers merge_error signal | Unit test | AUTOMATED | `reconnect/*_test.go` |
| A-09 | Reconnect machine: MergeSnapshot transitions to Live | Unit test | AUTOMATED | `reconnect/*_test.go` |
| A-10 | Gap detector: seq == prev+1 returns nil (no gap) | Unit test | AUTOMATED | `gapdetector/*_test.go` |
| A-11 | Gap detector: nil clock panics | Unit test | AUTOMATED | `gapdetector/*_test.go` |
| A-12 | Config: missing required env vars returns error listing all of them | Unit test | AUTOMATED | `config/config_test.go` |
| A-13 | Credential.Format redacts ALL fmt verbs | Unit test | AUTOMATED | `config/config_test.go` |
| A-14 | Credential.LogValue returns REDACTED | Unit test | AUTOMATED | `config/config_test.go` |
| A-15 | Credential.MarshalText returns REDACTED | Unit test | AUTOMATED | `config/config_test.go` |
| A-16 | Metrics.PreInit pre-initializes all label combinations to 0 | Unit test | AUTOMATED | `metrics/metrics_test.go` |
| A-17 | RecordGap filters external_disconnect from health window | Unit test | AUTOMATED | `metrics/metrics_test.go` |
| A-18 | RecordGap filters internal_buffer_overflow from health window | Unit test | AUTOMATED | `metrics/metrics_test.go` |
| A-19 | GapHealthy returns 0.0 after internal_merge_error within window | Unit test | AUTOMATED | `metrics/metrics_test.go` |
| A-20 | slogredact handler redacts all credentials in log output | Unit test | AUTOMATED | `slogredact/handler_test.go` |
| A-21 | Tick fanout: full channel drops tick with WARN log | Unit test | AUTOMATED | `coordinator/*_test.go` |
| A-22 | Coordinator: OBPublisher receives snapshot after every EventTypeUpdate | Unit test | AUTOMATED | `coordinator/obpublisher_test.go` |
| A-23 | Startup gate: exits cleanly on SIGTERM during gate wait | Integration test | AUTOMATED | `aggregator/internal/live/*_test.go` |
| A-24 | Startup gate: fails with correct error after timeout | Integration test | AUTOMATED | `live/*_test.go` |
| A-25 | time.Now() not called in any internal/ package | Static check | AUTOMATED | `scripts/audit-docs.sh` check #1 |
| A-26 | All 4 gap causes are defined | Static check | AUTOMATED | `scripts/audit-docs.sh` check #7 |
| A-27 | Credential type used for all API key fields | Static check | AUTOMATED | `scripts/audit-docs.sh` check #3 |
| A-28 | Redis ticks stream XADD includes all required fields | Integration test (L2) | AUTOMATED | `writer/redis/*_l2_test.go` |
| A-29 | QuestDB ILP writer retries on transient failures | Unit test | AUTOMATED | `writer/questdb/*_test.go` |
| A-30 | KUCOIN_PUBLIC=true uses bullet-public endpoint | Unit test | AUTOMATED | `config/config_test.go` |
| A-31 | OrderBook not shared between goroutines | Static check | AUTOMATED | `scripts/audit-docs.sh` check #2 |
| A-32 | Bybit subscription confirmed by ACK within timeout | Integration test | AUTOMATED | `exchange/bybit/*_test.go` |
| A-33 | KuCoin token renewal retries before triggering reconnect | Unit test | AUTOMATED | `exchange/kucoin/*_test.go` |

---

## Candle-service

| # | Claim | Method | Status | Evidence |
|---|-------|--------|--------|----------|
| C-01 | Consumer XACKs before dispatch | Static check | AUTOMATED | `scripts/audit-docs.sh` check #4 |
| C-02 | Consumer deduplicates messages within 1-second window | Unit test | AUTOMATED | `consumer/*_test.go` |
| C-03 | Gap dedup ZSET prevents double-apply of same gap | Unit test | AUTOMATED | `consumer/*_test.go` |
| C-04 | Shadow lag returns 0 when consumer is caught up | Unit test | AUTOMATED | `consumer/*_test.go` |
| C-05 | Promote transitions shadow → XREADGROUP correctly | Unit test | AUTOMATED | `consumer/*_test.go` |
| C-06 | XAutoClaimPending loops until next cursor is "0-0" | Unit test | AUTOMATED | `consumer/*_test.go` |
| C-07 | Accumulator Flush(false) resets state for next bar | Unit test | AUTOMATED | `accumulator/*_test.go` |
| C-08 | OFI computed correctly from pre/post BestQuote | Unit test | AUTOMATED | `accumulator/*_test.go` |
| C-09 | realized_vol uses mid-price returns (not trade prices) | Unit test | AUTOMATED | `accumulator/*_test.go` |
| C-10 | quote_stuff_ratio = (arrivals+cancels) / trade_count | Unit test | AUTOMATED | `accumulator/*_test.go` |
| C-11 | max_consecutive_run counts trade sign runs (not price direction) | Unit test | AUTOMATED | `accumulator/*_test.go` |
| C-12 | gap_count increments on GapMarker | Unit test | AUTOMATED | `accumulator/*_test.go` |
| C-13 | is_partial=true for 250ms flush, false for full bar | Unit test | AUTOMATED | `accumulator/*_test.go` |
| C-14 | QuestDB DEDUP overwrites partial bar with complete bar | Integration test (L2) | AUTOMATED | `writer/questdb/*_l2_test.go` |
| C-15 | Migration files are sequential with no gaps | Static check | AUTOMATED | `scripts/audit-docs.sh` check #6 |
| C-16 | All tables have DEDUP UPSERT KEYS | Static check | AUTOMATED | `scripts/audit-docs.sh` check #11 |
| C-17 | Flusher: catch-up runs on startup for missed days | Unit test | AUTOMATED | `flusher/*_test.go` |
| C-18 | Flusher: AbortMultipartUpload uses fresh context | Code review | MANUAL | See flusher.go package doc |
| C-19 | Cascade produces correct 1m OHLCV from 1s bars | Unit test | AUTOMATED | `cascade/*_test.go` |
| C-20 | ApplyGap: only internal_merge_error resets snapshotSeen | Unit test | AUTOMATED | `orderbook/*_test.go` |
| C-21 | Footprint: POC is price level with highest volume | Unit test | AUTOMATED | `features/footprint_test.go` |
| C-22 | Footprint: value area covers 70% of bar volume | Unit test | AUTOMATED | `features/footprint_test.go` |
| C-23 | Imbalance detection threshold is consistent | Unit test | AUTOMATED | `features/imbalance_test.go` |
| C-24 | stream_overflow gap emitted when len > 45,000 | Unit test | AUTOMATED | `consumer/*_test.go` |
| C-25 | B2 daily export uses zstd-compressed Parquet format | Integration test (L2) | AUTOMATED | `flusher/*_l2_test.go` |

---

## Bot-service

| # | Claim | Method | Status | Evidence |
|---|-------|--------|--------|----------|
| B-01 | BusManager queue overflow drops oldest + injects GapMarker | Unit test | AUTOMATED | `tests/test_bus_manager.py` |
| B-02 | BusManager persistent Redis failure (3× cap) sends SIGTERM | Unit test | AUTOMATED | `tests/test_bus_manager.py` |
| B-03 | BaseStrategy lookback gate suppresses handlers until min_lookback | Unit test | AUTOMATED | `tests/test_base_strategy.py` |
| B-04 | NaN guard suppresses handler when most recent row has NaN | Unit test | AUTOMATED | `tests/test_base_strategy.py` |
| B-05 | Gap invalidates signal; clean bars restore it | Unit test | AUTOMATED | `tests/test_base_strategy.py` |
| B-06 | Heartbeat SIGTERM after 10s non-ACK | Unit test | AUTOMATED | `tests/test_base_strategy.py` |
| B-07 | subscribe() timeout raises SubscribeTimeoutError | Unit test | AUTOMATED | `tests/test_base_strategy.py` |
| B-08 | max_position_pct must be in (0, 1] | Unit test | AUTOMATED | `tests/test_base_strategy.py` |
| B-09 | Paper exchange simulates fill with latency in [min, max] | Unit test | AUTOMATED | `tests/test_paper_exchange.py` |
| B-10 | Emergency close retries until success (no give-up) | Unit test | AUTOMATED | `tests/test_base_strategy.py` |
| B-11 | Emergency close is no-op for paper trading | Unit test | AUTOMATED | `tests/test_base_strategy.py` |
| B-12 | emergency_close_in_flight prevents concurrent threads for same symbol | Unit test | AUTOMATED | `tests/test_base_strategy.py` |
| B-13 | Daily loss limit rejects orders when exceeded | Unit test | AUTOMATED | `tests/test_order_worker.py` |
| B-14 | Max notional limit rejects oversized orders | Unit test | AUTOMATED | `tests/test_order_worker.py` |
| B-15 | Reconciliation detects positions from previous run | Integration test | AUTOMATED | `tests/test_reconciliation.py` |
| B-16 | redact_credentials processor redacts all SecretStr values from logs | Unit test | AUTOMATED | `tests/test_config.py` |
| B-17 | Funding rate poller delivers FundingRate events to strategies | Unit test | AUTOMATED | `tests/test_signals.py` |
| B-18 | Signal generator handles missing funding data gracefully | Unit test | AUTOMATED | `tests/test_signals.py` |

---

## Gateway

| # | Claim | Method | Status | Evidence |
|---|-------|--------|--------|----------|
| G-01 | Hub routes messages to subscribed clients only | Unit test | AUTOMATED | `gateway/internal/hub/hub_test.go` |
| G-02 | Hub drops message gracefully when client send buffer is full | Unit test | AUTOMATED | `hub_test.go` |
| G-03 | Binary codec encodes/decodes OB snapshot round-trip | Unit test | AUTOMATED | `gateway/internal/codec/codec_test.go` |
| G-04 | Hub delivers last snapshot to new subscribers immediately | Unit test | AUTOMATED | `hub_test.go` |
| G-05 | WebSocket WritePump exits on context cancel | Unit test | AUTOMATED | `hub_test.go` |

---

## Cross-service / System

| # | Claim | Method | Status | Evidence |
|---|-------|--------|--------|----------|
| S-01 | Redis stream key format matches between producer and consumer | Static check | AUTOMATED | `scripts/audit-docs.sh` check #5 |
| S-02 | Full pipeline: tick → Redis stream → candle bar → QuestDB | Integration test (L2/L4) | AUTOMATED | `candle-service/internal/l4/*_test.go` |
| S-03 | Blue-green promote: new slot catches up before going live | Integration test | MANUAL | `docs/nasa/OPERATIONAL-PROCEDURES/deploy.md` |
| S-04 | All services start cleanly from docker-compose | Smoke test | MANUAL | `make up && sleep 30 && curl aggregator:8080/healthz` |
| S-05 | Credential values never appear in any log output | Runtime check | MANUAL | `grep -r 'REDACTED\|YOUR_KEY' logs/` — should NEVER find raw key |
| S-06 | Candle DEDUP: partial bar overwritten by full bar within 1 second | QuestDB query | MANUAL | `SELECT count(*) FROM snapshot_1s WHERE is_partial=true AND ts < now()-60s` — expect 0 |
| S-07 | QuestDB WAL auto-resumes after suspension | Runbook | MANUAL | `docs/nasa/OPERATIONAL-PROCEDURES/emergency.md §WAL-suspension` |
| S-08 | Daily B2 flush produces valid Parquet readable by pandas | Integration test | MANUAL | `python -c "import pandas as pd; df=pd.read_parquet('...')"; shape check` |
| S-09 | Gap fraction in live data < 1% over 24h | Grafana dashboard | MANUAL | Monitor `gap_count / bar_count` ratio in Grafana |

---

## Summary

| Status | Count | % |
|--------|-------|---|
| AUTOMATED | 56 | 87% |
| MANUAL | 8 | 13% |
| UNTESTED | 0 | 0% |

**All untested claims are tracked in this matrix.** When a new claim is documented,
it must have a row here with explicit status. UNTESTED is an explicit acknowledgment
of known risk, not an omission.
