"use client";

import { useEffect, useRef, useState } from "react";
import { CandlestickChart, ScatterChart } from "echarts/charts";
import { BrushComponent, DataZoomComponent, GridComponent, MarkAreaComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useRouter } from "@/i18n/routing";

echarts.use([CandlestickChart, ScatterChart, GridComponent, TooltipComponent, DataZoomComponent, BrushComponent, MarkAreaComponent, CanvasRenderer]);

type Zone = { side: "long" | "short"; px_min: number; px_max: number; start: string; end: string };
type Point = { t: string; px: number; notional: number; label: string; leg: 1 | 2 };
type Labels = { first: string; second: string; drawHint: string; mode: string; long: string; short: string };

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

const day = (ms: number) => new Date(ms).toISOString().slice(0, 10);
// round a price to 2-3 significant digits so the generated query stays readable
const roundPx = (v: number) => {
  const step = 10 ** Math.max(0, Math.floor(Math.log10(Math.max(v, 1))) - 2);
  return Math.round(v / step) * step;
};

/** Daily K-line where dragging a box (time × price) fills the zone query. The current zones are shaded and the
 *  matching traders' entries are plotted on top (bubble size = notional). */
export function ZoneBrushChart({ candles, coin, first, second, points, labels, locale }: {
  candles: [string, number, number, number, number][];
  coin: string;
  first: Zone;
  second: Zone | null;
  points: Point[];
  labels: Labels;
  locale: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const [target, setTarget] = useState<1 | 2>(1);
  const [side, setSide] = useState<"long" | "short">(first.side);
  const [theme, setTheme] = useState(0);
  useEffect(() => {
    const obs = new MutationObserver(() => setTheme((n) => n + 1));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  useEffect(() => setSide(target === 1 ? first.side : (second?.side ?? (first.side === "long" ? "short" : "long"))), [target, first.side, second?.side]);

  useEffect(() => {
    if (!ref.current || !candles.length) return;
    const chart = echarts.init(ref.current);
    const up = cssVar("--up");
    const down = cssVar("--down");
    const text = cssVar("--muted");
    const border = cssVar("--border");
    const num = new Intl.NumberFormat(locale, { maximumSignificantDigits: 6 });
    const usd = new Intl.NumberFormat(locale, { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });
    const area = (z: Zone, name: string) => {
      const c = z.side === "long" ? up : down;
      return [{ name, xAxis: z.start, yAxis: z.px_min, itemStyle: { color: c, opacity: 0.12, borderColor: c, borderWidth: 1, borderType: "dashed" }, label: { color: c, position: "insideTopLeft", fontSize: 11 } }, { xAxis: `${z.end}T23:59:59Z`, yAxis: z.px_max }];
    };
    const maxN = Math.max(1, ...points.map((p) => p.notional));
    chart.setOption({
      animation: false,
      grid: { left: 64, right: 16, top: 12, bottom: 52 },
      brush: { xAxisIndex: 0, yAxisIndex: 0, brushType: "rect", brushMode: "single", transformable: false, throttleType: "debounce", throttleDelay: 300, brushStyle: { color: "rgba(108,156,255,0.12)", borderColor: cssVar("--accent") }, outOfBrush: { colorAlpha: 1 } },
      tooltip: {
        trigger: "item",
        backgroundColor: cssVar("--panel"),
        borderColor: border,
        textStyle: { color: cssVar("--text"), fontSize: 12 },
        formatter: (p: { data: { p?: Point } }) => (p.data && p.data.p ? `${p.data.p.label}<br/>${p.data.p.t.slice(0, 16).replace("T", " ")} UTC<br/>@ ${num.format(p.data.p.px)} · ${usd.format(p.data.p.notional)}` : ""),
      },
      xAxis: { type: "time", axisLabel: { color: text }, axisLine: { lineStyle: { color: border } } },
      yAxis: { type: "value", scale: true, axisLabel: { color: text, formatter: (v: number) => num.format(v) }, splitLine: { lineStyle: { color: border } } },
      dataZoom: [{ type: "slider", height: 16, bottom: 8, textStyle: { color: text }, borderColor: border }],
      series: [
        {
          name: coin,
          type: "candlestick",
          data: candles.map((c) => [c[0], c[1], c[4], c[3], c[2]]),
          itemStyle: { color: up, color0: down, borderColor: up, borderColor0: down },
          tooltip: { show: false },
          markArea: { silent: true, data: [area(first, labels.first), ...(second ? [area(second, labels.second)] : [])] },
        },
        ...([1, 2] as const).map((leg) => ({
          name: leg === 1 ? labels.first : labels.second,
          type: "scatter",
          data: points.filter((p) => p.leg === leg).map((p) => ({ value: [p.t, p.px], p })),
          symbolSize: (_: unknown, params: { data: { p: Point } }) => 6 + 18 * Math.sqrt(params.data.p.notional / maxN),
          itemStyle: { color: (leg === 1 ? first : second)?.side === "short" ? down : up, opacity: 0.75, borderColor: cssVar("--bg"), borderWidth: 1 },
        })),
      ],
    });
    chart.dispatchAction({ type: "takeGlobalCursor", key: "brush", brushOption: { brushType: "rect", brushMode: "single" } });
    chart.on("brushEnd", (e: unknown) => {
      const areas = (e as { areas?: { coordRange?: [[number, number], [number, number]] }[] }).areas;
      const r = areas?.[0]?.coordRange;
      if (!r) return;
      const [[x0, x1], [y0, y1]] = r;
      const z = { side, px_min: String(roundPx(Math.min(y0, y1))), px_max: String(roundPx(Math.max(y0, y1))), start: day(Math.min(x0, x1)), end: day(Math.max(x0, x1)) };
      const q: Record<string, string> = target === 1
        ? { coin, side: z.side, px_min: z.px_min, px_max: z.px_max, start: z.start, end: z.end }
        : { coin, side: first.side, px_min: String(first.px_min), px_max: String(first.px_max), start: first.start, end: first.end };
      const keep = target === 1 ? second : null;
      if (target === 2) Object.assign(q, { also_side: z.side, also_px_min: z.px_min, also_px_max: z.px_max, also_start: z.start, also_end: z.end });
      else if (keep) Object.assign(q, { also_side: keep.side, also_px_min: String(keep.px_min), also_px_max: String(keep.px_max), also_start: keep.start, also_end: keep.end });
      router.push({ pathname: "/zones", query: q });
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [candles, coin, first, second, points, labels, locale, theme, target, side, router]);

  const pill = (active: boolean) => `rounded border px-2 py-0.5 ${active ? "border-accent text-accent" : "border-border text-muted hover:text-text"}`;
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted">{labels.mode}:</span>
        <button type="button" onClick={() => setTarget(1)} className={pill(target === 1)}>
          {labels.first}
        </button>
        <button type="button" onClick={() => setTarget(2)} className={pill(target === 2)}>
          {labels.second}
        </button>
        <span className="ml-2 text-muted">·</span>
        <button type="button" onClick={() => setSide("long")} className={pill(side === "long")}>
          <span className="text-up">●</span> {labels.long}
        </button>
        <button type="button" onClick={() => setSide("short")} className={pill(side === "short")}>
          <span className="text-down">●</span> {labels.short}
        </button>
        <span className="text-muted">{labels.drawHint}</span>
      </div>
      <div ref={ref} className="h-[360px] w-full" />
    </div>
  );
}
