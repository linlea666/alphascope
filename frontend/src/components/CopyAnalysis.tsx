import { getLocale, getTranslations } from "next-intl/server";
import { HealthBadge } from "@/components/HealthBadge";
import { LinesChart } from "@/components/LinesChart";
import { Term } from "@/components/Term";
import { Badge, Panel, td, th } from "@/components/ui";
import { Ring } from "@/components/viz";
import { api, MAX_LEVERAGE, type Backtest, type Behavior, type Copyability, type ScorePart } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd, signClass } from "@/lib/format";

type Search = Record<string, string | undefined>;
const GRADE_TONE: Record<string, "up" | "accent" | "warn" | "down"> = { A: "up", B: "accent", C: "warn", D: "down" };
const DELAYS = [0, 30, 300, 1800, 7200];

/** Copyability score (0-100) with its parts, trader health and the nightly default copy backtests. */
export async function CopyabilityCard({ address }: { address: string }) {
  const t = await getTranslations("copyCard");
  const th2 = await getTranslations("health");
  const locale = await getLocale();
  let c: Copyability;
  try {
    c = await api.copyability(address);
  } catch {
    return null;
  }
  const sc = c.score;
  const pct = (v: unknown, d = 0) => (typeof v === "number" ? fmtPct(v, locale, d) : "—");
  const n = (v: unknown, d = 2) => (typeof v === "number" ? fmtNum(v, locale, d) : "—");
  const evidence = (p: ScorePart) => {
    switch (p.key) {
      case "skill":
        return t("ev.skill", { q: n(p.q, 3), verified: p.verified ? t("yes") : t("no") });
      case "copy_return":
        return t("ev.copy_return", { ret: pct(p.ret, 1), w: String(p.window) });
      case "drawdown":
        return t("ev.drawdown", { mdd: pct(p.mdd, 1), ratio: n(p.dd_ratio) });
      case "risk":
        return t("ev.risk", { liq: pct(p.liq_rate, 1), lev: n(p.median_leverage, 1) });
      case "latency":
        return p.kept === null ? t("ev.latencyNa") : t("ev.latency", { kept: pct(p.kept), r0: pct(p.r0, 1), r5: pct(p.r5, 1) });
      case "activity":
        return t("ev.activity", { n: n(p.n_closed, 0), days: n(p.days_since_last, 0) });
      default:
        return t("ev.concentration", { top: pct(p.top1_share) });
    }
  };
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <Term k="copyability">{t("title")}</Term>
          <HealthBadge status={c.health?.status} />
        </span>
      }
      hint={t("hint")}
    >
      {!sc ? (
        <p className="text-sm text-muted">{t("pending")}</p>
      ) : sc.status === "insufficient" ? (
        <p className="text-sm text-muted">{t("insufficient")}</p>
      ) : (
        <div className="grid gap-5 md:grid-cols-[auto_1fr]">
          <div className="flex flex-col items-center gap-1">
            <Ring share={(sc.score ?? 0) / 100} size={96} label={t("title")} />
            <Badge tone={GRADE_TONE[sc.grade ?? "D"]}>{t("grade", { g: sc.grade ?? "—" })}</Badge>
            <span className="text-[11px] text-muted">{fmtTime(sc.computed_at, locale, false)}</span>
          </div>
          <ul className="space-y-2">
            {sc.components.map((p) => (
              <li key={p.key}>
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span>{t(`parts.${p.key}`)}</span>
                  <span className="num text-muted">
                    {fmtNum(p.points, locale, 1)} / {p.max}
                  </span>
                </div>
                <div className="mt-0.5 h-1.5 rounded bg-panel-2">
                  <div className={`h-1.5 rounded ${p.points / p.max >= 0.7 ? "bg-up" : p.points / p.max >= 0.4 ? "bg-accent" : "bg-down/70"}`} style={{ width: `${(100 * p.points) / p.max}%` }} />
                </div>
                <div className="mt-0.5 text-[11px] text-muted">{evidence(p)}</div>
              </li>
            ))}
          </ul>
        </div>
      )}
      {c.health && c.health.signals.length > 0 && (
        <div className="mt-4 rounded border border-border/60 p-2 text-xs">
          <div className="text-muted">{th2("signalsTitle")}</div>
          <ul className="mt-1 space-y-0.5">
            {c.health.signals.map((s) => (
              <li key={s.key}>
                · {th2(`signals.${s.key}`)}: <span className="num">{n(s.recent)}</span> <span className="text-muted">vs</span> <span className="num">{n(s.prior)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {c.backtests.length > 0 && (
        <div className="-mx-4 -mb-4 mt-4 overflow-x-auto border-t border-border">
          <table className="w-full text-sm">
            <thead className="bg-panel-2">
              <tr>
                <th className={th}>{t("bt.window")}</th>
                <th className={`${th} text-right`}>{t("bt.ret")}</th>
                <th className={`${th} text-right`}>{t("bt.mdd")}</th>
                <th className={`${th} text-right`}>{t("bt.leader")}</th>
                <th className={`${th} text-right`}>{t("bt.trades")}</th>
                <th className={`${th} text-right`}>{t("bt.costs")}</th>
              </tr>
            </thead>
            <tbody>
              {c.backtests.map((b) => (
                <tr key={b.window_days} className="border-t border-border/60">
                  <td className={td}>{t("bt.days", { n: b.window_days })}{b.trades === 0 && <small className="block text-muted">{t("noExecutions")}</small>}</td>
                  <td className={`${td} num text-right ${signClass(b.ret)}`}>{b.trades ? fmtPct(b.ret, locale, 1) : "—"}</td>
                  <td className={`${td} num text-right text-down`}>{b.trades ? fmtPct(b.mdd, locale, 1) : "—"}</td>
                  <td className={`${td} num text-right ${signClass(b.leader_ret)}`}>{b.leader_ret === null ? "—" : fmtPct(b.leader_ret, locale, 1)}</td>
                  <td className={`${td} num text-right`}>
                    {b.trades}
                    {b.liquidations > 0 && <span className="ml-1 text-down">{t("bt.liq", { n: b.liquidations })}</span>}
                  </td>
                  <td className={`${td} num text-right text-muted`}>{fmtUsd(b.costs, locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!!c.replay_audit?.length && (
        <details className="mt-5 border-t border-border pt-3 text-xs">
          <summary className="cursor-pointer">{t("audit.title", { n: c.replay_audit.length })}</summary>
          <p className="mt-2 text-muted">{t("audit.vintage")}</p>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full">
              <thead><tr>
                <th className={th}>{t("audit.cutoff")}</th><th className={th}>{t("bt.window")}</th>
                <th className={th}>{t("audit.previous")}</th><th className={th}>{t("audit.replayed")}</th>
              </tr></thead>
              <tbody>{c.replay_audit.map(({ window_days, source_cutoff, version, outcome }) => (
                <tr className="border-t border-border/60" key={`${source_cutoff}-${window_days}-${version}`}>
                  <td className={td}><time dateTime={source_cutoff}>{fmtTime(source_cutoff, locale)}</time></td>
                  <td className={td}>{t("bt.days", { n: window_days })}</td>
                  <td className={td}>{outcome.previous_trades > 0 ? t("audit.fills", { n: outcome.previous_trades, ret: pct(outcome.previous_ret, 2) }) : t("noExecutions")}</td>
                  <td className={td}>{outcome.trades === undefined ? t("audit.unavailable") : outcome.trades > 0 ? t("audit.fills", { n: outcome.trades, ret: pct(outcome.ret, 2) }) : t("noExecutions")}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </details>
      )}
    </Panel>
  );
}

/** Run a historical copy backtest with your own settings (on demand: bt=1 in the URL). */
export async function BacktestCard({ address, search }: { address: string; search: Search }) {
  const t = await getTranslations("backtest");
  const locale = await getLocale();
  const days = Number(search.bd || 30);
  const ratio = Number(search.br || 100);
  const delay = Number(search.bdl ?? 30);
  const capital = Number(search.bc || 10000);
  const lev = search.bl === "leader" ? "leader" : "account";
  let r: Backtest | null = null;
  if (search.bt === "1") {
    try {
      r = await api.backtest(address, { days, ratio, delay, capital, leverage_mode: lev, max_leverage: lev === "leader" ? MAX_LEVERAGE : undefined });
    } catch {
      r = { ok: false, reason: "error" };
    }
  }
  const input = "rounded border border-border bg-panel-2 px-2 py-1 text-sm outline-none focus:border-accent";
  const keep = Object.entries(search).filter(([k, v]) => v !== undefined && !["bt", "bd", "br", "bdl", "bc", "bl"].includes(k));
  return (
    <section id="backtest" className="rounded-lg border border-border bg-panel">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-border px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold">{t("title")}</h2>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">{t("hint")}</p>
        </div>
        <form method="get" action="#backtest" className="flex flex-wrap items-end gap-2 text-xs">
          {keep.map(([k, v]) => (
            <input key={k} type="hidden" name={k} value={v} />
          ))}
          <input type="hidden" name="bt" value="1" />
          <label className="flex flex-col gap-1 text-muted">
            {t("days")}
            <select name="bd" defaultValue={String(days)} className={input}>
              {[7, 30, 90, 180].map((d) => (
                <option key={d} value={d}>
                  {t("daysN", { n: d })}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-muted">
            {t("ratio")}
            <input name="br" type="number" min={1} max={1000} defaultValue={ratio} className={`${input} w-20`} />
          </label>
          <label className="flex flex-col gap-1 text-muted">
            {t("delay")}
            <select name="bdl" defaultValue={String(delay)} className={input}>
              {DELAYS.map((d) => (
                <option key={d} value={d}>
                  {t(`delays.${d}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-muted">
            {t("lev")}
            <select name="bl" defaultValue={lev} className={input}>
              <option value="account">{t("levs.account")}</option>
              <option value="leader">{t("levs.leader")}</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-muted">
            {t("capital")}
            <input name="bc" type="number" min={100} step={100} defaultValue={capital} className={`${input} w-24`} />
          </label>
          <button type="submit" className="rounded bg-accent px-3 py-1 text-white">
            {t("run")}
          </button>
        </form>
      </div>
      <div className="p-4">
        {!r ? (
          <p className="py-4 text-center text-sm text-muted">{t("idle")}</p>
        ) : !r.ok ? (
          <p className="py-4 text-center text-sm text-muted">{t(`reasons.${r.reason ?? "error"}`)}</p>
        ) : r.trades === 0 ? (
          <p className="py-4 text-center text-sm text-muted">{t("noExecutions", { n: r.n_decisions ?? 0 })}</p>
        ) : (
          <>
            <div className="mb-3 flex flex-wrap items-baseline gap-x-6 gap-y-1">
              <span className={`num text-2xl font-semibold ${signClass(r.ret)}`}>{fmtUsd(r.final, locale, false)}</span>
              <span className={`num ${signClass(r.ret)}`}>{fmtPct(r.ret, locale, 1)}</span>
              <span className="num text-sm text-down">
                {t("mdd")} {fmtPct(r.mdd, locale, 1)}
              </span>
              <span className="text-sm text-muted">{t("tradesCosts", { n: r.trades ?? 0, costs: fmtUsd(r.costs, locale) })}</span>
              {(r.liquidations ?? 0) > 0 && <Badge tone="down">{t("liq", { n: r.liquidations ?? 0 })}</Badge>}
              {r.stopped === "liquidated" && <Badge tone="down">{t("blown")}</Badge>}
              <span className="text-sm">
                {t("leader")}: <span className={signClass(r.leader_ret)}>{r.leader_ret === null || r.leader_ret === undefined ? "—" : fmtPct(r.leader_ret, locale, 1)}</span>
                {r.retention !== null && r.retention !== undefined && <span className="ml-2 text-muted">{t("kept", { p: fmtPct(r.retention, locale, 0, false) })}</span>}
              </span>
            </div>
            <LinesChart lines={[{ name: t("series"), data: r.curve ?? [], area: true }]} formats={["usd"]} locale={locale} height={220} />
            {r.delay_curve && (
              <div className="mt-4">
                <h3 className="text-xs font-medium text-muted">
                  <Term k="delay">{t("delayTitle")}</Term>
                  {r.delay_curve_source === "nightly_30d" && <span className="ml-1">({t("nightly")})</span>}
                </h3>
                <div className="mt-2 grid grid-cols-5 gap-2 text-center text-xs">
                  {r.delay_curve.map(([d, v]) => (
                    <div key={d} className="rounded border border-border/60 p-2">
                      <div className="text-muted">{t(`delays.${d}`)}</div>
                      <div className={`num mt-1 text-sm font-medium ${signClass(v)}`}>{v === null ? "—" : fmtPct(v, locale, 1)}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <ul className="mt-3 space-y-0.5 text-[11px] leading-relaxed text-muted">
              <li>· {t(`equity.${r.equity_source ?? "history"}`)}</li>
              {(r.capped_share ?? 0) > 0.3 && <li className="text-warn">· {t("capped", { p: fmtPct(r.capped_share, locale, 0, false) })}</li>}
              <li>· {t("prices", { res: [...new Set(Object.values(r.resolution ?? {}))].join(" / ") || "—" })}</li>
              <li>· {t("notes")}</li>
            </ul>
          </>
        )}
      </div>
    </section>
  );
}

/** When the trader trades and how they handle wins and losses. */
export async function BehaviorPanel({ address }: { address: string }) {
  const t = await getTranslations("behavior");
  const locale = await getLocale();
  let b: Behavior;
  try {
    b = await api.behavior(address);
  } catch {
    return null;
  }
  if (b.closed < 5 && b.opens < 5) return null;
  const maxHeat = Math.max(1, ...b.heat.flat());
  const buckets = [...b.hold_buckets.map((h) => `<${h < 24 ? `${h}h` : `${h / 24}d`}`), `≥${b.hold_buckets[b.hold_buckets.length - 1] / 24}d`];
  const maxHold = Math.max(1, ...b.hold.win, ...b.hold.loss);
  const fmtFlag = (k: string, v: number) => (["holds_losers", "lets_winners_run", "fat_tail_losses", "cuts_losses", "size_up", "leverage_up"].includes(k) ? `${fmtNum(v, locale, 1)}×` : fmtPct(v, locale, 0, false));
  return (
    <Panel title={t("title")} hint={t("hint")}>
      {b.flags.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
          {b.flags.map((f) => (
            <span key={f.key} title={t(`flagHint.${f.key}`)} className={`rounded-full border px-2.5 py-1 text-xs ${f.good === true ? "border-up/40 text-up" : f.good === false ? "border-down/40 text-down" : "border-warn/40 text-warn"}`}>
              {f.good === true ? "👍" : f.good === false ? "⚠️" : "🔎"} {t(`flags.${f.key}`)} · <span className="num">{fmtFlag(f.key, f.value)}</span> <span className="text-muted">(n={f.n})</span>
            </span>
          ))}
        </div>
      )}
      <div className="grid gap-6 lg:grid-cols-2">
        <div>
          <h3 className="mb-2 text-xs font-medium text-muted">{t("heatTitle", { n: fmtNum(b.opens, locale) })}</h3>
          <div className="inline-grid gap-[2px]" style={{ gridTemplateColumns: `28px repeat(24, 12px)` }}>
            {b.heat.map((row, wd) => (
              <div key={wd} className="contents">
                <div className="pr-1 text-[10px] leading-[12px] text-muted">{t(`weekdays.${wd}`)}</div>
                {row.map((v, h) => (
                  <div key={h} title={`${t(`weekdays.${wd}`)} ${h}:00 UTC · ${v}`} className="h-3 w-3 rounded-[2px]" style={{ background: v ? `color-mix(in srgb, var(--accent) ${Math.round(15 + (v / maxHeat) * 85)}%, transparent)` : "var(--panel-2)" }} />
                ))}
              </div>
            ))}
          </div>
          <div className="mt-1 flex justify-between text-[10px] text-muted" style={{ marginLeft: 30, width: 24 * 14 }}>
            <span>0h</span>
            <span>12h</span>
            <span>23h UTC</span>
          </div>
        </div>
        <div>
          <h3 className="mb-2 text-xs font-medium text-muted">{t("holdTitle")}</h3>
          <div className="space-y-1">
            {buckets.map((label, i) => (
              <div key={label} className="grid grid-cols-[52px_1fr_1fr] items-center gap-2 text-[11px]">
                <span className="text-muted">{label}</span>
                <span className="flex justify-end">
                  <span className="h-2.5 rounded-l bg-down/70" style={{ width: `${(100 * b.hold.loss[i]) / maxHold}%` }} title={`${t("losses")} ${b.hold.loss[i]}`} />
                </span>
                <span className="h-2.5 rounded-r bg-up/70" style={{ width: `${(100 * b.hold.win[i]) / maxHold}%` }} title={`${t("wins")} ${b.hold.win[i]}`} />
              </div>
            ))}
          </div>
          <div className="mt-1 grid grid-cols-[52px_1fr_1fr] gap-2 text-[10px] text-muted">
            <span />
            <span className="text-right">← {t("losses")}</span>
            <span>{t("wins")} →</span>
          </div>
          <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
            <dt className="text-muted">{t("streaks")}</dt>
            <dd className="num">
              <span className="text-up">{b.win_streak}</span> / <span className="text-down">{b.loss_streak}</span>
            </dd>
            {b.revenge_share !== undefined && (
              <>
                <dt className="text-muted">{t("revenge")}</dt>
                <dd className="num">{fmtPct(b.revenge_share, locale, 0, false)}</dd>
              </>
            )}
            {b.avg_down_share !== undefined && (
              <>
                <dt className="text-muted">{t("avgDown")}</dt>
                <dd className="num">{fmtPct(b.avg_down_share, locale, 0, false)}</dd>
              </>
            )}
            {b.lev_recent !== undefined && (
              <>
                <dt className="text-muted">{t("lev")}</dt>
                <dd className="num">
                  {fmtNum(b.lev_before, locale, 1)}× → {fmtNum(b.lev_recent, locale, 1)}×
                </dd>
              </>
            )}
          </dl>
        </div>
      </div>
    </Panel>
  );
}
