import { getLocale, getTranslations } from "next-intl/server";
import { StyleBadge } from "@/components/StyleEvidence";
import { Term } from "@/components/Term";
import { Badge } from "@/components/ui";
import { LinesChart } from "@/components/LinesChart";
import { api, type FollowBacktest, type HighlightCycle, type Highlights, type StyleKey, type StyleStatus } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd, signClass } from "@/lib/format";

type Search = Record<string, string | undefined>;

const replayHref = (search: Search, c: HighlightCycle) => {
  const q = new URLSearchParams(Object.entries({ ...search, rc: c.coin, ri: c.holding_hours && c.holding_hours > 24 * 5 ? "1d" : "1h", re: c.closed_at ?? undefined }).filter((e): e is [string, string] => !!e[1]));
  return `?${q.toString()}#replay`;
};

/** First screen of a trader: a one-line portrait, evidence badges, best trades next to the worst ones. */
export async function ProfileCard({ address, search }: { address: string; search: Search }) {
  const t = await getTranslations("card");
  const tt = await getTranslations("trader");
  const locale = await getLocale();
  let h: Highlights;
  try {
    h = await api.highlights(address);
  } catch {
    return null;
  }
  if (!h.closed) return null;
  const hold = (x: number | null) => (x === null ? "—" : x < 48 ? tt("hours", { n: fmtNum(x, locale, 1) }) : tt("days", { n: fmtNum(x / 24, locale, 1) }));
  const coins = h.top_coins.map((c) => c.coin).join(" / ");
  const verified = h.styles.some((s) => s.status === "verified");
  const dd = h.factors.dd_ratio?.value;
  const card = (c: HighlightCycle, bad: boolean) => (
    <a key={`${c.coin}-${c.opened_at}`} href={replayHref(search, c)} className={`block rounded-lg border p-3 transition hover:-translate-y-0.5 hover:shadow-md ${bad ? "border-down/30 bg-down/5" : "border-up/30 bg-up/5"}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-sm font-medium">
          <Badge tone={c.direction === "long" ? "up" : "down"}>{tt(`dir.${c.direction}`)}</Badge>
          {c.coin}
        </span>
        <span className={`num text-sm font-semibold ${signClass(c.net)}`}>{fmtUsd(c.net, locale)}</span>
      </div>
      <div className="num mt-1.5 text-xs text-muted">
        {c.entry_px === null ? "—" : fmtNum(c.entry_px, locale, 2)} → {c.exit_px === null ? "—" : fmtNum(c.exit_px, locale, 2)}
        {c.move !== undefined && <span className={`ml-1.5 ${signClass(c.move)}`}>({fmtPct(c.move, locale, 1)})</span>}
      </div>
      <div className="mt-0.5 flex justify-between text-[11px] text-muted">
        <span className="num">{fmtTime(c.opened_at, locale, false)}</span>
        <span>
          {hold(c.holding_hours)} · {t("replay")} ↗
        </span>
      </div>
    </a>
  );
  return (
    <section className="rounded-lg border border-accent/30 bg-panel-2 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-base font-medium">
            {t("portrait", {
              coins: coins || "—",
              scale: h.main_scale ? tt(`scales.${h.main_scale}`).split(" ")[0] : "—",
              hold: hold(h.median_hours),
              long: fmtPct(h.long_share, locale, 0, false),
            })}
          </p>
          {h.styles.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {h.styles.map((s) => (
                <StyleBadge key={s.style} style={s.style as StyleKey} status={s.status as StyleStatus} />
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5 text-xs">
          {h.timing ? (
            <Badge tone="up" title={t("timingHint")}>
              ✓ {t("timing", { scale: tt(`scales.${h.timing.scale}`).split(" ")[0], q: fmtNum(h.timing.q_value, locale, 3) })}
            </Badge>
          ) : (
            <Badge title={t("timingHint")}>{t("timingNone")}</Badge>
          )}
          <Badge>{t("closed", { n: fmtNum(h.closed, locale), w: fmtPct(h.win_rate, locale, 0, false) })}</Badge>
          {dd !== undefined && dd !== null && (
            <Badge tone={dd <= 0.2 ? "up" : dd <= 0.5 ? "neutral" : "warn"}>
              <Term k="ddRatio">{t("dd", { v: fmtNum(dd, locale, 2) })}</Term>
            </Badge>
          )}
          {h.liquidations !== null && <Badge tone={h.liquidations ? "down" : "up"}>{t("liq", { n: h.liquidations })}</Badge>}
          {verified && <Badge tone="up">{t("oos")}</Badge>}
        </div>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-[3fr_2fr]">
        <div>
          <h3 className="mb-2 text-xs font-medium text-muted">✨ {t("best")}</h3>
          <div className="grid gap-2 sm:grid-cols-3">{h.best.length ? h.best.map((c) => card(c, false)) : <p className="text-xs text-muted">{t("noBest")}</p>}</div>
        </div>
        <div>
          <h3 className="mb-2 text-xs font-medium text-muted">⚠️ {t("worst")}</h3>
          <div className="grid gap-2 sm:grid-cols-2">{h.worst.length ? h.worst.map((c) => card(c, true)) : <p className="text-xs text-muted">{t("noWorst")}</p>}</div>
        </div>
      </div>
      <p className="mt-3 text-[11px] text-muted">{t("scope", { net: fmtUsd(h.net, locale) })}</p>
    </section>
  );
}

/** "What if I had followed": the account's flow-adjusted return applied to a chosen capital since a chosen date. */
export async function FollowCard({ address, search }: { address: string; search: Search }) {
  const t = await getTranslations("follow");
  const locale = await getLocale();
  const since = search.fs || new Date(Date.now() - 90 * 86400000).toISOString().slice(0, 10);
  const capital = Number(search.fc || 10000) || 10000;
  let f: FollowBacktest;
  try {
    f = await api.follow(address, { since, capital });
  } catch {
    return null;
  }
  if (!f.supported) return null;
  const input = "rounded border border-border bg-panel-2 px-2 py-1 text-sm outline-none focus:border-accent";
  const keep = Object.entries(search).filter(([k, v]) => v !== undefined && k !== "fs" && k !== "fc");
  return (
    <section id="follow" className="rounded-lg border border-border bg-panel">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-border px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold">{t("title")}</h2>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">{t("hint")}</p>
        </div>
        <form method="get" action="#follow" className="flex flex-wrap items-end gap-2 text-xs">
          {keep.map(([k, v]) => (
            <input key={k} type="hidden" name={k} value={v} />
          ))}
          <label className="flex flex-col gap-1 text-muted">
            {t("since")}
            <input type="date" name="fs" defaultValue={since} className={input} />
          </label>
          <label className="flex flex-col gap-1 text-muted">
            {t("capital")}
            <input type="number" name="fc" min={100} step={100} defaultValue={capital} className={`${input} w-28`} />
          </label>
          <button type="submit" className="rounded bg-accent px-3 py-1 text-white">
            {t("run")}
          </button>
        </form>
      </div>
      <div className="p-4">
        {!f.rows?.length ? (
          <p className="py-6 text-center text-sm text-muted">{t("insufficient")}</p>
        ) : (
          <>
            <div className="mb-3 flex flex-wrap items-baseline gap-x-6 gap-y-1">
              <span className="text-sm text-muted">{t("result", { capital: fmtUsd(capital, locale, false), since: fmtTime(f.since, locale, false) })}</span>
              <span className={`num text-2xl font-semibold ${signClass(f.ret)}`}>{fmtUsd(f.final, locale, false)}</span>
              <span className={`num text-sm ${signClass(f.ret)}`}>{fmtPct(f.ret, locale, 1)}</span>
              <span className="num text-sm text-down">
                <Term k="mdd">{t("mdd")}</Term> {fmtPct(f.mdd, locale, 1)}
              </span>
            </div>
            <LinesChart lines={[{ name: t("series"), data: f.rows, area: true }]} formats={["usd"]} locale={locale} height={220} />
            <p className="mt-2 text-[11px] leading-relaxed text-muted">
              {t("notes", { n: f.n_points ?? 0, step: fmtNum((f.median_step_hours ?? 0) / 24, locale, 1) })}
              {!f.covered && ` ${t("notCovered")}`}
            </p>
          </>
        )}
      </div>
    </section>
  );
}
