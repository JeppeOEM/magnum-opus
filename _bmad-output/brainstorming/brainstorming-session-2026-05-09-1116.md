---
stepsCompleted: [1, 2, 3, 4]
inputDocuments: []
session_topic: 'Trading bot service architecture — consuming Redis streams and executing trades'
session_goals: 'Strategy architecture patterns, subscription models, data contracts, execution layer design'
selected_approach: 'ai-recommended'
techniques_used: ['First Principles Thinking', 'Morphological Analysis', 'Reverse Brainstorming']
ideas_generated: [29]
context_file: ''
---

# Brainstorming Session Results

**Facilitator:** mrqdt
**Date:** 2026-05-09

## Session Overview

**Topic:** Trading bot service architecture — consuming Redis streams and executing trades
**Goals:** Strategy architecture patterns, subscription models, data contracts, execution layer design

## QuestDB order_events Schema (final)

```sql
CREATE TABLE order_events (
    ts                  TIMESTAMP,  -- event timestamp
    order_id            SYMBOL,     -- exchange order ID
    client_order_id     SYMBOL,     -- internal UUID
    strategy            SYMBOL,
    exchange            SYMBOL,
    symbol              SYMBOL,
    market_type         SYMBOL,     -- spot / perp
    side                SYMBOL,     -- buy / sell
    order_type          SYMBOL,     -- limit / market
    status              SYMBOL,     -- placed/open/partially_filled/filled/cancelled/rejected
    limit_price         DOUBLE,
    stop_price          DOUBLE,     -- stop-loss trigger price (null if none)
    take_profit_price   DOUBLE,     -- take-profit target price (null if none)
    requested_size      DOUBLE,     -- original order size
    filled_size         DOUBLE,     -- cumulative filled so far
    remaining_size      DOUBLE,     -- requested_size - filled_size (order remainder)
    avg_fill_price      DOUBLE,
    fee                 DOUBLE,
    fee_currency        SYMBOL,
    realized_pnl        DOUBLE,     -- populated on filled / cancelled
    slippage            DOUBLE,     -- avg_fill_price - limit_price
    position_size_after DOUBLE,     -- total position size after this fill
    signal_type         SYMBOL,
    ts_placed           TIMESTAMP,
    ts_exchange         TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY WAL;
```

---

## Phase 1: First Principles — Decisions Captured

**[Architecture #1]** Three-tier Strategy Data Taxonomy
- TA strategies: spread + price + timestamp + book depth + pandas indicators (in-process per bot)
- Microstructure strategies: raw tick-level OB data
- Combination: both profiles

**[Architecture #2]** QuestDB Cold-Start Hydration
- On startup each bot queries QuestDB directly for lookback window (e.g. 200 bars), hydrates DataFrame, then switches to live Redis stream

**[Architecture #3]** Kill Switch deferred to later Epic

**[Architecture #4]** All arb types (pre-funded hedge, funding rate, statistical) as BaseStrategy subclasses — Python asyncio latency is fine at their operating timescale (seconds–hours)

**[Architecture #5]** Three supported arb types: pre-funded hedge arb, funding rate arb, statistical arb

**[Architecture #6]** Hedge Arb → dedicated Go service (not inside aggregator). 1ms Redis hop irrelevant at seconds-scale entry/exit windows. Aggregator stays single-responsibility (data capture only). Implementation deferred — open question: own WebSocket connections vs Redis subscriber; code sharing via `exchange-sdk` lib vs `pkg/` promotion.

**[Architecture #7]** Bot Service as Python FastAPI
- Handles: microstructure+TA bots, statistical arb, funding rate arb
- Saves trade results, positions, fills → QuestDB via ILP (not SQLite)
- Exposes /metrics for Grafana
- No frontend serving — frontend is a separate deferred service

**[Architecture #8]** Grafana-first observability for Bot Service
- Prometheus metrics: P&L, positions, fills, drawdown, consumer lag, execution latency per strategy
- Trade history + positions in QuestDB `order_events` table (queryable directly in Grafana)

**[Architecture #9]** JavaScript frontend deferred — will be a separate service for per-coin market data viewer (candlestick charts, OB, microstructure features)

**Perp/Futures support added to aggregator architecture (FR37–42):**
- `.PERP` suffix on normalized symbols (e.g. `BTCUSDT.PERP`)
- Stream keys: `ticks:{exchange}:{symbol}.PERP` and `funding:{exchange}:{symbol}`
- Perp tick fields: `mark_price`, `index_price`, `funding_rate`, `predicted_funding_rate`, `next_funding_ts`, `open_interest`
- Config: `SYMBOLS_KUCOIN_PERP`, `SYMBOLS_BYBIT_PERP`

**Indicator computation:** per-bot, in-process. Each bot maintains its own rolling DataFrame. No shared indicator service (infinite parameter combinations make sharing impractical).

**⏳ WAITING — Lookback window hydration:** resolved — bots query QuestDB directly on startup ✓

---

## Technique Selection

**Approach:** AI-Recommended
**Analysis Context:** Complex distributed system architecture, financial domain, real money at stake

**Recommended Techniques:**
- **First Principles Thinking:** Strip to fundamentals — what does a bot truly need from this pipeline? Prevent over-engineering the interface.
- **Morphological Analysis:** Systematically map all parameters (subscription models, signal types, execution modes, risk layers) and explore combinations.
- **Reverse Brainstorming:** Generate failure modes to surface hidden constraints and architectural non-negotiables.

**AI Rationale:** The sequence moves from foundational clarity → systematic exploration → adversarial hardening. Appropriate for a high-stakes financial system where correctness and robustness outweigh speed of delivery.

---

## Phase 2 Addendum — Testing Architecture

**[Architecture #30]** Backtrader for Backtesting + Shared Signal Layer
- **Backtrader** is the backtesting engine — event-driven, battle-tested, maintained
- Signal logic extracted to pure functions in `strategy/signals/` — no framework dependency
- Both live `BaseStrategy` handlers and Backtrader `bt.Strategy.next()` call the same signal functions — written once, tested once
- `QuestDBFeed` subclasses `bt.feeds.PandasData` — queries QuestDB/Parquet, exposes all 67 microstructure features as extra lines
- Custom `bt.CommissionInfo` matches exact KuCoin/Bybit maker/taker fee schedules
- Results written to QuestDB `order_events` with `backtest=True` — queryable in Grafana alongside paper and live
- Backtrader provides out of the box: walk-forward validation, OHLC fill simulation, drawdown/Sharpe/trade log, Monte Carlo via TradeAnalyzer

**Testing layers (no shadow mode):**
1. L1 unit + L2 integration + L3 chaos + reconciliation fuzzing on exchange testnet
2. Fee impact analysis — first gate before any deployment
3. Walk-forward validation via Backtrader date range splits on QuestDB historical data
4. Stress testing on LUNA collapse (2022-05), FTX collapse (2022-11), flash crashes via Backtrader
5. Monte Carlo — shuffle TradeAnalyzer results 10,000 times, recompute P&L distribution
6. Paper trading 30+ days with PaperExchangeClient + slippage calibration
7. Exchange testnet reconciliation chaos test
8. Minimum-size live (30–60 days) alongside paper, capital ladder to full size

**Go/no-go metrics per capital ladder step:** Sharpe, max drawdown, fill rate, real vs simulated slippage deviation, zero reconciliation failures.

---

## Phase 2: Morphological Analysis — Bot Service Design Axes

**[Architecture #10]** Event Bus as Universal Strategy Interface
- All data sources publish typed events onto a central bus
- Single `on_event` entry point replaced by typed handler registration
- Event types: BarClose, Tick, OBSnapshot, FundingRate, AISignal, GapMarker, OrderFilled, OrderRejected, RiskBreached

**[Architecture #11]** Smart Event Bus with Barrier Synchronization
- Bus holds BarClose events per timestamp until all symbols in a multi-symbol strategy have arrived
- Delivers unified MultiBarClose event
- Barrier timeout: 500ms after first symbol arrives — delivers with missing symbols as None + GapMarker if applicable

**[Architecture #12]** Barrier Timeout + Gap Propagation
- On timeout: deliver MultiBarClose with whatever arrived, flag missing symbols
- GapMarker for a symbol short-circuits the barrier immediately — strategy sees the gap

**[Architecture #13]** Option C — Registration Method (subscribe)
- Strategy implements `subscribe(bus: EventBus)` — runs at instantiation before event loop
- Can query QuestDB for dynamic symbol discovery, check volatility regime, conditional AI signal subscription
- Registers multiple typed handlers per event type and timeframe
- Routing table fixed after subscribe() completes (no mid-run re-subscription for MVP)

**[Architecture #14]** Multi-timeframe DataFrame Management
- BaseStrategy maintains one rolling DataFrame per (symbol, timeframe) pair
- `get_history(symbol, tf, n_bars)` cold-starts from QuestDB on first call
- Each BarClose updates the relevant DataFrame before handler fires
- Strategy handlers read `self.df["BTCUSDT"]["1m"]` — never manage DataFrame state directly

**[Architecture #15]** Per-Strategy Thread with Own asyncio Event Loop
- Each loaded strategy runs in a dedicated thread with `asyncio.new_event_loop()`
- Central Bus Manager thread reads all Redis streams, routes into per-strategy asyncio.Queues via `run_coroutine_threadsafe()`
- Strategy isolation: crash in one thread cannot affect others
- Barrier synchronization lives in BaseStrategy infrastructure, not in the bus

**[Architecture #16]** Per-Strategy Order Queue
- Each strategy has own asyncio.Queue for OrderRequests + dedicated order worker coroutine
- Strategy posts OrderRequest and immediately continues processing events
- Order worker handles REST call → receives fill → posts OrderFilled back to strategy event queue

**[Architecture #17]** Position Tracking — Hybrid Local/Exchange
- In-memory position state during runtime, updated on every OrderFilled
- On startup: query exchange REST for open positions/orders, reconcile against QuestDB
- Handles crash-during-fill: exchange is authoritative on restart

**[Architecture #18]** Per-Strategy Risk Enforcement
- `max_position_pct` and `stop_loss_pct` enforced in BaseStrategy before OrderRequest is posted
- Risk gate sits between signal generation and order queue
- No cross-strategy portfolio-level risk guard

**[Architecture #19]** QuestDB as Trade Store (replaces SQLite)
- `order_events` table: append-only event log per order state transition
- Written via ILP — same fire-and-forget pattern as aggregator and candle service
- Grafana reads directly for trading dashboards

**[Architecture #20]** Auto-restart with Exchange Reconciliation
- Watchdog monitors strategy threads with `thread.is_alive()`
- On crash: log exception, exponential backoff (5s→10s→30s→60s max), restart
- Restart sequence: reconcile exchange → subscribe → resume event loop
- Mid-crash open orders picked up from exchange — nothing lost

**[Architecture #21]** Exchange Connectivity — Direct REST + Private WebSocket
- Direct `httpx` (async) REST calls to KuCoin and Bybit — no ccxt
- Private WebSocket per exchange for real-time fill events (~10ms)
- Private credentials always required at startup — no public mode fallback
- HMAC auth (KuCoin) and API key signing (Bybit) implemented directly

**[Architecture #22]** Paper Trading Mode with Latency Simulation
- `paper_trading = True` class var swaps real ExchangeClient for PaperExchangeClient
- Simulates REST latency: random draw from configurable distribution (50–250ms)
- Checks live tick stream: did price cross limit during latency window?
- Market orders: fill at mid-price + configurable slippage bps
- Writes to QuestDB `order_events` with `paper_trading=true` column — filterable in Grafana

**[Architecture #23]** Open Order State — Three-Layer Tracking
- Layer 1: `self.open_orders` dict in-memory, keyed by client_order_id. Checked before every new order.
- Layer 2: Write to QuestDB only after exchange confirms placement. Every outcome saved:
  - Confirmed → status=open
  - Rejected → status=rejected + reason
  - Network timeout → status=failed + error detail
  - Partial confirmation → status=partially_filled
- Layer 3: On restart, query QuestDB for non-terminal orders → reconcile against exchange REST:
  - In QuestDB + on exchange → still open, load into self.open_orders
  - In QuestDB but NOT on exchange → write terminal event, update position
  - On exchange but NOT in QuestDB → status=orphaned in order_alerts table, alert operator, never auto-cancel

**[Architecture #24]** Open Positions Override Dynamic Discovery
- On restart: load open positions from exchange reconciliation first
- Force-subscribe to those symbols before running dynamic pair discovery
- Open positions always managed regardless of new pair selection

**[Architecture #25]** Heartbeat Timeout with Configurable Emergency Exit
- Per-strategy `bus_timeout_seconds` and `close_on_bus_timeout: bool`
- On timeout: always alert
- `close_on_bus_timeout=True`: market-sell all open positions immediately
- Defaults by type: microstructure=True, funding rate arb=False

**[Architecture #26]** Barrier Matches on Timeframe Boundary Floor
- `ts // tf_ms * tf_ms` for timestamp matching, not exact equality
- Tolerates cross-exchange clock skew up to one full bar period

**[Architecture #27]** Orphaned Exchange Orders — Alert, Never Auto-Cancel
- Orders on exchange but absent from QuestDB → status=orphaned in order_alerts table
- Operator must manually resolve — never auto-cancel silently

**[Architecture #28]** Simultaneous Opposite Positions Explicitly Allowed
- Independent strategies may hold opposite positions on the same symbol simultaneously — valid by design
- Each strategy manages its own entry, exit, stop, TP independently
- Net portfolio exposure not tracked or constrained — intentional

**[Architecture #29]** Funding Rate Arb Continuous Monitoring
- Subscribes to FundingRate events throughout position lifetime, not only at entry
- Exit conditions: basis convergence OR funding rate sign flip OR next_funding_ts within threshold with rate below minimum profitability
- Rate sign flip triggers immediate exit

---

## Phase 3: Reverse Brainstorming — Failure Modes & Constraints

| # | Failure | Implication | Architecture |
|---|---|---|---|
| 1 | Gap marker arrives, strategy computes signal on corrupted DataFrame | BaseStrategy must handle GapMarker and invalidate/pause signal computation — not optional | GapMarker event required in all subscriptions |
| 2 | Crash mid-order → restart places same order again | Check open_orders before every new signal | Architecture #23 |
| 3 | Bus Manager crashes silently — strategies sit idle with open positions | Per-strategy heartbeat timeout | Architecture #25 |
| 4 | Private WebSocket drops silently — fills stop, position tracking diverges | Private WS needs reconnect + heartbeat; REST poll fallback when WS silent >N seconds | Architecture #21 |
| 5 | QuestDB down at startup — DataFrame starts empty, NaN signals | Minimum lookback gate before enabling signal computation | Architecture #14 |
| 6 | NaN propagates silently through pandas indicators | Explicit NaN check before every signal action; BaseStrategy decorator | BaseStrategy contract |
| 7 | Dynamic symbol discovery returns different pairs on restart — open position unmanaged | Open positions force-subscribe before discovery runs | Architecture #24 |
| 8 | Candle service down — strategies idle with open positions | Heartbeat timeout triggers emergency close if configured | Architecture #25 |
| 9 | Clock skew between exchanges breaks barrier | Floor to timeframe boundary, not exact timestamp match | Architecture #26 |
| 10 | QuestDB ILP write drops silently — open order invisible on restart | Orphaned exchange orders → alert, never auto-cancel | Architecture #27 |
| 11 | Two strategies opposite direction on same symbol | Explicitly allowed by design | Architecture #28 |
| 12 | Funding rate flips sign — strategy pays instead of receives | Continuous FundingRate subscription, exit on sign flip | Architecture #29 |

---

## Idea Organization and Prioritization

### Thematic Clusters

**Theme 1 — Event Bus & Data Routing** (#10–13, #26)
Smart barrier, floor-timestamp clock-skew tolerance, runtime conditional registration, typed events. Foundation everything else depends on.

**Theme 2 — Strategy Execution Model** (#1, #2, #14, #15, #18)
Three-tier data taxonomy, per-strategy threads, multi-timeframe DataFrames, QuestDB cold-start, risk gate in BaseStrategy.

**Theme 3 — Order Lifecycle & Position Tracking** (#16, #17, #19, #23)
Per-strategy order queue, three-layer open order state, QuestDB event log with full lifecycle, exchange reconciliation.

**Theme 4 — Reliability & Recovery** (#20, #24, #25, #27, Failure modes)
Watchdog restart, position-first discovery override, heartbeat emergency exit, orphaned order alerts, GapMarker mandatory, NaN guard.

**Theme 5 — Exchange Connectivity & Simulation** (#21, #22, #30)
Direct httpx REST, private WebSocket fills, PaperExchangeClient, Backtrader + shared signal layer + QuestDBFeed.

**Theme 6 — Service Architecture** (#3–9, #28, #29)
FastAPI Bot Service, Grafana-first, QuestDB trade store, Hedge Arb deferred, frontend deferred, opposite positions by design, funding rate continuous monitoring.

### Breakthrough Concepts

1. **Three-layer open order state** — in-memory + QuestDB + exchange reconciliation. No single source trusted blindly. Handles every crash scenario without silent corruption.
2. **Shared signal layer** — pure functions in `strategy/signals/` called by both live BaseStrategy and Backtrader bt.Strategy. Signal logic written once, tested once, runs everywhere.
3. **Barrier with floor-timestamp matching** — tolerates cross-exchange clock skew without complex synchronization protocols. Simple floor division, big correctness gain.
4. **QuestDBFeed with 67 microstructure features** — feeds all existing candle service output directly into Backtrader. Full microstructure backtesting with no data transformation.

### Implementation Sequence

| Step | What | Unblocks |
|---|---|---|
| 1 | QuestDB DDL — `order_events`, `order_alerts` | Persistence |
| 2 | Event type dataclasses | Bus + strategies |
| 3 | BaseStrategy ABC + risk gate + NaN guard + GapMarker handler + heartbeat | All strategies |
| 4 | Event bus + barrier + routing table | Strategy execution |
| 5 | Exchange clients — KuCoin + Bybit REST + private WS | Live orders |
| 6 | PaperExchangeClient — latency sim + tick-based fill | Paper trading |
| 7 | Backtrader QuestDBFeed + commission model + shared signal layer | Backtesting |
| 8 | File watcher + watchdog + reconciliation | Strategy lifecycle |
| 9 | FastAPI shell + Prometheus /metrics | Observability |
| 10 | First strategy implementation | Real trading |

## Session Summary

**30 architecture decisions** across First Principles Thinking, Morphological Analysis, and Reverse Brainstorming.

**What was designed:**
A Python FastAPI Bot Service with a typed event bus, per-strategy asyncio threads, smart multi-symbol barrier synchronization, runtime conditional subscriptions, multi-timeframe DataFrames cold-started from QuestDB, three-layer open order state, direct exchange REST + private WebSocket fills, Backtrader backtesting with shared signal layer, PaperExchangeClient paper trading, Prometheus observability into Grafana, QuestDB as trade store, and a complete testing pyramid from unit tests through staged real-money capital ladder.

**Key principle throughout:** correctness and recoverability over cleverness. Every failure mode has an explicit designed response. No silent corruption, no auto-cancellation without operator awareness, no signal computation on stale or gap-affected data.
