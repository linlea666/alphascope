"use client";

import { useTranslations } from "next-intl";
import { useRef, useState, useTransition } from "react";
import { Link } from "@/i18n/routing";
import { bff } from "@/lib/bff";
import type { MarketContributor } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime, fmtUsd, isOkx, shortAddr, signClass } from "@/lib/format";
import { Identicon } from "./Identicon";
import { WatchButton } from "./WatchButton";

type Sort = "notional" | "margin" | "upl" | "liq" | "leverage" | "opened";
type Side = "all" | "long" | "short";
type Order = "asc" | "desc";
const DEFAULT_ORDER: Record<Sort, Order> = { notional: "desc", margin: "desc", upl: "asc", liq: "asc", leverage: "desc", opened: "desc" };
const th = "px-3 py-2 text-left text-xs font-medium text-muted whitespace-nowrap";
const td = "px-3 py-2 whitespace-nowrap";

/** A coin's positions, re-sorted and filtered in place (no page navigation, so the page keeps its scroll position).
 *  Side, sort and direction are kept together and mirrored into the URL so the view can be shared. */
export function ContributorsTable({ coin, query, initial, aliases, watched, locale }: {
  coin: string;
  query: Record<string, string>; // venue, cohort and any entry / liquidation range from the page
  initial: { rows: MarketContributor[]; side: Side; sort: Sort; order: Order };
  aliases: Record<string, string>;
  watched: string[];
  locale: string;
}) {
  const t = useTranslations("market");
  const tg = useTranslations("glossary");
  const [rows, setRows] = useState(initial.rows);
  const [state, setState] = useState({ side: initial.side, sort: initial.sort, order: initial.order });
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const seq = useRef(0);
  const watchedSet = new Set(watched);

  const load = (next: { side: Side; sort: Sort; order: Order }) => {
    setState(next);
    setError(null);
    const n = ++seq.current;
    const params = new URLSearchParams({ ...query, coin, side: next.side, sort: next.sort, order: next.order, limit: "200" });
    start(async () => {
      try {
        const d = await bff<{ rows: MarketContributor[] }>(`market/contributors?${params}`);
        if (n === seq.current) setRows(d.rows); // ignore answers to clicks that were overtaken
      } catch (e) {
        if (n === seq.current) setError(e instanceof Error ? e.message : String(e));
      }
    });
    // keep the address bar shareable without a navigation (no scroll, no reload)
    const url = new URL(window.location.href);
    const set = (k: string, v: string, dflt: string) => (v === dflt ? url.searchParams.delete(k) : url.searchParams.set(k, v));
    set("cside", next.side, "all");
    set("csort", next.sort, "notional");
    set("corder", next.order, DEFAULT_ORDER[next.sort]);
    window.history.replaceState(window.history.state, "", url);
  };
  const sortBy = (k: Sort) =>
    load({ ...state, sort: k, order: state.sort === k ? (state.order === "asc" ? "desc" : "asc") : DEFAULT_ORDER[k] });

  const fmtPx = (v: number | null | undefined) => (v === null || v === undefined ? "—" : fmtNum(v, locale, v >= 100 ? 1 : v >= 1 ? 4 : 6));
  const pill = (active: boolean) => `rounded border px-2 py-0.5 ${active ? "border-accent text-accent" : "border-border text-muted hover:text-text"}`;
  const head = (k: Sort, label: string, right = true, title?: string) => (
    <th className={`${th} ${right ? "text-right" : ""}`}>
      <button type="button" onClick={() => sortBy(k)} title={title} className={`hover:text-text ${state.sort === k ? "text-accent" : ""} ${title ? "cursor-help border-b border-dotted border-muted/60" : ""}`}>
        {label}
        {state.sort === k ? (state.order === "desc" ? " ↓" : " ↑") : ""}
      </button>
    </th>
  );

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-1 text-xs">
        {(["all", "long", "short"] as const).map((s) => (
          <button key={s} type="button" onClick={() => load({ ...state, side: s })} className={pill(s === state.side)}>
            {t(`sides.${s}`)}
          </button>
        ))}
        <span className="ml-2 text-muted">{t("sortHint")}</span>
        {pending && <span className="ml-2 h-3 w-3 animate-spin rounded-full border-2 border-muted/40 border-t-accent" />}
        {error && <span className="ml-2 text-down">{error}</span>}
      </div>
      {rows.length ? (
        <div className={`-mx-4 -mb-4 max-h-[560px] overflow-auto transition-opacity ${pending ? "opacity-60" : ""}`}>
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 bg-panel-2">
              <tr>
                <th className={th}>{t("cols.trader")}</th>
                {head("notional", t("cols.notional"))}
                {head("margin", t("cols.margin"), true, tg("margin.text"))}
                <th className={`${th} text-right`}>{t("cols.entry")}</th>
                {head("leverage", t("cols.lev"))}
                {head("liq", t("cols.liq"))}
                {head("upl", t("cols.upl"))}
                {head("opened", t("cols.opened"), false)}
                <th className={th}>{t("cols.observed")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const alias = aliases[r.address] ?? (isOkx(r.address) ? r.display_name : null);
                return (
                  <tr key={r.address} className="border-t border-border/60 hover:bg-panel-2">
                    <td className={td}>
                      <span className="flex items-start gap-2">
                        <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] leading-none ${r.szi > 0 ? "border-up/40 text-up" : "border-down/40 text-down"}`}>{r.szi > 0 ? "▲" : "▼"}</span>
                        <WatchButton address={r.address} watched={watchedSet.has(r.address)} compact />
                        <Link href={`/trader/${r.address}`} className="group inline-flex items-start gap-1.5">
                          <span className="pt-px">
                            <Identicon address={r.address} />
                          </span>
                          <span className="inline-flex flex-col leading-tight">
                            <span className={`text-accent group-hover:underline ${alias ? "" : "num"}`}>{alias ?? shortAddr(r.address)}</span>
                            <span className="num text-[11px] text-muted">{alias ? shortAddr(r.address) : r.display_name}</span>
                          </span>
                        </Link>
                      </span>
                    </td>
                    <td className={`${td} num text-right`}>{fmtUsd(r.notional_usd, locale)}</td>
                    <td className={`${td} num text-right`}>{fmtUsd(r.margin, locale)}</td>
                    <td className={`${td} num text-right`}>{fmtPx(r.entry_px)}</td>
                    <td className={`${td} num text-right`}>
                      {r.leverage ? `${r.leverage}×` : "—"}
                      {r.margin_mode && <div className="text-[11px] text-muted">{t(`modes.${r.margin_mode === "isolated" ? "isolated" : "cross"}`)}</div>}
                    </td>
                    <td className={`${td} num text-right text-muted`}>
                      {fmtPx(r.liq_px)}
                      {r.liq_dist !== null && r.liq_dist !== undefined && <div className={`text-[11px] ${r.liq_dist <= 0.02 ? "font-semibold text-down" : r.liq_dist <= 0.05 ? "text-warn" : ""}`}>{fmtPct(r.liq_dist, locale, 1, false)}</div>}
                    </td>
                    <td className={`${td} num text-right ${signClass(r.upl)}`}>
                      {fmtUsd(r.upl, locale)}
                      {r.roe !== null && r.roe !== undefined && <div className="text-[11px]">{fmtPct(r.roe, locale, 0)}</div>}
                    </td>
                    <td className={`${td} num text-xs text-muted`}>
                      {r.opened_at ? fmtTime(r.opened_at, locale) : r.opened_at_source === "window" && r.opened_after ? t("openedAfter", { t: fmtTime(r.opened_after, locale) }) : t("openedUnknown")}
                    </td>
                    <td className={`${td} num text-xs text-muted`}>{fmtTime(r.observed_at, locale)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="py-6 text-center text-sm text-muted">{t("empty")}</p>
      )}
    </div>
  );
}
