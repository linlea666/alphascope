import { getLocale, getTranslations } from "next-intl/server";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import type { Me } from "@/lib/types";
import { LogoutButton } from "./LogoutButton";

const LINKS = [
  ["/", "dashboard"], ["/publish", "publish"], ["/radar", "radar"], ["/drafts", "drafts"], ["/experiments", "experiments"], ["/assignments", "assignments"], ["/calendar", "calendar"],
  ["/columns", "columns"], ["/library", "library"], ["/stories", "stories"], ["/accounts", "accounts"],
  ["/publications", "publications"], ["/settings", "settings"], ["/p", "public"],
] as const;

export async function Nav() {
  const t = await getTranslations("nav");
  const locale = await getLocale();
  const [me, settings] = await Promise.all([nrGet<Me>("auth/me"), nrGet<{ emergency_stop: { active: boolean } }>("settings")]);
  return (
    <header className="border-b border-border bg-panel">
      {settings.emergency_stop.active && (
        <div className="bg-down px-4 py-1 text-center text-xs font-medium text-white">{t("emergency")}</div>
      )}
      <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3">
        <span className="font-semibold tracking-tight">AlphaScope Newsroom</span>
        <nav className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {LINKS.map(([href, key]) => (
            <Link key={key} href={href} className="text-muted hover:text-text">{t(key)}</Link>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3 text-xs text-muted">
          <Link href="/" locale={locale === "zh" ? "en" : "zh"} className="hover:text-text">{locale === "zh" ? "EN" : "中文"}</Link>
          <span>{me.username} · {me.roles.join(", ")}</span>
          <LogoutButton label={t("logout")} />
        </div>
      </div>
    </header>
  );
}
