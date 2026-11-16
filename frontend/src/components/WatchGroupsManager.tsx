"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { bff } from "@/lib/bff";

export function WatchGroupsManager({ groups }: { groups: { id: number; name: string; color: string | null }[] }) {
  const t = useTranslations("watch");
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <div className="mt-3 text-xs">
      <button type="button" onClick={() => setOpen(!open)} className="text-muted underline-offset-2 hover:text-text hover:underline">
        {t("manage")}
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          {groups.map((g) => (
            <div key={g.id} className="flex items-center gap-2">
              <span className="w-40 truncate">{g.name}</span>
              <button
                type="button"
                className="rounded border border-border px-2 py-0.5"
                onClick={() => {
                  const n = window.prompt(t("rename"), g.name);
                  if (n && n.trim() && n !== g.name) run(() => bff(`watch/groups/${g.id}`, "PATCH", { name: n.trim() }));
                }}
              >
                {t("rename")}
              </button>
              <input
                type="color"
                defaultValue={g.color ?? "#6c9cff"}
                onChange={(e) => run(() => bff(`watch/groups/${g.id}`, "PATCH", { name: g.name, color: e.target.value }))}
                className="h-6 w-8 cursor-pointer bg-transparent"
              />
              <button
                type="button"
                className="rounded border border-border px-2 py-0.5 text-down"
                onClick={() => {
                  if (window.confirm(t("confirmDelete", { name: g.name }))) run(() => bff(`watch/groups/${g.id}`, "DELETE"));
                }}
              >
                {t("delete")}
              </button>
            </div>
          ))}
          <div className="flex gap-2">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("newGroup")} className="w-40 rounded border border-border bg-panel-2 px-2 py-1" />
            <button
              type="button"
              className="rounded bg-accent px-3 py-1 text-white"
              onClick={() => name.trim() && run(async () => {
                await bff("watch/groups", "POST", { name: name.trim() });
                setName("");
              })}
            >
              {t("create")}
            </button>
          </div>
          {error && <p className="text-down">{error}</p>}
        </div>
      )}
    </div>
  );
}
