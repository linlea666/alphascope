"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { FieldForm, type Field } from "./FieldForm";
import { input } from "./ui";

type Preset = { label: string; values: Record<string, unknown> };
const PRESETS: Record<string, Preset> = {
  custom: { label: "Custom", values: {} },
  deepseek: { label: "DeepSeek 官方 · deepseek-chat", values: { name: "deepseek-chat", protocol: "openai_chat",
    base_url: "https://api.deepseek.com", model: "deepseek-chat", json_mode: "json_object", auth_header: "bearer" } },
  deepseek_reasoner: { label: "DeepSeek 官方 · deepseek-reasoner", values: { name: "deepseek-reasoner", protocol: "openai_chat",
    base_url: "https://api.deepseek.com", model: "deepseek-reasoner", json_mode: "none", auth_header: "bearer", timeout_s: 180 } },
  acm_codex: { label: "aicodemirror · Codex (Responses API)", values: { name: "aicodemirror-codex", protocol: "openai_responses",
    base_url: "https://api.aicodemirror.ai/api/codex/backend-api/codex", json_mode: "none", auth_header: "bearer",
    extra_body: { store: false, stream: true } } },
  acm_claude: { label: "aicodemirror · Claude (Messages API)", values: { name: "aicodemirror-claude", protocol: "anthropic_messages",
    base_url: "https://api.aicodemirror.ai/api/claudecode", json_mode: "none", auth_header: "bearer" } },
  openai: { label: "OpenAI 官方 (Chat Completions)", values: { name: "openai", protocol: "openai_chat",
    base_url: "https://api.openai.com/v1", json_mode: "json_object", auth_header: "bearer" } },
};

/** "Add model" with presets that fill protocol and URL; the key is the only thing left to type. */
export function ModelPresetForm({ fields }: { fields: Field[] }) {
  const t = useTranslations("settingsX");
  const [preset, setPreset] = useState("deepseek");
  const values = PRESETS[preset].values;
  const filled = fields.map((f) => (f.name in values ? { ...f, value: values[f.name] } : f));
  return (
    <div className="space-y-3">
      <label className="block text-xs text-muted">{t("preset")}
        <select className={input} value={preset} onChange={(e) => setPreset(e.target.value)}>
          {Object.entries(PRESETS).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
        </select>
      </label>
      <p className="text-xs text-muted">{t("presetHint")}</p>
      <FieldForm key={preset} fields={filled} path="ai/models" />
    </div>
  );
}
