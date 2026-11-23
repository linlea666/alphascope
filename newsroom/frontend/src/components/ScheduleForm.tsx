"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { bff } from "@/lib/bff";
import { button, input } from "./ui";

/** Pick a time (in the browser's time zone; stored as UTC) for an approved article. */
export function ScheduleForm({ articleId }: { articleId: number }) {
  const t = useTranslations("calendar");
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [force, setForce] = useState(false);
  return (
    <form className="flex flex-wrap items-center gap-2 text-sm" action={async (f) => {
      setError(null);
      try {
        await bff(`articles/${articleId}/plan`, "POST", { planned_at: new Date(String(f.get("at"))).toISOString(), force });
        router.refresh();
      } catch (e) {
        setError((e as Error).message);
      }
    }}>
      <input name="at" type="datetime-local" required className={`${input} w-48`} />
      <label className="flex items-center gap-1 text-xs text-muted"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />{t("force")}</label>
      <button className={button}>{t("schedule")}</button>
      {error && <span className="text-xs text-down">{error}</span>}
    </form>
  );
}
