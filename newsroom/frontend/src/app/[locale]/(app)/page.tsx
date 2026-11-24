import { getLocale, getTranslations } from "next-intl/server";
import { Action } from "@/components/Action";
import { AiFixes } from "@/components/AiFixes";
import { AiReadiness } from "@/components/AiReadiness";
import { Onboarding } from "@/components/Onboarding";
import { ClassLabel, StateBadge } from "@/components/badges";
import { Badge, Empty, PageHeader, Panel, Stat, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import { fmtTime, shortKey } from "@/lib/format";

type Dashboard = {
  source: { reachable: boolean; radar: Record<string, string>; backlog: number | null; last_sync_ok_at: string | null; health_error: string | null };
  emergency_stop: { active: boolean; reason: string; since: string | null };
  ai: { enabled: boolean; cost_today: number; budget_today: number; queued: number; failed_24h: number; models: { id: number; name: string; model: string; last_test_ok: boolean | null }[] };
  states: Record<string, number>;
  top_candidates: { id: number; event_class: string; coin: string; venue: string; direction: string; score: number; priority: string | null; state: string; detected_at: string; trader_key: string; display_name: string | null }[];
  pending_review: { id: number; status: string; handle: string; language: string; headline: string | null; updated_at: string }[];
  publishing: { proposed: number; metrics_due: number; ready: number; expiring_soon: number; todo: number; reviewer_rejected: number; expired_today: number; published_today: number };
  accounts: { id: number; handle: string; display_name: string; status: string; authorization_status: string; authorization_expires_at: string | null; daily_post_budget: number; published_today: number; ready: number }[];
  open_corrections: number;
  expiring_authorizations: { id: number; handle: string; authorization_expires_at: string }[];
};

export default async function DashboardPage() {
  const tw = await getTranslations("statusWords");
  const [t, ts, locale, d] = await Promise.all([getTranslations("dashboard"), getTranslations("states"), getLocale(),
    nrGet<Dashboard>("dashboard")]);
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        {d.emergency_stop.active ? (
          <Action path="settings/emergency-stop" body={{ active: false, reason: "" }} tone="primary">{t("emergencyOff")}</Action>
        ) : (
          <Action path="settings/emergency-stop" body={{ active: true }} prompt={{ label: t("emergencyReason"), field: "reason" }}
            tone="danger" confirm={t("emergencyHint")}>{t("emergencyOn")}</Action>
        )}
      </PageHeader>
      <Onboarding />
      <AiReadiness />
      <AiFixes />
      <div className="mb-2 text-sm font-semibold">{t("publishingToday")}</div>
      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {d.publishing.proposed > 0 && (
          <Link href="/assignments" className="hover:opacity-90">
            <Stat label={t("pub.proposed")} value={d.publishing.proposed} sub={t("proposedHint")} />
          </Link>
        )}
        {d.publishing.metrics_due > 0 && (
          <Link href="/publish?tab=published" className="hover:opacity-90">
            <Stat label={t("pub.metricsDue")} value={d.publishing.metrics_due} sub={t("metricsDueHint")} />
          </Link>
        )}
        {([["ready", "ready", d.publishing.ready, d.publishing.expiring_soon ? t("expiringSoon", { n: d.publishing.expiring_soon }) : undefined],
           ["published", "published", d.publishing.published_today, undefined],
           ["todo", "review", d.publishing.todo, undefined],
           ["rejected", "attention", d.publishing.reviewer_rejected, t("rejectedHint")],
           ["expired", "trash", d.publishing.expired_today, undefined]] as const).map(([k, tab, n, sub]) => (
          <Link key={k} href={`/publish?tab=${tab}`} className="hover:opacity-90">
            <Stat label={t(`pub.${k}`)} value={n} sub={sub} />
          </Link>
        ))}
      </div>
      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label={t("source")} value={d.source.reachable ? t("reachable") : t("unreachable")} sub={d.source.health_error ?? undefined} />
        <Stat label={t("radar")} value={`HL ${d.source.radar.hyperliquid} · OKX ${d.source.radar.okx}`} />
        <Stat label={t("backlog")} value={d.source.backlog ?? "—"} sub={`${t("lastSync")} ${fmtTime(d.source.last_sync_ok_at, locale)}`} />
        <Stat label={t("costToday")} value={`$${d.ai.cost_today.toFixed(2)} / $${d.ai.budget_today}`} sub={d.ai.enabled ? t("aiOn") : t("aiOff")} />
        <Stat label={t("queued")} value={d.ai.queued} sub={`${t("failed")}: ${d.ai.failed_24h}`} />
        <Stat label={t("corrections")} value={d.open_corrections} />
      </div>
      <div className="grid gap-5 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <Panel title={t("candidates")}>
            {d.top_candidates.length === 0 ? <Empty>—</Empty> : (
              <table className="w-full text-sm">
                <tbody>
                  {d.top_candidates.map((e) => (
                    <tr key={e.id} className="border-b border-border/60">
                      <td className={td}><Link className="text-accent" href={`/radar/${e.id}`}>#{e.id}</Link></td>
                      <td className={td}><ClassLabel cls={e.event_class} /> · {e.coin} {e.direction}</td>
                      <td className={td}>{e.display_name ?? shortKey(e.trader_key)}</td>
                      <td className={`${td} num`}>{Number(e.score).toFixed(1)}</td>
                      <td className={td}>{e.priority && <Badge tone="accent">{e.priority}</Badge>}</td>
                      <td className={td}><StateBadge state={e.state} /></td>
                      <td className={`${td} text-xs text-muted`}>{fmtTime(e.detected_at, locale)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
        </div>
        <Panel title={t("states")}>
          <div className="flex flex-wrap gap-2">
            {Object.entries(d.states).map(([k, v]) => <Badge key={k}>{ts.has(k) ? ts(k) : k}: {v}</Badge>)}
          </div>
        </Panel>
        <div className="xl:col-span-2">
          <Panel title={t("pendingReview")}>
            {d.pending_review.length === 0 ? <Empty>—</Empty> : (
              <table className="w-full text-sm">
                <tbody>
                  {d.pending_review.map((a) => (
                    <tr key={a.id} className="border-b border-border/60">
                      <td className={td}><Link className="text-accent" href={`/desk/${a.id}`}>#{a.id}</Link></td>
                      <td className={td}>@{a.handle} · {a.language}</td>
                      <td className={`${td} max-w-md truncate`}>{a.headline}</td>
                      <td className={td}><StateBadge state={a.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
        </div>
        <Panel title={t("accounts")}>
          <table className="w-full text-sm">
            <thead><tr><th className={th}>@</th><th className={th}>{t("publishedToday")}</th><th className={th}>{t("readyCol")}</th><th className={th} /></tr></thead>
            <tbody>
              {d.accounts.map((a) => (
                <tr key={a.id} className="border-b border-border/60">
                  <td className={td}>{a.handle}</td>
                  <td className={`${td} num ${a.published_today >= a.daily_post_budget ? "text-down" : ""}`}
                    title={t("budgetLeft", { n: Math.max(0, a.daily_post_budget - a.published_today) })}>{a.published_today}/{a.daily_post_budget}</td>
                  <td className={`${td} num`}><Link className="text-accent" href={`/publish?tab=ready&account_id=${a.id}`}>{a.ready}</Link></td>
                  <td className={td}><StateBadge state={a.status} /> <Badge tone={a.authorization_status === "authorized" ? "up" : ["revoked", "expired"].includes(a.authorization_status) ? "down" : "neutral"}>{tw(a.authorization_status)}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
          {d.expiring_authorizations.length > 0 && (
            <p className="mt-3 text-xs text-warn">{t("expiring")}: {d.expiring_authorizations.map((a) => `@${a.handle} ${fmtTime(a.authorization_expires_at, locale)}`).join(", ")}</p>
          )}
        </Panel>
      </div>
    </>
  );
}
