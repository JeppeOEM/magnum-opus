---
stepsCompleted: [question-storming, reversal-inversion, scamper]
inputDocuments: []
session_topic: 'Frontend design + full live orderbook data fanout architecture'
session_goals: 'Decide how to fanout full L2 orderbook to frontend and bot service; design frontend visualization panels'
selected_approach: 'Option F — split by latency tier: adepthviewregator tick-level pub/sub + candle service 1s pub/sub, depthview gateway multiplexes both'
techniques_used: [question-storming, reversal-inversion, scamper]
ideas_generated: []
context_file: ''
---

# Brainstorming Session Results

**Facilitator:** mrqdt
**Date:** 2026-05-10

---

## Topic

Frontend design + full live orderbook data fanout architecture. User intuition: candle service should own the pub/sub fanout.

---

## Technique 1: Question Storming

46 questions generated across clusters: ownership, data shape & timing, architecture coupling, frontend architecture, failure modes, scope & simplicity.

**Key questions that shaped decisions:**

- Does the frontend need full L2 or top-N? → Top-N for heatmap, full for ladder
- What latency does heatmap need? → 1s is fine (matches candle service cadence)
- Does the bot need full book or derived features? → Strategy declares its own mode
- Raw diff vs snapshot? → Full snapshots (single user, local network, no reliability SLA)
- Can heatmap be built from existing candle adepthviewregates? → No — adepthviewregates collapse per-level detail; need new QuestDB table written from the candle service's internal L2 state

**Locked decisions from Question Storming:**

| Decision | Choice |
|---|---|
| Browser scope | Single user, no reliability SLA |
| Pub/sub format | Full snapshots always (no diffs) |
| Heatmap storage | New `orderbook_heatmap` QuestDB table — top-N price levels, 1s cadence |
| Bot orderbook access | Strategy config declares mode: `none`, `snapshot_1s`, `full_stream`, `both` |
| Frontend protocol | depthview binary encoding as-is |
| Two channels | Yes — orderbook (tick-level) and candles1s (1s features) separate |

---

## Technique 2: Reversal Inversion

Attacked the "candle service owns fanout" hypothesis. Key findings:

1. Two latency tiers needed — tick-level for live ladder, 1s for heatmap/charts
2. depthview's real value is binary encoding + connection lifecycle, not routing logic
3. Per-bot orderbook config IS useful — two channels are genuinely different data
4. Heatmap storage needed for post-hoc strategy research and backtest replay
5. Two separate channels is the right call (don't multiplex different cadences into one)

User: "I don't like these findings" → moved to open ideation.

---

## Technique 3: Open Ideation — Architectural Options

Eight distinct options presented (A–H). User chose **F**.

**Option F — Split by latency tier:**

```
Adepthviewregator  ──PUBLISH──▶  orderbook:{ex}:{sym}   ──▶  depthview  ──▶  Browser (live ladder, tick-level)
Candle Svc  ──PUBLISH──▶  candles1s:{ex}:{sym}   ──▶  depthview  ──▶  Browser (charts, heatmap, OFI, spread)
```

depthview does `PSUBSCRIBE orderbook:*` and `PSUBSCRIBE candles1s:*`, tags each message with a type field, pushes both to the same WebSocket connection. Frontend routes by message type.

**Ownership:**
- Adepthviewregator → raw book shape, tick-level
- Candle service → all computed features + heatmap QuestDB writer
- depthview → connection lifecycle and binary encoding only
- Bot service → subscribes based on strategy config

---

## Technique 4: SCAMPER — Frontend Panels

Applied to: heatmap, live ladder, spread, adepthviewressive buy/sell, charts, liquidity.

**Highest value ideas:**

| Idea | Technique | Why |
|---|---|---|
| Ladder + heatmap on shared price axis | Combine | Eliminates mental context-switch between current state and history |
| Footprint chart | Adapt | Direct use of trade tape; shows volume traded per price tick inside each bar |
| Bot activity overlaid on price chart timeline | Put to other uses | Makes bot decisions legible in context |
| Heatmap scrubber for backtest replay | Put to other uses | QuestDB heatmap table becomes a replay engine |
| Microstructure health gauge | Combine | Single number from OFI + spread + imbalance — instant situational awareness |
| Non-linear time axis on heatmap | Modify | Compress quiet periods, expand high-activity periods (scale by OFI or volume) |
| Embed spread as color band on ladder | Eliminate | Removes separate spread panel; tight = green gap, wide = red |
| Bot status at top of layout | Rearrange | Everything else is context for bot decisions |

**Locked from SCAMPER:**
- Heatmap time axis: **both linear and non-linear** options available (user todepthviewle)

---

## Architecture Decision — Final

**Epic 17: Live Frontend & Orderbook Fanout**

Components to build:

1. **Adepthviewregator** — add `PUBLISH orderbook:{ex}:{sym}` (full L2 snapshot JSON) after each tick
2. **Candle service** — add `PUBLISH candles1s:{ex}:{sym}` after each 1s cycle; add heatmap writer to QuestDB `orderbook_heatmap` table
3. **depthview gateway** — subscribe to both pub/sub channels; add message type field; multiplex to browser WebSocket
4. **Bot service** — pub/sub subscriber for orderbook channel; strategy config declares subscription mode
5. **Frontend** — heatmap (linear/non-linear todepthviewle), live ladder + heatmap on shared price axis, bot activity overlay, microstructure gauge, footprint chart

**New QuestDB table: `orderbook_heatmap`**
```
timestamp  TIMESTAMP (partition key)
exchange   SYMBOL
symbol     SYMBOL
level      INT        -- 1 = best, 2 = next, ...
bid_price  DOUBLE
bid_size   DOUBLE
ask_price  DOUBLE
ask_size   DOUBLE
```
Top-20 depth, 1s cadence, one symbol → ~1.7M rows/day.
