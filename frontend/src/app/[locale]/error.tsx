"use client";

import { useTranslations } from "next-intl";

export default function ErrorPage({ error, reset }: { error: Error; reset: () => void }) {
  const t = useTranslations("common");
  return (
    <div className="py-24 text-center">
      <h1 className="text-lg font-semibold text-down">{t("apiError")}</h1>
      <p className="mt-2 text-sm text-muted">{t("apiErrorHint")}</p>
      <p className="num mt-3 text-xs text-muted">{error.message}</p>
      <button type="button" onClick={reset} className="mt-4 rounded border border-border px-3 py-1 text-sm hover:text-text">
        {t("retry")}
      </button>
    </div>
  );
}
