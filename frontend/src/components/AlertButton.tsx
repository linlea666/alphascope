"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Link } from "@/i18n/routing";
import { bff } from "@/lib/bff";
import { toast } from "@/lib/toast";

const KINDS = ["open", "increase", "decrease", "close", "flip", "leverage", "liq_risk"] as const;
type Sub = { enabled: boolean; events: string[]; min_notional: number; min_change_pct: number; coins: string[]; recipients: string[]; quiet_start: number | null; quiet_end: number | null };

export function AlertButton({ address, subscribed, compact = false }: { address: string; subscribed: boolean; compact?: boolean }) {
  const t = useTranslations("alerts");
  const tt = useTranslations("toast");
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [sub, setSub] = useState<Sub | null>(null);
  const [smtpReady, setSmtpReady] = useState(true);
  const [coins, setCoins] = useState("");
  const [recipients, setRecipients] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const load = async () => {
    const r = await bff<{ subscribed: boolean; smtp_ready: boolean; subscription: Sub }>(`alerts/wallet/${address}`);
    setSub(r.subscription);
    setSmtpReady(r.smtp_ready);
    setCoins(r.subscription.coins.join(", "));
    setRecipients(r.subscription.recipients.join(", "));
  };
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setOpen(false);
      toast(tt("alertSaved"));
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const split = (v: string) => v.split(/[,，\s]+/).map((x) => x.trim()).filter(Boolean);
  const hourSelect = (value: number | null, onChange: (v: number | null) => void) => (
    <select value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} className="rounded border border-border bg-panel-2 px-1 py-0.5">
      <option value="">{t("none")}</option>
      {Array.from({ length: 24 }, (_, h) => (
        <option key={h} value={h}>
          {String(h).padStart(2, "0")}:00
        </option>
      ))}
    </select>
  );

  return (
    <span className="relative inline-block" ref={box}>
      <button
        type="button"
        title={subscribed ? t("subscribed") : t("subscribe")}
        onClick={async (e) => {
          e.preventDefault();
          if (!open) {
            try {
              await load();
            } catch (err) {
              setError(err instanceof Error ? err.message : String(err));
            }
          }
          setOpen(!open);
        }}
        className={`${compact ? "text-sm leading-[18px]" : "rounded border border-border px-2 py-0.5 text-xs"} ${subscribed ? "text-accent" : "text-muted hover:text-accent"}`}
      >
        {subscribed ? "🔔" : "🔕"}
        {!compact && <span className="ml-1">{subscribed ? t("subscribed") : t("subscribe")}</span>}
      </button>
      {open && (
        <div className="absolute left-0 top-6 z-30 w-80 space-y-3 rounded-lg border border-border bg-panel p-3 text-left text-xs shadow-lg">
          {!sub ? (
            <p className="text-muted">{error ?? "…"}</p>
          ) : (
            <>
              {!smtpReady && (
                <p className="text-warn">
                  {t("smtpMissing")}{" "}
                  <Link href="/settings" className="underline">
                    {t("goSettings")}
                  </Link>
                </p>
              )}
              <div>
                <div className="mb-1 text-muted">{t("events")}</div>
                <div className="grid grid-cols-2 gap-1">
                  {KINDS.map((k) => (
                    <label key={k} className="flex items-center gap-1.5">
                      <input
                        type="checkbox"
                        checked={sub.events.includes(k)}
                        onChange={(e) => setSub({ ...sub, events: e.target.checked ? [...sub.events, k] : sub.events.filter((x) => x !== k) })}
                      />
                      {t(`kinds.${k}`)}
                    </label>
                  ))}
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <label>
                  <span className="text-muted">{t("minNotional")}</span>
                  <input type="number" min={0} value={sub.min_notional} onChange={(e) => setSub({ ...sub, min_notional: Number(e.target.value) })} className="mt-1 w-full rounded border border-border bg-panel-2 px-2 py-1" />
                </label>
                <label>
                  <span className="text-muted">{t("minPct")}</span>
                  <input type="number" min={0} max={100} value={sub.min_change_pct} onChange={(e) => setSub({ ...sub, min_change_pct: Number(e.target.value) })} className="mt-1 w-full rounded border border-border bg-panel-2 px-2 py-1" />
                </label>
              </div>
              <label className="block">
                <span className="text-muted">{t("coins")}</span>
                <input value={coins} onChange={(e) => setCoins(e.target.value)} placeholder="BTC, ETH" className="mt-1 w-full rounded border border-border bg-panel-2 px-2 py-1" />
              </label>
              <label className="block">
                <span className="text-muted">{t("recipients")}</span>
                <input value={recipients} onChange={(e) => setRecipients(e.target.value)} className="mt-1 w-full rounded border border-border bg-panel-2 px-2 py-1" />
              </label>
              <div className="flex flex-wrap items-center gap-1">
                <span className="text-muted">{t("quiet")}:</span>
                {t("quietFrom")} {hourSelect(sub.quiet_start, (v) => setSub({ ...sub, quiet_start: v }))} {t("quietTo")} {hourSelect(sub.quiet_end, (v) => setSub({ ...sub, quiet_end: v }))}
              </div>
              <label className="flex items-center gap-1.5">
                <input type="checkbox" checked={sub.enabled} onChange={(e) => setSub({ ...sub, enabled: e.target.checked })} />
                {t("enabled")}
              </label>
              <p className="text-muted">{t("baseline")}</p>
              {error && <p className="text-down">{error}</p>}
              <div className="flex justify-between">
                {subscribed ? (
                  <button type="button" disabled={busy} onClick={() => act(() => bff(`alerts/wallet/${address}`, "DELETE"))} className="rounded border border-border px-2 py-1 text-down">
                    {t("remove")}
                  </button>
                ) : (
                  <span />
                )}
                <button
                  type="button"
                  disabled={busy || sub.events.length === 0}
                  onClick={() => act(() => bff(`alerts/wallet/${address}`, "PUT", { ...sub, coins: split(coins), recipients: split(recipients) }))}
                  className="rounded bg-accent px-3 py-1 text-white disabled:opacity-50"
                >
                  {busy ? t("saving") : t("save")}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </span>
  );
}
