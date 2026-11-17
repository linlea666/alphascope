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

export type Venue = "hyperliquid" | "okx";

export type OkxDetail = {
  profile_url: string | null;
  trader: {
    history_status: "partial" | "target_complete" | "source_complete" | "error_backoff";
    pool_status: "active" | "watching" | "dropped" | "inactive" | "error_backoff";
    oldest_at: string | null;
    newest_at: string | null;
    n_positions: number;
    target_days: number;
    history_synced_at: string | null;
    asset: number | null;
    asset_at: string | null;
    first_pool_source: string | null;
    last_seen_official_at: string | null;
    last_error: string | null;
    retry_after: string | null;
  } | null;
  presence: {
    time_window: "7d" | "30d" | "90d" | "365d";
    first_seen_at: string;
    last_seen_at: string;
    seen_days: number;
    streak: number;
    currently_seen: boolean;
    last_rank: number | null;
    best_rank: number | null;
  }[];
  ranks: {
    business_date: string;
    time_window: string;
    rank: number;
    asset: number | null;
    pnl: number | null;
    pnl_ratio: number | null;
    win_rate: number | null;
    max_drawdown: number | null;
  }[];
  totals: { positions: number; realized: number | null; fees: number | null; funding: number | null; liquidations: number; coins: number; wins: number };
  pnl_curve: [string, number, number][];
};

export type MarketCoverage = { watched: number; fresh: number; positioned: number; latest: string | null; oldest: string | null };
export type MarketSideStats = {
  long_ntl: number;
  short_ntl: number;
  lev10_long_ntl: number;
  lev10_short_ntl: number;
  lev20_long_ntl: number;
  lev20_short_ntl: number;
  liq_known_ntl: number;
};
export type MarketOverview = {
  as_of: string;
  coverage: MarketCoverage;
  by_venue: Record<"hyperliquid" | "okx", MarketCoverage>;
  totals: MarketSideStats & { traders: number; long_traders: number; short_traders: number; positions: number; long_positions: number; short_positions: number };
  opens_24h: number;
  closes_24h: number;
};
export type MarketCoin = MarketSideStats & {
  coin: string;
  traders: number;
  long_traders: number;
  short_traders: number;
  mark_px: number | null;
  median_leverage: number | null;
  opens_1h: number;
  opens_4h: number;
  opens_24h: number;
  near_long_liq_pct: number | null;
  near_short_liq_pct: number | null;
};
export type MarketContributor = {
  address: string;
  display_name: string | null;
  venue: Venue;
  szi: number;
  notional_usd: number;
  mark_px: number | null;
  entry_px: number | null;
  leverage: number | null;
  margin_mode: string | null;
  liq_px: number | null;
  upl: number | null;
  opened_at: string | null;
  opened_at_source: "source" | "window" | "unknown";
  opened_after: string | null;
  first_seen_at: string;
  observed_at: string;
  account_value: number | null;
};
export type MarketEvent = {
  id: number;
  address: string;
  display_name: string | null;
  venue: Venue;
  coin: string;
  kind: "open" | "increase" | "decrease" | "close" | "flip" | "first_seen";
  side: "long" | "short";
  szi_before: number;
  szi_after: number;
  notional_change: number;
  mark_px: number | null;
  entry_px: number | null;
  leverage: number | null;
  liq_px: number | null;
  event_time: string;
  time_precision: "exact" | "window";
  window_start: string | null;
  observed_at: string;
  current_px: number | null;
  upl: number | null;
};
export type MarketHistoryRow = { bucket_ts: string; observed: number; long_traders: number; short_traders: number; long_ntl: number; short_ntl: number; lev20_long_ntl: number; lev20_short_ntl: number; opens: number; closes: number };
export type PriceBin = { lo: number; hi: number; notional: number; positions: number };
export type PriceZone = { lo: number; hi: number; notional: number; share: number; positions: number; avg_px: number; avg_lev: number | null; distance: number; in_profit?: boolean };
export type EntrySide = {
  positions: number;
  notional: number;
  avg_entry: number | null;
  avg_distance: number | null;
  median_entry: number | null;
  profit_share: number | null;
  underwater_share: number | null;
  top5_share: number | null;
  bins: PriceBin[];
  zones: PriceZone[];
  outside: { positions: number; notional: number };
};
export type LiqSide = {
  positions: number;
  notional: number;
  known_share: number | null;
  cross_share: number | null;
  without_liq: { positions: number; notional: number };
  crossed: { positions: number; notional: number };
  beyond_notional: number;
  bins: PriceBin[];
  zones: PriceZone[];
  outside: { positions: number; notional: number };
  nearest: { address: string | null; display_name: string | null; px: number; distance: number; notional: number; leverage: number | null; mode: string | null } | null;
  nearest_cluster: { lo: number; hi: number; notional: number; positions: number; distance: number } | null;
  ladder: { move: number; notional: number; positions: number }[];
};
export type MarketStructure = {
  coin: string;
  as_of: string;
  positions: number;
  ref: { px: number; observed_at: string } | null;
  entries?: { long: EntrySide; short: EntrySide };
  liquidations?: { long: LiqSide; short: LiqSide; min_leverage: number };
};

export type MarketLiquidations = {
  coin: string;
  mark_px: number | null;
  span_pct?: number;
  positions?: number;
  bins: { lo: number; hi: number; long_ntl: number; short_ntl: number; long_traders: number; short_traders: number; cross_ntl: number }[];
  without_liq: { positions: number; notional: number };
  outside: { positions: number; notional: number };
};

export type StyleKey = "steady" | "trend" | "swing" | "bottom" | "top_short" | "scalper" | "low_dd" | "specialist" | "high_risk";
export type StyleStatus = "verified" | "candidate" | "watch";
export type EvidenceRule = { factor: string; op: string; threshold: number; value: number | null; n: number; ok: boolean | null; gate?: boolean };
export type Evidence = EvidenceRule | { any: EvidenceRule[]; ok: boolean | null } | { meta: string; value: string | null };
export type Oos = { cut?: string; forward_n?: number; forward_net?: number; forward_pf?: number | null; passed?: boolean; tested?: boolean; reason?: string; pre_status?: string } | null;
export type FactorCell = { value: number | null; n: number };
export type DiscoverStyles = {
  computed_at: string | null;
  oos_days: number;
  styles: { key: StyleKey; risk_flag: boolean; note: string; oos: boolean; counts: Partial<Record<StyleStatus, number>> }[];
};
export type DiscoverBoard = {
  style: StyleKey;
  rows: {
    address: string;
    display_name: string | null;
    venue: Venue;
    status: StyleStatus;
    score: number | null;
    confidence: number;
    n: number;
    evidence: Evidence[];
    oos: Oos;
    computed_at: string;
    other: StyleKey[];
    factors: Record<string, FactorCell>;
  }[];
};
export type DiscoverProfile = {
  factors: Record<string, Partial<Record<"all" | "90d", FactorCell & { as_of: string }>>>;
  styles: { style: StyleKey; status: StyleStatus; score: number | null; confidence: number; n: number; evidence: Evidence[]; oos: Oos; computed_at: string }[];
};
export type TopRow = { address: string; display_name: string | null; venue: Venue; asset: number | null; net: number | null; ret: number | null; n?: number; wins?: number; mdd?: number | null; reliability?: string | null; open_loss_ratio?: number | null; styles: StyleKey[] };
export type DiscoverTop = { period: "7d" | "30d"; since: string; okx: TopRow[]; hyperliquid: TopRow[] };

export type OkxSummary = {
  traders: number;
  positions: number;
  complete: number;
  active: number;
  dropped: number;
  watching: number;
  latest_7d: { business_date: string; completed_at: string; expected_total: number; unique_count: number; new_traders: number } | null;
};

export type OkxBoard = {
  window: "7d" | "30d" | "90d" | "365d";
  view: "board" | "new" | "dropped";
  timezone: string;
  run: { id: number; business_date: string; completed_at: string; expected_total: number; unique_count: number; new_traders: number; source: string; data_version: string | null } | null;
  runs: { business_date: string; complete: number; failed: number }[];
  rows: {
    rank: number | null;
    address: string;
    display_name: string | null;
    avatar_url: string | null;
    pnl: number | null;
    pnl_ratio: number | null;
    win_rate: number | null;
    max_drawdown: number | null;
    asset: number | null;
    is_new: boolean;
    last_seen_at: string | null;
    history_status: string | null;
    pool_status: string | null;
    n_positions: number | null;
    oldest_at: string | null;
    seen_days: number | null;
    streak: number | null;
    first_seen_at: string | null;
    best_rank: number | null;
    currently_seen: boolean | null;
    realized_30d: number | null;
    realized_90d: number | null;
    liquidations_90d: number;
    best_edge: number | null;
  }[];
};

export type WalletDetail = {
  freshness: Freshness;
  okx: OkxDetail | null;
  wallet: {
    address: string;
    venue: Venue;
    handle: string | null;
    avatar_url: string | null;
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
  okx: {
    runs: { business_date: string; time_window: string; status: string; source: string; expected_total: number | null; unique_count: number; new_traders: number; started_at: string; completed_at: string | null; error: string | null }[];
    pool: { pool_status: string; history_status: string; n: number; positions: number | null; synced_24h: number; backing_off: number }[];
    without_handle: number;
    candle_coins: number;
    instruments_at: string | null;
    requests_per_minute: number;
  };
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
  null_quantiles?: number[] | null;
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
    okx_asset: number | null;
    venue: string;
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

export type Watchlist = {
  groups: { id: number; name: string; color: string | null; sort: number; n: number }[];
  items: {
    item_id: number;
    group_id: number;
    note: string | null;
    tags: string[];
    created_at: string;
    address: string;
    display_name: string | null;
    venue: Venue;
    alias: string | null;
    account_value: number | null;
    week_pnl: number | null;
    month_pnl: number | null;
    tier: string | null;
    flags: string[];
    sync_status: string | null;
    n_fills: number | null;
    best_edge: number | null;
    positions: { coin: string; szi: number; value: number }[];
  }[];
};

export type AlertSub = {
  enabled: boolean;
  events: string[];
  min_notional: number;
  min_change_pct: number;
  coins: string[];
  recipients: string[];
  quiet_start: number | null;
  quiet_end: number | null;
};

export type AlertsOverview = {
  subscriptions: (AlertSub & {
    id: number;
    address: string;
    name: string | null;
    last_polled: string | null;
    last_error: string | null;
    account_value: number | null;
    last_event_at: string | null;
  })[];
  event_kinds: string[];
};

export type PositionEvents = {
  events: {
    id: number;
    t: string;
    coin: string;
    kind: string;
    szi_before: number;
    szi_after: number;
    px: number | null;
    notional_change: number | null;
    notify_status: string;
    address: string;
    name: string | null;
  }[];
};

export type Notifications = {
  notifications: { id: number; created_at: string; sent_at: string | null; status: string; attempts: number; error: string | null; subject: string; recipients: string[]; address: string | null }[];
  sent_today: number;
};

export type AppSettings = {
  smtp: { host: string; port: number; security: "starttls" | "ssl" | "none"; username: string; from_name: string; recipients: string[]; public_url: string; password_set: boolean };
  live: { interval_seconds: number; okx_interval_seconds?: number; coalesce_seconds: number; max_emails_per_hour: number; liq_risk_pct: number; timezone: string };
  live_status: { cycle_started_at?: string; interval_seconds?: number; wallets?: number; events?: number; errors?: number; cycle_seconds?: number; error?: string; notify?: Record<string, number> };
};

export type SimStatus = "running" | "paused" | "stopped";
export type SimSizing = "equity_ratio" | "fixed_multiple" | "fixed_cap";

export type SimSummary = {
  id: number;
  name: string;
  status: SimStatus;
  stop_reason: "manual" | "drawdown" | "liquidated" | null;
  stop_pending: boolean;
  capital: number;
  cash: number;
  peak_equity: number;
  sizing: SimSizing;
  size_factor: number;
  max_leverage: number;
  delay_seconds: number;
  created_at: string;
  stopped_at: string | null;
  leader: string;
  leader_name: string | null;
  equity: number;
  equity_at: string | null;
  gross_exposure: number | null;
  trades: number;
  fees: number;
  slippage: number;
  funding: number;
  max_dd: number | null;
  open_positions: number;
  pnl: number;
  return: number | null;
};

export type SimParams = {
  sizing: SimSizing;
  size_factor: number;
  cap_per_coin: number | null;
  max_leverage: number;
  coins_include: string[];
  coins_exclude: string[];
  follow_adjust: boolean;
  copy_existing: boolean;
  delay_seconds: number;
  fee_bps: number;
  slippage_bps: number;
  min_trade_usd: number;
  rebalance_pct: number;
  stop_drawdown_pct: number | null;
};

export type SimPeriod = "1d" | "7d" | "30d" | "all";

export type SimPeriodStats = {
  pnl: number;
  return: number | null;
  max_dd: number;
  leader_return: number | null;
  retention: number | null;
  closed: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  median_hold_hours: number | null;
};

export type SimDaily = { day: string; pnl: number; return: number | null; equity: number };

export type SimDetail = {
  summary: SimSummary;
  analytics: {
    timezone: string;
    periods: Record<SimPeriod, SimPeriodStats>;
    daily: SimDaily[];
    per_coin: { coin: string; realized: number; unrealized: number; fees: number; slippage: number; funding: number; net: number; trades: number }[];
    cycles: { coin: string; side: 1 | -1; opened_at: string; closed_at: string | null; pnl: number; fees: number; funding: number; max_notional: number; trades: number }[];
  };
  params: SimParams;
  state: { followed?: string[]; baseline?: Record<string, number>; pending?: Record<string, { target: string; due: string }>; started_at?: string };
  positions: { coin: string; size: number; entry_px: number; opened_at: string; mark: number | null }[];
  leader_positions: { coin: string; szi: number; value: number | null }[];
  trades: {
    t: string;
    coin: string;
    kind: "trade" | "funding" | "liquidation" | "stop";
    side: "buy" | "sell" | null;
    size: number;
    mid: number | null;
    px: number | null;
    fee: number;
    slippage_cost: number;
    funding: number;
    realized_pnl: number;
    position_after: number | null;
    leader_szi: number | null;
    reason: string | null;
  }[];
  equity: [string, number, number, number | null][];
};

export type SimDashboard = {
  period: SimPeriod;
  timezone: string;
  sims: (SimSummary & { period: SimPeriodStats & { trades: number; fees: number; slippage: number; funding: number } })[];
  portfolio: {
    count: number;
    active: number;
    capital: number;
    equity: number;
    pnl: number;
    period_pnl: number;
    period_fees: number;
    period_funding: number;
    period_slippage: number;
    period_trades: number;
  };
  calendar: { day: string; pnl: number; sims: number }[];
  correlation: { ids: number[]; matrix: (number | null)[][]; min_days: number };
};

export type Person = { address: string; display_name: string | null; venue: string };

export type LegHall = {
  coin: string;
  zone_pad: number;
  min_notional: number;
  legs: {
    leg_start: string;
    leg_end: string;
    direction: "up" | "down";
    leg_change: number;
    confirmed: boolean;
    wallets: number;
    start_px: number | null;
    end_px: number | null;
    capturers: (Person & { capture_ratio: number; pnl_mark: number; max_notional: number; time_aligned: number; coverage: string })[];
    turn: { wallets: number; rows: (Person & { opened_at: string; entry_px: number; notional: number; net: number | null })[] };
    wrong: (Person & { pnl_mark: number; max_notional: number; time_against: number })[];
    query: { direction: string; entry_min: number; entry_max: number; exit_min: number; exit_max: number; start: string; end: string } | null;
  }[];
};

export type HighlightCycle = {
  coin: string;
  direction: "long" | "short";
  opened_at: string;
  closed_at: string | null;
  entry_px: number | null;
  exit_px: number | null;
  max_notional: number | null;
  holding_hours: number | null;
  net: number;
  scale: string;
  move?: number;
};

export type Highlights = {
  venue: string;
  closed: number;
  wins: number;
  win_rate: number | null;
  net: number | null;
  median_hours: number | null;
  long_share: number | null;
  main_scale: string | null;
  top_coins: { coin: string; n: number }[];
  liquidations: number | null;
  timing: { scale: ScaleKey; time_window: string; q_value: number; edge_shrunk: number | null; n_scored: number; p_value: number } | null;
  styles: { style: string; status: string }[];
  factors: Record<string, { value: number | null; n: number }>;
  best: HighlightCycle[];
  worst: HighlightCycle[];
};

export type FollowBacktest = {
  supported: boolean;
  reason?: string;
  capital?: number;
  since?: string;
  until?: string;
  covered?: boolean;
  final?: number;
  ret?: number;
  mdd?: number;
  n_points?: number;
  median_step_hours?: number | null;
  rows?: [string, number][];
};

export type SimilarTraders = {
  factors: string[];
  min_closed: number;
  rows: (Person & { similarity: number; closest: string[]; net_pnl: number | null; n_closed: number; styles: string[] })[];
};

export type Comparison = {
  days: number;
  since: string;
  traders: (Person & { size: number | null; highlights: Omit<Highlights, "venue" | "wins" | "best" | "worst">; curve: [string, number][] })[];
  correlation: (number | null)[][];
};

export type Divergence = {
  as_of: string;
  venue: string;
  min_smart: number;
  coin: string;
  coins: { coin: string; smart_long_share: number; crowd_long_share: number; divergence: number; smart_traders: number; crowd_traders: number; smart_net_ntl: number }[];
  history: { day: string; smart_long_share: number; crowd_long_share: number; divergence: number; smart_traders: number; close: number | null; forward_return: number | null }[];
};

export type Sentinel = {
  id: number;
  name: string;
  coin: string;
  side: "long" | "short";
  px_min: number;
  px_max: number;
  cohort: string;
  venue: string;
  min_notional: number;
  recipients: string[];
  enabled: boolean;
  created_at: string;
  last_checked_at: string | null;
  last_hit_at: string | null;
  hits: number;
};

export type Sentinels = {
  sentinels: Sentinel[];
  recent: (Person & { sentinel_id: number; first_at: string; avg_px: number | null; notional: number | null; found_at: string; notify_status: string })[];
};

export type Digest = {
  settings: { enabled: boolean; recipients: string[] };
  digest: null | {
    day: string;
    created_at: string;
    emailed_at: string | null;
    baseline: boolean;
    watch_moves: { address: string; name: string | null; venue: string; coin: string; kind: string; side: string; notional_change: number; entry_px: number | null; event_time: string }[];
    divergence: Divergence["coins"];
    new_significant: (Person & { scale: string; q: number })[];
    new_significant_total: number;
    significant_total: number;
    sentinel_hits: { name: string; coin: string; side: string; address: string; name_w: string | null; avg_px: number | null; notional: number | null }[];
  };
};

export type Candles = { coin: string; interval: string; source: string | null; rows: [string, number, number, number, number][] };

export const api = {
  rankings: (params: Record<string, string | number | boolean | undefined>) => get<Rankings>("/api/v1/rankings", params),
  wallet: (address: string) => get<WalletDetail>(`/api/v1/wallets/${encodeURIComponent(address)}`),
  walletPositions: (address: string) => get<WalletPositions>(`/api/v1/wallets/${encodeURIComponent(address)}/positions`),
  overview: () => get<Overview>("/api/v1/overview"),
  watchlist: () => get<Watchlist>("/api/v1/watch"),
  alerts: () => get<AlertsOverview>("/api/v1/alerts"),
  alertEvents: (limit = 100) => get<PositionEvents>("/api/v1/alerts/events", { limit }),
  notifications: () => get<Notifications>("/api/v1/alerts/notifications"),
  settings: () => get<AppSettings>("/api/v1/settings"),
  watchAliases: () => get<{ aliases: Record<string, string> }>("/api/v1/watch/aliases"),
  dataHealth: () => get<DataHealth>("/api/v1/data-health"),
  swings: () => get<SwingOverview>("/api/v1/swings"),
  timing: (params: Record<string, string | number | boolean | undefined>) => get<TimingBoard>("/api/v1/timing", params),
  walletTiming: (address: string) => get<WalletTiming>(`/api/v1/wallets/${encodeURIComponent(address)}/timing`),
  zones: (params: Record<string, string | number | boolean | undefined>) => get<ZoneResult>("/api/v1/zones", params),
  timingCoins: (scale: string, window: string, venue?: string) => get<{ coins: { coin: string; wallets: number }[] }>("/api/v1/timing/coins", { scale, window, venue }),
  okxSummary: () => get<OkxSummary>("/api/v1/okx/summary"),
  marketOverview: (venue: string, cohort: string) => get<MarketOverview>("/api/v1/market/overview", { venue, cohort }),
  marketCoins: (venue: string, cohort: string) => get<{ as_of: string; rows: MarketCoin[] }>("/api/v1/market/coins", { venue, cohort }),
  marketContributors: (params: Record<string, string | number | undefined>) => get<{ rows: MarketContributor[] }>("/api/v1/market/contributors", params),
  marketEvents: (params: Record<string, string | number | undefined>) => get<{ as_of: string; rows: MarketEvent[] }>("/api/v1/market/events", params),
  marketHistory: (params: Record<string, string | number | undefined>) => get<{ rows: MarketHistoryRow[] }>("/api/v1/market/history", params),
  marketStructure: (params: Record<string, string | number | undefined>) => get<MarketStructure>("/api/v1/market/structure", params),
  marketLiquidations: (params: Record<string, string | number | undefined>) => get<MarketLiquidations>("/api/v1/market/liquidations", params),
  discoverStyles: (venue: string) => get<DiscoverStyles>("/api/v1/discover/styles", { venue }),
  discoverBoard: (params: Record<string, string | number | undefined>) => get<DiscoverBoard>("/api/v1/discover", params),
  discoverProfile: (address: string) => get<DiscoverProfile>(`/api/v1/discover/profile/${encodeURIComponent(address)}`),
  discoverTop: (period: string) => get<DiscoverTop>("/api/v1/discover/top", { period }),
  okxBoard: (params: Record<string, string | number | boolean | undefined>) => get<OkxBoard>("/api/v1/okx/board", params),
  holderZones: (params: Record<string, string | number | boolean | undefined>) => get<HolderZones>("/api/v1/holders/zones", params),
  holderLegs: (coin: string) => get<LegList>("/api/v1/holders/legs", { coin }),
  holderCapture: (params: Record<string, string | number | boolean | undefined>) => get<LegCaptureRanking>("/api/v1/holders/capture", params),
  replay: (address: string, params: Record<string, string | number | boolean | undefined>) =>
    get<Replay>(`/api/v1/wallets/${encodeURIComponent(address)}/replay`, params),
  simDashboard: (period: SimPeriod) => get<SimDashboard>("/api/v1/sims/dashboard", { period }),
  sim: (id: number) => get<SimDetail>(`/api/v1/sims/${id}`),
  legsHall: (coin: string, venue: string) => get<LegHall>("/api/v1/legs/hall", { coin, venue }),
  highlights: (address: string) => get<Highlights>(`/api/v1/wallets/${encodeURIComponent(address)}/highlights`),
  follow: (address: string, params: Record<string, string | number | undefined>) => get<FollowBacktest>(`/api/v1/wallets/${encodeURIComponent(address)}/follow`, params),
  similar: (address: string) => get<SimilarTraders>(`/api/v1/discover/similar/${encodeURIComponent(address)}`),
  compare: async (addrs: string[], days = 180) => {
    const url = new URL("/api/v1/compare", BASE);
    addrs.forEach((a) => url.searchParams.append("addr", a));
    url.searchParams.set("days", String(days));
    return get<Comparison>(url.pathname + url.search);
  },
  divergence: (venue: string, coin: string) => get<Divergence>("/api/v1/market/divergence", { venue, coin }),
  sentinels: () => get<Sentinels>("/api/v1/alerts/sentinels"),
  digest: () => get<Digest>("/api/v1/alerts/digest"),
  candles: (coin: string, interval: string, since: string) => get<Candles>("/api/v1/candles", { coin, interval, since }),
  swingCandidates: (params: Record<string, string | number | boolean | undefined>) => get<SwingCandidates>("/api/v1/swings/candidates", params),
};
