import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
import { ApiErrorState } from "@/components/ApiErrorState";
import { FlagBadges } from "@/components/badges";
import { Freshness } from "@/components/Freshness";
import { Badge, PageHeader, Panel, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { api, ApiError, type ScaleKey, type TimingBoard } from "@/lib/api";
import { fmtNum, fmtPct, fmtUsd, signClass } from "@/lib/format";

const SCALES: ScaleKey[] = ["scalp", "short", "swing", "macro"];
const WINDOWS = ["7d", "30d", "90d", "all"] as const;
const GROUPS = ["all", "btc_eth"] as const;
const SORTS = ["edge", "location", "precision", "recall", "pnl", "q"] as const;
const VALID: Record<ScaleKey, string[]> = {
  scalp: ["7d", "30d", "90d"],
  short: ["7d", "30d", "90d", "all"],
  swing: ["30d", "90d", "all"],
  macro: ["90d", "all"],
};
const PAGE_SIZE = 50;

type Search = Record<string, string | undefined>;

export default async function TimingPage({ searchParams }: { searchParams: Promise<Search> }) {
  const sp = await searchParams;
  const scale = (SCALES.includes(sp.scale as ScaleKey) ? sp.scale : "swing") as ScaleKey;
  const window = WINDOWS.includes(sp.window as (typeof WINDOWS)[number]) ? sp.window! : VALID[scale].includes("90d") ? "90d" : VALID[scale][0];
  const group = GROUPS.includes(sp.group as (typeof GROUPS)[number]) ? sp.group! : "all";
  const sort = SORTS.includes(sp.sort as (typeof SORTS)[number]) ? sp.sort! : "edge";
  const sig = sp.sig === "1";
  const small = sp.small === "1";
  const page = Math.max(1, Number(sp.page) || 1);
  const t = await getTranslations("timing");
  const tt = await getTranslations("trader");
  const tc = await getTranslations("common");
  const tcol = await getTranslations("cols");
  const locale = await getLocale();

  let d: TimingBoard;
  try {
    d = await api.timing({ scale, window, group, sort, significant_only: sig || undefined, include_insufficient: small || undefined, page, page_size: PAGE_SIZE });
  } catch (e) {
    return <ApiErrorState detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const pages = Math.max(1, Math.ceil(d.total / PAGE_SIZE));
  const q = (over: Record<string, string | number>) => {
    const base: Record<string, string> = { scale, window, group, sort };
    if (sig) base.sig = "1";
    if (small) base.small = "1";
    return { ...base, ...Object.fromEntries(Object.entries(over).map(([k, v]) => [k, String(v)])) };
  };
  const tab = (active: boolean) => `px-3 py-1 ${active ? "bg-accent text-white" : "text-muted hover:text-text"}`;
  const sortLabel: Record<string, string> = {
    edge: t("sortEdge"), location: t("sortLocation"), precision: t("sortPrecision"), recall: t("sortRecall"), pnl: t("sortPnl"), q: t("sortQ"),
  };

  return (
    <div className="space-y-4">
      <PageHeader title={t("title")} subtitle={t("subtitle", { n: 500 })}>
        <Freshness value={d.freshness} />
      </PageHeader>
      <p className="-mt-2 text-xs text-warn">{t("retro")}</p>

      <Panel>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <div className="flex overflow-hidden rounded border border-border">
            {SCALES.map((s) => (
              <Link key={s} href={{ pathname: "/timing", query: q({ scale: s, window: VALID[s].includes(window) ? window : VALID[s][0], page: 1 }) }} className={tab(s === scale)}>
                {tt(`scales.${s}`)}
              </Link>
            ))}
          </div>
          <div className="flex overflow-hidden rounded border border-border">
            {WINDOWS.map((w) => (
              <Link key={w} href={{ pathname: "/timing", query: q({ window: w, page: 1 }) }} className={`${tab(w === window)} ${VALID[scale].includes(w) ? "" : "opacity-40"}`}>
                {t(`windows.${w}`)}
              </Link>
            ))}
          </div>
          <div className="flex overflow-hidden rounded border border-border">
            {GROUPS.map((g) => (
              <Link key={g} href={{ pathname: "/timing", query: q({ group: g, page: 1 }) }} className={tab(g === group)}>
                {t(`groups.${g}`)}
              </Link>
            ))}
          </div>
          <Link href={{ pathname: "/timing", query: { ...q({ page: 1 }), sig: sig ? "0" : "1" } }} className={`rounded border px-2 py-1 text-xs ${sig ? "border-accent text-accent" : "border-border text-muted"}`}>
            {t("significantOnly")}
          </Link>
          <Link href={{ pathname: "/timing", query: { ...q({ page: 1 }), small: small ? "0" : "1" } }} className={`rounded border px-2 py-1 text-xs ${small ? "border-accent text-accent" : "border-border text-muted"}`}>
            {t("includeInsufficient")}
          </Link>
        </div>
        <div className="mt-3 flex flex-wrap gap-1 text-xs">
          <span className="py-1 text-muted">{t("sort")}:</span>
          {SORTS.map((s) => (
            <Link key={s} href={{ pathname: "/timing", query: q({ sort: s, page: 1 }) }} className={`rounded border px-2 py-1 ${s === sort ? "border-accent text-accent" : "border-border text-muted hover:text-text"}`}>
              {sortLabel[s]}
            </Link>
          ))}
        </div>
        {!d.valid && (
          <p className="mt-3 text-xs text-warn">
            {t("invalidWindow", { scale: tt(`scales.${scale}`), windows: VALID[scale].map((w) => t(`windows.${w}`)).join(" / ") })}
          </p>
        )}
      </Panel>

      <Panel>
        {d.rows.length ? (
          <div className="-mx-4 -my-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-panel-2">
                <tr>
                  <th className={th}>#</th>
                  <th className={th}>{tcol("trader")}</th>
                  <th className={`${th} text-right`}>{tcol("accountValue")}</th>
                  <th className={`${th} text-right`}>{t("decisions")}</th>
                  <th className={`${th} text-right`} title={t("locationHint")}>{t("location")} ⓘ</th>
                  <th className={`${th} text-right`} title={t("edgeHint")}>{t("edge")} ⓘ</th>
                  <th className={`${th} text-right`} title={t("qHint")}>{t("q")} ⓘ</th>
                  <th className={`${th} text-right`} title={t("precisionHint")}>{t("precision")} ⓘ</th>
                  <th className={`${th} text-right`} title={t("recallHint")}>{t("recall")} ⓘ</th>
                  <th className={`${th} text-right`} title={t("alignmentHint")}>{t("alignment")} ⓘ</th>
                  <th className={`${th} text-right`}>{t("netPnl")}</th>
                  <th className={th}></th>
                </tr>
              </thead>
              <tbody>
                {d.rows.map((r) => (
                  <tr key={r.address} className="border-t border-border/60 hover:bg-panel-2">
                    <td className={`${td} num text-muted`}>{r.rank}</td>
                    <td className={td}>
                      <AddressLink address={r.address} name={r.display_name} />
                    </td>
                    <td className={`${td} num text-right`}>{fmtUsd(r.account_value, locale)}</td>
                    <td className={`${td} num text-right`}>
                      {fmtNum(r.n_scored, locale)}
                      {r.status !== "scored" && (
                        <>
                          {" "}
                          <Badge tone="warn">{t(`status.${r.status}`)}</Badge>
                        </>
                      )}
                    </td>
                    <td className={`${td} num text-right`}>{r.location === null ? "—" : fmtNum(r.location, locale, 3)}</td>
                    <td className={`${td} num text-right ${signClass(r.edge_shrunk)}`}>{r.edge_shrunk === null ? "—" : fmtNum(r.edge_shrunk, locale, 3)}</td>
                    <td className={`${td} num text-right`}>
                      {r.q_value === null ? "—" : r.q_value < 0.1 ? <Badge tone="up">{fmtNum(r.q_value, locale, 3)}</Badge> : <span className="text-muted">{fmtNum(r.q_value, locale, 2)}</span>}
                    </td>
                    <td className={`${td} num text-right`}>{fmtPct(r.precision, locale, 0, false)}</td>
                    <td className={`${td} num text-right`}>
                      {fmtPct(r.recall, locale, 0, false)} <span className="text-xs text-muted">({r.pivots_hit}/{r.pivots})</span>
                    </td>
                    <td className={`${td} num text-right`}>{fmtPct(r.alignment, locale, 0, false)}</td>
                    <td className={`${td} num text-right ${signClass(r.net_pnl)}`}>{fmtUsd(r.net_pnl, locale)}</td>
                    <td className={td}>
                      <FlagBadges flags={r.flags} hide={["inactive_30d"]} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-10 text-center text-sm text-muted">{t("empty", { min: d.min_decisions })}</p>
        )}
      </Panel>

      <div className="flex items-center justify-between text-sm">
        <span className="text-xs text-muted">{tc("pageOf", { page, pages, total: fmtNum(d.total, locale) })}</span>
        <div className="flex gap-2">
          {page > 1 && (
            <Link href={{ pathname: "/timing", query: q({ page: page - 1 }) }} className="rounded border border-border px-3 py-1">
              {tc("prev")}
            </Link>
          )}
          {page < pages && (
            <Link href={{ pathname: "/timing", query: q({ page: page + 1 }) }} className="rounded border border-border px-3 py-1">
              {tc("next")}
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}
