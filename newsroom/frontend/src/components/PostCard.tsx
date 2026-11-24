"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Link } from "@/i18n/routing";
import { bff, toast } from "@/lib/bff";
import type { Review } from "@/lib/types";
import { ReviewCard } from "./ReviewCard";
import { Badge, button, input, primary } from "./ui";

export type Card = {
  id: number; status: string; language: string; expires_at: string | null; account_id: number; handle: string;
  display_name: string; platform: string; source: string | null; event_id: number; event_class: string; coin: string;
  column_key: string | null; venue: string; n_versions: number; claimed_by: string | null; claimed_at: string | null;
  why: "stale" | "guard" | "rejected" | null;
  version_id: number; headline: string; body: string; thread: string[] | null; reply_text: string | null;
  style_key: string | null; style_name: string | null; model: string | null; agent_key: string | null; guard_status: string;
  facts_used: string[] | null;
  x_length: number | null; length_limit: number | null; card_asset_id: number | null; review: Review | null;
  publication: { x_url: string | null; reply_url: string | null; channel: string; published_at: string;
    matches_version: boolean; guard_status: string | null; warnings: string[] | null } | null;
  plan: { id: number; planned_at: string; status: string } | null; metrics_due: number | null;
};
type Check = { matches_version: boolean; blocked: boolean; findings: { code: string; severity: string; text: string | null }[] };

function remaining(expires: string | null, now: number): { text: string; tone: "up" | "warn" | "down" } | null {
  if (!expires) return null;
  const min = Math.round((new Date(expires).getTime() - now) / 60000);
  if (min <= 0) return { text: "0", tone: "down" };
  return { text: min >= 60 ? `${Math.floor(min / 60)}h${min % 60 ? ` ${min % 60}m` : ""}` : `${min}m`, tone: min < 30 ? "warn" : "up" };
}

/** One post the way it will look on X, with everything needed to get it out by hand or hand it to a robot. */
export function PostCard({ c, now, roleName, className, statusName }: { c: Card; now: number; roleName: string;
  className: string; statusName?: string }) {
  const t = useTranslations("publish");
  const tcol = useTranslations("columnNames");
  const tg = useTranslations("guardCodes");
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [posting, setPosting] = useState(false);
  const [url, setUrl] = useState("");
  const [edited, setEdited] = useState("");
  const [check, setCheck] = useState<Check | null>(null);
  const [planning, setPlanning] = useState(false);
  const [when, setWhen] = useState("");
  const [metrics, setMetrics] = useState<Record<string, string>>({});
  const guardWord = (code: string) => (tg.has(`${code}.what`) ? tg(`${code}.what`) : code);
  const posts = [c.body, ...(c.thread ?? [])];
  const left = remaining(c.expires_at, now);
  const copy = async (text: string, what: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast({ ok: true, message: t("copied", { what }) });
      bff(`articles/${c.id}/copied`, "POST", {}, { toast: false }).catch(() => undefined);
    } catch {
      toast({ ok: false, message: t("copyFailed") });
    }
  };
  const act = async (path: string, body: unknown, message: string) => {
    setBusy(true);
    try {
      await bff(path, "POST", body, { toast: message });
      router.refresh();
    } catch { /* the toaster already said why */ } finally {
      setBusy(false);
    }
  };
  // "I posted it": an edited text is checked first (the claims guard on what actually went out); a failure stops the
  // record, warnings need a second click
  const posted = async () => {
    const text = edited.trim();
    if (text && !check) {
      setBusy(true);
      try {
        const r = await bff<Check>(`articles/${c.id}/check-text`, "POST", { text }, { toast: false });
        if (!r.matches_version && (r.blocked || r.findings.length > 0)) { setCheck(r); return; }
      } catch { return; } finally { setBusy(false); }
    }
    if (check?.blocked) return;
    const expired = c.status === "expired";
    if (expired && !window.confirm(t("expiredConfirm"))) return;
    await act(`articles/${c.id}/publish`, { x_url: url.trim() || null, final_text: text || null, allow_expired: expired },
      t("postedDone"));
    setPosting(false);
  };
  // X takes the text in its compose link; the squares have no such link, so the text goes to the clipboard first
  const square = { okx_square: { url: "https://www.okx.com/zh-hans/square", label: t("openOkx") },
    binance_square: { url: "https://www.binance.com/zh-CN/square", label: t("openBinance") } }[c.platform];
  const intent = square ? square.url : `https://x.com/intent/post?text=${encodeURIComponent(c.body)}`;
  const subject = c.column_key ? (tcol.has(c.column_key) ? tcol(c.column_key) : c.column_key) : c.coin;
  return (
    <div className={`rounded-lg border border-border bg-panel p-4 ${className}`}>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <Badge tone="accent">{c.language === "zh" ? "中文" : "English"}</Badge>
        {c.style_name && <Badge>{c.style_name}</Badge>}
        {c.source === "trial" && <Badge tone="warn">{t("trial")}</Badge>}
        <span className="text-muted">{subject} · {roleName}{c.model ? ` · ${c.model}` : ""}</span>
        {left && c.status !== "published" && <Badge tone={left.tone}>{left.text === "0" ? t("expiredNow") : t("left", { t: left.text })}</Badge>}
        {c.status === "expired" && <Badge tone="down">{t("expired")}</Badge>}
        {statusName && <Badge tone="warn">{statusName}</Badge>}
        <Link className="ml-auto text-accent" href={`/desk/${c.id}`}>
          {c.n_versions > 1 ? t("edits", { n: c.n_versions - 1 }) : t("openEditor")}</Link>
      </div>
      {/* the post, roughly as X shows it */}
      <div className="rounded-lg border border-border/70 bg-bg p-3">
        <div className="mb-2 flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-accent/20 text-sm font-bold text-accent">
            {c.display_name.slice(0, 1)}</div>
          <div className="leading-tight"><div className="text-sm font-semibold">{c.display_name}</div>
            <div className="text-xs text-muted">@{c.handle}</div></div>
        </div>
        {posts.map((p, i) => (
          <div key={i} className={i ? "mt-3 border-t border-border/60 pt-3" : ""}>
            <p className="whitespace-pre-wrap text-[15px] leading-relaxed">{p}</p>
            {i === 0 && c.card_asset_id && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={`/api/bff/assets/${c.card_asset_id}/file`} alt="card" className="mt-2 w-full rounded-lg border border-border" />
            )}
            {posts.length > 1 && <button className="mt-1 text-xs text-accent" onClick={() => copy(p, t("postN", { n: i + 1 }))}>{t("copyThis")}</button>}
          </div>
        ))}
        {c.reply_text && (
          <div className="mt-3 border-l-2 border-accent/40 pl-3">
            <div className="text-xs text-muted">{t("replyLabel")}</div>
            <p className="whitespace-pre-wrap break-all text-sm">{c.reply_text}</p>
          </div>
        )}
        {c.x_length !== null && (
          <div className="mt-2 text-xs text-muted">{t("length", { n: c.x_length, max: c.length_limit ?? 280 })}</div>
        )}
      </div>
      {c.review && <div className="mt-3"><ReviewCard review={c.review} compact /></div>}
      {c.publication && (
        <div className="mt-3 text-xs text-muted">
          {t("publishedVia", { channel: t(`channel.${c.publication.channel}`) })}
          {c.publication.x_url && <> · <a className="text-accent" href={c.publication.x_url} target="_blank" rel="noreferrer">{t("viewPost")}</a></>}
          {c.publication.matches_version === false && (
            <div className={c.publication.warnings?.length ? "text-warn" : ""}>
              {t("editedPosted")} · {c.publication.warnings?.length
                ? t("editedWarn", { codes: c.publication.warnings.map((w) => guardWord(w)).join("、") })
                : t("editedPass")}
            </div>
          )}
        </div>
      )}
      {c.metrics_due && (
        <div className="mt-3 rounded border border-warn/40 bg-warn/10 p-3 text-xs">
          <div className="mb-2 text-warn">{t("metricsDue")}</div>
          <div className="flex flex-wrap items-center gap-2">
            {["impressions", "likes", "reposts", "replies"].map((k) => (
              <input key={k} className={`${input} w-24`} inputMode="numeric" placeholder={t(`metric.${k}`)} value={metrics[k] ?? ""}
                onChange={(e) => setMetrics({ ...metrics, [k]: e.target.value.replace(/\D/g, "") })} />
            ))}
            <button className={button} disabled={busy || Object.values(metrics).every((v) => !v)} onClick={() => act(
              `publications/${c.metrics_due}/performance`,
              { captured_at: new Date().toISOString(), ...Object.fromEntries(Object.entries(metrics).filter(([, v]) => v).map(([k, v]) => [k, Number(v)])) },
              t("metricsSaved"))}>{t("save")}</button>
          </div>
        </div>
      )}
      {c.plan && (
        <div className="mt-3 flex items-center gap-2 text-xs text-muted">
          {t("plannedAt", { at: new Date(c.plan.planned_at).toLocaleString() })}
          <button className="text-accent" disabled={busy} onClick={() => act(`plans/${c.plan!.id}/cancel`, {}, t("planCancelled"))}>{t("cancelPlan")}</button>
        </div>
      )}
      {planning && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded border border-accent/40 p-3 text-sm">
          <input type="datetime-local" className={`${input} w-56`} value={when} onChange={(e) => setWhen(e.target.value)} />
          <button className={primary} disabled={busy || !when} onClick={async () => {
            await act(`articles/${c.id}/plan`, { planned_at: new Date(when).toISOString() }, t("planned"));
            setPlanning(false);
          }}>{t("planConfirm")}</button>
          <button className={button} onClick={() => setPlanning(false)}>{t("cancel")}</button>
          <span className="w-full text-xs text-muted">{t("planHint")}</span>
        </div>
      )}
      {posting && (
        <div className="mt-3 space-y-2 rounded border border-accent/40 p-3 text-sm">
          <input className={`${input} w-full`} placeholder={t("postedPrompt")} value={url} onChange={(e) => setUrl(e.target.value)} />
          <details>
            <summary className="cursor-pointer text-xs text-muted">{t("editedToggle")}</summary>
            <textarea className={`${input} mt-2 h-28 w-full`} placeholder={t("editedPlaceholder")} value={edited}
              onChange={(e) => { setEdited(e.target.value); setCheck(null); }} />
          </details>
          {check && (
            <div className={`text-xs ${check.blocked ? "text-down" : "text-warn"}`}>
              <b>{check.blocked ? t("checkBlocked") : t("checkWarn")}</b>
              <ul className="mt-1 list-disc pl-4">
                {check.findings.map((f, i) => <li key={i}>{guardWord(f.code)}{f.text ? `：${f.text}` : ""}</li>)}
              </ul>
            </div>
          )}
          <div className="flex gap-2">
            <button className={primary} disabled={busy || !!check?.blocked} onClick={posted}>
              {check && !check.blocked ? t("postedAnyway") : t("postedConfirm")}</button>
            <button className={button} onClick={() => { setPosting(false); setCheck(null); }}>{t("cancel")}</button>
          </div>
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {c.status !== "published" && <>
          <button className={button} onClick={() => copy(posts.join("\n\n"), t("body"))}>{t("copyBody")}</button>
          {c.reply_text && <button className={button} onClick={() => copy(c.reply_text!, t("reply"))}>{t("copyReply")}</button>}
          {c.card_asset_id && <a className={button} href={`/api/bff/assets/${c.card_asset_id}/file`} download>{t("downloadCard")}</a>}
        </>}
        {(c.status === "approved" || c.status === "expired") && <>
          <a className={button} href={intent} target="_blank" rel="noreferrer" title={square ? t("squareHint") : t("intentHint")}
            onClick={() => { if (square) copy(posts.join("\n\n"), t("body")); }}>{square ? square.label : t("openX")}</a>
          {!posting && <button className={primary} disabled={busy} onClick={() => setPosting(true)}>{t("posted")}</button>}
          {c.status === "approved" && !c.plan && !planning && (
            <button className={button} disabled={busy} onClick={() => setPlanning(true)}>{t("planRpa")}</button>
          )}
        </>}
        {c.review && c.review.verdict !== "approve" && ["draft_ready", "changes_requested", "in_review", "approved"].includes(c.status) && (
          <button className={button} disabled={busy} title={t("reviseHint")}
            onClick={() => act(`articles/${c.id}/revise`, {}, t("revising"))}>{t("revise")}</button>
        )}
        {["draft_ready", "changes_requested", "in_review", "stale"].includes(c.status) && c.source !== "trial" && (
          <button className={primary} disabled={busy} title={c.status === "stale" ? t("approveStaleHint") : undefined}
            onClick={() => act(`articles/${c.id}/review`, { action: "quick_approve" }, t("approved"))}>
            {c.status === "stale" ? t("approveStale") : t("approve")}</button>
        )}
        {c.source === "trial" && c.status !== "published" && (
          <button className={button} disabled={busy} onClick={() => act(`articles/${c.id}/promote`, {}, t("promoted"))}>{t("promote")}</button>
        )}
        {["expired", "stale", "draft_ready", "changes_requested"].includes(c.status) && (
          <button className={button} disabled={busy} onClick={() => act(`articles/${c.id}/rewrite`, {}, t("rewriting"))}>{t("rewrite")}</button>
        )}
        {!["published", "retracted", "killed"].includes(c.status) && (
          <button className={`${button} text-down`} disabled={busy} onClick={() => {
            const note = window.prompt(t("killPrompt"), "");
            if (note !== null) act(`articles/${c.id}/review`, { action: "kill", note }, t("killed"));
          }}>{t("kill")}</button>
        )}
      </div>
    </div>
  );
}
