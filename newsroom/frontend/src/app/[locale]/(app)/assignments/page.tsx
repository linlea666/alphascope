import { getLocale, getTranslations } from "next-intl/server";
import { BoardControls } from "@/components/BoardRow";
import { ClassLabel, StateBadge } from "@/components/badges";
import { Badge, Empty, PageHeader, Panel, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { named } from "@/lib/glossary";
import { nrGet } from "@/lib/api";
import { fmtTime } from "@/lib/format";

type Row = { id: number; news_event_id: number; handle: string; language: string; agent_key: string; angle: string; status: string;
  needs_human: boolean; due_at: string | null; claimed_by: string | null; reviewer: string | null; event_class: string; coin: string;
  priority: string | null; article_id: number | null; article_status: string | null; overdue: boolean };
const ROLES = ["reporter", "reporter_breaking", "reporter_investigator", "reporter_data", "reporter_derivatives", "reporter_historian"];

export default async function AssignmentsPage({ searchParams }: { searchParams: Promise<{ mine?: string }> }) {
  const mine = (await searchParams).mine === "1";
  const [t, locale, list, tr] = await Promise.all([getTranslations("board"), getLocale(), nrGet<Row[]>("assignments", { mine }),
    getTranslations("roles")]);
  const roles = ROLES.map((key) => ({ key, name: named(tr, key).name }));
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <Link className="text-sm text-accent" href={mine ? "/assignments" : "/assignments?mine=1"}>{t("mine")}{mine ? " ✓" : ""}</Link>
      </PageHeader>
      <Panel>
        {list.length === 0 ? <Empty>—</Empty> : (
          <table className="w-full text-sm">
            <thead><tr><th className={th}>#</th><th className={th}>event</th><th className={th}>@</th><th className={th}>{t("reporter")}</th>
              <th className={th}>status</th><th className={th}>{t("due")}</th><th className={th} /></tr></thead>
            <tbody>
              {list.map((a) => (
                <tr key={a.id} className="border-b border-border/60 align-top">
                  <td className={td}>{a.id} {a.overdue && <Badge tone="down">{t("overdue")}</Badge>}</td>
                  <td className={`${td} whitespace-normal`}>
                    <Link className="text-accent" href={`/radar/${a.news_event_id}`}>#{a.news_event_id}</Link> <ClassLabel cls={a.event_class} /> · {a.coin}
                    {a.priority && <Badge tone="accent">{a.priority}</Badge>}
                    <div className="max-w-xs text-xs text-muted">{a.angle}</div>
                  </td>
                  <td className={td}>@{a.handle} · {a.language}</td>
                  <td className={td}><span className="text-xs" title={a.agent_key}>{named(tr, a.agent_key).name}</span>{a.needs_human && <Badge tone="warn">!</Badge>}</td>
                  <td className={td}>
                    <StateBadge state={a.status} /> {a.article_status && <StateBadge state={a.article_status} />}
                    <div className="text-xs text-muted">{a.claimed_by ?? ""}{a.reviewer ? ` → ${a.reviewer}` : ""}</div>
                  </td>
                  <td className={`${td} text-xs`}>{fmtTime(a.due_at, locale)}</td>
                  <td className={td}>
                    <BoardControls id={a.id} claimed={Boolean(a.claimed_by)} roles={roles} />
                    {a.article_id && <Link className="text-xs text-accent" href={`/desk/${a.article_id}`}>{t("open")}</Link>}
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
