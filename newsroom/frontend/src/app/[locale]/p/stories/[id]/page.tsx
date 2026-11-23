import { getLocale, getTranslations } from "next-intl/server";
import { notFound } from "next/navigation";
import { ClassLabel } from "@/components/badges";
import { PositionNow } from "@/components/PublicBits";
import { Badge, PageHeader, Panel } from "@/components/ui";
import { publicGet } from "@/lib/api";
import { fmtTime } from "@/lib/format";

type Chapter = { seq: number; role: string; event_id: number; event_class: string; coin: string; direction: string | null;
  liquidation_status: string; fact_version: number | null; position_now: string | null;
  facts: { id: string; display: { zh?: string[]; en?: string[] }; label?: string }[];
  constraints: { code: string; text: string }[] | null;
  posts: { handle: string; x_url: string; published_at: string; final_text: string; status: string; retract_reason: string | null }[] };

export default async function PublicStory({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [t, locale] = await Promise.all([getTranslations("public"), getLocale()]);
  const d = await publicGet<{ story: { id: number; status: string; name: string | null }; chapters: Chapter[] }>(`stories/${id}`);
  if (!d) notFound();
  const lang = locale === "zh" ? "zh" : "en";
  return (
    <>
      <PageHeader title={d.story.name ?? `#${d.story.id}`} subtitle={t("subtitle")} />
      <ol className="relative space-y-5 border-l border-border pl-5">
        {d.chapters.map((c) => (
          <li key={c.seq}>
            <div className="absolute -left-1.5 mt-2 h-3 w-3 rounded-full bg-accent" />
            <Panel title={<span><ClassLabel cls={c.event_class} /> · {c.coin} {c.direction} {c.liquidation_status !== "none" && <Badge tone="warn">{c.liquidation_status}</Badge>}</span>}
              actions={<span className="text-xs text-muted">{t("now")}: <PositionNow status={c.position_now} /></span>}>
              {c.posts.map((p, i) => (
                <div key={i} className="mb-3 text-sm">
                  <p className="whitespace-pre-wrap">{p.status === "retracted" ? `[${t("retractions")}] ${p.retract_reason ?? ""}` : p.final_text}</p>
                  <div className="mt-1 text-xs text-muted">@{p.handle} · {t("published")} {fmtTime(p.published_at, locale)} · <a className="text-accent" href={p.x_url} target="_blank" rel="noreferrer">X</a></div>
                </div>
              ))}
              <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-border/60 pt-2 text-xs text-muted">
                {c.facts.map((f) => <span key={f.id}>{(f.display[lang] ?? [])[0]}</span>)}
                {c.fact_version && <span>· fact packet v{c.fact_version}</span>}
              </div>
              {(c.constraints ?? []).length > 0 && <ul className="mt-2 text-[11px] text-muted">{(c.constraints ?? []).map((x) => <li key={x.code}>· {x.text}</li>)}</ul>}
            </Panel>
          </li>
        ))}
      </ol>
    </>
  );
}
