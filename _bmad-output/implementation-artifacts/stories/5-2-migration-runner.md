# Story 5.2: Migration Runner

Status: done

## Story

As a service operator,
I want the candle service to run QuestDB schema migrations automatically on startup,
so that the correct tables exist before any consumers or writers touch the database, and schema drift is caught immediately.

## Acceptance Criteria

1. `internal/migrator/` package exists with a `Migrator` struct; constructor accepts `*http.Client`, `httpAddr string` (host:port), and `migsDir string`; `Run(ctx context.Context) error` applies all pending migrations.
2. On startup, `cmd/candle/main.go` calls the migrator before proceeding past startup; if migration returns a non-context error, logs it and calls `os.Exit(1)`.
3. `schema_migrations` table is created in QuestDB on first run (idempotent: `IF NOT EXISTS`); schema: `version LONG, name STRING, applied_at TIMESTAMP, checksum STRING`, with `TIMESTAMP(applied_at) PARTITION BY YEAR WAL`.
4. Migration files in `migsDir` are discovered, sorted by filename (ascending), and applied in order; only unapplied migrations are executed (idempotent re-runs are a no-op).
5. SHA-256 checksum of each file is computed and stored on first apply; if a previously-applied migration's checksum no longer matches the file on disk, `Run` returns a descriptive error and the service exits non-zero — no migrations are applied during that run.
6. `internal/config/config.go` adds `QuestDBHTTPAddr string`, populated from `QUESTDB_HTTP_ADDR` env var (default `localhost:9000`); `cmd/candle/main.go` uses `cfg.QuestDBHTTPAddr` to construct the migrator.
7. L1 unit tests cover: SHA-256 checksum computation, migration filename parsing (version extraction, sorting), and the checksum-mismatch error path without any HTTP calls.
8. L2 tests (build tag `//go:build l2`) cover: fresh run applying two migrations, idempotent re-run, checksum mismatch detection, and QuestDB HTTP error propagation — all against `httptest.NewServer`.
9. `go test ./...` (L1) and `go test -tags l2 ./...` (L2) both pass with zero failures.
10. Context cancellation (SIGTERM during migration) causes `Run` to return `ctx.Err()` and `main.go` exits 0, not 1.

## Tasks / Subtasks

- [x] Add `QuestDBHTTPAddr` to `internal/config/` (AC: 6)
  - [x] Add `QuestDBHTTPAddr string` field to `Config` struct
  - [x] Load from `getEnv("QUESTDB_HTTP_ADDR", "localhost:9000")` in `Load()`
  - [x] Add assertion to `TestLoad_Defaults` and `TestLoad_EnvOverrides` in `config_test.go`

- [x] Implement `internal/migrator/migrator.go` (AC: 1, 3, 4, 5)
  - [x] `Migrator` struct with `client *http.Client`, `httpAddr string`, `migsDir string`
  - [x] `New(client *http.Client, httpAddr, migsDir string) *Migrator`
  - [x] `Run(ctx context.Context) error` — orchestrates the full migration sequence
  - [x] `exec(ctx, query string) error` — private: sends `GET http://<httpAddr>/exec?query=<urlencoded>`, returns error on non-200 or QuestDB error JSON
  - [x] `queryRows(ctx, query string) ([][]interface{}, error)` — private: sends query, parses `dataset` from response JSON
  - [x] `ensureSchemaTable(ctx) error` — creates `schema_migrations` if absent
  - [x] `loadApplied(ctx) (map[int64]string, error)` — returns map of version → checksum from `schema_migrations`
  - [x] `discoverFiles() ([]migrationFile, error)` — reads `migsDir`, filters `*.sql`, parses version from filename, sorts ascending
  - [x] `fileChecksum(path string) (string, error)` — reads file, returns hex-encoded SHA-256
  - [x] `applyOne(ctx, mf migrationFile) error` — executes SQL, inserts into `schema_migrations`

- [x] L1 tests: `internal/migrator/migrator_test.go` (no build tag) (AC: 7)
  - [x] `TestFileChecksum_KnownValue` — SHA-256 of a known string matches expected hex
  - [x] `TestDiscoverFiles_SortedAscending` — given a temp dir with sql files named `002_b.sql`, `001_a.sql`, `010_c.sql`, returns them sorted 1, 2, 10
  - [x] `TestDiscoverFiles_SkipsNonSQL` — `.txt`, `.go` files in dir are ignored
  - [x] `TestDiscoverFiles_BadFilename` — file named `abc_bad.sql` (no leading digits) returns an error
  - [x] `TestChecksumMismatch_ReturnsError` — calling Run against a server where schema_migrations reports a different checksum for an already-applied version returns a descriptive error (use httptest.NewServer even though this is L1-ish — see note in Dev Notes)

- [x] L2 tests: `internal/migrator/migrator_l2_test.go` (build tag `//go:build l2`) (AC: 8)
  - [x] `TestRun_FreshAppliesTwoMigrations` — empty schema_migrations → both `001_` and `002_` SQL files applied; server receives correct queries in order
  - [x] `TestRun_IdempotentRerun` — both versions already in schema_migrations with correct checksums → no migration SQL executed, no error
  - [x] `TestRun_ChecksumMismatch` — version 1 in schema_migrations with wrong checksum → Run returns error mentioning version and filename
  - [x] `TestRun_QuestDBHTTPError` — server returns 500 on first query → Run returns error
  - [x] `TestRun_ContextCancellation` — ctx cancelled before first query → Run returns ctx.Err()

- [x] Wire migrator into `cmd/candle/main.go` (AC: 2, 10)
  - [x] Import `internal/migrator`
  - [x] After logger setup and HTTP server goroutine launch, construct `migrator.New(&http.Client{Timeout: 30 * time.Second}, cfg.QuestDBHTTPAddr, "migrations")`
  - [x] Call `migr.Run(ctx)` synchronously
  - [x] If `err != nil && ctx.Err() == nil`: log error, `os.Exit(1)`
  - [x] If `err != nil && ctx.Err() != nil`: log "migration aborted by shutdown signal", let normal shutdown path proceed
  - [x] Log "migrations applied successfully" on nil error

### Review Findings (AI) — 2026-05-08

- [x] [Review][Patch] AC5 violation: single-pass loop applies new migrations before all stored checksums are validated — scenario `[001 ok, 002 NEW, 003 MISMATCH]` applies 002 before detecting 003's mismatch; fix: two-pass design (validate all stored checksums first, then apply pending) [candle-service/internal/migrator/migrator.go:69–89]
- [x] [Review][Patch] SQL injection in INSERT: `mf.name` interpolated unquoted via `fmt.Sprintf` — a filename with an apostrophe (e.g. `001_o'brien.sql`) breaks the SQL; fix: validate that filenames contain only `[0-9A-Za-z_-]` in `discoverFiles` [candle-service/internal/migrator/migrator.go:244]
- [x] [Review][Patch] No duplicate version detection: two files with the same numeric prefix (e.g. `001_first.sql` + `001_second.sql`) silently shadow each other; fix: detect duplicates in `discoverFiles` and return an error [candle-service/internal/migrator/migrator.go:218]
- [x] [Review][Patch] `io.ReadAll` error silently discarded: `body, _ := io.ReadAll(resp.Body)` in `exec` and `queryRows` ignores partial-read errors; fix: return the error [candle-service/internal/migrator/migrator.go:105,130]
- [x] [Review][Patch] `applyOne` reads file twice: `fileChecksum` reads the file then `applyOne` re-reads it (TOCTOU risk); fix: read once in `Run` loop and pass `[]byte` content to `applyOne` [candle-service/internal/migrator/migrator.go:240]
- [x] [Review][Patch] `queryRows` silently drops rows that fail type assertions: rows where JSON type doesn't match `float64`/`string` are skipped with `continue`; fix: return a descriptive error [candle-service/internal/migrator/migrator.go:170]
- [x] [Review][Patch] `discoverFiles` accepts version ≤ 0: `ParseInt` allows `0` and negative values which are semantically invalid migration versions; fix: add `if ver <= 0 { return error }` [candle-service/internal/migrator/migrator.go:206]
- [x] [Review][Patch] `TestDiscoverFiles_SortedAscending` doesn't prove integer sort: zero-padded 3-digit filenames sort identically lexicographically and numerically; add a case like `2_foo.sql` + `10_bar.sql` to distinguish [candle-service/internal/migrator/migrator_test.go:36]
- [x] [Review][Patch] No test for empty `migsDir`: valid no-op code path (no SQL files) has zero test coverage; add `TestRun_EmptyMigrationsDir` [candle-service/internal/migrator/migrator_test.go]
- [x] [Review][Defer] Phantom migration: DDL applied but `schema_migrations` INSERT fails → blocks startup permanently [candle-service/internal/migrator/migrator.go:240] — deferred, QuestDB REST has no multi-statement transactions; current migration files use `IF NOT EXISTS`; inherent limitation documented in package comment
- [x] [Review][Defer] Context cancellation only at `Run` entry; inter-migration cancellation relies on http.Client context propagation [candle-service/internal/migrator/migrator.go:51] — deferred, http.Client propagates ctx on each call; startup-only path with small file count
- [x] [Review][Defer] Concurrent blue/green starts produce duplicate `schema_migrations` rows [candle-service/internal/migrator/migrator.go:149] — deferred, benign (duplicate checksums overwrite identically in map); deployment strategy avoids simultaneous starts
- [x] [Review][Defer] `exec`/`queryRows` response body unbounded — no `io.LimitReader` [candle-service/internal/migrator/migrator.go:105] — deferred, startup-only runner against known QuestDB; not a realistic attack surface
- [x] [Review][Defer] `TestRun_ContextCancellation` tests pre-cancellation only, not mid-flight [candle-service/internal/migrator/migrator_l2_test.go:165] — deferred, fast-path guard tested; mid-flight handled by http.Client ctx propagation

## Dev Notes

### QuestDB REST API Pattern

Follow the aggregator's exact pattern — no special library needed, just `net/http`:

```go
import (
    "encoding/json"
    "fmt"
    "io"
    "net/http"
    "net/url"
)

// exec sends a single SQL statement to QuestDB REST and returns nil on HTTP 200.
func (m *Migrator) exec(ctx context.Context, query string) error {
    u := fmt.Sprintf("http://%s/exec?query=%s", m.httpAddr, url.QueryEscape(query))
    req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
    if err != nil {
        return fmt.Errorf("migrator: build request: %w", err)
    }
    resp, err := m.client.Do(req)
    if err != nil {
        return fmt.Errorf("migrator: exec: %w", err)
    }
    defer resp.Body.Close()
    body, _ := io.ReadAll(resp.Body)
    if resp.StatusCode != http.StatusOK {
        // Parse QuestDB error JSON {"error":"..."} if present
        var e struct{ Error string `json:"error"` }
        if json.Unmarshal(body, &e) == nil && e.Error != "" {
            return fmt.Errorf("migrator: exec: questdb error: %s", e.Error)
        }
        return fmt.Errorf("migrator: exec: HTTP %d", resp.StatusCode)
    }
    return nil
}
```

For SELECT queries, parse the `dataset` field:

```go
// QuestDB SELECT response shape
var result struct {
    Dataset [][]interface{} `json:"dataset"`
}
```

Each row in `dataset` is `[]interface{}` — for `schema_migrations` the columns are ordered: `version` (float64 from JSON), `checksum` (string). Cast appropriately.

**Reference:** `aggregator/internal/writer/questdb/writer.go:364–438` — exact same pattern, verified working.

### `schema_migrations` DDL

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version LONG,
    name STRING,
    applied_at TIMESTAMP,
    checksum STRING
) TIMESTAMP(applied_at) PARTITION BY YEAR WAL
```

### Migration File Convention

Files in `migrations/` named `NNN_description.sql` (NNN = leading digits, e.g. `001`, `002`, `010`).

Version extraction: split filename on `_`, parse the first segment as an integer. If the first segment contains no digits or is not parseable as int, return an error. Examples:
- `001_snapshot_1s.sql` → version 1
- `002_flush_manifest.sql` → version 2
- `010_add_column.sql` → version 10

Sort by parsed integer version, not lexicographically (so `010_` sorts after `002_`).

File content may contain a single DDL statement. QuestDB REST does NOT accept multiple semicolon-separated statements in one request — each SQL statement requires a separate `/exec` call. For the current migration files (each contains exactly one CREATE TABLE), this is not an issue. Do NOT attempt to split on `;` — keep it simple; document this constraint.

### INSERT into schema_migrations

After successfully applying a migration file, record it:

```sql
INSERT INTO schema_migrations VALUES (
    <version>,
    '<name>',
    systimestamp(),
    '<hex_checksum>'
)
```

Use `systimestamp()` for `applied_at` — QuestDB built-in, no client-side timestamp needed.

The name should be the filename without the `.sql` extension, e.g. `001_snapshot_1s`.

### Checksum Mismatch Error Message

Return a clear, actionable error. Example:
```
migrator: checksum mismatch for migration 1 (001_snapshot_1s.sql): stored=abc123, computed=def456 — file may have been modified after apply
```

### L1 vs L2 Test Boundary

The `TestChecksumMismatch_ReturnsError` test in the L1 file is the only exception — it uses `httptest.NewServer` to simulate the QuestDB response because the mismatch logic requires a round-trip. This is acceptable: the test creates no real infrastructure, only an in-process HTTP server. Keep the build tag absent (no `//go:build l2`) so it runs in CI without Docker.

All other HTTP-dependent tests go in `migrator_l2_test.go` with `//go:build l2`.

### `cmd/candle/main.go` Changes — Exact Wiring

```go
import "github.com/mrqdt/magnum-opus/candle-service/internal/migrator"

// After HTTP server goroutine is launched:
migr := migrator.New(&http.Client{Timeout: 30 * time.Second}, cfg.QuestDBHTTPAddr, "migrations")
if err := migr.Run(ctx); err != nil {
    if ctx.Err() != nil {
        logger.Info("migration aborted by shutdown signal")
        // fall through to normal graceful shutdown below
    } else {
        logger.Error("migration failed", "error", err)
        os.Exit(1)
    }
} else {
    logger.Info("migrations applied successfully")
}
```

The migrator is called **after** the HTTP server goroutine is launched so that the `/health` endpoint remains reachable during migration. This is important for the blue/green deploy script (Story 9-4), which polls `/health` immediately after `docker-compose up`.

### What This Story Does NOT Include

- The actual migration SQL files (`001_snapshot_1s.sql`, `002_flush_manifest.sql`) — those are Stories 5-3 and 9-1 respectively
- Redis consumer, QuestDB ILP writer — later stories
- Prometheus metrics for migration success/failure — Story 5-7
- The `migrations/` directory already exists (created in Story 5-1 with `.gitkeep`)

### Existing Code Being Modified

**`internal/config/config.go`** — currently has `Slot`, `ServicePort`, `ShutdownTimeout`, `LogLevel`. Add `QuestDBHTTPAddr string` loaded from `QUESTDB_HTTP_ADDR` (default `localhost:9000`). Follow existing `getEnv()` pattern exactly.

**`cmd/candle/main.go`** — currently: load config → start logger → start HTTP server goroutine → `<-ctx.Done()` → graceful shutdown. Insert migrator call between HTTP server goroutine and `<-ctx.Done()`. The `<-ctx.Done()` line stays; it is the normal steady-state block.

### Package Dependency Rules (non-negotiable)

- `internal/config/` is the ONLY package that calls `os.Getenv` — do not call `os.Getenv` in `internal/migrator/`
- `internal/migrator/` imports only stdlib (`context`, `crypto/sha256`, `encoding/hex`, `encoding/json`, `fmt`, `io`, `net/http`, `net/url`, `os`, `sort`, `strconv`, `strings`) — zero internal imports
- `cmd/candle/main.go` is the only file that instantiates `migrator.New` — no other package imports `internal/migrator/`
- Error wrapping: `fmt.Errorf("migrator: <operation>: %w", err)` — always wrap with context

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 5 Story 2] — full migration runner spec
- [Source: candle-service/project-context.md#Migration Runner] — rules and schema
- [Source: aggregator/internal/writer/questdb/writer.go:364–438] — exact QuestDB REST pattern to follow
- [Source: candle-service/internal/config/config.go] — config pattern to extend
- [Source: candle-service/cmd/candle/main.go] — current main.go to update

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

- TDD approach: config_test.go assertions written first (RED), then QuestDBHTTPAddr field added to config.go (GREEN).
- Internal test package (`package migrator`) used for L1 tests to access private methods `fileChecksum` and `discoverFiles` without exporting them.
- `filepath.Join` used for path construction in `discoverFiles`; string concatenation was not used.

### Completion Notes List

- All 10 ACs satisfied and verified.
- `internal/migrator/migrator.go`: full implementation with `New`, `Run`, `exec`, `queryRows`, `ensureSchemaTable`, `loadApplied`, `discoverFiles`, `fileChecksum`, `applyOne`.
- `internal/migrator/migrator_test.go`: 5 L1 tests (no build tag); `TestChecksumMismatch_ReturnsError` uses `httptest.NewServer` as specified.
- `internal/migrator/migrator_l2_test.go`: 5 L2 tests (`//go:build l2`).
- `internal/config/config.go`: `QuestDBHTTPAddr` field added; loaded from `QUESTDB_HTTP_ADDR` env var (default `localhost:9000`).
- `cmd/candle/main.go`: migrator wired after HTTP server goroutine, before `<-ctx.Done()`; context cancellation → exit 0; migration error → `os.Exit(1)`.
- `go test ./...` and `go test -tags l2 ./...` both pass with zero failures.
- `go build ./cmd/candle/` and `go vet ./...` both clean.

### File List

- candle-service/internal/config/config.go (modified)
- candle-service/internal/config/config_test.go (modified)
- candle-service/internal/migrator/migrator.go (new)
- candle-service/internal/migrator/migrator_test.go (new)
- candle-service/internal/migrator/migrator_l2_test.go (new)
- candle-service/cmd/candle/main.go (modified)

## Change Log

- Initial implementation complete (Date: 2026-05-08)
