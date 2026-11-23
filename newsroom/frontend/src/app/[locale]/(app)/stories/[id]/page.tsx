import { getLocale, getTranslations } from "next-intl/server";
import { Action } from "@/components/Action";
import { ClassLabel, StateBadge } from "@/components/badges";
import { Badge, PageHeader, Panel } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import { fmtTime, shortKey } from "@/lib/format";

type StoryDetail = {
  story: { id: number; story_key: string; status: string; trader_key: string | null; display_name: string | null; public_label: string | null };
  timeline: { seq: number; role: string; event_id: number; event_class: string; coin: string; direction: string | null; state: string; score: number | null;
    liquidation_status: string; detected_at: string; pinned_version: number | null; publications: { handle: string; url: string; at: string; status: string }[] | null }[];
};

export default async function StoryPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [t, tc, locale, d] = await Promise.all([getTranslations("stories"), getTranslations("columns"), getLocale(),
    nrGet<StoryDetail>(`stories/${id}`)]);
  const s = d.story;
  return (
    <>
      <PageHeader title={s.public_label ?? s.display_name ?? (s.trader_key ? shortKey(s.trader_key) : s.story_key)} subtitle={s.story_key}>
        <StateBadge state={s.status} />
        {s.trader_key && <Action path="columns/dossier/run" body={{ params: { trader_key: s.trader_key } }}>{tc("dossier")}</Action>}
      </PageHeader>
      <Panel title={t("timeline")}>
        <ol className="relative space-y-4 border-l border-border pl-5">
          {d.timeline.map((c) => (
            <li key={c.seq}>
              <div className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full bg-accent" />
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge tone={c.role === "resolution" ? "up" : c.role === "origin" ? "accent" : "neutral"}>{t(c.role as "origin" | "follow_up" | "resolution")}</Badge>
                <Link className="text-accent" href={`/radar/${c.event_id}`}>#{c.event_id}</Link>
                <ClassLabel cls={c.event_class} /> · {c.coin} {c.direction}
                {c.liquidation_status !== "none" && <Badge tone="warn">{c.liquidation_status}</Badge>}
                <StateBadge state={c.state} />
                <span className="text-xs text-muted">{fmtTime(c.detected_at, locale)}</span>
                {c.pinned_version && <span className="text-xs text-muted">{t("pinned")} v{c.pinned_version}</span>}
              </div>
              {(c.publications ?? []).map((p, i) => (
                <div key={i} className="ml-2 text-xs">@{p.handle} · <a className="text-accent" href={p.url} target="_blank" rel="noreferrer">{p.url}</a> · {fmtTime(p.at, locale)} · {p.status}</div>
              ))}
            </li>
          ))}
        </ol>
      </Panel>
    </>
  );
}
