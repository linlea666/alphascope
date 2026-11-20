"use client";

import { useEffect, useRef, useState } from "react";
import { LineChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, MarkLineComponent, CanvasRenderer]);

export type Line = { name: string; data: [string, number | null][]; color?: string; axis?: 0 | 1; dashed?: boolean; area?: boolean; connectNulls?: boolean };
type Fmt = "usd" | "pct" | "num";

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Time-series lines on one or two value axes, themed from the CSS variables (redrawn on theme change). */
export function LinesChart({ lines, locale, formats = ["num"], height = 280, zeroLine = false, empty }: { lines: Line[]; locale: string; formats?: Fmt[]; height?: number; zeroLine?: boolean; empty?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [theme, setTheme] = useState(0);
  useEffect(() => {
    const obs = new MutationObserver(() => setTheme((n) => n + 1));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  const hasData = lines.some((l) => l.data.length > 1);
  useEffect(() => {
    if (!ref.current || !hasData) return;
    const chart = echarts.init(ref.current);
    const text = cssVar("--muted");
    const border = cssVar("--border");
    const palette = [cssVar("--chart-1"), cssVar("--chart-2"), cssVar("--warn"), cssVar("--down")];
    const fmt = (f: Fmt) => {
      const usd = new Intl.NumberFormat(locale, { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });
      const pct = new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 1 });
      const num = new Intl.NumberFormat(locale, { maximumSignificantDigits: 4 });
      return (v: number) => (f === "usd" ? usd.format(v) : f === "pct" ? pct.format(v) : num.format(v));
    };
    const fmts = formats.map(fmt);
    chart.setOption({
      animation: false,
      grid: { left: 64, right: formats.length > 1 ? 64 : 20, top: 30, bottom: 48 },
      legend: { top: 0, textStyle: { color: text } },
      tooltip: {
        trigger: "axis",
        backgroundColor: cssVar("--panel"),
        borderColor: border,
        textStyle: { color: cssVar("--text"), fontSize: 12 },
        valueFormatter: undefined,
        formatter: (ps: { seriesName: string; value: [string, number | null]; color: string; seriesIndex: number }[]) => {
          if (!ps.length) return "";
          const head = String(ps[0].value[0]).replace("T", " ").slice(0, 16);
          return [head, ...ps.map((p) => `<span style="color:${p.color}">●</span> ${p.seriesName}: ${p.value[1] === null ? "—" : fmts[lines[p.seriesIndex].axis ?? 0](p.value[1])}`)].join("<br/>");
        },
      },
      xAxis: { type: "time", axisLabel: { color: text }, axisLine: { lineStyle: { color: border } } },
      yAxis: formats.map((_, i) => ({ type: "value", scale: true, position: i ? "right" : "left", axisLabel: { color: text, formatter: fmts[i] }, splitLine: { show: i === 0, lineStyle: { color: border } } })),
      dataZoom: [{ type: "inside" }, { type: "slider", height: 16, bottom: 6, textStyle: { color: text }, borderColor: border }],
      series: lines.map((l, i) => ({
        name: l.name,
        type: "line",
        showSymbol: false,
        connectNulls: l.connectNulls ?? true,
        yAxisIndex: l.axis ?? 0,
        data: l.data,
        lineStyle: { width: 1.8, color: l.color ?? palette[i % palette.length], type: l.dashed ? "dashed" : "solid" },
        itemStyle: { color: l.color ?? palette[i % palette.length] },
        areaStyle: l.area ? { opacity: 0.08 } : undefined,
        markLine: zeroLine && i === 0 ? { silent: true, symbol: "none", data: [{ yAxis: 0 }], lineStyle: { color: text, type: "dotted" }, label: { show: false } } : undefined,
      })),
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [lines, locale, formats, zeroLine, hasData, theme]);
  if (!hasData) return <p className="py-8 text-center text-sm text-muted">{empty}</p>;
  return <div ref={ref} className="w-full" style={{ height }} />;
}
