import { getTranslations } from "next-intl/server";
import { AutoRefresh } from "@/components/AutoRefresh";
import { PostCard, type Card } from "@/components/PostCard";
import { Empty, PageHeader, button, input } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import { named } from "@/lib/glossary";
import type { Account } from "@/lib/types";

const TABS = ["ready", "todo", "published", "expired", "trash"] as const;
type D = { tab: string; cards: Card[]; counts: Record<string, number>; now: string; today: { published_24h: number };
  styles: { key: string; name: string; language: string }[] };

export default async function PublishPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const tab = (TABS as readonly string[]).includes(sp.tab ?? "") ? sp.tab! : "ready";
  const [t, tr, tc, d, accounts] = await Promise.all([getTranslations("publish"), getTranslations("roles"), getTranslations("classes"),
    nrGet<D>("publish", { tab, language: sp.language, account_id: sp.account_id, style: sp.style, event_class: sp.event_class }),
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
      <p className="mb-3 text-xs text-muted">{t(`tabHint.${tab}`)}</p>
      <form className="mb-5 flex flex-wrap gap-2 text-sm">
        <input type="hidden" name="tab" value={tab} />
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
      {d.cards.length === 0 ? <Empty>{t(`empty.${tab}`)}</Empty> : (
        <div className="grid gap-5 lg:grid-cols-2 2xl:grid-cols-3">
          {d.cards.map((c) => <PostCard key={c.id} c={c} now={now} roleName={named(tr, c.agent_key).name} className="" />)}
        </div>
      )}
    </>
  );
}
