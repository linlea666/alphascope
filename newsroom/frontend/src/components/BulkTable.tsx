"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { bff } from "@/lib/bff";
import { button } from "./ui";

/** A table whose rows can be selected for bulk trash / restore / submit / kill. */
export function BulkTable({ ids, head, rows, trashView }: { ids: number[]; head: ReactNode; rows: ReactNode[]; trashView: boolean }) {
  const t = useTranslations("manage");
  const router = useRouter();
  const [sel, setSel] = useState<number[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  async function act(action: string) {
    let reason = "";
    if (action === "trash" || action === "kill") {
      const r = window.prompt(t("trashReason"));
      if (r === null) return;
      reason = r;
    }
    const r = await bff<{ done: number[]; failed: { id: number; error: string }[] }>("articles/bulk", "POST", { ids: sel, action, reason });
    setMsg(`${r.done.length} ok${r.failed.length ? ` · ${r.failed.map((f) => `#${f.id}: ${f.error}`).join("; ")}` : ""}`);
    setSel([]);
    router.refresh();
  }
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-xs text-muted">{t("bulk")} · {t("selected")} {sel.length}</span>
        {trashView ? <button className={button} disabled={!sel.length} onClick={() => act("restore")}>{t("restore")}</button> : (
          <>
            <button className={button} disabled={!sel.length} onClick={() => act("submit")}>{t("submit")}</button>
            <button className={button} disabled={!sel.length} onClick={() => act("kill")}>{t("kill")}</button>
            <button className={`${button} text-down`} disabled={!sel.length} onClick={() => act("trash")}>{t("trash")}</button>
          </>
        )}
        {msg && <span className="text-xs text-muted">{msg}</span>}
      </div>
      <table className="w-full text-sm">
        <thead><tr><th className="w-6 px-2"><input type="checkbox" checked={sel.length === ids.length && ids.length > 0}
          onChange={(e) => setSel(e.target.checked ? ids : [])} /></th>{head}</tr></thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={ids[i]} className="border-b border-border/60 align-top">
              <td className="px-2 py-2"><input type="checkbox" checked={sel.includes(ids[i])}
                onChange={(e) => setSel(e.target.checked ? [...sel, ids[i]] : sel.filter((x) => x !== ids[i]))} /></td>
              {row}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
