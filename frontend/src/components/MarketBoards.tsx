import { getLocale, getTranslations } from "next-intl/server";
import type { ReactNode } from "react";
import { AddressLink } from "@/components/AddressLink";
import { Term } from "@/components/Term";
import { Badge, Panel, Stat, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { api, type MarketEvent, type MarketPositionRow } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd, signClass } from "@/lib/format";

export const VIEWS = ["overview", "risk", "liquidated", "opens", "underwater", "whales"] as const;
export type View = (typeof VIEWS)[number];
type Search = Record<string, string | undefined>;
type Q = (o: Record<string, string | undefined>) => Record<string, string>;

const pill = (active: boolean) => `rounded border px-2 py-0.5 ${active ? "border-accent text-accent" : "border-border text-muted hover:text-text"}`;
const NOTIONALS = [0, 100_000, 1_000_000, 5_000_000];

/** Tabs over the market radar: the overview and the position lists. */
export async function ViewTabs({ view, q }: { view: View; q: Q }) {
  const t = await getTranslations("boards");
  return (
    <div className="flex flex-wrap gap-1 border-b border-border text-sm">
      {VIEWS.map((v) => (
        <Link key={v} href={{ pathname: "/market", query: q({ view: v === "overview" ? undefined : v, coin: undefined }) }} className={`-mb-px border-b-2 px-3 py-1.5 ${v === view ? "border-accent text-text" : "border-transparent text-muted hover:text-text"}`}>
          {t(`tabs.${v}`)}
        </Link>
      ))}
    </div>
  );
}

function Pills({ label, options, current, render, q, param }: { label: string; options: (string | number)[]; current: string; render: (v: string) => string; q: Q; param: string }) {
  return (
    <span className="flex flex-wrap items-center gap-1 text-xs">
      <span className="text-muted">{label}:</span>
      {options.map((o) => (
        <Link key={String(o)} href={{ pathname: "/market", query: q({ [param]: String(o) }) }} className={pill(String(o) === current)}>
          {render(String(o))}
        </Link>
      ))}
    </span>
  );
}

async function PositionTable({ rows, extra }: { rows: MarketPositionRow[]; extra?: { head: ReactNode; cell: (r: MarketPositionRow) => ReactNode } }) {
  const t = await getTranslations("boards");
  const tm = await getTranslations("market");
  const locale = await getLocale();
  if (!rows.length) return <p className="py-10 text-center text-sm text-muted">{t("empty")}</p>;
  const px = (v: number | null | undefined) => (v === null || v === undefined ? "—" : fmtNum(v, locale, v >= 100 ? 1 : v >= 1 ? 4 : 6));
  return (
    <div className="-mx-4 -my-4 max-h-[640px] overflow-auto">
      <table className="w-full text-sm">
        <thead className="sticky top-0 z-10 bg-panel-2">
          <tr>
            <th className={th}>{tm("cols.trader")}</th>
            <th className={th}>{t("cols.coin")}</th>
            <th className={`${th} text-right`}>{tm("cols.notional")}</th>
            <th className={`${th} text-right`}>
              <Term k="margin" align="right">{t("cols.margin")}</Term>
            </th>
            <th className={`${th} text-right`}>{tm("cols.lev")}</th>
            <th className={`${th} text-right`}>{t("cols.entryMark")}</th>
            <th className={`${th} text-right`}>{t("cols.liq")}</th>
            <th className={`${th} text-right`}>{tm("cols.upl")}</th>
            {extra && <th className={`${th} text-right`}>{extra.head}</th>}
            <th className={th}>{tm("cols.observed")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.address}-${r.coin}`} className="border-t border-border/60 hover:bg-panel-2">
              <td className={td}>
                <AddressLink address={r.address} name={r.display_name} />
              </td>
              <td className={td}>
                <Link href={{ pathname: "/market", query: { coin: r.coin } }} className="hover:underline">
                  <Badge tone={r.szi > 0 ? "up" : "down"}>
                    {r.szi > 0 ? "▲" : "▼"} {r.coin}
                  </Badge>
                </Link>
              </td>
              <td className={`${td} num text-right`}>{fmtUsd(r.notional_usd, locale)}</td>
              <td className={`${td} num text-right`}>{fmtUsd(r.margin, locale)}</td>
              <td className={`${td} num text-right`}>
                {r.leverage ? `${r.leverage}×` : "—"}
                {r.margin_mode && <div className="text-[11px] text-muted">{tm(`modes.${r.margin_mode === "isolated" ? "isolated" : "cross"}`)}</div>}
              </td>
              <td className={`${td} num text-right`}>
                {px(r.entry_px)}
                <div className="text-[11px] text-muted">{px(r.mark_px)}</div>
              </td>
              <td className={`${td} num text-right`}>
                {px(r.liq_px)}
                {r.liq_dist !== null && <div className={`text-[11px] ${r.liq_dist <= 0.02 ? "font-semibold text-down" : r.liq_dist <= 0.05 ? "text-warn" : "text-muted"}`}>{fmtPct(r.liq_dist, locale, 1, false)}</div>}
              </td>
              <td className={`${td} num text-right ${signClass(r.upl)}`}>
                {fmtUsd(r.upl, locale)}
                {r.roe !== null && <div className="text-[11px]">{fmtPct(r.roe, locale, 0)}</div>}
              </td>
              {extra && <td className={`${td} num text-right`}>{extra.cell(r)}</td>}
              <td className={`${td} num text-xs text-muted`}>{fmtTime(r.observed_at, locale)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One position list of the market radar, filtered by venue and cohort like the overview. */
export async function MarketBoard({ view, venue, cohort, sp, q }: { view: Exclude<View, "overview">; venue: string; cohort: string; sp: Search; q: Q }) {
  const t = await getTranslations("boards");
  const locale = await getLocale();
  const usd = (v: string) => (Number(v) ? fmtUsd(Number(v), locale) : t("any"));
  const mn = sp.mn ?? "0";
  const minNotional = (
    <Pills label={t("minNotional")} options={NOTIONALS} current={mn} render={usd} q={q} param="mn" />
  );
  const common = { venue, cohort, min_notional: Number(mn) || undefined };

  if (view === "risk") {
    const dist = sp.dist ?? "0.05";
    const side = sp.bside ?? "all";
    const d = await api.marketAtRisk({ ...common, max_dist: dist, side, limit: 300 }).catch(() => null);
    if (!d) return <Panel><p className="py-8 text-center text-sm text-muted">{t("error")}</p></Panel>;
    const sum = (within: string[], s: string) => d.buckets.filter((b) => within.includes(b.within) && b.side === s).reduce((a, b) => a + b.notional, 0);
    return (
      <>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label={t("risk.long2")} value={<span className="text-up">{fmtUsd(sum(["2"], "long"), locale)}</span>} />
          <Stat label={t("risk.short2")} value={<span className="text-down">{fmtUsd(sum(["2"], "short"), locale)}</span>} />
          <Stat label={t("risk.long5")} value={<span className="text-up">{fmtUsd(sum(["2", "5"], "long"), locale)}</span>} />
          <Stat label={t("risk.short5")} value={<span className="text-down">{fmtUsd(sum(["2", "5"], "short"), locale)}</span>} />
        </div>
        <Panel
          title={t("risk.title")}
          hint={t("risk.hint")}
          actions={
            <div className="flex flex-wrap gap-3">
              <Pills label={t("risk.within")} options={["0.02", "0.05", "0.1", "0.2"]} current={dist} render={(v) => `≤${Number(v) * 100}%`} q={q} param="dist" />
              <Pills label={t("side")} options={["all", "long", "short"]} current={side} render={(v) => t(`sides.${v}`)} q={q} param="bside" />
              {minNotional}
            </div>
          }
        >
          <PositionTable rows={d.rows} />
        </Panel>
      </>
    );
  }

  if (view === "liquidated") {
    const hours = sp.lh ?? "24";
    const d = await api.marketLiquidated({ ...common, hours, limit: 300 }).catch(() => null);
    if (!d) return <Panel><p className="py-8 text-center text-sm text-muted">{t("error")}</p></Panel>;
    const tot = (s: string) => d.totals.find((x) => x.side === s);
    const px = (v: number | null) => (v === null ? "—" : fmtNum(v, locale, v >= 100 ? 1 : 4));
    return (
      <>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label={t("liq.longs")} value={<span className="text-down">{fmtUsd(tot("long")?.notional ?? 0, locale)}</span>} sub={t("liq.n", { n: tot("long")?.positions ?? 0 })} />
          <Stat label={t("liq.shorts")} value={<span className="text-down">{fmtUsd(tot("short")?.notional ?? 0, locale)}</span>} sub={t("liq.n", { n: tot("short")?.positions ?? 0 })} />
        </div>
        <Panel
          title={t("liq.title")}
          hint={t("liq.hint")}
          actions={
            <div className="flex flex-wrap gap-3">
              <Pills label={t("window")} options={["24", "72", "168"]} current={hours} render={(v) => t("hoursN", { n: v })} q={q} param="lh" />
              {minNotional}
            </div>
          }
        >
          {d.rows.length ? (
            <div className="-mx-4 -my-4 max-h-[640px] overflow-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-panel-2">
                  <tr>
                    <th className={th}>{t("cols.trader")}</th>
                    <th className={th}>{t("cols.coin")}</th>
                    <th className={`${th} text-right`}>{t("cols.notional")}</th>
                    <th className={`${th} text-right`}>{t("cols.margin")}</th>
                    <th className={`${th} text-right`}>{t("cols.lev")}</th>
                    <th className={`${th} text-right`}>{t("liq.entry")}</th>
                    <th className={`${th} text-right`}>{t("liq.liqPx")}</th>
                    <th className={`${th} text-right`}>{t("liq.closePx")}</th>
                    <th className={th}>{t("liq.when")}</th>
                  </tr>
                </thead>
                <tbody>
                  {d.rows.map((r, i) => (
                    <tr key={i} className="border-t border-border/60">
                      <td className={td}>
                        <AddressLink address={r.address} name={r.display_name} />
                      </td>
                      <td className={td}>
                        <Badge tone={r.side === "long" ? "up" : "down"}>
                          {r.side === "long" ? "▲" : "▼"} {r.coin}
                        </Badge>{" "}
                        <Badge tone="down">{t("liq.badge")}</Badge>
                      </td>
                      <td className={`${td} num text-right`}>{fmtUsd(r.notional_change, locale)}</td>
                      <td className={`${td} num text-right text-down`}>{fmtUsd(r.margin, locale)}</td>
                      <td className={`${td} num text-right`}>{r.leverage ? `${r.leverage}×` : "—"}</td>
                      <td className={`${td} num text-right`}>{px(r.entry_px)}</td>
                      <td className={`${td} num text-right`}>{px(r.liq_px)}</td>
                      <td className={`${td} num text-right`}>{px(r.mark_px)}</td>
                      <td className={`${td} num text-xs text-muted`}>
                        {r.window_start ? t("between", { a: fmtTime(r.window_start, locale), b: fmtTime(r.event_time, locale) }) : fmtTime(r.event_time, locale)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="py-10 text-center text-sm text-muted">{t("liq.empty")}</p>
          )}
        </Panel>
      </>
    );
  }

  if (view === "opens") {
    const hours = sp.oh ?? "24";
    const big = sp.mn ?? "100000";
    const side = sp.bside ?? "all";
    const rows: MarketEvent[] = (await api.marketEvents({ venue, cohort, hours, kinds: "open,flip,increase", side, min_notional: big, limit: 300 }).catch(() => ({ rows: [] }))).rows;
    const px = (v: number | null) => (v === null ? "—" : fmtNum(v, locale, v >= 100 ? 1 : 4));
    const total = rows.reduce((a, r) => a + (r.notional_change ?? 0), 0);
    return (
      <Panel
        title={t("opens.title", { n: rows.length, ntl: fmtUsd(total, locale) })}
        hint={t("opens.hint")}
        actions={
          <div className="flex flex-wrap gap-3">
            <Pills label={t("window")} options={["1", "4", "24", "72"]} current={hours} render={(v) => t("hoursN", { n: v })} q={q} param="oh" />
            <Pills label={t("side")} options={["all", "long", "short"]} current={side} render={(v) => t(`sides.${v}`)} q={q} param="bside" />
            <Pills label={t("minNotional")} options={[100_000, 500_000, 1_000_000, 5_000_000]} current={big} render={usd} q={q} param="mn" />
          </div>
        }
      >
        {rows.length ? (
          <div className="-mx-4 -my-4 max-h-[640px] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-panel-2">
                <tr>
                  <th className={th}>{t("opens.time")}</th>
                  <th className={th}>{t("cols.trader")}</th>
                  <th className={th}>{t("cols.coin")}</th>
                  <th className={th}>{t("opens.kind")}</th>
                  <th className={`${th} text-right`}>{t("opens.amount")}</th>
                  <th className={`${th} text-right`}>{t("cols.lev")}</th>
                  <th className={`${th} text-right`}>{t("opens.entry")}</th>
                  <th className={`${th} text-right`}>{t("cols.liq")}</th>
                  <th className={`${th} text-right`}>{t("opens.now")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-t border-border/60">
                    <td className={`${td} num text-xs text-muted`}>
                      {fmtTime(r.event_time, locale)}
                      {r.time_precision === "window" && <div className="text-[10px]">{t("opens.approx")}</div>}
                    </td>
                    <td className={td}>
                      <AddressLink address={r.address} name={r.display_name} />
                    </td>
                    <td className={td}>
                      <Link href={{ pathname: "/market", query: { coin: r.coin } }}>
                        <Badge tone={r.side === "long" ? "up" : "down"}>
                          {r.side === "long" ? "▲" : "▼"} {r.coin}
                        </Badge>
                      </Link>
                    </td>
                    <td className={`${td} text-xs`}>{t(`opens.kinds.${r.kind}`)}</td>
                    <td className={`${td} num text-right font-medium`}>{fmtUsd(r.notional_change, locale)}</td>
                    <td className={`${td} num text-right`}>{r.leverage ? `${r.leverage}×` : "—"}</td>
                    <td className={`${td} num text-right`}>{px(r.entry_px)}</td>
                    <td className={`${td} num text-right text-muted`}>{px(r.liq_px)}</td>
                    <td className={`${td} num text-right ${signClass(r.upl)}`}>{r.upl === null ? "—" : fmtUsd(r.upl, locale)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-10 text-center text-sm text-muted">{t("empty")}</p>
        )}
      </Panel>
    );
  }

  if (view === "underwater") {
    const adding = sp.adding === "1";
    const d = await api.marketUnderwater({ ...common, adding_only: adding || undefined, limit: 300 }).catch(() => null);
    if (!d) return <Panel><p className="py-8 text-center text-sm text-muted">{t("error")}</p></Panel>;
    return (
      <Panel
        title={t("under.title")}
        hint={t("under.hint")}
        actions={
          <div className="flex flex-wrap gap-3">
            <Link href={{ pathname: "/market", query: q({ adding: adding ? undefined : "1" }) }} className={pill(adding)}>
              {t("under.addingOnly")}
            </Link>
            {minNotional}
          </div>
        }
      >
        <PositionTable
          rows={d.rows}
          extra={{
            head: t("under.adds"),
            cell: (r) => (r.adds_underwater ? <span className="text-warn">{t("under.addsN", { n: r.adds_underwater, ntl: fmtUsd(r.added_notional ?? 0, locale) })}</span> : <span className="text-muted">—</span>),
          }}
        />
      </Panel>
    );
  }

  // whales
  const hours = sp.wh ?? "24";
  const d = await api.marketWhales({ venue, cohort, hours, limit: 40 }).catch(() => null);
  if (!d) return <Panel><p className="py-8 text-center text-sm text-muted">{t("error")}</p></Panel>;
  const arrow = (c: number | null | undefined) => (!c ? "—" : c > 0 ? `▲ ${fmtUsd(c, locale)}` : `▼ ${fmtUsd(-c, locale)}`);
  return (
    <>
      <Panel
        title={t("whales.title")}
        hint={t("whales.hint")}
        actions={<Pills label={t("window")} options={["24", "72", "168"]} current={hours} render={(v) => t("hoursN", { n: v })} q={q} param="wh" />}
      >
        <PositionTable
          rows={d.rows}
          extra={{
            head: t("whales.change", { n: hours }),
            cell: (r) => (
              <span className={r.change ? (r.change > 0 ? "text-up" : "text-down") : "text-muted"}>
                {arrow(r.change)}
                {r.last_kind && <div className="text-[11px] text-muted">{t(`opens.kinds.${r.last_kind}`)}</div>}
              </span>
            ),
          }}
        />
      </Panel>
      {d.closed.length > 0 && (
        <Panel title={t("whales.closed", { n: hours })}>
          <ul className="space-y-1.5 text-sm">
            {d.closed.map((c, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2">
                <AddressLink address={c.address} name={c.display_name} star={false} />
                <Badge tone={c.side === "long" ? "up" : "down"}>
                  {c.side === "long" ? "▲" : "▼"} {c.coin}
                </Badge>
                <span className="num">{fmtUsd(c.notional_change, locale)}</span>
                <span className="num text-xs text-muted">{fmtTime(c.event_time, locale)}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </>
  );
}
