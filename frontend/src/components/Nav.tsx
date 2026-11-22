import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/routing";
import { LocaleSwitcher } from "./LocaleSwitcher";
import { Logo } from "./Logo";
import { NavMenu, type NavEntry } from "./NavMenu";
import { ThemeToggle } from "./ThemeToggle";

export async function Nav() {
  const t = await getTranslations("nav");
  const item = (href: string, key: string) => ({ href, label: t(key), desc: t(`desc.${key}`) });
  // grouped by task: read the market, find people, research a move, act on it, keep the system healthy
  const entries: NavEntry[] = [
    { href: "/", label: t("overview") },
    { href: "/market", label: t("market") },
    {
      label: t("find"),
      items: [item("/copy-board", "copyBoard"), item("/traders", "traders"), item("/compare", "compare")],
    },
    { label: t("research"), items: [item("/replay", "replayLab"), item("/cycles", "cycles")] },
    { label: t("mine"), items: [item("/watchlist", "watchlist"), item("/alerts", "alerts"), item("/sims", "sims")] },
    { label: t("system"), items: [item("/data-health", "dataHealth"), item("/settings", "settings")] },
  ];
  return (
    <header className="sticky top-0 z-20 border-b border-border bg-bg/90 backdrop-blur">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-7 gap-y-2 px-4 py-3">
        <Link href="/" className="flex items-center gap-2">
          <Logo />
          <span className="text-lg font-semibold tracking-tight">AlphaScope</span>
          <span className="hidden text-xs text-muted lg:inline">Hyperliquid · OKX · {t("tagline")}</span>
        </Link>
        <NavMenu entries={entries} />
        <div className="ml-auto flex items-center gap-2">
          <LocaleSwitcher />
          <ThemeToggle label={t("theme")} />
        </div>
      </div>
    </header>
  );
}
