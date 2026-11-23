"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Re-renders the page every few seconds while something is being generated (for at most ten minutes). */
export function AutoRefresh({ active, label, seconds = 5 }: { active: boolean; label?: string; seconds?: number }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const started = Date.now();
    const id = setInterval(() => {
      if (Date.now() - started > 600_000) return clearInterval(id);
      router.refresh();
    }, seconds * 1000);
    return () => clearInterval(id);
  }, [active, router, seconds]);
  return active && label ? <span className="animate-pulse text-xs text-warn">{label}</span> : null;
}
