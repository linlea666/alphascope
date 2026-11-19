import { getLocale } from "next-intl/server";
import { Link } from "@/i18n/routing";
import { research, noticeLabel, numeric, percent, utc } from "@/lib/research";
import { Panel } from "./ui";

export async function ResearchSummary({ health = false }: { health?: boolean }) {
  const zh = (await getLocale()) === "zh";
  const coins = (await research.coins().catch(() => ({ coins: ["BTC", "ETH"] }))).coins;
  const results = await Promise.allSettled(coins.map((coin) => research.summary(coin)));
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {coins.map((coin, i) => {
        const r = results[i];
        const s = r.status === "fulfilled" ? r.value : null;
        return (
          <Panel
            key={coin}
            title={coin}
            actions={
              <Link href={{ pathname: "/replay", query: { coin } }} className="text-xs text-accent">
                {zh ? "研究回放" : "Open replay"} →
              </Link>
            }
          >
            {health
              ? s?.coverage.map((c) => (
                  <p className="mb-2 text-xs" key={c.venue}>
                    {c.venue} · {utc(c.first)} → {utc(c.updated)}
                    <br />
                    {zh ? "小时桶 / 缺失" : "Hourly samples / missing"}　{c.n} / {numeric(c.missing_hours, 0)}
                  </p>
                ))
              : s?.frames.map((f) => (
                  <div
                    className="mb-2 flex items-baseline justify-between gap-2 border-b border-border pb-2"
                    key={f.venue}
                  >
                    <div>
                      <span className="text-sm">{f.venue}</span>
                      <p className="text-xs text-muted">
                        {utc(f.available_at)}
                        {f.stale && (zh ? " · 已过期" : " · stale")}
                      </p>
                    </div>
                    <div className="text-right">
                      <span className="num">{percent(f.metrics.long_share)}</span>
                      <p className="text-xs text-muted">
                        {zh ? "做多人数占比" : "Long trader share"} · n={f.metrics.n}
                      </p>
                    </div>
                  </div>
                ))}
            {!s ? (
              <span className="text-xs text-warn">
                {zh ? "暂时无法读取此币种" : "Market temporarily unavailable"}
              </span>
            ) : (
              !health &&
              (s.notices.length ? (
                <Link
                  href={{
                    pathname: "/replay",
                    query: { coin, tab: "live", venue: s.notices[0].venue, frame: s.notices[0].frame_id },
                  }}
                  className="text-xs text-accent"
                >
                  {zh ? "最新异动" : "Latest observation"} · {noticeLabel(s.notices[0].kind, zh)} ·{" "}
                  {percent(s.notices[0].evidence.rank)} →
                </Link>
              ) : (
                <span className="text-xs text-muted">
                  {zh ? "当前没有已登记异动" : "No registered anomaly in this window"}
                </span>
              ))
            )}
          </Panel>
        );
      })}
    </div>
  );
}
