import { getLocale, getTranslations } from "next-intl/server";
import { ApiErrorState } from "@/components/ApiErrorState";
import { FlagBadges, ReliabilityBadge, TierBadge } from "@/components/badges";
import { CopyButton } from "@/components/CopyButton";
import { EquityChart } from "@/components/EquityChart";
import { Freshness } from "@/components/Freshness";
import { PositionCycles } from "@/components/PositionCycles";
import { REPLAY_BARS, ReplayPanel } from "@/components/ReplayPanel";
import { TimingPanel } from "@/components/TimingPanel";
import { Badge, Panel, td, th } from "@/components/ui";
import { api, ApiError, type Metric, type WalletDetail, type Replay, type WalletPositions, type WalletTiming, type Window } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd, signClass, splitNote } from "@/lib/format";

const WINDOWS: Window[] = ["day", "week", "month", "alltime"];

export default async function TraderPage({
  params,
  searchParams,
}: {
  params: Promise<{ address: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const { address } = await params;
  const search = await searchParams;
  const t = await getTranslations();
  const locale = await getLocale();
  let d: WalletDetail;
  try {
    d = await api.wallet(address);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 422)) {
      return (
        <div className="py-24 text-center">
          <h1 className="text-lg font-semibold">{t("trader.notInDb")}</h1>
          <p className="num mt-2 text-sm text-muted">{address}</p>
          <p className="mt-1 text-sm text-muted">{t("trader.notInDbHint")}</p>
        </div>
      );
    }
    return <ApiErrorState detail={e instanceof ApiError ? e.message : undefined} />;
  }
  // Position cycles exist only for wallets whose fills are archived; a failure here must not break the page.
  const [positions, timing, replay]: [WalletPositions | null, WalletTiming | null, Replay | null] = await Promise.all([
    api.walletPositions(address).catch(() => null),
    api.walletTiming(address).catch(() => null),
    api.replay(address, { coin: search.rc, interval: search.ri || "1h", end: search.re, bars: REPLAY_BARS }).catch(() => null),
  ]);
  const w = d.wallet;
  const enriched = d.metrics.length > 0;

  const note = (n: string) => {
    const [key, count] = splitNote(n);
    return t.has(`notes.${key}`) ? t(`notes.${key}`, { n: count ?? 0 }) : n;
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold tracking-tight">{w.display_name ?? t("trader.titleFallback")}</h1>
            <TierBadge tier={w.tier} />
            <FlagBadges flags={[...w.flags, ...w.enrichment_flags]} />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="num break-all text-sm text-muted">{w.address}</span>
            <CopyButton text={w.address} label={t("common.copy")} done={t("common.copied")} />
            <a href={`https://hypurrscan.io/address/${w.address}`} target="_blank" rel="noreferrer" className="text-xs text-accent hover:underline">
              {t("common.viewOnExplorer")} ↗
            </a>
          </div>
          {!w.present_in_latest && w.removed_at && (
            <p className="mt-2 text-sm text-warn">{t("trader.removed", { date: fmtTime(w.removed_at, locale) })}</p>
          )}
        </div>
        <div className="text-right">
          <div className="text-xs text-muted">{t("cols.accountValue")} ({t("common.official")})</div>
          <div className="num text-2xl font-semibold">{fmtUsd(w.account_value, locale, false)}</div>
        </div>
      </div>
      <Freshness value={d.freshness} />

      <div className="grid gap-5 lg:grid-cols-3">
        <Panel title={t("trader.officialTitle")} hint={t("trader.officialHint")}>
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className={`${th} pl-0`}>{t("trader.window")}</th>
                <th className={`${th} text-right`}>{t("cols.pnl")}</th>
                <th className={`${th} text-right`}>{t("cols.roi")}</th>
                <th className={`${th} pr-0 text-right`}>{t("cols.volume")}</th>
              </tr>
            </thead>
            <tbody>
              {WINDOWS.map((k) => {
                const v = d.leaderboard[k];
                return (
                  <tr key={k} className="border-t border-border/60">
                    <td className={`${td} pl-0 text-muted`}>{t(`windows.${k}`)}</td>
                    <td className={`${td} num text-right ${signClass(v.pnl)}`}>{fmtUsd(v.pnl, locale)}</td>
                    <td className={`${td} num text-right ${signClass(v.roi)}`}>{fmtPct(v.roi, locale)}</td>
                    <td className={`${td} num pr-0 text-right text-muted`}>{fmtUsd(v.vlm, locale)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Panel>

        <Panel title={t("trader.identity")}>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted">{t("trader.firstSeen")}</dt>
            <dd className="num">{fmtTime(w.first_seen_at, locale)}</dd>
            <dt className="text-muted">{t("trader.source")}</dt>
            <dd>{w.first_seen_source}</dd>
            <dt className="text-muted">{t("trader.lastSeen")}</dt>
            <dd className="num">{fmtTime(w.last_seen_at, locale)}</dd>
            <dt className="text-muted">{t("trader.portfolioFetched")}</dt>
            <dd className="num">{w.portfolio_fetched_at ? fmtTime(w.portfolio_fetched_at, locale) : t("trader.notEnriched")}</dd>
            <dt className="text-muted">{t("cols.tier")}</dt>
            <dd className="text-xs text-muted">{w.tier ? `${t(`tierDesc.${w.tier}`)} (${w.tier_rule_version})` : "—"}</dd>
          </dl>
          {d.tier_history.length > 0 && (
            <div className="mt-4">
              <div className="text-xs text-muted">{t("trader.tierHistory")}</div>
              <ul className="mt-1 space-y-1 text-xs">
                {d.tier_history.map((h) => (
                  <li key={h.assigned_at} className="num">
                    {fmtTime(h.assigned_at, locale)} · {h.previous_tier ?? "∅"} → {h.tier} ({h.rule_version})
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Panel>

        <Panel title={t("trader.positionsTitle")} hint={d.account ? t("trader.positionsAt", { time: fmtTime(d.account.fetched_at, locale) }) : undefined}>
          {!d.account ? (
            <p className="text-sm text-muted">{t("trader.notEnriched")}</p>
          ) : d.positions.length === 0 ? (
            <p className="text-sm text-muted">{t("trader.noPositions")}</p>
          ) : (
            <div className="-mx-4 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className={th}>{t("cols.coin")}</th>
                    <th className={`${th} text-right`}>{t("cols.positionValue")}</th>
                    <th className={`${th} text-right`}>{t("cols.upnl")}</th>
                    <th className={`${th} text-right`}>{t("cols.leverage")}</th>
                    <th className={`${th} text-right`}>{t("cols.liqPx")}</th>
                  </tr>
                </thead>
                <tbody>
                  {d.positions.map((p) => (
                    <tr key={p.coin} className="border-t border-border/60">
                      <td className={td}>
                        <span className={p.szi > 0 ? "text-up" : "text-down"}>{p.szi > 0 ? "▲" : "▼"}</span> {p.coin}
                      </td>
                      <td className={`${td} num text-right`}>{fmtUsd(p.position_value, locale)}</td>
                      <td className={`${td} num text-right ${signClass(p.unrealized_pnl)}`}>{fmtUsd(p.unrealized_pnl, locale)}</td>
                      <td className={`${td} num text-right`}>{p.leverage_value ? `${p.leverage_value}× ${p.leverage_type ?? ""}` : "—"}</td>
                      <td className={`${td} num text-right text-muted`}>{p.liquidation_px ? fmtNum(p.liquidation_px, locale, 4) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>

      <Panel title={t("trader.equityTitle")} hint={t("trader.equityHint")}>
        <EquityChart
          month={d.equity.month}
          allTime={d.equity.allTime}
          locale={locale}
          labels={{
            equity: t("trader.equity"),
            cumPnl: t("trader.cumPnl"),
            seriesMonth: t("trader.seriesMonth"),
            seriesAll: t("trader.seriesAll"),
            empty: enriched ? t("common.noData") : t("trader.notEnrichedHint"),
          }}
        />
      </Panel>

      <PositionCycles data={positions} />

      <TimingPanel data={timing} />

      <ReplayPanel address={w.address} data={replay} search={search} />

      <Panel title={t("trader.computedTitle")} hint={t("trader.computedHint")}>
        {enriched ? <MetricsTable metrics={d.metrics} note={note} /> : <p className="text-sm text-muted">{t("trader.notEnrichedHint")}</p>}
      </Panel>

      <Panel title={t("trader.snapshotsTitle")} hint={t("trader.snapshotsHint")}>
        <div className="-mx-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className={th}>{t("cols.time")}</th>
                <th className={`${th} text-right`}>{t("cols.accountValue")}</th>
                {WINDOWS.map((k) => (
                  <th key={k} className={`${th} text-right`}>
                    {t(`windows.${k}`)} {t("cols.pnl")}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {d.snapshots.map((s) => (
                <tr key={s.fetched_at} className="border-t border-border/60">
                  <td className={`${td} num text-xs text-muted`}>{fmtTime(s.fetched_at, locale)}</td>
                  <td className={`${td} num text-right`}>{fmtUsd(s.account_value, locale)}</td>
                  {[s.day_pnl, s.week_pnl, s.month_pnl, s.alltime_pnl].map((v, i) => (
                    <td key={i} className={`${td} num text-right ${signClass(v)}`}>
                      {fmtUsd(v, locale)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

async function MetricsTable({ metrics, note }: { metrics: Metric[]; note: (n: string) => string }) {
  const t = await getTranslations();
  const locale = await getLocale();
  return (
    <div className="-mx-4 overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr>
            <th className={th}>{t("trader.window")}</th>
            <th className={`${th} text-right`}>{t("trader.twr")}</th>
            <th className={`${th} text-right`}>{t("trader.mdd")}</th>
            <th className={`${th} text-right`}>{t("trader.pnlCurve")}</th>
            <th className={`${th} text-right`}>{t("trader.pnlMdd")}</th>
            <th className={`${th} text-right`}>{t("trader.flows")}</th>
            <th className={`${th} text-right`}>{t("trader.posRatio")}</th>
            <th className={th}>{t("trader.span")}</th>
            <th className={`${th} text-right`}>{t("trader.points")}</th>
            <th className={`${th} text-right`}>{t("trader.step")}</th>
            <th className={th}>{t("reliability.label")}</th>
          </tr>
        </thead>
        <tbody>
          {metrics.map((m) => (
            <tr key={m.metric_window} className="border-t border-border/60 align-top">
              <td className={`${td} font-medium`}>
                {m.metric_window.toUpperCase()}
                {!m.covered && (
                  <div className="mt-1">
                    <Badge tone="warn">{t("common.insufficient")}</Badge>
                  </div>
                )}
              </td>
              <td className={`${td} num text-right ${signClass(m.twr)}`}>{fmtPct(m.twr, locale)}</td>
              <td className={`${td} num text-right ${m.mdd ? "text-down" : "text-muted"}`}>{fmtPct(m.mdd, locale)}</td>
              <td className={`${td} num text-right ${signClass(m.pnl)}`}>{fmtUsd(m.pnl, locale)}</td>
              <td className={`${td} num text-right text-down`}>{fmtUsd(m.pnl_mdd_usd, locale)}</td>
              <td className={`${td} num text-right text-muted`}>{fmtUsd(m.net_flows, locale)}</td>
              <td className={`${td} num text-right`}>{fmtPct(m.positive_period_ratio, locale, 0, false)}</td>
              <td className={`${td} num text-xs text-muted`}>
                {fmtTime(m.span_start, locale, false)} → {fmtTime(m.span_end, locale, false)}
              </td>
              <td className={`${td} num text-right`}>{m.n_points}</td>
              <td className={`${td} num text-right text-muted`}>{m.median_step_hours !== null ? t("trader.hours", { n: fmtNum(m.median_step_hours, locale, 1) }) : "—"}</td>
              <td className={`${td} whitespace-normal`}>
                <ReliabilityBadge value={m.reliability} />
                {m.notes.length > 0 && (
                  <ul className="mt-1 max-w-xs space-y-0.5 text-[11px] leading-snug text-muted">
                    {m.notes.map((n) => (
                      <li key={n}>· {note(n)}</li>
                    ))}
                  </ul>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
