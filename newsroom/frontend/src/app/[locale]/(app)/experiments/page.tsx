import { getLocale, getTranslations } from "next-intl/server";
import { AutoRefresh } from "@/components/AutoRefresh";
import { FieldForm } from "@/components/FieldForm";
import { Badge, Empty, PageHeader, Panel, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import type { Account } from "@/lib/types";

type X = { experiment: string; created_at: string; pairs: number; ready: number; failed: number; my_votes: number; votes: number };
type D = { experiments: X[]; pending: { experiment: string; status: string; last_error: string | null; created_at: string;
  result: { pairs: number; candidates?: number; skipped: Record<string, number> } | null }[] };
const REASONS = ["no_recent_events", "none_usable", "gate_blocked", "refresh_limit"] as const;

export default async function ExperimentsPage() {
  const [t, locale] = await Promise.all([getTranslations("experiments"), getLocale()]);
  const [d, accounts, models] = await Promise.all([nrGet<D>("experiments"), nrGet<Account[]>("accounts"),
    nrGet<{ id: number; name: string; model: string; last_test_ok: boolean | null }[]>("ai/model-options")]);
  const busy = d.pending.some((p) => p.status === "queued" || p.status === "running") || d.experiments.some((x) => x.ready + x.failed < x.pairs);
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <AutoRefresh active={busy} label={t("generating")} />
      </PageHeader>
      <div className="grid gap-5 xl:grid-cols-3">
        <div className="xl:col-span-2 space-y-5">
          <Panel title={t("list")}>
            {d.experiments.length === 0 ? <Empty>{t("none")}</Empty> : (
              <table className="w-full text-sm">
                <thead><tr>
                  <th className={th}>{t("name")}</th><th className={th}>{t("pairs")}</th><th className={th}>{t("ready")}</th>
                  <th className={th}>{t("myVotes")}</th><th className={th}>{t("votes")}</th><th className={th} />
                </tr></thead>
                <tbody>
                  {d.experiments.map((x) => (
                    <tr key={x.experiment} className="border-b border-border/60">
                      <td className={td}><div className="font-medium">{x.experiment}</div><div className="text-xs text-muted">{fmtTime(x.created_at, locale)}</div></td>
                      <td className={td}>{x.pairs}</td>
                      <td className={td}>{x.ready}{x.failed > 0 && <> <Badge tone="down">{t("failed", { n: x.failed })}</Badge></>}</td>
                      <td className={td}>{x.my_votes} / {x.ready}</td>
                      <td className={td}>{x.votes}</td>
                      <td className={`${td} space-x-3 text-xs`}>
                        <Link className="text-accent" href={`/experiments/${x.experiment}`}>{t("review")}</Link>
                        <Link className="text-accent" href={`/experiments/${x.experiment}/results`}>{t("results")}</Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
          {d.pending.length > 0 && (
            <Panel title={t("pending")}>
              {d.pending.map((p, i) => (
                <div key={i} className="mb-2 text-sm">
                  {p.experiment} · {p.status === "done"
                    ? <Badge tone="down">{t("noSample")}</Badge>
                    : <Badge tone={p.status === "failed" || p.status === "dead" ? "down" : "warn"}>{t(`job.${p.status}` as "job.queued")}</Badge>}
                  <span className="ml-2 text-xs text-muted">{fmtTime(p.created_at, locale)}</span>
                  {p.result && p.result.pairs === 0 && (
                    <div className="text-xs text-down">
                      {REASONS.filter((r) => p.result!.skipped[r]).map((r) => t(`reason.${r}`, { n: p.result!.skipped[r] })).join("；")}
                    </div>
                  )}
                  {p.last_error && <div className="text-xs text-down">{p.last_error}</div>}
                </div>
              ))}
            </Panel>
          )}
          <Panel title={t("howTitle")}>
            <ul className="list-disc space-y-1 pl-5 text-sm text-muted">
              {(["how1", "how2", "how3", "how4"] as const).map((k) => <li key={k}>{t(k)}</li>)}
            </ul>
          </Panel>
        </div>
        <Panel title={t("create")} hint={t("createHint")}>
          <FieldForm path="experiments" submitLabel={t("start")} successMessage={t("started")} fields={[
            { name: "name", label: t("name"), required: true, value: `contrast-${new Date().toISOString().slice(0, 10)}`, wide: true },
            { name: "account_id", label: t("account"), type: "select", numeric: true, required: true,
              options: accounts.map((a) => ({ value: String(a.id), label: `@${a.handle} (${a.language}, ${a.status})` })) },
            { name: "model_config_id", label: t("model"), type: "select", numeric: true, required: true,
              value: String(models.find((m) => m.last_test_ok)?.id ?? models[0]?.id ?? ""),
              options: models.map((m) => ({ value: String(m.id), label: `${m.name} · ${m.model}${m.last_test_ok ? " ✓" : ""}` })) },
            { name: "n", label: t("n"), type: "number", value: 30 },
          ]} />
        </Panel>
      </div>
    </>
  );
}
