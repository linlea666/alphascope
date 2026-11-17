import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
import { ApiErrorState } from "@/components/ApiErrorState";
import { LiqLadder, MarketHistoryChart, MarketTreemap, PriceLadder } from "@/components/MarketCharts";
import { DivergencePanel } from "@/components/DivergencePanel";
import { Badge, PageHeader, Panel, Stat, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { api, ApiError, type MarketCoin, type MarketContributor, type MarketEvent, type MarketHistoryRow, type MarketOverview, type MarketStructure, type PriceZone } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd, signClass } from "@/lib/format";

const VENUES = ["all", "hyperliquid", "okx"] as const;
const COHORTS = ["all", "timing", "watchlist", "okx_board"] as const;
const HOURS = [1, 4, 24] as const;
const KINDS = { opens: "open,flip", changes: "open,increase,decrease,close,flip", closes: "close" } as const;

type Search = Record<string, string | undefined>;

export default async function MarketPage({ searchParams }: { searchParams: Promise<Search> }) {
  const sp = await searchParams;
  const t = await getTranslations("market");
  const locale = await getLocale();
  const venue = VENUES.includes(sp.venue as (typeof VENUES)[number]) ? sp.venue! : "all";
  const cohort = COHORTS.includes(sp.cohort as (typeof COHORTS)[number]) ? sp.cohort! : "all";
  const hours = HOURS.includes(Number(sp.hours) as (typeof HOURS)[number]) ? Number(sp.hours) : 4;
  const kind = (sp.kind && sp.kind in KINDS ? sp.kind : "opens") as keyof typeof KINDS;
  const side = sp.side === "long" || sp.side === "short" ? sp.side : "all";
  const base: Record<string, string> = { venue, cohort, hours: String(hours), kind, side };
  if (sp.coin) base.coin = sp.coin;

  let ov: MarketOverview;
  let coins: MarketCoin[];
  let events: MarketEvent[];
  try {
    [ov, { rows: coins }, { rows: events }] = await Promise.all([
      api.marketOverview(venue, cohort),
      api.marketCoins(venue, cohort),
      api.marketEvents({ venue, cohort, hours, kinds: KINDS[kind], side, coin: sp.coin, limit: 150 }),
    ]);
  } catch (e) {
    return <ApiErrorState status={e instanceof ApiError ? e.status : undefined} detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const coin = sp.coin ? coins.find((c) => c.coin === sp.coin) ?? null : null;
  let history: MarketHistoryRow[] = [];
  let st: MarketStructure | null = null;
  let contributors: MarketContributor[] = [];
  if (sp.coin) {
    [{ rows: history }, st, { rows: contributors }] = await Promise.all([
      api.marketHistory({ coin: sp.coin, venue, cohort, days: 7 }).catch(() => ({ rows: [] })),
      api.marketStructure({ coin: sp.coin, venue, cohort, min_lev: sp.minlev ? Number(sp.minlev) : 0 }).catch(() => null),
      api.marketContributors({ coin: sp.coin, venue, cohort, side: sp.cside === "long" || sp.cside === "short" ? sp.cside : "all", limit: 200, entry_min: sp.elo, entry_max: sp.ehi, liq_min: sp.llo, liq_max: sp.lhi, min_lev: sp.llo ? sp.minlev : undefined }).catch(() => ({ rows: [] })),
    ]);
  } else {
    history = (await api.marketHistory({ coin: "*", venue, cohort, days: 7 }).catch(() => ({ rows: [] }))).rows;
  }

  const T = ov.totals;
  const pill = (active: boolean) => `rounded border px-3 py-1 ${active ? "border-accent bg-accent text-white" : "border-border text-muted hover:text-text"}`;
  const ratio = (a: number, b: number) => (b ? fmtNum(a / b, locale, 2) : "—");
  const longShare = (c: MarketCoin) => (c.traders ? c.long_traders / c.traders : 0);
  const lev20 = T.long_ntl + T.short_ntl ? (T.lev20_long_ntl + T.lev20_short_ntl) / (T.long_ntl + T.short_ntl) : 0;
  const q = (over: Record<string, string | undefined>) => Object.fromEntries(Object.entries({ ...base, ...over }).filter(([, v]) => v !== undefined)) as Record<string, string>;
  const ago = (iso: string | null) => (iso ? Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000)) : null);

  return (
    <div className="space-y-4">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />

      <div className="flex flex-wrap items-center gap-2 text-sm">
        {VENUES.map((v) => (
          <Link key={v} href={{ pathname: "/market", query: q({ venue: v }) }} className={pill(v === venue)}>
            {t(`venues.${v}`)}
          </Link>
        ))}
        <span className="mx-1 h-5 w-px bg-border" />
        {COHORTS.map((c) => (
          <Link key={c} href={{ pathname: "/market", query: q({ cohort: c }) }} className={pill(c === cohort)} title={t(`cohortHints.${c}`)}>
            {t(`cohorts.${c}`)}
          </Link>
        ))}
      </div>
      <p className="text-xs text-muted">
        {t("coverage", {
          fresh: fmtNum(ov.coverage.fresh, locale),
          watched: fmtNum(ov.coverage.watched, locale),
          hl: fmtNum(ov.by_venue.hyperliquid.fresh, locale),
          okx: fmtNum(ov.by_venue.okx.fresh, locale),
          latest: ago(ov.coverage.latest) ?? "—",
        })}
      </p>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <Stat label={t("kpi.positioned")} value={fmtNum(T.traders, locale)} sub={t("kpi.positions", { n: fmtNum(T.positions, locale) })} />
        <Stat label={t("kpi.longTraders")} value={<span className="text-up">{fmtNum(T.long_traders, locale)}</span>} sub={t("kpi.netLongHint")} />
        <Stat label={t("kpi.shortTraders")} value={<span className="text-down">{fmtNum(T.short_traders, locale)}</span>} sub={t("kpi.ratio", { r: ratio(T.long_traders, T.short_traders) })} />
        <Stat label={t("kpi.longNtl")} value={<span className="text-up">{fmtUsd(T.long_ntl, locale)}</span>} />
        <Stat label={t("kpi.shortNtl")} value={<span className="text-down">{fmtUsd(T.short_ntl, locale)}</span>} sub={t("kpi.ratio", { r: ratio(T.long_ntl, T.short_ntl) })} />
        <Stat label={t("kpi.net")} value={<span className={signClass(T.long_ntl - T.short_ntl)}>{fmtUsd(T.long_ntl - T.short_ntl, locale)}</span>} />
        <Stat label={t("kpi.lev20")} value={fmtPct(lev20, locale, 1, false)} sub={t("kpi.liqKnown", { p: fmtPct(T.long_ntl + T.short_ntl ? T.liq_known_ntl / (T.long_ntl + T.short_ntl) : 0, locale, 0, false) })} />
        <Stat label={t("kpi.opens24")} value={fmtNum(ov.opens_24h, locale)} sub={t("kpi.closes", { n: fmtNum(ov.closes_24h, locale) })} />
      </div>

      <div className="grid gap-4 xl:grid-cols-5">
        <div className="xl:col-span-2">
          <Panel title={t("mapTitle")} hint={t("mapHint")}>
            {coins.length ? (
              <MarketTreemap rows={coins.slice(0, 40)} query={base} locale={locale} labels={{ longShare: t("longShare"), traders: t("cols.traders"), notional: t("cols.notional") }} />
            ) : (
              <p className="py-16 text-center text-sm text-muted">{t("empty")}</p>
            )}
          </Panel>
        </div>
        <div className="xl:col-span-3">
          <Panel title={t("coinsTitle")} hint={t("coinsHint")}>
            <div className="-mx-4 -my-4 max-h-[420px] overflow-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-panel-2">
                  <tr>
                    <th className={th}>{t("cols.coin")}</th>
                    <th className={`${th} text-right`}>{t("cols.traders")}</th>
                    <th className={th}>{t("cols.longShort")}</th>
                    <th className={`${th} text-right`}>{t("cols.longNtl")}</th>
                    <th className={`${th} text-right`}>{t("cols.shortNtl")}</th>
                    <th className={`${th} text-right`}>{t("cols.net")}</th>
                    <th className={`${th} text-right`}>{t("cols.lev20")}</th>
                    <th className={`${th} text-right`} title={t("cols.nearLiqHint")}>{t("cols.nearLiq")}</th>
                    <th className={`${th} text-right`}>{t("cols.opens")}</th>
                  </tr>
                </thead>
                <tbody>
                  {coins.map((c) => {
                    const share = longShare(c);
                    const tot = c.long_ntl + c.short_ntl;
                    return (
                      <tr key={c.coin} className={`border-t border-border/60 hover:bg-panel-2 ${c.coin === sp.coin ? "bg-panel-2" : ""}`}>
                        <td className={td}>
                          <Link href={{ pathname: "/market", query: q({ coin: c.coin }) }} className="font-medium hover:text-accent">
                            {c.coin}
                          </Link>
                        </td>
                        <td className={`${td} num text-right`}>{c.traders}</td>
                        <td className={`${td} text-xs`}>
                          <div className="flex items-center gap-2">
                            <span className="num text-up">{c.long_traders}</span>
                            <span className="relative h-1.5 w-20 overflow-hidden rounded bg-down/70">
                              <span className="absolute inset-y-0 left-0 bg-up" style={{ width: `${share * 100}%` }} />
                            </span>
                            <span className="num text-down">{c.short_traders}</span>
                          </div>
                        </td>
                        <td className={`${td} num text-right text-up`}>{fmtUsd(c.long_ntl, locale)}</td>
                        <td className={`${td} num text-right text-down`}>{fmtUsd(c.short_ntl, locale)}</td>
                        <td className={`${td} num text-right ${signClass(c.long_ntl - c.short_ntl)}`}>{fmtUsd(c.long_ntl - c.short_ntl, locale)}</td>
                        <td className={`${td} num text-right text-muted`}>{tot ? fmtPct((c.lev20_long_ntl + c.lev20_short_ntl) / tot, locale, 0, false) : "—"}</td>
                        <td className={`${td} num text-right text-xs`}>
                          <span className="text-up">{c.near_long_liq_pct === null ? "—" : `-${fmtPct(c.near_long_liq_pct, locale, 1, false)}`}</span>
                          {" / "}
                          <span className="text-down">{c.near_short_liq_pct === null ? "—" : `+${fmtPct(c.near_short_liq_pct, locale, 1, false)}`}</span>
                        </td>
                        <td className={`${td} num text-right text-xs`}>
                          {c.opens_1h} / {c.opens_4h} / {c.opens_24h}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
      </div>

      {sp.coin ? (
        <CoinDetail coin={sp.coin} row={coin} history={history} st={st} contributors={contributors} sp={sp} q={q} locale={locale} />
      ) : (
        <>
          <DivergencePanel venue={venue} coin={sp.dcoin || "BTC"} query={q} />
          <Panel title={t("historyTitle", { coin: t("allCoins") })} hint={t("historyHint")}>
            <MarketHistoryChart rows={history} locale={locale} labels={{ longs: t("longTraders"), shorts: t("shortTraders"), net: t("netNtl"), empty: t("historyEmpty") }} />
          </Panel>
        </>
      )}

      <Panel
        title={t("eventsTitle")}
        hint={t("eventsHint")}
        actions={
          <div className="flex flex-wrap gap-1 text-xs">
            {HOURS.map((h) => (
              <Link key={h} href={{ pathname: "/market", query: q({ hours: String(h) }) }} className={pill(h === hours)}>
                {t("hours", { h })}
              </Link>
            ))}
            <span className="mx-1 w-px bg-border" />
            {(Object.keys(KINDS) as (keyof typeof KINDS)[]).map((k) => (
              <Link key={k} href={{ pathname: "/market", query: q({ kind: k }) }} className={pill(k === kind)}>
                {t(`kinds.${k}`)}
              </Link>
            ))}
            <span className="mx-1 w-px bg-border" />
            {(["all", "long", "short"] as const).map((s) => (
              <Link key={s} href={{ pathname: "/market", query: q({ side: s }) }} className={pill(s === side)}>
                {t(`sides.${s}`)}
              </Link>
            ))}
          </div>
        }
      >
        <EventTable rows={events} locale={locale} />
      </Panel>
    </div>
  );
}

async function CoinDetail({ coin, row, history, st, contributors, sp, q, locale }: { coin: string; row: MarketCoin | null; history: MarketHistoryRow[]; st: MarketStructure | null; contributors: MarketContributor[]; sp: Search; q: (o: Record<string, string | undefined>) => Record<string, string>; locale: string }) {
  const t = await getTranslations("market");
  const pill = (active: boolean) => `rounded border px-2 py-0.5 ${active ? "border-accent text-accent" : "border-border text-muted hover:text-text"}`;
  const cside = sp.cside === "long" || sp.cside === "short" ? sp.cside : "all";
  const fmtPx = (v: number | null) => (v === null ? "—" : fmtNum(v, locale, v >= 100 ? 2 : 6));
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-lg font-semibold">{coin}</h2>
        {row && (
          <span className="text-sm text-muted">
            {t("coinLine", { traders: row.traders, longs: row.long_traders, shorts: row.short_traders, px: fmtPx(row.mark_px), lev: row.median_leverage === null ? "—" : fmtNum(row.median_leverage, locale, 0) })}
          </span>
        )}
        <Link href={{ pathname: "/market", query: q({ coin: undefined }) }} className="text-xs text-muted hover:text-text">
          ✕ {t("clearCoin")}
        </Link>
      </div>
      {st?.ref && st.entries && st.liquidations ? (
        <Structure st={st} coin={coin} history={history} sp={sp} q={q} locale={locale} />
      ) : (
        <Panel title={t("historyTitle", { coin })} hint={t("historyHint")}>
          <MarketHistoryChart rows={history} locale={locale} labels={{ longs: t("longTraders"), shorts: t("shortTraders"), net: t("netNtl"), empty: t("historyEmpty") }} />
        </Panel>
      )}
      <Panel
        title={t("contribTitle", { coin })}
        hint={
          sp.elo || sp.llo ? (
            <>
              {t(sp.elo ? "rangeEntry" : "rangeLiq", { lo: fmtPx(Number(sp.elo ?? sp.llo)), hi: fmtPx(Number(sp.ehi ?? sp.lhi)) })}{" "}
              <Link href={{ pathname: "/market", query: q({ elo: undefined, ehi: undefined, llo: undefined, lhi: undefined }) }} className="text-accent hover:underline">
                ✕ {t("clearRange")}
              </Link>
            </>
          ) : (
            t("contribHint")
          )
        }
        actions={
          <div className="flex gap-1 text-xs">
            {(["all", "long", "short"] as const).map((s) => (
              <Link key={s} href={{ pathname: "/market", query: q({ cside: s === "all" ? undefined : s }) }} className={pill(s === cside)}>
                {t(`sides.${s}`)}
              </Link>
            ))}
          </div>
        }
      >
        {contributors.length ? (
          <div className="-mx-4 -my-4 max-h-[480px] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-panel-2">
                <tr>
                  <th className={th}>{t("cols.trader")}</th>
                  <th className={`${th} text-right`}>{t("cols.notional")}</th>
                  <th className={`${th} text-right`}>{t("cols.entry")}</th>
                  <th className={`${th} text-right`}>{t("cols.lev")}</th>
                  <th className={`${th} text-right`}>{t("cols.liq")}</th>
                  <th className={`${th} text-right`}>{t("cols.upl")}</th>
                  <th className={th}>{t("cols.opened")}</th>
                  <th className={th}>{t("cols.observed")}</th>
                </tr>
              </thead>
              <tbody>
                {contributors.map((r) => (
                  <tr key={`${r.address}`} className="border-t border-border/60 hover:bg-panel-2">
                    <td className={td}>
                      <span className="flex items-start gap-2">
                        <Badge tone={r.szi > 0 ? "up" : "down"}>{r.szi > 0 ? "▲" : "▼"}</Badge>
                        <AddressLink address={r.address} name={r.display_name} />
                      </span>
                    </td>
                    <td className={`${td} num text-right`}>{fmtUsd(r.notional_usd, locale)}</td>
                    <td className={`${td} num text-right`}>{fmtPx(r.entry_px)}</td>
                    <td className={`${td} num text-right`}>
                      {r.leverage ? `${r.leverage}×` : "—"}
                      {r.margin_mode && <div className="text-[11px] text-muted">{t(`modes.${r.margin_mode === "isolated" ? "isolated" : "cross"}`)}</div>}
                    </td>
                    <td className={`${td} num text-right text-muted`}>{fmtPx(r.liq_px)}</td>
                    <td className={`${td} num text-right ${signClass(r.upl)}`}>{fmtUsd(r.upl, locale)}</td>
                    <td className={`${td} num text-xs text-muted`}>
                      {r.opened_at ? fmtTime(r.opened_at, locale) : r.opened_at_source === "window" && r.opened_after ? t("openedAfter", { t: fmtTime(r.opened_after, locale) }) : t("openedUnknown")}
                    </td>
                    <td className={`${td} num text-xs text-muted`}>{fmtTime(r.observed_at, locale)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted">{t("empty")}</p>
        )}
      </Panel>
    </div>
  );
}

async function EventTable({ rows, locale }: { rows: MarketEvent[]; locale: string }) {
  const t = await getTranslations("market");
  if (!rows.length) return <p className="py-8 text-center text-sm text-muted">{t("noEvents")}</p>;
  const fmtPx = (v: number | null) => (v === null ? "—" : fmtNum(v, locale, v >= 100 ? 2 : 6));
  return (
    <div className="-mx-4 -my-4 max-h-[560px] overflow-auto">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-panel-2">
          <tr>
            <th className={th}>{t("cols.time")}</th>
            <th className={th}>{t("cols.trader")}</th>
            <th className={th}>{t("cols.event")}</th>
            <th className={`${th} text-right`}>{t("cols.change")}</th>
            <th className={`${th} text-right`}>{t("cols.entry")}</th>
            <th className={`${th} text-right`}>{t("cols.now")}</th>
            <th className={`${th} text-right`}>{t("cols.lev")}</th>
            <th className={`${th} text-right`}>{t("cols.liq")}</th>
            <th className={th} />
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.id} className="border-t border-border/60 hover:bg-panel-2">
              <td className={`${td} num text-xs`}>
                {fmtTime(e.event_time, locale)}
                <div className="text-[11px] text-muted" title={e.time_precision === "window" && e.window_start ? t("windowHint", { a: fmtTime(e.window_start, locale), b: fmtTime(e.observed_at, locale) }) : undefined}>
                  {e.time_precision === "exact" ? t("precision.exact") : e.window_start ? t("precision.window") : t("precision.firstSeen")}
                </div>
              </td>
              <td className={td}>
                <AddressLink address={e.address} name={e.display_name} />
              </td>
              <td className={`${td} text-xs`}>
                <Badge tone={e.kind === "close" || e.kind === "decrease" ? "neutral" : e.side === "long" ? "up" : "down"}>
                  {t(`eventKinds.${e.kind}`)} {e.side === "long" ? t("long") : t("short")}
                </Badge>{" "}
                <span className="font-medium">{e.coin}</span>
              </td>
              <td className={`${td} num text-right`}>{fmtUsd(e.notional_change, locale)}</td>
              <td className={`${td} num text-right`}>{fmtPx(e.entry_px)}</td>
              <td className={`${td} num text-right text-muted`}>{fmtPx(e.current_px ?? e.mark_px)}</td>
              <td className={`${td} num text-right`}>{e.leverage ? `${e.leverage}×` : "—"}</td>
              <td className={`${td} num text-right text-muted`}>{fmtPx(e.liq_px)}</td>
              <td className={`${td} text-xs`}>
                <Link href={{ pathname: `/trader/${e.address}`, query: { rc: e.coin, ri: "1h", re: e.event_time } }} className="text-accent hover:underline">
                  {t("replay")}
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

async function Structure({ st, coin, history, sp, q, locale }: { st: MarketStructure; coin: string; history: MarketHistoryRow[]; sp: Search; q: (o: Record<string, string | undefined>) => Record<string, string>; locale: string }) {
  const t = await getTranslations("market");
  const E = st.entries!;
  const L = st.liquidations!;
  const ref = st.ref!.px;
  const fmtPx = (v: number | null | undefined) => (v === null || v === undefined ? "—" : fmtNum(v, locale, v >= 100 ? 0 : v >= 1 ? 3 : 6));
  const dist = (v: number | null | undefined) => (v === null || v === undefined ? "" : fmtPct(v, locale, 1));
  const pill = (active: boolean) => `rounded border px-2 py-0.5 ${active ? "border-accent text-accent" : "border-border text-muted hover:text-text"}`;
  const ladderLabels = { longs: t("longs"), shorts: t("shorts"), price: t("price"), zone: t("zone"), empty: t("liqEmpty") };
  const nearest = (side: "long" | "short") => {
    const n = L[side].nearest;
    return n ? (
      <span>
        {fmtPx(n.px)} <span className="text-xs text-muted">{dist(n.distance)}</span>
      </span>
    ) : (
      "—"
    );
  };
  const zoneRows = (zones: PriceZone[], side: "long" | "short", kind: "entry" | "liq") =>
    zones.map((z, i) => (
      <tr key={`${kind}-${side}-${i}`} className="border-t border-border/60">
        <td className={`${td} text-xs`}>
          <Badge tone={side === "long" ? "up" : "down"}>
            {t(kind === "entry" ? "zoneEntry" : "zoneLiq")} · {t(side === "long" ? "long" : "short")}
          </Badge>
        </td>
        <td className={`${td} num`}>
          {fmtPx(z.lo)} – {fmtPx(z.hi)}
        </td>
        <td className={`${td} num text-right text-xs ${signClass(-Math.abs(z.distance))}`}>{dist(z.distance)}</td>
        <td className={`${td} num text-right`}>{fmtUsd(z.notional, locale)}</td>
        <td className={`${td} num text-right text-muted`}>{fmtPct(z.share, locale, 0, false)}</td>
        <td className={`${td} num text-right`}>
          {z.positions}
          {z.positions <= 2 && (
            <span className="ml-1" title={t("whaleHint")}>
              <Badge tone="warn">{t("whale")}</Badge>
            </span>
          )}
        </td>
        <td className={`${td} num text-right text-muted`}>{z.avg_lev ? `${fmtNum(z.avg_lev, locale, 1)}×` : "—"}</td>
        <td className={`${td} text-xs`}>{kind === "entry" ? (z.in_profit ? <span className="text-up">{t("inProfit")}</span> : <span className="text-down">{t("underwater")}</span>) : ""}</td>
        <td className={`${td} text-xs`}>
          <Link href={{ pathname: "/market", query: q(kind === "entry" ? { cside: side, elo: String(z.lo), ehi: String(z.hi), llo: undefined, lhi: undefined } : { cside: side, llo: String(z.lo), lhi: String(z.hi), elo: undefined, ehi: undefined }) }} className="text-accent hover:underline">
            {t("drill")}
          </Link>
        </td>
      </tr>
    ));
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <Stat label={t("st.longCost")} value={fmtPx(E.long.avg_entry)} sub={dist(E.long.avg_distance)} />
        <Stat label={t("st.shortCost")} value={fmtPx(E.short.avg_entry)} sub={dist(E.short.avg_distance)} />
        <Stat label={t("st.longUnder")} value={<span className="text-down">{fmtPct(E.long.underwater_share, locale, 0, false)}</span>} sub={t("st.top5", { p: fmtPct(E.long.top5_share, locale, 0, false) })} />
        <Stat label={t("st.shortUnder")} value={<span className="text-down">{fmtPct(E.short.underwater_share, locale, 0, false)}</span>} sub={t("st.top5", { p: fmtPct(E.short.top5_share, locale, 0, false) })} />
        <Stat label={t("st.nearLong")} value={nearest("long")} sub={L.long.nearest ? `${fmtUsd(L.long.nearest.notional, locale)} · ${L.long.nearest.leverage ?? "—"}×` : undefined} />
        <Stat label={t("st.nearShort")} value={nearest("short")} sub={L.short.nearest ? `${fmtUsd(L.short.nearest.notional, locale)} · ${L.short.nearest.leverage ?? "—"}×` : undefined} />
        <Stat label={t("st.clusterLong")} value={L.long.nearest_cluster ? `${fmtPx(L.long.nearest_cluster.lo)}–${fmtPx(L.long.nearest_cluster.hi)}` : "—"} sub={L.long.nearest_cluster ? `${dist(L.long.nearest_cluster.distance)} · ${fmtUsd(L.long.nearest_cluster.notional, locale)}` : undefined} />
        <Stat label={t("st.clusterShort")} value={L.short.nearest_cluster ? `${fmtPx(L.short.nearest_cluster.lo)}–${fmtPx(L.short.nearest_cluster.hi)}` : "—"} sub={L.short.nearest_cluster ? `${dist(L.short.nearest_cluster.distance)} · ${fmtUsd(L.short.nearest_cluster.notional, locale)}` : undefined} />
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title={t("entryTitle", { coin })} hint={t("entryHint", { px: fmtPx(ref), t: fmtTime(st.ref!.observed_at, locale) })}>
          <PriceLadder long={E.long.bins} short={E.short.bins} longZones={E.long.zones} shortZones={E.short.zones} refPx={ref} locale={locale} labels={{ ...ladderLabels, empty: t("empty") }} />
        </Panel>
        <Panel
          title={t("liqTitle", { coin })}
          hint={t("liqHint2", {
            known: fmtPct(L.long.notional + L.short.notional ? ((L.long.known_share ?? 0) * L.long.notional + (L.short.known_share ?? 0) * L.short.notional) / (L.long.notional + L.short.notional) : 0, locale, 0, false),
            without: fmtNum(L.long.without_liq.positions + L.short.without_liq.positions, locale),
            crossed: fmtNum(L.long.crossed.positions + L.short.crossed.positions, locale),
            cross: fmtPct(L.long.beyond_notional + L.short.beyond_notional ? ((L.long.cross_share ?? 0) * L.long.beyond_notional + (L.short.cross_share ?? 0) * L.short.beyond_notional) / (L.long.beyond_notional + L.short.beyond_notional) : 0, locale, 0, false),
          })}
          actions={
            <div className="flex gap-1 text-xs">
              {[0, 10, 20].map((l) => (
                <Link key={l} href={{ pathname: "/market", query: q({ minlev: l ? String(l) : undefined }) }} className={pill(Number(sp.minlev || 0) === l)}>
                  {l ? `≥${l}×` : t("allLev")}
                </Link>
              ))}
            </div>
          }
        >
          <PriceLadder long={L.long.bins} short={L.short.bins} longZones={L.long.zones} shortZones={L.short.zones} refPx={ref} locale={locale} labels={ladderLabels} />
        </Panel>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title={t("ladderTitle")} hint={t("ladderHint")}>
          <LiqLadder long={L.long.ladder} short={L.short.ladder} locale={locale} labels={{ longs: t("longLiq"), shorts: t("shortLiq"), empty: t("liqEmpty") }} />
        </Panel>
        <Panel title={t("historyTitle", { coin })} hint={t("historyHint")}>
          <MarketHistoryChart rows={history} locale={locale} labels={{ longs: t("longTraders"), shorts: t("shortTraders"), net: t("netNtl"), empty: t("historyEmpty") }} />
        </Panel>
      </div>
      <Panel title={t("zonesTitle")} hint={t("zonesHint")}>
        <div className="-mx-4 -my-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-panel-2">
              <tr>
                <th className={th}>{t("zcols.kind")}</th>
                <th className={th}>{t("zcols.range")}</th>
                <th className={`${th} text-right`}>{t("zcols.distance")}</th>
                <th className={`${th} text-right`}>{t("cols.notional")}</th>
                <th className={`${th} text-right`}>{t("zcols.share")}</th>
                <th className={`${th} text-right`}>{t("zcols.positions")}</th>
                <th className={`${th} text-right`}>{t("zcols.lev")}</th>
                <th className={th}>{t("zcols.pnl")}</th>
                <th className={th} />
              </tr>
            </thead>
            <tbody>
              {zoneRows(E.long.zones, "long", "entry")}
              {zoneRows(E.short.zones, "short", "entry")}
              {zoneRows(L.long.zones, "long", "liq")}
              {zoneRows(L.short.zones, "short", "liq")}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
