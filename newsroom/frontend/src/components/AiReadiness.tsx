import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/routing";
import { nrGetOptional } from "@/lib/api";
import { named } from "@/lib/glossary";

export type Readiness = { enabled: boolean; models: { id: number }[]; tested_ok: number[]; core_unbound: string[]; unbound: string[] };

/** The one reason AI work is not running, shown wherever someone would wonder why nothing is being written. */
export async function AiReadiness({ status }: { status?: Readiness | null }) {
  const [t, tr] = await Promise.all([getTranslations("settingsX"), getTranslations("roles")]);
  const r = status ?? (await nrGetOptional<Readiness>("ai/status"));
  if (!r) return null;
  const [msg, tab] = !r.enabled ? [t("aiOff"), "rules"]
    : r.models.length === 0 ? [t("noModelYet"), "ai"]
    : r.core_unbound.length > 0 ? [t("unbound", { agents: r.core_unbound.map((k) => named(tr, k).name).join("、") }), "agents"]
    : r.tested_ok.length === 0 ? [t("noTested"), "ai"] : [null, null];
  if (!msg) return null;
  return (
    <div className="mb-4 rounded border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-warn">
      {msg} <Link className="underline" href={`/settings?tab=${tab}`}>{t("fixHere")} →</Link>
    </div>
  );
}
