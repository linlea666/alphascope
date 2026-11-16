"use client";

import { useState } from "react";

/** navigator.clipboard only exists in secure contexts (HTTPS / localhost); over plain http://IP fall back to
 *  a hidden textarea + execCommand("copy"), which browsers still allow inside a click handler. */
async function copyText(text: string): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* fall through to the legacy path */
    }
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.top = "-1000px";
  document.body.appendChild(area);
  area.select();
  area.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(area);
  return ok;
}

export function CopyButton({ text, label, done, failed }: { text: string; label: string; done: string; failed?: string }) {
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");
  return (
    <button
      type="button"
      onClick={async () => {
        const ok = await copyText(text);
        setState(ok ? "done" : "failed");
        setTimeout(() => setState("idle"), 1800);
      }}
      className="rounded border border-border px-2 py-0.5 text-xs text-muted hover:text-text"
    >
      {state === "done" ? done : state === "failed" ? (failed ?? label) : label}
    </button>
  );
}
