"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { Fragment, useState } from "react";
import { bff, toast } from "@/lib/bff";
import { platformName } from "@/lib/glossary";
import { PostCard, type Card } from "./PostCard";
import { Badge, button } from "./ui";

function left(expires: string | null, now: number): { text: string; tone: "up" | "warn" | "down" } | null {
  if (!expires) return null;
  const min = Math.round((new Date(expires).getTime() - now) / 60000);
  if (min <= 0) return { text: "0", tone: "down" };
  return { text: min >= 60 ? `${Math.floor(min / 60)}h${min % 60 ? ` ${min % 60}m` : ""}` : `${min}m`, tone: min < 30 ? "warn" : "up" };
}

/** One line per post: what it is, where it goes, how long it stays good, what the reviewer said. Open a line to see
 * the post as it will look; tick lines to approve or kill them together. */
export function PostList({ cards, now, roleNames, columnNames, bulk }: { cards: Card[]; now: number;
  roleNames: Record<string, string>; columnNames: Record<string, string>; bulk: ("approve" | "kill")[] }) {
  const t = useTranslations("publish");
  const router = useRouter();
  const [open, setOpen] = useState<number | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const toggle = (id: number) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const run = async (action: "approve" | "kill") => {
    setBusy(true);
    try {
      const r = await bff<{ done: number[]; failed: { id: number }[] }>("articles/bulk", "POST",
        { ids: [...picked], action, reason: action === "kill" ? "bulk" : "" }, { toast: false });
      toast({ ok: r.failed.length === 0, message: t("bulkDone", { done: r.done.length,
        failed: r.failed.length ? t("bulkFailed", { n: r.failed.length }) : "" }) });
      setPicked(new Set());
      router.refresh();
    } catch { /* the toaster said why */ } finally { setBusy(false); }
  };
  return (
    <div>
      {bulk.length > 0 && (
        <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
          <label className="flex items-center gap-2 text-xs text-muted">
            <input type="checkbox" checked={picked.size === cards.length && cards.length > 0}
              onChange={(e) => setPicked(e.target.checked ? new Set(cards.map((c) => c.id)) : new Set())} />{t("selectAll")}
          </label>
          {picked.size > 0 && <>
            <span className="text-xs text-muted">{t("selected", { n: picked.size })}</span>
            {bulk.includes("approve") && <button className={button} disabled={busy} onClick={() => run("approve")}>{t("bulkApprove")}</button>}
            {bulk.includes("kill") && <button className={`${button} text-down`} disabled={busy} onClick={() => run("kill")}>{t("bulkKill")}</button>}
          </>}
        </div>
      )}
      <div className="divide-y divide-border/60 rounded-lg border border-border bg-panel">
        {cards.map((c) => {
          const l = left(c.expires_at, now);
          const title = (c.headline || c.body.split("\n").find((x) => x.trim()) || t("noTitle")).slice(0, 80);
          const subject = c.column_key ? (columnNames[c.column_key] ?? c.column_key) : c.coin;
          const score = c.review?.scores?.publishable;
          return (
            <Fragment key={c.id}>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm hover:bg-bg/50">
                {bulk.length > 0 && <input type="checkbox" checked={picked.has(c.id)} onChange={() => toggle(c.id)} />}
                <button className="min-w-0 flex-1 truncate text-left" onClick={() => setOpen(open === c.id ? null : c.id)} title={title}>
                  <span className="font-medium">{title}</span>
                </button>
                <span className="flex flex-wrap items-center gap-1.5 text-xs">
                  {c.why && <Badge tone="down">{t(`why.${c.why}`)}</Badge>}
                  <Badge>{t(`venue.${c.venue}`)}</Badge>
                  <span className="text-muted">{platformName(c.platform)} @{c.handle} · {c.language === "zh" ? "中文" : "EN"} · {subject}
                    {c.agent_key ? ` · ${roleNames[c.agent_key] ?? c.agent_key}` : ""}</span>
                  {score !== undefined && <Badge tone={score >= 4 ? "up" : score >= 3 ? "warn" : "down"}>{t("score", { n: score })}</Badge>}
                  {l && c.status !== "published" && <Badge tone={l.tone}>{l.text === "0" ? t("expiredNow") : t("left", { t: l.text })}</Badge>}
                  {c.claimed_by && <Badge tone="accent">{t("claimed", { name: c.claimed_by })}</Badge>}
                  <button className="text-accent" onClick={() => setOpen(open === c.id ? null : c.id)}>{open === c.id ? t("close") : t("open")}</button>
                </span>
              </div>
              {open === c.id && (
                <div className="bg-bg/40 p-3">
                  <PostCard c={c} now={now} roleName={c.agent_key ? roleNames[c.agent_key] ?? c.agent_key : ""} className="max-w-2xl" />
                </div>
              )}
            </Fragment>
          );
        })}
      </div>
    </div>
  );
}
