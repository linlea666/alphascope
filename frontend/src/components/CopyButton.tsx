"use client";

import { useState } from "react";

export function CopyButton({ text, label, done }: { text: string; label: string; done: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          /* clipboard may be blocked */
        }
      }}
      className="rounded border border-border px-2 py-0.5 text-xs text-muted hover:text-text"
    >
      {copied ? done : label}
    </button>
  );
}
