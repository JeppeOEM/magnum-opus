# Story 5.3: snapshot_1s QuestDB DDL

Status: done

## Story

As a service operator,
I want the `snapshot_1s` QuestDB table created via the migration runner on startup,
so that the OHLCV accumulator (Story 5-6) and all later feature stories have the correct schema from the very first deploy, and ILP upsert deduplication works correctly.

## Acceptance Criteria

1. `candle-service/migrations/001_snapshot_1s.sql` exists and contains a single `CREATE TABLE IF NOT EXISTS snapshot_1s` DDL statement (no semicolon-separated multi-statement content).
2. The DDL declares `TIMESTAMP(ts) PARTITION BY DAY TTL 30d WAL` — WAL required for ILP upsert; TTL 30d for automatic data expiry; daily partitioning for flush-to-Parquet alignment.
3. The DDL declares `DEDUP UPSERT KEYS(ts, exchange, symbol)` — required so ILP writes are idempotent; without this every ILP write appends unconditionally and correction writes produce duplicate rows.
4. `exchange` column is `SYMBOL CAPACITY 8 INDEX` and `symbol` column is `SYMBOL CAPACITY 256 INDEX`.
5. All non-identity columns are nullable (QuestDB default — no `NOT NULL` constraints anywhere).
6. The DDL includes all field groups required for the full feature set: identity (3), OHLCV (8), mid-price path (4), spread (4), OB best quotes (4 including `best_bid_open` and `best_ask_open`), OB depth open (8) and OB depth close (8), book shape (2), market impact (2), OFI (2), trade flow (2), block trades (2), trade distribution (7), volatility (4), OB activity (10), trade microstructure (3), quality (3 including `is_partial`).
7. `go test ./...` continues to pass with zero failures after the file is added (migrator L1 tests still pass; the `.gitkeep` in `migrations/` is replaced or coexists).
8. `go test -tags l2 ./...` continues to pass — migrator L2 tests (which apply SQL files against httptest servers) handle the file correctly.

## Tasks / Subtasks

- [x] Create `candle-service/migrations/001_snapshot_1s.sql` (AC: 1–6)
  - [x] Remove `.gitkeep` from `candle-service/migrations/` (git housekeeping)
  - [x] Write the complete DDL per the schema in Dev Notes
  - [x] Verify: exactly one SQL statement (no `;` except at the very end if desired — QuestDB REST `/exec` accepts a single statement without trailing semicolon; omit the semicolon to be safe and consistent with the no-multi-statement rule)
  - [x] Verify: `DEDUP UPSERT KEYS(ts, exchange, symbol)` is present
  - [x] Verify: `TIMESTAMP(ts) PARTITION BY DAY TTL 30d WAL` is present

- [x] Verify test suite still passes (AC: 7–8)
  - [x] Run `go test ./...` — expect: all existing tests pass
  - [x] Run `go test -tags l2 ./...` — expect: all L2 tests pass
  - [x] Confirm L1 migrator tests (`TestDiscoverFiles_*`) pick up the new file and parse version 1 correctly

## Dev Notes

### The Migration File Location

`candle-service/migrations/001_snapshot_1s.sql`

The migration runner (`internal/migrator`) reads `os.ReadDir("migrations")` (relative to process CWD) at startup. In Docker the CWD is `/` and the directory is mounted at `/migrations`. The file must be named exactly `001_snapshot_1s.sql` — version parsed as integer `1` from the leading `001` segment.

### Complete DDL

```sql
CREATE TABLE IF NOT EXISTS snapshot_1s (

    -- Identity (3)
    ts               TIMESTAMP,
    exchange         SYMBOL CAPACITY 8   INDEX,
    symbol           SYMBOL CAPACITY 256 INDEX,

    -- OHLCV (8)
    open             DOUBLE,
    high             DOUBLE,
    low              DOUBLE,
    close            DOUBLE,
    volume           DOUBLE,
    quote_volume     DOUBLE,
    trade_count      INT,
    twap             DOUBLE,

    -- Mid-price path (4)
    mid_price_open   DOUBLE,
    mid_price_high   DOUBLE,
    mid_price_low    DOUBLE,
    vwmp             DOUBLE,

    -- Spread (4)
    spread_high      DOUBLE,
    spread_low       DOUBLE,
    spread_mean      DOUBLE,
    effective_spread DOUBLE,

    -- OB best quotes open + close (4)
    -- best_bid_open and best_ask_open are required for idle-second semantics:
    -- on seconds with zero ticks, these carry from last-known OB state (not null).
    best_bid_open    DOUBLE,
    best_ask_open    DOUBLE,
    best_bid         DOUBLE,
    best_ask         DOUBLE,

    -- OB depth at open (8)
    bid_depth_l1_open     DOUBLE,
    ask_depth_l1_open     DOUBLE,
    bid_depth_l2_open     DOUBLE,
    ask_depth_l2_open     DOUBLE,
    bid_depth_top10_open  DOUBLE,
    ask_depth_top10_open  DOUBLE,
    bid_depth_total_open  DOUBLE,
    ask_depth_total_open  DOUBLE,

    -- OB depth at close (8)
    bid_depth_l1_close    DOUBLE,
    ask_depth_l1_close    DOUBLE,
    bid_depth_l2_close    DOUBLE,
    ask_depth_l2_close    DOUBLE,
    bid_depth_top10_close DOUBLE,
    ask_depth_top10_close DOUBLE,
    bid_depth_total_close DOUBLE,
    ask_depth_total_close DOUBLE,

    -- Book shape (2)
    weighted_bid_price    DOUBLE,
    weighted_ask_price    DOUBLE,

    -- Market impact (2)
    depth_to_1pct_bid     DOUBLE,
    depth_to_1pct_ask     DOUBLE,

    -- Order flow imbalance (2)
    ofi                   DOUBLE,
    ofi_l1                DOUBLE,

    -- Trade flow (2)
    buy_volume            DOUBLE,
    buy_count             INT,

    -- Block trades (2)
    block_buy_volume      DOUBLE,
    block_sell_volume     DOUBLE,

    -- Trade distribution (7)
    max_trade_size              DOUBLE,
    large_bid_orders            INT,
    large_ask_orders            INT,
    first_trade_offset_ms       INT,
    last_trade_offset_ms        INT,
    trade_clustering            DOUBLE,
    max_consecutive_run         INT,

    -- Volatility (4)
    realized_vol                DOUBLE,
    realized_skewness           DOUBLE,
    uptick_count                INT,
    downtick_count              INT,

    -- OB activity (10)
    bid_order_arrivals          INT,
    ask_order_arrivals          INT,
    bid_cancel_count            INT,
    ask_cancel_count            INT,
    ob_modify_count             INT,
    avg_bid_order_size          DOUBLE,
    avg_ask_order_size          DOUBLE,
    best_bid_changes            INT,
    best_ask_changes            INT,
    quote_stuff_ratio           DOUBLE,

    -- Trade microstructure (3)
    trade_sign_autocorr         DOUBLE,
    inter_trade_interval_std_ms DOUBLE,
    num_trade_price_levels      INT,

    -- Quality (3)
    is_partial                  BOOLEAN,
    gap_count                   INT,
    bar_count                   INT

) TIMESTAMP(ts) PARTITION BY DAY TTL 30d WAL
DEDUP UPSERT KEYS(ts, exchange, symbol)
```

**Column count: 76 total** (3 identity + 73 feature columns). The spec mentions "67-column schema" — that count is from an earlier Python design iteration. The Go redesign expanded OB depth to full open/close pairs, added `best_bid_open`, `best_ask_open`, `is_partial`, and `ofi_l1`, resulting in the above 76-column layout. All explicitly required field names from `project-context.md` are included. **Do not attempt to trim columns to hit 67 — use this full schema.**

### QuestDB DDL Syntax Rules

1. **Single statement only** — QuestDB REST `/exec` accepts exactly one DDL statement per request. This file contains one `CREATE TABLE IF NOT EXISTS` statement. No semicolons mid-statement. End without semicolon (cleaner) or with one at the end — both accepted.

2. **`DEDUP UPSERT KEYS` syntax** — placed AFTER the closing `)` and AFTER the `TIMESTAMP/PARTITION/WAL/TTL` clause. This is a QuestDB 8.x syntax; verify placement exactly as shown.

3. **`WAL` keyword** — required for DEDUP to work. Without WAL, the table is non-WAL and DEDUP is not supported.

4. **`TTL 30d`** — QuestDB 8.2.1 supports TTL for automatic partition expiry. Specified immediately after `PARTITION BY DAY`.

5. **SYMBOL CAPACITY** — `CAPACITY 8` for exchange (KuCoin + ByBit = 2 values, 8 is the minimum meaningful capacity), `CAPACITY 256` for symbol. `INDEX` enables fast GROUP BY / WHERE on the symbol column.

6. **BOOLEAN type** — supported in QuestDB 8.x for `is_partial`. Stores `true`/`false`, with null supported.

7. **All DOUBLEs are nullable** — QuestDB stores NaN internally for NULL doubles. INT stores `Long.MIN_VALUE` (−2,147,483,648) for null. Story 5-6 leaves all non-OHLCV columns null; later epics fill them in. This is correct behavior — not a bug.

### Existing Code Being Modified

**`candle-service/migrations/`** — currently contains only `.gitkeep`. The new `001_snapshot_1s.sql` is added. The `.gitkeep` file should be removed (`git rm candle-service/migrations/.gitkeep`) since the directory now has real content.

**No Go code changes.** The migration runner (Story 5-2) is already wired to apply all `.sql` files in `migrations/` on startup. No `main.go` changes, no `migrator.go` changes.

### Impact on Existing Migrator Tests

The L1 and L2 migrator tests use `t.TempDir()` — they create their own temporary directories with synthetic SQL files. They do NOT read from `candle-service/migrations/`. Therefore:
- `TestDiscoverFiles_*` tests are unaffected
- `TestRun_FreshAppliesTwoMigrations` is unaffected

The only concern: if any test hardcodes a path to the `migrations/` directory. A quick grep confirms no existing test does this.

### ILP Upsert Semantics (why DEDUP matters)

The QuestDB ILP writer (Story 5-6) writes rows via TCP/ILP (port 9009). ILP is append-only at the protocol level — it cannot update existing rows. The `DEDUP UPSERT KEYS(ts, exchange, symbol)` declaration in the DDL tells QuestDB's WAL layer to deduplicate incoming rows by `(ts, exchange, symbol)` during WAL commit. This is what makes crash-recovery correct: if the service crashes after writing a bar but before XACK, the restarted service writes the same bar again — QuestDB deduplicates it to one row.

Without `DEDUP UPSERT KEYS`, every ILP write appends, and a restart after crash produces duplicate rows for the same second.

### What This Story Does NOT Include

- Redis consumer, OHLCV accumulator, ILP writer — Story 5-6
- `flush_manifest` DDL — Story 9-1 (`002_flush_manifest.sql`)
- Prometheus metrics — Story 5-7
- Any Go code changes — no new `.go` files, no modified `.go` files

### References

- [Source: candle-service/project-context.md#QuestDB Schema] — snapshot_1s and migration runner rules
- [Source: candle-service/project-context.md#Idle Second Field Semantics] — OB field carry semantics (authoritative column list)
- [Source: _bmad-output/planning-artifacts/epics.md#Epic 5 Story 3] — story spec
- [Source: candle-service/internal/migrator/migrator.go] — current migration runner implementation
- [Source: _bmad-output/implementation-artifacts/stories/5-2-migration-runner.md] — migration runner story, fully done

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- Created `candle-service/migrations/001_snapshot_1s.sql` with complete 76-column DDL (76 = 3 identity + 73 feature columns). Note: story spec's "67-column" count is from an earlier Python design era; the Go candle service spec in project-context.md requires the full set including best_bid_open, best_ask_open, full open/close depth pairs, ofi_l1, is_partial.
- Removed `.gitkeep` (was untracked; never committed). Replaced by real migration file.
- `go test ./...` and `go test -tags l2 ./...` both pass. No Go code changes — migration runner (Story 5-2) already auto-discovers *.sql files.

### File List

- candle-service/migrations/001_snapshot_1s.sql (new)

## Change Log

- 2026-05-08: Story implemented — migration file created, tests verified passing
