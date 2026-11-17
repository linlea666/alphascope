/** Small server-rendered visual helpers (pure SVG / CSS, no client JavaScript). */

/** A thin bar proportional to |value| / max next to a number, green or red by sign. */
export function AmountBar({ value, max }: { value: number | null | undefined; max: number }) {
  if (value === null || value === undefined || !max) return null;
  const w = Math.max(2, Math.round((Math.min(Math.abs(value), max) / max) * 48));
  return <span className={`ml-2 inline-block h-1.5 rounded-full align-middle ${value >= 0 ? "bg-up/70" : "bg-down/70"}`} style={{ width: w }} aria-hidden />;
}

/** Ring showing a share (0..1), e.g. how much of the archive queue covers a query. */
export function Ring({ share, size = 34, label }: { share: number; size?: number; label?: string }) {
  const r = 15;
  const c = 2 * Math.PI * r;
  const s = Math.max(0, Math.min(1, share));
  return (
    <svg width={size} height={size} viewBox="0 0 36 36" className="shrink-0" role="img" aria-label={label}>
      <circle cx="18" cy="18" r={r} fill="none" stroke="var(--border)" strokeWidth="4" />
      <circle cx="18" cy="18" r={r} fill="none" stroke="var(--accent)" strokeWidth="4" strokeDasharray={`${c * s} ${c}`} strokeLinecap="round" transform="rotate(-90 18 18)" />
      <text x="18" y="21.5" textAnchor="middle" fontSize="9" fill="var(--text)" className="num">
        {Math.round(s * 100)}
      </text>
    </svg>
  );
}

/** Two needles on one half-dial: share of long traders among significant traders (accent) vs everyone (grey). */
export function Gauge({ a, b, size = 120 }: { a: number; b: number; size?: number }) {
  const needle = (v: number, color: string, len: number, w: number) => {
    const ang = Math.PI * (1 - v);
    return <line x1="50" y1="50" x2={50 + len * Math.cos(ang)} y2={50 - len * Math.sin(ang)} stroke={color} strokeWidth={w} strokeLinecap="round" />;
  };
  const hot = Math.abs(a - b) >= 0.2;
  return (
    <svg width={size} height={size * 0.58} viewBox="0 0 100 58" aria-hidden>
      <path d="M8 50 A42 42 0 0 1 50 8" fill="none" stroke="var(--down)" strokeOpacity="0.45" strokeWidth="7" />
      <path d="M50 8 A42 42 0 0 1 92 50" fill="none" stroke="var(--up)" strokeOpacity="0.45" strokeWidth="7" />
      {hot && <path d="M4 50 A46 46 0 0 1 96 50" fill="none" stroke="var(--warn)" strokeWidth="1.5" strokeDasharray="2 3" />}
      {needle(b, "var(--muted)", 34, 3)}
      {needle(a, "var(--accent)", 40, 3.5)}
      <circle cx="50" cy="50" r="4" fill="var(--text)" />
    </svg>
  );
}
