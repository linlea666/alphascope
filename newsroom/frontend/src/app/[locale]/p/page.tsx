import { getLocale, getTranslations } from "next-intl/server";
import { ClassLabel } from "@/components/badges";
import { PositionNow } from "@/components/PublicBits";
import { Empty, PageHeader, Panel, td } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { publicGet } from "@/lib/api";
import { fmtTime } from "@/lib/format";

type Story = { id: number; status: string; name: string | null; chapters: number; last_published: string };
type Ledger = { id: number; event_class: string; coin: string; direction: string | null; story_id: number | null; first_published: string;
  position_now: string | null; closed_by: string | null; name: string | null };
type Corrections = { corrections: { id: number; detected_at: string; status: string; note: string | null; changed_facts: string[]; event_class: string; coin: string }[];
  retractions: { id: number; handle: string; x_url: string; retracted_at: string; retract_reason: string }[] };

export default async function PublicHome() {
  const [t, locale] = await Promise.all([getTranslations("public"), getLocale()]);
  const [stories, ledger, corr] = await Promise.all([publicGet<Story[]>("stories"), publicGet<Ledger[]>("ledger"),
    publicGet<Corrections>("corrections")]);
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <div className="space-y-5">
        <Panel title={t("stories")}>
          {!stories?.length ? <Empty>{t("noItems")}</Empty> : stories.map((s) => (
            <div key={s.id} className="flex justify-between border-b border-border/60 py-2 text-sm">
              <Link className="text-accent" href={`/p/stories/${s.id}`}>{s.name ?? `#${s.id}`}</Link>
              <span className="text-xs text-muted">{s.chapters} · {fmtTime(s.last_published, locale)}</span>
            </div>
          ))}
        </Panel>
        <Panel title={t("ledger")} hint={t("ledgerHint")}>
          {!ledger?.length ? <Empty>{t("noItems")}</Empty> : (
            <table className="w-full text-sm"><tbody>
              {ledger.map((r) => (
                <tr key={r.id} className="border-b border-border/60">
                  <td className={`${td} text-xs text-muted`}>{fmtTime(r.first_published, locale)}</td>
                  <td className={td}><ClassLabel cls={r.event_class} /> · {r.coin} {r.direction}</td>
                  <td className={td}>{r.story_id ? <Link className="text-accent" href={`/p/stories/${r.story_id}`}>{r.name}</Link> : r.name}</td>
                  <td className={td}><PositionNow status={r.position_now} /></td>
                </tr>
              ))}
            </tbody></table>
          )}
        </Panel>
        <Panel title={t("corrections")}>
          {!corr || (corr.corrections.length === 0 && corr.retractions.length === 0) ? <Empty>{t("noItems")}</Empty> : (
            <div className="space-y-2 text-sm">
              {corr.corrections.map((c) => (
                <div key={c.id}>{fmtTime(c.detected_at, locale)} · <ClassLabel cls={c.event_class} /> {c.coin} · {c.changed_facts.join(", ")} · {c.status} {c.note}</div>
              ))}
              {corr.retractions.map((r) => (
                <div key={`r${r.id}`}>{t("retractions")}: @{r.handle} · {fmtTime(r.retracted_at, locale)} · {r.retract_reason}</div>
              ))}
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}
