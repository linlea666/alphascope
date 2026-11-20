import type { EntrySide, LiqSide } from "./api";

export type ProfileSettings = {
  ids: string[];
  directory_count: number;
  verified_at?: string;
  checked_at?: string;
  verification?: string;
  discovery?: Partial<
    Record<"BTC" | "ETH", { status: string; at: string; pages: number; public_profiles: number }>
  >;
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
  holders: number;
  liq_coverage: number | null;
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
    complete: ["本轮发现完成", "Discovery complete"],
    partial: ["本轮发现不完整", "Partial discovery"],
    busy: ["正在读取，请稍后重试", "Collection busy, retry shortly"],
    missing_fields: ["需要完整的币安网页凭证", "Binance login fields required"],
    no_shared_profile: [
      "测试主页未分享持仓，暂时无法验证",
      "No shared test profile; verification inconclusive",
    ],
    source_restricted: ["来源限制访问，暂时无法读取", "Access restricted by source"],
    secure_connection_required: ["凭证验证需要 HTTPS 或本机隧道", "Use HTTPS or a localhost tunnel"],
    idle: ["未采集", "Idle"],
    pending: ["等待验证", "Pending verification"],
    ready: ["读取成功", "Verified"],
    hidden: ["最近检查的主页未分享持仓", "Last profile does not share positions"],
    access_denied: ["登录凭证未获授权", "Credential rejected"],
    rate_limited: ["限流退避中", "Rate limited"],
    upstream_error: ["最近读取失败", "Last read failed"],
  };
  return labels[key]?.[zh ? 0 : 1] ?? key;
}
export function profileLink(coin: string, group = "traders") {
  return { pathname: "/market", query: { coin, source: "binance", group } };
}
