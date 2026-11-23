"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { bff } from "@/lib/bff";
import { input, primary } from "./ui";

const TAGS = ["information", "lede", "evidence", "story", "tone", "wrong_angle", "misleading"] as const;
export type Rating = { article_version_id: number; score: number; tags: string[]; note: string; blind: boolean; username: string; mine: boolean };

/** One rating per person per version: 1-5, what was good or wrong, and why. Feeds the weekly quality report. */
export function RateVersion({ articleId, versionId, ratings }: { articleId: number; versionId: number; ratings: Rating[] }) {
  const t = useTranslations("rating");
  const router = useRouter();
  const mine = ratings.find((r) => r.mine && r.article_version_id === versionId);
  const [score, setScore] = useState(mine?.score ?? 0);
  const [tags, setTags] = useState<string[]>(mine?.tags ?? []);
  const [note, setNote] = useState(mine?.note ?? "");
  const [msg, setMsg] = useState<string | null>(null);
  const others = ratings.filter((r) => r.article_version_id === versionId && !r.mine);
  return (
    <div className="space-y-2 text-sm">
      <div className="flex gap-1">
        {[1, 2, 3, 4, 5].map((v) => (
          <button key={v} type="button" onClick={() => setScore(v)}
            className={`h-7 w-7 rounded border text-xs ${score === v ? "border-accent bg-accent text-white" : "border-border bg-panel-2"}`}>{v}</button>
        ))}
      </div>
      <div className="flex flex-wrap gap-2 text-xs">
        {TAGS.map((tag) => (
          <label key={tag} className="flex items-center gap-1">
            <input type="checkbox" checked={tags.includes(tag)} onChange={(e) => setTags(e.target.checked ? [...tags, tag] : tags.filter((x) => x !== tag))} />
            {t(`tag.${tag}`)}
          </label>
        ))}
      </div>
      <input className={input} placeholder={t("note")} value={note} onChange={(e) => setNote(e.target.value)} />
      <button className={primary} disabled={!score} onClick={async () => {
        try {
          await bff(`articles/${articleId}/versions/${versionId}/rating`, "PUT", { score, tags, note });
          setMsg(t("saved"));
          router.refresh();
        } catch (e) {
          setMsg((e as Error).message);
        }
      }}>{mine ? t("update") : t("rate")}</button>
      {msg && <span className="ml-2 text-xs text-muted">{msg}</span>}
      {others.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs text-muted">
          {others.map((r, i) => <li key={i}>{r.username}: {r.score}/5 {r.blind ? `(${t("blind")})` : ""} {r.tags.map((x) => t(`tag.${x}`)).join(" · ")} {r.note}</li>)}
        </ul>
      )}
    </div>
  );
}
