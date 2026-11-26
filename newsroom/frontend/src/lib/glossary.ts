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

/** A fact id in words. Numbered book lines ("book.pos2.upl_usd") read "#2 · Unrealized P&L". */
export function factName(t: T, id: string, fallback?: string | null): string {
  const pos = /^book\.pos(\d+)\.(.+)$/.exec(id);
  if (pos) {
    const field = { coin: "change.size_after", side: "liquidation.status", notional_usd: "position.notional_usd",
      leverage: "position.leverage", entry_px: "position.entry_px", mark_px: "position.mark_px",
      upl_usd: "position.unrealized_pnl_usd" }[pos[2]];
    const label = pos[2] === "coin" ? "Token" : pos[2] === "side" ? "Side" : field ? word(t, field) : pos[2];
    return `#${pos[1]} · ${label}`;
  }
  const opp = /^book\.opposite_(.+)_usd$/.exec(id);
  if (opp) return `${word(t, "book.opposite")} · ${opp[1]}`;
  return word(t, id, fallback ?? id);
}

/** "thread.2" → "Thread #2" style labels for guard findings. */
export function partName(t: T, part: string): string {
  const m = /^thread\.(\d+)$/.exec(part);
  return m ? `${t.has("thread") ? t("thread") : "thread"} ${m[1]}` : word(t, part);
}

/** "ai.agent_bind" → "AI · 绑定模型" */
export function auditName(groups: T, actions: T, action: string): string {
  const [g, ...rest] = action.split(".");
  const a = rest.join(".");
  return [word(groups, g), a ? word(actions, a) : ""].filter(Boolean).join(" · ");
}

/** A publishing platform's name as the desk says it. */
export function platformName(p: string | null | undefined): string {
  return { okx_square: "OKX 广场", binance_square: "币安广场", x: "X" }[p ?? "x"] ?? p ?? "X";
}

// the one English house voice without a Chinese twin
const STYLE_ZH_EXTRA: Record<string, string> = { en_market_desk: "全球市场版" };

/** A style as a Chinese reader recognises it: Chinese styles by their name, English ones by their Chinese twin's
 * name with the English name after it, e.g. 鲸鱼警报（英文版 · Whale alert）. */
export function styleLabel(styles: { key: string; name: string }[], key: string | null | undefined, zhName?: string | null): string {
  if (!key) return "";
  const name = styles.find((s) => s.key === key)?.name ?? key;
  if (!key.startsWith("en_")) return `${name}（中文）`;
  const twin = zhName ?? styles.find((s) => s.key === `zh_${key.slice(3)}`)?.name ?? STYLE_ZH_EXTRA[key];
  return twin ? `${twin}（英文版 · ${name}）` : `${name}（英文版）`;
}
