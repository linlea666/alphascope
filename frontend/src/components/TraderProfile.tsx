import { getLocale, getTranslations } from "next-intl/server";
import { EvidenceList, StyleBadge, fmtFactor } from "@/components/StyleEvidence";
import { Panel, td, th } from "@/components/ui";
import { api, type DiscoverProfile } from "@/lib/api";
import { fmtPct } from "@/lib/format";

const KEY_FACTORS = ["n_closed", "net_pnl", "win_rate_lo", "profit_factor", "expectancy_bp", "top1_share", "positive_month_ratio", "months", "dd_ratio", "open_loss_ratio", "liq_rate", "median_leverage", "short_share", "short_pf", "scalp_share", "top_coin_share", "hold_win_h", "hold_loss_h", "capture_avg"];

/** Style labels with their evidence, and the factor profile (full history vs last 90 days). */
export async function TraderProfile({ address }: { address: string }) {
  const t = await getTranslations("discover");
  const locale = await getLocale();
  let p: DiscoverProfile;
  try {
    p = await api.discoverProfile(address);
  } catch {
    return null;
  }
  if (!Object.keys(p.factors).length) return null;
  return (
    <Panel title={t("profileTitle")} hint={t("profileHint")}>
      {p.styles.length ? (
        <div className="mb-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {p.styles.map((s) => (
            <details key={s.style} className="rounded border border-border p-2" open={s.status === "verified"}>
              <summary className="flex cursor-pointer flex-wrap items-center gap-2 text-sm">
                <StyleBadge style={s.style} status={s.status} />
                <span className="text-xs text-muted">
                  {t(`status.${s.status}`)} · {t("confidence", { c: fmtPct(s.confidence, locale, 0, false) })}
                </span>
              </summary>
              <div className="mt-2">
                <EvidenceList evidence={s.evidence} oos={s.oos} />
              </div>
            </details>
          ))}
        </div>
      ) : (
        <p className="mb-4 text-sm text-muted">{t("noStyles")}</p>
      )}
      <div className="-mx-4 -mb-4 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-panel-2">
            <tr>
              <th className={th}>{t("cols.factor")}</th>
              <th className={`${th} text-right`}>{t("cols.all")}</th>
              <th className={`${th} text-right`}>{t("cols.d90")}</th>
              <th className={`${th} text-right`}>n</th>
            </tr>
          </thead>
          <tbody>
            {KEY_FACTORS.filter((f) => p.factors[f]).map((f) => (
              <tr key={f} className="border-t border-border/60">
                <td className={`${td} text-muted`}>{t.has(`factors.${f}`) ? t(`factors.${f}`) : f}</td>
                <td className={`${td} num text-right`}>{fmtFactor(f, p.factors[f].all?.value, locale)}</td>
                <td className={`${td} num text-right`}>{fmtFactor(f, p.factors[f]["90d"]?.value, locale)}</td>
                <td className={`${td} num text-right text-muted`}>{p.factors[f].all?.n ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
