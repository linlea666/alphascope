import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/routing";
import { nrGetOptional } from "@/lib/api";

type Steps = { steps: { key: string; done: boolean; tab: string | null }[]; done: boolean };
const HREF: Record<string, string> = { account: "/accounts", trial: "/radar", publish: "/publish?tab=ready" };

/** The getting-started checklist: what is done, what is next and where to do it. Gone once everything is done. */
export async function Onboarding() {
  const t = await getTranslations("onboarding");
  const d = await nrGetOptional<Steps>("onboarding");
  if (!d || d.done) return null;
  const next = d.steps.find((s) => !s.done)?.key;
  return (
    <div className="mb-5 rounded border border-accent/40 bg-accent/5 p-4">
      <div className="mb-1 font-semibold">{t("title")}</div>
      <p className="mb-3 text-xs text-muted">{t("intro")}</p>
      <ol className="space-y-2 text-sm">
        {d.steps.map((s, i) => (
          <li key={s.key} className={`flex flex-wrap items-baseline gap-2 ${s.done ? "text-muted" : ""}`}>
            <span className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs ${s.done ? "bg-up text-white" : s.key === next ? "bg-accent text-white" : "border border-border"}`}>
              {s.done ? "✓" : i + 1}
            </span>
            <span className={s.key === next ? "font-medium" : ""}>{t(`${s.key}.title`)}</span>
            {!s.done && <span className="text-xs text-muted">{t(`${s.key}.how`)}</span>}
            {!s.done && (
              <Link className="text-xs text-accent" href={s.tab ? `/settings?tab=${s.tab}` : HREF[s.key] ?? "/"}>{t("go")} →</Link>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
