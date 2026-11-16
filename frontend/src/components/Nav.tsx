import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/routing";
import { LocaleSwitcher } from "./LocaleSwitcher";
import { ThemeToggle } from "./ThemeToggle";

export async function Nav() {
  const t = await getTranslations("nav");
  const items = [
    { href: "/", label: t("overview") },
    { href: "/rankings", label: t("rankings") },
    { href: "/swings", label: t("swings") },
    { href: "/timing", label: t("timing") },
    { href: "/holders", label: t("holders") },
    { href: "/zones", label: t("zones") },
    { href: "/watchlist", label: t("watchlist") },
    { href: "/alerts", label: t("alerts") },
    { href: "/data-health", label: t("dataHealth") },
    { href: "/settings", label: t("settings") },
  ] as const;
  return (
    <header className="sticky top-0 z-20 border-b border-border bg-bg/90 backdrop-blur">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
        <Link href="/" className="flex items-baseline gap-2">
          <span className="text-lg font-semibold tracking-tight">AlphaScope</span>
          <span className="hidden text-xs text-muted sm:inline">Hyperliquid · {t("tagline")}</span>
        </Link>
        <nav className="flex gap-4 text-sm">
          {items.map((i) => (
            <Link key={i.href} href={i.href} className="text-muted hover:text-text">
              {i.label}
            </Link>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <LocaleSwitcher />
          <ThemeToggle label={t("theme")} />
        </div>
      </div>
    </header>
  );
}
