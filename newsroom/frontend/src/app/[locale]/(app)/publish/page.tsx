import { getTranslations } from "next-intl/server";
import { AutoRefresh } from "@/components/AutoRefresh";
import { PostCard, type Card } from "@/components/PostCard";
import { Empty, PageHeader, button, input } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import { factName, named } from "@/lib/glossary";
import type { Account } from "@/lib/types";

const TABS = ["ready", "todo", "published", "expired", "trash"] as const;
type D = { tab: string; cards: Card[]; siblings: Card[]; counts: Record<string, number>; now: string; today: { published_24h: number };
  styles: { key: string; name: string; language: string }[] };

export default async function PublishPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const tab = (TABS as readonly string[]).includes(sp.tab ?? "") ? sp.tab! : "ready";
  const pair = sp.pair === "1";
  const [t, tr, tc, ts, tf, d, accounts] = await Promise.all([getTranslations("publish"), getTranslations("roles"),
    getTranslations("classes"), getTranslations("states"), getTranslations("factNames"),
    nrGet<D>("publish", { tab, language: sp.language, account_id: sp.account_id, style: sp.style, event_class: sp.event_class,
      pair: pair ? "true" : undefined }),
    nrGet<Account[]>("accounts")]);
  const now = new Date(d.now).getTime();
  const q = (extra: Record<string, string>) => {
    const p = new URLSearchParams(Object.entries({ ...sp, ...extra }).filter(([, v]) => v) as [string, string][]);
    return `/publish?${p.toString()}`;
  };
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <AutoRefresh active={tab === "ready" || tab === "todo"} label={t("autoRefresh")} seconds={60} />
      </PageHeader>
      <div className="mb-4 flex flex-wrap gap-2 border-b border-border pb-2">
        {TABS.map((k) => (
          <Link key={k} href={q({ tab: k })} className={`rounded px-3 py-1.5 text-sm ${k === tab ? "bg-accent text-white" : "text-muted hover:text-text"}`}>
            {t(`tab.${k}`)} <span className="opacity-70">{d.counts[k] ?? 0}</span>
          </Link>
        ))}
        <span className="ml-auto self-center text-xs text-muted">{t("today", { n: d.today?.published_24h ?? 0 })}</span>
      </div>
      <p className="mb-3 flex flex-wrap items-center gap-3 text-xs text-muted">
        <span>{t(`tabHint.${tab}`)}</span>
        <Link className="ml-auto text-accent" href={q({ pair: pair ? "" : "1" })}>{pair ? t("unpair") : t("pair")}</Link>
      </p>
      {pair && <p className="-mt-2 mb-3 text-xs text-muted">{t("pairHint")}</p>}
      <form className="mb-5 flex flex-wrap gap-2 text-sm">
        <input type="hidden" name="tab" value={tab} />
        {pair && <input type="hidden" name="pair" value="1" />}
        <select name="language" defaultValue={sp.language ?? ""} className={`${input} w-32`}>
          <option value="">{t("allLanguages")}</option><option value="zh">中文</option><option value="en">English</option>
        </select>
        <select name="account_id" defaultValue={sp.account_id ?? ""} className={`${input} w-48`}>
          <option value="">{t("allAccounts")}</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>@{a.handle}</option>)}
        </select>
        <select name="style" defaultValue={sp.style ?? ""} className={`${input} w-48`}>
          <option value="">{t("allStyles")}</option>
          {d.styles.map((s) => <option key={s.key} value={s.key}>{s.name}（{s.language === "zh" ? "中文" : "EN"}）</option>)}
        </select>
        <select name="event_class" defaultValue={sp.event_class ?? ""} className={`${input} w-40`}>
          <option value="">{t("allClasses")}</option>
          {["whale_open", "whale_add", "underwater_add", "flip", "near_liquidation", "liquidation", "whale_close", "notable_trader_move"]
            .map((c) => <option key={c} value={c}>{tc(c)}</option>)}
        </select>
        <button className={button}>{t("filter")}</button>
      </form>
      {d.cards.length === 0 ? <Empty>{t(`empty.${tab}`)}</Empty> : pair ? (
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
