import { get } from "./api";

export type Side = { n: number; usd: number; qty: number };
export type Group = {
  long: Side;
  short: Side;
  money_share: number | null;
  head_share: number | null;
  net: number;
};
export type Sample<T> = { at: string; stale: boolean; data: T };
type Groups = { traders: Group; whales: Group };
type Ratio = { ratio: number; long_share: number; source_at: string };
export const windows = ["30m", "1h", "24h", "7D"] as const;
export type Window = (typeof windows)[number] | "ALL";
type Funding = Sample<{
  rate: number;
  payer: "long" | "short" | "none";
  per_10000: number;
  due: string;
  source_at: string;
}>;
type Readings = Partial<Record<Window, Sample<Groups>>> &
  Partial<Record<`${"binance" | "okx"}_${"accounts" | "top_accounts" | "top_positions"}`, Sample<Ratio>>> & {
    roster?: Sample<{ updated: string; total: number }>;
    funding?: Funding;
    okx_funding?: Funding;
  };
export type Observation = {
  id: number;
  coin: string;
  published_at: string;
  watching: boolean;
  readings: Readings;
  errors: Record<string, string>;
};
type Specialist = { n: number; long_share: number | null; long_notional_share: number | null };
type Mark = { id: number; published_at: string };
export type Evidence = {
  observation: Observation | null;
  coverage_start: string | null;
  saved: Mark[];
  groups: { id: number; venue: string; available_at: string; specialist: Specialist; control: Specialist }[];
  history: (Mark & {
    bucket: string;
    observed: string | null;
    flow_at: string | null;
    smart_accounts: number | null;
    overall_accounts: number | null;
    share: number | null;
    flow: number | null;
  })[];
};
export type Page = {
  total: number;
  collected_at: string;
  rows: {
    side: "BUY" | "SELL";
    time: string | null;
    public_id: string | null;
    name: string | null;
    n: number;
    usd: number | null;
    qty: number | null;
    entry: number | null;
  }[];
};
export const exchange = {
  summary: (coin: string) => get<Observation | null>("/api/v1/research/exchange/summary", { coin }),
  evidence: (coin: string, observation?: string, as_of?: string) =>
    get<Evidence>("/api/v1/research/exchange/evidence", { coin, observation, as_of }),
  page: (coin: string, params: Record<string, string | number>) =>
    get<Page>("/api/v1/research/exchange/details", { coin, ...params }),
};
export const exchangeLink = (coin: string, params: Record<string, string | number> = {}) => ({
  pathname: "/replay",
  query: { coin, tab: "exchange", ...params },
});
export function formatValue(v: number | null | undefined, zh: boolean, compact = false) {
  if (v == null) return "—";
  return new Intl.NumberFormat(zh ? "zh-CN" : "en-US", {
    notation: compact ? "compact" : "standard",
    maximumFractionDigits: compact ? 2 : 4,
  }).format(v);
}
