import { getLocale, getTranslations } from "next-intl/server";
import { Action } from "@/components/Action";
import { GateBadge, StateBadge } from "@/components/badges";
import { FieldForm } from "@/components/FieldForm";
import { Badge, PageHeader, Panel, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import { fmtTime } from "@/lib/format";

type Col = { key: string; zh: string; en: string; cadence: string; phase: string; enabled: boolean; issues: number;
  latest: { id: number; period_key: string; state: string; gate_status: string; detected_at: string; blocking_issues: { code: string; detail: string }[] } | null };

export default async function ColumnsPage() {
  const [t, locale, cols] = await Promise.all([getTranslations("columns"), getLocale(), nrGet<Col[]>("columns")]);
  const runnable = cols.filter((c) => !["recap", "dossier"].includes(c.key));
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <Panel>
        <table className="w-full text-sm">
          <thead><tr><th className={th}>{t("title")}</th><th className={th}>{t("phase")}</th><th className={th}>{t("cadence")}</th>
            <th className={th}>{t("issues")}</th><th className={th}>{t("latest")}</th><th className={th} /></tr></thead>
          <tbody>
            {cols.map((c) => (
              <tr key={c.key} className="border-b border-border/60 align-top">
                <td className={td}>{locale === "zh" ? c.zh : c.en}<div className="font-mono text-[11px] text-muted">{c.key}</div></td>
                <td className={td}><Badge tone="accent">{c.phase}</Badge> {!c.enabled && <Badge tone="warn">off</Badge>}</td>
                <td className={td}>{t(c.cadence as "weekly")}</td>
                <td className={`${td} num`}>{c.issues}</td>
                <td className={`${td} whitespace-normal`}>
                  {c.latest ? (
                    <div>
                      <Link className="text-accent" href={`/radar/${c.latest.id}`}>#{c.latest.id}</Link> {c.latest.period_key}{" "}
                      <StateBadge state={c.latest.state} /> <GateBadge status={c.latest.gate_status} />
                      <div className="text-xs text-muted">{fmtTime(c.latest.detected_at, locale)} {c.latest.blocking_issues.map((b) => b.detail).join("; ")}</div>
                    </div>
                  ) : "—"}
                </td>
                <td className={td}>{runnable.includes(c) && <Action path={`columns/${c.key}/run`} body={{ params: {} }}>{t("run")}</Action>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <div className="mt-5">
        <Panel title={t("dossier")}>
          <FieldForm path="columns/dossier/run" submitLabel={t("run")} fields={[{ name: "params", label: t("traderKey"), type: "json", value: { trader_key: "" } }]} />
        </Panel>
      </div>
    </>
  );
}
