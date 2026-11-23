import { getLocale, getTranslations } from "next-intl/server";
import { Action } from "@/components/Action";
import { AssignForm } from "@/components/AssignForm";
import { AutoRefresh } from "@/components/AutoRefresh";
import { TrialPanel } from "@/components/TrialPanel";
import { ClassLabel, GateBadge, StateBadge } from "@/components/badges";
import { FactsTable } from "@/components/FactsTable";
import { Badge, Empty, KV, PageHeader, Panel, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import { fmtTime, fmtUsd, shortKey } from "@/lib/format";
import type { Account, NewsEvent, Packet } from "@/lib/types";

type Detail = {
  event: NewsEvent & { episode_id: number };
  packet: { id: number; version: number; content: Packet; content_hash: string; change_summary: { changed: string[] } | null } | null;
  packets: { id: number; version: number; built_at: string; gate_status: string; change_summary: { changed: string[] } | null }[];
  episode: Record<string, string | number | null> | null;
  source_events: { id: number; source_event_id: number; kind: string; side: string; szi_before: string; szi_after: string; notional_change: string; mark_px: string | null; time_precision: string; window_start: string | null; observed_at: string }[];
  evidence: { kind: string; ref_id: number; captured_at: string }[];
  history: { from_state: string | null; to_state: string; at: string; actor_kind: string; username: string | null; reason: string | null }[];
  evaluations: { id: number; decision: string; priority: string; created_at: string; output: { explanation: string; newsworthiness: { factor: string; assessment: string; fact_ids: string[] }[]; recommended_accounts: { account_id: number; angle: string; reason: string; format: string; urgency: string }[]; silent_accounts: { account_id: number; reason: string }[]; follow_up: { watch_for: string[]; story: string }; risk_flags: string[] } }[];
  assignments: { id: number; account_id: number; handle: string; language: string; angle: string; reason: string; format: string; source: string; status: string; needs_human: boolean }[];
  articles: { id: number; handle: string; platform: string; language: string; status: string; headline: string | null; guard_status: string | null;
    fact_packet_version: number | null; origin: string | null; model: string | null; source: string | null; deleted_at: string | null; versions: number }[];
  pending_drafts: number;
  story: { id: number; story_key: string } | null;
  corrections: { id: number; status: string; material: boolean; changed_facts: string[]; detected_at: string }[];
};

export default async function EventPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [t, tt, tdr, locale, d, accounts, models] = await Promise.all([getTranslations("events"), getTranslations("trial"),
    getTranslations("drafts"), getLocale(), nrGet<Detail>(`events/${id}`), nrGet<Account[]>("accounts"),
    nrGet<{ id: number; name: string; model: string; last_test_ok: boolean | null }[]>("ai/model-options")]);
  const e = d.event;
  const handle = (aid: number) => accounts.find((a) => a.id === aid)?.handle ?? aid;
  const bd = e.score_breakdown;
  return (
    <>
      <PageHeader
        title={<span>#{e.id} <ClassLabel cls={e.event_class} /> · {e.coin} {e.direction}</span>}
        subtitle={`${e.venue} · ${e.public_label ?? e.display_name ?? shortKey(e.trader_key)} · ${fmtTime(e.detected_at, locale)}`}
      >
        <div className="flex flex-wrap items-start gap-2">
          <StateBadge state={e.state} /><GateBadge status={e.gate_status} />
          {["candidate", "evaluated"].includes(e.state) && e.gate_status === "pass" && (
            <Action path={`events/${e.id}/evaluate`} tone="primary">{t("evaluate")}</Action>
          )}
          <Action path={`events/${e.id}/refresh`}>{t("refreshFacts")}</Action>
          {["candidate", "evaluated", "assigned"].includes(e.state) && (
            <Action path={`events/${e.id}/state`} body={{ to: "declined" }} prompt={{ label: t("decline"), field: "reason" }}>{t("decline")}</Action>
          )}
          {["declined", "dismissed"].includes(e.state) && (
            <Action path={`events/${e.id}/state`} body={{ to: "candidate", reason: "reopened" }}>{t("reopen")}</Action>
          )}
        </div>
      </PageHeader>
      {e.blocking_issues.length > 0 && (
        <div className="mb-4 rounded border border-down/40 bg-down/10 px-4 py-3 text-sm">
          <b>{t("blocking")}:</b> {e.blocking_issues.map((b) => `${b.code} (${b.detail})`).join("; ")}
        </div>
      )}
      <div className="grid gap-5 xl:grid-cols-3">
        <div className="space-y-5 xl:col-span-2">
          <Panel title={t("facts")}>{d.packet ? <FactsTable packet={d.packet.content} /> : <Empty>—</Empty>}</Panel>
          <Panel title={t("evaluations")}>
            {d.evaluations.length === 0 ? <Empty>—</Empty> : d.evaluations.map((ev) => (
              <div key={ev.id} className="mb-4 space-y-2 border-b border-border pb-4 text-sm last:border-0">
                <div className="flex gap-2"><Badge tone="accent">{ev.decision}</Badge><Badge>{ev.priority}</Badge>
                  {ev.output.risk_flags.map((r) => <Badge key={r} tone="warn">{r}</Badge>)}
                  <span className="text-xs text-muted">{fmtTime(ev.created_at, locale)}</span></div>
                <p>{ev.output.explanation}</p>
                <ul className="text-xs text-muted">
                  {ev.output.newsworthiness.map((n, i) => <li key={i}>· <b>{n.factor}</b>: {n.assessment} <span className="font-mono">[{n.fact_ids.join(", ")}]</span></li>)}
                </ul>
                <div className="grid gap-2 md:grid-cols-2">
                  <div><div className="text-xs text-muted">{t("recommended")}</div>
                    {ev.output.recommended_accounts.map((r) => <div key={r.account_id}>@{handle(r.account_id)}: {r.angle} <span className="text-xs text-muted">({r.format}, {r.urgency}) {r.reason}</span></div>)}</div>
                  <div><div className="text-xs text-muted">{t("silent")}</div>
                    {ev.output.silent_accounts.map((r) => <div key={r.account_id}>@{handle(r.account_id)}: <span className="text-muted">{r.reason}</span></div>)}</div>
                </div>
                <div className="text-xs text-muted">{t("followUp")}: {ev.output.follow_up.watch_for.join(", ")} ({ev.output.follow_up.story})</div>
              </div>
            ))}
          </Panel>
          <Panel title={t("assignments")}>
            <table className="mb-4 w-full text-sm">
              <tbody>
                {d.assignments.map((a) => (
                  <tr key={a.id} className="border-b border-border/60 align-top">
                    <td className={td}>@{a.handle} · {a.language}</td>
                    <td className={`${td} whitespace-normal`}>{a.angle}<div className="text-xs text-muted">{a.reason}</div>
                      {a.needs_human && <Badge tone="warn">{t("needsHuman")}</Badge>}</td>
                    <td className={td}><Badge>{a.source}</Badge> <StateBadge state={a.status} /></td>
                    <td className={td}>
                      <div className="flex flex-wrap gap-1">
                        {a.status === "proposed" && <Action path={`assignments/${a.id}/accept`} tone="primary">{t("accept")}</Action>}
                        {["proposed", "accepted"].includes(a.status) && <Action path={`assignments/${a.id}/reject`} prompt={{ label: t("reject"), field: "reason" }}>{t("reject")}</Action>}
                        {["accepted", "drafting"].includes(a.status) && <Action path={`assignments/${a.id}/draft`}>{t("aiDraft")}</Action>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {["candidate", "evaluated", "assigned", "covered"].includes(e.state) && (
              <AssignForm eventId={e.id} accounts={accounts.filter((a) => a.status === "active").map((a) => ({ id: a.id, handle: a.handle, language: a.language }))} />
            )}
          </Panel>
          <Panel title={tt("title")}>
            <TrialPanel eventId={e.id} venue={e.venue} models={models}
              accounts={accounts.map((a) => ({ id: a.id, handle: a.handle, platform: a.platform, venues: a.venues ?? [], status: a.status, language: a.language }))} />
          </Panel>
          <Panel title={t("articles")} actions={<AutoRefresh active={d.pending_drafts > 0} label={`${tdr("refreshing")} (${d.pending_drafts})`} />}>
            {d.articles.length === 0 ? <Empty>—</Empty> : (
              <table className="w-full text-sm">
                <tbody>
                  {d.articles.map((a) => (
                    <tr key={a.id} className={`border-b border-border/60 ${a.deleted_at ? "opacity-50" : ""}`}>
                      <td className={td}><Link className="text-accent" href={`/desk/${a.id}`}>{t("openDesk")} #{a.id}</Link></td>
                      <td className={td}>{a.platform === "okx_square" ? "OKX 广场" : "X"} @{a.handle} · {a.language}</td>
                      <td className={`${td} max-w-sm truncate`}>{a.headline}</td>
                      <td className={td}>
                        <StateBadge state={a.status} /> {a.source === "trial" && <Badge tone="warn">{tdr("trial")}</Badge>}
                        {a.guard_status && <Badge tone={a.guard_status === "pass" ? "up" : "down"}>{a.guard_status}</Badge>}
                        <span className="text-xs text-muted"> v×{a.versions}{a.model ? ` · ${a.model}` : ""}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
        </div>
        <div className="space-y-5">
          <Panel title={`${t("scoreBreakdown")} ${bd ? `(${bd.version}) ${bd.total}` : ""}`}>
            {bd ? Object.entries(bd.components).map(([k, c]) => (
              <div key={k} className="mb-2 text-sm">
                <div className="flex justify-between"><span>{k}</span><span className="num">{c.points} / {c.max}</span></div>
                <div className="text-[11px] text-muted">{c.note} {Object.keys(c.inputs).length > 0 && JSON.stringify(c.inputs)}</div>
              </div>
            )) : <Empty>—</Empty>}
          </Panel>
          <Panel title={t("packetVersion")}>
            {d.packets.map((p) => (
              <div key={p.id} className="flex justify-between border-b border-border/60 py-1 text-xs">
                <span>v{p.version} <GateBadge status={p.gate_status} /></span>
                <span className="text-muted">{fmtTime(p.built_at, locale)}</span>
                <span className="max-w-40 truncate font-mono text-muted" title={p.change_summary?.changed.join(", ")}>{p.change_summary?.changed.join(", ")}</span>
              </div>
            ))}
          </Panel>
          <Panel title={t("episode")}>
            {d.episode && ["start_kind", "start_precision", "start_from", "start_to", "status", "peak_notional", "added_notional", "n_increases"].map((k) => (
              <KV key={k} k={k} v={String(k.includes("notional") ? fmtUsd(d.episode![k]) : k.startsWith("start_") && k !== "start_kind" && k !== "start_precision" ? fmtTime(d.episode![k] as string, locale) : d.episode![k] ?? "—")} />
            ))}
          </Panel>
          <Panel title={t("sourceEvents")}>
            <table className="w-full text-xs">
              <thead><tr><th className={th}>kind</th><th className={th}>Δ</th><th className={th}>{t("precision")}</th><th className={th}>{t("observed")}</th></tr></thead>
              <tbody>
                {d.source_events.map((s) => (
                  <tr key={s.id} className="border-b border-border/60">
                    <td className={td}>{s.kind}</td>
                    <td className={`${td} num`}>{fmtUsd(s.notional_change)}</td>
                    <td className={td}>{s.time_precision}</td>
                    <td className={td}>{fmtTime(s.observed_at, locale)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
          <Panel title={t("history")}>
            {d.history.map((h, i) => (
              <div key={i} className="border-b border-border/60 py-1 text-xs">
                <span className="text-muted">{fmtTime(h.at, locale)}</span> {h.from_state ?? "∅"} → <b>{h.to_state}</b> · {h.actor_kind}{h.username ? ` ${h.username}` : ""}
                {h.reason && <div className="text-muted">{h.reason}</div>}
              </div>
            ))}
          </Panel>
          {d.story && <Panel title={t("story")}><Link className="text-accent" href={`/stories/${d.story.id}`}>{d.story.story_key}</Link></Panel>}
          {d.corrections.length > 0 && (
            <Panel title={t("corrections")}>
              {d.corrections.map((c) => (
                <div key={c.id} className="text-xs">#{c.id} <StateBadge state={c.status} /> {c.material ? "material" : ""} {c.changed_facts.join(", ")}</div>
              ))}
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}
