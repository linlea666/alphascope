import { getTranslations } from "next-intl/server";
import { ViewTabs } from "@/components/ViewTabs";
import DiscoverPage from "../discover/page";
import OkxPage from "../okx/page";
import RankingsPage from "../rankings/page";
import TimingPage from "../timing/page";

// The former /discover, /rankings, /timing and /okx pages; their old addresses redirect here (middleware.ts).
const VIEWS = { discover: DiscoverPage, rankings: RankingsPage, timing: TimingPage, okx: OkxPage } as const;
type View = keyof typeof VIEWS;

export default async function TradersPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const t = await getTranslations("nav");
  const view: View = sp.view && sp.view in VIEWS ? (sp.view as View) : "discover";
  // each former page takes its own query type; they only read the keys they know
  const Page = VIEWS[view] as (props: { searchParams: Promise<Record<string, string | undefined>> }) => Promise<React.ReactNode>;
  return (
    <div className="space-y-4">
      <ViewTabs pathname="/traders" active={view} views={(Object.keys(VIEWS) as View[]).map((key) => ({ key, label: t(key) }))} />
      <Page searchParams={Promise.resolve(sp)} />
    </div>
  );
}
