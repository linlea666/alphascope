"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

/** Thin bar at the top while a same-site navigation is under way. The previous page stays on screen (no full-page
 *  loading placeholder), so filtering and sorting do not collapse the page or lose the scroll position. */
export function NavProgress() {
  const pathname = usePathname();
  const search = useSearchParams();
  const [active, setActive] = useState(false);
  useEffect(() => setActive(false), [pathname, search]);
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = (e.target as Element | null)?.closest?.("a");
      if (!a || !a.href || a.target === "_blank" || a.hasAttribute("download")) return;
      const url = new URL(a.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname && url.search === window.location.search) return; // hash only
      setActive(true);
    };
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, []);
  if (!active) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 top-0 z-50 h-0.5 overflow-hidden" aria-hidden>
      <div className="as-progress h-full w-1/3 bg-accent" />
    </div>
  );
}
