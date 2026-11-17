"use client";

import { useFormStatus } from "react-dom";
import type { ReactNode } from "react";

/** Submit button that shows a pending state while the GET navigation runs. */
export function SubmitButton({ children, pending }: { children: ReactNode; pending: ReactNode }) {
  const { pending: busy } = useFormStatus();
  return (
    <button type="submit" disabled={busy} className="rounded bg-accent px-4 py-1 text-white disabled:opacity-70">
      {busy ? (
        <span className="inline-flex items-center gap-1.5">
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/40 border-t-white" />
          {pending}
        </span>
      ) : (
        children
      )}
    </button>
  );
}
