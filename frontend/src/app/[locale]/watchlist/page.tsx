import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
import { AlertButton } from "@/components/AlertButton";
import { ApiErrorState } from "@/components/ApiErrorState";
import { Badge, PageHeader, Panel, td, th } from "@/components/ui";
import { WatchGroupsManager } from "@/components/WatchGroupsManager";
import { Link } from "@/i18n/routing";
import { api, ApiError, type Watchlist } from "@/lib/api";
import { getWatchContext } from "@/lib/watch";
import { fmtNum, fmtTime, fmtUsd, signClass } from "@/lib/format";

export default async function WatchlistPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const t = await getTranslations("watch");
  const locale = await getLocale();
  let d: Watchlist;
  try {
    d = await api.watchlist();
  } catch (e) {
    return <ApiErrorState detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const gid = sp.g ? Number(sp.g) : null;
  const { alerted } = await getWatchContext();
  const seen = new Set<string>();
  // "All" shows each wallet once; a group shows its own entries
  const items = d.items.filter((i) => (gid ? i.group_id === gid : !seen.has(i.address) && (seen.add(i.address), true)));
  const pill = (active: boolean) => `rounded border px-3 py-1 ${active ? "border-accent bg-accent text-white" : "border-border text-muted hover:text-text"}`;

  return (
    <div className="space-y-4">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <Panel>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Link href="/watchlist" className={pill(gid === null)}>
            {t("all")} <span className="opacity-70">{new Set(d.items.map((i) => i.address)).size}</span>
          </Link>
          {d.groups.map((g) => (
            <Link key={g.id} href={{ pathname: "/watchlist", query: { g: g.id } }} className={pill(gid === g.id)} style={g.color && gid !== g.id ? { color: g.color } : undefined}>
              {g.name} <span className="opacity-70">{g.n}</span>
            </Link>
          ))}
        </div>
        <WatchGroupsManager groups={d.groups} />
      </Panel>
      <Panel>
        {items.length ? (
          <div className="-mx-4 -my-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-panel-2">
                <tr>
                  <th className={th}>{t("cols.wallet")}</th>
                  <th className={`${th} text-right`}>{t("cols.equity")}</th>
                  <th className={`${th} text-right`}>{t("cols.pnl7")}</th>
                  <th className={`${th} text-right`}>{t("cols.pnl30")}</th>
                  <th className={th}>{t("cols.positions")}</th>
                  <th className={`${th} text-right`}>{t("cols.timing")}</th>
                  <th className={th}>{t("cols.archive")}</th>
                  <th className={th}>{t("cols.note")}</th>
                  <th className={th}>{t("cols.added")}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((i) => (
                  <tr key={i.item_id} className="border-t border-border/60 align-top hover:bg-panel-2">
                    <td className={td}>
                      <span className="flex items-start gap-2">
                        <AddressLink address={i.address} name={i.display_name} />
                        <AlertButton address={i.address} subscribed={alerted.has(i.address)} compact />
                      </span>
                    </td>
                    <td className={`${td} num text-right`}>{fmtUsd(i.account_value, locale)}</td>
                    <td className={`${td} num text-right ${signClass(i.week_pnl)}`}>{fmtUsd(i.week_pnl, locale)}</td>
                    <td className={`${td} num text-right ${signClass(i.month_pnl)}`}>{fmtUsd(i.month_pnl, locale)}</td>
                    <td className={`${td} text-xs`}>
                      <span className="flex flex-wrap gap-1">
                        {i.positions.map((p) => (
                          <Badge key={p.coin} tone={p.szi > 0 ? "up" : "down"}>
                            {p.szi > 0 ? "▲" : "▼"} {p.coin} {fmtUsd(Math.abs(p.value), locale)}
                          </Badge>
                        ))}
                      </span>
                    </td>
                    <td className={`${td} num text-right ${signClass(i.best_edge)}`}>{i.best_edge === null ? "—" : fmtNum(i.best_edge, locale, 3)}</td>
                    <td className={`${td} text-xs text-muted`}>
                      {i.n_fills ? t("archived", { n: fmtNum(i.n_fills, locale) }) : t("queued")}
                    </td>
                    <td className={`${td} max-w-xs whitespace-normal text-xs text-muted`}>
                      {i.note}
                      {i.tags.length > 0 && (
                        <span className="mt-1 flex flex-wrap gap-1">
                          {i.tags.map((tag) => (
                            <Badge key={tag}>{tag}</Badge>
                          ))}
                        </span>
                      )}
                    </td>
                    <td className={`${td} num text-xs text-muted`}>{fmtTime(i.created_at, locale, false)}</td>
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
