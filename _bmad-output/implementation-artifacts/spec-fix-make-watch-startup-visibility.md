# Spec: Fix make watch — startup visibility and failure feedback

**Type:** one-shot dev-tooling fix  
**Date:** 2026-05-10  
**Status:** done

## Problem

`make watch` piped docker compose output through `logfmt.py --alerts`, which:
1. Silently dropped all non-JSON service logs (uvicorn startup, Python tracebacks) — operator had no way to see if the bot or candle services failed during startup.
2. Dropped raw docker output (build steps, pull progress) entirely unless they contained error keywords.
3. After docker compose exited unexpectedly, printed nothing — operator saw a blank terminal and no hint of what went wrong.
4. Used `EXIT=$?` after `set -o pipefail`, which captured logfmt.py's exit code rather than docker compose's when logfmt.py itself crashed.

## Solution

### `scripts/logfmt.py` — `_run_alerts` rewrite
- All lines with a service prefix (`servicename-N | ...`) that fail JSON parsing are printed as-is. This surfaces uvicorn startup lines, Python tracebacks, and any other non-JSON service output.
- Lines without a service prefix (raw docker output: build steps, engine events) are printed dimmed if they contain `_ALERT_ERROR_KEYWORDS`; suppressed otherwise to avoid progress-bar noise.
- JSON service logs at WARN/ERROR are formatted as before with color coding.

### `Makefile` — `watch` and `up` targets
- Added `--profile bot` so the bot service starts alongside aggregator and candle.
- Added a port table printf block that prints all service URLs before compose starts.
- Added `set -o pipefail` and `EXIT=${PIPESTATUS[0]}` to capture docker compose's exit code (not logfmt.py's) — prints a failure hint with diagnostic commands when compose exits non-zero and non-130 (SIGINT).

## Adversarial review findings

| # | Severity | Type | Finding | Resolution |
|---|----------|------|---------|------------|
| 1 | Medium | Patch | `EXIT=$?` captured logfmt.py exit code, not docker compose | Fixed: `EXIT=${PIPESTATUS[0]}` |
| 2 | Low | Defer | `_ALERT_ERROR_KEYWORDS` broad substrings may match Docker pull retry lines | Deferred as D-QD-1 |

## Files changed

- `scripts/logfmt.py` — rewrote `_run_alerts`; added `_ALERT_ERROR_KEYWORDS` constant
- `Makefile` — `up` and `watch` targets: `--profile bot`, port table, `set -o pipefail`, `${PIPESTATUS[0]}`, failure hint block
