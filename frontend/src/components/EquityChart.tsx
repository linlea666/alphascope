"use client";

import { useEffect, useRef, useState } from "react";
import { LineChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, CanvasRenderer]);

type Point = [string, number, number];

type Labels = { equity: string; cumPnl: string; seriesMonth: string; seriesAll: string; empty: string };

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function EquityChart({ month, allTime, labels, locale }: { month: Point[]; allTime: Point[]; labels: Labels; locale: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [series, setSeries] = useState<"month" | "allTime">(allTime.length ? "allTime" : "month");
  const [theme, setTheme] = useState(0);

  useEffect(() => {
    const obs = new MutationObserver(() => setTheme((n) => n + 1));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);

  const points = series === "month" ? month : allTime;

  useEffect(() => {
    if (!ref.current || !points.length) return;
    const chart = echarts.init(ref.current);
    const text = cssVar("--muted");
    const border = cssVar("--border");
    const usd = new Intl.NumberFormat(locale, { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 2 });
    chart.setOption({
      animation: false,
      grid: { left: 64, right: 64, top: 36, bottom: 56 },
      legend: { top: 0, textStyle: { color: text }, data: [labels.equity, labels.cumPnl] },
      tooltip: {
        trigger: "axis",
        valueFormatter: (v: number) => usd.format(v),
        backgroundColor: cssVar("--panel"),
        borderColor: border,
        textStyle: { color: cssVar("--text") },
      },
      xAxis: { type: "time", axisLabel: { color: text }, axisLine: { lineStyle: { color: border } } },
      yAxis: [
        { type: "value", scale: true, axisLabel: { color: text, formatter: (v: number) => usd.format(v) }, splitLine: { lineStyle: { color: border } } },
        { type: "value", scale: true, axisLabel: { color: text, formatter: (v: number) => usd.format(v) }, splitLine: { show: false } },
      ],
      dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8, textStyle: { color: text }, borderColor: border }],
      series: [
        { name: labels.equity, type: "line", showSymbol: false, data: points.map((p) => [p[0], p[1]]), lineStyle: { width: 1.5, color: cssVar("--chart-1") }, itemStyle: { color: cssVar("--chart-1") } },
        { name: labels.cumPnl, type: "line", yAxisIndex: 1, showSymbol: false, data: points.map((p) => [p[0], p[2]]), lineStyle: { width: 1.5, color: cssVar("--chart-2") }, itemStyle: { color: cssVar("--chart-2") } },
      ],
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [points, labels, locale, theme]);

  return (
    <div>
      <div className="mb-2 flex gap-1 text-xs">
        {(["month", "allTime"] as const).map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setSeries(s)}
            className={`rounded border px-2 py-1 ${series === s ? "border-accent text-accent" : "border-border text-muted hover:text-text"}`}
          >
            {s === "month" ? labels.seriesMonth : labels.seriesAll}
          </button>
        ))}
      </div>
      {points.length ? <div ref={ref} className="h-[320px] w-full" /> : <div className="py-16 text-center text-sm text-muted">{labels.empty}</div>}
    </div>
  );
}
