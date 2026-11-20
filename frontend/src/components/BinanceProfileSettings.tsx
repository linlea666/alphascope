"use client";

import { useState } from "react";
import { utc } from "@/lib/research";
import { profileStatus, type ProfileSettings } from "@/lib/binanceProfiles";

export function BinanceProfileSettings({
  initial,
  candidate,
  zh,
}: {
  initial: ProfileSettings;
  candidate?: string;
  zh: boolean;
}) {
  const [cfg, setCfg] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const t = (cn: string, en: string) => (zh ? cn : en);
  return (
    <form
      className="grid max-w-2xl gap-3 text-sm"
      onSubmit={async (e) => {
        e.preventDefault();
        const form = e.currentTarget;
        const data = new FormData(form);
        const clear = data.get("clear") === "on";
        const credential = String(data.get("credential") || "").trim();
        const ids = String(data.get("ids") || "")
          .trim()
          .split(/[\s,，]+/)
          .filter(Boolean);
        setBusy(true);
        try {
          const r = await fetch("/api/bff/settings/binance-profiles", {
            method: "PUT",
            headers: { "content-type": "application/json", "x-alphascope": "1" },
            body: JSON.stringify({
              enabled: !clear && data.get("enabled") === "on",
              ids,
              minutes: Number(data.get("minutes")),
              credential: clear ? null : credential || null,
              clear,
            }),
          });
          if (!r.ok) {
            setMessage(
              r.status === 409
                ? t("正在采集，稍后再保存。", "Collection in progress. Try again shortly.")
                : t(
                    "保存失败，请检查主页 ID、间隔和凭证。",
                    "Could not save. Check IDs, interval and credentials.",
                  ),
            );
            return;
          }
          setCfg(await r.json());
          (form.elements.namedItem("credential") as HTMLTextAreaElement).value = "";
          if (clear) (form.elements.namedItem("enabled") as HTMLInputElement).checked = false;
          (form.elements.namedItem("clear") as HTMLInputElement).checked = false;
          setMessage(
            t("设置已保存。刷新页面可查看验证结果。", "Saved. Refresh to see the validation result."),
          );
        } catch {
          setMessage(t("未能连接服务", "Service unavailable"));
        } finally {
          setBusy(false);
        }
      }}
    >
      <p>
        {t("凭证", "Credential")} · {cfg.has_credential ? t("已保存", "Saved") : t("未设置", "Missing")} ·{" "}
        {profileStatus(cfg.status, zh)}
      </p>
      <p className="text-xs text-muted">
        {t("上次尝试", "Last attempt")} {utc(cfg.last_attempt)} · {t("上次成功", "Last success")}{" "}
        {utc(cfg.last_success)}
        {cfg.resume_at && (
          <>
            {" "}
            · {t("最早恢复", "Retry after")} {utc(cfg.resume_at)}
          </>
        )}
      </p>
      <label>
        <input name="enabled" type="checkbox" defaultChecked={cfg.enabled} />{" "}
        {t("采集已分享的 BTC、ETH 永续仓位", "Collect shared BTC and ETH perpetual positions")}
      </label>
      <label>
        {t(
          "聪明钱主页 ID（19 位数字，最多 10 个，用空格分隔）",
          "Smart Money profile IDs (19 digits, up to ten, space separated)",
        )}
        <textarea
          name="ids"
          defaultValue={Array.from(new Set([...cfg.ids, ...(candidate ? [candidate] : [])])).join(" ")}
          className="block w-full rounded border border-border bg-panel-2 p-2"
        />
      </label>
      <label>
        {t("每个主页最短采集间隔（分钟）", "Minimum interval per profile (minutes)")}{" "}
        <input
          type="number"
          min={15}
          max={240}
          required
          className="w-20 rounded border border-border bg-panel-2 p-1"
          name="minutes"
          defaultValue={cfg.minutes}
        />
      </label>
      <label>
        {t(
          "更新币安网页凭证（粘贴 cURL，留空沿用）",
          "Replace Binance web credential (paste cURL, leave blank to keep)",
        )}
        <textarea
          name="credential"
          autoComplete="off"
          spellCheck={false}
          maxLength={65536}
          className="block w-full rounded border border-border bg-panel-2 p-2"
        />
      </label>
      <p className="text-xs text-muted">
        {t(
          "服务器加密保存登录字段。首次采集时验证，访问被拒会暂停。",
          "Login fields are encrypted on the server and checked on first collection. Access denial pauses collection.",
        )}
      </p>
      <label>
        <input name="clear" type="checkbox" />{" "}
        {t("清除已存凭证并停止采集", "Remove credential and stop collection")}
      </label>
      <button disabled={busy} className="w-fit rounded border border-border px-3 py-2">
        {t("保存设置", "Save settings")}
      </button>
      <span role="status">{message}</span>
    </form>
  );
}
