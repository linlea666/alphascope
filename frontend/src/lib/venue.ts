import { cookies } from "next/headers";

export type VenueFilter = "all" | "hyperliquid" | "okx";
export const VENUE_COOKIE = "as_venue";

/** The exchange the research pages are filtered to, remembered in a cookie by VenueSwitch. */
export async function getVenue(allowAll = true): Promise<VenueFilter> {
  const v = (await cookies()).get(VENUE_COOKIE)?.value;
  if (v === "hyperliquid" || v === "okx") return v;
  return allowAll ? "all" : "hyperliquid";
}
