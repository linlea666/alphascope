/** The scope mark: a crosshair over a rising line — "aiming at traders who catch the move". */
export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden className="shrink-0">
      <circle cx="12" cy="12" r="9" stroke="var(--accent)" strokeWidth="1.8" />
      <path d="M12 1.5v4M12 18.5v4M1.5 12h4M18.5 12h4" stroke="var(--accent)" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M6.5 15l3.2-3.4 2.4 2 5-5.2" stroke="var(--up)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
