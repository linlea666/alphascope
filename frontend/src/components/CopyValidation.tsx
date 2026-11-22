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
      <Persistence report={report} />
    </Panel>
  );
}

async function Persistence({ report }: { report: Report }) {
  const t = await getTranslations("copyBoard.validation");
  const locale = await getLocale();
  const p = report.persistence;
  if (!p) return <p className="mt-4 border-t border-border pt-3 text-xs text-muted">{t("persistNone")}</p>;
  const first = p.runs[0];
  return (
    <div className="mt-4 space-y-2 border-t border-border pt-3">
      <h3 className="text-sm font-semibold">{t("persistTitle", { lookback: first.lookback })}</h3>
      <p className="max-w-3xl text-xs leading-relaxed text-muted">{t("persistHint", { lookback: first.lookback, sample: first.sample })}</p>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr>
              <th className={th}>{t("venue")}</th>
              <th className={`${th} text-right`}>{t("after")}</th>
              <th className={`${th} text-right`}>{t("dates")}</th>
              <th className={`${th} text-right`}>{t("rhoCol")}</th>
              <th className={`${th} text-right`}>{t("positive")}</th>
              <th className={`${th} text-right`}>{t("spread")}</th>
              <th className={th}>{t("verdict")}</th>
            </tr>
          </thead>
          <tbody>
            {p.runs.map((r) => {
              const tested = r.dates.filter((d) => d.rho !== null).length;
              const verdict =
                r.status !== "tested" ? (
                  <Badge>{t("accumulating")}</Badge>
                ) : r.p_value !== null && r.p_value < 0.05 ? (
                  <Badge tone={r.shared_denominator ? "warn" : "up"} title={r.shared_denominator ? t("sharedScale") : undefined}>
                    {t("persists")}{r.shared_denominator ? " *" : ""}
                  </Badge>
                ) : r.p_value !== null && r.p_value > 0.95 ? (
                  <Badge tone="down">{t("reverses")}</Badge>
                ) : (
                  <Badge tone="warn">{t("noPersist")}</Badge>
                );
              return (
                <tr key={r.venue + r.horizon} className="border-t border-border/60">
                  <td className={td}>{r.venue === "okx" ? "OKX" : "Hyperliquid"}</td>
                  <td className={`${td} num text-right`}>{t("days", { n: r.horizon })}</td>
                  <td className={`${td} num text-right`}>{r.status === "tested" ? tested : `${tested}/${r.need_days}`}</td>
                  <td className={`${td} num text-right`}>
                    {fmtNum(r.mean_rho, locale, 2)}
                    {r.p_value !== null && <span className="text-muted"> · p={fmtNum(r.p_value, locale, 3)}</span>}
                  </td>
                  <td className={`${td} num text-right`}>{r.positive_share == null ? "—" : fmtPct(r.positive_share, locale, 0, false)}</td>
                  <td className={`${td} num text-right ${signClass(r.top_minus_bottom)}`}>
                    {r.top_minus_bottom == null ? "—" : fmtPct(r.top_minus_bottom, locale, 1)}
                  </td>
                  <td className={td}>{verdict}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs leading-relaxed text-muted">{t("persistNote", { date: fmtTime(p.computed_at, locale) })}</p>
      {p.runs.some((r) => r.shared_denominator) && <p className="text-xs leading-relaxed text-muted">{t("sharedScale")}</p>}
    </div>
  );
}
