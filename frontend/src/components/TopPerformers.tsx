import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
import { StyleBadge } from "@/components/StyleEvidence";
import { Panel, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { api, type DiscoverTop, type TopRow } from "@/lib/api";
import { fmtPct, fmtUsd, signClass } from "@/lib/format";

/** The week's / month's best performers per venue, after sample and risk gates: short-term results, not skill. */
export async function TopPerformers({ period }: { period: "7d" | "30d" }) {
  const t = await getTranslations("top");
  const locale = await getLocale();
  let d: DiscoverTop;
  try {
    d = await api.discoverTop(period);
  } catch {
    return null;
  }
  const pill = (active: boolean) => `rounded border px-2 py-0.5 ${active ? "border-accent text-accent" : "border-border text-muted hover:text-text"}`;
  const table = (rows: TopRow[], venue: "okx" | "hyperliquid") => (
    <div>
      <h3 className="mb-2 text-xs font-medium text-muted">{t(`venue.${venue}`)}</h3>
      {rows.length ? (
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className={`${th} pl-0`}>{t("trader")}</th>
              <th className={`${th} text-right`}>{t("ret")}</th>
              <th className={`${th} text-right`}>{t("pnl")}</th>
              <th className={`${th} pr-0 text-right`}>{venue === "okx" ? t("closed") : t("mdd")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.address} className="border-t border-border/60 align-top">
                <td className={`${td} pl-0`}>
                  <AddressLink address={r.address} name={r.display_name} />
                  {r.styles.length > 0 && (
                    <span className="mt-1 flex flex-wrap gap-1">
                      {r.styles.slice(0, 3).map((s) => (
                        <StyleBadge key={s} style={s} />
                      ))}
                    </span>
                  )}
                </td>
                <td className={`${td} num text-right ${signClass(r.ret)}`}>{fmtPct(r.ret, locale)}</td>
                <td className={`${td} num text-right ${signClass(r.net)}`}>{fmtUsd(r.net, locale)}</td>
                <td className={`${td} num pr-0 text-right text-muted`}>{venue === "okx" ? (r.n ?? "—") : fmtPct(r.mdd ?? null, locale)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="py-6 text-center text-sm text-muted">{t("empty")}</p>
      )}
    </div>
  );
  return (
    <Panel
      title={t(period === "7d" ? "titleWeek" : "titleMonth")}
      hint={t("hint")}
      actions={
        <div className="flex gap-1 text-xs">
          {(["7d", "30d"] as const).map((p) => (
            <Link scroll={false} key={p} href={{ pathname: "/", query: { top: p } }} className={pill(p === period)}>
              {t(`periods.${p}`)}
            </Link>
          ))}
        </div>
      }
    >
      <div className="grid gap-6 lg:grid-cols-2">
        {table(d.okx, "okx")}
        {table(d.hyperliquid, "hyperliquid")}
      </div>
    </Panel>
  );
}
