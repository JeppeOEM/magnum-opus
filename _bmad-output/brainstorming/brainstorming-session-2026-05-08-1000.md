---
stepsCompleted: [1, 2, 3]
inputDocuments: []
session_topic: 'Blue/green deployment mechanics for the Go candle service'
session_goals: 'Generate ideas for how the deployment swap actually works — start, health gate, promote, stop — with zero data loss and safe Redis consumer group overlap'
selected_approach: 'ai-recommended'
techniques_used: ['Constraint Mapping', 'Reverse Brainstorming', 'Decision Tree Mapping']
ideas_generated: [5]
context_file: ''
---

# Brainstorming Session Results

**Facilitator:** mrqdt
**Date:** 2026-05-08

## Session Overview

**Topic:** Blue/green deployment mechanics for the Go candle service
**Goals:** How the deployment swap actually works — start, health gate, promote, stop — with zero data loss and safe Redis consumer group overlap during the transition window

---

## Constraint Mapping

**C1 — Single active consumer:** The production consumer group must have exactly one active consumer at any time. Two instances can run simultaneously as long as the new slot has not joined the production group yet.

---

## Deployment Mechanics Considered

**[Deploy #1]: Standby + Signal Handoff**
_Concept:_ Green starts fully — migrations, health check, all connections — but never calls XREADGROUP. Deploy script sends SIGTERM to blue. Once blue is confirmed gone, sends SIGUSR1 to green which then joins the consumer group.
_Novelty:_ No Redis coordination needed. Gap is sub-second. Simple orchestration.

**[Deploy #2]: Shadow Read Warmup** ← Selected as core mechanism
_Concept:_ Green reads the live stream via XREAD (no consumer group, no message claiming), processing every tick to build OB state and accumulators but writing nothing to QuestDB. Once warm, deploy script stops blue and green switches to XREADGROUP.
_Novelty:_ Green's OB state and rolling windows are fully warm the moment it takes over — no cold-start null rows. Shadow reads are invisible to blue.

**[Deploy #3]: Redis Lease / Active Slot Key**
_Concept:_ Redis key `candle:active_slot` determines which slot consumes. Both instances watch it. Deploy script atomically sets the key to the new slot. Old slot drains and stops; new slot starts consuming.
_Novelty:_ Self-orchestrating — works even if deploy script crashes mid-deploy.

**[Deploy #4]: Claim Pending on Takeover** ← Selected as safety net
_Concept:_ On startup, new slot calls XAUTOCLAIM to recover any messages the old slot claimed but never ACKed. Handles ungraceful shutdowns.
_Novelty:_ Universal recovery — XAUTOCLAIM-on-startup is also the rollback mechanism.

**[Deploy #5]: Freeze-Frame Swap**
_Concept:_ Deploy script pauses the aggregator's Redis writes, swaps slots, resumes. Clean stream during swap.
_Novelty:_ Eliminates overlap entirely — but couples deploy script to aggregator.

---

## Selected Design: Deploy #2 + Deploy #4

**Why this combination covers every data loss scenario:**

- **Loss A (ticks blue processed):** Shadow XREAD means green has already processed every tick blue processed. Accumulators are complete at handoff.
- **Loss B (messages blue claimed but didn't ACK):** XAUTOCLAIM on green startup recovers all orphaned pending messages.
- **Loss C (messages between blue's last read and green's first read):** Safe by design — undelivered messages sit in the stream and are picked up by green's first XREADGROUP call.

---

## Decision Tree

```
deploy-candle.sh green blue
│
├─► START GREEN
│     ├─ fails (container, migration, Redis) → ABORT, blue untouched
│     └─ started OK
│           ▼
├─► POLL GREEN /health (timeout: DEPLOY_HEALTH_TIMEOUT_S)
│     ├─ timeout or degraded → stop green → ABORT, blue untouched
│     └─ status=ok, shadow XREAD confirmed running
│           ▼
├─► SIGTERM BLUE
│     ├─ blue already dead → skip wait, proceed (XAUTOCLAIM covers pending)
│     └─ SIGTERM sent
│           ▼
├─► WAIT FOR BLUE EXIT (timeout: SHUTDOWN_TIMEOUT_S + 5s buffer)
│     ├─ blue exits cleanly → proceed, all messages ACK'd
│     └─ timeout → SIGKILL blue → proceed (XAUTOCLAIM covers unACK'd)
│           ▼
├─► GREEN PROMOTES
│     ├─ switch XREAD → XREADGROUP on production group
│     ├─ XAUTOCLAIM all messages pending > 0ms
│     ├─ XAUTOCLAIM fails → INCIDENT, page operator
│     └─ XAUTOCLAIM ok
│           ▼
├─► VERIFY GREEN CONSUMING (30s window, consumer_lag_max trending down)
│     ├─ green crashes or health fails
│     │     └─► RESTART BLUE (normal startup)
│     │           ├─ blue rejoins consumer group
│     │           ├─ XAUTOCLAIM recovers green's pending messages
│     │           ├─ state restored from Redis
│     │           └─► ROLLBACK COMPLETE
│     └─ green healthy → SUCCESS
```

---

## Three Deployment Rules

> **Rule 1 — Abort is always safe before SIGTERM.**
> Blue is untouched up to that point. Stop green and walk away with zero impact.

> **Rule 2 — SIGKILL is safe after SIGTERM timeout.**
> XAUTOCLAIM recovers any messages blue didn't ACK. Graceful shutdown is an optimisation, not a safety requirement.

> **Rule 3 — If green fails post-promotion, restart blue.**
> Blue's Redis state (cascade accumulators, block trade window, gap dedup) is intact. XAUTOCLAIM-on-startup is the universal recovery mechanism — restart is rollback.

---

## Zero Data Loss: The XREAD → XREADGROUP Transition Problem

**The gap in the naive design:**
Green shadow-reads via XREAD up to position 110. At promotion, `XREADGROUP >` re-delivers messages that arrived between green's last XREAD poll and promotion. Green processes them twice — double-counts ticks into the OHLCV accumulator. DEDUP UPSERT overwrites the row but the value is wrong. Silent corruption.

**Three precise rules at promotion time:**

> **Promotion Rule 1 — Green tracks `lastShadowID` continuously.**
> As green shadow-reads, it records the last message ID it processed.

> **Promotion Rule 2 — At promotion, green starts XREADGROUP from `lastShadowID`, not `>`.**
> Only messages green has never seen are delivered. No double-counting, no skips.

> **Promotion Rule 3 — At promotion, green discards OHLCV accumulators and reconstructs from QuestDB.**
> Query the last `is_partial=true` row per symbol and rebuild the current in-flight second from there. OB state and rolling windows are kept — they are idempotent. Only the current second's OHLCV is reset.

**Additional deploy gate:**
Before sending SIGTERM to blue, the deploy script must confirm green's `shadow_lag≈0` (green's `lastShadowID` has caught up to the stream tip). If green is still behind, promotion would miss those messages permanently.

**Final promotion sequence:**
```
1. Green shadow-reads, tracks lastShadowID continuously
2. Deploy script: wait for health=ok AND shadow_lag≈0
3. SIGTERM blue → flush accumulator (is_partial=true), XACK all pending, exit
4. Green promotes:
   a. Switch XREAD → XREADGROUP starting from lastShadowID
   b. XAUTOCLAIM all pending > 0ms (blue's unACKed messages)
   c. Discard OHLCV accumulators → reconstruct from QuestDB last is_partial row
   d. Resume — OB state and rolling windows already warm, no cold-start
```
