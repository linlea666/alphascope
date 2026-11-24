import { getLocale, getTranslations } from "next-intl/server";
import { Action } from "@/components/Action";
import { FieldForm } from "@/components/FieldForm";
import { StarSearch } from "@/components/StarSearch";
import { Badge, Empty, PageHeader, Panel, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import { fmtTime } from "@/lib/format";

type Star = { id: number; trader_key: string; venue: string; name: string; note: string; active: boolean;
  min_notional_usd: string | null; equity: string | null; snapshot_at: string | null;
  stats_30d: { roi?: number; pnl?: number; win_rate?: number; max_drawdown?: number; trades?: number } | null;
  tags: { tag: string; why: string }[] | null; stage: string | null; prev_stage: string | null; stage_since: string | null;
  editor_note: string | null; published: number; ready: number;
  positions: { symbol: string; side: string; size: string; entry_px: string | null; upl: string | null; lever: string | null; notional: string | null }[];
  closes: { symbol: string; side: string; closed_at: string; pnl: string | null }[] };
type D = { stars: Star[]; relay: { last_ok_at: string | null; last_error: string | null; last_error_at: string | null } | null;
  relay_configured: boolean };

const usd = (v: string | number | null | undefined) => (v === null || v === undefined ? "—" : `$${Math.round(Number(v)).toLocaleString()}`);

/** The star list: traders the desk follows move by move, each with its persona, positions, results and posts. */
export default async function StarsPage() {
  const [t, locale, d] = await Promise.all([getTranslations("stars"), getLocale(), nrGet<D>("stars")]);
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <Panel title={t("relayTitle")}>
        {!d.relay_configured ? <p className="text-sm text-warn">{t("relayOff")}</p> : (
          <p className="text-sm">
            {d.relay?.last_ok_at ? t("relayOk", { at: fmtTime(d.relay.last_ok_at, locale) }) : t("relayNever")}
            {d.relay?.last_error && d.relay.last_error_at && (!d.relay.last_ok_at || d.relay.last_error_at > d.relay.last_ok_at) && (
              <span className="ml-2 text-down">{t("relayError", { e: d.relay.last_error })}</span>)}
          </p>
        )}
      </Panel>
      <div className="my-5"><Panel title={t("addTitle")} hint={t("addHint")}><StarSearch /></Panel></div>
      {d.stars.length === 0 ? <Empty>{t("empty")}</Empty> : (
        <div className="grid gap-5 xl:grid-cols-2">
          {d.stars.map((s) => (
            <Panel key={s.id} title={<span className="flex flex-wrap items-center gap-2">
              {s.name} <Badge tone="accent">{t(`venue.${s.venue}`)}</Badge>
              {s.stage && <Badge tone={["fall", "crisis"].includes(s.stage) ? "down" : ["peak", "streak", "comeback", "rising"].includes(s.stage) ? "up" : "neutral"}>
                {t(`stage.${s.stage}`)}{s.prev_stage && s.prev_stage !== s.stage ? ` ← ${t(`stage.${s.prev_stage}`)}` : ""}</Badge>}
              {!s.active && <Badge>{t("paused")}</Badge>}
            </span>}>
              <div className="space-y-3 text-sm">
                {s.tags && s.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1">{s.tags.map((x) => <Badge key={x.tag} title={x.why}>{x.tag}</Badge>)}</div>
                )}
                {s.stats_30d && (
                  <div className="text-xs text-muted">{t("stats30", { roi: `${Number(s.stats_30d.roi ?? 0).toFixed(1)}%`,
                    pnl: usd(s.stats_30d.pnl), win: `${Number(s.stats_30d.win_rate ?? 0).toFixed(1)}%`,
                    dd: `${Number(s.stats_30d.max_drawdown ?? 0).toFixed(1)}%`, trades: s.stats_30d.trades ?? "—" })}
                    {s.equity && ` · ${t("equity", { v: usd(s.equity) })}`}</div>
                )}
                <div>
                  <div className="mb-1 text-xs text-muted">{t("positions")}</div>
                  {s.positions.length === 0 ? <span className="text-xs text-muted">{s.venue === "binance" ? t("noPositions") : t("okxPositionsHint")}</span> : (
                    <table className="w-full text-xs"><thead><tr>{["coin", "side", "notional", "lever", "upl"].map((k) => <th key={k} className={th}>{t(`col.${k}`)}</th>)}</tr></thead>
                      <tbody>{s.positions.map((p, i) => (
                        <tr key={i} className="border-b border-border/60">
                          <td className={td}>{p.symbol}</td><td className={td}>{t(`side.${p.side}`)}</td>
                          <td className={`${td} num`}>{usd(p.notional)}</td><td className={`${td} num`}>{p.lever ? `${Number(p.lever)}x` : "—"}</td>
                          <td className={`${td} num ${Number(p.upl) >= 0 ? "text-up" : "text-down"}`}>{usd(p.upl)}</td>
                        </tr>))}</tbody></table>
                  )}
                </div>
                {s.closes.length > 0 && (
                  <div>
                    <div className="mb-1 text-xs text-muted">{t("closes")}</div>
                    <div className="flex flex-wrap gap-2 text-xs">{s.closes.map((c, i) => (
                      <span key={i} className={Number(c.pnl) >= 0 ? "text-up" : "text-down"}>{c.symbol} {t(`side.${c.side}`)} {usd(c.pnl)}</span>))}</div>
                  </div>
                )}
                <div className="text-xs text-muted">
                  {t("posts", { published: s.published, ready: s.ready })}{" "}
                  {s.ready > 0 && <Link className="text-accent" href="/publish?tab=ready">{t("goPublish")}</Link>}
                </div>
                <details>
                  <summary className="cursor-pointer text-xs text-accent">{t("edit")}</summary>
                  <div className="mt-2 space-y-3">
                    <FieldForm path={`stars/${s.id}`} method="PATCH" fields={[
                      { name: "name", label: t("f.name"), value: s.name, required: true },
                      { name: "note", label: t("f.note"), value: s.note, wide: true },
                      { name: "min_notional_usd", label: t("f.min"), type: "number", value: s.min_notional_usd ?? undefined, help: t("f.minHelp") },
                    ]} />
                    {s.stage && (
                      <FieldForm path={`personas/${encodeURIComponent(s.trader_key)}`} method="PUT" fields={[
                        { name: "editor_note", label: t("f.persona"), type: "textarea", value: s.editor_note ?? "", wide: true, help: t("f.personaHelp") },
                      ]} />
                    )}
                    <div className="flex gap-2">
                      <Action path={`stars/${s.id}`} method="PATCH" body={{ active: !s.active }}>{s.active ? t("pause") : t("resume")}</Action>
                      <Action path={`stars/${s.id}`} method="DELETE" tone="danger" confirm={t("removeConfirm", { name: s.name })}>{t("remove")}</Action>
                    </div>
                  </div>
                </details>
              </div>
            </Panel>
          ))}
        </div>
      )}
    </>
  );
}
