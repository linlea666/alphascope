import { getLocale, getTranslations } from "next-intl/server";
import { ClassLabel, StateBadge } from "@/components/badges";
import { Badge, Empty, PageHeader, Panel, button, input, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import type { Account } from "@/lib/types";

type Row = { id: number; news_event_id: number; handle: string; language: string; status: string; updated_at: string; event_class: string;
  coin: string; headline: string | null; angle: string | null; origin: string | null; guard_status: string | null; version_no: number | null };

export default async function LibraryPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const [t, tc, locale, accounts] = await Promise.all([getTranslations("library"), getTranslations("common"), getLocale(), nrGet<Account[]>("accounts")]);
  const data = await nrGet<{ total: number; rows: Row[] }>("articles", {
    q: sp.q, account_id: sp.account_id, status: sp.status, language: sp.language, event_class: sp.event_class,
  });
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <form className="mb-4 flex flex-wrap gap-2 text-sm">
        <input name="q" defaultValue={sp.q ?? ""} placeholder={t("search")} className={`${input} w-72`} />
        <select name="account_id" defaultValue={sp.account_id ?? ""} className={`${input} w-44`}>
          <option value="">{tc("account")}</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>@{a.handle}</option>)}
        </select>
        <select name="language" defaultValue={sp.language ?? ""} className={`${input} w-24`}>
          <option value="">{tc("language")}</option><option value="zh">zh</option><option value="en">en</option>
        </select>
        <input name="status" defaultValue={sp.status ?? ""} placeholder={tc("status")} className={`${input} w-32`} />
        <button className={button}>{tc("filter")}</button>
      </form>
      <Panel title={String(data.total)}>
        {data.rows.length === 0 ? <Empty>—</Empty> : (
          <table className="w-full text-sm">
            <thead><tr><th className={th}>#</th><th className={th}>{tc("account")}</th><th className={th}>headline</th><th className={th}>event</th><th className={th}>{tc("status")}</th><th className={th}>{tc("time")}</th></tr></thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.id} className="border-b border-border/60 align-top">
                  <td className={td}><Link className="text-accent" href={`/desk/${r.id}`}>#{r.id}</Link></td>
                  <td className={td}>@{r.handle} · {r.language}</td>
                  <td className={`${td} max-w-lg whitespace-normal`}>{r.headline}<div className="text-xs text-muted">{r.angle}</div></td>
                  <td className={td}><ClassLabel cls={r.event_class} /> · {r.coin}</td>
                  <td className={td}><StateBadge state={r.status} /> {r.origin && <Badge>{r.origin} v{r.version_no}</Badge>} {r.guard_status && <Badge tone={r.guard_status === "pass" ? "up" : "down"}>{r.guard_status}</Badge>}</td>
                  <td className={`${td} text-xs text-muted`}>{fmtTime(r.updated_at, locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  );
}
