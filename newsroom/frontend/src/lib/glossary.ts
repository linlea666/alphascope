/** Human words for internal codes. Messages can't hold dotted keys (next-intl nests on "."), so codes are stored with
 * "." → "__"; unknown codes fall back to what the caller passes (usually the code itself, or the backend's label). */

type T = ((key: string, values?: Record<string, string | number>) => string) & { has: (key: string) => boolean };

const k = (code: string) => code.replace(/\./g, "__");

export function word(t: T, code: string | null | undefined, fallback?: string | null): string {
  if (!code) return fallback ?? "";
  return t.has(k(code)) ? t(k(code)) : fallback ?? code;
}

/** For namespaces whose entries are {name, desc}. */
export function named(t: T, code: string | null | undefined): { name: string; desc: string | null } {
  if (!code) return { name: "", desc: null };
  return t.has(`${k(code)}.name`) ? { name: t(`${k(code)}.name`), desc: t(`${k(code)}.desc`) } : { name: code, desc: null };
}

/** "reporter_breaking@v4.zh" → { role: "reporter_breaking", version: 4, language: "zh" } */
export function parsePromptRef(ref: string | null | undefined) {
  const m = /^([a-z_]+)@v(\d+)(?:\.(zh|en))?$/.exec(ref ?? "");
  return m ? { role: m[1], version: Number(m[2]), language: m[3] ?? null } : null;
}
