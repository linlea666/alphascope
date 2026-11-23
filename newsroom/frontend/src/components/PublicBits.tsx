import { getTranslations } from "next-intl/server";
import { Badge } from "./ui";

export async function PositionNow({ status }: { status: string | null }) {
  const t = await getTranslations("public");
  const key = (status ?? "unknown") as "open" | "closed" | "flipped" | "unknown";
  return <Badge tone={key === "open" ? "accent" : key === "closed" ? "neutral" : "warn"}>{t(["open", "closed", "flipped"].includes(key) ? key : "unknown")}</Badge>;
}
