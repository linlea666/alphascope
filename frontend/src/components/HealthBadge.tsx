import { getTranslations } from "next-intl/server";
import type { HealthStatus } from "@/lib/api";
import { Badge } from "./ui";

const TONE: Record<HealthStatus, "up" | "down" | "warn" | "neutral" | "accent"> = {
  stable: "neutral",
  hot: "up",
  declining: "down",
  drifting: "warn",
  dormant: "neutral",
  insufficient: "neutral",
};

/** Trader health from the nightly 30-day vs earlier comparison; nothing is shown when unknown. */
export async function HealthBadge({ status, compact = false }: { status: HealthStatus | null | undefined; compact?: boolean }) {
  if (!status || status === "insufficient" || (compact && status === "stable")) return null;
  const t = await getTranslations("health");
  return (
    <Badge tone={TONE[status]} title={t(`hint.${status}`)}>
      {t(`icon.${status}`)} {t(`status.${status}`)}
    </Badge>
  );
}
