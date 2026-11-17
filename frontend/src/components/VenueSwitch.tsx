"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useTransition } from "react";
import type { VenueFilter } from "@/lib/venue";

/** Exchange filter shared by the research pages; the choice is kept in a cookie so every link and form keeps it. */
export function VenueSwitch({ value, allowAll = true }: { value: VenueFilter; allowAll?: boolean }) {
  const t = useTranslations("venue");
  const router = useRouter();
  const [pending, start] = useTransition();
  const options: VenueFilter[] = allowAll ? ["all", "hyperliquid", "okx"] : ["hyperliquid", "okx"];
  const set = (v: VenueFilter) => {
    document.cookie = `as_venue=${v}; path=/; max-age=31536000; samesite=lax`;
    start(() => router.refresh());
  };
  return (
    <div className={`flex overflow-hidden rounded border border-border text-sm ${pending ? "opacity-60" : ""}`} style={{ width: "fit-content" }}>
      {options.map((v) => (
        <button key={v} type="button" onClick={() => set(v)} className={`px-3 py-1 ${v === value ? "bg-accent text-white" : "text-muted hover:text-text"}`}>
          {t(v)}
        </button>
      ))}
    </div>
  );
}
