import { getLocale, getTranslations } from "next-intl/server";
import type { WalletPositions } from "@/lib/api";
import { fmtNum, fmtUsd } from "@/lib/format";

const W = 1000;
const ROW = 22;
const LEFT = 70;

/** Recent position cycles on a time axis, one lane per coin: bar length = holding time, green long / red short,
 *  stronger colour = larger result. Click a bar to replay it on the K-line. */
export async function CycleTimeline({ data, search }: { data: WalletPositions | null; search: Record<string, string | undefined> }) {
  const t = await getTranslations("timeline");
  const locale = await getLocale();
  const cycles = data?.cycles ?? [];
  if (cycles.length < 3) return null;
  const counts = new Map<string, number>();
  cycles.forEach((c) => counts.set(c.coin, (counts.get(c.coin) ?? 0) + 1));
  const lanes = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6).map(([c]) => c);
  const shown = cycles.filter((c) => lanes.includes(c.coin));
  const now = Date.now();
  const t0 = Math.min(...shown.map((c) => new Date(c.opened_at).getTime()));
  const t1 = Math.max(...shown.map((c) => (c.closed_at ? new Date(c.closed_at).getTime() : now)));
  const x = (ms: number) => LEFT + ((ms - t0) / Math.max(t1 - t0, 1)) * (W - LEFT - 10);
  const maxAbs = Math.max(1, ...shown.map((c) => Math.abs(c.net_pnl ?? 0)));
  const height = lanes.length * ROW + 26;
  const ticks = Array.from({ length: 5 }, (_, i) => t0 + ((t1 - t0) * i) / 4);
  const href = (c: (typeof shown)[number]) => {
    const q = new URLSearchParams(Object.entries({ ...search, rc: c.coin, ri: (c.holding_hours ?? 0) > 24 * 5 ? "1d" : "1h", re: c.closed_at ?? undefined }).filter((e): e is [string, string] => !!e[1]));
    return `?${q.toString()}#replay`;
  };
  return (
    <div>
      <h3 className="mb-1 text-xs font-medium text-muted">{t("title")}</h3>
      <p className="mb-2 text-[11px] text-muted">{t("hint")}</p>
      <svg viewBox={`0 0 ${W} ${height}`} className="w-full" role="img" aria-label={t("title")}>
        {lanes.map((coin, i) => (
          <g key={coin}>
            <line x1={LEFT} x2={W - 10} y1={i * ROW + ROW / 2} y2={i * ROW + ROW / 2} stroke="var(--border)" strokeDasharray="2 4" />
            <text x={LEFT - 8} y={i * ROW + ROW / 2 + 4} textAnchor="end" fontSize="11" fill="var(--muted)">
              {coin}
            </text>
          </g>
        ))}
        {shown.map((c) => {
          const lane = lanes.indexOf(c.coin);
          const a = x(new Date(c.opened_at).getTime());
          const b = x(c.closed_at ? new Date(c.closed_at).getTime() : now);
          const strength = 0.35 + 0.65 * Math.min(1, Math.abs(c.net_pnl ?? 0) / maxAbs);
          const color = c.direction === "long" ? "var(--up)" : "var(--down)";
          return (
            <a key={`${c.coin}-${c.opened_at}`} href={href(c)}>
              <title>{`${c.coin} ${c.direction} · ${c.opened_at.slice(0, 16).replace("T", " ")} → ${c.closed_at ? c.closed_at.slice(0, 16).replace("T", " ") : "…"} · ${fmtUsd(c.net_pnl, locale)}`}</title>
              <rect x={a} y={lane * ROW + 5} width={Math.max(3, b - a)} height={ROW - 10} rx={3} fill={color} fillOpacity={strength} stroke={c.closed_at ? "none" : color} strokeDasharray="3 2" />
            </a>
          );
        })}
        {ticks.map((ms) => (
          <text key={ms} x={x(ms)} y={height - 6} textAnchor="middle" fontSize="10" fill="var(--muted)">
            {new Date(ms).toISOString().slice(5, 10)}
          </text>
        ))}
      </svg>
      <p className="mt-1 text-[11px] text-muted">{t("legend", { n: fmtNum(shown.length, locale) })}</p>
    </div>
  );
}
