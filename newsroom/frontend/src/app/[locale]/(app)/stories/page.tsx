import { getLocale, getTranslations } from "next-intl/server";
import { StateBadge } from "@/components/badges";
import { Empty, PageHeader, Panel, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import { fmtTime, shortKey } from "@/lib/format";

type Story = { id: number; story_key: string; status: string; trader_key: string | null; display_name: string | null; public_label: string | null;
  chapters: number; publications: number; last_event_at: string | null; opened_at: string };

export default async function StoriesPage() {
  const [t, locale, rows] = await Promise.all([getTranslations("stories"), getLocale(), nrGet<Story[]>("stories")]);
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <Panel>
        {rows.length === 0 ? <Empty>—</Empty> : (
          <table className="w-full text-sm">
            <thead><tr><th className={th}>#</th><th className={th}>trader</th><th className={th}>{t("chapters")}</th><th className={th}>{t("publications")}</th><th className={th} /><th className={th} /></tr></thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id} className="border-b border-border/60">
                  <td className={td}><Link className="text-accent" href={`/stories/${s.id}`}>#{s.id}</Link></td>
                  <td className={td}>{s.public_label ?? s.display_name ?? (s.trader_key ? shortKey(s.trader_key) : s.story_key)}</td>
                  <td className={`${td} num`}>{s.chapters}</td>
                  <td className={`${td} num`}>{s.publications}</td>
                  <td className={td}><StateBadge state={s.status} /></td>
                  <td className={`${td} text-xs text-muted`}>{fmtTime(s.last_event_at ?? s.opened_at, locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  );
}
