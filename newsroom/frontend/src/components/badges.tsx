import { getTranslations } from "next-intl/server";
import { Badge } from "./ui";

const STATE_TONE: Record<string, "neutral" | "warn" | "up" | "down" | "accent"> = {
  blocked: "down", candidate: "accent", evaluated: "accent", assigned: "accent", covered: "up", dismissed: "neutral",
  declined: "neutral", draft_ready: "accent", in_review: "warn", approved: "up", published: "up", stale: "warn",
  killed: "neutral", retracted: "down", changes_requested: "warn", proposed: "warn", accepted: "up", rejected: "neutral",
  open: "warn",
};

export async function StateBadge({ state }: { state: string }) {
  const [t, tw] = await Promise.all([getTranslations("states"), getTranslations("statusWords")]);
  return <Badge tone={STATE_TONE[state] ?? "neutral"}>{t.has(state) ? t(state) : tw.has(state) ? tw(state) : state}</Badge>;
}

export async function ClassLabel({ cls }: { cls: string }) {
  const [t, tc] = await Promise.all([getTranslations("classes"), getTranslations("columnNames")]);
  const col = cls.startsWith("column:") ? cls.slice(7) : null;
  return <>{col ? (tc.has(col) ? tc(col) : col) : t.has(cls) ? t(cls) : cls}</>;
}

export async function GateBadge({ status }: { status: string }) {
  const t = await getTranslations("statusWords");
  return <Badge tone={status === "pass" ? "up" : status === "blocked" ? "down" : "neutral"}>{t.has(status) ? t(status) : status}</Badge>;
}

/** A gate block in words, the code kept in the tooltip. */
export async function GateCodes({ issues }: { issues: { code: string; detail: string }[] }) {
  const t = await getTranslations("gateCodes");
  return <>{issues.map((b) => <Badge key={b.code} tone="down" title={`${b.code}: ${b.detail}`}>{t.has(b.code) ? t(b.code) : b.code}</Badge>)}</>;
}

export function GuardBadge({ status, label }: { status: string; label: string }) {
  return <Badge tone={status === "pass" ? "up" : status === "warn" ? "warn" : "down"}>{label}</Badge>;
}
