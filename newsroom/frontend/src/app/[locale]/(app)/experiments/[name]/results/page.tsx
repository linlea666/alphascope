import { getTranslations } from "next-intl/server";
import { Badge, Empty, PageHeader, Panel, Stat, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGetOptional } from "@/lib/api";

type Arm = { drafts: number; errors: number; guard_pass: number | null; misleading: number; mean_scores: Record<string, number | null>; tokens: number };
type ArmCfg = { prompt_version: number; variant?: string; style_key?: string | null; model_config_id?: number | null };
type R = { experiment: string; arms_config: { a: ArmCfg; b: ArmCfg }; pairs: number; ready: number; votes: number; voters: number; b_win_rate: number | null;
  arms: { a: Arm; b: Arm }; contrast_coverage: number | null; families: Record<string, number>; extra_tokens_b: number | null;
  by_stratum: Record<string, number>; checks: Record<string, boolean | null>; stop: string[]; verdict: "pass" | "stop" | "open" };

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);

export default async function ResultsPage({ params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  const t = await getTranslations("experiments");
  const r = await nrGetOptional<R>(`experiments/${name}/results`);
  if (!r) {
    return (<><PageHeader title={`${t("results")} · ${name}`} /><Panel><Empty>{t("resultsLocked")}</Empty></Panel>
      <Link className="text-sm text-accent" href={`/experiments/${name}`}>{t("review")}</Link></>);
  }
  const tone = r.verdict === "pass" ? "up" : r.verdict === "stop" ? "down" : "warn";
  return (
    <>
      <PageHeader title={`${t("results")} · ${name}`} subtitle={t("resultsSubtitle")}>
        <Badge tone={tone}>{t(`verdict.${r.verdict}`)}</Badge>
      </PageHeader>
      <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Stat label={t("bWin")} value={pct(r.b_win_rate)} sub={t("bWinSub")} />
        <Stat label={t("coverage")} value={pct(r.contrast_coverage)} sub={t("coverageSub")} />
        <Stat label={t("votes")} value={r.votes} sub={t("voters", { n: r.voters })} />
        <Stat label={t("ready")} value={`${r.ready} / ${r.pairs}`} />
        <Stat label={t("extraTokens")} value={pct(r.extra_tokens_b)} sub={t("extraTokensSub")} />
      </div>
      <div className="grid gap-5 xl:grid-cols-2">
        <Panel title={t("arms")}>
          <div className="mb-3 space-y-1 text-xs text-muted">
            {(["a", "b"] as const).map((k) => { const c = r.arms_config[k]; return (
              <div key={k}>{k === "a" ? t("armA") : t("armB")}：v{c.prompt_version}{c.variant ? ` (${c.variant})` : ""}
                {" · "}{c.style_key ?? t("styleDefault")}{c.model_config_id ? ` · ${t("modelNo", { id: c.model_config_id })}` : ""}</div>); })}
          </div>
          <table className="w-full text-sm">
            <thead><tr><th className={th} /><th className={th}>{t("armA")}</th><th className={th}>{t("armB")}</th></tr></thead>
            <tbody>
              {(["information", "lede", "evidence", "publishable"] as const).map((d) => (
                <tr key={d} className="border-b border-border/60"><td className={td}>{t(`dim.${d}`)}</td>
                  <td className={td}>{r.arms.a.mean_scores[d] ?? "—"}</td><td className={td}>{r.arms.b.mean_scores[d] ?? "—"}</td></tr>
              ))}
              <tr className="border-b border-border/60"><td className={td}>{t("guardPass")}</td><td className={td}>{pct(r.arms.a.guard_pass)}</td><td className={td}>{pct(r.arms.b.guard_pass)}</td></tr>
              <tr className="border-b border-border/60"><td className={td}>{t("misleadingCount")}</td><td className={td}>{r.arms.a.misleading}</td><td className={td}>{r.arms.b.misleading}</td></tr>
              <tr className="border-b border-border/60"><td className={td}>{t("tokens")}</td><td className={td}>{r.arms.a.tokens}</td><td className={td}>{r.arms.b.tokens}</td></tr>
              <tr><td className={td}>{t("errors")}</td><td className={td}>{r.arms.a.errors}</td><td className={td}>{r.arms.b.errors}</td></tr>
            </tbody>
          </table>
        </Panel>
        <div className="space-y-5">
          <Panel title={t("checks")}>
            <ul className="space-y-1 text-sm">
              {Object.entries(r.checks).map(([k, v]) => (
                <li key={k} className="flex justify-between"><span>{t(`check.${k}`)}</span>
                  <Badge tone={v === null ? "neutral" : v ? "up" : "down"}>{v === null ? "—" : v ? "✓" : "✗"}</Badge></li>
              ))}
              {r.stop.map((k) => <li key={k} className="text-down">{t(`stopReason.${k}`)}</li>)}
            </ul>
          </Panel>
          <Panel title={t("familiesTitle")} hint={t("familiesHint")}>
            <ul className="space-y-1 text-sm">
              {Object.entries(r.families).map(([k, n]) => (
                <li key={k} className="flex justify-between"><span>{t(`family.${k}`)}</span><span>{n} / {r.ready}</span></li>
              ))}
            </ul>
            <div className="mt-3 text-xs text-muted">{Object.entries(r.by_stratum).map(([k, n]) => `${t(`stratum.${k}`)} ${n}`).join(" · ")}</div>
          </Panel>
        </div>
      </div>
    </>
  );
}
