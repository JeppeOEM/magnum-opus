import { WebSocketClient } from "./ws.js";
import { OrderBook } from "./orderbook.js";
import { HeatmapBuffer } from "./heatmap-buffer.js";
import { LadderRenderer } from "./ladder.js";
import { HeatmapRenderer } from "./heatmap.js";

const WS_URL = (import.meta.env.VITE_WS_URL as string | undefined) ?? "/ws";
const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? "";

const HEATMAP_COLS = 600;
const HEATMAP_BINS = 400;

const symbolSelect = document.getElementById("symbol-select") as HTMLSelectElement;
const midPriceEl = document.getElementById("mid-price")!;
const spreadEl = document.getElementById("spread-label")!;
const statusEl = document.getElementById("status")!;
const ladderCanvas = document.getElementById("ladder-canvas") as HTMLCanvasElement;
const heatmapCanvas = document.getElementById("heatmap-canvas") as HTMLCanvasElement;
const axesCanvas = document.getElementById("axes-canvas") as HTMLCanvasElement;

const book = new OrderBook();
const heatmapBuf = new HeatmapBuffer(HEATMAP_COLS, HEATMAP_BINS);
const ladder = new LadderRenderer(ladderCanvas);
const heatmap = new HeatmapRenderer(heatmapCanvas, heatmapBuf);

const ws = new WebSocketClient(WS_URL);

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
    const range = book.priceRange(0.05);
    const priceStep = (range.max - range.min) / HEATMAP_BINS;
    const priceMap = new Map<number, number>();

    for (const level of [...bids, ...asks]) {
      const bin = Math.floor((level.price - range.min) / priceStep);
      if (bin >= 0 && bin < HEATMAP_BINS) {
        priceMap.set(bin, (priceMap.get(bin) ?? 0) + level.size);
      }
    }

    const prevCol = (heatmapBuf.writePosition - 1 + HEATMAP_COLS) % HEATMAP_COLS;
    heatmapBuf.writeColumn(priceMap);
    heatmap.updateColumn(prevCol);
    drawAxes(range.min, range.max);
  }
});

ws.subscribe(symbolSelect.value);

symbolSelect.addEventListener("change", () => {
  const prev = symbolSelect.value;
  ws.unsubscribe(prev);
  book.bids = [];
  book.asks = [];
  heatmapBuf.data.fill(0);
  heatmapBuf.writePosition = 0;
  heatmap.uploadAll();
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
    heatmap.uploadAll();
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
