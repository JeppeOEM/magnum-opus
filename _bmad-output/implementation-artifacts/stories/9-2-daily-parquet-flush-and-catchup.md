# Story 9.2: Daily Parquet Flush and Catch-up

Status: done

## Story

As the candle service,
I want to export all `snapshot_1s` rows for each past day to a zstd-compressed Parquet file uploaded to Backblaze B2,
So that all captured market data is durably archived and retrievable for offline analysis and model training.

## Acceptance Criteria

### AC 1 — New package `internal/flusher`

1. A new package `candle-service/internal/flusher/flusher.go` (package `flusher`) is created.
2. The package exports a single public type `Flusher` and constructor `New`.
3. Purity contract: no `time.Now()` in business logic (inject `clock flusher.Clock` interface, same pattern as cascade). No `init()`, no global state.

### AC 2 — Config additions

4. `internal/config/config.go` gains the following fields with the corresponding env var and default:

   | Field | Env var | Default |
   |---|---|---|
   | `B2KeyID` | `B2_KEY_ID` | `""` |
   | `B2AppKey` | `B2_APP_KEY` | `""` |
   | `B2Bucket` | `B2_BUCKET` | `""` |
   | `B2Endpoint` | `B2_ENDPOINT` | `""` |
   | `FlushTimeUTC` | `FLUSH_TIME_UTC` | `"03:00"` |
   | `FlushDateOverride` | `FLUSH_DATE_OVERRIDE` | `""` |

5. `B2KeyID` and `B2AppKey` must be in `sensitiveKeys` in `cmd/candle/main.go` so the `redactHandler` redacts them from logs. Specifically, add `"b2_key_id"` and `"b2_app_key"` to the `sensitiveKeys` slice (or verify existing entries cover them).

### AC 3 — Flusher constructor and Run

6. Constructor signature:
   ```go
   func New(cfg Config, rdb *goredis.Client, questHTTPAddr string, logger *slog.Logger, clk Clock) *Flusher
   ```
   where `Config` is a local `flusher.Config` struct (not `config.Config`) containing B2KeyID, B2AppKey, B2Bucket, B2Endpoint, FlushTimeUTC string, FlushDateOverride string.

7. `Run(ctx context.Context) error` — main loop:
   a. On startup, before entering the daily wait loop, run catch-up (AC 5).
   b. If `FlushDateOverride` is non-empty, parse as `YYYY-MM-DD`, flush that date, write manifest row, return (no scheduler loop).
   c. Otherwise, wait until the next `FlushTimeUTC` (parsed as `HH:MM` UTC), then flush yesterday's date. Loop until `ctx` is cancelled.

### AC 4 — Single-date flush

8. `flushDate(ctx context.Context, date time.Time) error` flushes all `snapshot_1s` rows for `date` (midnight-to-midnight UTC):
   a. Record start time.
   b. Query QuestDB via `/exp?query=SELECT+...` (CSV export endpoint) — see dev notes for full query.
   c. Parse CSV into `[]Snapshot1sRow` — see dev notes for struct definition.
   d. Serialize rows to Parquet (zstd, uncompressed page size ≥ 1 MB) using parquet-go. Write to a `bytes.Buffer` or OS temp file — developer's choice.
   e. Derive B2 path: `snapshot_1s/date=<YYYY-MM-DD>/data.parquet`.
   f. Upload to B2 using `s3manager.Uploader` wrapped in `context.WithTimeout(4 * time.Hour)`.
   g. **On upload context cancellation or any upload error**: call `s3.AbortMultipartUpload` with a **fresh `context.Background()`** (not the cancelled context) to prevent leaked B2 incomplete parts.
   h. Write `flush_manifest` row via QuestDB ILP (ts_flush=now, exchange='all', date_flushed=date, row_count=len(rows), b2_path=path, success=true/false, error_msg='' or err.Error(), duration_ms=elapsed).
   i. On success: update Redis key `candle:last_flush_date` to `date.Format("2006-01-02")`.
   j. Return nil on success, wrapped error on failure.

### AC 5 — Catch-up on startup

9. On startup (before the daily wait loop), determine `lastFlushDate`:
   a. Read Redis key `candle:last_flush_date`. If present and parseable as `YYYY-MM-DD`, set `lastFlushDate`.
   b. If Redis key absent or unreadable: query `flush_manifest` for the most recent `success=true` row (ORDER BY date_flushed DESC LIMIT 1). If found, use that `date_flushed` as `lastFlushDate`.
   c. If `flush_manifest` is also empty (true first run): skip catch-up entirely.
10. For each calendar day D from `lastFlushDate + 1 day` up to (but not including) today UTC:
    a. Query `flush_manifest`: if a `success=true` row exists for D, skip (idempotent).
    b. Otherwise, call `flushDate(ctx, D)`. Log result. Continue to next D even on failure.

### AC 6 — Wire into main.go

11. In `cmd/candle/main.go`, after the migration step and Redis connection:
    - If `cfg.B2KeyID != ""` AND `cfg.B2Bucket != ""`: construct a `flusher.Flusher` and run it in a goroutine: `go flusher.Run(ctx)`.
    - If credentials are absent, skip — flusher is disabled. Log at INFO: `"flush disabled: B2_KEY_ID or B2_BUCKET not set"`.

12. Flusher errors returned from `Run` are logged but do not cause `os.Exit` — the candle service continues operating without the flusher.

### AC 7 — L1 tests

13. `flusher_test.go` (package `flusher`, no build tag) has at least 3 L1 tests that do not hit real B2 or QuestDB:
    - **`TestNextFlushTime`**: given a `FlushTimeUTC = "03:00"` and a known wall clock, assert `nextFlushTime()` returns the correct next 03:00 UTC. Test at least: before 03:00 same day (returns today 03:00), after 03:00 same day (returns tomorrow 03:00).
    - **`TestFlushDateRange`**: given `lastFlushDate = "2026-05-04"` and today = `2026-05-07`, assert catch-up returns dates `[2026-05-05, 2026-05-06]` (today excluded).
    - **`TestB2PathFormat`**: given date `2026-05-07`, assert `b2Path(date)` returns `"snapshot_1s/date=2026-05-07/data.parquet"`.

### AC 8 — Build and test gates

14. `go build ./...` compiles clean.
15. `make test-l1` passes with all new L1 tests.

---

## Dev Notes

### Why QuestDB `/exp` (CSV) endpoint

QuestDB supports two HTTP query endpoints:
- `/exec?query=...` — returns JSON; field names repeated per row, expensive at scale.
- `/exp?query=...` — returns CSV; first row is header, subsequent rows are values.

Use `/exp` for the daily flush — it streams efficiently and avoids JSON overhead for potentially millions of rows.

Full query:
```
GET http://<questHTTPAddr>/exp?query=SELECT+ts,exchange,symbol,open,high,low,close,...+FROM+snapshot_1s+WHERE+ts+%3E%3D+<date_start_us>+AND+ts+%3C+<date_end_us>+ORDER+BY+ts
```
where `date_start_us` and `date_end_us` are Unix microseconds (QuestDB `TIMESTAMP` is microseconds):
```go
dateStartUs := date.UTC().UnixMicro()
dateEndUs   := date.UTC().Add(24 * time.Hour).UnixMicro()
```

Use `SELECT *` or list all columns explicitly. Listing explicitly is safer to avoid column order surprises if QuestDB schema evolves.

### Snapshot1sRow struct (parquet-go)

Define `Snapshot1sRow` in `internal/flusher/row.go`. Use `parquet:"<column_name>"` struct tags matching snapshot_1s column names exactly. QuestDB DOUBLE → Go `float64`; INT → Go `int32`; LONG → Go `int64`; BOOLEAN → Go `bool`; SYMBOL/STRING → Go `string`; TIMESTAMP → Go `int64` (microseconds since epoch).

All DOUBLE, INT, LONG, STRING columns from snapshot_1s are nullable — use pointer types (`*float64`, `*int32`, `*int64`, `*string`). parquet-go handles Go pointer → Parquet optional column automatically.

`ts` is non-null (designated timestamp) — use `int64` (not pointer).
`exchange` and `symbol` are non-null SYMBOL — use `string`.
`is_partial`, `gap_count`, `bar_count` are quality fields written on every row — use non-pointer types.

Example for first 4 fields:
```go
type Snapshot1sRow struct {
    TS          int64    `parquet:"ts"`
    Exchange    string   `parquet:"exchange"`
    Symbol      string   `parquet:"symbol"`
    Open        *float64 `parquet:"open"`
    High        *float64 `parquet:"high"`
    // ... all 76 fields in DDL order
}
```

### CSV parsing

QuestDB `/exp` returns CSV with header row. Use `encoding/csv` from stdlib. Column positions match the SELECT list. For nullable DOUBLE/INT/LONG columns, empty CSV cells (`,,`) map to nil pointers. For `ts` (TIMESTAMP), QuestDB returns ISO 8601 string (e.g., `2026-05-07T00:00:00.000000Z`); parse with `time.Parse(time.RFC3339Nano, ...)` then `.UnixMicro()`.

### Parquet serialization (parquet-go)

```go
import "github.com/parquet-go/parquet-go"

buf := &bytes.Buffer{}
w := parquet.NewGenericWriter[Snapshot1sRow](buf,
    parquet.Compression(&parquet.Zstd),
)
if _, err := w.Write(rows); err != nil { ... }
if err := w.Close(); err != nil { ... }
// buf now contains the Parquet file bytes
```

### B2 upload (AWS SDK v2, S3-compatible)

B2 uses an S3-compatible endpoint. Configure the SDK with:
```go
customResolver := aws.EndpointResolverWithOptionsFunc(func(service, region string, options ...interface{}) (aws.Endpoint, error) {
    return aws.Endpoint{URL: cfg.B2Endpoint, SigningRegion: "us-east-1"}, nil
})
awsCfg, err := awsconfig.LoadDefaultConfig(ctx,
    awsconfig.WithEndpointResolverWithOptions(customResolver),
    awsconfig.WithCredentialsProvider(credentials.NewStaticCredentialsProvider(cfg.B2KeyID, cfg.B2AppKey, "")),
)
client := s3.NewFromConfig(awsCfg)
uploader := manager.NewUploader(client)
```

Upload with 4h timeout and AbortMultipartUpload on cancel:
```go
uploadCtx, cancel := context.WithTimeout(ctx, 4*time.Hour)
defer cancel()

_, err = uploader.Upload(uploadCtx, &s3.PutObjectInput{
    Bucket: &cfg.B2Bucket,
    Key:    &b2Path,
    Body:   bytes.NewReader(parquetBytes),
})
if err != nil {
    // Always abort with fresh context — uploadCtx may be cancelled.
    abortCtx, abortCancel := context.WithTimeout(context.Background(), 30*time.Second)
    defer abortCancel()
    _ = client.AbortMultipartUpload(abortCtx, &s3.AbortMultipartUploadInput{
        Bucket: &cfg.B2Bucket,
        Key:    &b2Path,
    })
    return fmt.Errorf("B2 upload failed: %w", err)
}
```

**Critical:** `AbortMultipartUpload` must use a fresh `context.Background()` — not `uploadCtx` (which is cancelled or timed out). Using the cancelled context silently fails the abort and leaks incomplete B2 parts.

### flush_manifest write via QuestDB ILP

Use the existing QuestDB ILP writer pattern. However, `internal/flusher` should NOT import `internal/writer/questdb` directly (avoid coupling). Instead, accept a `manifestFn func(row ManifestRow)` callback in the Flusher constructor, and let `main.go` wire it to a QuestDB ILP write.

Alternatively, for simplicity, write directly to QuestDB HTTP `/exec` using a parameterized INSERT:
```sql
INSERT INTO flush_manifest VALUES (now(), 'all', <date_flushed_us>, <row_count>, '<b2_path>', <success>, '<error_msg>', <duration_ms>)
```
This avoids ILP dependency in the flusher package and keeps it testable.

### Redis last_flush_date key

Key: `candle:last_flush_date`  
Value: `"YYYY-MM-DD"` (e.g., `"2026-05-06"`)  
Write via `rdb.Set(ctx, "candle:last_flush_date", date.Format("2006-01-02"), 0)` (no expiry).

### FLUSH_DATE_OVERRIDE

When `FLUSH_DATE_OVERRIDE=2026-05-03` is set:
- Flush exactly `2026-05-03`, write manifest row.
- Print result to log and return — the service exits the flusher goroutine. (Useful for testing: start service with this env var, verify flush_manifest success row and B2 object.)
- Do NOT run the scheduler loop or catch-up.

### nextFlushTime pure function

```go
func nextFlushTime(now time.Time, flushTimeUTC string) (time.Time, error) {
    parts := strings.Split(flushTimeUTC, ":")
    // parse hour, minute, build today's flush time in UTC, return it or +1d if past
}
```

Test this function exhaustively: before flush time, exactly at flush time, after flush time.

### FlushDateRange utility

```go
func flushDateRange(lastFlushDate, today time.Time) []time.Time {
    // returns dates (d+1, d+2, ... today-1) in ascending order
}
```

Test: lastFlush = yesterday → empty slice. lastFlush = 2d ago → one date. lastFlush = 4d ago → three dates.

---

## Tasks / Subtasks

- [x] Add B2/Flush config fields to `internal/config/config.go` and verify `sensitiveKeys` covers `b2_key_id`/`b2_app_key` (AC 2)
- [x] Create `internal/flusher/row.go` with `Snapshot1sRow` struct matching all 76 snapshot_1s columns (AC 4c)
- [x] Create `internal/flusher/flusher.go` with `Flusher`, `New`, `Run`, `flushDate`, catch-up logic (AC 1–5)
- [x] Implement `nextFlushTime`, `flushDateRange`, `b2Path` pure helpers (AC 7, testable)
- [x] Wire Flusher in `cmd/candle/main.go` (AC 6)
- [x] Add L1 tests: `TestNextFlushTime`, `TestFlushDateRange`, `TestB2PathFormat` (AC 7)
- [x] `go build ./...` and `make test-l1` pass (AC 8)

### Review Findings

- [x] [Review][Patch] Empty-day: skip B2 upload, manifest write, and Redis update entirely when `len(rows)==0`; log INFO "no data rows for date, skipping flush" [internal/flusher/flusher.go:161-213]
- [x] [Review][Patch] Silent TS=0 on timestamp parse failure — if neither ISO 8601 layout matches, `tsUs` stays 0 and the row is silently written with `ts=1970-01-01` [internal/flusher/flusher.go:579-587]
- [x] [Review][Patch] HTTP client 60s timeout too short for full-day CSV export — timeout fires mid-stream on large datasets, causing partial-data flush that fails cleanly but wastes retry time [internal/flusher/flusher.go:71]
- [x] [Review][Patch] `writeManifestRow` ignores QuestDB /exec error body — QuestDB returns HTTP 200 with a JSON `{"error":"..."}` body on failure; status code check alone is insufficient [internal/flusher/flusher.go:394-396]
- [x] [Review][Patch] Parquet page size not configured — spec requires uncompressed page size ≥ 1 MB; parquet-go default is 256 KB [internal/flusher/flusher.go:271]
- [x] [Review][Defer] Full-day in-memory materialization without size bound [internal/flusher/flusher.go:162-186] — deferred, architectural constraint
- [x] [Review][Defer] `errMsg` SQL-escape uses only single-quote doubling — other characters (backslash, semicolon) unescaped; low risk since errMsg is internal Go error strings [internal/flusher/flusher.go:378-382] — deferred, low practical risk
- [x] [Review][Defer] `s3Client()` allocates new HTTP transport per flush — no TLS session reuse across daily flushes [internal/flusher/flusher.go:282-291] — deferred, once-daily op, negligible overhead
- [x] [Review][Defer] `FlushDateOverride` accepts today or future date without guard — partial-day data flushed silently [internal/flusher/flusher.go:83-88] — deferred, operator-controlled override
- [x] [Review][Defer] `readManifestLastSuccess` ORDER BY date_flushed may return older backfill target when FlushDateOverride used — triggers unnecessary catch-up range [internal/flusher/flusher.go:446] — deferred, idempotency guard handles re-attempts
- [x] [Review][Defer] `publishFlushAlert` calls `f.clk.Now()` — Clock concurrency not documented [internal/flusher/flusher.go:415] — deferred, production clock is safe, no test fake planned
- [x] [Review][Defer] `parseInt32` silently drops values exceeding int32 range [internal/flusher/flusher.go:696-706] — deferred, OB activity counts well within int32 range in practice
- [x] [Review][Defer] `flush_manifest` written via HTTP /exec INSERT instead of ILP — dev notes explicitly authorize this as the simpler alternative [internal/flusher/flusher.go:371-397] — deferred, dev-note-authorized alternative

---

## Dev Agent Record

### Completion Notes

All 8 ACs satisfied. Key implementation decisions:
- `parquet.Compression(&parquet.Zstd)` required — `CompressionCodec` has pointer receiver in v0.24.0
- Manual multipart upload (CreateMultipartUpload → UploadPart → CompleteMultipartUpload) used instead of `s3manager.Uploader` so we hold the upload ID for clean `AbortMultipartUpload` with fresh context on failure
- Inline `staticCreds` implements `aws.CredentialsProvider` — no `aws-sdk-go-v2/credentials` module needed
- CSV header map for column→index (not positional) for robustness against QuestDB column order changes
- `publishFlushAlert` is in flusher.go (story 9-3 extends it with callbacks)
- Makefile updated to include `./internal/flusher/...` in L1 targets
- `go build ./...` clean; `make test-l1` passes (4 packages, exit 0)

### File List

- `candle-service/internal/config/config.go` — B2/Flush fields added
- `candle-service/internal/flusher/row.go` — Snapshot1sRow struct (76 columns)
- `candle-service/internal/flusher/flusher.go` — Flusher, New, Run, flushDate, helpers
- `candle-service/internal/flusher/flusher_test.go` — 11 L1 tests
- `candle-service/cmd/candle/main.go` — flusher import + wiring, sensitiveKeys updated
- `candle-service/Makefile` — flusher added to L1 test targets

### Change Log

- 2026-05-08: Implemented internal/flusher package with Parquet export, B2 multipart upload (fresh-context abort), QuestDB CSV query, catch-up logic, and 11 L1 tests; wired in main.go
- 2026-05-08: Code review patches — skip empty-day flush; return error on ts parse failure; remove 60s HTTP client timeout (4h per-query context); decode QuestDB /exec error body in writeManifestRow; add PageBufferSize(1MB) to Parquet writer
