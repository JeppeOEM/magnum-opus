---
stepsCompleted: ['step-01-preflight-and-context', 'step-02-generation-mode', 'step-03-test-strategy', 'step-04-generate-tests', 'step-04c-aggregate', 'step-05-validate-and-complete']
lastStep: 'step-05-validate-and-complete'
lastSaved: '2026-05-06'
storyId: '2.1'
storyKey: '2-1-exchange-interface-and-websocket-transport-layer'
storyFile: '_bmad-output/implementation-artifacts/stories/2-1-exchange-interface-and-websocket-transport-layer.md'
# confirmed: story file created at path above
atddChecklistPath: '_bmad-output/test-artifacts/atdd-checklist-2-1-exchange-interface-and-websocket-transport-layer.md'
generatedTestFiles:
  - aggregator/internal/exchange/transport/conn_test.go
  - aggregator/internal/exchange/exchange_compile_test.go
inputDocuments:
  - _bmad-output/planning-artifacts/epics.md
  - _bmad-output/project-context.md
  - _bmad-output/planning-artifacts/architecture.md
---

# ATDD Checklist: Story 2.1 — Exchange Interface & WebSocket Transport Layer

**TDD Phase:** 🔴 RED (scaffolds generated — all tests skipped)
**Test Level:** L1 (compile-time) + L3 (//go:build l3, in-process MockWSServer)
**Stack:** Go backend
**Framework:** `go test` + `github.com/stretchr/testify`

---

## Red-Phase Summary

| File | Build Tag | Tests | Status |
|---|---|---|---|
| `aggregator/internal/exchange/transport/conn_test.go` | `//go:build l3` | 6 | 🔴 All skipped |
| `aggregator/internal/exchange/exchange_compile_test.go` | *(none — L1)* | 3 | 🔴 All skipped |

**Total:** 9 tests — all with `t.Skip()` (TDD red phase)

---

## Acceptance Criteria Coverage

| AC | Description | Test | Priority |
|---|---|---|---|
| AC1 | `exchange.go` defines Exchange interface only — no implementations | `TestExchangeInterfaceNaming` (compile) | P1 |
| AC1 | Interface naming: noun pattern, no `I`-prefix | `TestExchangeInterfaceNaming` (compile) | P1 |
| AC2 | Transport uses `nhooyr.io/websocket` exclusively | Enforced by `make check-deps` + grep gate | P0 |
| AC3 | Read/write via `wsjson.Read` / `wsjson.Write` | `TestConn_DialAndReadWrite` | P1 |
| AC4 | Graceful teardown: cancel ctx first, then `conn.Close(StatusNormalClosure, "")` | `TestConn_TeardownOrder` | P0 |
| AC5 | Reconnect triggers within 5 seconds of silent drop (NFR6) | `TestConn_ReconnectFiresWithin5sOnSilentDrop` | P0 |
| AC5 | Reconnect channel is buffered (min size 1) | `TestConn_ReconnectChannelMinBufferSize` | P0 |
| AC5 | Non-blocking send: if channel full, skip (not block) | `TestConn_ReconnectChannelIsNonBlocking` | P0 |
| AC6 | ctx cancel → transport exits cleanly | `TestConn_CleanExitOnCtxCancel` | P1 |
| AC6 | `for-select` has `ctx.Done()` as peer case | `TestConn_CleanExitOnCtxCancel` (structural) | P1 |
| AC7 | `price` and `size` are always `string` — never `float64` | `TestTickFieldTypes` (compile) | P0 |
| AC7 | `EventType.String()` values match Redis schema ("update", "trade") | `TestEventTypeStringValues` | P0 |

**Coverage:** 12/12 acceptance criteria covered (100%)

---

## Test Strategy

**Why L3 for transport tests?**
The transport layer wraps `nhooyr.io/websocket` and requires a real WebSocket server to verify its behavior. Pure unit tests (L1) cannot test connection lifecycle, keepalive behavior, or reconnect signals without a network server. L3 uses `net/http/httptest` to spin up an in-process server — no external dependencies, but a real TCP connection.

**Why not L2 (mock interfaces)?**
The transport itself is the low-level component. There's no interface to mock beneath it. L2 mocks are used at the coordinator/exchange-adapter layer (FakeRedis, FakeQuestDB) — not at the transport layer.

**Gorilla/websocket exclusion:**
This is enforced structurally via `go.mod` (gorilla/websocket is not in deps) and the `make check-deps` grep gate. No test is needed for this — it's a build-time constraint.

---

## Pre-Implementation Notes

### What already exists
- `exchange.go` — Exchange interface ✓ (already implemented)
- `transport/conn.go` — Conn type with keepalive ✓ (already implemented)

### What the tests verify
Even though the code exists, **no acceptance test coverage exists yet**. These scaffolds create the formal verification gate. Once activated (t.Skip removed), they become the L3 regression gate that runs on every pre-push.

### Story 2.1 specifically tracks
- The grep extension: `make test-l1` must cover `internal/exchange/` (add to the Makefile target)
- Compile-time `var _ exchange.Exchange = (*kucoin.Adapter)(nil)` assertion (lives in `exchange/kucoin/`)

---

## Activation Instructions (Task-by-Task)

During story 2.1 implementation, activate tests one by one as each AC is verified:

1. **Remove `t.Skip()`** from the test for the current task
2. **Run tests:** `cd aggregator && go test -tags=l3 ./internal/exchange/...`
3. **Verify the activated test FAILS** (red phase confirmed) — if it passes, the test may be wrong
4. **Implement or verify** the feature
5. **Verify the test PASSES** (green phase)
6. **Commit** the passing test

**For L1 tests (no build tag):**
```bash
cd aggregator && go test ./internal/exchange/...
```

**For L3 tests:**
```bash
cd aggregator && go test -tags=l3 ./internal/exchange/transport/...
```

---

## ATDD Artifacts

- **Checklist:** `_bmad-output/test-artifacts/atdd-checklist-2-1-exchange-interface-and-websocket-transport-layer.md`
- **L3 tests:** `aggregator/internal/exchange/transport/conn_test.go`
- **L1 compile tests:** `aggregator/internal/exchange/exchange_compile_test.go`

---

## Next Steps

1. Commit working-tree changes (Clock injection, go.mod cleanup) — clean baseline before story creation
2. **Run `bmad-create-story`** to generate the story 2.1 file in `implementation-artifacts/stories/`
3. Link ATDD artifacts into the story file under `## Dev Notes > ### ATDD Artifacts`
4. Implement story 2.1 (extend `make test-l1` grep scope, add compile assertion in kucoin package)
5. Activate tests one by one as ACs are implemented
6. Run `make test-l3` to verify all transport tests pass before marking story done
