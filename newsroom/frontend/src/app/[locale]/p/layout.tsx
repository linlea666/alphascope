import { getTranslations } from "next-intl/server";
import type { ReactNode } from "react";
import { Link } from "@/i18n/routing";

export default async function PublicLayout({ children }: { children: ReactNode }) {
  const t = await getTranslations("public");
  return (
    <>
      <header className="border-b border-border bg-panel">
        <div className="mx-auto flex max-w-4xl items-center gap-4 px-4 py-3">
          <Link href="/p" className="font-semibold tracking-tight">{t("title")}</Link>
        </div>
      </header>
      <main className="mx-auto max-w-4xl px-4 py-6">{children}</main>
    </>
  );
}
