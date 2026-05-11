import type { OrderBook } from "./orderbook.js";

const ROW_H = 18;
const BAR_MAX_WIDTH = 120;
const PRICE_COL_W = 90;
const SIZE_COL_W = 80;
const FLASH_MS = 300;

interface FlashState {
  price: number;
  until: number;
  side: "bid" | "ask";
}

export class LadderRenderer {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private dpr = window.devicePixelRatio || 1;
  private rafId = 0;
  private pendingBook: OrderBook | null = null;
  private flashes: FlashState[] = [];
  private prevSizes = new Map<number, number>(); // price → size for flash detection

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d")!;
    this.resize();
    const ro = new ResizeObserver(() => this.resize());
    ro.observe(this.canvas.parentElement!);
  }

  resize() {
    const parent = this.canvas.parentElement!;
    const w = parent.clientWidth || parent.offsetWidth;
    const h = parent.clientHeight || parent.offsetHeight;
    if (w === 0 || h === 0) return;
    this.canvas.width = w * this.dpr;
    this.canvas.height = h * this.dpr;
    this.canvas.style.width = w + "px";
    this.canvas.style.height = h + "px";
    this.ctx.scale(this.dpr, this.dpr);
    if (this.pendingBook) this.drawNow(this.pendingBook);
  }

  render(book: OrderBook) {
    this.pendingBook = book;
    if (this.rafId === 0) {
      this.rafId = requestAnimationFrame(() => {
        this.rafId = 0;
        if (this.pendingBook) this.drawNow(this.pendingBook);
      });
    }
  }

  private drawNow(book: OrderBook) {
    const w = this.canvas.width / this.dpr;
    const h = this.canvas.height / this.dpr;
    const ctx = this.ctx;
    const now = Date.now();

    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#111";
    ctx.fillRect(0, 0, w, h);

    const maxSize = Math.max(
      ...book.bids.map((l) => l.size),
      ...book.asks.map((l) => l.size),
      1
    );

    const midY = h / 2;
    const totalLevels = Math.max(book.bids.length, book.asks.length);
    const visibleRows = Math.floor(midY / ROW_H);

    // Detect size changes for flashes
    const newFlashes: FlashState[] = [];
    for (const level of book.bids) {
      const prev = this.prevSizes.get(level.price);
      if (prev !== undefined && Math.abs(prev - level.size) > 0.001) {
        newFlashes.push({ price: level.price, until: now + FLASH_MS, side: "bid" });
      }
      this.prevSizes.set(level.price, level.size);
    }
    for (const level of book.asks) {
      const prev = this.prevSizes.get(level.price);
      if (prev !== undefined && Math.abs(prev - level.size) > 0.001) {
        newFlashes.push({ price: level.price, until: now + FLASH_MS, side: "ask" });
      }
      this.prevSizes.set(level.price, level.size);
    }
    this.flashes = [...this.flashes.filter((f) => f.until > now), ...newFlashes];

    const flashSet = new Map<number, FlashState>();
    for (const f of this.flashes) flashSet.set(f.price, f);

    const drawRow = (level: { price: number; size: number }, y: number, side: "bid" | "ask") => {
      const isBid = side === "bid";
      const flash = flashSet.get(level.price);
      const barW = (level.size / maxSize) * BAR_MAX_WIDTH;

      // Background bar
      ctx.fillStyle = flash
        ? (isBid ? "#2e7d3260" : "#c62828a0")
        : (isBid ? "#1b2e1b" : "#2e1b1b");
      ctx.fillRect(0, y, barW, ROW_H - 1);

      // Price
      ctx.fillStyle = isBid ? "#81c784" : "#e57373";
      ctx.font = "11px monospace";
      ctx.textBaseline = "middle";
      ctx.fillText(level.price.toFixed(2), 4, y + ROW_H / 2);

      // Size
      ctx.fillStyle = "#aaa";
      ctx.fillText(level.size.toFixed(4), PRICE_COL_W + 4, y + ROW_H / 2);
    };

    // Draw asks above mid — asks[0] is best ask (lowest price), drawn closest to mid.
    book.asks.slice(0, visibleRows).forEach((level, i) => {
      const y = midY - (i + 1) * ROW_H;
      if (y >= 0) drawRow(level, y, "ask");
    });

    // Draw mid line
    ctx.strokeStyle = "#555";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, midY);
    ctx.lineTo(w, midY);
    ctx.stroke();

    // Mid price + spread label in the gap below the midline
    if (book.midPrice > 0) {
      ctx.fillStyle = "#fff";
      ctx.font = "12px monospace";
      ctx.textBaseline = "middle";
      ctx.fillText(`${book.midPrice.toFixed(2)}  Δ${book.spread.toFixed(4)}`, 4, midY + ROW_H / 2);
      ctx.textBaseline = "alphabetic";
    }

    // Draw bids below mid + one row gap reserved for the price label
    book.bids.slice(0, visibleRows).forEach((level, i) => {
      const y = midY + ROW_H + i * ROW_H + 1;
      if (y + ROW_H <= h) drawRow(level, y, "bid");
    });

    // Column headers
    ctx.fillStyle = "#444";
    ctx.font = "10px monospace";
    ctx.fillText("PRICE", 4, 12);
    ctx.fillText("SIZE", PRICE_COL_W + 4, 12);
  }
}
