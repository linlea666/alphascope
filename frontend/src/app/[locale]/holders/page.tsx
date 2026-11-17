import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
import { ApiErrorState } from "@/components/ApiErrorState";
import { FlagBadges } from "@/components/badges";
import { Freshness } from "@/components/Freshness";
import { ShareSlider } from "@/components/QueryHelpers";
import { SubmitButton } from "@/components/SubmitButton";
import { Term } from "@/components/Term";
import { Badge, PageHeader, Panel, Stat, td, th } from "@/components/ui";
import { VenueSwitch } from "@/components/VenueSwitch";
import { Link } from "@/i18n/routing";
import { getVenue } from "@/lib/venue";
import { api, ApiError, type HolderZones, type LegCaptureRanking, type LegList } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd, signClass } from "@/lib/format";

type Search = Record<string, string | undefined>;
const PRESETS = ["btc_long_hold", "btc_short_hold"] as const;

export default async function HoldersPage({ searchParams }: { searchParams: Promise<Search> }) {
  const sp = await searchParams;
  const tab = sp.tab === "capture" ? "capture" : "zones";
  const t = await getTranslations("holders");
  const locale = await getLocale();
  const tabClass = (active: boolean) => `px-4 py-1.5 ${active ? "bg-accent text-white" : "text-muted hover:text-text"}`;

  return (
    <div className="space-y-4">
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <VenueSwitch value={await getVenue()} />
      </PageHeader>
      <div className="flex overflow-hidden rounded border border-border text-sm" style={{ width: "fit-content" }}>
        <Link scroll={false} href={{ pathname: "/holders", query: { tab: "zones" } }} className={tabClass(tab === "zones")}>
          {t("tabZones")}
        </Link>
        <Link scroll={false} href={{ pathname: "/holders", query: { tab: "capture" } }} className={tabClass(tab === "capture")}>
          {t("tabCapture")}
        </Link>
        <Link href="/legs" className={tabClass(false)}>
          {t("tabHall")} →
        </Link>
      </div>
      {tab === "zones" ? <ZonesTab sp={sp} locale={locale} /> : <CaptureTab sp={sp} locale={locale} />}
    </div>
  );
}

async function ZonesTab({ sp, locale }: { sp: Search; locale: string }) {
  const t = await getTranslations("holders");
  const tcol = await getTranslations("cols");
  const custom = !!sp.direction;
  const preset = custom ? undefined : PRESETS.includes(sp.preset as (typeof PRESETS)[number]) ? sp.preset : "btc_long_hold";
  const near = sp.near === "1";
  const venue = await getVenue();
  let d: HolderZones;
  try {
    d = await api.holderZones(
      custom
        ? { coin: sp.coin || "BTC", direction: sp.direction, entry_min: sp.entry_min, entry_max: sp.entry_max, exit_min: sp.exit_min, exit_max: sp.exit_max, start: sp.start, end: sp.end, min_share: sp.min_share, include_near: near || undefined, venue }
        : { preset, include_near: near || undefined, venue },
    );
  } catch (e) {
    return <ApiErrorState status={e instanceof ApiError ? e.status : undefined} detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const q = d.query;
  const input = "rounded border border-border bg-panel-2 px-2 py-1 text-sm outline-none focus:border-accent";
  const presetLabel = { btc_long_hold: t("presetLong"), btc_short_hold: t("presetShort") };
  const statusTone = (s: string) => (s === "open" ? "accent" : s === "data_end" ? "warn" : "neutral");
  const tt = await getTranslations("trader");
  const qualified = d.rows.filter((r) => r.qualifies);
  const median = (xs: number[]) => {
    if (!xs.length) return null;
    const a = [...xs].sort((x, y) => x - y);
    return a.length % 2 ? a[(a.length - 1) / 2] : (a[a.length / 2 - 1] + a[a.length / 2]) / 2;
  };
  const medianMove = median(qualified.flatMap((r) => (r.move_captured === null ? [] : [r.move_captured])));
  const medianDays = median(qualified.map((r) => r.holding_days));

  return (
    <>
      <Panel hint={t("zonesHint", { share: fmtPct(q.min_share, locale, 0, false) })}>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          {PRESETS.map((p) => (
            <Link scroll={false} key={p} href={{ pathname: "/holders", query: { tab: "zones", preset: p, ...(near ? { near: "1" } : {}) } }} className={`rounded border px-3 py-1 ${preset === p ? "border-accent bg-accent text-white" : "border-border text-muted hover:text-text"}`}>
              {presetLabel[p]}
            </Link>
          ))}
          <Link scroll={false} href={{ pathname: "/holders", query: { ...sp, tab: "zones", near: near ? "0" : "1" } as Record<string, string> }} className={`rounded border px-2 py-1 text-xs ${near ? "border-accent text-accent" : "border-border text-muted"}`}>
            {t("showNear")}
          </Link>
        </div>
        <form key={JSON.stringify(q)} method="get" className="mt-4 flex flex-wrap items-end gap-2 text-sm">
          <input type="hidden" name="tab" value="zones" />
          {near && <input type="hidden" name="near" value="1" />}
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("coin")}
            <input name="coin" defaultValue={q.coin} className={`${input} w-24`} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("direction")}
            <select name="direction" defaultValue={q.direction} className={input}>
              <option value="long">{t("long")}</option>
              <option value="short">{t("short")}</option>
            </select>
          </label>
          {(["entry_min", "entry_max", "exit_min", "exit_max"] as const).map((k) => (
            <label key={k} className="flex flex-col gap-1 text-xs text-muted">
              {t({ entry_min: "entryMin", entry_max: "entryMax", exit_min: "exitMin", exit_max: "exitMax" }[k])}
              <input name={k} type="number" defaultValue={q[k]} className={`${input} w-28`} />
            </label>
          ))}
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("start")}
            <input name="start" type="date" defaultValue={q.start} className={input} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("end")}
            <input name="end" type="date" defaultValue={q.end} className={input} />
          </label>
          <ShareSlider name="min_share" defaultValue={q.min_share} label={t("minShare")} explain={t("minShareExplain", { p: "{p}" })} />
          <SubmitButton pending={t("querying")}>{t("query")}</SubmitButton>
        </form>
        <p className="mt-3 text-xs text-muted">
          {d.last_price !== null && <span className="mr-3">{t("lastPrice", { px: fmtNum(d.last_price, locale, 1) })}</span>}
          {t("coverage", { archived: fmtNum(d.coverage.archived, locale), covering: fmtNum(d.coverage.covering, locale), start: q.start, queued: fmtNum(d.coverage.queued_total, locale) })}
        </p>
      </Panel>

      <Panel title={t("qualifying", { n: d.qualifying })}>
        {d.qualifying > 0 && (
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label={t("sum.qualifying")} value={fmtNum(d.qualifying, locale)} />
            <Stat label={t("sum.holding")} value={fmtNum(qualified.filter((r) => r.status === "open").length, locale)} sub={t("sum.holdingSub")} />
            <Stat label={t("sum.medianMove")} value={<span className={signClass(medianMove)}>{fmtPct(medianMove, locale, 1)}</span>} />
            <Stat label={t("sum.medianDays")} value={fmtNum(medianDays, locale, 1)} />
          </div>
        )}
        {d.rows.length ? (
          <div className="-mx-4 -my-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-panel-2">
                <tr>
                  <th className={th}>{tcol("trader")}</th>
                  <th className={th}>{t("cols.result")}</th>
                  <th className={th}>{t("cols.opened")}</th>
                  <th className={`${th} text-right`}>{t("cols.entry")}</th>
                  <th className={`${th} text-right`}>{t("cols.zoneExit")}</th>
                  <th className={`${th} text-right`}>
                    <Term k="exitedShare">{t("cols.exitedShare")}</Term>
                  </th>
                  <th className={`${th} text-right`}>
                    <Term k="holdingShare">{t("cols.holding")}</Term>
                  </th>
                  <th className={`${th} text-right`}>
                    <Term k="moveCaptured">{t("cols.move")}</Term>
                  </th>
                  <th className={`${th} text-right`}>{t("cols.days")}</th>
                  <th className={`${th} text-right`}>{t("cols.size")}</th>
                  <th className={`${th} text-right`}>{t("cols.sizeVsEq")}</th>
                  <th className={`${th} text-right`}>{t("cols.pnl")}</th>
                  <th className={`${th} text-right`}>{t("cols.unrealized")}</th>
                </tr>
              </thead>
              <tbody>
                {d.rows.map((r) => (
                  <tr key={r.cycle_id} className={`border-t border-border/60 ${r.qualifies ? "" : "opacity-60"}`}>
                    <td className={td}>
                      {r.address ? <AddressLink address={r.address} name={r.display_name} /> : "—"}
                      {r.flags && <FlagBadges flags={r.flags} hide={["inactive_30d"]} />}
                      {r.address && r.qualifies && (
                        <Link href={{ pathname: "/sims", query: { new: 1, leader: r.address } }} className="ml-1 text-[11px] text-muted hover:text-accent" title={t("simHint")}>
                          ⧉
                        </Link>
                      )}
                    </td>
                    <td className={td}>
                      <Badge tone={r.qualifies ? "up" : "neutral"}>{r.qualifies ? t("yes") : t("no")}</Badge>{" "}
                      <Badge tone={statusTone(r.status)}>{tt(`status.${r.status}`)}</Badge>
                    </td>
                    <td className={`${td} num text-xs text-muted`}>{fmtTime(r.opened_at, locale)}</td>
                    <td className={`${td} num text-right`}>{fmtNum(r.entry_px, locale, 1)}</td>
                    <td className={`${td} num text-right`}>{r.zone_exit_px === null ? "—" : fmtNum(r.zone_exit_px, locale, 1)}</td>
                    <td className={`${td} num text-right`}>{fmtPct(r.exited_in_zone_share, locale, 0, false)}</td>
                    <td className={`${td} num text-right`}>{r.status === "open" ? fmtPct(r.holding_share, locale, 0, false) : "—"}</td>
                    <td className={`${td} num text-right ${signClass(r.move_captured)}`}>{fmtPct(r.move_captured, locale, 1)}</td>
                    <td className={`${td} num text-right`}>{fmtNum(r.holding_days, locale, 1)}</td>
                    <td className={`${td} num text-right`}>{fmtUsd(r.max_notional, locale)}</td>
                    <td className={`${td} num text-right text-muted`}>{r.size_vs_equity === null ? "—" : `${fmtNum(r.size_vs_equity, locale, 1)}×`}</td>
                    <td className={`${td} num text-right ${signClass(r.net)}`}>{fmtUsd(r.net, locale)}</td>
                    <td className={`${td} num text-right ${signClass(r.unrealized)}`}>{r.unrealized === null ? "—" : fmtUsd(r.unrealized, locale)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-10 text-center text-sm text-muted">{t("empty")}</p>
        )}
      </Panel>
    </>
  );
}

async function CaptureTab({ sp, locale }: { sp: Search; locale: string }) {
  const t = await getTranslations("holders");
  const tcol = await getTranslations("cols");
  const coin = sp.coin || "BTC";
  let legs: LegList;
  let d: LegCaptureRanking;
  try {
    legs = await api.holderLegs(coin);
    d = await api.holderCapture({
      venue: await getVenue(),
      coin,
      leg_start: sp.leg || legs.legs.find((l) => l.confirmed)?.leg_start || legs.legs[0]?.leg_start,
      min_aligned: sp.min_aligned ?? 0.5,
      min_notional: sp.min_notional ?? 10000,
      full_only: sp.full === "1" || undefined,
    });
  } catch (e) {
    return <ApiErrorState status={e instanceof ApiError ? e.status : undefined} detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const input = "rounded border border-border bg-panel-2 px-2 py-1 text-sm outline-none focus:border-accent";
  const current = d.leg_start;
  const legLabel = (l: LegList["legs"][number]) =>
    `${l.direction === "up" ? t("up") : t("down")} ${fmtPct(l.leg_change, locale, 1)} · ${l.leg_start.slice(0, 10)} → ${l.leg_end.slice(0, 10)}${l.confirmed ? "" : ` (${t("provisional")})`}`;

  if (!legs.legs.length) {
    return (
      <Panel>
        <p className="py-8 text-center text-sm text-muted">{t("noLegs")}</p>
      </Panel>
    );
  }
  return (
    <>
      <Panel hint={t("captureHint")}>
        <form key={`${coin}|${current}|${JSON.stringify(d.filters)}`} method="get" className="flex flex-wrap items-end gap-2 text-sm">
          <input type="hidden" name="tab" value="capture" />
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("coin")}
            <select name="coin" defaultValue={coin} className={input}>
              {legs.coins.map((c) => (
                <option key={c.coin} value={c.coin}>
                  {c.coin} ({c.wallets})
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("leg")}
            <select name="leg" defaultValue={current ?? ""} className={input}>
              {legs.legs.map((l) => (
                <option key={l.leg_start} value={l.leg_start}>
                  {legLabel(l)}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("minAligned")}
            <input name="min_aligned" type="number" step="0.05" min="0" max="1" defaultValue={d.filters.min_aligned} className={`${input} w-20`} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("minNotional")}
            <input name="min_notional" type="number" min="0" defaultValue={d.filters.min_notional} className={`${input} w-28`} />
          </label>
          <label className="flex items-center gap-1.5 pb-1.5 text-xs text-muted">
            <input type="checkbox" name="full" value="1" defaultChecked={d.filters.full_only} />
            {t("fullOnly")}
          </label>
          <SubmitButton pending={t("querying")}>{t("query")}</SubmitButton>
        </form>
      </Panel>
      <Panel>
        {d.rows.length ? (
          <div className="-mx-4 -my-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-panel-2">
                <tr>
                  <th className={th}>#</th>
                  <th className={th}>{tcol("trader")}</th>
                  <th className={`${th} text-right`}>
                    <Term k="capture">{t("capCols.capture")}</Term>
                  </th>
                  <th className={`${th} text-right`}>
                    <Term k="timeAligned">{t("capCols.aligned")}</Term>
                  </th>
                  <th className={`${th} text-right`}>{t("capCols.against")}</th>
                  <th className={`${th} text-right`}>{t("capCols.maxNotional")}</th>
                  <th className={`${th} text-right`}>{t("capCols.avgNotional")}</th>
                  <th className={`${th} text-right`}>{t("capCols.pnl")}</th>
                  <th className={th}>{t("capCols.coverage")}</th>
                </tr>
              </thead>
              <tbody>
                {d.rows.map((r, i) => (
                  <tr key={r.address} className="border-t border-border/60 hover:bg-panel-2">
                    <td className={`${td} num text-muted`}>{i + 1}</td>
                    <td className={td}>
                      <AddressLink address={r.address} name={r.display_name} />
                    </td>
                    <td className={`${td} num text-right font-medium ${signClass(r.capture_ratio)}`}>{r.capture_ratio === null ? "—" : fmtNum(r.capture_ratio, locale, 3)}</td>
                    <td className={`${td} num text-right`}>{fmtPct(r.time_aligned, locale, 0, false)}</td>
                    <td className={`${td} num text-right text-muted`}>{fmtPct(r.time_against, locale, 0, false)}</td>
                    <td className={`${td} num text-right`}>{fmtUsd(r.max_notional, locale)}</td>
                    <td className={`${td} num text-right`}>{fmtUsd(r.avg_aligned_notional, locale)}</td>
                    <td className={`${td} num text-right ${signClass(r.pnl_mark)}`}>{fmtUsd(r.pnl_mark, locale)}</td>
                    <td className={td}>
                      <Badge tone={r.coverage === "full" ? "up" : "warn"}>{t(r.coverage)}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-10 text-center text-sm text-muted">{t("captureEmpty")}</p>
        )}
      </Panel>
      <Freshness value={d.freshness} />
    </>
  );
}
