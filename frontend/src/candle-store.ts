import type { SnapshotMsg } from "./ws.js";

export interface Candle {
  time: number; // Unix ms
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export class CandleStore {
  candles: Candle[] = [];
  profileMap = new Map<number, number>(); // bucketPrice → totalSize
  private prevClose = 0;
  private _bucketWidth = 1;

  get bucketWidth() { return this._bucketWidth; }

  setBucketWidth(w: number) {
    this._bucketWidth = w;
    this.rebuildProfile();
  }

  push(snap: SnapshotMsg) {
    const { tsNs, bids, asks } = snap;
    if (bids.length === 0 || asks.length === 0) return;

    const close = (bids[0].price + asks[0].price) / 2;
    const open = this.prevClose > 0 ? this.prevClose : close;
    const high = asks[0].price;
    const low = bids[0].price;
    const volume = [...bids, ...asks].reduce((s, l) => s + l.size, 0);

    this.candles.push({ time: tsNs / 1e6, open, high, low, close, volume });
    this.prevClose = close;

    // update profile incrementally
    for (const level of [...bids, ...asks]) {
      const bucket = Math.floor(level.price / this._bucketWidth) * this._bucketWidth;
      this.profileMap.set(bucket, (this.profileMap.get(bucket) ?? 0) + level.size);
    }
  }

  reset() {
    this.candles = [];
    this.profileMap.clear();
    this.prevClose = 0;
  }

  private rebuildProfile() {
    this.profileMap.clear();
    for (const c of this.candles) {
      // re-bucket using stored high/low as proxy for price range
      const bucket = Math.floor(c.close / this._bucketWidth) * this._bucketWidth;
      this.profileMap.set(bucket, (this.profileMap.get(bucket) ?? 0) + c.volume);
    }
  }
}
