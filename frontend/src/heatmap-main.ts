import { WebSocketClient, type Candles1sMsg } from "./ws.js";
import { OrderBook } from "./orderbook.js";
import { HeatmapBuffer } from "./heatmap-buffer.js";
import { LadderRenderer } from "./ladder.js";
import { HeatmapRenderer } from "./heatmap.js";

const WS_URL = (import.meta.env.VITE_WS_URL as string | undefined) ?? "/ws";
const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? "";

const HEATMAP_COLS = 600;
const HEATMAP_BINS = 400;
const OFI_HISTORY_LEN = 20;

const symbolSelect = document.getElementById("symbol-select") as HTMLSelectElement;
const midPriceEl = document.getElementById("mid-price")!;
const spreadEl = document.getElementById("spread-label")!;
const statusEl = document.getElementById("status")!;
const ladderCanvas = document.getElementById("ladder-canvas") as HTMLCanvasElement;
const heatmapCanvas = document.getElementById("heatmap-canvas") as HTMLCanvasElement;
const axesCanvas = document.getElementById("axes-canvas") as HTMLCanvasElement;
const waitingOverlay = document.getElementById("waiting-overlay")!;
const toggleBtn = document.getElementById("nonlinear-toggle") as HTMLButtonElement;
const gaugeValueEl = document.getElementById("gauge-value")!;
const gaugeDescEl = document.getElementById("gauge-desc")!;

const book = new OrderBook();
const heatmapBuf = new HeatmapBuffer(HEATMAP_COLS, HEATMAP_BINS);
const ladder = new LadderRenderer(ladderCanvas);
let heatmap: HeatmapRenderer | null = null;
try {
  heatmap = new HeatmapRenderer(heatmapCanvas, heatmapBuf);
} catch (e) {
  console.warn("HeatmapRenderer init failed (WebGL2 unavailable?):", e);
  heatmapCanvas.style.display = "none";
  const msg = document.createElement("div");
  msg.style.cssText = "position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#f44;font-size:13px;";
  msg.textContent = "WebGL2 unavailable — heatmap disabled";
  heatmapCanvas.parentElement?.appendChild(msg);
}

const ws = new WebSocketClient(WS_URL);

let nonLinearMode = false;
let hasData = false;
let currentSecPriceMap = new Map<number, number>();
const ofiHistory: number[] = [];

toggleBtn.addEventListener("click", () => {
  nonLinearMode = !nonLinearMode;
  toggleBtn.textContent = nonLinearMode ? "Non-Linear ✓" : "Linear";
  currentSecPriceMap.clear();
});

ws.onStatus((s) => {
  statusEl.textContent = s;
  statusEl.className = s === "connected" ? "connected" : s === "error" ? "error" : "";
});

ws.onSnapshot(({ tsNs, bids, asks }) => {
  book.applySnapshot(bids, asks, tsNs);

  midPriceEl.textContent = book.midPrice > 0 ? book.midPrice.toFixed(2) : "—";
  spreadEl.textContent = `spread ${book.spread.toFixed(4)}`;

  ladder.render(book);

  if (book.midPrice > 0) {
    if (!hasData) {
      hasData = true;
      waitingOverlay.style.display = "none";
    }

    const range = book.priceRange(0.05);
    const priceStep = (range.max - range.min) / HEATMAP_BINS;

    if (nonLinearMode) {
      for (const level of [...bids, ...asks]) {
        const bin = Math.floor((level.price - range.min) / priceStep);
        if (bin >= 0 && bin < HEATMAP_BINS) {
          currentSecPriceMap.set(bin, (currentSecPriceMap.get(bin) ?? 0) + level.size);
        }
      }
    } else {
      const priceMap = new Map<number, number>();
      for (const level of [...bids, ...asks]) {
        const bin = Math.floor((level.price - range.min) / priceStep);
        if (bin >= 0 && bin < HEATMAP_BINS) {
          priceMap.set(bin, (priceMap.get(bin) ?? 0) + level.size);
        }
      }
      const col = heatmapBuf.writePosition;
      heatmapBuf.writeColumn(priceMap);
      heatmap?.updateColumn(col);
      drawAxes(range.min, range.max);
    }
  }
});

ws.onCandles1s((msg: Candles1sMsg) => {
  if (nonLinearMode && book.midPrice > 0) {
    const range = book.priceRange(0.05);
    flushNonLinear(msg.ofi, range.min, range.max);
  }
  updateGauge(msg);
});

function flushNonLinear(ofi: number, priceMin: number, priceMax: number) {
  ofiHistory.push(Math.abs(ofi));
  if (ofiHistory.length > OFI_HISTORY_LEN) ofiHistory.shift();
  const meanOfi = ofiHistory.reduce((a, b) => a + b, 0) / ofiHistory.length;
  const n = meanOfi > 0
    ? Math.max(1, Math.round(Math.min(4, Math.abs(ofi) / meanOfi)))
    : 1;
  for (let i = 0; i < n; i++) {
    const col = heatmapBuf.writePosition;
    heatmapBuf.writeColumn(currentSecPriceMap);
    heatmap?.updateColumn(col);
  }
  currentSecPriceMap = new Map();
  drawAxes(priceMin, priceMax);
}

function updateGauge(msg: Candles1sMsg) {
  const { ofi, spread, bid_depth_l1, ask_depth_l1 } = msg;
  gaugeDescEl.style.display = "none";
  if (spread === 0 || (bid_depth_l1 + ask_depth_l1) === 0) {
    gaugeValueEl.textContent = "—";
    return;
  }
  const imbalance = (bid_depth_l1 - ask_depth_l1) / (bid_depth_l1 + ask_depth_l1);
  const raw = (ofi / spread) * imbalance;
  const clamped = Math.min(1, Math.max(-1, raw));
  gaugeValueEl.textContent = clamped.toFixed(3);
  if (clamped > 0.3) {
    gaugeValueEl.style.color = "#4caf50";
  } else if (clamped < -0.3) {
    gaugeValueEl.style.color = "#ef5350";
  } else {
    gaugeValueEl.style.color = "#ff9800";
  }
}

ws.subscribe(symbolSelect.value);

symbolSelect.addEventListener("change", () => {
  const prev = symbolSelect.value;
  ws.unsubscribe(prev);
  book.bids = [];
  book.asks = [];
  heatmapBuf.data.fill(0);
  heatmapBuf.writePosition = 0;
  heatmap?.uploadAll();
  currentSecPriceMap.clear();
  ofiHistory.length = 0;
  hasData = false;
  waitingOverlay.style.display = "flex";
  ws.subscribe(symbolSelect.value);
  loadHistorical();
});

loadHistorical();

async function loadHistorical() {
  const symbol = symbolSelect.value;
  const end = Date.now();
  const start = end - HEATMAP_COLS * 3000;

  try {
    const res = await fetch(
      `${API_URL}/heatmap?symbol=${encodeURIComponent(symbol)}&exchange=kucoin` +
      `&start=${new Date(start).toISOString()}&end=${new Date(end).toISOString()}` +
      `&priceMin=0&priceMax=1000000&bins=${HEATMAP_BINS}`
    );
    if (!res.ok) return;
    const buf = await res.arrayBuffer();
    heatmapBuf.loadHistorical(buf, 0, 1_000_000);
    heatmap?.uploadAll();
  } catch {
    // Historical data unavailable
  }
}

function drawAxes(priceMin: number, priceMax: number) {
  const dpr = window.devicePixelRatio || 1;
  const parent = axesCanvas.parentElement!;
  const w = parent.clientWidth;
  const h = parent.clientHeight;
  axesCanvas.width = w * dpr;
  axesCanvas.height = h * dpr;
  axesCanvas.style.width = w + "px";
  axesCanvas.style.height = h + "px";

  const ctx = axesCanvas.getContext("2d")!;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  const steps = 6;
  ctx.fillStyle = "#888";
  ctx.font = "10px monospace";
  ctx.textAlign = "right";
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const price = priceMin + t * (priceMax - priceMin);
    const y = h - t * h;
    ctx.fillText(price.toFixed(0), w - 2, y + 4);
    ctx.strokeStyle = "#2a2a2a";
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w - 45, y);
    ctx.stroke();
  }
}
