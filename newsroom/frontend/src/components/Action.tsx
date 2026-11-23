"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition, type ReactNode } from "react";
import { bff } from "@/lib/bff";
import { button as buttonCls, primary as primaryCls } from "./ui";

/** A button that calls the API through the proxy and refreshes the page; errors are shown inline. */
export function Action({ path, body, method = "POST", children, confirm, prompt, tone = "default", onDone }: {
  path: string;
  body?: unknown;
  method?: "POST" | "PUT" | "PATCH";
  children: ReactNode;
  confirm?: string;
  prompt?: { label: string; field: string };
  tone?: "default" | "primary" | "danger";
  onDone?: (data: unknown) => void;
}) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const cls = tone === "primary" ? primaryCls : tone === "danger" ? `${buttonCls} text-down` : buttonCls;
  return (
    <span className="inline-flex flex-col gap-1">
      <button
        className={cls}
        disabled={pending}
        onClick={() => {
          if (confirm && !window.confirm(confirm)) return;
          let payload = body;
          if (prompt) {
            const v = window.prompt(prompt.label);
            if (v === null) return;
            payload = { ...(body as object), [prompt.field]: v };
          }
          setError(null);
          start(async () => {
            try {
              const data = await bff(path, method, payload ?? {});
              onDone?.(data);
              router.refresh();
            } catch (e) {
              setError((e as Error).message);
            }
          });
        }}
      >
        {children}
      </button>
      {error && <span className="max-w-xs text-xs text-down">{error}</span>}
    </span>
  );
}
