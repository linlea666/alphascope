import { getLocale, getTranslations } from "next-intl/server";
import { Link } from "@/i18n/routing";
import type { Replay } from "@/lib/api";
import { ReplayChart } from "./ReplayChart";
import { Panel } from "./ui";

const INTERVALS = ["15m", "1h", "4h", "1d"] as const;
const STEP_MIN: Record<string, number> = { "15m": 15, "1h": 60, "4h": 240, "1d": 1440 };
export const REPLAY_BARS = 300;

export async function ReplayPanel({ address, data, search }: { address: string; data: Replay | null; search: Record<string, string | undefined> }) {
  const t = await getTranslations("replay");
  const locale = await getLocale();
  if (!data || !data.coin) {
    return (
      <Panel title={t("title")}>
        <p className="text-sm text-muted">{t("none")}</p>
      </Panel>
    );
  }
  const shift = (bars: number) => new Date(new Date(data.end).getTime() + bars * STEP_MIN[data.interval] * 60_000).toISOString();
  const q = (over: Record<string, string | undefined>) => {
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries({ ...search, rc: data.coin!, ri: data.interval, re: search.re, ...over })) if (v) out[k] = v;
    return { pathname: `/trader/${address}`, query: out, hash: "replay" };
  };
  const pill = (active: boolean) => `rounded border px-2 py-0.5 ${active ? "border-accent text-accent" : "border-border text-muted hover:text-text"}`;

  return (
    <div id="replay">
      <Panel title={t("title")} hint={t("hint")}>
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
          <span className="text-muted">{t("coin")}:</span>
          {data.coins.slice(0, 12).map((c) => (
            <Link key={c.coin} href={q({ rc: c.coin, re: undefined })} className={pill(c.coin === data.coin)}>
              {c.coin} <span className="text-muted">{c.decisions}</span>
            </Link>
          ))}
          <span className="ml-3 text-muted">{t("interval")}:</span>
          {INTERVALS.map((iv) => (
            <Link key={iv} href={q({ ri: iv })} className={pill(iv === data.interval)}>
              {iv}
            </Link>
          ))}
          <span className="ml-auto flex gap-1">
            <Link href={q({ re: shift(-REPLAY_BARS * 0.8) })} className={pill(false)}>
              {t("earlier")}
            </Link>
            <Link href={q({ re: shift(REPLAY_BARS * 0.8) })} className={pill(false)}>
              {t("later")}
            </Link>
            <Link href={q({ re: undefined })} className={pill(!search.re)}>
              {t("latest")}
            </Link>
          </span>
        </div>
        {data.candles.length ? (
          <ReplayChart data={data} locale={locale} labels={{ buy: t("buy"), sell: t("sell"), top: t("top"), bottom: t("bottom") }} />
        ) : (
          <p className="py-10 text-center text-sm text-muted">{t("noCandles")}</p>
        )}
      </Panel>
    </div>
  );
}
