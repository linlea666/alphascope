import { getLocale, getTranslations } from "next-intl/server";
import { Fragment } from "react";
import { AddressLink } from "@/components/AddressLink";
import { ApiErrorState } from "@/components/ApiErrorState";
import { Freshness } from "@/components/Freshness";
import { Badge, PageHeader, Panel, td, th } from "@/components/ui";
import { VenueSwitch } from "@/components/VenueSwitch";
import { Link } from "@/i18n/routing";
import { getVenue } from "@/lib/venue";
import { api, ApiError, type ZoneResult, type ZoneSide } from "@/lib/api";
import { fmtNum, fmtTime, fmtUsd, signClass } from "@/lib/format";

const PRESETS = ["btc_low_long", "btc_high_short", "btc_both"] as const;

type Search = Record<string, string | undefined>;

export default async function ZonesPage({ searchParams }: { searchParams: Promise<Search> }) {
  const sp = await searchParams;
  const custom = !!sp.side;
  const preset = !custom ? (PRESETS.includes(sp.preset as (typeof PRESETS)[number]) ? sp.preset! : "btc_both") : undefined;
  const t = await getTranslations("zones");
  const tcol = await getTranslations("cols");
  const locale = await getLocale();
  const venue = await getVenue();

  let d: ZoneResult;
  try {
    d = await api.zones({
      venue,
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
    return <ApiErrorState detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const first = d.query.first;
  const second = d.query.second;
  const presetLabel = { btc_low_long: t("presetLow"), btc_high_short: t("presetHigh"), btc_both: t("presetBoth") };
  const input = "rounded border border-border bg-panel-2 px-2 py-1 text-sm outline-none focus:border-accent";
  const describe = (z: typeof first) =>
    `${z.coin} ${z.side === "long" ? t("long") : t("short")} · ${fmtNum(z.px_min, locale)}–${fmtNum(z.px_max, locale)} · ${z.start} → ${z.end}`;

  const sideCells = (z: ZoneSide) => (
    <>
      <td className={`${td} num text-right`}>{fmtNum(z.decisions, locale)}</td>
      <td className={`${td} num text-right`}>{fmtNum(z.avg_px, locale, 1)}</td>
      <td className={`${td} num text-right`}>{fmtUsd(z.notional, locale)}</td>
      <td className={`${td} num text-xs text-muted`}>{fmtTime(z.first_at, locale)}</td>
      <td className={`${td} text-xs`}>
        <span className="flex flex-wrap gap-1">
          {z.closed > 0 && <Badge>{t("statusClosed", { n: z.closed })}</Badge>}
          {z.open > 0 && <Badge tone="accent">{t("statusOpen", { n: z.open })}</Badge>}
          {z.data_end > 0 && <Badge tone="warn">{t("statusDataEnd", { n: z.data_end })}</Badge>}
        </span>
      </td>
      <td className={`${td} num text-right`}>{z.avg_exit_px === null ? "—" : fmtNum(z.avg_exit_px, locale, 1)}</td>
      <td className={`${td} num text-right ${signClass(z.realized_net)}`}>{fmtUsd(z.realized_net, locale)}</td>
      <td className={`${td} num text-right ${signClass(z.unrealized)}`}>{z.unrealized ? fmtUsd(z.unrealized, locale) : "—"}</td>
    </>
  );
  const sideHead = (
    <>
      <th className={`${th} text-right`}>{t("entries")}</th>
      <th className={`${th} text-right`}>{t("avgPx")}</th>
      <th className={`${th} text-right`}>{t("notional")}</th>
      <th className={th}>{t("firstAt")}</th>
      <th className={th}>{t("result")}</th>
      <th className={`${th} text-right`}>{t("avgExit")}</th>
      <th className={`${th} text-right`}>{t("realized")}</th>
      <th className={`${th} text-right`}>{t("unrealized")}</th>
    </>
  );

  return (
    <div className="space-y-4">
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <VenueSwitch value={venue} />
        <Freshness value={d.freshness} />
      </PageHeader>

      <Panel>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-xs text-muted">{t("presets")}:</span>
          {PRESETS.map((p) => (
            <Link key={p} href={{ pathname: "/zones", query: { preset: p } }} className={`rounded border px-3 py-1 ${preset === p ? "border-accent bg-accent text-white" : "border-border text-muted hover:text-text"}`}>
              {presetLabel[p]}
            </Link>
          ))}
        </div>
        <form method="get" className="mt-4 space-y-3 text-sm">
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
          </div>
          <div className="text-xs font-medium text-muted">{t("alsoTitle")}</div>
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
            <button type="submit" className="rounded bg-accent px-4 py-1 text-white">
              {t("query")}
            </button>
          </div>
        </form>
        <p className="mt-4 text-xs leading-relaxed text-muted">
          {t("coverage", { archived: fmtNum(d.coverage.archived, locale), covering: fmtNum(d.coverage.covering, locale), queued: fmtNum(d.coverage.queued_total, locale) })}
        </p>
      </Panel>

      <Panel
        title={`${fmtNum(d.total, locale)} · ${describe(first)}${second ? `  ∩  ${describe(second)}` : ""}`}
      >
        {d.rows.length ? (
          <div className="-mx-4 -my-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-panel-2">
                <tr>
                  <th className={th}>{tcol("trader")}</th>
                  <th className={`${th} text-right`}>{tcol("accountValue")}</th>
                  <th className={th}></th>
                  {sideHead}
                </tr>
              </thead>
              <tbody>
                {d.rows.map((r) => (
                  <Fragment key={r.address}>
                    <tr className="border-t border-border/60 align-top">
                      <td className={td} rowSpan={r.also ? 2 : 1}>
                        <AddressLink address={r.address} name={r.display_name} />
                        {r.truncated && <div className="mt-1"><Badge tone="warn">{t("truncatedNote")}</Badge></div>}
                      </td>
                      <td className={`${td} num text-right`} rowSpan={r.also ? 2 : 1}>
                        {fmtUsd(r.account_value, locale)}
                        {r.macro_edge !== null && (
                          <div className={`text-xs ${signClass(r.macro_edge)}`}>
                            {t("macroEdge")} {fmtNum(r.macro_edge, locale, 3)}
                          </div>
                        )}
                      </td>
                      <td className={`${td} text-xs text-muted`}>{second ? t("firstLeg") : ""}</td>
                      {sideCells(r.zone)}
                    </tr>
                    {r.also && (
                      <tr className="align-top">
                        <td className={`${td} text-xs text-muted`}>{t("secondLeg")}</td>
                        {sideCells(r.also)}
                      </tr>
                    )}
                  </Fragment>
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
