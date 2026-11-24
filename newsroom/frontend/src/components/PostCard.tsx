"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Link } from "@/i18n/routing";
import { bff, toast } from "@/lib/bff";
import type { Review } from "@/lib/types";
import { ReviewCard } from "./ReviewCard";
import { Badge, button, primary } from "./ui";

export type Card = {
  id: number; status: string; language: string; expires_at: string | null; account_id: number; handle: string;
  display_name: string; platform: string; source: string | null; event_id: number; event_class: string; coin: string;
  version_id: number; headline: string; body: string; thread: string[] | null; reply_text: string | null;
  style_key: string | null; style_name: string | null; model: string | null; agent_key: string | null; guard_status: string;
  x_length: number | null; length_limit: number | null; card_asset_id: number | null; review: Review | null;
  publication: { x_url: string | null; reply_url: string | null; channel: string; published_at: string } | null;
};

function remaining(expires: string | null, now: number): { text: string; tone: "up" | "warn" | "down" } | null {
  if (!expires) return null;
  const min = Math.round((new Date(expires).getTime() - now) / 60000);
  if (min <= 0) return { text: "0", tone: "down" };
  return { text: min >= 60 ? `${Math.floor(min / 60)}h${min % 60 ? ` ${min % 60}m` : ""}` : `${min}m`, tone: min < 30 ? "warn" : "up" };
}

/** One post the way it will look on X, with everything needed to get it out by hand or hand it to a robot. */
export function PostCard({ c, now, roleName, className }: { c: Card; now: number; roleName: string; className: string }) {
  const t = useTranslations("publish");
  const router = useRouter();
  const [busy, setBusy] = useState(false);
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
  const posted = async () => {
    const url = window.prompt(t("postedPrompt"), "");
    if (url === null) return;
    const expired = c.status === "expired";
    if (expired && !window.confirm(t("expiredConfirm"))) return;
    await act(`articles/${c.id}/publish`, { x_url: url.trim() || null, allow_expired: expired }, t("postedDone"));
  };
  const intent = `https://x.com/intent/post?text=${encodeURIComponent(c.body)}`;
  return (
    <div className={`rounded-lg border border-border bg-panel p-4 ${className}`}>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <Badge tone="accent">{c.language === "zh" ? "中文" : "English"}</Badge>
        {c.style_name && <Badge>{c.style_name}</Badge>}
        {c.source === "trial" && <Badge tone="warn">{t("trial")}</Badge>}
        <span className="text-muted">{c.coin} · {roleName}{c.model ? ` · ${c.model}` : ""}</span>
        {left && c.status !== "published" && <Badge tone={left.tone}>{left.text === "0" ? t("expiredNow") : t("left", { t: left.text })}</Badge>}
        {c.status === "expired" && <Badge tone="down">{t("expired")}</Badge>}
        <Link className="ml-auto text-accent" href={`/desk/${c.id}`}>{t("openEditor")}</Link>
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
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {c.status !== "published" && <>
          <button className={button} onClick={() => copy(posts.join("\n\n"), t("body"))}>{t("copyBody")}</button>
          {c.reply_text && <button className={button} onClick={() => copy(c.reply_text!, t("reply"))}>{t("copyReply")}</button>}
          {c.card_asset_id && <a className={button} href={`/api/bff/assets/${c.card_asset_id}/file`} download>{t("downloadCard")}</a>}
        </>}
        {(c.status === "approved" || c.status === "expired") && <>
          <a className={button} href={intent} target="_blank" rel="noreferrer" title={t("intentHint")}>{t("openX")}</a>
          <button className={primary} disabled={busy} onClick={posted}>{t("posted")}</button>
        </>}
        {["draft_ready", "changes_requested", "in_review"].includes(c.status) && c.source !== "trial" && (
          <button className={primary} disabled={busy} onClick={() => act(`articles/${c.id}/review`, { action: "quick_approve" }, t("approved"))}>{t("approve")}</button>
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
