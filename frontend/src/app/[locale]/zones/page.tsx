import { getLocale, getTranslations } from "next-intl/server";
import { Fragment } from "react";
import { AddressLink } from "@/components/AddressLink";
import { ApiErrorState } from "@/components/ApiErrorState";
import { Freshness } from "@/components/Freshness";
import { DateQuick, RecentQueries } from "@/components/QueryHelpers";
import { SaveSentinel } from "@/components/SaveSentinel";
import { SubmitButton } from "@/components/SubmitButton";
import { Term } from "@/components/Term";
import { Badge, PageHeader, Panel, Stat, td, th } from "@/components/ui";
import { VenueSwitch } from "@/components/VenueSwitch";
import { AmountBar, Ring } from "@/components/viz";
import { ZoneBrushChart } from "@/components/ZoneBrushChart";
import { Link } from "@/i18n/routing";
import { getVenue } from "@/lib/venue";
import { api, ApiError, type Candles, type LegHall, type ZoneResult, type ZoneSide } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd, signClass } from "@/lib/format";

const PRESETS = ["btc_low_long", "btc_high_short", "btc_both"] as const;
const SORTS = ["notional", "realized", "earliest"] as const;
const ROWS = 300;

type Search = Record<string, string | undefined>;

const iso = (d: Date) => d.toISOString().slice(0, 10);
const kfmt = (v: number) => (v >= 1000 ? `${fmtNum(v / 1000, "en", 1)}k` : fmtNum(v, "en", 2));

export default async function ZonesPage({ searchParams }: { searchParams: Promise<Search> }) {
  const sp = await searchParams;
  const custom = !!sp.side;
  const preset = !custom ? (PRESETS.includes(sp.preset as (typeof PRESETS)[number]) ? sp.preset! : "btc_both") : undefined;
  const t = await getTranslations("zones");
  const tcol = await getTranslations("cols");
  const locale = await getLocale();
  const venue = await getVenue();
  const sort = SORTS.includes(sp.sort as (typeof SORTS)[number]) ? sp.sort! : "notional";

  let d: ZoneResult;
  try {
    d = await api.zones({
      venue,
      sort,
      limit: ROWS,
      ...(custom
        ? {
            coin: sp.coin || "BTC",
            side: sp.side,
            px_min: sp.px_min,
            px_max: sp.px_max,
            start: sp.start,
            end: sp.end,
            also_side: sp.also_side || undefined,
            also_px_min: sp.also_side ? sp.also_px_min : undefined,
            also_px_max: sp.also_side ? sp.also_px_max : undefined,
            also_start: sp.also_side ? sp.also_start || undefined : undefined,
            also_end: sp.also_side ? sp.also_end || undefined : undefined,
          }
        : { preset }),
    });
  } catch (e) {
    return <ApiErrorState status={e instanceof ApiError ? e.status : undefined} detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const first = d.query.first;
  const second = d.query.second;
  // the chart and wave-derived shortcuts are extras: the page works without them
  const [candles, hall]: [Candles | null, LegHall | null] = await Promise.all([
    api.candles(first.coin, "1d", "2026-01-01").catch(() => null),
    api.legsHall(first.coin, venue).catch(() => null),
  ]);

  const presetLabel = { btc_low_long: t("presetLow"), btc_high_short: t("presetHigh"), btc_both: t("presetBoth") };
  const input = "rounded border border-border bg-panel-2 px-2 py-1 text-sm outline-none focus:border-accent";
  const sideText = (s: string) => (s === "long" ? t("long") : t("short"));
  const describe = (z: typeof first) => `${z.coin} ${sideText(z.side)} · ${fmtNum(z.px_min, locale)}–${fmtNum(z.px_max, locale)} · ${z.start} → ${z.end}`;
  const chip = (z: typeof first) => (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs ${z.side === "long" ? "border-up/40 text-up" : "border-down/40 text-down"}`}>
      <span>{z.side === "long" ? "▲" : "▼"}</span>
      {z.coin} {sideText(z.side)} · {kfmt(z.px_min)}–{kfmt(z.px_max)} · {z.start.slice(5)}→{z.end.slice(5)}
    </span>
  );
  const firstQuery = { coin: first.coin, side: first.side, px_min: String(first.px_min), px_max: String(first.px_max), start: first.start, end: first.end };
  const current = Object.fromEntries(Object.entries(sp).filter((e): e is [string, string] => e[1] !== undefined));
  const pill = (active: boolean) => `rounded border px-2 py-0.5 ${active ? "border-accent text-accent" : "border-border text-muted hover:text-text"}`;

  // summary over the rows returned (all of them unless the result is larger than ROWS)
  const legs = (r: ZoneResult["rows"][number]) => [r.zone, ...(r.also ? [r.also] : [])];
  const realized = (r: ZoneResult["rows"][number]) => legs(r).reduce((a, z) => a + (z.realized_net ?? 0), 0);
  const profitable = d.rows.filter((r) => realized(r) > 0).length;
  const notional = d.rows.reduce((a, r) => a + legs(r).reduce((b, z) => b + (z.notional ?? 0), 0), 0);
  const holding = d.rows.filter((r) => legs(r).some((z) => z.open > 0)).length;
  const maxRealized = Math.max(1, ...d.rows.flatMap((r) => legs(r).map((z) => Math.abs(z.realized_net ?? 0))));
  const showUnrealized = d.rows.some((r) => legs(r).some((z) => z.unrealized));
  const covered = d.coverage.queued_total ? d.coverage.covering / d.coverage.queued_total : 0;

  const today = new Date();
  const daysAgo = (n: number) => iso(new Date(today.getTime() - n * 86400000));
  const legShortcuts = (hall?.legs ?? []).filter((l) => l.start_px).slice(0, 4);
  const dateOptions = [
    { label: t("quick.d30"), start: daysAgo(30), end: iso(today) },
    { label: t("quick.d90"), start: daysAgo(90), end: iso(today) },
    { label: t("quick.ytd"), start: `${today.getUTCFullYear()}-01-01`, end: iso(today) },
    ...legShortcuts.slice(0, 2).map((l) => ({ label: `${l.direction === "up" ? "↑" : "↓"} ${l.leg_start.slice(5, 10)}→${l.leg_end.slice(5, 10)}`, start: l.leg_start.slice(0, 10), end: l.leg_end.slice(0, 10) })),
  ];
  const points = d.rows.flatMap((r) => [
    ...(r.zone.first_at && r.zone.avg_px ? [{ t: r.zone.first_at, px: r.zone.avg_px, notional: r.zone.notional ?? 0, label: r.display_name ?? r.address, leg: 1 as const }] : []),
    ...(r.also?.first_at && r.also.avg_px ? [{ t: r.also.first_at, px: r.also.avg_px, notional: r.also.notional ?? 0, label: r.display_name ?? r.address, leg: 2 as const }] : []),
  ]);

  const sideCells = (z: ZoneSide) => (
    <>
      <td className={`${td} num text-right`}>{fmtNum(z.decisions, locale)}</td>
      <td className={`${td} num text-right`}>{fmtNum(z.avg_px, locale, 1)}</td>
      <td className={`${td} num text-right`}>{fmtUsd(z.notional, locale)}</td>
      <td className={`${td} num text-xs text-muted`}>{fmtTime(z.first_at, locale)}</td>
      <td className={`${td} text-xs`}>
        <span className="flex flex-wrap gap-1">
          {z.closed > 0 && <Badge title={t("statusHint")}>{t("statusClosed", { n: z.closed })}</Badge>}
          {z.open > 0 && <Badge tone="accent" title={t("statusHint")}>{t("statusOpen", { n: z.open })}</Badge>}
          {z.data_end > 0 && <Badge tone="warn" title={t("statusHint")}>{t("statusDataEnd", { n: z.data_end })}</Badge>}
        </span>
      </td>
      <td className={`${td} num text-right`}>{z.avg_exit_px === null ? "—" : fmtNum(z.avg_exit_px, locale, 1)}</td>
      <td className={`${td} num text-right ${signClass(z.realized_net)}`}>
        {fmtUsd(z.realized_net, locale)}
        <AmountBar value={z.realized_net} max={maxRealized} />
      </td>
      {showUnrealized && <td className={`${td} num text-right ${signClass(z.unrealized)}`}>{z.unrealized ? fmtUsd(z.unrealized, locale) : "—"}</td>}
    </>
  );

  return (
    <div className="space-y-4">
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <VenueSwitch value={venue} />
        <Freshness value={d.freshness} />
      </PageHeader>

      {candles && candles.rows.length > 0 && (
        <Panel title={t("chartTitle", { coin: first.coin })} hint={t("chartHint")}>
          <ZoneBrushChart
            candles={candles.rows}
            coin={first.coin}
            first={first}
            second={second}
            points={points}
            locale={locale}
            labels={{ first: t("firstLeg"), second: t("secondLeg"), drawHint: t("drawHint"), mode: t("drawFor"), long: t("long"), short: t("short") }}
          />
        </Panel>
      )}

      <Panel>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-xs text-muted">{t("presets")}:</span>
          {PRESETS.map((p) => (
            <Link scroll={false} key={p} href={{ pathname: "/zones", query: { preset: p, ...(sort !== "notional" ? { sort } : {}) } }} className={`rounded border px-3 py-1 ${preset === p ? "border-accent bg-accent text-white" : "border-border text-muted hover:text-text"}`}>
              {presetLabel[p]}
            </Link>
          ))}
        </div>
        {legShortcuts.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
            <span className="text-muted">{t("fromLegs")}:</span>
            {legShortcuts.map((l) => {
              const up = l.direction === "up";
              const px = l.start_px!;
              const dur = new Date(l.leg_end).getTime() - new Date(l.leg_start).getTime();
              const q = {
                coin: first.coin,
                side: up ? "long" : "short",
                px_min: String(Math.round(px * (1 - hall!.zone_pad))),
                px_max: String(Math.round(px * (1 + hall!.zone_pad))),
                start: iso(new Date(new Date(l.leg_start).getTime() - 7 * 86400000)),
                end: iso(new Date(new Date(l.leg_start).getTime() + dur * 0.5)),
              };
              return (
                <Link scroll={false} key={l.leg_start} href={{ pathname: "/zones", query: q }} className={`rounded-full border px-2.5 py-0.5 hover:bg-panel-2 ${up ? "border-up/40 text-up" : "border-down/40 text-down"}`} title={t("fromLegsHint")}>
                  {t(up ? "legBottom" : "legTop", { date: l.leg_start.slice(5, 10), px: kfmt(px) })}
                  {!l.confirmed && <span className="ml-1 text-muted">({t("provisional")})</span>}
                </Link>
              );
            })}
          </div>
        )}
        <form key={JSON.stringify(d.query)} method="get" className="mt-4 space-y-3 text-sm">
          {sort !== "notional" && <input type="hidden" name="sort" value={sort} />}
          <div className="text-xs font-medium text-muted">{t("custom")}</div>
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("coin")}
              <input name="coin" defaultValue={first.coin} className={`${input} w-24`} />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("side")}
              <select name="side" defaultValue={first.side} className={input}>
                <option value="long">{t("long")}</option>
                <option value="short">{t("short")}</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("pxMin")}
              <input name="px_min" type="number" defaultValue={first.px_min} className={`${input} w-28`} />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("pxMax")}
              <input name="px_max" type="number" defaultValue={first.px_max} className={`${input} w-28`} />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("start")}
              <input name="start" type="date" defaultValue={first.start} className={input} />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("end")}
              <input name="end" type="date" defaultValue={first.end} className={input} />
            </label>
            <DateQuick startName="start" endName="end" options={dateOptions} />
          </div>
          <div className="flex items-center gap-3 text-xs font-medium text-muted">
            {t("alsoTitle")}
            {second && (
              <Link scroll={false} href={{ pathname: "/zones", query: firstQuery }} className="font-normal text-accent hover:underline">
                {t("clearSecond")}
              </Link>
            )}
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("alsoSide")}
              <select name="also_side" defaultValue={second?.side ?? ""} className={input}>
                <option value="">—</option>
                <option value="long">{t("long")}</option>
                <option value="short">{t("short")}</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("pxMin")}
              <input name="also_px_min" type="number" defaultValue={second?.px_min} className={`${input} w-28`} />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("pxMax")}
              <input name="also_px_max" type="number" defaultValue={second?.px_max} className={`${input} w-28`} />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("start")}
              <input name="also_start" type="date" defaultValue={second?.start} className={input} />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("end")}
              <input name="also_end" type="date" defaultValue={second?.end} className={input} />
            </label>
            <DateQuick startName="also_start" endName="also_end" options={dateOptions} />
            <SubmitButton pending={t("querying")}>{t("query")}</SubmitButton>
          </div>
        </form>
        <RecentQueries storageKey="zones" title={t("recent")} current={custom ? { label: `${describe(first)}${second ? ` ∩ ${sideText(second.side)} ${kfmt(second.px_min)}–${kfmt(second.px_max)}` : ""}`, query: current } : null} />
        <div className="mt-4 flex items-center gap-3 rounded-md border border-border/60 bg-panel-2/50 px-3 py-2">
          <Ring share={covered} label={t("coverageRing")} />
          <div className="text-xs leading-relaxed text-muted">
            <span className="text-text">{t("coverageLead", { p: fmtPct(covered, locale, 0, false) })}</span>{" "}
            {t("coverage", { archived: fmtNum(d.coverage.archived, locale), covering: fmtNum(d.coverage.covering, locale), queued: fmtNum(d.coverage.queued_total, locale) })}
          </div>
        </div>
      </Panel>

      <Panel
        title={
          <span className="flex flex-wrap items-center gap-2">
            <span className="num text-lg">{t("hits", { n: fmtNum(d.total, locale) })}</span>
            {chip(first)}
            {second && (
              <>
                <span className="text-muted">∩</span>
                {chip(second)}
              </>
            )}
          </span>
        }
        actions={
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-muted">{t("sort")}:</span>
            {SORTS.map((s) => (
              <Link scroll={false} key={s} href={{ pathname: "/zones", query: { ...current, sort: s } }} className={pill(sort === s)}>
                {t(`sorts.${s}`)}
              </Link>
            ))}
            <SaveSentinel zone={{ coin: first.coin, side: first.side as "long" | "short", px_min: first.px_min, px_max: first.px_max, venue }} defaultName={describe(first)} />
          </div>
        }
      >
        {d.rows.length > 0 && (
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label={t("sum.traders")} value={fmtNum(d.total, locale)} sub={d.total > d.rows.length ? t("sum.shown", { n: d.rows.length }) : undefined} />
            <Stat label={t("sum.profitable")} value={<span className="text-up">{fmtNum(profitable, locale)}</span>} sub={fmtPct(d.rows.length ? profitable / d.rows.length : 0, locale, 0, false)} />
            <Stat label={t("sum.notional")} value={fmtUsd(notional, locale)} />
            <Stat label={t("sum.holding")} value={fmtNum(holding, locale)} sub={t("sum.holdingSub")} />
          </div>
        )}
        {d.rows.length ? (
          <div className="-mx-4 -mb-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-panel-2">
                <tr>
                  <th className={th}>{tcol("trader")}</th>
                  <th className={`${th} text-right`}>{t("size")}</th>
                  <th className={th}></th>
                  <th className={`${th} text-right`}>
                    <Term k="entries">{t("entries")}</Term>
                  </th>
                  <th className={`${th} text-right`}>{t("avgPx")}</th>
                  <th className={`${th} text-right`}>{t("notional")}</th>
                  <th className={th}>{t("firstAt")}</th>
                  <th className={th}>{t("result")}</th>
                  <th className={`${th} text-right`}>{t("avgExit")}</th>
                  <th className={`${th} text-right`}>
                    <Term k="realized" align="right">{t("realized")}</Term>
                  </th>
                  {showUnrealized && (
                    <th className={`${th} text-right`}>
                      <Term k="unrealized" align="right">{t("unrealized")}</Term>
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {d.rows.map((r, i) => {
                  const band = i % 2 ? "bg-panel-2/40" : "";
                  const okx = r.venue === "okx";
                  return (
                    <Fragment key={r.address}>
                      <tr className={`border-t border-border align-top ${band}`}>
                        <td className={td} rowSpan={r.also ? 2 : 1}>
                          <AddressLink address={r.address} name={r.display_name} />
                          <div className="mt-1 flex flex-wrap items-center gap-1">
                            {r.truncated && <Badge tone="warn">{t("truncatedNote")}</Badge>}
                            <Link href={{ pathname: "/sims", query: { new: 1, leader: r.address } }} title={t("simHint")} className="text-[11px] text-muted hover:text-accent">
                              ⧉ {t("sim")}
                            </Link>
                          </div>
                        </td>
                        <td className={`${td} num text-right`} rowSpan={r.also ? 2 : 1}>
                          {fmtUsd(okx ? r.okx_asset : r.account_value, locale)}
                          {okx && <div className="text-[11px] text-muted">{t("okxAsset")}</div>}
                          {r.macro_edge !== null && (
                            <div className={`text-xs ${signClass(r.macro_edge)}`}>
                              <Term k="macroEdge">{t("macroEdge")}</Term> {fmtNum(r.macro_edge, locale, 3)}
                            </div>
                          )}
                        </td>
                        <td className={`${td} text-xs text-muted`}>
                          {second && (
                            <span className="inline-flex items-center gap-1">
                              <span className={first.side === "long" ? "text-up" : "text-down"}>●</span>
                              {t("firstLeg")}
                            </span>
                          )}
                        </td>
                        {sideCells(r.zone)}
                      </tr>
                      {r.also && (
                        <tr className={`align-top ${band}`}>
                          <td className={`${td} text-xs text-muted`}>
                            <span className="inline-flex items-center gap-1">
                              <span className={second?.side === "long" ? "text-up" : "text-down"}>●</span>
                              {t("secondLeg")}
                            </span>
                          </td>
                          {sideCells(r.also)}
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
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
