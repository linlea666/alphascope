"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { bff } from "@/lib/bff";
import { input, primary } from "./ui";

type Acc = { id: number; handle: string; platform: string; venues: string[]; status: string; language: string };
type ModelOpt = { id: number; name: string; model: string; last_test_ok: boolean | null };

export function TrialPanel({ eventId, venue, accounts, models }: { eventId: number; venue: string; accounts: Acc[]; models: ModelOpt[] }) {
  const t = useTranslations("trial");
  const router = useRouter();
  const v = venue === "multi" ? "hyperliquid" : venue;
  const fits = accounts.filter((a) => a.status !== "retired" && (!a.venues.length || a.venues.includes(v)));
  const [acc, setAcc] = useState<number[]>(fits.slice(0, 1).map((a) => a.id));
  const [mods, setMods] = useState<number[]>([]);
  const [angle, setAngle] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const toggle = (list: number[], id: number, on: boolean) => (on ? [...list, id] : list.filter((x) => x !== id));
  return (
    <div className="space-y-3 text-sm">
      <p className="text-xs text-muted">{t("hint")}</p>
      <div>
        <div className="text-xs text-muted">{t("accounts")}</div>
        <div className="flex flex-wrap gap-3">
          {fits.map((a) => (
            <label key={a.id} className="flex items-center gap-1">
              <input type="checkbox" checked={acc.includes(a.id)} onChange={(e) => setAcc(toggle(acc, a.id, e.target.checked))} />
              @{a.handle} <span className="text-xs text-muted">{a.platform === "okx_square" ? "OKX 广场" : "X"} · {a.language} · {a.status}</span>
            </label>
          ))}
        </div>
      </div>
      <div>
        <div className="text-xs text-muted">{t("models")}</div>
        <div className="flex flex-wrap gap-3">
          {models.map((m) => (
            <label key={m.id} className="flex items-center gap-1">
              <input type="checkbox" checked={mods.includes(m.id)} onChange={(e) => setMods(toggle(mods, m.id, e.target.checked))} />
              {m.name} <span className="text-xs text-muted">{m.model}{m.last_test_ok === false ? " ⚠" : ""}</span>
            </label>
          ))}
        </div>
      </div>
      <input className={input} placeholder={t("angle")} value={angle} onChange={(e) => setAngle(e.target.value)} />
      <button className={primary} disabled={!acc.length} onClick={async () => {
        setMsg(null);
        try {
          await bff(`events/${eventId}/trial`, "POST", { account_ids: acc, model_ids: mods, angle });
          setMsg(t("done"));
          router.refresh();
        } catch (e) {
          setMsg((e as Error).message);
        }
      }}>{t("run")}</button>
      {msg && <p className="text-xs text-muted">{msg}</p>}
    </div>
  );
}
