import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/routing";
import { LocaleSwitcher } from "./LocaleSwitcher";
import { ThemeToggle } from "./ThemeToggle";

export async function Nav() {
  const t = await getTranslations("nav");
  const items = [
    { href: "/", label: t("overview") },
    { href: "/discover", label: t("discover") },
    { href: "/market", label: t("market") },
    { href: "/rankings", label: t("rankings") },
    { href: "/okx", label: t("okx") },
  ] as const;
  // research tools grouped under one menu (CSS-only dropdown: works without client JavaScript)
  const research = [
    { href: "/swings", label: t("swings") },
    { href: "/timing", label: t("timing") },
    { href: "/holders", label: t("holders") },
    { href: "/zones", label: t("zones") },
  ] as const;
  const tail = [
    { href: "/watchlist", label: t("watchlist") },
    { href: "/alerts", label: t("alerts") },
    { href: "/sims", label: t("sims") },
    { href: "/data-health", label: t("dataHealth") },
    { href: "/settings", label: t("settings") },
  ] as const;
  const link = "text-muted hover:text-text";
  return (
    <header className="sticky top-0 z-20 border-b border-border bg-bg/90 backdrop-blur">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
        <Link href="/" className="flex items-baseline gap-2">
          <span className="text-lg font-semibold tracking-tight">AlphaScope</span>
          <span className="hidden text-xs text-muted sm:inline">Hyperliquid · OKX · {t("tagline")}</span>
        </Link>
        <nav className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
          {items.map((i) => (
            <Link key={i.href} href={i.href} className={link}>
              {i.label}
            </Link>
          ))}
          <div className="group relative">
            <button type="button" className={`${link} cursor-default`}>
              {t("research")} ▾
            </button>
            <div className="invisible absolute left-0 top-full z-30 min-w-[140px] pt-2 opacity-0 transition group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100">
              <div className="rounded-lg border border-border bg-panel py-1 shadow-lg">
                {research.map((i) => (
                  <Link key={i.href} href={i.href} className="block px-3 py-1.5 text-muted hover:bg-panel-2 hover:text-text">
                    {i.label}
                  </Link>
                ))}
              </div>
            </div>
          </div>
          {tail.map((i) => (
            <Link key={i.href} href={i.href} className={link}>
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
