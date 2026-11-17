"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { bff } from "@/lib/bff";
import { toast } from "@/lib/toast";

/** Pause / resume / delete one sentinel. */
export function SentinelActions({ id, enabled }: { id: number; enabled: boolean }) {
  const t = useTranslations("sentinel");
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>, msg: string) => {
    setBusy(true);
    try {
      await fn();
      toast(msg);
      router.refresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="flex gap-1.5 text-xs">
      <button type="button" disabled={busy} onClick={() => run(() => bff(`alerts/sentinels/${id}`, "PATCH", { enabled: !enabled }), enabled ? t("paused") : t("resumed"))} className="rounded border border-border px-2 py-0.5 text-muted hover:text-text">
        {enabled ? t("pause") : t("resume")}
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={() => confirm(t("confirmDelete")) && run(() => bff(`alerts/sentinels/${id}`, "DELETE"), t("deleted"))}
        className="rounded border border-border px-2 py-0.5 text-down"
      >
        {t("delete")}
      </button>
    </span>
  );
}

/** Daily digest email switch + recipients, and a "build now" button. */
export function DigestSettings({ enabled, recipients }: { enabled: boolean; recipients: string[] }) {
  const t = useTranslations("digest");
  const router = useRouter();
  const [on, setOn] = useState(enabled);
  const [to, setTo] = useState(recipients.join(", "));
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await bff("settings/digest", "PUT", { enabled: on, recipients: to.split(/[,，\s]+/).filter(Boolean) });
      toast(t("saved"));
      router.refresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  };
  const build = async () => {
    setBusy(true);
    try {
      await bff("alerts/digest/run", "POST", {});
      toast(t("built"));
      router.refresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-wrap items-end gap-3 text-sm">
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} />
        {t("enable")}
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted">
        {t("recipients")}
        <input value={to} onChange={(e) => setTo(e.target.value)} placeholder={t("recipientsHint")} className="w-72 rounded border border-border bg-panel-2 px-2 py-1 text-sm" />
      </label>
      <button type="button" disabled={busy} onClick={save} className="rounded bg-accent px-3 py-1 text-white disabled:opacity-60">
        {t("save")}
      </button>
      <button type="button" disabled={busy} onClick={build} className="rounded border border-border px-3 py-1 text-muted hover:text-text disabled:opacity-60">
        {t("buildNow")}
      </button>
    </div>
  );
}
