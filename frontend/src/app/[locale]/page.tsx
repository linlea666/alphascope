import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
import { ApiErrorState } from "@/components/ApiErrorState";
import { FlagBadges, ReliabilityBadge, TierBadge } from "@/components/badges";
import { Freshness } from "@/components/Freshness";
import { DigestCard, StartGuide } from "@/components/HomeExtras";
import { TopPerformers } from "@/components/TopPerformers";
import { Badge, PageHeader, Panel, Stat, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { api, ApiError, type Overview, type OkxSummary } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd, signClass } from "@/lib/format";

const TIER_ORDER = ["B", "C", "Z", "A", "S"];

export default async function OverviewPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const top = (await searchParams).top === "30d" ? "30d" : "7d";
  const t = await getTranslations();
  const locale = await getLocale();
  let data: Overview;
  try {
    data = await api.overview();
  } catch (e) {
    return <ApiErrorState status={e instanceof ApiError ? e.status : undefined} detail={e instanceof ApiError ? e.message : undefined} />;
  }
  let okx: OkxSummary | null = null;
  try {
    okx = await api.okxSummary();
  } catch {
    okx = null; // OKX is a second source: the overview still renders without it
  }
  const { counts, screening } = data;
  const presentTotal = Object.values(data.tiers).reduce((a, b) => a + b, 0) || 1;
  const flags = Object.entries(data.flags).sort((a, b) => b[1] - a[1]);

  return (
    <div className="space-y-5">
      <PageHeader title={t("overview.title")} subtitle={t("overview.subtitle")}>
        <Freshness value={data.freshness} />
      </PageHeader>

      <StartGuide />

      <DigestCard />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        <Stat label={t("overview.walletsTotal")} value={fmtNum(counts.total, locale)} />
        <Stat label={t("overview.present")} value={fmtNum(counts.present, locale)} />
        <Stat label={t("overview.tierB")} value={fmtNum(counts.tier_b, locale)} />
        <Stat
          label={t("overview.enriched")}
          value={fmtNum(counts.enriched, locale)}
          sub={t("overview.enrichedOf", { pct: fmtPct(counts.tier_b ? counts.enriched / counts.tier_b : 0, locale, 1, false) })}
        />
        <Stat label={t("overview.new24h")} value={fmtNum(counts.new_24h, locale)} />
        <Stat label={t("overview.new7d")} value={fmtNum(counts.new_7d, locale)} />
      </div>

      {okx && (
        <Panel
          title={t("overview.okxTitle")}
          hint={okx.latest_7d ? t("overview.okxHint", { date: okx.latest_7d.business_date, n: okx.latest_7d.unique_count, total: okx.latest_7d.expected_total }) : undefined}
          actions={
            <Link href="/okx" className="rounded border border-accent px-3 py-1 text-xs text-accent hover:bg-accent hover:text-white">
              {t("overview.okxOpen")} →
            </Link>
          }
        >
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
            <Stat label={t("overview.okxTraders")} value={fmtNum(okx.traders, locale)} />
            <Stat label={t("overview.okxPositions")} value={fmtNum(okx.positions, locale)} />
            <Stat label={t("overview.okxComplete")} value={fmtNum(okx.complete, locale)} />
            <Stat label={t("overview.okxActive")} value={fmtNum(okx.active, locale)} />
            <Stat label={t("overview.okxDropped")} value={fmtNum(okx.dropped, locale)} />
            <Stat label={t("overview.okxNewToday")} value={fmtNum(okx.latest_7d?.new_traders ?? 0, locale)} />
          </div>
        </Panel>
      )}

      <TopPerformers period={top} />

      <Panel
        title={t("overview.screeningTitle")}
        hint={t("overview.screeningHint", {
          dd: fmtPct(screening.criteria.max_drawdown_30d, locale, 0),
          pos: fmtPct(screening.criteria.min_positive_period_ratio, locale, 0, false),
        })}
      >
        {screening.rows.length ? (
          <div className="-mx-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className={th}>{t("cols.trader")}</th>
                  <th className={`${th} text-right`}>{t("cols.accountValue")}</th>
                  <th className={`${th} text-right`}>{t("cols.twr30")}</th>
                  <th className={`${th} text-right`}>{t("cols.mdd30")}</th>
                  <th className={`${th} text-right`}>{t("overview.ratio")}</th>
                  <th className={`${th} text-right`}>{t("trader.posRatio")}</th>
                  <th className={th}>{t("reliability.label")}</th>
                </tr>
              </thead>
              <tbody>
                {screening.rows.map((r) => (
                  <tr key={r.address} className="border-b border-border/60 last:border-0 hover:bg-panel-2">
                    <td className={td}>
                      <AddressLink address={r.address} name={r.display_name} />
                    </td>
                    <td className={`${td} num text-right`}>{fmtUsd(r.account_value, locale)}</td>
                    <td className={`${td} num text-right ${signClass(r.twr)}`}>{fmtPct(r.twr, locale)}</td>
                    <td className={`${td} num text-right text-down`}>{fmtPct(r.mdd, locale)}</td>
                    <td className={`${td} num text-right`}>{fmtNum(r.twr / Math.max(Math.abs(r.mdd), 0.01), locale, 2)}</td>
                    <td className={`${td} num text-right`}>{fmtPct(r.positive_period_ratio, locale, 0, false)}</td>
                    <td className={td}>
                      <ReliabilityBadge value={r.reliability} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted">{t("overview.screeningEmpty")}</p>
        )}
      </Panel>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title={t("overview.tiersTitle")}>
          <ul className="space-y-3">
            {TIER_ORDER.filter((k) => data.tiers[k] !== undefined).map((k) => (
              <li key={k}>
                <div className="flex items-center justify-between gap-3 text-sm">
                  <span className="flex items-center gap-2">
                    <TierBadge tier={k} />
                    <span className="text-xs text-muted">{t(`tierDesc.${k}`)}</span>
                  </span>
                  <span className="num">{fmtNum(data.tiers[k], locale)}</span>
                </div>
                <div className="mt-1 h-1.5 rounded bg-panel-2">
                  <div className="h-1.5 rounded bg-accent" style={{ width: `${(100 * data.tiers[k]) / presentTotal}%` }} />
                </div>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title={t("overview.flagsTitle")} hint={t("overview.flagsHint")}>
          <ul className="space-y-2 text-sm">
            {flags.map(([f, n]) => (
              <li key={f} className="flex items-start justify-between gap-3">
                <span>
                  <FlagBadges flags={[f]} />
                  <span className="mt-1 block text-xs text-muted">{t.has(`flagDesc.${f}`) ? t(`flagDesc.${f}`) : ""}</span>
                </span>
                <span className="num shrink-0">{fmtNum(n, locale)}</span>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <Panel title={t("overview.newTitle")} hint={t("overview.newHint")}>
        {data.new_wallets.length ? (
          <div className="-mx-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className={th}>{t("cols.trader")}</th>
                  <th className={th}>{t("cols.firstSeen")}</th>
                  <th className={`${th} text-right`}>{t("cols.accountValue")}</th>
                  <th className={`${th} text-right`}>30D {t("cols.pnl")}</th>
                  <th className={th}>{t("cols.tier")}</th>
                </tr>
              </thead>
              <tbody>
                {data.new_wallets.map((w) => (
                  <tr key={w.address} className="border-b border-border/60 last:border-0">
                    <td className={td}>
                      <AddressLink address={w.address} name={w.display_name} />
                    </td>
                    <td className={`${td} num text-xs text-muted`}>{fmtTime(w.first_seen_at, locale)}</td>
                    <td className={`${td} num text-right`}>{fmtUsd(w.account_value, locale)}</td>
                    <td className={`${td} num text-right ${signClass(w.month_pnl)}`}>{fmtUsd(w.month_pnl, locale)}</td>
                    <td className={td}>
                      <TierBadge tier={w.tier} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted">
            {t("overview.newEmpty")} {data.baseline_import_at && <Badge>{fmtTime(data.baseline_import_at, locale)}</Badge>}
          </p>
        )}
      </Panel>
    </div>
  );
}
