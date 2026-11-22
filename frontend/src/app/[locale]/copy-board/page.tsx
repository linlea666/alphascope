import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
import { ApiErrorState } from "@/components/ApiErrorState";
import { CopyValidation } from "@/components/CopyValidation";
import { HealthBadge } from "@/components/HealthBadge";
import { Term } from "@/components/Term";
import { Badge, PageHeader, Panel, td, th } from "@/components/ui";
import { VenueSwitch } from "@/components/VenueSwitch";
import { DelayBars, Sparkline } from "@/components/viz";
import { Link } from "@/i18n/routing";
import { getVenue } from "@/lib/venue";
import { api, ApiError, type CopyBoard, type CopyValidation as Validation } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd, signClass } from "@/lib/format";

const WINDOWS = [7, 30, 90] as const;
const SORTS = ["ret", "pnl", "score", "mdd", "retention"] as const;
const GRADE_TONE: Record<string, "up" | "accent" | "warn" | "down"> = { A: "up", B: "accent", C: "warn", D: "down" };

/** Who would have been most profitable to copy: nightly historical copy backtests with one default setting. */
export default async function CopyBoardPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const t = await getTranslations("copyBoard");
  const locale = await getLocale();
  const venue = await getVenue();
  const window = WINDOWS.find((w) => String(w) === sp.window) ?? 30;
  const sort = (SORTS as readonly string[]).includes(sp.sort ?? "") ? sp.sort! : "ret";
  let d: CopyBoard;
  // the score check is secondary: the board still renders without it
  const validation = api.copyValidation().catch((): Validation | null => null);
  try {
    d = await api.copyBoard({ window, sort, venue, limit: 150 });
  } catch (e) {
    return <ApiErrorState status={e instanceof ApiError ? e.status : undefined} detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const pill = (active: boolean) => `rounded border px-3 py-1 ${active ? "border-accent bg-accent text-white" : "border-border text-muted hover:text-text"}`;
  const small = (active: boolean) => `rounded border px-2 py-0.5 ${active ? "border-accent text-accent" : "border-border text-muted hover:text-text"}`;
  const report = await validation;
  const delayLabels = [t("delays.0"), t("delays.30"), t("delays.300"), t("delays.1800"), t("delays.7200")];

  return (
    <div className="space-y-4">
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <VenueSwitch value={venue} />
      </PageHeader>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {WINDOWS.map((w) => (
          <Link scroll={false} key={w} href={{ pathname: "/copy-board", query: { window: w, sort } }} className={pill(w === window)}>
            {t("window", { n: w })}
          </Link>
        ))}
        <span className="text-xs text-muted">{d.computed_at ? t("computed", { t: fmtTime(d.computed_at, locale) }) : t("notYet")}</span>
      </div>
      <Panel
        title={t("tableTitle", { n: window })}
        hint={t("hint")}
        actions={
          <div className="flex flex-wrap items-center gap-1 text-xs">
            <span className="text-muted">{t("sort")}:</span>
            {SORTS.map((s) => (
              <Link scroll={false} key={s} href={{ pathname: "/copy-board", query: { window, sort: s } }} className={small(s === sort)}>
                {t(`sorts.${s}`)}
              </Link>
            ))}
          </div>
        }
      >
        {d.rows.length ? (
          <div className="-mx-4 -my-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-panel-2">
                <tr>
                  <th className={th}>#</th>
                  <th className={th}>{t("cols.trader")}</th>
                  <th className={`${th} text-right`}>{t("cols.ret")}</th>
                  <th className={th}></th>
                  <th className={`${th} text-right`}>
                    <Term k="mdd" align="right">{t("cols.mdd")}</Term>
                  </th>
                  <th className={`${th} text-right`}>{t("cols.leader")}</th>
                  <th className={`${th} text-right`}>
                    <Term k="retention" align="right">{t("cols.retention")}</Term>
                  </th>
                  <th className={th}>
                    <Term k="delay">{t("cols.delay")}</Term>
                  </th>
                  <th className={th}>
                    <Term k="copyability">{t("cols.score")}</Term>
                  </th>
                  <th className={th}></th>
                </tr>
              </thead>
              <tbody>
                {d.rows.map((r, i) => (
                  <tr key={r.address} className="border-t border-border/60 align-top hover:bg-panel-2/60">
                    <td className={`${td} num text-muted`}>{i < 3 && sort !== "mdd" ? ["🥇", "🥈", "🥉"][i] : i + 1}</td>
                    <td className={td}>
                      <AddressLink address={r.address} name={r.display_name} />
                      <div className="mt-1 flex flex-wrap gap-1">
                        <HealthBadge status={r.health} />
                        {r.approx_equity && <Badge title={t("approxHint")}>{t("approx")}</Badge>}
                      </div>
                    </td>
                    <td className={`${td} num text-right ${signClass(r.ret)}`}>
                      <span className="text-base font-semibold">{fmtPct(r.ret, locale, 1)}</span>
                      <div className="text-xs">{t("onCapital", { pnl: fmtUsd(r.pnl, locale) })}</div>
                      <div className="text-xs text-muted">
                        {t("trades", { n: r.trades })}
                        {r.liquidations > 0 && <span className="ml-1 text-down">{t("liq", { n: r.liquidations })}</span>}
                      </div>
                    </td>
                    <td className={td}>{r.curve && <Sparkline values={r.curve.map((c) => c[1])} />}</td>
                    <td className={`${td} num text-right text-down`}>{fmtPct(r.mdd, locale, 1)}</td>
                    <td className={`${td} num text-right ${signClass(r.leader_ret)}`}>{r.leader_ret === null ? "—" : fmtPct(r.leader_ret, locale, 1)}</td>
                    <td className={`${td} num text-right`}>{r.retention === null ? "—" : fmtPct(r.retention, locale, 0, false)}</td>
                    <td className={td}>{r.delay_curve ? <DelayBars curve={r.delay_curve} labels={delayLabels} /> : "—"}</td>
                    <td className={td}>
                      {r.grade ? (
                        <span className="inline-flex items-center gap-1">
                          <Badge tone={GRADE_TONE[r.grade]}>{r.grade}</Badge>
                          <span className="num text-xs text-muted">{fmtNum(r.score, locale, 0)}</span>
                        </span>
                      ) : (
                        <span className="text-xs text-muted">{t("insufficient")}</span>
                      )}
                    </td>
                    <td className={`${td} text-xs`}>
                      <Link href={{ pathname: "/sims", query: { new: 1, leader: r.address } }} className="mr-2 text-accent hover:underline">
                        ⧉ {t("simulate")}
                      </Link>
                      <Link href={`/trader/${r.address}#backtest`} className="text-muted hover:text-accent">
                        {t("details")} →
                      </Link>
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
      <p className="text-xs leading-relaxed text-muted">{t("method")}</p>
      {report && <CopyValidation report={report} />}
    </div>
  );
}
