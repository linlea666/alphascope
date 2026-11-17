import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
import { HealthBadge } from "@/components/HealthBadge";
import { Term } from "@/components/Term";
import { ApiErrorState } from "@/components/ApiErrorState";
import { PnlCalendar } from "@/components/PnlCalendar";
import { SimControls } from "@/components/SimControls";
import { SimForm } from "@/components/SimForm";
import { Badge, PageHeader, Panel, Stat, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { api, ApiError, type SimDashboard, type SimPeriod } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd, signClass, simStatusTone } from "@/lib/format";

const PERIODS: SimPeriod[] = ["1d", "7d", "30d", "all"];
const SORTS = ["return", "pnl", "mdd", "retention", "calmar"] as const;
type Row = SimDashboard["sims"][number];
// return per unit of drawdown: rewards making money without deep dips (a 0% drawdown counts as 1%)
const calmar = (r: Row) => (r.period.return ?? 0) / Math.max(Math.abs(r.period.max_dd), 0.01);

export default async function SimsPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const t = await getTranslations("sims");
  const locale = await getLocale();
  const period = (PERIODS as string[]).includes(sp.period ?? "") ? (sp.period as SimPeriod) : "7d";
  let d: SimDashboard;
  try {
    d = await api.simDashboard(period);
  } catch (e) {
    return <ApiErrorState status={e instanceof ApiError ? e.status : undefined} detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const pf = d.portfolio;
  const pill = (active: boolean) => `rounded border px-3 py-1 ${active ? "border-accent bg-accent text-white" : "border-border text-muted hover:text-text"}`;
  const names = new Map(d.sims.map((s) => [s.id, s.name]));
  const sort = (SORTS as readonly string[]).includes(sp.sort ?? "") ? (sp.sort as (typeof SORTS)[number]) : "return";
  const byLeader = sp.group === "leader";
  const key: Record<(typeof SORTS)[number], (r: Row) => number> = {
    return: (r) => r.period.return ?? -Infinity,
    pnl: (r) => r.period.pnl,
    mdd: (r) => r.period.max_dd,
    retention: (r) => r.period.retention ?? -Infinity,
    calmar,
  };
  const ranked = [...d.sims].sort((a, b) => key[sort](b) - key[sort](a));
  const live = d.sims.filter((r) => r.period.return !== null);
  const best = (f: (r: Row) => number) => (live.length ? live.reduce((a, b) => (f(b) > f(a) ? b : a)) : null);
  const champions = [
    { k: "topReturn", r: best((r) => r.period.return ?? -Infinity), v: (r: Row) => fmtPct(r.period.return, locale, 1) },
    { k: "steadiest", r: best(calmar), v: (r: Row) => `${fmtNum(calmar(r), locale, 1)}` },
    { k: "smallestDd", r: best((r) => r.period.max_dd), v: (r: Row) => fmtPct(r.period.max_dd, locale, 1) },
  ];
  // one row per leader: summed capital / PnL, the worst drawdown of their sims
  const leaders = Object.values(
    d.sims.reduce<Record<string, { leader: string; name: string | null; sims: number; capital: number; pnl: number; periodPnl: number; mdd: number; liq: number; health: Row["leader_health"] }>>((acc, r) => {
      const g = (acc[r.leader] ??= { leader: r.leader, name: r.leader_name, sims: 0, capital: 0, pnl: 0, periodPnl: 0, mdd: 0, liq: 0, health: r.leader_health });
      g.sims += 1;
      g.capital += r.capital;
      g.pnl += r.pnl;
      g.periodPnl += r.period.pnl;
      g.mdd = Math.min(g.mdd, r.period.max_dd);
      g.liq += r.period.liquidations ?? 0;
      return acc;
    }, {}),
  ).sort((a, b) => b.periodPnl - a.periodPnl);
  const medal = (i: number) => (sort !== "mdd" && i < 3 ? ["🥇", "🥈", "🥉"][i] : "");
  const q = (o: Record<string, string>) => ({ period, sort, ...(byLeader ? { group: "leader" } : {}), ...o });
  const corrTone = (v: number | null) =>
    v === null ? "transparent" : `color-mix(in srgb, var(${v >= 0 ? "--warn" : "--accent"}) ${Math.round(Math.abs(v) * 70)}%, var(--panel-2))`;

  return (
    <div className="space-y-4">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <details className="rounded-lg border border-border bg-panel" open={sp.new === "1" || d.sims.length === 0}>
        <summary className="cursor-pointer px-4 py-3 text-sm font-semibold">{t("new")}</summary>
        <div className="border-t border-border p-4">
          <SimForm leader={sp.leader ?? ""} />
        </div>
      </details>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        {PERIODS.map((p) => (
          <Link scroll={false} key={p} href={{ pathname: "/sims", query: q({ period: p }) }} className={pill(p === period)}>
            {t(`periods.${p}`)}
          </Link>
        ))}
        <span className="text-xs text-muted">{t("tz", { tz: d.timezone })}</span>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <Stat label={t("stats.active")} value={`${pf.active} / ${pf.count}`} />
        <Stat label={t("stats.capital")} value={fmtUsd(pf.capital, locale)} />
        <Stat label={t("stats.equity")} value={fmtUsd(pf.equity, locale)} sub={<span className={signClass(pf.pnl)}>{fmtUsd(pf.pnl, locale)} {pf.capital ? fmtPct(pf.pnl / pf.capital, locale) : ""}</span>} />
        <Stat label={t("stats.periodPnl", { p: t(`periods.${period}`) })} value={<span className={signClass(pf.period_pnl)}>{fmtUsd(pf.period_pnl, locale)}</span>} />
        <Stat label={t("stats.trades")} value={fmtNum(pf.period_trades, locale)} />
        <Stat
          label={t("stats.costs")}
          value={fmtUsd(pf.period_fees + pf.period_slippage + pf.period_funding, locale)}
          sub={t("stats.costsSub", { fee: fmtUsd(pf.period_fees, locale), slip: fmtUsd(pf.period_slippage, locale), fund: fmtUsd(pf.period_funding, locale) })}
        />
      </div>

      {live.length > 1 && (
        <div className="grid gap-3 md:grid-cols-3">
          {champions.map(({ k, r, v }) =>
            r ? (
              <Link key={k} href={`/sims/${r.id}`} className="group rounded-lg border border-warn/30 bg-panel p-4 transition hover:-translate-y-0.5 hover:border-warn">
                <div className="text-xs text-muted">
                  {t(`champ.${k}`)} · {t(`periods.${period}`)}
                </div>
                <div className="mt-1 flex items-baseline justify-between gap-2">
                  <span className="truncate font-medium group-hover:text-accent">🏆 {r.name}</span>
                  <span className="num text-lg font-semibold">{v(r)}</span>
                </div>
                <div className="mt-1 truncate text-xs text-muted">{r.leader_name ?? r.leader}</div>
              </Link>
            ) : null,
          )}
        </div>
      )}

      <Panel
        title={t("listTitle")}
        hint={t("listHint")}
        actions={
          <div className="flex flex-wrap items-center gap-1 text-xs">
            <span className="text-muted">{t("sortBy")}:</span>
            {SORTS.map((k) => (
              <Link scroll={false} key={k} href={{ pathname: "/sims", query: q({ sort: k }) }} className={`rounded border px-2 py-0.5 ${k === sort ? "border-accent text-accent" : "border-border text-muted hover:text-text"}`}>
                {t(`sorts.${k}`)}
              </Link>
            ))}
            <Link scroll={false} href={{ pathname: "/sims", query: byLeader ? { period, sort } : { period, sort, group: "leader" } }} className={`ml-2 rounded border px-2 py-0.5 ${byLeader ? "border-accent text-accent" : "border-border text-muted hover:text-text"}`}>
              {t("groupByLeader")}
            </Link>
          </div>
        }
      >
        {byLeader && d.sims.length ? (
          <div className="-mx-4 -my-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-panel-2">
                <tr>
                  <th className={th}>{t("cols.leader")}</th>
                  <th className={`${th} text-right`}>{t("cols.sims")}</th>
                  <th className={`${th} text-right`}>{t("stats.capital")}</th>
                  <th className={`${th} text-right`}>{t("cols.total")}</th>
                  <th className={`${th} text-right`}>{t("cols.period")}</th>
                  <th className={`${th} text-right`}>{t("cols.mdd")}</th>
                  <th className={`${th} text-right`}>{t("cols.liq")}</th>
                </tr>
              </thead>
              <tbody>
                {leaders.map((g, i) => (
                  <tr key={g.leader} className="border-t border-border/60">
                    <td className={td}>
                      <span className="mr-1">{i < 3 ? ["🥇", "🥈", "🥉"][i] : ""}</span>
                      <AddressLink address={g.leader} name={g.name} /> <HealthBadge status={g.health} compact />
                    </td>
                    <td className={`${td} num text-right`}>{g.sims}</td>
                    <td className={`${td} num text-right`}>{fmtUsd(g.capital, locale)}</td>
                    <td className={`${td} num text-right ${signClass(g.pnl)}`}>{fmtUsd(g.pnl, locale)}</td>
                    <td className={`${td} num text-right ${signClass(g.periodPnl)}`}>{fmtUsd(g.periodPnl, locale)}</td>
                    <td className={`${td} num text-right ${signClass(g.mdd)}`}>{fmtPct(g.mdd, locale)}</td>
                    <td className={`${td} num text-right ${g.liq ? "text-down" : "text-muted"}`}>{g.liq}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : d.sims.length ? (
          <div className="-mx-4 -my-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-panel-2">
                <tr>
                  <th className={th}>{t("cols.sim")}</th>
                  <th className={th}>{t("cols.leader")}</th>
                  <th className={`${th} text-right`}>{t("cols.equity")}</th>
                  <th className={`${th} text-right`}>{t("cols.total")}</th>
                  <th className={`${th} text-right`}>{t("cols.period")}</th>
                  <th className={`${th} text-right`} title={t("cols.leaderHint")}>{t("cols.leaderRet")}</th>
                  <th className={`${th} text-right`} title={t("cols.retentionHint")}>{t("cols.retention")}</th>
                  <th className={`${th} text-right`}>
                    <Term k="mdd" align="right">{t("cols.mdd")}</Term>
                  </th>
                  <th className={`${th} text-right`}>{t("cols.winRate")}</th>
                  <th className={`${th} text-right`}>{t("cols.costs")}</th>
                  <th className={`${th} text-right`}>{t("cols.exposure")}</th>
                  <th className={th}>{t("cols.status")}</th>
                </tr>
              </thead>
              <tbody>
                {ranked.map((s, i) => (
                  <tr key={s.id} className="border-t border-border/60 align-top hover:bg-panel-2">
                    <td className={td}>
                      <span className="mr-1">{medal(i)}</span>
                      <Link href={`/sims/${s.id}`} className="font-medium hover:text-accent">
                        {s.name}
                      </Link>
                      <div className="text-xs text-muted">
                        {fmtUsd(s.capital, locale)} · {s.sizing === "equity_ratio" ? t("ratioShort", { r: fmtNum(s.size_factor * 100, locale) }) : `${t(`form.sizings.${s.sizing}`)} ×${fmtNum(s.size_factor, locale, 2)}`} · {t(`form.leverageModes.${s.leverage_mode ?? "account"}`)} · {fmtTime(s.created_at, locale, false)}
                      </div>
                    </td>
                    <td className={td}>
                      <AddressLink address={s.leader} name={s.leader_name} />
                      <div className="mt-1">
                        <HealthBadge status={s.leader_health} compact />
                      </div>
                    </td>
                    <td className={`${td} num text-right`}>{fmtUsd(s.equity, locale)}</td>
                    <td className={`${td} num text-right ${signClass(s.pnl)}`}>
                      {fmtUsd(s.pnl, locale)}
                      <div className="text-xs">{fmtPct(s.return, locale)}</div>
                    </td>
                    <td className={`${td} num text-right ${signClass(s.period.pnl)}`}>
                      {fmtUsd(s.period.pnl, locale)}
                      <div className="text-xs">{fmtPct(s.period.return, locale)}</div>
                    </td>
                    <td className={`${td} num text-right ${signClass(s.period.leader_return)}`}>{fmtPct(s.period.leader_return, locale)}</td>
                    <td className={`${td} num text-right`}>{s.period.retention === null ? "—" : fmtPct(s.period.retention, locale, 0, false)}</td>
                    <td className={`${td} num text-right ${signClass(s.period.max_dd)}`}>
                      {fmtPct(s.period.max_dd, locale)}
                      <div className="text-xs text-muted">{t("ddDays", { n: fmtNum(s.period.max_dd_days, locale, 1) })}</div>
                      {(s.period.liquidations ?? 0) > 0 && <Badge tone="down">{t("liqN", { n: s.period.liquidations })}</Badge>}
                    </td>
                    <td className={`${td} num text-right`}>
                      {fmtPct(s.period.win_rate, locale, 0, false)}
                      <div className="text-xs text-muted">{t("closedN", { n: s.period.closed })}</div>
                    </td>
                    <td className={`${td} num text-right text-muted`}>{fmtUsd(s.period.fees + s.period.slippage + s.period.funding, locale)}</td>
                    <td className={`${td} num text-right`}>
                      {s.gross_exposure !== null && s.equity > 0 ? `${fmtNum(s.gross_exposure / s.equity, locale, 1)}×` : "—"}
                      <div className="text-xs text-muted">{t("positionsN", { n: s.open_positions })}</div>
                    </td>
                    <td className={td}>
                      <div className="mb-1">
                        <Badge tone={simStatusTone(s.status)}>{t(`status.${s.status}`)}</Badge>
                        {s.stop_reason && <span className="ml-1 text-xs text-muted">{t(`reason.${s.stop_reason}`)}</span>}
                      </div>
                      <SimControls id={s.id} status={s.status} stopPending={s.stop_pending} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-10 text-center text-sm text-muted">{t("empty")}</p>
        )}
      </Panel>

      <Panel title={t("calendarTitle")} hint={t("calendarHint")}>
        <PnlCalendar days={d.calendar} locale={locale} labels={{ week: t("weekOf"), weekdays: t.raw("weekdays") as string[], empty: t("noDaily") }} />
      </Panel>

      {d.correlation.ids.length >= 2 && (
        <Panel title={t("corrTitle")} hint={t("corrHint", { n: d.correlation.min_days })}>
          <div className="overflow-x-auto">
            <table className="text-xs">
              <thead>
                <tr>
                  <th />
                  {d.correlation.ids.map((id) => (
                    <th key={id} className="max-w-24 truncate px-2 py-1 font-medium text-muted">
                      {names.get(id)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.correlation.ids.map((a, i) => (
                  <tr key={a}>
                    <th className="max-w-32 truncate px-2 py-1 text-left font-medium text-muted">{names.get(a)}</th>
                    {d.correlation.matrix[i].map((v, j) => (
                      <td key={j} className="num px-2 py-1 text-center" style={{ background: i === j ? "transparent" : corrTone(v) }}>
                        {i === j ? "" : v === null ? "—" : fmtNum(v, locale, 2)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  );
}
