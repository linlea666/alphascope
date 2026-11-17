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

/** Tiny line of a series (e.g. a backtest's equity), green if it ends above its start. */
export function Sparkline({ values, width = 90, height = 26 }: { values: number[]; width?: number; height?: number }) {
  if (values.length < 2) return null;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * width},${height - 2 - ((v - lo) / span) * (height - 4)}`).join(" ");
  const up = values[values.length - 1] >= values[0];
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden className="inline-block align-middle">
      <polyline points={pts} fill="none" stroke={up ? "var(--up)" : "var(--down)"} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

/** Returns kept at increasing copy delays, as small bars (negative bars point down). */
export function DelayBars({ curve, labels }: { curve: [number, number | null][]; labels: string[] }) {
  const vals = curve.map(([, v]) => v ?? 0);
  const max = Math.max(0.0001, ...vals.map(Math.abs));
  return (
    <span className="inline-flex items-center gap-[3px]" title={curve.map(([, v], i) => `${labels[i]}: ${v === null ? "—" : `${(v * 100).toFixed(1)}%`}`).join("  ")}>
      {curve.map(([d, v]) => {
        const h = Math.max(2, Math.round((Math.abs(v ?? 0) / max) * 12));
        return (
          <span key={d} className="flex h-6 w-1.5 flex-col justify-center">
            <span className={`w-1.5 rounded-sm ${(v ?? 0) >= 0 ? "self-end bg-up/80" : "bg-down/80"}`} style={{ height: h, marginTop: (v ?? 0) >= 0 ? 12 - h : 12 }} />
          </span>
        );
      })}
    </span>
  );
}
