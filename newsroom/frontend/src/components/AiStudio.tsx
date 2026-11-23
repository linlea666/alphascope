"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { bff } from "@/lib/bff";
import { GuardList } from "./DeskEditor";
import type { GuardReport } from "@/lib/types";
import { Badge, button, input, primary } from "./ui";

type ModelOpt = { id: number; name: string; model: string; last_test_ok: boolean | null };
type CompareVersion = { id: number; version_no: number; model: string | null; agent_key: string | null; headline: string; body: string;
  thread: string[] | null; angle: string; guard_status: string; findings: GuardReport["findings"]; x_length: number | null;
  cost_usd: number | null; latency_ms: number | null; input_tokens: number | null; output_tokens: number | null };

/** AI revisions (draft / polish / fix) with an optional model, and side-by-side drafts from several models. */
export function AiStudio({ articleId, models, editable }: { articleId: number; models: ModelOpt[]; editable: boolean }) {
  const t = useTranslations("studio");
  const router = useRouter();
  const [model, setModel] = useState<string>("");
  const [picked, setPicked] = useState<number[]>(models.map((m) => m.id).slice(0, 3));
  const [msg, setMsg] = useState<string | null>(null);
  const [data, setData] = useState<{ versions: CompareVersion[]; failures: { model: string; status: string; error: string }[]; pending: number } | null>(null);

  async function act(fn: () => Promise<unknown>) {
    setMsg(null);
    try {
      await fn();
      setMsg(t("pending"));
      router.refresh();
    } catch (e) {
      setMsg((e as Error).message);
    }
  }
  const load = async () => setData(await bff(`articles/${articleId}/compare`));

  return (
    <div className="space-y-4 text-sm">
      {editable && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <select className={`${input} w-56`} value={model} onChange={(e) => setModel(e.target.value)}>
              <option value="">{t("defaultModel")}</option>
              {models.map((m) => <option key={m.id} value={m.id}>{m.name} · {m.model}{m.last_test_ok === false ? " ⚠" : ""}</option>)}
            </select>
            {(["draft", "polish", "fix"] as const).map((mode) => (
              <button key={mode} className={button} onClick={() => act(() => bff(`articles/${articleId}/ai`, "POST",
                { mode, model_config_id: model ? Number(model) : null }))}>{t(mode)}</button>
            ))}
          </div>
          {models.length === 0 && <p className="text-xs text-warn">{t("noModels")}</p>}
        </div>
      )}
      <div className="border-t border-border pt-3">
        <div className="mb-1 font-semibold">{t("compare")}</div>
        <p className="mb-2 text-xs text-muted">{t("compareHint")}</p>
        {editable && (
          <div className="mb-2 flex flex-wrap items-center gap-3">
            {models.map((m) => (
              <label key={m.id} className="flex items-center gap-1 text-xs">
                <input type="checkbox" checked={picked.includes(m.id)}
                  onChange={(e) => setPicked(e.target.checked ? [...picked, m.id] : picked.filter((x) => x !== m.id))} />{m.name}
              </label>
            ))}
            <button className={primary} disabled={!picked.length}
              onClick={() => act(() => bff(`articles/${articleId}/compare`, "POST", { model_ids: picked }))}>{t("runCompare")}</button>
          </div>
        )}
        <button className="text-xs text-accent" onClick={load}>↻ {t("compare")}</button>
        {msg && <span className="ml-2 text-xs text-muted">{msg}</span>}
        {data && (
          <div className="mt-2 space-y-3">
            {data.pending > 0 && <Badge tone="warn">{t("pending")}: {data.pending}</Badge>}
            <div className="grid gap-3 md:grid-cols-2">
              {data.versions.map((v) => (
                <div key={v.id} className="rounded border border-border p-2">
                  <div className="mb-1 flex flex-wrap items-center gap-1 text-xs">
                    <Badge tone="accent">{v.model ?? "?"}</Badge><span className="text-muted">v{v.version_no} · {v.agent_key}</span>
                    <Badge tone={v.guard_status === "pass" ? "up" : v.guard_status === "warn" ? "warn" : "down"}>{v.guard_status}</Badge>
                    <span className="text-muted">{t("cost")} ${Number(v.cost_usd ?? 0).toFixed(4)} · {t("latency")} {v.latency_ms ?? "—"}ms · X {v.x_length ?? "—"}</span>
                  </div>
                  {v.headline && <div className="font-medium">{v.headline}</div>}
                  <p className="whitespace-pre-wrap">{v.body}</p>
                  {(v.thread ?? []).map((x, i) => <p key={i} className="mt-1 whitespace-pre-wrap text-muted">{x}</p>)}
                  <GuardList report={{ status: v.guard_status as GuardReport["status"], findings: v.findings ?? [], x_length: v.x_length ?? 0 }} />
                </div>
              ))}
            </div>
            {data.failures.length > 0 && (
              <div className="text-xs text-down">{t("failures")}: {data.failures.map((f) => `${f.model} ${f.status} ${f.error ?? ""}`).join(" | ")}</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** Character diff (zh) / word diff (en) between two versions, by longest common subsequence. */
export function DiffView({ a, b }: { a: string; b: string }) {
  const tokens = (s: string) => (/[一-鿿]/.test(s) ? Array.from(s) : s.split(/(\s+)/));
  const x = tokens(a), y = tokens(b);
  const n = x.length, m = y.length;
  if (n * m > 400000) return <p className="text-xs text-muted">too long to diff</p>;
  const dp = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: { k: "=" | "-" | "+"; s: string }[] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) { out.push({ k: "=", s: x[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) out.push({ k: "-", s: x[i++] });
    else out.push({ k: "+", s: y[j++] });
  }
  while (i < n) out.push({ k: "-", s: x[i++] });
  while (j < m) out.push({ k: "+", s: y[j++] });
  return (
    <p className="whitespace-pre-wrap rounded border border-border bg-panel-2 p-2 text-sm">
      {out.map((p, idx) => p.k === "=" ? <span key={idx}>{p.s}</span>
        : <span key={idx} className={p.k === "-" ? "bg-down/25 line-through" : "bg-up/25"}>{p.s}</span>)}
    </p>
  );
}
