# Epic R — Retrospective

**Date:** 2026-05-26  
**Epic:** R — Repo Split & Deploy Readiness  
**Stories:** R1, R2, R3, R4 (all done)  
**Code Review Patches Applied:** 10  

---

## What We Built

| Story | Deliverable |
|---|---|
| R.1 | `deploy-candle.sh` — Step 5b QuestDB write probe + Step 6 hard fail + rollback |
| R.2 | `docker-compose.pipeline.yml` + `scripts/setup-pipeline-repo.sh` |
| R.3 | `scripts/setup-trading-repo.sh` + trading repo skeleton files |
| R.4 | `scripts/validate-contract.py` (146 lines, exit 0/1) |
| Shared | `VERSION`, `Makefile` pipeline targets, CI `validate-contract` placeholder |

---

## Code Review Findings (10 patches)

### 🔴 Critical — Production Risk

**P1 — Split-brain rollback (deploy-candle.sh)**  
Both Step 5b and Step 6 failure paths restarted the old slot without stopping the new slot first.  
Both candle-service instances would run simultaneously, double-writing to QuestDB and competing on the same Redis consumer group.  
*Fix:* Added `docker-compose --profile "candle-${NEW_SLOT}" stop --timeout 5 || true` before old slot restart in both paths.

**P2 — Redis bound to all interfaces (docker-compose.pipeline.yml)**  
`"6379:6379"` binds Redis to `0.0.0.0` — all network interfaces including any public NIC on a VPS.  
*Fix:* Changed to `"127.0.0.1:6379:6379"`.

### 🟡 Correctness — Silent Failure

**P3 — python3 missing blocks deploy (deploy-candle.sh)**  
`verify_questdb_writes()` called `python3` unconditionally; if missing, the whole deploy would abort.  
*Fix:* `command -v python3` check at start; skip with `WARNING` log if not found, return 0.

**P4 — candle-blue has profile tag (docker-compose.pipeline.yml)**  
`profiles: [candle-blue]` prevents candle-blue from starting with bare `docker compose up`.  
*Fix:* Removed profiles from candle-blue service (only candle-green needs a profile).

**P5 — "null" string passes integer guard (deploy-candle.sh)**  
`[ "${count_int:-0}" -gt 0 ]` evaluates `"null"` as 0, but `"null" -gt 0` is a bash arithmetic error in strict mode.  
`jq` returns the string `"null"` when the dataset is missing.  
*Fix:* Guard with `[[ "${count_int}" =~ ^[0-9]+$ ]]` before the comparison.

**P6 — Empty XREVRANGE → WARN instead of FAIL (validate-contract.py)**  
A stream that exists but has zero entries is a corrupted stream, not a stale one.  
XREVRANGE returning empty is distinct from "stream not found" (KeyError) and stale (timestamp check).  
*Fix:* Changed to `fail()` with "corrupted stream" message.

**P7 — HTTP error conflated with missing table (validate-contract.py)**  
`get_columns()` returned `None` for both "table not found" (404/empty dataset) and "network error".  
Callers couldn't distinguish the two, producing wrong error messages.  
*Fix:* Returns `None` = missing table, `str` = error message, `dict` = columns. Caller handles all three.

### 🔵 Compliance / Quality

**P8 — Setup scripts in extracted pipeline repo (setup-pipeline-repo.sh)**  
`git filter-repo` kept `scripts/setup-pipeline-repo.sh` and `scripts/setup-trading-repo.sh` in the extracted repo.  
A repo shouldn't contain the script that creates it.  
*Fix:* Post-filter removal loop for `setup-*.sh` before the initial commit.

**P9 — QUESTDB_HTTP_ADDR vs QUESTDB_URL naming drift (setup-trading-repo.sh)**  
`setup-pipeline-repo.sh` used `QUESTDB_HTTP_ADDR`; `setup-trading-repo.sh` used `QUESTDB_URL`.  
Env var name mismatch between the two repos.  
*Fix:* Standardised to `QUESTDB_URL` throughout.

**P10 — validate-contract.py 281 lines (spec: < 150) (validate-contract.py)**  
Script grew to 281 lines — 87% over the AC 6 limit.  
*Fix:* Trimmed docstrings, compressed column definitions; final count 146 lines.

---

## What Went Well

1. **Blue-green polling logic was correct on first pass** — `verify_questdb_writes()` had correct microsecond timestamp math, timeout loop, and `// 0` null coalescing. Only the rollback path had the bug.

2. **Contract script design was architecturally clean** — `get_columns()` / `check_streams()` separation, correct `table_columns()` QuestDB function, output format matching AC 4 exactly.

3. **Setup scripts are repeatable and idempotent** — Both scripts rm-rf existing targets, include preflight checks, print actionable next steps. Can be re-run safely.

4. **`network_mode: host` decision** — Correct for single-developer environment; documented the escape hatch (change `REDIS_URL` in `.env` for remote host). Zero Docker bridge config.

5. **CI placeholder pattern** — `if: false` job with full run configuration is the right pattern: documents exactly how to enable when self-hosted runner is available, but doesn't fail CI now.

---

## What Went Wrong — Root Causes

### Rollback paths need a "current state audit"

**What happened:** Both rollback paths were written by analogy with existing rollback logic that only restarts the old slot. Neither asked: "what is the new slot doing right now?"  
**Root cause:** Implementation focused on the target state ("old slot running") without enumerating the full current state ("new slot also running").  
**Pattern:** Split-brain bugs in multi-service rollbacks come from incomplete state enumeration, not from wrong commands.

### Port bindings are security decisions

**What happened:** `"6379:6379"` was written as a natural port-forward without considering which interface it binds.  
**Root cause:** Infrastructure stories default to "does it work locally?" — not "is it safe on a machine with a public IP?"  
**Pattern:** Any port binding that should not be externally reachable needs `127.0.0.1:` prefix. This belongs on a deploy-time checklist.

### Measurable AC constraints need a pre-review verification step

**What happened:** AC 6 spec said "< 150 lines". Script was written organically and reached 281 lines. No verification before marking done.  
**Root cause:** The constraint was read during planning but not checked at the end of implementation.  
**Pattern:** Measurable constraints (line count, timeout, size) need an explicit check as the last step before "done".

### Shared names drift when files are written sequentially

**What happened:** `QUESTDB_HTTP_ADDR` in setup-pipeline-repo.sh, `QUESTDB_URL` in setup-trading-repo.sh — written by the same agent in sequence, without cross-referencing.  
**Root cause:** Each file was considered independently; no "shared names" audit across files.  
**Pattern:** Any string appearing in >1 file (env var, Redis key, port constant) should be established canonically first, then referenced.

### Error types need distinct code paths

**What happened:** `get_columns()` returned `None` for both "table missing" and "HTTP error". Wrong diagnosis, wrong user message.  
**Root cause:** Designing a function for the happy path, then adding error handling as a single `None` fallback.  
**Pattern:** When a function has multiple failure modes, make them distinguishable at the type level (return type, exception type, error string).

---

## Lessons Learned

1. **Rollback = stop new → start old (never just start old)** — For any blue-green rollback: enumerate what is running at the failure point, then explicitly stop unwanted services before starting wanted ones.

2. **Port bindings: default to loopback** — `127.0.0.1:port:port` for any service that shouldn't be network-reachable. Only expose to `0.0.0.0` intentionally and with documentation.

3. **Verify measurable ACs before "done"** — Line count, latency, size constraints. Put them in a checklist at the bottom of every story that has one.

4. **Define shared names first** — When multiple files share env var names, port names, or stream keys, establish the canonical name before writing any file. A single-line "shared names" section in the story is enough.

5. **Function return types must distinguish failure modes** — `None` for missing, `str` for error, `dict` for data. Or use exceptions with distinct types. The caller must be able to produce the right message without guessing.

---

## Action Items — Execute the Repo Split

| Task | Notes |
|---|---|
| `bash scripts/setup-pipeline-repo.sh` | Creates `/tmp/magnum-opus-pipeline` |
| `gh repo create JeppeOEM/magnum-opus-pipeline --private` | Then push with `git remote add origin ...` |
| `make up` in pipeline repo | Verify aggregator + candle-service + Redis + QuestDB start |
| `bash scripts/setup-trading-repo.sh` | Creates `/tmp/magnum-opus-trading` |
| `gh repo create JeppeOEM/magnum-opus-trading --private` | Then push |
| `make up` in trading repo | Verify dashboard connects to pipeline localhost |
| `python3 scripts/validate-contract.py` | Smoke test against live pipeline |
| Remove `dashboard/` from monorepo | After trading repo dashboard is confirmed working (AC 9 of R.3) |

---

## Pre-Deploy Checklist (derived from this retro)

Before any infrastructure deploy:

- [ ] Every port binding: `127.0.0.1:port:port` (not `port:port`)
- [ ] Every rollback path: stop new service → start old service
- [ ] Every measurable AC constraint verified (line count, timeout, size)
- [ ] Env var names consistent across all files in the same change
- [ ] Error returns: missing vs error vs success have distinct types/values
