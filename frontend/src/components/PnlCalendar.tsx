import { fmtUsd } from "@/lib/format";

type Day = { day: string; pnl: number };

function tone(pnl: number, max: number): string {
  if (!max || pnl === 0) return "var(--panel-2)";
  const share = Math.round(15 + 85 * Math.min(1, Math.abs(pnl) / max));
  return `color-mix(in srgb, var(${pnl > 0 ? "--up" : "--down"}) ${share}%, var(--panel-2))`;
}

/** Month blocks of 7-column day grids (Monday first), plus weekly and monthly totals. */
export function PnlCalendar({ days, locale, labels }: { days: Day[]; locale: string; labels: { week: string; weekdays: string[]; empty: string } }) {
  if (!days.length) return <p className="py-8 text-center text-sm text-muted">{labels.empty}</p>;
  const byDay = new Map(days.map((d) => [d.day, d.pnl]));
  const max = Math.max(...days.map((d) => Math.abs(d.pnl)));
  const months = [...new Set(days.map((d) => d.day.slice(0, 7)))].sort().slice(-6);
  const weeks = new Map<string, number>();
  const monthTotals = new Map<string, number>();
  for (const d of days) {
    const dt = new Date(`${d.day}T00:00:00Z`);
    const monday = new Date(dt.getTime() - ((dt.getUTCDay() + 6) % 7) * 86400000).toISOString().slice(0, 10);
    weeks.set(monday, (weeks.get(monday) ?? 0) + d.pnl);
    monthTotals.set(d.day.slice(0, 7), (monthTotals.get(d.day.slice(0, 7)) ?? 0) + d.pnl);
  }
  const sign = (v: number) => (v > 0 ? "text-up" : v < 0 ? "text-down" : "text-muted");

  return (
    <div className="flex flex-wrap gap-6">
      <div className="flex flex-wrap gap-5">
        {months.map((m) => {
          const [y, mo] = m.split("-").map(Number);
          const first = new Date(Date.UTC(y, mo - 1, 1));
          const lead = (first.getUTCDay() + 6) % 7;
          const n = new Date(Date.UTC(y, mo, 0)).getUTCDate();
          return (
            <div key={m}>
              <div className="mb-1 flex justify-between text-xs">
                <span className="text-muted">{m}</span>
                <span className={`num ${sign(monthTotals.get(m) ?? 0)}`}>{fmtUsd(monthTotals.get(m) ?? 0, locale)}</span>
              </div>
              <div className="grid grid-cols-7 gap-0.5 text-[10px]">
                {labels.weekdays.map((w) => (
                  <div key={w} className="text-center text-muted">
                    {w}
                  </div>
                ))}
                {Array.from({ length: lead }, (_, i) => (
                  <div key={`x${i}`} />
                ))}
                {Array.from({ length: n }, (_, i) => {
                  const day = `${m}-${String(i + 1).padStart(2, "0")}`;
                  const pnl = byDay.get(day);
                  return (
                    <div
                      key={day}
                      title={pnl === undefined ? day : `${day}  ${fmtUsd(pnl, locale, false)}`}
                      className="flex h-7 w-7 items-center justify-center rounded-sm text-muted"
                      style={{ background: pnl === undefined ? "transparent" : tone(pnl, max) }}
                    >
                      {i + 1}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
      <table className="self-start text-xs">
        <thead>
          <tr>
            <th className="px-2 py-1 text-left font-medium text-muted">{labels.week}</th>
            <th className="px-2 py-1 text-right font-medium text-muted">PnL</th>
          </tr>
        </thead>
        <tbody>
          {[...weeks.entries()]
            .sort((a, b) => (a[0] < b[0] ? 1 : -1))
            .slice(0, 8)
            .map(([w, v]) => (
              <tr key={w} className="border-t border-border/60">
                <td className="num px-2 py-1 text-muted">{w}</td>
                <td className={`num px-2 py-1 text-right ${sign(v)}`}>{fmtUsd(v, locale)}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}
