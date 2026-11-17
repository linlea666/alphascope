"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { useRouter } from "@/i18n/routing";
import { bff } from "@/lib/bff";
import { toast } from "@/lib/toast";
import type { LeverageMode, SimParams, SimSizing } from "@/lib/api";

const DEFAULTS: SimParams = {
  sizing: "equity_ratio",
  size_factor: 1,
  cap_per_coin: null,
  max_leverage: 10,
  coins_include: [],
  coins_exclude: [],
  follow_adjust: true,
  copy_existing: false,
  delay_seconds: 0,
  fee_bps: 4.5,
  slippage_bps: 3,
  min_trade_usd: 10,
  rebalance_pct: 5,
  stop_drawdown_pct: null,
  leverage_mode: "account",
};

const input = "mt-1 w-full rounded border border-border bg-panel-2 px-2 py-1";

/** Create a simulation, or (with simId) edit the parameters of a running one. */
export function SimForm({ simId, initial, leader = "" }: { simId?: number; initial?: SimParams; leader?: string }) {
  const t = useTranslations("sims.form");
  const tt = useTranslations("toast");
  const router = useRouter();
  // OKX's taker fee is 5 bp (Hyperliquid 4.5 bp); a prefilled OKX leader starts from OKX costs
  const [p, setP] = useState<SimParams>(
    initial ? { ...DEFAULTS, ...initial, leverage_mode: initial.leverage_mode ?? "account" } : leader.startsWith("okx:") ? { ...DEFAULTS, fee_bps: 5 } : DEFAULTS,
  );
  const [name, setName] = useState("");
  const [addr, setAddr] = useState(leader);
  const [capital, setCapital] = useState(10000);
  const [include, setInclude] = useState((initial?.coins_include ?? []).join(", "));
  const [exclude, setExclude] = useState((initial?.coins_exclude ?? []).join(", "));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const split = (v: string) => v.split(/[,，\s]+/).map((x) => x.trim().toUpperCase()).filter(Boolean);
  const num = (k: keyof SimParams, nullable = false) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setP({ ...p, [k]: e.target.value === "" && nullable ? null : Number(e.target.value) });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    const params = { ...p, coins_include: split(include), coins_exclude: split(exclude) };
    try {
      if (simId) {
        await bff(`sims/${simId}`, "PATCH", { params });
        setSaved(true);
        toast(tt("simSaved"));
        router.refresh();
      } else {
        const r = await bff<{ id: number }>("sims", "POST", { ...params, name: name.trim() || addr.slice(0, 10), leader: addr.trim(), capital });
        toast(tt("simCreated"));
        router.push(`/sims/${r.id}`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const field = (label: string, el: React.ReactNode, hint?: string) => (
    <label className="block text-xs">
      <span className="text-muted">{label}</span>
      {el}
      {hint && <span className="mt-0.5 block text-[11px] leading-snug text-muted/80">{hint}</span>}
    </label>
  );

  return (
    <form onSubmit={submit} className="space-y-4 text-sm">
      {!simId && (
        <div className="grid gap-3 sm:grid-cols-3">
          {field(t("leader"), <input
              required
              value={addr}
              onChange={(e) => {
                const v = e.target.value.trim();
                // switch the default taker fee with the venue, unless the owner already changed it
                if (p.fee_bps === 4.5 && v.startsWith("okx:")) setP({ ...p, fee_bps: 5 });
                else if (p.fee_bps === 5 && v.startsWith("0x")) setP({ ...p, fee_bps: 4.5 });
                setAddr(e.target.value);
              }}
              placeholder="0x… / okx:…"
              className={`${input} font-mono text-xs`}
            />, t("leaderHint"))}
          {field(t("name"), <input value={name} maxLength={64} onChange={(e) => setName(e.target.value)} className={input} />)}
          {field(t("capital"), <input type="number" min={1} step="any" required value={capital} onChange={(e) => setCapital(Number(e.target.value))} className={input} />)}
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-3">
        {field(
          t("sizing"),
          <select value={p.sizing} onChange={(e) => setP({ ...p, sizing: e.target.value as SimSizing })} className={input}>
            {(["equity_ratio", "fixed_multiple", "fixed_cap"] as const).map((s) => (
              <option key={s} value={s}>
                {t(`sizings.${s}`)}
              </option>
            ))}
          </select>,
          t(`sizingHints.${p.sizing}`),
        )}
        {p.sizing === "fixed_cap"
          ? field(t("capPerCoin"), <input type="number" min={1} step="any" required value={p.cap_per_coin ?? ""} onChange={num("cap_per_coin", true)} className={input} />)
          : p.sizing === "equity_ratio"
            ? field(
                t("ratio"),
                <span className="mt-1 flex items-center gap-1.5">
                  <input type="number" min={1} max={10000} step="any" required value={Math.round(p.size_factor * 10000) / 100} onChange={(e) => setP({ ...p, size_factor: Number(e.target.value) / 100 })} className={`${input} mt-0 w-24`} />
                  <span className="text-muted">%</span>
                  {[50, 100, 200].map((r) => (
                    <button key={r} type="button" onClick={() => setP({ ...p, size_factor: r / 100 })} className={`rounded-full border px-2 py-0.5 text-[11px] ${Math.abs(p.size_factor * 100 - r) < 0.01 ? "border-accent text-accent" : "border-border text-muted hover:text-text"}`}>
                      {r}%
                    </button>
                  ))}
                </span>,
                t("ratioHint"),
              )
            : field(t("factor"), <input type="number" min={0.0001} max={100} step="any" required value={p.size_factor} onChange={num("size_factor")} className={input} />, t("factorHint"))}
        {field(t("maxLeverage"), <input type="number" min={0.1} max={50} step="any" required value={p.max_leverage} onChange={num("max_leverage")} className={input} />, t("maxLeverageHint"))}
      </div>
      {p.sizing === "equity_ratio" && <FormulaCard capital={simId ? 10000 : capital} ratio={p.size_factor} />}
      <div className="grid gap-3 sm:grid-cols-2">
        {field(
          t("leverageMode"),
          <select value={p.leverage_mode} onChange={(e) => setP({ ...p, leverage_mode: e.target.value as LeverageMode })} className={input}>
            {(["account", "leader"] as const).map((m) => (
              <option key={m} value={m}>
                {t(`leverageModes.${m}`)}
              </option>
            ))}
          </select>,
          t(`leverageModeHints.${p.leverage_mode}`),
        )}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {field(t("include"), <input value={include} onChange={(e) => setInclude(e.target.value)} placeholder="BTC, ETH" className={input} />, t("includeHint"))}
        {field(t("exclude"), <input value={exclude} onChange={(e) => setExclude(e.target.value)} placeholder="kPEPE" className={input} />)}
      </div>
      <div className="grid gap-3 sm:grid-cols-4">
        {field(t("delay"), <input type="number" min={0} max={3600} value={p.delay_seconds} onChange={num("delay_seconds")} className={input} />, t("delayHint"))}
        {field(t("fee"), <input type="number" min={0} max={100} step="any" value={p.fee_bps} onChange={num("fee_bps")} className={input} />, t("feeHint"))}
        {field(t("slippage"), <input type="number" min={0} max={500} step="any" value={p.slippage_bps} onChange={num("slippage_bps")} className={input} />, t("slippageHint"))}
        {field(t("drawdown"), <input type="number" min={1} max={99} step="any" value={p.stop_drawdown_pct ?? ""} onChange={num("stop_drawdown_pct", true)} placeholder={t("off")} className={input} />, t("drawdownHint"))}
        {field(t("minTrade"), <input type="number" min={0} step="any" value={p.min_trade_usd} onChange={num("min_trade_usd")} className={input} />)}
        {field(t("rebalance"), <input type="number" min={0} max={100} step="any" value={p.rebalance_pct} onChange={num("rebalance_pct")} className={input} />, t("rebalanceHint"))}
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={p.follow_adjust} onChange={(e) => setP({ ...p, follow_adjust: e.target.checked })} />
          {t("followAdjust")}
        </label>
        {!simId && (
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={p.copy_existing} onChange={(e) => setP({ ...p, copy_existing: e.target.checked })} />
            {t("copyExisting")}
          </label>
        )}
      </div>
      {error && <p className="text-xs text-down">{error}</p>}
      {saved && <p className="text-xs text-up">{t("saved")}</p>}
      <button type="submit" disabled={busy} className="rounded bg-accent px-4 py-1.5 text-white disabled:opacity-50">
        {busy ? t("saving") : simId ? t("save") : t("create")}
      </button>
    </form>
  );
}

/** The proportional-copy rule with a worked example on the owner's capital. */
function FormulaCard({ capital, ratio }: { capital: number; ratio: number }) {
  const t = useTranslations("sims.form");
  const f = (v: number) => new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(v);
  return (
    <div className="rounded-lg border border-accent/30 bg-accent/5 p-3 text-xs leading-relaxed">
      <div className="font-medium text-text">{t("formulaTitle")}</div>
      <div className="mt-1 font-mono text-[12px] text-accent">{t("formula")}</div>
      <div className="mt-1.5 text-muted">
        {t("formulaExample", { capital: f(capital), ratio: f(ratio * 100), result: f(capital * 0.1 * ratio) })}
      </div>
    </div>
  );
}
