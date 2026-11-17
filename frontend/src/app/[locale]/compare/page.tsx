import { getLocale, getTranslations } from "next-intl/server";
import type { ReactNode } from "react";
import { AddressLink } from "@/components/AddressLink";
import { ApiErrorState } from "@/components/ApiErrorState";
import { LinesChart } from "@/components/LinesChart";
import { StyleBadge } from "@/components/StyleEvidence";
import { SubmitButton } from "@/components/SubmitButton";
import { Term } from "@/components/Term";
import { PageHeader, Panel, td, th } from "@/components/ui";
import { api, ApiError, type Comparison, type StyleKey, type StyleStatus } from "@/lib/api";
import { fmtNum, fmtPct, fmtUsd, signClass } from "@/lib/format";

const SLOTS = 4;

/** Up to four traders side by side: same metrics, cumulative realized PnL, and how correlated their results are
 *  (following two highly correlated traders is close to following one). */
export default async function ComparePage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const t = await getTranslations("compare");
  const tt = await getTranslations("trader");
  const locale = await getLocale();
  const raw = sp.addr;
  const addrs = (Array.isArray(raw) ? raw : raw ? [raw] : []).map((a) => a.trim()).filter(Boolean).slice(0, SLOTS);
  const days = Number(sp.days) || 180;
  let d: Comparison | null = null;
  let error: ApiError | null = null;
  if (addrs.length) {
    try {
      d = await api.compare(addrs, days);
    } catch (e) {
      error = e instanceof ApiError ? e : new ApiError(0, String(e));
    }
  }
  const input = "rounded border border-border bg-panel-2 px-2 py-1 text-sm outline-none focus:border-accent";
  const hold = (x: number | null) => (x === null ? "—" : x < 48 ? tt("hours", { n: fmtNum(x, locale, 1) }) : tt("days", { n: fmtNum(x / 24, locale, 1) }));
  const rows: { label: ReactNode; cell: (p: Comparison["traders"][number]) => ReactNode }[] = [
    { label: t("rows.size"), cell: (p) => fmtUsd(p.size, locale) },
    { label: t("rows.closed"), cell: (p) => fmtNum(p.highlights.closed, locale) },
    { label: t("rows.winRate"), cell: (p) => fmtPct(p.highlights.win_rate, locale, 0, false) },
    { label: t("rows.net"), cell: (p) => <span className={signClass(p.highlights.net)}>{fmtUsd(p.highlights.net, locale)}</span> },
    { label: <Term k="profitFactor">{t("rows.pf")}</Term>, cell: (p) => fmtNum(p.highlights.factors.profit_factor?.value, locale, 2) },
    { label: <Term k="ddRatio">{t("rows.dd")}</Term>, cell: (p) => fmtNum(p.highlights.factors.dd_ratio?.value, locale, 2) },
    { label: t("rows.hold"), cell: (p) => hold(p.highlights.median_hours) },
    { label: t("rows.long"), cell: (p) => fmtPct(p.highlights.long_share, locale, 0, false) },
    { label: t("rows.coins"), cell: (p) => p.highlights.top_coins.map((c) => c.coin).join(" / ") || "—" },
    { label: t("rows.liq"), cell: (p) => (p.highlights.liquidations === null ? "—" : <span className={p.highlights.liquidations ? "text-down" : ""}>{p.highlights.liquidations}</span>) },
    {
      label: <Term k="q">{t("rows.timing")}</Term>,
      cell: (p) => (p.highlights.timing ? <span className="text-up">{`${tt(`scales.${p.highlights.timing.scale}`).split(" ")[0]} q=${fmtNum(p.highlights.timing.q_value, locale, 3)}`}</span> : <span className="text-muted">{t("none")}</span>),
    },
    {
      label: t("rows.styles"),
      cell: (p) => (
        <span className="flex flex-wrap justify-end gap-1">
          {p.highlights.styles.map((s) => (
            <StyleBadge key={s.style} style={s.style as StyleKey} status={s.status as StyleStatus} />
          ))}
        </span>
      ),
    },
  ];
  const corrColor = (v: number | null) => (v === null ? "transparent" : v >= 0 ? `color-mix(in srgb, var(--warn) ${Math.round(Math.abs(v) * 60)}%, transparent)` : `color-mix(in srgb, var(--accent) ${Math.round(Math.abs(v) * 60)}%, transparent)`);

  return (
    <div className="space-y-4">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <Panel>
        <form method="get" className="flex flex-wrap items-end gap-2 text-sm">
          {Array.from({ length: SLOTS }, (_, i) => (
            <label key={i} className="flex flex-col gap-1 text-xs text-muted">
              {t("slot", { n: i + 1 })}
              <input name="addr" defaultValue={addrs[i] ?? ""} placeholder={t("placeholder")} className={`${input} w-56`} />
            </label>
          ))}
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("days")}
            <select name="days" defaultValue={String(days)} className={input}>
              {[90, 180, 365, 730].map((n) => (
                <option key={n} value={n}>
                  {t("daysN", { n })}
                </option>
              ))}
            </select>
          </label>
          <SubmitButton pending={t("loading")}>{t("go")}</SubmitButton>
        </form>
        <p className="mt-2 text-xs text-muted">{t("tip")}</p>
      </Panel>
      {error && <ApiErrorState status={error.status} detail={error.message} />}
      {d && (
        <>
          <Panel>
            <div className="-mx-4 -my-4 overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-panel-2">
                  <tr>
                    <th className={th}></th>
                    {d.traders.map((p) => (
                      <th key={p.address} className={`${th} text-right`}>
                        <AddressLink address={p.address} name={p.display_name} />
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i} className="border-t border-border/60">
                      <td className={`${td} text-muted`}>{r.label}</td>
                      {d.traders.map((p) => (
                        <td key={p.address} className={`${td} num text-right`}>
                          {r.cell(p)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
          <Panel title={t("curveTitle", { n: d.days })} hint={t("curveHint")}>
            <LinesChart lines={d.traders.map((p) => ({ name: p.display_name ?? p.address.slice(0, 10), data: p.curve }))} formats={["usd"]} locale={locale} zeroLine empty={t("noCurve")} />
          </Panel>
          {d.traders.length > 1 && (
            <Panel title={t("corrTitle")} hint={t("corrHint")}>
              <table className="text-sm">
                <tbody>
                  {d.traders.map((p, i) => (
                    <tr key={p.address}>
                      <td className={`${td} text-xs text-muted`}>{p.display_name ?? p.address.slice(0, 10)}</td>
                      {d!.correlation[i].map((v, j) => (
                        <td key={j} className="num h-10 w-20 border border-border/40 text-center text-xs" style={{ background: i === j ? "var(--panel-2)" : corrColor(v) }}>
                          {i === j ? "—" : v === null ? t("tooFew") : fmtNum(v, locale, 2)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          )}
        </>
      )}
    </div>
  );
}
