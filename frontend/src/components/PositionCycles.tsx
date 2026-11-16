import { getLocale, getTranslations } from "next-intl/server";
import type { WalletPositions } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd, signClass } from "@/lib/format";
import { Badge, Panel, td, th } from "./ui";

export async function PositionCycles({ data }: { data: WalletPositions | null }) {
  const t = await getTranslations("trader");
  const locale = await getLocale();
  const cov = data?.coverage;
  if (!data || !cov || !cov.n_fills) {
    return (
      <Panel title={t("cyclesTitle")}>
        <p className="text-sm text-muted">{t("cyclesNone")}</p>
      </Panel>
    );
  }
  const hold = (h: number | null | undefined) =>
    h === null || h === undefined ? "—" : h < 48 ? t("hours", { n: fmtNum(h, locale, 1) }) : t("days", { n: fmtNum(h / 24, locale, 1) });

  return (
    <Panel
      title={t("cyclesTitle")}
      hint={t("cyclesHint")}
      actions={
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <span>
            {t("coverageText", {
              n: fmtNum(cov.n_fills, locale),
              from: fmtTime(cov.earliest_fill_at, locale, false),
              synced: fmtTime(cov.last_synced_at, locale),
            })}
          </span>
          {cov.truncated && <Badge tone="warn">{t("truncated")}</Badge>}
          {!!cov.recon_breaks && <Badge tone="warn">{t("breaks", { n: cov.recon_breaks })}</Badge>}
        </div>
      }
    >
      {!data.sync_complete && (
        <p className="mb-3 text-xs text-warn">{t("syncIncomplete", { date: fmtTime(cov.latest_fill_at, locale) })}</p>
      )}
      <div className="-mx-4 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className={th}>{t("scale")}</th>
              <th className={`${th} text-right`}>{t("cycles")}</th>
              <th className={`${th} text-right`}>{t("winRate")}</th>
              <th className={`${th} text-right`}>{t("netPnl")}</th>
              <th className={`${th} text-right`}>{t("medianHold")}</th>
              <th className={`${th} text-right`}>{t("openNow")}</th>
            </tr>
          </thead>
          <tbody>
            {data.scales.map((s) => (
              <tr key={s.scale} className="border-t border-border/60">
                <td className={td}>{t(`scales.${s.scale}`)}</td>
                <td className={`${td} num text-right`}>
                  {fmtNum(s.complete_closed ?? 0, locale)} <span className="text-muted">/ {fmtNum(s.cycles, locale)}</span>
                </td>
                <td className={`${td} num text-right`}>
                  {s.win_rate === null || s.win_rate === undefined ? (
                    "—"
                  ) : (
                    <>
                      {fmtPct(s.win_rate, locale, 0, false)}{" "}
                      {s.win_rate_ci && (
                        <span className="text-xs text-muted">
                          ({fmtPct(s.win_rate_ci[0], locale, 0, false)}–{fmtPct(s.win_rate_ci[1], locale, 0, false)})
                        </span>
                      )}
                    </>
                  )}
                </td>
                <td className={`${td} num text-right ${signClass(s.net_pnl)}`}>{fmtUsd(s.net_pnl, locale)}</td>
                <td className={`${td} num text-right text-muted`}>{hold(s.median_hours)}</td>
                <td className={`${td} num text-right`}>{s.open_now ? fmtNum(s.open_now, locale) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 className="mt-5 text-xs font-medium text-muted">{t("recentCycles")}</h3>
      <div className="-mx-4 mt-2 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className={th}>{t("scale")}</th>
              <th className={th}>{t("coin")}</th>
              <th className={th}>{t("period")}</th>
              <th className={`${th} text-right`}>{t("entryExit")}</th>
              <th className={`${th} text-right`}>{t("hold")}</th>
              <th className={`${th} text-right`}>{t("decisions")}</th>
              <th className={`${th} text-right`}>{t("netPnl")}</th>
            </tr>
          </thead>
          <tbody>
            {data.cycles.map((c) => (
              <tr key={`${c.coin}-${c.opened_at}`} className="border-t border-border/60">
                <td className={`${td} text-xs text-muted`}>{t(`scales.${c.scale}`)}</td>
                <td className={td}>
                  <Badge tone={c.direction === "long" ? "up" : "down"}>{t(`dir.${c.direction}`)}</Badge> {c.coin}{" "}
                  {!c.complete && <Badge tone="warn">{t("incomplete")}</Badge>}
                </td>
                <td className={`${td} num text-xs text-muted`}>
                  {fmtTime(c.opened_at, locale)} → {c.closed_at ? fmtTime(c.closed_at, locale) : <Badge tone={c.status === "data_end" ? "warn" : "accent"}>{t(`status.${c.status}`)}</Badge>}
                </td>
                <td className={`${td} num text-right text-xs`}>
                  {fmtNum(c.entry_px, locale, 4)} / {c.exit_px === null ? "—" : fmtNum(c.exit_px, locale, 4)}
                </td>
                <td className={`${td} num text-right text-muted`}>{hold(c.holding_hours)}</td>
                <td className={`${td} num text-right`}>{fmtNum(c.n_decisions, locale)}</td>
                <td className={`${td} num text-right ${signClass(c.net_pnl)}`}>{fmtUsd(c.net_pnl, locale)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
