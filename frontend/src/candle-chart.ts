import * as d3 from "d3";
import type { Candle } from "./candle-store.js";

const PROFILE_W = 150;   // px reserved on the left for volume profile bars
const VOL_RATIO = 0.25;  // fraction of SVG height used by volume sub-chart
const GAP = 6;           // px between price chart and volume chart
const MARGIN = { top: 10, right: 60, bottom: 20 };
const VOL_MARGIN_BOTTOM = 30;

interface BotOrder {
  time: number;
  price: number;
  side: "buy" | "sell";
  strategy: string;
}

export class CandleChart {
  private svg: d3.Selection<SVGSVGElement, unknown, null, undefined>;
  private gCandles: d3.Selection<SVGGElement, unknown, null, undefined>;
  private gProfile: d3.Selection<SVGGElement, unknown, null, undefined>;
  private gVolume: d3.Selection<SVGGElement, unknown, null, undefined>;
  private gOverlay: d3.Selection<SVGGElement, unknown, null, undefined>;
  private gXAxis: d3.Selection<SVGGElement, unknown, null, undefined>;
  private gYAxis: d3.Selection<SVGGElement, unknown, null, undefined>;
  private gVolAxis: d3.Selection<SVGGElement, unknown, null, undefined>;
  private xScale = d3.scaleTime<number>();
  private yScale = d3.scaleLinear();
  private volYScale = d3.scaleLinear();
  private profileXScale = d3.scaleLinear();
  private width = 0;
  private priceH = 0;
  private volH = 0;
  private volY = 0;

  constructor(private container: HTMLElement) {
    this.svg = d3.select(container).append("svg")
      .style("width", "100%")
      .style("height", "100%")
      .style("display", "block");

    this.gProfile = this.svg.append("g").attr("class", "profile");
    this.gCandles = this.svg.append("g").attr("class", "candles");
    this.gVolume  = this.svg.append("g").attr("class", "volume");
    this.gOverlay = this.svg.append("g").attr("class", "overlay");
    this.gXAxis   = this.svg.append("g").attr("class", "x-axis");
    this.gYAxis   = this.svg.append("g").attr("class", "y-axis");
    this.gVolAxis = this.svg.append("g").attr("class", "vol-axis");

    // Dark theme axis styling injected once
    this.svg.append("style").text(`
      .x-axis text, .y-axis text, .vol-axis text { fill: #888; font: 10px monospace; }
      .x-axis path, .x-axis line,
      .y-axis path, .y-axis line,
      .vol-axis path, .vol-axis line { stroke: #333; }
      .mid-line { stroke: #555; stroke-width: 1; stroke-dasharray: 2,3; }
      .candle-wick { stroke-width: 1; }
    `);

    new ResizeObserver(() => this.redraw()).observe(container);
  }

  private dims() {
    const totalW = this.container.clientWidth;
    const totalH = this.container.clientHeight;
    this.width  = totalW - PROFILE_W - MARGIN.right;
    this.volH   = (totalH - MARGIN.top - GAP - VOL_MARGIN_BOTTOM) * VOL_RATIO;
    this.priceH = (totalH - MARGIN.top - GAP - VOL_MARGIN_BOTTOM) * (1 - VOL_RATIO);
    this.volY   = MARGIN.top + this.priceH + GAP;
  }

  private candles: Candle[] = [];
  private profileMap = new Map<number, number>();

  setData(candles: Candle[], profileMap: Map<number, number>) {
    this.candles = candles;
    this.profileMap = profileMap;
    this.redraw();
  }

  private redraw() {
    if (this.candles.length === 0) return;
    this.dims();

    const { candles, profileMap, width, priceH, volH, volY } = this;

    // X scale — time domain
    const xDomain = d3.extent(candles, d => d.time) as [number, number];
    const candleSpan = candles.length > 1 ? (xDomain[1] - xDomain[0]) / (candles.length - 1) : 3000;
    this.xScale
      .domain([xDomain[0], xDomain[1] + candleSpan])
      .range([PROFILE_W, PROFILE_W + width]);

    // Y price scale
    const yPad = (d3.max(candles, d => d.high)! - d3.min(candles, d => d.low)!) * 0.05;
    this.yScale
      .domain([d3.min(candles, d => d.low)! - yPad, d3.max(candles, d => d.high)! + yPad])
      .range([MARGIN.top + priceH, MARGIN.top]);

    // Y volume scale
    this.volYScale
      .domain([0, d3.max(candles, d => d.volume)!])
      .range([volY + volH, volY]);

    // Profile X scale
    const maxProfile = d3.max(Array.from(profileMap.values())) ?? 1;
    this.profileXScale
      .domain([0, maxProfile])
      .range([0, PROFILE_W - 4]);

    this.drawCandles();
    this.drawProfile();
    this.drawVolume();
    this.drawAxes();
    this.drawOverlay();
  }

  private drawCandles() {
    const { candles, xScale, yScale } = this;
    const candleSpan = candles.length > 1
      ? (xScale(candles[1].time) - xScale(candles[0].time))
      : 6;
    const bodyW = Math.max(1, candleSpan * 0.6);

    // Wicks
    const wicks = this.gCandles.selectAll<SVGLineElement, Candle>("line.candle-wick")
      .data(candles, d => d.time);
    wicks.enter().append("line").attr("class", "candle-wick")
      .merge(wicks as any)
      .attr("x1", d => xScale(d.time) + candleSpan / 2)
      .attr("x2", d => xScale(d.time) + candleSpan / 2)
      .attr("y1", d => yScale(d.high))
      .attr("y2", d => yScale(d.low))
      .attr("stroke", d => d.close >= d.open ? "#4caf50" : "#ef5350");
    wicks.exit().remove();

    // Bodies
    const bodies = this.gCandles.selectAll<SVGRectElement, Candle>("rect.candle-body")
      .data(candles, d => d.time);
    bodies.enter().append("rect").attr("class", "candle-body")
      .merge(bodies as any)
      .attr("x", d => xScale(d.time) + (candleSpan - bodyW) / 2)
      .attr("width", bodyW)
      .attr("y", d => yScale(Math.max(d.open, d.close)))
      .attr("height", d => Math.max(1, Math.abs(yScale(d.open) - yScale(d.close))))
      .attr("fill", d => d.close >= d.open ? "#4caf50" : "#ef5350");
    bodies.exit().remove();
  }

  private drawProfile() {
    const bw = this._bucketWidth;
    // Height of one bucket in pixels (may be <1 when many price levels are visible).
    const bucketPx = Math.max(1, Math.abs(this.yScale(0) - this.yScale(bw)));

    // Aggregate buckets that map to the same pixel row so bars never stack on top
    // of each other when bucketPx < 1.
    const rowVol = new Map<number, number>();
    for (const [price, vol] of this.profileMap) {
      const row = Math.round(this.yScale(price + bw));
      rowVol.set(row, (rowVol.get(row) ?? 0) + vol);
    }

    const maxVol = Math.max(...rowVol.values(), 1);
    const entries = Array.from(rowVol.entries()); // [pixelRow, aggregated volume]

    const bars = this.gProfile
      .selectAll<SVGRectElement, [number, number]>("rect.profile-bar")
      .data(entries, d => d[0]);

    bars.enter().append("rect").attr("class", "profile-bar")
      .merge(bars as any)
      .attr("x", 0)
      .attr("y", d => d[0])
      .attr("width", d => (d[1] / maxVol) * (PROFILE_W - 4))
      .attr("height", bucketPx)
      .attr("fill", "#1565c0")
      .attr("opacity", 0.7);
    bars.exit().remove();
  }

  private drawVolume() {
    const { candles, xScale, volYScale, volH, volY } = this;
    const candleSpan = candles.length > 1
      ? (xScale(candles[1].time) - xScale(candles[0].time))
      : 6;
    const barW = Math.max(1, candleSpan * 0.6);

    // Separator line
    this.svg.selectAll("line.vol-sep").data([0]).join("line")
      .attr("class", "vol-sep")
      .attr("x1", PROFILE_W).attr("x2", PROFILE_W + this.width)
      .attr("y1", volY - GAP / 2).attr("y2", volY - GAP / 2)
      .attr("stroke", "#333").attr("stroke-width", 1);

    const bars = this.gVolume.selectAll<SVGRectElement, Candle>("rect.vol-bar")
      .data(candles, d => d.time);
    bars.enter().append("rect").attr("class", "vol-bar")
      .merge(bars as any)
      .attr("x", d => xScale(d.time) + (candleSpan - barW) / 2)
      .attr("width", barW)
      .attr("y", d => volYScale(d.volume))
      .attr("height", d => Math.max(1, volY + volH - volYScale(d.volume)))
      .attr("fill", d => d.close >= d.open ? "#2e7d32" : "#b71c1c")
      .attr("opacity", 0.8);
    bars.exit().remove();
  }

  private drawAxes() {
    const { xScale, yScale, volYScale, priceH, volH, volY, width } = this;

    // X axis at bottom of volume chart
    this.gXAxis
      .attr("transform", `translate(0,${volY + volH})`)
      .call(d3.axisBottom(xScale)
        .ticks(Math.floor(width / 80))
        .tickFormat(d => d3.timeFormat("%H:%M:%S")(d as Date)));

    // Y price axis on right
    this.gYAxis
      .attr("transform", `translate(${PROFILE_W + width},0)`)
      .call(d3.axisRight(yScale).ticks(6));

    // Vol axis on right
    this.gVolAxis
      .attr("transform", `translate(${PROFILE_W + width},0)`)
      .call(d3.axisRight(volYScale).ticks(3)
        .tickFormat(d => d3.format(".2s")(d as number)));
  }

  private getTestOrders(): BotOrder[] {
    if (this.candles.length === 0) return [];
    const last = this.candles[this.candles.length - 1];
    return [
      { time: last.time, price: last.low * 0.999, side: "buy", strategy: "ofi_bot" },
      { time: last.time, price: last.high * 1.001, side: "sell", strategy: "ma_cross" },
    ];
  }

  private drawOverlay() {
    const orders = this.getTestOrders();
    if (orders.length === 0) return;
    const { xScale, yScale } = this;
    const candleSpan = this.candles.length > 1
      ? (xScale(this.candles[1].time) - xScale(this.candles[0].time))
      : 6;
    const size = 8;

    this.gOverlay.selectAll<SVGPathElement, BotOrder>("path.order-marker")
      .data(orders, d => `${d.time}-${d.side}`)
      .join(
        enter => enter.append("path").attr("class", "order-marker")
          .call(sel => sel.append("title")),
        update => update,
        exit => exit.remove(),
      )
      .attr("d", d => {
        const x = xScale(d.time) + candleSpan / 2;
        const y = yScale(d.price);
        if (d.side === "buy") {
          return `M${x},${y - size} L${x + size * 0.6},${y} L${x - size * 0.6},${y} Z`;
        }
        return `M${x},${y + size} L${x + size * 0.6},${y} L${x - size * 0.6},${y} Z`;
      })
      .attr("fill", d => d.side === "buy" ? "#4caf50" : "#ef5350")
      .attr("opacity", 0.85)
      .select("title")
      .text(d => d.strategy);
  }

  // Store bucket width for profile bar height calculation
  private _bucketWidth = 1;
  setBucketWidth(w: number) { this._bucketWidth = w; }
}
