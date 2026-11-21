// ECharts option builders for the stock page (要件 3.2・3.3).
//
// Rules enforced here:
// * Up candle: #FF4B00 border, white (hollow) body. Down candle: #005AFF filled.
//   Volume bars follow the same colour + hollow/filled rule.
// * Red/blue are used only for up/down candles (6.2). Everything else is neutral.
// * MAs differ by line style and width as well as colour (5d thin solid,
//   25d thick solid, 75d dashed, 200d dotted) so they survive grayscale.
// * Every grid has exactly one Y axis. No dual-axis chart anywhere.
// * All panels use the same category X axis (same dates, same left/right
//   margins) and are linked via echarts.connect (see EChart.tsx).
// * No animation (6.2).
import type { EChartsOption } from "echarts";
import type { Candle, ChartResponse, CrossEvent, MaKey, Num, TechnicalsResponse } from "../api";

export const UP = "#FF4B00";
export const DOWN = "#005AFF";
export const INK = "#1a1a1a";
export const MUTED = "#4a4a4a";
const GRIDLINE = "#e3e3e3";
export const GRID_LEFT = 76;
export const GRID_RIGHT = 20;

export const MA_LOOK: Record<MaKey, { color: string; width: number; type: "solid" | "dashed" | "dotted"; label: string }> = {
  sma5: { color: "#8a6100", width: 1, type: "solid", label: "5日（細い実線）" },
  sma25: { color: "#1a1a1a", width: 2.75, type: "solid", label: "25日（太い実線）" },
  sma75: { color: "#6b3fa0", width: 1.75, type: "dashed", label: "75日（破線）" },
  sma200: { color: "#5c5c5c", width: 2.25, type: "dotted", label: "200日（点線）" },
};

export function fmt(v: Num | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const r = Number(v.toFixed(digits));
  const s = Math.abs(r).toLocaleString("ja-JP", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return r < 0 ? `−${s}` : s;
}

export function fmtSigned(v: Num | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const r = Number(v.toFixed(digits));
  if (r === 0) return (0).toFixed(digits);
  return r > 0 ? `+${fmt(r, digits)}` : fmt(r, digits);
}

function compactVolume(v: number): string {
  if (Math.abs(v) >= 1e8) return `${fmt(v / 1e8, 1)}億`;
  if (Math.abs(v) >= 1e4) return `${fmt(v / 1e4, 0)}万`;
  return fmt(v, 0);
}

export const baseText = { color: MUTED, fontSize: 11 };

export function xAxis(dates: string[], gridIndex: number, showLabels: boolean) {
  return {
    type: "category" as const,
    data: dates,
    gridIndex,
    boundaryGap: true,
    axisLine: { lineStyle: { color: "#9a9a9a" } },
    axisTick: { show: showLabels, alignWithLabel: true },
    axisLabel: { show: showLabels, ...baseText, hideOverlap: true },
    axisPointer: { label: { show: showLabels } },
  };
}

export function yAxis(gridIndex: number, name: string, extra: Record<string, unknown> = {}) {
  return {
    type: "value" as const,
    gridIndex,
    scale: true,
    name,
    nameLocation: "end" as const,
    nameTextStyle: { ...baseText, align: "left" as const, padding: [0, 0, 0, -GRID_LEFT + 8] },
    axisLabel: baseText,
    splitLine: { lineStyle: { color: GRIDLINE } },
    ...extra,
  };
}

export const tooltipBase = {
  trigger: "axis" as const,
  axisPointer: { type: "line" as const, lineStyle: { color: "#767676" } },
  backgroundColor: "#ffffff",
  borderColor: "#767676",
  textStyle: { color: INK, fontSize: 12 },
  confine: true,
};

export type AxisParam = { axisValue?: string; seriesName?: string; value?: unknown };

export function lineSeries(name: string, data: Num[], color: string, opts: { width?: number; type?: "solid" | "dashed" | "dotted"; xAxisIndex?: number; yAxisIndex?: number } = {}) {
  return {
    type: "line" as const,
    name,
    data,
    xAxisIndex: opts.xAxisIndex ?? 0,
    yAxisIndex: opts.yAxisIndex ?? 0,
    showSymbol: false,
    symbol: "none",
    connectNulls: false,
    lineStyle: { color, width: opts.width ?? 1.5, type: opts.type ?? "solid" },
    itemStyle: { color },
    emphasis: { disabled: true },
  };
}

export function refLine(value: number, label: string) {
  return {
    silent: true,
    symbol: "none",
    lineStyle: { color: "#8a8a8a", type: "dashed" as const, width: 1 },
    label: { formatter: label, position: "insideEndTop" as const, color: MUTED, fontSize: 11 },
    data: [{ yAxis: value }],
  };
}

const isUp = (c: Candle) => c.close !== null && c.open !== null && c.close >= c.open;

// ---------------------------------------------------------------- price + volume

export function priceVolumeOption(ch: ChartResponse): EChartsOption {
  const dates = ch.dates;
  const byDate = new Map(ch.candles.map((c) => [c.date, c]));
  const earnings = new Map(ch.markers.earnings.map((m) => [m.bar_date, m]));
  const stopHigh = new Set(ch.markers.stop_high);
  const stopLow = new Set(ch.markers.stop_low);

  const candleData = ch.candles.map((c) => [c.open, c.close, c.low, c.high]);
  const volumeData = ch.candles.map((c) => ({
    value: c.volume,
    itemStyle: isUp(c)
      ? { color: "#ffffff", borderColor: UP, borderWidth: 1 }
      : { color: DOWN, borderColor: DOWN, borderWidth: 1 },
  }));

  const markerSeries = [
    {
      type: "scatter" as const,
      name: "決算開示日",
      data: ch.markers.earnings.map((m) => [m.bar_date, byDate.get(m.bar_date)?.high ?? null]),
      symbol: "triangle",
      symbolRotate: 180, // ▼
      symbolSize: 11,
      symbolOffset: [0, -26],
      itemStyle: { color: INK },
      label: { show: true, formatter: "決", position: "top" as const, color: INK, fontSize: 12, fontWeight: "bold" as const },
      z: 5,
    },
    {
      type: "scatter" as const,
      name: "ストップ高",
      data: ch.markers.stop_high.map((d) => [d, byDate.get(d)?.high ?? null]),
      symbol: "triangle", // ▲
      symbolSize: 10,
      symbolOffset: [0, -10],
      itemStyle: { color: INK },
      z: 5,
    },
    {
      type: "scatter" as const,
      name: "ストップ安",
      data: ch.markers.stop_low.map((d) => [d, byDate.get(d)?.low ?? null]),
      symbol: "triangle",
      symbolRotate: 180, // ▽ (hollow)
      symbolSize: 10,
      symbolOffset: [0, 10],
      itemStyle: { color: "#ffffff", borderColor: INK, borderWidth: 1.5 },
      z: 5,
    },
  ];

  return {
    animation: false,
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: GRID_LEFT, right: GRID_RIGHT, top: 36, height: 300 },
      { left: GRID_LEFT, right: GRID_RIGHT, top: 372, height: 90 },
    ],
    xAxis: [xAxis(dates, 0, false), xAxis(dates, 1, true)],
    yAxis: [
      yAxis(0, "価格（円・調整後）"),
      yAxis(1, "出来高（株・調整後）", { scale: false, splitNumber: 2, axisLabel: { ...baseText, formatter: (v: number) => compactVolume(v) } }),
    ],
    tooltip: {
      ...tooltipBase,
      formatter: (raw: unknown) => {
        const params = (Array.isArray(raw) ? raw : [raw]) as AxisParam[];
        const date = params[0]?.axisValue ?? "";
        const c = byDate.get(date);
        if (!c) return date;
        const i = dates.indexOf(date);
        const lines = [
          `<b>${date}</b>`,
          `始値 ${fmt(c.open)}　高値 ${fmt(c.high)}　安値 ${fmt(c.low)}　終値 ${fmt(c.close)}（円）`,
          `出来高 ${fmt(c.volume, 0)}株`,
          (Object.keys(MA_LOOK) as MaKey[]).map((k) => `${k.replace("sma", "")}日線 ${fmt(ch.ma[k][i])}`).join("　"),
        ];
        const m = earnings.get(date);
        if (m) lines.push(`▼決 決算開示日 ${m.date}`);
        if (stopHigh.has(date)) lines.push("▲ ストップ高");
        if (stopLow.has(date)) lines.push("▽ ストップ安");
        return lines.join("<br/>");
      },
    },
    series: [
      {
        type: "candlestick",
        name: "ローソク足",
        data: candleData,
        xAxisIndex: 0,
        yAxisIndex: 0,
        barMaxWidth: 14,
        itemStyle: { color: "#ffffff", borderColor: UP, color0: DOWN, borderColor0: DOWN, borderWidth: 1 },
        emphasis: { disabled: true },
      },
      ...ch.ma_styles.map((s) => {
        const look = MA_LOOK[s.key];
        return { ...lineSeries(`${s.label}移動平均`, ch.ma[s.key], look.color, { width: look.width, type: look.type }), z: 3 };
      }),
      ...markerSeries,
      {
        type: "bar",
        name: "出来高",
        data: volumeData,
        xAxisIndex: 1,
        yAxisIndex: 1,
        barMaxWidth: 14,
        emphasis: { disabled: true },
      },
    ],
  } as EChartsOption;
}

// ---------------------------------------------------------------- single-panel helpers

export function panel(dates: string[], yName: string, series: unknown[], opts: { height?: number; yExtra?: Record<string, unknown>; showX?: boolean; digits?: number } = {}): EChartsOption {
  const digits = opts.digits ?? 2;
  return {
    animation: false,
    grid: [{ left: GRID_LEFT, right: GRID_RIGHT, top: 30, bottom: opts.showX === false ? 12 : 28 }],
    xAxis: [xAxis(dates, 0, opts.showX !== false)],
    yAxis: [yAxis(0, yName, opts.yExtra)],
    tooltip: {
      ...tooltipBase,
      formatter: (raw: unknown) => {
        const params = (Array.isArray(raw) ? raw : [raw]) as AxisParam[];
        const date = params[0]?.axisValue ?? "";
        return [`<b>${date}</b>`, ...params.map((p) => `${p.seriesName} ${fmt(p.value as Num, digits)}`)].join("<br/>");
      },
    },
    series,
  } as EChartsOption;
}

/** ①トレンド: close + 25d/75d with the crosses inside the range. */
export function trendOption(ch: ChartResponse, events: CrossEvent[]): EChartsOption {
  const closes = ch.candles.map((c) => c.close);
  const inRange = events.filter((e) => ch.dates.includes(e.date));
  const idx = (d: string) => ch.dates.indexOf(d);
  return panel(ch.dates, "円（調整後）", [
    lineSeries("終値", closes, "#9a9a9a", { width: 1 }),
    lineSeries("25日移動平均", ch.ma.sma25, MA_LOOK.sma25.color, { width: MA_LOOK.sma25.width }),
    lineSeries("75日移動平均", ch.ma.sma75, MA_LOOK.sma75.color, { width: MA_LOOK.sma75.width, type: "dashed" }),
    {
      type: "scatter",
      name: "25日線が75日線を上抜け",
      data: inRange.filter((e) => e.kind === "golden").map((e) => [e.date, ch.ma.sma25[idx(e.date)]]),
      symbol: "circle",
      symbolSize: 11,
      itemStyle: { color: "#ffffff", borderColor: INK, borderWidth: 2 },
      label: { show: true, formatter: "上抜け", position: "top", color: INK, fontSize: 11 },
      z: 5,
    },
    {
      type: "scatter",
      name: "25日線が75日線を下抜け",
      data: inRange.filter((e) => e.kind === "dead").map((e) => [e.date, ch.ma.sma25[idx(e.date)]]),
      symbol: "rect",
      symbolSize: 10,
      itemStyle: { color: INK },
      label: { show: true, formatter: "下抜け", position: "bottom", color: INK, fontSize: 11 },
      z: 5,
    },
  ], { digits: 1 });
}

/** ②過熱感: RSI(14) — its own panel, 0–100. */
export function rsiOption(t: TechnicalsResponse): EChartsOption {
  return panel(t.dates, "RSI(14)", [
    { ...lineSeries("RSI(14)", t.series.rsi, INK, { width: 1.5 }), markLine: { ...refLine(70, "70（慣行的な目安）"), data: [{ yAxis: 70 }, { yAxis: 30 }], label: { formatter: "{c}（慣行的な目安）", position: "insideEndTop", color: MUTED, fontSize: 11 } } },
  ], { yExtra: { min: 0, max: 100, scale: false, interval: 10, axisLabel: { ...baseText, formatter: (v: number) => (v % 20 === 0 || v === 30 || v === 70 ? String(v) : "") } }, digits: 1 });
}

/** ②過熱感: Bollinger bands (yen) — its own panel. */
export function bollingerOption(t: TechnicalsResponse): EChartsOption {
  return panel(t.dates, "円（調整後）", [
    lineSeries("終値", t.series.close, INK, { width: 1.25 }),
    lineSeries("中心（20日移動平均）", t.series.bb_mid, "#6b3fa0", { width: 1.25 }),
    lineSeries("+2σ", t.series.bb_upper, "#5c5c5c", { width: 1.25, type: "dashed" }),
    lineSeries("−2σ", t.series.bb_lower, "#5c5c5c", { width: 1.25, type: "dotted" }),
  ], { digits: 1 });
}

/** ③転換: MACD / signal / histogram — same unit, its own panel. */
export function macdOption(t: TechnicalsResponse): EChartsOption {
  return panel(t.dates, "MACD（円）", [
    {
      type: "bar",
      name: "ヒストグラム",
      data: t.series.macd_hist,
      itemStyle: { color: "#bdbdbd", borderColor: "#7a7a7a", borderWidth: 0.5 },
      barMaxWidth: 10,
      emphasis: { disabled: true },
    },
    { ...lineSeries("MACD", t.series.macd, INK, { width: 1.75 }), markLine: refLine(0, "0") },
    lineSeries("シグナル", t.series.macd_signal, "#6b3fa0", { width: 1.5, type: "dashed" }),
  ]);
}

/** ④相対比較: stock and TOPIX indexed to 100 on the same axis. */
export function indexedOption(t: TechnicalsResponse): EChartsOption {
  return panel(t.dates, `指数（${t.stage4.t0 ?? "先頭日"}＝100）`, [
    { ...lineSeries("銘柄", t.series.idx_stock, INK, { width: 2 }), markLine: refLine(100, "100") },
    lineSeries("TOPIX", t.series.idx_topix, "#6b3fa0", { width: 1.75, type: "dashed" }),
  ], { digits: 1 });
}

/** ④相対比較: relative-strength line — its own panel. */
export function rsLineOption(t: TechnicalsResponse): EChartsOption {
  return panel(t.dates, "相対力ライン", [
    { ...lineSeries("相対力ライン", t.series.rs, INK, { width: 1.75 }), markLine: refLine(100, "100") },
  ], { digits: 1 });
}
