import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
import { ApiErrorState } from "@/components/ApiErrorState";
import { Term } from "@/components/Term";
import { Badge, PageHeader, Panel } from "@/components/ui";
import { VenueSwitch } from "@/components/VenueSwitch";
import { Link } from "@/i18n/routing";
import { getVenue } from "@/lib/venue";
import { api, ApiError, type LegHall, type LegList } from "@/lib/api";
import { fmtNum, fmtPct, fmtUsd, signClass } from "@/lib/format";

/** Swing-leg hall of fame: every recent macro leg of a coin, with who captured it, who entered at the turn and who
 *  sat on the wrong side. Presets for the zone / holder tools are generated from the same legs. */
export default async function LegsPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const coin = sp.coin || "BTC";
  const t = await getTranslations("legs");
  const locale = await getLocale();
  const venue = await getVenue();
  let hall: LegHall;
  let list: LegList | null;
  try {
    [hall, list] = await Promise.all([api.legsHall(coin, venue), api.holderLegs(coin).catch(() => null)]);
  } catch (e) {
    return <ApiErrorState status={e instanceof ApiError ? e.status : undefined} detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const px = (v: number | null) => (v === null ? "—" : fmtNum(v, locale, v >= 100 ? 0 : 4));
  const days = (a: string, b: string) => Math.round((new Date(b).getTime() - new Date(a).getTime()) / 86400000);

  return (
    <div className="space-y-4">
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <VenueSwitch value={venue} />
      </PageHeader>
      {list && list.coins.length > 1 && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-muted">{t("coin")}:</span>
          {list.coins.slice(0, 16).map((c) => (
            <Link key={c.coin} href={{ pathname: "/legs", query: { coin: c.coin } }} className={`rounded border px-2 py-0.5 ${c.coin === coin ? "border-accent text-accent" : "border-border text-muted hover:text-text"}`}>
              {c.coin}
            </Link>
          ))}
        </div>
      )}
      {hall.legs.length === 0 ? (
        <Panel>
          <p className="py-10 text-center text-sm text-muted">{t("empty")}</p>
        </Panel>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {hall.legs.map((l) => {
            const up = l.direction === "up";
            const tone = up ? "text-up" : "text-down";
            return (
              <section key={l.leg_start} className={`overflow-hidden rounded-lg border bg-panel ${up ? "border-up/30" : "border-down/30"}`}>
                <div className={`flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3 ${up ? "bg-up/5" : "bg-down/5"}`}>
                  <div className="flex items-center gap-3">
                    <span className={`num text-2xl font-semibold ${tone}`}>
                      {up ? "↑" : "↓"} {fmtPct(l.leg_change, locale, 1)}
                    </span>
                    <div className="text-xs leading-snug text-muted">
                      <div className="num">
                        {l.leg_start.slice(0, 10)} → {l.leg_end.slice(0, 10)} · {t("days", { n: days(l.leg_start, l.leg_end) })}
                      </div>
                      <div className="num">
                        {px(l.start_px)} → {px(l.end_px)}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {!l.confirmed && <Badge tone="warn" title={t("provisionalHint")}>{t("provisional")}</Badge>}
                    <Badge>{t("wallets", { n: l.wallets })}</Badge>
                  </div>
                </div>
                <div className="grid gap-4 p-4 md:grid-cols-3">
                  <div>
                    <h3 className="mb-2 text-xs font-medium text-muted">
                      🏆 <Term k="capture">{t("capturers")}</Term>
                    </h3>
                    <ol className="space-y-2">
                      {l.capturers.length ? (
                        l.capturers.slice(0, 3).map((c, i) => (
                          <li key={c.address} className="flex items-start gap-1.5 text-sm">
                            <span className="num w-4 text-xs text-muted">{i + 1}</span>
                            <div className="min-w-0">
                              <AddressLink address={c.address} name={c.display_name} star={false} />
                              <div className="num text-[11px] text-muted">
                                {fmtNum(c.capture_ratio, locale, 2)} · <span className={signClass(c.pnl_mark)}>{fmtUsd(c.pnl_mark, locale)}</span>
                              </div>
                            </div>
                          </li>
                        ))
                      ) : (
                        <li className="text-xs text-muted">{t("none")}</li>
                      )}
                    </ol>
                  </div>
                  <div>
                    <h3 className="mb-2 text-xs font-medium text-muted">
                      🎯 {up ? t("turnUp") : t("turnDown")} <span className="num">({l.turn.wallets})</span>
                    </h3>
                    <ul className="space-y-2">
                      {l.turn.rows.length ? (
                        l.turn.rows.map((c) => (
                          <li key={c.address} className="text-sm">
                            <AddressLink address={c.address} name={c.display_name} star={false} />
                            <div className="num text-[11px] text-muted">
                              @ {px(c.entry_px)} · {fmtUsd(c.notional, locale)}
                            </div>
                          </li>
                        ))
                      ) : (
                        <li className="text-xs text-muted">{t("none")}</li>
                      )}
                    </ul>
                  </div>
                  <div>
                    <h3 className="mb-2 text-xs font-medium text-muted">⚠️ {t("wrong")}</h3>
                    <ul className="space-y-2">
                      {l.wrong.length ? (
                        l.wrong.map((c) => (
                          <li key={c.address} className="text-sm">
                            <AddressLink address={c.address} name={c.display_name} star={false} />
                            <div className="num text-[11px] text-down">{fmtUsd(c.pnl_mark, locale)}</div>
                          </li>
                        ))
                      ) : (
                        <li className="text-xs text-muted">{t("none")}</li>
                      )}
                    </ul>
                  </div>
                </div>
                {l.query && (
                  <div className="flex flex-wrap gap-2 border-t border-border px-4 py-2.5 text-xs">
                    <Link href={{ pathname: "/holders", query: { tab: "zones", coin, ...Object.fromEntries(Object.entries(l.query).map(([k, v]) => [k, String(v)])) } }} className="rounded border border-accent px-2.5 py-1 text-accent hover:bg-accent hover:text-white">
                      {t("openHolders")} →
                    </Link>
                    <Link href={{ pathname: "/zones", query: { coin, side: l.query.direction, px_min: String(l.query.entry_min), px_max: String(l.query.entry_max), start: l.query.start, end: l.query.end } }} className="rounded border border-border px-2.5 py-1 text-muted hover:text-text">
                      {t("openZones")} →
                    </Link>
                    <Link href={{ pathname: "/holders", query: { tab: "capture", coin, leg: l.leg_start } }} className="rounded border border-border px-2.5 py-1 text-muted hover:text-text">
                      {t("openCapture")} →
                    </Link>
                  </div>
                )}
              </section>
            );
          })}
        </div>
      )}
      <p className="text-xs leading-relaxed text-muted">{t("note", { pad: fmtPct(hall.zone_pad, locale, 0, false), mn: fmtUsd(hall.min_notional, locale) })}</p>
    </div>
  );
}
