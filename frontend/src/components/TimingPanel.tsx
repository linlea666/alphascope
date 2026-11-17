import { getLocale, getTranslations } from "next-intl/server";
import type { ScaleKey, TimingScore, WalletTiming } from "@/lib/api";
import { fmtNum, fmtPct } from "@/lib/format";
import { Badge, Panel, td, th } from "./ui";

const SCALES: ScaleKey[] = ["scalp", "short", "swing", "macro"];
const WINDOWS = ["7d", "30d", "90d", "all"] as const;

/** Diverging colour around 0.5 (random timing): green = bought lows / sold highs, red = the opposite. */
function cellColor(loc: number | null): string {
  if (loc === null) return "transparent";
  const d = Math.max(-1, Math.min(1, (loc - 0.5) * 2));
  return d >= 0 ? `color-mix(in srgb, var(--up) ${Math.round(15 + d * 75)}%, transparent)` : `color-mix(in srgb, var(--down) ${Math.round(15 - d * 75)}%, transparent)`;
}

export async function TimingPanel({ data }: { data: WalletTiming | null }) {
  const t = await getTranslations("timing");
  const tt = await getTranslations("trader");
  const locale = await getLocale();
  if (!data || data.scores.length === 0) {
    return (
      <Panel title={t("panelTitle")}>
        <p className="text-sm text-muted">{t("noTiming")}</p>
      </Panel>
    );
  }
  const cell = (scale: ScaleKey, w: string) => data.scores.find((s) => s.scale === scale && s.time_window === w && s.coin_group === "all");

  // calendar: last 90 days with any scored decision, 4 rows
  const days = Array.from(new Set(data.calendar.map((c) => c.day))).sort().slice(-90);
  const byKey = new Map(data.calendar.map((c) => [`${c.scale}|${c.day}`, c]));
  const pending = data.decision_status.pending ?? 0;

  return (
    <Panel title={t("panelTitle")} hint={`${t("panelHint")} ${t("retro")}`}>
      <div className="-mx-4 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className={th}>{tt("scale")}</th>
              {WINDOWS.map((w) => (
                <th key={w} className={`${th} text-center`}>
                  {t(`windows.${w}`)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {SCALES.map((s) => (
              <tr key={s} className="border-t border-border/60">
                <td className={`${td} text-xs`}>{tt(`scales.${s}`)}</td>
                {WINDOWS.map((w) => {
                  const c = cell(s, w);
                  if (!c || c.status === "invalid_window") return <td key={w} className={`${td} text-center text-xs text-muted/50`}>·</td>;
                  if (c.status === "insufficient")
                    return (
                      <td key={w} className={`${td} text-center text-xs text-muted`}>
                        {t("status.insufficient")} ({c.n_scored})
                      </td>
                    );
                  return (
                    <td key={w} className={`${td} text-center`} style={{ background: cellColor(c.location) }}>
                      <div className="num font-medium">{fmtNum(c.location, locale, 3)}</div>
                      <div className="num text-[11px] text-muted">
                        {t("edge")} {c.edge_shrunk === null ? "—" : fmtNum(c.edge_shrunk, locale, 3)} · n {c.n_scored}
                      </div>
                      {c.q_value !== null && c.q_value < 0.1 && <Badge tone="up">q {fmtNum(c.q_value, locale, 3)}</Badge>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <LuckChart data={data} />

      <h3 className="mt-5 text-xs font-medium text-muted">{t("calendarTitle")}</h3>
      <p className="mt-1 text-xs text-muted">{t("calendarHint")}</p>
      {days.length ? (
        <div className="mt-2 overflow-x-auto">
          <div className="inline-grid gap-[2px]" style={{ gridTemplateColumns: `90px repeat(${days.length}, 14px)` }}>
            {SCALES.map((s) => (
              <div key={s} className="contents">
                <div className="pr-2 text-[11px] leading-[14px] text-muted">{tt(`scales.${s}`)}</div>
                {days.map((day) => {
                  const c = byKey.get(`${s}|${day}`);
                  return (
                    <div
                      key={day}
                      title={c ? `${day} · ${tt(`scales.${s}`)} · n ${c.n} · ${fmtNum(c.location, locale, 2)} · ${c.hits}` : day}
                      className="h-[14px] w-[14px] rounded-[2px] border border-border/40 text-center text-[9px] leading-[13px]"
                      style={{ background: c ? cellColor(c.location) : "transparent" }}
                    >
                      {c && c.hits > 0 ? c.hits : ""}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
          <div className="mt-1 flex justify-between text-[10px] text-muted" style={{ marginLeft: 90 }}>
            <span>{days[0]}</span>
            <span>{days[days.length - 1]}</span>
          </div>
        </div>
      ) : null}
      {pending > 0 && <p className="mt-3 text-xs text-muted">{t("pending", { n: pending })}</p>}
    </Panel>
  );
}

/** "Luck or skill": where the trader's actual timing score sits inside the distribution of the same decisions
 *  shifted to random times (the permutation test behind p / q). Shown for the most significant scored cell. */
async function LuckChart({ data }: { data: WalletTiming }) {
  const t = await getTranslations("luck");
  const tt = await getTranslations("trader");
  const tw = await getTranslations("timing");
  const locale = await getLocale();
  const scored = data.scores.filter((s): s is TimingScore & { location: number; p_value: number } => s.status === "scored" && s.coin_group === "all" && s.location !== null && s.p_value !== null);
  if (!scored.length) return null;
  const best = [...scored].sort((a, b) => a.p_value - b.p_value || (b.n_scored ?? 0) - (a.n_scored ?? 0))[0];
  const better = Math.max(0, 1 - best.p_value);
  const q = best.null_quantiles;
  const W = 600;
  const H = 120;
  let chart = null;
  if (q && q.length === 21) {
    const lo = Math.min(q[0], best.location) - 0.03;
    const hi = Math.max(q[20], best.location) + 0.03;
    const x = (v: number) => 20 + ((v - lo) / (hi - lo)) * (W - 40);
    const dens = q.slice(0, 20).map((v, i) => 0.05 / Math.max(q[i + 1] - v, 1e-4));
    const top = Math.max(...dens);
    chart = (
      <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 w-full max-w-2xl" role="img" aria-label={t("title")}>
        {dens.map((d, i) => {
          const h = (Math.min(d, top) / top) * (H - 40);
          return <rect key={i} x={x(q[i])} y={H - 22 - h} width={Math.max(1, x(q[i + 1]) - x(q[i]) - 1)} height={h} fill="var(--muted)" fillOpacity={0.35} />;
        })}
        <line x1={20} x2={W - 20} y1={H - 22} y2={H - 22} stroke="var(--border)" />
        <line x1={x(best.location)} x2={x(best.location)} y1={8} y2={H - 22} stroke="var(--accent)" strokeWidth={3} />
        <text x={x(best.location)} y={8} dy={-1} textAnchor="middle" fontSize="11" fill="var(--accent)" dominantBaseline="hanging">
          {t("you")}
        </text>
        {[q[0], q[10], q[20]].map((v, i) => (
          <text key={i} x={x(v)} y={H - 6} textAnchor="middle" fontSize="10" fill="var(--muted)">
            {fmtNum(v, locale, 2)}
          </text>
        ))}
      </svg>
    );
  }
  return (
    <div className="mt-5 rounded-lg border border-border/60 bg-panel-2/50 p-3">
      <h3 className="text-xs font-medium text-muted">
        🎲 {t("title")} · {tt(`scales.${best.scale}`).split(" ")[0]} · {tw(`windows.${best.time_window}`)}
      </h3>
      <p className="mt-1 text-sm">
        {t("lead", { better: fmtPct(better, locale, 0, false), loc: fmtNum(best.location, locale, 3), p: fmtNum(best.p_value, locale, 3) })}
        {best.q_value !== null && best.q_value < 0.1 ? <span className="ml-1 text-up">{t("significant")}</span> : <span className="ml-1 text-muted">{t("notSignificant")}</span>}
      </p>
      {chart ?? <p className="mt-1 text-[11px] text-muted">{t("pending")}</p>}
      <p className="mt-1 text-[11px] leading-relaxed text-muted">{t("explain")}</p>
    </div>
  );
}
