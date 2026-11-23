"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { bff } from "@/lib/bff";
import { button, input, primary } from "./ui";

/** Review buttons, copy-to-post and the "record as published" form. Publishing itself happens on X, by a person. */
export function ReviewButtons({ articleId, status }: { articleId: number; status: string }) {
  const t = useTranslations("desk");
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [ack, setAck] = useState(false);
  const [needsAck, setNeedsAck] = useState(false);
  async function act(action: string, note = "") {
    setError(null);
    try {
      await bff(`articles/${articleId}/review`, "POST", { action, note, ack_duplicate: ack });
      router.refresh();
    } catch (e) {
      const err = e as Error & { detail?: { message?: string } };
      if (err.message.includes("acknowledge")) setNeedsAck(true);
      setError(err.message);
    }
  }
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {["draft_ready", "changes_requested"].includes(status) && <button className={primary} onClick={() => act("submit")}>{t("submitReview")}</button>}
        {status === "in_review" && <button className={primary} onClick={() => act("approve")}>{t("approve")}</button>}
        {["in_review", "approved"].includes(status) && <button className={button} onClick={() => { const n = window.prompt(t("note")); if (n !== null) act("request_changes", n); }}>{t("requestChanges")}</button>}
        {!["published", "retracted", "killed"].includes(status) && <button className={`${button} text-down`} onClick={() => { const n = window.prompt(t("note")); if (n !== null) act("kill", n); }}>{t("kill")}</button>}
      </div>
      {needsAck && (
        <label className="flex items-center gap-1 text-xs text-warn"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> {t("ackDuplicate")}</label>
      )}
      {error && <p className="text-xs text-down">{error}</p>}
    </div>
  );
}

export function PublishForm({ articleId, handle, text }: { articleId: number; handle: string; text: string }) {
  const t = useTranslations("desk");
  const tc = useTranslations("common");
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [blockers, setBlockers] = useState<string[] | null>(null);
  const [copied, setCopied] = useState(false);
  return (
    <div className="space-y-3">
      <button className={button} onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        const r = await bff<{ blockers: string[] }>(`articles/${articleId}/copied`, "POST", {}).catch(() => null);
        setBlockers(r?.blockers ?? null);
      }}>{copied ? tc("copied") : t("copyText")}</button>
      {blockers && blockers.length > 0 && <p className="text-xs text-down">{t("blockers")}: {blockers.join("; ")}</p>}
      <form className="space-y-2" action={async (f) => {
        setError(null);
        const local = String(f.get("published_at"));
        try {
          await bff(`articles/${articleId}/publish`, "POST", {
            x_url: f.get("x_url"), published_at: new Date(local).toISOString(),
            final_text: String(f.get("final_text") ?? "").trim() || null,
          });
          router.refresh();
        } catch (e) {
          setError((e as Error).message);
        }
      }}>
        <input name="x_url" className={input} placeholder={`https://x.com/${handle}/status/…`} required />
        <label className="block text-xs text-muted">{t("publishedAt")}
          <input name="published_at" type="datetime-local" className={input} required defaultValue={new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16)} />
        </label>
        <textarea name="final_text" className={`${input} h-16`} placeholder={t("finalText")} />
        <button className={primary}>{t("markPublished")}</button>
        {error && <p className="text-xs text-down">{error}</p>}
      </form>
    </div>
  );
}

export function CardPanel({ packetId, language }: { packetId: number; language: string }) {
  const t = useTranslations("desk");
  const [asset, setAsset] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {["position_card", "close_card", "liq_card"].map((tpl) => (
          <button key={tpl} className={button} onClick={async () => {
            setError(null);
            try {
              const r = await bff<{ assets: { card_png: number } }>("assets/render", "POST", { fact_packet_id: packetId, template: tpl, language });
              setAsset(r.assets.card_png);
            } catch (e) {
              setError((e as Error).message);
            }
          }}>{t("renderCard")}: {tpl}</button>
        ))}
      </div>
      {error && <p className="text-xs text-down">{error}</p>}
      {asset && (
        <a href={`/api/bff/assets/${asset}/file`} download>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={`/api/bff/assets/${asset}/file`} alt="card" className="w-full rounded border border-border" />
        </a>
      )}
    </div>
  );
}
