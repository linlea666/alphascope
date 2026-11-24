/** Times are stored in UTC; zh pages show Beijing time and en pages show UTC, always labelled. */
export function zoneFor(locale: string, tz?: string | null): string {
  return tz ?? (locale === "zh" ? "Asia/Shanghai" : "UTC");
}

export function fmtTime(iso: string | null | undefined, locale: string, tz?: string | null): string {
  if (!iso) return "—";
  const zone = zoneFor(locale, tz);
  const s = new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-GB", {
    timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(iso));
  const label = zone === "UTC" ? "UTC" : zone === "Asia/Shanghai" ? (locale === "zh" ? "北京" : "UTC+8") : zone;
  return `${s} ${label}`;
}

export function fmtUsd(v: number | string | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  const a = Math.abs(n);
  const s = a >= 1e9 ? `${(a / 1e9).toFixed(2)}B` : a >= 1e6 ? `${(a / 1e6).toFixed(2)}M` : a >= 1e3 ? `${(a / 1e3).toFixed(1)}K` : a.toFixed(2);
  return `${n < 0 ? "-" : ""}$${s}`;
}

export function shortKey(key: string | null | undefined): string {
  if (!key) return "—";
  if (key.startsWith("okx:")) return `OKX·${key.slice(-6)}`;
  if (key.startsWith("binance:")) return `币安·${key.slice(8)}`;
  return `${key.slice(0, 6)}…${key.slice(-4)}`;
}
