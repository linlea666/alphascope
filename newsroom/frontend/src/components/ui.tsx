import type { ReactNode } from "react";

export function Panel({ title, hint, children, actions }: { title?: ReactNode; hint?: ReactNode; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="rounded-lg border border-border bg-panel">
      {(title || actions) && (
        <div className="flex flex-wrap items-start justify-between gap-2 border-b border-border px-4 py-3">
          <div>
            {title && <h2 className="text-sm font-semibold">{title}</h2>}
            {hint && <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">{hint}</p>}
          </div>
          {actions}
        </div>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub }: { label: ReactNode; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-panel px-4 py-3">
      <div className="text-xs text-muted">{label}</div>
      <div className="num mt-1 text-xl font-semibold">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}

export function Badge({ children, tone = "neutral", title }: { children: ReactNode; tone?: "neutral" | "warn" | "up" | "down" | "accent"; title?: string }) {
  const tones = {
    neutral: "border-border text-muted",
    warn: "border-warn/40 text-warn",
    up: "border-up/40 text-up",
    down: "border-down/40 text-down",
    accent: "border-accent/40 text-accent",
  };
  return (
    <span title={title} className={`inline-flex items-center whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] leading-none ${tones[tone]}`}>
      {children}
    </span>
  );
}

export function PageHeader({ title, subtitle, children }: { title: ReactNode; subtitle?: ReactNode; children?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm leading-relaxed text-muted">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}

export const th = "px-3 py-2 text-left text-xs font-medium text-muted whitespace-nowrap";
export const td = "px-3 py-2 whitespace-nowrap";

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded border border-dashed border-border px-4 py-6 text-center text-sm text-muted">{children}</div>;
}

export function KV({ k, v }: { k: ReactNode; v: ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-b border-border/60 py-1.5 text-sm last:border-0">
      <span className="text-muted">{k}</span>
      <span className="text-right">{v}</span>
    </div>
  );
}

export const input = "w-full rounded border border-border bg-panel-2 px-2 py-1.5 text-sm outline-none focus:border-accent";
export const button = "rounded border border-border bg-panel-2 px-3 py-1.5 text-sm hover:border-accent disabled:opacity-50";
export const primary = "rounded bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50";
