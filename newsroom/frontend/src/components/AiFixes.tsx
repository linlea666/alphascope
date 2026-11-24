import { getTranslations } from "next-intl/server";
import { nrGetOptional } from "@/lib/api";
import { named } from "@/lib/glossary";
import { Action } from "./Action";

type Fix =
  | { kind: "prompts_behind"; agents: string[] }
  | { kind: "replace_model"; model: { id: number; name: string }; reason: string; primary_in: string[]; fallback_in: string[];
      suggest: { id: number; name: string } | null };

/** What keeps failing and the one click that fixes it: shown where failures are, so nobody has to work out which
 * settings page holds the cure. */
export async function AiFixes() {
  const [t, tr, td] = await Promise.all([getTranslations("fixes"), getTranslations("roles"), getTranslations("diagnosis")]);
  const d = await nrGetOptional<{ fixes: Fix[] }>("ai/fixes");
  if (!d || d.fixes.length === 0) return null;
  const roles = (keys: string[]) => keys.map((k) => named(tr, k).name).join("、");
  return (
    <div className="mb-4 space-y-2 rounded border border-warn/40 bg-warn/10 px-4 py-3 text-sm">
      <div className="font-semibold text-warn">{t("title")}</div>
      {d.fixes.map((f, i) => f.kind === "prompts_behind" ? (
        <div key={i} className="flex flex-wrap items-center gap-3">
          <span>{t("promptsBehind", { roles: roles(f.agents) })}</span>
          <Action path="ai/agents/prompt-version" body={{ agent_keys: f.agents, prompt_version: null }} tone="primary">
            {t("switchLatest")}</Action>
        </div>
      ) : (
        <div key={i} className="flex flex-wrap items-center gap-3">
          <span>
            {t("badModel", { model: f.model.name, why: f.reason === "test_failed" ? t("testFailed") : td(`${f.reason}.what`) })}
            {f.primary_in.length > 0 && <> {t("primaryIn", { roles: roles(f.primary_in) })}</>}
            {f.fallback_in.length > 0 && <> {t("fallbackIn", { roles: roles(f.fallback_in) })}</>}
          </span>
          {f.suggest ? (
            <Action path="ai/agents/replace-model" body={{ from_model_id: f.model.id, to_model_id: f.suggest.id }} tone="primary"
              confirm={t("replaceConfirm", { from: f.model.name, to: f.suggest.name })}>
              {t("replace", { to: f.suggest.name })}</Action>
          ) : f.primary_in.length === 0 ? (
            <Action path="ai/agents/replace-model" body={{ from_model_id: f.model.id, to_model_id: null }}>{t("dropFallback")}</Action>
          ) : <span className="text-xs text-muted">{t("noSpare")}</span>}
        </div>
      ))}
    </div>
  );
}
