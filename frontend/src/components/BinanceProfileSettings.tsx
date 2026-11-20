"use client";

import { useEffect, useState } from "react";
import { utc } from "@/lib/research";
import { profileStatus, type ProfileSettings } from "@/lib/binanceProfiles";

export function BinanceProfileSettings({ initial, zh }: { initial: ProfileSettings; zh: boolean }) {
  const t = (cn: string, en: string) => (zh ? cn : en);
  const [cfg, setCfg] = useState(initial);
  const [secure, setSecure] = useState(false);
  const [credential, setCredential] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [enabled, setEnabled] = useState(initial.enabled);
  const [minutes, setMinutes] = useState(initial.minutes);
  useEffect(() => {
    setSecure(
      location.protocol === "https:" || ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname),
    );
  }, []);
  async function submit(action: "verify" | "save" | "clear") {
    if (action === "verify" && !secure) return;
    setBusy(true);
    setMessage("");
    try {
      const verify = action === "verify";
      const response = await fetch("/api/bff/settings/binance-profiles" + (verify ? "/verify" : ""), {
        method: verify ? "POST" : "PUT",
        headers: { "content-type": "application/json", "x-alphascope": "1" },
        body: JSON.stringify(
          verify
            ? { credential: credential.trim() || null }
            : { enabled: action === "clear" ? false : enabled, minutes, clear: action === "clear" },
        ),
      });
      const result = await response.json();
      if (response.ok) {
        setCfg(result);
        if (verify || action === "clear") setCredential("");
        if (action === "clear") setEnabled(false);
        setMessage(
          verify
            ? t("验证成功，可以读取公开持仓。", "Verified. Shared positions are accessible.")
            : t("设置已保存。", "Settings saved."),
        );
      } else {
        setMessage(profileStatus(typeof result.detail === "string" ? result.detail : "upstream_error", zh));
        const latest = await fetch("/api/bff/settings/binance-profiles", { cache: "no-store" });
        if (latest.ok) setCfg(await latest.json());
      }
    } catch {
      setMessage(t("服务连接失败", "Service unavailable"));
    } finally {
      setBusy(false);
    }
  }
  const button = "rounded border border-border px-3 py-2 disabled:opacity-50";
  return (
    <div className="grid max-w-2xl gap-4 text-sm">
      <div>
        <b>{t("公开名单每 30 分钟自动发现", "Public profiles discovered every 30 minutes")}</b>
        <p>
          {t("已收录主页", "Profiles recorded")} {cfg.directory_count} · {t("优先观察", "Prioritized")}{" "}
          {cfg.ids.length}
        </p>
        {(["BTC", "ETH"] as const).map((coin) => {
          const r = cfg.discovery?.[coin];
          return (
            <p key={coin} className="text-xs text-muted">
              {coin} ·{" "}
              {r ? (
                <>
                  {profileStatus(r.status, zh)} · {utc(r.at)} · {r.pages} {t("页", "pages")} ·{" "}
                  {r.public_profiles} {t("个公开主页", "public profiles")}
                </>
              ) : (
                t("等待首次发现", "Awaiting discovery")
              )}
            </p>
          );
        })}
      </div>
      <div className="grid gap-2">
        <b>
          {t("币安网页凭证", "Binance web credential")} ·{" "}
          {cfg.has_credential ? t("已保存", "Saved") : t("未设置", "Missing")}
        </b>
        <span>
          {t("上次验证成功", "Last verified")} {utc(cfg.verified_at)}
        </span>
        {cfg.verification && (
          <p>
            {t("最近验证", "Latest check")} · {profileStatus(cfg.verification, zh)} · {utc(cfg.checked_at)}
          </p>
        )}
        {cfg.resume_at && (
          <p>
            {t("最早重试", "Retry after")} {utc(cfg.resume_at)}
          </p>
        )}
        {secure ? (
          <label>
            {t(
              "粘贴新的 cURL，或留空验证已保存的凭证",
              "Paste new cURL or leave blank to check the saved credential",
            )}
            <textarea
              value={credential}
              onChange={(e) => setCredential(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              maxLength={65536}
              className="block w-full rounded border border-border bg-panel-2 p-2"
            />
          </label>
        ) : (
          <p>
            {t(
              "请通过 HTTPS 或本机 SSH 隧道打开设置，以输入或验证凭证。",
              "Use HTTPS or a localhost SSH tunnel to enter or verify credentials.",
            )}
          </p>
        )}
        <div className="flex gap-3">
          <button
            type="button"
            className={button}
            disabled={busy || !secure || (!credential.trim() && !cfg.has_credential)}
            onClick={() => submit("verify")}
          >
            {credential.trim()
              ? t("验证并保存凭证", "Verify and save credential")
              : t("验证已保存凭证", "Verify saved credential")}
          </button>
          {cfg.has_credential && (
            <button type="button" className={button} disabled={busy} onClick={() => submit("clear")}>
              {t("清除凭证并停采", "Clear and stop")}
            </button>
          )}
        </div>
      </div>
      <form
        className="grid gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void submit("save");
        }}
      >
        <label>
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />{" "}
          {t("采集已分享的 BTC、ETH 持仓", "Collect shared BTC and ETH positions")}
        </label>
        <label>
          {t("同一活跃主页最短间隔（分钟）", "Minimum interval per active profile (minutes)")}{" "}
          <input
            type="number"
            min={15}
            max={240}
            required
            value={minutes || ""}
            onChange={(e) => setMinutes(Number(e.target.value))}
            className="w-20 rounded border border-border bg-panel-2 p-1"
          />
        </label>
        <p className="text-xs text-muted">
          {t(
            "每分钟最多轮询一个主页，优先更新重点账户，并为新发现和久未更新的主页保留轮次。",
            "One profile per minute, with turns reserved for new and older profiles.",
          )}
        </p>
        <p>
          {t("采集状态", "Collection")} ·{" "}
          {cfg.enabled ? profileStatus(cfg.status, zh) : t("未启用", "Disabled")} ·{" "}
          {t("上次成功", "Last success")} {utc(cfg.last_success)}
        </p>
        <button className={button + " w-fit"} disabled={busy || !!credential.trim()}>
          {t("保存采集设置", "Save collection settings")}
        </button>
      </form>
      <span role="status">{message}</span>
    </div>
  );
}
