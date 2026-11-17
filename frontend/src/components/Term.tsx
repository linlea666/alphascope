import { getTranslations } from "next-intl/server";
import type { ReactNode } from "react";

/** A metric name with a plain-language explanation on hover / focus (glossary.<k> in messages). */
export async function Term({ k, children, align = "left" }: { k: string; children?: ReactNode; align?: "left" | "right" }) {
  const t = await getTranslations("glossary");
  if (!t.has(k)) return <>{children}</>;
  return (
    <span tabIndex={0} className="group relative inline-flex cursor-help items-center border-b border-dotted border-muted/60 outline-none">
      {children ?? t(`${k}.label`)}
      <span
        role="tooltip"
        className={`invisible absolute top-full z-40 mt-1 w-64 whitespace-normal rounded-md border border-border bg-panel p-2.5 text-left text-xs font-normal normal-case leading-relaxed text-text opacity-0 shadow-lg transition group-hover:visible group-hover:opacity-100 group-focus:visible group-focus:opacity-100 ${align === "right" ? "right-0" : "left-0"}`}
      >
        <span className="block">{t(`${k}.text`)}</span>
        {t.has(`${k}.example`) && <span className="mt-1 block text-muted">{t(`${k}.example`)}</span>}
      </span>
    </span>
  );
}
