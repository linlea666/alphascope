import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
import { ApiErrorState } from "@/components/ApiErrorState";
import { EvidenceList, StyleBadge, fmtFactor } from "@/components/StyleEvidence";
import { Badge, PageHeader, Panel, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { api, ApiError, type DiscoverBoard, type DiscoverStyles, type StyleKey, type StyleStatus } from "@/lib/api";
import { fmtPct, fmtTime } from "@/lib/format";

const VENUES = ["all", "hyperliquid", "okx"] as const;
const STATUSES: StyleStatus[] = ["verified", "candidate", "watch"];
const SORTS = ["score", "confidence", "net_pnl"] as const;
const COLUMNS = ["n_closed", "net_pnl", "win_rate_lo", "profit_factor", "positive_month_ratio", "dd_ratio", "open_loss_ratio", "liq_rate", "median_leverage", "days_since_last"];

export default async function DiscoverPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const t = await getTranslations("discover");
  const locale = await getLocale();
  const venue = VENUES.includes(sp.venue as (typeof VENUES)[number]) ? sp.venue! : "all";
  const status = (STATUSES.includes(sp.status as StyleStatus) ? sp.status : "candidate") as StyleStatus;
  const sort = SORTS.includes(sp.sort as (typeof SORTS)[number]) ? sp.sort! : "score";
  let styles: DiscoverStyles;
  let board: DiscoverBoard;
  try {
    styles = await api.discoverStyles(venue);
    const style = (styles.styles.some((s) => s.key === sp.style) ? sp.style : "steady") as StyleKey;
    board = await api.discoverBoard({ style, venue, status, sort, limit: 150 });
  } catch (e) {
    return <ApiErrorState status={e instanceof ApiError ? e.status : undefined} detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const style = board.style;
  const meta = styles.styles.find((s) => s.key === style)!;
  const q = (over: Record<string, string>) => ({ style, venue, status, sort, ...over });
  const pill = (active: boolean) => `rounded border px-3 py-1 ${active ? "border-accent bg-accent text-white" : "border-border text-muted hover:text-text"}`;

  return (
    <div className="space-y-4">
      <PageHeader title={t("title")} subtitle={t("subtitle", { days: styles.oos_days })} />
      <div className="flex flex-wrap gap-2 text-sm">
        {styles.styles.map((s) => {
          const good = (s.counts.verified ?? 0) + (s.counts.candidate ?? 0);
          return (
            <Link scroll={false} key={s.key} href={{ pathname: "/discover", query: q({ style: s.key }) }} className={`${pill(s.key === style)} ${s.risk_flag && s.key !== style ? "border-down/40 text-down" : ""}`}>
              {t(`styles.${s.key}`)}
              <span className="ml-1.5 text-xs opacity-80">
                {s.counts.verified ? `${s.counts.verified}✓ ` : ""}
                {good}
              </span>
            </Link>
          );
        })}
      </div>
      <Panel
        title={t(`styles.${style}`)}
        hint={t(`styleHints.${style}`)}
        actions={
          <div className="flex flex-wrap gap-1 text-xs">
            {VENUES.map((v) => (
              <Link scroll={false} key={v} href={{ pathname: "/discover", query: q({ venue: v }) }} className={pill(v === venue)}>
                {t(`venues.${v}`)}
              </Link>
            ))}
            <span className="mx-1 w-px bg-border" />
            {STATUSES.map((s) => (
              <Link scroll={false} key={s} href={{ pathname: "/discover", query: q({ status: s }) }} className={pill(s === status)} title={t(`statusHints.${s}`)}>
                {t(`statusFilter.${s}`)}
              </Link>
            ))}
            <span className="mx-1 w-px bg-border" />
            {SORTS.map((s) => (
              <Link scroll={false} key={s} href={{ pathname: "/discover", query: q({ sort: s }) }} className={pill(s === sort)}>
                {t(`sorts.${s}`)}
              </Link>
            ))}
          </div>
        }
      >
        <p className="mb-3 text-xs text-muted">
          {meta.risk_flag ? t("riskNote") : meta.oos ? t("oosNote", { days: styles.oos_days }) : t("noOosNote")}
          {meta.note === "copy_hard" ? ` ${t("copyHard")}` : ""} {styles.computed_at ? t("computedAt", { t: fmtTime(styles.computed_at, locale) }) : ""}
        </p>
        {board.rows.length ? (
          <div className="-mx-4 -mb-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-panel-2">
                <tr>
                  <th className={th}>{t("cols.trader")}</th>
                  <th className={th}>{t("cols.status")}</th>
                  {COLUMNS.map((c) => (
                    <th key={c} className={`${th} text-right`}>
                      {t(`factorsShort.${c}`)}
                    </th>
                  ))}
                  <th className={th}>{t("cols.other")}</th>
                </tr>
              </thead>
              <tbody>
                {board.rows.map((r) => (
                  <tr key={r.address} className="border-t border-border/60 align-top hover:bg-panel-2">
                    <td className={td}>
                      <AddressLink address={r.address} name={r.display_name} />
                    </td>
                    <td className={`${td} text-xs`}>
                      <Badge tone={r.status === "verified" ? "up" : r.status === "candidate" ? "accent" : "neutral"}>{t(`status.${r.status}`)}</Badge>
                      <div className="mt-1 text-[11px] text-muted">{t("confidence", { c: fmtPct(r.confidence, locale, 0, false) })}</div>
                      <details className="mt-1">
                        <summary className="cursor-pointer text-[11px] text-accent">{t("evidence")}</summary>
                        <div className="mt-2 w-72 whitespace-normal rounded border border-border bg-panel p-2">
                          <EvidenceList evidence={r.evidence} oos={r.oos} />
                        </div>
                      </details>
                    </td>
                    {COLUMNS.map((c) => (
                      <td key={c} className={`${td} num text-right ${c === "open_loss_ratio" && (r.factors[c]?.value ?? 0) > 0.15 ? "text-down" : ""}`}>
                        {fmtFactor(c, r.factors[c]?.value, locale)}
                      </td>
                    ))}
                    <td className={`${td} whitespace-normal`}>
                      <span className="flex max-w-[220px] flex-wrap gap-1">
                        {r.other.map((o) => (
                          <StyleBadge key={o} style={o} />
                        ))}
                      </span>
                    </td>
                  </tr>
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
