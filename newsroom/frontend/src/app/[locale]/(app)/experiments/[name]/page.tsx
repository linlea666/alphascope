import { getTranslations } from "next-intl/server";
import { BlindVote } from "@/components/BlindVote";
import { ClassLabel } from "@/components/badges";
import { Empty, PageHeader, Panel } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";

type Pair = { pair_id: number; event: { id: number; event_class: string; coin: string; direction: string | null; venue: string };
  facts: { id: string; label: string | null; display: string | null }[]; constraints: { code: string; text: string }[];
  drafts: { slot: string; headline: string; body: string; thread: string[] | null; language: string }[] };
type D = { experiment: string; pairs: number; my_votes: number; pair: Pair | null };

export default async function ReviewPage({ params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  const t = await getTranslations("experiments");
  const d = await nrGet<D>(`experiments/${name}/next`);
  const p = d.pair;
  return (
    <>
      <PageHeader title={`${t("reviewTitle")} · ${name}`} subtitle={t("reviewSubtitle", { done: d.my_votes, total: d.pairs })}>
        <Link className="text-sm text-accent" href={`/experiments/${name}/results`}>{t("results")}</Link>
      </PageHeader>
      {!p ? <Panel><Empty>{t("allDone")}</Empty></Panel> : (
        <div className="grid gap-5 xl:grid-cols-3">
          <div className="space-y-5 xl:col-span-2">
            <div className="grid gap-4 md:grid-cols-2">
              {p.drafts.map((v) => (
                <Panel key={v.slot} title={t("draft", { n: v.slot })}>
                  {v.headline && <div className="mb-2 font-medium">{v.headline}</div>}
                  <p className="whitespace-pre-wrap text-sm leading-relaxed">{v.body}</p>
                  {v.thread?.map((x, i) => <p key={i} className="mt-2 whitespace-pre-wrap border-t border-border/60 pt-2 text-sm">{x}</p>)}
                </Panel>
              ))}
            </div>
            <Panel title={t("judge")}><BlindVote experiment={name} pairId={p.pair_id} /></Panel>
          </div>
          <Panel title={t("facts")} hint={<><ClassLabel cls={p.event.event_class} /> · {p.event.coin} · {p.event.direction ?? ""} · #{p.event.id}</>}>
            <ul className="space-y-1 text-xs">
              {p.facts.map((f) => (
                <li key={f.id} className="flex justify-between gap-3 border-b border-border/40 pb-1">
                  <span className="text-muted">{f.id}{f.label && <div className="text-[11px] opacity-70">{f.label}</div>}</span>
                  <span className="text-right">{f.display}</span>
                </li>
              ))}
            </ul>
            <div className="mt-3 space-y-1 text-[11px] text-muted">{p.constraints.map((c) => <div key={c.code}>· {c.text}</div>)}</div>
          </Panel>
        </div>
      )}
    </>
  );
}
