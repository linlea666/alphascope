/** Deterministic 5×5 mirrored pattern from the address, so a trader is recognisable without reading the address.
 *  Pure SVG (no network), the same in every table, card and email-free view. */
function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

export function Identicon({ address, size = 18 }: { address: string; size?: number }) {
  let h = hash(address.toLowerCase());
  const hue = h % 360;
  const cells: [number, number][] = [];
  for (let y = 0; y < 5; y++) {
    for (let x = 0; x < 3; x++) {
      h = Math.imul(h ^ (h >>> 15), 2246822519) >>> 0;
      if (h & 1) {
        cells.push([x, y]);
        if (x < 2) cells.push([4 - x, y]);
      }
    }
  }
  return (
    <svg width={size} height={size} viewBox="0 0 5 5" className="shrink-0 rounded-[4px]" aria-hidden style={{ background: `hsl(${hue} 45% 18%)` }}>
      {cells.map(([x, y]) => (
        <rect key={`${x}-${y}`} x={x} y={y} width={1} height={1} fill={`hsl(${hue} 70% 62%)`} />
      ))}
    </svg>
  );
}
