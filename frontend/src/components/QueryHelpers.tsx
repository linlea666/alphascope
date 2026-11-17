"use client";

import { useEffect, useRef, useState } from "react";
import { Link } from "@/i18n/routing";

/** Buttons that fill the start / end date inputs of the surrounding form. */
export function DateQuick({ startName, endName, options }: { startName: string; endName: string; options: { label: string; start: string; end: string }[] }) {
  const ref = useRef<HTMLSpanElement>(null);
  const set = (start: string, end: string) => {
    const form = ref.current?.closest("form");
    if (!form) return;
    for (const [name, v] of [[startName, start], [endName, end]] as const) {
      const el = form.elements.namedItem(name);
      if (el instanceof HTMLInputElement) el.value = v;
    }
  };
  return (
    <span ref={ref} className="flex flex-wrap items-center gap-1 pb-1">
      {options.map((o) => (
        <button key={o.label} type="button" onClick={() => set(o.start, o.end)} className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted hover:border-accent hover:text-accent">
          {o.label}
        </button>
      ))}
    </span>
  );
}

type Recent = { label: string; query: Record<string, string> };

/** The last few custom queries of a page, kept in this browser only (localStorage). */
export function RecentQueries({ storageKey, current, title }: { storageKey: string; current: Recent | null; title: string }) {
  const [items, setItems] = useState<Recent[]>([]);
  const key = `as_recent_${storageKey}`;
  const currentJson = current ? JSON.stringify(current) : "";
  useEffect(() => {
    let list: Recent[] = [];
    try {
      list = JSON.parse(localStorage.getItem(key) || "[]");
    } catch {
      list = [];
    }
    if (currentJson) {
      const c = JSON.parse(currentJson) as Recent;
      list = [c, ...list.filter((x) => x.label !== c.label)].slice(0, 5);
      try {
        localStorage.setItem(key, JSON.stringify(list));
      } catch {
        /* private mode: keep the in-memory list */
      }
    }
    setItems(list);
  }, [key, currentJson]);
  if (!items.length) return null;
  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs">
      <span className="text-muted">{title}:</span>
      {items.map((r) => (
        <Link key={r.label} href={{ pathname: `/${storageKey}`, query: r.query }} className="rounded-full border border-border px-2 py-0.5 text-muted hover:border-accent hover:text-accent">
          {r.label}
        </Link>
      ))}
    </div>
  );
}

/** 0–100% slider that submits a 0–1 share, with a plain-language reading of the current value. */
export function ShareSlider({ name, defaultValue, label, explain }: { name: string; defaultValue: number; label: string; explain: string }) {
  const [v, setV] = useState(Math.round(defaultValue * 100));
  return (
    <label className="flex flex-col gap-1 text-xs text-muted">
      {label}
      <span className="flex items-center gap-2">
        <input type="range" min={0} max={100} step={5} value={v} onChange={(e) => setV(Number(e.target.value))} className="w-32 accent-[var(--accent)]" />
        <span className="num w-10 text-text">{v}%</span>
      </span>
      <input type="hidden" name={name} value={String(v / 100)} />
      <span className="max-w-[220px] text-[11px] leading-snug">{explain.replace("{p}", `${v}%`)}</span>
    </label>
  );
}
