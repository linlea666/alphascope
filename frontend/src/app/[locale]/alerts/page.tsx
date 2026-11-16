import { getLocale, getTranslations } from "next-intl/server";
import { AddressLink } from "@/components/AddressLink";
import { AlertButton } from "@/components/AlertButton";
import { ApiErrorState } from "@/components/ApiErrorState";
import { Badge, PageHeader, Panel, td, th } from "@/components/ui";
import { api, ApiError, type AlertsOverview, type Notifications, type PositionEvents } from "@/lib/api";
import { fmtNum, fmtTime, fmtUsd } from "@/lib/format";

export default async function AlertsPage() {
  const t = await getTranslations("alerts");
  const locale = await getLocale();
  let subs: AlertsOverview;
  let events: PositionEvents;
  let log: Notifications;
  try {
    [subs, events, log] = await Promise.all([api.alerts(), api.alertEvents(100), api.notifications()]);
  } catch (e) {
    return <ApiErrorState detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const tone = (s: string) => (s === "sent" ? "up" : s === "failed" ? "down" : s === "pending" ? "accent" : "neutral");
  return (
    <div className="space-y-5">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <Panel title={t("subs")}>
        {subs.subscriptions.length ? (
          <div className="-mx-4 -my-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-panel-2">
                <tr>
                  <th className={th}>{t("cols.wallet")}</th>
                  <th className={th}>{t("cols.events")}</th>
                  <th className={th}>{t("cols.filters")}</th>
                  <th className={th}>{t("cols.polled")}</th>
                  <th className={th}>{t("cols.lastEvent")}</th>
                  <th className={th}>{t("cols.status")}</th>
                </tr>
              </thead>
              <tbody>
                {subs.subscriptions.map((s) => (
                  <tr key={s.id} className="border-t border-border/60 align-top">
                    <td className={td}>
                      <span className="flex items-start gap-2">
                        <AlertButton address={s.address} subscribed compact />
                        <AddressLink address={s.address} name={s.name} />
                      </span>
                    </td>
                    <td className={`${td} whitespace-normal text-xs`}>{s.events.map((k) => t(`kinds.${k}`)).join(" · ")}</td>
                    <td className={`${td} text-xs text-muted`}>
                      {s.min_notional > 0 && <div>≥ {fmtUsd(s.min_notional, locale)}</div>}
                      {s.min_change_pct > 0 && <div>≥ {fmtNum(s.min_change_pct, locale)}%</div>}
                      {s.coins.length > 0 && <div>{s.coins.join(", ")}</div>}
                      {s.quiet_start !== null && s.quiet_end !== null && (
                        <div>
                          🌙 {String(s.quiet_start).padStart(2, "0")}:00–{String(s.quiet_end).padStart(2, "0")}:00
                        </div>
                      )}
                    </td>
                    <td className={`${td} num text-xs text-muted`}>
                      {fmtTime(s.last_polled, locale)}
                      {s.last_error && <div className="text-down">{s.last_error.slice(0, 80)}</div>}
                    </td>
                    <td className={`${td} num text-xs text-muted`}>{fmtTime(s.last_event_at, locale)}</td>
                    <td className={td}>
                      <Badge tone={s.enabled ? "up" : "neutral"}>{s.enabled ? t("on") : t("off")}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted">{t("empty")}</p>
        )}
      </Panel>

      <Panel title={t("recent")} hint={t("baseline")}>
        {events.events.length ? (
          <div className="-mx-4 -my-4 max-h-[480px] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-panel-2">
                <tr>
                  <th className={th}>{t("eventCols.time")}</th>
                  <th className={th}>{t("eventCols.wallet")}</th>
                  <th className={th}>{t("eventCols.action")}</th>
                  <th className={th}>{t("eventCols.coin")}</th>
                  <th className={`${th} text-right`}>{t("eventCols.size")}</th>
                  <th className={`${th} text-right`}>{t("eventCols.amount")}</th>
                  <th className={`${th} text-right`}>{t("eventCols.price")}</th>
                  <th className={th}>{t("eventCols.notify")}</th>
                </tr>
              </thead>
              <tbody>
                {events.events.map((e) => (
                  <tr key={e.id} className="border-t border-border/60">
                    <td className={`${td} num text-xs text-muted`}>{fmtTime(e.t, locale)}</td>
                    <td className={td}>
                      <AddressLink address={e.address} name={e.name} star={false} />
                    </td>
                    <td className={td}>
                      <Badge tone={e.szi_after > e.szi_before ? "up" : e.szi_after < e.szi_before ? "down" : "warn"}>{t(`kinds.${e.kind}`)}</Badge>
                    </td>
                    <td className={td}>{e.coin}</td>
                    <td className={`${td} num text-right text-xs`}>
                      {fmtNum(e.szi_before, locale, 4)} → {fmtNum(e.szi_after, locale, 4)}
                    </td>
                    <td className={`${td} num text-right`}>{fmtUsd(e.notional_change, locale)}</td>
                    <td className={`${td} num text-right`}>{e.px === null ? "—" : fmtNum(e.px, locale, 4)}</td>
                    <td className={td}>
                      <Badge tone={tone(e.notify_status)}>{t(`notify.${e.notify_status}`)}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted">{t("noEvents")}</p>
        )}
      </Panel>

      <Panel title={t("log")} hint={t("sentToday", { n: log.sent_today })}>
        {log.notifications.length ? (
          <div className="-mx-4 -my-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-panel-2">
                <tr>
                  <th className={th}>{t("logCols.time")}</th>
                  <th className={th}>{t("logCols.subject")}</th>
                  <th className={th}>{t("logCols.to")}</th>
                  <th className={th}>{t("logCols.status")}</th>
                  <th className={th}>{t("logCols.error")}</th>
                </tr>
              </thead>
              <tbody>
                {log.notifications.map((n) => (
                  <tr key={n.id} className="border-t border-border/60">
                    <td className={`${td} num text-xs text-muted`}>{fmtTime(n.sent_at ?? n.created_at, locale)}</td>
                    <td className={`${td} whitespace-normal text-xs`}>{n.subject}</td>
                    <td className={`${td} text-xs text-muted`}>{n.recipients.join(", ")}</td>
                    <td className={td}>
                      <Badge tone={tone(n.status)}>{t(`notify.${n.status}`)}</Badge>
                    </td>
                    <td className={`${td} max-w-xs truncate text-xs text-down`} title={n.error ?? undefined}>
                      {n.error}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted">—</p>
        )}
      </Panel>
    </div>
  );
}
