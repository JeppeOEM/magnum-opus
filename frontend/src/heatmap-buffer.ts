/**
 * Ring-buffer storing a grid of float32 values: columns = time, rows = price bins.
 * The GPU reads the flat Float32Array as an R32F texture and the fragment shader
 * unwraps the ring using fract(uv.x + writePosition).
 */
export class HeatmapBuffer {
  readonly data: Float32Array;
  readonly width: number;  // time columns
  readonly height: number; // price bins
  writePosition = 0;       // next column to write (0..width-1), normalised below for GPU

  constructor(width: number, height: number) {
    this.width = width;
    this.height = height;
    this.data = new Float32Array(width * height);
  }

  /**
   * Writes a column of size values at each price bin index.
   * priceMap: Map<binIndex, size> — sparse; missing bins stay 0.
   */
  writeColumn(priceMap: Map<number, number>) {
    const col = this.writePosition;
    // Clear the column
    for (let row = 0; row < this.height; row++) {
      this.data[col * this.height + row] = 0;
    }
    // Fill from map
    for (const [bin, size] of priceMap) {
      if (bin >= 0 && bin < this.height) {
        this.data[col * this.height + bin] = size;
      }
    }
    this.writePosition = (col + 1) % this.width;
  }

  /**
   * Returns write position normalised to [0, 1) for the fragment shader uniform.
   */
  get writePositionNorm(): number {
    return this.writePosition / this.width;
  }

  /**
   * Loads binary heatmap response from the gateway /heatmap endpoint.
   * Layout: uint32 timeCount, uint32 priceCount, float32[] timeAxis,
   *         float32[] priceAxis, float32[] cells (row=time, col=price).
   * Maps cells into this ring buffer in time order, ignoring axes.
   */
  loadHistorical(buf: ArrayBuffer, priceMin: number, priceMax: number) {
    const view = new DataView(buf);
    const timeCount = view.getUint32(0, true);
    const priceCount = view.getUint32(4, true);
    if (timeCount === 0) return;

    const priceStep = (priceMax - priceMin) / this.height;
    let off = 8 + (timeCount + priceCount) * 4; // skip axes

    for (let t = 0; t < timeCount; t++) {
      const priceMap = new Map<number, number>();
      for (let p = 0; p < priceCount; p++) {
        const val = view.getFloat32(off, true);
        off += 4;
        if (val > 0) {
          // Map from historical price bin to our bin
          const centerPrice = priceMin + (p + 0.5) * ((priceMax - priceMin) / priceCount);
          const ourBin = Math.floor((centerPrice - priceMin) / priceStep);
          if (ourBin >= 0 && ourBin < this.height) {
            priceMap.set(ourBin, (priceMap.get(ourBin) ?? 0) + val);
          }
        }
      }
      this.writeColumn(priceMap);
    }
  }
}
