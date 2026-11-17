"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { AppSettings } from "@/lib/api";
import { bff } from "@/lib/bff";

export function SettingsForm({ initial }: { initial: AppSettings }) {
  const t = useTranslations("settings");
  const router = useRouter();
  const [smtp, setSmtp] = useState({ ...initial.smtp, recipients: initial.smtp.recipients.join(", "), password: "" });
  const [live, setLive] = useState(initial.live);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const input = "mt-1 w-full rounded border border-border bg-panel-2 px-2 py-1 text-sm";
  const run = async (fn: () => Promise<string>) => {
    setBusy(true);
    setMsg(null);
    try {
      setMsg({ ok: true, text: await fn() });
      router.refresh();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };
  const saveSmtp = () =>
    bff("settings/smtp", "PUT", {
      host: smtp.host,
      port: Number(smtp.port),
      security: smtp.security,
      username: smtp.username,
      password: smtp.password || null,
      from_name: smtp.from_name,
      recipients: smtp.recipients.split(/[,，\s]+/).map((x) => x.trim()).filter(Boolean),
      public_url: smtp.public_url,
    });

  return (
    <div className="space-y-5">
      <section className="rounded-lg border border-border bg-panel p-4">
        <h2 className="text-sm font-semibold">{t("smtp")}</h2>
        <p className="mt-1 text-xs leading-relaxed text-muted">{t("smtpHint")}</p>
        <div className="mt-3 grid gap-3 text-xs text-muted md:grid-cols-3">
          <label>
            {t("host")}
            <input className={input} value={smtp.host} onChange={(e) => setSmtp({ ...smtp, host: e.target.value })} />
          </label>
          <label>
            {t("port")}
            <input className={input} type="number" value={smtp.port} onChange={(e) => setSmtp({ ...smtp, port: Number(e.target.value) })} />
          </label>
          <label>
            {t("security")}
            <select className={input} value={smtp.security} onChange={(e) => setSmtp({ ...smtp, security: e.target.value as AppSettings["smtp"]["security"] })}>
              <option value="starttls">STARTTLS (587)</option>
              <option value="ssl">SSL (465)</option>
              <option value="none">none</option>
            </select>
          </label>
          <label>
            {t("username")}
            <input className={input} value={smtp.username} autoComplete="off" onChange={(e) => setSmtp({ ...smtp, username: e.target.value })} />
          </label>
          <label>
            {t("password")}
            <input
              className={input}
              type="password"
              autoComplete="new-password"
              value={smtp.password}
              placeholder={initial.smtp.password_set ? t("passwordSet") : t("passwordUnset")}
              onChange={(e) => setSmtp({ ...smtp, password: e.target.value })}
            />
          </label>
          <label>
            {t("fromName")}
            <input className={input} value={smtp.from_name} onChange={(e) => setSmtp({ ...smtp, from_name: e.target.value })} />
          </label>
          <label className="md:col-span-2">
            {t("recipients")}
            <input className={input} value={smtp.recipients} onChange={(e) => setSmtp({ ...smtp, recipients: e.target.value })} />
          </label>
          <label>
            {t("publicUrl")}
            <input className={input} value={smtp.public_url} placeholder="http://194.163.135.37:3200" onChange={(e) => setSmtp({ ...smtp, public_url: e.target.value })} />
          </label>
        </div>
        <div className="mt-3 flex gap-2">
          <button type="button" disabled={busy} className="rounded bg-accent px-4 py-1 text-sm text-white" onClick={() => run(async () => (await saveSmtp(), t("saved")))}>
            {t("save")}
          </button>
          <button
            type="button"
            disabled={busy}
            className="rounded border border-border px-4 py-1 text-sm"
            onClick={() =>
              run(async () => {
                await saveSmtp();
                const r = await bff<{ recipients: string[] }>("settings/smtp/test", "POST");
                return t("testOk", { to: r.recipients.join(", ") });
              })
            }
          >
            {busy ? t("testing") : t("test")}
          </button>
        </div>
      </section>

      <section className="rounded-lg border border-border bg-panel p-4">
        <h2 className="text-sm font-semibold">{t("live")}</h2>
        <p className="mt-1 text-xs text-muted">{t("intervalHint")}</p>
        <div className="mt-3 grid gap-3 text-xs text-muted md:grid-cols-6">
          <label>
            {t("interval")}
            <input className={input} type="number" min={10} value={live.interval_seconds} onChange={(e) => setLive({ ...live, interval_seconds: Number(e.target.value) })} />
          </label>
          <label>
            {t("okxInterval")}
            <input className={input} type="number" min={30} value={live.okx_interval_seconds ?? 60} onChange={(e) => setLive({ ...live, okx_interval_seconds: Number(e.target.value) })} />
          </label>
          <label>
            {t("coalesce")}
            <input className={input} type="number" min={0} value={live.coalesce_seconds} onChange={(e) => setLive({ ...live, coalesce_seconds: Number(e.target.value) })} />
          </label>
          <label>
            {t("maxPerHour")}
            <input className={input} type="number" min={1} value={live.max_emails_per_hour} onChange={(e) => setLive({ ...live, max_emails_per_hour: Number(e.target.value) })} />
          </label>
          <label>
            {t("liqPct")}
            <input className={input} type="number" step="0.5" value={live.liq_risk_pct} onChange={(e) => setLive({ ...live, liq_risk_pct: Number(e.target.value) })} />
          </label>
          <label>
            {t("timezone")}
            <input className={input} value={live.timezone} onChange={(e) => setLive({ ...live, timezone: e.target.value })} />
          </label>
        </div>
        <button type="button" disabled={busy} className="mt-3 rounded bg-accent px-4 py-1 text-sm text-white" onClick={() => run(async () => (await bff("settings/live", "PUT", live), t("saved")))}>
          {t("save")}
        </button>
      </section>
      {msg && <p className={`text-sm ${msg.ok ? "text-up" : "text-down"}`}>{msg.text}</p>}
    </div>
  );
}
