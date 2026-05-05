# Story 1.1: Go Module Initialization & Dependency Verification

Status: ready-for-dev

## Story

As the developer,
I want to initialize the Go module with all verified, actively maintained dependencies,
so that the project compiles from day one and no archived or unmaintained library can enter the codebase.

## Acceptance Criteria

1. **Given** an empty project directory, **When** `go mod init github.com/mrqdt/magnum-opus/aggregator` is run and all dependencies are added, **Then** `go build ./...` succeeds with zero errors.
2. `go.mod` declares `go 1.21` minimum (or higher — 1.21 is the floor required for `log/slog` stdlib).
3. The following five dependencies are pinned to specific versions: `nhooyr.io/websocket`, `github.com/redis/go-redis/v9`, `github.com/questdb/go-questdb-client/v3`, `github.com/prometheus/client_golang`, `github.com/stretchr/testify`.
4. `gorilla/websocket` does NOT appear in `go.mod` or `go.sum`.
5. Each direct dependency has had a commit within the last 12 months (verified at time of pinning, noted in `DEPS.md`).
6. `go.mod` is annotated with a `// verified: YYYY-MM-DD` comment for each direct external dependency so drift is visible in git diff.
7. `make check-deps` is listed in `DEPS.md` as the re-verification command to run before any dependency update.
8. `.env.example` documents every environment variable accepted by `config.Load()` with description and example value.
9. `.env` is listed in `.gitignore`.

## Tasks / Subtasks

- [ ] Verify `go build ./...` produces zero errors (AC: 1)
  - [ ] Run `cd aggregator && go build ./...` and confirm clean exit
- [ ] Verify `go.mod` module path and Go version floor (AC: 1, 2)
  - [ ] Confirm `module github.com/mrqdt/magnum-opus/aggregator` in first line
  - [ ] Confirm `go` directive is 1.21 or higher
- [ ] Verify all five required dependencies are present and pinned (AC: 3)
  - [ ] `nhooyr.io/websocket` in `require` block with specific version
  - [ ] `github.com/redis/go-redis/v9` (not `go-redis/v8`) in `require` block
  - [ ] `github.com/questdb/go-questdb-client/v3` in `require` block
  - [ ] `github.com/prometheus/client_golang` in `require` block
  - [ ] `github.com/stretchr/testify` in `require` block
- [ ] Verify no prohibited dependencies (AC: 4)
  - [ ] `grep gorilla/websocket go.mod go.sum` returns no matches
- [ ] Verify `// verified: YYYY-MM-DD` annotations on all direct deps (AC: 6)
  - [ ] Each direct dep line in `require` block carries the annotation
- [ ] Verify `DEPS.md` and `make check-deps` (AC: 5, 7)
  - [ ] `DEPS.md` exists and references `make check-deps` as the re-verification command
  - [ ] `make check-deps` target exists in `Makefile` and runs `go run ./internal/tools/checkdeps/main.go`
  - [ ] `make check-deps` exits 0 with current annotations
- [ ] Verify `.env.example` completeness (AC: 8)
  - [ ] All env vars consumed by `internal/config/config.go` are documented with description and example
  - [ ] `.env.example` lives at `aggregator/.env.example`
- [ ] Verify `.gitignore` (AC: 9)
  - [ ] `.env` entry present in `aggregator/.gitignore`

## Dev Notes

### Current Implementation State

**This implementation already exists** in the `aggregator/` directory from the initial "start" commit. This story is a **verification and acceptance pass** — not a greenfield build. All file references below describe what to check, not what to create.

Key files already present:
- `aggregator/go.mod` — module declaration, Go version, 5 direct deps with `// verified: 2026-05-05`
- `aggregator/go.sum` — lockfile for reproducible builds
- `aggregator/DEPS.md` — dependency table, maintenance policy (ARC11), re-verification command
- `aggregator/Makefile` — `check-deps`, `test-l1`, `test-l2`, `build` targets
- `aggregator/.env.example` — all env vars documented
- `aggregator/.gitignore` — `.env` gitignored
- `aggregator/internal/tools/checkdeps/main.go` — the `check-deps` implementation

### Go Version Note

`go.mod` currently declares `go 1.24`. The AC requires `go 1.21` minimum (floor). Since 1.24 > 1.21, this satisfies the AC — the floor is met. `log/slog` is available in both.

### Critical Library Decisions (non-negotiable, baked into go.mod)

| Library | Why | Forbidden Alternative |
|---|---|---|
| `nhooyr.io/websocket` | Context-native, actively maintained | `gorilla/websocket` — archived 2022 |
| `github.com/redis/go-redis/v9` | Org renamed from `go-redis` | `github.com/go-redis/go-redis/v8` |
| `github.com/questdb/go-questdb-client/v3` | ILP TCP port 9009 | Any HTTP-only QuestDB client |

### `make check-deps` Behavior

The tool reads `go.mod` directly (not `go list` output), scans direct dependencies for `// verified: YYYY-MM-DD` annotations, and fails if:
- Any direct dep is missing the annotation
- Any annotation date is older than 365 days

It does NOT make network calls — it only validates the annotation date. The developer is responsible for manual verification before updating the date. This is intentional (no CI secrets needed for the check).

### `.env.example` Env Var Contract

Variables documented in `.env.example` (all consumed by `internal/config/config.go`):
- `KUCOIN_API_KEY`, `KUCOIN_API_SECRET`, `KUCOIN_API_PASSPHRASE`
- `BYBIT_API_KEY`, `BYBIT_API_SECRET`
- `REDIS_ADDR`, `REDIS_PASSWORD` (optional), `REDIS_STREAM_MAXLEN`
- `QUESTDB_ILP_ADDR`, `QUESTDB_HTTP_ADDR`
- `KUCOIN_SYMBOLS`, `BYBIT_SYMBOLS`
- `LOG_LEVEL`, `HTTP_ADDR`, `STARTUP_TIMEOUT_SEC`

If `internal/config/config.go` is changed to add/remove variables, `.env.example` MUST be updated in the same commit.

### Project Structure Notes

- All work is under `aggregator/` — this is a subdirectory of the `magnum-opus` monorepo
- The Go module root is `aggregator/go.mod`; all `go` commands must run from `aggregator/`
- `internal/tools/checkdeps/` is a build tool, not a library — it is excluded from the `time.Now()` ban in `make test-l1` via `grep -v '/tools/'`
- `internal/testutil/` and `internal/testutil/mock/` exist but carry no L2 tags at the package level (individual files in `mock/` carry `//go:build l2`)

### Testing Standards for L1

`make test-l1` runs `orderbook`, `reconnect`, `gapdetector`, `symbol`, `backoff`, `config` packages. Story 1-1 has no L1 test targets itself, but `make test-l1` must pass before this story can be considered done (it validates the `time.Now()` ban and coverage gate, which depend on the packages initialized in this story).

### References

- [Source: architecture.md#Module Initialization] — `go mod init github.com/mrqdt/magnum-opus/aggregator`, library choices, WebSocket decision rationale
- [Source: architecture.md#Enforcement Guidelines] — ARC11 dependency maintenance policy
- [Source: epics.md#Story 1.1] — Full AC text including dependency annotation requirement
- [Source: epics.md#ARC1, ARC2, ARC11] — Architecture requirements covered by this story
- [Source: project-context.md#Technology Stack & Versions] — Non-negotiable library versions and forbidden alternatives
- [Source: aggregator/DEPS.md] — Current dependency table and maintenance policy

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

### File List
