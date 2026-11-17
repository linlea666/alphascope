import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
import { StyleBadge } from "@/components/StyleEvidence";
import { Panel } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { api, type SimilarTraders as Similar, type StyleKey } from "@/lib/api";
import { fmtNum, fmtPct, fmtUsd, signClass } from "@/lib/format";

/** Traders who trade most like this one (style factors, not results), each one click away from a comparison. */
export async function SimilarTraders({ address }: { address: string }) {
  const t = await getTranslations("similar");
  const tf = await getTranslations("discover");
  const locale = await getLocale();
  let d: Similar;
  try {
    d = await api.similar(address);
  } catch {
    return null;
  }
  if (!d.rows.length) return null;
  const factor = (f: string) => (tf.has(`factors.${f}`) ? tf(`factors.${f}`) : f);
  return (
    <Panel title={t("title")} hint={t("hint", { n: d.min_closed })}>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {d.rows.map((r) => (
          <div key={r.address} className="rounded-lg border border-border p-3">
            <div className="flex items-start justify-between gap-2">
              <AddressLink address={r.address} name={r.display_name} />
              <span className="num rounded-full bg-accent/15 px-2 py-0.5 text-xs text-accent">{fmtPct(r.similarity, locale, 0, false)}</span>
            </div>
            <div className="mt-2 text-[11px] text-muted">{t("closest", { f: r.closest.map(factor).join(" · ") })}</div>
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
              <span className="text-muted">{t("closed", { n: fmtNum(r.n_closed, locale) })}</span>
              <span className={`num ${signClass(r.net_pnl)}`}>{fmtUsd(r.net_pnl, locale)}</span>
              {r.styles.slice(0, 2).map((s) => (
                <StyleBadge key={s} style={s as StyleKey} />
              ))}
            </div>
            <Link href={{ pathname: "/compare", query: { addr: [address, r.address] } }} className="mt-2 inline-block text-xs text-accent hover:underline">
              {t("compare")} →
            </Link>
          </div>
        ))}
      </div>
    </Panel>
  );
}
