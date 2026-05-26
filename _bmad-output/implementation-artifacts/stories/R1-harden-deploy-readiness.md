# Story R.1: Harden deploy-candle.sh Readiness Checks

Status: done

## Story

As a service operator,
I want the candle-service blue-green deploy to fail fast and roll back automatically
when QuestDB writes stop or consumer lag doesn't converge after promotion,
so that a broken deploy never silently becomes a production data gap.

## Context

`scripts/deploy-candle.sh` already has health gates in Steps 2 and 5, but has
two gaps that allow a broken deployment to "succeed":

1. **Step 6 warns but doesn't fail.** If `consumer_lag_max` is not trending down
   after the 30-second observation window, the script logs a warning and exits 0.
   The old slot has already been stopped. Result: broken candle-service running as
   active slot with no rollback triggered.

2. **No QuestDB write verification.** `status=ok` in `/health` means the service is
   running and consuming Redis, but does NOT confirm rows are landing in `snapshot_1s`.
   A broken ILP connection (wrong port, QuestDB overloaded, WAL error) is completely
   silent until a consumer queries QuestDB and finds a gap.

## Acceptance Criteria

### AC 1 — Step 5b: QuestDB write probe (new step)

1. A new `verify_questdb_writes()` bash function is added between Step 5 and Step 6.
2. The function polls `GET http://localhost:${QUESTDB_HTTP_PORT}/exec?query=SELECT count() FROM snapshot_1s WHERE ts >= {10_seconds_ago_in_microseconds}` every 2 seconds for up to 15 seconds.
3. `QUESTDB_HTTP_PORT` defaults to `9000`; overridable via environment variable.
4. If `count > 0` is returned → function returns 0 (success).
5. If the 15-second timeout expires with count still 0 → function returns 1.
6. On failure: restart old slot (`docker-compose --profile candle-${OLD_SLOT} up -d`) and call `fail "candle-${NEW_SLOT} not writing to QuestDB after promotion"` (exit 1).
7. Step header logged: `"Step 5b: verifying QuestDB writes (up to 15s for a fresh snapshot_1s row)"`.
8. On success logged: `"Step 5b: QuestDB writes confirmed"`.
9. Requires `python3` for URL encoding (already required by the host environment). Falls back gracefully: if python3 is unavailable, the query URL is skipped and the step passes with a warning (does not block deploy).

### AC 2 — Step 6: hard fail + rollback on lag not converging

10. The final `if/else` in Step 6 is changed from warn-and-continue to fail-and-rollback:
    ```bash
    # Before (warn only)
    log "Step 6: WARNING — consumer lag not clearly trending down; monitor manually"

    # After (hard fail)
    log "Step 6 FAILED: consumer lag not converging after promotion — rolling back"
    docker-compose --profile "candle-${OLD_SLOT}" up -d || true
    fail "candle-${NEW_SLOT} consumer lag not converging — rolled back to candle-${OLD_SLOT}"
    ```
11. The success path is unchanged: if `converging=true` OR `prev_lag <= 0`, log "deployment confirmed" and exit 0.

### AC 3 — Script header updated

12. `QUESTDB_HTTP_PORT` is documented in the `# Environment:` section of the script header.

### AC 4 — Exit codes remain correct

13. All failure paths exit with code 1 via the existing `fail()` function.
14. A successful deploy still exits 0.

### AC 5 — No new runtime dependencies

15. The only new tool used is `python3` (for URL encoding), which is already available in the deploy environment. The `jq` `.dataset[0][0] // 0` filter handles null safely.

## Dev Notes

### Why microseconds for the QuestDB timestamp filter

QuestDB stores `ts` as a `TIMESTAMP` type with microsecond precision. The `WHERE ts >= N` filter expects microseconds since epoch. `$(date +%s)` returns seconds; multiply by 1,000,000 to convert.

```bash
from_us=$(( ($(date +%s) - 10) * 1000000 ))
```

### Why 10-second lookback window

Candle-service writes a `snapshot_1s` row once per second per symbol. After promotion, the first bar close may take up to 1 second. The 10-second window comfortably captures the first few bars even if there's a short startup delay.

### QuestDB JSON response format

```json
{"dataset": [[3.0]], "columns": [{"name": "count", "type": "LONG"}]}
```

`jq -r '.dataset[0][0] // 0'` extracts the count. QuestDB returns `count()` as float in JSON; strip decimals with bash `${count%.*}` before integer comparison.

### Rollback safety

Both new failure paths use the same rollback pattern already in Steps 4 and 5:
`docker-compose --profile "candle-${OLD_SLOT}" up -d || true`

The `|| true` prevents the rollback command itself from masking the original failure in logs. The `fail()` call after it exits the script with code 1.

## Tasks / Subtasks

- [x] Add `verify_questdb_writes()` function between Step 5 and Step 6 (AC 1)
- [x] Add Step 5b invocation with rollback on failure (AC 1)
- [x] Change Step 6 else-branch from warn to fail+rollback (AC 2)
- [x] Update script header with `QUESTDB_HTTP_PORT` env var doc (AC 3)
- [x] Manual smoke test: run `./scripts/deploy-candle.sh green blue` against running stack

## Dev Agent Record

### Completion Notes

Both changes applied to `scripts/deploy-candle.sh`:
- Step 5b added between Step 5 and Step 6 — polls `snapshot_1s` row count via QuestDB HTTP exec API, rolls back if no rows within 15s
- Step 6 else-branch hardened — restarts old slot and exits 1 instead of warning and continuing
- `QUESTDB_HTTP_PORT` env var added with default 9000, documented in script header

### File List

- `scripts/deploy-candle.sh` — Step 5b added, Step 6 hardened, header updated

### Change Log

- 2026-05-26: R.1 implemented — QuestDB write probe (Step 5b) + Step 6 hard fail
