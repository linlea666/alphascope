"use client";

export default function ErrorPage({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div className="rounded border border-down/40 bg-down/10 p-4 text-sm">
      <p className="mb-2 font-medium">{error.message || "Error"}</p>
      <button className="text-accent" onClick={reset}>↻</button>
    </div>
  );
}
