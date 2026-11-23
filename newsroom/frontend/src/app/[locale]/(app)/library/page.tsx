import { getLocale, getTranslations } from "next-intl/server";
import { ClassLabel, StateBadge } from "@/components/badges";
import { BulkTable } from "@/components/BulkTable";
import { Badge, Empty, PageHeader, Panel, button, input, td, th } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { nrGet } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import type { Account } from "@/lib/types";

type Row = { id: number; news_event_id: number; handle: string; platform: string; language: string; status: string; updated_at: string;
  event_class: string; coin: string; headline: string | null; angle: string | null; origin: string | null; guard_status: string | null;
  version_no: number | null; model: string | null; source: string | null; deleted_at: string | null; deleted_reason: string | null; versions: number };

export default async function LibraryPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const trash = sp.trash === "1";
  const [t, tm, tc, locale, accounts] = await Promise.all([getTranslations("library"), getTranslations("manage"),
    getTranslations("common"), getLocale(), nrGet<Account[]>("accounts")]);
  const data = await nrGet<{ total: number; rows: Row[] }>("articles", {
    q: sp.q, account_id: sp.account_id, status: sp.status, language: sp.language, origin: sp.origin, guard: sp.guard,
    trial: sp.trial === "1" ? true : sp.trial === "0" ? false : undefined, trash,
  });
  const rows = data.rows.map((r) => (
    <>
      <td className={td}><Link className="text-accent" href={`/desk/${r.id}`}>#{r.id}</Link></td>
      <td className={td}>{r.platform === "okx_square" ? "OKX 广场" : "X"} @{r.handle} · {r.language}</td>
      <td className={`${td} max-w-lg whitespace-normal`}>{r.headline || <span className="text-muted">—</span>}
        <div className="text-xs text-muted">{r.angle}</div>
        {r.deleted_at && <div className="text-xs text-down">{tm("deleted")} {fmtTime(r.deleted_at, locale)} {r.deleted_reason}</div>}</td>
      <td className={td}><ClassLabel cls={r.event_class} /> · {r.coin}</td>
      <td className={td}>
        <StateBadge state={r.status} /> {r.source === "trial" && <Badge tone="warn">trial</Badge>}
        {r.origin && <Badge>{r.origin}{r.model ? ` · ${r.model}` : ""}</Badge>}
        {r.guard_status && <Badge tone={r.guard_status === "pass" ? "up" : "down"}>{r.guard_status}</Badge>}
        <div className="text-xs text-muted">{tm("versions")} {r.versions}</div>
      </td>
      <td className={`${td} text-xs text-muted`}>{fmtTime(r.updated_at, locale)}</td>
    </>
  ));
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <Link className="text-sm text-accent" href={trash ? "/library" : "/library?trash=1"}>{trash ? `← ${t("title")}` : `🗑 ${tm("trashView")}`}</Link>
      </PageHeader>
      <form className="mb-4 flex flex-wrap gap-2 text-sm">
        {trash && <input type="hidden" name="trash" value="1" />}
        <input name="q" defaultValue={sp.q ?? ""} placeholder={t("search")} className={`${input} w-64`} />
        <select name="account_id" defaultValue={sp.account_id ?? ""} className={`${input} w-44`}>
          <option value="">{tc("account")}</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>@{a.handle}</option>)}
        </select>
        <select name="origin" defaultValue={sp.origin ?? ""} className={`${input} w-28`}>
          <option value="">{tm("origin")}</option><option value="ai">AI</option><option value="human">human</option>
        </select>
        <select name="guard" defaultValue={sp.guard ?? ""} className={`${input} w-28`}>
          <option value="">guard</option><option value="pass">pass</option><option value="warn">warn</option><option value="fail">fail</option>
        </select>
        <select name="trial" defaultValue={sp.trial ?? ""} className={`${input} w-32`}>
          <option value="">{tm("any")}</option><option value="1">{tm("trialOnly")}</option><option value="0">{tm("realOnly")}</option>
        </select>
        <input name="status" defaultValue={sp.status ?? ""} placeholder={tc("status")} className={`${input} w-28`} />
        <select name="language" defaultValue={sp.language ?? ""} className={`${input} w-20`}>
          <option value="">{tc("language")}</option><option value="zh">zh</option><option value="en">en</option>
        </select>
        <button className={button}>{tc("filter")}</button>
      </form>
      <Panel title={String(data.total)}>
        {data.rows.length === 0 ? <Empty>—</Empty> : (
          <BulkTable ids={data.rows.map((r) => r.id)} trashView={trash} rows={rows}
            head={<><th className={th}>#</th><th className={th}>{tc("account")}</th><th className={th}>headline</th><th className={th}>event</th>
              <th className={th}>{tc("status")}</th><th className={th}>{tc("time")}</th></>} />
        )}
      </Panel>
    </>
  );
}
