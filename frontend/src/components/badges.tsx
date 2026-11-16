import { getTranslations } from "next-intl/server";
import type { Reliability } from "@/lib/api";
import { Badge } from "./ui";

const SEVERE = new Set(["zero_account_value", "extreme_roi", "small_denominator_roi", "aggregate_account", "passive_pnl"]);

export async function FlagBadges({ flags, hide = [] }: { flags: string[]; hide?: string[] }) {
  const t = await getTranslations();
  const shown = flags.filter((f) => !hide.includes(f));
  if (!shown.length) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {shown.map((f) => (
        <Badge key={f} tone={SEVERE.has(f) ? "warn" : "neutral"} title={t.has(`flagDesc.${f}`) ? t(`flagDesc.${f}`) : f}>
          {t.has(`flags.${f}`) ? t(`flags.${f}`) : f}
        </Badge>
      ))}
    </span>
  );
}

export async function TierBadge({ tier }: { tier: string | null }) {
  const t = await getTranslations();
  if (!tier) return <span className="text-muted">—</span>;
  return (
    <Badge tone={tier === "B" ? "accent" : "neutral"} title={t(`tierDesc.${tier}`)}>
      {t(`tiers.${tier}`)}
    </Badge>
  );
}

export async function ReliabilityBadge({ value }: { value: Reliability | null }) {
  const t = await getTranslations("reliability");
  if (!value) return null;
  const tone = value === "high" ? "up" : value === "medium" ? "neutral" : "warn";
  return (
    <Badge tone={tone} title={t("label")}>
      {t(value)}
    </Badge>
  );
}
