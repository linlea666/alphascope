const BASE = process.env.API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function get<T>(path: string, params?: Record<string, string | number | boolean | undefined>): Promise<T> {
  const url = new URL(path, BASE);
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
  }
  let res: Response;
  try {
    res = await fetch(url, { cache: "no-store" });
  } catch {
    throw new ApiError(0, `cannot reach ${BASE}`);
  }
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.json() as Promise<T>;
}

export type Freshness = {
  leaderboard_checked_at: string | null;
  enrichment_at: string | null;
  leaderboard_snapshot_at: string | null;
};

export type Window = "day" | "week" | "month" | "alltime";
export type Reliability = "high" | "medium" | "low";

export type RankingRow = {
  rank: number;
  address: string;
  display_name: string | null;
  account_value: number | null;
  pnl: number | null;
  roi: number | null;
  vlm: number | null;
  tier: string | null;
  flags: string[];
  first_seen_at: string;
  twr_30d: number | null;
  mdd_30d: number | null;
  reliability_30d: Reliability | null;
};

export type Rankings = {
  window: Window;
  sort: string;
  order: "asc" | "desc";
  page: number;
  page_size: number;
  total: number;
  excluded_flags: string[];
  activity_required: boolean;
  freshness: Freshness;
  rows: RankingRow[];
};

export type Metric = {
  metric_window: string;
  metric_version: string;
  computed_at: string;
  source_series: string;
  covered: boolean;
  span_start: string | null;
  span_end: string | null;
  n_points: number;
  n_valid_periods: number;
  median_step_hours: number | null;
  twr: number | null;
  mdd: number | null;
  pnl: number | null;
  pnl_mdd_usd: number | null;
  net_flows: number | null;
  positive_period_ratio: number | null;
  reliability: Reliability;
  notes: string[];
};

export type WalletDetail = {
  freshness: Freshness;
  wallet: {
    address: string;
    display_name: string | null;
    first_seen_at: string;
    first_seen_source: string;
    last_seen_at: string;
    present_in_latest: boolean;
    removed_at: string | null;
    account_value: number | null;
    tier: string | null;
    tier_rule_version: string | null;
    flags: string[];
    enrichment_flags: string[];
    portfolio_fetched_at: string | null;
    leaderboard_fetched_at: string | null;
  };
  leaderboard: Record<Window, { pnl: number | null; roi: number | null; vlm: number | null }>;
  metrics: Metric[];
  equity: { month: [string, number, number][]; allTime: [string, number, number][] };
  snapshots: {
    fetched_at: string;
    account_value: number | null;
    day_pnl: number | null;
    week_pnl: number | null;
    month_pnl: number | null;
    alltime_pnl: number | null;
    month_roi: number | null;
    month_vlm: number | null;
  }[];
  tier_history: { tier: string; previous_tier: string | null; rule_version: string; assigned_at: string }[];
  account: { fetched_at: string; account_value: number; total_ntl_pos: number; total_margin_used: number } | null;
  positions: {
    coin: string;
    szi: number;
    entry_px: number | null;
    position_value: number | null;
    unrealized_pnl: number | null;
    leverage_type: string | null;
    leverage_value: number | null;
    liquidation_px: number | null;
  }[];
};

export type Overview = {
  freshness: Freshness;
  baseline_import_at: string | null;
  counts: { total: number; present: number; new_24h: number; new_7d: number; enriched: number; tier_b: number };
  tiers: Record<string, number>;
  flags: Record<string, number>;
  screening: {
    criteria: { min_twr_30d: number; max_drawdown_30d: number; min_positive_period_ratio: number };
    rows: {
      address: string;
      display_name: string | null;
      account_value: number | null;
      twr: number;
      mdd: number;
      positive_period_ratio: number;
      n_valid_periods: number;
      reliability: Reliability;
      month_pnl: number | null;
    }[];
  };
  new_wallets: {
    address: string;
    display_name: string | null;
    first_seen_at: string;
    account_value: number | null;
    month_pnl: number | null;
    tier: string | null;
  }[];
};

export type Run = {
  id: number;
  kind: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  stats: Record<string, unknown>;
  error: string | null;
};

export type DataHealth = {
  freshness: Freshness;
  latest_success: { kind: string; status: string; started_at: string; finished_at: string | null }[];
  runs: Run[];
  tables: Record<string, number>;
  raw_archive: { source: string; n: number; bytes: number }[];
  api_budget: {
    ip_limit_per_minute: number;
    safety_share: number;
    weight_used_24h: number;
    weight_capacity_24h: number;
  };
  schedule: {
    leaderboard_interval_minutes: number;
    leaderboard_snapshot_hours: number;
    enrich_interval_minutes: number;
    enrich_batch_size: number;
    portfolio_refresh_days: number;
  };
};

export const api = {
  rankings: (params: Record<string, string | number | boolean | undefined>) => get<Rankings>("/api/v1/rankings", params),
  wallet: (address: string) => get<WalletDetail>(`/api/v1/wallets/${encodeURIComponent(address)}`),
  overview: () => get<Overview>("/api/v1/overview"),
  dataHealth: () => get<DataHealth>("/api/v1/data-health"),
};
