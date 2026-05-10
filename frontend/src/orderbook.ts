export interface Level {
  price: number;
  size: number;
}

export class OrderBook {
  bids: Level[] = [];
  asks: Level[] = [];
  midPrice = 0;
  spread = 0;
  lastUpdate = 0;

  applySnapshot(bids: Level[], asks: Level[], tsNs: number) {
    // Bids arrive sorted descending, asks ascending — from the encoder
    this.bids = bids;
    this.asks = asks;
    this.lastUpdate = tsNs;

    if (bids.length > 0 && asks.length > 0) {
      this.midPrice = (bids[0].price + asks[0].price) / 2;
      this.spread = asks[0].price - bids[0].price;
    }
  }

  /** Price range covered by the book (best bid - worst ask, with padding). */
  priceRange(padding = 0.1): { min: number; max: number } {
    const allPrices = [...this.bids, ...this.asks].map((l) => l.price);
    if (allPrices.length === 0) return { min: 0, max: 1 };
    const min = Math.min(...allPrices);
    const max = Math.max(...allPrices);
    const pad = (max - min) * padding;
    return { min: min - pad, max: max + pad };
  }
}
