"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { bff } from "@/lib/bff";
import { Badge, button, input, primary } from "./ui";

type Hit = { trader_key: string; name: string; active: boolean; added: boolean; intro?: string | null; asset?: string | null;
  avatar_url?: string | null;
  stats_30d: { roi?: number | string | null; pnl?: number | string | null; win_rate?: number | null; rank?: number | null } | null };

const pct = (v: number | string | null | undefined, fraction: boolean) =>
  v === null || v === undefined ? "—" : `${(Number(v) * (fraction ? 100 : 1)).toFixed(1)}%`;
const usd = (v: number | string | null | undefined) =>
  v === null || v === undefined ? "—" : `$${Math.round(Number(v)).toLocaleString()}`;

/** Find a Binance lead trader (through the relay) or an OKX trader (through AlphaScope) and put them on the list. */
export function StarSearch() {
  const t = useTranslations("stars");
  const router = useRouter();
  const [venue, setVenue] = useState<"binance" | "okx">("binance");
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [busy, setBusy] = useState(false);
  const search = async () => {
    setBusy(true);
    try {
      setHits(await bff<Hit[]>(`stars/search?venue=${venue}&q=${encodeURIComponent(q.trim())}`));
    } catch { setHits(null); } finally { setBusy(false); }
  };
  const add = async (h: Hit) => {
    const name = window.prompt(t("namePrompt"), h.name.replace(/\s+/g, " ").trim());
    if (name === null || !name.trim()) return;
    try {
      await bff("stars", "POST", { trader_key: h.trader_key, name: name.trim().slice(0, 64),
        avatar_url: h.avatar_url?.startsWith("https://") ? h.avatar_url : null }, { toast: t("added", { name }) });
      setHits((x) => x?.map((y) => (y.trader_key === h.trader_key ? { ...y, added: true } : y)) ?? null);
      router.refresh();
    } catch { /* the toaster said why */ }
  };
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 text-sm">
        <select className={`${input} w-40`} value={venue} onChange={(e) => { setVenue(e.target.value as "binance" | "okx"); setHits(null); }}>
          <option value="binance">{t("venue.binance")}</option>
          <option value="okx">{t("venue.okx")}</option>
        </select>
        <input className={`${input} w-56`} value={q} placeholder={venue === "binance" ? t("searchBinance") : t("searchOkx")}
          onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") search(); }} />
        <button className={primary} disabled={busy || (venue === "okx" && !q.trim())} onClick={search}>{t("search")}</button>
      </div>
      <p className="text-xs text-muted">{venue === "binance" ? t("binanceHint") : t("okxHint")}</p>
      {hits && (hits.length === 0 ? <p className="text-sm text-muted">{t("noHits")}</p> : (
        <div className="divide-y divide-border/60 rounded border border-border">
          {hits.map((h) => (
            <div key={h.trader_key} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
              <span className="font-medium">{h.name}</span>
              {!h.active && <Badge>{t("inactive")}</Badge>}
              <span className="text-xs text-muted">
                {venue === "binance"
                  ? t("binanceStats", { roi: pct(h.stats_30d?.roi, false), pnl: usd(h.stats_30d?.pnl), win: pct(h.stats_30d?.win_rate, false) })
                  : t("okxStats", { rank: h.stats_30d?.rank ?? "—", roi: pct(h.stats_30d?.roi, true), asset: usd(h.asset) })}
              </span>
              {h.intro && <span className="max-w-xs truncate text-xs text-muted" title={h.intro}>{h.intro}</span>}
              <span className="ml-auto">
                {h.added ? <Badge tone="up">{t("onList")}</Badge>
                  : <button className={button} onClick={() => add(h)}>{t("add")}</button>}
              </span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
