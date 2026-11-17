"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { bff } from "@/lib/bff";
import { toast } from "@/lib/toast";

type Membership = { groups: { id: number; name: string; color: string | null; member: boolean }[]; alias: string; note: string; tags: string[] };

/** Star toggle with a small panel: choose groups, alias, note and tags. Saving replaces the membership. */
export function WatchButton({ address, watched, compact = false }: { address: string; watched: boolean; compact?: boolean }) {
  const t = useTranslations("watch");
  const tt = useTranslations("toast");
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Membership | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [alias, setAlias] = useState("");
  const [note, setNote] = useState("");
  const [tags, setTags] = useState("");
  const [newGroup, setNewGroup] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  const load = async () => {
    const m = await bff<Membership>(`watch/wallet/${address}`);
    setData(m);
    const current = new Set(m.groups.filter((g) => g.member).map((g) => g.id));
    // first time: pre-select the first (default) group so one click + save is enough
    setSelected(current.size ? current : new Set(m.groups.slice(0, 1).map((g) => g.id)));
    setAlias(m.alias);
    setNote(m.note);
    setTags(m.tags.join(", "));
  };

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const save = async (groupIds: number[]) => {
    setBusy(true);
    setError(null);
    try {
      await bff(`watch/wallet/${address}`, "PUT", {
        group_ids: groupIds,
        alias,
        note,
        tags: tags.split(/[,，]/).map((x) => x.trim()).filter(Boolean),
      });
      setOpen(false);
      toast(groupIds.length ? tt("watched") : tt("unwatched"));
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const addGroup = async () => {
    if (!newGroup.trim()) return;
    try {
      const { id } = await bff<{ id: number }>("watch/groups", "POST", { name: newGroup.trim() });
      setNewGroup("");
      await load();
      setSelected((s) => new Set([...s, id]));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <span className="relative inline-block" ref={box}>
      <button
        type="button"
        title={watched ? t("edit") : t("add")}
        aria-label={watched ? t("edit") : t("add")}
        onClick={async (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (!open) {
            setError(null);
            try {
              await load();
            } catch (err) {
              setError(err instanceof Error ? err.message : String(err));
            }
          }
          setOpen(!open);
        }}
        className={`${compact ? "text-sm leading-[18px]" : "rounded border border-border px-2 py-0.5 text-xs"} ${watched ? "text-warn" : "text-muted hover:text-warn"}`}
      >
        <span key={String(watched)} className={watched ? "as-pop" : undefined}>{watched ? "★" : "☆"}</span>
        {!compact && <span className="ml-1">{watched ? t("watched") : t("add")}</span>}
      </button>
      {open && (
        <div className="absolute left-0 top-6 z-30 w-72 rounded-lg border border-border bg-panel p-3 text-left text-xs shadow-lg">
          {!data ? (
            <p className="text-muted">{error ?? t("loading")}</p>
          ) : (
            <div className="space-y-3">
              <div>
                <div className="mb-1 text-muted">{t("groups")}</div>
                <div className="max-h-40 space-y-1 overflow-auto">
                  {data.groups.map((g) => (
                    <label key={g.id} className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={selected.has(g.id)}
                        onChange={(e) => {
                          const next = new Set(selected);
                          if (e.target.checked) next.add(g.id);
                          else next.delete(g.id);
                          setSelected(next);
                        }}
                      />
                      <span style={g.color ? { color: g.color } : undefined}>{g.name}</span>
                    </label>
                  ))}
                </div>
                <div className="mt-2 flex gap-1">
                  <input value={newGroup} onChange={(e) => setNewGroup(e.target.value)} placeholder={t("newGroup")} className="w-full rounded border border-border bg-panel-2 px-2 py-1" />
                  <button type="button" onClick={addGroup} className="rounded border border-border px-2">
                    +
                  </button>
                </div>
              </div>
              <label className="block">
                <span className="text-muted">{t("alias")}</span>
                <input value={alias} maxLength={64} onChange={(e) => setAlias(e.target.value)} placeholder={t("aliasHint")} className="mt-1 w-full rounded border border-border bg-panel-2 px-2 py-1" />
              </label>
              <label className="block">
                <span className="text-muted">{t("note")}</span>
                <textarea value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} rows={2} className="mt-1 w-full rounded border border-border bg-panel-2 px-2 py-1" />
              </label>
              <label className="block">
                <span className="text-muted">{t("tags")}</span>
                <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="btc, 大周期" className="mt-1 w-full rounded border border-border bg-panel-2 px-2 py-1" />
              </label>
              {error && <p className="text-down">{error}</p>}
              <div className="flex justify-between gap-2">
                {watched ? (
                  <button type="button" disabled={busy} onClick={() => save([])} className="rounded border border-border px-2 py-1 text-down">
                    {t("remove")}
                  </button>
                ) : (
                  <span />
                )}
                <button type="button" disabled={busy || selected.size === 0} onClick={() => save([...selected])} className="rounded bg-accent px-3 py-1 text-white disabled:opacity-50">
                  {busy ? t("saving") : t("save")}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </span>
  );
}
