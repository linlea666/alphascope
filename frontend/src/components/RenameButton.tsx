"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { bff } from "@/lib/bff";
import { toast } from "@/lib/toast";

/** ✎ next to a trader's name: set the owner's custom name without watching them. The same alias is used on every
 *  page and in alert emails; an empty name falls back to the public nickname / short address. */
export function RenameButton({ address, alias, compact = false }: { address: string; alias?: string | null; compact?: boolean }) {
  const t = useTranslations("watch");
  const tt = useTranslations("toast");
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(alias ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLSpanElement>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    input.current?.select();
    const onDoc = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await bff(`watch/alias/${encodeURIComponent(address)}`, "PUT", { alias: value.trim() });
      setOpen(false);
      toast(value.trim() ? tt("renamed") : tt("renameCleared"));
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="relative inline-block" ref={box}>
      <button
        type="button"
        title={t("renameTrader")}
        aria-label={t("renameTrader")}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setValue(alias ?? "");
          setError(null);
          setOpen(!open);
        }}
        className={compact ? "text-xs leading-[18px] text-muted hover:text-accent" : "rounded border border-border px-2 py-0.5 text-xs text-muted hover:text-accent"}
      >
        ✎{!compact && <span className="ml-1">{alias ? t("renameTrader") : t("nameTrader")}</span>}
      </button>
      {open && (
        <div className="absolute left-0 top-6 z-30 w-72 rounded-lg border border-border bg-panel p-3 text-left text-xs shadow-lg">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
            className="space-y-2"
          >
            <label className="block">
              <span className="text-muted">{t("alias")}</span>
              <input
                ref={input}
                value={value}
                maxLength={64}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") setOpen(false);
                }}
                placeholder={t("aliasHint")}
                className="mt-1 w-full rounded border border-border bg-panel-2 px-2 py-1"
              />
            </label>
            <p className="text-muted">{t("renameHint")}</p>
            {error && <p className="text-down">{error}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setOpen(false)} className="rounded border border-border px-2 py-1">
                {t("cancel")}
              </button>
              <button type="submit" disabled={busy} className="rounded bg-accent px-3 py-1 text-white disabled:opacity-50">
                {busy ? t("saving") : t("save")}
              </button>
            </div>
          </form>
        </div>
      )}
    </span>
  );
}
