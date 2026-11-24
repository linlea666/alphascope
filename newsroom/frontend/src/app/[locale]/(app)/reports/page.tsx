import { getTranslations } from "next-intl/server";
import { Empty, PageHeader, Panel, Stat, input, button, td, th } from "@/components/ui";
import { nrGet } from "@/lib/api";
import { named } from "@/lib/glossary";

type Row = { key: string | number; written: number; published: number; expired: number; guard_failed: number; reviewed: number;
  review_approved: number; publishable: number | null; ai_tone: number | null; native: number | null; hook: number | null;
  with_metrics: number; impressions: number | null; likes: number | null; reposts: number | null; replies: number | null };
type Report = { days: number; total: Row; by_style: Row[]; by_agent: Row[]; by_account: Row[]; by_publishable: Row[];
  ai_calls: { calls: number; failed: number; cost_usd: number } };

const COLS = ["written", "published", "expired", "guard_failed", "review_approved", "publishable", "ai_tone", "native", "hook",
  "with_metrics", "impressions", "likes", "reposts", "replies"] as const;

/** The desk's weekly quality report: what got written and out, what the AI reviewer thought, how readers responded. */
export default async function ReportsPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const [t, tr, d, styles] = await Promise.all([getTranslations("reports"), getTranslations("roles"),
    nrGet<Report>("reports/weekly", { days: sp.days ?? 7 }),
    nrGet<{ key: string; name: string }[]>("styles")]);
  const styleName = (k: string | number) => styles.find((s) => s.key === k)?.name ?? String(k);
  const table = (rows: Row[], label: (k: string | number) => string) => rows.length === 0 ? <Empty>—</Empty> : (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead><tr><th className={th} />{COLS.map((c) => <th key={c} className={th}>{t(`col.${c}`)}</th>)}</tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={String(r.key)} className="border-b border-border/60">
              <td className={td}>{label(r.key)}</td>
              {COLS.map((c) => <td key={c} className={`${td} num`}>{r[c] ?? "—"}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
  const pct = (a: number, b: number) => (b ? `${Math.round((a / b) * 100)}%` : "—");
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <form className="flex gap-2 text-sm">
          <select name="days" defaultValue={sp.days ?? "7"} className={`${input} w-28`}>
            {[7, 14, 30].map((n) => <option key={n} value={n}>{t("days", { n })}</option>)}
          </select>
          <button className={button}>{t("show")}</button>
        </form>
      </PageHeader>
      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label={t("col.written")} value={d.total.written} />
        <Stat label={t("col.published")} value={d.total.published} sub={pct(d.total.published, d.total.written)} />
        <Stat label={t("col.expired")} value={d.total.expired} sub={pct(d.total.expired, d.total.written)} />
        <Stat label={t("col.review_approved")} value={`${d.total.review_approved}/${d.total.reviewed}`} />
        <Stat label={t("col.publishable")} value={d.total.publishable ?? "—"} sub={t("outOf5")} />
        <Stat label={t("aiCalls")} value={`${d.ai_calls.failed}/${d.ai_calls.calls}`} sub={`$${Number(d.ai_calls.cost_usd).toFixed(2)}`} />
      </div>
      <div className="space-y-5">
        <Panel title={t("byStyle")}>{table(d.by_style, styleName)}</Panel>
        <Panel title={t("byAgent")}>{table(d.by_agent, (k) => named(tr, String(k)).name)}</Panel>
        <Panel title={t("byAccount")}>{table(d.by_account, (k) => `@${k}`)}</Panel>
        <Panel title={t("byPublishable")} hint={t("byPublishableHint")}>{table(d.by_publishable, (k) => t("score", { n: k }))}</Panel>
      </div>
    </>
  );
}
