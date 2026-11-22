import { get } from "./api";

export type EventDef = {
  id: number;
  coin: string;
  event_key: string;
  available_at: string;
  title: string;
  start_at: string;
  end_at: string;
  start_px: number;
  end_px: number;
  direction: string;
  method: string;
  source: string;
  revision: number;
};
export type WalletFlow = {
  address: string;
  net: number;
  fills: number;
  long_add: number;
  long_cut: number;
  short_add: number;
  short_cut: number;
  liquidation_fills: number;
};
export type Flow = {
  long_add: number | null;
  long_cut: number | null;
  short_add: number | null;
  short_cut: number | null;
  wallets: number;
  fills: number;
  missing: number;
  net: number | null;
  without_largest: number | null;
  top1_share: number | null;
  top5_share: number | null;
  top10_share: number | null;
  contributors: WalletFlow[];
};
export type Detail = {
  event: EventDef;
  coin: string;
  hours: number;
  evidence_cutoff: string;
  vintage: string;
  mode: string;
  flow: Flow;
  okx: { positions: number; traders: number };
  prices: [string, number][];
  frame: number | null;
  common_wallets?: number;
  common_net?: number | null;
  net_fraction?: number | null;
};
type Group = { n: number; long_share: number | null; long_notional_share: number | null };
export type Frame = {
  id: number;
  venue: string;
  available_at: string;
  stale: boolean;
  metrics: {
    windows?: Record<
      string,
      {
        net_units: number | null;
        long_change: number | null;
        short_change: number | null;
        common_n: number;
      } | null
    >;
    n: number;
    fresh: number;
    watched: number;
    ref: number | null;
    long_share: number | null;
    divergence: number | null;
    smart: Group;
    control: Group;
    nearest_distance?: number | null;
    distance_in_hourly_sigma?: number | null;
    high_leverage_share?: number | null;
    delta: { common_n: number; net_units: number | null };
    zone_followup?: {
      side: string;
      lo: number;
      hi: number;
      bars: number;
      touch_bar: string | null;
      venue_liquidation_records: number;
      suspected_closes: number;
      reductions_before_touch_or_cutoff: number;
    }[];
    zones: Record<string, { lo: number; hi: number; notional: number }[]>;
  };
  context: {
    fields: {
      oi?: number;
      mark?: number;
      funding?: number;
      basis_bps?: number;
      depth?: { bid: number; ask: number };
    };
    oi_unit?: string;
    missing?: string[];
  };
};
export type Notice = {
  id: number;
  frame_id: number;
  at: string;
  venue: string;
  kind: string;
  watching: boolean;
  outcome: { status: string; ret?: number } | null;
  evidence: {
    rank: number;
    n: number;
    history_n: number;
    value: number;
    contributors: { address: string }[];
    counterevidence: { largest_share: number | null; common_n: number };
  };
};
export type Summary = {
  coin: string;
  as_of: string;
  frames: Frame[];
  notices: Notice[];
  coverage: {
    venue: string;
    first: string | null;
    updated: string | null;
    n: number;
    missing_hours: number | null;
  }[];
};
type Evaluation = {
  id: number;
  cutoff: string;
  result: { status: string; pairs: number; q: number; tested_pairs?: number; review_eligible?: boolean };
};
type Forecast = {
  id: number;
  horizon: number;
  issued_at: string;
  due_at: string;
  model: string;
  cutoff: string;
  frame_id: number;
  evidence: {
    p: number[];
    baseline: number[];
    train_n: number;
    analog_n: number;
    momentum_4h: number;
    band: number;
    reviewed_factors?: { hypothesis: string; review: number; treatment: boolean | null }[];
  };
  outcome: { status: string; ret?: number } | null;
};
export type Archive = {
  calibration: {
    model: string;
    horizon: number;
    cls: string;
    bin: number;
    n: number;
    predicted: number;
    observed: number;
  }[];
  studies: {
    id: number;
    version: number;
    review: { decision: string; rationale: string } | null;
    hypothesis: string;
    registered_at: string;
    runs: Evaluation[];
  }[];
  forecasts: Forecast[];
  scores: {
    model: string;
    horizon: number;
    n: number;
    brier: number;
    base_brier: number;
    log_loss: number;
    base_log_loss: number;
    missing: number;
    missing_rate: number | null;
    pending: number;
    skill: { n: number; mean_diff: number | null; blocks: number; block_size: number; p: number | null };
  }[];
};
export type FactorTest = {
  factor: string;
  horizon: number;
  n_is: number;
  n_oos: number;
  ic_is: number | null;
  p_is: number | null;
  ic_oos: number | null;
  p_oos: number | null;
  q: number | null;
  status: "candidate" | "in_sample_only" | "none";
};
export type RegimeStats = { n: number; mean_1d: number | null; up_1d?: number | null; mean_3d: number | null; up_3d?: number | null };
export type FactorScreen = {
  coin: string;
  status: "screened" | "insufficient_history" | "not_run";
  computed_at: string | null;
  version?: string;
  fdr?: number;
  oos_alpha?: number;
  test_count?: number;
  tests?: FactorTest[];
  factors: Record<string, string>;
  days?: number;
  start?: string | null;
  end?: string | null;
  split?: string;
  regimes?: Record<"long_build" | "short_cover" | "short_build" | "long_unwind" | "all", RegimeStats>;
  today?: { day: string; regime: string | null; values: Record<string, { value: number | null; percentile: number | null }> };
};
type Tiered<K, D> = { kind: K; coin: string | null; tier: "verified" | "watching" | "descriptive"; data: D };
export type Conclusion =
  | Tiered<"forecast", { n: number; mean_diff: number | null; blocks: number; block_size: number; p: number | null }>
  | Tiered<"study", { hypothesis: string; status: string; pairs: number; tested_pairs: number; weeks: number;
      effect: number | null; q: number | null; eligible: boolean; review: string | null }>
  | Tiered<"regime", { regime: string | null; day: string; stats: RegimeStats | null; all: RegimeStats }>
  | Tiered<"factors", { candidates: FactorTest[]; tests: number; computed_at: string }>
  | Tiered<"funding", { rate: number | null; level: string | null; percentile: number | null; source_at: string | null }>
  | Tiered<"persistence", { venue: string; horizon: number; lookback: number; status: string; mean_rho: number | null;
      p_value: number | null; top_minus_bottom: number | null; need_days: number; tested_days: number;
      shared_denominator?: boolean; sign_mean?: number | null; sign_p?: number | null }>
  | Tiered<"copy_score", { horizon: number; status: string; graded_days: number; need_days: number;
      mean_rho: number | null; p_value: number | null; tested_days: number }>;
export const research = {
  sources: (
    id: number,
    params: {
      coin: string;
      venue: string;
      hours: number;
      as_of: string;
      mode: string;
      offset: number;
      wallet?: string;
    },
  ) =>
    get<{
      event: EventDef;
      cutoff: string;
      has_more: boolean;
      rows: {
        reference: string;
        event_time: string;
        known_at: string;
        address: string;
        side: string;
        price: number;
        quantity: number;
        archive_id?: number;
        sha256?: string;
        start_position?: number | null;
      }[];
    }>(`/api/v1/research/events/${id}/sources`, params),
  report: (id: number, coin: string) =>
    get<{ published_at: string; source_version: string; detail: Detail }>(`/api/v1/research/reports/${id}`, {
      coin,
    }),
  coins: () => get<{ coins: string[] }>("/api/v1/research/coins"),
  summary: (coin: string) => get<Summary>("/api/v1/research/summary", { coin }),
  catalog: (coin: string) => get<{ as_of: string; events: EventDef[] }>("/api/v1/research/events", { coin }),
  replay: (id: number, hours: number, mode: string, asOf: string, sync?: string) =>
    get<Detail>(`/api/v1/research/events/${id}`, { hours, mode, as_of: asOf, synchronous_coin: sync }),
  compare: (ids: number[], hours: number, mode: string, asOf: string, cross: boolean) =>
    get<{ panels: Detail[]; overlap_clusters: number }>(
      `/api/v1/research/compare?${ids.map((i) => "ids=" + i).join("&")}`,
      { hours, mode, as_of: asOf, cross_coin: cross },
    ),
  timeline: (coin: string, venue: string) =>
    get<{ rows: Frame[] }>("/api/v1/research/frames", { coin, venue }),
  frame: (id: number, coin: string, offset = 0) =>
    get<
      Frame & {
        total_accounts: number;
        accounts: {
          address: string;
          observed_at: string;
          notional_usd: number;
          liq_px: number | null;
          cohort: string;
        }[];
      }
    >(`/api/v1/research/frames/${id}`, { coin, offset }),
  archive: (coin: string) => get<Archive>("/api/v1/research/archive", { coin }),
  factors: (coin: string) => get<FactorScreen>("/api/v1/research/factors", { coin }),
  conclusions: () => get<{ items: Conclusion[]; at: string }>("/api/v1/research/conclusions"),
  similar: (id: number, hours: number, asOf: string, cross: boolean, mode: string) =>
    get<{
      n: number;
      matches: { event: EventDef; distance: number; opposite: boolean }[];
      counterexample?: { event: EventDef };
    }>(`/api/v1/research/events/${id}/similar`, { hours, as_of: asOf, cross_coin: cross, mode }),
};
export const numeric = (n: number | null | undefined, decimals = 2) =>
  n == null ? "—" : Number(n).toLocaleString("en-US", { maximumFractionDigits: decimals });
export const percent = (n: number | null | undefined) => (n == null ? "—" : numeric(n * 100) + "%");
export const utc = (value: string | null | undefined) =>
  value ? value.replace("T", " ").slice(0, 16) + " UTC" : "—";

export function noticeLabel(kind: string, zh: boolean) {
  const labels: Record<string, [string, string]> = {
    long_share: ["做多人数占比", "Long trader share"],
    divergence: ["专项群体分歧", "Specialist divergence"],
    top5_share: ["头部账户集中度", "Top five concentration"],
    high_leverage_share: ["高杠杆金额占比", "High-leverage share"],
    nearest_distance: ["最近强平距离", "Nearest liquidation distance"],
    hourly_volatility: ["小时波动率", "Hourly volatility"],
    depth: ["盘口可见深度", "Visible depth"],
    net_change: ["固定账户净变化", "Common-holder net change"],
  };
  return labels[kind]?.[zh ? 0 : 1] ?? kind;
}
