"use client";

import { useLocale } from "next-intl";
import { useSearchParams } from "next/navigation";
import { usePathname, useRouter } from "@/i18n/routing";

export function LocaleSwitcher() {
  const locale = useLocale();
  const pathname = usePathname();
  const router = useRouter();
  const search = useSearchParams();
  const other = locale === "zh" ? "en" : "zh";
  return (
    <button
      type="button"
      onClick={() => {
        const qs = search.toString();
        router.replace(qs ? `${pathname}?${qs}` : pathname, { locale: other });
      }}
      className="rounded border border-border px-2 py-1 text-xs text-muted hover:text-text"
    >
      {other === "en" ? "EN" : "中文"}
    </button>
  );
}
