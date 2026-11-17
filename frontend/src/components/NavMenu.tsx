"use client";

import { Link, usePathname } from "@/i18n/routing";

export type NavItem = { href: string; label: string; desc?: string };
export type NavEntry = NavItem | { label: string; items: NavItem[] };

const isActive = (path: string, href: string) => (href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`));

/** Top navigation: plain links and CSS-hover dropdowns (also open on focus), with the current page highlighted. */
export function NavMenu({ entries }: { entries: NavEntry[] }) {
  const path = usePathname();
  const on = "text-text after:absolute after:inset-x-0 after:-bottom-[13px] after:h-0.5 after:rounded after:bg-accent";
  return (
    <nav className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm">
      {entries.map((e) =>
        "items" in e ? (
          <div key={e.label} className="group relative">
            <button type="button" className={`relative cursor-default ${e.items.some((i) => isActive(path, i.href)) ? on : "text-muted hover:text-text"}`}>
              {e.label} <span className="text-[10px]">▾</span>
            </button>
            <div className="invisible absolute left-0 top-full z-30 min-w-[240px] pt-3 opacity-0 transition group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100">
              <div className="rounded-lg border border-border bg-panel py-1 shadow-lg">
                {e.items.map((i) => (
                  <Link key={i.href} href={i.href} className={`block px-3 py-2 hover:bg-panel-2 ${isActive(path, i.href) ? "bg-panel-2" : ""}`}>
                    <span className={isActive(path, i.href) ? "text-accent" : "text-text"}>{i.label}</span>
                    {i.desc && <span className="mt-0.5 block text-[11px] leading-snug text-muted">{i.desc}</span>}
                  </Link>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <Link key={e.href} href={e.href} className={`relative ${isActive(path, e.href) ? on : "text-muted hover:text-text"}`}>
            {e.label}
          </Link>
        ),
      )}
    </nav>
  );
}
