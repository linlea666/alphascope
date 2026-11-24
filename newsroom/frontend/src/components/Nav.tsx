import { getLocale, getTranslations } from "next-intl/server";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import type { Me } from "@/lib/types";
import { LogoutButton } from "./LogoutButton";

// what a newcomer needs every day; the rest is one click away under "more"
const LINKS = [["/", "dashboard"], ["/publish", "publish"], ["/stars", "stars"], ["/accounts", "accounts"], ["/settings", "settings"]] as const;
const MORE = [["/radar", "radar"], ["/assignments", "assignments"], ["/calendar", "calendar"], ["/columns", "columns"],
  ["/stories", "stories"], ["/experiments", "experiments"], ["/reports", "reports"], ["/publications", "publications"],
  ["/library", "library"], ["/drafts", "drafts"], ["/p", "public"]] as const;

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
          <details className="relative">
            <summary className="cursor-pointer list-none text-muted hover:text-text">{t("more")} ▾</summary>
            <div className="absolute left-0 z-20 mt-2 grid w-44 gap-1 rounded border border-border bg-panel p-2 shadow-lg">
              {MORE.map(([href, key]) => (
                <Link key={key} href={href} className="rounded px-2 py-1 text-muted hover:bg-bg hover:text-text">{t(key)}</Link>
              ))}
            </div>
          </details>
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
