import { getLocale, getTranslations } from "next-intl/server";
import { Action } from "@/components/Action";
import { StateBadge } from "@/components/badges";
import { FieldForm } from "@/components/FieldForm";
import { Badge, Empty, PageHeader, Panel, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import { fmtTime } from "@/lib/format";

type Pub = { id: number; article_id: number; handle: string; language: string; x_url: string; x_post_id: string; published_at: string; status: string;
  matches_version: boolean; version_no: number; headline: string; retract_reason: string | null;
  latest_performance: { impressions: number | null; likes: number | null; reposts: number | null; replies: number | null; follows: number | null; captured_at: string } | null };

export default async function PublicationsPage() {
  const [t, tc, locale, pubs] = await Promise.all([getTranslations("publications"), getTranslations("common"), getLocale(), nrGet<Pub[]>("publications")]);
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <Panel>
        {pubs.length === 0 ? <Empty>—</Empty> : (
          <table className="w-full text-sm">
            <thead><tr><th className={th}>{t("post")}</th><th className={th}>{tc("account")}</th><th className={th}>{tc("version")}</th>
              <th className={th}>{t("impressions")}</th><th className={th}>{tc("status")}</th><th className={th}>{tc("actions")}</th></tr></thead>
            <tbody>
              {pubs.map((p) => (
                <tr key={p.id} className="border-b border-border/60 align-top">
                  <td className={`${td} whitespace-normal`}>
                    <a className="text-accent" href={p.x_url} target="_blank" rel="noreferrer">{p.x_post_id}</a>
                    <div className="max-w-md text-xs text-muted">{p.headline}</div>
                    <div className="text-xs text-muted">{fmtTime(p.published_at, locale)}</div>
                  </td>
                  <td className={td}>@{p.handle} · {p.language}</td>
                  <td className={td}><Link className="text-accent" href={`/desk/${p.article_id}`}>#{p.article_id} v{p.version_no}</Link> {!p.matches_version && <Badge tone="warn">≠</Badge>}</td>
                  <td className={`${td} num`}>{p.latest_performance ? `${p.latest_performance.impressions ?? "—"} · ♥${p.latest_performance.likes ?? "—"} · +${p.latest_performance.follows ?? "—"}` : "—"}</td>
                  <td className={td}><StateBadge state={p.status} />{p.retract_reason && <div className="text-xs text-muted">{p.retract_reason}</div>}</td>
                  <td className={td}>
                    {p.status === "published" && (
                      <div className="space-y-2">
                        <Action path={`publications/${p.id}/retract`} prompt={{ label: t("retractReason"), field: "reason" }} tone="danger">{t("retract")}</Action>
                        <details><summary className="cursor-pointer text-xs text-accent">{t("performance")}</summary>
                          <div className="mt-2 w-80">
                            <FieldForm path={`publications/${p.id}/performance`} fields={[
                              { name: "captured_at", label: t("capturedAt"), value: new Date().toISOString(), required: true },
                              { name: "impressions", label: t("impressions"), type: "number" },
                              { name: "likes", label: t("likes"), type: "number" },
                              { name: "reposts", label: t("reposts"), type: "number" },
                              { name: "replies", label: t("replies"), type: "number" },
                              { name: "follows", label: t("follows"), type: "number" },
                            ]} />
                          </div>
                        </details>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  );
}
