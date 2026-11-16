"use client";

import { useEffect, useRef, useState } from "react";
import { CandlestickChart, ScatterChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import type { Replay } from "@/lib/api";

echarts.use([CandlestickChart, ScatterChart, GridComponent, TooltipComponent, DataZoomComponent, CanvasRenderer]);

type Labels = { buy: string; sell: string; top: string; bottom: string };

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function ReplayChart({ data, labels, locale }: { data: Replay; labels: Labels; locale: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [theme, setTheme] = useState(0);

  useEffect(() => {
    const obs = new MutationObserver(() => setTheme((n) => n + 1));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);

  useEffect(() => {
    if (!ref.current || !data.candles.length) return;
    const chart = echarts.init(ref.current);
    const up = cssVar("--up");
    const down = cssVar("--down");
    const text = cssVar("--muted");
    const border = cssVar("--border");
    const accent = cssVar("--accent");
    const num = new Intl.NumberFormat(locale, { maximumSignificantDigits: 6 });
    const usd = new Intl.NumberFormat(locale, { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });
    const t0 = data.candles[0][0];
    const t1 = data.candles[data.candles.length - 1][0];
    const inRange = (ts: string) => ts >= t0 && ts <= t1;
    const marker = (side: "buy" | "sell") =>
      data.decisions
        .filter((d) => d.side === side)
        .map((d) => ({ value: [d.started_at, d.vwap], d }));
    chart.setOption({
      animation: false,
      grid: { left: 64, right: 20, top: 16, bottom: 56 },
      tooltip: {
        trigger: "item",
        backgroundColor: cssVar("--panel"),
        borderColor: border,
        textStyle: { color: cssVar("--text"), fontSize: 12 },
        formatter: (p: { seriesName: string; data: { d?: Replay["decisions"][number]; value: [string, number] } | number[] }) => {
          const item = p.data as { d?: Replay["decisions"][number]; value: [string, number] };
          if (item && item.d) {
            const d = item.d;
            const loc = d.location === null ? "—" : num.format(d.location);
            return `${p.seriesName} · ${d.kind}<br/>${d.started_at.replace("T", " ").slice(0, 16)} UTC<br/>@ ${num.format(d.vwap)} · ${usd.format(d.notional)}<br/>location ${loc}${d.pivot_ts ? " · pivot ✓" : ""}`;
          }
          if (item && Array.isArray(item.value)) return `${p.seriesName}<br/>${String(item.value[0]).replace("T", " ").slice(0, 16)} UTC<br/>${num.format(item.value[1])}`;
          return "";
        },
      },
      xAxis: { type: "time", axisLabel: { color: text }, axisLine: { lineStyle: { color: border } } },
      yAxis: { type: "value", scale: true, axisLabel: { color: text, formatter: (v: number) => num.format(v) }, splitLine: { lineStyle: { color: border } } },
      dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8, textStyle: { color: text }, borderColor: border }],
      series: [
        {
          name: "K",
          type: "candlestick",
          data: data.candles.map((c) => [c[0], c[1], c[4], c[3], c[2]]),
          itemStyle: { color: up, color0: down, borderColor: up, borderColor0: down },
          encode: { x: 0, y: [1, 2, 3, 4] },
          tooltip: { show: false },
        },
        {
          name: labels.bottom,
          type: "scatter",
          symbol: "circle",
          symbolSize: 16,
          itemStyle: { color: "transparent", borderColor: up, borderType: "dashed", borderWidth: 1.5 },
          data: data.pivots.filter((p) => p.kind === "L" && inRange(p.ts)).map((p) => ({ value: [p.ts, p.px] })),
        },
        {
          name: labels.top,
          type: "scatter",
          symbol: "circle",
          symbolSize: 16,
          itemStyle: { color: "transparent", borderColor: down, borderType: "dashed", borderWidth: 1.5 },
          data: data.pivots.filter((p) => p.kind === "H" && inRange(p.ts)).map((p) => ({ value: [p.ts, p.px] })),
        },
        {
          name: labels.buy,
          type: "scatter",
          symbol: "pin",
          symbolSize: 26,
          symbolRotate: 180,
          symbolOffset: [0, 14],
          itemStyle: { color: up },
          label: { show: true, formatter: "B", color: "#fff", fontSize: 10, fontWeight: "bold", offset: [0, 2] },
          data: marker("buy"),
        },
        {
          name: labels.sell,
          type: "scatter",
          symbol: "pin",
          symbolSize: 26,
          symbolOffset: [0, -14],
          itemStyle: { color: down },
          label: { show: true, formatter: "S", color: "#fff", fontSize: 10, fontWeight: "bold", offset: [0, -2] },
          data: marker("sell"),
        },
      ],
      color: [accent],
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [data, labels, locale, theme]);

  return <div ref={ref} className="h-[440px] w-full" />;
}
