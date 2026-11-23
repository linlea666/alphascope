import { getTranslations } from "next-intl/server";
import { Badge } from "./ui";

const STATE_TONE: Record<string, "neutral" | "warn" | "up" | "down" | "accent"> = {
  blocked: "down", candidate: "accent", evaluated: "accent", assigned: "accent", covered: "up", dismissed: "neutral",
  declined: "neutral", draft_ready: "accent", in_review: "warn", approved: "up", published: "up", stale: "warn",
  killed: "neutral", retracted: "down", changes_requested: "warn", proposed: "warn", accepted: "up", rejected: "neutral",
  open: "warn",
};

export async function StateBadge({ state }: { state: string }) {
  const t = await getTranslations("states");
  return <Badge tone={STATE_TONE[state] ?? "neutral"}>{t.has(state) ? t(state) : state}</Badge>;
}

export async function ClassLabel({ cls }: { cls: string }) {
  const t = await getTranslations("classes");
  return <>{t.has(cls) ? t(cls) : cls}</>;
}

export function GateBadge({ status }: { status: string }) {
  return <Badge tone={status === "pass" ? "up" : status === "blocked" ? "down" : "neutral"}>{status}</Badge>;
}

export function GuardBadge({ status, label }: { status: string; label: string }) {
  return <Badge tone={status === "pass" ? "up" : status === "warn" ? "warn" : "down"}>{label}</Badge>;
}
