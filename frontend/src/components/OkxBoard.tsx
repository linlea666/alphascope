import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
import { ApiErrorState } from "@/components/ApiErrorState";
import { Badge, Panel, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { api, ApiError, type OkxBoard as Board } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd, signClass } from "@/lib/format";

const WINDOWS = ["7d", "30d", "90d", "365d"] as const;
const VIEWS = ["board", "new", "dropped"] as const;

/** OKX lead-trader boards with our own evidence next to the official numbers. */
export async function OkxBoard({ sp }: { sp: Record<string, string | undefined> }) {
  const t = await getTranslations("okxBoard");
  const to = await getTranslations("okx");
  const locale = await getLocale();
  const window = WINDOWS.includes(sp.owin as (typeof WINDOWS)[number]) ? (sp.owin as (typeof WINDOWS)[number]) : "7d";
  const view = VIEWS.includes(sp.view as (typeof VIEWS)[number]) ? (sp.view as (typeof VIEWS)[number]) : "board";
  let d: Board;
  try {
    d = await api.okxBoard({ window, view });
  } catch (e) {
    return <ApiErrorState detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const pill = (active: boolean) => `rounded border px-3 py-1 ${active ? "border-accent bg-accent text-white" : "border-border text-muted hover:text-text"}`;
  const run = d.run;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {VIEWS.map((v) => (
          <Link key={v} href={{ pathname: "/rankings", query: { view: v, owin: window } }} className={pill(v === view)}>
            {t(`views.${v}`)}
          </Link>
        ))}
        <span className="mx-2 h-5 w-px bg-border" />
        {WINDOWS.map((w) => (
          <Link key={w} href={{ pathname: "/rankings", query: { view, owin: w } }} className={pill(w === window)}>
            {to(`windows.${w}`)}
          </Link>
        ))}
      </div>
      <Panel
        title={t(`titles.${view}`)}
        hint={
          view === "dropped"
            ? t("droppedHint")
            : run
              ? t("runHint", { date: run.business_date, total: run.expected_total, n: run.unique_count, new: run.new_traders, at: fmtTime(run.completed_at, locale), partial: run.source === "crawl_top" ? t("partial") : "" })
              : t("noRun")
        }
        actions={
          <div className="flex flex-wrap gap-1 text-[11px]">
            {d.runs.slice(0, 7).map((r) => (
              <Badge key={r.business_date} tone={r.complete ? "up" : r.failed ? "down" : "neutral"} title={`${r.business_date}: ${r.complete}✓ ${r.failed}✗`}>
                {r.business_date.slice(5)}
              </Badge>
            ))}
          </div>
        }
      >
        {d.rows.length ? (
          <div className="-mx-4 -my-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-panel-2">
                <tr>
                  {view !== "dropped" && <th className={`${th} text-right`}>#</th>}
                  <th className={th}>{t("cols.trader")}</th>
                  {view !== "dropped" && (
                    <>
                      <th className={`${th} text-right`}>{t("cols.roi")}</th>
                      <th className={`${th} text-right`}>{t("cols.pnl")}</th>
                      <th className={`${th} text-right`}>{t("cols.win")}</th>
                      <th className={`${th} text-right`}>{t("cols.dd")}</th>
                    </>
                  )}
                  <th className={`${th} text-right`}>{t("cols.asset")}</th>
                  <th className={`${th} text-right`}>{t("cols.onBoard")}</th>
                  <th className={`${th} text-right`}>{t("cols.history")}</th>
                  <th className={`${th} text-right`}>{t("cols.r30")}</th>
                  <th className={`${th} text-right`}>{t("cols.r90")}</th>
                  <th className={`${th} text-right`}>{t("cols.liq")}</th>
                  <th className={`${th} text-right`}>{t("cols.edge")}</th>
                </tr>
              </thead>
              <tbody>
                {d.rows.map((r) => (
                  <tr key={r.address} className="border-t border-border/60 align-top hover:bg-panel-2">
                    {view !== "dropped" && <td className={`${td} num text-right text-muted`}>{r.rank}</td>}
                    <td className={td}>
                      <span className="flex items-start gap-1.5">
                        <AddressLink address={r.address} name={r.display_name} />
                        {r.is_new && <Badge tone="accent">{t("new")}</Badge>}
                      </span>
                    </td>
                    {view !== "dropped" && (
                      <>
                        <td className={`${td} num text-right ${signClass(r.pnl_ratio)}`}>{fmtPct(r.pnl_ratio, locale)}</td>
                        <td className={`${td} num text-right ${signClass(r.pnl)}`}>{fmtUsd(r.pnl, locale)}</td>
                        <td className={`${td} num text-right`}>{fmtPct(r.win_rate, locale, 0, false)}</td>
                        <td className={`${td} num text-right text-muted`}>{fmtPct(r.max_drawdown, locale, 0, false)}</td>
                      </>
                    )}
                    <td className={`${td} num text-right`}>{fmtUsd(r.asset, locale)}</td>
                    <td className={`${td} num text-right text-xs`}>
                      {r.seen_days ? t("daysStreak", { days: r.seen_days, streak: r.streak ?? 0 }) : "—"}
                      {view === "dropped" && r.last_seen_at && <div className="text-muted">{fmtTime(r.last_seen_at, locale, false)}</div>}
                    </td>
                    <td className={`${td} num text-right text-xs`}>
                      {r.n_positions ? fmtNum(r.n_positions, locale) : "—"}
                      <div className="text-muted">{r.history_status ? to(`history.${r.history_status}`) : ""}</div>
                    </td>
                    <td className={`${td} num text-right ${signClass(r.realized_30d)}`}>{fmtUsd(r.realized_30d, locale)}</td>
                    <td className={`${td} num text-right ${signClass(r.realized_90d)}`}>{fmtUsd(r.realized_90d, locale)}</td>
                    <td className={`${td} num text-right ${r.liquidations_90d ? "text-down" : "text-muted"}`}>{r.liquidations_90d}</td>
                    <td className={`${td} num text-right ${signClass(r.best_edge)}`}>{r.best_edge === null ? "—" : fmtNum(r.best_edge, locale, 3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-10 text-center text-sm text-muted">{t("empty")}</p>
        )}
      </Panel>
    </div>
  );
}
