import { getLocale, getTranslations } from "next-intl/server";
import { Action } from "@/components/Action";
import { ClassLabel, StateBadge } from "@/components/badges";
import { ScheduleForm } from "@/components/ScheduleForm";
import { Badge, Empty, PageHeader, Panel, td } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import { fmtTime } from "@/lib/format";

type Plan = { id: number; article_id: number; handle: string; timezone: string; planned_at: string; status: string; note: string | null;
  headline: string | null; event_class: string; coin: string; aging: boolean };
type Unscheduled = { article_id: number; handle: string; headline: string | null; event_class: string; coin: string };

export default async function CalendarPage() {
  const [t, locale, d] = await Promise.all([getTranslations("calendar"), getLocale(),
    nrGet<{ plans: Plan[]; unscheduled: Unscheduled[] }>("plans")]);
  const days = new Map<string, Plan[]>();
  for (const p of d.plans) {
    const day = fmtTime(p.planned_at, locale, p.timezone).slice(0, 10);
    days.set(day, [...(days.get(day) ?? []), p]);
  }
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <div className="grid gap-5 xl:grid-cols-3">
        <div className="space-y-5 xl:col-span-2">
          {days.size === 0 && <Empty>—</Empty>}
          {[...days.entries()].map(([day, plans]) => (
            <Panel key={day} title={day}>
              <table className="w-full text-sm"><tbody>
                {plans.map((p) => (
                  <tr key={p.id} className="border-b border-border/60 align-top">
                    <td className={`${td} text-xs`}>{fmtTime(p.planned_at, locale, p.timezone)}</td>
                    <td className={td}>@{p.handle}</td>
                    <td className={`${td} whitespace-normal`}>
                      <Link className="text-accent" href={`/desk/${p.article_id}`}>{p.headline || `#${p.article_id}`}</Link>
                      <div className="text-xs text-muted"><ClassLabel cls={p.event_class} /> · {p.coin} {p.note}</div>
                    </td>
                    <td className={td}><StateBadge state={p.status === "planned" ? "proposed" : p.status} /> {p.aging && <Badge tone="warn">{t("aging")}</Badge>}</td>
                    <td className={td}>{["planned", "claimed"].includes(p.status) && <Action path={`plans/${p.id}/cancel`} tone="danger">{t("cancel")}</Action>}</td>
                  </tr>
                ))}
              </tbody></table>
            </Panel>
          ))}
        </div>
        <Panel title={t("unscheduled")}>
          {d.unscheduled.length === 0 ? <Empty>—</Empty> : d.unscheduled.map((u) => (
            <div key={u.article_id} className="mb-4 space-y-1 border-b border-border/60 pb-3 text-sm">
              <Link className="text-accent" href={`/desk/${u.article_id}`}>@{u.handle} · {u.headline || `#${u.article_id}`}</Link>
              <div className="text-xs text-muted"><ClassLabel cls={u.event_class} /> · {u.coin}</div>
              <ScheduleForm articleId={u.article_id} />
            </div>
          ))}
        </Panel>
      </div>
    </>
  );
}
