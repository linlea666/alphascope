import { getLocale, getTranslations } from "next-intl/server";
import { Badge } from "@/components/ui";
import type { Evidence, EvidenceRule, Oos, StyleKey, StyleStatus } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd } from "@/lib/format";

const PCT = new Set(["win_rate", "win_rate_lo", "positive_month_ratio", "top1_share", "top5_share", "dd_ratio", "open_loss_ratio", "liq_rate", "short_share", "scalp_share", "top_coin_share", "short_win_lo", "swing_win_lo", "top_coin_win_lo", "buy_loc", "sell_loc", "capture_avg", "coin_hhi"]);
const USD = new Set(["net_pnl", "short_net", "long_net", "swing_net", "big_net", "top_coin_net", "max_dd_usd", "open_upl"]);
const MULT = new Set(["profit_factor", "short_pf", "swing_pf", "top_coin_pf", "buy_lift", "sell_lift", "median_leverage", "hold_ratio"]);

/** One factor value in its natural unit. */
export function fmtFactor(name: string, v: number | null | undefined, locale: string): string {
  if (v === null || v === undefined) return "—";
  if (PCT.has(name)) return fmtPct(v, locale, 1, false);
  if (USD.has(name)) return fmtUsd(v, locale);
  if (MULT.has(name)) return v >= 99 ? "∞" : `${fmtNum(v, locale, 2)}×`;
  if (name.startsWith("q_")) return fmtNum(v, locale, 3);
  if (name.startsWith("edge_")) return fmtNum(v, locale, 3);
  if (name === "days_since_last") return `${fmtNum(v, locale, 0)}d`;
  if (name.endsWith("_h")) return `${fmtNum(v, locale, 1)}h`;
  if (name === "expectancy_bp") return `${fmtNum(v, locale, 0)}bp`;
  return fmtNum(v, locale, 0);
}

export async function StyleBadge({ style, status }: { style: StyleKey; status?: StyleStatus }) {
  const t = await getTranslations("discover");
  const tone = style === "high_risk" ? "down" : status === "verified" ? "up" : status === "watch" ? "neutral" : "accent";
  return (
    <Badge tone={tone} title={status ? t(`status.${status}`) : undefined}>
      {status === "verified" && style !== "high_risk" ? "✓ " : ""}
      {t(`styles.${style}`)}
      {status === "watch" ? ` · ${t("status.watch")}` : ""}
    </Badge>
  );
}

function Rule({ r, locale, label, gate }: { r: EvidenceRule; locale: string; label: string; gate: string }) {
  const mark = r.ok === true ? "✓" : r.ok === false ? "✗" : "?";
  const tone = r.ok === true ? "text-up" : r.ok === false ? "text-down" : "text-warn";
  return (
    <li className="flex flex-wrap items-baseline gap-x-2">
      <span className={`w-3 ${tone}`}>{mark}</span>
      <span className="text-muted">
        {r.gate && <span className="mr-1 rounded border border-border px-1 text-[10px]">{gate}</span>}
        {label}
      </span>
      <span className="num">{fmtFactor(r.factor, r.value, locale)}</span>
      <span className="num text-[11px] text-muted">
        {r.op} {fmtFactor(r.factor, r.threshold, locale)}
        {r.n ? ` · n=${r.n}` : ""}
      </span>
    </li>
  );
}

/** The rules behind a style label, each with the trader's value, the threshold and the sample size. */
export async function EvidenceList({ evidence, oos }: { evidence: Evidence[]; oos: Oos }) {
  const t = await getTranslations("discover");
  const locale = await getLocale();
  const label = (f: string) => (t.has(`factors.${f}`) ? t(`factors.${f}`) : f);
  return (
    <div className="space-y-2 text-xs">
      <ul className="space-y-1">
        {evidence.map((e, i) =>
          "meta" in e ? (
            <li key={i} className="text-muted">
              {t("topCoin")}: <span className="text-text">{e.value}</span>
            </li>
          ) : "any" in e ? (
            <li key={i}>
              <span className="text-muted">{t("anyOf")}</span>
              <ul className="ml-4 mt-1 space-y-1">
                {e.any.map((r, j) => (
                  <Rule key={j} r={r} locale={locale} label={label(r.factor)} gate={t("gate")} />
                ))}
              </ul>
            </li>
          ) : (
            <Rule key={i} r={e} locale={locale} label={label(e.factor)} gate={t("gate")} />
          ),
        )}
      </ul>
      {oos && (
        <p className={oos.passed ? "text-up" : "text-muted"}>
          {oos.tested === false
            ? oos.reason === "not_point_in_time"
              ? t("oos.notPit")
              : t("oos.notTested", { status: oos.pre_status ? t(`preStatus.${oos.pre_status}`) : "—" })
            : t(oos.passed ? "oos.passed" : "oos.failed", {
                cut: fmtTime(oos.cut ?? null, locale, false),
                n: oos.forward_n ?? 0,
                net: fmtUsd(oos.forward_net ?? 0, locale),
                pf: oos.forward_pf === null || oos.forward_pf === undefined ? "—" : oos.forward_pf >= 99 ? "∞" : fmtNum(oos.forward_pf, locale, 2),
              })}
        </p>
      )}
    </div>
  );
}
