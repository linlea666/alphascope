import { getLocale, getTranslations } from "next-intl/server";
import { ApiErrorState } from "@/components/ApiErrorState";
import { SettingsForm } from "@/components/SettingsForm";
import { PageHeader, Panel } from "@/components/ui";
import { api, ApiError, type AppSettings } from "@/lib/api";
import { fmtNum, fmtTime } from "@/lib/format";

export default async function SettingsPage() {
  const t = await getTranslations("settings");
  const locale = await getLocale();
  let d: AppSettings;
  try {
    d = await api.settings();
  } catch (e) {
    return <ApiErrorState detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const st = d.live_status;
  return (
    <div className="space-y-5">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <Panel title={t("status")}>
        {st.cycle_started_at ? (
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm md:grid-cols-4">
            <dt className="text-muted">{t("lastCycle")}</dt>
            <dd className="num">{fmtTime(st.cycle_started_at, locale)}</dd>
            <dt className="text-muted">{t("wallets")}</dt>
            <dd className="num">{fmtNum(st.wallets ?? 0, locale)}</dd>
            <dt className="text-muted">{t("cycleSec")}</dt>
            <dd className="num">{fmtNum(st.cycle_seconds ?? 0, locale, 1)}s</dd>
            <dt className="text-muted">{t("events")}</dt>
            <dd className="num">{fmtNum(st.events ?? 0, locale)}</dd>
            {st.error && (
              <>
                <dt className="text-down">{t("error")}</dt>
                <dd className="col-span-3 text-xs text-down">{st.error}</dd>
              </>
            )}
          </dl>
        ) : (
          <p className="text-sm text-muted">{t("notRunning")}</p>
        )}
      </Panel>
      <SettingsForm initial={d} />
    </div>
  );
}
