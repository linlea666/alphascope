import { getLocale, getTranslations } from "next-intl/server";
import { ApiErrorState } from "@/components/ApiErrorState";
import { BinanceProfileSettings } from "@/components/BinanceProfileSettings";
import type { ProfileSettings } from "@/lib/binanceProfiles";
import { get } from "@/lib/api";
import { SettingsForm } from "@/components/SettingsForm";
import { PageHeader, Panel } from "@/components/ui";
import { api, ApiError, type AppSettings } from "@/lib/api";
import { fmtNum, fmtTime } from "@/lib/format";

export default async function SettingsPage({searchParams}: {searchParams: Promise<{profile?: string}>}) {
  const {profile} = await searchParams;
  const t = await getTranslations("settings");
  const locale = await getLocale();
  let d: AppSettings;
  try {
    d = await api.settings();
  } catch (e) {
    return <ApiErrorState status={e instanceof ApiError ? e.status : undefined} detail={e instanceof ApiError ? e.message : undefined} />;
  }
  const profiles = await get<ProfileSettings>("/api/v1/settings/binance-profiles").catch(() => null);
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
      <section id="binance-profiles" className="scroll-mt-20"><Panel title={locale === "zh" ? "币安公开持仓采集" : "Binance shared positions"}>
        {profiles ? <BinanceProfileSettings initial={profiles} candidate={profile && /^[0-9]{19}$/.test(profile) ? profile : undefined} zh={locale === "zh"} /> : <p>{locale === "zh" ? "设置暂不可用" : "Settings unavailable"}</p>}
      </Panel></section>
      <SettingsForm initial={d} />
    </div>
  );
}
