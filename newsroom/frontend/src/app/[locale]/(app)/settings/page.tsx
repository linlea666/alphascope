import { getLocale, getTranslations } from "next-intl/server";
import { Action } from "@/components/Action";
import { FieldForm } from "@/components/FieldForm";
import { ModelPresetForm } from "@/components/ModelPresetForm";
import { Badge, Empty, KV, PageHeader, Panel, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet, nrGetOptional } from "@/lib/api";
import { fmtTime } from "@/lib/format";

const TABS = ["sources", "ai", "agents", "prompts", "styles", "users", "costs", "rules", "jobs", "rpa", "audit"] as const;
type Tab = (typeof TABS)[number];

type Model = { id: number; name: string; protocol: string; base_url: string; model: string; auth_header: string; anthropic_version: string; json_mode: string;
  timeout_s: number; max_retries: number; max_concurrency: number; input_usd_per_mtok: number; output_usd_per_mtok: number; enabled: boolean; extra_body: Record<string, unknown>;
  last_test_at: string | null; last_test_ok: boolean | null; last_test_detail: Record<string, unknown> | null; has_key: boolean };
type Agent = { id: number; agent_key: string; version: number; display_name: string; role_description: string; model_config_id: number | null;
  fallback_model_config_id: number | null; prompt_key: string; prompt_version: number; max_output_tokens: number; budget_usd_per_task: number;
  active: boolean; model_name: string | null; fallback_name: string | null };
type Prompt = { id: number; key: string; version: number; system_text: string; user_template: string; notes: string | null; created_at: string };
type User = { id: number; username: string; display_name: string | null; is_active: boolean; roles: string[]; last_login_at: string | null; locked_until: string | null };

export default async function SettingsPage({ searchParams }: { searchParams: Promise<{ tab?: string }> }) {
  const tab: Tab = (TABS as readonly string[]).includes((await searchParams).tab ?? "") ? ((await searchParams).tab as Tab) : "sources";
  const [t, tx, locale] = await Promise.all([getTranslations("settings"), getTranslations("settingsX"), getLocale()]);
  const label = (k: Tab) => (k === "styles" || k === "rpa" ? tx(k) : t(k));
  return (
    <>
      <PageHeader title={t("title")} />
      <div className="mb-5 flex flex-wrap gap-1 border-b border-border text-sm">
        {TABS.map((k) => (
          <Link key={k} href={`/settings?tab=${k}`} className={`px-3 py-2 ${k === tab ? "border-b-2 border-accent text-text" : "text-muted hover:text-text"}`}>{label(k)}</Link>
        ))}
      </div>
      {tab === "sources" && <Sources locale={locale} />}
      {tab === "ai" && <Models />}
      {tab === "agents" && <Agents />}
      {tab === "prompts" && <Prompts locale={locale} />}
      {tab === "users" && <Users locale={locale} />}
      {tab === "costs" && <Costs locale={locale} />}
      {tab === "rules" && <Rules />}
      {tab === "jobs" && <Jobs locale={locale} />}
      {tab === "styles" && <Styles />}
      {tab === "rpa" && <Rpa />}
      {tab === "audit" && <Audit locale={locale} />}
    </>
  );
}

async function NoAccess() {
  const tc = await getTranslations("common");
  return <Empty>{tc("noAccess")}</Empty>;
}

async function Sources({ locale }: { locale: string }) {
  const t = await getTranslations("settings");
  const d = await nrGet<{ overview: { reachable: boolean; radar: Record<string, string>; cursor: number | null; head_id: number | null; backlog: number | null; last_sync_ok_at: string | null; health_error: string | null };
    connections: { name: string; base_url: string; last_ok_at: string | null; last_error: string | null; last_error_at: string | null }[];
    runs: { id: number; mode: string; status: string; started_at: string; fetched: number; inserted: number; backlog: number | null; error: string | null }[];
    health: { at: string; reachable: boolean; status: { radar: Record<string, string>; error?: string } }[] }>("sources");
  const o = d.overview;
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <Panel title={t("sources")}>
        <KV k="reachable" v={<Badge tone={o.reachable ? "up" : "down"}>{String(o.reachable)}</Badge>} />
        <KV k="radar" v={`HL ${o.radar.hyperliquid} · OKX ${o.radar.okx}`} />
        <KV k={t("cursor")} v={o.cursor ?? "—"} />
        <KV k={t("head")} v={o.head_id ?? "—"} />
        <KV k="backlog" v={o.backlog ?? "—"} />
        <KV k="last sync" v={fmtTime(o.last_sync_ok_at, locale)} />
        {o.health_error && <p className="mt-2 text-xs text-down">{o.health_error}</p>}
        {d.connections.map((c) => <p key={c.name} className="mt-2 text-xs text-muted">{c.name}: {c.base_url} · ok {fmtTime(c.last_ok_at, locale)} {c.last_error && `· ${c.last_error}`}</p>)}
      </Panel>
      <Panel title={t("runs")}>
        <table className="w-full text-xs">
          <tbody>
            {d.runs.map((r) => (
              <tr key={r.id} className="border-b border-border/60">
                <td className={td}>{fmtTime(r.started_at, locale)}</td><td className={td}>{r.mode}</td>
                <td className={td}><Badge tone={r.status === "success" ? "up" : "down"}>{r.status}</Badge></td>
                <td className={`${td} num`}>{r.fetched}/{r.inserted}</td><td className={`${td} num`}>{r.backlog ?? "—"}</td>
                <td className={`${td} max-w-xs truncate text-down`}>{r.error}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}

async function Models() {
  const t = await getTranslations("settings");
  const models = await nrGetOptional<Model[]>("ai/models");
  if (!models) return <NoAccess />;
  const fields = (m?: Model) => [
    { name: "name", label: "name", value: m?.name, required: true },
    { name: "protocol", label: t("protocol"), type: "select" as const, options: ["openai_chat", "openai_responses", "anthropic_messages"], value: m?.protocol ?? "openai_chat" },
    { name: "base_url", label: t("baseUrl"), value: m?.base_url, required: true, placeholder: "https://www.aicodemirror.com/api/... or https://api.example.com/v1", wide: true },
    { name: "model", label: t("model"), value: m?.model, required: true },
    { name: "api_key", label: t("apiKey"), type: "password" as const },
    { name: "auth_header", label: "auth header", type: "select" as const, options: ["bearer", "x-api-key"], value: m?.auth_header ?? "bearer" },
    { name: "anthropic_version", label: "anthropic-version", value: m?.anthropic_version ?? "2023-06-01" },
    { name: "json_mode", label: t("jsonMode"), type: "select" as const, options: ["json_object", "json_schema", "none"], value: m?.json_mode ?? "json_object" },
    { name: "timeout_s", label: "timeout (s)", type: "number" as const, value: m?.timeout_s ?? 90 },
    { name: "max_retries", label: "retries", type: "number" as const, value: m?.max_retries ?? 2 },
    { name: "max_concurrency", label: "concurrency", type: "number" as const, value: m?.max_concurrency ?? 2 },
    { name: "input_usd_per_mtok", label: `${t("price")} · in`, type: "number" as const, value: m?.input_usd_per_mtok ?? 0 },
    { name: "output_usd_per_mtok", label: `${t("price")} · out`, type: "number" as const, value: m?.output_usd_per_mtok ?? 0 },
    { name: "extra_body", label: "extra body (JSON)", type: "json" as const, value: m?.extra_body ?? {} },
    { name: "enabled", label: "enabled", type: "checkbox" as const, value: m?.enabled ?? true },
  ];
  return (
    <div className="space-y-5">
      {models.map((m) => (
        <Panel key={m.id} title={<span>{m.name} · {m.model} <Badge tone={m.last_test_ok ? "up" : m.last_test_ok === false ? "down" : "neutral"}>{t("lastTest")}: {String(m.last_test_ok ?? "—")}</Badge> {!m.has_key && <Badge tone="warn">no key</Badge>}</span>}
          hint={`${m.protocol} · ${m.base_url} · ${m.json_mode}`}>
          <FieldForm fields={[]} path={`ai/models/${m.id}/test`} submitLabel={t("testModel")} showResult />
          {m.last_test_detail && <pre className="mt-2 max-h-40 overflow-auto rounded bg-panel-2 p-2 text-xs">{JSON.stringify(m.last_test_detail, null, 2)}</pre>}
          <details className="mt-3"><summary className="cursor-pointer text-sm text-accent">edit</summary>
            <div className="mt-3"><FieldForm fields={fields(m)} path={`ai/models/${m.id}`} method="PUT" /></div></details>
        </Panel>
      ))}
      <Panel title={t("addModel")}><ModelPresetForm fields={fields()} /></Panel>
    </div>
  );
}

async function Agents() {
  const t = await getTranslations("settings");
  const [tx, agents, models] = await Promise.all([getTranslations("settingsX"), nrGetOptional<Agent[]>("ai/agents"),
    nrGetOptional<Model[]>("ai/models")]);
  if (!agents || !models) return <NoAccess />;
  const modelOpts = [{ value: "", label: "—" }, ...models.map((m) => ({ value: String(m.id), label: `${m.name} · ${m.model}` }))];
  return (
    <div className="space-y-5">
      {agents.filter((a) => a.active).map((a) => (
        <Panel key={a.id} title={`${a.display_name} (${a.agent_key} v${a.version})`} hint={a.role_description}>
          <KV k={t("primaryModel")} v={a.model_name ?? "—"} />
          <KV k={t("fallbackModel")} v={a.fallback_name ?? "—"} />
          <KV k={t("promptVersion")} v={`${a.prompt_key}@v${a.prompt_version}`} />
          <details className="mt-3"><summary className="cursor-pointer text-sm text-accent">{t("newVersion")}</summary>
            <div className="mt-3">
              <FieldForm path={`ai/agents/${a.agent_key}`} method="PUT" fields={[
                { name: "model_config_id", label: `${t("primaryModel")}${a.agent_key === "reporter" || a.agent_key === "eic" ? "" : ` · ${tx("inherit")}`}`,
                  type: "select", numeric: true, value: a.model_config_id ?? "", options: modelOpts },
                { name: "fallback_model_config_id", label: t("fallbackModel"), type: "select", numeric: true,
                  value: a.fallback_model_config_id ?? "", options: modelOpts },
                { name: "prompt_version", label: t("promptVersion"), type: "number", value: a.prompt_version },
                { name: "max_output_tokens", label: "max output tokens", type: "number", value: a.max_output_tokens },
                { name: "budget_usd_per_task", label: "budget / task (USD)", type: "number", value: a.budget_usd_per_task },
              ]} />
            </div>
          </details>
        </Panel>
      ))}
    </div>
  );
}

async function Prompts({ locale }: { locale: string }) {
  const t = await getTranslations("settings");
  const prompts = await nrGetOptional<Prompt[]>("ai/prompts");
  if (!prompts) return <NoAccess />;
  const latest = new Map<string, Prompt>();
  prompts.forEach((p) => { if (!latest.has(p.key)) latest.set(p.key, p); });
  return (
    <div className="space-y-5">
      {[...latest.values()].map((p) => (
        <Panel key={p.key} title={`${p.key} v${p.version}`} hint={`${fmtTime(p.created_at, locale)} · ${p.notes ?? ""}`}>
          <FieldForm path="ai/prompts" submitLabel={t("newVersion")} fields={[
            { name: "key", label: "key", value: p.key, required: true },
            { name: "notes", label: "notes" },
            { name: "system_text", label: t("systemText"), type: "textarea", value: p.system_text, required: true },
            { name: "user_template", label: t("userTemplate"), type: "textarea", value: p.user_template, required: true },
          ]} />
          <p className="mt-2 text-xs text-muted">{prompts.filter((x) => x.key === p.key).map((x) => `v${x.version}`).join(" · ")}</p>
        </Panel>
      ))}
    </div>
  );
}

async function Users({ locale }: { locale: string }) {
  const t = await getTranslations("settings");
  const users = await nrGetOptional<User[]>("users");
  if (!users) return <NoAccess />;
  const roles = "admin, editor_in_chief, editor, reporter, reviewer, account_operator, analyst";
  return (
    <div className="space-y-5">
      <Panel>
        <table className="w-full text-sm">
          <thead><tr><th className={th}>#</th><th className={th}>user</th><th className={th}>{t("roles")}</th><th className={th}>{t("active")}</th><th className={th}>login</th><th className={th} /></tr></thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-b border-border/60 align-top">
                <td className={td}>{u.id}</td><td className={td}>{u.username}</td><td className={td}>{u.roles.join(", ")}</td>
                <td className={td}>{String(u.is_active)}</td><td className={`${td} text-xs text-muted`}>{fmtTime(u.last_login_at, locale)}</td>
                <td className={td}>
                  <details><summary className="cursor-pointer text-xs text-accent">edit</summary>
                    <div className="mt-2 w-96"><FieldForm path={`users/${u.id}`} method="PATCH" fields={[
                      { name: "roles", label: roles, type: "list", value: u.roles },
                      { name: "is_active", label: t("active"), type: "checkbox", value: u.is_active },
                      { name: "reset_password", label: t("resetPassword"), type: "password" },
                    ]} /></div>
                  </details>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <Panel title={t("addUser")}>
        <FieldForm path="users" fields={[
          { name: "username", label: "username", required: true },
          { name: "display_name", label: "display name" },
          { name: "password", label: "password", type: "password", required: true },
          { name: "roles", label: roles, type: "list", required: true },
        ]} />
      </Panel>
    </div>
  );
}

async function Costs({ locale }: { locale: string }) {
  const t = await getTranslations("settings");
  const d = await nrGetOptional<{ by_day: { day: string; agent_key: string; calls: number; cost: number }[]; by_status: { status: string; n: number }[];
    recent: { id: number; created_at: string; agent_key: string; purpose: string; model: string; status: string; cost_usd: number; latency_ms: number | null; error: string | null }[] }>("ai/costs");
  if (!d) return <NoAccess />;
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <Panel title={t("costs")}>
        <table className="w-full text-sm"><tbody>
          {d.by_day.map((r, i) => <tr key={i} className="border-b border-border/60"><td className={td}>{fmtTime(r.day, locale)}</td><td className={td}>{r.agent_key}</td><td className={`${td} num`}>{r.calls}</td><td className={`${td} num`}>${Number(r.cost).toFixed(4)}</td></tr>)}
        </tbody></table>
        <div className="mt-3 flex flex-wrap gap-1">{d.by_status.map((s) => <Badge key={s.status} tone={s.status === "ok" ? "up" : "warn"}>{s.status}: {s.n}</Badge>)}</div>
      </Panel>
      <Panel title={t("calls")}>
        <table className="w-full text-xs"><tbody>
          {d.recent.map((r) => <tr key={r.id} className="border-b border-border/60"><td className={td}>{fmtTime(r.created_at, locale)}</td><td className={td}>{r.purpose}</td><td className={td}>{r.model}</td><td className={td}><Badge tone={r.status === "ok" ? "up" : "down"}>{r.status}</Badge></td><td className={`${td} num`}>${Number(r.cost_usd).toFixed(4)}</td><td className={`${td} max-w-xs truncate text-muted`}>{r.error}</td></tr>)}
        </tbody></table>
      </Panel>
    </div>
  );
}

async function Rules() {
  const t = await getTranslations("settings");
  const s = await nrGet<Record<string, Record<string, unknown>>>("settings");
  return (
    <div className="space-y-5">
      <p className="text-xs text-muted">{t("ruleHint")}</p>
      {["detect", "gate", "budgets", "ai", "guard", "review"].map((k) => (
        <Panel key={k} title={k}>
          <SettingsJson keyName={k} value={s[k]} />
        </Panel>
      ))}
    </div>
  );
}

function SettingsJson({ keyName, value }: { keyName: string; value: Record<string, unknown> }) {
  return <FieldForm path={`settings/${keyName}`} method="PUT" fields={Object.entries(value).map(([k, v]) => (
    typeof v === "boolean" ? { name: k, label: k, type: "checkbox" as const, value: v }
      : typeof v === "number" ? { name: k, label: k, type: "number" as const, value: v }
        : Array.isArray(v) && v.every((x) => typeof x === "number") ? { name: k, label: k, type: "numbers" as const, value: v }
          : Array.isArray(v) ? { name: k, label: k, type: "list" as const, value: v }
            : { name: k, label: k, type: "json" as const, value: v }))} />;
}

async function Jobs({ locale }: { locale: string }) {
  const t = await getTranslations("settings");
  const jobs = await nrGetOptional<{ id: number; kind: string; payload: Record<string, unknown>; status: string; attempts: number; max_attempts: number; last_error: string | null; created_at: string; run_after: string }[]>("jobs");
  if (!jobs) return <NoAccess />;
  return (
    <Panel title={t("jobs")}>
      <table className="w-full text-xs"><tbody>
        {jobs.map((j) => (
          <tr key={j.id} className="border-b border-border/60">
            <td className={td}>#{j.id}</td><td className={td}>{j.kind}</td><td className={td}>{JSON.stringify(j.payload)}</td>
            <td className={td}><Badge tone={j.status === "done" ? "up" : j.status === "dead" ? "down" : "neutral"}>{j.status}</Badge> {j.attempts}/{j.max_attempts}</td>
            <td className={td}>{fmtTime(j.created_at, locale)}</td><td className={`${td} max-w-xs truncate text-down`}>{j.last_error}</td>
            <td className={td}>{j.status === "dead" && <Action path={`jobs/${j.id}/retry`}>{t("retry")}</Action>}</td>
          </tr>
        ))}
      </tbody></table>
    </Panel>
  );
}

async function Audit({ locale }: { locale: string }) {
  const rows = await nrGetOptional<{ id: number; at: string; actor_kind: string; username: string | null; action: string; object_type: string; object_id: string | null; reason: string | null }[]>("audit");
  if (!rows) return <NoAccess />;
  return (
    <Panel>
      <table className="w-full text-xs"><tbody>
        {rows.map((r) => (
          <tr key={r.id} className="border-b border-border/60">
            <td className={td}>{fmtTime(r.at, locale)}</td><td className={td}>{r.actor_kind} {r.username}</td><td className={td}>{r.action}</td>
            <td className={td}>{r.object_type} {r.object_id}</td><td className={`${td} text-muted`}>{r.reason}</td>
          </tr>
        ))}
      </tbody></table>
    </Panel>
  );
}

async function Styles() {
  const tx = await getTranslations("settingsX");
  const styles = await nrGet<{ key: string; version: number; language: string; name: string; guide: string; forbidden: string[]; structure: Record<string, unknown> }[]>("styles");
  const latest = new Map<string, (typeof styles)[number]>();
  styles.forEach((st) => { if (!latest.has(st.key)) latest.set(st.key, st); });
  return (
    <div className="space-y-5">
      <Panel title={tx("newStyle")}>
        <FieldForm path="styles" fields={[
          { name: "key", label: "key (zh_xxx / en_xxx)", required: true },
          { name: "language", label: "language", type: "select", options: ["zh", "en"], value: "zh" },
          { name: "name", label: "name", required: true },
          { name: "guide", label: tx("guide"), type: "textarea", required: true },
          { name: "forbidden", label: tx("forbidden"), type: "list" },
          { name: "structure", label: "structure (JSON)", type: "json", value: {} },
        ]} />
      </Panel>
      <div className="grid gap-3 md:grid-cols-2">
        {[...latest.values()].map((st) => (
          <Panel key={st.key} title={`${st.name} · ${st.key} v${st.version}`} hint={st.guide}>
            <p className="text-xs text-muted">{st.forbidden.join(" · ")}</p>
          </Panel>
        ))}
      </div>
    </div>
  );
}

async function Rpa() {
  const tx = await getTranslations("settingsX");
  return (
    <Panel title={tx("rpa")} hint={tx("rpaHint")}>
      <FieldForm fields={[]} path="settings/rpa-token" submitLabel={tx("newToken")} showResult />
      <pre className="mt-3 overflow-auto rounded bg-panel-2 p-2 text-xs">{`GET  /api/rpa/next                     Authorization: Bearer <token>
POST /api/rpa/plans/<id>/result        {"ok": true, "x_url": "...", "published_at": "...", "final_text": "..."}
POST /api/rpa/plans/<id>/result        {"ok": false, "error": "..."}`}</pre>
    </Panel>
  );
}
