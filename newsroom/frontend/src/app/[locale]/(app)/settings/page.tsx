import { getLocale, getTranslations } from "next-intl/server";
import { Action } from "@/components/Action";
import { AiReadiness } from "@/components/AiReadiness";
import { ExampleCandidates, PromptProposals } from "@/components/Learning";
import { FieldForm } from "@/components/FieldForm";
import { ModelPresetForm } from "@/components/ModelPresetForm";
import { Badge, Empty, KV, PageHeader, Panel, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet, nrGetOptional } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { auditName, named, styleLabel, word } from "@/lib/glossary";

const TABS = ["sources", "ai", "agents", "prompts", "styles", "users", "costs", "rules", "jobs", "rpa", "audit"] as const;
type Tab = (typeof TABS)[number];

type Model = { id: number; name: string; protocol: string; base_url: string; model: string; auth_header: string; anthropic_version: string; json_mode: string;
  timeout_s: number; max_retries: number; max_concurrency: number; input_usd_per_mtok: number; output_usd_per_mtok: number; enabled: boolean; extra_body: Record<string, unknown>;
  last_test_at: string | null; last_test_ok: boolean | null; last_test_detail: Record<string, unknown> | null; has_key: boolean;
  diagnosis: string | null };
type Agent = { id: number; agent_key: string; version: number; display_name: string; role_description: string; model_config_id: number | null;
  fallback_model_config_id: number | null; prompt_key: string; prompt_version: number; max_output_tokens: number; budget_usd_per_task: number;
  active: boolean; model_name: string | null; fallback_name: string | null; fallback_test_ok: boolean | null; model_test_ok: boolean | null };
type Prompt = { id: number; key: string; version: number; language: "zh" | "en" | null; system_text: string; user_template: string;
  notes: string | null; created_at: string; calls: number; active_for: string[] | null; deletable: boolean };
const REPORTERS = ["reporter", "reporter_breaking", "reporter_investigator", "reporter_data", "reporter_derivatives", "reporter_historian"];
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
      {tab === "rpa" && <Rpa locale={locale} />}
      {tab === "audit" && <Audit locale={locale} />}
    </>
  );
}

async function NoAccess() {
  const tc = await getTranslations("common");
  return <Empty>{tc("noAccess")}</Empty>;
}

async function Sources({ locale }: { locale: string }) {
  const [t, ts, tr] = await Promise.all([getTranslations("settings"), getTranslations("sourcesX"), getTranslations("sourcesX.words")]);
  const d = await nrGet<{ overview: { reachable: boolean; radar: Record<string, string>; cursor: number | null; head_id: number | null; backlog: number | null; last_sync_ok_at: string | null; health_error: string | null };
    connections: { name: string; base_url: string; last_ok_at: string | null; last_error: string | null; last_error_at: string | null }[];
    runs: { id: number; mode: string; status: string; started_at: string; fetched: number; inserted: number; backlog: number | null; error: string | null }[];
    health: { at: string; reachable: boolean; status: { radar: Record<string, string>; error?: string } }[] }>("sources");
  const o = d.overview;
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <Panel title={t("sources")}>
        <KV k={ts("reachable")} v={<Badge tone={o.reachable ? "up" : "down"}>{o.reachable ? ts("yes") : ts("no")}</Badge>} />
        <KV k={ts("radar")} v={`Hyperliquid ${word(tr, o.radar.hyperliquid)} · OKX ${word(tr, o.radar.okx)}`} />
        <KV k={t("cursor")} v={o.cursor ?? "—"} />
        <KV k={t("head")} v={o.head_id ?? "—"} />
        <KV k={ts("backlog")} v={o.backlog ?? "—"} />
        <KV k={ts("lastSync")} v={fmtTime(o.last_sync_ok_at, locale)} />
        {o.health_error && <p className="mt-2 text-xs text-down">{o.health_error}</p>}
        {d.connections.map((c) => <p key={c.name} className="mt-2 text-xs text-muted">{c.name}: {c.base_url} · {ts("lastOk")} {fmtTime(c.last_ok_at, locale)} {c.last_error && `· ${c.last_error}`}</p>)}
      </Panel>
      <Panel title={t("runs")}>
        <table className="w-full text-xs">
          <tbody>
            {d.runs.map((r) => (
              <tr key={r.id} className="border-b border-border/60">
                <td className={td}>{fmtTime(r.started_at, locale)}</td><td className={td}>{word(tr, r.mode)}</td>
                <td className={td}><Badge tone={r.status === "success" ? "up" : "down"}>{word(tr, r.status)}</Badge></td>
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
  const [t, tm, tpr, td] = await Promise.all([getTranslations("settings"), getTranslations("modelForm"), getTranslations("protocols"),
    getTranslations("diagnosis")]);
  const models = await nrGetOptional<Model[]>("ai/models");
  if (!models) return <NoAccess />;
  const f = (name: string) => ({ name, label: tm(`${name}.name`), help: tm(`${name}.help`) });
  const opts = (vals: string[]) => vals.map((v) => ({ value: v, label: tpr.has(v) ? tpr(v) : v }));
  const fields = (m?: Model) => [
    { ...f("name"), value: m?.name, required: true },
    { ...f("protocol"), type: "select" as const, options: opts(["openai_chat", "openai_responses", "anthropic_messages"]), value: m?.protocol ?? "openai_chat" },
    { ...f("base_url"), value: m?.base_url, required: true, placeholder: "https://api.deepseek.com · https://api.aicodemirror.ai/v1", wide: true },
    { ...f("model"), value: m?.model, required: true },
    { ...f("api_key"), type: "password" as const },
    { ...f("auth_header"), type: "select" as const, options: ["bearer", "x-api-key"], value: m?.auth_header ?? "bearer" },
    { ...f("anthropic_version"), value: m?.anthropic_version ?? "2023-06-01" },
    { ...f("json_mode"), type: "select" as const, options: opts(["json_object", "json_schema", "none"]), value: m?.json_mode ?? "json_object" },
    { ...f("timeout_s"), type: "number" as const, value: m?.timeout_s ?? 90 },
    { ...f("max_retries"), type: "number" as const, value: m?.max_retries ?? 2 },
    { ...f("max_concurrency"), type: "number" as const, value: m?.max_concurrency ?? 2 },
    { ...f("input_usd_per_mtok"), type: "number" as const, value: m?.input_usd_per_mtok ?? 0 },
    { ...f("output_usd_per_mtok"), type: "number" as const, value: m?.output_usd_per_mtok ?? 0 },
    { ...f("extra_body"), type: "json" as const, value: m?.extra_body ?? {} },
    { ...f("enabled"), type: "checkbox" as const, value: m?.enabled ?? true },
  ];
  const tested = (ok: boolean | null) => (ok ? tm("lastTestOk") : ok === false ? tm("lastTestFail") : tm("lastTestNone"));
  return (
    <div className="space-y-5">
      <AiReadiness />
      {models.map((m) => (
        <Panel key={m.id} title={<span>{m.name} · {m.model} <Badge tone={m.last_test_ok ? "up" : m.last_test_ok === false ? "down" : "neutral"}>{t("lastTest")}: {tested(m.last_test_ok)}</Badge> {!m.has_key && <Badge tone="warn">{tm("noKey")}</Badge>}</span>}
          hint={`${tpr.has(m.protocol) ? tpr(m.protocol) : m.protocol} · ${m.base_url} · ${tpr.has(m.json_mode) ? tpr(m.json_mode) : m.json_mode}`}>
          <FieldForm fields={[]} path={`ai/models/${m.id}/test`} submitLabel={t("testModel")} showResult />
          {m.diagnosis && td.has(`${m.diagnosis}.what`) && (
            <div className="mt-2 rounded border border-down/40 bg-down/10 px-3 py-2 text-sm">
              <b>{td(`${m.diagnosis}.what`)}</b> — {td(`${m.diagnosis}.fix`)}
            </div>
          )}
          {m.last_test_detail && (
            <details className="mt-2"><summary className="cursor-pointer text-xs text-muted">{tm("testDetail")}</summary>
              <pre className="mt-1 max-h-40 overflow-auto rounded bg-panel-2 p-2 text-xs">{JSON.stringify(m.last_test_detail, null, 2)}</pre></details>
          )}
          <details className="mt-3"><summary className="cursor-pointer text-sm text-accent">{tm("edit")}</summary>
            <div className="mt-3"><FieldForm fields={fields(m)} path={`ai/models/${m.id}`} method="PUT" /></div></details>
        </Panel>
      ))}
      <Panel title={t("addModel")}><ModelPresetForm fields={fields()} /></Panel>
    </div>
  );
}

async function Agents() {
  const t = await getTranslations("settings");
  const [tx, tp, tr, agents, models, prompts] = await Promise.all([getTranslations("settingsX"), getTranslations("promptsX"),
    getTranslations("roles"), nrGetOptional<Agent[]>("ai/agents"), nrGetOptional<Model[]>("ai/models"), nrGetOptional<Prompt[]>("ai/prompts")]);
  if (!agents || !models || !prompts) return <NoAccess />;
  const modelOpts = [{ value: "", label: "—" }, ...models.map((m) => ({ value: String(m.id), label: `${m.name} · ${m.model}` }))];
  const usable = models.filter((m) => m.enabled);
  const best = usable.find((m) => m.last_test_ok) ?? usable[0];
  // one entry per version; a version can carry a Chinese and an English text
  const versionsOf = (key: string) => [...new Set(prompts.filter((p) => p.key === key).map((p) => p.version))];
  const describe = (key: string, v: number) => {
    const rows = prompts.filter((p) => p.key === key && p.version === v);
    const langs = rows.map((p) => (p.language ? tp(`lang.${p.language}`) : tp("lang.shared"))).join(" + ");
    const note = rows.find((p) => p.notes)?.notes;
    return `v${v} · ${langs}${note ? ` · ${note === "seed" ? tp("seed") : note.slice(0, 30)}` : ""}`;
  };
  const active = agents.filter((a) => a.active);
  const allVersions = [...new Set(prompts.filter((p) => REPORTERS.includes(p.key)).map((p) => p.version))].sort((x, y) => y - x);
  return (
    <div className="space-y-5">
      <AiReadiness />
      {usable.length > 0 && (
        <Panel title={tx("bindTitle")} hint={tx("bindHint")}>
          <FieldForm path="ai/agents/bind" submitLabel={tx("bind")} fields={[
            { name: "model_config_id", label: t("primaryModel"), type: "select", numeric: true, required: true,
              value: best?.id ?? "", options: modelOpts.slice(1) },
            { name: "fallback_model_config_id", label: t("fallbackModel"), type: "select", numeric: true, value: "", options: modelOpts },
            { name: "only_unbound", label: tx("onlyUnbound"), type: "checkbox", value: true },
          ]} />
        </Panel>
      )}
      <Panel title={tp("switchTitle")} hint={tp("switchHint")}>
        <div className="mb-3 grid gap-1 text-xs text-muted sm:grid-cols-2">
          {active.map((a) => (
            <div key={a.agent_key}>{named(tr, a.agent_key).name}：<b className="text-text">v{a.prompt_version}</b>
              <span className="opacity-60">（{tp("available")} {versionsOf(a.prompt_key).map((v) => `v${v}`).join(" / ")}）</span></div>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          <Action path="ai/agents/prompt-version" body={{ agent_keys: active.map((a) => a.agent_key) }} tone="primary"
            confirm={tp("switchLatestConfirm")}>{tp("switchLatest")}</Action>
          {allVersions.map((v) => (
            <Action key={v} path="ai/agents/prompt-version" body={{ prompt_version: v, agent_keys: REPORTERS }}
              confirm={tp("switchConfirm", { v })}>{tp("switchReporters", { v })}</Action>
          ))}
        </div>
      </Panel>
      {active.map((a) => {
        const role = named(tr, a.agent_key);
        return (
          <Panel key={a.id} title={<>{role.name} <span className="text-xs font-normal text-muted">{a.agent_key} · {tp("roleVersion", { v: a.version })}</span></>}
            hint={role.desc ?? a.role_description}>
            <KV k={t("primaryModel")} v={<>{a.model_name ?? "—"} {a.model_test_ok === false && <Badge tone="down">{tp("testFailed")}</Badge>}</>} />
            <KV k={t("fallbackModel")} v={<>{a.fallback_name ?? "—"} {a.fallback_test_ok === false
              && <Badge tone="down" title={tp("fallbackSkippedHint")}>{tp("fallbackSkipped")}</Badge>}</>} />
            <KV k={t("promptVersion")} v={<>{describe(a.prompt_key, a.prompt_version)}{" "}
              <Link className="text-xs text-accent" href={`/settings?tab=prompts#${a.prompt_key}`}>{tp("manage")}</Link></>} />
            <details className="mt-3"><summary className="cursor-pointer text-sm text-accent">{tp("editRole")}</summary>
              <div className="mt-3">
                <FieldForm path={`ai/agents/${a.agent_key}`} method="PUT" fields={[
                  { name: "model_config_id", label: `${t("primaryModel")}${a.agent_key === "reporter" || a.agent_key === "eic" ? "" : ` · ${tx("inherit")}`}`,
                    type: "select", numeric: true, value: a.model_config_id ?? "", options: modelOpts },
                  { name: "fallback_model_config_id", label: t("fallbackModel"), type: "select", numeric: true,
                    value: a.fallback_model_config_id ?? "", options: modelOpts },
                  { name: "prompt_version", label: t("promptVersion"), type: "select", numeric: true, value: a.prompt_version,
                    options: versionsOf(a.prompt_key).reverse().map((v) => ({ value: String(v), label: describe(a.prompt_key, v) })) },
                  { name: "max_output_tokens", label: tp("maxTokens"), type: "number", value: a.max_output_tokens },
                  { name: "budget_usd_per_task", label: tp("budgetTask"), type: "number", value: a.budget_usd_per_task },
                ]} />
              </div>
            </details>
          </Panel>
        );
      })}
    </div>
  );
}
async function Prompts({ locale }: { locale: string }) {
  const [t, tp, tr] = await Promise.all([getTranslations("settings"), getTranslations("promptsX"), getTranslations("roles")]);
  const prompts = await nrGetOptional<Prompt[]>("ai/prompts");
  if (!prompts) return <NoAccess />;
  const keys = [...new Set(prompts.map((p) => p.key))];
  const langName = (l: string | null) => tp(`lang.${l ?? "shared"}`);
  return (
    <div className="space-y-5">
      <PromptProposals />
      <p className="text-sm text-muted">{tp("intro")}</p>
      {keys.map((key) => {
        const rows = prompts.filter((p) => p.key === key);
        const versions = [...new Set(rows.map((p) => p.version))];
        const role = named(tr, key);
        const latest = rows[0];
        // a version with one language's text and no role on it yet can take the other language's text
        const other = latest?.language === "zh" ? "en" : latest?.language === "en" ? "zh" : null;
        const canAdd = Boolean(other && !latest.active_for?.length && latest.calls === 0
          && !rows.some((p) => p.version === latest.version && p.language === other));
        return (
          <div key={key} id={key}>
            <Panel title={<>{role.name} <span className="text-xs font-normal text-muted">{key}</span></>}
              hint={<>{role.desc} · {tp("versions", { n: versions.length })}</>}>
              <table className="w-full text-sm">
                <thead><tr><th className={th}>{tp("version")}</th><th className={th}>{tp("language")}</th><th className={th}>{tp("created")}</th>
                  <th className={th}>{tp("notes")}</th><th className={th}>{tp("usedBy")}</th><th className={th}>{tp("calls")}</th><th className={th} /></tr></thead>
                <tbody>
                  {rows.map((p) => (
                    <tr key={`${p.version}-${p.language}`} className="border-b border-border/60 align-top">
                      <td className={td}><b>v{p.version}</b></td>
                      <td className={td}>{langName(p.language)}</td>
                      <td className={`${td} text-xs`}>{fmtTime(p.created_at, locale)}</td>
                      <td className={`${td} max-w-xs whitespace-normal text-xs`}>{p.notes === "seed" ? tp("seed") : p.notes ?? ""}</td>
                      <td className={td}>{p.active_for?.length ? <Badge tone="accent">{tp("current")}</Badge> : null}</td>
                      <td className={td}>{p.calls}</td>
                      <td className={`${td} space-x-2 text-xs`}>
                        {!p.active_for?.length && (
                          <Action path="ai/agents/prompt-version" body={{ prompt_version: p.version, agent_keys: [key] }}>{tp("use")}</Action>
                        )}
                        {p.deletable && (
                          <Action path={`ai/prompts/${key}/${p.version}${p.language ? `?language=${p.language}` : ""}`} method="DELETE"
                            tone="danger" confirm={tp("deleteConfirm", { v: p.version })}>{tp("delete")}</Action>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-xs text-muted">{tp("deleteRule")}</p>
              {rows.map((p) => (
                <details key={`${p.version}-${p.language}`} className="mt-3">
                  <summary className="cursor-pointer text-sm text-accent">{tp("viewEdit", { v: p.version })} · {langName(p.language)}</summary>
                  <div className="mt-3">
                    <p className="mb-2 text-xs text-muted">{tp("newVersionNote")}</p>
                    <FieldForm path="ai/prompts" submitLabel={t("newVersion")} successMessage={tp("saved")} fields={[
                      { name: "key", label: tp("role"), type: "select", value: key, options: [{ value: key, label: role.name }] },
                      { name: "language", label: tp("language"), type: "select", value: p.language ?? "",
                        options: [{ value: "zh", label: tp("lang.zh") }, { value: "en", label: tp("lang.en") }, { value: "", label: tp("lang.shared") }] },
                      { name: "notes", label: tp("notes"), placeholder: tp("notesHint"), wide: true },
                      { name: "system_text", label: t("systemText"), type: "textarea", value: p.system_text, required: true },
                      { name: "user_template", label: t("userTemplate"), type: "textarea", value: p.user_template, required: true },
                    ]} />
                  </div>
                </details>
              ))}
              {canAdd && other && (
                <details className="mt-3">
                  <summary className="cursor-pointer text-sm text-accent">
                    {tp("addLanguage", { lang: langName(other) })} (v{latest.version})</summary>
                  <div className="mt-3">
                    <FieldForm path="ai/prompts" submitLabel={t("save")} successMessage={tp("saved")} fields={[
                      { name: "key", label: tp("role"), type: "select", value: key, options: [{ value: key, label: role.name }] },
                      { name: "version", label: tp("version"), type: "select", numeric: true, value: latest.version,
                        options: [{ value: String(latest.version), label: `v${latest.version}` }] },
                      { name: "language", label: tp("language"), type: "select", value: other, options: [{ value: other, label: langName(other) }] },
                      { name: "system_text", label: t("systemText"), type: "textarea", value: "", required: true },
                      { name: "user_template", label: t("userTemplate"), type: "textarea", value: "", required: true },
                    ]} />
                  </div>
                </details>
              )}
            </Panel>
          </div>
        );
      })}
    </div>
  );
}

async function Users({ locale }: { locale: string }) {
  const [t, tu, ts] = await Promise.all([getTranslations("settings"), getTranslations("userRoles"), getTranslations("usersX")]);
  const users = await nrGetOptional<User[]>("users");
  if (!users) return <NoAccess />;
  const roleOpts = ["admin", "editor_in_chief", "editor", "reporter", "reviewer", "account_operator", "analyst"]
    .map((r) => ({ value: r, label: tu(r) }));
  return (
    <div className="space-y-5">
      <Panel hint={ts("hint")}>
        <table className="w-full text-sm">
          <thead><tr><th className={th}>#</th><th className={th}>{ts("user")}</th><th className={th}>{t("roles")}</th><th className={th}>{t("active")}</th>
            <th className={th}>{ts("lastLogin")}</th><th className={th} /></tr></thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-b border-border/60 align-top">
                <td className={td}>{u.id}</td><td className={td}>{u.username}{u.display_name && <div className="text-xs text-muted">{u.display_name}</div>}</td>
                <td className={td}>{u.roles.map((r) => (tu.has(r) ? tu(r) : r)).join("、")}</td>
                <td className={td}><Badge tone={u.is_active ? "up" : "neutral"}>{u.is_active ? ts("yes") : ts("no")}</Badge></td>
                <td className={`${td} text-xs text-muted`}>{fmtTime(u.last_login_at, locale)}</td>
                <td className={td}>
                  <details><summary className="cursor-pointer text-xs text-accent">{ts("edit")}</summary>
                    <div className="mt-2 w-96"><FieldForm path={`users/${u.id}`} method="PATCH" fields={[
                      { name: "roles", label: t("roles"), type: "multi", value: u.roles, options: roleOpts },
                      { name: "is_active", label: t("active"), type: "checkbox", value: u.is_active },
                      { name: "reset_password", label: t("resetPassword"), type: "password", help: ts("resetHelp") },
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
          { name: "username", label: ts("username"), required: true },
          { name: "display_name", label: ts("displayName") },
          { name: "password", label: ts("password"), type: "password", required: true, help: ts("passwordHelp") },
          { name: "roles", label: t("roles"), type: "multi", required: true, options: roleOpts, wide: true },
        ]} />
      </Panel>
    </div>
  );
}

async function Costs({ locale }: { locale: string }) {
  const [t, tr, tp, tcs] = await Promise.all([getTranslations("settings"), getTranslations("roles"), getTranslations("callPurposes"),
    getTranslations("callStatus")]);
  const d = await nrGetOptional<{ by_day: { day: string; agent_key: string; calls: number; cost: number }[]; by_status: { status: string; n: number }[];
    recent: { id: number; created_at: string; agent_key: string; purpose: string; model: string; status: string; cost_usd: number; latency_ms: number | null; error: string | null }[] }>("ai/costs");
  if (!d) return <NoAccess />;
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <Panel title={t("costs")}>
        <table className="w-full text-sm"><tbody>
          {d.by_day.map((r, i) => <tr key={i} className="border-b border-border/60"><td className={td}>{fmtTime(r.day, locale)}</td><td className={td}>{named(tr, r.agent_key).name}</td><td className={`${td} num`}>{r.calls}</td><td className={`${td} num`}>${Number(r.cost).toFixed(4)}</td></tr>)}
        </tbody></table>
        <div className="mt-3 flex flex-wrap gap-1">{d.by_status.map((s) => <Badge key={s.status} tone={s.status === "ok" ? "up" : "warn"}>{word(tcs, s.status)}: {s.n}</Badge>)}</div>
      </Panel>
      <Panel title={t("calls")}>
        <table className="w-full text-xs"><tbody>
          {d.recent.map((r) => <tr key={r.id} className="border-b border-border/60"><td className={td}>{fmtTime(r.created_at, locale)}</td><td className={td}>{named(tr, r.agent_key).name} · {word(tp, r.purpose)}</td><td className={td}>{r.model}</td><td className={td}><Badge tone={r.status === "ok" ? "up" : "down"}>{word(tcs, r.status)}</Badge></td><td className={`${td} num`}>${Number(r.cost_usd).toFixed(4)}</td><td className={`${td} max-w-xs truncate text-muted`}>{r.error}</td></tr>)}
        </tbody></table>
      </Panel>
    </div>
  );
}

const HOUSE_STYLES = ["whale_alert", "degen", "dossier", "crowd", "outcome", "one_liner", "portfolio"];

async function Rules() {
  const [t, tr, tf, tx] = await Promise.all([getTranslations("settings"), getTranslations("roles"), getTranslations("settingFields"),
    getTranslations("rulesX")]);
  const [s, styles] = await Promise.all([nrGet<Record<string, Record<string, unknown>>>("settings"),
    nrGet<{ key: string; name: string; language: string }[]>("styles")]);
  const field = (section: string, key: string) => (tf.has(`${section}__${key}.name`)
    ? { label: tf(`${section}__${key}.name`), help: tf(`${section}__${key}.desc`) } : { label: key });
  const zhName = (k: string) => `${styles.find((x) => x.key === `zh_${k}`)?.name ?? k}（中文账号用中文版，英文账号用英文版）`;
  const styleOpts = HOUSE_STYLES.map((k) => ({ value: `{language}_${k}`, label: zhName(k) }));
  const byReporter = (s.styles?.by_reporter ?? {}) as Record<string, string>;
  const reporters = ["reporter_breaking", "reporter_investigator", "reporter_data", "reporter_derivatives", "reporter_historian", "reporter"];
  return (
    <div className="space-y-5">
      <Panel title={tx("aiTitle")} hint={tx("aiHint")}>
        <FieldForm path="settings/ai" method="PUT" fields={[
          { name: "enabled", ...field("ai", "enabled"), type: "checkbox", value: s.ai.enabled },
          { name: "auto_eic", ...field("ai", "auto_eic"), type: "checkbox", value: s.ai.auto_eic },
        ]} />
      </Panel>
      <Panel title={tx("stylesTitle")} hint={tx("stylesHint")}>
        <FieldForm path="settings/styles" method="PUT" fields={reporters.map((r) => ({
          name: `by_reporter.${r}`, label: named(tr, r).name, help: named(tr, r).desc ?? undefined, type: "select" as const,
          value: byReporter[r] ?? "", options: styleOpts }))} />
      </Panel>
      <div className="grid gap-5 xl:grid-cols-2">
        <Panel title={tx("scoresTitle")} hint={tx("scoresHint")}>
          <FieldForm path="settings/detect" method="PUT" fields={[
            { name: "candidate_score", ...field("detect", "candidate_score"), type: "number", value: s.detect.candidate_score },
            { name: "auto_eic_score", ...field("detect", "auto_eic_score"), type: "number", value: s.detect.auto_eic_score },
          ]} />
        </Panel>
        <Panel title={tx("squaresTitle")} hint={tx("squaresHint")}>
          <FieldForm path="settings/squares" method="PUT" fields={(["min_asset_usd", "min_pnl_usd", "big_result_usd", "fall_off_peak",
            "board_top"] as const).map((k) => ({ name: k, ...field("squares", k), type: "number" as const, value: s.squares?.[k] }))} />
          <div className="mt-4">
            <FieldForm path="settings/stars" method="PUT" fields={(["min_notional_usd", "merge_minutes"] as const).map((k) => ({
              name: k, ...field("stars", k), type: "number" as const, value: s.stars?.[k] }))} />
          </div>
        </Panel>
        <Panel title={tx("deskTitle")}>
          <FieldForm path="settings/desk" method="PUT" fields={[
            { name: "language", ...field("desk", "language"), type: "select", value: s.desk?.language ?? "zh",
              options: [{ value: "zh", label: "中文" }, { value: "en", label: "English" }] },
            { name: "auto_accept", ...field("desk", "auto_accept"), type: "checkbox", value: Boolean(s.desk?.auto_accept) },
            { name: "auto_min_priority", ...field("desk", "auto_min_priority"), type: "select", value: String(s.desk?.auto_min_priority ?? "p1"),
              options: ["p0", "p1", "p2"].map((p) => ({ value: p, label: tx(`priority.${p}`) })) },
          ]} />
          <div className="mt-4">
            <FieldForm path="settings/review" method="PUT" fields={[
              { name: "ai_ready", ...field("review", "ai_ready"), type: "checkbox", value: s.review.ai_ready },
              { name: "four_eyes", ...field("review", "four_eyes"), type: "checkbox", value: s.review.four_eyes },
            ]} />
          </div>
          <div className="mt-4">
            <FieldForm path="settings/budgets" method="PUT" fields={[
              { name: "global_daily_usd", ...field("budgets", "global_daily_usd"), type: "number", value: s.budgets.global_daily_usd },
            ]} />
          </div>
        </Panel>
      </div>
      <details>
        <summary className="cursor-pointer text-sm text-accent">{tx("advanced")}</summary>
        <p className="my-3 text-xs text-muted">{t("ruleHint")} {tx("advancedHint")}</p>
        <div className="space-y-5">
          {["detect", "gate", "budgets", "guard", "columns"].map((k) => (
            <Panel key={k} title={tx.has(`section.${k}`) ? tx(`section.${k}`) : k}>
              <SettingsJson keyName={k} value={s[k]} labels={(key) => field(k, key)} />
            </Panel>
          ))}
        </div>
      </details>
    </div>
  );
}

function SettingsJson({ keyName, value, labels }: { keyName: string; value: Record<string, unknown>;
  labels: (key: string) => { label: string; help?: string } }) {
  return <FieldForm path={`settings/${keyName}`} method="PUT" fields={Object.entries(value).map(([k, v]) => (
    typeof v === "boolean" ? { name: k, ...labels(k), type: "checkbox" as const, value: v }
      : typeof v === "number" ? { name: k, ...labels(k), type: "number" as const, value: v }
        : Array.isArray(v) && v.every((x) => typeof x === "number") ? { name: k, ...labels(k), type: "numbers" as const, value: v }
          : Array.isArray(v) ? { name: k, ...labels(k), type: "list" as const, value: v }
            : { name: k, ...labels(k), type: "json" as const, value: v }))} />;
}

async function Jobs({ locale }: { locale: string }) {
  const [t, tj, tjs] = await Promise.all([getTranslations("settings"), getTranslations("jobKinds"), getTranslations("jobStatus")]);
  const jobs = await nrGetOptional<{ id: number; kind: string; payload: Record<string, unknown>; status: string; attempts: number; max_attempts: number; last_error: string | null; created_at: string; run_after: string }[]>("jobs");
  if (!jobs) return <NoAccess />;
  return (
    <Panel title={t("jobs")}>
      <table className="w-full text-xs"><tbody>
        {jobs.map((j) => (
          <tr key={j.id} className="border-b border-border/60">
            <td className={td}>#{j.id}</td><td className={td}>{word(tj, j.kind)}</td>
            <td className={`${td} max-w-xs truncate text-muted`} title={JSON.stringify(j.payload)}>{JSON.stringify(j.payload)}</td>
            <td className={td}><Badge tone={j.status === "done" ? "up" : j.status === "dead" ? "down" : "neutral"}>{word(tjs, j.status)}</Badge> {j.attempts}/{j.max_attempts}</td>
            <td className={td}>{fmtTime(j.created_at, locale)}</td><td className={`${td} max-w-xs truncate text-down`}>{j.last_error}</td>
            <td className={td}>{j.status === "dead" && <Action path={`jobs/${j.id}/retry`}>{t("retry")}</Action>}</td>
          </tr>
        ))}
      </tbody></table>
    </Panel>
  );
}

async function Audit({ locale }: { locale: string }) {
  const [tg, ta, ts] = await Promise.all([getTranslations("auditGroups"), getTranslations("auditActions"), getTranslations("usersX")]);
  const rows = await nrGetOptional<{ id: number; at: string; actor_kind: string; username: string | null; action: string; object_type: string; object_id: string | null; reason: string | null }[]>("audit");
  if (!rows) return <NoAccess />;
  return (
    <Panel>
      <table className="w-full text-xs"><tbody>
        {rows.map((r) => (
          <tr key={r.id} className="border-b border-border/60">
            <td className={td}>{fmtTime(r.at, locale)}</td>
            <td className={td}>{r.username ?? (ts.has(`actor.${r.actor_kind}`) ? ts(`actor.${r.actor_kind}`) : r.actor_kind)}</td>
            <td className={td} title={r.action}>{auditName(tg, ta, r.action)}</td>
            <td className={`${td} text-muted`}>{r.object_type} {r.object_id}</td><td className={`${td} text-muted`}>{r.reason}</td>
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
      <ExampleCandidates />
      <Panel title={tx("newStyle")}>
        <FieldForm path="styles" fields={[
          { name: "key", label: tx("styleKey"), required: true, help: tx("styleKeyHelp"), placeholder: "zh_my_style" },
          { name: "language", label: tx("styleLang"), type: "select", options: [{ value: "zh", label: "中文" }, { value: "en", label: "English" }], value: "zh" },
          { name: "name", label: tx("styleName"), required: true },
          { name: "guide", label: tx("guide"), type: "textarea", required: true },
          { name: "forbidden", label: tx("forbidden"), type: "list" },
          { name: "structure", label: tx("styleStructure"), type: "json", value: {}, help: tx("styleStructureHelp") },
        ]} />
      </Panel>
      <div className="grid gap-3 md:grid-cols-2">
        {[...latest.values()].map((st) => (
          <Panel key={st.key} title={<>{styleLabel(styles, st.key)} <span className="text-xs font-normal text-muted">{st.key} · {tx("styleVersion", { v: st.version })}</span></>} hint={st.guide}>
            <p className="text-xs text-muted">{st.forbidden.join(" · ")}</p>
          </Panel>
        ))}
      </div>
    </div>
  );
}

async function Rpa({ locale }: { locale: string }) {
  const [tx, tk] = await Promise.all([getTranslations("settingsX"), getTranslations("tokens")]);
  const [tokens, accounts] = await Promise.all([
    nrGetOptional<{ id: number; name: string; channel: string; account_ids: number[]; active: boolean; created_at: string;
      last_used_at: string | null; publications: number }[]>("api-tokens"),
    nrGet<{ id: number; handle: string }[]>("accounts")]);
  if (!tokens) return <NoAccess />;
  const handle = (id: number) => accounts.find((a) => a.id === id)?.handle ?? id;
  return (
    <div className="space-y-5">
      <Panel title={tk("title")} hint={tk("hint")}>
        <table className="w-full text-sm">
          <thead><tr><th className={th}>{tk("name")}</th><th className={th}>{tk("channel")}</th><th className={th}>{tk("accounts")}</th>
            <th className={th}>{tk("lastUsed")}</th><th className={th}>{tk("published")}</th><th className={th} /></tr></thead>
          <tbody>
            {tokens.map((k) => (
              <tr key={k.id} className={`border-b border-border/60 ${k.active ? "" : "opacity-50"}`}>
                <td className={td}>{k.name}</td><td className={td}>{tk(`ch.${k.channel}`)}</td>
                <td className={td}>{k.account_ids.length ? k.account_ids.map((i) => `@${handle(i)}`).join(" ") : tk("allAccounts")}</td>
                <td className={`${td} text-xs`}>{fmtTime(k.last_used_at, locale)}</td><td className={td}>{k.publications}</td>
                <td className={td}>{k.active ? <Action path={`api-tokens/${k.id}/disable`} tone="danger" confirm={tk("disableConfirm")}>{tk("disable")}</Action>
                  : <Badge>{tk("disabled")}</Badge>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <Panel title={tk("create")} hint={tk("createHint")}>
        <FieldForm path="api-tokens" submitLabel={tk("createBtn")} showResult fields={[
          { name: "name", label: tk("name"), required: true, placeholder: tk("namePlaceholder") },
          { name: "channel", label: tk("channel"), type: "select", value: "extension",
            options: ["extension", "rpa", "api"].map((c) => ({ value: c, label: tk(`ch.${c}`) })) },
          { name: "account_ids", label: tk("accounts"), type: "multi", options: accounts.map((a) => ({ value: String(a.id), label: `@${a.handle}` })),
            help: tk("accountsHelp"), wide: true },
        ]} />
      </Panel>
      <Panel title={tk("howTitle")}>
        <pre className="overflow-auto rounded bg-panel-2 p-3 text-xs leading-relaxed">{`GET  /api/feed/ready?language=zh                     ${tk("api.ready")}
POST /api/feed/<article_id>/claim                    ${tk("api.claim")}
GET  /api/feed/assets/<asset_id>                     ${tk("api.asset")}
POST /api/feed/<article_id>/result  {"ok": true, "x_url": "https://x.com/...", "reply_url": "..."}
POST /api/feed/<article_id>/result  {"ok": false, "error": "..."}    ${tk("api.fail")}
POST /api/feed/publications/<id>/metrics  {"impressions": 1200, "likes": 31}
Authorization: Bearer <token>`}</pre>
        <p className="mt-2 text-xs text-muted">{tk("docs")}</p>
      </Panel>
      <details><summary className="cursor-pointer text-sm text-muted">{tx("rpa")}（{tk("legacy")}）</summary>
        <div className="mt-3"><FieldForm fields={[]} path="settings/rpa-token" submitLabel={tx("newToken")} showResult /></div>
      </details>
    </div>
  );
}
