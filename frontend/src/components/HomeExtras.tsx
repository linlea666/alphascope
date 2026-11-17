import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
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
    </Panel>
  );
}
