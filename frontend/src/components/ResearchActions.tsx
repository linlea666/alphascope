"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

export function NoticeWatch({ id, watching, zh, resource = "notices" }: { id: number; watching: boolean; zh: boolean; resource?: "notices" | "exchange" }) {
  const router = useRouter();
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  async function toggle() {
    setBusy(true);
    try {
      const r = await fetch(`/api/bff/research/${resource}/${id}/watch`, {
        method: "PUT",
        headers: { "content-type": "application/json", "x-alphascope": "1" },
        body: JSON.stringify({ watching: !watching }),
      });
      if (!r.ok) throw new Error();
      setError(false);
      router.refresh();
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <button type="button" disabled={busy} onClick={toggle} className="text-xs text-accent">
      {error
        ? zh
          ? "保存失败，重试"
          : "Retry"
        : watching
          ? zh
            ? "已关注 · 取消"
            : "Watching · remove"
          : zh
            ? "保存观察"
            : "Watch"}
    </button>
  );
}

export function PublishReport({
  body,
  zh,
}: {
  body: { event_id: number; coin: string; hours: number; mode: string; vintage: string };
  zh: boolean;
}) {
  const router = useRouter();
  const [state, setState] = useState<"idle" | "pending" | "error">("idle");
  async function publish() {
    setState("pending");
    try {
      const r = await fetch("/api/bff/research/reports", {
        method: "POST",
        headers: { "content-type": "application/json", "x-alphascope": "1" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error();
      const { id } = await r.json();
      router.push("?" + new URLSearchParams({ coin: body.coin, report: String(id) }));
    } catch {
      setState("error");
    }
  }
  return (
    <button className="mt-2 text-sm text-accent" disabled={state === "pending"} onClick={publish}>
      {state === "error"
        ? zh
          ? "保存失败 · 重试"
          : "Save failed · retry"
        : zh
          ? "保存此版本的研究证据"
          : "Save this evidence version"}
    </button>
  );
}

export function EventEditor({ coin, parent, zh }: { coin: string; parent?: number; zh: boolean }) {
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(form: FormData) {
    setBusy(true);
    setMessage("");
    try {
      const payload = {
        coin,
        title: form.get("title"),
        start_at: form.get("start") + "Z",
        end_at: form.get("end") + "Z",
        parent_id: form.has("revise") ? parent : null,
      };
      const r = await fetch("/api/bff/research/events", {
        method: "POST",
        headers: { "content-type": "application/json", "x-alphascope": "1" },
        body: JSON.stringify(payload),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : String(r.status));
      router.push("?" + new URLSearchParams({ coin, event: String(data.id) }));
      router.refresh();
    } catch (err) {
      setMessage(String(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="rounded border border-border p-3">
      <summary className="cursor-pointer text-sm text-accent">
        {zh ? "定义研究事件" : "Define a research event"}
      </summary>
      <form action={submit} className="mt-2 flex flex-wrap items-end gap-3 text-sm">
        <label>
          {zh ? "名称" : "Title"} <input required maxLength={160} className="bg-panel-2" name="title" />
        </label>
        <label>
          {zh ? "起点 UTC" : "Start UTC"}{" "}
          <input required className="bg-panel-2" type="datetime-local" name="start" />
        </label>
        <label>
          {zh ? "终点 UTC" : "End UTC"}{" "}
          <input required className="bg-panel-2" type="datetime-local" name="end" />
        </label>
        {parent && (
          <label>
            <input type="checkbox" name="revise" /> {zh ? "修订所选事件" : "Revise selected event"}
          </label>
        )}
        <button disabled={busy} className="text-accent">
          {zh ? "核对分钟价格并保存" : "Verify minute prices and save"}
        </button>
        {message && <p role="alert">{message}</p>}
      </form>
    </details>
  );
}

export function FactorReviewForm({
  study,
  evaluation,
  eligible,
  zh,
}: {
  study: number;
  evaluation: number;
  eligible: boolean;
  zh: boolean;
}) {
  const router = useRouter();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  async function submit(form: FormData) {
    setSaving(true);
    setError("");
    try {
      const response = await fetch(`/api/bff/research/experiments/${study}/reviews`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-alphascope": "1" },
        body: JSON.stringify({
          evaluation_id: evaluation,
          decision: form.get("decision"),
          rationale: form.get("reason"),
        }),
      });
      if (!response.ok) throw new Error((await response.json()).detail || String(response.status));
      router.refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }
  return (
    <details className="mt-1 text-xs">
      <summary className="text-accent">{zh ? "审核此版本" : "Review this version"}</summary>
      <form action={submit} className="mt-2 flex flex-wrap gap-2">
        <select aria-label={zh ? "决定" : "Decision"} className="bg-panel-2" name="decision">
          {eligible && (
            <option value="approve">{zh ? "纳入该币种正式观察" : "Approve for this asset"}</option>
          )}
          <option value="reject">{zh ? "拒绝" : "Reject"}</option>
          <option value="withdraw">{zh ? "撤回" : "Withdraw"}</option>
        </select>
        <input
          required
          maxLength={2000}
          className="bg-panel-2"
          name="reason"
          aria-label={zh ? "审核理由" : "Rationale"}
          placeholder={zh ? "审核理由" : "Rationale"}
        />
        <button disabled={saving} className="text-accent">
          {zh ? "保存审核" : "Save review"}
        </button>
        {error && <span role="alert">{error}</span>}
      </form>
    </details>
  );
}
