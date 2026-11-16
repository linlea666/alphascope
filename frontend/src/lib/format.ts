export function fmtUsd(v: number | null | undefined, locale: string, compact = true): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: "USD",
    notation: compact && Math.abs(v) >= 10_000 ? "compact" : "standard",
    maximumFractionDigits: compact && Math.abs(v) >= 10_000 ? 2 : Math.abs(v) < 1 ? 4 : 0,
  }).format(v);
}

/** Returns are signed (+/-); shares and ratios (signed=false) are not. */
export function fmtPct(v: number | null | undefined, locale: string, digits = 2, signed = true): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat(locale, {
    style: "percent",
    maximumFractionDigits: Math.abs(v) >= 10 ? 0 : digits,
    signDisplay: signed ? "exceptZero" : "auto",
  }).format(v);
}

export function fmtNum(v: number | null | undefined, locale: string, digits = 0): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat(locale, { maximumFractionDigits: digits }).format(v);
}

export function fmtTime(v: string | null | undefined, locale: string, withTime = true): string {
  if (!v) return "—";
  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...(withTime ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}),
    timeZone: "UTC",
  }).format(new Date(v)) + (withTime ? " UTC" : "");
}

export function fmtBytes(v: number, locale: string): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${fmtNum(v, locale, 1)} ${units[i]}`;
}

export function shortAddr(a: string): string {
  return `${a.slice(0, 6)}…${a.slice(-4)}`;
}

export function signClass(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "text-muted";
  return v > 0 ? "text-up" : "text-down";
}

/** "skipped_low_capital_periods:3" -> ["skipped_low_capital_periods", 3] */
export function splitNote(note: string): [string, number | undefined] {
  const [key, n] = note.split(":");
  return [key, n === undefined ? undefined : Number(n)];
}
