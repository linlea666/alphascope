import type { EntrySide, LiqSide } from "./api";

export type ProfileSettings = {
  ids: string[];
  minutes: number;
  enabled: boolean;
  status: string;
  has_credential: boolean;
  last_success?: string;
  last_attempt?: string;
  resume_at?: string;
};
export type ProfileMarket = {
  enabled: boolean;
  configured: number;
  fresh: number;
  ref: number | null;
  status: string;
  last_success: string | null;
  structure: {
    entries: { long: EntrySide; short: EntrySide };
    liquidations: { long: LiqSide; short: LiqSide };
  } | null;
  positions: {
    profile_id: string;
    observed_at: string;
    side: string;
    entry: number | null;
    mark: number;
    liq: number | null;
    lev: number | null;
    qty: number;
    notional: number;
  }[];
};
export function profileStatus(key: string, zh: boolean) {
  const labels: Record<string, [string, string]> = {
    idle: ["未采集", "Idle"],
    pending: ["等待验证", "Pending verification"],
    ready: ["读取成功", "Verified"],
    hidden: ["最近检查的主页未分享持仓", "Last profile does not share positions"],
    access_denied: ["访问被拒，采集已暂停", "Access denied. Collection paused"],
    rate_limited: ["限流退避中", "Rate limited"],
    upstream_error: ["最近读取失败", "Last read failed"],
  };
  return labels[key]?.[zh ? 0 : 1] ?? key;
}
export function profileLink(coin: string, group = "traders") {
  return { pathname: "/market", query: { coin, source: "binance", group } };
}
