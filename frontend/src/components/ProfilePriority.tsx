"use client";

import { useState } from "react";

export function ProfilePriority({ id, initial, zh }: { id: string; initial: boolean; zh: boolean }) {
  const [pinned, setPinned] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <button
      type="button"
      aria-pressed={pinned}
      disabled={busy}
      className="text-xs text-accent"
      onClick={async () => {
        setBusy(true);
        try {
          const res = await fetch("/api/bff/settings/binance-profiles/priority/" + id, {
            method: "PUT",
            headers: { "content-type": "application/json", "x-alphascope": "1" },
            body: JSON.stringify({ pinned: !pinned }),
          });
          if (!res.ok) throw new Error();
          setPinned((await res.json()).pinned);
          setFailed(false);
        } catch {
          setFailed(true);
        } finally {
          setBusy(false);
        }
      }}
    >
      {failed
        ? zh
          ? "保存失败，重试"
          : "Retry"
        : pinned
          ? zh
            ? "★ 已优先观察"
            : "★ Prioritized"
          : zh
            ? "☆ 优先观察"
            : "☆ Prioritize"}
    </button>
  );
}
