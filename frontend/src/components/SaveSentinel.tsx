"use client";

import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";
import { bff } from "@/lib/bff";
import { toast } from "@/lib/toast";

type Zone = { coin: string; side: "long" | "short"; px_min: number; px_max: number; venue: string };

/** Bell button that saves a price-zone query as a sentinel (emails new traders opening inside the zone). */
export function SaveSentinel({ zone, defaultName }: { zone: Zone; defaultName: string }) {
  const t = useTranslations("sentinel");
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(defaultName);
  const [cohort, setCohort] = useState("all");
  const [minNotional, setMinNotional] = useState(10000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const on = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", on);
    return () => document.removeEventListener("mousedown", on);
  }, [open]);
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await bff("alerts/sentinels", "POST", { ...zone, name, cohort, min_notional: minNotional });
      setOpen(false);
      toast(t("saved"));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const input = "mt-1 w-full rounded border border-border bg-panel-2 px-2 py-1";
  return (
    <span ref={box} className="relative inline-block">
      <button type="button" onClick={() => setOpen(!open)} className="rounded border border-border px-2 py-0.5 text-xs text-muted hover:border-accent hover:text-accent">
        🔔 {t("save")}
      </button>
      {open && (
        <div className="absolute right-0 top-7 z-30 w-72 space-y-2 rounded-lg border border-border bg-panel p-3 text-left text-xs shadow-lg">
          <p className="leading-relaxed text-muted">{t("explain")}</p>
          <label className="block">
            <span className="text-muted">{t("name")}</span>
            <input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} className={input} />
          </label>
          <label className="block">
            <span className="text-muted">{t("cohort")}</span>
            <select value={cohort} onChange={(e) => setCohort(e.target.value)} className={input}>
              {["all", "timing", "watchlist"].map((c) => (
                <option key={c} value={c}>
                  {t(`cohorts.${c}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-muted">{t("minNotional")}</span>
            <input type="number" min={0} value={minNotional} onChange={(e) => setMinNotional(Number(e.target.value))} className={input} />
          </label>
          {error && <p className="text-down">{error}</p>}
          <div className="flex justify-end">
            <button type="button" disabled={busy || !name.trim()} onClick={save} className="rounded bg-accent px-3 py-1 text-white disabled:opacity-50">
              {busy ? t("saving") : t("confirm")}
            </button>
          </div>
        </div>
      )}
    </span>
  );
}
