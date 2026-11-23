"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { bff } from "@/lib/bff";
import { button, input } from "./ui";

/** Claim / release, due time, reviewer and reporter role of one assignment. */
export function BoardControls({ id, claimed, roles }: { id: number; claimed: boolean; roles: { key: string; name: string }[] }) {
  const t = useTranslations("board");
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  async function patch(body: Record<string, unknown>) {
    setError(null);
    try {
      await bff(`assignments/${id}`, "PATCH", body);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-1">
      <button className={button} onClick={() => patch({ claim: !claimed })}>{claimed ? t("unclaim") : t("claim")}</button>
      <input type="datetime-local" className={`${input} w-44`} title={t("due")}
        onChange={(e) => e.target.value && patch({ due_at: new Date(e.target.value).toISOString() })} />
      <select className={`${input} w-40`} defaultValue="" onChange={(e) => e.target.value && patch({ agent_key: e.target.value })}>
        <option value="">{t("reporter")}</option>
        {roles.map((r) => <option key={r.key} value={r.key}>{r.name}</option>)}
      </select>
      <input className={`${input} w-24`} placeholder={t("reviewer")}
        onKeyDown={(e) => e.key === "Enter" && patch({ reviewer_id: Number((e.target as HTMLInputElement).value) })} />
      {error && <span className="text-xs text-down">{error}</span>}
    </div>
  );
}
