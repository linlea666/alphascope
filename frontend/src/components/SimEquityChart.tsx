"use client";

import { useEffect, useRef, useState } from "react";
import { BarChart, LineChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, CanvasRenderer]);

type Labels = { sim: string; leader: string; daily: string; empty: string };

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Simulated return vs the leader's account-value change (both from the sim's first snapshot), plus daily PnL bars. */
export function SimEquityChart({ equity, daily, capital, labels, locale }: { equity: [string, number, number, number | null][]; daily: { day: string; pnl: number }[]; capital: number; labels: Labels; locale: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [theme, setTheme] = useState(0);

  useEffect(() => {
    const obs = new MutationObserver(() => setTheme((n) => n + 1));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);

  useEffect(() => {
    if (!ref.current || !equity.length) return;
    const chart = echarts.init(ref.current);
    const text = cssVar("--muted");
    const border = cssVar("--border");
    const pct = new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 2, signDisplay: "exceptZero" });
    const usd = new Intl.NumberFormat(locale, { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 2 });
    const lead0 = equity.find((e) => e[3] !== null)?.[3] ?? null;
    chart.setOption({
      animation: false,
      grid: [
        { left: 64, right: 24, top: 32, height: "52%" },
        { left: 64, right: 24, top: "72%", bottom: 48 },
      ],
      legend: { top: 0, textStyle: { color: text }, data: [labels.sim, labels.leader, labels.daily] },
      tooltip: { trigger: "axis", backgroundColor: cssVar("--panel"), borderColor: border, textStyle: { color: cssVar("--text") } },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      xAxis: [
        { type: "time", gridIndex: 0, axisLabel: { show: false }, axisLine: { lineStyle: { color: border } } },
        { type: "time", gridIndex: 1, axisLabel: { color: text }, axisLine: { lineStyle: { color: border } } },
      ],
      yAxis: [
        { type: "value", gridIndex: 0, scale: true, axisLabel: { color: text, formatter: (v: number) => pct.format(v) }, splitLine: { lineStyle: { color: border } } },
        { type: "value", gridIndex: 1, splitNumber: 2, axisLabel: { color: text, formatter: (v: number) => usd.format(v) }, splitLine: { lineStyle: { color: border } } },
      ],
      dataZoom: [{ type: "inside", xAxisIndex: [0, 1] }, { type: "slider", xAxisIndex: [0, 1], height: 16, bottom: 8, textStyle: { color: text }, borderColor: border }],
      series: [
        {
          name: labels.sim,
          type: "line",
          showSymbol: false,
          data: equity.map((e) => [e[0], e[1] / capital - 1]),
          lineStyle: { width: 1.6, color: cssVar("--chart-1") },
          itemStyle: { color: cssVar("--chart-1") },
          tooltip: { valueFormatter: (v: number) => pct.format(v) },
        },
        {
          name: labels.leader,
          type: "line",
          showSymbol: false,
          data: lead0 ? equity.filter((e) => e[3] !== null).map((e) => [e[0], (e[3] as number) / lead0 - 1]) : [],
          lineStyle: { width: 1.2, type: "dashed", color: text },
          itemStyle: { color: text },
          tooltip: { valueFormatter: (v: number) => pct.format(v) },
        },
        {
          name: labels.daily,
          type: "bar",
          barMaxWidth: 16,
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: daily.map((d) => ({ value: [`${d.day}T12:00:00`, d.pnl], itemStyle: { color: cssVar(d.pnl >= 0 ? "--up" : "--down") } })),
          tooltip: { valueFormatter: (v: number) => usd.format(v) },
        },
      ],
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [equity, daily, capital, labels, locale, theme]);

  return equity.length ? <div ref={ref} className="h-[380px] w-full" /> : <div className="py-16 text-center text-sm text-muted">{labels.empty}</div>;
}
