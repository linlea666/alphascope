import { getLocale, getTranslations } from "next-intl/server";
import { Badge, Panel, td, th } from "@/components/ui";
import type { CopyValidation as Report } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, signClass } from "@/lib/format";

const GRADES = ["A", "B", "C", "D"] as const;

/** Whether the grades shown on the board were right before: frozen daily, then measured by the copy that followed. */
export async function CopyValidation({ report }: { report: Report }) {
  const t = await getTranslations("copyBoard.validation");
  const locale = await getLocale();
  return (
    <Panel title={t("title")} hint={t("hint")}>
      <div className="grid gap-4 md:grid-cols-2">
        {report.horizons.map((h) => {
          const verdict =
            h.status !== "tested" ? (
              <Badge>{t("accumulating")}</Badge>
            ) : h.p_value !== null && h.p_value < 0.05 && (h.mean_rho ?? 0) > 0 ? (
              <Badge tone="up">{t("significant")}</Badge>
            ) : (
              <Badge tone="warn">{t("notSignificant")}</Badge>
            );
          return (
            <div key={h.horizon} className="space-y-2 rounded border border-border bg-panel-2 p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <b>{t("horizon", { n: h.horizon })}</b>
                {verdict}
              </div>
              <p className="text-xs text-muted">
                {h.next_due
                  ? t("firstDue", { date: fmtTime(h.next_due, locale, false) })
                  : t("progress", { n: h.days.length, need: h.need_days, graded: h.graded_days, min: h.min_traders })}
              </p>
              {h.mean_rho !== null && (
                <p className="text-xs">
                  {t("rho", { rho: fmtNum(h.mean_rho, locale, 2) })}
                  {h.p_value !== null && ` · p = ${fmtNum(h.p_value, locale, 3)}`}
                </p>
              )}
              {h.days.length > 0 && (
                <table className="w-full text-xs">
                  <thead>
                    <tr>
                      <th className={th}>{t("grade")}</th>
                      <th className={`${th} text-right`}>{t("n")}</th>
                      <th className={`${th} text-right`}>{t("mean")}</th>
                      <th className={`${th} text-right`}>{t("win")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {GRADES.map((g) => {
                      const o = h.grades[g];
                      return (
                        <tr key={g} className="border-t border-border/60">
                          <td className={td}>{g}</td>
                          <td className={`${td} num text-right`}>{o.n}</td>
                          <td className={`${td} num text-right ${signClass(o.mean)}`}>{o.mean === null ? "—" : fmtPct(o.mean, locale, 1)}</td>
                          <td className={`${td} num text-right`}>{o.win === null ? "—" : fmtPct(o.win, locale, 0, false)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          );
        })}
      </div>
      <p className="mt-3 text-xs leading-relaxed text-muted">
        {report.first_graded ? t("since", { date: fmtTime(report.first_graded, locale, false) }) : t("none")} {t("method")}
      </p>
    </Panel>
  );
}
