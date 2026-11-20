import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
import { HealthBadge } from "@/components/HealthBadge";
import { Panel } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { api, type Digest } from "@/lib/api";
import { fmtPct, fmtUsd } from "@/lib/format";

/** Three-step path for newcomers: find → verify → try, each step one click. */
export async function StartGuide() {
  const t = await getTranslations("guide");
  const steps = [
    { n: 1, icon: "🔭", href: "/discover", key: "find" },
    { n: 2, icon: "🎲", href: "/timing", key: "verify" },
    { n: 3, icon: "⧉", href: "/sims", key: "try" },
  ] as const;
  return (
    <section className="grid gap-3 md:grid-cols-3">
      {steps.map((s) => (
        <Link key={s.n} href={s.href} className="group relative overflow-hidden rounded-lg border border-border bg-panel p-4 transition hover:-translate-y-0.5 hover:border-accent">
          <span className="num absolute -right-2 -top-4 text-7xl font-bold text-accent/10">{s.n}</span>
          <div className="text-xl">{s.icon}</div>
          <div className="mt-2 text-sm font-semibold">{t(`${s.key}.title`)}</div>
          <p className="mt-1 text-xs leading-relaxed text-muted">{t(`${s.key}.text`)}</p>
          <span className="mt-2 inline-block text-xs text-accent group-hover:underline">{t(`${s.key}.cta`)} →</span>
        </Link>
      ))}
    </section>
  );
}

/** The latest daily digest: watched traders' moves, smart-vs-crowd gaps, newly significant traders. */
export async function DigestCard() {
  const t = await getTranslations("digest");
  const th = await getTranslations("health");
  const locale = await getLocale();
  let d: Digest;
  try {
    d = await api.digest();
  } catch {
    return null;
  }
  const g = d.digest;
  if (!g) return null;
  const kind = (k: string) => (t.has(`kinds.${k}`) ? t(`kinds.${k}`) : k);
  return (
    <Panel
      title={`📰 ${t("cardTitle", { day: g.day })}`}
      hint={t("cardHint")}
      actions={
        <Link href="/alerts" className="text-xs text-accent hover:underline">
          {t("manage")} →
        </Link>
      }
    >
      {Object.entries(g.exchange_observations ?? {}).map(([coin,id]) => <Link key={coin} className="mr-4 text-sm text-accent" href={{pathname:"/replay",query:{coin,tab:"exchange",observation:id}}}>{coin} · {locale==="zh"?"查看当时的交易所资金观察":"Open archived exchange evidence"} #{id}</Link>)}
      {g.market_review && <div className="mb-3 space-y-1 text-xs text-muted">
        {Object.entries(g.market_review.markets).map(([coin,m])=><div key={coin}>{Object.entries(m.venues).map(([venue,f])=><Link key={venue} className="mr-3 text-accent" href={{pathname:"/replay",query:{coin,venue,tab:"live",frame:f.frame}}}>{coin} · {venue} · Δ24h {f.change?.net_units?.toLocaleString(locale,{maximumFractionDigits:2})??"—"} · n={f.change?.common_n??"—"}</Link>)}{m.outcomes.map(o=><span className="mr-2" key={o.horizon}>{o.horizon}h · Brier {o.brier.toFixed(3)} / {o.baseline.toFixed(3)} · n={o.n}</span>)}</div>)}
        {Object.entries(g.market_review.comparison).map(([venue,v])=><span key={venue} className="mr-3">{venue} · BTC / ETH · {({aligned:locale==="zh"?"同向":"aligned",opposite:locale==="zh"?"反向":"opposite",flat:locale==="zh"?"一侧持平":"flat",insufficient:locale==="zh"?"等待双币 24h 样本":"awaiting 24h samples"} as Record<string,string>)[v]}</span>)}
      </div>}
      {g.major_markets && <div className="mb-3 flex flex-wrap gap-4">{Object.entries(g.major_markets).map(([coin, notices]) =>
        <Link key={coin} className="text-xs text-accent" href={{pathname:"/replay",query:{coin,tab:"live",venue:notices[0]?.venue,frame:notices[0]?.frame_id}}}>
          {coin} · {notices[0] ? `#${notices[0].id} ${notices[0].kind}` : "—"} →
        </Link>)}</div>}
      <div className="grid gap-5 md:grid-cols-3">
        <div>
          <h3 className="mb-2 text-xs font-medium text-muted">{t("moves")}</h3>
          {g.watch_moves.length ? (
            <ul className="space-y-1.5 text-sm">
              {g.watch_moves.slice(0, 6).map((m, i) => (
                <li key={i} className="flex items-start justify-between gap-2">
                  <AddressLink address={m.address} name={m.name} star={false} />
                  <span className="shrink-0 text-xs">
                    <span className={m.side === "long" ? "text-up" : "text-down"}>
                      {kind(m.kind)} {m.coin}
                    </span>{" "}
                    <span className="num text-muted">{fmtUsd(m.notional_change, locale)}</span>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted">{t("noMoves")}</p>
          )}
        </div>
        <div>
          <h3 className="mb-2 text-xs font-medium text-muted">{t("gaps")}</h3>
          {g.divergence.length ? (
            <ul className="space-y-1.5 text-sm">
              {g.divergence.slice(0, 5).map((c) => (
                <li key={c.coin} className="flex justify-between gap-2">
                  <Link href={{ pathname: "/market", query: { dcoin: c.coin } }} className="hover:text-accent">
                    {c.coin}
                  </Link>
                  <span className="num text-xs">
                    <span className="text-accent">{fmtPct(c.smart_long_share, locale, 0, false)}</span> vs <span className="text-muted">{fmtPct(c.crowd_long_share, locale, 0, false)}</span>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted">{t("noGaps")}</p>
          )}
        </div>
        <div>
          <h3 className="mb-2 text-xs font-medium text-muted">{t("newSig", { n: g.new_significant_total })}</h3>
          {g.baseline ? (
            <p className="text-xs text-muted">{t("baseline", { n: g.significant_total })}</p>
          ) : g.new_significant.length ? (
            <ul className="space-y-1.5 text-sm">
              {g.new_significant.slice(0, 5).map((r) => (
                <li key={r.address} className="flex justify-between gap-2">
                  <AddressLink address={r.address} name={r.display_name} star={false} />
                  <span className="num text-xs text-up">q {r.q.toFixed(3)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted">{t("noNew")}</p>
          )}
        </div>
      </div>
      {g.health_changes && g.health_changes.length > 0 && (
        <div className="mt-4 border-t border-border pt-3">
          <h3 className="mb-2 text-xs font-medium text-muted">{t("health")}</h3>
          <ul className="flex flex-wrap gap-x-5 gap-y-1.5 text-sm">
            {g.health_changes.slice(0, 8).map((h) => (
              <li key={h.address} className="flex items-center gap-1.5">
                <AddressLink address={h.address} name={h.name} star={false} />
                <span className="text-xs text-muted">{h.prev_status ? `${th(`status.${h.prev_status}`)} →` : ""}</span>
                <HealthBadge status={h.status} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}
