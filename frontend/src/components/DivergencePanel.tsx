import { getLocale, getTranslations } from "next-intl/server";
import { LinesChart } from "@/components/LinesChart";
import { Term } from "@/components/Term";
import { Panel } from "@/components/ui";
import { Gauge } from "@/components/viz";
import { Link } from "@/i18n/routing";
import { api, type Divergence } from "@/lib/api";
import { fmtPct } from "@/lib/format";

/** Smart money vs the crowd: per coin, long share among timing-significant traders vs all observed traders,
 *  plus the daily history of the gap next to the coin's next-7-day move (a historical relation, not a forecast). */
export async function DivergencePanel({ venue, coin, query }: { venue: string; coin: string; query: (o: Record<string, string | undefined>) => Record<string, string> }) {
  const t = await getTranslations("divergence");
  const locale = await getLocale();
  let d: Divergence;
  try {
    d = await api.divergence(venue, coin);
  } catch {
    return null;
  }
  const top = d.coins.slice(0, 8);
  return (
    <Panel title={<Term k="divergence">{t("title")}</Term>} hint={t("hint", { n: d.min_smart })}>
      {top.length ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-8">
          {top.map((c) => (
            <Link key={c.coin} href={{ pathname: "/market", query: query({ dcoin: c.coin }) }} scroll={false} className={`rounded-lg border p-2 text-center transition hover:bg-panel-2 ${c.coin === d.coin ? "border-accent" : "border-border"}`}>
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium">{c.coin}</span>
                <span className={`num ${Math.abs(c.divergence) >= 0.2 ? "text-warn" : "text-muted"}`}>{fmtPct(c.divergence, locale, 0)}</span>
              </div>
              <div className="flex justify-center">
                <Gauge a={c.smart_long_share} b={c.crowd_long_share} />
              </div>
              <div className="num flex justify-between text-[11px]">
                <span className="text-accent">{fmtPct(c.smart_long_share, locale, 0, false)}</span>
                <span className="text-muted">{fmtPct(c.crowd_long_share, locale, 0, false)}</span>
              </div>
              <div className="text-[10px] text-muted">{t("n", { s: c.smart_traders, a: c.crowd_traders })}</div>
            </Link>
          ))}
        </div>
      ) : (
        <p className="py-6 text-center text-sm text-muted">{t("empty", { n: d.min_smart })}</p>
      )}
      <div className="mt-2 flex gap-4 text-[11px] text-muted">
        <span>
          <span className="text-accent">━</span> {t("smart")}
        </span>
        <span>━ {t("crowd")}</span>
      </div>
      <h3 className="mt-5 text-xs font-medium text-muted">{t("historyTitle", { coin: d.coin })}</h3>
      <LinesChart
        lines={[
          { name: t("gap"), data: d.history.map((h) => [h.day, h.divergence]), area: true },
          { name: t("forward"), data: d.history.map((h) => [h.day, h.forward_return]), axis: 1, dashed: true },
        ]}
        formats={["pct", "pct"]}
        locale={locale}
        height={240}
        zeroLine
        empty={t("historyEmpty")}
      />
      <p className="mt-1 text-[11px] leading-relaxed text-muted">{t("caveat")}</p>
    </Panel>
  );
}
