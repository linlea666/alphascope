import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
import { ApiErrorState } from "@/components/ApiErrorState";
import { PnlCalendar } from "@/components/PnlCalendar";
import { SimControls } from "@/components/SimControls";
import { SimForm } from "@/components/SimForm";
import { Badge, PageHeader, Panel, Stat, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { api, ApiError, type SimDashboard, type SimPeriod } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd, signClass, simStatusTone } from "@/lib/format";

const PERIODS: SimPeriod[] = ["1d", "7d", "30d", "all"];

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
          <Link key={p} href={{ pathname: "/sims", query: { period: p } }} className={pill(p === period)}>
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

      <Panel title={t("listTitle")} hint={t("listHint")}>
        {d.sims.length ? (
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
                  <th className={`${th} text-right`}>{t("cols.mdd")}</th>
                  <th className={`${th} text-right`}>{t("cols.winRate")}</th>
                  <th className={`${th} text-right`}>{t("cols.costs")}</th>
                  <th className={`${th} text-right`}>{t("cols.exposure")}</th>
                  <th className={th}>{t("cols.status")}</th>
                </tr>
              </thead>
              <tbody>
                {d.sims.map((s) => (
                  <tr key={s.id} className="border-t border-border/60 align-top hover:bg-panel-2">
                    <td className={td}>
                      <Link href={`/sims/${s.id}`} className="font-medium hover:text-accent">
                        {s.name}
                      </Link>
                      <div className="text-xs text-muted">
                        {fmtUsd(s.capital, locale)} · {t(`form.sizings.${s.sizing}`)} ×{fmtNum(s.size_factor, locale, 2)} · {fmtTime(s.created_at, locale, false)}
                      </div>
                    </td>
                    <td className={td}>
                      <AddressLink address={s.leader} name={s.leader_name} />
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
                    <td className={`${td} num text-right ${signClass(s.period.max_dd)}`}>{fmtPct(s.period.max_dd, locale)}</td>
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
