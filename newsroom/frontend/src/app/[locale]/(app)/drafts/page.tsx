import { getLocale, getTranslations } from "next-intl/server";
import { AiReadiness } from "@/components/AiReadiness";
import { AutoRefresh } from "@/components/AutoRefresh";
import { ClassLabel, StateBadge } from "@/components/badges";
import { Badge, Empty, PageHeader, Panel, button, input, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import { fmtTime } from "@/lib/format";

type V = { id: number; article_id: number; version_no: number; model: string | null; agent_key: string | null; prompt_ref: string | null;
  headline: string; body: string; guard_status: string; length: number | null; created_at: string; is_current: boolean;
  article_status: string; deleted: boolean; source: string | null; handle: string; platform: string; event_id: number;
  event_class: string; coin: string; cost_usd: number | null; latency_ms: number | null; thread: string[] | null;
  card_asset_id: number | null };
type D = { versions: V[]; failures: { created_at: string; purpose: string; model: string | null; status: string; error: string | null; article_id: number | null }[];
  waiting: { kind: string; n: number; last_error: string | null }[]; models: { model: string }[] };

export default async function DraftsPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const [t, locale] = await Promise.all([getTranslations("drafts"), getLocale()]);
  const d = await nrGet<D>("drafts/ai", { days: sp.days ?? 7, model: sp.model, guard: sp.guard });
  const waiting = d.waiting.reduce((n, w) => n + Number(w.n), 0);
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <AutoRefresh active={waiting > 0} label={`${t("refreshing")} (${waiting})`} />
      </PageHeader>
      <AiReadiness />
      <form className="mb-4 flex flex-wrap gap-2 text-sm">
        <input name="days" defaultValue={sp.days ?? "7"} className={`${input} w-20`} title={t("days")} />
        <select name="model" defaultValue={sp.model ?? ""} className={`${input} w-56`}>
          <option value="">{t("allModels")}</option>
          {d.models.map((m) => <option key={m.model} value={m.model}>{m.model}</option>)}
        </select>
        <select name="guard" defaultValue={sp.guard ?? ""} className={`${input} w-40`}>
          <option value="">{t("allGuard")}</option><option value="pass">pass</option><option value="warn">warn</option><option value="fail">fail</option>
        </select>
        <button className={button}>OK</button>
      </form>
      <div className="grid gap-5 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <Panel title={`${d.versions.length}`}>
            {d.versions.length === 0 ? <Empty>—</Empty> : (
              <table className="w-full text-sm">
                <thead><tr><th className={th}>#</th><th className={th}>draft</th><th className={th}>model</th><th className={th}>guard</th><th className={th} /></tr></thead>
                <tbody>
                  {d.versions.map((v) => (
                    <tr key={v.id} className={`border-b border-border/60 align-top ${v.deleted ? "opacity-50" : ""}`}>
                      <td className={`${td} text-xs`}>
                        <Link className="text-accent" href={`/desk/${v.article_id}`}>#{v.article_id} v{v.version_no}</Link>
                        <div className="text-muted">{fmtTime(v.created_at, locale)}</div>
                      </td>
                      <td className={`${td} max-w-xl whitespace-normal`}>
                        <div className="text-xs text-muted">
                          <Link href={`/radar/${v.event_id}`} className="text-accent">#{v.event_id}</Link> <ClassLabel cls={v.event_class} /> · {v.coin} ·{" "}
                          {v.platform === "okx_square" ? "OKX 广场" : "X"} @{v.handle}
                          {v.source === "trial" && <> <Badge tone="warn">{t("trial")}</Badge></>}
                          {v.is_current && <> <Badge tone="accent">{t("current")}</Badge></>} <StateBadge state={v.article_status} />
                        </div>
                        {v.headline && <div className="font-medium">{v.headline}</div>}
                        <p className="line-clamp-6 whitespace-pre-wrap">{v.body}</p>
                        {v.thread?.map((x, i) => <p key={i} className="mt-1 whitespace-pre-wrap border-l-2 border-border pl-2 text-xs text-muted">{x}</p>)}
                        {v.card_asset_id && (
                          <a href={`/api/bff/assets/${v.card_asset_id}/file`} target="_blank" rel="noreferrer">
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img src={`/api/bff/assets/${v.card_asset_id}/file`} alt="card" className="mt-2 w-80 rounded border border-border" />
                          </a>
                        )}
                      </td>
                      <td className={`${td} text-xs`}>{v.model}<div className="text-muted">{v.agent_key}</div>
                        <div className="text-muted">${Number(v.cost_usd ?? 0).toFixed(4)} · {v.latency_ms ?? "—"}ms</div></td>
                      <td className={td}><Badge tone={v.guard_status === "pass" ? "up" : v.guard_status === "warn" ? "warn" : "down"}>{v.guard_status}</Badge>
                        <div className="text-xs text-muted">{v.length ?? "—"}</div></td>
                      <td className={td}><Link className="text-xs text-accent" href={`/desk/${v.article_id}`}>{t("open")}</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
        </div>
        <div className="space-y-5">
          <Panel title={t("waiting")}>
            {d.waiting.length === 0 ? <Empty>—</Empty> : d.waiting.map((w) => (
              <div key={w.kind} className="text-sm">{w.kind}: <b>{w.n}</b>{w.last_error && <div className="text-xs text-warn">{w.last_error}</div>}</div>
            ))}
          </Panel>
          <Panel title={t("failures")}>
            {d.failures.length === 0 ? <Empty>—</Empty> : d.failures.map((f, i) => (
              <div key={i} className="mb-2 border-b border-border/60 pb-2 text-xs">
                <span className="text-muted">{fmtTime(f.created_at, locale)}</span> {f.purpose} · {f.model} · <Badge tone="down">{f.status}</Badge>
                {f.article_id && <> <Link className="text-accent" href={`/desk/${f.article_id}`}>#{f.article_id}</Link></>}
                <div className="text-down">{f.error}</div>
              </div>
            ))}
          </Panel>
        </div>
      </div>
    </>
  );
}
