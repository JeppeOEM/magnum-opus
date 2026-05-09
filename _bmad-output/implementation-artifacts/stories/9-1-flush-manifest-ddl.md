# Story 9.1: Flush Manifest DDL

Status: done

## Story

As the candle service,
I want a `flush_manifest` table in QuestDB that records every Parquet flush attempt (success or failure),
So that the daily flush loop can reliably determine catch-up state after a Redis reset without silently skipping data.

## Acceptance Criteria

### AC 1 — Migration file

1. A new file `candle-service/migrations/002_flush_manifest.sql` exists with exactly one DDL statement (migrator constraint: no semicolons inside the statement body).
2. The statement creates the table:

   ```sql
   CREATE TABLE IF NOT EXISTS flush_manifest (
       ts_flush     TIMESTAMP,
       exchange     SYMBOL,
       date_flushed TIMESTAMP,
       row_count    LONG,
       b2_path      STRING,
       success      BOOLEAN,
       error_msg    STRING,
       duration_ms  LONG
   ) TIMESTAMP(ts_flush) PARTITION BY YEAR WAL
   ```

3. `date_flushed` uses `TIMESTAMP` (not `DATE`) — QuestDB `DATE` stores epoch milliseconds without time zone; `TIMESTAMP` stores microseconds and is consistent with the rest of the schema. The daily flush date is stored as midnight UTC (e.g., `2026-05-07T00:00:00.000000Z`).
4. `error_msg` is `STRING` — QuestDB STRING columns are nullable by default. No explicit `NULL` or `NOT NULL` keyword is needed.
5. `PARTITION BY YEAR` — flush manifest rows are infrequent (1/day); year partitioning is appropriate.

### AC 2 — Migration runner picks it up automatically

6. The migration runner (Story 5-2) discovers migration files from the `migrations/` directory sorted by numeric prefix. `002_flush_manifest.sql` is applied after `001_snapshot_1s.sql` on service startup.
7. No changes to `internal/migrator/` are required — the file naming convention is sufficient.

### AC 3 — No code changes

8. No Go source files are modified in this story. The only change is the new SQL file.

### AC 4 — Build and test gates

9. `go build ./...` compiles clean (no Go changes, trivially passes).
10. `make test-l1` passes.

---

## Dev Notes

### Why `date_flushed` is TIMESTAMP not DATE

QuestDB `DATE` stores milliseconds since epoch with no explicit TZ, and it's less ergonomic to query than `TIMESTAMP`. Using `TIMESTAMP` for `date_flushed` with midnight UTC (e.g., `2026-05-07T00:00:00.000000Z`) is consistent with the rest of the schema and makes date comparison queries straightforward:

```sql
SELECT * FROM flush_manifest WHERE date_flushed = '2026-05-07T00:00:00.000000Z' AND success = true
```

### exchange column purpose

The `exchange` column is included for forward compatibility — future stories may flush per-exchange partitions. For the initial daily flush (all exchanges, all symbols in one file), leave `exchange` empty (`''`) or set to `'all'`. The catch-up query in Story 9-2 uses `WHERE success = true ORDER BY date_flushed DESC LIMIT 1` without filtering on exchange.

### Migrator behavior on CREATE TABLE IF NOT EXISTS

The migrator tracks applied migrations by filename in a `schema_migrations` QuestDB table. Once `002_flush_manifest.sql` is applied, subsequent restarts will not re-execute it. `CREATE TABLE IF NOT EXISTS` is idempotent even if the migrator tracking fails — safe to run twice.

### File must not end with trailing semicolon outside the statement

The migrator doc says "each migration file must contain exactly one DDL statement." The statement itself does not end with `;`. Write the CREATE TABLE block without a trailing semicolon to avoid confusion, though QuestDB REST accepts a trailing `;` fine.

---

## Tasks / Subtasks

- [x] Create `candle-service/migrations/002_flush_manifest.sql` with the `flush_manifest` DDL (AC 1–2)
- [x] `go build ./...` and `make test-l1` pass (AC 4)

### Review Findings

- [x] [Review][Defer] No DEDUP UPSERT KEYS — retried flushes produce duplicate rows; idempotency is application-level (9-2 checks `success=true` before inserting) — deferred, pre-existing design
- [x] [Review][Defer] PARTITION BY YEAR with no TTL — rows accumulate forever; 1 row/day volume makes this acceptable, TTL deferred to future ops story — deferred, pre-existing design
- [x] [Review][Defer] `success BOOLEAN` nullable — QuestDB WAL has no NOT NULL constraint; application always sets this field — deferred, QuestDB limitation
- [x] [Review][Defer] `error_msg STRING` unbounded — story 9-3 truncates at 512 chars at application level — deferred, pre-existing
- [x] [Review][Defer] `b2_path STRING` unconstrained — empty vs null indistinguishable in schema; application enforces correctness — deferred, pre-existing
- [x] [Review][Defer] `duration_ms LONG` signed, no non-negative guard — QuestDB has no CHECK constraints; application concern — deferred, pre-existing
- [x] [Review][Defer] SYMBOL CAPACITY hint absent on `exchange` — defaults to 128 vs 2–3 actual values; cosmetic inconsistency with snapshot_1s pattern — deferred, low priority

---

## Dev Agent Record

### Completion Notes

All 4 ACs satisfied. Single SQL migration file created with the flush_manifest DDL. Uses TIMESTAMP (not DATE) for date_flushed per AC 3. No Go code changes needed — the existing migration runner picks up 002_flush_manifest.sql automatically by numeric prefix ordering. `go build ./...` clean, `make test-l1` passes (exit 0).

### File List

- `candle-service/migrations/002_flush_manifest.sql` — new migration file

### Change Log

- 2026-05-08: Created 002_flush_manifest.sql with flush_manifest DDL (TIMESTAMP(ts_flush), PARTITION BY YEAR WAL)
