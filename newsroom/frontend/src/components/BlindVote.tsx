"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { bff } from "@/lib/bff";
import { input, primary } from "./ui";

const DIMS = ["information", "lede", "evidence", "publishable"] as const;
type Slot = "1" | "2";

/** Scores both drafts on four dimensions, picks the better one and flags misleading ones; the arms stay hidden. */
export function BlindVote({ experiment, pairId }: { experiment: string; pairId: number }) {
  const t = useTranslations("experiments");
  const router = useRouter();
  const [scores, setScores] = useState<Record<Slot, Record<string, number>>>({ "1": {}, "2": {} });
  const [winner, setWinner] = useState<string>("");
  const [misleading, setMisleading] = useState<Slot[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const complete = winner !== "" && (["1", "2"] as Slot[]).every((s) => DIMS.every((d) => scores[s][d]));
  return (
    <div className="space-y-4">
      <table className="text-sm">
        <thead><tr><th className="pr-4 text-left text-xs text-muted" />
          {(["1", "2"] as Slot[]).map((s) => <th key={s} className="px-2 text-left text-xs text-muted">{t("draft", { n: s })}</th>)}</tr></thead>
        <tbody>
          {DIMS.map((d) => (
            <tr key={d}>
              <td className="py-1 pr-4"><div>{t(`dim.${d}`)}</div><div className="text-xs text-muted">{t(`dimHint.${d}`)}</div></td>
              {(["1", "2"] as Slot[]).map((s) => (
                <td key={s} className="px-2">
                  <div className="flex gap-1">
                    {[1, 2, 3, 4, 5].map((v) => (
                      <button key={v} type="button" onClick={() => setScores({ ...scores, [s]: { ...scores[s], [d]: v } })}
                        className={`h-7 w-7 rounded border text-xs ${scores[s][d] === v ? "border-accent bg-accent text-white" : "border-border bg-panel-2"}`}>{v}</button>
                    ))}
                  </div>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <span className="text-muted">{t("better")}</span>
        {[["1", t("draft", { n: "1" })], ["2", t("draft", { n: "2" })], ["tie", t("tie")]].map(([v, label]) => (
          <label key={v} className="flex items-center gap-1"><input type="radio" name="winner" checked={winner === v} onChange={() => setWinner(v)} />{label}</label>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <span className="text-muted">{t("misleading")}</span>
        {(["1", "2"] as Slot[]).map((s) => (
          <label key={s} className="flex items-center gap-1">
            <input type="checkbox" checked={misleading.includes(s)}
              onChange={(e) => setMisleading(e.target.checked ? [...misleading, s] : misleading.filter((x) => x !== s))} />
            {t("draft", { n: s })}
          </label>
        ))}
      </div>
      <textarea className={`${input} h-16`} placeholder={t("note")} value={note} onChange={(e) => setNote(e.target.value)} />
      <div>
        <button className={primary} disabled={!complete || busy} onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await bff(`experiments/${experiment}/pairs/${pairId}/vote`, "POST", { winner, misleading, scores, note });
            setScores({ "1": {}, "2": {} });
            setWinner("");
            setMisleading([]);
            setNote("");
            router.refresh();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}>{busy ? "…" : t("submit")}</button>
        {!complete && <span className="ml-3 text-xs text-muted">{t("incomplete")}</span>}
        {error && <span className="ml-3 text-xs text-down">{error}</span>}
      </div>
    </div>
  );
}
