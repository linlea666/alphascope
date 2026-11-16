"use client";

import { useEffect, useRef, useState } from "react";
import { LineChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, MarkAreaComponent, MarkPointComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([LineChart, GridComponent, TooltipComponent, DataZoomComponent, MarkAreaComponent, MarkPointComponent, CanvasRenderer]);

type Candle = [string, number, number, number, number];
type Leg = { id: number; direction: "up" | "down"; start_ts: string; end_ts: string; start_px: number; end_px: number; change: number; confirmed: boolean };

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function SwingChart({ candles, legs, locale, label }: { candles: Candle[]; legs: Leg[]; locale: string; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [theme, setTheme] = useState(0);

  useEffect(() => {
    const obs = new MutationObserver(() => setTheme((n) => n + 1));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);

  useEffect(() => {
    if (!ref.current || !candles.length) return;
    const chart = echarts.init(ref.current);
    const text = cssVar("--muted");
    const border = cssVar("--border");
    const up = cssVar("--up");
    const down = cssVar("--down");
    const first = candles[0][0];
    const usd = new Intl.NumberFormat(locale, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
    const pct = new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 1, signDisplay: "exceptZero" });
    const visible = legs.filter((l) => l.end_ts >= first);
    chart.setOption({
      animation: false,
      grid: { left: 64, right: 24, top: 24, bottom: 56 },
      tooltip: { trigger: "axis", valueFormatter: (v: number) => usd.format(v), backgroundColor: cssVar("--panel"), borderColor: border, textStyle: { color: cssVar("--text") } },
      xAxis: { type: "time", axisLabel: { color: text }, axisLine: { lineStyle: { color: border } } },
      yAxis: { type: "value", scale: true, axisLabel: { color: text, formatter: (v: number) => usd.format(v) }, splitLine: { lineStyle: { color: border } } },
      dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8, textStyle: { color: text }, borderColor: border }],
      series: [
        {
          name: label,
          type: "line",
          showSymbol: false,
          data: candles.map((c) => [c[0], c[4]]),
          lineStyle: { width: 1.5, color: cssVar("--chart-1") },
          itemStyle: { color: cssVar("--chart-1") },
          markArea: {
            silent: true,
            data: visible.map((l) => [
              {
                xAxis: l.start_ts < first ? first : l.start_ts,
                itemStyle: { color: l.direction === "up" ? up : down, opacity: l.confirmed ? 0.1 : 0.05 },
                label: { show: true, position: "insideTop", color: text, fontSize: 11, formatter: `${pct.format(l.change)}${l.confirmed ? "" : " *"}` },
              },
              { xAxis: l.end_ts },
            ]),
          },
          markPoint: {
            symbol: "circle",
            symbolSize: 6,
            label: { show: true, position: "top", color: text, fontSize: 10, formatter: (p: { value: number }) => usd.format(p.value) },
            data: visible.flatMap((l) => [{ coord: [l.end_ts, l.end_px], value: l.end_px, itemStyle: { color: l.direction === "up" ? up : down } }]),
          },
        },
      ],
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [candles, legs, locale, label, theme]);

  return <div ref={ref} className="h-[360px] w-full" />;
}
