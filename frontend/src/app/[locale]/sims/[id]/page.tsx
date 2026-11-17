import { getLocale, getTranslations } from "next-intl/server";
import { notFound } from "next/navigation";
import { AddressLink } from "@/components/AddressLink";
import { ApiErrorState } from "@/components/ApiErrorState";
import { SimControls } from "@/components/SimControls";
import { SimEquityChart } from "@/components/SimEquityChart";
import { SimForm } from "@/components/SimForm";
import { Term } from "@/components/Term";
import { Badge, PageHeader, Panel, Stat, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { api, ApiError, type SimDetail, type SimPeriod } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd, signClass, simStatusTone } from "@/lib/format";

const PERIODS: SimPeriod[] = ["1d", "7d", "30d", "all"];

export default async function SimPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const t = await getTranslations("sims");
  const locale = await getLocale();
  if (!/^\d+$/.test(id)) notFound();
  let d: SimDetail;
  try {
    d = await api.sim(Number(id));
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    return <ApiErrorState status={e instanceof ApiError ? e.status : undefined} detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const s = d.summary;
  const a = d.analytics;
  const leaderBy = new Map(d.leader_positions.map((p) => [p.coin, p]));
  // ours by notional, then coins only the leader holds (already sorted by value)
  const ours = [...d.positions].sort((x, y) => Math.abs(y.size * (y.mark ?? y.entry_px)) - Math.abs(x.size * (x.mark ?? x.entry_px)));
  const coins = [...new Set([...ours.map((p) => p.coin), ...d.leader_positions.filter((p) => p.szi !== 0).map((p) => p.coin)])];
  const followed = new Set(d.state.followed ?? []);
  const pending = d.state.pending ?? {};
  const unreal = d.positions.reduce((acc, p) => acc + (p.mark === null ? 0 : p.size * (p.mark - p.entry_px)), 0);
  const fmtPx = (v: number | null) => (v === null ? "—" : fmtNum(v, locale, v >= 100 ? 2 : 6));
  const fmtSize = (v: number | null) => (v === null ? "—" : fmtNum(v, locale, 6));

  return (
    <div className="space-y-4">
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            {s.name}
            <Badge tone={simStatusTone(s.status)}>{t(`status.${s.status}`)}</Badge>
            {s.stop_reason && <span className="text-sm font-normal text-muted">{t(`reason.${s.stop_reason}`)}</span>}
          </span>
        }
        subtitle={
          <span className="flex flex-wrap items-center gap-x-2">
            {t("following")} <AddressLink address={s.leader} name={s.leader_name} /> · {t("since", { t: fmtTime(s.created_at, locale) })}
            {s.stopped_at && <> · {t("stoppedAt", { t: fmtTime(s.stopped_at, locale) })}</>}
          </span>
        }
      >
        <span className="flex items-center gap-3">
          <Link href="/sims" className="text-sm text-muted hover:text-text">
            ← {t("back")}
          </Link>
          <SimControls id={s.id} status={s.status} stopPending={s.stop_pending} afterDelete="/sims" />
        </span>
      </PageHeader>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <Stat label={t("stats.equity")} value={fmtUsd(s.equity, locale)} sub={t("capitalOf", { v: fmtUsd(s.capital, locale) })} />
        <Stat label={t("stats.pnl")} value={<span className={signClass(s.pnl)}>{fmtUsd(s.pnl, locale)}</span>} sub={<span className={signClass(s.pnl)}>{fmtPct(s.return, locale)}</span>} />
        <Stat label={t("stats.unrealized")} value={<span className={signClass(unreal)}>{fmtUsd(unreal, locale)}</span>} sub={t("positionsN", { n: d.positions.length })} />
        <Stat label={t("stats.mdd")} value={<span className={signClass(s.max_dd)}>{fmtPct(s.max_dd, locale)}</span>} />
        {s.sized_cycles > 0 && (
          <Stat
            label={t("stats.capped")}
            value={<span className={s.capped_cycles / s.sized_cycles > 0.1 ? "text-warn" : ""}>{fmtPct(s.capped_cycles / s.sized_cycles, locale, 0, false)}</span>}
            sub={t("stats.cappedSub", { n: s.capped_cycles, total: s.sized_cycles })}
          />
        )}
        <Stat label={t("stats.exposure")} value={s.gross_exposure !== null && s.equity > 0 ? `${fmtNum(s.gross_exposure / s.equity, locale, 2)}×` : "—"} sub={fmtUsd(s.gross_exposure, locale)} />
        <Stat
          label={t("stats.costs")}
          value={fmtUsd(s.fees + s.slippage + s.funding, locale)}
          sub={t("stats.costsSub", { fee: fmtUsd(s.fees, locale), slip: fmtUsd(s.slippage, locale), fund: fmtUsd(s.funding, locale) })}
        />
      </div>

      <Panel title={t("periodTitle")} hint={t("periodHint")}>
        <div className="-mx-4 -my-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-panel-2">
              <tr>
                <th className={th} />
                {PERIODS.map((p) => (
                  <th key={p} className={`${th} text-right`}>
                    {t(`periods.${p}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(
                [
                  ["pnl", (v) => <span className={signClass(v.pnl)}>{fmtUsd(v.pnl, locale)}</span>],
                  ["return", (v) => <span className={signClass(v.return)}>{fmtPct(v.return, locale)}</span>],
                  ["leaderRet", (v) => <span className={signClass(v.leader_return)}>{fmtPct(v.leader_return, locale)}</span>],
                  ["retention", (v) => (v.retention === null ? "—" : fmtPct(v.retention, locale, 0, false))],
                  ["mdd", (v) => <span className={signClass(v.max_dd)}>{fmtPct(v.max_dd, locale)}</span>],
                  ["ddDays", (v) => t("days", { n: fmtNum(v.max_dd_days, locale, 1) })],
                  ["currentDd", (v) => <span className={signClass(v.current_dd)}>{fmtPct(v.current_dd, locale)}</span>],
                  ["worstMae", (v) => <span className="text-down">{fmtUsd(v.worst_mae, locale)}</span>],
                  ["liquidated", (v) => <span className={v.liquidated ? "text-down" : ""}>{fmtNum(v.liquidated, locale)}</span>],
                  ["cycles", (v) => fmtNum(v.closed, locale)],
                  ["winRate", (v) => fmtPct(v.win_rate, locale, 0, false)],
                  ["profitFactor", (v) => (v.profit_factor === null ? "—" : fmtNum(v.profit_factor, locale, 2))],
                  ["avgWinLoss", (v) => `${fmtUsd(v.avg_win, locale)} / ${fmtUsd(v.avg_loss, locale)}`],
                  ["medianHold", (v) => (v.median_hold_hours === null ? "—" : t("hours", { n: fmtNum(v.median_hold_hours, locale, 1) }))],
                ] as [string, (v: SimDetail["analytics"]["periods"]["7d"]) => React.ReactNode][]
              ).map(([key, render]) => (
                <tr key={key} className="border-t border-border/60">
                  <td className={`${td} text-muted`}>{t(`rows.${key}`)}</td>
                  {PERIODS.map((p) => (
                    <td key={p} className={`${td} num text-right`}>
                      {render(a.periods[p])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title={t("chartTitle")} hint={t("chartHint", { tz: a.timezone })}>
        <SimEquityChart
          equity={d.equity}
          daily={a.daily}
          capital={s.capital}
          locale={locale}
          labels={{ sim: t("chart.sim"), leader: t("chart.leader"), daily: t("chart.daily"), empty: t("chart.empty") }}
        />
      </Panel>

      <Panel title={t("posTitle")} hint={t("posHint")}>
        {coins.length ? (
          <div className="-mx-4 -my-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-panel-2">
                <tr>
                  <th className={th}>{t("pos.coin")}</th>
                  <th className={`${th} text-right`}>{t("pos.size")}</th>
                  <th className={`${th} text-right`}>{t("pos.entry")}</th>
                  <th className={`${th} text-right`}>{t("pos.mark")}</th>
                  <th className={`${th} text-right`}>{t("pos.value")}</th>
                  <th className={`${th} text-right`}>{t("pos.upnl")}</th>
                  <th className={`${th} text-right`}>
                    <Term k="mae" align="right">{t("pos.worst")}</Term>
                  </th>
                  <th className={`${th} text-right`}>{t("pos.lev")}</th>
                  <th className={`${th} text-right`}>{t("pos.liq")}</th>
                  <th className={`${th} text-right`}>{t("pos.leader")}</th>
                  <th className={th}>{t("pos.state")}</th>
                </tr>
              </thead>
              <tbody>
                {coins.map((c) => {
                  const p = d.positions.find((x) => x.coin === c);
                  const l = leaderBy.get(c);
                  const upnl = p && p.mark !== null ? p.size * (p.mark - p.entry_px) : null;
                  return (
                    <tr key={c} className="border-t border-border/60">
                      <td className={td}>
                        {p ? <Badge tone={p.size > 0 ? "up" : "down"}>{p.size > 0 ? "▲" : "▼"} {c}</Badge> : <span className="text-muted">{c}</span>}
                      </td>
                      <td className={`${td} num text-right`}>{p ? fmtSize(p.size) : "—"}</td>
                      <td className={`${td} num text-right`}>{p ? fmtPx(p.entry_px) : "—"}</td>
                      <td className={`${td} num text-right`}>{p ? fmtPx(p.mark) : "—"}</td>
                      <td className={`${td} num text-right`}>{p && p.mark !== null ? fmtUsd(Math.abs(p.size * p.mark), locale) : "—"}</td>
                      <td className={`${td} num text-right ${signClass(upnl)}`}>{fmtUsd(upnl, locale)}</td>
                      <td className={`${td} num text-right text-down`}>{p?.worst_upnl ? fmtUsd(p.worst_upnl, locale) : "—"}</td>
                      <td className={`${td} num text-right`}>
                        {p?.leverage ? `${fmtNum(p.leverage, locale, 1)}×` : d.params.leverage_mode === "account" && p ? t("pos.cross") : "—"}
                        {p?.margin ? <div className="text-xs text-muted">{fmtUsd(p.margin, locale)}</div> : null}
                      </td>
                      <td className={`${td} num text-right`}>
                        {p?.liq_px ? fmtPx(p.liq_px) : "—"}
                        {p?.liq_px && p.mark ? <div className={`text-xs ${Math.abs(p.mark / p.liq_px - 1) < 0.05 ? "text-down" : "text-muted"}`}>{fmtPct(Math.abs(p.mark / p.liq_px - 1), locale, 1, false)}</div> : null}
                      </td>
                      <td className={`${td} num text-right ${l ? signClass(l.szi) : "text-muted"}`}>
                        {l ? `${fmtSize(l.szi)}` : "—"}
                        {l?.value !== null && l?.value !== undefined && <div className="text-xs text-muted">{fmtUsd(Math.abs(l.value), locale)}</div>}
                        {l?.leverage ? <div className="text-xs text-muted">{l.leverage}× · {t("pos.liqShort")} {fmtPx(l.liq_px)}</div> : null}
                      </td>
                      <td className={`${td} text-xs`}>
                        {pending[c] ? (
                          <span className="text-warn">{t("pendingUntil", { t: fmtTime(pending[c].due, locale) })}</span>
                        ) : followed.has(c) ? (
                          <span className="text-muted">{t("followed")}</span>
                        ) : (
                          <span className="text-muted" title={t("preexistingHint")}>
                            {t("preexisting")}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted">{t("noPositions")}</p>
        )}
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title={t("coinTitle")}>
          {a.per_coin.length ? (
            <div className="-mx-4 -my-4 overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-panel-2">
                  <tr>
                    <th className={th}>{t("pos.coin")}</th>
                    <th className={`${th} text-right`}>{t("coin.net")}</th>
                    <th className={`${th} text-right`}>{t("coin.realized")}</th>
                    <th className={`${th} text-right`}>{t("coin.unrealized")}</th>
                    <th className={`${th} text-right`}>{t("coin.fees")}</th>
                    <th className={`${th} text-right`}>{t("coin.funding")}</th>
                    <th className={`${th} text-right`}>{t("coin.trades")}</th>
                  </tr>
                </thead>
                <tbody>
                  {a.per_coin.map((r) => (
                    <tr key={r.coin} className="border-t border-border/60">
                      <td className={td}>{r.coin}</td>
                      <td className={`${td} num text-right ${signClass(r.net)}`}>{fmtUsd(r.net, locale)}</td>
                      <td className={`${td} num text-right ${signClass(r.realized)}`}>{fmtUsd(r.realized, locale)}</td>
                      <td className={`${td} num text-right ${signClass(r.unrealized)}`}>{fmtUsd(r.unrealized, locale)}</td>
                      <td className={`${td} num text-right text-muted`}>{fmtUsd(r.fees, locale)}</td>
                      <td className={`${td} num text-right text-muted`}>{fmtUsd(r.funding, locale)}</td>
                      <td className={`${td} num text-right`}>{r.trades}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="py-6 text-center text-sm text-muted">{t("noTrades")}</p>
          )}
        </Panel>
        <Panel title={t("cycleTitle")} hint={t("cycleHint")}>
          {a.cycles.length ? (
            <div className="-mx-4 -my-4 max-h-[420px] overflow-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-panel-2">
                  <tr>
                    <th className={th}>{t("pos.coin")}</th>
                    <th className={th}>{t("cycle.opened")}</th>
                    <th className={th}>{t("cycle.closed")}</th>
                    <th className={`${th} text-right`}>{t("cycle.peak")}</th>
                    <th className={`${th} text-right`}>
                      <Term k="mae" align="right">{t("cycle.mae")}</Term>
                    </th>
                    <th className={`${th} text-right`}>{t("cycle.pnl")}</th>
                  </tr>
                </thead>
                <tbody>
                  {a.cycles.map((c, i) => (
                    <tr key={i} className="border-t border-border/60">
                      <td className={td}>
                        <Badge tone={c.side > 0 ? "up" : "down"}>
                          {c.side > 0 ? "▲" : "▼"} {c.coin}
                        </Badge>
                      </td>
                      <td className={`${td} num text-xs text-muted`}>{fmtTime(c.opened_at, locale)}</td>
                      <td className={`${td} num text-xs text-muted`}>{c.closed_at ? fmtTime(c.closed_at, locale) : t("cycle.open")}</td>
                      <td className={`${td} num text-right`}>{fmtUsd(c.max_notional, locale)}</td>
                      <td className={`${td} num text-right text-down`}>
                        {c.mae === null ? "—" : fmtUsd(c.mae, locale)}
                        {c.mae !== null && c.max_notional > 0 && <div className="text-xs text-muted">{fmtPct(c.mae / c.max_notional, locale, 1)}</div>}
                      </td>
                      <td className={`${td} num text-right ${signClass(c.pnl)}`}>
                        {fmtUsd(c.pnl, locale)}
                        {c.liquidated && (
                          <div>
                            <Badge tone="down">{c.leader_liquidated ? t("cycle.leaderLiq") : t("cycle.liq")}</Badge>
                          </div>
                        )}
                        {!c.liquidated && c.leader_liquidated && (
                          <div>
                            <Badge tone="warn">{t("cycle.leaderLiq")}</Badge>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="py-6 text-center text-sm text-muted">{t("noTrades")}</p>
          )}
        </Panel>
      </div>

      <Panel title={t("tradesTitle")} hint={t("tradesHint")}>
        {d.trades.length ? (
          <div className="-mx-4 -my-4 max-h-[520px] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-panel-2">
                <tr>
                  <th className={th}>{t("trade.time")}</th>
                  <th className={th}>{t("pos.coin")}</th>
                  <th className={th}>{t("trade.kind")}</th>
                  <th className={`${th} text-right`}>{t("trade.size")}</th>
                  <th className={`${th} text-right`}>{t("trade.mid")}</th>
                  <th className={`${th} text-right`}>{t("trade.px")}</th>
                  <th className={`${th} text-right`}>{t("trade.cost")}</th>
                  <th className={`${th} text-right`}>{t("trade.realized")}</th>
                  <th className={`${th} text-right`}>{t("trade.after")}</th>
                  <th className={`${th} text-right`}>{t("trade.leader")}</th>
                </tr>
              </thead>
              <tbody>
                {d.trades.map((r, i) => (
                  <tr key={i} className="border-t border-border/60">
                    <td className={`${td} num text-xs text-muted`}>{fmtTime(r.t, locale)}</td>
                    <td className={td}>{r.coin}</td>
                    <td className={`${td} text-xs`}>
                      {r.kind === "trade" ? (
                        <span className={r.side === "buy" ? "text-up" : "text-down"}>{t(`trade.${r.side}`)}</span>
                      ) : (
                        <Badge tone={r.kind === "funding" ? "neutral" : r.kind === "liquidation" ? "down" : "warn"}>{t(`trade.kinds.${r.kind}`)}</Badge>
                      )}
                      {r.reason === "follow_delayed" && <span className="ml-1 text-muted">{t("trade.delayed")}</span>}
                      {(r.reason === "leader_liquidated" || r.reason === "isolated_liquidation") && <span className="ml-1 text-down">{t(`trade.reasons.${r.reason}`)}</span>}
                    </td>
                    <td className={`${td} num text-right`}>{r.kind === "funding" ? "—" : fmtSize(r.size)}</td>
                    <td className={`${td} num text-right text-muted`}>{fmtPx(r.mid)}</td>
                    <td className={`${td} num text-right`}>{fmtPx(r.px)}</td>
                    <td className={`${td} num text-right text-muted`}>{fmtUsd(r.kind === "funding" ? r.funding : r.fee + r.slippage_cost, locale, false)}</td>
                    <td className={`${td} num text-right ${signClass(r.realized_pnl)}`}>{r.realized_pnl ? fmtUsd(r.realized_pnl, locale) : "—"}</td>
                    <td className={`${td} num text-right`}>{fmtSize(r.position_after)}</td>
                    <td className={`${td} num text-right text-muted`}>{fmtSize(r.leader_szi)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted">{t("noTrades")}</p>
        )}
      </Panel>

      {s.status !== "stopped" && (
        <details className="rounded-lg border border-border bg-panel">
          <summary className="cursor-pointer px-4 py-3 text-sm font-semibold">{t("editParams")}</summary>
          <div className="border-t border-border p-4">
            <p className="mb-3 text-xs text-muted">{t("editHint")}</p>
            <SimForm simId={s.id} initial={d.params} />
          </div>
        </details>
      )}
    </div>
  );
}
