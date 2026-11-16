"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { useRouter } from "@/i18n/routing";
import { bff } from "@/lib/bff";
import type { SimStatus } from "@/lib/api";

/** Pause / resume / stop / delete. Stopping closes positions at the live process's next prices. */
export function SimControls({ id, status, stopPending = false, afterDelete }: { id: number; status: SimStatus; stopPending?: boolean; afterDelete?: string }) {
  const t = useTranslations("sims.controls");
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (fn: () => Promise<unknown>, confirmText?: string) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(true);
    setError(null);
    try {
      await fn();
      if (afterDelete) router.push(afterDelete);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const btn = "rounded border border-border px-2 py-0.5 text-xs disabled:opacity-50";

  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {status === "running" && !stopPending && (
        <button type="button" disabled={busy} className={`${btn} text-muted hover:text-text`} onClick={() => run(() => bff(`sims/${id}`, "PATCH", { action: "pause" }))}>
          {t("pause")}
        </button>
      )}
      {status === "paused" && (
        <button type="button" disabled={busy} className={`${btn} text-accent`} onClick={() => run(() => bff(`sims/${id}`, "PATCH", { action: "resume" }))}>
          {t("resume")}
        </button>
      )}
      {status !== "stopped" &&
        (stopPending ? (
          <span className="text-xs text-warn">{t("stopping")}</span>
        ) : (
          <button type="button" disabled={busy} className={`${btn} text-warn`} onClick={() => run(() => bff(`sims/${id}`, "PATCH", { action: "stop" }), t("confirmStop"))}>
            {t("stop")}
          </button>
        ))}
      {(status === "stopped" || afterDelete) && (
        <button
          type="button"
          disabled={busy}
          className={`${btn} text-down`}
          onClick={() => run(async () => {
            await bff(`sims/${id}`, "DELETE");
          }, t("confirmDelete"))}
        >
          {t("delete")}
        </button>
      )}
      {error && <span className="text-xs text-down">{error}</span>}
    </span>
  );
}
