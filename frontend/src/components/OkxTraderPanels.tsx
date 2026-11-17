import { getLocale, getTranslations } from "next-intl/server";
import { EquityChart } from "@/components/EquityChart";
import { Badge, Panel, Stat, td, th } from "@/components/ui";
import type { OkxDetail } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd, signClass } from "@/lib/format";

/** OKX-only sections of the trader page: track record from closed positions, history coverage, leaderboard presence. */
export async function OkxTraderPanels({ okx }: { okx: OkxDetail }) {
  const t = await getTranslations("okx");
  const locale = await getLocale();
  const tr = okx.trader;
  const tot = okx.totals;
  const closed = tot.positions;
  const curve = okx.pnl_curve;
  const monthAgo = Date.now() - 30 * 86400_000;

  return (
    <>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <Stat label={t("realized")} value={<span className={signClass(tot.realized)}>{fmtUsd(tot.realized, locale)}</span>} sub={t("closedN", { n: fmtNum(closed, locale) })} />
        <Stat label={t("winRate")} value={closed ? fmtPct(tot.wins / closed, locale, 0, false) : "—"} sub={t("winRateSub")} />
        <Stat label={t("liquidations")} value={<span className={tot.liquidations ? "text-down" : ""}>{fmtNum(tot.liquidations, locale)}</span>} sub={closed ? fmtPct(tot.liquidations / closed, locale, 1, false) : undefined} />
        <Stat label={t("costs")} value={fmtUsd((tot.fees ?? 0) + (tot.funding ?? 0), locale)} sub={t("costsSub", { fee: fmtUsd(tot.fees, locale), fund: fmtUsd(tot.funding, locale) })} />
        <Stat label={t("coins")} value={fmtNum(tot.coins, locale)} />
        <Stat
          label={t("coverage")}
          value={tr?.oldest_at ? t("days", { n: fmtNum((Date.parse(tr.newest_at ?? tr.oldest_at) - Date.parse(tr.oldest_at)) / 86400_000, locale) }) : "—"}
          sub={tr ? t(`history.${tr.history_status}`) : undefined}
        />
      </div>

      <Panel title={t("curveTitle")} hint={t("curveHint")}>
        <EquityChart
          month={curve.filter((p) => Date.parse(p[0]) >= monthAgo)}
          allTime={curve}
          locale={locale}
          labels={{ equity: t("cumRealized"), cumPnl: t("dayRealized"), seriesMonth: t("last30"), seriesAll: t("all"), empty: t("noPositions") }}
        />
      </Panel>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title={t("presenceTitle")} hint={t("presenceHint")}>
          {okx.presence.length ? (
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className={`${th} pl-0`}>{t("window")}</th>
                  <th className={`${th} text-right`}>{t("seenDays")}</th>
                  <th className={`${th} text-right`}>{t("streak")}</th>
                  <th className={`${th} text-right`}>{t("bestRank")}</th>
                  <th className={`${th} pr-0`}>{t("lastSeen")}</th>
                </tr>
              </thead>
              <tbody>
                {okx.presence.map((p) => (
                  <tr key={p.time_window} className="border-t border-border/60">
                    <td className={`${td} pl-0`}>
                      {t(`windows.${p.time_window}`)} {p.currently_seen && <Badge tone="up">{t("onBoard")}</Badge>}
                    </td>
                    <td className={`${td} num text-right`}>{p.seen_days}</td>
                    <td className={`${td} num text-right`}>{p.streak}</td>
                    <td className={`${td} num text-right`}>{p.best_rank ?? "—"}</td>
                    <td className={`${td} num pr-0 text-xs text-muted`}>{fmtTime(p.last_seen_at, locale, false)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-sm text-muted">{t("neverRanked")}</p>
          )}
        </Panel>
        <Panel title={t("syncTitle")}>
          {tr ? (
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              <dt className="text-muted">{t("pool")}</dt>
              <dd>
                <Badge tone={tr.pool_status === "active" ? "up" : tr.pool_status === "watching" ? "accent" : "neutral"}>{t(`pool_.${tr.pool_status}`)}</Badge>
              </dd>
              <dt className="text-muted">{t("historyStatus")}</dt>
              <dd className="text-xs">{t(`history.${tr.history_status}`)}</dd>
              <dt className="text-muted">{t("range")}</dt>
              <dd className="num text-xs">
                {fmtTime(tr.oldest_at, locale, false)} → {fmtTime(tr.newest_at, locale, false)}
              </dd>
              <dt className="text-muted">{t("synced")}</dt>
              <dd className="num text-xs">{fmtTime(tr.history_synced_at, locale)}</dd>
              <dt className="text-muted">{t("asset")}</dt>
              <dd className="num">{tr.asset !== null ? `${fmtUsd(tr.asset, locale)} · ${fmtTime(tr.asset_at, locale, false)}` : "—"}</dd>
              <dt className="text-muted">{t("source")}</dt>
              <dd className="text-xs text-muted">{tr.first_pool_source ?? "—"}</dd>
              {tr.last_error && (
                <>
                  <dt className="text-muted">{t("lastError")}</dt>
                  <dd className="text-xs text-down">{tr.last_error}</dd>
                </>
              )}
            </dl>
          ) : (
            <p className="text-sm text-muted">—</p>
          )}
          <p className="mt-3 text-xs leading-relaxed text-muted">{t("granularity")}</p>
        </Panel>
      </div>

      {okx.ranks.length > 0 && (
        <Panel title={t("ranksTitle")} hint={t("ranksHint")}>
          <div className="-mx-4 -my-4 max-h-[360px] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-panel-2">
                <tr>
                  <th className={th}>{t("date")}</th>
                  <th className={th}>{t("window")}</th>
                  <th className={`${th} text-right`}>{t("rank")}</th>
                  <th className={`${th} text-right`}>{t("asset")}</th>
                  <th className={`${th} text-right`}>PnL</th>
                  <th className={`${th} text-right`}>ROI</th>
                  <th className={`${th} text-right`}>{t("officialWin")}</th>
                  <th className={`${th} text-right`}>{t("officialDd")}</th>
                </tr>
              </thead>
              <tbody>
                {okx.ranks.map((r, i) => (
                  <tr key={i} className="border-t border-border/60">
                    <td className={`${td} num text-xs text-muted`}>{r.business_date}</td>
                    <td className={td}>{t(`windows.${r.time_window}`)}</td>
                    <td className={`${td} num text-right`}>{r.rank}</td>
                    <td className={`${td} num text-right`}>{fmtUsd(r.asset, locale)}</td>
                    <td className={`${td} num text-right ${signClass(r.pnl)}`}>{fmtUsd(r.pnl, locale)}</td>
                    <td className={`${td} num text-right ${signClass(r.pnl_ratio)}`}>{fmtPct(r.pnl_ratio, locale)}</td>
                    <td className={`${td} num text-right`}>{fmtPct(r.win_rate, locale, 0, false)}</td>
                    <td className={`${td} num text-right text-muted`}>{fmtPct(r.max_drawdown, locale, 0, false)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </>
  );
}
