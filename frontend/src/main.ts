import { WebSocketClient } from "./ws.js";
import { OrderBook } from "./orderbook.js";
import { LadderRenderer } from "./ladder.js";
import type { Candle } from "./candle-store.js";
import { CandleChart } from "./candle-chart.js";

const WS_URL  = (import.meta.env.VITE_WS_URL  as string | undefined) ?? "/ws";
const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? "";

const symbolSelect   = document.getElementById("symbol-select")   as HTMLSelectElement;
const intervalSelect = document.getElementById("interval-select")  as HTMLSelectElement;
const bucketSelect   = document.getElementById("bucket-width")     as HTMLSelectElement;
const midPriceEl     = document.getElementById("mid-price")!;
const spreadEl       = document.getElementById("spread-label")!;
const statusEl       = document.getElementById("status")!;
const ladderCanvas   = document.getElementById("ladder-canvas")    as HTMLCanvasElement;
const chartPanel     = document.getElementById("chart-panel")      as HTMLElement;

const book   = new OrderBook();
const ladder = new LadderRenderer(ladderCanvas);
const chart  = new CandleChart(chartPanel);

// ── candle state ─────────────────────────────────────────────────────────────

let lastCandles: Candle[] = [];

function buildProfileMap(candles: Candle[], bucketWidth: number): Map<number, number> {
  const m = new Map<number, number>();
  for (const c of candles) {
    const bucket = Math.floor(c.close / bucketWidth) * bucketWidth;
    m.set(bucket, (m.get(bucket) ?? 0) + c.volume);
  }
  return m;
}

function refreshChart() {
  chart.setData(lastCandles, buildProfileMap(lastCandles, parseFloat(bucketSelect.value)));
}

// ── candle fetching ───────────────────────────────────────────────────────────

async function loadCandles() {
  const symbol      = symbolSelect.value;
  const intervalSec = intervalSelect.value;
  const url = `${API_URL}/candles?symbol=${symbol}&interval=${intervalSec}`;
  try {
    const res = await fetch(url);
    if (!res.ok) return;
    const candles: Candle[] = await res.json();
    lastCandles = candles;
    refreshChart();
  } catch {
    // network error — keep showing last data
  }
}

let pollTimer: ReturnType<typeof setInterval> | null = null;

function startPolling() {
  if (pollTimer !== null) clearInterval(pollTimer);
  // Poll at the candle interval, capped to [3s, 30s].
  const intervalSec = parseInt(intervalSelect.value, 10);
  const pollMs = Math.min(Math.max(intervalSec, 3), 30) * 1000;
  pollTimer = setInterval(loadCandles, pollMs);
}

// ── WebSocket (ladder + live mid/spread + live candle update) ─────────────────

const ws = new WebSocketClient(WS_URL);

ws.onStatus((s) => {
  statusEl.textContent = s;
  statusEl.className = s === "connected" ? "connected" : s === "error" ? "error" : "";
});

let rafId: number | null = null;

ws.onSnapshot((snap) => {
  const { tsNs, bids, asks } = snap;
  book.applySnapshot(bids, asks, tsNs);
  midPriceEl.textContent = book.midPrice > 0 ? book.midPrice.toFixed(2) : "—";
  spreadEl.textContent   = `spread ${book.spread.toFixed(4)}`;
  ladder.render(book);

  // Keep the developing (last) candle current with live mid-price.
  if (book.midPrice > 0 && lastCandles.length > 0) {
    const intervalMs = parseInt(intervalSelect.value, 10) * 1000;
    const tMs        = tsNs / 1e6;
    const bucketMs   = Math.floor(tMs / intervalMs) * intervalMs;
    const last       = lastCandles[lastCandles.length - 1];

    if (last.time === bucketMs) {
      last.close = book.midPrice;
      if (book.midPrice > last.high) last.high = book.midPrice;
      if (book.midPrice < last.low)  last.low  = book.midPrice;
    } else if (bucketMs > last.time) {
      // New bucket opened — add a developing candle until the next poll
      lastCandles.push({
        time:   bucketMs,
        open:   last.close,
        high:   Math.max(book.midPrice, last.close),
        low:    Math.min(book.midPrice, last.close),
        close:  book.midPrice,
        volume: 0,
      });
    }

    // Throttle chart redraws to once per animation frame (~60 fps max).
    if (rafId === null) {
      rafId = requestAnimationFrame(() => {
        rafId = null;
        refreshChart();
      });
    }
  }
});

let currentSymbol = symbolSelect.value;
ws.subscribe(currentSymbol);

// ── controls ─────────────────────────────────────────────────────────────────

symbolSelect.addEventListener("change", () => {
  ws.unsubscribe(currentSymbol);
  currentSymbol = symbolSelect.value;
  book.bids = [];
  book.asks = [];
  lastCandles = [];
  ws.subscribe(currentSymbol);
  loadCandles();
  startPolling();
});

intervalSelect.addEventListener("change", () => {
  lastCandles = [];
  loadCandles();
  startPolling();
});

bucketSelect.addEventListener("change", () => {
  const bw = parseFloat(bucketSelect.value);
  chart.setBucketWidth(bw);
  chart.setData(lastCandles, buildProfileMap(lastCandles, bw));
});

// ── boot ─────────────────────────────────────────────────────────────────────

loadCandles();
startPolling();
