import { getLocale, getTranslations } from "next-intl/server";
import { ClassLabel, StateBadge } from "@/components/badges";
import { Action } from "@/components/Action";
import { AiStudio } from "@/components/AiStudio";
import { AutoRefresh } from "@/components/AutoRefresh";
import { DeskEditor } from "@/components/DeskEditor";
import { ScheduleForm } from "@/components/ScheduleForm";
import { FactsTable } from "@/components/FactsTable";
import { CardPanel, PublishForm, ReviewButtons } from "@/components/PublishPanel";
import { Badge, Empty, KV, PageHeader, Panel } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { RateVersion, type Rating } from "@/components/RateVersion";
import { nrGet, nrGetOptional } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { platformName } from "@/lib/glossary";
import type { Account, Me, Packet, Version } from "@/lib/types";

type ArticleDetail = {
  article: { id: number; news_event_id: number; account_id: number; handle: string; account_name: string; language: string; status: string;
    current_version_id: number | null; current_fact_packet_id: number; platform: string; current_fact_version: number; gate_status: string;
    event_class: string; coin: string; authorization_status: string; authorization_expires_at: string | null; stale_reason: string | null;
    deleted_at: string | null; deleted_reason: string | null; source: string | null; pending_jobs: number };
  versions: Version[];
  publications: { id: number; x_url: string; published_at: string; status: string; matches_version: boolean }[];
  audit: { at: string; action: string; username: string | null; reason: string | null }[];
};
type EventDetail = { packet: { id: number; content: Packet } | null; articles: { id: number; handle: string; language: string; status: string; headline: string | null }[];
  evaluations: { output: { explanation: string; recommended_accounts: { account_id: number; angle: string; reason: string }[]; silent_accounts: { account_id: number; reason: string }[] } }[] };

const EDITABLE = ["drafting", "draft_ready", "changes_requested", "stale", "in_review"];

export default async function DeskPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [t, tc, ts, tm, tt, tdr, tr, tp, locale] = await Promise.all([getTranslations("desk"), getTranslations("common"),
    getTranslations("studio"), getTranslations("manage"), getTranslations("trial"), getTranslations("drafts"),
    getTranslations("rating"), getTranslations("publish"), getLocale()]);
  const d = await nrGetOptional<ArticleDetail>(`articles/${id}`);
  if (!d) return <Empty>{tc("noAccess")}</Empty>;
  const a = d.article;
  const [ev, me, accounts, models, ratings] = await Promise.all([nrGet<EventDetail>(`events/${a.news_event_id}`), nrGet<Me>("auth/me"),
    nrGet<Account[]>("accounts"), nrGet<{ id: number; name: string; model: string; last_test_ok: boolean | null }[]>("ai/model-options"),
    nrGet<Rating[]>(`articles/${a.id}/ratings`)]);
  const current = d.versions.find((v) => v.id === a.current_version_id) ?? null;
  const packet = ev.packet!.content;
  const perms = new Set(me.roles.some((r) => ["admin", "editor_in_chief"].includes(r)) ? ["view", "draft", "review", "publish"] : me.account_permissions[String(a.account_id)] ?? []);
  const evaluation = ev.evaluations[0]?.output;
  const myRec = evaluation?.recommended_accounts.find((r) => r.account_id === a.account_id);
  const postText = current ? [current.body, ...(current.thread ?? [])].join("\n\n") : "";
  const account = accounts.find((x) => x.id === a.account_id);
  const factIds = new Set(packet.facts.map((f) => f.id));
  const templates = factIds.has("chart") ? ["chart_card"]
    : [...(packet.event.class === "liquidation" ? ["liq_card"] : []), ...(["whale_close", "liquidation"].includes(packet.event.class) ? ["close_card"] : ["position_card"]),
      ...(factIds.has("episode.path") ? ["lifecycle_card"] : [])];
  return (
    <>
      <PageHeader
        title={<span>{t("title")} · #{a.id} @{a.handle}</span>}
        subtitle={<span><Link className="text-accent" href={`/radar/${a.news_event_id}`}>#{a.news_event_id}</Link> <ClassLabel cls={a.event_class} /> · {a.coin} · {a.language}</span>}
      >
        <div className="flex flex-wrap items-center gap-2">
          <StateBadge state={a.status} />
          <AutoRefresh active={a.pending_jobs > 0} label={`${tdr("refreshing")} (${a.pending_jobs})`} />
          <a className="text-sm text-accent" href={`/api/bff/articles/${a.id}/export`}>{tm("export")}</a>
          {perms.has("draft") && !["published", "retracted"].includes(a.status) && (a.deleted_at
            ? <Action path={`articles/${a.id}/restore`}>{tm("restore")}</Action>
            : <Action path={`articles/${a.id}/trash`} body={{ reason: "" }} prompt={{ label: tm("trashReason"), field: "reason" }} tone="danger">{tm("trash")}</Action>)}
        </div>
      </PageHeader>
      {a.source === "trial" && <div className="mb-3 rounded border border-warn/40 bg-warn/10 px-4 py-2 text-sm text-warn">{tt("banner")}</div>}
      {a.deleted_at && <div className="mb-3 rounded border border-down/40 bg-down/10 px-4 py-2 text-sm">{tm("deleted")} · {fmtTime(a.deleted_at, locale)} {a.deleted_reason}</div>}
      <p className="mb-4 text-xs text-muted">{t("aiNote")}</p>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_minmax(0,1fr)]">
        <Panel title={t("facts")}>
          <FactsTable packet={packet} compact />
          {evaluation && (
            <div className="mt-4 space-y-1 border-t border-border pt-3 text-xs">
              <div className="text-muted">EIC</div>
              <p>{evaluation.explanation}</p>
              {myRec && <p className="text-accent">{myRec.angle} — {myRec.reason}</p>}
            </div>
          )}
          <div className="mt-4 border-t border-border pt-3 text-xs">
            <div className="mb-1 text-muted">{t("otherArticles")}</div>
            {ev.articles.filter((x) => x.id !== a.id).map((x) => (
              <div key={x.id}><Link className="text-accent" href={`/desk/${x.id}`}>#{x.id}</Link> @{x.handle} {x.language} · {x.headline}</div>
            ))}
          </div>
        </Panel>
        <div className="space-y-5">
          <Panel title={t("draft")}>
            <DeskEditor articleId={a.id} status={a.status} current={current} versions={d.versions} facts={packet.facts}
              currentPacketId={a.current_fact_packet_id} editable={perms.has("draft") && EDITABLE.includes(a.status) && !a.deleted_at} />
          </Panel>
          {current && (
            <Panel title={`${tr("title")} · v${current.version_no}`} hint={tr("hint")}>
              <RateVersion articleId={a.id} versionId={current.id} ratings={ratings} />
            </Panel>
          )}
          <Panel title={ts("aiActions")}>
            {perms.has("draft") && ["draft_ready", "changes_requested", "in_review", "approved", "stale"].includes(a.status) && !a.deleted_at && (
              <div className="mb-4 flex flex-wrap items-center gap-3 border-b border-border pb-4">
                <Action path={`articles/${a.id}/revise`} tone="primary">{tp("revise")}</Action>
                <span className="text-xs text-muted">{tp("reviseHint")}</span>
              </div>
            )}
            <AiStudio articleId={a.id} models={models} editable={perms.has("draft") && EDITABLE.includes(a.status) && !a.deleted_at} />
          </Panel>
        </div>
        <div className="space-y-5">
          <Panel title={t("publish")}>
            <KV k={tc("account")} v={`${platformName(a.platform)} · @${a.handle} · ${a.account_name}`} />
            <KV k={t("authorization")} v={<Badge tone={a.authorization_status === "authorized" ? "up" : ["revoked", "expired"].includes(a.authorization_status) ? "down" : "neutral"}>{a.authorization_status}</Badge>} />
            <KV k={t("expires")} v={fmtTime(a.authorization_expires_at, locale, account?.timezone)} />
            {account && <p className="mt-2 text-xs text-muted">{account.positioning}</p>}
            <div className="mt-4"><ReviewButtons articleId={a.id} status={a.status} /></div>
            {a.status === "approved" && perms.has("publish") && (
              <div className="mt-4 space-y-3 border-t border-border pt-4">
                <div className="text-xs text-muted">{ts("schedule")}</div>
                <ScheduleForm articleId={a.id} />
                <PublishForm articleId={a.id} handle={a.handle} text={postText} platform={a.platform} />
              </div>
            )}
            {d.publications.map((p) => (
              <div key={p.id} className="mt-3 text-xs">
                <a className="text-accent" href={p.x_url} target="_blank" rel="noreferrer">{p.x_url}</a> · {fmtTime(p.published_at, locale)} · <StateBadge state={p.status} />
              </div>
            ))}
          </Panel>
          <Panel title={t("card")}><CardPanel packetId={a.current_fact_packet_id} language={a.language} templates={templates} /></Panel>
          <Panel title="Audit">
            {d.audit.map((x, i) => <div key={i} className="text-xs"><span className="text-muted">{fmtTime(x.at, locale)}</span> {x.action} {x.username} {x.reason}</div>)}
          </Panel>
        </div>
      </div>
    </>
  );
}
