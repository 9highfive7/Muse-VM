import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, CandlestickChart, LineChart, ScatterChart } from "echarts/charts";
import { AxisPointerComponent, GridComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsOption } from "echarts";

echarts.use([
  CandlestickChart, BarChart, LineChart, ScatterChart,
  GridComponent, TooltipComponent, AxisPointerComponent, MarkLineComponent,
  CanvasRenderer,
]);

/**
 * One ECharts instance. Every panel on the stock page passes the same `group`,
 * so crosshair / tooltip position are linked across panels (X axes linked,
 * 要件 2.3). Each panel has exactly one Y axis per grid — no dual-axis charts.
 */
export default function EChart({
  option,
  group,
  height,
  label,
}: {
  option: EChartsOption;
  group: string;
  height: number;
  label: string;
}) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!el.current) return;
    const c = echarts.init(el.current, undefined, { renderer: "canvas" });
    chart.current = c;
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      c.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    c.setOption(option, { notMerge: true });
    c.group = group;
    echarts.connect(group);
  }, [option, group]);

  return <div ref={el} className="echart" style={{ height }} role="img" aria-label={label} />;
}
