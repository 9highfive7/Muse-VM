// ECharts option builders for the financial section (要件 3.4・3.5・3.6).
//
// Same rules as chartOptions.ts:
// * One Y axis per grid. Different units always go to separate panels
//   (amounts / % / 倍 are never mixed). No dual-axis chart.
// * Red/blue stay reserved for candles; series here use a neutral validated
//   set (teal / purple / ochre / ink) and a second encoding (filled vs. hollow,
//   dashed borders, direct labels) so identity never relies on colour alone.
// * Colour never means good/bad: negative amounts use the same colour.
// * Forecasts are drawn as hollow (border-only) bars, actuals filled (3.4).
// * No animation (6.2).
import type { EChartsOption } from "echarts";
import type { AnnualForecast, AnnualRow, FinItem, Num, Percentile, ProgressItem, QuarterRow } from "../api";
import {
  fmt,
  GRID_LEFT,
  GRID_RIGHT,
  INK,
  lineSeries,
  MUTED,
  tooltipBase,
  xAxis,
  yAxis,
  type AxisParam,
} from "./chartOptions";

// validated with the dataviz palette checker (light surface): all checks pass
export const FIN_COLORS = {
  teal: "#0090a8",
  purple: "#6b3fa0",
  ochre: "#a87400",
  gray: "#8a8a8a",
  ink: INK,
};

const OKU = 1e8;

/** Yen with 兆/億/万 units, same rules as the server's wording.yen(). */
export function fmtYen(v: Num | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  let s: string;
  if (a >= 1e12) {
    let cho = Math.floor(a / 1e12);
    let oku = Math.round((a % 1e12) / 1e8);
    if (oku >= 10000) {
      cho += 1;
      oku = 0;
    }
    s = `${cho.toLocaleString("ja-JP")}兆${oku ? `${oku.toLocaleString("ja-JP")}億` : ""}円`;
  } else if (a >= 1e10) {
    s = `${Math.round(a / 1e8).toLocaleString("ja-JP")}億円`;
  } else if (a >= 1e8) {
    s = `${(Math.round((a / 1e8) * 10) / 10).toLocaleString("ja-JP", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}億円`;
  } else if (a >= 1e4) {
    s = `${Math.round(a / 1e4).toLocaleString("ja-JP")}万円`;
  } else {
    s = `${Math.round(a).toLocaleString("ja-JP")}円`;
  }
  return v < 0 && s !== "0円" ? `−${s}` : s;
}

const toOku = (v: Num | undefined) => (v === null || v === undefined ? null : v / OKU);

function grid(bottom = 28) {
  return [{ left: GRID_LEFT, right: GRID_RIGHT, top: 30, bottom }];
}

function categoryTooltip(lines: (i: number, cat: string) => string[], cats: string[]) {
  return {
    ...tooltipBase,
    axisPointer: { type: "shadow" as const, shadowStyle: { color: "rgba(0,0,0,0.05)" } },
    formatter: (raw: unknown) => {
      const params = (Array.isArray(raw) ? raw : [raw]) as AxisParam[];
      const cat = params[0]?.axisValue ?? "";
      const i = cats.indexOf(cat);
      return [`<b>${cat}</b>`, ...lines(i, cat)].join("<br/>");
    },
  };
}

/** Dashed reference line without an in-chart label (the value is printed in
 *  the HTML legend / caption instead, so labels never collide with marks). */
function plainLines(values: number[]) {
  return {
    silent: true,
    symbol: "none",
    lineStyle: { color: "#8a8a8a", type: "dashed" as const, width: 1 },
    label: { show: false },
    data: values.map((v) => ({ yAxis: v })),
  };
}

const filled = (color: string) => ({ color, borderColor: color, borderWidth: 1 });
const hollow = (color: string) => ({ color: "#ffffff", borderColor: color, borderWidth: 1.75, borderType: "dashed" as const });

// ---------------------------------------------------------------- 通期業績推移 (3.4)

export interface AnnualCats {
  cats: string[];
  forecastIndex: number | null;
}

export function annualCategories(rows: AnnualRow[], forecast: AnnualForecast | null): AnnualCats {
  const cats = rows.map((r) => r.fiscal_year);
  if (forecast && !cats.includes(forecast.fiscal_year)) {
    cats.push(`${forecast.fiscal_year}（予想）`);
    return { cats, forecastIndex: cats.length - 1 };
  }
  return { cats, forecastIndex: null };
}

function markLabel(r: AnnualRow): string {
  const marks = [];
  if (r.standard_changed) marks.push("基準変更");
  if (r.irregular_year) marks.push(`変則${r.months ?? ""}か月`);
  return marks.join("・");
}

/** One amount item (売上高 / 営業利益 / 純利益) — its own panel, bars in 億円. */
export function annualItemOption(
  rows: AnnualRow[],
  forecast: AnnualForecast | null,
  item: FinItem,
  label: string,
  color: string,
): EChartsOption {
  const { cats, forecastIndex } = annualCategories(rows, forecast);
  const data: unknown[] = rows.map((r) => {
    const mark = markLabel(r);
    return {
      value: toOku(r[item]),
      itemStyle: filled(color),
      label: mark ? { show: true, formatter: mark, position: "top", color: INK, fontSize: 11 } : undefined,
    };
  });
  if (forecastIndex !== null && forecast) {
    data.push({
      value: toOku(forecast[item]),
      itemStyle: hollow(color),
      label: { show: true, formatter: "予想", position: "top", color: INK, fontSize: 11 },
    });
  }
  return {
    animation: false,
    grid: grid(),
    xAxis: [xAxis(cats, 0, true)],
    yAxis: [yAxis(0, `${label}（億円）`, { scale: false })],
    tooltip: categoryTooltip((i) => {
      if (i === forecastIndex && forecast) return [`${label}（今期予想） ${fmtYen(forecast[item])}`, `${forecast.disclosed_date}開示`];
      const r = rows[i];
      if (!r) return [];
      const extra = markLabel(r);
      return [`${label}（実績） ${r[item] === null ? "未開示" : fmtYen(r[item])}`, `${r.disclosed_date}開示${extra ? `・${extra}` : ""}`];
    }, cats),
    series: [{ type: "bar", name: label, data, barMaxWidth: 34, emphasis: { disabled: true } }],
  } as EChartsOption;
}

/** 営業利益率 (%) — its own panel. Forecast point is hollow, joined by a dashed line. */
export function marginOption(rows: AnnualRow[], forecast: AnnualForecast | null): EChartsOption {
  const { cats, forecastIndex } = annualCategories(rows, forecast);
  const actual: Num[] = rows.map((r) => r.operating_margin);
  const fc: ({ value: Num; symbol?: string } | null)[] = cats.map(() => null);
  if (forecastIndex !== null && forecast) {
    actual.push(null);
    fc[forecastIndex] = { value: forecast.operating_margin };
    // connector from the last actual point: no symbol, so the actual ● stays visible
    if (rows.length) fc[rows.length - 1] = { value: rows[rows.length - 1].operating_margin, symbol: "none" };
  }
  return {
    animation: false,
    grid: grid(),
    xAxis: [xAxis(cats, 0, true)],
    yAxis: [yAxis(0, "営業利益率（%）", { scale: false })],
    tooltip: categoryTooltip((i) => {
      if (i === forecastIndex && forecast) return [`営業利益率（今期予想） ${fmt(forecast.operating_margin, 1)}%`];
      const r = rows[i];
      return r ? [`営業利益率（実績） ${r.operating_margin === null ? "算出不可" : `${fmt(r.operating_margin, 1)}%`}`] : [];
    }, cats),
    series: [
      {
        ...lineSeries("営業利益率（実績）", actual, INK, { width: 2 }),
        showSymbol: true,
        symbol: "circle",
        symbolSize: 8,
      },
      {
        ...lineSeries("営業利益率（今期予想）", [], INK, { width: 1.5, type: "dashed" }),
        data: fc,
        showSymbol: true,
        symbol: "emptyCircle",
        symbolSize: 10,
        itemStyle: { color: INK, borderWidth: 2 },
        z: 4,
      },
    ],
  } as EChartsOption;
}

// ---------------------------------------------------------------- 四半期単独値 (3.4)

export function quarterlyOption(rows: QuarterRow[], item: FinItem, label: string, color: string): EChartsOption {
  const cats = rows.map((q) => q.label);
  const data = rows.map((q) => ({
    value: toOku(q.values[item]),
    itemStyle: filled(color),
    label: q.special_factors_note ? { show: true, formatter: "※", position: "top", color: INK, fontSize: 12 } : undefined,
  }));
  return {
    animation: false,
    grid: grid(),
    xAxis: [xAxis(cats, 0, true)],
    yAxis: [yAxis(0, `${label}・単独（億円）`, { scale: false })],
    tooltip: categoryTooltip((i) => {
      const q = rows[i];
      if (!q) return [];
      const v = q.values[item];
      return [
        `${label}（単独） ${v === null ? q.reasons[item] ?? "算出不可" : fmtYen(v)}`,
        `${q.disclosed_date}開示`,
        ...(q.special_factors_note ? ["※第4四半期（年度末特別要因が集中しやすい）"] : []),
      ];
    }, cats),
    series: [{ type: "bar", name: label, data, barMaxWidth: 30, emphasis: { disabled: true } }],
  } as EChartsOption;
}

// ---------------------------------------------------------------- 進捗率 (3.4)

export function progressOption(item: ProgressItem, fiscalYear: string, periodLabel: string): EChartsOption {
  const cats = [`今期 ${fiscalYear}`, ...item.pattern_years.map((y) => y.fiscal_year)];
  const values: Num[] = [item.actual_pct, ...item.pattern_years.map((y) => y.pct)];
  const data = values.map((v, i) => ({
    value: v,
    itemStyle: i === 0 ? filled(FIN_COLORS.teal) : filled(FIN_COLORS.gray),
    label: { show: v !== null, formatter: `${fmt(v, 1)}%`, position: "top", color: INK, fontSize: 11 },
  }));
  const avg = item.pattern_avg_pct;
  return {
    animation: false,
    grid: grid(),
    xAxis: [xAxis(cats, 0, true)],
    yAxis: [yAxis(0, `${periodLabel}の進捗率（%）`, { scale: false, min: 0 })],
    tooltip: categoryTooltip((i) => {
      if (i === 0)
        return [`今期：通期予想に対する進捗率 ${item.actual_pct === null ? item.actual_reason ?? "算出不可" : `${fmt(item.actual_pct, 1)}%`}`];
      const y = item.pattern_years[i - 1];
      return y ? [`同四半期累計 ÷ 同年度の通期実績 ${y.pct === null ? y.reason ?? "算出不可" : `${fmt(y.pct, 1)}%`}`] : [];
    }, cats),
    series: [
      {
        type: "bar",
        name: "進捗率",
        data,
        barMaxWidth: 44,
        emphasis: { disabled: true },
        ...(avg !== null ? { markLine: plainLines([avg]) } : {}),
      },
    ],
  } as EChartsOption;
}

// ---------------------------------------------------------------- 簡易財務・CF (3.5)

/** 総資産・自己資本 (same unit: 億円) in one panel. */
export function balanceOption(rows: AnnualRow[]): EChartsOption {
  const cats = rows.map((r) => r.fiscal_year);
  return {
    animation: false,
    grid: grid(),
    xAxis: [xAxis(cats, 0, true)],
    yAxis: [yAxis(0, "億円", { scale: false })],
    tooltip: categoryTooltip((i) => {
      const r = rows[i];
      return r ? [`総資産 ${fmtYen(r.total_assets)}`, `自己資本 ${fmtYen(r.equity)}`] : [];
    }, cats),
    series: [
      { type: "bar", name: "総資産", data: rows.map((r) => toOku(r.total_assets)), itemStyle: filled(FIN_COLORS.gray), barMaxWidth: 26, barGap: "12%", emphasis: { disabled: true } },
      { type: "bar", name: "自己資本", data: rows.map((r) => toOku(r.equity)), itemStyle: filled(FIN_COLORS.teal), barMaxWidth: 26, emphasis: { disabled: true } },
    ],
  } as EChartsOption;
}

/** 自己資本比率 (%) — its own panel. */
export function equityRatioOption(rows: AnnualRow[]): EChartsOption {
  const cats = rows.map((r) => r.fiscal_year);
  return {
    animation: false,
    grid: grid(),
    xAxis: [xAxis(cats, 0, true)],
    yAxis: [yAxis(0, "自己資本比率（%）")],
    tooltip: categoryTooltip((i) => {
      const r = rows[i];
      return r ? [`自己資本比率 ${r.equity_ratio_pct === null ? "未開示" : `${fmt(r.equity_ratio_pct, 1)}%`}（期末）`] : [];
    }, cats),
    series: [{ ...lineSeries("自己資本比率", rows.map((r) => r.equity_ratio_pct), INK, { width: 2 }), showSymbol: true, symbol: "circle", symbolSize: 8 }],
  } as EChartsOption;
}

export const CF_LOOK = {
  cf_operating: { label: "営業CF", color: FIN_COLORS.teal },
  cf_investing: { label: "投資CF", color: FIN_COLORS.purple },
  cf_financing: { label: "財務CF", color: FIN_COLORS.ochre },
} as const;

/** 営業・投資・財務CF (same unit: 億円), grouped bars, one panel. */
export function cashFlowOption(rows: AnnualRow[]): EChartsOption {
  const cats = rows.map((r) => r.fiscal_year);
  const keys = Object.keys(CF_LOOK) as (keyof typeof CF_LOOK)[];
  return {
    animation: false,
    grid: grid(),
    xAxis: [xAxis(cats, 0, true)],
    yAxis: [yAxis(0, "キャッシュフロー（億円）", { scale: false })],
    tooltip: categoryTooltip((i) => {
      const r = rows[i];
      return r ? keys.map((k) => `${CF_LOOK[k].label} ${r[k] === null ? "未開示" : fmtYen(r[k])}`) : [];
    }, cats),
    series: keys.map((k, n) => ({
      type: "bar",
      name: CF_LOOK[k].label,
      data: rows.map((r) => toOku(r[k])),
      itemStyle: filled(CF_LOOK[k].color),
      barMaxWidth: 22,
      barGap: "12%",
      emphasis: { disabled: true },
      ...(n === 0 ? { markLine: plainLines([0]) } : {}),
    })),
  } as EChartsOption;
}

/** 簡易フリーCF (億円) — its own panel. */
export function fcfOption(rows: AnnualRow[]): EChartsOption {
  const cats = rows.map((r) => r.fiscal_year);
  return {
    animation: false,
    grid: grid(),
    xAxis: [xAxis(cats, 0, true)],
    yAxis: [yAxis(0, "簡易フリーCF（億円）", { scale: false })],
    tooltip: categoryTooltip((i) => {
      const r = rows[i];
      return r ? [`簡易フリーCF（営業CF＋投資CF） ${r.simple_fcf === null ? "算出不可" : fmtYen(r.simple_fcf)}`] : [];
    }, cats),
    series: [{ type: "bar", name: "簡易フリーCF", data: rows.map((r) => toOku(r.simple_fcf)), itemStyle: filled(FIN_COLORS.gray), barMaxWidth: 30, emphasis: { disabled: true }, markLine: plainLines([0]) }],
  } as EChartsOption;
}

/** 営業CF ÷ 純利益 (倍) — its own panel, with the 1.0 reference and the 0.5 check line. */
export function ocfRatioOption(rows: AnnualRow[], reference: number, check: number): EChartsOption {
  const cats = rows.map((r) => r.fiscal_year);
  return {
    animation: false,
    grid: grid(),
    xAxis: [xAxis(cats, 0, true)],
    yAxis: [yAxis(0, "営業CF÷純利益（倍）", { scale: false })],
    tooltip: categoryTooltip((i) => {
      const r = rows[i];
      return r ? [`営業CF÷純利益 ${r.ocf_to_profit === null ? "算出不可（純利益が0以下または未開示）" : `${fmt(r.ocf_to_profit, 2)}倍`}`] : [];
    }, cats),
    series: [
      {
        ...lineSeries("営業CF÷純利益", rows.map((r) => r.ocf_to_profit), INK, { width: 2 }),
        showSymbol: true,
        symbol: "circle",
        symbolSize: 8,
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: "#8a8a8a", type: "dashed" as const, width: 1 },
          label: { position: "insideEndTop" as const, color: MUTED, fontSize: 11 },
          data: [
            { yAxis: reference, label: { formatter: `${reference.toFixed(1)}倍（参照値）` } },
            { yAxis: check, label: { formatter: `${check.toFixed(1)}倍（確認ポイントの基準値）` } },
          ],
        },
      },
    ],
  } as EChartsOption;
}

// ---------------------------------------------------------------- バリュエーション (3.6)

/** Daily valuation series (PER / PBR / 配当利回り) — one panel each, linked X. */
export function valuationSeriesOption(
  dates: string[],
  values: Num[],
  name: string,
  unit: string,
  pct: Percentile | undefined,
  showX: boolean,
): EChartsOption {
  const marks =
    pct && pct.state === "ok"
      ? ([
          [pct.p25, "25%点"],
          [pct.p50, "中央値"],
          [pct.p75, "75%点"],
        ] as [Num | undefined, string][]).filter((m): m is [number, string] => typeof m[0] === "number")
      : [];
  return {
    animation: false,
    grid: [{ left: GRID_LEFT, right: GRID_RIGHT, top: 30, bottom: showX ? 28 : 12 }],
    xAxis: [xAxis(dates, 0, showX)],
    yAxis: [yAxis(0, `${name}（${unit}）`)],
    tooltip: {
      ...tooltipBase,
      formatter: (raw: unknown) => {
        const params = (Array.isArray(raw) ? raw : [raw]) as AxisParam[];
        const date = params[0]?.axisValue ?? "";
        const v = params[0]?.value as Num;
        return `<b>${date}</b><br/>${name} ${v === null || v === undefined ? "算出不可" : `${fmt(v, 2)}${unit}`}`;
      },
    },
    series: [
      {
        ...lineSeries(name, values, INK, { width: 1.5 }),
        ...(marks.length ? { markLine: plainLines(marks.map(([v]) => v)) } : {}),
      },
    ],
  } as EChartsOption;
}

