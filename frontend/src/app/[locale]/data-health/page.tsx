import { getLocale, getTranslations } from "next-intl/server";
import { ApiErrorState } from "@/components/ApiErrorState";
import { Freshness } from "@/components/Freshness";
import { Badge, PageHeader, Panel, td, th } from "@/components/ui";
import { api, ApiError, type DataHealth, type Run } from "@/lib/api";
import { fmtBytes, fmtNum, fmtPct, fmtTime } from "@/lib/format";

const STATUS_TONE: Record<string, "up" | "down" | "warn" | "neutral"> = {
  success: "up",
  not_modified: "neutral",
  partial: "warn",
  failed: "down",
  abandoned: "warn",
  running: "neutral",
};

function summary(r: Run): string {
  const s = r.stats as Record<string, unknown>;
  if (r.kind === "leaderboard") {
    if (s.reason) return String(s.reason);
    if (s.rows === undefined) return "";
    return `rows=${s.rows} new=${s.new_wallets} gone=${s.disappeared_wallets}${s.rows_persisted ? " snapshot" : ""}`;
  }
  if (s.selected === undefined) return "";
  const apiStats = s.api as { weight?: number } | undefined;
  return `ok=${s.ok}/${s.selected} failed=${s.failed} weight=${apiStats?.weight ?? 0}`;
}

export default async function DataHealthPage() {
  const t = await getTranslations();
  const locale = await getLocale();
  let d: DataHealth;
  try {
    d = await api.dataHealth();
  } catch (e) {
    return <ApiErrorState status={e instanceof ApiError ? e.status : undefined} detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const b = d.api_budget;
  const s = d.schedule;
  const statusBadge = (st: string) => <Badge tone={STATUS_TONE[st] ?? "neutral"}>{t.has(`status.${st}`) ? t(`status.${st}`) : st}</Badge>;

  return (
    <div className="space-y-5">
      <PageHeader title={t("health.title")} subtitle={t("health.subtitle")}>
        <Freshness value={d.freshness} />
      </PageHeader>

      <div className="grid gap-5 lg:grid-cols-3">
        <Panel title={t("health.latest")}>
          <ul className="space-y-2 text-sm">
            {d.latest_success.map((r) => (
              <li key={r.kind} className="flex items-center justify-between gap-2">
                <span>{r.kind}</span>
                <span className="flex items-center gap-2">
                  {statusBadge(r.status)}
                  <span className="num text-xs text-muted">{fmtTime(r.finished_at, locale)}</span>
                </span>
              </li>
            ))}
          </ul>
        </Panel>
        <Panel title={t("health.budget")} hint={t("health.budgetHint", { share: fmtPct(b.safety_share, locale, 0, false) })}>
          <div className="text-sm">{t("health.budgetUsed", { used: fmtNum(b.weight_used_24h, locale), cap: fmtNum(b.weight_capacity_24h, locale) })}</div>
          <div className="mt-2 h-1.5 rounded bg-panel-2">
            <div className="h-1.5 rounded bg-accent" style={{ width: `${Math.min(100, (100 * b.weight_used_24h) / b.weight_capacity_24h)}%` }} />
          </div>
        </Panel>
        <Panel title={t("health.schedule")}>
          <p className="text-sm leading-relaxed text-muted">
            {t("health.scheduleText", {
              lb: s.leaderboard_interval_minutes,
              snap: s.leaderboard_snapshot_hours,
              en: s.enrich_interval_minutes,
              batch: s.enrich_batch_size,
              days: s.portfolio_refresh_days,
            })}
          </p>
        </Panel>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title={t("health.tables")}>
          <table className="w-full text-sm">
            <tbody>
              {Object.entries(d.tables).map(([k, v]) => (
                <tr key={k} className="border-t border-border/60 first:border-0">
                  <td className={`${td} num pl-0 text-muted`}>{k}</td>
                  <td className={`${td} num pr-0 text-right`}>{fmtNum(v, locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <Panel title={t("health.raw")}>
          <table className="w-full text-sm">
            <tbody>
              {d.raw_archive.map((r) => (
                <tr key={r.source} className="border-t border-border/60 first:border-0">
                  <td className={`${td} num pl-0 text-muted`}>{r.source}</td>
                  <td className={`${td} num text-right`}>{fmtNum(r.n, locale)}</td>
                  <td className={`${td} num pr-0 text-right`}>{fmtBytes(r.bytes, locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title={t("health.marketTitle")} hint={t("health.marketHint")}>
          <div className="-mx-4 max-h-96 overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className={th}>{t("health.source")}</th>
                  <th className={th}>{t("health.coin")}</th>
                  <th className={`${th} text-right`}>{t("health.rows1m")}</th>
                  <th className={th}>{t("health.range")}</th>
                </tr>
              </thead>
              <tbody>
                {d.market_data.map((m) => (
                  <tr key={`${m.source}-${m.coin}`} className="border-t border-border/60">
                    <td className={`${td} text-xs text-muted`}>{m.source}</td>
                    <td className={td}>{m.coin}</td>
                    <td className={`${td} num text-right`}>{fmtNum(m.m1, locale)}</td>
                    <td className={`${td} num text-xs text-muted`}>
                      {m.first_t.slice(0, 10)} → {m.last_t.slice(0, 10)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel title={t("health.symbolsTitle")} hint={t("health.symbolsHint")}>
          <div className="-mx-4 max-h-96 overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className={th}>{t("health.coin")}</th>
                  <th className={th}>{t("health.status")}</th>
                  <th className={`${th} text-right`}>{t("health.ratio")}</th>
                  <th className={`${th} text-right`}>{t("health.p95")}</th>
                  <th className={`${th} text-right`}>{t("health.samples")}</th>
                </tr>
              </thead>
              <tbody>
                {d.symbols.map((s) => (
                  <tr key={s.coin} className="border-t border-border/60" title={s.notes ?? undefined}>
                    <td className={td}>
                      {s.coin} <span className="text-xs text-muted">{s.symbol}</span>
                    </td>
                    <td className={td}>
                      <Badge tone={s.status === "valid" ? "up" : s.status === "invalid" ? "down" : "neutral"}>{s.status}</Badge>
                    </td>
                    <td className={`${td} num text-right`}>{s.median_ratio === null ? "—" : fmtNum(s.median_ratio, locale, 4)}</td>
                    <td className={`${td} num text-right text-muted`}>{s.basis_p95_bps === null ? "—" : `${fmtNum(s.basis_p95_bps, locale, 1)} bp`}</td>
                    <td className={`${td} num text-right`}>{fmtNum(s.n_samples, locale)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <Panel title={t("health.okxTitle")} hint={t("health.okxHint", { rpm: d.okx.requests_per_minute, missing: d.okx.without_handle, coins: d.okx.candle_coins })}>
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="-mx-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className={th}>{t("health.okxDate")}</th>
                  <th className={th}>{t("health.okxBoard")}</th>
                  <th className={th}>{t("health.status")}</th>
                  <th className={`${th} text-right`}>{t("health.okxCaptured")}</th>
                  <th className={`${th} text-right`}>{t("health.okxNew")}</th>
                  <th className={th}>{t("health.error")}</th>
                </tr>
              </thead>
              <tbody>
                {d.okx.runs.map((r, i) => (
                  <tr key={i} className="border-t border-border/60">
                    <td className={`${td} num text-xs text-muted`}>{fmtTime(r.started_at, locale)}</td>
                    <td className={td}>{r.time_window}{r.source === "crawl_top" ? " (top)" : ""}</td>
                    <td className={td}>{statusBadge(r.status === "complete" ? "success" : r.status)}</td>
                    <td className={`${td} num text-right`}>{r.expected_total === null ? "—" : `${r.unique_count} / ${r.expected_total}`}</td>
                    <td className={`${td} num text-right`}>{r.new_traders}</td>
                    <td className={`${td} max-w-xs truncate text-xs text-down`} title={r.error ?? undefined}>{r.error ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="-mx-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className={th}>{t("okx.pool")}</th>
                  <th className={th}>{t("okx.historyStatus")}</th>
                  <th className={`${th} text-right`}>{t("health.okxTraders")}</th>
                  <th className={`${th} text-right`}>{t("health.okxPositions")}</th>
                  <th className={`${th} text-right`}>{t("health.okxSynced")}</th>
                  <th className={`${th} text-right`}>{t("health.okxBackoff")}</th>
                </tr>
              </thead>
              <tbody>
                {d.okx.pool.map((r) => (
                  <tr key={`${r.pool_status}-${r.history_status}`} className="border-t border-border/60">
                    <td className={td}>{t.has(`okx.pool_.${r.pool_status}`) ? t(`okx.pool_.${r.pool_status}`) : r.pool_status}</td>
                    <td className={`${td} text-xs`}>{t.has(`okx.history.${r.history_status}`) ? t(`okx.history.${r.history_status}`) : r.history_status}</td>
                    <td className={`${td} num text-right`}>{fmtNum(r.n, locale)}</td>
                    <td className={`${td} num text-right`}>{fmtNum(r.positions, locale)}</td>
                    <td className={`${td} num text-right`}>{fmtNum(r.synced_24h, locale)}</td>
                    <td className={`${td} num text-right ${r.backing_off ? "text-warn" : "text-muted"}`}>{r.backing_off}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </Panel>

      <Panel title={t("health.runs")}>
        <div className="-mx-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className={th}>#</th>
                <th className={th}>{t("health.kind")}</th>
                <th className={th}>{t("health.status")}</th>
                <th className={th}>{t("health.started")}</th>
                <th className={`${th} text-right`}>{t("health.duration")}</th>
                <th className={th}>{t("health.summary")}</th>
                <th className={th}>{t("health.error")}</th>
              </tr>
            </thead>
            <tbody>
              {d.runs.map((r) => {
                const secs = r.finished_at ? (new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000 : null;
                return (
                  <tr key={r.id} className="border-t border-border/60">
                    <td className={`${td} num text-muted`}>{r.id}</td>
                    <td className={td}>{r.kind}</td>
                    <td className={td}>{statusBadge(r.status)}</td>
                    <td className={`${td} num text-xs text-muted`}>{fmtTime(r.started_at, locale)}</td>
                    <td className={`${td} num text-right`}>{secs === null ? "—" : `${fmtNum(secs, locale, 1)}s`}</td>
                    <td className={`${td} num text-xs`}>{summary(r)}</td>
                    <td className={`${td} max-w-xs truncate text-xs text-down`} title={r.error ?? undefined}>
                      {r.error ?? ""}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
