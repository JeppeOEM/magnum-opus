# Epic 20 Retrospective — DeepCharts Parity Rendering

**Date:** 2026-05-12
**Epic:** 20 — DeepCharts Parity Rendering
**Status:** Complete (5/5 stories done, all reviewed)

---

## Epic Summary

**Delivered:** Full DeepCharts-parity rendering in the Dash dashboard, activating all Epic 19 signal fields visually:

- **20.1:** Liquidity overlay (POC line + Value Area band) + absorption diamond markers on candlestick chart
- **20.2:** Iceberg cell borders on OB depth heatmap (cyan bid / magenta ask square-open scatter markers)
- **20.3:** Per-candle footprint modal on candlestick clickData (bidirectional horizontal bar chart, proportional price y-axis)
- **20.4:** Imbalance diagonal stack highlighting in footprint modal (per-level color, stack count annotation)
- **20.5:** Single-print level highlighting in footprint modal (amber bars, SP count in title)

**Total stories completed:** 5/5
**Files modified:** `dashboard/charts.py`, `dashboard/callbacks.py`, `dashboard/layout.py`
**Review patches applied across epic:** ~14 patches, ~10 deferred items

---

## What Went Well

**1. Dashboard architecture was stable — standalone go.Figure() held throughout.**
After the 0411bae fix (standalone figures, no make_subplots base), every story added clean traces with no row/col arguments. No architecture re-work needed across 5 stories. The "simple figure" constraint became routine.

**2. Adversarial review caught a critical functional bug every story.**
- 20.2: `.values` positional assignment (index alignment risk)
- 20.3: Plotly normalizes clickData timestamps (modal would never open without the pd.Timestamp.floor fix)
- 20.4: No critical bugs (all High findings were false positives)
- 20.5: Price key format mismatch (silent miss between footprint_json and single_print_levels_json)

The timestamp normalization finding (20.3) was particularly high-value — the feature would have appeared broken in the browser with no error message, making it very hard to debug without knowing to look at clickData format.

**3. All imbalance/auction logic faithfully mirrors the Go candle-service implementation.**
Story 20.4 replicated `ComputeImbalance` (3× threshold, per-level boolean lists, longestRun) and story 20.5 used `single_print_levels_json` directly from QuestDB rather than recomputing. No divergence from the authoritative Go logic.

**4. json.loads → try/except pattern was consistently applied.**
Every JSON parsing operation (footprint_json, single_print_levels_json) is wrapped in try/except with safe fallback. The footprint chart never crashes the Dash worker regardless of data quality.

**5. Incremental modal enrichment pattern worked well.**
Stories 20.3 → 20.4 → 20.5 each added one visual layer to the footprint modal without re-architecting it. The `build_footprint_chart` function accepted successive optional parameters cleanly.

---

## Challenges

**1. Plotly clickData timestamp format is undocumented and surprising.**
When a candlestick x-axis is auto-detected as `date` type, Plotly.js silently normalizes `clickData["points"][0]["x"]` — stripping the `T` separator, `Z` suffix, and microseconds. This is not documented in Plotly's Python or JS docs. The exact string match `r.get("ts") == clicked_ts` silently failed in the browser with no server-side error. Fix required: `pd.Timestamp(ts).floor("s")` on both sides.

**Lesson:** For any Plotly clickData timestamp comparison, always normalize to second precision via `pd.Timestamp.floor("s")`. Never use exact string equality.

**2. `__import__("json")` anti-pattern crept in during initial implementation.**
`build_footprint_chart` initially used `__import__("json").loads(...)` inside the function body to avoid adding a top-level import. The blind hunter caught this as a maintainability issue. Fixed by adding `import json` at the top of charts.py. The lesson is to always add standard library imports at the module level.

**3. build_footprint_chart try/except scope was too narrow initially.**
The initial implementation only wrapped `json.loads()` in try/except. The list comprehensions and `float(cell.get(...))` calls were outside it — meaning a malformed cell in the footprint JSON would crash the Dash callback worker (caught at framework level, showing as a 500 in browser DevTools). Fixed by wrapping the entire function body. This is a general pattern for Dash: if a callback crashes, the UI silently shows the last known state, which is very hard to debug.

**Lesson:** In Dash callbacks and chart builder functions, wrap the entire body in try/except to ensure graceful degradation instead of framework-level 500 errors.

**4. Price key format normalization is implicit, not guaranteed.**
The footprint_json and single_print_levels_json both use string price keys from the same Go `footprintMap`, so in practice their formats match exactly. However, the dashboard code had no defensive normalization — a future schema change or data migration could break the set lookup silently. Fixed by normalizing both sides through `str(float(key))`.

---

## Key Insights

1. **Plotly clickData timestamps are normalized by the browser.** Never use exact string comparison for Plotly x-axis timestamps. Always normalize through `pd.Timestamp(ts).floor("s")`.

2. **Wrap the full Dash chart builder body in try/except, not just the risky line.** Dash catches exceptions at the framework level and serves stale state — silent failure is worse than a crash.

3. **Frozenset for O(1) price-level membership lookup is the right pattern.** The footprint chart may have 50–200 price levels; linear scan would work but frozenset makes the intent and performance explicit.

4. **Reuse the Go algorithm directly in Python rather than reimplementing from data.** Story 20.4's imbalance computation (`s > 0 and b > 3.0 * s`) matches Go exactly; story 20.5 uses the pre-computed `single_print_levels_json` from QuestDB rather than re-running the 10% threshold logic. Both approaches prevent Python/Go divergence.

5. **Per-bar color lists in Plotly `go.Bar` are well-supported.** Passing `marker_color` as a list of hex strings (one per bar) is the correct API for conditional bar coloring. This is not obvious from the Plotly docs but works cleanly.

6. **The review "High finding that is actually wrong" pattern.** Story 20.4's High finding (stack direction inversion) was mathematically incorrect — the reviewer's own examples disproved it. The `_longest_run` result is invariant under list reversal. When a review finding's examples all show "no difference," the finding should be challenged before applying a patch.

---

## Review Findings Summary

| Story | High Patch | Med Patch | High False Positive | Deferred |
|---|---|---|---|---|
| 20.1 | absorption_overlay never wired | ts KeyError, close KeyError, va inversion | — | 2 |
| 20.2 | `.values` alignment (conservative) | string "true" normalization | — | 2 |
| 20.3 | Timestamp normalization, full try/except, numeric y-axis | — | — | 3 |
| 20.4 | — | float("abc") (defer) | Stack direction inversion | 2 |
| 20.5 | — | Price key normalization, isinstance guard | — | 2 |

---

## Epic 20 Complete — Project Status

Epic 20 is the last in-progress epic. With its completion:

| Epic | Status |
|---|---|
| Epics 1–17 | Done |
| Epic 18 (Dashboard Core) | Done |
| Epic 19 (Candle Footprint + Signals) | Done |
| **Epic 20 (DeepCharts Parity)** | **Done** |
| Epic 15 (Bot Strategies) | In-progress (15-4 blocked — funding rate arb) |

The trading system now has:
- Real-time order book processing (aggregator)
- 1s OHLCV + 67 microstructure features (candle-service)
- Multi-timeframe cascade (candle-service)
- Live Dash dashboard with footprint modal, imbalance/SP highlighting, heatmaps, CVD, bid/ask panels
- Three strategy implementations (Epic 15, one blocked)

---

## Action Items

### Documentation
1. Add Plotly clickData timestamp normalization note to `CLAUDE.md` or a dashboard-specific dev note.

### Technical Debt
- Modal re-open after client-side dismiss race (20.3 defer) — low priority
- Modal stays open on symbol switch (20.3 defer) — UX issue, low priority
- Single-print title shows "SP: 0" for pre-migration rows (20.5 defer) — cosmetic only

### No blocking items for any future work.
