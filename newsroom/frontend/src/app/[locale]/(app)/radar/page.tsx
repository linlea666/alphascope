import { getLocale, getTranslations } from "next-intl/server";
import { ClassLabel, GateBadge, StateBadge } from "@/components/badges";
import { Badge, Empty, PageHeader, Panel, button, input, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import { fmtTime, shortKey } from "@/lib/format";
import type { NewsEvent } from "@/lib/types";

const CLASSES = ["whale_open", "flip", "whale_add", "underwater_add", "whale_close", "liquidation", "near_liquidation", "notable_trader_move"];
const STATES = ["detected", "blocked", "candidate", "evaluating", "evaluated", "assigned", "covered", "declined", "dismissed", "archived"];

export default async function RadarPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const [t, tc, ts, locale] = await Promise.all([getTranslations("events"), getTranslations("classes"), getTranslations("states"), getLocale()]);
  const data = await nrGet<{ total: number; rows: NewsEvent[] }>("events", {
    state: sp.state, event_class: sp.event_class, venue: sp.venue, coin: sp.coin, gate: sp.gate,
    min_score: sp.min_score, include_dismissed: sp.include_dismissed === "on",
  });
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <form className="mb-4 flex flex-wrap items-end gap-2 text-sm">
        <select name="state" defaultValue={sp.state ?? ""} className={`${input} w-36`}>
          <option value="">{t("state")}</option>
          {STATES.map((s) => <option key={s} value={s}>{ts(s)}</option>)}
        </select>
        <select name="event_class" defaultValue={sp.event_class ?? ""} className={`${input} w-40`}>
          <option value="">{t("class")}</option>
          {CLASSES.map((c) => <option key={c} value={c}>{tc(c)}</option>)}
        </select>
        <select name="venue" defaultValue={sp.venue ?? ""} className={`${input} w-32`}>
          <option value="">venue</option><option value="hyperliquid">Hyperliquid</option><option value="okx">OKX</option>
        </select>
        <select name="gate" defaultValue={sp.gate ?? ""} className={`${input} w-28`}>
          <option value="">{t("gate")}</option><option value="pass">pass</option><option value="blocked">blocked</option>
        </select>
        <input name="coin" defaultValue={sp.coin ?? ""} placeholder="BTC" className={`${input} w-24`} />
        <input name="min_score" defaultValue={sp.min_score ?? ""} placeholder={t("minScore")} className={`${input} w-24`} />
        <label className="flex items-center gap-1 text-muted">
          <input type="checkbox" name="include_dismissed" defaultChecked={sp.include_dismissed === "on"} /> {t("includeDismissed")}
        </label>
        <button className={button}>OK</button>
      </form>
      <Panel title={`${data.total}`}>
        {data.rows.length === 0 ? <Empty>{t("noEvents")}</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr><th className={th}>#</th><th className={th}>{t("class")}</th><th className={th}>{t("trader")}</th>
                  <th className={th}>{t("gate")}</th><th className={th}>score</th><th className={th}>{t("state")}</th>
                  <th className={th}>{t("detected")}</th></tr>
              </thead>
              <tbody>
                {data.rows.map((e) => (
                  <tr key={e.id} className="border-b border-border/60 align-top">
                    <td className={td}><Link className="text-accent" href={`/radar/${e.id}`}>#{e.id}</Link></td>
                    <td className={td}>
                      <ClassLabel cls={e.event_class} /> · {e.coin} {e.direction}
                      {e.escalation_level > 0 && <Badge>L{e.escalation_level}</Badge>}
                      {e.liquidation_status !== "none" && <Badge tone="warn">{e.liquidation_status}</Badge>}
                    </td>
                    <td className={td}>
                      <span className="text-xs text-muted">{e.venue}</span> {e.public_label ?? e.display_name ?? shortKey(e.trader_key)}
                    </td>
                    <td className={td}>
                      <GateBadge status={e.gate_status} />
                      {e.blocking_issues.map((b) => <Badge key={b.code} tone="down" title={b.detail}>{b.code}</Badge>)}
                    </td>
                    <td className={`${td} num`}>{e.score === null ? "—" : Number(e.score).toFixed(1)}</td>
                    <td className={td}><StateBadge state={e.state} /> {e.priority && <Badge tone="accent">{e.priority}</Badge>}</td>
                    <td className={`${td} text-xs text-muted`}>{fmtTime(e.detected_at, locale)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}
