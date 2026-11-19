"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

export function NoticeWatch({ id, watching, zh }: { id: number; watching: boolean; zh: boolean }) {
  const router = useRouter();
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  async function toggle() {
    setBusy(true);
    try {
      const r = await fetch(`/api/bff/research/notices/${id}/watch`, {
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
