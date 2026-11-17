"use client";

import { useEffect, useRef, useState } from "react";
import { BarChart, LineChart, TreemapChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useRouter } from "@/i18n/routing";
import type { LiqSide, MarketCoin, MarketHistoryRow, MarketLiquidations, PriceBin, PriceZone } from "@/lib/api";

echarts.use([TreemapChart, LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, MarkLineComponent, MarkAreaComponent, CanvasRenderer]);

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** `share` of colour a over b, both #rrggbb (canvas does not take CSS color-mix everywhere). */
function blend(a: string, b: string, share: number): string {
  const rgb = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [x, y] = [rgb(a), rgb(b)];
  return `rgb(${x.map((v, i) => Math.round(v * share + y[i] * (1 - share))).join(",")})`;
}

/** Re-render on theme switches (the colours come from CSS variables). */
function useTheme(): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    const obs = new MutationObserver(() => setN((x) => x + 1));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return n;
}

function useChart(build: (chart: echarts.ECharts) => void, deps: unknown[]) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    build(chart);
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, theme]);
  return ref;
}

/** Area = notional held, colour = share of traders long (red short-heavy -> green long-heavy). Click selects the coin. */
export function MarketTreemap({ rows, query, locale, labels }: { rows: MarketCoin[]; query: Record<string, string>; locale: string; labels: { longShare: string; traders: string; notional: string } }) {
  const router = useRouter();
  const ref = useChart(
    (chart) => {
      const usd = new Intl.NumberFormat(locale, { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });
      const up = cssVar("--up");
      const down = cssVar("--down");
      const panel = cssVar("--panel-2");
      const mix = (share: number) => (share >= 0.5 ? blend(up, panel, (share - 0.5) * 1.8 + 0.1) : blend(down, panel, (0.5 - share) * 1.8 + 0.1));
      chart.setOption({
        animation: false,
        tooltip: {
          backgroundColor: cssVar("--panel"),
          borderColor: cssVar("--border"),
          textStyle: { color: cssVar("--text") },
          formatter: (p: { data: { name: string; value: number; share: number; traders: number } }) =>
            `<b>${p.data.name}</b><br/>${labels.notional}: ${usd.format(p.data.value)}<br/>${labels.longShare}: ${Math.round(p.data.share * 100)}%<br/>${labels.traders}: ${p.data.traders}`,
        },
        series: [
          {
            type: "treemap",
            roam: false,
            nodeClick: false,
            breadcrumb: { show: false },
            width: "100%",
            height: "100%",
            label: { show: true, formatter: (p: { name: string; data: { share: number } }) => `${p.name}\n${Math.round(p.data.share * 100)}%`, color: cssVar("--text"), fontSize: 12 },
            itemStyle: { borderColor: cssVar("--bg"), borderWidth: 2, gapWidth: 2 },
            data: rows.map((r) => {
              const share = r.traders ? r.long_traders / r.traders : 0.5;
              return { name: r.coin, value: r.long_ntl + r.short_ntl, share, traders: r.traders, itemStyle: { color: mix(share) } };
            }),
          },
        ],
      });
      chart.on("click", (p) => router.push({ pathname: "/market", query: { ...query, coin: (p.data as { name: string }).name } }));
    },
    [rows, locale],
  );
  return <div ref={ref} className="h-[360px] w-full" />;
}

export function MarketHistoryChart({ rows, locale, labels }: { rows: MarketHistoryRow[]; locale: string; labels: { longs: string; shorts: string; net: string; empty: string } }) {
  const ref = useChart(
    (chart) => {
      const text = cssVar("--muted");
      const border = cssVar("--border");
      const usd = new Intl.NumberFormat(locale, { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });
      chart.setOption({
        animation: false,
        grid: { left: 48, right: 64, top: 32, bottom: 28 },
        legend: { top: 0, textStyle: { color: text } },
        tooltip: { trigger: "axis", backgroundColor: cssVar("--panel"), borderColor: border, textStyle: { color: cssVar("--text") } },
        xAxis: { type: "time", axisLabel: { color: text }, axisLine: { lineStyle: { color: border } } },
        yAxis: [
          { type: "value", axisLabel: { color: text }, splitLine: { lineStyle: { color: border } } },
          { type: "value", axisLabel: { color: text, formatter: (v: number) => usd.format(v) }, splitLine: { show: false } },
        ],
        series: [
          { name: labels.longs, type: "line", showSymbol: false, data: rows.map((r) => [r.bucket_ts, r.long_traders]), lineStyle: { color: cssVar("--up") }, itemStyle: { color: cssVar("--up") } },
          { name: labels.shorts, type: "line", showSymbol: false, data: rows.map((r) => [r.bucket_ts, r.short_traders]), lineStyle: { color: cssVar("--down") }, itemStyle: { color: cssVar("--down") } },
          { name: labels.net, type: "line", yAxisIndex: 1, showSymbol: false, data: rows.map((r) => [r.bucket_ts, r.long_ntl - r.short_ntl]), lineStyle: { color: cssVar("--accent"), type: "dashed" }, itemStyle: { color: cssVar("--accent") }, tooltip: { valueFormatter: (v: number) => usd.format(v) } },
        ],
      });
    },
    [rows, locale],
  );
  return rows.length > 1 ? <div ref={ref} className="h-[260px] w-full" /> : <p className="py-10 text-center text-sm text-muted">{labels.empty}</p>;
}

/** Venue-reported liquidation prices of the observed sample by price band, long below / short above the price. */
export function LiqHistogram({ data, locale, labels }: { data: MarketLiquidations; locale: string; labels: { longs: string; shorts: string; price: string; empty: string } }) {
  const ref = useChart(
    (chart) => {
      const text = cssVar("--muted");
      const border = cssVar("--border");
      const usd = new Intl.NumberFormat(locale, { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });
      const px = new Intl.NumberFormat(locale, { maximumFractionDigits: (data.mark_px ?? 0) >= 100 ? 0 : 4 });
      const bins = data.bins; // category axes grow upwards: the highest price ends up on top
      const cats = bins.map((b) => px.format((b.lo + b.hi) / 2));
      const markIdx = bins.findIndex((b) => data.mark_px !== null && b.lo <= data.mark_px && data.mark_px < b.hi);
      chart.setOption({
        animation: false,
        grid: { left: 72, right: 24, top: 28, bottom: 24 },
        legend: { top: 0, textStyle: { color: text } },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow" },
          backgroundColor: cssVar("--panel"),
          borderColor: border,
          textStyle: { color: cssVar("--text") },
          valueFormatter: (v: number) => usd.format(Math.abs(v)),
        },
        xAxis: { type: "value", axisLabel: { color: text, formatter: (v: number) => usd.format(Math.abs(v)) }, splitLine: { lineStyle: { color: border } } },
        yAxis: { type: "category", data: cats, axisLabel: { color: text, fontSize: 10 }, axisLine: { lineStyle: { color: border } } },
        series: [
          {
            name: labels.longs,
            type: "bar",
            stack: "x",
            data: bins.map((b) => -b.long_ntl),
            itemStyle: { color: cssVar("--up") },
            markLine: markIdx >= 0 ? { symbol: "none", label: { formatter: `${labels.price} ${px.format(data.mark_px ?? 0)}`, color: text }, lineStyle: { color: cssVar("--accent"), type: "dashed" }, data: [{ yAxis: markIdx }] } : undefined,
          },
          { name: labels.shorts, type: "bar", stack: "x", data: bins.map((b) => b.short_ntl), itemStyle: { color: cssVar("--down") } },
        ],
      });
    },
    [data, locale],
  );
  const placed = data.bins.some((b) => b.long_ntl || b.short_ntl);
  return placed ? <div ref={ref} className="h-[520px] w-full" /> : <p className="py-10 text-center text-sm text-muted">{labels.empty}</p>;
}

/** Notional by price band, longs to the left and shorts to the right, zones shaded, the reference price dashed.
 *  Used for both entry prices and venue-reported liquidation prices (each with its own price range). */
export function PriceLadder({ long, short, longZones, shortZones, refPx, locale, labels, height = 520 }: { long: PriceBin[]; short: PriceBin[]; longZones: PriceZone[]; shortZones: PriceZone[]; refPx: number; locale: string; labels: { longs: string; shorts: string; price: string; zone: string; empty: string }; height?: number }) {
  const ref = useChart(
    (chart) => {
      const text = cssVar("--muted");
      const border = cssVar("--border");
      const usd = new Intl.NumberFormat(locale, { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });
      const px = new Intl.NumberFormat(locale, { maximumFractionDigits: refPx >= 100 ? 0 : refPx >= 1 ? 3 : 6 });
      const cats = long.map((b) => px.format((b.lo + b.hi) / 2)); // category axes grow upwards: high prices on top
      const idx = (p: number) => Math.max(0, Math.min(long.length - 1, long.findIndex((b) => b.lo <= p && p < b.hi)));
      const refIdx = long.findIndex((b) => b.lo <= refPx && refPx < b.hi);
      const area = (zones: PriceZone[], color: string) =>
        zones.map((z) => [
          { yAxis: idx(z.lo), itemStyle: { color: blend(color, cssVar("--panel"), 0.18) }, label: { show: true, position: "insideLeft", color: text, fontSize: 10, formatter: `${labels.zone} ${Math.round(z.share * 100)}%` } },
          { yAxis: idx(z.hi - 1e-9) },
        ]);
      chart.setOption({
        animation: false,
        grid: { left: 72, right: 24, top: 28, bottom: 24 },
        legend: { top: 0, textStyle: { color: text } },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow" },
          backgroundColor: cssVar("--panel"),
          borderColor: border,
          textStyle: { color: cssVar("--text") },
          valueFormatter: (v: number) => usd.format(Math.abs(v)),
        },
        xAxis: { type: "value", axisLabel: { color: text, formatter: (v: number) => usd.format(Math.abs(v)) }, splitLine: { lineStyle: { color: border } } },
        yAxis: { type: "category", data: cats, axisLabel: { color: text, fontSize: 10 }, axisLine: { lineStyle: { color: border } } },
        series: [
          {
            name: labels.longs,
            type: "bar",
            stack: "x",
            data: long.map((b) => -b.notional),
            itemStyle: { color: cssVar("--up") },
            markArea: { silent: true, data: area(longZones, cssVar("--up")) },
            markLine: refIdx >= 0 ? { symbol: "none", label: { formatter: `${labels.price} ${px.format(refPx)}`, color: text }, lineStyle: { color: cssVar("--accent"), type: "dashed" }, data: [{ yAxis: refIdx }] } : undefined,
          },
          { name: labels.shorts, type: "bar", stack: "x", data: short.map((b) => b.notional), itemStyle: { color: cssVar("--down") }, markArea: { silent: true, data: area(shortZones, cssVar("--down")) } },
        ],
      });
    },
    [long, short, longZones, shortZones, refPx, locale],
  );
  const any = long.some((b) => b.notional) || short.some((b) => b.notional);
  return any ? <div ref={ref} className="w-full" style={{ height }} /> : <p className="py-10 text-center text-sm text-muted">{labels.empty}</p>;
}

/** Cumulative notional whose venue-reported liquidation price a move of x% would reach (longs down, shorts up). */
export function LiqLadder({ long, short, locale, labels }: { long: LiqSide["ladder"]; short: LiqSide["ladder"]; locale: string; labels: { longs: string; shorts: string; empty: string } }) {
  const ref = useChart(
    (chart) => {
      const text = cssVar("--muted");
      const border = cssVar("--border");
      const usd = new Intl.NumberFormat(locale, { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });
      const steps = [...[...long].reverse(), ...short];
      chart.setOption({
        animation: false,
        grid: { left: 64, right: 16, top: 16, bottom: 28 },
        tooltip: {
          trigger: "axis",
          backgroundColor: cssVar("--panel"),
          borderColor: border,
          textStyle: { color: cssVar("--text") },
          formatter: (ps: { dataIndex: number }[]) => {
            const s = steps[ps[0].dataIndex];
            return `${s.move > 0 ? "+" : ""}${s.move}%<br/>${s.move < 0 ? labels.longs : labels.shorts}: ${usd.format(s.notional)} · ${s.positions}`;
          },
        },
        xAxis: { type: "category", data: steps.map((s) => `${s.move > 0 ? "+" : ""}${s.move}%`), axisLabel: { color: text }, axisLine: { lineStyle: { color: border } } },
        yAxis: { type: "value", axisLabel: { color: text, formatter: (v: number) => usd.format(v) }, splitLine: { lineStyle: { color: border } } },
        series: [{ type: "bar", data: steps.map((s) => ({ value: s.notional, itemStyle: { color: cssVar(s.move < 0 ? "--up" : "--down") } })) }],
      });
    },
    [long, short, locale],
  );
  const any = [...long, ...short].some((s) => s.notional);
  return any ? <div ref={ref} className="h-[220px] w-full" /> : <p className="py-8 text-center text-sm text-muted">{labels.empty}</p>;
}
