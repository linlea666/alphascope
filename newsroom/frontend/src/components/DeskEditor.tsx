"use client";

import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { bff } from "@/lib/bff";
import { factName, named, parsePromptRef, partName, word } from "@/lib/glossary";
import type { Fact, GuardReport, Version } from "@/lib/types";
import { DiffView } from "./AiStudio";
import { Badge, button, input, primary } from "./ui";

function Highlighted({ text, report, part }: { text: string; report: GuardReport | null; part: string }) {
  const tg = useTranslations("guardCodes");
  const spans = (report?.findings ?? []).filter((f) => f.part === part && f.span).sort((a, b) => a.span![0] - b.span![0]);
  if (!spans.length) return null;
  const out: React.ReactNode[] = [];
  let at = 0;
  spans.forEach((f, i) => {
    const [a, b] = f.span!;
    if (a < at) return;
    out.push(text.slice(at, a));
    out.push(<mark key={i} title={tg.has(`${f.code}.what`) ? `${tg(`${f.code}.what`)}：${tg(`${f.code}.fix`)}` : `${f.code}: ${f.detail}`} className={f.severity === "fail" ? "bg-down/30 text-text" : "bg-warn/30 text-text"}>{text.slice(a, b)}</mark>);
    at = b;
  });
  out.push(text.slice(at));
  return <div className="mt-1 whitespace-pre-wrap rounded border border-border bg-panel-2 p-2 text-sm">{out}</div>;
}

export function GuardList({ report }: { report: GuardReport | null }) {
  const t = useTranslations("desk");
  const tg = useTranslations("guardCodes");
  const tp = useTranslations("guardParts");
  if (!report) return null;
  return (
    <div className="space-y-1 text-xs">
      <div className="flex items-center gap-2">
        <Badge tone={report.status === "pass" ? "up" : report.status === "warn" ? "warn" : "down"}>
          {t("guard")}: {report.status === "pass" ? t("guardPass") : report.status === "warn" ? t("guardWarn") : t("guardFail")}
        </Badge>
        <span className="text-muted">{t("xLength")} {report.x_length}/{report.length_limit ?? 280}</span>
      </div>
      {report.findings.map((f, i) => {
        const known = tg.has(`${f.code}.what`);
        return (
          <div key={i} className={f.severity === "fail" ? "text-down" : "text-warn"}>
            [{partName(tp, f.part)}] <b>{known ? tg(`${f.code}.what`) : f.code}</b> {f.text && <code>“{f.text}”</code>}
            {known ? <span className="text-muted"> — {tg(`${f.code}.fix`)}</span> : <> — {f.detail}</>}
          </div>
        );
      })}
    </div>
  );
}

export function DeskEditor({ articleId, status, current, versions, facts, currentPacketId, editable }: {
  articleId: number;
  status: string;
  current: Version | null;
  versions: Version[];
  facts: Fact[];
  currentPacketId: number;
  editable: boolean;
}) {
  const t = useTranslations("desk");
  const tf = useTranslations("factNames");
  const tr = useTranslations("roles");
  const tst = useTranslations("statusWords");
  const locale = useLocale() as "zh" | "en";
  const router = useRouter();
  const origin = (v: Version) => {
    if (v.origin !== "ai") return `${t("human")} · ${v.author ?? ""}`;
    const ref = parsePromptRef(v.prompt_ref);
    return [t("ai"), named(tr, v.agent_key ?? ref?.role).name, v.model, ref ? `${t("promptV")}${ref.version}${ref.language ? ` (${ref.language})` : ""}` : null]
      .filter(Boolean).join(" · ");
  };
  const [headline, setHeadline] = useState(current?.headline ?? "");
  const [body, setBody] = useState(current?.body ?? "");
  const [thread, setThread] = useState((current?.thread ?? []).join("\n"));
  const [used, setUsed] = useState<string[]>(current?.facts_used ?? []);
  const [report, setReport] = useState<GuardReport | null>(current?.guard_report ?? null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [diffWith, setDiffWith] = useState<Version | null>(null);
  const threadList = useMemo(() => thread.split("\n").map((s) => s.trim()).filter(Boolean), [thread]);
  const payload = { headline, body, thread: threadList.length ? threadList : null, facts_used: used };

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      {current && current.fact_packet_id !== currentPacketId && <div className="rounded border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">{t("packetOutdated")}</div>}
      {status === "stale" && <div className="rounded border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">{t("stale")}</div>}
      <label className="block text-xs text-muted">{t("headline")}
        <input className={input} value={headline} onChange={(e) => setHeadline(e.target.value)} disabled={!editable} />
      </label>
      <Highlighted text={headline} report={report} part="headline" />
      <label className="block text-xs text-muted">{t("body")}
        <textarea className={`${input} h-48 font-sans`} value={body} onChange={(e) => setBody(e.target.value)} disabled={!editable} />
      </label>
      <Highlighted text={body} report={report} part="body" />
      <label className="block text-xs text-muted">{t("thread")}
        <textarea className={`${input} h-20`} value={thread} onChange={(e) => setThread(e.target.value)} disabled={!editable} />
      </label>
      <details className="text-xs">
        <summary className="cursor-pointer text-muted">{t("factsUsed")} ({used.length})</summary>
        <div className="mt-2 grid gap-1 md:grid-cols-2">
          {facts.map((f) => (
            <label key={f.id} className="flex items-center gap-1">
              <input type="checkbox" checked={used.includes(f.id)} disabled={!editable}
                onChange={(e) => setUsed(e.target.checked ? [...used, f.id] : used.filter((x) => x !== f.id))} />
              <span title={f.id}>{factName(tf, f.id, f.label)} <span className="text-muted">{(f.display[locale] ?? f.display.zh)?.[0] ?? ""}</span></span>
            </label>
          ))}
        </div>
      </details>
      {editable && (
        <div className="flex flex-wrap gap-2">
          <button className={button} disabled={busy} onClick={() => run(async () => setReport(await bff<GuardReport>(`articles/${articleId}/guard`, "POST", payload)))}>{t("checkGuard")}</button>
          <button className={primary} disabled={busy || !body.trim()} onClick={() => run(async () => {
            const v = await bff<{ guard: GuardReport }>(`articles/${articleId}/versions`, "POST", { ...payload, fact_packet_id: currentPacketId });
            setReport(v.guard);
            router.refresh();
          })}>{t("saveVersion")}</button>
        </div>
      )}
      {error && <p className="text-xs text-down">{error}</p>}
      <GuardList report={report} />
      {current && (
        <div className="grid gap-1 rounded border border-border p-2 text-xs text-muted">
          <div>{t("origin")}: {origin(current)}</div>
          {current.angle && <div>{t("angle")}: {current.angle}</div>}
          {current.added_value && <div>{t("addedValue")}: {current.added_value}</div>}
          {current.difference_from_others && <div>{t("difference")}: {current.difference_from_others}</div>}
          {current.why_shareable && <div>{t("whyShareable")}: {current.why_shareable}</div>}
          {current.why_now && <div>{t("whyNow")}: {current.why_now}</div>}
          {current.open_question && <div>{t("openQuestion")}: {current.open_question}</div>}
          {(current.lede_fact_ids?.length ?? 0) > 0 && <div>{t("ledeFacts")}: {current.lede_fact_ids!.map((id) => factName(tf, id)).join("、")}</div>}
          {current.card_asset_id && (
            <a href={`/api/bff/assets/${current.card_asset_id}/file`} target="_blank" rel="noreferrer">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={`/api/bff/assets/${current.card_asset_id}/file`} alt="card" className="mt-2 w-full max-w-md rounded border border-border" />
            </a>
          )}
          {current.risks?.length > 0 && <div>{t("risks")}: {current.risks.join("; ")}</div>}
          {current.alt_headlines?.length > 0 && <div>alt: {current.alt_headlines.join(" | ")}</div>}
        </div>
      )}
      {diffWith && (
        <div>
          <div className="mb-1 text-xs text-muted">Δ v{diffWith.version_no} → editor</div>
          <DiffView a={[diffWith.headline, diffWith.body].filter(Boolean).join("\n")} b={[headline, body].filter(Boolean).join("\n")} />
        </div>
      )}
      <div>
        <div className="mb-1 text-xs font-semibold">{t("versions")}</div>
        {versions.map((v) => (
          <div key={v.id} className="flex flex-wrap items-center gap-2 border-b border-border/60 py-1 text-xs">
            <span>v{v.version_no}</span>
            <Badge>{v.origin === "ai" ? named(tr, v.agent_key).name || t("ai") : t("human")}</Badge>
            <Badge tone={v.guard_status === "pass" ? "up" : v.guard_status === "warn" ? "warn" : "down"}>{word(tst, v.guard_status)}</Badge>
            <span className="text-muted" title={t("factsVersionHint")}>{t("factsVersion", { v: v.fact_packet_version })}</span>
            {current?.id === v.id ? <Badge tone="accent">{t("current")}</Badge> : editable && (
              <button className="text-accent" onClick={() => run(async () => { await bff(`articles/${articleId}/accept-version`, "POST", { version_id: v.id }); router.refresh(); })}>{t("useVersion")}</button>
            )}
            <button className="text-muted hover:text-text" onClick={() => { setHeadline(v.headline); setBody(v.body); setThread((v.thread ?? []).join("\n")); setUsed(v.facts_used); setReport(v.guard_report); }}>↺</button>
            <button className="text-muted hover:text-text" title="diff" onClick={() => setDiffWith(diffWith?.id === v.id ? null : v)}>Δ</button>
            <span className="truncate text-muted">{v.headline || v.body.slice(0, 40)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
