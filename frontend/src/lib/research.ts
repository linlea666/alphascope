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
  evidence: { rank: number; n: number; history_n: number };
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
type Evaluation = { cutoff: string; result: { status: string; pairs: number; q: number } };
type Forecast = {
  id: number;
  horizon: number;
  issued_at: string;
  due_at: string;
  evidence: { p: number[]; baseline: number[] };
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
  studies: { id: number; hypothesis: string; registered_at: string; runs: Evaluation[] }[];
  forecasts: Forecast[];
  scores: {
    horizon: number;
    n: number;
    brier: number;
    base_brier: number;
    log_loss: number;
    base_log_loss: number;
  }[];
};
export const research = {
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
