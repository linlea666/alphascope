import { getLocale, getTranslations } from "next-intl/server";
import { AutoRefresh } from "@/components/AutoRefresh";
import { PostCard, type Card } from "@/components/PostCard";
import { PostList } from "@/components/PostList";
import { Empty, PageHeader, button, input } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { factName, named, styleLabel } from "@/lib/glossary";
import type { Account } from "@/lib/types";

const TILES = ["ready", "review", "attention"] as const;
const TABS = ["ready", "review", "attention", "writing", "published", "trash"] as const;
const VENUES = ["hyperliquid", "okx", "binance"] as const;
type D = { tab: string; cards: Card[]; siblings: Card[]; counts: Record<string, number>; now: string;
  today: { published_today: number }; styles: { key: string; name: string; language: string }[]; agents: string[];
  robots: { name: string; channel: string; last_used_at: string | null }[] };

/** Posts: every post once, in its final version, in one of a few plain states. "Ready" is the list the RPA and the
 * browser extension take. */
export default async function PublishPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const tab = (TABS as readonly string[]).includes(sp.tab ?? "") ? sp.tab! : "ready";
  const preview = sp.view === "preview";
  const pair = preview && sp.pair === "1";
  const [t, tr, tc, ts, tf, tcol, locale, d, accounts] = await Promise.all([getTranslations("publish"), getTranslations("roles"),
    getTranslations("classes"), getTranslations("states"), getTranslations("factNames"), getTranslations("columnNames"), getLocale(),
    nrGet<D>("publish", { tab, language: sp.language, account_id: sp.account_id, style: sp.style, event_class: sp.event_class,
      within: sp.within, venue: sp.venue, agent: sp.agent, pair: pair ? "true" : undefined }),
    nrGet<Account[]>("accounts")]);
  const now = new Date(d.now).getTime();
  const q = (extra: Record<string, string>) => {
    const p = new URLSearchParams(Object.entries({ ...sp, ...extra }).filter(([, v]) => v) as [string, string][]);
    return `/publish?${p.toString()}`;
  };
  const roleNames = Object.fromEntries(d.agents.concat(d.cards.map((c) => c.agent_key ?? "")).filter(Boolean)
    .map((k) => [k, named(tr, k).name]));
  const columnNames = Object.fromEntries(d.cards.filter((c) => c.column_key).map((c) => [c.column_key!, tcol.has(c.column_key!) ? tcol(c.column_key!) : c.column_key!]));
  const chip = (active: boolean) => `rounded-full border px-3 py-1 text-xs ${active ? "border-accent bg-accent/15 text-accent" : "border-border text-muted hover:text-text"}`;
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <AutoRefresh active={tab === "ready" || tab === "review" || tab === "writing"} label={t("autoRefresh")} seconds={60} />
      </PageHeader>
      <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
        {TILES.map((k) => (
          <Link key={k} href={q({ tab: k })} className={`rounded-lg border px-4 py-3 ${k === tab ? "border-accent bg-accent/10" : "border-border bg-panel hover:border-accent/50"}`}>
            <div className="text-sm text-muted">{t(`tab.${k}`)}</div>
            <div className={`num mt-1 text-3xl font-semibold ${k === "attention" && d.counts[k] ? "text-down" : k === "ready" ? "text-up" : ""}`}>{d.counts[k] ?? 0}</div>
            <div className="mt-0.5 text-xs text-muted">{t(`tile.${k}`)}</div>
          </Link>
        ))}
        <Link href={q({ tab: "published" })} className={`rounded-lg border px-4 py-3 ${tab === "published" ? "border-accent bg-accent/10" : "border-border bg-panel hover:border-accent/50"}`}>
          <div className="text-sm text-muted">{t("tab.published")}</div>
          <div className="num mt-1 text-3xl font-semibold">{d.today?.published_today ?? 0}</div>
          <div className="mt-0.5 text-xs text-muted">{t("tile.published")}</div>
        </Link>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-3 text-xs text-muted">
        {(["writing", "trash"] as const).map((k) => (
          <Link key={k} href={q({ tab: k })} className={k === tab ? "text-accent" : "hover:text-text"}>{t(`tab.${k}`)} {d.counts[k] ?? 0}</Link>
        ))}
        <span className="ml-auto flex gap-2">
          <Link href={q({ view: "" })} className={chip(!preview)}>{t("viewList")}</Link>
          <Link href={q({ view: "preview" })} className={chip(preview)}>{t("viewPreview")}</Link>
          {preview && <Link href={q({ pair: pair ? "" : "1" })} className={chip(pair)}>{t("pair")}</Link>}
        </span>
      </div>
      <p className="mb-3 text-sm text-muted">{t(`tabHint.${tab}`)}</p>
      {tab === "ready" && (
        <div className="mb-3 rounded border border-border bg-panel px-3 py-2 text-xs">
          {d.robots.length ? <>
            <b className="text-text">{t("robots", { n: d.counts.ready ?? 0 })}</b>
            <span className="ml-2 text-muted">{d.robots.map((r) => t("robotSeen", { name: r.name, at: fmtTime(r.last_used_at, locale) })).join(" · ")}</span>
          </> : <span className="text-muted">{t("noRobots")}</span>}
        </div>
      )}
      <div className="mb-2 flex flex-wrap gap-2">
        <Link href={q({ venue: "" })} className={chip(!sp.venue)}>{t("allVenues")}</Link>
        {VENUES.map((v) => <Link key={v} href={q({ venue: v })} className={chip(sp.venue === v)}>{t(`venue.${v}`)}</Link>)}
        <span className="mx-1 border-l border-border" />
        <Link href={q({ language: "" })} className={chip(!sp.language)}>{t("allLanguages")}</Link>
        <Link href={q({ language: "zh" })} className={chip(sp.language === "zh")}>中文</Link>
        <Link href={q({ language: "en" })} className={chip(sp.language === "en")}>English</Link>
      </div>
      <form className="mb-5 flex flex-wrap gap-2 text-sm">
        {Object.entries(sp).filter(([k, v]) => v && ["tab", "venue", "language", "view", "pair"].includes(k))
          .map(([k, v]) => <input key={k} type="hidden" name={k} value={v} />)}
        <select name="account_id" defaultValue={sp.account_id ?? ""} className={`${input} w-48`}>
          <option value="">{t("allAccounts")}</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>@{a.handle}</option>)}
        </select>
        <select name="agent" defaultValue={sp.agent ?? ""} className={`${input} w-40`}>
          <option value="">{t("allAgents")}</option>
          {d.agents.map((k) => <option key={k} value={k}>{named(tr, k).name}</option>)}
        </select>
        <select name="style" defaultValue={sp.style ?? ""} className={`${input} w-44`}>
          <option value="">{t("allStyles")}</option>
          {d.styles.map((s) => <option key={s.key} value={s.key}>{styleLabel(d.styles, s.key)}</option>)}
        </select>
        {(tab === "ready" || tab === "review" || tab === "attention") && (
          <select name="within" defaultValue={sp.within ?? ""} className={`${input} w-40`}>
            <option value="">{t("anyTimeLeft")}</option>
            {[30, 60, 180, 1440].map((m) => <option key={m} value={m}>{t(`within.${m}`)}</option>)}
          </select>
        )}
        <button className={button}>{t("filter")}</button>
      </form>
      {d.cards.length === 0 ? <Empty>{t(`empty.${tab}`)}</Empty> : !preview ? (
        <PostList cards={d.cards} now={now} roleNames={roleNames} columnNames={columnNames}
          bulk={tab === "review" || tab === "attention" ? ["approve", "kill"] : tab === "ready" ? ["kill"] : []} />
      ) : pair ? (
        <div className="space-y-6">
          {pairs(d.cards, d.siblings ?? []).map(({ event, zh, en }) => {
            const card = (c: Card | undefined, lang: "zh" | "en") => c ? (
              <PostCard c={c} now={now} roleName={named(tr, c.agent_key).name} className=""
                statusName={c.status !== "approved" || tab !== "ready" ? (ts.has(c.status) ? ts(c.status) : c.status) : undefined} />
            ) : <Empty>{t("noPartner", { lang: t(lang === "zh" ? "langZh" : "langEn") })}</Empty>;
            const only = (a?: Card, b?: Card) => (a?.facts_used ?? []).filter((f) => !(b?.facts_used ?? []).includes(f));
            const [onlyZh, onlyEn] = [only(zh, en), only(en, zh)];
            return (
              <div key={event} className="rounded-lg border border-border/60 p-3">
                <div className="mb-2 text-xs text-muted">
                  #{event} · {tc.has((zh ?? en)!.event_class) ? tc((zh ?? en)!.event_class) : (zh ?? en)!.event_class} · {(zh ?? en)!.coin}
                  {zh && en && (onlyZh.length + onlyEn.length === 0
                    ? <span className="ml-2 text-up">{t("factsSame")}</span>
                    : <span className="ml-2 text-warn">{t("factsDiff")}
                      {onlyZh.length > 0 && <> · {t("onlyIn", { lang: t("langZh"), facts: onlyZh.map((f) => factName(tf, f)).join("、") })}</>}
                      {onlyEn.length > 0 && <> · {t("onlyIn", { lang: t("langEn"), facts: onlyEn.map((f) => factName(tf, f)).join("、") })}</>}
                    </span>)}
                </div>
                <div className="grid gap-4 lg:grid-cols-2">{card(zh, "zh")}{card(en, "en")}</div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="grid gap-5 lg:grid-cols-2 2xl:grid-cols-3">
          {d.cards.map((c) => <PostCard key={c.id} c={c} now={now} roleName={named(tr, c.agent_key).name} className="" />)}
        </div>
      )}
    </>
  );
}

/** One row per event: the tab's own posts first, the other language filled in from anywhere else. */
function pairs(cards: Card[], siblings: Card[]) {
  const by = new Map<number, { event: number; zh?: Card; en?: Card }>();
  for (const c of [...cards, ...siblings]) {
    const g = by.get(c.event_id) ?? { event: c.event_id };
    const lang = c.language === "zh" ? "zh" : "en";
    if (!g[lang]) g[lang] = c;
    by.set(c.event_id, g);
  }
  return [...by.values()].filter((g) => cards.some((c) => c.event_id === g.event));
}
