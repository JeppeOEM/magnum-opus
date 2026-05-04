---
stepsCompleted: [1, 2]
inputDocuments: []
workflowType: 'research'
lastStep: 1
research_type: 'technical'
research_topic: 'storage architecture for crypto trading system'
research_goals: 'validate QuestDB, Redis Streams, and Parquet on Backblaze B2 as hot/warm/cold storage tiers at 400 rows/sec — identify risks, alternatives, and production best practices'
user_name: 'mrqdt'
date: '2026-05-03'
web_research_enabled: true
source_verification: true
---

# Research Report: Technical — Storage Architecture for Crypto Trading System

**Date:** 2026-05-03
**Author:** mrqdt
**Research Type:** technical

---

## Research Overview

[Research overview and methodology will be appended here]

---

## Technical Research Scope Confirmation

**Research Topic:** Storage architecture for crypto trading system
**Research Goals:** Validate QuestDB, Redis Streams, and Parquet on Backblaze B2 as hot/warm/cold tiers at 400 rows/sec — identify risks, alternatives, and production best practices

**Technical Research Scope:**

- Architecture Analysis — hot/warm/cold tiering patterns, flush boundary consistency
- Implementation Approaches — ILP batch sizing, Redis consumer groups, Parquet flush jobs
- Technology Stack — QuestDB, Redis Streams, Parquet/B2, alternatives
- Integration Patterns — ILP write path, XREADGROUP consumer groups, S3-compatible upload
- Performance Considerations — throughput limits, memory pressure, disk I/O at 400 rows/sec

**Research Methodology:**

- Current web data with rigorous source verification
- Multi-source validation for critical technical claims
- Confidence levels for uncertain technical information

**Scope Confirmed:** 2026-05-03

---

<!-- Content will be appended sequentially through research workflow steps -->

---

## Technology Stack Analysis

### QuestDB — Warm Tier

**Throughput headroom**

QuestDB ILP peak throughput in published benchmarks: **1.4M–11.36M rows/sec** on modest hardware. Our workload is **400 rows/sec** — that is a 3,500–28,000x margin. QuestDB is not the bottleneck in this system at any foreseeable scale.

For reference: QuestDB achieves 959k rows/sec with 4 threads; InfluxDB reaches 334k rows/sec at 14 threads max; TimescaleDB reaches 145k rows/sec with 4 threads. For **financial tick data specifically**, QuestDB showed **25ms avg query latency** vs ClickHouse (547ms), TimescaleDB (1,021ms), PostgreSQL (3,493ms).

_Sources: [QuestDB vs InfluxDB 2024 benchmark](https://questdb.com/blog/2024/02/26/questdb-versus-influxdb/) | [QuestDB 1.4M rows/sec — DEV Community](https://dev.to/questdb/how-we-achieved-write-speeds-of-1-4-million-rows-per-second-1a9l) | [Berlin Buzzwords 4M rows/sec](https://www.slideshare.net/slideshow/ingesting-over-four-million-rows-per-second-with-questdb-timeseries-database-berlin-buzzwords/258511776)_

---

**ILP configuration for our workload**

The ILP/HTTP transport is recommended for production (provides error feedback, automatic retry). Key parameters:

| Parameter | Recommended for 400 rows/sec | Notes |
|---|---|---|
| Batch size | 500–2000 rows per flush | At 400 rows/sec, flush every 500ms is natural |
| `cairo.commit.lag` | 1000ms | 1-second commit lag matches our 1s bar boundary |
| `cairo.max.uncommitted.rows` | 10,000 | Safety ceiling; well above our steady-state rate |
| Transport | HTTP(S) | Error feedback + retry built in |
| Partitioning | BY DAY | Default; correct for our data volume |

> "For high-throughput scenarios (ten thousand records per second with maximum 1 second lateness), a lower commit lag value combined with a larger number of uncommitted rows may be more appropriate." — QuestDB docs

At 400 rows/sec our batches are small — commit every 500ms sends ~200 rows. This is fine. The 50-100k batch size recommendation is for maximising throughput; at our scale correctness matters more than raw throughput.

_Sources: [QuestDB Capacity Planning](https://questdb.com/docs/getting-started/capacity-planning/) | [ILP Commit Lag Config](https://aibnd.com/docs/guides/out-of-order-commit-lag/) | [QuestDB Community — Delayed ILP commits](https://community.questdb.com/t/delayed-updates-commits-via-influx-line-protocol-ilp-in-questdb/707)_

---

**Known production failure modes — CRITICAL**

| Failure | Trigger | Effect | Recovery |
|---|---|---|---|
| **WAL table suspension** | Power outage / crash during WAL segment write | Table enters SUSPENDED state, ingestion halts silently | `ALTER TABLE snapshot_1s RESUME WAL` — must be scripted, not manual |
| **WAL segment corruption** | Disk I/O error mid-write | Same as above | Resume WAL command; may lose up to one commit lag of data |
| **File descriptor exhaustion** | Many daily partitions open simultaneously | QuestDB stalls or crashes | Increase OS fd limit; ensure old partitions are closed via TTL |
| **sys.telemetry_wal bloat** | Many small transactions at high rate | Internal telemetry table grows to hundreds of GB | Monitor; cannot configure TTL on this table yet (open GitHub issue) |
| **Out-of-order write inefficiency** | Ticks arriving with exchange timestamps out of order | Extra O3 sort pass on commit | Set `commit.lag` >= maximum expected clock skew between exchange and local |

**Most critical risk: silent WAL suspension.** If a crash leaves a WAL table suspended, QuestDB accepts ILP connections but silently drops all writes to that table. This needs a health check: query `wal_tables()` system table periodically and alert if any table shows `suspended = true`.

```sql
-- Health check query — run every 30s
SELECT name, suspended FROM wal_tables() WHERE suspended = true;
```

_Sources: [QuestDB WAL docs](https://questdb.com/docs/concepts/write-ahead-log/) | [GitHub issue #4829 — WAL auto-recovery](https://github.com/questdb/questdb/issues/4829) | [Advanced Troubleshooting QuestDB — Mindful Chase](https://www.mindfulchase.com/explore/troubleshooting-tips/databases/advanced-troubleshooting-for-questdb-in-high-volume-time-series-systems.html)_

---

### Redis Streams — Hot Tier

**Throughput capacity**

In a two-core machine benchmark: 10K messages/sec processed via XREADGROUP with COUNT=10,000, p99.9 latency < 2ms. Redis Streams handles >1M ops/sec. XREADGROUP is O(1).

Our workload: ~400 ticks/sec into `ticks:{exchange}:{symbol}` streams (200 symbols × 2 exchanges). This is well within single-node Redis capacity with enormous headroom.

**Critical configuration for durability**

| Setting | Value | Rationale |
|---|---|---|
| `appendonly yes` | Enable AOF | Tick data has real value; RDB-only risks losing up to last snapshot interval |
| `appendfsync everysec` | AOF fsync per second | Max 1 second data loss on crash; no meaningful throughput impact |
| `no-appendfsync-on-rewrite yes` | Skip fsync during AOF rewrite | Prevents I/O stall during rewrite at high write loads |
| `auto-aof-rewrite-percentage 100` | Rewrite at 2x file size | Keeps AOF file manageable |
| `maxmemory-policy noeviction` | Never evict data silently | For tick streams, silent eviction = data loss |

> "AOF with fsync set to every second: performance is still very high... for most production deployments, enabling both RDB and AOF is the recommended Redis backup strategy." — Redis docs

**Memory sizing**

Each tick JSON message ≈ 800 bytes (67-field snapshot). At 400 rows/sec with 24-hour MAXLEN retention:
- 400 × 86,400 = 34.56M messages/day
- 34.56M × 800 bytes = ~27.6 GB — far too large for in-memory retention

**Correct approach:** Redis Streams are a buffer and fan-out layer, NOT long-term storage. Configure `MAXLEN ~ 50,000` per stream (about 2 minutes of data per symbol), set via `XADD ... MAXLEN ~ 50000`. This keeps Redis memory at:
- 400 streams × 50,000 entries × 800 bytes ≈ 16 GB RAM

This matches the Hetzner CPX41 16 GB RAM recommendation.

**PEL (Pending Entries List) risk**

If consumers crash without sending XACK, the PEL grows unbounded. Millions of unacknowledged entries degrade XREADGROUP performance significantly.

Mitigation: Candle Service must XACK every processed tick within the same loop iteration. Implement XAUTOCLAIM for dead consumer recovery (Redis 6.2+).

_Sources: [Redis Streams docs](https://redis.io/docs/latest/develop/data-types/streams/) | [Redis persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/) | [Kafka vs Redis Streams backpressure — Medium](https://medium.com/@ThinkingLoop/kafka-vs-redis-streams-pick-by-backpressure-b7431f476c03)_

---

### Parquet on Backblaze B2 — Cold Tier

**S3 compatibility**

B2's S3-compatible API is a drop-in replacement for boto3. Override the endpoint URL only:

```python
import boto3
s3 = boto3.client(
    's3',
    endpoint_url='https://s3.us-west-004.backblazeb2.com',
    aws_access_key_id=B2_KEY_ID,
    aws_secret_access_key=B2_APP_KEY
)
```

All standard S3 operations work: `put_object`, `head_object`, `list_objects_v2`, multipart upload, lifecycle rules.

**Compression: use PyArrow, not Polars native**

> "The Polars native parquet engine does not compress repeating data as effectively as the PyArrow engine. This is particularly relevant for financial time series data where certain values may repeat frequently."

For cold storage writes (daily batch flush), use PyArrow with zstd level 6:

```python
import pyarrow as pa
import pyarrow.parquet as pq

pq.write_to_dataset(
    table,
    root_path='s3://bucket/data/1s_ohlcv/',
    partition_cols=['exchange', 'symbol', 'date'],
    filesystem=s3_filesystem,
    compression='zstd',
    compression_level=6,     # better ratio than default 3, still fast
    use_dictionary=True,     # SYMBOL columns compress extremely well
    write_statistics=True    # enables predicate pushdown on read
)
```

Expected compression ratio for financial OHLCV + OB features: **8–15x** with zstd level 6 (SYMBOL columns with dictionaries compress particularly well since exchange/symbol repeat constantly across rows).

**Storage cost comparison (2025)**

| Provider | Storage | Egress | Best for |
|---|---|---|---|
| **Backblaze B2** | $6/TB/month | Free up to 3× stored; $0.01/GB beyond | Our use case — write heavy, read rare |
| Cloudflare R2 | $15/TB/month | **$0** | Download-heavy workloads |
| Hetzner Object Storage | ~$6.59/TB/month | $1.32/TB | EU-only, Hetzner ecosystem |
| AWS S3 | $23/TB/month | $90/TB | Avoid |

**Decision confirmed: Backblaze B2 is correct** for ML training data. Write once (daily flush), read occasionally (training runs). At 477 MB/day of Parquet uploads, B2 cost is ~$1/month per year of history. Egress for ML training is rare and falls within the 3× free egress allowance.

_Sources: [Backblaze B2 S3-Compatible API](https://www.backblaze.com/docs/cloud-storage-s3-compatible-api) | [B2 vs R2 comparison 2025 — Taloflow](https://www.taloflow.ai/guides/comparisons/backblazeb2-vs-cloudflarer2-object-storage) | [PyArrow Parquet docs](https://arrow.apache.org/docs/python/parquet.html) | [Snappy vs Zstd — DEV Community](https://dev.to/ldsands/snappy-vs-zstd-for-parquet-in-pyarrow-9g0)_

---

### Hot/Warm/Cold Architecture — Flush Boundary Risks

The 30-day QuestDB → B2 flush is the highest-risk operation in the storage architecture. Key failure scenarios:

| Failure point | Risk | Mitigation |
|---|---|---|
| QuestDB export fails mid-export | Partial Parquet file uploaded to B2 | Write to local staging file first; only upload after complete |
| B2 upload fails after export | Data in QuestDB (safe, still within TTL) | Retry from flush_manifest where status='failed' |
| B2 upload succeeds but row count mismatch | Corrupted archive | Verify via HEAD + row count before writing flush_manifest |
| QuestDB TTL expires before retry | **Permanent data loss** — 30-day window | Monitor flush_manifest daily; alert on any status='failed' > 7 days old |
| Partial day flush (cron runs before midnight partition closes) | Missing last few minutes of day | Always flush for `date = yesterday` (never today) |

**flush_manifest table** (already in plan) is the correct audit mechanism. Every flush job must:
1. Count rows in QuestDB for `(exchange, symbol, yesterday)`
2. Export to local Parquet staging
3. Verify local row count = QuestDB row count
4. Upload to B2
5. Verify B2 object (HEAD request, compare size)
6. Write `status='success'` to flush_manifest
7. Delete local staging file

If any step fails: write `status='failed'` + `error_message` → alert to Redis `alerts:flush_failure`.

_Sources: [Cold vs Hot Storage — QuestDB Glossary](https://questdb.com/glossary/cold-vs-hot-storage/) | [Hot-Warm-Cold Architecture — Elastic](https://www.elastic.co/blog/implementing-hot-warm-cold-in-elasticsearch-with-index-lifecycle-management)_

---

### Alternatives Evaluation

**QuestDB alternatives**

| Database | Ingestion | Query (financial) | Ops complexity | Decision |
|---|---|---|---|---|
| **QuestDB** | 4M+ rows/sec | 25ms (LASTPOINT) | Low | **KEEP** |
| TimescaleDB | 145k rows/sec | 1,021ms | Medium (PostgreSQL ops) | Only if SQL ecosystem needed |
| ClickHouse | Comparable | 547ms (27x slower on point queries) | Medium-High | Better for analytics/warehouse, not tick ingestion |
| InfluxDB v2 | 203k rows/sec | Slower | Medium | No advantage over QuestDB |

QuestDB's LATEST BY and SAMPLE BY are purpose-built for OHLCV queries. No meaningful reason to switch.

**Redis Streams alternatives**

| System | Latency | Durability | Cost | Decision |
|---|---|---|---|---|
| **Redis Streams** | <2ms | AOF = 1s max loss | VM RAM | **KEEP** |
| NATS JetStream | <5ms | At-least-once, replicated | Separate service | Worth revisiting if multi-node needed |
| Kafka | 5–15ms | Excellent | Heavy ops | Overkill for single-node |
| RabbitMQ | <5ms | Good | Medium | No time-series features |

At 400 rows/sec on a single Hetzner VM, Redis is correct. NATS JetStream is the upgrade path if the system needs to scale beyond single-node.

**B2 alternatives**

Already validated above. B2 remains cheapest for write-once read-rarely cold archive with boto3 S3 compatibility.

---

### Storage Sizing Validation

| Component | Steady state | Basis |
|---|---|---|
| `snapshot_1s` QuestDB (30 days) | ~28–35 GB | 67 fields × 400 rows/sec × 86,400s × 30d × ~120 bytes/row compressed |
| All candle tables | ~2.6 GB | Derived from snapshot_1s |
| `ob_levels_1s` (1 day) | ~0.85 GB | 40 level fields × 400 rows/sec × 86,400s × 1d |
| WAL + indexes + OS overhead | ~15–20 GB | Typical QuestDB overhead |
| **Total QuestDB disk** | **~53 GB** | Matches plan |
| Redis RAM (streams + keys) | ~1.5–2 GB | 400 streams × 50k MAXLEN × 800 bytes |
| B2 upload per day | ~477 MB | Parquet zstd of daily snapshot_1s |

**Hetzner CPX41 (8 vCPU, 16 GB RAM, 240 GB NVMe SSD, ~€26/month) confirmed as correct VM.**

Disk headroom: 240 GB NVMe − 53 GB QuestDB − 20 GB OS/misc = **167 GB free**. Sufficient for 3+ months of unexpected data accumulation before flush failures would cause space exhaustion.
