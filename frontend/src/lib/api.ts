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
  market_data: { source: string; coin: string; m1: number; first_t: string; last_t: string }[];
  symbols: { coin: string; symbol: string | null; status: string; median_ratio: number | null; basis_p95_bps: number | null; n_samples: number; checked_at: string; notes: string | null }[];
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

export type SwingLeg = {
  id: number;
  direction: "up" | "down";
  start_ts: string;
  start_px: number;
  end_ts: string;
  end_px: number;
  change: number;
  confirmed: boolean;
  covered?: number;
  profitable_share?: number | null;
  median_twr?: number | null;
};

export type SwingOverview = {
  freshness: Freshness;
  coin: string;
  version: string;
  thresholds: Record<string, number>;
  anchor_tolerance_days: number;
  criteria: { min_account_value: number; min_cycle_return: number; max_luck_p: number };
  legs: SwingLeg[];
  minor_legs: SwingLeg[];
  candles: [string, number, number, number, number][];
  screen: { screened: number; candidates: number; cycle_both: number; computed_at: string | null };
  fill_archive: { queued: number; synced: number; truncated: number; fills: number };
};

export type SwingCandidate = {
  address: string;
  display_name: string | null;
  tier: string | null;
  flags: string[];
  account_value: number | null;
  legs_covered: number;
  legs_profitable: number;
  up_profitable: number;
  down_profitable: number;
  up_capture: number | null;
  down_capture: number | null;
  luck_p: number | null;
  current_cycle_both: boolean;
  min_cycle_return: number | null;
  candidate: boolean;
  fill_status: string | null;
  n_fills: number | null;
  earliest_fill_at: string | null;
  truncated: boolean | null;
  last_synced_at: string | null;
  realized: { major: number | null; total: number | null; major_share: number | null };
  legs: Record<string, { twr: number | null; pnl: number | null; bench: number | null }>;
};

export type SwingCandidates = {
  freshness: Freshness;
  scope: string;
  sort: string;
  page: number;
  page_size: number;
  total: number;
  legs: SwingLeg[];
  cycle_leg_ids: number[];
  major_coins: string[];
  rows: SwingCandidate[];
};

export type ScaleKey = "scalp" | "short" | "swing" | "macro";

export type WalletPositions = {
  coverage: {
    status: string;
    reason: string;
    requested_start: string;
    earliest_fill_at: string | null;
    latest_fill_at: string | null;
    n_fills: number;
    truncated: boolean | null;
    last_synced_at: string | null;
    reconstructed_at: string | null;
    recon_breaks: number | null;
  } | null;
  sync_complete: boolean;
  scales: {
    scale: ScaleKey;
    cycles: number;
    complete_closed?: number;
    wins?: number;
    win_rate?: number | null;
    win_rate_ci?: [number, number] | null;
    net_pnl?: number | null;
    median_hours?: number | null;
    open_now?: number | null;
  }[];
  cycles: {
    coin: string;
    direction: "long" | "short";
    opened_at: string;
    closed_at: string | null;
    status: "closed" | "flipped" | "open" | "data_end";
    complete: boolean;
    scale: ScaleKey;
    holding_hours: number;
    entry_px: number | null;
    exit_px: number | null;
    max_notional: number;
    n_fills: number;
    n_decisions: number;
    net_pnl: number;
  }[];
};

export type TimingScore = {
  scale: ScaleKey;
  time_window: "7d" | "30d" | "90d" | "all";
  coin_group: "all" | "btc_eth";
  status: "scored" | "insufficient" | "invalid_window";
  as_of: string;
  n_decisions: number;
  n_scored: number;
  location: number | null;
  null_mean: number | null;
  edge: number | null;
  edge_shrunk: number | null;
  p_value: number | null;
  q_value: number | null;
  pivot_hits: number;
  pivots: number;
  pivots_hit: number;
  precision: number | null;
  recall: number | null;
  alignment: number | null;
  cycles: number;
  net_pnl: number | null;
  n_buy: number | null;
  n_sell: number | null;
  buy_location: number | null;
  sell_location: number | null;
  buy_extreme: number | null;
  sell_extreme: number | null;
  null_extreme: number | null;
  maker_share: number | null;
};

export type TimingBoard = {
  freshness: Freshness;
  scale: ScaleKey;
  window: string;
  group: string;
  coin: string | null;
  sort: string;
  valid: boolean;
  maker_heavy: number;
  extreme: number;
  min_decisions: number;
  page: number;
  page_size: number;
  total: number;
  rows: (Omit<TimingScore, "scale" | "time_window" | "coin_group"> & {
    rank: number;
    address: string;
    display_name: string | null;
    account_value: number | null;
    tier: string | null;
    flags: string[];
  })[];
};

export type WalletTiming = {
  version: string;
  valid_windows: Record<ScaleKey, string[]>;
  min_decisions: Record<ScaleKey, number>;
  scores: TimingScore[];
  calendar: { day: string; scale: ScaleKey; n: number; location: number | null; hits: number }[];
  decision_status: Record<string, number>;
};

export type ZoneSide = {
  decisions: number;
  notional: number | null;
  avg_px: number | null;
  first_at: string;
  last_at: string;
  cycles: number;
  closed: number;
  open: number;
  data_end: number;
  realized_net: number;
  unrealized: number;
  avg_exit_px: number | null;
  max_holding_hours: number | null;
};

export type ZoneQuery = { coin: string; side: "long" | "short"; px_min: number; px_max: number; start: string; end: string };

export type ZoneResult = {
  freshness: Freshness;
  query: { first: ZoneQuery; second: ZoneQuery | null };
  coverage: { archived: number; covering: number; queued_total: number };
  total: number;
  rows: {
    address: string;
    display_name: string | null;
    account_value: number | null;
    tier: string | null;
    flags: string[];
    earliest_fill_at: string | null;
    truncated: boolean | null;
    sync_status: string | null;
    macro_edge: number | null;
    macro_q: number | null;
    zone: ZoneSide;
    also?: ZoneSide;
  }[];
};

export type HolderCycle = {
  cycle_id: number;
  qualifies: boolean;
  status: "closed" | "flipped" | "open" | "data_end";
  complete: boolean;
  opened_at: string;
  closed_at: string | null;
  first_zone_exit_at: string | null;
  entry_px: number;
  exit_px: number | null;
  zone_exit_px: number | null;
  exited_in_zone_share: number;
  holding_share: number;
  max_notional: number | null;
  net: number | null;
  unrealized: number | null;
  move_captured: number | null;
  holding_days: number;
  address?: string;
  display_name?: string | null;
  account_value?: number | null;
  flags?: string[];
  size_vs_equity: number | null;
};

export type HolderZones = {
  freshness: Freshness;
  query: { coin: string; direction: "long" | "short"; entry_min: number; entry_max: number; exit_min: number; exit_max: number; start: string; end: string; min_share: number };
  last_price: number | null;
  coverage: { archived: number; covering: number; queued_total: number };
  qualifying: number;
  rows: HolderCycle[];
};

export type LegList = {
  coin: string;
  legs: { leg_start: string; leg_end: string; direction: "up" | "down"; leg_change: number; confirmed: boolean; wallets: number }[];
  coins: { coin: string; wallets: number }[];
};

export type LegCaptureRanking = {
  freshness: Freshness;
  coin: string;
  leg_start: string | null;
  filters: { min_aligned: number; min_notional: number; full_only: boolean };
  rows: {
    address: string;
    display_name: string | null;
    account_value: number | null;
    leg_start: string;
    leg_end: string;
    direction: "up" | "down";
    leg_change: number;
    confirmed: boolean;
    coverage: "full" | "partial";
    time_aligned: number;
    time_against: number;
    max_notional: number;
    avg_aligned_notional: number;
    pnl_mark: number;
    capture_ratio: number | null;
  }[];
};

export type Replay = {
  coins: { coin: string; decisions: number; last_at: string }[];
  coin: string | null;
  interval: string;
  scale: ScaleKey;
  end: string;
  candles: [string, number, number, number, number][];
  decisions: { started_at: string; side: "buy" | "sell"; kind: string; vwap: number; size: number; notional: number; pos_after: number; location: number | null; pivot_ts: string | null }[];
  pivots: { kind: "H" | "L"; ts: string; px: number; leg_change: number; confirmed: boolean }[];
};

export const api = {
  rankings: (params: Record<string, string | number | boolean | undefined>) => get<Rankings>("/api/v1/rankings", params),
  wallet: (address: string) => get<WalletDetail>(`/api/v1/wallets/${encodeURIComponent(address)}`),
  walletPositions: (address: string) => get<WalletPositions>(`/api/v1/wallets/${encodeURIComponent(address)}/positions`),
  overview: () => get<Overview>("/api/v1/overview"),
  dataHealth: () => get<DataHealth>("/api/v1/data-health"),
  swings: () => get<SwingOverview>("/api/v1/swings"),
  timing: (params: Record<string, string | number | boolean | undefined>) => get<TimingBoard>("/api/v1/timing", params),
  walletTiming: (address: string) => get<WalletTiming>(`/api/v1/wallets/${encodeURIComponent(address)}/timing`),
  zones: (params: Record<string, string | number | boolean | undefined>) => get<ZoneResult>("/api/v1/zones", params),
  timingCoins: (scale: string, window: string) => get<{ coins: { coin: string; wallets: number }[] }>("/api/v1/timing/coins", { scale, window }),
  holderZones: (params: Record<string, string | number | boolean | undefined>) => get<HolderZones>("/api/v1/holders/zones", params),
  holderLegs: (coin: string) => get<LegList>("/api/v1/holders/legs", { coin }),
  holderCapture: (params: Record<string, string | number | boolean | undefined>) => get<LegCaptureRanking>("/api/v1/holders/capture", params),
  replay: (address: string, params: Record<string, string | number | boolean | undefined>) =>
    get<Replay>(`/api/v1/wallets/${encodeURIComponent(address)}/replay`, params),
  swingCandidates: (params: Record<string, string | number | boolean | undefined>) => get<SwingCandidates>("/api/v1/swings/candidates", params),
};
