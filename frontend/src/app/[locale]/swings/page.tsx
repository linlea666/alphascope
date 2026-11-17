import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
import { ApiErrorState } from "@/components/ApiErrorState";
import { FlagBadges } from "@/components/badges";
import { Freshness } from "@/components/Freshness";
import { SwingChart } from "@/components/SwingChart";
import { Badge, PageHeader, Panel, Stat, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { api, ApiError, type SwingCandidates, type SwingOverview } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd, signClass } from "@/lib/format";

const SCOPES = ["candidates", "cycle", "all"] as const;
const SORTS = ["evidence", "cycle", "luck", "btc_share"] as const;
const PAGE_SIZE = 50;

type Search = Record<string, string | undefined>;

export default async function SwingsPage({ searchParams }: { searchParams: Promise<Search> }) {
  const sp = await searchParams;
  const scope = SCOPES.includes(sp.scope as (typeof SCOPES)[number]) ? sp.scope! : "candidates";
  const sort = SORTS.includes(sp.sort as (typeof SORTS)[number]) ? sp.sort! : "evidence";
  const page = Math.max(1, Number(sp.page) || 1);
  const t = await getTranslations("swings");
  const tc = await getTranslations("common");
  const tcol = await getTranslations("cols");
  const locale = await getLocale();

  let o: SwingOverview;
  let c: SwingCandidates;
  try {
    [o, c] = await Promise.all([api.swings(), api.swingCandidates({ scope, sort, page, page_size: PAGE_SIZE })]);
  } catch (e) {
    return <ApiErrorState status={e instanceof ApiError ? e.status : undefined} detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const pages = Math.max(1, Math.ceil(c.total / PAGE_SIZE));
  const firstCandle = o.candles[0]?.[0] ?? "";
  const legsShown = o.legs.filter((l) => l.end_ts >= firstCandle);
  const [upId, downId] = c.cycle_leg_ids;
  const q = (over: Record<string, string | number>) => ({ scope, sort, ...over });
  const sortLabel = { evidence: t("sortEvidence"), cycle: t("sortCycle"), luck: t("sortLuck"), btc_share: t("sortMajor") };
  const scopeLabel = { candidates: t("scopeCandidates"), cycle: t("scopeCycle"), all: t("scopeAll") };

  return (
    <div className="space-y-5">
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <Freshness value={o.freshness} />
      </PageHeader>
      <p className="-mt-2 text-xs text-warn">{t("stage")}</p>

      <Panel title={t("chartTitle", { coin: o.coin })} hint={t("chartHint", { major: fmtPct(o.thresholds.major, locale, 0, false) })}>
        <SwingChart candles={o.candles} legs={o.legs} locale={locale} label={o.coin} />
      </Panel>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Panel title={t("legsTitle")} hint={t("legsHint", { days: o.anchor_tolerance_days })}>
            <div className="-mx-4 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className={th}>{t("leg")}</th>
                    <th className={th}>{t("period")}</th>
                    <th className={`${th} text-right`}>{t("change")}</th>
                    <th className={`${th} text-right`}>{t("covered")}</th>
                    <th className={`${th} text-right`}>{t("profitableShare")}</th>
                    <th className={`${th} text-right`}>{t("medianTwr")}</th>
                  </tr>
                </thead>
                <tbody>
                  {legsShown.map((l) => (
                    <tr key={l.id} className="border-t border-border/60">
                      <td className={td}>
                        <Badge tone={l.direction === "up" ? "up" : "down"}>
                          #{l.id} {t(l.direction)}
                        </Badge>{" "}
                        {!l.confirmed && <Badge tone="warn">{t("provisional")}</Badge>}
                      </td>
                      <td className={`${td} num text-xs text-muted`}>
                        {fmtTime(l.start_ts, locale, false)} → {fmtTime(l.end_ts, locale, false)}
                        <div>
                          {fmtUsd(l.start_px, locale, false)} → {fmtUsd(l.end_px, locale, false)}
                        </div>
                      </td>
                      <td className={`${td} num text-right ${signClass(l.change)}`}>{fmtPct(l.change, locale, 1)}</td>
                      <td className={`${td} num text-right`}>{fmtNum(l.covered ?? 0, locale)}</td>
                      <td className={`${td} num text-right`}>{fmtPct(l.profitable_share, locale, 0, false)}</td>
                      <td className={`${td} num text-right ${signClass(l.median_twr)}`}>{fmtPct(l.median_twr, locale, 1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Stat label={t("screened")} value={fmtNum(o.screen.screened, locale)} />
            <Stat label={t("candidates")} value={fmtNum(o.screen.candidates, locale)} />
            <Stat label={t("cycleBoth")} value={fmtNum(o.screen.cycle_both, locale)} />
            <Stat label={t("archive")} value={fmtNum(o.fill_archive.fills, locale)} />
          </div>
          <p className="text-xs leading-relaxed text-muted">
            {t("archiveText", {
              synced: fmtNum(o.fill_archive.synced, locale),
              queued: fmtNum(o.fill_archive.queued, locale),
              fills: fmtNum(o.fill_archive.fills, locale),
              truncated: fmtNum(o.fill_archive.truncated, locale),
            })}
          </p>
        </div>
      </div>

      <Panel
        title={t("candTitle")}
        hint={t("candHint", {
          av: fmtUsd(o.criteria.min_account_value, locale),
          cycle: fmtPct(o.criteria.min_cycle_return, locale, 0, false),
          luck: fmtNum(o.criteria.max_luck_p, locale, 2),
        })}
        actions={
          <div className="flex flex-wrap gap-2 text-xs">
            <div className="flex overflow-hidden rounded border border-border">
              {SCOPES.map((s) => (
                <Link key={s} href={{ pathname: "/swings", query: q({ scope: s, page: 1 }) }} className={`px-2 py-1 ${scope === s ? "bg-accent text-white" : "text-muted hover:text-text"}`}>
                  {scopeLabel[s]}
                </Link>
              ))}
            </div>
            <div className="flex overflow-hidden rounded border border-border">
              {SORTS.map((s) => (
                <Link key={s} href={{ pathname: "/swings", query: q({ sort: s, page: 1 }) }} className={`px-2 py-1 ${sort === s ? "bg-panel-2 text-text" : "text-muted hover:text-text"}`}>
                  {sortLabel[s]}
                </Link>
              ))}
            </div>
          </div>
        }
      >
        {c.rows.length ? (
          <div className="-mx-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className={th}>#</th>
                  <th className={th}>{tcol("trader")}</th>
                  <th className={`${th} text-right`}>{tcol("accountValue")}</th>
                  <th className={`${th} text-right`} title={t("luckHint")}>
                    {t("legsWon")} · {t("luck")} ⓘ
                  </th>
                  <th className={`${th} text-right`}>{t("upLeg")}</th>
                  <th className={`${th} text-right`}>{t("downLeg")}</th>
                  <th className={`${th} text-right`} title={t("majorShareHint")}>
                    {t("majorShare")} ⓘ
                  </th>
                  <th className={th}>{t("fills")}</th>
                  <th className={th}></th>
                </tr>
              </thead>
              <tbody>
                {c.rows.map((r, i) => {
                  const up = r.legs[String(upId)]?.twr ?? null;
                  const down = r.legs[String(downId)]?.twr ?? null;
                  return (
                    <tr key={r.address} className="border-t border-border/60 hover:bg-panel-2">
                      <td className={`${td} num text-muted`}>{(page - 1) * PAGE_SIZE + i + 1}</td>
                      <td className={td}>
                        <AddressLink address={r.address} name={r.display_name} />
                      </td>
                      <td className={`${td} num text-right`}>{fmtUsd(r.account_value, locale)}</td>
                      <td className={`${td} num text-right`}>
                        {r.legs_profitable}/{r.legs_covered} · <span className="text-muted">{r.luck_p === null ? "—" : r.luck_p < 0.001 ? "<0.001" : fmtNum(r.luck_p, locale, 3)}</span>
                      </td>
                      <td className={`${td} num text-right ${signClass(up)}`}>{fmtPct(up, locale, 1)}</td>
                      <td className={`${td} num text-right ${signClass(down)}`}>{fmtPct(down, locale, 1)}</td>
                      <td className={`${td} num text-right ${signClass(r.realized.major_share)}`}>
                        {r.fill_status && r.n_fills ? fmtPct(r.realized.major_share, locale, 0) : <span className="text-muted">—</span>}
                      </td>
                      <td className={`${td} text-xs`}>
                        {!r.fill_status ? (
                          <span className="text-muted">—</span>
                        ) : r.n_fills ? (
                          <span className="num text-muted">
                            {t("fillsSince", { n: fmtNum(r.n_fills, locale), date: fmtTime(r.earliest_fill_at, locale, false) })}{" "}
                            {r.truncated && <Badge tone="warn">{t("truncated")}</Badge>}
                          </span>
                        ) : (
                          <Badge>{t("fillsQueued")}</Badge>
                        )}
                      </td>
                      <td className={td}>
                        <FlagBadges flags={r.flags} hide={["inactive_30d"]} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-8 text-center text-sm text-muted">{t("empty")}</p>
        )}
        <div className="mt-3 flex items-center justify-between text-sm">
          <span className="text-xs text-muted">{tc("pageOf", { page, pages, total: fmtNum(c.total, locale) })}</span>
          <div className="flex gap-2">
            {page > 1 && (
              <Link href={{ pathname: "/swings", query: q({ page: page - 1 }) }} className="rounded border border-border px-3 py-1">
                {tc("prev")}
              </Link>
            )}
            {page < pages && (
              <Link href={{ pathname: "/swings", query: q({ page: page + 1 }) }} className="rounded border border-border px-3 py-1">
                {tc("next")}
              </Link>
            )}
          </div>
        </div>
      </Panel>

      <Panel title={t("method")}>
        <ul className="list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
          {(t.raw("methodItems") as string[]).map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
