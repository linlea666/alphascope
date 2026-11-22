import { getTranslations } from "next-intl/server";
import { ViewTabs } from "@/components/ViewTabs";
import HoldersPage from "../holders/page";
import LegsPage from "../legs/page";
import SwingsPage from "../swings/page";
import ZonesPage from "../zones/page";

// The former /legs, /zones, /holders and /swings pages; their old addresses redirect here (middleware.ts).
const VIEWS = { legs: LegsPage, zones: ZonesPage, holders: HoldersPage, swings: SwingsPage } as const;
type View = keyof typeof VIEWS;

export default async function CyclesPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const t = await getTranslations("nav");
  const view: View = sp.view && sp.view in VIEWS ? (sp.view as View) : "legs";
  const Page = VIEWS[view] as (props: { searchParams: Promise<Record<string, string | undefined>> }) => Promise<React.ReactNode>;
  return (
    <div className="space-y-4">
      <ViewTabs pathname="/cycles" active={view} views={(Object.keys(VIEWS) as View[]).map((key) => ({ key, label: t(key) }))} />
      <Page searchParams={Promise.resolve(sp)} />
    </div>
  );
}
