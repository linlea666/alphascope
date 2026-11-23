import { getLocale, getTranslations } from "next-intl/server";
import { factName, word } from "@/lib/glossary";
import type { Packet } from "@/lib/types";
import { Badge } from "./ui";

export async function FactsTable({ packet, compact = false }: { packet: Packet; compact?: boolean }) {
  const [t, tf, tc, locale] = await Promise.all([getTranslations("events"), getTranslations("factNames"),
    getTranslations("constraintCodes"), getLocale()]);
  const lang = locale === "zh" ? "zh" : "en";
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1">
        {packet.constraints.map((c) => <Badge key={c.code} tone="warn" title={c.text}>{word(tc, c.code)}</Badge>)}
      </div>
      {!compact && (
        <ul className="space-y-1 text-xs text-muted">
          {packet.constraints.map((c) => <li key={c.code}>· {lang === "zh" ? word(tc, c.code, c.text) : c.text}</li>)}
        </ul>
      )}
      <table className="w-full text-sm">
        <tbody>
          {packet.facts.map((f) => (
            <tr key={f.id} className="border-b border-border/60 align-top">
              <td className="py-1.5 pr-2 text-xs text-muted" title={f.id}>{factName(tf, f.id, f.label)}</td>
              <td className="py-1.5">
                <div>{(f.display[lang] ?? [String(f.value)])[0]}</div>
                {!compact && (
                  <details className="text-[11px] text-muted">
                    <summary className="cursor-pointer">{t("factDetail")}</summary>
                    <div className="font-mono">{f.id} · {f.kind}{f.label ? ` · ${f.label}` : ""}{f.derivation ? ` · ${f.derivation}` : ""} · {f.source}
                      {f.coverage ? ` · ${JSON.stringify(f.coverage)}` : ""}</div>
                  </details>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[11px] text-muted">{t("packetVersion")} v{packet.version} · {packet.subject.trader_ref} · {packet.subject.venue}</p>
    </div>
  );
}
