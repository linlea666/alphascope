"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { bff } from "@/lib/bff";
import { button, input } from "./ui";

export function AssignForm({ eventId, accounts }: { eventId: number; accounts: { id: number; handle: string; language: string }[] }) {
  const t = useTranslations("events");
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  return (
    <form
      className="flex flex-wrap items-end gap-2 text-sm"
      action={async (f) => {
        setError(null);
        try {
          await bff(`events/${eventId}/assignments`, "POST", {
            account_id: Number(f.get("account_id")), angle: f.get("angle"), format: f.get("format"),
          });
          router.refresh();
        } catch (e) {
          setError((e as Error).message);
        }
      }}
    >
      <select name="account_id" className={`${input} w-44`}>
        {accounts.map((a) => <option key={a.id} value={a.id}>@{a.handle} ({a.language})</option>)}
      </select>
      <input name="angle" placeholder={t("angle")} className={`${input} w-64`} />
      <select name="format" className={`${input} w-32`}>
        <option value="single_post">single_post</option><option value="thread">thread</option><option value="card_post">card_post</option>
      </select>
      <button className={button}>{t("addAssignment")}</button>
      {error && <span className="text-xs text-down">{error}</span>}
    </form>
  );
}
