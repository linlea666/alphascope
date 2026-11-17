"use client";

import { useEffect, useState } from "react";
import type { ToastTone } from "@/lib/toast";

type Item = { id: number; message: string; tone: ToastTone };

export function Toaster() {
  const [items, setItems] = useState<Item[]>([]);
  useEffect(() => {
    let n = 0;
    const on = (e: Event) => {
      const { message, tone } = (e as CustomEvent<{ message: string; tone: ToastTone }>).detail;
      const id = ++n;
      setItems((xs) => [...xs.slice(-2), { id, message, tone }]);
      setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 4200);
    };
    window.addEventListener("as-toast", on);
    return () => window.removeEventListener("as-toast", on);
  }, []);
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex max-w-[calc(100vw-2rem)] flex-col gap-2" aria-live="polite">
      {items.map((t) => (
        <div
          key={t.id}
          className={`as-toast pointer-events-auto flex items-start gap-2 rounded-lg border bg-panel px-3 py-2 text-sm shadow-lg ${t.tone === "ok" ? "border-up/40" : "border-down/40"}`}
        >
          <span className={t.tone === "ok" ? "text-up" : "text-down"}>{t.tone === "ok" ? "✓" : "!"}</span>
          <span>{t.message}</span>
        </div>
      ))}
    </div>
  );
}
