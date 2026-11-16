import { getLocale, getTranslations } from "next-intl/server";
import type { Freshness as F } from "@/lib/api";
import { fmtTime } from "@/lib/format";

export async function Freshness({ value }: { value: F }) {
  const t = await getTranslations("common");
  const locale = await getLocale();
  const item = (label: string, v: string | null) => (
    <span>
      {label}: <span className="num text-text">{v ? fmtTime(v, locale) : t("never")}</span>
    </span>
  );
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
      {item(t("snapshotAt"), value.leaderboard_snapshot_at)}
      {item(t("leaderboardChecked"), value.leaderboard_checked_at)}
      {item(t("enrichmentAt"), value.enrichment_at)}
    </div>
  );
}
