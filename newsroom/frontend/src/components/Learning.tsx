import { getLocale, getTranslations } from "next-intl/server";
import { Action } from "./Action";
import { Badge, Empty, Panel } from "./ui";
import { Link } from "@/i18n/routing";
import { nrGetOptional } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { named } from "@/lib/glossary";

type Candidate = { id: number; style_key: string; style_name: string | null; handle: string; original: string; example: string;
  reason: "review" | "engagement"; score: Record<string, number>; created_at: string };
type Scores = { n: number; publishable: number | null; hook: number | null; native: number | null; ai_tone: number | null;
  clarity: number | null; approved: number } | null;
type Proposal = { id: number; prompt_key: string; language: string | null; base_version: number; version: number;
  rationale: string; changes: { problem: string; change: string }[]; issues: { n_reviews: number; scores: Record<string, number> };
  status: string; experiment_name: string | null; created_at: string; agents: string[] | null;
  experiment?: { pairs: number; ready: number; votes: number; b_win_rate: number | null;
    guard_pass: Record<string, number | null> | null; ai_scores: { a: Scores; b: Scores } } };

/** Posts the reviewer or readers liked, as placeholder examples a person adopts into their style. */
export async function ExampleCandidates() {
  const [t, locale] = await Promise.all([getTranslations("learning"), getLocale()]);
  const list = await nrGetOptional<Candidate[]>("learning/examples");
  if (!list) return null;
  return (
    <Panel title={t("examplesTitle")} hint={t("examplesHint")}>
      {list.length === 0 ? <Empty>{t("noCandidates")}</Empty> : (
        <div className="space-y-4">
          {list.map((c) => (
            <div key={c.id} className="rounded border border-border p-3 text-sm">
              <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                <Badge tone="accent">{c.style_name ?? c.style_key}</Badge>
                <Badge tone={c.reason === "review" ? "up" : "warn"}>{t(`reason.${c.reason}`)}</Badge>
                <span className="text-muted">@{c.handle} · {fmtTime(c.created_at, locale)}</span>
                <span className="text-muted">{c.reason === "review" ? t("reviewScore", { n: c.score.publishable ?? "—" })
                  : t("engagementScore", { n: c.score.engagement ?? "—", m: c.score.account_median ?? "—" })}</span>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                <div><div className="mb-1 text-xs text-muted">{t("original")}</div><p className="whitespace-pre-wrap">{c.original}</p></div>
                <div><div className="mb-1 text-xs text-muted">{t("asExample")}</div><p className="whitespace-pre-wrap text-accent">{c.example}</p></div>
              </div>
              <div className="mt-3 flex gap-2">
                <Action path={`learning/examples/${c.id}/adopt`} tone="primary">{t("adopt")}</Action>
                <Action path={`learning/examples/${c.id}/dismiss`}>{t("dismiss")}</Action>
              </div>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

const fmt = (v: number | null | undefined) => (v === null || v === undefined ? "—" : String(v));

/** The prompt coach's weekly drafts, with the experiment that compares each with the current version. */
export async function PromptProposals() {
  const [t, tr, locale] = await Promise.all([getTranslations("learning"), getTranslations("roles"), getLocale()]);
  const list = await nrGetOptional<Proposal[]>("learning/proposals");
  if (!list) return null;
  return (
    <Panel title={t("proposalsTitle")} hint={t("proposalsHint")}>
      <div className="mb-3"><Action path="learning/proposals/run" confirm={t("runConfirm")}>{t("runNow")}</Action></div>
      {list.length === 0 ? <Empty>{t("noProposals")}</Empty> : (
        <div className="space-y-4">
          {list.map((p) => {
            const e = p.experiment;
            return (
              <div key={p.id} className="rounded border border-border p-3 text-sm">
                <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                  <b>{(p.agents ?? [p.prompt_key]).map((k) => named(tr, k).name).join("、")}</b>
                  <Badge>{p.prompt_key} v{p.base_version} → v{p.version}{p.language ? ` · ${p.language === "zh" ? "中文" : "English"}` : ""}</Badge>
                  <Badge tone={p.status === "adopted" ? "up" : p.status === "rejected" ? "down" : "warn"}>{t(`status.${p.status}`)}</Badge>
                  <span className="text-muted">{t("fromReviews", { n: p.issues.n_reviews })} · {fmtTime(p.created_at, locale)}</span>
                </div>
                <p className="mb-2">{p.rationale}</p>
                <ul className="mb-2 list-disc pl-5 text-xs">
                  {p.changes.map((c, i) => <li key={i}><span className="text-muted">{c.problem}</span> → {c.change}</li>)}
                </ul>
                {e ? (
                  <div className="mb-2 rounded bg-panel p-2 text-xs">
                    <div className="mb-1">{t("experiment", { ready: e.ready, pairs: e.pairs })}{" "}
                      {p.experiment_name && <Link className="text-accent" href={`/experiments/${p.experiment_name}`}>{t("openExperiment")}</Link>}</div>
                    <table className="text-xs"><thead><tr><th className="pr-4 text-left text-muted" />
                      {["publishable", "hook", "native", "ai_tone", "clarity"].map((d) => <th key={d} className="pr-4 text-left text-muted">{t(`dim.${d}`)}</th>)}
                      <th className="pr-4 text-left text-muted">{t("guardPass")}</th></tr></thead>
                      <tbody>{(["a", "b"] as const).map((arm) => (
                        <tr key={arm}><td className="pr-4">{arm === "a" ? t("current", { v: p.base_version }) : t("draft", { v: p.version })}</td>
                          {(["publishable", "hook", "native", "ai_tone", "clarity"] as const).map((d) => <td key={d} className="pr-4 num">{fmt(e.ai_scores[arm]?.[d])}</td>)}
                          <td className="num">{e.guard_pass?.[arm] !== null && e.guard_pass?.[arm] !== undefined ? `${Math.round((e.guard_pass[arm] as number) * 100)}%` : "—"}</td></tr>
                      ))}</tbody></table>
                    {e.votes > 0 && <div className="mt-1">{t("votes", { n: e.votes, w: e.b_win_rate !== null ? `${Math.round(e.b_win_rate * 100)}%` : "—" })}</div>}
                  </div>
                ) : <p className="mb-2 text-xs text-muted">{t("noExperiment")}</p>}
                {["draft", "testing"].includes(p.status) && (
                  <div className="flex gap-2">
                    <Action path={`learning/proposals/${p.id}/adopt`} tone="primary" confirm={t("adoptConfirm", { v: p.version })}>{t("adoptVersion", { v: p.version })}</Action>
                    <Action path={`learning/proposals/${p.id}/reject`}>{t("reject")}</Action>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}
