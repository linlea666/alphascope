import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
import { ApiErrorState } from "@/components/ApiErrorState";
import { FlagBadges, ReliabilityBadge, TierBadge } from "@/components/badges";
import { Freshness } from "@/components/Freshness";
import { PageHeader, Panel, td, th } from "@/components/ui";
import { OkxBoard } from "@/components/OkxBoard";
import { VenueSwitch } from "@/components/VenueSwitch";
import { Link } from "@/i18n/routing";
import { getVenue } from "@/lib/venue";
import { api, ApiError, type Rankings, type Window } from "@/lib/api";
import { fmtNum, fmtPct, fmtUsd, signClass } from "@/lib/format";

const WINDOWS: Window[] = ["day", "week", "month", "alltime"];
const SORTS = ["pnl", "roi", "vlm", "account_value", "twr_30d", "mdd_30d"] as const;
const TIERS = ["B", "C", "Z"];
const PAGE_SIZE = 50;

type Search = Record<string, string | undefined>;

function pick(sp: Search) {
  const window = WINDOWS.includes(sp.window as Window) ? (sp.window as Window) : "month";
  const sort = SORTS.includes(sp.sort as (typeof SORTS)[number]) ? sp.sort! : "pnl";
  return {
    window,
    sort,
    order: sp.order === "asc" ? "asc" : "desc",
    raw: sp.raw === "1",
    enriched: sp.enriched === "1",
    tier: sp.tier && TIERS.includes(sp.tier) ? sp.tier : "",
    min_av: sp.min_av && Number(sp.min_av) >= 0 ? sp.min_av : "",
    max_av: sp.max_av && Number(sp.max_av) >= 0 ? sp.max_av : "",
    q: (sp.q ?? "").slice(0, 64),
    page: Math.max(1, Number(sp.page) || 1),
  };
}

type State = ReturnType<typeof pick>;

function query(s: State, over: Partial<State> = {}) {
  const m = { ...s, ...over };
  const q: Record<string, string> = { window: m.window, sort: m.sort, order: m.order };
  if (m.raw) q.raw = "1";
  if (m.enriched) q.enriched = "1";
  if (m.tier) q.tier = m.tier;
  if (m.min_av) q.min_av = m.min_av;
  if (m.max_av) q.max_av = m.max_av;
  if (m.q) q.q = m.q;
  if (m.page > 1) q.page = String(m.page);
  return q;
}

export default async function RankingsPage({ searchParams }: { searchParams: Promise<Search> }) {
  const sp = await searchParams;
  const s = pick(sp);
  const t = await getTranslations();
  const locale = await getLocale();
  const venue = await getVenue(false);
  if (venue === "okx") {
    return (
      <div className="space-y-4">
        <PageHeader title={t("okxBoard.title")} subtitle={t("okxBoard.subtitle")}>
          <VenueSwitch value={venue} allowAll={false} />
        </PageHeader>
        <OkxBoard sp={sp} />
      </div>
    );
  }
  let data: Rankings;
  try {
    data = await api.rankings({
      window: s.window,
      sort: s.sort,
      order: s.order,
      raw: s.raw || undefined,
      enriched_only: s.enriched || undefined,
      tier: s.tier || undefined,
      min_av: s.min_av || undefined,
      max_av: s.max_av || undefined,
      q: s.q || undefined,
      page: s.page,
      page_size: PAGE_SIZE,
    });
  } catch (e) {
    return <ApiErrorState detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const pages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));

  const sortHeader = (key: (typeof SORTS)[number], label: string) => {
    const active = s.sort === key;
    const nextOrder = active && s.order === "desc" ? "asc" : "desc";
    return (
      <Link href={{ pathname: "/rankings", query: query(s, { sort: key, order: nextOrder, page: 1 }) }} className={active ? "text-text" : "hover:text-text"}>
        {label}
        {active ? (s.order === "desc" ? " ↓" : " ↑") : ""}
      </Link>
    );
  };

  const input = "rounded border border-border bg-panel-2 px-2 py-1 text-sm outline-none focus:border-accent";

  return (
    <div className="space-y-4">
      <PageHeader title={t("rankings.title")} subtitle={t("rankings.subtitle")}>
        <div className="flex flex-col items-end gap-2">
          <VenueSwitch value={venue} allowAll={false} />
          <Freshness value={data.freshness} />
        </div>
      </PageHeader>

      <Panel>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex overflow-hidden rounded border border-border text-sm">
            {WINDOWS.map((w) => (
              <Link
                key={w}
                href={{ pathname: "/rankings", query: query(s, { window: w, page: 1 }) }}
                className={`px-3 py-1 ${s.window === w ? "bg-accent text-white" : "text-muted hover:text-text"}`}
              >
                {t(`windows.${w}`)}
              </Link>
            ))}
          </div>
          <div className="flex overflow-hidden rounded border border-border text-sm">
            {[false, true].map((raw) => (
              <Link
                key={String(raw)}
                href={{ pathname: "/rankings", query: query(s, { raw, page: 1 }) }}
                className={`px-3 py-1 ${s.raw === raw ? "bg-panel-2 text-text" : "text-muted hover:text-text"}`}
              >
                {raw ? t("rankings.raw") : t("rankings.denoised")}
              </Link>
            ))}
          </div>
        </div>

        <form method="get" className="mt-3 flex flex-wrap items-end gap-2 text-sm">
          <input type="hidden" name="window" value={s.window} />
          <input type="hidden" name="sort" value={s.sort} />
          <input type="hidden" name="order" value={s.order} />
          {s.raw && <input type="hidden" name="raw" value="1" />}
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("common.search")}
            <input name="q" defaultValue={s.q} placeholder="0x… / name" className={`${input} w-56`} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("rankings.minAv")}
            <input name="min_av" type="number" min={0} defaultValue={s.min_av} className={`${input} w-32`} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("rankings.maxAv")}
            <input name="max_av" type="number" min={0} defaultValue={s.max_av} className={`${input} w-32`} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("rankings.tier")}
            <select name="tier" defaultValue={s.tier} className={input}>
              <option value="">{t("rankings.anyTier")}</option>
              {TIERS.map((k) => (
                <option key={k} value={k}>
                  {t(`tiers.${k}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1.5 pb-1.5 text-xs text-muted">
            <input type="checkbox" name="enriched" value="1" defaultChecked={s.enriched} />
            {t("rankings.enrichedOnly")}
          </label>
          <button type="submit" className="rounded bg-accent px-3 py-1 text-sm text-white">
            {t("common.apply")}
          </button>
          <Link href={{ pathname: "/rankings", query: { window: s.window } }} className="px-2 py-1 text-xs text-muted hover:text-text">
            {t("common.reset")}
          </Link>
        </form>

        <p className="mt-3 text-xs text-muted">
          {s.raw
            ? t("rankings.rawHint")
            : t("rankings.denoisedHint", { flags: data.excluded_flags.map((f) => t(`flags.${f}`)).join(" · ") })}
          {(s.sort === "twr_30d" || s.sort === "mdd_30d") && ` ${t("rankings.sortTwrHint")}`}
        </p>
      </Panel>

      <Panel>
        {data.rows.length ? (
          <div className="-mx-4 -my-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-panel-2">
                <tr>
                  <th className={th}>{t("cols.rank")}</th>
                  <th className={th}>{t("cols.trader")}</th>
                  <th className={`${th} text-right`}>{sortHeader("account_value", t("cols.accountValue"))}</th>
                  <th className={`${th} text-right`}>
                    {sortHeader("pnl", `${t(`windows.${s.window}`)} ${t("cols.pnl")}`)} <span className="text-[10px]">({t("common.official")})</span>
                  </th>
                  <th className={`${th} text-right`}>{sortHeader("roi", t("cols.roi"))}</th>
                  <th className={`${th} text-right`}>{sortHeader("vlm", t("cols.volume"))}</th>
                  <th className={`${th} text-right`}>
                    {sortHeader("twr_30d", t("cols.twr30"))} <span className="text-[10px]">({t("common.computed")})</span>
                  </th>
                  <th className={`${th} text-right`}>{sortHeader("mdd_30d", t("cols.mdd30"))}</th>
                  <th className={th}>{t("cols.tier")}</th>
                  <th className={th}>{t("cols.flags")}</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.address} className="border-t border-border/60 hover:bg-panel-2">
                    <td className={`${td} num text-muted`}>{r.rank}</td>
                    <td className={td}>
                      <AddressLink address={r.address} name={r.display_name} />
                    </td>
                    <td className={`${td} num text-right`}>{fmtUsd(r.account_value, locale)}</td>
                    <td className={`${td} num text-right ${signClass(r.pnl)}`}>{fmtUsd(r.pnl, locale)}</td>
                    <td className={`${td} num text-right ${signClass(r.roi)}`}>{fmtPct(r.roi, locale)}</td>
                    <td className={`${td} num text-right text-muted`}>{fmtUsd(r.vlm, locale)}</td>
                    <td className={`${td} num text-right ${signClass(r.twr_30d)}`}>
                      {fmtPct(r.twr_30d, locale)} {r.reliability_30d && r.reliability_30d !== "high" && <ReliabilityBadge value={r.reliability_30d} />}
                    </td>
                    <td className={`${td} num text-right ${r.mdd_30d ? "text-down" : "text-muted"}`}>{fmtPct(r.mdd_30d, locale)}</td>
                    <td className={td}>
                      <TierBadge tier={r.tier} />
                    </td>
                    <td className={td}>
                      <FlagBadges flags={r.flags} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-10 text-center text-sm text-muted">{t("rankings.empty")}</p>
        )}
      </Panel>

      <div className="flex items-center justify-between text-sm">
        <span className="text-xs text-muted">{t("common.pageOf", { page: fmtNum(s.page, locale), pages: fmtNum(pages, locale), total: fmtNum(data.total, locale) })}</span>
        <div className="flex gap-2">
          {s.page > 1 && (
            <Link href={{ pathname: "/rankings", query: query(s, { page: s.page - 1 }) }} className="rounded border border-border px-3 py-1 hover:text-text">
              {t("common.prev")}
            </Link>
          )}
          {s.page < pages && (
            <Link href={{ pathname: "/rankings", query: query(s, { page: s.page + 1 }) }} className="rounded border border-border px-3 py-1 hover:text-text">
              {t("common.next")}
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}
