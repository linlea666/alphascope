"use client";

import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import type { ToastDetail } from "@/lib/bff";

type Item = ToastDetail & { id: number };

/** Bottom-right confirmations for every write made through bff(): green on success, red with the reason on failure. */
export function Toaster() {
  const t = useTranslations("common");
  const [items, setItems] = useState<Item[]>([]);
  useEffect(() => {
    let n = 0;
    const on = (e: Event) => {
      const d = (e as CustomEvent<ToastDetail>).detail;
      const id = ++n;
      setItems((xs) => [...xs.slice(-3), { ...d, id }]);
      setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), d.ok ? 2500 : 6000);
    };
    window.addEventListener("nr-toast", on);
    return () => window.removeEventListener("nr-toast", on);
  }, []);
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2" aria-live="polite">
      {items.map((x) => (
        <div key={x.id} className={`pointer-events-auto rounded border px-3 py-2 text-sm shadow-lg ${x.ok
          ? "border-up/40 bg-panel text-up" : "border-down/40 bg-panel text-down"}`}>
          {x.ok ? `✓ ${x.message ?? t("saved")}` : `✗ ${t("failed")}${x.message ? `：${x.message}` : ""}`}
        </div>
      ))}
    </div>
  );
}
