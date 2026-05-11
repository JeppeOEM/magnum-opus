// Binary message type bytes
export const MSG_SNAPSHOT = 0x01;
export const MSG_HEARTBEAT = 0x03;
export const MSG_SUBSCRIBE = 0x10;
export const MSG_UNSUBSCRIBE = 0x11;

export interface SnapshotMsg {
  tsNs: number;
  bids: { price: number; size: number }[];
  asks: { price: number; size: number }[];
}

export interface Candles1sMsg {
  type: "candles1s";
  ts_ns: number;
  exchange: string;
  symbol: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  ofi: number;
  ofi_l1: number;
  spread: number;
  bid_depth_l1: number;
  ask_depth_l1: number;
  bid_depth_top10: number;
  ask_depth_top10: number;
  realized_vol: number;
}

type Handler<T> = (msg: T) => void;

export class WebSocketClient {
  private ws: WebSocket | null = null;
  private subscriptions = new Set<string>();
  private snapshotHandlers: Handler<SnapshotMsg>[] = [];
  private candles1sHandlers: Handler<Candles1sMsg>[] = [];
  private statusHandlers: Handler<string>[] = [];
  private retryDelay = 100;

  constructor(private url: string) {
    this.connect();
  }

  onSnapshot(h: Handler<SnapshotMsg>) { this.snapshotHandlers.push(h); }
  onCandles1s(h: Handler<Candles1sMsg>) { this.candles1sHandlers.push(h); }
  onStatus(h: Handler<string>) { this.statusHandlers.push(h); }

  subscribe(symbol: string) {
    this.subscriptions.add(symbol);
    this.sendSubscribe(symbol);
  }

  unsubscribe(symbol: string) {
    this.subscriptions.delete(symbol);
    this.sendUnsubscribe(symbol);
  }

  private emit<T>(handlers: Handler<T>[], msg: T) {
    for (const h of handlers) h(msg);
  }

  private connect() {
    this.emit(this.statusHandlers, "connecting");
    const ws = new WebSocket(this.url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;

    ws.addEventListener("open", () => {
      this.retryDelay = 100;
      this.emit(this.statusHandlers, "connected");
      for (const sym of this.subscriptions) this.sendSubscribe(sym);
    });

    ws.addEventListener("message", (ev: MessageEvent) => {
      if (ev.data instanceof ArrayBuffer) {
        this.decode(ev.data);
      } else if (typeof ev.data === "string") {
        this.decodeText(ev.data);
      }
    });

    ws.addEventListener("close", () => {
      this.emit(this.statusHandlers, "reconnecting");
      this.ws = null;
      const delay = this.retryDelay + Math.random() * this.retryDelay * 0.5;
      this.retryDelay = Math.min(this.retryDelay * 2, 30_000);
      setTimeout(() => this.connect(), delay);
    });

    ws.addEventListener("error", () => {
      this.emit(this.statusHandlers, "error");
    });
  }

  private decode(buf: ArrayBuffer) {
    const view = new DataView(buf);
    const type = view.getUint8(0);

    if (type === MSG_SNAPSHOT) {
      if (buf.byteLength < 13) {
        console.error(`[ws] snapshot too short: ${buf.byteLength} bytes, hex: ${hexDump(buf)}`);
        return;
      }
      const tsNs = view.getFloat64(1, false); // big-endian
      const bidCount = view.getUint16(9, true);
      const askCount = view.getUint16(11, true);
      const expected = 13 + (bidCount + askCount) * 12;
      if (buf.byteLength < expected) {
        console.error(`[ws] snapshot underflow: bidCount=${bidCount} askCount=${askCount} expected=${expected} got=${buf.byteLength}, hex: ${hexDump(buf)}`);
        return;
      }
      let off = 13;

      const bids: { price: number; size: number }[] = [];
      for (let i = 0; i < bidCount; i++) {
        const price = view.getFloat64(off, false); off += 8;
        const size = view.getFloat32(off, false); off += 4;
        bids.push({ price, size });
      }
      const asks: { price: number; size: number }[] = [];
      for (let i = 0; i < askCount; i++) {
        const price = view.getFloat64(off, false); off += 8;
        const size = view.getFloat32(off, false); off += 4;
        asks.push({ price, size });
      }

      this.emit(this.snapshotHandlers, { tsNs, bids, asks });
    }
    // HEARTBEAT (0x03): nothing to do beyond keeping the connection alive
  }

  private decodeText(text: string) {
    try {
      const msg = JSON.parse(text);
      if (msg.type === "candles1s") {
        this.emit(this.candles1sHandlers, msg as Candles1sMsg);
      }
    } catch {
      console.error("[ws] failed to parse text message:", text.slice(0, 200));
    }
  }

  private sendSubscribe(symbol: string) {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    const enc = new TextEncoder();
    const symBytes = enc.encode(symbol);
    const buf = new ArrayBuffer(2 + symBytes.length);
    const view = new DataView(buf);
    view.setUint8(0, MSG_SUBSCRIBE);
    view.setUint8(1, symBytes.length);
    new Uint8Array(buf, 2).set(symBytes);
    this.ws.send(buf);
  }

  private sendUnsubscribe(symbol: string) {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    const enc = new TextEncoder();
    const symBytes = enc.encode(symbol);
    const buf = new ArrayBuffer(2 + symBytes.length);
    const view = new DataView(buf);
    view.setUint8(0, MSG_UNSUBSCRIBE);
    view.setUint8(1, symBytes.length);
    new Uint8Array(buf, 2).set(symBytes);
    this.ws.send(buf);
  }
}

function hexDump(buf: ArrayBuffer): string {
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, "0")).join(" ");
}
